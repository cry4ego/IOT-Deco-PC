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
import os
import sys
import threading
import time
from pathlib import Path

import cv2
import numpy as np
import requests
from PIL import Image

from capture import Grabber, Monitor, fit_rect, list_monitors, parse_region, pick_virtual, set_dpi_aware
from guard import Watchdog, acquire_single_instance, restart_self
import status_file as st
import usb_flash
from link import FrameSender, HandshakeError, LinkError, address_of, open_link
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
REFLASH_EVERY_S = 300.0        # kiểm tra / nạp DeskMon vào vùng dự phòng tối đa 5 phút một lần
# Nạp màn phụ lỗi (log 14/11 17:28: đứt giữa chừng lúc mới mở máy) thì thử lại dần thưa ra,
# trước đây chờ luôn 5 phút nên mở máy lên chỉ thấy DeskMon
REFLASH_BACKOFF_S = (30.0, 60.0, 120.0, 300.0)
REFLASH_CHECK_S = 10.0        # chưa nối được: cứ chừng này hỏi xem ESP32 có đang chạy DeskMon không
REFLASH_TIMEOUT_S = 180
REBOOT_WAIT_S = 20.0
RATE_WINDOW_S = 2.0
# Vòng chính kẹt quá chừng này thì tự chạy lại (< 90 giây ESP32 chờ trước khi về DeskMon)
WATCHDOG_S = 45.0
LONG_WORK_S = REFLASH_TIMEOUT_S + REBOOT_WAIT_S + 60    # nạp firmware: được phép lâu hơn
CONNECT_BUSY_S = 30.0          # nối ESP32 (thử IP cũ, hỏi tên, bắt tay) lâu nhất chừng này
# Cắm cáp USB mà bắt tay lỗi liền chừng này lần (board đang chạy DeskMon) thì nạp màn phụ qua USB:
# không cần WiFi / web DeskMon (log 15/11: web DeskMon hỏng sau một lần nạp WiFi bị ngắt giữa chừng)
USB_HANDSHAKE_FAILS = 2
USB_FLASH_EVERY_S = 60.0       # nạp qua USB xong mà vẫn không bắt tay được: không nạp liên tục
USB_BOOT_WAIT_S = 4.0


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


def install_deskmon_if_missing(host: str, firmware: Path, on_flash=None) -> bool:
    """Nạp DeskMon vào vùng firmware còn lại; màn phụ vẫn chạy, không khởi động lại. True nếu đã nạp."""
    host, status = board_status(host)
    if status is None or not needs_deskmon(status):
        return False
    if not firmware.is_file():
        log.error("Vùng còn lại của ESP32 không có DeskMon và không thấy %s (build DeskMon ở thư mục gốc)", firmware)
        return False
    log.info("Vùng còn lại của ESP32 không có DeskMon (PC tắt sẽ không về DeskMon được), nạp lại DeskMon...")
    if on_flash:
        on_flash()
    with firmware.open("rb") as f:
        resp = requests.post(f"http://{host}/api/deskmon", files={"firmware": f}, timeout=REFLASH_TIMEOUT_S)
    ok = resp.ok and resp.json().get("deskmon") is True
    log.log(logging.INFO if ok else logging.ERROR, "Nạp DeskMon vào vùng còn lại: %s", "xong" if ok else resp.text)
    return ok


def reflash_if_deskmon(host: str, firmware: Path, on_flash=None) -> bool | None:
    """ESP32 đang chạy DeskMon (do PC vừa tắt) thì nạp lại firmware màn phụ.
    True: đã nạp. False: nạp lỗi. None: không cần nạp hoặc chưa hỏi được ESP32."""
    host, status = board_status(host)
    if status is None or not is_deskmon(status):
        return None
    if not firmware.is_file():
        log.error("ESP32 đang chạy DeskMon nhưng không thấy %s (build ở thư mục monitor/ trước)", firmware)
        return False
    log.info("ESP32 đang chạy DeskMon, nạp lại firmware màn phụ qua WiFi...")
    if on_flash:
        on_flash()
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
    def __init__(self, args, watchdog: Watchdog | None = None, status: st.StatusWriter | None = None):
        self.args = args
        self.watchdog = watchdog
        self.status = status                          # cho bảng điều khiển (bang_dieu_khien.py) đọc
        self.reflash_failures = 0
        self.usb_failures = 0                         # số lần bắt tay qua USB lỗi liền nhau
        self.usb_flash_failures = 0
        self.next_usb_flash = 0.0
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
        self.usb_failures = 0
        self.beat(LONG_WORK_S)                           # có thể phải nạp DeskMon vào vùng còn lại
        self.ensure_deskmon()
        self.beat()
        self.sender = FrameSender(self.link, in_flight=FRAMES_IN_FLIGHT, threaded=True, max_bytes=BYTES_IN_FLIGHT)
        host = a.host or DEFAULT_HOST
        ip = address_of(host)
        self.report(state=st.CONNECTED, link=self.link.name, detail="", retry_at=0.0,
                    board_ip=ip if ip != host else "")

    def ensure_deskmon(self):
        """Mỗi lần nối lại thì kiểm tra; nạp lỗi thì chờ REFLASH_EVERY_S mới thử tiếp."""
        if time.monotonic() < self.next_deskmon_check:
            return
        self.next_deskmon_check = time.monotonic() + REFLASH_EVERY_S
        try:
            install_deskmon_if_missing(self.args.host or DEFAULT_HOST, Path(self.args.deskmon_firmware),
                                       on_flash=lambda: self.report(state=st.INSTALLING_DESKMON, busy_s=LONG_WORK_S))
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

    def report(self, busy_s: float = 0.0, **fields):
        if self.status:
            self.status.update(busy_s, **fields)

    def report_error(self, message: str, **fields):
        if self.status:
            self.status.error(message, **fields)

    def tick(self):
        side = self.side.image() if self.side else None
        sent = self.sender.send(to_screen(self.grabber.grab()), side=side)
        self.bytes += sent
        self.frames += 1
        self.rate_bytes += sent
        self.rate_frames += 1
        span = time.monotonic() - self.rate_since
        if span >= RATE_WINDOW_S:
            fps, kbps = self.rate_frames / span, self.rate_bytes / 1024 / span
            if self.side:
                self.side.set_link_stats(fps, kbps, self.link.name, self.source)
            self.report(state=st.CONNECTED, fps=round(fps, 1), kbps=round(kbps, 1), link=self.link.name)
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
                    self.report(state=st.NO_MONITOR, fps=0.0, kbps=0.0)
                    time.sleep(NO_MONITOR_RETRY_S)
                    continue
                if self.link is None:
                    self.report(state=self.waiting_state(), busy_s=CONNECT_BUSY_S)
                    self.connect()
                t0 = time.monotonic()
                self.tick()
                time.sleep(max(0.0, period - (time.monotonic() - t0)))
            except KeyboardInterrupt:
                raise
            except (LinkError, OSError) as e:
                log.warning("Mất kết nối: %s, thử lại sau %.0f s", e, RETRY_S)
                self.recover(f"Mất kết nối: {e}", e)
            except Exception as e:                    # lỗi lạ: ghi lại rồi chạy tiếp, đừng tắt hẳn màn phụ
                log.exception("Lỗi không lường trước, thử lại sau %.0f s", RETRY_S)
                self.recover(f"Lỗi không lường trước: {e}")

    def waiting_state(self) -> str:
        """Đang chờ tới lượt nạp lại sau một lần nạp lỗi, hay chỉ đang nối lại."""
        if self.reflash_failures and time.monotonic() < self.next_reflash:
            return st.RETRY_WAIT
        return st.CONNECTING

    def recover(self, reason: str, error: Exception | None = None):
        self.disconnect()
        self.report_error(reason, state=self.waiting_state(), fps=0.0, kbps=0.0)
        self.beat(LONG_WORK_S)
        try:
            usb_port = error.usb_port if isinstance(error, HandshakeError) else None
            if not (usb_port and self.maybe_usb_flash(usb_port)):
                self.maybe_reflash()
        except Exception:                             # nạp lại lỗi bất ngờ: ghi lại, vòng sau thử tiếp
            log.exception("Lỗi khi nạp lại firmware")
        time.sleep(RETRY_S)

    def maybe_usb_flash(self, port: str) -> bool:
        """Bắt tay qua USB lỗi: board đang chạy DeskMon (hoặc kẹt), nạp màn phụ qua USB.
        True nếu USB lo việc này (đã nạp, hoặc đợi thêm một lần bắt tay cho chắc); False thì thử WiFi."""
        self.usb_failures += 1
        if self.usb_failures < USB_HANDSHAKE_FAILS:
            return True
        if time.monotonic() < self.next_usb_flash:
            return False
        log.info("Bắt tay qua USB %s không được (board đang chạy DeskMon?), nạp lại màn phụ qua cáp USB...", port)
        self.report(state=st.REFLASHING, detail="qua cáp USB", busy_s=LONG_WORK_S)
        try:
            done = usb_flash.flash_monitor(port, Path(self.args.firmware))
        except usb_flash.UsbFlashError as e:
            wait = REFLASH_BACKOFF_S[min(self.usb_flash_failures, len(REFLASH_BACKOFF_S) - 1)]
            self.usb_flash_failures += 1
            self.next_usb_flash = time.monotonic() + wait
            log.error("Nạp màn phụ qua USB lỗi: %s", e)
            self.report_error(f"Nạp qua USB lỗi lần {self.usb_flash_failures}: {e}", state=st.RETRY_WAIT,
                              detail="", retry_at=time.time() + wait)
            return False
        log.info("Nạp qua USB xong: %s", done)
        self.usb_failures = self.usb_flash_failures = 0
        self.next_usb_flash = time.monotonic() + USB_FLASH_EVERY_S
        self.report(state=st.CONNECTING, detail="", retry_at=0.0)
        time.sleep(USB_BOOT_WAIT_S)                   # chờ firmware màn phụ khởi động
        return True

    def maybe_reflash(self):
        if time.monotonic() < self.next_reflash:
            return
        error = ""
        try:
            done = reflash_if_deskmon(self.args.host or DEFAULT_HOST, Path(self.args.firmware),
                                      on_flash=lambda: self.report(state=st.REFLASHING, detail="qua WiFi",
                                                                   busy_s=LONG_WORK_S))
        except requests.RequestException as e:
            log.error("Nạp firmware màn phụ lỗi: %s", e)
            done, error = False, str(e)
        if done is False:
            wait = REFLASH_BACKOFF_S[min(self.reflash_failures, len(REFLASH_BACKOFF_S) - 1)]
            self.reflash_failures += 1
            self.report_error(f"Nạp firmware màn phụ lỗi lần {self.reflash_failures}: {error or 'xem log'}",
                              state=st.RETRY_WAIT, retry_at=time.time() + wait)
        else:
            wait = REFLASH_CHECK_S                    # chưa hỏi được ESP32 / không cần nạp: hỏi lại sớm
            if done:
                self.reflash_failures = 0
                self.report(state=st.CONNECTING, retry_at=0.0)
        self.next_reflash = time.monotonic() + wait


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
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")   # tên màn có dấu tiếng Việt
        print(describe(list_monitors()))
        return
    if not acquire_single_instance():
        log.error("Màn phụ đang chạy ở một bản khác, bản này thoát")
        return
    # Chạy ẩn bằng pythonw thì lỗi làm dừng chương trình không hiện ở đâu cả: ghi vào log
    sys.excepthook = lambda t, e, tb: log.critical("Lỗi làm dừng chương trình", exc_info=(t, e, tb))
    threading.excepthook = lambda a: log.critical("Lỗi trong luồng %s", a.thread.name if a.thread else "?",
                                                  exc_info=(a.exc_type, a.exc_value, a.exc_traceback))
    log.info("Bắt đầu chạy (tiến trình %d)", os.getpid())
    status = st.StatusWriter()
    status.update(state=st.STARTING)
    script = Path(__file__).resolve()
    watchdog = Watchdog(WATCHDOG_S, lambda: restart_self(script, f"Vòng chính đứng quá {WATCHDOG_S:.0f} giây"))
    watchdog.start()
    App(args, watchdog, status).run()


if __name__ == "__main__":
    main()
