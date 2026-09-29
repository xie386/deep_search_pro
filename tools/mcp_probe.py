# -*- coding: utf-8 -*-
"""MCP 来源体检（v3.0 M2-3）：**七步**，形状与 API 来源的 `/test` 一致（`{"ok","steps":[...]}`），
另外把拿到的工具表一起返回，供"发现候选"直接复用（省掉第二次连接）。

七步（步骤名照参考项目的 MCPVerifier 对齐，实现全部走官方 SDK v2）：
  ① 配置解析         —— transport / 命令 / 白名单 / url / 超时
  ② 环境检测         —— stdio：命令真能找到 + cwd 存在；http：TCP 能连通
  ③ 协议握手         —— 真实建立连接并完成 initialize（**唯一能证明"这是个 MCP 服务"的一步**）
  ④ tools/list       —— 核心接口，拿到工具表（数量 / 名字 / 各自 input_schema）
  ⑤ 异常容错         —— 调一个**不存在的工具名**：要求"干净报错"，不允许卡死/把客户端带崩
  ⑥ 性能指标         —— 握手 / list / 容错各自的耗时
  ⑦ 汇总             —— 总数、工具数、只读工具数、耗时、启动命令全行（确认界面要展示给用户看）

★ 为什么第 ⑤ 步有价值：MCP 服务端实现质量参差，一个"未知工具就挂住连接"的服务接进池里，
  之后每次调用都会拖满超时，比直接拒绝糟糕得多 —— 这一步把它挡在门外。
"""
from __future__ import annotations

import os
import socket
import shutil
import time
from urllib.parse import urlparse

from tools._runtime import mcp_driver as drv

PROBE_MISSING_TOOL = "__probe_nonexistent_tool__"


def _which(cmd: str) -> str:
    """命令在哪：PATH 查找，或给定的绝对/相对路径确实是个文件。"""
    cmd = (cmd or "").strip()
    if not cmd:
        return ""
    p = shutil.which(cmd)
    if p:
        return p
    if os.path.isfile(cmd):
        return os.path.abspath(cmd)
    return ""


def _tcp_ok(url: str, timeout: float = 3.0) -> tuple[bool, str]:
    u = urlparse(url)
    host = u.hostname or ""
    port = u.port or (443 if u.scheme == "https" else 80)
    if not host:
        return False, "url 里没有主机名"
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True, "%s:%s 可连通" % (host, port)
    except Exception as e:  # noqa: BLE001
        return False, "%s:%s 连不上：%s" % (host, port, type(e).__name__)


def _stdio_stderr_hint(cfg: dict, wait: float = 6.0, limit: int = 500) -> str:
    """stdio 握手失败时的**取证**：把命令原样跑一次，抓子进程 stderr 的尾巴。

    为什么需要它：stdio 服务起不来时，SDK 只报 `ExceptionGroup: unhandled errors in a TaskGroup`
    —— 真正的原因（如 `ModuleNotFoundError: No module named 'mcp.server.fastmcp'`）全在**子进程的 stderr** 里。
    实测踩过：一只老服务因为它自己的 requirements 钉得太松，装成了 mcp 2.x 直接崩，而我们只看到 ExceptionGroup，
    查了半天。**只在握手失败时执行**（happy path 零成本），跑完即杀。
    """
    import subprocess
    cmd = [str(cfg.get("command") or "")] + [str(a) for a in (cfg.get("args") or [])]
    env = dict(os.environ)
    env.update({str(k): str(v) for k, v in (cfg.get("env") or {}).items()})
    try:
        p = subprocess.Popen(cmd, cwd=cfg.get("cwd") or None, env=env,
                             stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    except Exception as e:  # noqa: BLE001
        return "（连子进程都没起起来：%s: %s）" % (type(e).__name__, str(e)[:120])
    try:
        try:
            _out, err = p.communicate(timeout=wait)
        except subprocess.TimeoutExpired:
            p.kill()
            _out, err = p.communicate()
        txt = (err or b"").decode("utf-8", "replace").strip()
        if not txt:
            try:
                txt = (_out or b"").decode("utf-8", "replace").strip()
            except Exception:  # noqa: BLE001
                txt = ""
        txt = "\n".join(txt.splitlines()[-6:])[:limit]
        return txt
    finally:
        try:
            if p.poll() is None:
                p.kill()
        except Exception:  # noqa: BLE001
            pass


def probe(cfg: dict, *, timeout=None) -> dict:
    """跑一次七步体检。返回 ``{"ok", "steps", "tools", "ms"}``（steps 里每项含 name/ok/detail/ms）。"""
    cfg = dict(cfg or {})
    t_all = time.time()
    to = drv._timeout(cfg, timeout)
    steps: list[dict] = []
    tools: list[dict] = []

    def add(name, ok, detail="", ms=None):
        s = {"name": name, "ok": bool(ok), "detail": str(detail)}
        if ms is not None:
            s["ms"] = int(ms)
        steps.append(s)
        return s

    # ① 配置解析（把 validate_cfg 的每一条都摊平成一步里的明细；只要有不合法的就到此为止）
    t = time.time()
    cfg_steps = drv.validate_cfg(cfg)
    bad = [s for s in cfg_steps if not s["ok"]]
    add("① 配置解析", not bad,
        "；".join("%s=%s" % (s["name"], s["detail"]) for s in cfg_steps)[:300] if not bad
        else "不合法：%s（%s）" % (bad[0]["name"], bad[0]["detail"]), (time.time() - t) * 1000)
    if bad:
        return _finish(steps, tools, t_all)

    transport = (cfg.get("transport") or "").strip().lower()

    # ② 环境检测
    t = time.time()
    if transport == "stdio":
        where = _which(cfg.get("command") or "")
        cwd = cfg.get("cwd") or os.getcwd()
        ok = bool(where) and os.path.isdir(cwd)
        add("② 环境检测", ok, ("命令 → %s；工作目录 → %s" % (where or "找不到", cwd))[:240],
            (time.time() - t) * 1000)
    elif transport == "http":
        okc, detail = _tcp_ok(cfg.get("url") or "")
        add("② 环境检测", okc, detail, (time.time() - t) * 1000)
    else:
        add("② 环境检测", True, "进程内来源（测试用），无需环境检测", (time.time() - t) * 1000)
    if not steps[-1]["ok"]:
        return _finish(steps, tools, t_all)

    # ③④⑤ 一次连接里分阶段跑（握手 / list / 容错），各自计时
    phase: dict = {}

    async def body():
        Client, _P, _S = drv._sdk()
        param, _t = drv.client_param(cfg, to)
        t0 = time.time()
        async with Client(param, read_timeout_seconds=to) as c:
            phase["handshake_ms"] = (time.time() - t0) * 1000          # 进入 with 即握手完成
            t1 = time.time()
            res = await c.list_tools()
            phase["tools"] = [drv._tool_view(x) for x in (getattr(res, "tools", None) or [])]
            phase["list_ms"] = (time.time() - t1) * 1000
            t2 = time.time()
            try:
                r = await c.call_tool(PROBE_MISSING_TOOL, {})
                is_err = bool(getattr(r, "is_error", None) or getattr(r, "isError", False))
                phase["tolerant_ok"] = True
                phase["tolerant_detail"] = ("未知工具被按规范标记为错误返回" if is_err
                                            else "服务端对未知工具返回了**正常响应**（不规范，但不算挂死）")
            except Exception as e:  # noqa: BLE001  期望路径：服务端干净拒绝
                phase["tolerant_ok"] = True
                phase["tolerant_detail"] = "未知工具被干净拒绝：%s: %s" % (type(e).__name__, str(e)[:80])
            phase["tolerant_ms"] = (time.time() - t2) * 1000

    try:
        drv.run_sync(body(), timeout=to + 5)
    except drv.McpDriverError as e:
        add("③ 协议握手", False, str(e))
        return _finish(steps, tools, t_all)
    except Exception as e:  # noqa: BLE001
        detail = "握手失败：%s: %s" % (type(e).__name__, str(e)[:200])
        if transport == "stdio":
            # ★ 把子进程真正的报错抓出来 —— 否则这里只会是没用的 "ExceptionGroup"（真机踩过）
            hint = _stdio_stderr_hint(cfg)
            detail += ("；子进程输出：%s" % hint) if hint else "；子进程没有输出（命令可能不认 stdin/stdout 的 JSON-RPC）"
        add("③ 协议握手", False, detail)
        return _finish(steps, tools, t_all)

    add("③ 协议握手", True, "已连接并完成 initialize（%s）" % transport, phase.get("handshake_ms", 0))
    tools = phase.get("tools") or []
    names = [t["name"] for t in tools]
    add("④ tools/list", True, "共 %d 个工具：%s" % (len(names), ", ".join(names[:12]) + ("…" if len(names) > 12 else "")),
        phase.get("list_ms", 0))
    add("⑤ 异常容错", phase.get("tolerant_ok", False), phase.get("tolerant_detail", "未执行"),
        phase.get("tolerant_ms", 0))

    # ⑥ 性能指标
    hs, ls, tl = phase.get("handshake_ms", 0), phase.get("list_ms", 0), phase.get("tolerant_ms", 0)
    add("⑥ 性能指标", True, "握手 %dms · tools/list %dms · 容错调用 %dms（合计 %dms ≈ 每次连接开销）"
        % (hs, ls, tl, hs + ls + tl), hs + ls + tl)

    # ⑦ 汇总（把"这个来源要跑什么"完整呈现，确认界面直接展示）
    ro = sum(1 for t in tools if t.get("read_only_hint") is True)
    unknown = sum(1 for t in tools if t.get("read_only_hint") is None)
    if transport == "stdio":
        cmdline = " ".join([cfg.get("command") or ""] + [str(a) for a in (cfg.get("args") or [])])
    elif transport == "http":
        cmdline = cfg.get("url") or ""
    else:
        cmdline = "（进程内）"
    add("⑦ 汇总", True, "来源类型 %s · 工具 %d 个（明确只读 %d · 未标记 %d，未标记一律按「需人工确认」处理）· 启动：%s"
        % (transport, len(tools), ro, unknown, cmdline[:200]))
    return _finish(steps, tools, t_all)


def _finish(steps, tools, t_all) -> dict:
    return {"ok": all(s["ok"] for s in steps), "steps": steps, "tools": tools,
            "ms": int((time.time() - t_all) * 1000)}
