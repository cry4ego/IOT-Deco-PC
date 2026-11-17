"""
Cài đặt chỉnh được từ bảng điều khiển, lưu ở pc/man_phu_settings.json.
Chương trình màn phụ đọc lại khi file đổi (vài giây một lần): không cần khởi động lại.
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable, Optional

SETTINGS_FILE = Path(__file__).resolve().parent / "man_phu_settings.json"
CHECK_EVERY_S = 2.0

SIDE, CLAUDE, STREAM = "Màn 1.54\"", "Claude", "Màn phụ 2.0\""


@dataclass(frozen=True)
class Field:
    key: str
    group: str
    label: str
    kind: str                     # int / bool / choice
    default: object
    min: Optional[int] = None
    max: Optional[int] = None
    step: int = 1
    unit: str = ""
    choices: tuple = ()           # ((giá trị, tên hiển thị), ...)
    help: str = ""


FIELDS = (
    Field("side_pc_s", SIDE, "Trang hiệu năng PC", "int", 10, 0, 60, 1, "giây", help="0 = ẩn trang này"),
    Field("side_claude_s", SIDE, "Trang Claude", "int", 8, 0, 60, 1, "giây",
          help="0 = ẩn. Phiên từ ngưỡng cảnh báo trở lên thì hiện gấp đôi"),
    Field("side_monitor_s", SIDE, "Trang màn phụ", "int", 5, 0, 60, 1, "giây", help="0 = ẩn trang này"),
    Field("claude_enabled", CLAUDE, "Theo dõi mức dùng Claude", "bool", True,
          help="Tắt thì không đọc token Claude Code, không hỏi Anthropic"),
    Field("claude_poll_min", CLAUDE, "Hỏi mức dùng mỗi", "int", 5, 1, 60, 1, "phút"),
    Field("claude_hot_pct", CLAUDE, "Ngưỡng cảnh báo \"sắp hết\"", "int", 80, 50, 100, 5, "%"),
    Field("claude_reset_show_s", CLAUDE, "Hiện \"Đã hồi token\" trong", "int", 30, 0, 300, 5, "giây",
          help="0 = không hiện"),
    Field("stream_fps", STREAM, "Khung hình tối đa", "int", 30, 5, 30, 1, "khung/giây",
          help="Thấp hơn thì đỡ tốn CPU và đường truyền"),
    Field("stream_link", STREAM, "Đường truyền", "choice", "auto",
          choices=(("auto", "Tự chọn (USB trước)"), ("usb", "Chỉ cáp USB"), ("wifi", "Chỉ WiFi"))),
)
DEFAULTS = {f.key: f.default for f in FIELDS}
_BY_KEY = {f.key: f for f in FIELDS}


def _check(field: Field, value) -> tuple[object, str]:
    """(giá trị hợp lệ, lỗi). Lỗi rỗng là hợp lệ."""
    if field.kind == "bool":
        return (value, "") if isinstance(value, bool) else (None, f"{field.label}: phải là bật/tắt")
    if field.kind == "choice":
        allowed = [c[0] for c in field.choices]
        return (value, "") if value in allowed else (None, f"{field.label}: phải là một trong {allowed}")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None, f"{field.label}: phải là số"
    number = round(value)
    if not field.min <= number <= field.max:
        return None, f"{field.label}: phải từ {field.min} đến {field.max}"
    return number, ""


def validate(values: dict) -> tuple[dict, list[str]]:
    """Giữ các khoá biết và hợp lệ; khoá lạ bỏ qua; trả kèm danh sách lỗi."""
    clean, errors = {}, []
    for key, value in (values or {}).items():
        field = _BY_KEY.get(key)
        if field is None:
            continue
        ok, error = _check(field, value)
        if error:
            errors.append(error)
        else:
            clean[key] = ok
    return clean, errors


def load(path: Path = SETTINGS_FILE) -> dict:
    try:
        stored = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        stored = {}
    clean, _ = validate(stored if isinstance(stored, dict) else {})
    return {**DEFAULTS, **clean}


def save(changes: dict, path: Path = SETTINGS_FILE) -> dict:
    clean, errors = validate(changes)
    if errors:
        raise ValueError("; ".join(errors))
    merged = {**load(path), **clean}
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)
    return merged


def schema() -> list[dict]:
    return [{**asdict(f), "choices": [list(c) for c in f.choices]} for f in FIELDS]


class SettingsWatcher:
    """Đọc lại file khi nó đổi; kiểm tra tối đa CHECK_EVERY_S giây một lần."""

    def __init__(self, path: Path = SETTINGS_FILE, mono: Callable[[], float] = time.monotonic):
        self.path, self.mono = path, mono
        self._values = load(path)
        self._mtime = self._stat()
        self._next_check = mono() + CHECK_EVERY_S

    def _stat(self) -> Optional[float]:
        try:
            return self.path.stat().st_mtime
        except OSError:
            return None

    def current(self) -> dict:
        if self.mono() >= self._next_check:
            self._next_check = self.mono() + CHECK_EVERY_S
            mtime = self._stat()
            if mtime != self._mtime:
                self._mtime, self._values = mtime, load(self.path)
        return self._values
