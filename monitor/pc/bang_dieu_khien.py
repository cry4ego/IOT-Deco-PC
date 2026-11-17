#!/usr/bin/env python3
"""
Bảng điều khiển màn hình phụ: giao diện web chạy ngay trong máy (http://127.0.0.1:8765),
mở thành cửa sổ riêng bằng Edge (chế độ ứng dụng, không thanh địa chỉ).

Theo dõi: trạng thái, mức dùng Claude, màn phụ 2.0", board ESP32, hiệu năng PC, nhật ký.
Chỉnh: thời gian từng trang màn 1.54", Claude, khung hình, đường truyền, tự chạy cùng Windows.

Chạy:
    pythonw bang_dieu_khien.py              # shortcut trong Startup và trên Desktop
    python bang_dieu_khien.py --no-window   # chỉ chạy máy chủ, tự mở trình duyệt vào địa chỉ trên
Mở lần nữa khi đang chạy thì chỉ mở thêm cửa sổ, không chạy thêm máy chủ.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import threading
import webbrowser
from pathlib import Path

import program_ctl as ctl
from capture import set_dpi_aware
from dashboard_data import Collector
from dashboard_server import PORT, DashboardServer
from guard import acquire_single_instance

MUTEX_NAME = "Local\\DeskMonBangDieuKhien"
EDGE_PATHS = (Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"),
              Path(r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"))
WINDOW_SIZE = "1320,920"
OPEN_DELAY_S = 0.8
# Hồ sơ Edge riêng cho cửa sổ bảng điều khiển: tiện ích cài trong Edge của bạn (VPN, chặn quảng cáo...)
# không bật popup đè lên, và bảng không đụng tới lịch sử / đăng nhập của Edge chính
EDGE_PROFILE = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "DeskMonDashboard" / "edge"


def open_window(url: str):
    edge = next((p for p in EDGE_PATHS if p.is_file()), None)
    if edge is None:
        webbrowser.open(url)
        return
    subprocess.Popen([str(edge), f"--app={url}", f"--window-size={WINDOW_SIZE}", f"--user-data-dir={EDGE_PROFILE}",
                      "--no-first-run", "--no-default-browser-check"], creationflags=ctl.NO_WINDOW)


def main():
    p = argparse.ArgumentParser(description="Bảng điều khiển màn hình phụ")
    p.add_argument("--no-window", action="store_true", help="Chỉ chạy máy chủ, không mở cửa sổ")
    p.add_argument("--port", type=int, default=PORT)
    args = p.parse_args()
    url = f"http://127.0.0.1:{args.port}/"
    if not acquire_single_instance(wait_s=0, name=MUTEX_NAME):
        if not args.no_window:
            open_window(url)                    # máy chủ đã chạy: chỉ mở thêm cửa sổ
        return
    ctl.log_actions_to_file()
    set_dpi_aware()                             # toạ độ màn ảo đúng điểm ảnh thật
    collector = Collector()
    collector.start()
    server = DashboardServer(collector, args.port)
    if not args.no_window:
        threading.Timer(OPEN_DELAY_S, open_window, args=(url,)).start()
    server.serve_forever()


if __name__ == "__main__":
    main()
