# -*- coding: utf-8 -*-
"""批量抠图 + 统一尺寸，产出桌宠帧（v4.1-04 素材流水线）。

输入目录（默认 `C:/Users/ZQK/Desktop/subwinpic`）约定：

    wait_nothing/<组号>/*.png   待机动作，每组一串帧（组间随机播放）
    say/*.png                   agent 输出中
    think/*.png                 思考中
    wait_keyboard/*.png         输入框被选中
    clicked_or_grab/*.png       被点击 / 被拖拽

输出：`front/desktop/assets/pet/<状态><组>_<序号>.png`，统一 256×256、透明底。

为什么每一组用**同一套缩放与偏移**：同一动作的帧之间本来就有轻微位移（呼吸、点头），
逐帧各自居中会把动作抹平；正确做法是先算全组并集的包围盒，再对这一组统一缩放对齐。

运行（独立环境，不动项目 venv）：

    D:/code/_assetpip/venv/Scripts/python.exe scripts/pet_assets.py
    D:/code/_assetpip/venv/Scripts/python.exe scripts/pet_assets.py --src <目录> --out <目录>

依赖：rembg[cpu]（MIT）+ onnxruntime + pillow；模型 isnet-anime（动漫专用，首跑自动下载 176MB）。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from PIL import Image

CANVAS = 256            # 画布边长（文档约定 256）
MARGIN = 10             # 四周留白
ALPHA_KEEP = 12         # 低于此 alpha 视为纯背景（去掉抠图的灰色雾边）

# 逐组微调：同一角色在不同图里「取景松紧」不同（有的只到胸口、有的带肩），
# 单纯按包围盒铺满画布会让「特写那组」的头明显更大。这里给需要缩小的组一个系数。
SCALE_OVERRIDE = {
    "idle1": 0.80,     # 胸口以上特写，按包围盒铺满会显得头大
}

# 这两组的背景与人贴在一起（idle3 是墙 + 黑色圆环、idle5 是被褥），通用抠图分不清，
# 会留一条深色墙带/一整块被褥 —— 需要重拍（纯色背景）或手工抠，先不产出。
SKIP_GROUPS = {"idle3", "idle5"}

# 原图左右边缘混进了别的物体（墙上的黑色圆环、背景板），通用抠图分不清「道具 vs 背景」。
# 这里按比例裁掉那一侧（left/top/right/bottom ∈ 0~1），再走抠图。
CROPS = {}   # 需要时按「组名: (左, 上, 右, 下) 比例」补，例如 {"idle3": (0.14, 0, 0.86, 1)}

# 目录名 → （输出前缀, 是否多组）
GROUPS = {
    "wait_nothing": ("idle", True),      # 多组：idle1 / idle2 ...
    "say": ("say", False),
    "think": ("think", False),
    "wait_keyboard": ("waitkb", False),
    "clicked_or_grab": ("click", False),
}


CUT_CACHE = Path("D:/code/_assetpip/_cutcache")
KEEP_RATIO = 0.02          # 连通块保留阈值（main 里按参数覆盖）


def load_cut(path: Path, session, model: str = "", pfx: str = "") -> Image.Image:
    """抠图并做一次轻量边缘清理（去雾边，保留画稿自带的描边/阴影）。

    结果按「文件路径 + 修改时间 + 模型」缓存：调参数重跑时不必再等模型推理。
    """
    from rembg import remove

    box = CROPS.get(pfx)
    key = "%s|%d|%s|%s" % (path.resolve(), path.stat().st_mtime_ns, model, box)
    import hashlib
    cache = CUT_CACHE / (hashlib.sha1(key.encode("utf-8")).hexdigest() + ".png")
    if cache.is_file():
        return Image.open(cache).convert("RGBA")
    im = Image.open(path).convert("RGB")
    if box:
        w, h = im.size
        im = im.crop((int(w * box[0]), int(h * box[1]), int(w * box[2]), int(h * box[3])))
    out = remove(im, session=session).convert("RGBA")
    out = drop_small_blobs(out, KEEP_RATIO)
    out = tighten_alpha(out)
    a = out.split()[3]
    a = a.point(lambda v: 0 if v < ALPHA_KEEP else min(255, int((v - ALPHA_KEEP) * 255 / (255 - ALPHA_KEEP))))
    out.putalpha(a)
    try:
        CUT_CACHE.mkdir(parents=True, exist_ok=True)
        out.save(cache, "PNG")
    except Exception:                                  # noqa: BLE001
        pass
    return out


def bbox_of(im: Image.Image, thr: int = 16):
    a = im.split()[3].point(lambda v: 255 if v >= thr else 0)
    return a.getbbox()


def tighten_alpha(im: Image.Image, grow: int = 2, thr: int = 200) -> Image.Image:
    """削掉人物外围的低透明度雾气/投影，只保留紧贴不透明核心的一圈软边。

    做法：取 alpha>200 的核心 → 膨胀 grow 像素 → 用它裁掉其余的低 alpha 像素。
    膨胀这 2px 正好保住头发边缘的过渡，同时把画稿自带的那层灰色投影切掉。
    """
    import numpy as np
    from scipy import ndimage as ndi

    a = np.array(im.split()[3])
    core = a > thr
    if not core.any():
        return im
    grown = ndi.binary_dilation(core, iterations=max(1, grow))
    arr = np.array(im)
    arr[..., 3] = np.where(grown, a, 0)
    return Image.fromarray(arr, "RGBA")


def drop_small_blobs(im: Image.Image, keep_ratio: float = 0.02) -> Image.Image:
    """只保留面积够大的连通块 —— 抠图常把截图边缘的色块/阴影留成一块「漂浮的方块」。

    判定：连通域面积 ≥ 最大连通域的 keep_ratio（默认 2%）。人物的手臂、尾巴一般远大于此，
    而残留的矩形边框、边角阴影会被丢掉。
    """
    import numpy as np
    from scipy import ndimage as ndi

    a = np.array(im.split()[3])
    mask = a > 16
    lab, n = ndi.label(mask)
    if n <= 1:
        return im
    sizes = ndi.sum(mask, lab, range(1, n + 1))
    biggest = float(sizes.max()) if len(sizes) else 0.0
    keep = {i + 1 for i, s in enumerate(sizes) if s >= biggest * keep_ratio}
    if len(keep) == n:
        return im
    keep_mask = np.isin(lab, list(keep))
    out = im.copy()
    arr = np.array(out)
    arr[..., 3] = np.where(keep_mask, arr[..., 3], 0)
    return Image.fromarray(arr, "RGBA")


def group_box(frames: list[Image.Image]):
    """一组帧的并集包围盒（保留组内的呼吸/点头位移）。"""
    boxes = [bbox_of(f) for f in frames]
    boxes = [b for b in boxes if b]
    if not boxes:
        return None
    return (min(b[0] for b in boxes), min(b[1] for b in boxes),
            max(b[2] for b in boxes), max(b[3] for b in boxes))


def build_group(frames: list[Image.Image], prefix: str, out_dir: Path, scale: float | None = None) -> list[Path]:
    """一组的帧统一缩放/对齐后落盘，返回生成的文件列表。

    `scale` 为空时按本组自适应；由调用方传入全局 scale 时，跨组的人物大小才一致
    （否则「近景那组」会被放大，切状态时宠物会忽大忽小）。
    """
    box = group_box(frames)
    if not box:
        return []
    left, top, right, bottom = box

    avail = CANVAS - MARGIN * 2
    if scale is None:
        scale = min(avail / max(1, right - left), avail / max(1, bottom - top))
    cx_src = (left + right) / 2.0
    # 锚点：并集顶部贴到 MARGIN，水平居中
    dx = CANVAS / 2.0 - cx_src * scale
    dy = MARGIN - top * scale

    written = []
    for i, im in enumerate(frames):
        w = max(1, int(round(im.width * scale)))
        h = max(1, int(round(im.height * scale)))
        resized = im.resize((w, h), Image.LANCZOS)
        canvas = Image.new("RGBA", (CANVAS, CANVAS), (0, 0, 0, 0))
        canvas.alpha_composite(resized, (int(round(dx)), int(round(dy))))
        p = out_dir / ("%s_%02d.png" % (prefix, i))
        canvas.save(p, "PNG", optimize=True)
        written.append(p)
    return written


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", default=r"C:/Users/ZQK/Desktop/subwinpic")
    ap.add_argument("--out", default=str(Path(__file__).resolve().parents[1] / "front" / "desktop" / "assets" / "pet"))
    ap.add_argument("--model", default="birefnet-general")
    ap.add_argument("--only", default="", help="只跑这些组（逗号分隔，如 idle3,say）")
    ap.add_argument("--keep-ratio", type=float, default=0.02, help="连通块保留阈值（占最大块的比例）")
    args = ap.parse_args(argv)

    src, out = Path(args.src), Path(args.out)
    if not src.is_dir():
        print("源目录不存在：%s" % src)
        return 2
    out.mkdir(parents=True, exist_ok=True)

    global KEEP_RATIO
    KEEP_RATIO = args.keep_ratio
    from rembg import new_session
    session = new_session(args.model)

    # 第一遍：抠图 + 收框（只算几何，先不缩放）
    jobs = []
    for folder, (prefix, multi) in GROUPS.items():
        d = src / folder
        if not d.is_dir():
            print("跳过（无此目录）：%s" % folder)
            continue
        if multi:
            pairs = [(("%s%d" % (prefix, int(sub.name) if sub.name.isdigit() else 1)), sorted(sub.glob("*.png")))
                     for sub in sorted(d.iterdir()) if sub.is_dir()]
        else:
            pairs = [(prefix, sorted(d.glob("*.png")))]
        for pfx, files in pairs:
            if not files:
                continue
            if pfx in SKIP_GROUPS:
                print("跳过 %s（原图背景与人粘连：墙带/被褥，通用抠图会留下色块；建议重拍或手工抠）" % pfx)
                continue
            frames = [load_cut(f, session, args.model, pfx) for f in files]
            box = group_box(frames)
            if box:
                jobs.append((pfx, frames, box))

    # 每组按自身包围盒铺满画布（取景松紧不同 → 用 SCALE_OVERRIDE 校正），
    # 这样不同分辨率来源的同一角色会得到同样的绝对大小。
    avail = CANVAS - MARGIN * 2
    only = {s.strip() for s in args.only.split(",") if s.strip()}
    if only:
        jobs = [j for j in jobs if j[0] in only]

    report = {}
    for pfx, frames, box in jobs:
        fit = min(avail / max(1, box[2] - box[0]), avail / max(1, box[3] - box[1]))
        sc = fit * SCALE_OVERRIDE.get(pfx, 1.0)
        written = build_group(frames, pfx, out, sc)
        report[pfx] = {"frames": [p.name for p in written],
                       "fit_scale": round(fit, 4), "applied_scale": round(sc, 4)}
        print("%-8s %d 帧  fit=%.3f 用=%.3f → %s"
              % (pfx, len(written), fit, sc, ", ".join(p.name for p in written)))

    meta = {
        "canvas": [CANVAS, CANVAS],
        "margin": MARGIN,
        "model": args.model,
        "anchor": "top-center",
        "fps": {"say": 8, "think": 6, "waitkb": 6, "click": 10},
        "idle_switch_seconds": 2,
        "groups": report,
        "note": "idle1..N 为多组待机动作，运行时每 2 秒随机切一组循环播放",
    }
    (out / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    total = sum(len(v["frames"]) for v in report.values())
    print("共 %d 组，%d 帧；meta 已写 %s" % (len(report), total, out / "meta.json"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
