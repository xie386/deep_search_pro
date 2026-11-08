# -*- coding: utf-8 -*-
"""悬浮球真机自检：分层窗口 + 圆形命中 + 点击回调 + 截图取证。

只开球窗（不启后端、不启面板），跑完自动关闭；截图落 tests/test_out/（已 gitignore）。
跑法：.venv/Scripts/python.exe tests/live/desktop_ball_e2e.py
"""
from __future__ import annotations

import ctypes
import sys
import time
from ctypes import wintypes as w
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "front" / "desktop"))

from ball_window import (HTCLIENT, HTTRANSPARENT, WM_LBUTTONDOWN, WM_LBUTTONUP,  # noqa: E402
                         WM_NCHITTEST, BallWindow)
from ball_geometry import logical_to_physical  # noqa: E402

user32 = ctypes.WinDLL("user32", use_last_error=True)
user32.SendMessageW.restype = ctypes.c_ssize_t
user32.SendMessageW.argtypes = [w.HWND, w.UINT, w.WPARAM, w.LPARAM]
user32.GetWindowLongW.restype = ctypes.c_long

OUT = ROOT / "tests" / "test_out"
OUT.mkdir(parents=True, exist_ok=True)

results = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, bool(ok), detail))
    print("%s %s %s" % ("PASS" if ok else "FAIL", name, detail))


def main() -> int:
    hits = []

    def on_click():
        hits.append(time.time())

    ball = BallWindow(on_click=on_click, menu=[("打开主窗口", lambda: None)],
                      size=64, state_path=OUT / "_ball_state.json",
                      log=lambda m: print(m))
    if not ball.start():
        check("窗口启动", False, "start() 未就绪")
        return 1
    time.sleep(0.6)
    hwnd = ball.hwnd
    check("窗口启动", bool(hwnd), "hwnd=%s" % hwnd)

    ex = user32.GetWindowLongW(hwnd, -20)          # GWL_EXSTYLE
    layered, topmost, tool = 0x00080000, 0x00000008, 0x00000080
    check("分层+置顶+工具栏样式", bool(ex & layered) and bool(ex & topmost) and bool(ex & tool),
          "exstyle=0x%08X" % ex)

    r = ball._rect_physical()
    px = logical_to_physical(64, ball.scale)
    check("尺寸=64 逻辑", (r.right - r.left, r.bottom - r.top) == (px, px),
          "实际 %dx%d，期望 %dx%d，倍率 %.2f" % (r.right - r.left, r.bottom - r.top, px, px, ball.scale))

    # 命中：左上角在圆外 → 穿透；正中 → 客户区
    hit_corner = user32.SendMessageW(hwnd, WM_NCHITTEST, 0,
                                     ((r.top + 2) << 16) | ((r.left + 2) & 0xFFFF))
    hit_center = user32.SendMessageW(hwnd, WM_NCHITTEST, 0,
                                     (((r.top + px // 2) << 16) | ((r.left + px // 2) & 0xFFFF)))
    check("圆外穿透", hit_corner == HTTRANSPARENT, "返回 %s" % hit_corner)
    check("圆内可点", hit_center == HTCLIENT, "返回 %s" % hit_center)

    # 截图取证：圆外像素应等于桌面，而不是窗口底色
    from PIL import ImageGrab
    img = ImageGrab.grab(all_screens=True)
    corner = img.getpixel((r.left + 2, r.top + 2))
    center = img.getpixel((r.left + px // 2, r.top + px // 2))
    shot = OUT / "_ball_live.png"
    img.crop((r.left, r.top, r.right, r.bottom)).save(shot)
    check("圆外透明（与球色不同）", corner != center, "角=%s 心=%s 截图=%s" % (corner, center, shot.name))

    # 点击：把光标移到球心再投递按下/抬起
    user32.SetCursorPos(r.left + px // 2, r.top + px // 2)
    time.sleep(0.1)
    user32.PostMessageW(hwnd, WM_LBUTTONDOWN, 1, ((r.top + px // 2) << 16) | ((r.left + px // 2) & 0xFFFF))
    time.sleep(0.05)
    user32.PostMessageW(hwnd, WM_LBUTTONUP, 0, ((r.top + px // 2) << 16) | ((r.left + px // 2) & 0xFFFF))
    time.sleep(0.5)
    check("单击回调", len(hits) == 1, "触发 %d 次" % len(hits))

    ball.stop()
    time.sleep(0.3)
    check("关闭无残留窗口", not user32.IsWindow(hwnd), "IsWindow=%s" % user32.IsWindow(hwnd))

    bad = [n for n, ok, _ in results if not ok]
    print("\n合计 %d 项：通过 %d，失败 %s" % (len(results), len(results) - len(bad), bad or "无"))
    return 0 if not bad else 2


if __name__ == "__main__":
    raise SystemExit(main())
