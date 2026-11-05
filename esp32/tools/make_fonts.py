"""
Sinh phông VLW (smooth font của LovyanGFX) có dấu tiếng Việt cho chế độ offline của ESP32.

Chạy:  python esp32/tools/make_fonts.py
Ra:    esp32/deskmon_esp32/fonts_vi.h

Định dạng VLW (big-endian):
  header 6 x int32 : số glyph, version (11), cỡ chữ, 0, ascent, descent
  mỗi glyph 7 x int32: unicode, cao, rộng, xAdvance, dY (đỉnh so với baseline), dX, 0
  sau đó là bitmap 8-bit (độ phủ) của từng glyph, theo đúng thứ tự trên.
Glyph phải xếp tăng dần theo unicode (LovyanGFX tìm bằng lower_bound). Không cần ký tự
dấu cách: LovyanGFX tự chèn khoảng trống.
"""
import struct
import sys
import unicodedata
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

FONT_DIR = Path(r"C:\Windows\Fonts")
OUT = Path(__file__).resolve().parent.parent / "deskmon_esp32" / "fonts_vi.h"


def vietnamese_letters() -> str:
    tones = ["", "\u0300", "\u0301", "\u0309", "\u0303", "\u0323"]
    letters = {"đ", "Đ"}
    for v in "aăâeêioôơuưy":
        for t in tones:
            for base in (v, v.upper()):
                letters.add(unicodedata.normalize("NFC", base + t))
    return "".join(sorted(letters))


ASCII = "".join(chr(c) for c in range(0x21, 0x7F))
TEXT_CHARS = ASCII + "°·–" + vietnamese_letters()

# (tên mảng, file TTF, cỡ px, tập ký tự)
FONTS = [
    ("FONT_CLOCK_S", "seguisb.ttf", 88, "0123456789:"),
    ("FONT_CLOCK", "seguisb.ttf", 108, "0123456789:"),
    ("FONT_CLOCK_L", "seguisb.ttf", 128, "0123456789:"),
    ("FONT_BIG", "seguisb.ttf", 54, "0123456789-°:"),
    ("FONT_MED", "seguisb.ttf", 22, TEXT_CHARS),
    ("FONT_SMALL", "segoeui.ttf", 16, TEXT_CHARS),
    ("FONT_TINY", "segoeui.ttf", 11, "0123456789/"),
]


def build_vlw(ttf: Path, size: int, chars: str) -> bytes:
    font = ImageFont.truetype(str(ttf), size)
    ascent, descent = font.getmetrics()
    headers, bitmaps = [], []
    for ch in sorted(set(chars), key=ord):
        left, top, right, bottom = font.getbbox(ch, anchor="ls")
        w, h = right - left, bottom - top
        if w <= 0 or h <= 0:
            continue
        img = Image.new("L", (w, h), 0)
        ImageDraw.Draw(img).text((-left, -top), ch, font=font, fill=255, anchor="ls")
        advance = round(font.getlength(ch))
        if not (0 < w < 256 and 0 < advance < 256 and -128 <= left < 128):
            raise ValueError(f"glyph {ch!r} cỡ {size} vượt giới hạn VLW (w={w}, adv={advance}, dX={left})")
        headers.append(struct.pack(">7i", ord(ch), h, w, advance, -top, left, 0))
        bitmaps.append(img.tobytes())
    head = struct.pack(">6i", len(headers), 11, size, 0, ascent, descent)
    return head + b"".join(headers) + b"".join(bitmaps)


def to_c_array(name: str, data: bytes) -> str:
    rows = [", ".join(f"0x{b:02X}" for b in data[i:i + 24]) for i in range(0, len(data), 24)]
    body = ",\n  ".join(rows)
    return f"static const uint8_t {name}[{len(data)}] = {{\n  {body}\n}};\n"


def main() -> int:
    parts = [
        "// Sinh tự động bởi esp32/tools/make_fonts.py - không sửa tay.\n",
        "// Phông Segoe UI (Windows) dạng VLW cho LovyanGFX: lcd.loadFont(FONT_xxx).\n",
        "#pragma once\n#include <stdint.h>\n\n",
    ]
    for name, file, size, chars in FONTS:
        data = build_vlw(FONT_DIR / file, size, chars)
        parts.append(to_c_array(name, data))
        parts.append("\n")
        print(f"{name:11s} {file:12s} {size:3d}px  {len(data) / 1024:6.1f} KB")
    OUT.write_text("".join(parts), encoding="utf-8")
    print(f"-> {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
