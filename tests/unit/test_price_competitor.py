# -*- coding: utf-8 -*-
"""M5-7 用例：竞品对比（关联 / 价差 / 提醒节流）。

判据来源：M5 方案 §六 M5-7（≥6 项）+ §3.3（阈值默认 10%）+ G5 + D5（竞品价差提醒每 7 天最多一次）。

运行：.venv/Scripts/python.exe -m pytest tests/test_price_competitor.py -q
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from tools.price_ledger import (RULE_COMPETITOR_GAP, _parse_ids, competitor_comparison,   # noqa: E402
                                record_price)
from tools.schema_personal import ensure_tables, get_personal_conn, purge_account   # noqa: E402

ensure_tables()


def mk_account():
    conn = get_personal_conn()
    try:
        aid = int(conn.execute("INSERT INTO accounts (username, password_hash, role) VALUES (?,?,?)",
                               ("m5c_%d" % (int(time.time() * 1000) % 1000000), "x", "user")).lastrowid)
        conn.commit()
    finally:
        conn.close()
    return aid


def mk_own(aid, name="我方戒烟贴", price=100.0):
    conn = get_personal_conn()
    try:
        cur = conn.execute("INSERT INTO company_products (owner_id, product_name, category, price, status)"
                           " VALUES (?,?,?,?,?)", (aid, name, "个护", price, "active"))
        conn.commit()
        return int(cur.lastrowid)
    finally:
        conn.close()


def mk_comp(aid, name="某竞品", mapped=None):
    conn = get_personal_conn()
    try:
        cur = conn.execute("INSERT INTO company_competitors (owner_id, comp_name, is_competitor,"
                           " mapped_product_ids) VALUES (?,?,?,?)",
                           (aid, name, 1, None if mapped is None else str(mapped)))
        conn.commit()
        return int(cur.lastrowid)
    finally:
        conn.close()


def gap_alerts(aid):
    conn = get_personal_conn()
    try:
        return list(conn.execute("SELECT id, detail, fired_at FROM price_alerts WHERE account_id=?"
                                 " AND rule=? ORDER BY id", (aid, RULE_COMPETITOR_GAP)))
    finally:
        conn.close()


# ---------------------------------------------------------------- ① 关联解析（容忍三种写法）
def test_parse_ids_tolerates_formats():
    assert _parse_ids("[1, 2]") == [1, 2]
    assert _parse_ids("3,4") == [3, 4]
    assert _parse_ids("5") == [5]
    assert _parse_ids(None) == [] and _parse_ids("") == []


# ---------------------------------------------------------------- ② 价差视图（G5）
def test_comparison_computes_gap():
    aid = mk_account()
    try:
        pid = mk_own(aid, price=100.0)
        cid = mk_comp(aid, mapped=[pid])
        record_price(aid, "competitor_product", cid, 85.0)
        got = competitor_comparison(aid)
        assert len(got) == 1 and got[0]["gap_pct"] == -15.0
        assert got[0]["competitor_name"] == "某竞品" and got[0]["own_product_name"] == "我方戒烟贴"
    finally:
        purge_account(aid)


def test_comparison_skips_when_either_side_has_no_price():
    aid = mk_account()
    try:
        pid = mk_own(aid, price=100.0)
        cid = mk_comp(aid, mapped=[pid])
        assert competitor_comparison(aid) == [], "竞品还没记价 → 不显示（也不报错）"
        record_price(aid, "competitor_product", cid, 90.0)
        got = competitor_comparison(aid)
        assert got and got[0]["competitor_price"] == 90.0
    finally:
        purge_account(aid)


def test_comparison_is_account_scoped():
    a, b = mk_account(), mk_account()
    try:
        pid = mk_own(a, price=100.0)
        cid = mk_comp(a, mapped=[pid])
        record_price(a, "competitor_product", cid, 80.0)
        assert competitor_comparison(b) == [], "★ 别人的竞品对比不该出现"
    finally:
        purge_account(a); purge_account(b)


# ---------------------------------------------------------------- ③ 提醒与阈值 / 节流（D5）
def test_gap_over_threshold_alerts():
    aid = mk_account()
    try:
        pid = mk_own(aid, price=100.0)
        cid = mk_comp(aid, mapped=[pid])
        r = record_price(aid, "competitor_product", cid, 85.0)      # 低 15% > 10%
        assert r["alert"] == RULE_COMPETITOR_GAP
        assert len(gap_alerts(aid)) == 1 and "低 15.0%" in gap_alerts(aid)[0][1]
    finally:
        purge_account(aid)


def test_gap_within_threshold_does_not_alert():
    aid = mk_account()
    try:
        pid = mk_own(aid, price=100.0)
        cid = mk_comp(aid, mapped=[pid])
        r = record_price(aid, "competitor_product", cid, 95.0)      # 只低 5%，不到 10%
        assert r["alert"] == "" and gap_alerts(aid) == []
    finally:
        purge_account(aid)


def test_gap_alert_throttled_within_seven_days():
    aid = mk_account()
    try:
        pid = mk_own(aid, price=100.0)
        cid = mk_comp(aid, mapped=[pid])
        assert record_price(aid, "competitor_product", cid, 85.0)["alert"] == RULE_COMPETITOR_GAP
        # 当天/几天内再记一个更低的价格 → 不再提醒（D5：每 7 天最多一次）
        record_price(aid, "competitor_product", cid, 80.0, observed_at=time.strftime("%Y-%m-%d"))
        # ★ 注意：80 比 85 低 → 它同时创了"竞品自己的新低"（new_low 是合理的），
        #   要验的是**价差提醒被节流**，所以查条数而不是查单值返回字段。
        assert len(gap_alerts(aid)) == 1, "★ D5：7 天内不重复提醒价差"
    finally:
        purge_account(aid)


def test_gap_alert_allowed_after_seven_days():
    aid = mk_account()
    try:
        pid = mk_own(aid, price=100.0)
        cid = mk_comp(aid, mapped=[pid])
        record_price(aid, "competitor_product", cid, 85.0)
        # 把已有提醒的时间改到 8 天前 → 应当可以再提醒
        conn = get_personal_conn()
        try:
            old = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(time.time() - 8 * 86400))
            conn.execute("UPDATE price_alerts SET fired_at=? WHERE account_id=? AND rule=?",
                         (old, aid, RULE_COMPETITOR_GAP))
            conn.commit()
        finally:
            conn.close()
        record_price(aid, "competitor_product", cid, 80.0)
        assert len(gap_alerts(aid)) == 2, "过了 7 天应当可以再提醒一次价差"
    finally:
        purge_account(aid)


def test_unmapped_competitor_neither_alerts_nor_crashes():
    aid = mk_account()
    try:
        cid = mk_comp(aid, name="没关联的竞品", mapped=None)
        r = record_price(aid, "competitor_product", cid, 1.0)
        assert r["ok"] and r["alert"] == "" and gap_alerts(aid) == [], "★ 没关联自有产品就不提醒（也绝不猜）"
    finally:
        purge_account(aid)


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q"]))
