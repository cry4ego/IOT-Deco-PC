"""Đo và ngắt chữ theo độ rộng pixel (dùng chung cho các phần vẽ màn hình)."""
from __future__ import annotations


def text_w(font, text: str) -> float:
    return font.getlength(text)


def ellipsize(font, text: str, max_w: float) -> str:
    if text_w(font, text) <= max_w:
        return text
    lo, hi = 0, len(text)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if text_w(font, text[:mid].rstrip() + "…") <= max_w:
            lo = mid
        else:
            hi = mid - 1
    return text[:lo].rstrip() + "…"


def wrap_text(font, text: str, max_w: float) -> list[str]:
    """Xuống dòng theo độ rộng pixel. Văn bản không có dấu cách (CJK) thì cắt theo ký tự."""
    text = text.strip()
    if not text:
        return [""]
    tokens = text.split(" ") if " " in text else list(text)
    joiner = " " if " " in text else ""
    lines, cur = [], ""
    for tok in tokens:
        cand = (cur + joiner + tok) if cur else tok
        if text_w(font, cand) <= max_w:
            cur = cand
            continue
        if cur:
            lines.append(cur)
        # từ quá dài: cắt theo ký tự
        while text_w(font, tok) > max_w and len(tok) > 1:
            cut = len(tok)
            while cut > 1 and text_w(font, tok[:cut]) > max_w:
                cut -= 1
            lines.append(tok[:cut])
            tok = tok[cut:]
        cur = tok
    if cur:
        lines.append(cur)
    return lines
