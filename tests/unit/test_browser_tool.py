# -*- coding: utf-8 -*-
"""05 · 浏览器抓取工具（`browse_pages`）单元测试 —— **全离线**，不启浏览器、不花钱。

覆盖（对应 `docs/v4.0/v4.0规划文档.md` §05 ③ 段 H 的判据 1/3/4 与 G 的接入点）：
  1. 装配：默认带两个工具（internet_search + browse_pages）；`BROWSER_USE_ENABLED=0` 时**只剩 Tavily**
     （与升级前逐字等价，可安全回滚）；
  2. 开关关掉时**调用也不碰浏览器**，直接回中文说明；
  3. 缺 `browser_venv/` 时给可照做的中文指引；没有可用模型配置时不发起浏览；
  4. 子进程契约：解析 stdout 末行 JSON；失败/空结果/超时各有降级文案（**不抛异常**）；
  5. 上限生效：目标页数按 `BROWSER_MAX_URLS` 截断；
  6. 用量分类：`browse_pages` → 外部检索（界面能把「进网址抓取」与 Tavily 分开看）；
  7. 设置登记：5 项都在 SPECS 里、分组标签存在（否则设置页会显示成裸 key）；
  8. runner 侧（`scripts/browser_runner.py`）：域名推导 + 缺 task 的入参校验（离线可测，不需要 browser-use）。
"""
import importlib
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))


# ---------------------------------------------------------------- 装配与开关
def test_subagent_has_both_tools_by_default():
    """默认（开关=1）子 Agent 挂两个工具：检索走 Tavily、进网址走浏览器。"""
    from agent.subagents.network_search_agent import network_search_agent
    names = [t.name for t in network_search_agent["tools"]]
    assert names == ["internet_search", "browse_pages"], names


def test_switch_off_removes_browser_tool(monkeypatch):
    """关掉总开关后子 Agent **只剩 internet_search**（装配期生效 → 行为与升级前逐字等价）。"""
    from utils import settings
    import agent.subagents.network_search_agent as mod
    monkeypatch.setattr(settings, "get_bool",
                        lambda k, d=None: False if k == "BROWSER_USE_ENABLED" else bool(d))
    try:
        m2 = importlib.reload(mod)
        assert [t.name for t in m2.network_search_agent["tools"]] == ["internet_search"]
    finally:
        monkeypatch.undo()
        importlib.reload(mod)                     # 复原，别把开关状态漏给别的用例
    assert [t.name for t in mod.network_search_agent["tools"]] == ["internet_search", "browse_pages"]


def test_call_when_switch_off_talks_chinese(monkeypatch):
    """开关关掉时调用工具：直接回中文说明，**绝不碰浏览器**。"""
    from tools import browser_tool
    from utils import settings
    monkeypatch.setattr(settings, "get_bool",
                        lambda k, d=None: False if k == "BROWSER_USE_ENABLED" else bool(d))
    got = browser_tool.browse_pages.invoke({"task": "看下价格", "urls": ["https://a.b/"]})
    assert got["ok"] is False and "关闭" in got["error"]


# ---------------------------------------------------------------- 前置条件的人话
def test_missing_venv_gives_actionable_hint(monkeypatch, tmp_path):
    from tools import browser_tool
    from utils import settings
    monkeypatch.setattr(settings, "get_bool", lambda k, d=None: True)
    monkeypatch.setattr(browser_tool, "_VENV_PY", tmp_path / "nope" / "python.exe")
    got = browser_tool.browse_pages.invoke({"task": "看下价格"})
    assert got["ok"] is False and "browser_venv" in got["error"] and "uv pip install" in got["error"]


def test_no_model_config_talks_chinese(monkeypatch, tmp_path):
    from tools import browser_tool
    from utils import settings
    monkeypatch.setattr(settings, "get_bool", lambda k, d=None: True)
    monkeypatch.setattr(browser_tool, "_VENV_PY", Path(sys.executable))   # 存在即可
    monkeypatch.setattr(browser_tool, "_active_llm", lambda: {})
    got = browser_tool.browse_pages.invoke({"task": "看下价格"})
    assert got["ok"] is False and "模型" in got["error"]


# ---------------------------------------------------------------- 子进程契约
def _fake_run(monkeypatch, browser_tool, payload, recorder=None, raise_timeout=False):
    """替掉 subprocess.run：把入参 JSON 记下来，回放给定的 payload。"""
    class _P:
        def __init__(self, stdout="", stderr=""):
            self.stdout, self.stderr, self.returncode = stdout, stderr, 0

    def fake(cmd, **kw):
        if recorder is not None:
            recorder.append(kw.get("input") or (cmd and json.loads(kw["input"] if isinstance(kw.get("input"), str) else "{}")))
        if raise_timeout:
            raise subprocess.TimeoutExpired(cmd, 300)
        return _P(stdout=(payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)) + "\n")

    monkeypatch.setattr(browser_tool.subprocess, "run", fake)


def _ready(monkeypatch, browser_tool, tmp_path):
    from utils import settings
    monkeypatch.setattr(settings, "get_bool", lambda k, d=None: True)
    monkeypatch.setattr(settings, "get_int", lambda k, d=0: d)
    monkeypatch.setattr(browser_tool, "_VENV_PY", Path(sys.executable))
    monkeypatch.setattr(browser_tool, "_active_llm",
                        lambda: {"model": "m", "base_url": "https://x/v1", "api_key": "k", "vision": False})


def test_parses_last_json_line(monkeypatch, tmp_path):
    from tools import browser_tool
    _ready(monkeypatch, browser_tool, tmp_path)
    _fake_run(monkeypatch, browser_tool,
              'INFO 一些日志\n{"ok": true, "results": [{"url": "https://a.b/", "content": "售价 99"}], "steps": 2}')
    got = browser_tool.browse_pages.invoke({"task": "看下价格", "urls": ["https://a.b/"]})
    assert got["ok"] is True and got["results"][0]["content"] == "售价 99"


def test_runner_failure_passes_through(monkeypatch, tmp_path):
    from tools import browser_tool
    _ready(monkeypatch, browser_tool, tmp_path)
    _fake_run(monkeypatch, browser_tool, {"ok": False, "error": "站点打不开"})
    got = browser_tool.browse_pages.invoke({"task": "看下价格"})
    assert got["ok"] is False and got["error"] == "站点打不开"


def test_empty_results_adds_note(monkeypatch, tmp_path):
    from tools import browser_tool
    _ready(monkeypatch, browser_tool, tmp_path)
    _fake_run(monkeypatch, browser_tool, {"ok": True, "results": []})
    got = browser_tool.browse_pages.invoke({"task": "看下价格"})
    assert got["ok"] is True and "note" in got


def test_timeout_degrades_not_raises(monkeypatch, tmp_path):
    from tools import browser_tool
    _ready(monkeypatch, browser_tool, tmp_path)
    _fake_run(monkeypatch, browser_tool, {}, raise_timeout=True)
    got = browser_tool.browse_pages.invoke({"task": "看下价格"})
    assert got["ok"] is False and "超时" in got["error"]


def test_url_cap_applied(monkeypatch, tmp_path):
    from tools import browser_tool
    _ready(monkeypatch, browser_tool, tmp_path)
    seen = []

    class _P:
        stdout, stderr, returncode = '{"ok": true, "results": [{"url": "u", "content": "c"}]}', "", 0

    def fake(cmd, **kw):
        seen.append(json.loads(kw["input"]))
        return _P()

    monkeypatch.setattr(browser_tool.subprocess, "run", fake)
    from utils import settings
    monkeypatch.setattr(settings, "get_int", lambda k, d=0: 1 if k == "BROWSER_MAX_URLS" else d)
    browser_tool.browse_pages.invoke({"task": "t", "urls": ["https://a/1", "https://a/2", "https://a/3"]})
    assert len(seen[0]["urls"]) == 1, seen[0]["urls"]
    assert seen[0]["llm"]["model"] == "m" and seen[0]["vision"] is False


# ---------------------------------------------------------------- 分类 / 设置 / runner
def test_usage_classified_as_external_retrieval():
    from agent.usage_counter import classify, KIND_RETRIEVAL_EXTERNAL
    assert classify("browse_pages") == (KIND_RETRIEVAL_EXTERNAL, "browse_pages")


def test_settings_registered_with_group_label():
    from utils import settings
    keys = {s["key"] for s in settings.SPECS}
    for k in ("BROWSER_USE_ENABLED", "BROWSER_MAX_STEPS", "BROWSER_TASK_TIMEOUT_S",
              "BROWSER_MAX_URLS", "BROWSER_HEADLESS"):
        assert k in keys, k
    assert "browser" in settings.GROUP_LABELS, "分组没有标签 → 设置页会显示成裸 key"
    enabled = [s for s in settings.SPECS if s["key"] == "BROWSER_USE_ENABLED"][0]
    assert enabled.get("restart") is True, "子 Agent 工具列表是装配期固定的，必须标 restart"


def _load_runner():
    spec = importlib.util.spec_from_file_location("browser_runner", ROOT / "scripts" / "browser_runner.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_runner_helpers_offline():
    rr = _load_runner()
    assert rr._domains_of(["https://a.b/c?d=1", "https://a.b/x", "http://e.f/"]) == ["a.b", "e.f"]
    assert rr._domains_of([]) == [] and rr._domains_of(["乱写的"]) == []
    assert rr.run({})["ok"] is False and "task" in rr.run({})["error"]
    assert rr.run({"task": "x"})["ok"] is False and "model" in rr.run({"task": "x"})["error"]
    assert "search" in rr.EXCLUDE_ACTIONS and "write_file" in rr.EXCLUDE_ACTIONS   # 搜索只走 Tavily、不开写能力

# ---------------------------------------------------------------- 浏览器侧模型（裁定 5）
def test_browser_model_ref_setting_registered():
    """「浏览器侧模型」设置项必须登记在浏览器组里（留空=跟随聊天侧）。"""
    from utils import settings
    spec = [s for s in settings.SPECS if s["key"] == "BROWSER_MODEL_REF"]
    assert spec, "BROWSER_MODEL_REF 没登记"
    assert spec[0]["group"] == "browser" and spec[0]["kind"] == "text"
    assert spec[0].get("default", "") == ""


def _patch_model_sources(monkeypatch, ref_value, ref_row, active_row):
    from tools import browser_tool as bt
    from api import context as ctxmod
    from api import customize as cz
    from utils import settings
    monkeypatch.setattr(ctxmod, "get_owner_context", lambda: 4)
    monkeypatch.setattr(settings, "get", lambda k, d=None: ref_value if k == "BROWSER_MODEL_REF" else d)
    monkeypatch.setattr(cz, "get_provider_by_ref", lambda aid, ref: ref_row)
    monkeypatch.setattr(cz, "get_active_provider", lambda aid: active_row)
    return bt


def test_browser_model_ref_wins(monkeypatch):
    """填了引用 → 用那一条（哪怕账号另有生效配置）。"""
    ref = {"model_name": "zai-org/GLM-5.3-Flash", "base_url": "https://api.siliconflow.cn/v1",
           "api_key": "k1", "supports_vision": False}
    act = {"model_name": "deepseek-flash", "base_url": "https://api.deepseek.com",
           "api_key": "k2", "supports_vision": False}
    bt = _patch_model_sources(monkeypatch, "GLM-5.3-Flash", ref, act)
    got = bt._active_llm()
    assert got["model"] == "zai-org/GLM-5.3-Flash" and got["api_key"] == "k1"


def test_browser_model_ref_empty_follows_chat_side(monkeypatch):
    """留空 → 跟随聊天侧（账号生效配置）。"""
    act = {"model_name": "deepseek-flash", "base_url": "https://api.deepseek.com",
           "api_key": "k2", "supports_vision": False}
    bt = _patch_model_sources(monkeypatch, "", None, act)
    assert bt._active_llm()["model"] == "deepseek-flash"


def test_browser_model_ref_miss_falls_back(monkeypatch):
    """填了但没命中（None）→ 不让整轮失败，回退到生效配置。"""
    act = {"model_name": "agnes-3.0-flash", "base_url": "https://x/v1", "api_key": "k", "supports_vision": True}
    bt = _patch_model_sources(monkeypatch, "不存在的名字", None, act)
    got = bt._active_llm()
    assert got["model"] == "agnes-3.0-flash" and got["vision"] is True


def test_runner_native_first_then_auto_compat(monkeypatch):
    """原生优先；只有上游拒绝结构化输出时才自动切兼容重试一次。"""
    rr = _load_runner()
    seen = []

    def fake(req):
        seen.append(bool(req.get("compat_json")))
        if len(seen) == 1:
            return {"ok": False, "error": "browser-use 不可用（RuntimeError: response_format type is unavailable now）"}
        return {"ok": True, "results": [{"url": "u", "content": "c"}]}

    monkeypatch.setattr(rr, "_run_once", fake)
    out = rr.run({"task": "x", "llm": {"model": "m"}})
    assert seen == [False, True] and out["ok"] is True


def test_runner_explicit_flag_disables_auto_retry(monkeypatch):
    """调用方显式指定 compat_json 时不再自动重试（测试与排查要靠它固定路径）。"""
    rr = _load_runner()
    seen = []

    def fake(req):
        seen.append(req.get("compat_json"))
        return {"ok": False, "error": "RuntimeError: response_format type is unavailable now"}

    monkeypatch.setattr(rr, "_run_once", fake)
    rr.run({"task": "x", "compat_json": True})
    rr.run({"task": "x", "compat_json": False})
    assert seen == [True, False]


def test_runner_other_errors_do_not_retry(monkeypatch):
    """与结构化输出无关的失败**不**重试（省时间、也避免掩盖真因）。"""
    rr = _load_runner()
    seen = []

    def fake(req):
        seen.append(1)
        return {"ok": False, "error": "站点打不开"}

    monkeypatch.setattr(rr, "_run_once", fake)
    assert rr.run({"task": "x"})["ok"] is False and len(seen) == 1
