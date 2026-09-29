# -*- coding: utf-8 -*-
"""M4-4 用例：`/api/usage` 出口（离线，临时账号 + 临时会话）。

判据来源：M4 方案 §六 M4-4（本轮 vs 本会话、分类、空会话、**跨账号 403** ≥8 项）。

运行：.venv/Scripts/python.exe -m pytest tests/test_usage_api.py -q
"""
import os
import sys
import time

import pytest
from fastapi import HTTPException

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from agent import usage_counter as uc                                  # noqa: E402
from api.usage_api import usage_report                                 # noqa: E402
from tools.schema_personal import ensure_tables, get_personal_conn, purge_account   # noqa: E402

ensure_tables()


def mk_account(tag="ua"):
    conn = get_personal_conn()
    try:
        uname = "m4u_%s_%d" % (tag, int(time.time() * 1000) % 1000000)
        aid = int(conn.execute("INSERT INTO accounts (username, password_hash, role) VALUES (?,?,?)",
                               (uname, "x", "user")).lastrowid)
        token = "tk_%d_%d" % (aid, int(time.time() * 1000) % 1000000)
        conn.execute("INSERT INTO sessions (token, account_id, login_at) VALUES (?,?,?)",
                     (token, aid, time.time()))
        conn.commit()
    finally:
        conn.close()
    return aid, token


def mk_thread(aid, tid):
    """按真实列类型插入（conversations.id 若是 INTEGER，塞字符串会 datatype mismatch）。"""
    conn = get_personal_conn()
    try:
        # 真实 schema：id=自增主键 INTEGER，thread_id=业务会话号 TEXT NOT NULL（越权校验按它查）
        conn.execute("INSERT INTO conversations (account_id, thread_id, title, created_at, updated_at)"
                     " VALUES (?,?,?,?,?)", (aid, tid, "t", "2026-09-27 10:00:00", "2026-09-27 10:00:00"))
        conn.commit()
    finally:
        conn.close()


def add(aid, tid, kind, turn, n=1):
    for _ in range(n):
        uc.record(aid, tid, kind, "t", "src", ok=True, latency_ms=10, turn_index=turn, result_chars=100)


@pytest.fixture()
def two():
    a1, t1 = mk_account("a")
    a2, t2 = mk_account("b")
    mk_thread(a1, "90001"); mk_thread(a2, "90002")
    yield {"a1": a1, "t1": t1, "a2": a2, "t2": t2}
    uc.set_context_reader(None)
    purge_account(a1); purge_account(a2)


# ---------------------------------------------------------------- ① 本轮 vs 本会话
def test_this_turn_vs_this_session(two):
    add(two["a1"], "90001", uc.KIND_RETRIEVAL_EXTERNAL, 1, 3)      # 第 1 轮 3 次检索
    add(two["a1"], "90001", uc.KIND_INTERNAL, 1, 2)
    add(two["a1"], "90001", uc.KIND_RETRIEVAL_EXTERNAL, 2, 1)      # 第 2 轮 1 次
    d = usage_report(two["t1"], "90001")
    assert d["this_session"]["total"] == 6 and d["this_session"]["cost_calls"] == 4
    assert d["this_turn"]["total"] == 1, "★ 本轮 = 最大 turn_index 那一轮"
    assert d["this_turn"]["cost_calls"] == 1 and d["turn_index"] == 2


def test_kinds_are_labelled(two):
    add(two["a1"], "90001", uc.KIND_RETRIEVAL_EXTERNAL, 1)
    add(two["a1"], "90001", uc.KIND_MCP, 1)
    got = usage_report(two["t1"], "90001")["this_session"]["by_kind"]
    assert got.get("外部检索") == 1 and got.get("MCP 工具") == 1


def test_only_retrieval_counts_as_cost(two):
    add(two["a1"], "90001", uc.KIND_INTERNAL, 1, 5)
    add(two["a1"], "90001", uc.KIND_SHELL, 1, 2)
    d = usage_report(two["t1"], "90001")
    assert d["this_session"]["total"] == 7 and d["this_session"]["cost_calls"] == 0


def test_empty_session_returns_zeros(two):
    d = usage_report(two["t1"], "90001")
    assert d["this_session"]["total"] == 0 and d["this_turn"]["total"] == 0
    assert d["this_session"]["by_kind"] == {}


def test_no_thread_id_returns_zeros_not_error(two):
    d = usage_report(two["t1"], "")
    assert d["thread_id"] == "" and d["this_session"]["total"] == 0


def test_other_accounts_events_do_not_leak(two):
    add(two["a2"], "90002", uc.KIND_RETRIEVAL_EXTERNAL, 1, 9)
    assert usage_report(two["t1"], "90001")["this_session"]["total"] == 0


# ---------------------------------------------------------------- ② 越权
def test_foreign_thread_is_403(two):
    with pytest.raises(HTTPException) as e:
        usage_report(two["t1"], "90002")
    assert e.value.status_code == 403, "★ 别人的会话不能「过滤成 0」糊过去，要 403"


def test_bad_token_is_rejected(two):
    with pytest.raises(HTTPException):
        usage_report("nope-token", "90001")


def test_endpoint_registered():
    s = open(os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                          "api", "server.py"), encoding="utf-8").read()
    assert '"/api/usage"' in s and "usage_report(token, thread_id)" in s


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
