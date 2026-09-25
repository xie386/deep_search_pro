# -*- coding: utf-8 -*-
"""CLI 文档证据采集 —— 给「CLI 能力描述撰写 AI」提供**权威事实来源**。

背景（2026-09-24 用户报障 + 复盘）：
    原实现只把「CLI 名称 + 只读清单里的子命令路径」喂给模型，模型**没有任何依据**，
    只能望文生义（按命令名联想语义）→ 实测 13 条映射里 3 条会写错，且**全部错在需要判断力的地方**
    （哪些命令需要 ID 才能调、哪个命令是「书名→ID」的解析器），于是示例会把 Agent 教去调一条
    必然报参数错的命令。

设计（用户 2026-09-24 定）：
    ① 语义层：用户填的 `docs` 地址（一般是 CLI 的 GitHub README）→ **唯一事实来源**；
    ② 校验层：`<bin> <cmd> --help` 真值 → 只用来分类与审计（见 tools/cli_registry.py）；
    ③ 兜底：文档抓不到 / 没覆盖那条命令时，回退 help 真值 → 用户粘贴的 help 文本 → 保守化。

本模块只做 ①：把 URL 规范化、安全抓取、抽取与缓存。**不调模型、不消耗 Tavily 额度**（普通 HTTP）。

安全（抓用户填的 URL 属 SSRF 面）：
    - 只允许 http/https；
    - 主机名解析后若落在**环回 / 私有 / 链路本地 / 保留**网段 → 拒绝（含 localhost、*.local）；
    - 超时 15s、响应体上限 40KB、「原始 HTML 去掉标签后取文本」；
    - 结果按 URL 哈希落盘缓存（默认 7 天），README 极少变，避免每次生成都出网。
"""
from __future__ import annotations

import hashlib
import io
import ipaddress
import json
import os
import re
import socket
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
CACHE_DIR = ROOT / "data" / "cli_docs_cache"
MAX_DOC_BYTES = 40_000          # 原始响应体上限
MAX_EVIDENCE_CHARS = 20_000     # 交给模型的证据上限（实测本机三份 README 为 4K/12K/19K）
CACHE_TTL_SECONDS = 7 * 86400
TIMEOUT = 15.0

# 这些小节与「命令怎么用」无关，抓下来只会挤占上下文
_DROP_HEADING_RE = re.compile(
    r"(^\s*#*\s*)(安装|安装说明|install|installation|快速开始|quick\s*start|贡献|contribut\w*|"
    r"许可|licen[cs]e|测试|test\w*|changelog|更新日志|变更记录|版本历史|致谢|acknowledg\w*|"
    r"赞助|sponsor\w*|star\s*历史|roadmap|开发计划)", re.I)


def normalize_docs_url(url: str) -> str:
    """把用户粘的地址规范成**能直接取到 markdown 文本**的地址。

    实测踩过的三种形态（本机三条真实 docs 都验过）：
      · GitHub blob 页（最常见）→ 换成 raw.githubusercontent.com，否则拿到的是 HTML；
      · 仓库根地址         → 补 `HEAD/README.md`（`HEAD` 免去找默认分支）；
      · 没有协议头         → 补 `https://`。
    npm 包页面 / 文档站不做特殊处理：交给调用方回退（实测 `@googleworkspace/cli` 的
    registry `readme` 字段只有 28 字节占位，npm 路线不可靠）。
    """
    u = (url or "").strip().strip("`")
    if not u:
        return ""
    if not re.match(r"^[a-zA-Z][a-zA-Z0-9+.\-]*://", u):
        u = "https://" + u.lstrip("/")
    m = re.match(r"https?://github\.com/([^/]+)/([^/]+)/blob/([^/]+)/(.+)", u)
    if m:
        return "https://raw.githubusercontent.com/%s/%s/%s/%s" % (m.group(1), m.group(2), m.group(3), m.group(4))
    m = re.match(r"https?://github\.com/([^/]+)/([^/]+?)(?:\.git)?/?$", u)
    if m:
        return "https://raw.githubusercontent.com/%s/%s/HEAD/README.md" % (m.group(1), m.group(2))
    m = re.match(r"https?://git(hub|lab)\.com/([^/]+)/([^/]+)/?$", u)
    if m:  # gitlab 之类：给 raw 形态
        return "https://gitlab.com/%s/%s/-/raw/HEAD/README.md" % (m.group(2), m.group(3))
    return u


def _host_is_safe(host: str) -> tuple[bool, str]:
    """SSRF 守卫：主机名解析后不能落在环回/私有/链路本地/保留网段。"""
    h = (host or "").strip().strip("[]").lower()
    if not h:
        return False, "地址里没有主机名"
    if h == "localhost" or h.endswith(".local") or h.endswith(".internal"):
        return False, f"拒绝内网主机 {h}"
    try:
        ip = ipaddress.ip_address(h)
        if ip.is_loopback or ip.is_private or ip.is_link_local or ip.is_reserved or ip.is_multicast:
            return False, f"拒绝非公网地址 {h}"
        return True, ""
    except ValueError:
        pass
    try:
        infos = socket.getaddrinfo(h, None)
    except Exception as e:  # noqa: BLE001
        return False, f"域名解析失败：{h}（{type(e).__name__}）"
    for info in infos:
        try:
            ip = ipaddress.ip_address(info[4][0])
        except ValueError:
            continue
        if ip.is_loopback or ip.is_private or ip.is_link_local or ip.is_reserved:
            return False, f"{h} 解析到非公网地址 {ip}"
    return True, ""


def _html_to_text(html: str) -> str:
    """极简 HTML→文本（只在拿到 HTML 时兜底用，raw 地址才是主路径）。"""
    s = re.sub(r"(?is)<(script|style|noscript)[^>]*>.*?</\1>", " ", html)
    s = re.sub(r"(?is)<br\s*/?>|</p>|</div>|</li>|</h[1-6]>", "\n", s)
    s = re.sub(r"(?is)<[^>]+>", "", s)
    s = (s.replace("&nbsp;", " ").replace("&amp;", "&").replace("&lt;", "<")
          .replace("&gt;", ">").replace("&quot;", '"').replace("&#39;", "'"))
    return re.sub(r"\n{3,}", "\n\n", s)


def _cache_paths(url: str) -> tuple[Path, Path]:
    key = hashlib.sha1(url.encode("utf-8")).hexdigest()[:16]
    return CACHE_DIR / f"{key}.md", CACHE_DIR / f"{key}.json"


def _cache_read(url: str) -> str | None:
    body, meta = _cache_paths(url)
    try:
        if not (body.exists() and meta.exists()):
            return None
        d = json.loads(meta.read_text(encoding="utf-8"))
        if d.get("url") != url or (time.time() - float(d.get("fetched_at") or 0)) > CACHE_TTL_SECONDS:
            return None
        return body.read_text(encoding="utf-8")
    except Exception:  # noqa: BLE001
        return None


def _cache_write(url: str, text: str) -> None:
    body, meta = _cache_paths(url)
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        body.write_text(text, encoding="utf-8")
        meta.write_text(json.dumps({"url": url, "fetched_at": time.time(),
                                    "chars": len(text)}, ensure_ascii=False), encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass


def fetch_docs(url: str, *, force: bool = False) -> dict:
    """抓取文档。返回 `{ok, text, note, url, source}`；永不抛异常（失败要能优雅回退）。

    `source` 取值：`cache` / `direct` / `proxy` / `""`（失败）。
    """
    raw = normalize_docs_url(url)
    if not raw:
        return {"ok": False, "text": "", "note": "没有填文档地址", "url": "", "source": ""}
    if not force:
        hit = _cache_read(raw)
        if hit:
            return {"ok": True, "text": hit, "note": "命中本地缓存", "url": raw, "source": "cache"}

    from urllib.parse import urlparse
    p = urlparse(raw)
    if p.scheme not in ("http", "https"):
        return {"ok": False, "text": "", "note": f"只支持 http/https：{p.scheme}", "url": raw, "source": ""}
    safe, why = _host_is_safe(p.hostname or "")
    if not safe:
        return {"ok": False, "text": "", "note": why, "url": raw, "source": ""}

    last = "未知错误"
    for tag, trust_env in (("direct", False), ("proxy", True)):
        try:
            with httpx.Client(timeout=TIMEOUT, follow_redirects=True, trust_env=trust_env) as c:
                r = c.get(raw, headers={"User-Agent": "zx-intel-ability-writer/1.0"})
            if r.status_code != 200:
                last = f"{tag} HTTP {r.status_code}"
                continue
            data = r.content[:MAX_DOC_BYTES]
            txt = data.decode(r.encoding or "utf-8", errors="replace")
            ctype = (r.headers.get("content-type") or "").lower()
            if "html" in ctype or txt.lstrip()[:1] == "<":
                txt = _html_to_text(txt)
            if not txt.strip():
                last = f"{tag} 抓到了空正文"
                continue
            _cache_write(raw, txt)
            return {"ok": True, "text": txt, "note": f"已抓取（{tag}）", "url": raw, "source": tag}
        except Exception as e:  # noqa: BLE001
            last = f"{tag} 失败：{type(e).__name__}: {str(e)[:60]}"
    return {"ok": False, "text": "", "note": last, "url": raw, "source": ""}


def extract_evidence(text: str, cmds: list[str], bin_name: str = "") -> str:
    """从文档里抽出「与命令用法相关」的部分，丢掉安装/贡献/许可等噪音。

    规则（实测三份 README 都适用）：保留所有**围栏代码块**（用法示例几乎都在这里）、
    以及提到「可执行名 / 任一子命令首词」的正文行；丢掉无关小节。
    """
    if not text:
        return ""
    heads = [c.split()[0] for c in cmds if c.split()]
    keep_tokens = {bin_name} | set(heads) | {"Usage", "usage", "用法", "命令", "command"}

    out: list[str] = []
    in_code = False
    dropping = False
    for line in text.splitlines():
        fence = line.strip().startswith("```")
        if fence:
            in_code = not in_code
            if not dropping:
                out.append(line)
            continue
        if in_code:
            if not dropping:
                out.append(line)
            continue
        if line.lstrip().startswith("#"):
            dropping = bool(_DROP_HEADING_RE.search(line))
        if dropping:
            continue
        if any(t and t in line for t in keep_tokens):
            out.append(line)
    # 兜底：抽得太干净（< 400 字）就整篇给，避免把有用的东西滤没了
    body = "\n".join(out).strip()
    if len(body) < 400:
        body = text.strip()
    if len(body) > MAX_EVIDENCE_CHARS:
        body = body[:MAX_EVIDENCE_CHARS] + "\n…（文档较长，已截断）"
    return body


def coverage(text: str, cmds: list[str]) -> tuple[list[str], list[str]]:
    """文档对只读清单的覆盖情况：返回 (已提及的命令, 未提及的命令)。"""
    if not text:
        return [], list(cmds)
    covered, missing = [], []
    for c in cmds:
        head = c.split()[0]
        (covered if (c in text or (head and head in text)) else missing).append(c)
    return covered, missing
