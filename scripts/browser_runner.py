#!/usr/bin/env python
"""browser-use 子进程跑手（v4.0 大点 05）。

**为什么要独立子进程**：实测 `uv pip install browser-use` 进主 venv 会让 uv 解析出 106 个包、
**替换 pydantic、并卸掉 14 个既有包**（见 `Verdict/05` 裁定 3 与其影响面）——依赖集与主项目锁定的栈不兼容。
因此：browser-use 装在**独立 venv** `browser_venv/` 里，本文件由该 venv 的 python 执行，
主项目只通过子进程 + 一行 JSON 与它对话。附带好处：异步边界干净（browser-use 只有 `await agent.run()`）、
超时/中断可以直接杀进程。

**适配的版本**：本机装到的是 PyPI 发布版 `browser-use 0.13.11`；仓库 main 上那份 `AGENTS.md` 描述的是
**未发布**的新一代 API，两者不一致 —— 一律以**实际装到的**为准：
  · `BrowserProfile` 承载 headless / channel / user_data_dir / accept_downloads / proxy / allowed_domains；
  · `Agent(task, llm, browser, tools, use_vision, max_actions_per_step, max_failures, …)`；
  · `max_steps` 是 **`agent.run(max_steps=…)` 的参数**（不是构造参数）；
  · `ProxySettings` 在 `browser_use.browser.profile`（顶层不导出）；
  · `Agent.run(on_step_start=…, on_step_end=…)` 提供**步级回调**（0.13.11 已有）；跨进程把它接上主项目的
    取消信号是后续项，当前以 max_steps + 内部超时兜底。

**调用契约**（由 `tools/browser_tool.py` 发起）：
  入参：**stdin** 一段 JSON（密钥走 stdin，不进机命令行）
        {"task": str, "urls": [str], "max_steps": int, "internal_timeout_s": int,
         "headless": bool, "allowed_domains": [str],
         "llm": {"model": str, "api_key": str, "base_url": str},   # 沿用主项目「定制助手」当前生效配置
         "vision": bool}                                           # 来自 llm_providers.supports_vision
  出参：**stdout 最后一行** 一段 JSON（其余日志走 stderr）
        {"ok": bool, "error": str, "urls_visited": [], "steps": int, "seconds": float,
         "cost_usd": float|None, "results": [{"url","title","content"}], "errors": [str], "success": bool|None}

**安全默认**（与主项目口径一致）：
  · 站内搜索动作 `search` 排除 → 搜索只走 Tavily（本大点的两段式设计）；
  · `write_file` / `read_file` / `replace_file` / `upload_file` 全部排除 → 本大点不开"Agent 写能力"（属 06）；
  · `accept_downloads=False` → 不静默落下载；
  · 只允许在给定域名内活动（`allowed_domains`，由调用方从目标 URL 推导）；
  · 匿名遥测默认关闭（`ANONYMIZED_TELEMETRY=false`）。
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import time

# 遥测必须在 import browser_use 之前关掉
os.environ.setdefault("ANONYMIZED_TELEMETRY", "false")

EXCLUDE_ACTIONS = ["search", "write_file", "read_file", "replace_file", "upload_file"]


def _log(msg: str) -> None:
    print("[browser_runner] %s" % msg, file=sys.stderr, flush=True)


def _domains_of(urls: list[str]) -> list[str]:
    """从 URL 推导域名白名单（`https://a.b/c` → `a.b`）。"""
    out = []
    for u in urls or []:
        try:
            from urllib.parse import urlparse
            h = urlparse(u).hostname
            if h and h not in out:
                out.append(h)
        except Exception:
            continue
    return out


def _profile_base() -> str:
    """本次运行用的临时 profile 根目录：优先落在 BROWSER_USE_CONFIG_DIR（D 盘）。"""
    base = os.getenv("BROWSER_USE_CONFIG_DIR") or tempfile.gettempdir()
    d = os.path.join(base, "profiles")
    try:
        os.makedirs(d, exist_ok=True)
    except Exception as e:                                     # noqa: BLE001
        _log("建 profile 目录失败，退回系统临时目录：%s" % e)
        d = tempfile.gettempdir()
    return d


def _cleanup_profile(req: dict) -> None:
    """删掉本次运行的临时 profile（best-effort：删不掉也不影响结果，最多留个目录）。"""
    d = req.get("_profile_dir")
    if not d:
        return
    try:
        import shutil
        shutil.rmtree(d, ignore_errors=True)
    except Exception:                                          # noqa: BLE001
        pass


def _run_once(req: dict) -> dict:
    """跑一次浏览器任务，返回契约里的 dict（**绝不抛异常**，错误也走返回值）。"""
    t0 = time.time()
    task = (req.get("task") or "").strip()
    urls = [u.strip() for u in (req.get("urls") or []) if (u or "").strip()]
    if not task:
        return {"ok": False, "error": "缺少 task（要在这页面拿什么，用自然语言写清）"}
    # browser-use 的 directly_open_url 默认为 True：把 URL 拼进任务串即"直接打开"
    if urls:
        task = "%s\n\n目标页面（逐个打开）：\n%s" % (task, "\n".join("- " + u for u in urls))

    llm_cfg = req.get("llm") or {}
    if not llm_cfg.get("model"):
        return {"ok": False, "error": "缺少 llm.model（应沿用「定制助手 · 模型选型」当前生效配置）"}

    try:
        from browser_use import Agent, Browser, BrowserProfile, ChatOpenAI, Tools
        from browser_use.browser.profile import ProxySettings   # 0.13.11：顶层不导出
    except Exception as e:                                     # noqa: BLE001
        return {"ok": False, "error": "browser-use 不可用（%s: %s）——请检查 browser_venv 是否装好" % (type(e).__name__, e)}

    try:
        llm_kw = {"model": llm_cfg["model"]}
        if llm_cfg.get("api_key"):
            llm_kw["api_key"] = llm_cfg["api_key"]
        if llm_cfg.get("base_url"):
            llm_kw["base_url"] = llm_cfg["base_url"]       # 官方：任意 OpenAI 兼容模型用自定义 URL
        # ★ 本机实测（2026-10-14）：浏览器侧每步提示词很大（含 DOM），慢模型会连续撞上 browser-use
        #   默认的 75 秒「LLM call timed out」，把步数白白耗光。故：
        #   · ChatOpenAI(timeout=…) 放宽单次调用上限（默认 120s）；
        #   · Agent(flash_mode=True, use_thinking=False) 砍掉长篇思考与冗余提示，显著提速。
        llm_kw["timeout"] = float(req.get("llm_timeout_s") or 120)
        # ★ 2026-10-14 真机实测（deepseek）：browser-use 会发结构化输出参数（`response_format`），
        #   deepseek 的 API 直接拒：`response_format type is unavailable now` → 整次浏览失败
        #   （真机实验里 browse_pages 因此连败 8 次）。故对不支持结构化输出的模型改用**兼容路径**：
        #   · dont_force_structured_output=True   —— 不向上游强推 response_format；
        #   · add_schema_to_system_prompt=True    —— 改为把 schema 写进系统提示，仍能拿到结构化结果。
        #   ★ 但真机对照（同站点同任务）显示兼容路径**会让格式纪律变松**：
        #     agnes-3.0-flash 在兼容模式下有 2 步把动作输出成大白话（AgentOutput Invalid JSON），
        #     而 GLM-5.3-Flash 走**原生**结构化输出时 0 报错。故做成可切换（默认兼容=最广适配），
        #     由调用方/实测决定该用哪条路 —— 见 `compat_json` 入参。
        if bool(req.get("compat_json", False)):           # 默认**原生**；只有上游拒绝时才由 run() 自动重试兼容路径
            llm_kw["dont_force_structured_output"] = True
            llm_kw["add_schema_to_system_prompt"] = True
        llm = ChatOpenAI(**llm_kw)

        # 独立临时 profile（不碰用户浏览器）；放在 BROWSER_USE_CONFIG_DIR 下（D 盘），跑完在 finally 里删掉。
        prof = tempfile.mkdtemp(prefix="run_", dir=_profile_base())
        req["_profile_dir"] = prof                          # 交给 finally 清理
        prof_kw = {
            "headless": bool(req.get("headless", True)),
            "channel": req.get("channel") or "chrome",     # 复用本机 Chrome，不下 Chromium
            "user_data_dir": prof,                     # 独立临时 profile（不碰用户浏览器）
            "accept_downloads": False,
            # ★ 本机实测必需（2026-10-14）：这里的 python 跑在受限令牌下（项目 user_data 的 ACL 里
            #   带 AppContainer 型 SID，全机只有项目目录可写），Chrome 在这种令牌下**无法创建自己的
            #   沙箱子进程** → 进程 0.2 秒即崩（returncode 0x80000003，无任何输出）。
            #   实测 `--no-sandbox` 后可正常起浏览器（CDP 就绪）；`--disable-gpu` 是因为本机 GPU 进程
            #   也常崩（早前日志有 `GPU process exited unexpectedly`）。
            #   代价：Chrome 自身沙箱被关掉。可接受的理由——本工具只打开 Agent 指定的页面，下载已关
            #   （accept_downloads=False），文件写/上传类动作已在 exclude_actions 里全部摘掉。
            "args": ["--no-sandbox", "--disable-gpu"],
        }
        doms = req.get("allowed_domains") or _domains_of(urls)
        if doms:
            prof_kw["allowed_domains"] = doms
        if req.get("proxy"):
            prof_kw["proxy"] = ProxySettings(server=req["proxy"])
        browser = Browser(browser_profile=BrowserProfile(**prof_kw))

        agent = Agent(
            task=task,
            llm=llm,
            browser=browser,
            tools=Tools(exclude_actions=list(EXCLUDE_ACTIONS)),
            use_vision=("auto" if req.get("vision", True) else False),
            flash_mode=True,                               # 砍掉长篇思考，按「打开一页取要点」的用途提速
            use_thinking=False,
            max_actions_per_step=3,                        # 取要点场景不宜一次铺开
            max_failures=3,
        )
        max_steps = int(req.get("max_steps") or 15)
        tmo = float(req.get("internal_timeout_s") or 0) or None

        async def _go():
            """跑到结束；**无论成败都收掉浏览器** —— 否则父进程超时被杀时 Chrome 会留成孤儿进程。"""
            try:
                if tmo:
                    return await asyncio.wait_for(agent.run(max_steps=max_steps), timeout=tmo)
                return await agent.run(max_steps=max_steps)
            finally:
                _cleanup_profile(req)                      # 删掉本次的临时 profile（不留垃圾）
                for m in ("kill", "stop", "close"):
                    fn = getattr(browser, m, None)
                    if callable(fn):
                        try:
                            got_ = fn()
                            if asyncio.iscoroutine(got_):
                                await got_
                        except Exception as e:                 # noqa: BLE001
                            _log("收浏览器时出错（忽略）：%s" % e)
                        break

        history = asyncio.run(_go())
    except asyncio.TimeoutError:
        return {"ok": False, "error": "浏览任务超时（内部 %s 秒上限）已中止并收掉浏览器" % req.get("internal_timeout_s"),
                "seconds": round(time.time() - t0, 1)}
    except Exception as e:                                     # noqa: BLE001
        return {"ok": False, "error": "浏览任务失败（%s: %s）" % (type(e).__name__, str(e)[:300]),
                "seconds": round(time.time() - t0, 1)}

    # ---- 整形输出（只回模型用得上的，不回原始 HTML）----
    results, seen = [], set()
    def _push(url, title, content):
        key = (url or "") + "|" + (title or "")
        if key in seen or not (content or "").strip():
            return
        seen.add(key)
        results.append({"url": url or "", "title": title or "", "content": str(content)[:4000]})

    try:
        for u in (history.urls() or []):
            pass                                                # 仅用于 urls_visited
        for item in (history.action_results() or []):
            _push(getattr(item, "url", "") or "", "", getattr(item, "extracted_content", "") or "")
        fin = ""
        try:
            fin = history.final_result() or ""
        except Exception:
            fin = ""
        if fin:
            _push((history.urls() or [""])[-1], "final_result", fin)
        if not results:
            for c in (history.extracted_content() or []):
                _push("", "", c)
    except Exception as e:                                     # noqa: BLE001
        _log("整形输出时出错（忽略）: %s" % e)

    try:
        cost = float(history.cost) if getattr(history, "cost", None) is not None else None
    except Exception:
        cost = None
    try:
        errs = [str(x) for x in (history.errors() or []) if x][-3:]
    except Exception:
        errs = []

    return {
        "ok": True,
        "urls_visited": [u for u in (history.urls() or [])][:20],
        "steps": int(history.number_of_steps() or 0),
        "seconds": round(history.total_duration_seconds() or (time.time() - t0), 1),
        "cost_usd": cost,
        "results": results[:8],
        "errors": errs,
        "success": bool(history.is_successful()) if history.is_done() else None,
    }


def run(req: dict) -> dict:
    """跑一次；**原生结构化输出优先**，仅当上游明确拒绝（`response_format` 不可用）时自动切兼容路径重试一次。

    为什么（2026-10-14 真机对照，同站点同任务）：
      · 原生路径（推 response_format）：GLM-5.3-Flash 0 报错、动作干净 → 首选；
      · 兼容路径（把 schema 写进系统提示）：agnes-3.0-flash 会丢步（动作输出成大白话）、
        GLM 也能吃，但格式纪律明显变松 → 只在必要时用；
      · deepseek 两种都不行（它回自己的 DSML 方言）→ 必须靠「浏览器侧模型」设置项另指定模型。
    调用方传了 `compat_json`（True/False）就尊重它，不再自动重试。
    """
    out = _run_once(req if req.get("compat_json") is not None else dict(req, compat_json=False))
    if (not out.get("ok")) and (req.get("compat_json") is None) \
            and "response_format" in str(out.get("error") or ""):
        _log("上游拒绝结构化输出（response_format 不可用）→ 改用兼容路径重试一次")
        out = _run_once(dict(req, compat_json=True))
    return out


def main() -> int:
    raw = sys.stdin.read() if not sys.stdin.isatty() else (sys.argv[1] if len(sys.argv) > 1 else "")
    try:
        req = json.loads(raw or "{}")
    except Exception as e:                                     # noqa: BLE001
        print(json.dumps({"ok": False, "error": "入参 JSON 解析失败：%s" % e}, ensure_ascii=False))
        return 2
    out = run(req)
    print(json.dumps(out, ensure_ascii=False))
    return 0 if out.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
