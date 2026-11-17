"""Kiểm thử bảng điều khiển web: cài đặt, máy chủ (mã bí mật, Host), chọn đường truyền, số liệu biểu đồ."""
import json
import os
import sys
import threading
import types
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import claude_usage as cu  # noqa: E402
import settings as cfg  # noqa: E402
import status_file as st  # noqa: E402
from dashboard_data import history_point  # noqa: E402
from dashboard_server import DashboardServer  # noqa: E402
from side_view import PAGE_CLAUDE, PAGE_MONITOR, PAGE_PC, schedules_from, side_page  # noqa: E402


# ---------------------------------------------------------------- cài đặt
def test_defaults_when_there_is_no_settings_file(tmp_path):
    assert cfg.load(tmp_path / "none.json") == cfg.DEFAULTS


@pytest.mark.parametrize("values, error", [
    ({"stream_fps": 99}, "từ 5 đến 30"),
    ({"stream_fps": "30"}, "phải là số"),
    ({"claude_enabled": 1}, "bật/tắt"),
    ({"stream_link": "bluetooth"}, "một trong"),
])
def test_invalid_values_are_rejected(tmp_path, values, error):
    with pytest.raises(ValueError, match=error):
        cfg.save(values, tmp_path / "s.json")


def test_save_merges_persists_and_ignores_unknown_keys(tmp_path):
    path = tmp_path / "s.json"
    cfg.save({"side_claude_s": 12, "la": 1}, path)

    saved = cfg.save({"stream_link": "wifi"}, path)

    assert saved["side_claude_s"] == 12 and saved["stream_link"] == "wifi" and "la" not in saved
    assert cfg.load(path) == saved


def test_broken_settings_file_falls_back_to_defaults(tmp_path):
    path = tmp_path / "s.json"
    path.write_text('{"stream_fps": 1000, "side_pc_s": 20', encoding="utf-8")

    assert cfg.load(path) == cfg.DEFAULTS


def test_watcher_reloads_when_the_file_changes(tmp_path):
    path, now = tmp_path / "s.json", [0.0]
    cfg.save({"claude_hot_pct": 80}, path)
    watcher = cfg.SettingsWatcher(path, mono=lambda: now[0])
    cfg.save({"claude_hot_pct": 90}, path)
    os.utime(path, (1, 1))                                   # chắc chắn thời gian sửa file khác đi

    assert watcher.current()["claude_hot_pct"] == 80         # chưa tới lượt kiểm tra
    now[0] += cfg.CHECK_EVERY_S
    assert watcher.current()["claude_hot_pct"] == 90


def test_schema_lists_every_setting_for_the_page():
    assert [f["key"] for f in cfg.schema()] == list(cfg.DEFAULTS)


# ---------------------------------------------------------------- trang màn 1.54" theo cài đặt
def test_zero_seconds_hides_a_page():
    schedules = schedules_from({**cfg.DEFAULTS, "side_monitor_s": 0})

    assert {side_page(t, cu.MODE_NORMAL, schedules) for t in range(18)} == {PAGE_PC, PAGE_CLAUDE}


def test_hiding_every_page_still_shows_pc_stats():
    schedules = schedules_from({**cfg.DEFAULTS, "side_pc_s": 0, "side_claude_s": 0, "side_monitor_s": 0})

    assert side_page(3, cu.MODE_NORMAL, schedules) == PAGE_PC


def test_custom_lengths_and_hot_doubling():
    schedules = schedules_from({**cfg.DEFAULTS, "side_pc_s": 4, "side_claude_s": 6, "side_monitor_s": 2})

    assert [side_page(t, cu.MODE_NORMAL, schedules) for t in (0, 4, 10, 12)] == [PAGE_PC, PAGE_CLAUDE, PAGE_MONITOR, PAGE_PC]
    assert dict(schedules[cu.MODE_HOT])[PAGE_CLAUDE] == 12


# ---------------------------------------------------------------- Claude theo cài đặt
NOW = datetime(2026, 11, 17, 12, 0, tzinfo=timezone(timedelta(hours=7)))
RAW = {"five_hour": {"utilization": 70.0, "resets_at": "2026-11-17T07:30:00Z"},
       "seven_day": {"utilization": 20.0, "resets_at": "2026-11-21T02:00:00Z"}}


def make_watcher(mono):
    return cu.UsageWatcher(fetch=lambda tok: RAW, token=lambda: "SECRET-TOKEN", mono=lambda: mono[0], wall=lambda: NOW)


def test_lower_hot_threshold_turns_the_page_hot():
    w = make_watcher([0.0])
    w.tick()
    assert w.mode() == cu.MODE_NORMAL

    w.configure(refresh_s=300, hot_pct=60, reset_show_s=30)

    assert w.mode() == cu.MODE_HOT


def test_shorter_poll_interval_applies_right_away():
    w = make_watcher([0.0])
    w.tick()

    w.configure(refresh_s=60, hot_pct=80, reset_show_s=30)

    assert w.next_fetch == 60


def test_dashboard_copy_of_claude_numbers_has_no_token():
    w = make_watcher([0.0])
    w.tick()

    data = w.to_dict()

    assert data["session"]["pct"] == 70.0 and data["week"]["pct"] == 20.0
    assert "SECRET-TOKEN" not in json.dumps(data)


# ---------------------------------------------------------------- số liệu biểu đồ
def test_history_point_only_counts_fresh_connected_numbers():
    program = {"state": st.CONNECTED, "fps": 22.5, "kbps": 6.0, "pc": {"cpu_temp": 55.0},
               "claude": {"session": {"pct": 32.0}}}

    fresh = history_point(program, fresh=True)
    stale = history_point(program, fresh=False)

    assert (fresh["fps"], fresh["cpu_temp"], fresh["claude_session"]) == (22.5, 55.0, 32.0)
    assert set(stale.values()) == {None}


# ---------------------------------------------------------------- máy chủ
class FakeCollector:
    def __init__(self, busy=False):
        self.wake = threading.Event()
        self.state = {"verdict": {"level": "ok"}, "busy": busy}

    def latest(self):
        return self.state


@pytest.fixture
def server(tmp_path):
    calls = []
    actions = {"start": lambda: calls.append("start") or "đã chạy", "stop": lambda: calls.append("stop") or "đã dừng"}
    srv = DashboardServer(FakeCollector(), port=0, actions=actions, settings_path=tmp_path / "s.json",
                          startup_folder=tmp_path)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    srv.calls, srv.url = calls, f"http://127.0.0.1:{srv.server_port}"
    yield srv
    srv.shutdown()
    srv.server_close()


def post(srv, path, body, token=True):
    return requests.post(srv.url + path, json=body, headers={"X-Dash-Token": srv.token if token else "sai"}, timeout=5)


def test_page_carries_this_runs_secret_and_a_strict_policy(server):
    r = requests.get(server.url + "/", timeout=5)

    assert r.status_code == 200 and server.token in r.text and "__DASH_TOKEN__" not in r.text
    assert "frame-ancestors 'none'" in r.headers["Content-Security-Policy"]


def test_state_comes_from_the_collector(server):
    assert requests.get(server.url + "/api/state", timeout=5).json() == server.collector.latest()


def test_post_without_the_secret_is_refused(server):
    r = post(server, "/api/action", {"action": "start"}, token=False)

    assert r.status_code == 403 and server.calls == []


def test_foreign_host_name_is_refused(server):
    r = requests.get(server.url + "/api/state", headers={"Host": "evil.example:80"}, timeout=5)

    assert r.status_code == 403


def test_settings_round_trip_through_the_api(server):
    r = post(server, "/api/settings", {"values": {"side_claude_s": 15, "stream_link": "usb"}})

    assert r.status_code == 200 and r.json()["values"]["side_claude_s"] == 15
    assert cfg.load(server.settings_path)["stream_link"] == "usb"
    got = requests.get(server.url + "/api/settings", timeout=5).json()
    assert got["values"]["side_claude_s"] == 15 and got["schema"] and set(got["startup"]) == {"program", "dashboard"}


def test_bad_setting_is_reported_and_nothing_is_saved(server):
    r = post(server, "/api/settings", {"values": {"stream_fps": 500}})

    assert r.status_code == 400 and "30" in r.json()["error"]
    assert not server.settings_path.exists()


def test_actions_run_and_unknown_actions_are_refused(server):
    assert post(server, "/api/action", {"action": "start"}).json()["message"] == "đã chạy"
    assert post(server, "/api/action", {"action": "format_c"}).status_code == 400
    assert server.calls == ["start"]


def test_stop_while_flashing_needs_confirmation(server):
    server.collector.state = {**server.collector.state, "busy": True}

    first = post(server, "/api/action", {"action": "stop"})
    forced = post(server, "/api/action", {"action": "stop", "force": True})

    assert first.status_code == 409 and forced.status_code == 200 and server.calls == ["stop"]


def test_startup_toggle_removes_the_shortcut(server):
    lnk = server.startup_folder / "Man hinh phu.lnk"
    lnk.write_bytes(b"x")

    r = post(server, "/api/settings", {"values": {}, "startup": {"program": False}})

    assert r.status_code == 200 and not lnk.exists() and r.json()["startup"]["program"] is False


def test_startup_toggle_creates_a_working_shortcut(tmp_path):
    import program_ctl as ctl

    ctl.set_startup("dashboard", True, tmp_path)

    assert ctl.startup_state(tmp_path)["dashboard"] is True


# ---------------------------------------------------------------- chọn đường truyền
def make_app(monkeypatch, port=None, host=None, usb_port="COM4"):
    import man_phu
    monkeypatch.setattr(man_phu.SerialLink, "find_port", staticmethod(lambda: usb_port))
    return man_phu, man_phu.App(types.SimpleNamespace(no_side=True, host=host, port=port, firmware="fw.bin"))


@pytest.mark.parametrize("pref, expected", [("auto", (None, None)), ("wifi", (None, "deskmon.local")),
                                            ("usb", ("COM4", None))])
def test_link_preference_picks_the_connection(monkeypatch, pref, expected):
    _, app = make_app(monkeypatch)

    assert app.link_target(pref) == expected


def test_usb_only_without_a_cable_is_a_clear_error(monkeypatch):
    man_phu, app = make_app(monkeypatch, usb_port=None)

    with pytest.raises(man_phu.LinkError, match="cáp USB"):
        app.link_target("usb")


def test_command_line_beats_the_settings(monkeypatch):
    _, app = make_app(monkeypatch, host="192.168.1.50")

    assert app.link_target("usb") == (None, "192.168.1.50")
