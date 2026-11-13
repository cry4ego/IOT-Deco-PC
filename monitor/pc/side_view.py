"""
Màn 1.54" khi PC bật: 2 trang luân phiên
  - Hiệu năng PC: CPU, GPU, RAM, VRAM, nhiệt độ, mạng. Dùng lại bộ đọc LibreHardwareMonitor và
    mẫu vẽ của DeskMon (pc/deskmon.py, chỉ import, không sửa).
  - Màn hình phụ: số khung hình mỗi giây, KB/s, đường truyền, vùng đang chụp.
Vẽ ở luồng nền mỗi giây, vòng lặp chính chỉ lấy ảnh mới nhất để gửi.
"""
from __future__ import annotations

import datetime as dt
import logging
import sys
import threading
import time
from pathlib import Path
from typing import Optional

from PIL import Image, ImageDraw

log = logging.getLogger("man_phu")

SIDE_W, SIDE_H = 240, 240
PAGE_PC, PAGE_MONITOR = 0, 1
PC_PAGE_SEC = 10.0
MONITOR_PAGE_SEC = 5.0
REFRESH_S = 1.0
DESKMON_PC_DIR = Path(__file__).resolve().parents[2] / "pc"
DEFAULT_LHM_URL = "http://localhost:8085/data.json"

BG = (11, 14, 20)
TXT = (236, 239, 244)
DIM = (139, 147, 167)
ACCENT = (56, 189, 248)


def side_page(elapsed: float) -> int:
    return PAGE_PC if elapsed % (PC_PAGE_SEC + MONITOR_PAGE_SEC) < PC_PAGE_SEC else PAGE_MONITOR


def _center(d: ImageDraw.ImageDraw, y: int, text: str, font, fill):
    d.text(((SIDE_W - font.getlength(text)) / 2, y), text, font=font, fill=fill)


def render_monitor_page(fonts, fps: float, kbps: float, link_name: str,
                        source: Optional[tuple[int, int, int, int]], now_text: str) -> Image.Image:
    img = Image.new("RGB", (SIDE_W, SIDE_H), BG)
    d = ImageDraw.Draw(img)
    title = "Màn hình phụ"
    d.text((12, 8), title, font=fonts.get(17, True, title), fill=TXT)
    clock = fonts.get(15, False, now_text)
    d.text((SIDE_W - 12 - clock.getlength(now_text), 10), now_text, font=clock, fill=DIM)
    value = f"{fps:.1f}"
    _center(d, 50, value, fonts.get(64, True, value), ACCENT)
    _center(d, 128, "khung hình / giây", fonts.get(15, False, "khung hình / giây"), DIM)
    rate = f"{kbps:.0f} KB/s"
    _center(d, 156, rate, fonts.get(18, True, rate), TXT)
    small = fonts.get(13, False, link_name)
    _center(d, 190, link_name, small, DIM)
    if source:
        size = f"Màn ảo {source[2]}×{source[3]} → 320×240"
        _center(d, 212, size, fonts.get(13, False, size), DIM)
    return img


class SideView(threading.Thread):
    """Luồng nền vẽ trang hiện tại của màn 1.54"."""

    def __init__(self, lhm_url: str = DEFAULT_LHM_URL):
        super().__init__(daemon=True, name="side")
        if str(DESKMON_PC_DIR) not in sys.path:
            sys.path.insert(0, str(DESKMON_PC_DIR))
        import deskmon                                   # code DeskMon cũ, chỉ dùng lại

        self.fonts = deskmon.FontBook()
        self.collector = deskmon.StatsCollector(lhm_url)
        self.renderer = deskmon.StatsRenderer(self.fonts)
        self._lock = threading.Lock()
        self._image: Optional[Image.Image] = None
        self._link = (0.0, 0.0, "", None)
        self._t0 = time.monotonic()

    def set_link_stats(self, fps: float, kbps: float, link_name: str, source):
        with self._lock:
            self._link = (fps, kbps, link_name, source)

    def image(self) -> Optional[Image.Image]:
        with self._lock:
            return self._image

    def run(self):
        while True:
            try:
                img = self._render()
                with self._lock:
                    self._image = img
            except Exception as e:          # LHM tắt / lỗi vẽ: giữ trang cũ, lần sau thử lại
                log.debug("Không vẽ được màn 1.54\": %s", e)
            time.sleep(REFRESH_S)

    def _render(self) -> Image.Image:
        now = dt.datetime.now()
        stats = self.collector.read()                    # đọc đều để tốc độ mạng tính đúng
        if side_page(time.monotonic() - self._t0) == PAGE_PC:
            return self.renderer.render(stats, now)
        with self._lock:
            fps, kbps, link_name, source = self._link
        return render_monitor_page(self.fonts, fps, kbps, link_name, source, now.strftime("%H:%M"))
