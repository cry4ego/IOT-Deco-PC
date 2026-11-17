"""
Mức dùng Claude (phiên 5 giờ, giới hạn tuần) cho trang Claude trên màn 1.54".

Cách lấy giống bản Kindle dashboard (D:\\Đồ Decô\\kindle-dashboard\\mac\\claude_usage.sh): token
đăng nhập Claude Code trong ~/.claude/.credentials.json, hỏi api.anthropic.com/api/oauth/usage.
Địa chỉ này là nội bộ, chưa công bố, có thể đổi bất cứ lúc nào: lỗi thì trang Claude tự ẩn.
Token chỉ gửi tới api.anthropic.com: không ghi log, không lưu đi đâu, không gửi sang ESP32.
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Optional

import requests

log = logging.getLogger("man_phu")

CREDENTIALS = Path.home() / ".claude" / ".credentials.json"
USAGE_URL = "https://api.anthropic.com/api/oauth/usage"
OAUTH_BETA = "oauth-2025-04-20"
REFRESH_S = 300.0              # 5 phút hỏi một lần
ERROR_RETRY_S = 900.0          # lỗi (token hết hạn, mất mạng): 15 phút sau thử lại
AFTER_RESET_S = 60.0           # vừa tới giờ hồi: hỏi lại sớm để thấy phiên mới
AFTER_RESET_WINDOW = timedelta(minutes=10)   # ... trong chừng này, rồi về nhịp 5 phút
OLD_DATA_S = 900.0             # số liệu cũ hơn chừng này thì ghi "số liệu lúc ..."
HTTP_TIMEOUT_S = 15
SESSION_SPAN = timedelta(hours=5)
WEEK_SPAN = timedelta(days=7)
HOT_PCT = 80.0                 # từ đây trang Claude hiện lâu hơn và có nhãn "sắp hết"
PROJECT_FROM_PCT = 50.0        # từ đây mới dự đoán giờ hết (sớm quá dễ báo động nhầm)
RESET_SHOW_S = 30.0            # màn "Đã hồi token" hiện chừng này

MODE_OFF, MODE_NORMAL, MODE_HOT, MODE_RESET = "off", "normal", "hot", "reset"
THU = ["T2", "T3", "T4", "T5", "T6", "T7", "CN"]


class UsageError(Exception):
    pass


@dataclass(frozen=True)
class Limit:
    pct: Optional[float]
    resets_at: Optional[datetime]           # có múi giờ


@dataclass(frozen=True)
class Usage:
    session: Limit                          # phiên 5 giờ
    week: Limit                             # giới hạn tuần
    model: Optional[tuple[str, Limit]]      # giới hạn tuần riêng theo model (vd Fable), nếu tài khoản có
    fetched_at: datetime


def read_token(path: Path = CREDENTIALS) -> Optional[str]:
    try:
        token = json.loads(path.read_text(encoding="utf-8"))["claudeAiOauth"]["accessToken"]
    except (OSError, ValueError, KeyError, TypeError):
        return None
    return token if isinstance(token, str) and token else None


def fetch_raw(token: str) -> dict:
    try:
        r = requests.get(USAGE_URL, timeout=HTTP_TIMEOUT_S,
                         headers={"Authorization": f"Bearer {token}", "anthropic-beta": OAUTH_BETA})
    except requests.RequestException as e:
        raise UsageError(f"không kết nối được ({type(e).__name__})") from None
    if r.status_code == 401:
        raise UsageError("token Claude Code hết hạn, mở Claude Code là tự làm mới")
    if r.status_code != 200:
        raise UsageError(f"HTTP {r.status_code}")
    try:
        data = r.json()
    except ValueError:
        raise UsageError("trả về không phải JSON") from None
    if not isinstance(data, dict):
        raise UsageError("trả về sai dạng")
    return data


def _time(value) -> Optional[datetime]:
    if not isinstance(value, str) or not value:
        return None
    try:
        t = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


def _limit(entry) -> Limit:
    if not isinstance(entry, dict):
        return Limit(None, None)
    pct = entry.get("utilization", entry.get("percent"))
    return Limit(float(pct) if isinstance(pct, (int, float)) else None, _time(entry.get("resets_at")))


def _model_limit(raw: dict) -> Optional[tuple[str, Limit]]:
    """Giới hạn tuần riêng cho một model: trong danh sách "limits" (scope.model.display_name),
    hoặc khoá kiểu seven_day_opus. Không có thì None."""
    for lim in raw.get("limits") or []:
        scope = lim.get("scope") if isinstance(lim, dict) else None
        model = scope.get("model") if isinstance(scope, dict) else None
        name = model.get("display_name") if isinstance(model, dict) else None
        if name:
            return name, _limit(lim)
    for key, value in raw.items():
        if key.startswith("seven_day_") and isinstance(value, dict) and value.get("utilization") is not None:
            return key.removeprefix("seven_day_").replace("_", " ").title(), _limit(value)
    return None


def parse_usage(raw: dict, fetched_at: datetime) -> Usage:
    return Usage(_limit(raw.get("five_hour")), _limit(raw.get("seven_day")), _model_limit(raw), fetched_at)


def active(limit: Limit, now: datetime) -> Limit:
    """Quá giờ hồi mà chưa có số liệu mới: phiên đó đã hết, coi như chưa có phiên nào."""
    return Limit(None, None) if limit.resets_at and limit.resets_at <= now else limit


# ---------------------------------------------------------------- chữ hiển thị
def remaining_text(resets_at: Optional[datetime], now: datetime) -> str:
    if not resets_at:
        return ""
    mins = max(0, int((resets_at - now).total_seconds() // 60))
    days, rest = divmod(mins, 1440)
    hours, m = divmod(rest, 60)
    if days:
        return f"còn {days} ngày {hours} giờ"
    if hours:
        return f"còn {hours} giờ {m:02d} phút"
    return f"còn {m} phút"


def reset_text(resets_at: Optional[datetime], now: datetime) -> str:
    if not resets_at:
        return ""
    t = resets_at.astimezone(now.tzinfo)
    return f"hồi lúc {t:%H:%M}" if t.date() == now.date() else f"hồi {THU[t.weekday()]} {t:%H:%M}"


def elapsed_fraction(limit: Limit, span: timedelta, now: datetime) -> Optional[float]:
    if not limit.resets_at:
        return None
    return min(max(1 - (limit.resets_at - now) / span, 0.0), 1.0)


def projected_full(limit: Limit, span: timedelta, now: datetime) -> Optional[datetime]:
    """Dùng đều như từ đầu phiên tới giờ thì lúc nào chạm 100%. None nếu không chạm trước giờ hồi."""
    if limit.pct is None or limit.pct < PROJECT_FROM_PCT or not limit.resets_at:
        return None
    start = limit.resets_at - span
    used = now - start
    if used.total_seconds() <= 0:
        return None
    full = start + used * (100.0 / limit.pct)
    return full if full < limit.resets_at else None


# ---------------------------------------------------------------- theo dõi
class UsageWatcher:
    """Hỏi mức dùng định kỳ. Luồng vẽ màn 1.54" gọi tick() mỗi giây (không cần luồng riêng)."""

    def __init__(self, fetch: Callable[[str], dict] = fetch_raw,
                 token: Callable[[], Optional[str]] = read_token,
                 mono: Callable[[], float] = time.monotonic,
                 wall: Callable[[], datetime] = lambda: datetime.now(timezone.utc).astimezone()):
        self.fetch, self.token, self.mono, self.wall = fetch, token, mono, wall
        self.usage: Optional[Usage] = None
        self.error = ""
        self.next_fetch = 0.0
        self.reset_until = 0.0
        self._announced_reset: Optional[datetime] = None
        self._last_fetch: Optional[float] = None
        self.refresh_s, self.hot_pct, self.reset_show_s = REFRESH_S, HOT_PCT, RESET_SHOW_S

    def configure(self, refresh_s: float, hot_pct: float, reset_show_s: float):
        """Cài đặt từ bảng điều khiển. Rút ngắn chu kỳ hỏi thì có hiệu lực ngay."""
        self.refresh_s, self.hot_pct, self.reset_show_s = refresh_s, hot_pct, reset_show_s
        if self._last_fetch is not None and not self.error:
            self.next_fetch = min(self.next_fetch, self._last_fetch + refresh_s)

    def tick(self):
        now = self.wall()
        session_end = self.usage.session.resets_at if self.usage else None
        if session_end and now >= session_end and self._announced_reset != session_end:
            self._announced_reset = session_end              # tới giờ hồi: báo ngay, hỏi số liệu mới sớm
            self.reset_until = self.mono() + self.reset_show_s
            self.next_fetch = min(self.next_fetch, self.mono() + AFTER_RESET_S)
            log.info("Claude: đã tới giờ hồi phiên 5 giờ")
        if self.mono() >= self.next_fetch:
            self._refresh(now)

    def _refresh(self, now: datetime):
        token = self.token()
        try:
            if not token:
                raise UsageError("chưa đăng nhập Claude Code trên máy này")
            usage = parse_usage(self.fetch(token), now)
        except UsageError as e:
            if str(e) != self.error:
                log.warning("Không lấy được mức dùng Claude: %s", e)
            self.error = str(e)
            self.next_fetch = self.mono() + ERROR_RETRY_S
            return
        if self.usage is None or self.error:
            log.info("Claude: phiên 5 giờ %s%%, tuần %s%%", _pct_log(usage.session), _pct_log(usage.week))
        self.usage, self.error = usage, ""
        self._last_fetch = self.mono()
        end = usage.session.resets_at
        just_reset = end is not None and end <= now < end + AFTER_RESET_WINDOW   # máy chủ chưa kịp sang phiên mới
        self.next_fetch = self.mono() + (AFTER_RESET_S if just_reset else self.refresh_s)

    def mode(self) -> str:
        if self.mono() < self.reset_until:
            return MODE_RESET
        if self.usage is None:
            return MODE_OFF
        pct = active(self.usage.session, self.wall()).pct
        return MODE_HOT if pct is not None and pct >= self.hot_pct else MODE_NORMAL

    def to_dict(self) -> dict:
        """Số liệu cho bảng điều khiển (qua man_phu_status.json). Không có token."""
        u = self.usage
        model = {"name": u.model[0], **_limit_dict(u.model[1])} if u and u.model else None
        return {"session": _limit_dict(active(u.session, self.wall())) if u else None,
                "week": _limit_dict(u.week) if u else None, "model": model,
                "fetched_at": u.fetched_at.isoformat() if u else None,
                "error": self.error, "mode": self.mode(), "hot_pct": self.hot_pct}


def _limit_dict(limit: Limit) -> dict:
    return {"pct": limit.pct, "resets_at": limit.resets_at.isoformat() if limit.resets_at else None}


def _pct_log(limit: Limit) -> str:
    return "--" if limit.pct is None else f"{limit.pct:.0f}"
