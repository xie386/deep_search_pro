# -*- coding: utf-8 -*-
"""M5-1 用例：价格台账的表与加列（离线，临时账号；不动真实数据）。

判据来源：M5 方案 §六 M5-1（建表/加列幂等、索引齐、清理生效 ≥8 项）+ §5.1/§5.2 + G4/G9。

运行：.venv/Scripts/python.exe -m pytest tests/test_price_schema.py -q
"""
import os
import sqlite3
import sys
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from tools.schema_personal import ensure_tables, get_personal_conn, purge_account   # noqa: E402

TABLES = ("price_history", "price_mentions", "price_alerts")
PROD_COLS = ("url", "target_price", "alert_drop_pct", "monitor_enabled", "last_price")
COMP_COLS = ("url", "target_price", "monitor_enabled", "last_price")


def cols(t):
    conn = get_personal_conn()
    try:
        return [r[1] for r in conn.execute("PRAGMA table_info(%s)" % t)]
    finally:
        conn.close()


def mk_account(tag="m5"):
    conn = get_personal_conn()
    try:
        aid = int(conn.execute("INSERT INTO accounts (username, password_hash, role) VALUES (?,?,?)",
                               ("m5_%s_%d" % (tag, int(time.time() * 1000) % 1000000), "x", "user")).lastrowid)
        conn.commit()
    finally:
        conn.close()
    return aid


# ---------------------------------------------------------------- ① 建表/加列幂等
def test_create_and_migrate_is_idempotent():
    ensure_tables()
    ensure_tables()                      # 再跑一次不该炸
    conn = get_personal_conn()
    try:
        tab = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    finally:
        conn.close()
    assert set(TABLES) <= tab, "缺表：%s" % (set(TABLES) - tab)


def test_planned_columns_added_to_products_and_company_products():
    ensure_tables()
    p, c = cols("products"), cols("company_products")
    assert [x for x in PROD_COLS if x not in p] == [], "products 缺列"
    assert [x for x in COMP_COLS if x not in c] == [], "company_products 缺列"


# ---------------------------------------------------------------- ② 三表结构
@pytest.mark.parametrize("t", TABLES)
def test_each_table_is_account_scoped(t):
    got = cols(t)
    assert "account_id" in got, "%s 必须按 account_id 隔离（G9）" % t


def test_price_history_core_columns():
    got = cols("price_history")
    for need in ("item_type", "item_id", "item_name", "price", "currency", "source_type",
                 "source_ref", "observed_at", "note"):
        assert need in got, "price_history 缺列 %s" % need


def test_indexes_exist():
    conn = get_personal_conn()
    try:
        idx = {}
        for t in TABLES:
            idx[t] = [r[1] for r in conn.execute("PRAGMA index_list(%s)" % t)]
    finally:
        conn.close()
    assert any("ph_item" in n for n in idx["price_history"]), "台账要按(账号,对象,时间)建索引"
    assert any("pm_entity" in n for n in idx["price_mentions"]), "资讯价要能按实体查"
    assert any("pa_acct" in n for n in idx["price_alerts"]), "price_alerts 应有(账号,触发时间)索引"


# ---------------------------------------------------------------- ③ G4 硬闸：来源类型
def test_source_type_check_rejects_news_in_history():
    """★ G4：资讯价（news）**绝不能**混进 price_history —— 那会把"资讯里的报价"当成"当前售价"。"""
    aid = mk_account("gate")
    try:
        conn = get_personal_conn()
        try:
            conn.execute("INSERT INTO price_history (account_id, item_type, item_id, price, source_type,"
                         " observed_at) VALUES (?,?,?,?,?,?)",
                         (aid, "product", 1, 99.0, "manual", "2026-09-28"))
            conn.commit()
            with pytest.raises(sqlite3.IntegrityError):
                conn.execute("INSERT INTO price_history (account_id, item_type, item_id, price, source_type,"
                             " observed_at) VALUES (?,?,?,?,?,?)",
                             (aid, "product", 1, 99.0, "news", "2026-09-28"))
        finally:
            conn.close()
    finally:
        purge_account(aid)


# ---------------------------------------------------------------- ④ 隔离与清理（G9）
def test_purge_account_cascades_to_new_tables():
    aid = mk_account("purge")
    conn = get_personal_conn()
    try:
        conn.execute("INSERT INTO price_history (account_id, item_type, price, source_type, observed_at)"
                     " VALUES (?,?,?,?,?)", (aid, "product", 10.0, "manual", "2026-09-28"))
        # 真实列：item_type / rule / detail（★ 之前我按想当然写 alert_type/message → OperationalError）
        conn.execute("INSERT INTO price_alerts (account_id, item_type, rule, detail, fired_at)"
                     " VALUES (?,?,?,?,?)", (aid, "product", "below_target", "测试", "2026-09-28 10:00:00"))
        conn.commit()
    finally:
        conn.close()
    removed = purge_account(aid)
    assert removed.get("price_history") == 1, "删账号要连带清台账（自省式清理）"
    assert "price_alerts" in removed
    conn = get_personal_conn()
    try:
        for t in ("price_history", "price_alerts"):
            assert conn.execute("SELECT COUNT(*) FROM %s WHERE account_id=?" % t, (aid,)).fetchone()[0] == 0
    finally:
        conn.close()


def test_account_isolation_on_history():
    a, b = mk_account("iso1"), mk_account("iso2")
    try:
        conn = get_personal_conn()
        try:
            conn.execute("INSERT INTO price_history (account_id, item_type, price, source_type, observed_at)"
                         " VALUES (?,?,?,?,?)", (a, "product", 1.0, "manual", "2026-09-28"))
            conn.commit()
            na = conn.execute("SELECT COUNT(*) FROM price_history WHERE account_id=?", (a,)).fetchone()[0]
            nb = conn.execute("SELECT COUNT(*) FROM price_history WHERE account_id=?", (b,)).fetchone()[0]
        finally:
            conn.close()
        assert (na, nb) == (1, 0)
    finally:
        purge_account(a); purge_account(b)


def test_migration_columns_really_exist_not_just_in_ddl():
    """★ 这条是给"迁移写进条件分支/except 里永不执行"那个坑留的哨兵：真跑一遍 ensure_tables 再断言列在。"""
    ensure_tables()
    conn = get_personal_conn()
    try:
        got = {r[1] for r in conn.execute("PRAGMA table_info(products)")}
    finally:
        conn.close()
    assert "last_price" in got and "target_price" in got


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
