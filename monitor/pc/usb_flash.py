"""
Nạp firmware màn phụ qua cáp USB (esptool) khi board đang chạy DeskMon.

Không cần WiFi hay trang web của DeskMon (log 15/11: web DeskMon cắt kết nối sau một lần nạp
qua WiFi bị ngắt giữa chừng, nên không nạp lại được). Không bao giờ ghi đè DeskMon:
1. đọc otadata (vùng nào đang được chọn để khởi động) và 16 KB đầu của hai vùng firmware;
2. ghi màn phụ vào vùng KHÔNG chứa DeskMon;
3. ghi otadata chọn vùng đó, rồi khởi động lại board.
"""
from __future__ import annotations

import struct
import subprocess
import sys
import tempfile
import zlib
from dataclasses import dataclass
from pathlib import Path

OTADATA_ADDR = 0xE000
SECTOR = 0x1000
SLOT_ADDRS = (0x10000, 0x1F0000)            # app0, app1 trong min_spiffs.csv
HEAD_LEN = 0x4000
IMAGE_MAGIC = 0xE9
DESKMON_MARK = b"DESKMON_READY"             # DeskMon in ra lúc khởi động, nằm gần đầu file firmware
APP_SHA = slice(0xB0, 0xD0)                 # esp_app_desc_t.app_elf_sha256 (ảnh + 0x20 + 0x90)
UNSET = 0xFFFFFFFF
STATE_VALID = 2
BAUD = 921600
READ_TIMEOUT_S = 60
WRITE_TIMEOUT_S = 180
ESPTOOL = Path.home() / ".platformio" / "packages" / "tool-esptoolpy" / "esptool.py"
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

DESKMON, MONITOR, EMPTY, OTHER = "deskmon", "monitor", "empty", "other"


class UsbFlashError(Exception):
    pass


@dataclass(frozen=True)
class OtaEntry:
    seq: int
    state: int
    valid: bool


def ota_crc(seq: int) -> int:
    """= esp_rom_crc32_le(UINT32_MAX, &seq, 4) của bootloader (đã đối chiếu với otadata thật)."""
    return zlib.crc32(struct.pack("<I", seq), UNSET)


def parse_otadata(raw: bytes) -> list[OtaEntry]:
    entries = []
    for i in range(2):
        seq, = struct.unpack_from("<I", raw, i * SECTOR)
        state, crc = struct.unpack_from("<II", raw, i * SECTOR + 24)
        entries.append(OtaEntry(seq, state, seq != UNSET and crc == ota_crc(seq)))
    return entries


def active_slot(entries: list[OtaEntry]) -> int:
    """Vùng bootloader sẽ chạy: mục hợp lệ có seq lớn nhất, (seq - 1) % 2. Không có mục nào thì app0."""
    valid = [e for e in entries if e.valid]
    return (max(e.seq for e in valid) - 1) % 2 if valid else 0


def otadata_write(entries: list[OtaEntry], slot: int) -> tuple[int, bytes]:
    """(địa chỉ sector otadata cần ghi, nội dung 4 KB) để lần khởi động sau chạy `slot`.
    Ghi vào mục cũ hơn, giữ nguyên mục đang dùng (giống esp_ota_set_boot_partition)."""
    valid = [e for e in entries if e.valid]
    seq = max((e.seq for e in valid), default=0) + 1
    while (seq - 1) % 2 != slot:
        seq += 1
    newest = max(range(2), key=lambda i: entries[i].seq if entries[i].valid else -1) if valid else 1
    index = 1 - newest
    entry = struct.pack("<I20sII", seq, b"\xff" * 20, STATE_VALID, ota_crc(seq))
    return OTADATA_ADDR + index * SECTOR, entry + b"\xff" * (SECTOR - len(entry))


def classify_slot(head: bytes, monitor_sha: bytes) -> str:
    if not head or head[0] != IMAGE_MAGIC:
        return EMPTY                         # trống, hoặc nạp dở (Update ghi byte đầu sau cùng)
    if DESKMON_MARK in head:
        return DESKMON
    if head[APP_SHA] == monitor_sha:
        return MONITOR
    return OTHER


@dataclass(frozen=True)
class Plan:
    target: int | None                       # vùng ghi màn phụ; None = không cần ghi gì
    write_app: bool
    reason: str


def plan_flash(running: int, kinds: list[str]) -> Plan:
    if kinds[running] == MONITOR:
        return Plan(None, False, "board đang được chọn chạy màn phụ, chỉ cần khởi động lại")
    deskmon = [i for i, k in enumerate(kinds) if k == DESKMON]
    target = 1 - deskmon[0] if len(deskmon) == 1 else 1 - running
    if kinds[target] == MONITOR:
        return Plan(target, False, f"vùng {target} đã có màn phụ, chỉ chọn lại vùng khởi động")
    keep = f"DeskMon ở vùng {deskmon[0]} giữ nguyên" if deskmon else "không thấy DeskMon trên board"
    return Plan(target, True, f"ghi màn phụ vào vùng {target}, {keep}")


def _esptool(port: str, args: list[str], timeout: float, after: str = "hard_reset") -> str:
    if not ESPTOOL.is_file():
        raise UsbFlashError(f"Không thấy esptool ở {ESPTOOL} (cài PlatformIO)")
    cmd = [sys.executable, str(ESPTOOL), "--chip", "esp32", "--port", port, "--baud", str(BAUD),
           "--after", after, *args]
    try:
        done = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, creationflags=NO_WINDOW)
    except (OSError, subprocess.SubprocessError) as e:
        raise UsbFlashError(f"esptool lỗi: {e}") from e
    if done.returncode != 0:
        tail = (done.stdout + done.stderr).strip().splitlines()[-3:]
        raise UsbFlashError("esptool lỗi: " + " | ".join(tail))
    return done.stdout


def read_board(port: str, tmp_dir: Path) -> tuple[list[OtaEntry], list[bytes]]:
    """otadata + 16 KB đầu hai vùng. Board đứng ở chế độ nạp giữa hai lần đọc (no_reset)."""
    first, second = tmp_dir / "head0.bin", tmp_dir / "head1.bin"
    _esptool(port, ["read_flash", hex(OTADATA_ADDR), hex(SLOT_ADDRS[0] - OTADATA_ADDR + HEAD_LEN), str(first)],
             READ_TIMEOUT_S, after="no_reset")
    _esptool(port, ["read_flash", hex(SLOT_ADDRS[1]), hex(HEAD_LEN), str(second)], READ_TIMEOUT_S, after="no_reset")
    raw = first.read_bytes()
    return parse_otadata(raw[:2 * SECTOR]), [raw[SLOT_ADDRS[0] - OTADATA_ADDR:], second.read_bytes()]


def flash_monitor(port: str, firmware: Path) -> str:
    """Đưa board về firmware màn phụ qua USB. Trả câu mô tả đã làm gì; lỗi thì UsbFlashError."""
    if not firmware.is_file():
        raise UsbFlashError(f"Không thấy {firmware} (build ở thư mục monitor/ trước)")
    monitor_sha = firmware.read_bytes()[APP_SHA]
    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)
        entries, heads = read_board(port, tmp_dir)
        kinds = [classify_slot(h, monitor_sha) for h in heads]
        running = active_slot(entries)
        plan = plan_flash(running, kinds)
        before = f"trước đó: vùng 0 {kinds[0]}, vùng 1 {kinds[1]}, đang chọn vùng {running}"
        if plan.target is None:
            _esptool(port, ["read_mac"], READ_TIMEOUT_S)          # chỉ để khởi động lại board
            return f"{plan.reason} ({before})"
        ota_addr, sector = otadata_write(entries, plan.target)
        ota_file = tmp_dir / "otadata.bin"
        ota_file.write_bytes(sector)
        if plan.write_app:      # ghi ảnh trước, xong hẳn mới đổi vùng khởi động; esptool tự kiểm MD5
            _esptool(port, ["write_flash", hex(SLOT_ADDRS[plan.target]), str(firmware)], WRITE_TIMEOUT_S,
                     after="no_reset")
        _esptool(port, ["write_flash", hex(ota_addr), str(ota_file)], WRITE_TIMEOUT_S)
    return f"{plan.reason} ({before})"
