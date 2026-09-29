# -*- coding: utf-8 -*-
"""①③⑥⑦⑨⑩ 用例（2026-09-29 拍板后的语义修正）。

- ① 目标价"后设"：首次跌破判据 =「上一次记价在目标价哪一侧」，历史老数据不再吃掉提醒
- ③ 首次记价基准三级回退（台账 → last_price → 登记价）
- ⑥⑦ 工具返回文案（无历史可比 / 较登记价 / 上次→本次）
- ⑨ 竞品进对话记价（否则 M5-7 从界面走不到）
- ⑩ 0 匹配时给"最相近的几条"（仍不猜）

运行：.venv/Scripts/python.exe -m pytest tests/test_price_semantics.py -q
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from api import context as ctxmod                                          # noqa: E402
from tools.price_ledger import (RULE_BELOW_TARGET, RULE_DROP_PCT, RULE_NEW_LOW,   # noqa: E402
                                record_price)
from tools.price_tool import _TABLES, record_price as tool                  # noqa: E402
from tools.schema_personal import ensure_tables, get_personal_conn, purge_account   # noqa: E402

ensure_tables()


def mk_account():
    conn = get_personal_conn()
    try:
        aid = int(conn.execute("INSERT INTO accounts (username,password_hash,role) VALUES (?,?,?)",
                               ("sem_%d" % (int(time.time() * 1000) % 1000000), "x", "user")).lastrowid)
        conn.commit()
    finally:
        conn.close()
    return aid


def mk(aid, name="米诺地尔泡沫剂", price=None, tbl="products", target=None, **kw):
    conn = get_personal_conn()
    try:
        nc = "product_name" if tbl != "company_competitors" else "comp_name"
        cols, vals = ["owner_id", nc], [aid, name]
        if tbl != "company_competitors":
            cols += ["price", "status", "target_price"]
            vals += [price, "active", target]
        for k, v in kw.items():
            cols.append(k)
            vals.append(v)
        cur = conn.execute("INSERT INTO %s (%s) VALUES (%s)"
                           % (tbl, ",".join(cols), ",".join("?" * len(cols))), tuple(vals))
        conn.commit()
        return int(cur.lastrowid)
    finally:
        conn.close()


def alerts(aid):
    conn = get_personal_conn()
    try:
        return [tuple(r) for r in conn.execute(
            "SELECT rule, price, detail FROM price_alerts WHERE account_id=? ORDER BY id", (aid,))]
    finally:
        conn.close()


# ---------------------------------------------------------------- ① 目标价后设
def test_historical_low_does_not_eat_a_later_target():
    """★ 这就是 M5-9 实测踩到的场景：台账里早有一条更低价 → 新设目标价后**仍然要能提醒**。"""
    aid = mk_account()
    try:
        pid = mk(aid, price=10.9)
        record_price(aid, "product", pid, 10.9)                 # 老数据（远低于后来的目标价）
        conn = get_personal_conn()
        try:
            conn.execute("UPDATE products SET target_price=100 WHERE id=?", (pid,))
            conn.commit()
        finally:
            conn.close()
        assert record_price(aid, "product", pid, 200)["alert"] == ""      # 在目标价之上
        assert record_price(aid, "product", pid, 180)["alert"] == ""      # 仍在之上
        r = record_price(aid, "product", pid, 90)                          # 跨到线下 → 必须提醒
        assert r["alert"] == RULE_BELOW_TARGET, alerts(aid)
        assert any("首次跌破目标价 100" in a[2] for a in alerts(aid))
    finally:
        purge_account(aid)


def test_first_record_below_target_alerts():
    aid = mk_account()
    try:
        pid = mk(aid, price=None, target=100)
        assert record_price(aid, "product", pid, 88)["alert"] == RULE_BELOW_TARGET
    finally:
        purge_account(aid)


def test_first_record_above_target_silent():
    aid = mk_account()
    try:
        pid = mk(aid, price=None, target=100)
        assert record_price(aid, "product", pid, 128)["alert"] == ""
        assert alerts(aid) == []
    finally:
        purge_account(aid)


def test_below_target_only_alerts_once_per_crossing():
    aid = mk_account()
    try:
        pid = mk(aid, price=None, target=100)
        assert record_price(aid, "product", pid, 90)["alert"] == RULE_BELOW_TARGET
        # 85 比 90 低 → 它同时创了新低（new_low 是合理的）；要验的是**跌破目标价不再重复提醒**
        record_price(aid, "product", pid, 85)
        n = len([a for a in alerts(aid) if a[0] == RULE_BELOW_TARGET])
        assert n == 1, alerts(aid)
    finally:
        purge_account(aid)


def test_new_low_still_needs_history():
    """新低语义**没变**：首条不算新低（登记价不是"历史观测"）。"""
    aid = mk_account()
    try:
        pid = mk(aid, price=120)
        assert record_price(aid, "product", pid, 89)["alert"] == ""
        assert record_price(aid, "product", pid, 80)["alert"] == RULE_NEW_LOW
    finally:
        purge_account(aid)


# ---------------------------------------------------------------- ③ 首次基准三级回退
def test_first_record_uses_registered_price_as_baseline():
    aid = mk_account()
    try:
        pid = mk(aid, price=120)
        r = record_price(aid, "product", pid, 90)
        assert r["delta_base_src"] == "registered" and r["delta_base"] == 120.0
        assert r["delta_pct"] == -25.0, r
        assert r["prev_price"] is None
    finally:
        purge_account(aid)


def test_second_record_prefers_ledger_baseline():
    aid = mk_account()
    try:
        pid = mk(aid, price=120)
        record_price(aid, "product", pid, 90)
        r = record_price(aid, "product", pid, 80)
        assert r["delta_base_src"] == "ledger" and r["prev_price"] == 90.0
        assert r["delta_pct"] == round((80 - 90) / 90 * 100, 2)
    finally:
        purge_account(aid)


def test_no_baseline_at_all_reports_zero():
    aid = mk_account()
    try:
        pid = mk(aid, price=None)
        r = record_price(aid, "product", pid, 59)
        assert r["delta_base"] is None and r["delta_pct"] == 0.0 and r["prev_price"] is None
    finally:
        purge_account(aid)


# ---------------------------------------------------------------- ⑥⑦ 工具返回文案
def test_tool_text_first_record_no_history():
    aid = mk_account()
    try:
        mk(aid, name="无价商品", price=None)
        ctxmod.set_owner_context(aid)
        out = tool.invoke({"item_type": "product", "item_name": "无价", "price": 59})
        ctxmod.set_owner_context(None)
        assert "暂无历史可比" in out, out
    finally:
        purge_account(aid)


def test_tool_text_registered_baseline():
    aid = mk_account()
    try:
        mk(aid, name="有登记价的商品", price=120)
        ctxmod.set_owner_context(aid)
        out = tool.invoke({"item_type": "product", "item_name": "有登记价", "price": 90})
        ctxmod.set_owner_context(None)
        assert "较登记价 120.0 元 → 本次 90.0 元" in out and "-25.0%" in out, out
    finally:
        purge_account(aid)


def test_tool_text_prev_to_now():
    aid = mk_account()
    try:
        pid = mk(aid, name="两次记价商品", price=100)
        ctxmod.set_owner_context(aid)
        tool.invoke({"item_type": "product", "item_name": "两次记价", "price": 100})
        out = tool.invoke({"item_type": "product", "item_name": "两次记价", "price": 80})
        ctxmod.set_owner_context(None)
        assert "上次 100.0 元 → 本次 80.0 元" in out, out
    finally:
        purge_account(aid)


# ---------------------------------------------------------------- ⑨ 竞品进对话记价
def test_competitor_is_now_recordable_from_chat():
    aid = mk_account()
    try:
        cid = mk(aid, name="至本洗面奶", tbl="company_competitors")
        assert "competitor_product" in _TABLES
        ctxmod.set_owner_context(aid)
        out = tool.invoke({"item_type": "competitor_product", "item_name": "至本", "price": 39})
        ctxmod.set_owner_context(None)
        assert out.startswith("已记价") and "竞品" in out, out
        conn = get_personal_conn()
        try:
            row = conn.execute("SELECT item_type, item_id, price FROM price_history WHERE account_id=?"
                               " ORDER BY id DESC LIMIT 1", (aid,)).fetchone()
            assert row[0] == "competitor_product" and row[1] == cid and row[2] == 39.0
        finally:
            conn.close()
    finally:
        purge_account(aid)


def test_competitor_multi_candidate_asks_user():
    aid = mk_account()
    try:
        mk(aid, name="至本洗面奶", tbl="company_competitors")
        mk(aid, name="至本面霜", tbl="company_competitors")
        ctxmod.set_owner_context(aid)
        out = tool.invoke({"item_type": "competitor_product", "item_name": "至本", "price": 39})
        ctxmod.set_owner_context(None)
        assert "先问用户是哪一个" in out and "已记价" not in out, out
    finally:
        purge_account(aid)


# ---------------------------------------------------------------- ⑩ 0 匹配给最相近的
def test_zero_match_lists_nearest_but_records_nothing():
    aid = mk_account()
    try:
        mk(aid, name="米诺地尔泡沫剂", price=120)
        ctxmod.set_owner_context(aid)
        out = tool.invoke({"item_type": "product", "item_name": "米诺地尔喷雾", "price": 88})
        ctxmod.set_owner_context(None)
        assert "没找到" in out and "最相近的几条" in out and "米诺地尔泡沫剂" in out, out
        conn = get_personal_conn()
        try:
            assert conn.execute("SELECT COUNT(*) FROM price_history WHERE account_id=?",
                                (aid,)).fetchone()[0] == 0, "★ 给相近候选时一个字都不许写进台账"
        finally:
            conn.close()
    finally:
        purge_account(aid)


def test_zero_match_unrelated_gives_no_nearest():
    aid = mk_account()
    try:
        mk(aid, name="米诺地尔泡沫剂", price=120)
        ctxmod.set_owner_context(aid)
        out = tool.invoke({"item_type": "product", "item_name": "笔记本电脑", "price": 5000})
        ctxmod.set_owner_context(None)
        assert "最相近的几条" not in out, out
    finally:
        purge_account(aid)


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q"]))

# ---------------------------------------------------------------- ② 跌幅提醒（alert_drop_pct）
def test_drop_pct_alerts_when_exceeded():
    aid = mk_account()
    try:
        pid = mk(aid, price=100.0, alert_drop_pct=10)
        record_price(aid, "product", pid, 100)
        record_price(aid, "product", pid, 85)                     # 单次 -15%，超 10% 阈值
        # 85 比 100 低 → 同时创了新低（返回字段按拍板口径只报第一条 = new_low）；
        # 要验的是**跌幅提醒本身也落了库**（三条规则各自独立落 price_alerts）
        got = [a for a in alerts(aid) if a[0] == RULE_DROP_PCT]
        assert got and "下跌 15.0%" in got[0][2] and "阈值 10.0%" in got[0][2], alerts(aid)
    finally:
        purge_account(aid)


def test_drop_pct_silent_within_limit():
    aid = mk_account()
    try:
        pid = mk(aid, price=100.0, alert_drop_pct=10)
        record_price(aid, "product", pid, 100)
        record_price(aid, "product", pid, 95)                     # 只跌 5%
        assert [a for a in alerts(aid) if a[0] == RULE_DROP_PCT] == []
    finally:
        purge_account(aid)


def test_drop_pct_ignores_rises():
    aid = mk_account()
    try:
        pid = mk(aid, price=100.0, alert_drop_pct=10)
        record_price(aid, "product", pid, 100)
        record_price(aid, "product", pid, 130)                    # 涨 → 不该有跌幅提醒
        assert [a for a in alerts(aid) if a[0] == RULE_DROP_PCT] == []
    finally:
        purge_account(aid)


def test_drop_pct_disabled_when_null():
    aid = mk_account()
    try:
        pid = mk(aid, price=100.0)                                # alert_drop_pct 为 NULL
        record_price(aid, "product", pid, 100)
        record_price(aid, "product", pid, 20)
        assert [a for a in alerts(aid) if a[0] == RULE_DROP_PCT] == []
    finally:
        purge_account(aid)


def test_drop_pct_needs_previous_observation():
    """★ 语义：跌幅是"两个观测之间的差"；**首条记录**没有上一次观测 → 不该报跌幅。
    （"较登记价低了 25%"属于 ⑥ 的文案，不是"跌了"这个事件）"""
    aid = mk_account()
    try:
        pid = mk(aid, price=100.0, alert_drop_pct=10)
        record_price(aid, "product", pid, 50)
        assert [a for a in alerts(aid) if a[0] == RULE_DROP_PCT] == [], alerts(aid)
    finally:
        purge_account(aid)


def test_multiple_rules_all_land_field_reports_first():
    """拍板口径：三条规则**各自独立落库**，返回字段只报**第一条**（顺序 new_low → below_target → drop_pct）。"""
    aid = mk_account()
    try:
        pid = mk(aid, price=100.0, target=90, alert_drop_pct=10)
        record_price(aid, "product", pid, 100)
        r = record_price(aid, "product", pid, 70)                 # 既创新低、又跌破目标价、又跌超阈值
        assert r["alert"] == RULE_NEW_LOW, r
        rules = {a[0] for a in alerts(aid)}
        assert {RULE_NEW_LOW, RULE_BELOW_TARGET, RULE_DROP_PCT} <= rules, rules
    finally:
        purge_account(aid)


def test_drop_pct_skips_rule_priced_item():
    aid = mk_account()
    try:
        pid = mk(aid, name="按量计费服务", price=None, price_kind="rule",
                 price_text="输入 ¥2/百万token", alert_drop_pct=10)
        try:
            record_price(aid, "product", pid, 5)
            raise AssertionError("规则计价商品不该被记价")
        except ValueError as e:
            assert "按规则计价" in str(e)
        assert [a for a in alerts(aid) if a[0] == RULE_DROP_PCT] == []
    finally:
        purge_account(aid)


# ------------------------------------------- 用户实测反馈（2026-09-29 晚）：记价 ≠ 只能创新低

def test_price_above_history_min_still_records():
    """★ 200 → 2 → 7：7 高于历史最低 2，**照样必须入台账**（今天没记过 7）。

    用户实测直觉："只有填更低价才生效"。真相：`record_price` 从来不挑价格方向，
    他没看到变化是因为**同一天同一价格去重**（今天已经记过 7）。这条把它钉死。
    """
    aid = mk_account()
    try:
        pid = mk(aid, price=200)
        record_price(aid, "product", pid, 200)
        record_price(aid, "product", pid, 2)
        out = record_price(aid, "product", pid, 7)
        assert out["ok"] is True and not out.get("deduped")
        conn = get_personal_conn()
        try:
            rows = [float(r[0]) for r in conn.execute(
                "SELECT price FROM price_history WHERE account_id=? AND item_type='product'"
                " AND item_id IS ? ORDER BY id", (aid, pid))]
            last = conn.execute("SELECT last_price FROM products WHERE id=?", (pid,)).fetchone()[0]
        finally:
            conn.close()
        assert rows == [200.0, 2.0, 7.0]          # ★ 涨价也进台账
        assert float(last) == 7.0                 # 最近价跟着走
        assert out["alert"] == ""                 # 7 不是新低、也没跌破目标价 → 不提醒
    finally:
        purge_account(aid)


def test_same_day_same_price_is_deduped_not_silent():
    """同一天同一价格 → 去重（不新增行），且返回 `deduped` + 人话 reason（前端据此如实提示）。"""
    aid = mk_account()
    try:
        pid = mk(aid, price=200)
        first = record_price(aid, "product", pid, 7)
        second = record_price(aid, "product", pid, 7)
        assert first.get("deduped") in (None, False)
        assert second["deduped"] is True and second["row_id"] == first["row_id"]
        assert "同一天" in (second.get("reason") or "")
        conn = get_personal_conn()
        try:
            n = conn.execute("SELECT COUNT(*) FROM price_history WHERE account_id=? AND item_id IS ?",
                             (aid, pid)).fetchone()[0]
        finally:
            conn.close()
        assert n == 1                             # ★ 只有一条，不是两条
    finally:
        purge_account(aid)


def test_new_low_text_points_at_history_min():
    """新低提示要写明"此前最低"（判据是历史最低，不是上一次）。"""
    aid = mk_account()
    try:
        pid = mk(aid, price=200)
        record_price(aid, "product", pid, 200)
        record_price(aid, "product", pid, 9)
        record_price(aid, "product", pid, 5)      # 5 < 9 且 < 200 → 新低
        hit = [a for a in alerts(aid) if a[0] == RULE_NEW_LOW]
        assert hit, "应当有 new_low 提醒"
        assert "此前最低" in hit[-1][2] and "9" in hit[-1][2].replace("此前最低 9", "9")
    finally:
        purge_account(aid)
