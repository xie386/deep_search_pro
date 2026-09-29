# -*- coding: utf-8 -*-
"""M4-2 用例：用量计数器中间件（离线，临时账号；不调模型、不联网）。

判据来源：M4 方案 §六 M4-2（计数正确、**异常/超时也计**、**不依赖 `report_tool` 的反证**、
kind 映射、未设置上下文时不炸 ≥12 项）+ §5.1 口径。

运行：.venv/Scripts/python.exe -m pytest tests/test_usage_counter.py -q
"""
import asyncio
import os
import sys
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from agent import usage_counter as uc                                     # noqa: E402
from tools.schema_personal import ensure_tables, get_personal_conn, purge_account   # noqa: E402

ensure_tables()


def mk_account():
    conn = get_personal_conn()
    try:
        uname = "m4c_%d" % (int(time.time() * 1000) % 1000000)
        aid = int(conn.execute("INSERT INTO accounts (username, password_hash, role) VALUES (?,?,?)",
                               (uname, "x", "user")).lastrowid)
        conn.commit()
    finally:
        conn.close()
    return aid


def rows(aid):
    conn = get_personal_conn()
    try:
        return [dict(zip(("kind", "tool_name", "source", "ok", "latency_ms"),
                         r)) for r in conn.execute(
            "SELECT kind, tool_name, source, ok, latency_ms FROM usage_events WHERE account_id=? ORDER BY id",
            (aid,))]
    finally:
        conn.close()


class Req:
    """极简 ToolCallRequest 替身（只需要 `.tool_call`）。"""

    def __init__(self, name, args=None, tid="x"):
        self.tool_call = {"name": name, "args": args or {}, "id": tid}


@pytest.fixture()
def acct():
    aid = mk_account()
    uc.set_context_reader(lambda: (aid, "th-1"))
    yield aid
    uc.set_context_reader(None)
    purge_account(aid)


# ---------------------------------------------------------------- ① kind 映射
@pytest.mark.parametrize("name,args,want", [
    # ★ 真机抽样（2026-09-28）发现的缺口：真实工具名是 internet_search —— 缺了它，
    #   唯一花额度的那一类就一条都记不上（这正是 M4 存在的意义）。别再删这条用例。
    ("internet_search", {}, uc.KIND_RETRIEVAL_EXTERNAL),
    ("tavily_search", {}, uc.KIND_RETRIEVAL_EXTERNAL),
    ("web_search", {}, uc.KIND_RETRIEVAL_EXTERNAL),
    ("run_shell_command", {}, uc.KIND_SHELL),
    ("query_kb", {}, uc.KIND_INTERNAL),
    ("update_user_profile", {}, uc.KIND_INTERNAL),
    ("invoke_tool", {"name": "api:steam#searchGame"}, uc.KIND_API),
    ("invoke_tool", {"name": "mcp:pdd/search_products"}, uc.KIND_MCP),
    ("invoke_tool", {"name": "weread"}, uc.KIND_INTERNAL),
    ("完全没见过的工具", {}, uc.KIND_INTERNAL),
])
def test_classify(name, args, want):
    assert uc.classify(name, args)[0] == want


def test_source_is_specific_ref():
    assert uc.classify("invoke_tool", {"name": "api:steam#gameDetail"})[1] == "api:steam#gameDetail"


def test_only_external_retrieval_costs_quota():
    assert uc.COST_KINDS == (uc.KIND_RETRIEVAL_EXTERNAL,)
    assert set(uc.KIND_LABELS) == set(uc.KINDS)


def test_extra_kinds_are_extensible():
    uc.EXTRA_TOOL_KINDS["my_custom_search"] = uc.KIND_RETRIEVAL_EXTERNAL
    try:
        assert uc.classify("my_custom_search")[0] == uc.KIND_RETRIEVAL_EXTERNAL
    finally:
        uc.EXTRA_TOOL_KINDS.pop("my_custom_search", None)


# ---------------------------------------------------------------- ② 计数（同步/异步）
def test_sync_counts_one_row(acct):
    mw = uc.UsageCounterMiddleware()
    out = mw.wrap_tool_call(Req("tavily_search"), lambda r: "结果")
    assert out == "结果"
    got = rows(acct)
    assert len(got) == 1 and got[0]["kind"] == uc.KIND_RETRIEVAL_EXTERNAL
    assert got[0]["ok"] == 1 and got[0]["tool_name"] == "tavily_search"
    assert got[0]["latency_ms"] >= 0


def test_async_counts_one_row(acct):
    mw = uc.UsageCounterMiddleware()

    async def h(r):
        return "ok"

    out = asyncio.run(mw.awrap_tool_call(Req("query_kb"), h))
    assert out == "ok"
    got = rows(acct)
    assert len(got) == 1 and got[0]["kind"] == uc.KIND_INTERNAL


def test_invoke_tool_records_capability_ref(acct):
    mw = uc.UsageCounterMiddleware()
    mw.wrap_tool_call(Req("invoke_tool", {"name": "mcp:city_lookup/search_city"}), lambda r: "ok")
    got = rows(acct)
    assert got[0]["kind"] == uc.KIND_MCP and got[0]["source"] == "mcp:city_lookup/search_city"


# ---------------------------------------------------------------- ③ 异常/超时也计
def test_exception_still_counted_and_reraised(acct):
    mw = uc.UsageCounterMiddleware()

    def boom(r):
        raise TimeoutError("超时了")

    with pytest.raises(TimeoutError):
        mw.wrap_tool_call(Req("tavily_search"), boom)
    got = rows(acct)
    assert len(got) == 1, "★ 异常也必须计（额度已经花掉了）"
    assert got[0]["ok"] == 0


def test_async_exception_still_counted(acct):
    mw = uc.UsageCounterMiddleware()

    async def boom(r):
        raise RuntimeError("x")

    with pytest.raises(RuntimeError):
        asyncio.run(mw.awrap_tool_call(Req("tavily_search"), boom))
    assert rows(acct)[0]["ok"] == 0


def test_multiple_calls_counted_separately(acct):
    """反证 C2：委派子 Agent 跑 2 次检索 → 2 条（这正是"只挂主图会漏"的那部分）。"""
    mw = uc.UsageCounterMiddleware()
    for _ in range(2):
        mw.wrap_tool_call(Req("tavily_search"), lambda r: "ok")
    assert len(rows(acct)) == 2


# ---------------------------------------------------------------- ④ 不依赖 report_tool
def test_does_not_depend_on_report_tool(acct, monkeypatch):
    """★ 反证：把 monitor.report_tool 换成"一调就炸"，计数照样成立 —— 说明没依赖它。"""
    import api.monitor as mon

    def bomb(*a, **k):
        raise AssertionError("计数器不得依赖 report_tool（那是每个工具各自上报的老路）")

    monkeypatch.setattr(mon.monitor, "report_tool", bomb, raising=False)
    uc.UsageCounterMiddleware().wrap_tool_call(Req("tavily_search"), lambda r: "ok")
    assert len(rows(acct)) == 1


# ---------------------------------------------------------------- ⑤ 上下文缺失/异常都不炸
def test_three_tuple_reader_supplies_turn_index(acct):
    """★ 真机抽样发现的缺口：读取器原来只给 2 元组 → turn_index 恒 0，「本轮」等于废的。"""
    uc.set_context_reader(lambda: (acct, "th-t", 7))
    uc.UsageCounterMiddleware().wrap_tool_call(Req("internet_search"), lambda r: "ok")
    conn = get_personal_conn()
    try:
        row = conn.execute("SELECT turn_index FROM usage_events WHERE account_id=?", (acct,)).fetchone()
    finally:
        conn.close()
    assert row[0] == 7, "3 元组读取器的第三个值要落进 turn_index"


def test_two_tuple_reader_still_works(acct):
    """兼容老注册方式（2 元组）：turn_index 记 0，但不能炸。"""
    uc.set_context_reader(lambda: (acct, "th-t"))
    uc.UsageCounterMiddleware().wrap_tool_call(Req("internet_search"), lambda r: "ok")
    conn = get_personal_conn()
    try:
        row = conn.execute("SELECT turn_index, kind FROM usage_events WHERE account_id=?", (acct,)).fetchone()
    finally:
        conn.close()
    assert row[0] == 0 and row[1] == uc.KIND_RETRIEVAL_EXTERNAL


def test_no_context_is_silent():
    uc.set_context_reader(None)
    try:
        out = uc.UsageCounterMiddleware().wrap_tool_call(Req("tavily_search"), lambda r: "结果")
        assert out == "结果", "没有上下文时照常执行工具，只是不计数"
    finally:
        uc.set_context_reader(None)


def test_broken_reader_does_not_break_the_call(acct):
    def broken():
        raise RuntimeError("读上下文炸了")

    uc.set_context_reader(broken)
    try:
        assert uc.UsageCounterMiddleware().wrap_tool_call(Req("query_kb"), lambda r: "ok") == "ok"
    finally:
        uc.set_context_reader(None)


def test_broken_recording_does_not_break_the_call(acct, monkeypatch):
    """落库炸了也不能影响工具返回（计数器坏了不能拖垮主流程）。"""
    import tools.schema_personal as sp

    def bomb(*a, **k):
        raise RuntimeError("库炸了")

    monkeypatch.setattr(sp, "get_personal_conn", bomb)
    assert uc.UsageCounterMiddleware().wrap_tool_call(Req("query_kb"), lambda r: "ok") == "ok"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
