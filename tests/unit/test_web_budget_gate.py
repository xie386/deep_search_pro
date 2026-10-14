# -*- coding: utf-8 -*-
"""05 · 检索闸门（`web_workflows.budget_gate`）单元测试 —— 全离线。

**为什么要进代码**：提示词里写"整轮 ≤5 次检索"被真机实测无视 —— 审计显示一轮 2 分钟内发了约 30 次、
且含字面重复（同一查询词前后各发一次）。所以改成代码强制两道闸：
  ① 同一轮里**同一个查询词**（大小写/空格归一后相同）→ 直接挡回；
  ② 本轮累计检索数达上限（设置项 `WEB_SEARCH_MAX_PER_TURN`，默认 15）→ 挡回并提示改用已有结果。
挡回时**不得发起任何外部调用**（省钱、也不浪费时间）。
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))


def _set_turn(monkeypatch, aid=3, tid="t1", turn=1):
    from agent import usage_counter as uc
    monkeypatch.setattr(uc, "context", lambda: (aid, tid, turn))


def _clean():
    from tools import web_workflows as wf
    wf._TURN_SEARCH.clear()
    wf._TURN_QUERIES.clear()


def test_gate_dedupes_same_query_in_one_turn(monkeypatch):
    from tools import web_workflows as wf
    _set_turn(monkeypatch)
    _clean()
    assert wf.budget_gate("King Gnu") is None                      # 第一次放行
    refused = wf.budget_gate("  king   gnu ")                      # 大小写/空格归一 → 同一句
    assert refused and "已经查过" in refused


def test_gate_caps_per_turn_and_resets_next_turn(monkeypatch):
    from tools import web_workflows as wf
    from utils import settings
    _set_turn(monkeypatch, turn=7)
    _clean()
    monkeypatch.setattr(settings, "get_int",
                        lambda k, d=0: 2 if k == "WEB_SEARCH_MAX_PER_TURN" else d)
    assert wf.budget_gate("q1") is None
    assert wf.budget_gate("q2") is None
    refused = wf.budget_gate("q3")
    assert refused and "上限" in refused
    _set_turn(monkeypatch, turn=8)                                 # 换一轮 → 计数清零
    assert wf.budget_gate("q3") is None


def test_gate_scoped_per_thread(monkeypatch):
    """不同会话各自计数（同一账号的两个会话互不影响）。"""
    from tools import web_workflows as wf
    from utils import settings
    _clean()
    monkeypatch.setattr(settings, "get_int", lambda k, d=0: 1 if k == "WEB_SEARCH_MAX_PER_TURN" else d)
    _set_turn(monkeypatch, tid="A", turn=1)
    assert wf.budget_gate("x") is None
    assert wf.budget_gate("y") is not None                          # A 会话已到上限
    _set_turn(monkeypatch, tid="B", turn=1)
    assert wf.budget_gate("y") is None                              # B 会话不受影响


def test_zero_means_unlimited(monkeypatch):
    from tools import web_workflows as wf
    from utils import settings
    _set_turn(monkeypatch, turn=11)
    _clean()
    monkeypatch.setattr(settings, "get_int", lambda k, d=0: 0 if k == "WEB_SEARCH_MAX_PER_TURN" else d)
    for i in range(30):
        assert wf.budget_gate("q%d" % i) is None


def test_refused_call_never_touches_the_engine(monkeypatch):
    """被挡回时**不发起任何外部调用**，并返回可读的中文说明（供模型改口）。"""
    from tools import web_workflows as wf
    from utils import settings
    _set_turn(monkeypatch, turn=13)
    _clean()
    monkeypatch.setattr(settings, "get_int", lambda k, d=0: 1 if k == "WEB_SEARCH_MAX_PER_TURN" else d)
    called = []
    monkeypatch.setattr(wf, "internet_search", lambda **kw: (called.append(kw) or {"results": []}))
    assert "results" in wf.web_search_only.func(query="a")      # 成功路径回的是 Tavily 原始结构（无 ok 键）
    got = wf.web_search_only.func(query="b")
    assert got["ok"] is False and got.get("refused_by_budget") is True
    assert "上限" in got["error"] and len(called) == 1, "超限后绝不能再打引擎"


def test_search_then_browse_also_respects_the_gate(monkeypatch):
    from tools import web_workflows as wf
    from utils import settings
    _set_turn(monkeypatch, turn=14)
    _clean()
    monkeypatch.setattr(settings, "get_int", lambda k, d=0: 1 if k == "WEB_SEARCH_MAX_PER_TURN" else d)
    monkeypatch.setattr(wf, "internet_search", lambda **kw: {"results": [{"url": "https://www.a.com/x"}]})
    monkeypatch.setattr(wf, "browse_pages", lambda **kw: {"ok": True, "results": []})
    assert wf.search_then_browse.func(query="q1", task="t")["ok"] is True
    got = wf.search_then_browse.func(query="q2", task="t")
    assert got["ok"] is False and got.get("refused_by_budget") is True


def test_setting_registered():
    from utils import settings
    keys = {s["key"] for s in settings.SPECS}
    assert "WEB_SEARCH_MAX_PER_TURN" in keys
    spec = [s for s in settings.SPECS if s["key"] == "WEB_SEARCH_MAX_PER_TURN"][0]
    assert spec["group"] == "browser" and spec["kind"] == "number" and spec["default"] == "15"
