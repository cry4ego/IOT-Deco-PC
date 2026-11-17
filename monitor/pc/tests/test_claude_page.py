"""Kiểm thử trang Claude trên màn 1.54": đọc số liệu, hỏi định kỳ, xoay trang, vẽ."""
import json
import logging
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import claude_usage as cu  # noqa: E402
from side_view import (BAD, GOOD, PAGE_CLAUDE, PAGE_MONITOR, PAGE_PC, RING_BOX, RING_W,  # noqa: E402
                       render_claude_page, side_page)

VN = timezone(timedelta(hours=7))
NOW = datetime(2026, 11, 17, 12, 0, tzinfo=VN)
RAW = {"five_hour": {"utilization": 42.0, "resets_at": "2026-11-17T07:30:00Z"},      # 14:30 giờ VN
       "seven_day": {"utilization": 18.0, "resets_at": "2026-11-21T02:00:00Z"},
       "seven_day_opus": {"utilization": 12.0, "resets_at": "2026-11-21T02:00:00Z"}}


class FakeFonts:
    def get(self, size, bold=False, text=""):
        from PIL import ImageFont
        return ImageFont.load_default(size)


def usage(session_pct=42.0, session_end=NOW + timedelta(hours=2, minutes=30), week_pct=18.0, fetched=NOW, model=None):
    return cu.Usage(cu.Limit(session_pct, session_end), cu.Limit(week_pct, NOW + timedelta(days=3, hours=14)),
                    model, fetched)


# ---------------------------------------------------------------- đọc token, đọc số liệu
def test_read_token_from_claude_code_credentials(tmp_path):
    creds = tmp_path / ".credentials.json"
    creds.write_text(json.dumps({"claudeAiOauth": {"accessToken": "tok-123", "subscriptionType": "pro"}}))

    assert cu.read_token(creds) == "tok-123"


@pytest.mark.parametrize("content", [None, "{không phải json", json.dumps({"other": 1})])
def test_read_token_without_a_login_returns_none(tmp_path, content):
    creds = tmp_path / ".credentials.json"
    if content is not None:
        creds.write_text(content, encoding="utf-8")

    assert cu.read_token(creds) is None


def test_parse_usage_reads_session_week_and_model_limits():
    u = cu.parse_usage(RAW, NOW)

    assert u.session.pct == 42.0 and u.session.resets_at.astimezone(VN).strftime("%H:%M") == "14:30"
    assert u.week.pct == 18.0
    assert u.model[0] == "Opus" and u.model[1].pct == 12.0


def test_parse_usage_reads_model_limits_from_the_limits_list():
    raw = {**RAW, "limits": [{"kind": "weekly_scoped", "scope": {"model": {"display_name": "Fable"}},
                              "percent": 7, "resets_at": "2026-11-21T02:00:00Z"}]}

    assert cu.parse_usage(raw, NOW).model[0] == "Fable"


def test_parse_usage_without_an_active_session():
    u = cu.parse_usage({"five_hour": None, "seven_day": RAW["seven_day"]}, NOW)

    assert u.session == cu.Limit(None, None) and u.model is None


# ---------------------------------------------------------------- chữ, dự đoán
def test_remaining_and_reset_texts():
    assert cu.remaining_text(NOW + timedelta(hours=1, minutes=23), NOW) == "còn 1 giờ 23 phút"
    assert cu.remaining_text(NOW + timedelta(minutes=18, seconds=30), NOW) == "còn 18 phút"
    assert cu.remaining_text(NOW + timedelta(days=4, hours=2), NOW) == "còn 4 ngày 2 giờ"
    assert cu.reset_text(NOW + timedelta(hours=2), NOW) == "hồi lúc 14:00"
    assert cu.reset_text(NOW + timedelta(days=3), NOW) == "hồi T6 12:00"        # 17/11/2026 là thứ Ba


def test_a_session_past_its_reset_time_counts_as_no_session():
    old = cu.Limit(95.0, NOW - timedelta(minutes=1))

    assert cu.active(old, NOW) == cu.Limit(None, None)
    assert cu.active(cu.Limit(40.0, NOW + timedelta(hours=1)), NOW).pct == 40.0


@pytest.mark.parametrize("pct, left, hits", [
    (92.0, timedelta(minutes=30), True),       # 4,5 giờ đã 92%: chạm 100% trước giờ hồi
    (60.0, timedelta(hours=4), True),          # mới 1 giờ đã 60%
    (60.0, timedelta(hours=1), False),         # 4 giờ mới 60%: không chạm trước giờ hồi
    (42.0, timedelta(hours=1), False),         # dưới 50%: chưa dự đoán
])
def test_projected_full_only_when_the_limit_is_hit_before_reset(pct, left, hits):
    limit = cu.Limit(pct, NOW + left)
    start = limit.resets_at - cu.SESSION_SPAN

    full = cu.projected_full(limit, cu.SESSION_SPAN, NOW)

    if hits:
        assert full == start + (NOW - start) * (100 / pct) and full < limit.resets_at
    else:
        assert full is None


# ---------------------------------------------------------------- hỏi định kỳ
class Clock:
    def __init__(self):
        self.mono, self.wall = 0.0, NOW

    def advance(self, seconds):
        self.mono += seconds
        self.wall += timedelta(seconds=seconds)


def watcher(clock, raw=RAW, token="SECRET-TOKEN"):
    calls = []

    def fetch(tok):
        calls.append(tok)
        return raw
    w = cu.UsageWatcher(fetch=fetch, token=lambda: token, mono=lambda: clock.mono, wall=lambda: clock.wall)
    return w, calls


def test_watcher_fetches_every_five_minutes():
    clock = Clock()
    w, calls = watcher(clock)

    w.tick()
    clock.advance(299)
    w.tick()
    clock.advance(2)
    w.tick()

    assert len(calls) == 2 and w.mode() == cu.MODE_NORMAL


def test_watcher_without_login_never_calls_anthropic():
    clock = Clock()
    w, calls = watcher(clock, token=None)

    w.tick()

    assert calls == [] and w.mode() == cu.MODE_OFF and "đăng nhập" in w.error


def test_watcher_keeps_the_last_numbers_on_error_and_backs_off(caplog):
    clock = Clock()
    w, _ = watcher(clock)
    w.tick()

    def broken(tok):
        raise cu.UsageError("HTTP 500")
    w.fetch = broken
    clock.advance(301)
    with caplog.at_level(logging.INFO, logger="man_phu"):
        w.tick()

    assert w.usage.session.pct == 42.0 and w.mode() == cu.MODE_NORMAL
    assert w.next_fetch == pytest.approx(clock.mono + cu.ERROR_RETRY_S)
    assert "HTTP 500" in caplog.text and "SECRET-TOKEN" not in caplog.text   # token không bao giờ vào log


def test_watcher_is_hot_from_80_percent():
    clock = Clock()
    w, _ = watcher(clock, raw={**RAW, "five_hour": {"utilization": 85, "resets_at": "2026-11-17T07:30:00Z"}})

    w.tick()

    assert w.mode() == cu.MODE_HOT


def test_reaching_the_reset_time_shows_the_reset_page_then_refetches_soon():
    clock = Clock()
    w, calls = watcher(clock)
    w.tick()                                                  # phiên hồi lúc 14:30, giờ là 12:00
    clock.advance(2.5 * 3600 + 1)

    w.tick()
    assert w.mode() == cu.MODE_RESET
    clock.advance(cu.RESET_SHOW_S + 1)
    assert w.mode() != cu.MODE_RESET

    before = len(calls)
    clock.advance(cu.AFTER_RESET_S)
    w.tick()
    assert len(calls) == before + 1


# ---------------------------------------------------------------- xoay trang
def test_without_claude_numbers_the_old_rotation_stays():
    assert [side_page(t) for t in (0, 9.9, 10, 14.9, 15)] == [PAGE_PC, PAGE_PC, PAGE_MONITOR, PAGE_MONITOR, PAGE_PC]


def test_claude_page_joins_the_rotation():
    pages = [side_page(t, cu.MODE_NORMAL) for t in (0, 10, 17.9, 18, 23)]

    assert pages == [PAGE_PC, PAGE_CLAUDE, PAGE_CLAUDE, PAGE_MONITOR, PAGE_PC]


def test_hot_session_shows_claude_twice_as_long():
    assert sum(1 for t in range(28) if side_page(t, cu.MODE_HOT) == PAGE_CLAUDE) == 16


def test_reset_page_takes_over_the_screen():
    assert {side_page(t, cu.MODE_RESET) for t in range(30)} == {PAGE_CLAUDE}


# ---------------------------------------------------------------- vẽ
def ring_top(img):
    return img.getpixel((round((RING_BOX[0] + RING_BOX[2]) / 2), RING_BOX[1] + RING_W // 2))


@pytest.mark.parametrize("mode, data", [
    (cu.MODE_NORMAL, usage()),
    (cu.MODE_HOT, usage(session_pct=92.0, session_end=NOW + timedelta(minutes=18))),
    (cu.MODE_RESET, usage()),
    (cu.MODE_RESET, None),
    (cu.MODE_NORMAL, usage(session_pct=None, session_end=None)),
    (cu.MODE_NORMAL, usage(model=("Fable", cu.Limit(7.0, NOW + timedelta(days=3))))),
    (cu.MODE_NORMAL, usage(fetched=NOW - timedelta(hours=1))),
])
def test_claude_page_draws_every_case(mode, data):
    assert render_claude_page(FakeFonts(), data, mode, NOW).size == (240, 240)


def test_ring_color_follows_the_usage():
    assert ring_top(render_claude_page(FakeFonts(), usage(42.0), cu.MODE_NORMAL, NOW)) == GOOD
    assert ring_top(render_claude_page(FakeFonts(), usage(92.0), cu.MODE_HOT, NOW)) == BAD
