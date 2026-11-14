"""
Gửi khung hình sang ESP32 của màn hình phụ, qua USB (Serial) hoặc WiFi (TCP).
Giao thức: monitor/esp32/man_phu/protocol.h. Mỗi khung chỉ gửi vùng thay đổi, xong khung chờ ESP32 trả lời.
"""
from __future__ import annotations

import io
import logging
import queue
import select
import socket
import struct
import threading
import time
import zlib
from collections import deque
from typing import Optional

import numpy as np
from PIL import Image

log = logging.getLogger("man_phu")

MAGIC = b"MP"
HELLO, JPEG, QOI, FRAME = 0, 1, 2, 3
ACK, NAK, NEED_FULL = 0x06, 0x15, 0x07
CTRL_BYTES = (ACK, NAK, NEED_FULL)
HEADER_LEN = 16
MAX_PAYLOAD = 60000          # phải <= MAX_PAYLOAD trong protocol.h
JPEG_QUALITY = 85
JPEG_QUALITY_BIG = 70
BIG_REGION_PIXELS = 320 * 120  # từ nửa màn trở lên
REPLY_TIMEOUT_S = 3.0        # WiFi đôi khi khựng 1-2 giây rồi chạy tiếp
QUEUE_POLL_S = 0.2           # chờ luồng ghi nhận khung: cứ chừng này lại xem nó đã lỗi chưa
CONNECT_TIMEOUT_S = 5.0
HELLO_TRIES = 10
KNOWN_VIDS = {0x10C4, 0x1A86, 0x0403, 0x303A}   # CP210x, CH340/CH9102, FTDI, Espressif


class LinkError(Exception):
    pass


def build_packet(ptype: int, x: int = 0, y: int = 0, payload: bytes = b"", screen: int = 0) -> bytes:
    """screen: 0 = màn 2.0" (màn hình phụ), 1 = màn 1.54" (trang thông số)."""
    return MAGIC + struct.pack("<BBHHII", ptype, screen, x, y, len(payload), zlib.crc32(payload)) + payload


def _qoi_supported() -> bool:
    try:
        Image.new("RGB", (2, 2)).save(io.BytesIO(), "QOI")
        return True
    except (KeyError, OSError, ValueError):
        return False


QOI_OK = _qoi_supported()


def encode_region(img: Image.Image) -> tuple[int, bytes]:
    """Vùng lớn (video, cuộn trang, đổi cửa sổ): JPEG nhẹ, ít dữ liệu nhất vì cáp USB chỉ chở ~147 KB/s.
    Vùng nhỏ (chữ, chuột): QOI giữ nét, hoặc JPEG chất lượng cao nếu QOI quá nặng."""
    jb = io.BytesIO()
    if img.width * img.height >= BIG_REGION_PIXELS:
        img.save(jb, "JPEG", quality=JPEG_QUALITY_BIG, subsampling=2)
        return JPEG, jb.getvalue()
    img.save(jb, "JPEG", quality=JPEG_QUALITY, subsampling=0)
    jpg = jb.getvalue()
    if QOI_OK:
        qb = io.BytesIO()
        img.save(qb, "QOI")
        qoi = qb.getvalue()
        if len(qoi) <= MAX_PAYLOAD and len(qoi) <= max(len(jpg) * 1.3, 4000):
            return QOI, qoi
    return JPEG, jpg


def diff_regions(a: np.ndarray, b: np.ndarray, gap: int = 6) -> list[tuple[int, int, int, int]]:
    """Các vùng (x0, y0, x1, y1) khác nhau giữa hai khung, gộp những hàng gần nhau."""
    mask = np.any(a != b, axis=2)
    rows = np.flatnonzero(mask.any(axis=1))
    if rows.size == 0:
        return []
    groups, start, prev = [], int(rows[0]), int(rows[0])
    for r in rows[1:]:
        r = int(r)
        if r - prev > gap:
            groups.append((start, prev))
            start = r
        prev = r
    groups.append((start, prev))
    out = []
    for r0, r1 in groups:
        cols = np.flatnonzero(mask[r0:r1 + 1].any(axis=0))
        out.append((int(cols[0]), r0, int(cols[-1]) + 1, r1 + 1))
    return out


class FrameSender:
    """Gửi từng khung (màn 2.0" và tuỳ chọn màn 1.54"), nhớ khung đã gửi để lần sau chỉ gửi phần đổi.

    in_flight = 2: gửi khung kế tiếp ngay trong lúc ESP32 còn đang vẽ khung trước, thay vì đứng
    chờ nó trả lời. Khung bị từ chối (NAK / NEED_FULL) thì khung sau nữa gửi lại toàn màn.
    threaded: ghi xuống cáp ở luồng riêng, để trong lúc gửi khung này PC đã chụp và nén khung sau."""

    def __init__(self, link, in_flight: int = 1, threaded: bool = False, max_bytes: Optional[int] = None):
        self.link = link
        self.in_flight = in_flight
        self.max_bytes = max_bytes                   # tổng byte chưa được trả lời: vừa bộ đệm nhận của ESP32
        self.in_air: deque[int] = deque()            # cỡ các khung đã gửi, chưa được trả lời
        self.prev: dict[int, Optional[np.ndarray]] = {0: None, 1: None}
        self._queue: Optional[queue.Queue] = None
        self._writer_error: Optional[BaseException] = None
        if threaded:
            self._queue = queue.Queue(maxsize=1)
            self._writer = threading.Thread(target=self._write_loop, daemon=True, name="writer")
            self._writer.start()

    def send(self, img: Image.Image, side: Optional[Image.Image] = None) -> int:
        data = self._screen_packets(0, img)
        if side is not None:
            data += self._screen_packets(1, side)
        data += build_packet(FRAME)
        while self.in_air and self.max_bytes and sum(self.in_air) + len(data) > self.max_bytes:
            self._wait_reply()                       # gửi thêm sẽ tràn bộ đệm ESP32: chờ khung cũ xong
        self._write(data)
        self.in_air.append(len(data))
        while len(self.in_air) >= self.in_flight:
            self._wait_reply()
        return len(data)

    def close(self):
        """Gửi hết những gì còn trong hàng đợi rồi dừng luồng ghi. Không bao giờ chờ quá vài giây:
        luồng ghi kẹt (WiFi khựng, cáp rút) thì bỏ phần chưa gửi."""
        if self._queue is None:
            return
        q, self._queue = self._queue, None
        try:
            q.put(None, timeout=self._stall_s())
        except queue.Full:
            while True:                              # luồng ghi kẹt: bỏ khung đang chờ để gửi được lệnh dừng
                try:
                    q.get_nowait()
                except queue.Empty:
                    break
            q.put_nowait(None)
        self._writer.join(REPLY_TIMEOUT_S)

    def _screen_packets(self, screen: int, img: Image.Image) -> bytes:
        arr = np.asarray(img.convert("RGB"))
        h, w = arr.shape[:2]
        prev = self.prev[screen]
        regions = [(0, 0, w, h)] if prev is None or prev.shape != arr.shape else diff_regions(prev, arr)
        self.prev[screen] = arr
        return b"".join(self._rect_packets(img, r, screen) for r in regions)

    def _write(self, data: bytes):
        if self._queue is None:
            self.link.write(data)
            return
        # put() không giới hạn từng làm treo cả chương trình: luồng ghi lỗi rồi thoát trong lúc
        # hàng đợi còn đầy, luồng chính chờ chỗ trống mãi mãi (log 14/11 lúc 13:22 và 15:19)
        deadline = time.monotonic() + self._stall_s()
        while True:
            if self._writer_error is not None:
                raise LinkError(f"Lỗi gửi: {self._writer_error}")
            try:
                self._queue.put(data, timeout=QUEUE_POLL_S)
                return
            except queue.Full:
                if time.monotonic() > deadline:
                    raise LinkError("ESP32 không nhận dữ liệu (luồng ghi bị kẹt)") from None

    def _write_loop(self):
        queue_ = self._queue
        while (data := queue_.get()) is not None:
            if self._writer_error is not None:
                continue                      # đã lỗi: vẫn lấy hết hàng đợi để luồng chính không kẹt
            try:
                self.link.write(data)
            except Exception as e:            # báo lại cho luồng chính ở lần gửi sau
                self._writer_error = e

    def _stall_s(self) -> float:
        return getattr(self.link, "reply_timeout", REPLY_TIMEOUT_S)

    def forget(self):
        self.prev = {0: None, 1: None}               # lần sau gửi lại toàn màn

    def _wait_reply(self):
        reply = self.link.read_reply(self._stall_s())
        if reply is None:
            self.forget()
            raise LinkError("ESP32 không phản hồi")
        self.in_air.popleft()
        if reply != ACK:
            self.forget()

    def _rect_packets(self, img: Image.Image, rect: tuple[int, int, int, int], screen: int) -> bytes:
        x0, y0, x1, y1 = rect
        ptype, payload = encode_region(img.crop(rect))
        if len(payload) > MAX_PAYLOAD and y1 - y0 > 1:
            mid = (y0 + y1) // 2
            return (self._rect_packets(img, (x0, y0, x1, mid), screen)
                    + self._rect_packets(img, (x0, mid, x1, y1), screen))
        return build_packet(ptype, x0, y0, payload, screen)


def keep_replies(replies: deque, data: bytes):
    """Giữ mọi byte trả lời: gửi trước nhiều khung thì vài câu trả lời có thể tới cùng lúc.
    Các byte khác (chữ log của ESP32) bỏ qua."""
    replies.extend(c for c in data if c in CTRL_BYTES)


class SerialLink:
    reply_timeout = REPLY_TIMEOUT_S

    def __init__(self, port: str, baud: int):
        import serial  # pyserial
        self.ser = serial.Serial()
        self.ser.port = port
        self.ser.baudrate = baud
        self.ser.timeout = 0.05
        self.ser.write_timeout = 5
        self.ser.dtr = False          # tránh reset ESP32 khi mở cổng
        self.ser.rts = False
        self.ser.open()
        time.sleep(0.2)
        self.ser.reset_input_buffer()
        self.name = f"USB {port} @ {baud}"
        self.replies: deque[int] = deque()

    @staticmethod
    def find_port() -> Optional[str]:
        from serial.tools import list_ports
        for p in list_ports.comports():
            if p.vid in KNOWN_VIDS:
                return p.device
        return None

    def write(self, data: bytes):
        self.ser.write(data)

    def read_reply(self, timeout: float) -> Optional[int]:
        end = time.monotonic() + timeout
        while not self.replies and time.monotonic() < end:
            keep_replies(self.replies, self.ser.read(64))
        return self.replies.popleft() if self.replies else None

    def close(self):
        self.ser.close()


_known_ips: dict[str, str] = {}


def lookup_ipv4(host: str) -> str:
    return socket.getaddrinfo(host, None, socket.AF_INET, socket.SOCK_STREAM)[0][4][0]


def address_of(host: str) -> str:
    """IP đã kết nối được lần trước (nếu có), để khỏi hỏi lại tên deskmon.local."""
    return _known_ips.get(host, host)


def connect_tcp(host: str, port: int) -> socket.socket:
    """Thử IP lần trước trước: mDNS (deskmon.local) trên Windows thỉnh thoảng không trả lời
    (log: "getaddrinfo failed" làm mất hình 10-20 giây). Không được mới hỏi lại tên (IP có thể đổi)."""
    known = _known_ips.get(host)
    if known:
        try:
            return socket.create_connection((known, port), timeout=CONNECT_TIMEOUT_S)
        except OSError:
            pass
    ip = lookup_ipv4(host)
    sock = socket.create_connection((ip, port), timeout=CONNECT_TIMEOUT_S)
    _known_ips[host] = ip
    return sock


class TcpLink:
    # WiFi hay khựng 1-3 giây nhưng TCP không làm mất byte nào: chờ thêm thay vì cắt kết nối
    # (log cũ: 64/66 lần mất kết nối là do chờ 3 giây không đủ khi đang dùng WiFi).
    reply_timeout = 6.0

    def __init__(self, host: str, port: int):
        self.sock = connect_tcp(host, port)
        self.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        self.name = f"WiFi {host}:{port}"
        self.replies: deque[int] = deque()

    def write(self, data: bytes):
        self.sock.sendall(data)

    def read_reply(self, timeout: float) -> Optional[int]:
        end = time.monotonic() + timeout
        while not self.replies and (remain := end - time.monotonic()) > 0:
            ready, _, _ = select.select([self.sock], [], [], remain)
            if not ready:
                break
            data = self.sock.recv(64)
            if not data:
                raise LinkError("ESP32 đã đóng kết nối")
            keep_replies(self.replies, data)
        return self.replies.popleft() if self.replies else None

    def close(self):
        self.sock.close()


def handshake(link):
    for _ in range(HELLO_TRIES):
        link.write(build_packet(HELLO))
        if link.read_reply(0.5) == ACK:
            return
    raise LinkError("Không bắt tay được với ESP32 (đúng firmware màn hình phụ chưa? DeskMon cũ còn chạy?)")


def open_link(port: Optional[str], baud: int, host: Optional[str], tcp_port: int, default_host: str):
    """--host: chỉ WiFi. --port: chỉ USB. Mặc định: USB nếu thấy cổng COM, không thì WiFi."""
    if host:
        link = TcpLink(host, tcp_port)
    else:
        port = port or SerialLink.find_port()
        link = SerialLink(port, baud) if port else TcpLink(default_host, tcp_port)
    try:
        handshake(link)
    except Exception:
        link.close()
        raise
    log.info("Đã kết nối ESP32 qua %s", link.name)
    return link
