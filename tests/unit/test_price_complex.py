# -*- coding: utf-8 -*-
"""Q6 用例：复杂价格规则（显式三态 / 显示唯一口径 / 数值链路跳过）。

判据来源：2026-09-29 拍板 —— 只在 `products`/`company_products` 加 `price_kind` + `price_text`；
**显式三态**（simple / rule / 未定价）；一处显示口径；数值链路（台账·涨跌·目标价·价差·新低）跳过 rule。

运行：.venv/Scripts/python.exe -m pytest tests/test_price_complex.py -q
"""
import io
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from api import context as ctxmod                                              # noqa: E402
from tools import price_display as pd                                          # noqa: E402
from tools.price_ledger import (_own_price, _target_of, competitor_comparison,  # noqa: E402
                                record_price)
from tools.price_tool import record_price as tool                              # noqa: E402
from tools.schema_personal import ensure_tables, get_personal_conn, purge_account   # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ensure_tables()

RULE_TEXT = "输入 ¥2/百万 token · 输出 ¥8/百万 token（缓存命中 ¥0.5）"
RENT_TEXT = "月租 ¥3500（租满 12 个月）· 提前退租 +¥500/月"


def mk_account():
    conn = get_personal_conn()
    try:
        aid = int(conn.execute("INSERT INTO accounts (username,password_hash,role) VALUES (?,?,?)",
                               ("q6_%d" % (int(time.time() * 1000) % 1000000), "x", "user")).lastrowid)
        conn.commit()
    finally:
        conn.close()
    return aid


def mk(aid, name="普通商品", price=100.0, kind=None, text=None, tbl="products"):
    conn = get_personal_conn()
    try:
        cur = conn.execute("INSERT INTO %s (owner_id, product_name, price, price_kind, price_text,"
                           " status) VALUES (?,?,?,?,?,?)" % tbl, (aid, name, price, kind, text, "active"))
        conn.commit()
        return int(cur.lastrowid)
    finally:
        conn.close()


def row_of(pid, tbl="products"):
    conn = get_personal_conn()
    try:
        return conn.execute("SELECT * FROM %s WHERE id=?" % tbl, (pid,)).fetchone()
    finally:
        conn.close()


# ---------------------------------------------------------------- ① 列与三态
def test_columns_exist_in_both_tables():
    conn = get_personal_conn()
    try:
        for t in ("products", "company_products"):
            cols = {r[1] for r in conn.execute("PRAGMA table_info(%s)" % t)}
            assert {"price_kind", "price_text"} <= cols, t + " 缺列"
    finally:
        conn.close()


def test_three_states_are_distinguishable():
    assert pd.price_kind_of({"price": 10.9, "price_kind": "simple"}) == "simple"
    assert pd.price_kind_of({"price": None, "price_kind": "rule", "price_text": RULE_TEXT}) == "rule"
    # ★ 关键：未定价（两列皆空）与复杂价**必须分得开** —— 这正是"不用数字为空隐式表示复杂价"的理由
    assert pd.price_kind_of({"price": None, "price_kind": None}) == ""


def test_display_price_three_states():
    assert pd.display_price({"price": 10.9, "price_kind": "simple"})["text"] == "10.9 元"
    assert pd.display_price({"price": None, "price_kind": "rule", "price_text": RULE_TEXT})["text"] == RULE_TEXT
    assert pd.display_price({"price": None, "price_kind": None})["text"] == pd.UNPRICED_TEXT
    # 规则列空着也不能显示成"未定价"
    assert "按规则" in pd.display_price({"price": None, "price_kind": "rule"})["text"]


def test_is_numeric_priced_gate():
    assert pd.is_numeric_priced({"price": 10.9, "price_kind": "simple"}) is True
    assert pd.is_numeric_priced({"price": None, "price_kind": "rule"}) is False
    assert pd.is_numeric_priced({"price": None, "price_kind": None}) is False


def test_backfill_marks_legacy_rows_simple():
    """老数据（有价、没写 kind）跑一次 ensure_tables → 应被回填成 simple，而不是留 NULL。"""
    aid = mk_account()
    try:
        pid = mk(aid, "老数据商品", price=88.0)
        conn = get_personal_conn()
        try:
            conn.execute("UPDATE products SET price_kind=NULL WHERE id=?", (pid,))
            conn.commit()
        finally:
            conn.close()
        ensure_tables()
        assert row_of(pid)["price_kind"] == "simple"
    finally:
        purge_account(aid)


# ---------------------------------------------------------------- ② 数值链路必须跳过 rule
def test_record_price_refuses_rule_item():
    aid = mk_account()
    try:
        pid = mk(aid, "DeepSeek API", price=None, kind="rule", text=RULE_TEXT)
        try:
            record_price(aid, "product", pid, 2.0)
            raise AssertionError("规则计价商品不该被记进台账")
        except ValueError as e:
            assert "按规则计价" in str(e) and RULE_TEXT in str(e)
        conn = get_personal_conn()
        try:
            n = conn.execute("SELECT COUNT(*) FROM price_history WHERE account_id=? AND item_id=?",
                             (aid, pid)).fetchone()[0]
        finally:
            conn.close()
        assert n == 0, "★ 拒绝时不能留下半条台账"
    finally:
        purge_account(aid)


def test_tool_refuses_rule_item_with_human_text():
    aid = mk_account()
    try:
        mk(aid, "DeepSeek API", price=None, kind="rule", text=RULE_TEXT)
        ctxmod.set_owner_context(aid)
        out = tool.invoke({"item_type": "product", "item_name": "DeepSeek", "price": 2})
        ctxmod.set_owner_context(None)
        assert "按规则计价" in out and "具体渠道" in out, out
    finally:
        purge_account(aid)


def test_unpriced_item_can_still_be_recorded():
    """★ 反向守卫：**未定价**（两列皆空）是最常见的场景（刚登记），必须照样能记价。"""
    aid = mk_account()
    try:
        pid = mk(aid, "刚登记的商品", price=None, kind=None)
        r = record_price(aid, "product", pid, 59.0)
        assert r["ok"] and r["price"] if "price" in r else r["ok"]
        conn = get_personal_conn()
        try:
            assert conn.execute("SELECT price FROM price_history WHERE id=?", (r["row_id"],)).fetchone()[0] == 59.0
        finally:
            conn.close()
    finally:
        purge_account(aid)


def test_own_price_and_target_skip_rule_items():
    aid = mk_account()
    try:
        pid = mk(aid, "规则价公司产品", price=None, kind="rule", text=RENT_TEXT, tbl="company_products")
        conn = get_personal_conn()
        try:
            cur = conn.cursor()
            conn.execute("UPDATE company_products SET target_price=3000 WHERE id=?", (pid,))
            conn.commit()
            assert _own_price(cur, aid, pid) is None, "★ 规则计价没有单一数值价"
            assert _target_of(cur, aid, "company_product", pid) is None, "★ 也不该有'跌破目标价'"
        finally:
            conn.close()
    finally:
        purge_account(aid)


def test_competitor_gap_skips_rule_priced_own_product():
    aid = mk_account()
    try:
        cid = mk(aid, "某竞品", price=None, kind=None, tbl="company_competitors") if False else None
        conn = get_personal_conn()
        try:
            cur = conn.cursor()
            pid = mk(aid, "规则价自家产品", price=None, kind="rule", text=RENT_TEXT, tbl="company_products")
            cur.execute("INSERT INTO company_competitors (owner_id, comp_name, is_competitor,"
                        " mapped_product_ids) VALUES (?,?,?,?)", (aid, "某竞品", 1, str([pid])))
            cid = int(cur.lastrowid)
            conn.commit()
        finally:
            conn.close()
        record_price(aid, "competitor_product", cid, 50.0)
        assert competitor_comparison(aid) == [], "★ 自家产品是规则计价 → 价差无从算起，不能炸也不能瞎算"
    finally:
        purge_account(aid)


def test_summary_marks_rule_and_hides_numeric_fields():
    """接口级：真调 summary()，规则计价商品必须带 price_kind/price_show 且**不出数值字段**。"""
    aid = mk_account()
    try:
        mk(aid, "DeepSeek API", price=None, kind="rule", text=RULE_TEXT)
        conn = get_personal_conn()
        try:
            tok = "q6tok%d" % (int(time.time() * 1000) % 1000000)
            cols = {r[1] for r in conn.execute("PRAGMA table_info(sessions)")}
            assert {"token", "account_id"} <= cols, "sessions 表结构变了，用例要跟着改"
            conn.execute("INSERT INTO sessions (token, account_id, login_at) VALUES (?,?,?)",
                           (tok, aid, time.strftime("%Y-%m-%d %H:%M:%S")))
            conn.commit()
        finally:
            conn.close()
        import api.price_api as pa
        out = pa.summary(tok)
        mine = [x for x in out["items"] if x["name"] == "DeepSeek API"]
        assert mine, "规则计价商品应当出现在价格汇总里（不能被当成没价而跳过）"
        it = mine[0]
        assert it["price_kind"] == "rule" and it["price_show"] == RULE_TEXT
        assert it["last_price"] is None and it["target_price"] is None and it["delta_pct"] == 0.0
        assert it["below_target"] is False
    finally:
        purge_account(aid)


def test_display_is_single_source_of_truth():
    """静态反证：除 `price_display.py` 外，不许有人自己拿 price_text 当数字或自判 kind。"""
    bad = []
    for f in ("tools/price_ledger.py", "tools/price_tool.py", "api/price_api.py"):
        src = io.open(os.path.join(ROOT, f), encoding="utf-8").read().replace("\r\n", "\n")
        for n, line in enumerate(src.split("\n"), 1):
            if "float(price_text" in line or "float(row[\"price_text\"]" in line:
                bad.append("%s:%d" % (f, n))
    assert not bad, "不许把规则文本当数字：" + ", ".join(bad)


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q"]))

# ---------------------------------------------------------------- ⑥ 新增表单的复杂价（2026-09-29 实测补）
def test_create_endpoints_accept_complex_price():
    """用户实测报的 Bug 2：**新增表单**没有复杂定价。这里钉住创建接口的三态。"""
    from api import me_user_data as mu
    aid = mk_account()
    conn = get_personal_conn()
    try:
        tok = "cx%d" % (int(time.time() * 1000) % 1000000)
        conn.execute("INSERT INTO sessions (token, account_id, login_at) VALUES (?,?,?)",
                     (tok, aid, time.strftime("%Y-%m-%d %H:%M:%S")))
        conn.commit()
    finally:
        conn.close()
    try:
        r = mu.pproducts_add(mu.PersonalProductReq(product_name="按量计费服务", price_kind="rule",
                                                    price_text="输入 ¥2/百万 token"), tok)
        row = row_of(r["id"])
        assert row["price"] is None and row["price_kind"] == "rule" and "百万 token" in row["price_text"]
        # ★ 只改名（不带价格字段）不得抹掉规则价 —— 这是"前端没提价格"时必须保持不动的语义
        mu.pproducts_update(r["id"], mu.PersonalProductReq(product_name="按量计费服务（改名）"), tok)
        row2 = row_of(r["id"])
        assert row2["price_kind"] == "rule" and row2["price_text"] == row["price_text"], "★ 改名不该抹掉规则价"
        # 切回数值价：数值列有值、文本清空
        mu.pproducts_update(r["id"], mu.PersonalProductReq(product_name="按量计费服务", price=2.5,
                                                           price_kind="simple"), tok)
        row3 = row_of(r["id"])
        assert row3["price"] == 2.5 and row3["price_kind"] == "simple" and row3["price_text"] is None
    finally:
        purge_account(aid)


def test_create_endpoints_reject_rule_without_text():
    from api import me_user_data as mu
    aid = mk_account()
    conn = get_personal_conn()
    try:
        tok = "cx2%d" % (int(time.time() * 1000) % 1000000)
        conn.execute("INSERT INTO sessions (token, account_id, login_at) VALUES (?,?,?)",
                     (tok, aid, time.strftime("%Y-%m-%d %H:%M:%S")))
        conn.commit()
    finally:
        conn.close()
    try:
        import pytest as _pytest
        with _pytest.raises(Exception):
            mu.pproducts_add(mu.PersonalProductReq(product_name="坏数据", price_kind="rule"), tok)
    finally:
        purge_account(aid)
