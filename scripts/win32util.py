"""Minimal Win32 helpers: enumerate windows, foreground, screenshot via ctypes + Pillow.

Kept dependency-light on purpose (Pillow only) so it works on any Python 3.10+ on Windows.
Only dependency: Pillow (`pip install pillow`).
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
from dataclasses import dataclass

user32 = ctypes.WinDLL("user32", use_last_error=True)
gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)

SW_SHOW = 5
SW_RESTORE = 9
SW_MAXIMIZE = 3
MOUSEEVENTF_WHEEL = 0x0800
MOUSEEVENTF_LEFTDOWN = 0x02
MOUSEEVENTF_LEFTUP = 0x04


@dataclass
class WinInfo:
    hwnd: int
    title: str
    cls: str
    rect: tuple[int, int, int, int]  # left, top, right, bottom
    visible: bool

    @property
    def box(self) -> tuple[int, int, int, int]:
        l, t, r, b = self.rect
        return (l, t, r - l, b - t)


def set_dpi_aware() -> None:
    """MUST call before any coordinate work or every x/y will be wrong on scaled displays."""
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:  # noqa: BLE001
        ctypes.windll.user32.SetProcessDPIAware()


def _enum_windows() -> list[int]:
    hwnds: list[int] = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, wt.HWND, wt.LPARAM)
    def cb(hwnd, _lparam):
        hwnds.append(int(hwnd))
        return True

    user32.EnumWindows(cb, 0)
    return hwnds


def list_windows(min_width: int = 200) -> list[WinInfo]:
    """Note: minimized windows have IsWindowVisible=0 and are NOT listed here."""
    out: list[WinInfo] = []
    buf_t = ctypes.create_unicode_buffer(512)
    buf_c = ctypes.create_unicode_buffer(512)
    rect = wt.RECT()
    for h in _enum_windows():
        if not user32.IsWindowVisible(h):
            continue
        user32.GetWindowTextW(h, buf_t, 512)
        user32.GetClassNameW(h, buf_c, 512)
        user32.GetWindowRect(h, ctypes.byref(rect))
        w = rect.right - rect.left
        if w < min_width:
            continue
        out.append(
            WinInfo(
                hwnd=h,
                title=buf_t.value,
                cls=buf_c.value,
                rect=(rect.left, rect.top, rect.right, rect.bottom),
                visible=True,
            )
        )
    return out


def find_windows(keyword: str) -> list[WinInfo]:
    kw = keyword.lower()
    return [
        w
        for w in list_windows(min_width=100)
        if kw in w.title.lower() or kw in w.cls.lower()
    ]


def restore(hwnd: int, maximize: bool = True) -> tuple[int, int, int, int]:
    """Restore a minimized window. Minimized windows report rect (-25600,-25600)."""
    user32.ShowWindow(hwnd, SW_RESTORE)
    user32.ShowWindow(hwnd, SW_SHOW)
    if maximize:
        user32.ShowWindow(hwnd, SW_MAXIMIZE)
    return _rect(hwnd)


def focus(hwnd: int, maximize: bool = False) -> None:
    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, SW_RESTORE)
    user32.ShowWindow(hwnd, SW_SHOW)
    if maximize:
        user32.ShowWindow(hwnd, SW_MAXIMIZE)
    user32.SetForegroundWindow(hwnd)


def click_at(x: int, y: int) -> None:
    user32.SetCursorPos(int(x), int(y))
    user32.mouse_event(MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
    user32.mouse_event(MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)


def wheel_at(x: int, y: int, clicks: int) -> None:
    """clicks positive=up, negative=down. 120 units per notch."""
    user32.SetCursorPos(int(x), int(y))
    user32.mouse_event(MOUSEEVENTF_WHEEL, 0, 0, int(clicks * 120), 0)


def scroll(hwnd: int, clicks: int, cx: int | None = None, cy: int | None = None) -> None:
    l, t, r, b = _rect(hwnd)
    if cx is None:
        cx = (l + r) // 2
    if cy is None:
        cy = (t + b) // 2
    wheel_at(cx, cy, clicks)


def _rect(hwnd: int) -> tuple[int, int, int, int]:
    rect = wt.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(rect))
    return (rect.left, rect.top, rect.right, rect.bottom)


def screenshot(box: tuple[int, int, int, int]):
    """Capture a screen region (x, y, width, height) -> PIL.Image."""
    from PIL import ImageGrab

    x, y, w, h = box
    return ImageGrab.grab(bbox=(x, y, x + w, y + h), all_screens=True)


def screenshot_window(hwnd: int):
    l, t, r, b = _rect(hwnd)
    return screenshot((l, t, r - l, b - t))


def dhash(img, size: int = 16) -> int:
    """Difference hash used to detect 'page did not change' while scrolling."""
    from PIL import Image

    g = img.convert("L").resize((size + 1, size), Image.Resampling.LANCZOS)
    px = list(g.getdata())
    bits = 0
    for row in range(size):
        base = row * (size + 1)
        for col in range(size):
            bits = (bits << 1) | (1 if px[base + col] > px[base + col + 1] else 0)
    return bits


def hamming(a: int, b: int) -> int:
    return bin(a ^ b).count("1")
