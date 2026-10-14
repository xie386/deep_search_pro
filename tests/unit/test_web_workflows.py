# -*- coding: utf-8 -*-
"""05 · 三个取法工作流工具（`tools/web_workflows.py`）单元测试 —— **全离线**。

钉住三件事（对应 2026-10-14 用户定稿的裁定 6/7）：
  1. **结构性约束**：取法写在**签名**里 —— `web_search_only` 没有 urls、`browse_only` 没有查询词，
     模型想越界也无从下手（这正是本方案相对"提示词自觉"的核心收益）；
  2. **情况③的挑站点规则**（纯函数 `pick_sites`）：按相关性顺序、去重到不同域名、跳过聚合/问答/电商站、受 max_sites 限制；
  3. **记账不被工作流遮蔽**：底层 Tavily / 浏览器各**显式**记一条「外部检索」账（M4 口径：要能回答"为什么烧了额度"）。
"""
import inspect
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))


# ---------------------------------------------------------------- ① 结构约束
def test_signatures_enforce_the_mode():
    from tools.web_workflows import browse_only, search_then_browse, web_search_only
    p1 = set(inspect.signature(web_search_only.func).parameters)
    p2 = set(inspect.signature(browse_only.func).parameters)
    p3 = set(inspect.signature(search_then_browse.func).parameters)
    assert "query" in p1 and "urls" not in p1, "① 只全网检索：不该有进站入口"
    assert "urls" in p2 and "query" not in p2, "② 只进站：不该有检索入口（结构性禁止）"
    assert {"query", "task"} <= p3 and "max_sites" in p3, "③ 需要检索词 + 进站任务 + 站点数上限"


def test_tool_names_are_stable():
    """工作流名会被主 Agent 在任务里点名、也进了用量分类表，不能随手改名。"""
    from tools.web_workflows import browse_only, search_then_browse, web_search_only
    assert [t.name for t in (web_search_only, browse_only, search_then_browse)] == \
        ["web_search_only", "browse_only", "search_then_browse"]
    from agent.usage_counter import classify, KIND_INTERNAL
    for n in ("web_search_only", "browse_only", "search_then_browse"):
        assert classify(n)[0] == KIND_INTERNAL, "工作流这层必须记成内部工具，否则额度被双记"


# ---------------------------------------------------------------- ② 挑站点规则
def test_pick_sites_rules():
    from tools.web_workflows import pick_sites
    res = {"results": [
        {"url": "https://baike.baidu.com/item/x"},      # 聚合/百科 → 跳过
        {"url": "https://www.a.com/p1"},                # 取
        {"url": "https://www.a.com/p2"},                # 同域名 → 去重
        {"url": "https://www.b.com/x"},                 # 取
        {"url": "https://www.c.com/x"},                 # 超出 max_sites 时不取
        {"url": ""},                                    # 脏数据 → 跳过
        {"url": "not-a-url"},
    ]}
    assert pick_sites(res, 2) == ["https://www.a.com/p1", "https://www.b.com/x"]
    assert pick_sites(res, 5) == ["https://www.a.com/p1", "https://www.b.com/x", "https://www.c.com/x"]
    assert pick_sites({"results": []}, 2) == []
    assert pick_sites(None, 2) == []


def test_pick_sites_skips_ecommerce_and_qa():
    from tools.web_workflows import pick_sites
    res = {"results": [{"url": "https://item.jd.com/1.html"}, {"url": "https://www.zhihu.com/q/1"},
                       {"url": "https://shop.example.com/goods/9"}]}
    assert pick_sites(res, 3) == ["https://shop.example.com/goods/9"]


# ---------------------------------------------------------------- ③ 三个工作流
def _capture(monkeypatch, search=None, browse=None):
    """替掉底层两个 util，并捕获显式记账。"""
    from tools import web_workflows as wf
    from agent import usage_counter as uc
    seen = {"search": [], "browse": [], "records": []}
    if search is not None:
        monkeypatch.setattr(wf, "internet_search",
                            lambda **kw: (seen["search"].append(kw) or search))
    if browse is not None:
        monkeypatch.setattr(wf, "browse_pages",
                            lambda **kw: (seen["browse"].append(kw) or browse))
    monkeypatch.setattr(uc, "record", lambda *a, **kw: seen["records"].append(a))
    return wf, uc, seen


def test_web_search_only_never_browses(monkeypatch):
    wf, _, seen = _capture(monkeypatch, search={"results": [{"url": "https://a.b/"}]},
                           browse={"ok": True})
    got = wf.web_search_only.func(query="某品牌 价格")
    assert got["results"] and seen["browse"] == [], "① 不该碰浏览器"
    assert [r[2] for r in seen["records"]] == ["retrieval_external"] and seen["records"][0][3] == "internet_search"


def test_browse_only_passes_urls_untouched_and_reports_degrade(monkeypatch):
    wf, _, seen = _capture(monkeypatch, browse={"ok": False, "error": "该网址抓不到", "results": []})
    got = wf.browse_only.func(urls=["https://a.b/x?y=1"], task="把价格照抄出来")
    assert got["ok"] is False and "抓不到" in got["error"]
    assert seen["browse"][0]["urls"] == ["https://a.b/x?y=1"], "网址必须原样传，不许改写成查询词"
    assert seen["search"] == [], "② 不该检索"
    assert seen["records"][0][3] == "browse_pages" and seen["records"][0][2] == "retrieval_external"


def test_search_then_browse_flow_and_accounting(monkeypatch):
    wf, _, seen = _capture(monkeypatch,
                           search={"results": [{"url": "https://www.a.com/p"}, {"url": "https://www.b.com/q"}]},
                           browse={"ok": True, "results": [{"url": "https://www.a.com/p", "content": "99 元"}]})
    got = wf.search_then_browse.func(query="某品牌 官网", task="取价格", max_sites=1)
    assert got["ok"] is True and got["sites"] == ["https://www.a.com/p"]
    assert seen["browse"][0]["urls"] == ["https://www.a.com/p"] and seen["browse"][0]["task"] == "取价格"
    assert len(seen["records"]) == 2, "Tavily 与浏览器各记一笔，否则额度账被工作流这层遮蔽"
    assert {r[2] for r in seen["records"]} == {"retrieval_external"}
    assert got["search"] and got["browsed"], "检索结果与进站结果要并列返回，便于逐条标注来源"


def test_search_then_browse_reports_when_nothing_pickable(monkeypatch):
    wf, _, seen = _capture(monkeypatch, search={"results": [{"url": "https://baike.baidu.com/x"}]},
                           browse={"ok": True})
    got = wf.search_then_browse.func(query="q", task="t")
    assert got["ok"] is False and "挑不出" in got["error"]
    assert seen["browse"] == [], "挑不出站点就不该硬进站"


def test_search_failure_degrades_with_chinese(monkeypatch):
    from tools import web_workflows as wf
    from agent import usage_counter as uc
    seen = []

    def boom(**kw):
        raise RuntimeError("proxy boom")

    monkeypatch.setattr(wf, "internet_search", boom)
    monkeypatch.setattr(uc, "record", lambda *a, **kw: seen.append((a, kw)))
    got = wf.web_search_only.func(query="x")
    assert got["ok"] is False and "全网检索失败" in got["error"]
    assert seen and seen[0][1].get("ok") is False, "失败也要记一笔（额度已经花了，不计就是自欺）"
    assert seen[0][0][2] == "retrieval_external"
