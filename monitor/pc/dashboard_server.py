"""
Máy chủ web của bảng điều khiển, chỉ nghe trong máy (127.0.0.1).

GET  /                giao diện (web/dashboard.html, có mã bí mật của lần chạy này)
GET  /api/state       trạng thái + lịch sử 5 phút (dashboard_data.Collector)
GET  /api/settings    danh sách cài đặt, giá trị hiện tại, tự chạy cùng Windows
POST /api/settings    lưu cài đặt (chương trình màn phụ áp dụng sau vài giây)
POST /api/action      chạy / khởi động lại / dừng chương trình, mở log, mở Cài đặt màn hình

An toàn: chỉ nhận Host 127.0.0.1/localhost (chặn trang web lạ đổi DNS trỏ về máy), mọi POST phải
kèm mã bí mật sinh mới mỗi lần chạy (trang web khác không đọc được mã này) và gửi dạng JSON.
"""
from __future__ import annotations

import hmac
import json
import os
import secrets
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Callable, Optional
from urllib.parse import urlsplit

import program_ctl as ctl
import settings as cfg

PORT = 8765
PAGE = Path(__file__).resolve().parent / "web" / "dashboard.html"
TOKEN_MARK = "__DASH_TOKEN__"
MAX_BODY = 64 * 1024
BUSY_ACTIONS = ("restart", "stop")
CSP = ("default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; "
       "img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'")


def default_actions() -> dict[str, Callable[[], str]]:
    def start():
        if ctl.find_program_pids():
            return "Chương trình màn phụ đang chạy rồi."
        ctl.start_program()
        return "Đã chạy chương trình màn phụ. Có hình sau khoảng 5–60 giây."

    def restart():
        ctl.stop_program()
        ctl.start_program()
        return "Đã khởi động lại. Board đang chạy DeskMon thì chương trình tự đưa về màn phụ."

    def stop():
        n = ctl.stop_program()
        return "Đã dừng chương trình màn phụ. 90 giây sau board tự về DeskMon." if n else "Chương trình không chạy."

    def open_log():
        os.startfile(ctl.LOG_FILE)  # noqa: S606 - mở file log bằng trình mặc định của Windows
        return "Đã mở file log."

    def open_display():
        os.startfile("ms-settings:display")  # noqa: S606 - mở Cài đặt > Màn hình
        return "Đã mở Cài đặt màn hình của Windows."

    return {"start": start, "restart": restart, "stop": stop, "open_log": open_log, "open_display": open_display}


ACTION_LABELS = {"start": "Chạy màn phụ", "restart": "Khởi động lại", "stop": "Dừng",
                 "open_log": "Mở log", "open_display": "Cài đặt màn hình"}


class DashboardServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, collector, port: int = PORT, actions: Optional[dict] = None,
                 settings_path: Path = cfg.SETTINGS_FILE, startup_folder: Path = ctl.STARTUP_DIR):
        super().__init__(("127.0.0.1", port), Handler)
        self.collector = collector
        self.actions = actions if actions is not None else default_actions()
        self.settings_path = settings_path
        self.startup_folder = startup_folder
        self.token = secrets.token_urlsafe(24)
        self.allowed_hosts = {f"127.0.0.1:{self.server_port}", f"localhost:{self.server_port}"}


class Handler(BaseHTTPRequestHandler):
    server: DashboardServer

    def log_message(self, fmt, *args):     # không ghi mỗi lần trình duyệt hỏi trạng thái
        pass

    # -------- trả lời
    def _send(self, code: int, body: bytes, content_type: str, extra: Optional[dict] = None):
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        for key, value in (extra or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code: int, data):
        self._send(code, json.dumps(data, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

    def _host_ok(self) -> bool:
        if self.headers.get("Host", "") in self.server.allowed_hosts:
            return True
        self._json(403, {"error": "Chỉ mở được từ chính máy này"})
        return False

    # -------- GET
    def do_GET(self):
        if not self._host_ok():
            return
        path = urlsplit(self.path).path
        if path == "/":
            page = PAGE.read_text(encoding="utf-8").replace(TOKEN_MARK, self.server.token)
            self._send(200, page.encode("utf-8"), "text/html; charset=utf-8",
                       {"Content-Security-Policy": CSP, "X-Frame-Options": "DENY", "Referrer-Policy": "no-referrer"})
        elif path == "/api/state":
            self._json(200, self.server.collector.latest())
        elif path == "/api/settings":
            self._json(200, self._settings_payload())
        else:
            self._json(404, {"error": "Không có trang này"})

    def _settings_payload(self) -> dict:
        return {"schema": cfg.schema(), "values": cfg.load(self.server.settings_path),
                "defaults": cfg.DEFAULTS, "startup": ctl.startup_state(self.server.startup_folder)}

    # -------- POST
    def _read_json(self) -> Optional[dict]:
        token = self.headers.get("X-Dash-Token", "")
        if not hmac.compare_digest(token, self.server.token):
            self._json(403, {"error": "Thiếu mã của bảng điều khiển"})
            return None
        if not self.headers.get("Content-Type", "").startswith("application/json"):
            self._json(415, {"error": "Chỉ nhận JSON"})
            return None
        length = int(self.headers.get("Content-Length") or 0)
        if length > MAX_BODY:
            self._json(413, {"error": "Dữ liệu quá lớn"})
            return None
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
        except ValueError:
            self._json(400, {"error": "JSON hỏng"})
            return None
        if not isinstance(body, dict):
            self._json(400, {"error": "JSON phải là một đối tượng"})
            return None
        return body

    def do_POST(self):
        if not self._host_ok():
            return
        body = self._read_json()
        if body is None:
            return
        path = urlsplit(self.path).path
        if path == "/api/settings":
            self._save_settings(body)
        elif path == "/api/action":
            self._run_action(body)
        else:
            self._json(404, {"error": "Không có trang này"})

    def _save_settings(self, body: dict):
        try:
            values = cfg.save(body.get("values") or {}, self.server.settings_path)
            for name, enabled in (body.get("startup") or {}).items():
                if name in ctl.startup_shortcuts() and isinstance(enabled, bool):
                    ctl.set_startup(name, enabled, self.server.startup_folder)
        except (ValueError, OSError) as e:
            self._json(400, {"error": str(e)})
            return
        ctl.actions.info("Bảng điều khiển: lưu cài đặt %s", json.dumps(values, ensure_ascii=False))
        self._json(200, {**self._settings_payload(), "values": values})

    def _run_action(self, body: dict):
        name = body.get("action")
        action = self.server.actions.get(name) if isinstance(name, str) else None
        if action is None:
            self._json(400, {"error": "Không có nút này"})
            return
        busy = self.server.collector.latest().get("busy")
        if name in BUSY_ACTIONS and busy and body.get("force") is not True:
            self._json(409, {"busy": True, "error": "Chương trình đang nạp firmware cho board"})
            return
        ctl.actions.info("Bảng điều khiển: bấm «%s»%s", ACTION_LABELS.get(name, name),
                         " (lúc đang nạp firmware)" if busy and name in BUSY_ACTIONS else "")
        try:
            message = action()
        except OSError as e:
            self._json(500, {"error": str(e)})
            return
        self.server.collector.wake.set()
        self._json(200, {"ok": True, "message": message})
