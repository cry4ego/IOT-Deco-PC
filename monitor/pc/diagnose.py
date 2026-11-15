"""
Từ những gì bảng điều khiển đọc được (trạng thái chương trình, board, Windows, cáp USB),
kết luận màn phụ đang ổn hay hỏng ở đâu và nên làm gì. Chỉ là logic, không đọc gì bên ngoài.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import status_file as st

OK, WARN, BAD = "ok", "warn", "bad"
RSSI_GOOD, RSSI_WEAK = -60, -70
RECENT_ERROR_S = 120.0


@dataclass(frozen=True)
class Snapshot:
    now: float
    program: Optional[dict]            # man_phu_status.json
    program_alive: bool
    monitor: Optional[tuple]           # màn ảo (trái, trên, rộng, cao) hoặc None
    usb_port: Optional[str]
    board: Optional[dict]              # /api/status của board, None nếu không hỏi được
    lhm_ok: bool


@dataclass(frozen=True)
class Check:
    name: str
    level: str
    text: str


@dataclass(frozen=True)
class Report:
    level: str
    title: str
    hint: str
    checks: list = field(default_factory=list)


def format_duration(seconds: float) -> str:
    s = max(0, int(seconds))
    if s < 60:
        return f"{s} giây"
    if s < 3600:
        return f"{s // 60} phút"
    h, m = divmod(s // 60, 60)
    return f"{h} giờ {m} phút" if m else f"{h} giờ"


def board_is_deskmon(board: Optional[dict]) -> bool:
    return bool(board) and "fw" in board


def _program_check(snap: Snapshot) -> Check:
    p = snap.program
    if not snap.program_alive:
        return Check("Chương trình màn phụ", BAD, "Không chạy")
    if p is None:
        return Check("Chương trình màn phụ", WARN, "Đang chạy (bản cũ, không báo trạng thái)")
    if st.is_stale(p, snap.now):
        return Check("Chương trình màn phụ", BAD,
                     f"Không phản hồi {format_duration(snap.now - p.get('updated', 0))} (có thể đang kẹt)")
    label = st.STATE_LABELS.get(p.get("state"), p.get("state", "?"))
    level = OK if p.get("state") == st.CONNECTED else WARN
    return Check("Chương trình màn phụ", level,
                 f"{label} · chạy được {format_duration(snap.now - p.get('started', snap.now))}")


def _board_checks(snap: Snapshot) -> list:
    b = snap.board
    if b is None:
        usb_ok = snap.program and snap.program.get("state") == st.CONNECTED and "USB" in snap.program.get("link", "")
        return [Check("Board ESP32", WARN if usb_ok else BAD,
                      "Không trả lời qua WiFi" + (" (USB vẫn chạy)" if usb_ok else ""))]
    uptime = format_duration(b.get("uptime", 0))
    if board_is_deskmon(b):
        checks = [Check("Board ESP32", WARN, f"Đang chạy DeskMon {b.get('fw')} · bật được {uptime}")]
    else:
        checks = [Check("Board ESP32", OK, f"Màn phụ {b.get('firmware', '?')} · bật được {uptime}")]
        slot = b.get("deskmonSlot")
        if slot is not None:
            checks.append(Check("DeskMon dự phòng", OK if slot else WARN,
                                "Có (tắt PC sẽ về DeskMon)" if slot else "Thiếu, chương trình sẽ tự nạp lại"))
    rssi = b.get("rssi")
    if isinstance(rssi, (int, float)):
        level = OK if rssi >= RSSI_GOOD else WARN if rssi >= RSSI_WEAK else BAD
        word = "tốt" if level == OK else "hơi yếu" if level == WARN else "yếu"
        checks.append(Check("WiFi của board", level, f"{rssi:.0f} dBm ({word})"))
    return checks


def _link_check(snap: Snapshot) -> Check:
    p = snap.program
    if snap.program_alive and p and p.get("state") == st.CONNECTED and not st.is_stale(p, snap.now):
        return Check("Đường truyền", OK,
                     f"{p.get('link', '?')} · {p.get('fps', 0):.1f} khung/giây · {p.get('kbps', 0):.0f} KB/s")
    return Check("Đường truyền", BAD, "Chưa nối")


def checks_for(snap: Snapshot) -> list:
    checks = [_program_check(snap)]
    m = snap.monitor
    checks.append(Check("Màn hình ảo của Windows", OK if m else BAD,
                        f"{m[2]}×{m[3]} tại ({m[0]}, {m[1]})" if m else "Không thấy (driver chưa chạy)"))
    checks.append(_link_check(snap))
    checks.append(Check("Cáp USB", OK if snap.usb_port else WARN,
                        f"Có ({snap.usb_port})" if snap.usb_port else "Không cắm, đang dùng WiFi (chậm hơn)"))
    checks.extend(_board_checks(snap))
    checks.append(Check("LibreHardwareMonitor", OK if snap.lhm_ok else WARN,
                        "Đang chạy" if snap.lhm_ok else "Không chạy (màn 1.54\" thiếu nhiệt độ, GPU)"))
    p = snap.program or {}
    if p.get("last_error"):
        ago = snap.now - p.get("last_error_at", 0)
        checks.append(Check("Lỗi gần nhất", WARN if ago < RECENT_ERROR_S else OK,
                            f"{format_duration(ago)} trước: {p['last_error']}"))
    return checks


def verdict(snap: Snapshot) -> tuple[str, str, str]:
    p = snap.program or {}
    state = p.get("state")
    if not snap.program_alive:
        return BAD, "Chương trình màn phụ không chạy", "Bấm «Chạy màn phụ»."
    if snap.program and st.is_stale(snap.program, snap.now):
        return (BAD, "Chương trình màn phụ không phản hồi",
                "Nó tự chạy lại sau 45 giây. Nếu vẫn vậy, bấm «Khởi động lại».")
    if not snap.monitor or state == st.NO_MONITOR:
        return (BAD, "Windows chưa nhận màn hình ảo",
                "Bấm «Cài đặt màn hình» xem có màn 800×600 không. Không có thì mở Virtual Driver Control, "
                "bấm Reload Driver (hoặc khởi động lại máy).")
    if state == st.REFLASHING:
        how = p.get("detail") or "qua WiFi"
        return (WARN, f"Đang nạp lại firmware màn phụ {how}",
                "Mất khoảng 20–40 giây. Đừng rút điện hay cáp board, đừng bấm «Khởi động lại» lúc này.")
    if state == st.INSTALLING_DESKMON:
        return WARN, "Đang nạp DeskMon vào vùng dự phòng", "Mất khoảng 20 giây, màn phụ vẫn chạy."
    if state == st.RETRY_WAIT:
        left = max(0.0, p.get("retry_at", 0) - snap.now)
        return (WARN, f"Nạp firmware màn phụ bị lỗi, tự thử lại sau {format_duration(left)}",
                f"Board đang chạy DeskMon. Muốn thử ngay: bấm «Khởi động lại». {p.get('last_error', '')}".strip())
    if state == st.CONNECTED:
        if "WiFi" in p.get("link", ""):
            return (OK, "Màn phụ đang chạy tốt (qua WiFi)",
                    "Cắm cáp USB dữ liệu từ board vào PC để mượt hơn (khoảng 22 khung/giây, không rớt kết nối).")
        return OK, "Màn phụ đang chạy tốt", "Không cần làm gì."
    if board_is_deskmon(snap.board):
        return (WARN, "Board đang chạy DeskMon, sắp nạp lại màn phụ",
                "Chương trình tự nạp lại qua WiFi, đợi khoảng 1 phút.")
    if snap.board is None and not snap.usb_port:
        return (BAD, "Không thấy board ESP32",
                "Kiểm tra board có điện và vào được WiFi, hoặc cắm cáp USB dữ liệu vào PC.")
    return WARN, "Đang kết nối ESP32", p.get("last_error", "") or "Đợi vài giây."


def diagnose(snap: Snapshot) -> Report:
    level, title, hint = verdict(snap)
    return Report(level, title, hint, checks_for(snap))
