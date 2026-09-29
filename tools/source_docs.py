# -*- coding: utf-8 -*-
"""M5c-2'：把「README / 文档地址」解析成文本 —— 支持 http(s) 网址与本地文件路径。

为什么需要：API/MCP 来源的工具描述原本只有「文档里的技术名」或「服务自述」，
用户没法用中文补一句"我会怎么问"。实测 BNF 那 8 个工具的描述**全是英文**，
中文提问时向量路几乎命中不了 —— 这是基准 precision@3 卡在 0.29 的真因之一。
于是给来源加一个可选「README 地址」，让能力撰写 AI 据此**预写**中文描述，用户一键导入后可自由改。

两种形态（用户 2026-09-27 定）：
  · `http(s)://…` → 交给 `tools.cli_docs.fetch_docs`（带 7 天缓存、直连/代理两轮、主机安全校验）；
  · 其它 = **本地文件路径**（本地 stdio MCP 服务一般有随仓库的 README.md）→ 直接读。

本地文件读取的边界（这是**用户自己填的路径**，但仍要保守）：
  · 拒绝明显二进制扩展名（.png/.exe/.zip/…）；
  · ≤ 300 KB（超出只读前 300 KB，并在 note 里说明已截断）；
  · 命中 NUL 字节 → 判定二进制，拒绝；
  · 不存在 / 不是文件 → 返回明确原因，不抛栈。

返回形状与 `cli_docs.fetch_docs` **一致**（`{ok,text,source,url,note}`），
这样能力撰写 AI 那边两种证据可以同一条路径处理。
"""
from __future__ import annotations

import os
import re
from pathlib import Path

MAX_FILE_BYTES = 300_000          # 本地文件读取上限（超出只读这么多，并注明）
_BINARY_EXT = {
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".ico", ".pdf", ".zip", ".gz", ".7z", ".rar",
    ".exe", ".dll", ".so", ".dylib", ".bin", ".pyc", ".pyd", ".mp3", ".mp4", ".mov", ".wav", ".woff",
    ".woff2", ".ttf", ".otf", ".db", ".sqlite", ".sqlite3", ".onnx", ".pt", ".safetensors", ".whl",
}
_URL_RE = re.compile(r"^https?://", re.I)


def _looks_like_url(docs: str) -> bool:
    return bool(_URL_RE.match((docs or "").strip()))


def read_local(path: str, *, base_dir: str | Path | None = None) -> dict:
    """读一份本地 README/文档。返回 `{ok,text,source,url,note}`（形状同 fetch_docs）。"""
    raw = (path or "").strip().strip('"').strip("'")
    if not raw:
        return {"ok": False, "text": "", "source": "file", "url": "", "note": "没填路径"}
    p = Path(raw)
    if not p.is_absolute() and base_dir:
        p = Path(base_dir) / p
    try:
        p = p.expanduser()
    except Exception:  # noqa: BLE001
        pass
    if not p.exists():
        return {"ok": False, "text": "", "source": "file", "url": str(p), "note": "文件不存在"}
    if p.is_dir():
        return {"ok": False, "text": "", "source": "file", "url": str(p),
                "note": "这是个目录，请填具体文件（如 README.md）"}
    if p.suffix.lower() in _BINARY_EXT:
        return {"ok": False, "text": "", "source": "file", "url": str(p),
                "note": "看着是二进制文件（%s），请填 Markdown/纯文本" % p.suffix.lower()}
    try:
        size = os.path.getsize(p)
        with open(p, "rb") as f:
            data = f.read(MAX_FILE_BYTES)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "text": "", "source": "file", "url": str(p),
                "note": "读取失败：%s: %s" % (type(e).__name__, e)}
    if b"\x00" in data[:4096]:
        return {"ok": False, "text": "", "source": "file", "url": str(p), "note": "内容像二进制（含 NUL 字节）"}
    text = data.decode("utf-8", errors="replace")
    note = "本地文件 %d 字节" % size
    if size > MAX_FILE_BYTES:
        note += "（超过 %d 字节，只读了前 %d 字节）" % (MAX_FILE_BYTES, MAX_FILE_BYTES)
    return {"ok": bool(text.strip()), "text": text, "source": "file", "url": str(p),
            "note": note if text.strip() else "文件是空的"}


def resolve(docs: str, *, force: bool = False, base_dir: str | Path | None = None) -> dict:
    """把用户填的「README / 文档地址」解析成文本。

    · 空 → 明确说明没填（**不是错误**，撰写 AI 会退化成"只按工具清单与参数写"）；
    · http(s) → `cli_docs.fetch_docs`（复用它的缓存、代理回退与主机安全校验）；
    · 其它 → 本地文件（相对路径按 `base_dir` 解析，例如本地 MCP 服务的工作目录）。
    """
    docs = (docs or "").strip()
    if not docs:
        return {"ok": False, "text": "", "source": "", "url": "", "note": "没填 README / 文档地址"}
    if _looks_like_url(docs):
        from tools import cli_docs
        d = cli_docs.fetch_docs(docs, force=force)
        return {"ok": bool(d.get("ok")), "text": d.get("text") or "", "source": d.get("source") or "http",
                "url": d.get("url") or docs, "note": d.get("note") or ""}
    return read_local(docs, base_dir=base_dir)
