"""聊天图片的落盘 / 压缩 / 校验 与「本轮多模态内容」装配（v3.1 视觉能力）

职责边界（**只做这一件事**）：
  · 用户贴进来的图 → 校验（真格式、防伪造）→ 压缩 → 落盘（按账号隔离）→ 返回 image_id；
  · 把 image_id 还原成 LangChain 能吃的多模态 content blocks（**只喂给本轮**，不进历史 ✓ 用户 2026-09-30 拍板）；
  · 不在本文件里做 HTTP、不 import api（与 tools/failure_log.py 同一层：API 侧基础设施，非 agent 工具）。

设计要点（都是踩过坑才写下来的）：
  1) **不信任文件名/扩展名** —— 只看 magic bytes ✓（用户可以改扩展名 ✗）；
  2) **账号隔离靠路径由 account_id 决定** —— 绝不接受外部传入路径 ✓ 防目录穿越（id 只允许 [0-9a-f]{16} ✓）；
  3) **压缩** ✓ —— 视觉模型按图计费/超限，长边压到 ≤ MAX_EDGE，再按质量降到 TARGET_KB 以下；
  4) **本轮限定** ✓ —— build_content() 只给这一轮用；历史里存 `image_id` 引用即可（前端按需回显 ✓）；
  5) 零魔法：常量集中在上方，全部可覆盖。
"""

from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import re
import sqlite3  # noqa: F401  (仅为与本目录其他模块保持一致的导入风格，本模块不用)
import time
from dataclasses import dataclass, asdict

# ───────────────────────────── 常量（拍板值）─────────────────────────────
MAX_IMAGES = 3                 # 每次提问最多 3 张 ✓ 用户 2026-09-30 拍板
MAX_UPLOAD_MB = 12             # 单张上传体积上限（压缩前）
MAX_EDGE = 1568                # 长边上限（视觉模型常用上限；超过会被服务端裁掉或报错）
TARGET_KB = 800                # 压缩目标（超过就降 quality / 缩边）
ID_RE = re.compile(r"^[0-9a-f]{16}\.(jpg|png|webp)$")   # image_id 的**唯一合法形状** ✓（防穿越）
_KINDS = {"image/jpeg": (".jpg", "JPEG"), "image/png": (".png", "PNG"), "image/webp": (".webp", "WEBP")}


class ChatImageError(Exception):
    """给 API 层用的人话错误（前端直接展示 ✓）。"""


# ───────────────────────────── 格式校验 ─────────────────────────────
def sniff(data: bytes) -> str | None:
    """按 **magic bytes** 判断真格式 ✓（不看扩展名 ✗）。返回 mime 或 None。"""
    if not data or len(data) < 12:
        return None
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


# ───────────────────────────── 压缩 ─────────────────────────────
def compress(data: bytes, mime: str, max_edge: int = MAX_EDGE, target_kb: int = TARGET_KB) -> tuple[bytes, str, int, int]:
    """压缩为「长边 ≤ max_edge、体积尽量 ≤ target_kb」的图 ✓。返回 (bytes, mime, w, h)。

    · 有透明通道的 PNG 保留 PNG ✓（否则透明会变黑 ✗）；其余一律转 JPEG（更省 ✓）。
    · 已经够小够小的原图**原样返回** ✓（不无谓重编码，省时也省质量）。
    """
    try:
        from PIL import Image  # 延迟 import：本项目 PIL(pillow) 已装 ✓
    except Exception as exc:  # pragma: no cover - 环境缺 pillow 时的兜底
        raise ChatImageError("服务器缺少图片处理库（pillow），无法处理图片") from exc

    orig = data
    size = len(data)
    im = Image.open(io.BytesIO(data))
    im.load()
    w, h = im.size
    keep_png = mime == "image/png" and (im.mode in ("RGBA", "LA", "P") or "transparency" in im.info)

    scale = 1.0
    if max(w, h) > max_edge:
        scale = max_edge / float(max(w, h))

    out_mime = "image/png" if keep_png else "image/jpeg"
    if scale == 1.0 and size <= target_kb * 1024 and mime in ("image/jpeg", "image/png"):
        # 已经够小 → 原样返回（但 mime 以真实嗅探为准 ✓）
        return orig, ("image/png" if keep_png else mime if mime in _KINDS else "image/jpeg"), w, h

    for attempt in range(6):
        im2 = im
        if scale < 1.0:
            im2 = im.resize((max(1, int(w * scale)), max(1, int(h * scale))), Image.LANCZOS)
        buf = io.BytesIO()
        if keep_png:
            im2.save(buf, format="PNG", optimize=True)
        else:
            rgb = im2.convert("RGB") if im2.mode not in ("RGB", "L") else im2
            q = max(45, 88 - attempt * 9)
            rgb.save(buf, format="JPEG", quality=q, optimize=True, progressive=True)
        out = buf.getvalue()
        if len(out) <= target_kb * 1024:
            return out, out_mime, im2.size[0], im2.size[1]
        scale = scale * 0.82 if scale < 1.0 else 0.82
    return out, out_mime, im2.size[0], im2.size[1]


# ───────────────────────────── 落盘（账号隔离）─────────────────────────────
@dataclass
class SavedImage:
    image_id: str        # 形如 1a2b3c4d5e6f7a8b.jpg ✓（唯一合法形状 ✓）
    mime: str
    bytes: int
    width: int
    height: int
    sha256: str
    created_at: int

    def to_dict(self) -> dict:
        return asdict(self)


def images_dir(account_id: int | str) -> str:
    """`pic/{账号}/chat_images/` ✓ —— 与项目既有图片目录（pic/{user}/）同源 ✓ 按账号隔离 ✓。"""
    # ★ 不臆测项目里的 helper ✗（第一版写成 utils.paths.pic_dir → 该模块根本不存在 ✗）：
    #   先探测项目**真实存在**的助手函数，探测不到就从项目根兜底拼 pic/{账号}/ ✓ 结果恒定。
    cands = []
    for modname, fns in (("utils.paths", ("pic_dir", "account_pic_dir", "user_pic_dir")),
                         ("utils", ("pic_dir", "account_pic_dir")),
                         ("utils.files", ("pic_dir", "account_pic_dir"))):
        try:
            mod = __import__(modname, fromlist=["x"])
        except Exception:
            continue
        for fn in fns:
            f = getattr(mod, fn, None)
            if callable(f):
                try:
                    cands.append(str(f(account_id)))
                except Exception:
                    pass
    if cands:
        base = os.path.join(cands[0], "chat_images")
    else:
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # 项目根 ✓
        base = os.path.join(root, "pic", str(account_id), "chat_images")
    os.makedirs(base, exist_ok=True)
    return base


def save(account_id: int | str, data: bytes, *, limit_checked: int = 1) -> SavedImage:
    """校验 → 压缩 → 落盘 → 返回 SavedImage ✓（同内容幂等：sha1 当 id ✓ 天然去重）"""
    if limit_checked > MAX_IMAGES:
        raise ChatImageError(f"一次最多上传 {MAX_IMAGES} 张图片")
    if not data:
        raise ChatImageError("图片内容为空")
    if len(data) > MAX_UPLOAD_MB * 1024 * 1024:
        raise ChatImageError(f"单张图片不能超过 {MAX_UPLOAD_MB}MB（可以先压缩再上传）")
    mime = sniff(data)
    if mime is None:
        raise ChatImageError("这不是可识别的图片（支持 JPG / PNG / WebP）")
    if mime == "image/webp":
        # PIL 读 webp 没问题 ✓，但输出统一转 JPEG/PNG，避免下游兼容问题 ✓
        pass
    out, out_mime, w, h = compress(data, mime)
    sha = hashlib.sha256(out).hexdigest()
    ext = "png" if out_mime == "image/png" else "jpg"
    image_id = sha[:16] + "." + ext
    if not ID_RE.match(image_id):  # 自检：id 形状必须合法（防自己写错 ✓）
        raise ChatImageError("内部错误：生成的 image_id 不合法")
    path = os.path.join(images_dir(account_id), image_id)
    if not os.path.exists(path):
        tmp = path + ".part"
        with open(tmp, "wb") as fh:
            fh.write(out)
        os.replace(tmp, path)   # 原子落盘 ✓（避免半张图被读到 ✗）
    return SavedImage(image_id=image_id, mime=out_mime, bytes=len(out), width=w, height=h,
                      sha256=sha, created_at=int(time.time()))


def resolve(account_id: int | str, image_id: str) -> str:
    """image_id → 绝对路径 ✓（**只允许合法 id** ✓ 防目录穿越 ✗）"""
    if not isinstance(image_id, str) or not ID_RE.match(image_id):
        raise ChatImageError("图片标识不合法")
    path = os.path.join(images_dir(account_id), image_id)
    if not os.path.exists(path):
        raise ChatImageError("图片不存在或已被清理")
    return path


def read(account_id: int | str, image_id: str) -> bytes:
    with open(resolve(account_id, image_id), "rb") as fh:
        return fh.read()


def to_data_url(account_id: int | str, image_id: str) -> str:
    """给 LangChain 用的 data URL（`image_url` 形态 ✓）"""
    mime = "image/png" if image_id.endswith(".png") else "image/jpeg"
    return "data:" + mime + ";base64," + base64.b64encode(read(account_id, image_id)).decode("ascii")


# ───────────────────────────── 本轮多模态内容装配 ─────────────────────────────
def build_content(text: str, image_ids: list[str] | None, account_id: int | str) -> str | list:
    """构造**本轮** HumanMessage 的 content ✓

    · 没图 → 直接返回字符串 ✓（与现状完全一致 → 零回归 ✓✓ 这点很重要：老链路一个字都不变）；
    · 有图 → 返回 blocks：先文字后图（顺序有讲究：模型先读指令再看图 ✓ 实测更稳）；
    · 图缺失 → **明确报错** ✓ 绝不静默丢图 ✗。
    """
    ids = [i for i in (image_ids or []) if i]
    if not ids:
        return text
    if len(ids) > MAX_IMAGES:
        raise ChatImageError(f"一次最多 {MAX_IMAGES} 张图片")
    blocks: list[dict] = [{"type": "text", "text": text or "请看图片。"}]
    for iid in ids:
        blocks.append({"type": "image_url", "image_url": {"url": to_data_url(account_id, iid)}})
    return blocks


def legacy_json(image_ids: list[str] | None) -> str:
    """给消息落库用的一行 JSON（**历史里只存引用** ✓ 不存图 ✓ 用户拍板「只跟本轮」✓）"""
    return json.dumps({"images": [i for i in (image_ids or []) if i]}, ensure_ascii=False)


# ───────────────────────────── 清理（保守）─────────────────────────────
def prune(account_id: int | str, max_age_days: int = 14, keep: list[str] | None = None) -> dict:
    """删掉过期的聊天图片 ✓（**保守**：默认只删 14 天前、且不在 keep 里的 ✓ 返回统计 ✓）"""
    keepset = set(keep or [])
    base = images_dir(account_id)
    cutoff = time.time() - max_age_days * 86400
    removed, kept = 0, 0
    for name in os.listdir(base):
        if not ID_RE.match(name) or name in keepset:
            kept += 1
            continue
        p = os.path.join(base, name)
        try:
            if os.path.getmtime(p) < cutoff:
                os.remove(p)
                removed += 1
            else:
                kept += 1
        except OSError:
            kept += 1
    return {"removed": removed, "kept": kept, "max_age_days": max_age_days}
