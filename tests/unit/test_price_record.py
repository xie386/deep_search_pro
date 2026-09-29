# -*- coding: utf-8 -*-
"""M5-2 用例：`record_price()` —— 去重 / 更新 last_price / 判 new_low·below_target / 写 alerts。

判据来源：M5 方案 §六 M5-2（≥14 项）+ §5.3 四步 + G2/G3 + D5（提醒去重口径）。

运行：.venv/Scripts/python.exe -m pytest tests/test_price_record.py -q
"""
import os
import sys
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from tools.price_ledger import (RULE_BELOW_TARGET, RULE_NEW_LOW, ack_alert, pending_alerts,   # noqa: E402
                                record_price)
from tools.schema_personal import ensure_tables, get_personal_conn, purge_account   # noqa: E402

ensure_tables()


def mk_account():
    conn = get_personal_conn()
    try:
        aid = int(conn.execute("INSERT INTO accounts (username, password_hash, role) VALUES (?,?,?)",
                               ("m5r_%d" % (int(time.time() * 1000) % 1000000), "x", "user")).lastrowid)
        conn.commit()
    finally:
        conn.close()
    return aid


def mk_product(aid, name="米诺地尔泡沫剂", target=None, tbl="products"):
    conn = get_personal_conn()
    try:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(%s)" % tbl)}
        base = "INSERT INTO %s (owner_id, product_name, category, price, status" % tbl
        vals = [aid, name, "个护", 120.0, "active"]
        extra = ""
        if "target_price" in cols and target is not None:
            extra, vals = ", target_price", vals + [target]
        cur = conn.execute(base + extra + ") VALUES (%s)" % ",".join(["?"] * len(vals)), tuple(vals))
        conn.commit()
        return int(cur.lastrowid)
    finally:
        conn.close()


def rows(aid, item_id=None):
    conn = get_personal_conn()
    try:
        sql = "SELECT id, price, observed_at FROM price_history WHERE account_id=?"
        args = [aid]
        if item_id is not None:
            sql += " AND item_id=?"
            args.append(item_id)
        return list(conn.execute(sql + " ORDER BY id", tuple(args)))
    finally:
        conn.close()


def alerts(aid, rule=""):
    conn = get_personal_conn()
    try:
        sql = ("SELECT id, rule, price, detail, acked_at FROM price_alerts WHERE account_id=?"
               + (" AND rule=?" if rule else "") + " ORDER BY id")
        return list(conn.execute(sql, (aid, rule) if rule else (aid,)))
    finally:
        conn.close()


def last_price(aid, pid, tbl="products"):
    conn = get_personal_conn()
    try:
        return conn.execute("SELECT last_price FROM %s WHERE id=? AND owner_id=?" % tbl,
                            (pid, aid)).fetchone()[0]
    finally:
        conn.close()


@pytest.fixture()
def acct():
    aid = mk_account()
    pid = mk_product(aid, target=100.0)
    yield {"aid": aid, "pid": pid}
    purge_account(aid)


# ---------------------------------------------------------------- ① 记账与去重（G2）
def test_first_record_no_alert_and_updates_last_price(acct):
    r = record_price(acct["aid"], "product", acct["pid"], 120.0)
    assert r["ok"] and not r["deduped"] and r["alert"] == "", "★ 首条记录不算创新低（否则一记价就报警）"
    assert r["delta_pct"] == 0.0
    assert len(rows(acct["aid"], acct["pid"])) == 1
    assert last_price(acct["aid"], acct["pid"]) == 120.0, "冗余 last_price 要同步"


def test_same_price_same_day_is_deduped(acct):
    a = record_price(acct["aid"], "product", acct["pid"], 99.0)
    b = record_price(acct["aid"], "product", acct["pid"], 99.0)
    assert b["deduped"] is True and b["row_id"] == a["row_id"]
    assert len(rows(acct["aid"], acct["pid"])) == 1, "★ 同值同日不新增行"


def test_same_price_other_day_is_new_row(acct):
    record_price(acct["aid"], "product", acct["pid"], 99.0, observed_at="2026-09-20")
    record_price(acct["aid"], "product", acct["pid"], 99.0, observed_at="2026-09-21")
    assert len(rows(acct["aid"], acct["pid"])) == 2


def test_delta_pct_is_negative_on_drop(acct):
    record_price(acct["aid"], "product", acct["pid"], 100.0, observed_at="2026-09-20")
    r = record_price(acct["aid"], "product", acct["pid"], 88.0, observed_at="2026-09-21")
    assert r["delta_pct"] == -12.0


# ---------------------------------------------------------------- ② 提醒触发与去重（G3/D5）
def test_new_low_fires_once(acct):
    record_price(acct["aid"], "product", acct["pid"], 120.0, observed_at="2026-09-20")
    r = record_price(acct["aid"], "product", acct["pid"], 110.0, observed_at="2026-09-21")
    assert r["alert"] == RULE_NEW_LOW and len(alerts(acct["aid"], RULE_NEW_LOW)) == 1


def test_not_a_new_low_does_not_alert_again(acct):
    """★ D5 核心：只有"创新低"才再提醒。

    数字刻意远离目标价（fixture 的目标价是 100）：200 → 180（创新低，1 条）→ 190（未创新低，0 条）。
    上一版我用 120→100→105，其中 100 正好触及目标价 → 本就该同时产生 below_target，
    是我的用例算错了，不是代码错。
    """
    record_price(acct["aid"], "product", acct["pid"], 200.0, observed_at="2026-09-20")
    record_price(acct["aid"], "product", acct["pid"], 180.0, observed_at="2026-09-21")   # 创新低
    r = record_price(acct["aid"], "product", acct["pid"], 190.0, observed_at="2026-09-22")  # 未创新低
    assert r["alert"] == "", "★ D5：未创新低不重复提醒"
    assert len(alerts(acct["aid"])) == 1, "全程只该有 1 条提醒"
    assert alerts(acct["aid"], RULE_NEW_LOW) and not alerts(acct["aid"], RULE_BELOW_TARGET)


def test_first_time_below_target_fires(acct):
    r = record_price(acct["aid"], "product", acct["pid"], 99.0, observed_at="2026-09-20")
    assert r["alert"] == RULE_BELOW_TARGET
    assert len(alerts(acct["aid"], RULE_BELOW_TARGET)) == 1


def test_already_below_target_does_not_refire(acct):
    record_price(acct["aid"], "product", acct["pid"], 99.0, observed_at="2026-09-20")   # 首次跌破
    r = record_price(acct["aid"], "product", acct["pid"], 95.0, observed_at="2026-09-21")  # 仍在目标下
    assert len(alerts(acct["aid"], RULE_BELOW_TARGET)) == 1, "★ 只提醒「首次」跨过目标价"


def test_below_target_and_new_low_both_fire(acct):
    record_price(acct["aid"], "product", acct["pid"], 120.0, observed_at="2026-09-20")
    record_price(acct["aid"], "product", acct["pid"], 99.0, observed_at="2026-09-21")
    assert len(alerts(acct["aid"], RULE_NEW_LOW)) == 1
    assert len(alerts(acct["aid"], RULE_BELOW_TARGET)) == 1


# ---------------------------------------------------------------- ③ 输入校验与隔离
@pytest.mark.parametrize("bad", [0, -1, "abc", None])
def test_invalid_price_rejected(acct, bad):
    with pytest.raises(ValueError):
        record_price(acct["aid"], "product", acct["pid"], bad)


def test_invalid_type_and_source_rejected(acct):
    with pytest.raises(ValueError):
        record_price(acct["aid"], "nope", acct["pid"], 10.0)
    with pytest.raises(ValueError):
        record_price(acct["aid"], "product", acct["pid"], 10.0, source_type="news")


def test_other_account_product_is_not_touched(acct):
    """别人的商品：既不该改它的 last_price，也不该给它发提醒（G9）。"""
    other = mk_account()
    try:
        opid = mk_product(other, "别人的东西", target=10.0)
        record_price(acct["aid"], "product", opid, 5.0)
        assert last_price(other, opid) is None, "★ 不能更新别的账号的商品"
        assert alerts(other) == [], "★ 也不能给别的账号发提醒"
    finally:
        purge_account(other)


def test_company_product_updates_its_own_table(acct):
    cpid = mk_product(acct["aid"], "我司产品", target=50.0, tbl="company_products")
    record_price(acct["aid"], "company_product", cpid, 45.0)
    assert last_price(acct["aid"], cpid, tbl="company_products") == 45.0


def test_competitor_type_records_without_error(acct):
    """竞品价走同一台账（item_type='competitor_product'），没有对应表要更新，但不能炸。"""
    r = record_price(acct["aid"], "competitor_product", 777, 85.0)
    assert r["ok"] and len(rows(acct["aid"], 777)) == 1


# ---------------------------------------------------------------- ④ 通知通道（M5-4 的前半）
def test_pending_and_ack_semantics(acct):
    record_price(acct["aid"], "product", acct["pid"], 99.0)
    pend = pending_alerts(acct["aid"])
    assert len(pend) == 1 and pend[0]["acked_at"] is None
    aid_alert = pend[0]["id"]
    assert ack_alert(aid_alert, acct["aid"])["acked"] == 1
    assert pending_alerts(acct["aid"]) == [], "ack 之后不再待通知（避免重复弹）"
    assert ack_alert(aid_alert, acct["aid"])["acked"] == 0, "重复 ack 幂等、不改时间"
    assert ack_alert(aid_alert, 999999)["acked"] == 0, "别人的提醒 ack 不到"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
