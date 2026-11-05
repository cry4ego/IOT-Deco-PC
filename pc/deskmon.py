#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
DeskMon - phần chạy trên PC (Windows).

  Màn 2.0"  (320x240, xoay ngang): lời bài hát đang phát (Spotify / YouTube trên trình duyệt)
  Màn 1.54" (240x240)            : theo dõi PC (CPU, GPU, RAM, VRAM, nhiệt độ, mạng)

PC vẽ sẵn giao diện bằng Pillow rồi chỉ gửi vùng ảnh thay đổi sang ESP32
qua USB (Serial) hoặc WiFi (TCP). ESP32 chỉ việc giải nén và hiển thị.

Chạy nhanh:
    python deskmon.py                 # tự tìm cổng COM của ESP32, không thấy thì qua WiFi (deskmon.local)
    python deskmon.py --port COM5     # chỉ dùng cổng này
    python deskmon.py --host 192.168.1.50   # chỉ dùng WiFi
    python deskmon.py --demo          # chạy dữ liệu mẫu để thử màn hình
    python deskmon.py --preview out   # không cần ESP32, lưu ảnh PNG để xem trước
"""
from __future__ import annotations

import argparse
import asyncio
import bisect
import datetime as dt
import difflib
import io
import json
import logging
import os
import queue
import re
import select
import socket
import struct
import sys
import threading
import time
import unicodedata
from collections import deque
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Optional

import numpy as np
import psutil
import requests
from PIL import Image, ImageDraw, ImageFont, ImageOps

from deskmon_news import NewsFeed, NewsRenderer, NewsTurn
from deskmon_remote import PcSettings, RemoteSync
from deskmon_text import ellipsize, text_w, wrap_text

log = logging.getLogger("deskmon")
IS_WIN = sys.platform == "win32"

# --------------------------------------------------------------------------
# Kích thước màn hình & giao thức (phải khớp với firmware ESP32)
# --------------------------------------------------------------------------
LYRICS_W, LYRICS_H = 320, 240      # màn 2.0" xoay ngang
STATS_W, STATS_H = 240, 240        # màn 1.54"
SCREEN_LYRICS, SCREEN_STATS = 0, 1

MAGIC = b"DM"
T_PING, T_JPEG, T_QOI, T_TIME, T_BRIGHT = 0, 1, 2, 3, 4
FLAG_FULL = 0x80                   # bit đánh dấu gói thuộc một lần vẽ lại toàn màn
ACK, NAK, NEED_FULL = 0x06, 0x15, 0x07
CTRL_BYTES = (ACK, NAK, NEED_FULL)
MAX_PAYLOAD = 23000                # phải <= MAX_PAYLOAD trong firmware
FULL_REFRESH_S = 60                # cứ 60 s gửi lại toàn màn cho chắc
DEFAULT_HOST = "deskmon.local"     # tên mDNS của ESP32 (MDNS_NAME trong firmware)

# --------------------------------------------------------------------------
# Màu sắc
# --------------------------------------------------------------------------
BG = (11, 14, 20)
BG_BOTTOM = (6, 7, 11)
TXT = (236, 239, 244)
DIM = (139, 147, 167)
DIMMER = (92, 99, 116)
TRACK_BG = (34, 39, 51)
RING_BG = (30, 35, 47)
C_CPU = (56, 189, 248)
C_GPU = (244, 63, 94)
C_RAM = (167, 139, 250)
C_VRAM = (52, 211, 153)
C_WARN = (251, 191, 36)
C_HOT = (248, 113, 113)
C_SPOTIFY = (30, 215, 96)
C_YOUTUBE = (255, 48, 64)
DEFAULT_ACCENT = (124, 140, 255)

WEEKDAYS_VI = ["Thứ Hai", "Thứ Ba", "Thứ Tư", "Thứ Năm", "Thứ Sáu", "Thứ Bảy", "Chủ Nhật"]


# ==========================================================================
# Tiện ích văn bản
# ==========================================================================
_EMOJI_RE = re.compile(
    "[\U0001F000-\U0001FFFF\u2600-\u27BF\u2B00-\u2BFF\uFE00-\uFE0F\u200B-\u200F\u20E3]+"
)


def strip_emoji(s: str) -> str:
    return re.sub(r"\s{2,}", " ", _EMOJI_RE.sub("", s or "")).strip()


def norm_text(s: str) -> str:
    """Chuẩn hoá để so khớp: bỏ dấu tiếng Việt, chữ thường, chỉ giữ chữ/số."""
    s = (s or "").replace("đ", "d").replace("Đ", "D")
    s = unicodedata.normalize("NFKD", s)
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    return re.sub(r"[\W_]+", " ", s.lower()).strip()


def similar(a: str, b: str) -> float:
    a, b = norm_text(a), norm_text(b)
    if not a or not b:
        return 0.0
    if a in b or b in a:
        return max(0.85, difflib.SequenceMatcher(None, a, b).ratio())
    return difflib.SequenceMatcher(None, a, b).ratio()


def detect_script(text: str) -> str:
    for ch in text or "":
        o = ord(ch)
        if 0xAC00 <= o <= 0xD7AF or 0x1100 <= o <= 0x11FF or 0x3130 <= o <= 0x318F:
            return "kr"
        if 0x3040 <= o <= 0x30FF:
            return "jp"
        if 0x0E00 <= o <= 0x0E7F:
            return "th"
    for ch in text or "":
        o = ord(ch)
        if 0x4E00 <= o <= 0x9FFF or 0x3400 <= o <= 0x4DBF:
            return "sc"
    return "latin"


def fmt_time(sec: float) -> str:
    sec = max(0, int(sec))
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def fmt_rate(bps: float) -> str:
    if bps >= 1024 * 1024:
        return f"{bps / 1024 / 1024:.1f} MB/s"
    if bps >= 1024:
        return f"{bps / 1024:.0f} KB/s"
    return f"{bps:.0f} B/s"


# ==========================================================================
# Font (hỗ trợ tiếng Việt + Hàn/Nhật/Trung/Thái)
# ==========================================================================
class FontBook:
    CANDIDATES = {
        ("latin", False): ["segoeui.ttf", "arial.ttf", "DejaVuSans.ttf", "NotoSans-Regular.ttf"],
        ("latin", True): ["segoeuib.ttf", "arialbd.ttf", "DejaVuSans-Bold.ttf", "NotoSans-Bold.ttf"],
        ("kr", False): ["malgun.ttf", ("NotoSansCJK-Regular.ttc", "KR")],
        ("kr", True): ["malgunbd.ttf", ("NotoSansCJK-Bold.ttc", "KR")],
        ("jp", False): ["YuGothM.ttc", "meiryo.ttc", "msgothic.ttc", ("NotoSansCJK-Regular.ttc", "JP")],
        ("jp", True): ["YuGothB.ttc", "meiryob.ttc", ("NotoSansCJK-Bold.ttc", "JP")],
        ("sc", False): ["msyh.ttc", "simsun.ttc", ("NotoSansCJK-Regular.ttc", "SC")],
        ("sc", True): ["msyhbd.ttc", ("NotoSansCJK-Bold.ttc", "SC")],
        ("th", False): ["LeelawUI.ttf", "NotoSansThai-Regular.ttf"],
        ("th", True): ["LeelaUIb.ttf", "NotoSansThai-Bold.ttf"],
    }

    def __init__(self, latin_regular: Optional[str] = None, latin_bold: Optional[str] = None):
        self._index: dict[str, Path] = {}
        dirs = []
        if IS_WIN:
            dirs.append(Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts")
            if os.environ.get("LOCALAPPDATA"):
                dirs.append(Path(os.environ["LOCALAPPDATA"]) / "Microsoft" / "Windows" / "Fonts")
        else:
            dirs += [Path("/usr/share/fonts"), Path("/usr/local/share/fonts"),
                     Path.home() / ".fonts", Path("/System/Library/Fonts"), Path("/Library/Fonts")]
        for d in dirs:
            if d.is_dir():
                for p in d.rglob("*"):
                    if p.suffix.lower() in (".ttf", ".ttc", ".otf"):
                        self._index.setdefault(p.name.lower(), p)
        self._override = {False: latin_regular, True: latin_bold}
        self._cache: dict = {}
        self._resolved: dict = {}

    def _ttc_index(self, path: Path, region: str) -> int:
        # Tìm đúng font con trong file .ttc của Noto CJK theo tên (JP/KR/SC...)
        for i in range(10):
            try:
                f = ImageFont.truetype(str(path), 12, index=i)
            except OSError:
                break
            if f.getname()[0].endswith(" " + region):
                return i
        return 0

    def _resolve(self, script: str, bold: bool):
        key = (script, bold)
        if key in self._resolved:
            return self._resolved[key]
        result = None
        if script == "latin" and self._override.get(bold):
            result = (self._override[bold], 0)
        if result is None:
            for cand in self.CANDIDATES.get(key, []):
                name, region = (cand if isinstance(cand, tuple) else (cand, None))
                p = self._index.get(name.lower())
                if p:
                    result = (str(p), self._ttc_index(p, region) if region else 0)
                    break
        if result is None and script != "latin":
            result = self._resolve("latin", bold)
        if result is None and bold:
            result = self._resolve(script, False)
        self._resolved[key] = result
        return result

    def get(self, size: int, bold: bool = False, text: str = "") -> ImageFont.FreeTypeFont:
        script = detect_script(text)
        ck = (script, bold, size)
        if ck not in self._cache:
            res = self._resolve(script, bold)
            if res:
                self._cache[ck] = ImageFont.truetype(res[0], size, index=res[1])
            else:
                self._cache[ck] = ImageFont.load_default(size)
        return self._cache[ck]


# ==========================================================================
# Đồ hoạ
# ==========================================================================
def mix(c1, c2, t: float):
    return tuple(int(round(c1[i] + (c2[i] - c1[i]) * t)) for i in range(3))


def vertical_gradient(w: int, h: int, top, bottom) -> Image.Image:
    t = np.linspace(0.0, 1.0, h, dtype=np.float32)[:, None]
    top_a, bot_a = np.array(top, np.float32), np.array(bottom, np.float32)
    rows = (top_a + (bot_a - top_a) * t).round().astype(np.uint8)       # (h, 3)
    return Image.fromarray(np.repeat(rows[:, None, :], w, axis=1), "RGB")


def rounded_mask(size: tuple[int, int], radius: int) -> Image.Image:
    s = 4
    m = Image.new("L", (size[0] * s, size[1] * s), 0)
    ImageDraw.Draw(m).rounded_rectangle([0, 0, size[0] * s - 1, size[1] * s - 1], radius * s, fill=255)
    return m.resize(size, Image.LANCZOS)


def accent_from_art(img: Optional[Image.Image]):
    if img is None:
        return DEFAULT_ACCENT
    arr = np.asarray(img.convert("RGB").resize((24, 24)), np.float32).reshape(-1, 3) / 255.0
    mx, mn = arr.max(1), arr.min(1)
    sat = (mx - mn) / (mx + 1e-6)
    score = sat * mx
    if float(np.percentile(score, 90)) < 0.15:
        return DEFAULT_ACCENT                       # ảnh gần như xám
    top = arr[np.argsort(score)[-40:]].mean(0)
    top = top / max(float(top.max()), 1e-3) * 0.95  # đẩy độ sáng lên cho dễ nhìn
    c = tuple(int(v * 255) for v in top)
    return mix(c, (255, 255, 255), 0.12)


def draw_note_icon(d: ImageDraw.ImageDraw, x: int, y: int, s: int, color):
    """Biểu tượng nốt nhạc vẽ bằng hình (không phụ thuộc font)."""
    d.ellipse([x, y + s * 0.62, x + s * 0.42, y + s * 0.95], fill=color)
    d.ellipse([x + s * 0.55, y + s * 0.52, x + s * 0.97, y + s * 0.85], fill=color)
    w = max(2, s // 10)
    d.rectangle([x + s * 0.34, y + s * 0.12, x + s * 0.34 + w, y + s * 0.8], fill=color)
    d.rectangle([x + s * 0.89, y + s * 0.02, x + s * 0.89 + w, y + s * 0.7], fill=color)
    d.polygon([(x + s * 0.34, y + s * 0.12), (x + s * 0.89 + w, y + s * 0.02),
               (x + s * 0.89 + w, y + s * 0.2), (x + s * 0.34, y + s * 0.3)], fill=color)


# ==========================================================================
# Dữ liệu
# ==========================================================================
@dataclass
class NowPlaying:
    key: str
    title: str
    artist: str
    album: str
    source: str          # "Spotify" / "YouTube" / tên ứng dụng
    app_id: str
    playing: bool
    position: float
    duration: float
    art: Optional[Image.Image] = None
    timeline_ok: bool = True


@dataclass
class LyricsState:
    status: str                          # none/loading/synced/plain/instrumental/notfound/error
    times: list = field(default_factory=list)
    lines: list = field(default_factory=list)
    plain: list = field(default_factory=list)


@dataclass
class Stats:
    cpu_load: Optional[float] = None
    cpu_temp: Optional[float] = None
    gpu_load: Optional[float] = None
    gpu_temp: Optional[float] = None
    ram_used: float = 0.0
    ram_total: float = 0.0
    vram_used: Optional[float] = None
    vram_total: Optional[float] = None
    vram_pct: Optional[float] = None
    net_down: float = 0.0
    net_up: float = 0.0
    lhm_ok: bool = False


# ==========================================================================
# LRC
# ==========================================================================
_TS_RE = re.compile(r"\[(\d{1,3}):(\d{1,2}(?:[.:]\d{1,3})?)\]")
_OFFSET_RE = re.compile(r"^\s*\[offset:\s*([+-]?\d+)\s*\]", re.I)


def parse_lrc(text: str) -> tuple[list[float], list[str]]:
    offset = 0.0
    items: list[tuple[float, str]] = []
    for raw in (text or "").splitlines():
        m = _OFFSET_RE.match(raw)
        if m:
            offset = int(m.group(1)) / 1000.0
            continue
        stamps = []
        rest = raw.strip()
        while True:
            m = _TS_RE.match(rest)
            if not m:
                break
            stamps.append(int(m.group(1)) * 60 + float(m.group(2).replace(":", ".")))
            rest = rest[m.end():]
        if not stamps:
            continue
        line = rest.strip()
        for t in stamps:
            items.append((t, line))
    items.sort(key=lambda it: it[0])
    # offset dương = lời hiện sớm hơn
    return [max(0.0, t - offset) for t, _ in items], [s for _, s in items]


# ==========================================================================
# Lời bài hát (LRCLIB)
# ==========================================================================
_NOISE = re.compile(
    r"official|music\s*video|lyrics?|audio|visuali[sz]er|\bm/?v\b|\bhd\b|\b4k\b|video\s*clip|"
    r"\bclip\b|teaser|full\s*album|karaoke|vietsub|engsub|\bletra\b|\bprod\b\.?|live\s*session|"
    r"performance\s*video|special\s*video|dance\s*practice|remaster(?:ed)?",
    re.I,
)
_FEAT = re.compile(r"\s*[\(\[]?\s*(?:ft\.?|feat\.?|featuring)\s.*$", re.I)
_BRACKETS = re.compile(r"[\(\[【「『（〔][^\)\]】」』）〕]*[\)\]】」』）〕]")
_BROWSERS = ("chrome", "msedge", "edge", "firefox", "308046b0af4a39cb", "opera", "brave",
             "vivaldi", "coccoc", "browser", "arc")


def is_browser(app_id: str) -> bool:
    a = (app_id or "").lower()
    return any(b in a for b in _BROWSERS)


def source_name(app_id: str) -> str:
    a = (app_id or "").lower()
    if "spotify" in a:
        return "Spotify"
    if is_browser(a):
        return "YouTube"
    name = re.split(r"[!\\/]", app_id or "")[-1]
    return re.sub(r"\.exe$", "", name, flags=re.I) or "Media"


def _strip_noise(s: str) -> str:
    s = _NOISE.sub(" ", s)
    s = re.sub(r"\s{2,}", " ", s)
    return s.strip(" -–—|｜:/,.~")


def clean_youtube(title: str, channel: str) -> tuple[str, str]:
    """Đoán (tên bài, ca sĩ) từ tiêu đề video YouTube + tên kênh."""
    t = strip_emoji(title)
    t = re.sub(r"#\S+", " ", t)
    t = _BRACKETS.sub(lambda m: " " if (_NOISE.search(m.group(0)) or _FEAT.search(m.group(0))) else m.group(0), t)
    ch = strip_emoji(channel)
    ch = re.sub(r"\s*-\s*Topic$", "", ch, flags=re.I)
    ch = re.sub(r"(VEVO|Official|Channel|Music|TV|Entertainment)$", "", ch, flags=re.I).strip(" -_")

    # Kiểu "Ca sĩ 'Tên bài' Official MV" hoặc "Ca sĩ「Tên bài」"
    q = re.search(r"「(.+?)」|『(.+?)』|(?:^|\s)['‘\"“](.+?)['’\"”](?=\s|$|[|｜(\[])", t)
    if q:
        track = next(g for g in q.groups() if g)
        before = _strip_noise(_BRACKETS.sub(" ", t[:q.start()]))
        artist = before or ch
        return _strip_noise(track), artist

    parts = [p.strip() for p in re.split(r"\s*[|｜]\s*", t)]
    parts = [p for p in parts if p and _strip_noise(p)]
    if not parts:
        parts = [t]

    artist, track = ch, parts[0]
    if len(parts) >= 2:
        sims = [similar(p, ch) for p in parts]
        ai = int(np.argmax(sims)) if max(sims) >= 0.35 else 1
        artist = parts[ai]
        track = next(p for i, p in enumerate(parts) if i != ai)
    else:
        m = re.split(r"\s+[-–—]\s+", parts[0], maxsplit=1)
        if len(m) == 2:
            left, right = m
            if similar(right, ch) > similar(left, ch):
                track, artist = left, right
            else:
                artist, track = left, right
    track = _strip_noise(_FEAT.sub("", _BRACKETS.sub(" ", track)))
    artist = _strip_noise(artist)
    return track or strip_emoji(title), artist


class LrcLib:
    BASE = "https://lrclib.net/api"

    def __init__(self):
        self.s = requests.Session()
        self.s.headers["User-Agent"] = "DeskMon/1.0 (ESP32 desk lyrics display)"

    def get(self, track, artist, album, duration):
        params = {"track_name": track, "artist_name": artist, "duration": int(round(duration))}
        if album:
            params["album_name"] = album
        r = self.s.get(f"{self.BASE}/get", params=params, timeout=10)
        if r.status_code == 404:
            return None
        r.raise_for_status()
        return r.json()

    def search(self, **params):
        r = self.s.get(f"{self.BASE}/search", params=params, timeout=10)
        r.raise_for_status()
        data = r.json()
        return data if isinstance(data, list) else []

    def lookup(self, track: str, artist: str, album: str, duration: float, loose: bool):
        if not loose and track and artist and duration > 0:
            try:
                rec = self.get(track, artist, album, duration)
                if rec and (rec.get("syncedLyrics") or rec.get("plainLyrics") or rec.get("instrumental")):
                    return rec
            except requests.RequestException as e:
                log.debug("lrclib get lỗi: %s", e)

        cands: list[dict] = []
        if artist:
            cands += self.search(track_name=track, artist_name=artist)
        if not any(c.get("syncedLyrics") for c in cands):
            cands += self.search(q=f"{artist} {track}".strip())
        if not cands and loose:
            cands += self.search(q=track)

        max_dd = 45 if loose else 15

        def score(c):
            s = 3.0 if c.get("syncedLyrics") else (1.0 if c.get("plainLyrics") else 0.0)
            s += 2.5 * similar(c.get("trackName", ""), track)
            s += 1.5 * similar(c.get("artistName", ""), artist) if artist else 0
            if duration > 0 and c.get("duration"):
                s -= min(abs(float(c["duration"]) - duration), 60) / 8.0
            return s

        best, best_s = None, -1e9
        for c in cands:
            if not (c.get("syncedLyrics") or c.get("plainLyrics") or c.get("instrumental")):
                continue
            if similar(c.get("trackName", ""), track) < 0.5:
                continue
            if duration > 0 and c.get("duration") and abs(float(c["duration"]) - duration) > max_dd:
                continue
            sc = score(c)
            if sc > best_s:
                best, best_s = c, sc
        return best


class LyricsManager:
    NEG_TTL = 12 * 3600

    def __init__(self, cache_dir: Path, lead: float):
        self.lead = lead
        self.api = LrcLib()
        self.cache_path = cache_dir / "lyrics_cache.json"
        self.lock = threading.Lock()
        self.mem: dict[str, LyricsState] = {}
        self.inflight: set[str] = set()
        self.first_seen: dict[str, float] = {}
        try:
            self.disk = json.loads(self.cache_path.read_text("utf-8"))
        except Exception:
            self.disk = {}

    def preload(self, key: str, state: LyricsState):
        self.mem[key] = state

    @staticmethod
    def _state_from(rec: Optional[dict]) -> LyricsState:
        if not rec:
            return LyricsState("notfound")
        if rec.get("instrumental"):
            return LyricsState("instrumental")
        if rec.get("syncedLyrics"):
            times, lines = parse_lrc(rec["syncedLyrics"])
            if times:
                return LyricsState("synced", times, lines)
        if rec.get("plainLyrics"):
            plain = [l.strip() for l in rec["plainLyrics"].splitlines()]
            plain = [l for l in plain if l]
            if plain:
                return LyricsState("plain", plain=plain)
        return LyricsState("notfound")

    def state_for(self, np_: Optional[NowPlaying]) -> LyricsState:
        if np_ is None:
            return LyricsState("none")
        k = np_.key
        with self.lock:
            st = self.mem.get(k)
            if st is not None:
                return st
            ent = self.disk.get(k)
            if ent and (ent.get("rec") or time.time() - ent.get("ts", 0) < self.NEG_TTL):
                st = self._state_from(ent.get("rec"))
                self.mem[k] = st
                return st
            first = self.first_seen.setdefault(k, time.monotonic())
            # đợi metadata ổn định một chút rồi mới tìm (tránh tìm nhầm khi đang đổi bài)
            if time.monotonic() - first >= 0.8 and k not in self.inflight:
                self.inflight.add(k)
                threading.Thread(target=self._fetch, args=(np_,), daemon=True).start()
        return LyricsState("loading")

    def _fetch(self, np_: NowPlaying):
        k = np_.key
        try:
            if np_.source == "YouTube" and not np_.album:
                track, artist = clean_youtube(np_.title, np_.artist)
                loose = True
            else:
                track, artist = strip_emoji(np_.title), strip_emoji(np_.artist)
                loose = np_.source == "YouTube"
            log.info("Tìm lời: '%s' - '%s' (%s)", track, artist, np_.source)
            dur = np_.duration if np_.timeline_ok else 0
            rec = self.api.lookup(track, artist, np_.album, dur, loose)
            if rec is None and _FEAT.search(track):
                rec = self.api.lookup(_FEAT.sub("", track), artist, "", dur, loose)
            if rec is None and loose and artist:
                # tiêu đề YouTube có thể ghi ngược "Tên bài - Ca sĩ"
                rec = self.api.lookup(artist, track, "", dur, loose)
            st = self._state_from(rec)
            log.info("Kết quả lời: %s", st.status)
            slim = None
            if rec:
                slim = {k2: rec.get(k2) for k2 in ("trackName", "artistName", "duration",
                                                   "instrumental", "syncedLyrics", "plainLyrics")}
            with self.lock:
                self.mem[k] = st
                self.disk[k] = {"ts": time.time(), "rec": slim}
                if len(self.disk) > 800:
                    for old in sorted(self.disk, key=lambda x: self.disk[x].get("ts", 0))[:100]:
                        self.disk.pop(old, None)
                self._save()
        except requests.RequestException as e:
            log.warning("Không kết nối được LRCLIB: %s", e)
            with self.lock:
                self.mem[k] = LyricsState("error")
            # thử lại sau 30 s
            threading.Timer(30, self._forget, args=(k,)).start()
        except Exception:
            log.exception("Lỗi khi tìm lời")
            with self.lock:
                self.mem[k] = LyricsState("error")
        finally:
            with self.lock:
                self.inflight.discard(k)

    def _forget(self, k):
        with self.lock:
            if self.mem.get(k) and self.mem[k].status == "error":
                self.mem.pop(k, None)
                self.first_seen.pop(k, None)

    def _save(self):
        try:
            self.cache_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.cache_path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.disk, ensure_ascii=False), "utf-8")
            tmp.replace(self.cache_path)
        except OSError as e:
            log.debug("Không lưu được cache lời: %s", e)


# ==========================================================================
# Nhạc đang phát (Windows Media Session: Spotify, Chrome/Edge/Firefox...)
# ==========================================================================
WINRT_OK = False
if IS_WIN:
    try:
        from winrt.windows.media.control import (  # type: ignore
            GlobalSystemMediaTransportControlsSessionManager as _SessionManager)
        from winrt.windows.storage.streams import (  # type: ignore
            Buffer as _Buffer, InputStreamOptions as _InputStreamOptions, DataReader as _DataReader)
        WINRT_OK = True
    except ImportError:
        try:
            from winsdk.windows.media.control import (  # type: ignore
                GlobalSystemMediaTransportControlsSessionManager as _SessionManager)
            from winsdk.windows.storage.streams import (  # type: ignore
                Buffer as _Buffer, InputStreamOptions as _InputStreamOptions, DataReader as _DataReader)
            WINRT_OK = True
        except ImportError:
            pass

PLAYING = 4   # GlobalSystemMediaTransportControlsSessionPlaybackStatus.PLAYING


class MediaWatcher(threading.Thread):
    def __init__(self):
        super().__init__(daemon=True, name="media")
        self._lock = threading.Lock()
        self._np: Optional[NowPlaying] = None
        self._anchor = (0.0, time.monotonic())
        self._last_sig = None
        self._last_key = None
        self._last_playing = None
        self._art_key = None
        self._art: Optional[Image.Image] = None
        self._art_tries = 0
        self._cmds: queue.SimpleQueue = queue.SimpleQueue()

    # ---- API cho luồng chính ----
    def request(self, cmd: str):
        """Lệnh nhạc từ web (gọi từ luồng khác): play_pause / next / prev."""
        self._cmds.put(cmd)

    def snapshot(self) -> Optional[NowPlaying]:
        with self._lock:
            np_ = self._np
            if np_ is None:
                return None
            pos = self._anchor[0]
            if np_.playing:
                pos += time.monotonic() - self._anchor[1]
            if np_.duration > 0:
                pos = min(pos, np_.duration)
            return replace(np_, position=max(0.0, pos))

    # ---- luồng nền ----
    def run(self):
        if not WINRT_OK:
            log.warning("Không có thư viện winrt -> không đọc được nhạc đang phát (chỉ chạy trên Windows).")
            return
        try:
            asyncio.run(self._main())
        except Exception:
            log.exception("MediaWatcher dừng vì lỗi")

    async def _main(self):
        mgr = await _SessionManager.request_async()
        while True:
            try:
                await self._run_commands(mgr)
                await self._poll(mgr)
            except Exception as e:
                log.debug("media poll lỗi: %s", e)
            await asyncio.sleep(0.4)

    async def _run_commands(self, mgr):
        while not self._cmds.empty():
            cmd = self._cmds.get_nowait()
            s = self._pick(mgr)
            if s is None:
                log.info("Không có ứng dụng nhạc nào để điều khiển (%s)", cmd)
                continue
            action = {"play_pause": s.try_toggle_play_pause_async,
                      "next": s.try_skip_next_async,
                      "prev": s.try_skip_previous_async}.get(cmd)
            if action is not None:
                await action()

    @staticmethod
    def _is_playing(s) -> bool:
        try:
            return int(s.get_playback_info().playback_status) == PLAYING
        except Exception:
            return False

    def _pick(self, mgr):
        try:
            sessions = list(mgr.get_sessions())
        except Exception:
            sessions = []
        playing = [s for s in sessions if self._is_playing(s)]

        def prio(s):
            app = (s.source_app_user_model_id or "").lower()
            return 0 if "spotify" in app else (1 if is_browser(app) else 2)

        if playing:
            return sorted(playing, key=prio)[0]
        return mgr.get_current_session()

    async def _read_thumb(self, ref) -> Optional[Image.Image]:
        if ref is None:
            return None
        try:
            stream = await ref.open_read_async()
            size = int(stream.size)
            if size <= 0 or size > 8 * 1024 * 1024:
                return None
            buf = _Buffer(size)
            res = await stream.read_async(buf, size, _InputStreamOptions.READ_AHEAD)
            res = res if res is not None else buf
            n = int(res.length)
            try:
                data = bytes(memoryview(res))[:n]
            except TypeError:
                reader = _DataReader.from_buffer(res)
                arr = bytearray(n)
                reader.read_bytes(arr)
                data = bytes(arr)
            img = Image.open(io.BytesIO(data))
            img.load()
            return img.convert("RGB")
        except Exception as e:
            log.debug("Không đọc được ảnh bìa: %s", e)
            return None

    async def _poll(self, mgr):
        s = self._pick(mgr)
        if s is None:
            with self._lock:
                self._np = None
            return
        app = s.source_app_user_model_id or ""
        props = await s.try_get_media_properties_async()
        title = (props.title or "").strip()
        if not title:
            with self._lock:
                self._np = None
            return
        artist = (props.artist or "").strip()
        album = (props.album_title or "").strip()
        playing = self._is_playing(s)

        tl = s.get_timeline_properties()
        pos = tl.position.total_seconds()
        end = tl.end_time.total_seconds()
        timeline_ok = end > 0
        upd = tl.last_updated_time
        pos_now = pos
        try:
            if playing and timeline_ok and upd is not None and upd.year > 2000:
                if upd.tzinfo is None:
                    upd = upd.replace(tzinfo=dt.timezone.utc)
                age = (dt.datetime.now(dt.timezone.utc) - upd).total_seconds()
                if 0 <= age <= end + 5:
                    pos_now = pos + age
        except Exception:
            pass

        key = f"{source_name(app)}|{artist}|{title}"
        if key != self._art_key:
            self._art_key, self._art, self._art_tries = key, None, 0
        if self._art is None and self._art_tries < 12:
            self._art_tries += 1
            self._art = await self._read_thumb(props.thumbnail)

        with self._lock:
            mono = time.monotonic()
            track_changed = key != self._last_key
            status_changed = playing != self._last_playing
            if timeline_ok:
                sig = (round(pos, 3), upd)
                if track_changed or status_changed or sig != self._last_sig:
                    self._anchor = (pos_now, mono)
                    self._last_sig = sig
            else:
                # app không báo vị trí: tự đếm từ lúc đổi bài
                if track_changed:
                    self._anchor = (0.0, mono)
                elif status_changed:
                    cur = self._anchor[0] + ((mono - self._anchor[1]) if self._last_playing else 0)
                    self._anchor = (cur, mono)
            self._last_key, self._last_playing = key, playing
            self._np = NowPlaying(key=key, title=title, artist=artist, album=album,
                                  source=source_name(app), app_id=app, playing=playing,
                                  position=0.0, duration=end if timeline_ok else 0.0,
                                  art=self._art, timeline_ok=timeline_ok)


class DemoMedia:
    """Dữ liệu giả để thử màn hình (lời mẫu tự viết)."""
    LRC = "\n".join([
        "[00:00.50]Đây là dòng lời mẫu số một",
        "[00:04.00]Dòng thứ hai dài hơn một chút để thử xuống dòng trên màn hình nhỏ",
        "[00:09.00]",
        "[00:11.00]Ba giây nhạc dạo rồi tới đoạn tiếp theo",
        "[00:15.00]DeskMon hiển thị lời theo thời gian thực",
        "[00:19.50]Tiếng Việt có dấu: ắ ằ ẳ ẵ ặ ơ ư đ",
        "[00:24.00]English line for testing the layout",
        "[00:28.00]Kết thúc vòng demo, quay lại từ đầu",
    ])
    KEY = "Demo|DeskMon|Bài hát mẫu"

    def __init__(self):
        self.t0 = time.monotonic()
        w = 200
        art = vertical_gradient(w, w, (255, 94, 98), (60, 40, 160))
        d = ImageDraw.Draw(art)
        d.ellipse([50, 50, 150, 150], outline=(255, 255, 255), width=10)
        d.ellipse([90, 90, 110, 110], fill=(255, 255, 255))
        self.art = art

    def start(self):
        pass

    def request(self, cmd: str):
        log.info("Demo: bỏ qua lệnh nhạc %s", cmd)

    def snapshot(self) -> NowPlaying:
        dur = 32.0
        pos = (time.monotonic() - self.t0) % dur
        return NowPlaying(key=self.KEY, title="Bài hát mẫu (Demo)", artist="DeskMon",
                          album="", source="Spotify", app_id="Spotify.exe", playing=True,
                          position=pos, duration=dur, art=self.art)


# ==========================================================================
# Số liệu PC (psutil + LibreHardwareMonitor)
# ==========================================================================
_NUM_RE = re.compile(r"[-+]?\d+(?:[.,]\d+)?")


def _parse_value(v) -> tuple[Optional[float], str]:
    if v is None:
        return None, ""
    if isinstance(v, (int, float)):
        return float(v), ""
    m = _NUM_RE.search(str(v))
    if not m:
        return None, ""
    return float(m.group(0).replace(",", ".")), str(v)[m.end():].strip()


def _hw_kind(sid: str, hw_name: str, image: str) -> str:
    s, n, im = sid.lower(), hw_name.lower(), (image or "").lower()
    if s.startswith(("/intelcpu", "/amdcpu", "/cpu")):
        return "cpu"
    if s.startswith(("/gpu-nvidia", "/nvidiagpu", "/gpu-amd", "/atigpu")):
        return "gpu"
    if s.startswith("/gpu-intel"):
        return "igpu"
    if s:
        return "other"
    if any(k in n for k in ("radeon rx", "geforce", "rtx", "gtx", "radeon pro", "arc a")):
        return "gpu"
    if any(k in n for k in ("uhd graphics", "iris", "radeon graphics", "radeon(tm) graphics")):
        return "igpu"
    if "cpu" in im or "ryzen" in n or ("intel" in n and "core" in n):
        return "cpu"
    if "nvidia" in im or "ati" in im:
        return "gpu"
    return "other"


_GROUP_TYPES = {"temperatures": "temperature", "load": "load", "data": "data",
                "smalldata": "smalldata", "clocks": "clock", "powers": "power", "throughput": "throughput"}


def parse_lhm(data: dict) -> dict:
    sensors = []

    def walk(node, path, hw_node):
        children = node.get("Children") or []
        depth = len(path)
        if depth == 2:                       # Sensor > Máy > [Phần cứng]
            hw_node = node
        if not children and node.get("Value") not in (None, ""):
            sid = (node.get("SensorId") or "").lower()
            typ = (node.get("Type") or "").lower()
            if not typ and sid:
                seg = sid.strip("/").split("/")
                typ = seg[-2] if len(seg) >= 2 else ""
            if not typ and path:
                typ = _GROUP_TYPES.get(path[-1].replace(" ", "").lower(), "")
            hw_name = (hw_node or {}).get("Text", "")
            kind = _hw_kind(sid, hw_name, (hw_node or {}).get("ImageURL", ""))
            val, unit = _parse_value(node.get("Value"))
            if val is not None:
                sensors.append((kind, typ, node.get("Text", ""), val, unit))
        for c in children:
            walk(c, path + [node.get("Text", "")], hw_node)

    walk(data, [], None)

    def pick(kinds, typ, names, fallback_first=True):
        for kind in kinds:
            for name in names:
                for k, t, txt, v, u in sensors:
                    if k == kind and t == typ and txt.lower() == name.lower():
                        return v, u
        if fallback_first:
            for kind in kinds:
                for k, t, txt, v, u in sensors:
                    if k == kind and t == typ:
                        return v, u
        return None, ""

    gpu_kinds = ("gpu", "igpu")
    out = {}
    out["cpu_temp"] = pick(("cpu",), "temperature",
                           ["CPU Package", "Core (Tctl/Tdie)", "CPU (Tctl/Tdie)", "Core Max", "Core Average"])[0]
    out["cpu_load"] = pick(("cpu",), "load", ["CPU Total"], False)[0]
    out["gpu_temp"] = pick(gpu_kinds, "temperature", ["GPU Core", "GPU Temperature"])[0]
    out["gpu_load"] = pick(gpu_kinds, "load", ["GPU Core", "D3D 3D"])[0]

    def to_gb(vu):
        v, u = vu
        if v is None:
            return None
        u = u.upper()
        if u.startswith("MB"):
            return v / 1024.0
        if u.startswith("KB"):
            return v / 1024.0 / 1024.0
        return v

    out["vram_used"] = to_gb(pick(gpu_kinds, "smalldata", ["GPU Memory Used", "D3D Dedicated Memory Used"], False))
    out["vram_total"] = to_gb(pick(gpu_kinds, "smalldata", ["GPU Memory Total"], False))
    out["vram_pct"] = pick(gpu_kinds, "load", ["GPU Memory"], False)[0]
    return out


class StatsCollector:
    def __init__(self, lhm_url: str):
        self.lhm_url = lhm_url
        self.http = requests.Session()
        self.lhm_ok = False
        self.lhm_next_try = 0.0
        self._warned = False
        psutil.cpu_percent(None)
        self._net = (psutil.net_io_counters(), time.monotonic())

    def _read_lhm(self) -> dict:
        now = time.monotonic()
        if not self.lhm_ok and now < self.lhm_next_try:
            return {}
        try:
            r = self.http.get(self.lhm_url, timeout=0.6)
            r.raise_for_status()
            data = parse_lhm(r.json())
            if not self.lhm_ok:
                log.info("Đã kết nối LibreHardwareMonitor.")
            self.lhm_ok = True
            return data
        except Exception:
            if self.lhm_ok or not self._warned:
                log.warning("Không đọc được LibreHardwareMonitor tại %s "
                            "(cần mở LHM + bật Options > Remote Web Server > Run). "
                            "Nhiệt độ/GPU sẽ hiện '--'.", self.lhm_url)
                self._warned = True
            self.lhm_ok = False
            self.lhm_next_try = now + 10
            return {}

    def read(self) -> Stats:
        st = Stats()
        st.cpu_load = psutil.cpu_percent(None)
        vm = psutil.virtual_memory()
        gb = 1024 ** 3
        st.ram_total = vm.total / gb
        st.ram_used = (vm.total - vm.available) / gb

        net, t = psutil.net_io_counters(), time.monotonic()
        pnet, pt = self._net
        dtm = max(1e-3, t - pt)
        st.net_down = max(0.0, (net.bytes_recv - pnet.bytes_recv) / dtm)
        st.net_up = max(0.0, (net.bytes_sent - pnet.bytes_sent) / dtm)
        self._net = (net, t)

        lhm = self._read_lhm()
        st.lhm_ok = self.lhm_ok
        if lhm.get("cpu_load") is not None:
            st.cpu_load = lhm["cpu_load"]
        st.cpu_temp = lhm.get("cpu_temp")
        st.gpu_temp = lhm.get("gpu_temp")
        st.gpu_load = lhm.get("gpu_load")
        st.vram_used = lhm.get("vram_used")
        st.vram_total = lhm.get("vram_total")
        if st.vram_used is not None and st.vram_total:
            st.vram_pct = 100.0 * st.vram_used / st.vram_total
        else:
            st.vram_pct = lhm.get("vram_pct")
        return st


class DemoStats:
    def __init__(self):
        self.t0 = time.monotonic()

    def read(self) -> Stats:
        t = time.monotonic() - self.t0
        w = lambda a, b, f: a + (b - a) * (0.5 + 0.5 * np.sin(t * f))
        return Stats(cpu_load=w(8, 72, 0.7), cpu_temp=w(42, 81, 0.3), gpu_load=w(20, 98, 0.5),
                     gpu_temp=w(48, 88, 0.25), ram_used=w(7.5, 11.2, 0.1), ram_total=15.8,
                     vram_used=w(2.1, 6.4, 0.2), vram_total=8.0, vram_pct=w(26, 80, 0.2),
                     net_down=w(2e3, 4.5e6, 0.9), net_up=w(1e3, 3.2e5, 0.6), lhm_ok=True)


# ==========================================================================
# Vẽ màn hình lời bài hát (320x240)
# ==========================================================================
NO_LYRICS_STATES = ("loading", "notfound", "instrumental", "error", "none")


class LyricsRenderer:
    W, H = LYRICS_W, LYRICS_H

    def __init__(self, fonts: FontBook, lead: float):
        self.f = fonts
        self.lead = lead
        self._art_cache = (None, None, None)       # (key, art image, mask)
        self._big_art_cache = (None, None, None)   # ảnh bìa lớn khi không có lời
        self._accent_cache = (None, DEFAULT_ACCENT)
        self._bg_cache: dict = {}
        self._names_cache = (None, ("", ""))

    def _bg(self, accent):
        if accent not in self._bg_cache:
            self._bg_cache.clear()
            self._bg_cache[accent] = vertical_gradient(self.W, self.H, mix(BG, accent, 0.22), BG_BOTTOM)
        return self._bg_cache[accent].copy()

    def _accent(self, np_: NowPlaying):
        if self._accent_cache[0] != (np_.key, np_.art is not None):
            self._accent_cache = ((np_.key, np_.art is not None), accent_from_art(np_.art))
        return self._accent_cache[1]

    def _display_names(self, np_: NowPlaying) -> tuple[str, str]:
        """Video YouTube: hiện tên bài/ca sĩ đã làm gọn thay vì tiêu đề video dài."""
        if np_.source == "YouTube" and not np_.album:
            if self._names_cache[0] != np_.key:
                self._names_cache = (np_.key, clean_youtube(np_.title, np_.artist))
            return self._names_cache[1]
        return strip_emoji(np_.title), strip_emoji(np_.artist)

    def _text_center(self, d, y, text, font, fill, max_w=None):
        if max_w:
            text = ellipsize(font, text, max_w)
        w = text_w(font, text)
        d.text(((self.W - w) / 2, y), text, font=font, fill=fill)

    def render(self, np_: Optional[NowPlaying], lyr: LyricsState, now: dt.datetime) -> Image.Image:
        if np_ is None:
            return self._render_idle(now)
        accent = self._accent(np_)
        if lyr.status in NO_LYRICS_STATES:
            return self._render_now_playing(np_, lyr, accent)
        img = self._bg(accent)
        d = ImageDraw.Draw(img)

        # ---- đầu: ảnh bìa + tên bài + ca sĩ ----
        art_sz, ax, ay = 52, 10, 10
        if np_.art is not None:
            if self._art_cache[0] != np_.key:
                art = ImageOps.fit(np_.art, (art_sz, art_sz), Image.LANCZOS)
                self._art_cache = (np_.key, art, rounded_mask((art_sz, art_sz), 9))
            img.paste(self._art_cache[1], (ax, ay), self._art_cache[2])
        else:
            box = Image.new("RGB", (art_sz, art_sz), mix(BG, accent, 0.35))
            img.paste(box, (ax, ay), rounded_mask((art_sz, art_sz), 9))
            draw_note_icon(d, ax + 13, ay + 12, 26, TXT)

        tx, tmax = ax + art_sz + 10, self.W - (ax + art_sz + 10) - 10
        title, artist = self._display_names(np_)
        ft = self.f.get(17, True, title)
        d.text((tx, ay - 1), ellipsize(ft, title, tmax), font=ft, fill=TXT)

        src_col = C_SPOTIFY if np_.source == "Spotify" else (C_YOUTUBE if np_.source == "YouTube" else accent)
        d.ellipse([tx, ay + 29, tx + 8, ay + 37], fill=src_col)
        sub = f"{np_.source} · {artist}" if artist else np_.source
        fs = self.f.get(13, False, sub)
        d.text((tx + 13, ay + 25), ellipsize(fs, sub, tmax - 13), font=fs, fill=DIM)

        # ---- vùng lời ----
        self._render_lyrics(d, np_, lyr, accent)
        self._render_progress(d, np_, accent, "lời chưa đồng bộ" if lyr.status == "plain" else None)
        return img

    def _render_now_playing(self, np_: NowPlaying, lyr: LyricsState, accent) -> Image.Image:
        """Không có lời: ảnh bìa lớn ở giữa + tên bài + ca sĩ."""
        img = self._bg(accent)
        d = ImageDraw.Draw(img)
        art_sz, ay = 132, 12
        ax = (self.W - art_sz) // 2
        if np_.art is not None:
            if self._big_art_cache[0] != np_.key:
                art = ImageOps.fit(np_.art, (art_sz, art_sz), Image.LANCZOS)
                self._big_art_cache = (np_.key, art, rounded_mask((art_sz, art_sz), 16))
            img.paste(self._big_art_cache[1], (ax, ay), self._big_art_cache[2])
        else:
            box = Image.new("RGB", (art_sz, art_sz), mix(BG, accent, 0.35))
            img.paste(box, (ax, ay), rounded_mask((art_sz, art_sz), 16))
            draw_note_icon(d, ax + 40, ay + 36, 52, TXT)

        title, artist = self._display_names(np_)
        max_w = self.W - 24
        self._text_center(d, ay + art_sz + 7, title, self.f.get(19, True, title), TXT, max_w)
        sub = f"{artist} · {np_.source}" if artist else np_.source
        self._text_center(d, ay + art_sz + 33, sub, self.f.get(14, False, sub), DIM, max_w)
        hint = {"loading": "Đang tìm lời…", "instrumental": "Nhạc không lời"}.get(lyr.status)
        self._render_progress(d, np_, accent, hint)
        return img

    def _render_progress(self, d, np_: NowPlaying, accent, hint: Optional[str]):
        bx0, bx1, by = 12, self.W - 12, 212
        d.rounded_rectangle([bx0, by, bx1, by + 4], 2, fill=TRACK_BG)
        if np_.duration > 0:
            frac = min(1.0, np_.position / np_.duration)
            fx = bx0 + int((bx1 - bx0) * frac)
            if fx > bx0 + 3:
                d.rounded_rectangle([bx0, by, fx, by + 4], 2, fill=accent)
            d.ellipse([fx - 4, by - 2, fx + 4, by + 6], fill=TXT)
        fsm = self.f.get(11)
        d.text((bx0, by + 10), fmt_time(np_.position), font=fsm, fill=DIM)
        if np_.duration > 0:
            tot = fmt_time(np_.duration)
            d.text((bx1 - text_w(fsm, tot), by + 10), tot, font=fsm, fill=DIM)
        if not np_.playing:                         # biểu tượng tạm dừng
            cx = self.W // 2
            d.rectangle([cx - 6, by + 11, cx - 3, by + 22], fill=DIM)
            d.rectangle([cx + 3, by + 11, cx + 6, by + 22], fill=DIM)
        elif hint:
            self._text_center(d, by + 10, hint, fsm, DIMMER)

    def _render_lyrics(self, d, np_: NowPlaying, lyr: LyricsState, accent):
        top = 72
        max_w = self.W - 24
        pos = np_.position + self.lead
        if lyr.status == "synced":
            idx = bisect.bisect_right(lyr.times, pos) - 1
            lines = lyr.lines
        else:  # plain: ước lượng theo tỉ lệ thời gian
            lines = lyr.plain
            idx = int(len(lines) * (np_.position / np_.duration)) if np_.duration > 0 else 0
            idx = min(max(idx, 0), len(lines) - 1)

        def prev_text(i):
            while i >= 0 and not lines[i]:
                i -= 1
            return lines[i] if i >= 0 else None

        def next_texts(i, n):
            out = []
            while i < len(lines) and len(out) < n:
                if lines[i]:
                    out.append(lines[i])
                i += 1
            return out

        if idx < 0:                       # chưa tới dòng đầu tiên
            prev_l, cur_l, nxt = None, "", next_texts(0, 2)
        else:
            prev_l, cur_l, nxt = prev_text(idx - 1), lines[idx], next_texts(idx + 1, 2)
        if not cur_l:
            cur_l = "• • •"               # đoạn nhạc dạo / gian tấu

        if prev_l:
            f = self.f.get(13, False, prev_l)
            self._text_center(d, top, prev_l, f, DIMMER, max_w)

        # dòng hiện tại: tối đa 3 dòng, quá dài thì giảm cỡ chữ
        for size, max_lines, lh in ((20, 3, 24), (16, 4, 18)):
            fc = self.f.get(size, True, cur_l)
            wrapped = wrap_text(fc, cur_l, max_w)
            if len(wrapped) <= max_lines:
                break
        wrapped = wrapped[:max_lines]
        if text_w(fc, wrapped[-1]) > max_w:
            wrapped[-1] = ellipsize(fc, wrapped[-1], max_w)
        block_top, block_bot = top + 20, top + 96          # 92..168
        block_h = lh * len(wrapped)
        y = block_top + max(0, (block_bot - block_top - block_h) // 2)
        cur_col = mix(TXT, accent, 0.15)
        for ln in wrapped:
            self._text_center(d, y, ln, fc, cur_col)
            y += lh

        y_next = top + 99                                   # 171
        if nxt:
            f = self.f.get(14, False, nxt[0])
            self._text_center(d, y_next, nxt[0], f, DIM, max_w)
        if len(nxt) > 1 and len(wrapped) < 3:
            f = self.f.get(13, False, nxt[1])
            self._text_center(d, y_next + 18, nxt[1], f, DIMMER, max_w)

    def _render_idle(self, now: dt.datetime) -> Image.Image:
        img = self._bg(DEFAULT_ACCENT)
        d = ImageDraw.Draw(img)
        hhmm = now.strftime("%H:%M")
        f = self.f.get(76, True)
        self._text_center(d, 34, hhmm, f, TXT)
        date = f"{WEEKDAYS_VI[now.weekday()]}, {now.strftime('%d/%m/%Y')}"
        self._text_center(d, 134, date, self.f.get(17, False, date), DIM)
        draw_note_icon(d, self.W // 2 - 9, 182, 18, DIMMER)
        self._text_center(d, 206, "Không có nhạc đang phát", self.f.get(13), DIMMER)
        return img


# ==========================================================================
# Vẽ màn hình theo dõi PC (240x240, vẽ ở 2x rồi thu nhỏ cho mượt)
# ==========================================================================
class StatsRenderer:
    S = 2

    def __init__(self, fonts: FontBook):
        self.f = fonts

    def _ft(self, size, bold=False):
        return self.f.get(size * self.S, bold)

    def _center(self, d, cx, y, text, font, fill):
        d.text((cx - text_w(font, text) / 2, y), text, font=font, fill=fill)

    @staticmethod
    def _temp_color(t):
        if t is None:
            return DIM
        if t >= 85:
            return C_HOT
        if t >= 72:
            return C_WARN
        return TXT

    def _gauge(self, d, cx, cy, r, width, pct, color, label, sub, sub_color):
        S = self.S
        box = [cx - r, cy - r, cx + r, cy + r]
        start, sweep = 135, 270
        d.arc(box, start, start + sweep, fill=RING_BG, width=width)
        rr = r - width / 2

        def cap(angle, col):
            a = np.deg2rad(angle)
            x, y = cx + rr * np.cos(a), cy + rr * np.sin(a)
            d.ellipse([x - width / 2, y - width / 2, x + width / 2, y + width / 2], fill=col)

        cap(start, RING_BG)
        cap(start + sweep, RING_BG)
        if pct is not None:
            p = max(0.0, min(100.0, pct)) / 100.0
            if p > 0.005:
                end = start + sweep * p
                d.arc(box, start, end, fill=color, width=width)
                cap(start, color)
                cap(end, color)
            val = f"{pct:.0f}%"
        else:
            val = "--"
        fv = self._ft(17, True)
        bb = fv.getbbox(val)
        self._center(d, cx, cy - (bb[1] + bb[3]) / 2 - 2 * S, val, fv, TXT)
        fl = self._ft(10, True)
        self._center(d, cx, cy + r - 12 * S, label, fl, color)
        fs = self._ft(12)
        self._center(d, cx, cy + r + 3 * S, sub, fs, sub_color)

    def _canvas(self):
        W = STATS_W * self.S
        img = vertical_gradient(W, W, (16, 20, 29), BG_BOTTOM)
        return img, ImageDraw.Draw(img), W

    def _header(self, d, W, now: dt.datetime, alert: Optional[str]):
        """Giờ + ngày; khi PC quá nóng: viền đỏ + dòng cảnh báo thay cho ngày."""
        S = self.S
        d.text((10 * S, 5 * S), now.strftime("%H:%M"), font=self._ft(15, True), fill=TXT)
        if alert:
            fa = self._ft(12, True)
            d.text((W - 10 * S - text_w(fa, alert), 8 * S), alert, font=fa, fill=C_HOT)
            d.rounded_rectangle([S, S, W - S, W - S], 10 * S, outline=C_HOT, width=3 * S)
        else:
            date = f"{WEEKDAYS_VI[now.weekday()]} {now.strftime('%d/%m')}"
            fd = self._ft(12)
            d.text((W - 10 * S - text_w(fd, date), 8 * S), date, font=fd, fill=DIM)
        d.line([10 * S, 27 * S, W - 10 * S, 27 * S], fill=(32, 37, 50), width=S)

    def _net_row(self, d, y, st: Stats):
        S = self.S
        fn = self._ft(12)
        tri = 4 * S
        x1 = 20 * S
        d.polygon([(x1, y + 5 * S), (x1 + 2 * tri, y + 5 * S), (x1 + tri, y + 11 * S)], fill=C_VRAM)
        d.text((x1 + 2 * tri + 5 * S, y), fmt_rate(st.net_down), font=fn, fill=TXT)
        x2 = 128 * S
        d.polygon([(x2, y + 11 * S), (x2 + 2 * tri, y + 11 * S), (x2 + tri, y + 5 * S)], fill=C_CPU)
        d.text((x2 + 2 * tri + 5 * S, y), fmt_rate(st.net_up), font=fn, fill=TXT)

    def render_disk_net(self, st: Stats, disks: list, net_hist: list, now: dt.datetime,
                        alert: Optional[str] = None) -> Image.Image:
        """Trang phụ: dung lượng ổ đĩa + biểu đồ tốc độ mạng 60 giây gần nhất."""
        S = self.S
        img, d, W = self._canvas()
        self._header(d, W, now, alert)
        fl, fb, fs = self._ft(10, True), self._ft(13, True), self._ft(12)
        d.text((10 * S, 33 * S), "Ổ ĐĨA", font=fl, fill=DIMMER)
        for i, (name, used, total) in enumerate(disks[:3]):
            y = 48 * S + i * 30 * S
            pct = used / total if total else 0.0
            d.text((10 * S, y), name, font=fb, fill=TXT)
            info = f"{used:.0f}/{total:.0f} GB"
            d.text((W - 10 * S - text_w(fs, info), y + S), info, font=fs, fill=DIM)
            bar_y = y + 19 * S
            d.rounded_rectangle([10 * S, bar_y, W - 10 * S, bar_y + 6 * S], 3 * S, fill=TRACK_BG)
            col = C_HOT if pct >= 0.9 else (C_WARN if pct >= 0.75 else C_RAM)
            fx = 10 * S + int((W - 20 * S) * pct)
            if fx > 13 * S:
                d.rounded_rectangle([10 * S, bar_y, fx, bar_y + 6 * S], 3 * S, fill=col)
        if not disks:
            d.text((10 * S, 50 * S), "Không đọc được ổ đĩa", font=fs, fill=DIM)

        d.text((10 * S, 141 * S), "MẠNG · 60 GIÂY", font=fl, fill=DIMMER)
        gx0, gx1, gy0, gy1 = 10 * S, W - 10 * S, 156 * S, 210 * S
        d.rounded_rectangle([gx0, gy0, gx1, gy1], 6 * S, fill=(20, 25, 36))
        peak = max([max(a, b) for a, b in net_hist] + [1024.0])
        for idx, col in ((0, C_VRAM), (1, C_CPU)):
            if len(net_hist) < 2:
                break
            step = (gx1 - gx0 - 8 * S) / (len(net_hist) - 1)
            pts = [(gx0 + 4 * S + k * step, gy1 - 4 * S - (gy1 - gy0 - 8 * S) * (v[idx] / peak))
                   for k, v in enumerate(net_hist)]
            d.line(pts, fill=col, width=2 * S, joint="curve")
        peak_txt = f"đỉnh {fmt_rate(peak)}"
        d.text((gx1 - text_w(self._ft(10), peak_txt), 141 * S), peak_txt, font=self._ft(10), fill=DIMMER)
        self._net_row(d, 219 * S, st)
        return img.resize((STATS_W, STATS_H), Image.LANCZOS)

    def render(self, st: Stats, now: dt.datetime, alert: Optional[str] = None) -> Image.Image:
        S = self.S
        img, d, W = self._canvas()
        self._header(d, W, now, alert)

        def temp_txt(t):
            return f"{t:.0f}°C" if t is not None else "--"

        ram_pct = 100.0 * st.ram_used / st.ram_total if st.ram_total else None
        if st.vram_used is not None and st.vram_total:
            vram_sub = f"{st.vram_used:.1f}/{st.vram_total:.0f} GB"
        elif st.vram_used is not None:
            vram_sub = f"{st.vram_used:.1f} GB"
        else:
            vram_sub = "cần LHM" if not st.lhm_ok else "--"
        gauges = [
            ("CPU", st.cpu_load, C_CPU, temp_txt(st.cpu_temp), self._temp_color(st.cpu_temp)),
            ("GPU", st.gpu_load, C_GPU,
             temp_txt(st.gpu_temp) if st.lhm_ok else "cần LHM", self._temp_color(st.gpu_temp)),
            ("RAM", ram_pct, C_RAM, f"{st.ram_used:.1f}/{st.ram_total:.0f} GB", DIM),
            ("VRAM", st.vram_pct, C_VRAM, vram_sub, DIM),
        ]
        r, width = 33 * S, 7 * S
        for i, (label, val, col, sub, subc) in enumerate(gauges):
            c, row = i % 2, i // 2
            cx = (60 + 120 * c) * S
            cy = (30 + 38 + 92 * row) * S
            self._gauge(d, cx, cy, r, width, val, col, label, sub, subc)

        self._net_row(d, 219 * S, st)
        return img.resize((STATS_W, STATS_H), Image.LANCZOS)


# ==========================================================================
# Mã hoá & gửi ảnh
# ==========================================================================
def _qoi_supported() -> bool:
    try:
        Image.new("RGB", (2, 2)).save(io.BytesIO(), "QOI")
        return True
    except Exception:
        return False


QOI_OK = _qoi_supported()


def encode_region(img: Image.Image) -> tuple[int, bytes]:
    jb = io.BytesIO()
    img.save(jb, "JPEG", quality=92, subsampling=0, optimize=False, progressive=False)
    jpg = jb.getvalue()
    if QOI_OK:
        qb = io.BytesIO()
        img.save(qb, "QOI")
        qoi = qb.getvalue()
        # ưu tiên QOI (không mất chi tiết, chữ sắc nét) trừ khi nặng hơn JPEG quá nhiều
        if len(qoi) <= MAX_PAYLOAD and len(qoi) <= max(len(jpg) * 1.8, 8000):
            return T_QOI, qoi
    return T_JPEG, jpg


def diff_regions(a: np.ndarray, b: np.ndarray, gap: int = 6) -> list[tuple[int, int, int, int]]:
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
    h, w = mask.shape
    out = []
    for r0, r1 in groups:
        cols = np.flatnonzero(mask[r0:r1 + 1].any(axis=0))
        out.append((int(cols[0]), r0, int(cols[-1]) + 1, r1 + 1))
    return out


def build_packet(ptype: int, screen: int, x: int, y: int, payload: bytes = b"") -> bytes:
    return MAGIC + struct.pack("<BBHHI", ptype, screen, x, y, len(payload)) + payload


class LinkError(Exception):
    pass


class BaseLink:
    def write(self, data: bytes):
        raise NotImplementedError

    def read_ctrl(self, timeout: float) -> Optional[int]:
        raise NotImplementedError

    def close(self):
        pass

    last_tx = 0.0

    def send(self, ptype, screen=0, x=0, y=0, payload=b"", timeout=4.0) -> int:
        self.write(build_packet(ptype, screen, x, y, payload))
        self.last_tx = time.monotonic()
        r = self.read_ctrl(timeout)
        if r is None:
            raise LinkError("ESP32 không phản hồi (timeout)")
        return r

    def handshake(self, tries=12):
        for _ in range(tries):
            try:
                if self.send(T_PING, timeout=0.6) in (ACK, NEED_FULL):
                    return
            except LinkError:
                pass
        raise LinkError("Không bắt tay được với ESP32 (kiểm tra firmware / tốc độ baud)")


class SerialLink(BaseLink):
    KNOWN_VIDS = {0x10C4, 0x1A86, 0x0403, 0x303A}   # CP210x, CH340/CH9102, FTDI, Espressif

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
        log.info("Đã mở %s @ %d baud", port, baud)

    @classmethod
    def find_port(cls) -> Optional[str]:
        from serial.tools import list_ports
        ports = list(list_ports.comports())
        for p in ports:
            if p.vid in cls.KNOWN_VIDS:
                return p.device
        return None

    def write(self, data: bytes):
        self.ser.write(data)

    def read_ctrl(self, timeout: float) -> Optional[int]:
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            chunk = self.ser.read(64)
            for c in chunk:
                if c in CTRL_BYTES:
                    return c
        return None

    def close(self):
        try:
            self.ser.close()
        except Exception:
            pass


class TcpLink(BaseLink):
    def __init__(self, host: str, port: int):
        self.sock = socket.create_connection((host, port), timeout=5)
        self.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        log.info("Đã kết nối WiFi tới %s:%d", host, port)

    def write(self, data: bytes):
        self.sock.sendall(data)

    def read_ctrl(self, timeout: float) -> Optional[int]:
        end = time.monotonic() + timeout
        while True:
            remain = end - time.monotonic()
            if remain <= 0:
                return None
            r, _, _ = select.select([self.sock], [], [], remain)
            if not r:
                return None
            data = self.sock.recv(64)
            if not data:
                raise LinkError("ESP32 đã đóng kết nối")
            for c in data:
                if c in CTRL_BYTES:
                    return c

    def close(self):
        try:
            self.sock.close()
        except Exception:
            pass


class ScreenPusher:
    def __init__(self, link: BaseLink):
        self.link = link
        self.cache: dict[int, Optional[np.ndarray]] = {SCREEN_LYRICS: None, SCREEN_STATS: None}
        self.last_full = {SCREEN_LYRICS: 0.0, SCREEN_STATS: 0.0}
        self.bytes_sent = 0

    def _send_rect(self, screen, img, x0, y0, x1, y1, full=False) -> bool:
        crop = img.crop((x0, y0, x1, y1))
        ptype, payload = encode_region(crop)
        if len(payload) > MAX_PAYLOAD and (y1 - y0) > 1:
            mid = (y0 + y1) // 2
            ok1 = self._send_rect(screen, img, x0, y0, x1, mid, full)
            ok2 = self._send_rect(screen, img, x0, mid, x1, y1, full)
            return ok1 and ok2
        r = self.link.send(ptype | (FLAG_FULL if full else 0), screen, x0, y0, payload)
        self.bytes_sent += len(payload) + 12
        if r == NAK:
            raise LinkError("ESP32 báo lỗi giải mã ảnh")
        return r != NEED_FULL

    def push(self, screen: int, img: Image.Image):
        arr = np.asarray(img.convert("RGB"))
        prev = self.cache[screen]
        now = time.monotonic()
        h, w = arr.shape[:2]
        full = prev is None or prev.shape != arr.shape or now - self.last_full[screen] > FULL_REFRESH_S
        if full:
            regions = [(0, 0, w, h)]
            self.last_full[screen] = now
        else:
            regions = diff_regions(prev, arr)
        ok = True
        for (x0, y0, x1, y1) in regions:
            ok = self._send_rect(screen, img, x0, y0, x1, y1, full) and ok
        self.cache[screen] = arr if ok else None


# ==========================================================================
# Chương trình chính
# ==========================================================================
def parse_night(s: str):
    try:
        a, b = s.split("-")
        return dt.time.fromisoformat(a.strip()), dt.time.fromisoformat(b.strip())
    except Exception:
        return None


def in_night(now: dt.datetime, rng) -> bool:
    if not rng:
        return False
    a, b, t = rng[0], rng[1], now.time()
    return (a <= t < b) if a < b else (t >= a or t < b)


def now_playing_info(np_: Optional[NowPlaying]) -> Optional[dict]:
    """Bài đang phát (tên đã làm gọn) gửi lên web cấu hình."""
    if np_ is None:
        return None
    if np_.source == "YouTube" and not np_.album:
        title, artist = clean_youtube(np_.title, np_.artist)
    else:
        title, artist = strip_emoji(np_.title), strip_emoji(np_.artist)
    return {"title": title, "artist": artist, "source": np_.source, "playing": np_.playing}


def heat_alert(st: Stats, s: PcSettings) -> Optional[str]:
    hot = []
    if st.cpu_temp is not None and st.cpu_temp >= s.hot_cpu:
        hot.append(f"CPU {st.cpu_temp:.0f}°C")
    if st.gpu_temp is not None and st.gpu_temp >= s.hot_gpu:
        hot.append(f"GPU {st.gpu_temp:.0f}°C")
    return " · ".join(hot) + " NÓNG" if hot else None


DISK_REFRESH_S = 30.0
NET_HISTORY = 60          # số mẫu (1 giây/mẫu) cho biểu đồ mạng


def read_disks() -> list[tuple[str, float, float]]:
    """(tên ổ, đã dùng GB, tổng GB) cho tối đa 3 ổ cố định."""
    out = []
    for p in psutil.disk_partitions(all=False):
        if IS_WIN and "fixed" not in p.opts:
            continue
        try:
            u = psutil.disk_usage(p.mountpoint)
        except OSError:
            continue
        out.append((p.mountpoint.rstrip("\\/") or p.mountpoint, u.used / 1024 ** 3, u.total / 1024 ** 3))
    return out[:3]


class App:
    def __init__(self, args):
        self.args = args
        self.fonts = FontBook(args.font, args.font_bold)
        self.lyr_renderer = LyricsRenderer(self.fonts, args.lyrics_lead)
        self.stats_renderer = StatsRenderer(self.fonts)
        cache_dir = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "DeskMon"
        self.lyrics = LyricsManager(cache_dir, args.lyrics_lead)
        if args.demo:
            self.media = DemoMedia()
            self.stats = DemoStats()
            t, l = parse_lrc(DemoMedia.LRC)
            self.lyrics.preload(DemoMedia.KEY, LyricsState("synced", t, l))
        else:
            self.media = MediaWatcher()
            self.stats = StatsCollector(args.lhm_url)
        self.media.start()
        self.night = parse_night(args.night) if args.night else None
        self.link: Optional[BaseLink] = None
        self.pusher: Optional[ScreenPusher] = None
        self.net_hist: deque = deque(maxlen=NET_HISTORY)
        self._disks: list = []
        self._disks_until = 0.0
        self.remote: Optional[RemoteSync] = None
        if not args.no_remote and not args.preview:
            host = args.remote_host or args.host or DEFAULT_HOST
            self.remote = RemoteSync(host, lambda: now_playing_info(self.media.snapshot()), self.media.request)
            self.remote.start()
        self.news: Optional[NewsFeed] = None
        self.news_renderer = NewsRenderer(self.fonts)
        self.news_turns = {SCREEN_LYRICS: NewsTurn(), SCREEN_STATS: NewsTurn()}
        if not args.demo and not args.preview:
            self.news = NewsFeed()
            self.news.start()

    def pc_settings(self) -> PcSettings:
        """Cài đặt từ web nếu đã đọc được, không thì dùng tham số dòng lệnh."""
        if self.remote and self.remote.settings:
            return self.remote.settings
        a, rng = self.args, self.night
        return PcSettings(lyrics_lead=a.lyrics_lead, bright_day=a.brightness, bright_night=a.night_brightness,
                          night_from=rng[0].hour if rng else 0, night_to=rng[1].hour if rng else 0)

    def disks(self) -> list:
        now = time.monotonic()
        if now >= self._disks_until:
            self._disks, self._disks_until = read_disks(), now + DISK_REFRESH_S
        return self._disks

    def stats_frame(self, st: Stats, now: dt.datetime, s: PcSettings, state: dict) -> Image.Image:
        """Màn 1.54": trang chính / trang ổ đĩa & mạng; quá nóng thì ghim trang chính + nhấp nháy viền."""
        now_m = time.monotonic()
        alert = heat_alert(st, s)
        if alert or not s.extra_page:
            state["page"], state["page_until"] = 0, now_m + s.page_sec
        elif now_m >= state["page_until"]:
            state["page"], state["page_until"] = 1 - state["page"], now_m + s.page_sec
        blink = alert if int(now_m) % 2 == 0 else None
        if state["page"] == 1:
            return self.stats_renderer.render_disk_net(st, self.disks(), list(self.net_hist), now, blink)
        return self.stats_renderer.render(st, now, blink)

    def news_frame(self, screen: int, s: PcSettings, now: dt.datetime, allowed: bool) -> Optional[Image.Image]:
        """Thẻ tin nếu đang tới lượt hiện tin trên màn này, không thì None (vẽ nội dung thường)."""
        if not self.news:
            return None
        bit = 1 if screen == SCREEN_LYRICS else 2
        want = allowed and s.news_on and bool(s.news_online & bit)
        item = self.news_turns[screen].tick(time.monotonic(), want, s.news_item_sec, s.news_gap_sec,
                                            self.news.next_item)
        if item is None:
            return None
        size = (LYRICS_W, LYRICS_H) if screen == SCREEN_LYRICS else (STATS_W, STATS_H)
        return self.news_renderer.render(size, item, self.news.image(item.image_url), now)

    # ---- kết nối ----
    def open_link(self) -> BaseLink:
        """--host: chỉ WiFi. --port: chỉ USB. Mặc định: USB nếu thấy cổng COM,
        không thì WiFi (board cắm cốc sạc, không nối USB với PC)."""
        a = self.args
        if a.host:
            return TcpLink(a.host, a.tcp_port)
        if a.port and a.port.lower() != "auto":
            return SerialLink(a.port, a.baud)
        port = SerialLink.find_port()
        if port:
            return SerialLink(port, a.baud)
        host = a.remote_host or DEFAULT_HOST
        try:
            return TcpLink(host, a.tcp_port)
        except OSError as e:
            raise LinkError(f"Không thấy ESP32 qua USB, cũng không vào được {host}:{a.tcp_port} qua WiFi ({e})") from e

    def connect(self):
        link = self.open_link()
        try:
            link.handshake()
        except Exception:
            link.close()
            raise
        self.link = link
        self.pusher = ScreenPusher(link)
        self.next_time_sync = 0.0
        self.last_bright = None
        log.info("Đã kết nối ESP32.")

    def disconnect(self):
        if self.link:
            self.link.close()
        self.link, self.pusher = None, None

    # ---- vòng lặp ----
    def frames(self):
        now = dt.datetime.now()
        np_ = self.media.snapshot()
        lyr = self.lyrics.state_for(np_)
        return self.lyr_renderer.render(np_, lyr, now), self.stats_renderer.render(self.stats.read(), now)

    def run_preview(self, out_dir: Path):
        out_dir.mkdir(parents=True, exist_ok=True)
        log.info("Chế độ xem trước: ghi ảnh vào %s (Ctrl+C để dừng)", out_dir.resolve())
        while True:
            a, b = self.frames()
            a.resize((a.width * 2, a.height * 2), Image.NEAREST).save(out_dir / "lyrics.png")
            b.resize((b.width * 2, b.height * 2), Image.NEAREST).save(out_dir / "stats.png")
            time.sleep(1)

    def tick(self, state):
        now_m = time.monotonic()
        now = dt.datetime.now()
        if now_m >= self.next_time_sync:
            off = now.astimezone().utcoffset() or dt.timedelta()
            local_epoch = int(time.time() + off.total_seconds())
            self.link.send(T_TIME, payload=struct.pack("<I", local_epoch))
            self.next_time_sync = now_m + 60
        s = self.pc_settings()
        self.lyr_renderer.lead = s.lyrics_lead
        bright = s.bright_night if s.is_night(now.hour) else s.bright_day
        if bright != self.last_bright:
            self.link.send(T_BRIGHT, payload=bytes([max(0, min(255, bright))]))
            self.last_bright = bright

        if self.news:
            self.news.configure(s.news_on and s.news_online != 0, s.news_domestic, s.news_world, s.news_refresh_min)
        np_ = self.media.snapshot()
        lyr = self.lyrics.state_for(np_)
        # đang phát nhạc thì màn 2.0" ưu tiên lời; PC quá nóng thì màn 1.54" ưu tiên thông số
        music_playing = np_ is not None and np_.playing
        card = self.news_frame(SCREEN_LYRICS, s, now, allowed=not music_playing)
        self.pusher.push(SCREEN_LYRICS, card or self.lyr_renderer.render(np_, lyr, now))
        if now_m >= state["next_stats"]:
            st = self.stats.read()
            self.net_hist.append((st.net_down, st.net_up))
            card = self.news_frame(SCREEN_STATS, s, now, allowed=not heat_alert(st, s))
            self.pusher.push(SCREEN_STATS, card or self.stats_frame(st, now, s, state))
            state["next_stats"] = now_m + 1.0

        # giữ kết nối: màn hình đứng yên lâu thì vẫn báo cho ESP32 biết PC còn chạy
        if time.monotonic() - self.link.last_tx > 2.0:
            if self.link.send(T_PING) == NEED_FULL:
                self.pusher.cache = {SCREEN_LYRICS: None, SCREEN_STATS: None}

    def run(self):
        state = {"next_stats": 0.0, "page": 0, "page_until": 0.0}
        period = 1.0 / max(1.0, self.args.fps)
        while True:
            try:
                if self.link is None:
                    self.connect()
                    state["next_stats"] = 0.0
                t0 = time.monotonic()
                self.tick(state)
                time.sleep(max(0.0, period - (time.monotonic() - t0)))
            except KeyboardInterrupt:
                raise
            except Exception as e:
                log.warning("Mất kết nối: %s — thử lại sau 3 s", e)
                self.disconnect()
                time.sleep(3)


def main():
    p = argparse.ArgumentParser(description="DeskMon - màn hình lời nhạc + theo dõi PC cho ESP32")
    p.add_argument("--port", default="auto", help="Cổng COM của ESP32 (mặc định: tự tìm)")
    p.add_argument("--baud", type=int, default=921600, help="Tốc độ Serial (phải khớp firmware)")
    p.add_argument("--host", help="IP của ESP32 nếu dùng WiFi (vd 192.168.1.50 hoặc deskmon.local)")
    p.add_argument("--tcp-port", type=int, default=7777)
    p.add_argument("--lhm-url", default="http://localhost:8085/data.json",
                   help="Địa chỉ web server của LibreHardwareMonitor")
    p.add_argument("--lyrics-lead", type=float, default=0.35,
                   help="Hiện lời sớm hơn bao nhiêu giây (bù độ trễ truyền). Web cấu hình ghi đè giá trị này")
    p.add_argument("--remote-host", help="Tên/IP web cấu hình trên ESP32 (mặc định: --host hoặc deskmon.local)")
    p.add_argument("--no-remote", action="store_true", help="Không đồng bộ với web cấu hình trên ESP32")
    p.add_argument("--fps", type=float, default=5, help="Số lần cập nhật màn lời mỗi giây")
    p.add_argument("--brightness", type=int, default=220, help="Độ sáng màn 1.54\" (0-255)")
    p.add_argument("--night", default="23:00-07:00", help="Khung giờ ban đêm, để trống để tắt")
    p.add_argument("--night-brightness", type=int, default=40)
    p.add_argument("--font", help="Đường dẫn font chữ thường (tuỳ chọn)")
    p.add_argument("--font-bold", help="Đường dẫn font chữ đậm (tuỳ chọn)")
    p.add_argument("--demo", action="store_true", help="Dùng dữ liệu mẫu (không cần nhạc / LHM)")
    p.add_argument("--preview", metavar="THU_MUC", help="Không gửi ESP32, chỉ lưu ảnh PNG để xem")
    p.add_argument("--log-file", help="Ghi log ra file (hữu ích khi chạy ẩn bằng pythonw)")
    p.add_argument("-v", "--verbose", action="store_true")
    args = p.parse_args()

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S",
                        filename=args.log_file, encoding="utf-8")
    if not QOI_OK:
        log.info("Pillow chưa hỗ trợ QOI -> dùng JPEG (nâng cấp: pip install -U pillow)")
    if not args.demo and IS_WIN and not WINRT_OK:
        log.warning("Thiếu gói winrt: pip install -r requirements.txt")

    app = App(args)
    try:
        if args.preview:
            app.run_preview(Path(args.preview))
        else:
            app.run()
    except KeyboardInterrupt:
        log.info("Đã dừng.")


if __name__ == "__main__":
    main()
