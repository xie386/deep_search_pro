# -*- coding: utf-8 -*-
"""生成悬浮球三态位图（idle / busy / alert）。

为什么是位图而不是 HTML：球窗是 Win32 分层窗口（`front/desktop/ball_window.py`），
逐像素 alpha 由位图提供；WebView2 的透明在这台机器上实测不可用（见 v4.1 Verdict 第 1 条）。

跑法：.venv/Scripts/python.exe scripts/make_ball_assets.py
产物：front/desktop/assets/ball_{idle,busy,alert}.png（128×128，逻辑 64 的 2 倍屏）
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

OUT = Path(__file__).resolve().parents[1] / "front" / "desktop" / "assets"
SIZE = 128          # 画布
D = 104             # 球直径
PAD = (SIZE - D) // 2


def _gradient(d: int) -> Image.Image:
    """径向渐变球体：左上高光 → 右下暖橙。"""
    img = Image.new("RGBA", (d, d), (0, 0, 0, 0))
    px = img.load()
    hi = (255, 212, 121)
    lo = (222, 133, 38)
    c = (d - 1) / 2
    for y in range(d):
        for x in range(d):
            dx, dy = (x - c) / c, (y - c) / c
            r = min((dx * dx + dy * dy) ** 0.5, 1.0)
            # 高光偏左上：把距离向光源方向加权
            hx, hy = (x - c * 0.72) / c, (y - c * 0.66) / c
            hr = min((hx * hx + hy * hy) ** 0.5, 1.0)
            t = min(1.0, 0.55 * r + 0.45 * hr)
            px[x, y] = (
                int(hi[0] + (lo[0] - hi[0]) * t),
                int(hi[1] + (lo[1] - hi[1]) * t),
                int(hi[2] + (lo[2] - hi[2]) * t),
                255,
            )
    return img


def _circle_mask(d: int) -> Image.Image:
    m = Image.new("L", (d, d), 0)
    ImageDraw.Draw(m).ellipse((0, 0, d - 1, d - 1), fill=255)
    return m


def _shadow() -> Image.Image:
    """柔和投影：模糊的黑色圆，向下偏移 3px。"""
    sh = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    ImageDraw.Draw(sh).ellipse((PAD + 2, PAD + 6, PAD + D - 3, PAD + D + 1), fill=(0, 0, 0, 110))
    return sh.filter(ImageFilter.GaussianBlur(6))


def build(state: str) -> Image.Image:
    base = _shadow()
    ball = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    ball.paste(_gradient(D), (PAD, PAD), _circle_mask(D))
    base.alpha_composite(ball)

    dr = ImageDraw.Draw(base)
    if state == "busy":
        # 外圈进度弧（静态一段，切换状态即换图）
        box = (PAD - 7, PAD - 7, PAD + D + 6, PAD + D + 6)
        dr.arc(box, start=-90, end=30, fill=(255, 255, 255, 235), width=4)
        dr.arc(box, start=40, end=300, fill=(255, 255, 255, 70), width=4)
    elif state == "alert":
        r = 13
        cx, cy = PAD + D - 4, PAD + 5
        dr.ellipse((cx - r, cy - r, cx + r, cy + r), fill=(255, 84, 84, 255),
                   outline=(255, 255, 255, 235), width=3)
    return base


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    for state in ("idle", "busy", "alert"):
        p = OUT / ("ball_%s.png" % state)
        build(state).save(p)
        print("写出 %s (%d 字节)" % (p, p.stat().st_size))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
