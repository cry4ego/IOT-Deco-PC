"""Kiểm thử phần PC của màn hình phụ: gói tin, tìm vùng đổi, chia gói, chọn màn ảo, gửi khung hình."""
import io
import socket
import struct
import sys
import zlib
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import link as link_module  # noqa: E402
from capture import Monitor, fit_rect, parse_region, pick_virtual  # noqa: E402
from link import (ACK, FRAME, HEADER_LEN, NAK, NEED_FULL, FrameSender, LinkError, TcpLink,  # noqa: E402
                  build_packet, diff_regions)


def unpack(packet: bytes):
    magic, ptype, _, x, y, length, crc = struct.unpack("<2sBBHHII", packet[:HEADER_LEN])
    return magic, ptype, x, y, length, crc, packet[HEADER_LEN:]


def screen_of(packet: bytes) -> int:
    return packet[3]


def split_packets(data: bytes):
    out, i = [], 0
    while i < len(data):
        length = struct.unpack_from("<I", data, i + 8)[0]
        out.append(data[i:i + HEADER_LEN + length])
        i += HEADER_LEN + length
    return out


def test_packet_header_carries_position_length_and_crc32():
    payload = b"\x01\x02\x03hello"

    magic, ptype, x, y, length, crc, body = unpack(build_packet(1, 10, 20, payload))

    assert (magic, ptype, x, y, length, body) == (b"MP", 1, 10, 20, len(payload), payload)
    assert crc == zlib.crc32(payload)


def test_packet_carries_the_target_screen():
    assert screen_of(build_packet(1, 0, 0, b"x", screen=1)) == 1
    assert screen_of(build_packet(1, 0, 0, b"x")) == 0


def test_large_region_uses_light_jpeg_and_small_region_stays_sharp():
    big_type, big = link_module.encode_region(noisy().crop((0, 0, 320, 240)))
    small_type, small = link_module.encode_region(Image.new("RGB", (40, 20), (30, 30, 30)))

    assert big_type == link_module.JPEG
    assert Image.open(io.BytesIO(big)).info.get("progression") is None    # JPEG thường, ESP32 đọc được
    assert small_type == link_module.QOI if link_module.QOI_OK else link_module.JPEG


def test_side_screen_frame_goes_to_screen_one_in_the_same_frame():
    link = FakeLink([ACK])

    FrameSender(link).send(noisy(1), side=Image.new("RGB", (240, 240), (10, 20, 30)))

    packets = split_packets(link.sent)
    assert {screen_of(p) for p in packets[:-1]} == {0, 1}
    assert unpack(packets[-1])[1] == FRAME


def test_side_screen_unchanged_sends_nothing_for_it():
    link = FakeLink([ACK, ACK])
    sender = FrameSender(link)
    side = Image.new("RGB", (240, 240), (10, 20, 30))
    img = noisy()
    sender.send(img, side=side)
    link.sent = b""

    sender.send(img.copy(), side=side.copy())

    assert [unpack(p)[1] for p in split_packets(link.sent)] == [FRAME]


def test_threaded_sender_writes_everything_in_order():
    link = FakeLink([ACK, ACK, ACK])
    sender = FrameSender(link, in_flight=2, threaded=True)
    frames = [noisy(i) for i in range(3)]
    expected = FakeLink([ACK, ACK, ACK])
    direct = FrameSender(expected)
    for f in frames:
        sender.send(f)
        direct.send(f)

    sender.close()

    assert link.sent == expected.sent


def test_diff_regions_finds_nothing_in_identical_frames():
    a = np.zeros((240, 320, 3), np.uint8)
    assert diff_regions(a, a.copy()) == []


def test_diff_regions_covers_each_separate_change():
    a = np.zeros((240, 320, 3), np.uint8)
    b = a.copy()
    b[5, 7] = 255
    b[200, 300] = 255

    assert diff_regions(a, b) == [(7, 5, 8, 6), (300, 200, 301, 201)]


class FakeLink:
    def __init__(self, replies):
        self.replies = list(replies)
        self.sent = b""

    def write(self, data: bytes):
        self.sent += data

    def read_reply(self, timeout: float):
        return self.replies.pop(0) if self.replies else None


class FakeFonts:
    def get(self, size, bold=False, text=""):
        from PIL import ImageFont
        return ImageFont.load_default(size)


def noisy(seed=0):
    rng = np.random.default_rng(seed)
    return Image.fromarray(rng.integers(0, 255, (240, 320, 3), dtype=np.uint8))


def test_first_frame_sends_whole_screen_in_payload_sized_pieces_then_frame_end(monkeypatch):
    monkeypatch.setattr(link_module, "MAX_PAYLOAD", 20000)
    link = FakeLink([ACK])

    FrameSender(link).send(noisy())

    packets = split_packets(link.sent)
    assert unpack(packets[-1])[1] == FRAME
    rects = packets[:-1]
    assert len(rects) > 1                                    # ảnh nhiễu vượt giới hạn: phải chia nhỏ
    assert all(unpack(p)[4] <= 20000 for p in rects)
    assert unpack(rects[0])[2:4] == (0, 0)


def test_unchanged_frame_only_sends_frame_end():
    link = FakeLink([ACK, ACK])
    sender = FrameSender(link)
    img = noisy()
    sender.send(img)
    link.sent = b""

    sender.send(img.copy())

    assert [unpack(p)[1] for p in split_packets(link.sent)] == [FRAME]


@pytest.mark.parametrize("reply", [NAK, NEED_FULL])
def test_rejected_frame_forces_full_screen_next_time(reply):
    link = FakeLink([reply, ACK])
    sender = FrameSender(link)
    img = noisy()
    sender.send(img)
    link.sent = b""

    sender.send(img.copy())

    assert len(split_packets(link.sent)) > 1


def test_two_frames_in_flight_waits_only_when_both_unanswered():
    link = FakeLink([ACK, ACK])
    sender = FrameSender(link, in_flight=2)

    sender.send(noisy(1))
    assert link.replies == [ACK, ACK]                        # khung đầu: chưa chờ trả lời
    sender.send(noisy(2))
    assert link.replies == [ACK]                             # khung thứ hai: chờ trả lời khung đầu


def test_byte_budget_waits_before_overfilling_the_esp32_buffer():
    link = FakeLink([ACK, ACK, ACK])
    sender = FrameSender(link, in_flight=4, max_bytes=1000)

    sender.send(noisy(1))                                    # khung lớn hơn hạn mức: vẫn gửi được
    assert link.replies == [ACK, ACK, ACK]
    sender.send(noisy(2))                                    # cộng thêm thì vượt: chờ khung trước xong
    assert link.replies == [ACK, ACK]


def test_rejected_frame_with_two_in_flight_still_forces_full_screen():
    link = FakeLink([NAK, ACK, ACK])
    sender = FrameSender(link, in_flight=2)
    img = noisy()
    sender.send(img)
    sender.send(img.copy())                                  # nhận NAK của khung đầu
    link.sent = b""

    sender.send(img.copy())

    assert len(split_packets(link.sent)) > 1


def test_tcp_link_keeps_replies_that_arrive_together():
    server = socket.create_server(("127.0.0.1", 0))
    tcp = TcpLink("127.0.0.1", server.getsockname()[1])
    peer, _ = server.accept()
    peer.sendall(bytes([ACK, NAK]))                          # 2 câu trả lời trong cùng 1 gói mạng

    replies = [tcp.read_reply(1.0), tcp.read_reply(1.0)]

    tcp.close()
    peer.close()
    server.close()
    assert replies == [ACK, NAK]


def test_missing_reply_raises_link_error():
    with pytest.raises(LinkError):
        FrameSender(FakeLink([])).send(noisy())


def test_fit_rect_scales_and_centres():
    assert fit_rect(640, 480, 320, 240) == (320, 240, 0, 0)
    assert fit_rect(1920, 1080, 320, 240) == (320, 180, 0, 30)


def monitor(name, adapter, left=0, width=1920, height=1080):
    return Monitor(device=name, adapter=adapter, left=left, top=0, width=width, height=height, primary=False)


def test_pick_virtual_finds_the_virtual_display_driver_monitor():
    monitors = [monitor(r"\\.\DISPLAY1", "AMD Radeon RX 5700 XT"),
                monitor(r"\\.\DISPLAY3", "Virtual Display Driver", left=4480, width=640, height=480)]

    assert pick_virtual(monitors).device == r"\\.\DISPLAY3"


def test_pick_virtual_returns_none_without_virtual_monitor():
    assert pick_virtual([monitor(r"\\.\DISPLAY1", "AMD Radeon RX 5700 XT")]) is None


def test_side_page_rotates_between_pc_stats_and_monitor_status():
    from side_view import PAGE_MONITOR, PAGE_PC, side_page

    assert side_page(0.0) == PAGE_PC
    assert side_page(9.9) == PAGE_PC
    assert side_page(10.0) == PAGE_MONITOR
    assert side_page(14.9) == PAGE_MONITOR
    assert side_page(15.0) == PAGE_PC


def test_monitor_status_page_is_screen_sized():
    from side_view import render_monitor_page

    img = render_monitor_page(FakeFonts(), fps=12.3, kbps=95, link_name="USB COM4 @ 2000000",
                              source=(4480, 0, 800, 600), now_text="00:30")

    assert img.size == (240, 240)


def test_is_deskmon_recognises_the_deskmon_firmware_only():
    from man_phu import is_deskmon

    assert is_deskmon({"fw": "2.1.0", "mode": "Offline (PC tắt)"}) is True
    assert is_deskmon({"firmware": "man-phu 0.2.0"}) is False
    assert is_deskmon({}) is False


def test_parse_region():
    assert parse_region("100,50,640,480") == (100, 50, 640, 480)
    with pytest.raises(ValueError):
        parse_region("100,50,640")
