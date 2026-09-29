# -*- coding: utf-8 -*-
"""MCP 执行驱动（v3.0 M2-2；与 `api_driver` 同形：给定来源配置 + 工具名 + 参数 → 归一化结果）。

设计要点（都来自真机探针，2026-09-27 实测 SDK **2.2.0**）：

1. **惰性导入** `mcp`：`import mcp` 实测要 **5.0s**（它带 httpx2/opentelemetry/jsonschema 一堆重依赖）。
   应用启动路径上绝不能碰它 —— 只有真正要连 MCP 时才 import（静默降级为"未安装 MCP SDK"的拒绝）。
   用 `tests/test_mcp_lazy_import.py` 钉住这条（否则启动会白白 +5s）。
2. **SDK v2 是纯 async**（`async with Client(...) as c: await c.list_tools()`），而本项目整条链路是同步的
   → 这里做 async→sync 桥接；并且**不能在已有事件循环的线程里直接 `asyncio.run`**（会抛
   `asyncio.run() cannot be called from a running event loop`）→ 换线程跑私有 loop。
3. **三种连接**（方案 §M2-2）：`stdio`（command/args）、`http`（Streamable HTTP url）、以及**进程内**
   （测试用：直接把 `MCPServer` 实例交给 Client，`transport="inproc"`）。
4. **stdio 的安全边界**（方案 §4.5 裁决 B）：命令必须命中来源里配置的**白名单**，否则拒绝。
   确认界面会把完整命令行显示给用户看（那是"显式确认"的一部分，在 M2-4/M2-5）。
5. **只读提示只是提示**：`annotations.read_only_hint` 只用于**预勾选**，最终以"人工确认"为准
   （MCP 规范明说 annotations 是 hint、客户端不得据此做安全决策）。
6. **`send_ping` 已经不可用**：SDK 警告它"as of 2026-07-28 移除，仅 legacy 模式可用"（实测报
   `MCPError Method not found`）→ 体检用 `list_tools()` 当健康检查（既能证明握手成功，又顺带拿到工具表）。
7. **stdio 首次连接要给足超时**：子进程要 import mcp（≈5s），实测 15s 超时会失败、20s 起才稳。
"""
from __future__ import annotations

import asyncio
import concurrent.futures
import json
import time

MAX_RESULT_CHARS = 6000          # 与 CLI / API 同量级（网关还会再按整段裁剪一次）

DEFAULT_TIMEOUT = 30.0
DEFAULT_STDIO_COMMANDS = ("npx", "uvx", "uv", "python", "python3", "node", "deno", "bun", "docker", "pnpx")


class McpDriverError(Exception):
    """可读的失败原因（直接给用户/模型看）。"""


# ---------------------------------------------------------------- 惰性 SDK 入口
def _sdk():
    """惰性拿到 SDK 符号。未安装时给出可照做的中文原因（不影响应用其余部分）。"""
    try:
        from mcp import Client, StdioServerParameters      # noqa: PLC0415  ★ 故意放在函数内
        from mcp.server import MCPServer                   # noqa: PLC0415
        return Client, StdioServerParameters, MCPServer
    except Exception as e:  # pragma: no cover - 只在未装依赖时触发
        raise McpDriverError("未安装 MCP SDK（`uv add \"mcp>=2,<3\"` 后重启后端）：%s" % e)


# ---------------------------------------------------------------- async → sync
def run_sync(coro, timeout: float | None = None):
    """在没有事件循环的线程里跑 async；若**已有**运行中的 loop，就换一个线程跑。

    为什么不能无脑 `asyncio.run`：在 async 端点/回调里调用会抛 "cannot be called from a running
    event loop"。本项目的工具调用目前都是同步路径，但这条护栏很便宜、避免将来踩雷。
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(asyncio.wait_for(coro, timeout) if timeout else coro)
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
        fut = ex.submit(lambda: asyncio.run(asyncio.wait_for(coro, timeout) if timeout else coro))
        return fut.result()


# ---------------------------------------------------------------- 配置校验（体检前 3 步，纯函数）
def _cmd_key(cmd: str) -> str:
    """命令名归一：取 basename、去 `.exe`、小写 —— 白名单两侧都用它比（`python` 与 `python.exe` 等价）。"""
    base = str(cmd or "").strip().replace("\\", "/").split("/")[-1].lower()
    return base[:-4] if base.endswith(".exe") else base


def validate_cfg(cfg: dict) -> list[dict]:
    """把来源配置检查成"分步体检"的形状（与 api 的 `/test` 同款 steps）。

    M2-3 的 `probe()` 会在这三步之后接上"真的连一次、列工具"的步骤。
    """
    cfg = cfg or {}
    transport = (cfg.get("transport") or "").strip().lower()
    steps = [{"name": "transport 合法", "ok": transport in ("stdio", "http", "inproc"),
              "detail": transport or "未填（stdio / http）"}]
    if transport == "stdio":
        cmd = (cfg.get("command") or "").strip()
        allow = [_cmd_key(x) for x in (cfg.get("allow_stdio_commands") or DEFAULT_STDIO_COMMANDS)]
        base = _cmd_key(cmd)
        steps.append({"name": "命令已填", "ok": bool(cmd), "detail": cmd or "空"})
        steps.append({"name": "命令在白名单内", "ok": bool(cmd) and base in allow,
                      "detail": "%s ∈ [%s]" % (base or "?", ", ".join(sorted(set(allow))))})
        args = cfg.get("args") or []
        steps.append({"name": "args 是字符串数组", "ok": isinstance(args, list) and all(isinstance(a, str) for a in args),
                      "detail": " ".join(str(a) for a in args)[:120] or "（无）"})
    elif transport == "http":
        url = (cfg.get("url") or "").strip()
        ok_url = url.startswith("http://") or url.startswith("https://")
        steps.append({"name": "url 合法", "ok": ok_url, "detail": url or "空"})
        if ok_url and not cfg.get("allow_private", True):
            from urllib.parse import urlparse
            host = (urlparse(url).hostname or "").lower()
            private = host in ("localhost", "127.0.0.1", "::1") or host.startswith(("10.", "192.168.", "172."))
            steps.append({"name": "允许内网地址", "ok": not private, "detail": host + "（当前配置不允许内网）"})
    try:
        t = float(cfg.get("timeout") or DEFAULT_TIMEOUT)
        steps.append({"name": "超时合理", "ok": 1 <= t <= 600, "detail": "%ss" % t})
    except Exception:
        steps.append({"name": "超时合理", "ok": False, "detail": "不是数字：%r" % cfg.get("timeout")})
    return steps


def client_param(cfg: dict, timeout: float):
    """cfg → (Client 的第一个参数, 超时秒)。支持 stdio / http / inproc（后者仅测试用）。

    抽成公共函数：`mcp_probe` 要自己开一次连接、分阶段计时，不能复用 `_with_client` 的整体超时。
    """
    _Client, StdioServerParameters, _MCPServer = _sdk()
    transport = (cfg.get("transport") or "").strip().lower()
    if transport == "inproc":
        server = cfg.get("_server")
        if server is None:
            raise McpDriverError("inproc 来源必须带 _server（进程内 MCPServer 实例，仅测试用）")
        return server, timeout
    if transport == "stdio":
        cmd = (cfg.get("command") or "").strip()
        if not cmd:
            raise McpDriverError("stdio 来源缺少 command")
        steps = validate_cfg(cfg)
        bad = [s for s in steps if not s["ok"]]
        if bad:
            raise McpDriverError("配置不通过：%s — %s" % (bad[0]["name"], bad[0]["detail"]))
        env = cfg.get("env") or None
        return StdioServerParameters(command=cmd, args=[str(a) for a in (cfg.get("args") or [])],
                                     env={str(k): str(v) for k, v in env.items()} if env else None,
                                     cwd=cfg.get("cwd") or None), timeout
    if transport == "http":
        url = (cfg.get("url") or "").strip()
        if not (url.startswith("http://") or url.startswith("https://")):
            raise McpDriverError("http 来源需要 http(s):// 开头的 url")
        return url, timeout
    raise McpDriverError("这台来源的 transport=%r 不被驱动支持（stdio / http）" % (cfg.get("transport") or ""))


def _timeout(cfg: dict, override=None) -> float:
    """超时取秒数：**<=0 或非数字视为"未配置"→ 用默认值**（0 不该被理解成"立即超时"）。"""
    raw = override if override is not None else (cfg.get("timeout") or DEFAULT_TIMEOUT)
    try:
        t = float(raw)
    except Exception:
        t = DEFAULT_TIMEOUT
    if t <= 0:
        t = DEFAULT_TIMEOUT
    return max(1.0, min(t, 600.0))


# ---------------------------------------------------------------- 连接（进程内 / stdio / http）
async def _with_client(cfg: dict, timeout: float, fn):
    """连一次、跑 fn(client)，然后断开。**每次调用一条新连接**（无状态、不用看护长活进程）。"""
    server_param, _t = client_param(cfg, timeout)
    Client, _P, _S = _sdk()
    async with Client(server_param, read_timeout_seconds=timeout) as c:
        return await fn(c)


# ---------------------------------------------------------------- 对外：列工具 / 调工具 / 归一化
def _tool_view(t) -> dict:
    """SDK Tool → 我们的统一形状（字段用 snake_case，与既有能力表一致）。"""
    ann = getattr(t, "annotations", None)
    hint = None
    if ann is not None:
        hint = getattr(ann, "read_only_hint", None)
        if hint is None and isinstance(ann, dict):
            hint = ann.get("read_only_hint", ann.get("readOnlyHint"))
    return {
        "name": str(getattr(t, "name", "") or ""),
        "title": getattr(t, "title", None),
        "description": str(getattr(t, "description", "") or ""),
        "input_schema": getattr(t, "input_schema", None) or {"type": "object", "properties": {}},
        "read_only_hint": bool(hint) if hint is not None else None,   # None = 服务端没说
    }


def list_tools(cfg: dict, *, timeout=None) -> dict:
    """列工具（也当健康检查：握手成功 + 拿到工具表）。返回 ``{"ok","tools","ms","error"}``。"""
    t0 = time.time()
    to = _timeout(cfg, timeout)
    async def _fn(c):
        res = await c.list_tools()
        return [_tool_view(t) for t in (getattr(res, "tools", None) or [])]
    try:
        tools = run_sync(_with_client(cfg, to, _fn), timeout=to + 5)
        return {"ok": True, "tools": tools, "ms": int((time.time() - t0) * 1000), "error": ""}
    except McpDriverError as e:
        return {"ok": False, "tools": [], "ms": int((time.time() - t0) * 1000), "error": str(e)}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "tools": [], "ms": int((time.time() - t0) * 1000),
                "error": "%s: %s" % (type(e).__name__, str(e)[:200])}


def _result_to_payload(res) -> tuple[dict | None, str]:
    """SDK CallToolResult →（结构化 JSON 或 None, 兜底文本）。二者必有其一。"""
    structured = getattr(res, "structured_content", None)
    if structured is None:
        structured = getattr(res, "structuredContent", None)
    parts, texts = [], []
    for item in (getattr(res, "content", None) or []):
        d = item.model_dump(exclude_none=True) if hasattr(item, "model_dump") else dict(item)
        parts.append(d)
        if d.get("type") == "text" and d.get("text"):
            texts.append(str(d["text"]))
    if structured is not None:
        return structured, "\n".join(texts)
    if len(texts) == 1:
        try:                                     # 单个 text 块里常常装的就是 JSON
            return json.loads(texts[0]), texts[0]
        except Exception:
            return None, texts[0]
    if parts:
        return {"content": parts}, "\n".join(texts)
    return None, "（该工具没有返回内容）"


def call_tool(cfg: dict, tool: str, args: dict | None = None, *, timeout=None) -> dict:
    """调用一个 MCP 工具。返回形状与 `api_driver.call` 对齐：``{"ok","status","json","text","ms","error"}``。"""
    t0 = time.time()
    to = _timeout(cfg, timeout)
    name = str(tool or "").strip()
    if not name:
        return {"ok": False, "status": None, "json": None, "text": "", "ms": 0, "error": "工具名不能为空"}
    payload = dict(args or {})
    async def _fn(c):
        return await c.call_tool(name, payload)
    try:
        res = run_sync(_with_client(cfg, to, _fn), timeout=to + 5)
    except McpDriverError as e:
        return {"ok": False, "status": None, "json": None, "text": "", "ms": int((time.time() - t0) * 1000),
                "error": str(e)}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "status": None, "json": None, "text": "", "ms": int((time.time() - t0) * 1000),
                "error": "调用异常：%s: %s" % (type(e).__name__, str(e)[:200])}
    is_err = bool(getattr(res, "is_error", None) or getattr(res, "isError", False))
    body, text = _result_to_payload(res)
    if len(text) > MAX_RESULT_CHARS:
        text = text[:MAX_RESULT_CHARS]
    return {"ok": not is_err, "status": 200 if not is_err else None, "json": body,
            "text": text, "ms": int((time.time() - t0) * 1000),
            "error": "" if not is_err else (text or "工具返回 isError=true")}
