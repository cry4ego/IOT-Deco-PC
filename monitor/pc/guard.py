"""
Giữ cho chương trình màn phụ chạy mãi khi chạy ẩn (pythonw, không ai nhìn thấy lỗi):
- Watchdog: vòng chính không báo "còn sống" quá lâu (bị kẹt ở đâu đó) thì ghi lại chỗ kẹt
  vào log rồi tự chạy lại chương trình. Nhanh hơn 90 giây ESP32 chờ trước khi về DeskMon.
- Chỉ một bản chạy một lúc: hai bản cùng gửi hình sẽ giành nhau ESP32, rớt kết nối liên tục.
"""
from __future__ import annotations

import logging
import os
import subprocess
import sys
import threading
import time
import traceback
from pathlib import Path
from typing import Callable, Optional

log = logging.getLogger("man_phu")

STUCK_EXIT_CODE = 3
POLL_S = 5.0
MUTEX_NAME = "Local\\DeskMonManHinhPhu"
ERROR_ALREADY_EXISTS = 183
INSTANCE_WAIT_S = 20.0       # bản cũ đang tự chạy lại: chờ nó thoát hẳn


class Watchdog(threading.Thread):
    def __init__(self, limit_s: float, on_stuck: Callable[[], None],
                 clock: Callable[[], float] = time.monotonic, poll_s: float = POLL_S):
        super().__init__(daemon=True, name="watchdog")
        self.limit_s = limit_s
        self.on_stuck = on_stuck
        self.clock = clock
        self.poll_s = poll_s
        self.deadline = clock() + limit_s

    def beat(self, grace_s: float = 0.0):
        """Vòng chính gọi mỗi lượt. Sắp làm việc lâu (nạp firmware) thì xin thêm grace_s giây."""
        self.deadline = self.clock() + max(self.limit_s, grace_s)

    def check(self) -> bool:
        if self.clock() <= self.deadline:
            return False
        self.on_stuck()
        return True

    def run(self):
        while not self.check():
            time.sleep(self.poll_s)


def main_thread_stack() -> str:
    frame = sys._current_frames().get(threading.main_thread().ident)
    return "".join(traceback.format_stack(frame)) if frame else "(không lấy được)"


def restart_command(script: Path, args: list[str], executable: str = sys.executable) -> list[str]:
    return [executable, str(script), *args]


def restart_self(script: Path, reason: str):
    """Chạy một bản mới y hệt lệnh cũ rồi thoát ngay bản này (kể cả khi luồng chính đang kẹt)."""
    log.error("%s. Luồng chính đang ở:\n%s", reason, main_thread_stack())
    log.error("Tự khởi động lại chương trình")
    try:
        subprocess.Popen(restart_command(script, sys.argv[1:]), cwd=str(script.parent), close_fds=True)
    except OSError as e:
        log.error("Không chạy lại được: %s", e)
    logging.shutdown()
    os._exit(STUCK_EXIT_CODE)


_instance_handle: Optional[int] = None


def acquire_single_instance(wait_s: float = INSTANCE_WAIT_S, name: str = MUTEX_NAME) -> bool:
    """True nếu đây là bản duy nhất. Khoá (mutex của Windows) tự nhả khi tiến trình thoát."""
    global _instance_handle
    if os.name != "nt":
        return True
    import ctypes
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateMutexW.restype = ctypes.c_void_p
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    deadline = time.monotonic() + wait_s
    while True:
        handle = kernel32.CreateMutexW(None, False, name)
        if handle and ctypes.get_last_error() != ERROR_ALREADY_EXISTS:
            _instance_handle = handle
            return True
        if handle:
            kernel32.CloseHandle(handle)
        if time.monotonic() > deadline:
            return False
        time.sleep(0.5)
