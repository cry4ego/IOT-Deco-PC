"""
Chương trình màn phụ ghi trạng thái của nó ra man_phu_status.json (vài giây một lần),
để bảng điều khiển biết nó đang làm gì: đang hiện hình, đang nối lại, đang nạp firmware...
File không cập nhật nữa nghĩa là chương trình đã dừng hoặc đang kẹt.
"""
from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Callable, Optional

log = logging.getLogger("man_phu")

STATUS_FILE = Path(__file__).resolve().parent / "man_phu_status.json"
STALE_S = 20.0                 # quá chừng này không cập nhật (mà không báo bận) là có chuyện

STARTING = "starting"
NO_MONITOR = "no_monitor"
CONNECTING = "connecting"
CONNECTED = "connected"
REFLASHING = "reflashing"
INSTALLING_DESKMON = "installing_deskmon"
RETRY_WAIT = "retry_wait"

STATE_LABELS = {
    STARTING: "Đang khởi động",
    NO_MONITOR: "Không thấy màn hình ảo của Windows",
    CONNECTING: "Đang kết nối ESP32",
    CONNECTED: "Đang hiện hình",
    REFLASHING: "Đang nạp lại firmware màn phụ",
    INSTALLING_DESKMON: "Đang nạp DeskMon vào vùng dự phòng",
    RETRY_WAIT: "Nạp firmware lỗi, sẽ thử lại",
}


class StatusWriter:
    def __init__(self, path: Path = STATUS_FILE, clock: Callable[[], float] = time.time):
        self.path = path
        self.clock = clock
        self.data: dict = {"pid": os.getpid(), "started": clock(), "state": STARTING, "detail": "",
                           "link": "", "fps": 0.0, "kbps": 0.0, "board_ip": "", "last_error": "",
                           "last_error_at": 0.0, "reconnects": 0, "busy_until": 0.0}

    def update(self, busy_s: float = 0.0, **fields):
        """busy_s: sắp làm việc lâu (nạp firmware), trong lúc đó không cập nhật được file."""
        now = self.clock()
        self.data = {**self.data, **fields, "updated": now, "busy_until": now + busy_s if busy_s else 0.0}
        tmp = self.path.with_name(self.path.name + ".tmp")
        try:
            tmp.write_text(json.dumps(self.data, ensure_ascii=False), encoding="utf-8")
            os.replace(tmp, self.path)              # bảng điều khiển không bao giờ đọc phải file dở
        except OSError as e:
            log.debug("Không ghi được %s: %s", self.path, e)

    def error(self, message: str, **fields):
        self.update(**fields, last_error=message, last_error_at=self.clock(),
                    reconnects=self.data.get("reconnects", 0) + 1)


def read_status(path: Path = STATUS_FILE) -> Optional[dict]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def is_stale(status: dict, now: float, stale_s: float = STALE_S) -> bool:
    fresh_until = max(status.get("updated", 0.0) + stale_s, status.get("busy_until", 0.0))
    return now > fresh_until
