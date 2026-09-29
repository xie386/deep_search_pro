# -*- coding: utf-8 -*-
"""M4-1 用例：`usage_events` 建表与账号域清理（离线，不动真实数据）。

判据来源：M4 方案 §六 M4-1（建表幂等、列齐、清理生效 ≥6 项）+ D3（永不清理）+ §5.1。

运行：.venv/Scripts/python.exe -m pytest tests/test_usage_schema.py -q
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from tools.schema_personal import ensure_tables, get_personal_conn, purge_account   # noqa: E402

# M4 的 12 列 + M6c-1 补的 10 列（模型用量/成本）——**加列后本用例要同步更新**，
# 它的价值就是"把当前列清单钉死"，所以是精确相等而不是包含。
COLS = {"id", "account_id", "thread_id", "turn_index", "kind", "tool_name", "source", "ok", "latency_ms",
        "result_chars", "created_at", "created_ts",
        "provider_id", "model_name", "input_tokens", "output_tokens", "cached_tokens", "uncached_tokens",
        "token_source", "cost", "cost_note", "price_snapshot", "recalculated_at"}


def mk_account(tag="ue"):
    conn = get_personal_conn()
    try:
        uname = "m4_%s_%d" % (tag, int(time.time() * 1000) % 1000000)
        aid = int(conn.execute("INSERT INTO accounts (username, password_hash, role) VALUES (?,?,?)",
                               (uname, "x", "user")).lastrowid)
        conn.commit()
    finally:
        conn.close()
    return aid


def add_event(aid, kind="retrieval_external", **kw):
    conn = get_personal_conn()
    try:
        conn.execute(
            "INSERT INTO usage_events (account_id, thread_id, kind, tool_name, source, ok, latency_ms,"
            " created_at, created_ts) VALUES (?,?,?,?,?,?,?,?,?)",
            (aid, kw.get("thread_id", "t1"), kind, kw.get("tool_name", "tavily_search"),
             kw.get("source", "网络搜索"), kw.get("ok", 1), kw.get("latency_ms", 0),
             kw.get("created_at", "2026-09-27 10:00:00"), kw.get("created_ts", time.time())))
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------- ① 建表幂等 + 列齐
def test_create_is_idempotent_and_complete():
    ensure_tables()
    ensure_tables()                       # 再跑一次不该炸
    conn = get_personal_conn()
    try:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(usage_events)")}
    finally:
        conn.close()
    assert cols == COLS, "列不全或多了列：%s" % (COLS ^ cols)


def test_not_null_and_defaults():
    conn = get_personal_conn()
    try:
        info = {r[1]: r for r in conn.execute("PRAGMA table_info(usage_events)")}
    finally:
        conn.close()
    for c in ("account_id", "kind", "ok", "latency_ms", "created_at", "created_ts"):
        assert info[c][3] == 1, "%s 应 NOT NULL" % c
    assert info["ok"][4] == "1", "ok 默认 1（成功）"


# ---------------------------------------------------------------- ② 索引与排序
def test_turn_and_result_chars_exist_for_m4_4():
    """M4-4「本轮 vs 本会话」必须靠 turn_index；成本卡要 result_chars（方案 §3.1 的列补齐）。"""
    conn = get_personal_conn()
    try:
        info = {r[1]: r for r in conn.execute("PRAGMA table_info(usage_events)")}
    finally:
        conn.close()
    assert "turn_index" in info and "result_chars" in info


def test_transient_insert_with_new_columns():
    aid = mk_account("cols")
    try:
        conn = get_personal_conn()
        try:
            conn.execute(
                "INSERT INTO usage_events (account_id, thread_id, turn_index, kind, tool_name, source, ok,"
                " latency_ms, result_chars, created_at, created_ts) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (aid, "t", 3, "retrieval_external", "internet_search", "网络搜索", 1, 120, 456,
                 "2026-09-27 10:00:00", 1.0))
            conn.commit()
            row = conn.execute("SELECT turn_index, result_chars FROM usage_events WHERE account_id=?",
                               (aid,)).fetchone()
        finally:
            conn.close()
        assert tuple(row) == (3, 456)
    finally:
        purge_account(aid)


def test_index_exists_for_aggregation():
    conn = get_personal_conn()
    try:
        idx = [r[1] for r in conn.execute("PRAGMA index_list(usage_events)")]
    finally:
        conn.close()
    assert "idx_usage_account_time" in idx, "★ 聚合索引必须在（D3 不清理 → 表会长期增长）"


def test_created_ts_sorts_numerically():
    aid = mk_account("sort")
    try:
        add_event(aid, created_ts=100.5)
        add_event(aid, created_ts=200.5)
        conn = get_personal_conn()
        try:
            got = [r[0] for r in conn.execute(
                "SELECT created_ts FROM usage_events WHERE account_id=? ORDER BY created_ts DESC", (aid,))]
        finally:
            conn.close()
        assert got == [200.5, 100.5], "按数值排序（不是字符串）"
    finally:
        purge_account(aid)


# ---------------------------------------------------------------- ③ 账号隔离 + 清理连带
def test_account_isolation():
    a, b = mk_account("iso1"), mk_account("iso2")
    try:
        add_event(a); add_event(a); add_event(b)
        conn = get_personal_conn()
        try:
            na = conn.execute("SELECT COUNT(*) FROM usage_events WHERE account_id=?", (a,)).fetchone()[0]
            nb = conn.execute("SELECT COUNT(*) FROM usage_events WHERE account_id=?", (b,)).fetchone()[0]
        finally:
            conn.close()
        assert (na, nb) == (2, 1)
    finally:
        purge_account(a); purge_account(b)


def test_purge_account_removes_usage_events():
    aid = mk_account("purge")
    add_event(aid)
    conn = get_personal_conn()
    try:
        assert conn.execute("SELECT COUNT(*) FROM usage_events WHERE account_id=?", (aid,)).fetchone()[0] == 1
    finally:
        conn.close()
    removed = purge_account(aid)
    assert "usage_events" in removed and removed["usage_events"] == 1, "★ 删账号要连带清掉用量事件（自省式清理）"
    conn = get_personal_conn()
    try:
        assert conn.execute("SELECT COUNT(*) FROM usage_events WHERE account_id=?", (aid,)).fetchone()[0] == 0
    finally:
        conn.close()


def test_foreign_key_declared():
    """带 REFERENCES accounts(id)：与其它账号域表一致，purge_account 的外键自省才能覆盖它。"""
    conn = get_personal_conn()
    try:
        fks = list(conn.execute("PRAGMA foreign_key_list(usage_events)"))
    finally:
        conn.close()
    assert fks and fks[0][2] == "accounts" and fks[0][3] == "account_id"


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q"]))
