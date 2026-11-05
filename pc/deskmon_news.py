"""
Tin tức khi PC bật: luồng nền tải RSS (VnExpress) + ảnh bài báo, vẽ thẻ tin bằng Pillow rồi gửi
sang ESP32 như mọi khung hình khác. Khi PC tắt, ESP32 tự tải và vẽ (esp32/deskmon_esp32/news.cpp).
"""
from __future__ import annotations

import datetime as dt
import html
import io
import logging
import re
import threading
import time
import unicodedata
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Callable, Optional

import requests
from PIL import Image, ImageDraw, ImageOps

from deskmon_text import ellipsize, text_w, wrap_text

log = logging.getLogger("deskmon")

FEED_DOMESTIC = "https://vnexpress.net/rss/thoi-su.rss"
FEED_WORLD = "https://vnexpress.net/rss/the-gioi.rss"
PER_FEED = 10
TOTAL = 2 * PER_FEED
HTTP_TIMEOUT_S = 10
RETRY_S = 60.0
IDLE_S = 1.0
IMAGE_MAX_BYTES = 8 * 1024 * 1024
IMAGE_KEEP_W = 480        # đủ cắt cho cả hai màn, đỡ tốn RAM hơn giữ ảnh gốc 1200 px
TURN_ITEMS = 3            # số tin mỗi lượt (giống TURN_ITEMS trong news_view.cpp)

BG = (11, 14, 20)
TXT = (236, 239, 244)
DIM = (139, 147, 167)
C_DOMESTIC = (56, 189, 248)
C_WORLD = (251, 191, 36)

_TAG_RE = re.compile(r"<[^>]+>")
_IMG_RE = re.compile(r'<img[^>]+src="([^"]+)"')


def _clean(text: Optional[str]) -> str:
    # NFC: một số bài dùng dấu rời ("ho" + dấu huyền), vẽ ra bị lệch dấu
    return " ".join(unicodedata.normalize("NFC", html.unescape(_TAG_RE.sub(" ", text or ""))).split())


@dataclass(frozen=True)
class NewsItem:
    title: str
    summary: str
    image_url: str
    world: bool


def parse_rss(xml_bytes: bytes, world: bool, limit: int = PER_FEED) -> list[NewsItem]:
    items: list[NewsItem] = []
    for node in ET.fromstring(xml_bytes).iterfind("./channel/item"):
        title = _clean(node.findtext("title"))
        if not title:
            continue
        description = node.findtext("description") or ""
        enclosure = node.find("enclosure")
        url = enclosure.get("url", "") if enclosure is not None else ""
        if not url:
            found = _IMG_RE.search(description)
            url = html.unescape(found.group(1)) if found else ""
        items.append(NewsItem(title, _clean(description), url, world))
        if len(items) >= limit:
            break
    return items


class NewsFeed(threading.Thread):
    def __init__(self):
        super().__init__(daemon=True, name="news")
        self._lock = threading.Lock()
        self._feeds: tuple[list[NewsItem], list[NewsItem]] = ([], [])
        self._cursor = 0
        self._images: dict[str, Image.Image] = {}
        self._wanted = False
        self._domestic = True
        self._world = True
        self._refresh_s = 300.0
        self._refresh_asked = False
        self._http = requests.Session()
        self._http.headers["User-Agent"] = "DeskMon"

    def configure(self, wanted: bool, domestic: bool, world: bool, refresh_min: int):
        with self._lock:
            if domestic != self._domestic or world != self._world:
                self._refresh_asked = True
            self._wanted, self._domestic, self._world = wanted, domestic, world
            self._refresh_s = refresh_min * 60.0

    def set_items(self, domestic: list[NewsItem], world: list[NewsItem]):
        with self._lock:
            old = self._feeds
            self._feeds = (domestic, world)
            if any(new[:1] != prev[:1] for new, prev in zip(self._feeds, old)):
                self._cursor = 0        # có tin mới -> đọc lại từ tin mới nhất

    def next_item(self) -> Optional[NewsItem]:
        """Xen kẽ trong nước / thế giới, hết thì quay lại."""
        with self._lock:
            for _ in range(TOTAL):
                idx = self._cursor % TOTAL
                self._cursor += 1
                feed, pos = self._feeds[idx % 2], idx // 2
                if pos < len(feed):
                    return feed[pos]
        return None

    def image(self, url: str) -> Optional[Image.Image]:
        with self._lock:
            return self._images.get(url)

    def run(self):
        last_try = 0.0
        last_ok = False
        tried = False
        while True:
            with self._lock:
                wanted, domestic, world = self._wanted, self._domestic, self._world
                refresh_s, asked = self._refresh_s, self._refresh_asked
            wait = refresh_s if last_ok else RETRY_S
            if wanted and (asked or not tried or time.monotonic() - last_try >= wait):
                with self._lock:
                    self._refresh_asked = False
                ok = self._refresh(domestic, world)
                if ok != last_ok or not tried:
                    log.info("Tin tức: %s", "đã tải" if ok else "chưa tải được, thử lại sau 1 phút")
                last_ok, last_try, tried = ok, time.monotonic(), True
            time.sleep(IDLE_S)

    def _fetch_feed(self, url: str, world: bool, current: list[NewsItem]) -> tuple[list[NewsItem], bool]:
        try:
            resp = self._http.get(url, timeout=HTTP_TIMEOUT_S)
            resp.raise_for_status()
            return parse_rss(resp.content, world), True
        except (requests.RequestException, ET.ParseError) as e:
            log.debug("Không tải được %s: %s", url, e)
            return current, False                # giữ tin cũ, lần sau thử lại

    def _refresh(self, domestic: bool, world: bool) -> bool:
        with self._lock:
            old_domestic, old_world = self._feeds
        new_domestic, new_world = [], []
        ok_domestic = ok_world = True
        if domestic:
            new_domestic, ok_domestic = self._fetch_feed(FEED_DOMESTIC, False, old_domestic)
        if world:
            new_world, ok_world = self._fetch_feed(FEED_WORLD, True, old_world)
        self.set_items(new_domestic, new_world)
        self._load_images([i.image_url for i in new_domestic + new_world if i.image_url])
        return ok_domestic and ok_world

    def _load_images(self, urls: list[str]):
        for url in urls:
            if self.image(url) is None:
                img = self._fetch_image(url)
                if img is not None:
                    with self._lock:
                        self._images[url] = img
        keep = set(urls)
        with self._lock:
            self._images = {u: im for u, im in self._images.items() if u in keep}

    def _fetch_image(self, url: str) -> Optional[Image.Image]:
        try:
            with self._http.get(url, timeout=HTTP_TIMEOUT_S, stream=True) as resp:
                resp.raise_for_status()
                data = resp.raw.read(IMAGE_MAX_BYTES + 1, decode_content=True)
            if len(data) > IMAGE_MAX_BYTES:
                return None
            img = Image.open(io.BytesIO(data)).convert("RGB")
            img.thumbnail((IMAGE_KEEP_W, IMAGE_KEEP_W))
            return img
        except (requests.RequestException, OSError, Image.DecompressionBombError) as e:
            log.debug("Không tải được ảnh %s: %s", url, e)
            return None


class NewsTurn:
    """Lịch hiện tin trên một màn: mỗi lượt vài tin, rồi trả màn cho nội dung thường trong gap giây."""

    def __init__(self):
        self.item: Optional[NewsItem] = None
        self.shown = 0
        self.next_at = 0.0

    def tick(self, now: float, want: bool, item_sec: float, gap_sec: float,
             next_item: Callable[[], Optional[NewsItem]]) -> Optional[NewsItem]:
        if not want:
            self.item, self.shown, self.next_at = None, 0, now + gap_sec
            return None
        if now < self.next_at:
            return self.item
        if self.item is not None and gap_sec > 0 and self.shown >= TURN_ITEMS:
            self.item, self.shown, self.next_at = None, 0, now + gap_sec
            return None
        self.item = next_item()
        if self.item is None:
            return None
        self.shown += 1
        self.next_at = now + item_sec
        return self.item


class NewsRenderer:
    """Thẻ tin cho cả hai màn: nhãn + giờ, ảnh bài báo, tiêu đề, tóm tắt."""
    HEAD_H = 26
    MARGIN = 8
    IMG_GAP = 5

    def __init__(self, fonts):
        self.f = fonts
        self._cache: dict = {}

    @staticmethod
    def image_height(width: int) -> int:
        return 124 if width >= 320 else 104

    def render(self, size: tuple[int, int], item: NewsItem, image: Optional[Image.Image],
               now: dt.datetime) -> Image.Image:
        key = (item, image is not None, now.strftime("%H:%M"))
        cached = self._cache.get(size)
        if cached and cached[0] == key:
            return cached[1]
        W, H = size
        img = Image.new("RGB", size, BG)
        d = ImageDraw.Draw(img)
        self._header(d, W, item, now)
        y = self.HEAD_H
        if image is not None:
            ih = self.image_height(W)
            img.paste(ImageOps.fit(image, (W, ih), Image.LANCZOS), (0, y))
            y += ih
        self._body(d, item, W, H, y + self.IMG_GAP)
        self._cache[size] = (key, img)
        return img

    def _header(self, d, W: int, item: NewsItem, now: dt.datetime):
        label = "THẾ GIỚI" if item.world else "TRONG NƯỚC"
        font = self.f.get(13, True, label)
        pill_w = text_w(font, label) + 14
        d.rounded_rectangle([self.MARGIN, 3, self.MARGIN + pill_w, 22], radius=10,
                            fill=C_WORLD if item.world else C_DOMESTIC)
        d.text((self.MARGIN + 7, 4), label, font=font, fill=BG)
        head = f"VnExpress · {now:%H:%M}"
        font = self.f.get(13, False, head)
        d.text((W - self.MARGIN - text_w(font, head), 4), head, font=font, fill=DIM)

    def _body(self, d, item: NewsItem, W: int, H: int, y: int):
        text_width = W - 2 * self.MARGIN
        # tiêu đề: chữ to nếu đủ chỗ, không thì chữ nhỏ hơn và cắt "…" ở dòng cuối
        for size, line_h in ((19, 25), (15, 20)):
            font = self.f.get(size, True, item.title)
            lines = wrap_text(font, item.title, text_width)
            if len(lines) * line_h <= H - y:
                break
        max_lines = max(1, (H - y) // line_h)
        if len(lines) > max_lines:
            lines = lines[:max_lines - 1] + [ellipsize(font, " ".join(lines[max_lines - 1:]), text_width)]
        for line in lines:
            d.text((self.MARGIN, y), line, font=font, fill=TXT)
            y += line_h
        y += 2

        font = self.f.get(13, False, item.summary)
        line_h = 18
        room = (H - y) // line_h
        if room <= 0 or not item.summary:
            return
        lines = wrap_text(font, item.summary, text_width)
        if len(lines) > room:
            lines = lines[:room - 1] + [ellipsize(font, " ".join(lines[room - 1:]), text_width)]
        for line in lines:
            d.text((self.MARGIN, y), line, font=font, fill=DIM)
            y += line_h
