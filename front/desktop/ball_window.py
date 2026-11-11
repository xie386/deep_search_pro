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
import math
import os
import random
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
CMD_MODE = WM_APP + 5
WM_TIMER = 0x0113
STATES = ("idle", "busy", "alert")          # 旧三态（保留兼容）

# ── v4.1-04 桌宠 ──────────────────────────────────────────────────────
PET_DIR = ASSET_DIR / "pet"
MODE_IDLE = "idle"
MODE_SAY = "say"
MODE_THINK = "think"
MODE_WAITKB = "waitkb"
MODE_CLICK = "click"
MODE_DRAG = "drag"

TIMER_TICK = 1                  # 一个 33ms 的定时器同时驱动动画、待机切组、漫步
TICK_MS = 33
ANIM_MS = 200                   # 换帧间隔（≈5fps；真机反馈 11fps / 6.7fps 都偏急，先放到 5fps）
IDLE_SWITCH_MS = 2000           # 待机动作多久换一组（与 meta 一致）
CLICK_HOLD_MS = 600             # 点击/拖拽这种单帧动作保持多久
WANDER_SPEED = 90               # 漫步速度：逻辑像素/秒
WANDER_MIN_GAP = (4000, 9000)   # 两次起步的间隔（毫秒）
WANDER_DIST = (40, 260)         # 单次移动距离（逻辑像素）


def scan_pet_groups() -> dict:
    """扫描 assets/pet/ 下的帧：`<组>_<NN>.png`，按序号排序。"""
    out: dict[str, list[Path]] = {}
    if not PET_DIR.is_dir():
        return out
    for p in sorted(PET_DIR.glob("*.png")):
        if p.name.startswith("_"):          # _preview 之类
            continue
        stem = p.stem
        if "_" not in stem:
            continue
        group, _, num = stem.rpartition("_")
        if not num.isdigit():
            continue
        out.setdefault(group, []).append(p)
    for g in out:
        out[g].sort(key=lambda x: int(x.stem.rpartition("_")[2]))
    return out

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
        # ★ 类名必须每个实例唯一：窗口类的 lpfnWndProc 在 RegisterClass 时就被钉死了，
        #   同名类第二次注册会返回 1410（已存在），新窗口便**继续沿用第一个实例的
        #   WndProc** —— 于是新球的 WM_TIMER / 鼠标消息全部落到一个早已销毁的旧实例上，
        #   表现就是「球出现了但帧不动、点着没反应」（2026-11-11 反复重登复现）。
        self._cls = "ZxIntelBallWindow_%x" % (id(self) & 0xFFFFFF)
        self._dragging = False
        self._moved = False
        self._drag_origin = (0, 0)
        self._win_origin = (0, 0)
        self._rect = Rect(0, 0, self.size, self.size)
        self._visible = True
        # ── 桌宠状态（v4.1-04）──
        self.groups = scan_pet_groups()
        self.pet = bool(self.groups)                 # 有素材就走逐帧动画，否则退回旧三态
        self.mode = MODE_IDLE
        self._mode_before_click = MODE_IDLE
        self._frame_i = 0
        self._frame_at = 0.0
        self._idle_group = ""
        self._idle_switch_at = 0.0
        self._click_until = 0.0
        self.wander = True
        self.panel_open = False
        self._target = None                          # 漫步目标（逻辑坐标）
        self._next_wander_at = 0.0
        self._paint_cache: dict = {}
        self._pending_mode = None
        self._last_tick = 0.0                     # 心跳时间戳（看门狗据此判断是否卡死）

    # ── 对外接口 ──────────────────────────────────────────────────────
    def start(self, timeout: float = 3.0) -> bool:
        if self._thread and self._thread.is_alive():
            return True
        self._thread = threading.Thread(target=self._run, name="ball-window", daemon=True)
        self._thread.start()
        return self._ready.wait(timeout)

    def stop(self) -> None:
        hwnd = self.hwnd
        if hwnd:
            user32.PostMessageW(hwnd, WM_CLOSE, 0, 0)
        if self._thread:
            self._thread.join(timeout=1.5)
            if self._thread.is_alive() and hwnd:
                # 线程卡住时至少把窗口从屏幕上撤掉，别让它挡着下一个账号的球
                try:
                    self.log("[球] 停止超时，强制隐藏残留窗口")
                    user32.ShowWindow(hwnd, 0)          # SW_HIDE
                except Exception:                       # noqa: BLE001
                    pass
        self.hwnd = None

    def set_state(self, state: str) -> None:
        """旧接口：idle / busy / alert。busy = 正在输出（say），alert = 提醒（点一次）。"""
        mapping = {"idle": MODE_IDLE, "busy": MODE_SAY, "alert": MODE_CLICK}
        self.set_mode(mapping.get(state, MODE_IDLE))

    def set_mode(self, mode: str) -> None:
        """设置桌宠动作：idle / say / think / waitkb / click / drag。"""
        # 模式名没法定死在一个 int 里：先写待处理字段再投递，窗口线程读到后清空
        self._pending_mode = mode
        if self.hwnd:
            user32.PostMessageW(self.hwnd, CMD_MODE, 0, 0)

    def healthy(self, idle_limit_ms: float = 2500.0) -> bool:
        """线程还活着、心跳没停 —— 看门狗用它判断球是不是卡死了。"""
        if not self.hwnd or not self._thread or not self._thread.is_alive():
            return False
        if not user32.IsWindow(self.hwnd):
            return False
        return (time.monotonic() * 1000.0 - (self._last_tick or 0.0)) < idle_limit_ms

    def set_wander(self, on: bool) -> None:
        """开/关自主漫步（设置项 BALL_WANDER）。"""
        self.wander = bool(on)
        if not self.wander:
            self._target = None

    def set_panel_open(self, on: bool) -> None:
        """面板打开时暂停漫步（否则球在面板旁边乱跑）。"""
        self.panel_open = bool(on)
        if self.panel_open:
            self._target = None

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
        if self.pet:
            self._pick_idle_group(force=True)
            self._paint_current()
            tid = user32.SetTimer(self.hwnd, TIMER_TICK, TICK_MS, None)
            if not tid:
                self.log("[球] SetTimer 失败 err=%s" % ctypes.get_last_error())
            elif os.environ.get("ZX_BALL_DEBUG") == "1":
                self.log("[球] 定时器已启动 id=%s hwnd=%s" % (tid, self.hwnd))
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
            if msg == CMD_MODE:
                pending, self._pending_mode = self._pending_mode, None
                if pending:
                    self._apply_mode(str(pending))
                return 0
            if msg == WM_TIMER and int(wparam) == TIMER_TICK:
                self._tick()
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
                user32.KillTimer(hwnd, TIMER_TICK)
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
        if self.pet:
            self._paint_current()
            return
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

    # ── 桌宠：分组、切帧、漫步（v4.1-04）──────────────────────────────
    def _idle_groups(self) -> list[str]:
        return sorted([g for g in self.groups if g.startswith("idle")])

    def _current_frames(self) -> list[Path]:
        if self.mode == MODE_IDLE:
            return self.groups.get(self._idle_group) or self.groups.get("idle1") or []
        if self.mode == MODE_CLICK:
            return self.groups.get("click") or self.groups.get("drag") or []
        if self.mode == MODE_DRAG:
            return self.groups.get("drag") or self.groups.get("click") or []
        return self.groups.get(self.mode) or self.groups.get("say") or []

    def _pick_idle_group(self, force: bool = False) -> None:
        import random
        groups = self._idle_groups()
        if not groups:
            return
        if not force and self._idle_group in groups and len(groups) > 1:
            others = [g for g in groups if g != self._idle_group]
            self._idle_group = random.choice(others)
        else:
            self._idle_group = random.choice(groups)
        self._frame_i = 0

    def _apply_mode(self, mode: str, paint: bool = True) -> None:
        """切模式：click / drag 是「临时态」，结束后回到进入前的模式。

        `paint=False` 时只改状态、不立刻贴图 —— 拖动开始那一刻不要重入 UpdateLayeredWindow
        （真机：在鼠标按下回调里重绘 + 随后开面板，把进程带崩过）。
        """
        now = time.monotonic() * 1000.0
        if mode == MODE_CLICK:
            # ★ 点完要回待机：按下时已经切到 drag，若按「原模式」恢复会永久卡在 drag 帧
            #   （真机表现：点一下球，变成举钱那张图再也不动）
            self._mode_before_click = (MODE_IDLE if self.mode in (MODE_CLICK, MODE_DRAG)
                                       else self.mode)
            self.mode = MODE_CLICK
            self._frame_i = 0
            self._click_until = now + CLICK_HOLD_MS
            self._target = None
        elif mode == MODE_DRAG:
            self.mode = MODE_DRAG
            self._frame_i = 0
            self._target = None
        else:
            self.mode = mode
            self._frame_i = 0
            if mode == MODE_IDLE:
                self._pick_idle_group(force=True)
                self._idle_switch_at = now + IDLE_SWITCH_MS
                self._next_wander_at = now + 2500
        if paint:
            self._paint_current()
        if os.environ.get("ZX_BALL_DEBUG") == "1":
            self.log("[球] 模式 → %s（%s 帧）" % (mode, len(self._current_frames())))

    def _paint_current(self) -> None:
        """按当前模式/帧号贴图（带缓存：同一帧同一尺寸只算一次预乘 alpha）。"""
        if not self.hwnd or not user32.IsWindow(self.hwnd):
            return
        frames = self._current_frames()
        if not frames:
            return
        idx = max(0, min(self._frame_i, len(frames) - 1))
        path = frames[idx]
        px = logical_to_physical(self.size, self.scale)
        key = (str(path), px, self.opacity)
        hit = self._paint_cache.get(key)
        if hit is None:
            try:
                hit = _bgra_premultiplied(path, px, self.opacity)
            except Exception as e:                     # noqa: BLE001
                self.log("[球] 贴图失败（%s）：%s" % (path.name, e))
                return
            self._paint_cache[key] = hit
        self._blit(*hit)

    def _tick(self) -> None:
        """33ms 心跳：切帧、待机换组、随机漫步。"""
        now = time.monotonic() * 1000.0
        self._last_tick = now
        if os.environ.get("ZX_BALL_DEBUG") == "1" and now - getattr(self, "_hb", 0) > 3000:
            self._hb = now
            self.log("[球] 心跳 mode=%s frame=%d obj=%d tick=%.0f"
                     % (self.mode, self._frame_i, id(self) & 0xFFFF, self._last_tick))

        if self.mode == MODE_CLICK and now >= self._click_until:
            self._apply_mode(self._mode_before_click or MODE_IDLE)
            return
        if self.mode == MODE_DRAG:
            self._blit_current_if_needed(now)
            return

        # 待机：每 2 秒换一组动作
        if self.mode == MODE_IDLE and now >= self._idle_switch_at:
            self._pick_idle_group()
            self._idle_switch_at = now + IDLE_SWITCH_MS
            self._frame_at = 0.0

        self._blit_current_if_needed(now)
        self._maybe_wander(now)

    def _blit_current_if_needed(self, now: float) -> None:
        if now - self._frame_at < ANIM_MS:
            return
        self._frame_at = now
        frames = self._current_frames()
        if not frames:
            return
        if self.mode != MODE_CLICK and self.mode != MODE_DRAG:
            self._frame_i = (self._frame_i + 1) % len(frames)
        elif self.mode == MODE_DRAG and len(frames) > 1:
            self._frame_i = (self._frame_i + 1) % len(frames)
        self._paint_current()

    def _maybe_wander(self, now: float) -> None:
        """随机漫步：只在「待机 + 未拖拽 + 面板关着 + 鼠标不在球上」时动。"""
        import random
        if (not self.pet or not self.wander or self.mode != MODE_IDLE
                or self._dragging or self.panel_open or not self._visible):
            return

        # 鼠标悬停即停：给用户一个「我在这儿」的稳定目标
        pt = POINT()
        user32.GetCursorPos(ctypes.byref(pt))
        r = self._rect_physical()
        if r.left <= pt.x <= r.right and r.top <= pt.y <= r.bottom:
            self._target = None
            return

        work = _work_area_logical(self.scale)
        if self._target is None:
            if now < self._next_wander_at:
                return
            ang = random.uniform(0, 6.283)
            dist = random.uniform(*WANDER_DIST)
            tx = int(self._rect.x + math.cos(ang) * dist)
            ty = int(self._rect.y + math.sin(ang) * dist)
            tx = max(work.x + 8, min(tx, work.right - self.size - 8))
            ty = max(work.y + 8, min(ty, work.bottom - self.size - 8))
            self._target = (tx, ty)
            return

        tx, ty = self._target
        dx, dy = tx - self._rect.x, ty - self._rect.y
        dist = math.hypot(dx, dy)
        if dist < 3:
            self._target = None
            self._next_wander_at = now + random.uniform(*WANDER_MIN_GAP)
            return
        step = WANDER_SPEED * (TICK_MS / 1000.0)
        k = min(1.0, step / dist)
        nx = int(round(self._rect.x + dx * k))
        ny = int(round(self._rect.y + dy * k))
        self._rect = Rect(nx, ny, self.size, self.size)
        user32.SetWindowPos(self.hwnd, None,
                            logical_to_physical(nx, self.scale),
                            logical_to_physical(ny, self.scale), 0, 0,
                            SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE)

    def _blit(self, data: bytes, wpx: int, hpx: int) -> None:
        if not self.hwnd or not user32.IsWindow(self.hwnd):
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
        g2 = gdi32
        g2.SelectObject(hdc_mem, old)
        g2.DeleteObject(hbm)
        g2.DeleteDC(hdc_mem)
        user32.ReleaseDC(None, hdc_screen)

    # ── 拖动 ──────────────────────────────────────────────────────────
    def _begin_drag(self) -> None:
        self._dragging = True
        self._moved = False
        if self.pet:
            self._apply_mode(MODE_DRAG, paint=False)   # 交给 33ms 心跳去画
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
            if self.pet:
                self._apply_mode(MODE_IDLE)      # 放下后回到待机
            self._save_rect()
        else:
            if self.pet:
                self.set_mode(MODE_CLICK)        # 点一下：给一个动作反馈
            if self.on_click:
                self._safe_click()

    def _safe_click(self) -> None:
        try:
            if self.on_click:
                self.on_click()
        except Exception as e:                             # noqa: BLE001
            self.log("[球] 点击回调异常：%s" % e)

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

# ── Win32 签名（v4.1-04）──────────────────────────────────────────────
# ★ 逐帧贴图后这里被高频调用：不声明 restype/argtypes 时 64 位句柄可能被截成 32 位，
#   拿到坏句柄再交给 GDI，表现就是「点一下球进程直接没了」（真机踩过）。
def _declare_win32() -> None:
    user32.GetDC.argtypes = [w.HWND]
    user32.GetDC.restype = w.HDC
    user32.ReleaseDC.argtypes = [w.HWND, w.HDC]
    user32.UpdateLayeredWindow.argtypes = [
        w.HWND, w.HDC, ctypes.POINTER(POINT), ctypes.POINTER(SIZE),
        w.HDC, ctypes.POINTER(POINT), w.COLORREF, ctypes.POINTER(BLENDFUNCTION), w.DWORD]
    user32.UpdateLayeredWindow.restype = w.BOOL
    user32.SetWindowPos.argtypes = [w.HWND, w.HWND, ctypes.c_int, ctypes.c_int,
                                    ctypes.c_int, ctypes.c_int, w.UINT]
    user32.SetWindowPos.restype = w.BOOL
    user32.IsWindow.argtypes = [w.HWND]
    user32.IsWindow.restype = w.BOOL
    gdi32.CreateDIBSection.argtypes = [w.HDC, ctypes.POINTER(BITMAPINFO), w.UINT,
                                       ctypes.POINTER(ctypes.c_void_p), w.HANDLE, w.DWORD]
    gdi32.CreateDIBSection.restype = w.HBITMAP
    gdi32.CreateCompatibleDC.argtypes = [w.HDC]
    gdi32.CreateCompatibleDC.restype = w.HDC
    gdi32.SelectObject.argtypes = [w.HDC, w.HGDIOBJ]
    gdi32.SelectObject.restype = w.HGDIOBJ
    gdi32.DeleteObject.argtypes = [w.HGDIOBJ]
    gdi32.DeleteObject.restype = w.BOOL
    gdi32.DeleteDC.argtypes = [w.HDC]
    gdi32.DeleteDC.restype = w.BOOL


_declare_win32()
