"""
Điều khiển chương trình màn phụ cho bảng điều khiển: tìm / chạy / dừng tiến trình, đọc cuối log,
bật tắt tự chạy cùng Windows (shortcut trong thư mục Startup).
"""
from __future__ import annotations

import ctypes
import logging
import os
import subprocess
import sys
import time
from ctypes import wintypes
from pathlib import Path

import status_file as st

PC_DIR = Path(__file__).resolve().parent
MAN_PHU = PC_DIR / "man_phu.py"
LOG_FILE = PC_DIR / "man_phu.log"
STOP_WAIT_S = 6.0
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
DETACHED = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
STARTUP_DIR = Path(os.environ.get("APPDATA", "")) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"

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


def pythonw() -> str:
    exe = Path(sys.executable).with_name("pythonw.exe")
    return str(exe) if exe.is_file() else sys.executable


def start_program():
    subprocess.Popen([pythonw(), str(MAN_PHU), "--log-file", LOG_FILE.name], cwd=str(PC_DIR),
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


# ---------------------------------------------------------------- tự chạy cùng Windows
def startup_shortcuts() -> dict[str, tuple[str, str, str]]:
    """{tên: (file .lnk, chương trình, tham số)}. Tên file không dấu: WScript.Shell không lưu được tên có dấu."""
    return {
        "program": ("Man hinh phu.lnk", str(PC_DIR / "chay_man_phu.bat"), ""),
        "dashboard": ("Bang dieu khien man phu.lnk", pythonw(), f'"{PC_DIR / "bang_dieu_khien.py"}"'),
    }


def startup_state(folder: Path = STARTUP_DIR) -> dict[str, bool]:
    return {name: (folder / lnk).is_file() for name, (lnk, _, _) in startup_shortcuts().items()}


def set_startup(name: str, enabled: bool, folder: Path = STARTUP_DIR):
    lnk, target, args = startup_shortcuts()[name]
    path = folder / lnk
    if not enabled:
        path.unlink(missing_ok=True)
        return
    script = ("$s = (New-Object -ComObject WScript.Shell).CreateShortcut($env:DM_LNK); "
              "$s.TargetPath = $env:DM_TARGET; $s.Arguments = $env:DM_ARGS; "
              "$s.WorkingDirectory = $env:DM_DIR; $s.Save()")
    env = {**os.environ, "DM_LNK": str(path), "DM_TARGET": target, "DM_ARGS": args, "DM_DIR": str(PC_DIR)}
    done = subprocess.run(["powershell", "-NoProfile", "-Command", script], env=env, capture_output=True,
                          text=True, timeout=30, creationflags=NO_WINDOW)
    if done.returncode != 0 or not path.is_file():
        raise OSError(f"Không tạo được shortcut {lnk}: {done.stderr.strip()[:200]}")
