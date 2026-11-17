"""
Gom trạng thái cho bảng điều khiển (2 giây một lần, ở luồng nền): chương trình màn phụ
(man_phu_status.json, có cả số liệu PC và Claude), board (/api/status), màn ảo của Windows,
cáp USB, LibreHardwareMonitor, cuối log. Giữ lịch sử 5 phút để vẽ biểu đồ nhỏ.
"""
from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import asdict
from typing import Optional

import requests

import program_ctl as ctl
import status_file as st
from capture import list_monitors, pick_virtual
from diagnose import Snapshot, diagnose
from link import SerialLink
from side_view import DEFAULT_LHM_URL

DEFAULT_BOARD = "deskmon.local"
REFRESH_S = 2.0
HISTORY_LEN = 150                  # 150 x 2 giây = 5 phút
PROCESS_SCAN_S = 15.0              # tìm bản chạy không ghi file trạng thái (chậm, nên thưa)
LOG_LINES = 40
BUSY_STATES = (st.REFLASHING, st.INSTALLING_DESKMON)
SERIES = ("fps", "kbps", "cpu_load", "cpu_temp", "gpu_load", "gpu_temp", "claude_session")


def history_point(program: Optional[dict], fresh: bool) -> dict:
    """Một điểm cho các biểu đồ. Chương trình không chạy / số liệu cũ thì để trống (None)."""
    p = program if (program and fresh) else {}
    connected = p.get("state") == st.CONNECTED
    pc = p.get("pc") or {}
    session = ((p.get("claude") or {}).get("session") or {})
    return {"fps": p.get("fps") if connected else None, "kbps": p.get("kbps") if connected else None,
            "cpu_load": pc.get("cpu_load"), "cpu_temp": pc.get("cpu_temp"),
            "gpu_load": pc.get("gpu_load"), "gpu_temp": pc.get("gpu_temp"),
            "claude_session": session.get("pct")}


class Collector(threading.Thread):
    def __init__(self):
        super().__init__(daemon=True, name="collector")
        self.wake = threading.Event()
        self.board_addr = DEFAULT_BOARD
        self._next_scan = 0.0
        self._stray_pids: list[int] = []
        self._lock = threading.Lock()
        self._state: dict = {"loading": True}
        self._history = {"t": deque(maxlen=HISTORY_LEN), **{k: deque(maxlen=HISTORY_LEN) for k in SERIES}}

    def latest(self) -> dict:
        with self._lock:
            return self._state

    def run(self):
        while True:
            try:
                state = self.collect()
            except Exception as e:  # noqa: BLE001 - bảng điều khiển không được chết vì một lần đọc lỗi
                state = {**self.latest(), "collect_error": str(e)}
            with self._lock:
                self._state = state
            self.wake.wait(REFRESH_S)
            self.wake.clear()

    def collect(self) -> dict:
        program = st.read_status()
        alive = bool(program) and ctl.pid_alive(program.get("pid", 0))
        if not alive:
            if time.monotonic() >= self._next_scan:          # bản cũ không ghi file trạng thái
                self._next_scan = time.monotonic() + PROCESS_SCAN_S
                self._stray_pids = ctl.find_program_pids()
            if self._stray_pids:
                alive, program = True, None
        monitor = pick_virtual(list_monitors())
        snap = Snapshot(now=time.time(), program=program, program_alive=alive,
                        monitor=(monitor.left, monitor.top, monitor.width, monitor.height) if monitor else None,
                        usb_port=SerialLink.find_port(), board=self.query_board(program), lhm_ok=lhm_running())
        report = diagnose(snap)
        fresh = bool(alive and program and not st.is_stale(program, snap.now))
        busy = fresh and program.get("state") in BUSY_STATES
        return {
            "now": snap.now,
            "verdict": {"level": report.level, "title": report.title, "hint": report.hint},
            "checks": [asdict(c) for c in report.checks],
            "program": program, "alive": alive, "fresh": fresh, "busy": busy,
            "board": snap.board, "board_addr": self.board_addr,
            "monitor": dict(zip(("left", "top", "width", "height"), snap.monitor)) if snap.monitor else None,
            "usb_port": snap.usb_port, "lhm_ok": snap.lhm_ok,
            "log": ctl.tail_lines(ctl.LOG_FILE, LOG_LINES),
            "history": self._add_history(snap.now, history_point(program, fresh)),
        }

    def _add_history(self, now: float, point: dict) -> dict:
        self._history["t"].append(now)
        for key in SERIES:
            self._history[key].append(point[key])
        return {key: list(values) for key, values in self._history.items()}

    def query_board(self, program: Optional[dict]):
        ip = (program or {}).get("board_ip") or ""
        for addr in dict.fromkeys(a for a in (ip, self.board_addr, DEFAULT_BOARD) if a):
            try:
                data = requests.get(f"http://{addr}/api/status", timeout=2).json()
            except (requests.RequestException, ValueError):
                continue
            self.board_addr = addr
            return data
        return None


def lhm_running() -> bool:
    try:
        return requests.get(DEFAULT_LHM_URL, timeout=1).ok
    except requests.RequestException:
        return False
