"""Kiểm thử bảng điều khiển: file trạng thái, kết luận đúng từng tình huống, thử nạp lại khi lỗi."""
import json
import sys
import time
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import status_file as st  # noqa: E402
from diagnose import BAD, OK, WARN, Snapshot, diagnose, format_duration  # noqa: E402

NOW = 1_000_000.0
MONITOR = (4480, 0, 800, 600)
MONITOR_FW = {"firmware": "man-phu 0.4.0", "uptime": 300, "deskmonSlot": True, "rssi": -55}
DESKMON_FW = {"fw": "2.1.0", "uptime": 130, "rssi": -55}


def program(state, **extra):
    return {"pid": 1, "started": NOW - 600, "updated": NOW - 1, "state": state, "link": "", "fps": 0.0,
            "kbps": 0.0, "last_error": "", "last_error_at": 0.0, "busy_until": 0.0, **extra}


def snap(prog=None, alive=True, monitor=MONITOR, usb=None, board=MONITOR_FW, lhm=True):
    return Snapshot(now=NOW, program=prog, program_alive=alive, monitor=monitor, usb_port=usb,
                    board=board, lhm_ok=lhm)


# ---------------------------------------------------------------- file trạng thái
def test_status_writer_merges_updates_into_a_readable_file(tmp_path):
    path = tmp_path / "status.json"
    clock = iter([10.0, 11.0, 12.0])
    writer = st.StatusWriter(path, clock=lambda: next(clock))

    writer.update(state=st.CONNECTING)
    writer.update(state=st.CONNECTED, link="USB COM4")

    data = st.read_status(path)
    assert data["state"] == st.CONNECTED and data["link"] == "USB COM4"
    assert data["updated"] == 12.0
    assert not (tmp_path / "status.json.tmp").exists()       # ghi qua file tạm rồi đổi tên


def test_status_writer_counts_errors(tmp_path):
    writer = st.StatusWriter(tmp_path / "s.json", clock=lambda: 5.0)

    writer.error("Mất kết nối: timed out", state=st.CONNECTING)
    writer.error("Mất kết nối: timed out")

    data = st.read_status(tmp_path / "s.json")
    assert data["reconnects"] == 2 and data["last_error"] == "Mất kết nối: timed out"
    assert data["state"] == st.CONNECTING


def test_read_status_survives_missing_or_broken_file(tmp_path):
    broken = tmp_path / "broken.json"
    broken.write_text("{nửa file", encoding="utf-8")

    assert st.read_status(tmp_path / "none.json") is None
    assert st.read_status(broken) is None


def test_status_goes_stale_unless_the_program_said_it_is_busy():
    assert not st.is_stale({"updated": NOW - 5}, NOW)
    assert st.is_stale({"updated": NOW - 25}, NOW)
    assert not st.is_stale({"updated": NOW - 100, "busy_until": NOW + 60}, NOW)   # đang nạp firmware


# ---------------------------------------------------------------- kết luận
def test_program_not_running_is_the_first_thing_reported():
    report = diagnose(snap(prog=None, alive=False))

    assert report.level == BAD and "không chạy" in report.title


def test_program_that_stopped_updating_is_reported_as_stuck():
    report = diagnose(snap(prog=program(st.CONNECTED, updated=NOW - 60)))

    assert report.level == BAD and "không phản hồi" in report.title


def test_missing_virtual_monitor_is_reported():
    report = diagnose(snap(prog=program(st.NO_MONITOR), monitor=None))

    assert report.level == BAD and "màn hình ảo" in report.title


def test_connected_over_wifi_is_ok_and_suggests_the_usb_cable():
    report = diagnose(snap(prog=program(st.CONNECTED, link="WiFi deskmon.local:7778", fps=7.5, kbps=60)))

    assert report.level == OK and "USB" in report.hint
    link = next(c for c in report.checks if c.name == "Đường truyền")
    assert link.level == OK and "7.5 khung/giây" in link.text


def test_connected_over_usb_needs_nothing():
    report = diagnose(snap(prog=program(st.CONNECTED, link="USB COM4 @ 2000000", fps=22.0), usb="COM4"))

    assert (report.level, report.hint) == (OK, "Không cần làm gì.")


def test_failed_reflash_says_when_it_retries():
    prog = program(st.RETRY_WAIT, retry_at=NOW + 30, last_error="Nạp firmware màn phụ lỗi lần 1: reset")

    report = diagnose(snap(prog=prog, board=DESKMON_FW))

    assert report.level == WARN and "30 giây" in report.title
    assert "Khởi động lại" in report.hint


def test_reflashing_warns_not_to_unplug_the_board():
    report = diagnose(snap(prog=program(st.REFLASHING, updated=NOW - 40, busy_until=NOW + 200),
                           board=DESKMON_FW))

    assert report.level == WARN and "rút điện" in report.hint


def test_board_on_deskmon_while_connecting_explains_the_wait():
    report = diagnose(snap(prog=program(st.CONNECTING), board=DESKMON_FW))

    assert report.level == WARN and "DeskMon" in report.title


def test_board_unreachable_without_usb_is_bad():
    report = diagnose(snap(prog=program(st.CONNECTING), board=None))

    assert report.level == BAD and "board" in report.title.lower()


@pytest.mark.parametrize("rssi, level", [(-50, OK), (-65, WARN), (-80, BAD)])
def test_board_wifi_strength(rssi, level):
    report = diagnose(snap(prog=program(st.CONNECTED), board={**MONITOR_FW, "rssi": rssi}))

    assert next(c for c in report.checks if c.name == "WiFi của board").level == level


def test_missing_deskmon_backup_is_flagged():
    report = diagnose(snap(prog=program(st.CONNECTED), board={**MONITOR_FW, "deskmonSlot": False}))

    assert next(c for c in report.checks if c.name == "DeskMon dự phòng").level == WARN


def test_format_duration():
    assert [format_duration(s) for s in (5, 125, 3600, 3720)] == ["5 giây", "2 phút", "1 giờ", "1 giờ 2 phút"]


# ---------------------------------------------------------------- chương trình màn phụ
def make_app(tmp_path):
    import man_phu
    writer = st.StatusWriter(tmp_path / "status.json")
    return man_phu, man_phu.App(types.SimpleNamespace(no_side=True, host=None, firmware="fw.bin"), status=writer)


def test_reflash_failures_back_off_30_60_120_then_every_5_minutes(monkeypatch, tmp_path):
    man_phu, app = make_app(tmp_path)
    monkeypatch.setattr(man_phu, "reflash_if_deskmon", lambda host, fw, on_flash=None: False)
    waits = []
    for _ in range(5):
        app.next_reflash = 0.0
        before = time.monotonic()
        app.maybe_reflash()
        waits.append(round(app.next_reflash - before))

    assert waits == [30, 60, 120, 300, 300]
    data = st.read_status(tmp_path / "status.json")
    assert data["state"] == st.RETRY_WAIT and "lần 5" in data["last_error"]


def test_successful_reflash_resets_the_back_off(monkeypatch, tmp_path):
    man_phu, app = make_app(tmp_path)
    app.reflash_failures = 3
    monkeypatch.setattr(man_phu, "reflash_if_deskmon", lambda host, fw, on_flash=None: True)

    app.maybe_reflash()

    assert app.reflash_failures == 0
    assert st.read_status(tmp_path / "status.json")["state"] == st.CONNECTING


def test_status_says_reflashing_while_the_upload_runs(monkeypatch, tmp_path):
    man_phu, app = make_app(tmp_path)
    seen = {}

    def fake_reflash(host, fw, on_flash=None):
        on_flash()
        seen.update(json.loads((tmp_path / "status.json").read_text(encoding="utf-8")))
        return True
    monkeypatch.setattr(man_phu, "reflash_if_deskmon", fake_reflash)

    app.maybe_reflash()

    assert seen["state"] == st.REFLASHING and seen["busy_until"] > seen["updated"]


# ---------------------------------------------------------------- bảng điều khiển
def test_tail_lines_reads_only_the_end_of_the_log(tmp_path):
    from bang_dieu_khien import tail_lines
    log = tmp_path / "man_phu.log"
    log.write_text("\n".join(f"dòng {i}" for i in range(5000)), encoding="utf-8")

    assert tail_lines(log, 3) == ["dòng 4997", "dòng 4998", "dòng 4999"]
    assert tail_lines(tmp_path / "none.log", 3) == []


def test_pid_alive_knows_this_process_and_rejects_nonsense():
    import os

    from bang_dieu_khien import pid_alive

    assert pid_alive(os.getpid())
    assert not pid_alive(0)
    assert not pid_alive(4_000_000)
