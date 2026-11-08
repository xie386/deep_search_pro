# -*- coding: utf-8 -*-
"""悬浮球窗：Win32 分层窗口（方窗 + 圆形 alpha 掩码）。

口径见 `docs/v4.1/Verdict/01-悬浮球外壳-对话入口.md`：
- 窗口是方的，只有圆内有不透明像素；圆外点击穿透（`WM_NCHITTEST` → `HTTRANSPARENT`）；
- 全流程逻辑像素，物理像素 = 逻辑 × 系统倍率；
- 尺寸**创建后**再设定（创建参数在 200% 缩放下实测不可信）；
- 独立线程 + 自己的消息循环；对外接口线程安全（`PostMessage` 编组）。

不依赖 pywebview：球是位图，面板才是网页。
"""
from __future__ import annotations

import ctypes
import json
import re
import threading
import time
from ctypes import wintypes as w
from pathlib import Path

from ball_geometry import (Rect, anchor_panel, clamp_rect, default_ball_rect,
                           logical_to_physical, move_rect, physical_to_logical,
                           point_in_circle, scale_for_dpi)

user32 = ctypes.WinDLL("user32", use_last_error=True)
gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

HERE = Path(__file__).resolve().parent
ASSET_DIR = HERE / "assets"
DEFAULT_STATE_PATH = HERE.parents[1] / "data" / "ball_state.json"

# ── 常量 ──────────────────────────────────────────────────────────────
WS_POPUP = 0x80000000
WS_EX_LAYERED = 0x00080000
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_NOACTIVATE = 0x08000000
WS_EX_TOPMOST = 0x00000008
ULW_ALPHA = 0x00000002
AC_SRC_OVER, AC_SRC_ALPHA = 0, 1
SWP_NOSIZE, SWP_NOZORDER, SWP_NOACTIVATE = 0x0001, 0x0004, 0x0010
SWP_SHOWWINDOW = 0x0040
WM_DESTROY, WM_CLOSE, WM_NCHITTEST = 0x0002, 0x0010, 0x0084
WM_LBUTTONDOWN, WM_LBUTTONUP, WM_MOUSEMOVE, WM_RBUTTONUP = 0x0201, 0x0202, 0x0200, 0x0205
WM_ERASEBKGND, WM_HOTKEY = 0x0014, 0x0312
WM_APP = 0x8000
HTTRANSPARENT, HTCLIENT = -1, 1
SPI_GETWORKAREA = 0x0030
MOD_CONTROL, MOD_ALT = 0x0002, 0x0001
VK_SPACE = 0x20
HOTKEY_ID = 0xB411
CMD_STATE, CMD_TOGGLE, CMD_SHOW, CMD_HIDE = (WM_APP + 1, WM_APP + 2, WM_APP + 3, WM_APP + 4)
STATES = ("idle", "busy", "alert")

LRESULT = ctypes.c_ssize_t
WNDPROC = ctypes.WINFUNCTYPE(LRESULT, w.HWND, w.UINT, w.WPARAM, w.LPARAM)


class WNDCLASSW(ctypes.Structure):
    _fields_ = [("style", w.UINT), ("lpfnWndProc", WNDPROC), ("cbClsExtra", ctypes.c_int),
                ("cbWndExtra", ctypes.c_int), ("hInstance", w.HINSTANCE), ("hIcon", w.HICON),
                ("hCursor", w.HANDLE), ("hbrBackground", w.HBRUSH),
                ("lpszMenuName", w.LPCWSTR), ("lpszClassName", w.LPCWSTR)]


class POINT(ctypes.Structure):
    _fields_ = [("x", w.LONG), ("y", w.LONG)]


class SIZE(ctypes.Structure):
    _fields_ = [("cx", w.LONG), ("cy", w.LONG)]


class RECT(ctypes.Structure):
    _fields_ = [("left", w.LONG), ("top", w.LONG), ("right", w.LONG), ("bottom", w.LONG)]


class BLENDFUNCTION(ctypes.Structure):
    _fields_ = [("BlendOp", ctypes.c_byte), ("BlendFlags", ctypes.c_byte),
                ("SourceConstantAlpha", ctypes.c_byte), ("AlphaFormat", ctypes.c_byte)]


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", w.DWORD), ("biWidth", w.LONG), ("biHeight", w.LONG),
                ("biPlanes", w.WORD), ("biBitCount", w.WORD), ("biCompression", w.DWORD),
                ("biSizeImage", w.DWORD), ("biXPelsPerMeter", w.LONG),
                ("biYPelsPerMeter", w.LONG), ("biClrUsed", w.DWORD), ("biClrImportant", w.DWORD)]


class BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", w.DWORD * 3)]


# ── Win32 签名（64 位下必须显式，否则句柄会被截断） ────────────────────
user32.DefWindowProcW.restype = LRESULT
user32.DefWindowProcW.argtypes = [w.HWND, w.UINT, w.WPARAM, w.LPARAM]
user32.CreateWindowExW.restype = w.HWND
user32.CreateWindowExW.argtypes = [w.DWORD, w.LPCWSTR, w.LPCWSTR, w.DWORD, ctypes.c_int,
                                   ctypes.c_int, ctypes.c_int, ctypes.c_int, w.HWND, w.HMENU,
                                   w.HINSTANCE, w.LPVOID]
user32.GetDC.restype = w.HDC
user32.GetDC.argtypes = [w.HWND]
user32.ReleaseDC.argtypes = [w.HWND, w.HDC]
gdi32.CreateCompatibleDC.restype = w.HDC
gdi32.CreateCompatibleDC.argtypes = [w.HDC]
gdi32.CreateDIBSection.restype = w.HBITMAP
gdi32.CreateDIBSection.argtypes = [w.HDC, ctypes.POINTER(BITMAPINFO), w.UINT,
                                   ctypes.POINTER(ctypes.c_void_p), w.HANDLE, w.DWORD]
gdi32.SelectObject.restype = w.HGDIOBJ
gdi32.SelectObject.argtypes = [w.HDC, w.HGDIOBJ]
user32.UpdateLayeredWindow.restype = w.BOOL
user32.UpdateLayeredWindow.argtypes = [w.HWND, w.HDC, ctypes.POINTER(POINT),
                                       ctypes.POINTER(SIZE), w.HDC, ctypes.POINTER(POINT),
                                       w.COLORREF, ctypes.POINTER(BLENDFUNCTION), w.DWORD]

# 句柄类参数必须显式声明，否则 ctypes 默认按 32 位 int 传 —— 句柄一旦超过 2^31
# 就抛 OverflowError（实测把绘制线程直接打死，且是随机的）。
gdi32.DeleteObject.argtypes = [w.HGDIOBJ]
gdi32.DeleteObject.restype = w.BOOL
gdi32.DeleteDC.argtypes = [w.HDC]
gdi32.DeleteDC.restype = w.BOOL
user32.SetCapture.argtypes = [w.HWND]
user32.SetCapture.restype = w.HWND
user32.ReleaseCapture.argtypes = []
user32.PostMessageW.argtypes = [w.HWND, w.UINT, w.WPARAM, w.LPARAM]
user32.PostMessageW.restype = w.BOOL
user32.ShowWindow.argtypes = [w.HWND, ctypes.c_int]
user32.DestroyWindow.argtypes = [w.HWND]
user32.SetWindowPos.argtypes = [w.HWND, w.HWND, ctypes.c_int, ctypes.c_int,
                                ctypes.c_int, ctypes.c_int, w.UINT]
user32.GetWindowRect.argtypes = [w.HWND, ctypes.POINTER(RECT)]
user32.GetWindowLongW.argtypes = [w.HWND, ctypes.c_int]
user32.SetCursorPos.argtypes = [ctypes.c_int, ctypes.c_int]
user32.GetCursorPos.argtypes = [ctypes.POINTER(POINT)]
user32.CreatePopupMenu.restype = w.HMENU
user32.CreatePopupMenu.argtypes = []
user32.AppendMenuW.argtypes = [w.HMENU, w.UINT, w.WPARAM, w.LPCWSTR]
user32.TrackPopupMenu.argtypes = [w.HMENU, w.UINT, ctypes.c_int, ctypes.c_int,
                                  ctypes.c_int, w.HWND, ctypes.c_void_p]
user32.TrackPopupMenu.restype = ctypes.c_int
user32.DestroyMenu.argtypes = [w.HMENU]
user32.RegisterHotKey.argtypes = [w.HWND, ctypes.c_int, w.UINT, w.UINT]
user32.UnregisterHotKey.argtypes = [w.HWND, ctypes.c_int]
user32.LoadCursorW.restype = w.HANDLE
user32.LoadCursorW.argtypes = [w.HINSTANCE, w.LPCWSTR]
user32.RegisterClassW.argtypes = [ctypes.POINTER(WNDCLASSW)]
user32.GetMessageW.argtypes = [ctypes.POINTER(w.MSG), w.HWND, w.UINT, w.UINT]
user32.TranslateMessage.argtypes = [ctypes.POINTER(w.MSG)]
user32.DispatchMessageW.argtypes = [ctypes.POINTER(w.MSG)]
user32.IsWindow.argtypes = [w.HWND]
user32.GetDpiForSystem.restype = w.UINT
kernel32.GetModuleHandleW.restype = w.HINSTANCE
kernel32.GetModuleHandleW.argtypes = [w.LPCWSTR]


def _ensure_dpi_aware() -> None:
    """统一进程 DPI 口径：system-aware。

    不声明的话 `GetDpiForSystem()` 恒返回 96（Windows 把坐标虚拟化），
    球的物理尺寸会和 pywebview 窗口差一个倍率（实测踩过）。
    """
    try:
        ctypes.WinDLL("shcore").SetProcessDpiAwareness(1)      # PROCESS_SYSTEM_DPI_AWARE
        return
    except Exception:                      # noqa: BLE001
        pass
    try:
        user32.SetProcessDPIAware()
    except Exception:                      # noqa: BLE001
        pass


_HOTKEY_MODS = {"ctrl": 0x0002, "control": 0x0002, "alt": 0x0001, "shift": 0x0004, "win": 0x0008}
_HOTKEY_KEYS = {"space": 0x20, "tab": 0x09, "enter": 0x0D, "esc": 0x1B, "escape": 0x1B,
                "insert": 0x2D, "delete": 0x2E, "home": 0x24, "end": 0x23,
                "pageup": 0x21, "pagedown": 0x22}
_HOTKEY_KEYS.update({chr(c): c for c in range(65, 91)})
_HOTKEY_KEYS.update({str(d): 0x30 + d for d in range(10)})
_HOTKEY_KEYS.update({"f%d" % i: 0x70 + i - 1 for i in range(1, 13)})


def parse_hotkey(spec: str) -> tuple[int, int]:
    """'Ctrl+Alt+Space' → (modifiers, vk)。解析失败退回 Ctrl+Alt+Space。"""
    mods, vk = 0, 0
    for part in re.split(r"[+\-\s]+", (spec or "").strip().lower()):
        if not part:
            continue
        if part in _HOTKEY_MODS:
            mods |= _HOTKEY_MODS[part]
        elif part in _HOTKEY_KEYS:
            vk = _HOTKEY_KEYS[part]
    if not vk or not mods:
        return MOD_CONTROL | MOD_ALT, VK_SPACE
    return mods, vk


def _dpi_scale() -> float:
    try:
        return scale_for_dpi(user32.GetDpiForSystem())
    except Exception:                      # noqa: BLE001  老系统没有该 API
        return 1.0


def _work_area_logical(scale: float) -> Rect:
    r = RECT()
    if not user32.SystemParametersInfoW(SPI_GETWORKAREA, 0, ctypes.byref(r), 0):
        return Rect(0, 0, 1920, 1080)
    return Rect(physical_to_logical(r.left, scale), physical_to_logical(r.top, scale),
                physical_to_logical(r.right - r.left, scale),
                physical_to_logical(r.bottom - r.top, scale))


def _bgra_premultiplied(path: Path, px: int, opacity: int = 100) -> tuple[bytes, int, int]:
    """PNG → 预乘 alpha 的 BGRA 字节（UpdateLayeredWindow 只认这个格式）。"""
    from PIL import Image
    img = Image.open(path).convert("RGBA").resize((px, px), Image.LANCZOS)
    wpx, hpx = img.size
    src = img.tobytes()
    out = bytearray(len(src))
    for i in range(0, len(src), 4):
        r, g, b, a = src[i], src[i + 1], src[i + 2], src[i + 3]
        if opacity < 100:
            a = (a * opacity) // 100
        out[i] = (b * a + 127) // 255
        out[i + 1] = (g * a + 127) // 255
        out[i + 2] = (r * a + 127) // 255
        out[i + 3] = a
    return bytes(out), wpx, hpx


class BallWindow:
    """圆形悬浮球。所有公开方法都可在任意线程调用。"""

    def __init__(self, on_click=None, menu=(), on_hotkey=None, size: int = 64,
                 opacity: int = 100, hotkey: str = "Ctrl+Alt+Space",
                 state_path: Path | None = None, log=print):
        self.on_click = on_click
        self.on_hotkey = on_hotkey
        self.menu = list(menu)                # [(label, callback), ...]
        self.size = int(size)
        self.opacity = max(40, min(100, int(opacity)))
        self.hotkey = parse_hotkey(hotkey)
        self.state_path = Path(state_path) if state_path else DEFAULT_STATE_PATH
        self.log = log
        _ensure_dpi_aware()
        self.scale = _dpi_scale()
        self.state = "idle"
        self.hwnd = None
        self._thread = None
        self._ready = threading.Event()
        self._wndproc = WNDPROC(self._proc)
        self._cls = "ZxIntelBallWindow"
        self._dragging = False
        self._moved = False
        self._drag_origin = (0, 0)
        self._win_origin = (0, 0)
        self._rect = Rect(0, 0, self.size, self.size)
        self._visible = True

    # ── 对外接口 ──────────────────────────────────────────────────────
    def start(self, timeout: float = 3.0) -> bool:
        if self._thread and self._thread.is_alive():
            return True
        self._thread = threading.Thread(target=self._run, name="ball-window", daemon=True)
        self._thread.start()
        return self._ready.wait(timeout)

    def stop(self) -> None:
        if self.hwnd:
            user32.PostMessageW(self.hwnd, WM_CLOSE, 0, 0)
        if self._thread:
            self._thread.join(timeout=1.5)
        self.hwnd = None

    def set_state(self, state: str) -> None:
        idx = STATES.index(state) if state in STATES else 0
        if self.hwnd:
            user32.PostMessageW(self.hwnd, CMD_STATE, idx, 0)

    def toggle(self) -> None:
        if self.hwnd:
            user32.PostMessageW(self.hwnd, CMD_TOGGLE, 0, 0)

    def show(self) -> None:
        if self.hwnd:
            user32.PostMessageW(self.hwnd, CMD_SHOW, 0, 0)

    def hide(self) -> None:
        if self.hwnd:
            user32.PostMessageW(self.hwnd, CMD_HIDE, 0, 0)

    @property
    def visible(self) -> bool:
        return self._visible

    # ── 线程与消息循环 ────────────────────────────────────────────────
    def _run(self) -> None:
        hinst = kernel32.GetModuleHandleW(None)
        wc = WNDCLASSW()
        wc.style = 0
        wc.lpfnWndProc = self._wndproc
        wc.hInstance = hinst
        wc.hCursor = user32.LoadCursorW(None, ctypes.c_wchar_p(32649))   # IDC_HAND
        wc.lpszClassName = self._cls
        if not user32.RegisterClassW(ctypes.byref(wc)):
            err = ctypes.get_last_error()
            if err != 1410:            # 1410 = 类已注册
                self.log("[球] RegisterClass 失败 err=%s" % err)
                return

        work = _work_area_logical(self.scale)
        rect = self._load_rect(work)
        self._rect = rect
        self.log("[球] 落点 work=%s rect=%s size=%d scale=%.2f" % (work, rect, self.size, self.scale))
        px = logical_to_physical(self.size, self.scale)
        ex = WS_EX_LAYERED | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE | WS_EX_TOPMOST
        self.hwnd = user32.CreateWindowExW(
            ex, self._cls, "智选情报官悬浮球", WS_POPUP,
            logical_to_physical(rect.x, self.scale), logical_to_physical(rect.y, self.scale),
            px, px, None, None, hinst, None)
        if not self.hwnd:
            self.log("[球] CreateWindowEx 失败 err=%s" % ctypes.get_last_error())
            return

        # 尺寸以创建后再设一次为准（创建参数在 200% 缩放下实测会被 WinForms/DPI 干扰）
        user32.SetWindowPos(self.hwnd, None, logical_to_physical(rect.x, self.scale),
                            logical_to_physical(rect.y, self.scale), px, px,
                            SWP_NOZORDER | SWP_NOACTIVATE | SWP_SHOWWINDOW)
        self._paint()
        try:
            user32.RegisterHotKey(None, HOTKEY_ID, self.hotkey[0], self.hotkey[1])
        except Exception:              # noqa: BLE001
            pass
        self._ready.set()

        msg = w.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))
        user32.UnregisterHotKey(None, HOTKEY_ID)

    # ── 窗口过程 ──────────────────────────────────────────────────────
    def _proc(self, hwnd, msg, wparam, lparam):
        try:
            if msg == WM_NCHITTEST:
                sx, sy = self._lo(lparam), self._hi(lparam)
                r = self._rect_physical()
                inside = point_in_circle(sx - r.left, sy - r.top,
                                         logical_to_physical(self.size, self.scale))
                return HTCLIENT if inside else HTTRANSPARENT
            if msg == WM_ERASEBKGND:
                return 1
            if msg == WM_LBUTTONDOWN:
                self._begin_drag()
                return 0
            if msg == WM_MOUSEMOVE and self._dragging:
                self._drag_move()
                return 0
            if msg == WM_LBUTTONUP:
                self._end_drag()
                return 0
            if msg == WM_RBUTTONUP:
                self._popup_menu()
                return 0
            if msg == WM_HOTKEY and int(wparam) == HOTKEY_ID:
                if self.on_hotkey:
                    self.on_hotkey()
                return 0
            if msg == CMD_STATE:
                self.state = STATES[int(wparam)] if 0 <= int(wparam) < len(STATES) else "idle"
                self._paint()
                return 0
            if msg == CMD_TOGGLE:
                self._visible = not self._visible
                user32.ShowWindow(hwnd, 5 if self._visible else 0)   # SW_SHOW / SW_HIDE
                if self._visible:
                    self._paint()
                return 0
            if msg == CMD_SHOW:
                self._visible = True
                user32.ShowWindow(hwnd, 5)
                self._paint()
                return 0
            if msg == CMD_HIDE:
                self._visible = False
                user32.ShowWindow(hwnd, 0)
                return 0
            if msg == WM_CLOSE:
                user32.DestroyWindow(hwnd)
                return 0
            if msg == WM_DESTROY:
                user32.PostQuitMessage(0)
                return 0
        except Exception as e:         # noqa: BLE001  窗口过程绝不能把异常抛回系统
            self.log("[球] 窗口过程异常：%s" % e)
        return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

    # ── 绘制 ──────────────────────────────────────────────────────────
    def _paint(self) -> None:
        if not self.hwnd:
            return
        asset = ASSET_DIR / ("ball_%s.png" % (self.state if self.state in STATES else "idle"))
        px = logical_to_physical(self.size, self.scale)
        try:
            data, wpx, hpx = _bgra_premultiplied(asset, px, self.opacity)
        except Exception as e:         # noqa: BLE001  资产缺失不该让球消失
            self.log("[球] 贴图失败（%s）：%s" % (asset.name, e))
            return

        bmi = BITMAPINFO()
        bmi.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        bmi.bmiHeader.biWidth = wpx
        bmi.bmiHeader.biHeight = -hpx
        bmi.bmiHeader.biPlanes = 1
        bmi.bmiHeader.biBitCount = 32
        bits = ctypes.c_void_p()
        hdc_screen = user32.GetDC(None)
        hbm = gdi32.CreateDIBSection(hdc_screen, ctypes.byref(bmi), 0,
                                     ctypes.byref(bits), None, 0)
        if not hbm:
            user32.ReleaseDC(None, hdc_screen)
            return
        ctypes.memmove(bits, data, len(data))
        hdc_mem = gdi32.CreateCompatibleDC(hdc_screen)
        old = gdi32.SelectObject(hdc_mem, hbm)
        pt_dst = POINT(logical_to_physical(self._rect.x, self.scale),
                       logical_to_physical(self._rect.y, self.scale))
        size = SIZE(wpx, hpx)
        pt_src = POINT(0, 0)
        blend = BLENDFUNCTION(AC_SRC_OVER, 0, 255, AC_SRC_ALPHA)
        ok = user32.UpdateLayeredWindow(self.hwnd, hdc_screen, ctypes.byref(pt_dst),
                                        ctypes.byref(size), hdc_mem, ctypes.byref(pt_src),
                                        0, ctypes.byref(blend), ULW_ALPHA)
        if not ok:
            self.log("[球] UpdateLayeredWindow 失败 err=%s" % ctypes.get_last_error())
        gdi32.SelectObject(hdc_mem, old)
        gdi32.DeleteObject(hbm)
        gdi32.DeleteDC(hdc_mem)
        user32.ReleaseDC(None, hdc_screen)

    # ── 拖动 ──────────────────────────────────────────────────────────
    def _begin_drag(self) -> None:
        self._dragging = True
        self._moved = False
        pt = POINT()
        user32.GetCursorPos(ctypes.byref(pt))
        self._drag_origin = (pt.x, pt.y)
        r = self._rect_physical()
        self._win_origin = (r.left, r.top)
        user32.SetCapture(self.hwnd)

    def _drag_move(self) -> None:
        pt = POINT()
        user32.GetCursorPos(ctypes.byref(pt))
        dx = pt.x - self._drag_origin[0]
        dy = pt.y - self._drag_origin[1]
        # 阈值 8 物理像素（≈4 逻辑像素）：高 DPI 下鼠标抖动很容易超过 4，会把「点击」误判成拖动
        if not self._moved and abs(dx) + abs(dy) < 8:
            return
        self._moved = True
        work = _work_area_logical(self.scale)
        moved = move_rect(self._rect, physical_to_logical(dx, self.scale),
                          physical_to_logical(dy, self.scale), work)
        self._rect = moved
        user32.SetWindowPos(self.hwnd, None,
                            logical_to_physical(moved.x, self.scale),
                            logical_to_physical(moved.y, self.scale), 0, 0,
                            SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE)

    def _end_drag(self) -> None:
        if not self._dragging:
            return
        self._dragging = False
        user32.ReleaseCapture()
        if self._moved:
            self._save_rect()
        elif self.on_click:
            self.on_click()

    # ── 右键菜单 ──────────────────────────────────────────────────────
    def _popup_menu(self) -> None:
        if not self.menu:
            return
        hmenu = user32.CreatePopupMenu()
        for i, (label, _cb) in enumerate(self.menu, start=1):
            user32.AppendMenuW(hmenu, 0x0000, i, label)
        pt = POINT()
        user32.GetCursorPos(ctypes.byref(pt))
        cmd = user32.TrackPopupMenu(hmenu, 0x0100 | 0x0002, pt.x, pt.y, 0, self.hwnd, None)
        user32.DestroyMenu(hmenu)
        if cmd and 1 <= cmd <= len(self.menu):
            cb = self.menu[cmd - 1][1]
            if cb:
                cb()

    # ── 几何与持久化 ──────────────────────────────────────────────────
    def _rect_physical(self) -> RECT:
        r = RECT()
        user32.GetWindowRect(self.hwnd, ctypes.byref(r))
        return r

    def _load_rect(self, work: Rect) -> Rect:
        try:
            data = json.loads(self.state_path.read_text(encoding="utf-8"))
            # saved_at = 新格式标记；旧文件（含被探针误写成的 0,0）一律不认，回默认落点
            if not data.get("saved_at"):
                return default_ball_rect(self.size, work)
            rect = Rect(int(data["x"]), int(data["y"]), self.size, self.size)
            if work.x <= rect.x <= work.right - self.size and work.y <= rect.y <= work.bottom - self.size:
                return rect
        except Exception:              # noqa: BLE001  没有状态文件=首次启动
            pass
        return default_ball_rect(self.size, work)

    def _save_rect(self) -> None:
        try:
            self.state_path.parent.mkdir(parents=True, exist_ok=True)
            self.state_path.write_text(json.dumps(
                {"x": self._rect.x, "y": self._rect.y, "size": self.size,
                 "saved_at": int(time.time())},
                ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception as e:         # noqa: BLE001
            self.log("[球] 落点保存失败：%s" % e)

    @staticmethod
    def _lo(lparam: int) -> int:
        return ctypes.c_short(lparam & 0xFFFF).value

    @staticmethod
    def _hi(lparam: int) -> int:
        return ctypes.c_short((lparam >> 16) & 0xFFFF).value

    # 面板落点（供外壳调用，纯计算）
    def panel_rect(self, panel_w: int, panel_h: int, gap: int = 12):
        work = _work_area_logical(self.scale)
        r = clamp_rect(self._rect, work)
        return anchor_panel(r, panel_w, panel_h, work, gap)
