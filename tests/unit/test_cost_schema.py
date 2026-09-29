# -*- coding: utf-8 -*-
"""M6c-1 用例：迁移 —— `llm_providers` 三个单价列 + `usage_events` 十个 token/成本列 + `failure_events` 表。

判据来源：`docs/v3.0/M6c-成本表与失败分类方案.md` §5.1 迁移契约 + §六 M6c-1（≥8 项）+ G5/G9。
★ 本项目最常踩的坑（M4/M5 各踩过一次）：新列只加在 ALTER 路径、忘了加到 CREATE TABLE，
  于是"老库升级正常、全新库缺列"—— 所以这里有一条**全新库**用例专门盯它。

运行：.venv/Scripts/python.exe -m pytest tests/unit/test_cost_schema.py -q
"""
import os
import sqlite3
import sys
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import tools.schema_personal as sp                                          # noqa: E402
from tools.schema_personal import ensure_tables, get_personal_conn, purge_account   # noqa: E402

ensure_tables()

PRICE_COLS = ("price_in_cached", "price_in_uncached", "price_out")
TOKEN_COLS = ("provider_id", "model_name", "input_tokens", "output_tokens", "cached_tokens",
              "uncached_tokens", "token_source", "cost", "cost_note", "price_snapshot")


def table_cols(conn, tbl):
    return [r[1] for r in conn.execute("PRAGMA table_info(%s)" % tbl)]


def mk_account(tag="c"):
    """建一个临时账号（用例自清，不动真实账号）。"""
    conn = get_personal_conn()
    try:
        aid = int(conn.execute("INSERT INTO accounts (username, password_hash, role) VALUES (?,?,?)",
                               ("m6c%s_%d" % (tag, int(time.time() * 1000) % 1000000), "x", "user")).lastrowid)
        conn.commit()
    finally:
        conn.close()
    return aid


# ---------------------------------------------------------------- 列/表存在

def test_llm_providers_has_three_price_columns():
    conn = get_personal_conn()
    try:
        cols = table_cols(conn, "llm_providers")
    finally:
        conn.close()
    for c in PRICE_COLS:
        assert c in cols, "缺列 %s（M6c 三处单价）" % c


def test_usage_events_has_ten_token_columns():
    conn = get_personal_conn()
    try:
        cols = table_cols(conn, "usage_events")
    finally:
        conn.close()
    for c in TOKEN_COLS:
        assert c in cols, "缺列 %s（M6c token/成本列）" % c


def test_failure_events_table_and_indexes():
    conn = get_personal_conn()
    try:
        cols = table_cols(conn, "failure_events")
        idx = [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='failure_events'")]
    finally:
        conn.close()
    assert cols[:5] == ["id", "account_id", "code", "detail", "occurred_at"]
    assert "idx_failure_time" in idx and "idx_failure_acct" in idx


def test_fresh_db_gets_all_new_columns(tmp_path, monkeypatch):
    """★ 全新库必须带上全部新列（DDL 路径）—— 只改 ALTER 会留下"新装即缺列"的坑。"""
    monkeypatch.setattr(sp, "DB_PATH", str(tmp_path / "fresh.db"))
    sp.ensure_tables()
    conn = sp.get_personal_conn()
    try:
        assert set(PRICE_COLS) <= set(table_cols(conn, "llm_providers"))
        assert set(TOKEN_COLS) <= set(table_cols(conn, "usage_events"))
        assert "failure_events" in [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")]
    finally:
        conn.close()


# ---------------------------------------------------------------- 幂等与数据安全

def test_migration_is_idempotent():
    """连跑三次不报错、行数不变、列数不重复。"""
    def snap():
        c = get_personal_conn()
        try:
            return (c.execute("SELECT COUNT(*) FROM usage_events").fetchone()[0],
                    c.execute("SELECT COUNT(*) FROM llm_providers").fetchone()[0],
                    len(table_cols(c, "usage_events")), len(table_cols(c, "llm_providers")))
        finally:
            c.close()
    before = snap()
    for _ in range(3):
        ensure_tables()
    assert snap() == before


def test_new_columns_default_to_null():
    """新列默认 NULL：老数据/工具行不该被塞进 0（0 与 NULL 在本议题里语义不同）。"""
    aid = mk_account()
    try:
        conn = get_personal_conn()
        try:
            pid = int(conn.execute(
                "INSERT INTO llm_providers (owner_id, provider_name, model_name, base_url, api_key)"
                " VALUES (?,?,?,?,?)", (aid, "T", "m", "u", "k")).lastrowid)
            row = conn.execute("SELECT price_in_cached, price_in_uncached, price_out FROM llm_providers"
                               " WHERE id=?", (pid,)).fetchone()
            assert tuple(row) == (None, None, None)          # 留空 = 未配置（不折算）
            conn.execute("INSERT INTO usage_events (account_id, kind, tool_name, created_at, created_ts)"
                         " VALUES (?,?,?,?,?)", (aid, "internal", "web_search", "2026-09-29 10:00:00", 1.0))
            conn.commit()
            r2 = conn.execute("SELECT provider_id, input_tokens, cost FROM usage_events"
                              " WHERE account_id=? AND kind='internal'", (aid,)).fetchone()
            assert tuple(r2) == (None, None, None)           # 工具行不写 token/成本
        finally:
            conn.close()
    finally:
        purge_account(aid)


def test_zero_price_is_distinct_from_null():
    """G5/①-b：填 0 = 明确免费（≠ 留空）。两列都能存，且读回来不混淆。"""
    aid = mk_account()
    try:
        conn = get_personal_conn()
        try:
            conn.execute("INSERT INTO llm_providers (owner_id, provider_name, model_name, base_url, api_key,"
                         " price_in_cached, price_in_uncached, price_out) VALUES (?,?,?,?,?,?,?,?)",
                         (aid, "免费", "agnes-2.5-flash", "u", "k", 0.0, 0.0, 0.0))
            conn.execute("INSERT INTO llm_providers (owner_id, provider_name, model_name, base_url, api_key)"
                         " VALUES (?,?,?,?,?)", (aid, "未配置", "x", "u", "k"))
            conn.commit()
            free = conn.execute("SELECT price_out FROM llm_providers WHERE owner_id=? AND provider_name='免费'",
                                (aid,)).fetchone()[0]
            unset = conn.execute("SELECT price_out FROM llm_providers WHERE owner_id=? AND provider_name='未配置'",
                                 (aid,)).fetchone()[0]
            assert free == 0.0 and unset is None
            assert free is not None                             # 0 不等于 NULL
        finally:
            conn.close()
    finally:
        purge_account(aid)


def test_model_usage_row_roundtrip():
    """模型用量行可写可读（M6c-2/3 之后要靠这些列出账）。"""
    aid = mk_account()
    try:
        conn = get_personal_conn()
        try:
            conn.execute(
                "INSERT INTO usage_events (account_id, kind, provider_id, model_name, input_tokens,"
                " output_tokens, cached_tokens, uncached_tokens, token_source, cost, cost_note,"
                " price_snapshot, created_at, created_ts) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (aid, "model", 7, "deepseek-chat", 1000, 200, 300, 700, "reported", 0.0123, "",
                 '{"price_out":8.0}', "2026-09-29 10:00:00", 1.0))
            conn.commit()
            r = conn.execute("SELECT input_tokens, output_tokens, cached_tokens, uncached_tokens,"
                             " token_source, cost, cost_note, price_snapshot, provider_id, model_name"
                             " FROM usage_events WHERE account_id=? AND kind='model'", (aid,)).fetchone()
            assert tuple(r) == (1000, 200, 300, 700, "reported", 0.0123, "", '{"price_out":8.0}', 7,
                                "deepseek-chat")
        finally:
            conn.close()
    finally:
        purge_account(aid)


# ---------------------------------------------------------------- 隔离与约束

def test_purge_account_cleans_failure_events():
    """G9：删账号时 `failure_events` 必须连带清掉（`purge_account` 自省 sqlite_master）。"""
    aid = mk_account()
    try:
        conn = get_personal_conn()
        try:
            conn.execute("INSERT INTO failure_events (account_id, code, detail, occurred_at)"
                         " VALUES (?,?,?,?)", (aid, "BACKEND_PORT_TAKEN", "10048", "2026-09-29 10:00:00"))
            conn.commit()
        finally:
            conn.close()
        removed = purge_account(aid)
        assert removed.get("failure_events") == 1
        conn = get_personal_conn()
        try:
            assert conn.execute("SELECT COUNT(*) FROM failure_events WHERE account_id=?",
                                (aid,)).fetchone()[0] == 0
        finally:
            conn.close()
    finally:
        purge_account(aid)


def test_failure_events_code_is_not_null():
    """`code` 是枚举名，不许为空（否则失败分类形同虚设）。"""
    aid = mk_account()
    try:
        conn = get_personal_conn()
        try:
            with pytest.raises(sqlite3.IntegrityError):
                conn.execute("INSERT INTO failure_events (account_id, code, occurred_at) VALUES (?,?,?)",
                             (aid, None, "2026-09-29 10:00:00"))
        finally:
            conn.close()
    finally:
        purge_account(aid)


def test_failure_events_rejects_unknown_account():
    """外键开着：account_id 必须指向真实账号（隔离不靠自觉）。"""
    conn = get_personal_conn()
    try:
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("INSERT INTO failure_events (account_id, code, occurred_at) VALUES (?,?,?)",
                         (99999999, "USER_QUIT", "2026-09-29 10:00:00"))
    finally:
        conn.close()
