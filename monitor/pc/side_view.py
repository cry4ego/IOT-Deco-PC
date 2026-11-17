"""
Màn 1.54" khi PC bật: các trang luân phiên
  - Hiệu năng PC: CPU, GPU, RAM, VRAM, nhiệt độ, mạng. Dùng lại bộ đọc LibreHardwareMonitor và
    mẫu vẽ của DeskMon (pc/deskmon.py, chỉ import, không sửa).
  - Claude: % phiên 5 giờ, giờ hồi token, giới hạn tuần (claude_usage.py). Phiên >= 80% thì trang
    này hiện lâu gấp đôi; tới giờ hồi thì hiện "Đã hồi token" 30 giây. Không lấy được số liệu thì ẩn.
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

import claude_usage as cu

log = logging.getLogger("man_phu")

SIDE_W, SIDE_H = 240, 240
PAGE_PC, PAGE_MONITOR, PAGE_CLAUDE = 0, 1, 2
# (trang, số giây) theo tình trạng mức dùng Claude
SCHEDULES = {
    cu.MODE_OFF: ((PAGE_PC, 10.0), (PAGE_MONITOR, 5.0)),
    cu.MODE_NORMAL: ((PAGE_PC, 10.0), (PAGE_CLAUDE, 8.0), (PAGE_MONITOR, 5.0)),
    cu.MODE_HOT: ((PAGE_PC, 8.0), (PAGE_CLAUDE, 16.0), (PAGE_MONITOR, 4.0)),
}
REFRESH_S = 1.0
DESKMON_PC_DIR = Path(__file__).resolve().parents[2] / "pc"
DEFAULT_LHM_URL = "http://localhost:8085/data.json"

BG = (11, 14, 20)
TXT = (236, 239, 244)
DIM = (139, 147, 167)
ACCENT = (56, 189, 248)
GOOD = (34, 197, 94)
WARN = (245, 158, 11)
BAD = (239, 68, 68)
TRACK = (40, 46, 60)
RING_BOX = (58, 32, 182, 156)
RING_W = 13


def side_page(elapsed: float, claude_mode: str = cu.MODE_OFF) -> int:
    if claude_mode == cu.MODE_RESET:
        return PAGE_CLAUDE
    schedule = SCHEDULES.get(claude_mode, SCHEDULES[cu.MODE_OFF])
    t = elapsed % sum(secs for _, secs in schedule)
    for page, secs in schedule:
        if t < secs:
            return page
        t -= secs
    return schedule[-1][0]


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


# ---------------------------------------------------------------- trang Claude
def _pct_color(pct: Optional[float]):
    if pct is None or pct < 50:
        return GOOD
    return WARN if pct < cu.HOT_PCT else BAD


def _right(d: ImageDraw.ImageDraw, y: int, text: str, font, fill):
    d.text((SIDE_W - 12 - font.getlength(text), y), text, font=font, fill=fill)


def _claude_header(d, fonts, usage: cu.Usage, hot: bool, now: dt.datetime):
    title = "Claude · phiên 5 giờ"
    d.text((12, 8), title, font=fonts.get(15, True, title), fill=TXT)
    if hot:
        label = "sắp hết"
        f = fonts.get(12, True, label)
        w = f.getlength(label) + 16
        d.rounded_rectangle([SIDE_W - 12 - w, 8, SIDE_W - 12, 27], radius=9, fill=BAD)
        d.text((SIDE_W - 12 - w + 8, 11), label, font=f, fill=TXT)
    elif (now - usage.fetched_at).total_seconds() > cu.OLD_DATA_S:
        note = f"lúc {usage.fetched_at.astimezone(now.tzinfo):%H:%M}"   # số liệu cũ: ghi rõ của lúc nào
        _right(d, 11, note, fonts.get(12, False, note), WARN)


def _claude_ring(d, fonts, session: cu.Limit, hot: bool, now: dt.datetime):
    d.arc(RING_BOX, 0, 360, fill=TRACK, width=RING_W)
    pct = session.pct
    if pct:
        d.arc(RING_BOX, -90, -90 + 3.6 * min(pct, 100.0), fill=_pct_color(pct), width=RING_W)
    cx, cy = (RING_BOX[0] + RING_BOX[2]) / 2, (RING_BOX[1] + RING_BOX[3]) / 2
    text = "--" if pct is None else f"{pct:.0f}%"
    d.text((cx, cy - 8), text, font=fonts.get(34, True, text), fill=TXT, anchor="mm")
    sub = (f"hồi sau {cu.remaining_text(session.resets_at, now).removeprefix('còn ')}" if hot
           else cu.reset_text(session.resets_at, now)) or "chưa có phiên"
    d.text((cx, cy + 22), sub, font=fonts.get(12, False, sub), fill=WARN if hot else DIM, anchor="mm")


def _claude_week(d, fonts, usage: cu.Usage, now: dt.datetime):
    week = usage.week
    label = f"Tuần này {'--' if week.pct is None else f'{week.pct:.0f}%'}"
    d.text((12, 184), label, font=fonts.get(13, True, label), fill=TXT)
    left = cu.remaining_text(week.resets_at, now)
    _right(d, 185, left, fonts.get(12, False, left), DIM)
    x0, x1, y0, y1 = 12, SIDE_W - 12, 204, 214
    d.rounded_rectangle([x0, y0, x1, y1], radius=5, fill=TRACK)
    if week.pct:
        d.rounded_rectangle([x0, y0, x0 + max(10, (x1 - x0) * min(week.pct, 100) / 100), y1],
                            radius=5, fill=_pct_color(week.pct))
    elapsed = cu.elapsed_fraction(week, cu.WEEK_SPAN, now)
    if elapsed is not None:             # ▲ thời gian đã trôi: thanh màu nằm trái ▲ là đang dùng chậm hơn thời gian
        mx = x0 + (x1 - x0) * elapsed
        d.polygon([(mx, 217), (mx - 5, 225), (mx + 5, 225)], fill=TXT)
        cap = f"đã trôi {elapsed * 100:.0f}% thời gian"
        d.text((12, 224) if mx > SIDE_W / 2 else (SIDE_W / 2, 224), cap, font=fonts.get(11, False, cap), fill=DIM)
    if usage.model:
        name, lim = usage.model
        text = f"{name} {'--' if lim.pct is None else f'{lim.pct:.0f}%'}"
        if elapsed is None or (SIDE_W / 2 < x0 + (x1 - x0) * elapsed):
            _right(d, 224, text, fonts.get(11, False, text), DIM)


def _claude_reset(d, fonts, usage: Optional[cu.Usage]):
    d.ellipse([80, 42, 160, 122], outline=GOOD, width=6)
    d.line([(101, 83), (115, 97), (140, 70)], fill=GOOD, width=8, joint="curve")
    title = "Đã hồi token"
    d.text((SIDE_W / 2, 150), title, font=fonts.get(22, True, title), fill=TXT, anchor="mm")
    sub = "phiên 5 giờ mới bắt đầu khi bạn dùng"
    d.text((SIDE_W / 2, 180), sub, font=fonts.get(12, False, sub), fill=DIM, anchor="mm")
    if usage and usage.week.pct is not None:
        week = f"tuần này {usage.week.pct:.0f}%"
        d.text((SIDE_W / 2, 204), week, font=fonts.get(13, False, week), fill=DIM, anchor="mm")


def render_claude_page(fonts, usage: Optional[cu.Usage], mode: str, now: dt.datetime) -> Image.Image:
    img = Image.new("RGB", (SIDE_W, SIDE_H), BG)
    d = ImageDraw.Draw(img)
    if mode == cu.MODE_RESET or usage is None:
        _claude_reset(d, fonts, usage)
        return img
    hot = mode == cu.MODE_HOT
    session = cu.active(usage.session, now)
    _claude_header(d, fonts, usage, hot, now)
    _claude_ring(d, fonts, session, hot, now)
    full = cu.projected_full(session, cu.SESSION_SPAN, now)
    if full:
        line = f"Theo tốc độ này: hết lúc {full.astimezone(now.tzinfo):%H:%M}"
        _center(d, 162, line, fonts.get(13, True, line), WARN)
    else:   # đang "sắp hết" thì trong vòng đã có "hồi sau ...", dòng này ghi giờ hồi cụ thể
        line = ((cu.reset_text if hot else cu.remaining_text)(session.resets_at, now)
                or "phiên mới bắt đầu khi bạn dùng")
        _center(d, 162, line, fonts.get(13, False, line), DIM)
    _claude_week(d, fonts, usage, now)
    return img


class SideView(threading.Thread):
    """Luồng nền vẽ trang hiện tại của màn 1.54"."""

    def __init__(self, lhm_url: str = DEFAULT_LHM_URL, claude: bool = True):
        super().__init__(daemon=True, name="side")
        self.claude = cu.UsageWatcher() if claude else None
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
        mode = cu.MODE_OFF
        if self.claude:
            self.claude.tick()                           # 5 phút mới hỏi Anthropic một lần
            mode = self.claude.mode()
        page = side_page(time.monotonic() - self._t0, mode)
        if page == PAGE_PC:
            return self.renderer.render(stats, now)
        if page == PAGE_CLAUDE:
            return render_claude_page(self.fonts, self.claude.usage, mode, self.claude.wall())
        with self._lock:
            fps, kbps, link_name, source = self._link
        return render_monitor_page(self.fonts, fps, kbps, link_name, source, now.strftime("%H:%M"))
