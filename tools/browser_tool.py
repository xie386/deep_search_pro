# -*- coding: utf-8 -*-
"""browser-use 工具 —— 网络搜索子 Agent 的第二个工具（v4.0 大点 05）。

**用法口径**（方案见 `docs/v4.0/v4.0规划文档.md` §05 ③ 段；裁定见 `docs/v4.0/Verdict/05-网络搜索子Agent升级.md`）：
  · 没有网址 → **只用** `internet_search`（Tavily，即现状）；
  · 有网址 → 先用 `internet_search` 快速定位与筛选 → 再用本工具打开关键页做**交互或深度抓取**；
  · **禁止**把网址当 query/topic 传给 `internet_search`（`topic` 只接受 news/finance/general）。

**为什么是子进程，而不是直接 import**（2026-10-14 实测，见 `Verdict/05` 裁定 3）：
  把 browser-use 装进主 venv 会让 uv 解析出 106 个包、**替换 pydantic 并卸掉 14 个既有包**
  （`pydantic`/`openai`/`requests`/`anyio`/`rich`/`typing-extensions`/…），项目当场起不来。故 browser-use 装在
  独立 `browser_venv/`，本工具用子进程调 `scripts/browser_runner.py`，JSON 进出。附带好处：异步边界干净
  （browser-use 只有 `await agent.run()`）、超时能连浏览器一起收掉、它崩了不拖垮主进程。

**模型**：沿用「定制助手 · 模型选型」当前生效那条（`llm_providers` 里 `is_active DESC, id DESC` 第一条）；
密钥走 `utils/secrets.get(secret_env)`（值只在 `.env`）；`use_vision` 跟随该配置的 `supports_vision`。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from dotenv import load_dotenv                              # noqa: E402
from langchain_core.tools import tool                       # noqa: E402

from api.monitor import monitor                             # noqa: E402
from utils import settings as _settings                     # noqa: E402

load_dotenv()

_ROOT = Path(__file__).resolve().parent.parent
_VENV_PY = _ROOT / "browser_venv" / "Scripts" / "python.exe"
_RUNNER = _ROOT / "scripts" / "browser_runner.py"


def _fail(msg: str) -> dict:
    """统一的失败返回 —— **不抛异常**：浏览失败不能中断整轮问答（与 tavily 的代理降级同款风格）。"""
    return {"ok": False, "error": msg, "results": []}


def _active_llm() -> dict:
    """浏览器侧模型 —— **与聊天侧同一处判定**：优先账号「定制助手 · 模型选型」里启用的那条
    （`api.customize.get_active_provider`：只认 `is_active=1`，返回**原样密钥**、老库明文作兜底）；
    **没有启用配置时退回 `.env` 默认模型**（`agent/llm.py` 同源：`LLM_MODEL_MAX` + `OPENAI_API_KEY`
    + `OPENAI_BASE_URL`；项目默认 agnes 本身多模态）。

    ★ 为什么必须对齐（2026-10-14 真机前发现）：本工具最初自己查 `llm_providers` 的
      `is_active DESC, id DESC` 第一条，会把**没启用**的自定义配置当成"生效配置" ——
      而聊天侧只有 `is_active=1` 才算，否则走 `.env` 默认模型。两边不一致就会出现
      「对话用 agnes、浏览器侧却偷偷用了另一个模型」。缺模型或缺密钥都返回 {}，由调用方说人话。
    """
    try:
        from api.context import get_owner_context
        from api.customize import get_active_provider
        aid = get_owner_context()
    except Exception as e:                                  # noqa: BLE001
        print(f"[browser] 读模型配置失败：{e}")
        return {}
    row = None
    if aid:
        try:
            row = get_active_provider(int(aid))
        except Exception as e:                              # noqa: BLE001
            print(f"[browser] 读生效模型配置失败：{e}")
    if row:
        model = (row.get("model_name") or "").strip()
        key = (row.get("api_key") or "").strip()            # mask=False → 已是真值（E5 的 secret_env 由它解析）
        if model and key:
            return {"model": model, "base_url": (row.get("base_url") or "").strip(),
                    "api_key": key, "vision": bool(row.get("supports_vision"))}
        print("[browser] 生效配置缺模型名或缺密钥 → 退回 .env 默认模型")
    model = (os.getenv("LLM_MODEL_MAX") or "").strip()
    key = (os.getenv("OPENAI_API_KEY") or "").strip()
    if model and key:
        return {"model": model, "base_url": (os.getenv("OPENAI_BASE_URL") or "").strip(),
                "api_key": key, "vision": True}             # 项目默认 agnes 本身多模态（与 api/server.py:711 同口径）
    return {}


def _proxy() -> str:
    """本机代理（交给浏览器用；runner 里已把 localhost 加进 bypass —— 项目既有教训：本机必须绕代理）。"""
    return (os.getenv("HTTPS_PROXY") or os.getenv("https_proxy")
            or os.getenv("HTTP_PROXY") or os.getenv("http_proxy") or "").strip()


@tool
def browse_pages(task: str, urls: list[str] | None = None, max_steps: int | None = None) -> dict:
    """进入**指定网址**的页面做交互或深度抓取，取回一手资料（价格 / 参数 / 公告 / 日期等）。

    ★ 什么时候用：提问里带了网址，或上一轮 `internet_search` 的结果里挑出了**关键页**需要打开细看时。
      **没有网址时不要用它** —— 那时只用 `internet_search`。
    ★ 不要用它替代检索：先用 `internet_search` 定位与筛选，再用本工具打开**少数**关键页深挖（成本更高）。

    :param task: 要在这些页面上拿什么，用自然语言写清（例：找出该商品当前售价与规格）。
    :param urls: 要打开的页面地址（**传了就直达该页**，不必让模型自己找入口）。建议 1~3 个。
    :param max_steps: 本次浏览最多多少步；不传则用设置里的默认值。
    :return: {"ok":…, "results":[{"url","title","content"}], "urls_visited":[…], "steps":…, "seconds":…, "cost_usd":…}
    """
    monitor.report_tool(tool_name="浏览器抓取工具",
                        args={"task": task, "urls": urls or [], "max_steps": max_steps})

    if not _settings.get_bool("BROWSER_USE_ENABLED", True):
        return _fail("浏览器抓取工具已在设置里关闭（BROWSER_USE_ENABLED=0）；请改用 internet_search。")
    if not _VENV_PY.exists():
        return _fail("浏览器运行环境未安装（缺 browser_venv/）：请在项目根执行 "
                     "`uv venv browser_venv --python 3.11` 后 `uv pip install --python "
                     "browser_venv/Scripts/python.exe browser-use`。")
    llm = _active_llm()
    if not llm:
        return _fail("没有可用的模型配置：浏览器侧沿用聊天侧同一处判定 —— 要么在「定制助手 · 模型选型」"
                     "里配置并**启用**一条模型，要么在 `.env` 里配好默认模型（LLM_MODEL_MAX / OPENAI_API_KEY）。")

    us = [u.strip() for u in (urls or []) if (u or "").strip()]
    cap = max(1, _settings.get_int("BROWSER_MAX_URLS", 3))
    if len(us) > cap:
        us = us[:cap]
        print(f"[browser] 目标页数超过上限，只取前 {cap} 个")

    timeout = max(30, _settings.get_int("BROWSER_TASK_TIMEOUT_S", 300))
    req = {
        "task": task,
        "urls": us,
        "max_steps": int(max_steps or _settings.get_int("BROWSER_MAX_STEPS", 8)),
        "headless": _settings.get_bool("BROWSER_HEADLESS", True),
        "internal_timeout_s": max(20, timeout - 15),        # 让 runner 先自己收尾（连浏览器一起杀），父进程超时只是兜底
        "llm": {"model": llm["model"], "api_key": llm["api_key"], "base_url": llm["base_url"]},
        "vision": llm["vision"],
        "proxy": _proxy(),
    }
    env = dict(os.environ)
    env["ANONYMIZED_TELEMETRY"] = "false"                    # 关匿名遥测（官方 AGENTS.md 明示可关）
    env.setdefault("PYTHONIOENCODING", "utf-8")
    # ★ 把 browser-use 的配置与浏览器 profile 目录挪到 D 盘（默认是 C:\Users\<用户>\.config\browseruse）：
    #   一是用户口径「数据落 D 盘、C 盘紧张」；二是实测首次运行在默认路径上建目录会撞 WinError 5（拒绝访问）。
    _cfg = _ROOT / "user_data" / "_runtime" / "browseruse"
    for _sub in ("profiles", "local", "tmp"):
        try:
            (_cfg / _sub).mkdir(parents=True, exist_ok=True)
        except Exception as e:                               # noqa: BLE001
            print(f"[browser] 建配置目录失败（继续尝试）：{e}")
    env["BROWSER_USE_CONFIG_DIR"] = str(_cfg)
    # ★ 本机实测（2026-10-14）：子进程与 Chrome 都运行在受限令牌下，**只有项目目录可写** ——
    #   Chrome 默认要往 %LOCALAPPDATA%\Google\Chrome\User Data\Crashpad 写，被拒会静默崩溃。
    #   故把 LOCALAPPDATA/TEMP/TMP 一并指到项目内（顺带不占 C 盘）。
    env["LOCALAPPDATA"] = str(_cfg / "local")
    env["TEMP"] = env["TMP"] = str(_cfg / "tmp")
    # ★ 本机 CDP / localhost **绝不能走系统代理**（项目既有教训：本机 HTTP/WS 走代理会拿不到有效响应）
    #   —— 实测症状：Chrome 起来了，但 browser-use 连 CDP 报 `did not receive a valid HTTP response`。
    env["NO_PROXY"] = env["no_proxy"] = "127.0.0.1,localhost,::1"
    try:
        p = subprocess.run([str(_VENV_PY), str(_RUNNER)],
                           input=json.dumps(req, ensure_ascii=False),
                           capture_output=True, text=True, encoding="utf-8", errors="replace",
                           timeout=timeout, cwd=str(_ROOT), env=env)
    except subprocess.TimeoutExpired:
        return _fail(f"浏览任务超时（>{timeout} 秒）已中止；可改用 internet_search，"
                     f"或在设置里调大 BROWSER_TASK_TIMEOUT_S。")
    except Exception as e:                                   # noqa: BLE001
        return _fail(f"浏览器子进程启动失败（{type(e).__name__}: {e}）")

    if p.stderr and p.stderr.strip():
        print("[browser] runner 日志：%s" % p.stderr.strip()[-800:])
    line = ""
    for ln in (p.stdout or "").splitlines()[::-1]:            # 契约：结果在最后一行 JSON
        if ln.strip().startswith("{"):
            line = ln.strip()
            break
    if not line:
        tail = ((p.stderr or "").strip() or (p.stdout or "").strip())[-400:]
        return _fail("浏览器子进程没有返回结果；诊断：%s" % (tail or "（无输出）"))
    try:
        got = json.loads(line)
    except Exception as e:                                   # noqa: BLE001
        return _fail(f"浏览器子进程返回无法解析（{e}）；原始输出：{line[:300]}")
    if not got.get("ok"):
        return _fail(got.get("error") or "浏览任务失败")
    if not got.get("results"):
        got["note"] = ("打开了页面但没有抽到可引用内容（页面可能是 JS 动态渲染 / 需要登录）；"
                       "可改用 internet_search 或换页面。")
    return got
