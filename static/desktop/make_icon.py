# -*- coding: utf-8 -*-
"""设计稿 → 桌面版图标（去白底 + 生成多尺寸 .ico）。

用法：
    .venv/Scripts/python.exe static/desktop/make_icon.py <设计稿.png> [更多.png ...]

做三件事（都是"设计稿给不了、但壳必须要有"的）：
  ① **去掉外围白底**：AI 出的图多半是"圆角方块画在白底上"，直接当图标贴到深色任务栏/托盘
     会露一个白方块。这里把**与外边界相连**的近白区域打成透明（猫脸那圈白是包在绿里的，
     不会被误伤）。
  ② 存 `assets/icon.png`（512×512 RGBA）—— 托盘直接用它（`_make_icon_image()` 会读这个文件）。
  ③ 生成 `assets/icon.ico`（16/24/32/48/64/128/256）—— 任务栏/窗口图标要的是真 .ico，
     `System.Drawing.Icon` 不认 PNG。

同时在 `assets/icon-meta.json` 里记下**设计稿里橙点的位置与半径**：那是"忙碌指示灯"要叠上去
的地方（`_make_icon_image(busy=True)` 会读它，避免和设计稿自带的圆点错位）。
"""
import json
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

HERE = Path(__file__).resolve().parent
ASSETS = HERE / "assets"
ICO_SIZES = [16, 24, 32, 48, 64, 128, 256]
SMALL_THRESHOLD = 32      # ≤ 这个尺寸可用"光学补偿"版本（当前**关闭**，见下）
SMALL_CROP = 0.0          # ★ 实测结论（2026-09-22）：裁 8% 会把圆角切出缺口
#                          （32/48px 下图标变成"削角方块"，比可读性那点收益亏得多）→ 默认 0
#                          想试的话 0.03~0.05 还算安全，超过 ~5% 圆角必然被切。
MASTER_PNG = 512          # 存盘尺寸（托盘/窗口都够，也便于以后换尺寸）


def strip_outer_white(im: Image.Image, thresh: int = 40) -> Image.Image:
    """把与外边界相连的近白区域变透明（保留被图形包围的白色，比如猫脸）。"""
    im = im.convert("RGBA")
    W, H = im.size
    sentinel = (255, 0, 255, 255)                      # 洋红哨兵（设计稿里不会有的颜色）
    filled = im.copy()
    for seed in ((0, 0), (W - 1, 0), (0, H - 1), (W - 1, H - 1)):
        try:
            ImageDraw.floodfill(filled, seed, sentinel, thresh=thresh)
        except Exception:                              # noqa: BLE001
            pass
    inside = Image.new("L", (W, H), 255)
    ip, fp = inside.load(), filled.load()
    hit = 0
    for y in range(H):
        for x in range(W):
            if fp[x, y] == sentinel:
                ip[x, y] = 0
                hit += 1
    # 边缘抗锯齿：二值掩膜轻微羽化，避免硬切出的锯齿
    inside = inside.filter(ImageFilter.GaussianBlur(1.0))
    im.putalpha(inside)
    print("   去白底：透明像素 %d / %d（%.1f%%）" % (hit, W * H, 100.0 * hit / (W * H)))
    return im


def find_accent_dot(im: Image.Image):
    """找设计稿里的橙色圆点（右下角那个）。返回 (cx, cy, r) 的**归一化**值或 None。"""
    im = im.convert("RGBA")
    W, H = im.size
    px = im.load()
    xs, ys = [], []
    step = max(1, W // 512)
    for y in range(0, H, step):
        for x in range(0, W, step):
            r, g, b, a = px[x, y]
            if a > 200 and r > 200 and 120 < g < 210 and b < 130:      # 橙/琥珀
                xs.append(x)
                ys.append(y)
    if not xs:
        return None
    cx = (min(xs) + max(xs)) / 2.0 / W
    cy = (min(ys) + max(ys)) / 2.0 / H
    r = (max(xs) - min(xs)) / 2.0 / W
    return {"cx": round(cx, 4), "cy": round(cy, 4), "r": round(r, 4),
            "found_px": len(xs)}


def optical_crop(im: Image.Image, pct: float = SMALL_CROP) -> Image.Image:
    """裁掉四边 pct 的边距（= 图形在画面里放大），专给小尺寸用。

    ★ 为什么要这个：16/24px 下细节必然丢，"图形占比大一点"能让轮廓更清楚（专业图标集
      都做光学尺寸补偿）。实测：8% 对 24/32px 有可感提升，16px 只能算不糊。
    """
    W, H = im.size
    c = int(W * pct / 2)
    return im.crop((c, c, W - c, H - c))


def remake_meta_dot(dot, pct: float = SMALL_CROP):
    """把归一化坐标换算到"裁边后"的坐标系（忙碌指示灯要叠在正确位置）。"""
    if not dot:
        return None
    k = 1.0 - pct
    return {"cx": round((dot["cx"] - pct / 2) / k, 4),
            "cy": round((dot["cy"] - pct / 2) / k, 4),
            "r": round(dot["r"] / k, 4)}


def main(argv):
    if len(argv) < 2:
        print(__doc__)
        return 2
    ASSETS.mkdir(parents=True, exist_ok=True)
    src = Path(argv[1])
    if not src.is_file():
        print("找不到设计稿：%s" % src)
        return 1
    print("[1] 处理设计稿 %s" % src.name)
    im = Image.open(src)
    print("   原始：%s %s" % (im.size, im.mode))
    dot = find_accent_dot(im)
    im = strip_outer_white(im)

    master = im.resize((MASTER_PNG, MASTER_PNG), Image.LANCZOS)
    png_out = ASSETS / "icon.png"
    master.save(png_out)
    print("[2] 写出 %s（%d×%d RGBA）" % (png_out.name, MASTER_PNG, MASTER_PNG))

    # 小尺寸版：只在确实设了裁切时才生成（裁切会切坏圆角，默认关闭，保留开关备用）
    small = None
    if SMALL_CROP > 0:
        small = optical_crop(master).resize((MASTER_PNG, MASTER_PNG), Image.LANCZOS)
        small_out = ASSETS / "icon-small.png"
        small.save(small_out)
        print("[3] 写出 %s（小尺寸光学补偿版，+%d%% 图形占比）" % (small_out.name, int(SMALL_CROP * 100)))
    else:
        stale = ASSETS / "icon-small.png"
        if stale.is_file():
            stale.unlink()                      # 之前版本留下的补偿版会抢走托盘，必须清掉
            print("[3] 已删除旧的 %s（补偿已关闭，避免它继续被托盘优先使用）" % stale.name)
        else:
            print("[3] 不做小尺寸补偿（裁切会切坏圆角，实测得不偿失）")

    # .ico：逐尺寸烘焙进同一个 ico
    ico_out = ASSETS / "icon.ico"
    frames = []
    for s in ICO_SIZES:
        base = small if (small is not None and s <= SMALL_THRESHOLD) else master
        frames.append(base.resize((s, s), Image.LANCZOS))
    big = frames[-1]
    # ★ 必须 bitmap_format="bmp"：PIL 默认把每一帧写成 **PNG 压缩帧**，而 .NET 的
    #   System.Drawing.Icon（pywebview 的 Windows 后端 + PowerShell 都走它）**读不了 PNG 帧**
    #   —— 实测表现是图标变成彩色噪点（48/16px 提取出来全是雪花），任务栏图标同样会坏。
    #   代价是文件大一些（含 256px 未压缩帧），换来"哪都能读"。
    big.save(ico_out, format="ICO", sizes=[(s, s) for s in ICO_SIZES],
             append_images=frames[:-1], bitmap_format="bmp")
    print("[4] 写出 %s（尺寸：%s%s）"
          % (ico_out.name, ",".join(str(s) for s in ICO_SIZES),
             "；≤%dpx 用补偿版" % SMALL_THRESHOLD if small is not None else ""))

    meta = {"source": src.name, "small_threshold": SMALL_THRESHOLD, "small_crop": SMALL_CROP}
    if dot:
        meta["accent_dot"] = dot
        if small is not None:
            meta["accent_dot_small"] = remake_meta_dot(dot)
            print("[5] 记录橙色圆点：原版 (%.2f, %.2f) r=%.2f ／ 补偿版 (%.2f, %.2f)"
                  % (dot["cx"], dot["cy"], dot["r"],
                     meta["accent_dot_small"]["cx"], meta["accent_dot_small"]["cy"]))
        else:
            print("[5] 记录橙色圆点：中心 (%.2f, %.2f) 半径 %.2f（忙碌指示灯叠在这里）"
                  % (dot["cx"], dot["cy"], dot["r"]))
    else:
        print("[5] 没找到橙色圆点（忙碌指示灯会画在默认位置）")
    (ASSETS / "icon-meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    print("完成 ✅ 托盘用 assets/icon.png；任务栏/窗口（含 desktop.exe）用 assets/icon.ico")
    print("      换完图标记得重编启动器：.venv/Scripts/python.exe static/desktop/build_launcher.py")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
