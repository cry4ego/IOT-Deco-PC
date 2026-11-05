"""
Đồng bộ với web cấu hình chạy trên ESP32 (http://deskmon.local).

Cứ ~1,5 giây gửi POST /api/pc:
  - gửi lên: bài đang phát (để trang web hiện tên bài)
  - nhận về: cài đặt phía PC (độ trễ lời, ngưỡng nhiệt, trang phụ, độ sáng) + lệnh nhạc từ web
Chạy ở luồng nền. Mất mạng thì giữ cài đặt cũ, không ảnh hưởng việc vẽ màn qua cáp USB.
"""
from __future__ import annotations

import json
import logging
import socket
import threading
import time
import urllib.request
from dataclasses import dataclass, fields
from typing import Callable, Optional

log = logging.getLogger("deskmon")

POLL_S = 1.5
TIMEOUT_S = 3.0           # khi PC gửi hình qua WiFi, ESP32 trả lời web chậm hơn
RESOLVE_RETRY_S = 30.0
RESOLVE_AFTER_FAILS = 3
MEDIA_COMMANDS = ("play_pause", "next", "prev")

# tên trường Python -> khoá JSON do firmware trả về
_JSON_KEYS = {
    "lyrics_lead": "lyricsLead", "hot_cpu": "hotCpu", "hot_gpu": "hotGpu",
    "extra_page": "extraPage", "page_sec": "pageSec", "bright_day": "brightDay",
    "bright_night": "brightNight", "night_from": "nightFrom", "night_to": "nightTo",
    "news_on": "newsOn", "news_online": "newsOnline", "news_domestic": "newsDomestic",
    "news_world": "newsWorld", "news_item_sec": "newsItemSec", "news_gap_sec": "newsGapSec",
    "news_refresh_min": "newsRefreshMin",
}


@dataclass(frozen=True)
class PcSettings:
    lyrics_lead: float = 0.35
    hot_cpu: float = 85.0
    hot_gpu: float = 85.0
    extra_page: bool = True
    page_sec: float = 10.0
    bright_day: int = 220
    bright_night: int = 40
    night_from: int = 23
    night_to: int = 7
    news_on: bool = False     # chưa đọc được web thì không tự tải tin
    news_online: int = 0      # bit 0 = màn 2.0", bit 1 = màn 1.54"
    news_domestic: bool = True
    news_world: bool = True
    news_item_sec: int = 10
    news_gap_sec: int = 60
    news_refresh_min: int = 5

    @classmethod
    def from_json(cls, data: dict) -> "PcSettings":
        base = cls()
        values = {}
        for f in fields(cls):
            default = getattr(base, f.name)
            try:
                values[f.name] = type(default)(data.get(_JSON_KEYS[f.name], default))
            except (TypeError, ValueError):
                values[f.name] = default
        return cls(**values)

    def is_night(self, hour: int) -> bool:
        a, b = self.night_from, self.night_to
        if a == b:
            return False
        return a <= hour < b if a < b else (hour >= a or hour < b)


class RemoteSync(threading.Thread):
    def __init__(self, host: str, now_playing: Callable[[], Optional[dict]],
                 on_media: Callable[[str], None]):
        super().__init__(daemon=True, name="remote")
        self.host = host
        self._now_playing = now_playing
        self._on_media = on_media
        self._lock = threading.Lock()
        self._settings: Optional[PcSettings] = None
        self._version: Optional[int] = None
        self._last_seq: Optional[int] = None
        self._ip: Optional[str] = None
        self._next_resolve = 0.0
        self._online = False
        self._fails = 0
        self._opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))   # mạng LAN, bỏ proxy

    @property
    def settings(self) -> Optional[PcSettings]:
        """None khi chưa từng đọc được cài đặt từ ESP32."""
        with self._lock:
            return self._settings

    def run(self):
        while True:
            self._step()
            time.sleep(POLL_S)

    def _step(self):
        try:
            self._poll()
            if not self._online:
                log.info("Đã kết nối web cấu hình DeskMon (http://%s)", self.host)
            self._online = True
            self._fails = 0
        except Exception as e:
            if self._online:
                log.debug("Web cấu hình %s không trả lời: %s", self.host, e)
            self._online = False
            self._fails += 1
            # quá giờ thoáng qua (ESP32 đang bận vẽ) thì giữ IP, tìm lại tên sẽ phải chờ tới 30 giây
            if self._fails >= RESOLVE_AFTER_FAILS:
                if self._ip:
                    log.warning("Mất kết nối web cấu hình %s: %s", self.host, e)
                self._ip = None

    def _resolve(self) -> str:
        if self._ip:
            return self._ip
        now = time.monotonic()
        if now < self._next_resolve:
            raise ConnectionError("đang chờ thử lại phân giải tên")
        self._next_resolve = now + RESOLVE_RETRY_S
        self._ip = socket.gethostbyname(self.host)
        return self._ip

    def _poll(self):
        ip = self._resolve()
        body = json.dumps({"np": self._now_playing() or {}}, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(f"http://{ip}/api/pc", data=body, method="POST",
                                     headers={"Content-Type": "application/json"})
        with self._opener.open(req, timeout=TIMEOUT_S) as resp:
            data = json.loads(resp.read().decode("utf-8"))

        settings = PcSettings.from_json(data.get("pc") or {})
        version = data.get("v")
        with self._lock:
            changed = version != self._version
            self._settings, self._version = settings, version
        if changed:
            log.info("Cài đặt từ web: %s", settings)

        cmd = data.get("cmd") or {}
        seq, name = cmd.get("seq"), cmd.get("name")
        if self._last_seq is None:
            self._last_seq = seq            # lần đầu: bỏ qua lệnh cũ còn lưu trên ESP32
        elif seq != self._last_seq:
            self._last_seq = seq
            if name in MEDIA_COMMANDS:
                log.info("Lệnh nhạc từ web: %s", name)
                self._on_media(name)
