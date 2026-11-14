#!/usr/bin/env python3
"""
Màn hình phụ: chụp màn hình ảo của Windows, thu nhỏ về 320x240 rồi gửi sang ESP32 (màn 2.0").

Chạy:
    python man_phu.py --list                    # xem các màn hình Windows đang có
    python man_phu.py                           # tự tìm màn ảo (Virtual Display Driver), tự chọn USB/WiFi
    python man_phu.py --monitor 2               # chọn màn theo số thứ tự trong --list
    python man_phu.py --region 0,0,640,480      # thử khi chưa cài driver: phản chiếu 1 vùng màn chính
    python man_phu.py --host deskmon.local      # chỉ dùng WiFi
"""
from __future__ import annotations

import argparse
import logging
import time
from pathlib import Path

import cv2
import numpy as np
import requests
from PIL import Image

from capture import Grabber, Monitor, fit_rect, list_monitors, parse_region, pick_virtual, set_dpi_aware
from guard import Watchdog, acquire_single_instance, restart_self
from link import FrameSender, LinkError, address_of, open_link
from side_view import DEFAULT_LHM_URL, SideView

log = logging.getLogger("man_phu")

SCREEN_W, SCREEN_H = 320, 240
DEFAULT_HOST = "deskmon.local"
RETRY_S = 1.0                  # WiFi chập chờn thì nối lại nhanh, đỡ đứng hình
NO_MONITOR_RETRY_S = 10.0      # tự khởi động cùng Windows: driver màn ảo có thể chưa sẵn sàng
GEOMETRY_CHECK_S = 5.0         # đổi độ phân giải / vị trí màn ảo trong Display settings
STATS_EVERY_S = 30.0
FRAMES_IN_FLIGHT = 4           # gửi trước vài khung trong lúc ESP32 đang vẽ (đo: 2 -> 10,6; 4 -> 13,3 khung/giây)
BYTES_IN_FLIGHT = 40000        # < bộ đệm nhận 48 KB của ESP32 (Serial.setRxBufferSize trong man_phu.ino)
# PC tắt thì ESP32 tự chuyển sang DeskMon; bật PC lên thì nạp lại firmware màn phụ qua WiFi
FIRMWARE = Path(__file__).resolve().parent.parent / ".pio" / "build" / "esp32dev" / "firmware.bin"
DESKMON_FIRMWARE = Path(__file__).resolve().parents[2] / ".pio" / "build" / "ota" / "firmware.bin"
REFLASH_EVERY_S = 300.0        # nạp lỗi thì 5 phút sau mới thử lại
REFLASH_CHECK_S = 10.0         # chưa nối được: cứ chừng này hỏi xem ESP32 có đang chạy DeskMon không
REFLASH_TIMEOUT_S = 180
REBOOT_WAIT_S = 20.0
RATE_WINDOW_S = 2.0
# Vòng chính kẹt quá chừng này thì tự chạy lại (< 90 giây ESP32 chờ trước khi về DeskMon)
WATCHDOG_S = 45.0
LONG_WORK_S = REFLASH_TIMEOUT_S + REBOOT_WAIT_S + 60    # nạp firmware: được phép lâu hơn


def describe(monitors: list[Monitor]) -> str:
    return "\n".join(f"  {i}: {m.device}  {m.width}x{m.height} tại ({m.left},{m.top})  {m.adapter}"
                     + ("  [màn chính]" if m.primary else "") for i, m in enumerate(monitors))


def choose_source(args) -> tuple[int, int, int, int] | None:
    if args.region:
        return parse_region(args.region)
    monitors = list_monitors()
    if args.monitor is not None:
        m = monitors[args.monitor] if 0 <= args.monitor < len(monitors) else None
    else:
        m = pick_virtual(monitors)
    if m is None:
        log.warning("Không thấy màn hình ảo. Các màn hiện có:\n%s", describe(monitors))
        return None
    return m.left, m.top, m.width, m.height


def is_deskmon(status: dict) -> bool:
    """/api/status của DeskMon có trường "fw", của màn phụ có "firmware"."""
    return "fw" in status


def needs_deskmon(status: dict) -> bool:
    """Màn phụ báo vùng firmware còn lại không chứa DeskMon (bị nạp đè): PC tắt sẽ không về DeskMon được."""
    return "firmware" in status and status.get("deskmonSlot") is False


def board_status(host: str) -> tuple[str | None, dict | None]:
    """(địa chỉ trả lời được, /api/status). Thử IP lần trước rồi mới tới tên (mDNS hay hụt)."""
    for addr in dict.fromkeys([address_of(host), host]):
        try:
            return addr, requests.get(f"http://{addr}/api/status", timeout=5).json()
        except (requests.RequestException, ValueError):
            continue
    return None, None


def install_deskmon_if_missing(host: str, firmware: Path) -> bool:
    """Nạp DeskMon vào vùng firmware còn lại; màn phụ vẫn chạy, không khởi động lại. True nếu đã nạp."""
    host, status = board_status(host)
    if status is None or not needs_deskmon(status):
        return False
    if not firmware.is_file():
        log.error("Vùng còn lại của ESP32 không có DeskMon và không thấy %s (build DeskMon ở thư mục gốc)", firmware)
        return False
    log.info("Vùng còn lại của ESP32 không có DeskMon (PC tắt sẽ không về DeskMon được), nạp lại DeskMon...")
    with firmware.open("rb") as f:
        resp = requests.post(f"http://{host}/api/deskmon", files={"firmware": f}, timeout=REFLASH_TIMEOUT_S)
    ok = resp.ok and resp.json().get("deskmon") is True
    log.log(logging.INFO if ok else logging.ERROR, "Nạp DeskMon vào vùng còn lại: %s", "xong" if ok else resp.text)
    return ok


def reflash_if_deskmon(host: str, firmware: Path) -> bool | None:
    """ESP32 đang chạy DeskMon (do PC vừa tắt) thì nạp lại firmware màn phụ.
    True: đã nạp. False: nạp lỗi. None: không cần nạp hoặc chưa hỏi được ESP32."""
    host, status = board_status(host)
    if status is None or not is_deskmon(status):
        return None
    if not firmware.is_file():
        log.error("ESP32 đang chạy DeskMon nhưng không thấy %s (build ở thư mục monitor/ trước)", firmware)
        return False
    log.info("ESP32 đang chạy DeskMon, nạp lại firmware màn phụ qua WiFi...")
    with firmware.open("rb") as f:
        resp = requests.post(f"http://{host}/update", files={"firmware": f}, timeout=REFLASH_TIMEOUT_S)
    if not resp.ok:
        log.error("Nạp firmware màn phụ lỗi: HTTP %s", resp.status_code)
        return False
    log.info("Đã nạp xong, chờ ESP32 khởi động lại")
    time.sleep(REBOOT_WAIT_S)
    return True


def to_screen(rgb: np.ndarray) -> Image.Image:
    """Thu nhỏ giữ tỉ lệ vào khung 320x240 (thừa thì viền đen)."""
    h, w = rgb.shape[:2]
    fw, fh, x, y = fit_rect(w, h, SCREEN_W, SCREEN_H)
    canvas = np.zeros((SCREEN_H, SCREEN_W, 3), np.uint8)
    canvas[y:y + fh, x:x + fw] = cv2.resize(rgb, (fw, fh), interpolation=cv2.INTER_AREA)
    return Image.fromarray(canvas)


class App:
    def __init__(self, args, watchdog: Watchdog | None = None):
        self.args = args
        self.watchdog = watchdog
        self.link = None
        self.sender: FrameSender | None = None
        self.grabber: Grabber | None = None
        self.source = None
        self.next_geometry_check = 0.0
        self.stats_since = time.monotonic()
        self.frames = 0
        self.bytes = 0
        self.next_reflash = 0.0
        self.next_deskmon_check = 0.0
        self.rate_since = time.monotonic()            # khung/giây hiện trên màn 1.54"
        self.rate_frames = 0
        self.rate_bytes = 0
        self.side: SideView | None = None
        if not args.no_side:
            self.side = SideView(args.lhm_url)
            self.side.start()

    def ensure_source(self) -> bool:
        now = time.monotonic()
        if self.grabber and now < self.next_geometry_check:
            return True
        self.next_geometry_check = now + GEOMETRY_CHECK_S
        source = choose_source(self.args)
        if source != self.source:
            if self.grabber:
                self.grabber.close()
                self.grabber = None
            self.source = source
            if source:
                self.grabber = Grabber(*source)
                log.info("Chụp vùng %s", source)
                if self.sender:
                    self.sender.forget()
        return self.grabber is not None

    def connect(self):
        a = self.args
        self.link = open_link(a.port, a.baud, a.host, a.tcp_port, DEFAULT_HOST)
        self.beat(LONG_WORK_S)                            # có thể phải nạp DeskMon vào vùng còn lại
        self.ensure_deskmon()
        self.beat()
        self.sender = FrameSender(self.link, in_flight=FRAMES_IN_FLIGHT, threaded=True, max_bytes=BYTES_IN_FLIGHT)

    def ensure_deskmon(self):
        """Mỗi lần nối lại thì kiểm tra; nạp lỗi thì chờ REFLASH_EVERY_S mới thử tiếp."""
        if time.monotonic() < self.next_deskmon_check:
            return
        self.next_deskmon_check = time.monotonic() + REFLASH_EVERY_S
        try:
            install_deskmon_if_missing(self.args.host or DEFAULT_HOST, Path(self.args.deskmon_firmware))
        except requests.RequestException as e:
            log.error("Nạp DeskMon vào vùng còn lại lỗi: %s", e)

    def disconnect(self):
        """Đóng kết nối trước: luồng ghi đang kẹt giữa chừng (WiFi khựng) sẽ thoát ra ngay."""
        link, sender = self.link, self.sender
        self.link, self.sender = None, None
        for close in (link.close if link else None, sender.close if sender else None):
            if close is None:
                continue
            try:
                close()
            except Exception as e:  # noqa: BLE001 - đang ngắt kết nối, lỗi khi đóng không quan trọng
                log.debug("Lỗi khi đóng kết nối: %s", e)

    def beat(self, grace_s: float = 0.0):
        if self.watchdog:
            self.watchdog.beat(grace_s)

    def tick(self):
        side = self.side.image() if self.side else None
        sent = self.sender.send(to_screen(self.grabber.grab()), side=side)
        self.bytes += sent
        self.frames += 1
        self.rate_bytes += sent
        self.rate_frames += 1
        span = time.monotonic() - self.rate_since
        if self.side and span >= RATE_WINDOW_S:
            self.side.set_link_stats(self.rate_frames / span, self.rate_bytes / 1024 / span,
                                     self.link.name, self.source)
            self.rate_frames, self.rate_bytes, self.rate_since = 0, 0, time.monotonic()
        elapsed = time.monotonic() - self.stats_since
        if elapsed >= STATS_EVERY_S:
            log.info("%.1f khung/giây, %.0f KB/s", self.frames / elapsed, self.bytes / 1024 / elapsed)
            self.frames, self.bytes, self.stats_since = 0, 0, time.monotonic()

    def run(self):
        period = 1.0 / max(1.0, self.args.fps)
        while True:
            self.beat()
            try:
                if not self.ensure_source():
                    time.sleep(NO_MONITOR_RETRY_S)
                    continue
                if self.link is None:
                    self.connect()
                t0 = time.monotonic()
                self.tick()
                time.sleep(max(0.0, period - (time.monotonic() - t0)))
            except KeyboardInterrupt:
                raise
            except (LinkError, OSError) as e:
                log.warning("Mất kết nối: %s, thử lại sau %.0f s", e, RETRY_S)
                self.recover()
            except Exception:                         # lỗi lạ: ghi lại rồi chạy tiếp, đừng tắt hẳn màn phụ
                log.exception("Lỗi không lường trước, thử lại sau %.0f s", RETRY_S)
                self.recover()

    def recover(self):
        self.disconnect()
        self.beat(LONG_WORK_S)
        self.maybe_reflash()
        time.sleep(RETRY_S)

    def maybe_reflash(self):
        if time.monotonic() < self.next_reflash:
            return
        try:
            done = reflash_if_deskmon(self.args.host or DEFAULT_HOST, Path(self.args.firmware))
        except requests.RequestException as e:
            log.error("Nạp firmware màn phụ lỗi: %s", e)
            done = False
        # Chỉ chờ lâu khi nạp thật sự lỗi; chưa hỏi được ESP32 thì hỏi lại sớm
        self.next_reflash = time.monotonic() + (REFLASH_EVERY_S if done is False else REFLASH_CHECK_S)


def main():
    p = argparse.ArgumentParser(description="Màn hình phụ 320x240 trên ESP32")
    p.add_argument("--list", action="store_true", help="Liệt kê các màn hình rồi thoát")
    p.add_argument("--monitor", type=int, help="Số thứ tự màn hình muốn dùng (xem --list)")
    p.add_argument("--region", help="Thử nghiệm: chụp vùng x,y,rộng,cao thay cho màn ảo")
    p.add_argument("--port", help="Cổng COM của ESP32 (mặc định: tự tìm)")
    p.add_argument("--baud", type=int, default=2000000, help="Phải khớp SERIAL_BAUD trong firmware")
    p.add_argument("--host", help="Chỉ dùng WiFi tới máy này (vd deskmon.local)")
    p.add_argument("--tcp-port", type=int, default=7778)
    p.add_argument("--fps", type=float, default=30, help="Số khung hình tối đa mỗi giây")
    p.add_argument("--no-side", action="store_true", help="Không vẽ màn 1.54\" (ESP32 tự hiện trạng thái)")
    p.add_argument("--lhm-url", default=DEFAULT_LHM_URL, help="Web server của LibreHardwareMonitor")
    p.add_argument("--firmware", default=str(FIRMWARE),
                   help="firmware.bin màn phụ, nạp lại khi ESP32 đang chạy DeskMon")
    p.add_argument("--deskmon-firmware", default=str(DESKMON_FIRMWARE),
                   help="firmware.bin DeskMon, nạp vào vùng còn lại nếu ESP32 báo thiếu")
    p.add_argument("--log-file", help="Ghi log ra file (khi chạy ẩn bằng pythonw)")
    p.add_argument("-v", "--verbose", action="store_true")
    args = p.parse_args()

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S",
                        filename=args.log_file, encoding="utf-8")
    set_dpi_aware()
    if args.list:
        print(describe(list_monitors()))
        return
    if not acquire_single_instance():
        log.error("Màn phụ đang chạy ở một bản khác, bản này thoát")
        return
    script = Path(__file__).resolve()
    watchdog = Watchdog(WATCHDOG_S, lambda: restart_self(script, f"Vòng chính đứng quá {WATCHDOG_S:.0f} giây"))
    watchdog.start()
    App(args, watchdog).run()


if __name__ == "__main__":
    main()
