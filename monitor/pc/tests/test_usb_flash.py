"""Kiểm thử nạp màn phụ qua USB: đọc/ghi otadata, nhận diện vùng, không bao giờ ghi đè DeskMon."""
import struct
import sys
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import usb_flash as uf  # noqa: E402
from link import HandshakeError  # noqa: E402

MONITOR_SHA = bytes(range(32))


def otadata(*seqs):
    """Hai sector otadata như trên board: mỗi mục seq, nhãn, trạng thái, crc."""
    raw = b""
    for seq in seqs:
        entry = (struct.pack("<I20sII", seq, b"\xff" * 20, 2, uf.ota_crc(seq)) if seq is not None
                 else b"\xff" * 32)
        raw += entry + b"\xff" * (uf.SECTOR - 32)
    return raw


def head(kind):
    data = bytearray(b"\xff" * uf.HEAD_LEN)
    if kind == "empty":
        return bytes(data)
    data[0] = uf.IMAGE_MAGIC
    data[uf.APP_SHA] = MONITOR_SHA if kind in ("monitor", "half") else bytes(32)
    if kind == "deskmon":
        data[0x2A8C:0x2A8C + len(uf.DESKMON_MARK)] = uf.DESKMON_MARK
    if kind == "half":                       # nạp qua WiFi bị ngắt: đủ dữ liệu nhưng chưa có byte đầu
        data[0] = 0xFF
    return bytes(data)


# ---------------------------------------------------------------- otadata
def test_crc_matches_the_otadata_read_from_the_real_board():
    assert uf.ota_crc(0x13) == 0xBD53078E                    # đọc qua esptool ngày 15/11
    assert uf.ota_crc(0x12) == 0x05EF60EB


def test_active_slot_follows_the_highest_valid_sequence():
    assert uf.active_slot(uf.parse_otadata(otadata(0x13, 0x12))) == 0     # đúng như board lúc lỗi
    assert uf.active_slot(uf.parse_otadata(otadata(0x13, 0x14))) == 1
    assert uf.active_slot(uf.parse_otadata(otadata(None, None))) == 0     # trống: bootloader chạy app0


def test_broken_crc_is_ignored():
    raw = bytearray(otadata(0x13, 0x14))
    raw[uf.SECTOR + 28] ^= 0xFF                               # hỏng crc của mục seq 0x14

    assert uf.active_slot(uf.parse_otadata(bytes(raw))) == 0


@pytest.mark.parametrize("slot, seq", [(1, 0x14), (0, 0x15)])
def test_otadata_write_selects_the_slot_and_keeps_the_entry_in_use(slot, seq):
    entries = uf.parse_otadata(otadata(0x13, 0x12))

    addr, sector = uf.otadata_write(entries, slot)

    assert addr == uf.OTADATA_ADDR + uf.SECTOR                 # ghi vào mục cũ (seq 0x12), giữ mục đang dùng
    assert len(sector) == uf.SECTOR
    new = otadata(0x13, None)[:uf.SECTOR] + sector
    assert uf.active_slot(uf.parse_otadata(new)) == slot
    assert struct.unpack_from("<I", sector)[0] == seq


def test_otadata_write_on_blank_otadata():
    addr, sector = uf.otadata_write(uf.parse_otadata(otadata(None, None)), 1)

    assert uf.active_slot(uf.parse_otadata(otadata(None, None)[:uf.SECTOR] + sector)) == 1
    assert addr == uf.OTADATA_ADDR


# ---------------------------------------------------------------- nhận diện vùng, chọn vùng ghi
@pytest.mark.parametrize("kind, expected", [("empty", uf.EMPTY), ("deskmon", uf.DESKMON), ("monitor", uf.MONITOR),
                                            ("other", uf.OTHER), ("half", uf.EMPTY)])
def test_classify_slot(kind, expected):
    assert uf.classify_slot(head(kind), MONITOR_SHA) == expected


def test_plan_writes_the_monitor_into_the_slot_without_deskmon():
    plan = uf.plan_flash(0, [uf.DESKMON, uf.EMPTY])           # đúng tình trạng board 15/11

    assert (plan.target, plan.write_app) == (1, True)


def test_plan_only_switches_when_the_monitor_is_already_there():
    assert uf.plan_flash(0, [uf.DESKMON, uf.MONITOR]) == uf.Plan(1, False, uf.plan_flash(0, [uf.DESKMON, uf.MONITOR]).reason)


def test_plan_only_resets_when_the_monitor_is_selected():
    assert uf.plan_flash(1, [uf.DESKMON, uf.MONITOR]).target is None


@pytest.mark.parametrize("running, kinds", [(1, [uf.EMPTY, uf.DESKMON]), (0, [uf.DESKMON, uf.OTHER])])
def test_plan_never_targets_the_deskmon_slot(running, kinds):
    plan = uf.plan_flash(running, kinds)

    assert kinds[plan.target] != uf.DESKMON


# ---------------------------------------------------------------- cả quy trình (esptool giả)
def fake_board(monkeypatch, ota_raw, heads):
    calls = []

    def esptool(port, args, timeout, after="hard_reset"):
        calls.append((args[0], args[1] if len(args) > 1 else "", after))
        if args[0] == "read_flash":
            addr, length, path = int(args[1], 16), int(args[2], 16), Path(args[3])
            image = ota_raw + b"\xff" * (uf.SLOT_ADDRS[0] - uf.OTADATA_ADDR - len(ota_raw)) + heads[0]
            if addr == uf.OTADATA_ADDR:
                path.write_bytes(image[:length])
            else:
                path.write_bytes(heads[1][:length])
        return ""
    monkeypatch.setattr(uf, "_esptool", esptool)
    return calls


def write_firmware(tmp_path):
    fw = bytearray(b"\x00" * 0x200)
    fw[0] = uf.IMAGE_MAGIC
    fw[uf.APP_SHA] = MONITOR_SHA
    path = tmp_path / "firmware.bin"
    path.write_bytes(bytes(fw))
    return path


def test_flash_monitor_writes_app_first_then_otadata_and_leaves_deskmon_alone(monkeypatch, tmp_path):
    calls = fake_board(monkeypatch, otadata(0x13, 0x12), [head("deskmon"), head("half")])

    result = uf.flash_monitor("COM4", write_firmware(tmp_path))

    writes = [c for c in calls if c[0] == "write_flash"]
    assert writes == [("write_flash", hex(uf.SLOT_ADDRS[1]), "no_reset"),
                      ("write_flash", hex(uf.OTADATA_ADDR + uf.SECTOR), "hard_reset")]
    assert all(c[1] != hex(uf.SLOT_ADDRS[0]) for c in writes)       # vùng DeskMon không bị ghi
    assert "vùng 1" in result


def test_flash_monitor_only_resets_a_board_already_on_the_monitor(monkeypatch, tmp_path):
    calls = fake_board(monkeypatch, otadata(0x13, 0x14), [head("deskmon"), head("monitor")])

    uf.flash_monitor("COM4", write_firmware(tmp_path))

    assert [c[0] for c in calls if c[0] != "read_flash"] == ["read_mac"]


def test_flash_monitor_needs_the_firmware_file(tmp_path):
    with pytest.raises(uf.UsbFlashError):
        uf.flash_monitor("COM4", tmp_path / "none.bin")


# ---------------------------------------------------------------- chương trình màn phụ
def make_app(monkeypatch):
    import man_phu
    monkeypatch.setattr(man_phu.time, "sleep", lambda s: None)
    app = man_phu.App(types.SimpleNamespace(no_side=True, host=None, firmware="fw.bin"))
    return man_phu, app


def test_usb_handshake_failure_flashes_over_usb_on_the_second_try(monkeypatch):
    man_phu, app = make_app(monkeypatch)
    flashed, wifi = [], []
    monkeypatch.setattr(man_phu.usb_flash, "flash_monitor", lambda port, fw: flashed.append(port) or "ok")
    monkeypatch.setattr(app, "maybe_reflash", lambda: wifi.append(1))
    error = HandshakeError("không bắt tay được", usb_port="COM4")

    app.recover("Mất kết nối", error)
    assert flashed == [] and wifi == []                       # lần đầu: chờ thêm một lần cho chắc
    app.recover("Mất kết nối", error)

    assert flashed == ["COM4"] and wifi == []                 # có cáp USB: không đụng tới WiFi
    assert app.usb_failures == 0


def test_failed_usb_flash_backs_off_and_lets_wifi_try(monkeypatch):
    man_phu, app = make_app(monkeypatch)
    wifi = []

    def broken(port, fw):
        raise man_phu.usb_flash.UsbFlashError("esptool lỗi")
    monkeypatch.setattr(man_phu.usb_flash, "flash_monitor", broken)
    monkeypatch.setattr(app, "maybe_reflash", lambda: wifi.append(1))
    error = HandshakeError("không bắt tay được", usb_port="COM4")

    app.recover("x", error)
    app.recover("x", error)

    assert wifi == [1] and app.usb_flash_failures == 1
    assert app.next_usb_flash > man_phu.time.monotonic() + 20


def test_wifi_handshake_failure_still_uses_wifi(monkeypatch):
    man_phu, app = make_app(monkeypatch)
    wifi = []
    monkeypatch.setattr(app, "maybe_reflash", lambda: wifi.append(1))

    app.recover("x", HandshakeError("không bắt tay được", usb_port=None))

    assert wifi == [1]


def test_an_unexpected_error_while_reflashing_does_not_stop_the_program(monkeypatch):
    man_phu, app = make_app(monkeypatch)

    def boom():
        raise RuntimeError("bất ngờ")
    monkeypatch.setattr(app, "maybe_reflash", boom)

    app.recover("x", OSError("timed out"))                    # không được ném lỗi ra ngoài
