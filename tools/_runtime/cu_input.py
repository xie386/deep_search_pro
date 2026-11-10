# -*- coding: utf-8 -*-
"""屏幕控制执行器（v4.1-03）：鼠标点击、文字输入、白名单判定、审计。

口径见 `docs/v4.1/Verdict/03-悬浮球-屏幕控制.md`：

1. **只做两件事**：鼠标左键点击（含双击）与文字输入；其余动作留后续 4.x。
2. **零新依赖**：`SendInput` / `SetCursorPos` 走 ctypes；中文走「写剪贴板 → Ctrl+V」，
   不经输入法（键盘扫描码没有汉字，逐键注入会和 IME 打架）。
3. **白名单双重判定**：前台窗口的进程名、以及落点处真正接收点击的窗口所属顶层窗口的进程名，
   都必须在设置项 `CU_WHITELIST` 里；**名单为空 = 全部拒绝**。
4. **审计只追加**：`data/audit/cu_actions.jsonl` 记元数据（时间/会话/帧号/动作/坐标/文本长度/
   注入后前台进程），不含屏幕内容。

这个模块是**执行器**：`tools/control_tool.py` 里的两个 `@tool` 只是薄封装，
面板或其它入口若要做同样的动作，也调这里，避免两套实现。
"""
from __future__ import annotations

import ctypes
import json
import os
import time
from ctypes import wintypes as w
from pathlib import Path

from utils import settings as _settings

ROOT = Path(__file__).resolve().parents[2]
AUDIT = ROOT / "data" / "audit" / "cu_actions.jsonl"

_c = ctypes.WinDLL("user32", use_last_error=True)
_k = ctypes.WinDLL("kernel32", use_last_error=True)

# ── 结构体 ────────────────────────────────────────────────────────────
PUL = ctypes.POINTER(ctypes.c_ulong)


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", w.WORD), ("wScan", w.WORD), ("dwFlags", w.DWORD),
                ("time", w.DWORD), ("dwExtraInfo", PUL)]


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", ctypes.c_long), ("dy", ctypes.c_long), ("mouseData", w.DWORD),
                ("dwFlags", w.DWORD), ("time", w.DWORD), ("dwExtraInfo", PUL)]


class _INPUTUNION(ctypes.Union):
    _fields_ = [("ki", KEYBDINPUT), ("mi", MOUSEINPUT)]


class INPUT(ctypes.Structure):
    _anonymous_ = ("u",)
    _fields_ = [("type", w.DWORD), ("u", _INPUTUNION)]


INPUT_MOUSE, INPUT_KEYBOARD = 0, 1
MOUSEEVENTF_MOVE, MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP, MOUSEEVENTF_ABSOLUTE = 0x1, 0x2, 0x4, 0x8000
MOUSEEVENTF_WHEEL = 0x0800
WHEEL_DELTA = 120
KEYEVENTF_KEYUP = 0x2

_c.SendInput.argtypes = [w.UINT, ctypes.POINTER(INPUT), ctypes.c_int]
_c.SendInput.restype = w.UINT
_c.SetCursorPos.argtypes = [ctypes.c_int, ctypes.c_int]
_c.GetForegroundWindow.restype = w.HWND
_c.WindowFromPoint.argtypes = [w.POINT]
_c.WindowFromPoint.restype = w.HWND
_c.GetAncestor.argtypes = [w.HWND, w.UINT]
_c.GetAncestor.restype = w.HWND
_c.GetWindowThreadProcessId.argtypes = [w.HWND, ctypes.POINTER(w.DWORD)]
_c.GetWindowTextW.argtypes = [w.HWND, w.LPWSTR, ctypes.c_int]
_k.OpenProcess.argtypes = [w.DWORD, w.BOOL, w.DWORD]
_k.OpenProcess.restype = w.HANDLE
_k.QueryFullProcessImageNameW.argtypes = [w.HANDLE, w.DWORD, w.LPWSTR, ctypes.POINTER(w.DWORD)]
_k.CloseHandle.argtypes = [w.HANDLE]
# ★ 64 位必须声明：不声明时 HGLOBAL 会被截成 32 位 int，GlobalLock 拿到的句柄是坏的
#   （真机踩过：GlobalLock 失败，中文一个字都打不进去）
_k.GlobalAlloc.argtypes = [w.UINT, ctypes.c_size_t]
_k.GlobalAlloc.restype = w.HGLOBAL
_k.GlobalLock.argtypes = [w.HGLOBAL]
_k.GlobalLock.restype = ctypes.c_void_p
_k.GlobalUnlock.argtypes = [w.HGLOBAL]
_k.GlobalFree.argtypes = [w.HGLOBAL]


def dpi_aware() -> None:
    """坐标必须与截图同一坐标系：进程必须最早声明 per-monitor aware（02 已做过，幂等再调一次）。"""
    try:
        ctypes.WinDLL("shcore").SetProcessDpiAwareness(2)
    except Exception:                                    # noqa: BLE001
        try:
            _c.SetProcessDPIAware()
        except Exception:                                # noqa: BLE001
            pass


# ★ 必须在**导入时**就声明 DPI 感知，不能等到 click() 里再调：
#   非感知进程的 WindowFromPoint / SetCursorPos 会把物理坐标当逻辑坐标处理（200% 缩放下
#   直接落到屏幕外，WindowFromPoint 返回 0 → 「未知进程，不在白名单」）。
#   真机踩过：模型点搜索框被白名单拒绝，实际是坐标被虚拟化。
dpi_aware()


# ── 进程/窗口 ─────────────────────────────────────────────────────────
def process_name_of(hwnd: int) -> str:
    """HWND → 进程文件名（小写、不含路径）。取不到返回空串。"""
    if not hwnd:
        return ""
    pid = w.DWORD(0)
    _c.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    if not pid.value:
        return ""
    h = _k.OpenProcess(0x1000, False, pid.value)          # PROCESS_QUERY_LIMITED_INFORMATION
    if not h:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(1024)
        size = w.DWORD(len(buf))
        if not _k.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
            return ""
        return os.path.basename(buf.value).lower()
    finally:
        _k.CloseHandle(h)


def whitelist() -> set[str]:
    """设置页里勾选的进程名（小写、去空白；允许带或不带 .exe）。"""
    raw = ""
    try:
        raw = str(_settings.get("CU_WHITELIST", "") or "")
    except Exception:                                    # noqa: BLE001
        raw = ""
    out = set()
    for line in raw.replace(",", "\n").splitlines():
        name = line.strip().lower()
        if not name:
            continue
        out.add(name)
        if not name.endswith(".exe"):
            out.add(name + ".exe")
    return out


def foreground_process() -> str:
    """当前前台窗口的进程文件名（小写）。"""
    return process_name_of(_c.GetForegroundWindow())


def check_foreground() -> tuple[bool, str]:
    """只判前台窗口（打字没有「落点」，唯一的目标就是前台）。"""
    allow = whitelist()
    if not allow:
        return False, "屏幕控制白名单为空：请到设置页添加允许操作的进程名（如 notepad.exe）"
    fg_proc = foreground_process()
    if fg_proc not in allow:
        return False, "前台窗口（%s）不在屏幕控制白名单" % (fg_proc or "未知进程")
    return True, fg_proc


def check_target(x: int, y: int) -> tuple[bool, str]:
    """白名单双重判定：前台窗口 + 落点窗口的顶层属主。返回 (是否放行, 人话原因)。"""
    allow = whitelist()
    if not allow:
        return False, "屏幕控制白名单为空：请到设置页添加允许操作的进程名（如 notepad.exe）"

    fg = _c.GetForegroundWindow()
    fg_proc = process_name_of(fg)
    if fg_proc not in allow:
        return False, "前台窗口（%s）不在屏幕控制白名单" % (fg_proc or "未知进程")

    hit = _c.WindowFromPoint(w.POINT(int(x), int(y)))
    root = _c.GetAncestor(hit, 2) if hit else 0        # GA_ROOT
    hit_proc = process_name_of(root or hit)
    if hit_proc not in allow:
        return False, "落点处的窗口（%s）不在屏幕控制白名单" % (hit_proc or "未知进程")
    return True, fg_proc


# ── 注入 ──────────────────────────────────────────────────────────────
def _send(events: list[INPUT]) -> None:
    arr = (INPUT * len(events))(*events)
    if _c.SendInput(len(events), arr, ctypes.sizeof(INPUT)) != len(events):
        raise RuntimeError("SendInput 失败（错误码 %d）" % ctypes.get_last_error())


def _mouse(flags: int, dx: int = 0, dy: int = 0) -> INPUT:
    ev = INPUT(); ev.type = INPUT_MOUSE
    ev.mi = MOUSEINPUT(dx, dy, 0, flags, 0, None)
    return ev


# 常用按键：只开放「不会产生破坏性后果」的那几个（回车/退格/方向/制表/Tab）。
VK: dict[str, int] = {
    "enter": 0x0D, "esc": 0x1B, "tab": 0x09, "space": 0x20, "backspace": 0x08,
    "delete": 0x2E, "up": 0x26, "down": 0x28, "left": 0x25, "right": 0x27,
    "home": 0x24, "end": 0x23, "pageup": 0x21, "pagedown": 0x22,
}


def window_text_at(x: int, y: int) -> str:
    """落点上那个窗口的标题（顶层）。用来在回执里说清「这一下点到哪了」。"""
    hit = _c.WindowFromPoint(w.POINT(int(x), int(y)))
    if not hit:
        return ""
    root = _c.GetAncestor(hit, 2) or hit
    buf = ctypes.create_unicode_buffer(512)
    _c.GetWindowTextW(root, buf, 512)
    return buf.value


def click(x: int, y: int, double: bool = False) -> None:
    """在物理像素 (x, y) 单击/双击。"""
    dpi_aware()
    _c.SetCursorPos(int(x), int(y))
    time.sleep(0.03)
    times = 2 if double else 1
    for _ in range(times):
        _send([_mouse(MOUSEEVENTF_LEFTDOWN), _mouse(MOUSEEVENTF_LEFTUP)])
        time.sleep(0.06)
    time.sleep(0.12)


# ── 剪贴板（ctypes 直调，零依赖） ─────────────────────────────────────
CF_UNICODETEXT = 13
GMEM_MOVEABLE = 0x0002
_c.OpenClipboard.argtypes = [w.HWND]
_c.SetClipboardData.argtypes = [w.UINT, w.HANDLE]
_c.SetClipboardData.restype = w.HANDLE
_c.GetClipboardData.argtypes = [w.UINT]
_c.GetClipboardData.restype = w.HANDLE
_c.IsClipboardFormatAvailable.argtypes = [w.UINT]


def _get_clip() -> str:
    if not _c.IsClipboardFormatAvailable(CF_UNICODETEXT):
        return ""
    if not _c.OpenClipboard(None):
        return ""
    try:
        h = _c.GetClipboardData(CF_UNICODETEXT)
        if not h:
            return ""
        ptr = _k.GlobalLock(h)
        if not ptr:
            return ""
        try:
            return ctypes.wstring_at(ptr)
        finally:
            _k.GlobalUnlock(h)
    finally:
        _c.CloseClipboard()


def _set_clip(text: str) -> None:
    buf = ctypes.create_unicode_buffer(text)
    size = ctypes.sizeof(buf)
    h = _k.GlobalAlloc(GMEM_MOVEABLE, size)
    if not h:
        raise RuntimeError("GlobalAlloc 失败")
    ptr = _k.GlobalLock(h)
    if not ptr:
        _k.GlobalFree(h)
        raise RuntimeError("GlobalLock 失败")
    try:
        ctypes.memmove(ptr, buf, size)
    finally:
        _k.GlobalUnlock(h)
    if not _c.OpenClipboard(None):
        raise RuntimeError("剪贴板被其它程序占用")
    try:
        _c.EmptyClipboard()
        if not _c.SetClipboardData(CF_UNICODETEXT, h):
            raise RuntimeError("写剪贴板失败")
    finally:
        _c.CloseClipboard()


def _key(vk: int, up: bool = False) -> INPUT:
    ev = INPUT(); ev.type = INPUT_KEYBOARD
    ev.ki = KEYBDINPUT(vk, 0, KEYEVENTF_KEYUP if up else 0, 0, None)
    return ev


def hotkey(*vks: int) -> None:
    """按一次组合键（如 hotkey(0x11, 0x41) = Ctrl+A）。仅执行器/自测用，未暴露成工具。"""
    dpi_aware()
    seq = [_key(v) for v in vks] + [_key(v, True) for v in reversed(vks)]
    _send(seq)
    time.sleep(0.12)


def scroll(x: int, y: int, notches: int) -> None:
    """在物理坐标 (x, y) 处滚动滚轮。notches > 0 向上（页面向回翻），< 0 向下（页面往下翻）。

    每格 120 单位；一次调用最多 20 格，超出截断（防止一口气甩飞列表）。
    """
    dpi_aware()
    n = int(notches)
    if not n:
        return
    _c.SetCursorPos(int(x), int(y))
    time.sleep(0.05)
    step = WHEEL_DELTA if n > 0 else -WHEEL_DELTA
    for _ in range(min(abs(n), 20)):
        ev = INPUT()
        ev.type = INPUT_MOUSE
        ev.mi = MOUSEINPUT(0, 0, step & 0xFFFFFFFF, MOUSEEVENTF_WHEEL, 0, None)
        _send([ev])
        time.sleep(0.05)
    time.sleep(0.18)


def press(key: str, times: int = 1) -> None:
    """按一次（或几次）单个键。key 取 VK 表的键名，大小写不敏感。"""
    name = str(key or "").strip().lower()
    vk = VK.get(name)
    if not vk:
        raise ValueError("不支持的按键：%s（可用：%s）" % (key, "/".join(VK)))
    dpi_aware()
    for _ in range(max(1, min(10, int(times or 1)))):
        _send([_key(vk), _key(vk, True)])
        time.sleep(0.12)


def type_text(text: str) -> None:
    """把文字粘贴到当前焦点。中文/任意 Unicode 都走剪贴板 + Ctrl+V；结束后恢复原剪贴板。"""
    dpi_aware()
    old = ""
    try:
        old = _get_clip()
    except Exception:                                    # noqa: BLE001
        old = ""
    _set_clip(text)
    try:
        time.sleep(0.05)
        _send([_key(0x11), _key(0x56), _key(0x56, True), _key(0x11, True)])   # Ctrl+V
        time.sleep(0.25)
    finally:
        try:
            if old:
                _set_clip(old)
        except Exception:                                # noqa: BLE001
            pass


# ── 审计 ──────────────────────────────────────────────────────────────
def audit(thread_id: str, capture_id: int, action: str, **extra) -> None:
    """只追加一行 JSONL；失败不影响动作本身。"""
    try:
        AUDIT.parent.mkdir(parents=True, exist_ok=True)
        row = {"ts": int(time.time()), "thread_id": thread_id, "capture_id": int(capture_id),
               "action": action, "fg_process": process_name_of(_c.GetForegroundWindow())}
        row.update(extra)
        with AUDIT.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    except Exception:                                    # noqa: BLE001
        pass
