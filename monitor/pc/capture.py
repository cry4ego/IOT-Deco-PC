"""
Tìm màn hình ảo của Windows và chụp hình nó bằng GDI (ctypes, không cần cài thêm thư viện).
Con trỏ chuột được vẽ luôn vào ảnh chụp, vì Windows không tự đưa con trỏ vào ảnh.
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes
from dataclasses import dataclass

import numpy as np

# Tên card màn hình ảo hay gặp: Virtual Display Driver, mẫu IddSample, Parsec, Amyuni usbmmidd
VIRTUAL_HINTS = ("virtual", "iddsample", "parsec", "usbmmidd")


@dataclass(frozen=True)
class Monitor:
    device: str       # \\.\DISPLAYn
    adapter: str      # tên card màn hình (driver)
    left: int
    top: int
    width: int
    height: int
    primary: bool


def pick_virtual(monitors: list[Monitor]):
    for m in monitors:
        if any(h in m.adapter.lower() for h in VIRTUAL_HINTS):
            return m
    return None


def fit_rect(src_w: int, src_h: int, dst_w: int, dst_h: int) -> tuple[int, int, int, int]:
    """Thu nhỏ giữ tỉ lệ cho vừa khung đích, đặt giữa. Trả về (w, h, x, y)."""
    scale = min(dst_w / src_w, dst_h / src_h)
    w, h = round(src_w * scale), round(src_h * scale)
    return w, h, (dst_w - w) // 2, (dst_h - h) // 2


def parse_region(text: str) -> tuple[int, int, int, int]:
    parts = [int(p) for p in text.split(",")]
    if len(parts) != 4:
        raise ValueError("vùng phải có dạng x,y,rộng,cao")
    return parts[0], parts[1], parts[2], parts[3]


# ---------------------------------------------------------------- Windows API
user32 = ctypes.WinDLL("user32", use_last_error=True)
gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)

SRCCOPY, CAPTUREBLT = 0x00CC0020, 0x40000000
CURSOR_SHOWING, DI_NORMAL = 0x1, 0x3
MONITORINFOF_PRIMARY = 0x1


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG), ("biHeight", wintypes.LONG),
                ("biPlanes", wintypes.WORD), ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", wintypes.LONG),
                ("biYPelsPerMeter", wintypes.LONG), ("biClrUsed", wintypes.DWORD),
                ("biClrImportant", wintypes.DWORD)]


class BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", wintypes.DWORD * 3)]


class CURSORINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("flags", wintypes.DWORD), ("hCursor", wintypes.HANDLE),
                ("ptScreenPos", wintypes.POINT)]


class ICONINFO(ctypes.Structure):
    _fields_ = [("fIcon", wintypes.BOOL), ("xHotspot", wintypes.DWORD), ("yHotspot", wintypes.DWORD),
                ("hbmMask", wintypes.HBITMAP), ("hbmColor", wintypes.HBITMAP)]


class MONITORINFOEXW(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT), ("rcWork", wintypes.RECT),
                ("dwFlags", wintypes.DWORD), ("szDevice", wintypes.WCHAR * 32)]


class DISPLAY_DEVICEW(ctypes.Structure):
    _fields_ = [("cb", wintypes.DWORD), ("DeviceName", wintypes.WCHAR * 32),
                ("DeviceString", wintypes.WCHAR * 128), ("StateFlags", wintypes.DWORD),
                ("DeviceID", wintypes.WCHAR * 128), ("DeviceKey", wintypes.WCHAR * 128)]


MONITORENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HMONITOR, wintypes.HDC,
                                     ctypes.POINTER(wintypes.RECT), wintypes.LPARAM)

user32.GetDC.restype = wintypes.HDC
user32.GetDC.argtypes = [wintypes.HWND]
user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
user32.EnumDisplayMonitors.argtypes = [wintypes.HDC, ctypes.c_void_p, MONITORENUMPROC, wintypes.LPARAM]
user32.GetMonitorInfoW.argtypes = [wintypes.HMONITOR, ctypes.POINTER(MONITORINFOEXW)]
user32.EnumDisplayDevicesW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, ctypes.POINTER(DISPLAY_DEVICEW),
                                       wintypes.DWORD]
user32.GetCursorInfo.argtypes = [ctypes.POINTER(CURSORINFO)]
user32.GetIconInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(ICONINFO)]
user32.DrawIconEx.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int, wintypes.HANDLE, ctypes.c_int,
                              ctypes.c_int, wintypes.UINT, wintypes.HBRUSH, wintypes.UINT]
gdi32.CreateCompatibleDC.restype = wintypes.HDC
gdi32.CreateCompatibleDC.argtypes = [wintypes.HDC]
gdi32.CreateDIBSection.restype = wintypes.HBITMAP
gdi32.CreateDIBSection.argtypes = [wintypes.HDC, ctypes.POINTER(BITMAPINFO), wintypes.UINT,
                                   ctypes.POINTER(ctypes.c_void_p), wintypes.HANDLE, wintypes.DWORD]
gdi32.SelectObject.restype = wintypes.HGDIOBJ
gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
gdi32.BitBlt.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.HDC,
                         ctypes.c_int, ctypes.c_int, wintypes.DWORD]
gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
gdi32.DeleteDC.argtypes = [wintypes.HDC]


def set_dpi_aware():
    """Toạ độ theo điểm ảnh thật, kể cả khi màn chính để tỉ lệ 125%/150%."""
    try:
        user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))   # PER_MONITOR_AWARE_V2
    except (AttributeError, OSError):
        ctypes.windll.shcore.SetProcessDpiAwareness(2)


def _adapter_names() -> dict[str, str]:
    names, i = {}, 0
    dd = DISPLAY_DEVICEW(cb=ctypes.sizeof(DISPLAY_DEVICEW))
    while user32.EnumDisplayDevicesW(None, i, ctypes.byref(dd), 0):
        names[dd.DeviceName] = dd.DeviceString
        i += 1
    return names


def list_monitors() -> list[Monitor]:
    adapters = _adapter_names()
    found: list[Monitor] = []

    def callback(hmon, _hdc, _rect, _data):
        info = MONITORINFOEXW(cbSize=ctypes.sizeof(MONITORINFOEXW))
        if user32.GetMonitorInfoW(hmon, ctypes.byref(info)):
            r = info.rcMonitor
            found.append(Monitor(info.szDevice, adapters.get(info.szDevice, ""), r.left, r.top,
                                 r.right - r.left, r.bottom - r.top, bool(info.dwFlags & MONITORINFOF_PRIMARY)))
        return True

    user32.EnumDisplayMonitors(None, None, MONITORENUMPROC(callback), 0)
    return found


class Grabber:
    """Chụp một vùng màn hình (toạ độ desktop ảo) vào bộ nhớ dùng lại mỗi lần."""

    def __init__(self, left: int, top: int, width: int, height: int):
        self.left, self.top, self.width, self.height = left, top, width, height
        self.screen_dc = user32.GetDC(None)
        self.mem_dc = gdi32.CreateCompatibleDC(self.screen_dc)
        bmi = BITMAPINFO()
        bmi.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        bmi.bmiHeader.biWidth = width
        bmi.bmiHeader.biHeight = -height            # âm = hàng trên cùng trước
        bmi.bmiHeader.biPlanes = 1
        bmi.bmiHeader.biBitCount = 32
        bits = ctypes.c_void_p()
        self.bitmap = gdi32.CreateDIBSection(self.mem_dc, ctypes.byref(bmi), 0, ctypes.byref(bits), None, 0)
        if not self.bitmap:
            raise OSError("Không tạo được bộ nhớ ảnh chụp")
        self.old = gdi32.SelectObject(self.mem_dc, self.bitmap)
        buf = (ctypes.c_ubyte * (width * height * 4)).from_address(bits.value)
        self.pixels = np.ctypeslib.as_array(buf).reshape(height, width, 4)

    def grab(self) -> np.ndarray:
        """Ảnh RGB (cao, rộng, 3), đã có con trỏ chuột nếu nó nằm trong vùng."""
        gdi32.BitBlt(self.mem_dc, 0, 0, self.width, self.height, self.screen_dc, self.left, self.top,
                     SRCCOPY | CAPTUREBLT)
        self._draw_cursor()
        return self.pixels[:, :, 2::-1].copy()        # BGRA -> RGB

    def _draw_cursor(self):
        ci = CURSORINFO(cbSize=ctypes.sizeof(CURSORINFO))
        if not user32.GetCursorInfo(ctypes.byref(ci)) or not ci.flags & CURSOR_SHOWING:
            return
        x, y = ci.ptScreenPos.x - self.left, ci.ptScreenPos.y - self.top
        if not (-64 < x < self.width and -64 < y < self.height):
            return
        ii = ICONINFO()
        if user32.GetIconInfo(ci.hCursor, ctypes.byref(ii)):
            x -= ii.xHotspot
            y -= ii.yHotspot
            for bmp in (ii.hbmMask, ii.hbmColor):
                if bmp:
                    gdi32.DeleteObject(bmp)
        user32.DrawIconEx(self.mem_dc, x, y, ci.hCursor, 0, 0, 0, None, DI_NORMAL)

    def close(self):
        gdi32.SelectObject(self.mem_dc, self.old)
        gdi32.DeleteObject(self.bitmap)
        gdi32.DeleteDC(self.mem_dc)
        user32.ReleaseDC(None, self.screen_dc)
