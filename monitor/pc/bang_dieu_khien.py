#!/usr/bin/env python3
"""
Bảng điều khiển màn hình phụ: mở cùng Windows, nhìn một cái là biết màn phụ đang ổn hay hỏng ở đâu.

Đọc: man_phu_status.json (chương trình màn phụ tự ghi), /api/status của board, danh sách màn hình
của Windows, cổng COM, LibreHardwareMonitor, cuối man_phu.log. Nút: chạy / khởi động lại / dừng
chương trình màn phụ, mở log, mở trang board, mở Cài đặt màn hình.

Chạy: pythonw bang_dieu_khien.py   (hoặc bấm đúp bang_dieu_khien.bat)
"""
from __future__ import annotations

import ctypes
import logging
import os
import queue
import subprocess
import sys
import threading
import time
import tkinter as tk
import webbrowser
from ctypes import wintypes
from pathlib import Path
from tkinter import messagebox

import requests

import status_file as st
from capture import list_monitors, pick_virtual, set_dpi_aware
from diagnose import BAD, OK, WARN, Report, Snapshot, diagnose
from guard import acquire_single_instance
from link import SerialLink
from side_view import DEFAULT_LHM_URL

PC_DIR = Path(__file__).resolve().parent
MAN_PHU = PC_DIR / "man_phu.py"
LOG_FILE = PC_DIR / "man_phu.log"
DEFAULT_BOARD = "deskmon.local"
REFRESH_S = 2.0
PROCESS_SCAN_S = 15.0           # tìm bản chạy không ghi file trạng thái (chậm, nên thưa)
LOG_LINES = 14
STOP_WAIT_S = 6.0
MUTEX_NAME = "Local\\DeskMonBangDieuKhien"

BG, CARD, BORDER, TEXT, DIM = "#0b0e14", "#141925", "#232a3a", "#eef1f6", "#8b93a7"
LEVEL_COLORS = {OK: "#22c55e", WARN: "#f59e0b", BAD: "#ef4444"}
BUTTON_BG, BUTTON_ACTIVE = "#1e2636", "#2a3448"
CHECK_TEXT_WRAP = 540
FONT = "Segoe UI"

NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
DETACHED = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
BUSY_STATES = (st.REFLASHING, st.INSTALLING_DESKMON)

# Ghi mỗi lần bấm nút vào man_phu.log, để đọc log biết chương trình bị dừng / chạy lại vì ai
actions = logging.getLogger("bang_dieu_khien")


def log_actions_to_file():
    handler = logging.FileHandler(LOG_FILE, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S"))
    actions.addHandler(handler)
    actions.setLevel(logging.INFO)


# ---------------------------------------------------------------- tiến trình màn phụ
_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True) if os.name == "nt" else None
if _kernel32:
    _kernel32.OpenProcess.restype = wintypes.HANDLE
    _kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    _kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    _kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    _kernel32.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR,
                                                     ctypes.POINTER(wintypes.DWORD)]
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
STILL_ACTIVE = 259


def pid_alive(pid: int) -> bool:
    """Tiến trình còn chạy và đúng là Python (PID cũ có thể đã thuộc về chương trình khác)."""
    if not pid or not _kernel32:
        return False
    handle = _kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return False
    try:
        code = wintypes.DWORD()
        if not _kernel32.GetExitCodeProcess(handle, ctypes.byref(code)) or code.value != STILL_ACTIVE:
            return False
        buf = ctypes.create_unicode_buffer(520)
        size = wintypes.DWORD(len(buf))
        if _kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
            return "python" in buf.value.lower()
        return True
    finally:
        _kernel32.CloseHandle(handle)


def find_program_pids() -> list[int]:
    script = ("Get-CimInstance Win32_Process -Filter \"Name='pythonw.exe' or Name='python.exe'\" | "
              "Where-Object { $_.CommandLine -like '*man_phu.py*' } | ForEach-Object { $_.ProcessId }")
    try:
        out = subprocess.run(["powershell", "-NoProfile", "-Command", script], capture_output=True,
                             text=True, timeout=20, creationflags=NO_WINDOW).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    return [int(x) for x in out.split() if x.isdigit()]


def start_program():
    pythonw = Path(sys.executable).with_name("pythonw.exe")
    exe = str(pythonw) if pythonw.is_file() else sys.executable
    subprocess.Popen([exe, str(MAN_PHU), "--log-file", LOG_FILE.name], cwd=str(PC_DIR),
                     creationflags=DETACHED, close_fds=True)


def stop_program() -> int:
    status = st.read_status() or {}
    pids = set(find_program_pids())
    if pid_alive(status.get("pid", 0)):
        pids.add(status["pid"])
    for pid in pids:
        subprocess.run(["taskkill", "/PID", str(pid), "/F"], capture_output=True, creationflags=NO_WINDOW)
    deadline = time.monotonic() + STOP_WAIT_S
    while any(pid_alive(p) for p in pids) and time.monotonic() < deadline:
        time.sleep(0.2)
    return len(pids)


def tail_lines(path: Path, n: int) -> list[str]:
    try:
        with path.open("rb") as f:
            f.seek(0, os.SEEK_END)
            f.seek(max(0, f.tell() - 16384))
            text = f.read().decode("utf-8", errors="replace")
    except OSError:
        return []
    return text.splitlines()[-n:]


# ---------------------------------------------------------------- thu thập (luồng nền)
class Collector(threading.Thread):
    def __init__(self, out: queue.Queue):
        super().__init__(daemon=True, name="collector")
        self.out = out
        self.wake = threading.Event()
        self.board_addr = DEFAULT_BOARD
        self.next_scan = 0.0
        self.stray_pids: list[int] = []

    def run(self):
        while True:
            try:
                self.out.put(("snapshot", self.collect()))
            except Exception as e:  # noqa: BLE001 - bảng điều khiển không được chết vì một lần đọc lỗi
                self.out.put(("message", f"Lỗi khi đọc trạng thái: {e}"))
            self.wake.wait(REFRESH_S)
            self.wake.clear()

    def collect(self) -> tuple[Report, list[str], str, bool]:
        program = st.read_status()
        alive = bool(program) and pid_alive(program.get("pid", 0))
        if not alive:
            if time.monotonic() >= self.next_scan:           # bản cũ không ghi file trạng thái
                self.next_scan = time.monotonic() + PROCESS_SCAN_S
                self.stray_pids = find_program_pids()
            if self.stray_pids:
                alive, program = True, None
        monitor = pick_virtual(list_monitors())
        snap = Snapshot(now=time.time(), program=program, program_alive=alive,
                        monitor=(monitor.left, monitor.top, monitor.width, monitor.height) if monitor else None,
                        usb_port=SerialLink.find_port(), board=self.query_board(program), lhm_ok=lhm_running())
        busy = bool(alive and program and program.get("state") in BUSY_STATES
                    and not st.is_stale(program, snap.now))
        return diagnose(snap), tail_lines(LOG_FILE, LOG_LINES), self.board_addr, busy

    def query_board(self, program: dict | None):
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


# ---------------------------------------------------------------- giao diện
class Dashboard:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.queue: queue.Queue = queue.Queue()
        self.collector = Collector(self.queue)
        self.board_addr = DEFAULT_BOARD
        self.busy = False                         # chương trình đang nạp firmware cho board
        self.rows: list[tuple[tk.Label, tk.Label, tk.Label]] = []
        root.title("Màn hình phụ – Bảng điều khiển")
        root.configure(bg=BG)
        root.geometry("860x780")
        root.minsize(620, 600)
        self._build()
        self.collector.start()
        self.root.after(200, self._poll)

    def _card(self, parent) -> tk.Frame:
        return tk.Frame(parent, bg=CARD, highlightthickness=1, highlightbackground=BORDER)

    def _build(self):
        pad = {"padx": 16, "pady": (12, 0)}
        self.banner = self._card(self.root)
        self.banner.pack(fill="x", **pad)
        self.stripe = tk.Frame(self.banner, width=6, bg=DIM)
        self.stripe.pack(side="left", fill="y")
        box = tk.Frame(self.banner, bg=CARD)
        box.pack(side="left", fill="both", expand=True, padx=14, pady=12)
        self.title = tk.Label(box, text="Đang kiểm tra…", font=(FONT, 15, "bold"), bg=CARD, fg=TEXT,
                              anchor="w", justify="left")
        self.title.pack(fill="x")
        self.hint = tk.Label(box, text="", font=(FONT, 10), bg=CARD, fg=DIM, anchor="w", justify="left",
                             wraplength=680)
        self.hint.pack(fill="x", pady=(4, 0))
        box.bind("<Configure>", lambda e: self.hint.configure(wraplength=max(200, e.width - 8)))

        self.checks = self._card(self.root)
        self.checks.pack(fill="x", **pad)
        self.checks.columnconfigure(2, weight=1)

        bar = tk.Frame(self.root, bg=BG)
        bar.pack(fill="x", **pad)
        for text, action in (("Chạy màn phụ", self.on_start), ("Khởi động lại", self.on_restart),
                             ("Dừng", self.on_stop), ("Mở log", self.on_log),
                             ("Trang board", self.on_board), ("Cài đặt màn hình", self.on_display)):
            tk.Button(bar, text=text, command=action, font=(FONT, 10), bg=BUTTON_BG, fg=TEXT,
                      activebackground=BUTTON_ACTIVE, activeforeground=TEXT, relief="flat", bd=0,
                      padx=10, pady=6, cursor="hand2").pack(side="left", padx=(0, 6))
        self.message = tk.Label(self.root, text="", font=(FONT, 10), bg=BG, fg=DIM, anchor="w")
        self.message.pack(fill="x", padx=16, pady=(8, 0))
        # dòng cuối xếp trước khung log: cửa sổ thấp thì khung log co lại chứ dòng này không bị đẩy mất
        self.footer = tk.Label(self.root, text="", font=(FONT, 9), bg=BG, fg=DIM, anchor="w")
        self.footer.pack(side="bottom", fill="x", padx=16, pady=(4, 10))

        log_card = self._card(self.root)
        log_card.pack(fill="both", expand=True, padx=16, pady=(8, 0))
        tk.Label(log_card, text="Nhật ký gần nhất (man_phu.log)", font=(FONT, 9, "bold"), bg=CARD,
                 fg=DIM, anchor="w").pack(fill="x", padx=10, pady=(8, 2))
        self.log = tk.Text(log_card, height=12, bg="#0f131c", fg=TEXT, font=("Consolas", 9), relief="flat",
                           wrap="word", padx=8, pady=6, highlightthickness=0)
        self.log.pack(fill="both", expand=True, padx=10, pady=(0, 10))
        self.log.tag_configure("warn", foreground=LEVEL_COLORS[WARN])
        self.log.tag_configure("err", foreground=LEVEL_COLORS[BAD])
        self.log.configure(state="disabled")

    # -------- cập nhật
    def _poll(self):
        try:
            while True:
                kind, payload = self.queue.get_nowait()
                if kind == "snapshot":
                    self._render(*payload)
                else:
                    self.message.configure(text=payload)
        except queue.Empty:
            pass
        self.root.after(250, self._poll)

    def _render(self, report: Report, log_lines: list[str], board_addr: str, busy: bool):
        self.board_addr = board_addr
        self.busy = busy
        color = LEVEL_COLORS[report.level]
        self.stripe.configure(bg=color)
        self.title.configure(text=report.title, fg=color)
        self.hint.configure(text=report.hint)
        self._render_checks(report.checks)
        self._render_log(log_lines)
        self.footer.configure(text=f"Cập nhật lúc {time.strftime('%H:%M:%S')} · tự làm mới mỗi {REFRESH_S:.0f} giây")

    def _render_checks(self, checks):
        while len(self.rows) < len(checks):
            r = len(self.rows)
            dot = tk.Label(self.checks, text="●", font=(FONT, 11), bg=CARD)
            name = tk.Label(self.checks, font=(FONT, 10, "bold"), bg=CARD, fg=DIM, anchor="w")
            text = tk.Label(self.checks, font=(FONT, 10), bg=CARD, fg=TEXT, anchor="w", justify="left",
                            wraplength=CHECK_TEXT_WRAP)   # lỗi dài thì xuống dòng, không bị cắt
            dot.grid(row=r, column=0, padx=(12, 6), pady=3)
            name.grid(row=r, column=1, sticky="w", padx=(0, 12), pady=3)
            text.grid(row=r, column=2, sticky="we", padx=(0, 12), pady=3)
            self.rows.append((dot, name, text))
        for i, (dot, name, text) in enumerate(self.rows):
            if i < len(checks):
                c = checks[i]
                dot.configure(fg=LEVEL_COLORS[c.level])
                name.configure(text=c.name)
                text.configure(text=c.text)
                for w in (dot, name, text):
                    w.grid()
            else:
                for w in (dot, name, text):
                    w.grid_remove()

    def _render_log(self, lines: list[str]):
        self.log.configure(state="normal")
        self.log.delete("1.0", "end")
        for line in lines:
            tag = "err" if " ERROR " in line else "warn" if " WARNING " in line else ""
            self.log.insert("end", line + "\n", tag)
        self.log.see("end")
        self.log.configure(state="disabled")

    # -------- nút bấm (chạy ở luồng riêng cho khỏi đơ cửa sổ)
    def _confirm_if_flashing(self, action: str) -> bool:
        """Dừng chương trình lúc đang nạp sẽ để lại vùng firmware hỏng (log 15/11 09:47:46-47)."""
        if not self.busy:
            return True
        return messagebox.askyesno(
            "Đang nạp firmware",
            f"Chương trình đang nạp firmware cho board.\n{action} lúc này sẽ làm hỏng lần nạp, "
            "chương trình phải nạp lại từ đầu.\n\nVẫn tiếp tục?", icon="warning", default="no")

    def _run(self, busy: str, work):
        self.message.configure(text=busy)

        def target():
            try:
                done = work()
            except Exception as e:  # noqa: BLE001 - báo lỗi lên cửa sổ thay vì im lặng
                done = f"Lỗi: {e}"
            self.queue.put(("message", done))
            self.collector.wake.set()
        threading.Thread(target=target, daemon=True).start()

    def on_start(self):
        def work():
            if find_program_pids():
                return "Chương trình màn phụ đang chạy rồi."
            actions.info("Bảng điều khiển: bấm «Chạy màn phụ»")
            start_program()
            return "Đã chạy chương trình màn phụ. Có hình sau khoảng 5–60 giây."
        self._run("Đang chạy chương trình màn phụ…", work)

    def on_restart(self):
        if not self._confirm_if_flashing("Khởi động lại"):
            return

        def work():
            actions.info("Bảng điều khiển: bấm «Khởi động lại»%s", " (lúc đang nạp firmware)" if self.busy else "")
            stop_program()
            start_program()
            return "Đã khởi động lại. Board đang chạy DeskMon thì chương trình tự nạp lại màn phụ (khoảng 1 phút)."
        self._run("Đang khởi động lại…", work)

    def on_stop(self):
        if not self._confirm_if_flashing("Dừng"):
            return

        def work():
            actions.info("Bảng điều khiển: bấm «Dừng»%s", " (lúc đang nạp firmware)" if self.busy else "")
            n = stop_program()
            return "Đã dừng chương trình màn phụ. 90 giây sau board tự về DeskMon." if n else "Chương trình không chạy."
        self._run("Đang dừng…", work)

    def on_log(self):
        if LOG_FILE.is_file():
            os.startfile(LOG_FILE)  # noqa: S606 - mở file log bằng trình mặc định của Windows
        else:
            self.message.configure(text="Chưa có file log.")

    def on_board(self):
        webbrowser.open(f"http://{self.board_addr}")

    def on_display(self):
        os.startfile("ms-settings:display")  # noqa: S606 - mở Cài đặt > Màn hình của Windows


def main():
    if not acquire_single_instance(wait_s=0, name=MUTEX_NAME):
        return                                  # bảng điều khiển đã mở rồi
    log_actions_to_file()
    set_dpi_aware()
    root = tk.Tk()
    Dashboard(root)
    root.mainloop()


if __name__ == "__main__":
    main()
