# -*- coding: utf-8 -*-
"""M5-3 用例：`record_price` 工具（模糊匹配 / 多候选 / 越权 / 友好失败）。

判据来源：M5 方案 §六 M5-3（≥8 项：命中/多候选/无候选/越权）+ §5.4（多候选要问用户，不许猜）。

运行：.venv/Scripts/python.exe -m pytest tests/test_price_tool.py -q
"""
import os
import sys
import time

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

from api import context as ctxmod                                     # noqa: E402
from tools.price_tool import record_price                             # noqa: E402
from tools.schema_personal import ensure_tables, get_personal_conn, purge_account   # noqa: E402

ensure_tables()


def mk_account(tag="m5t"):
    conn = get_personal_conn()
    try:
        aid = int(conn.execute("INSERT INTO accounts (username, password_hash, role) VALUES (?,?,?)",
                               ("m5t_%s_%d" % (tag, int(time.time() * 1000) % 1000000), "x", "user")).lastrowid)
        conn.commit()
    finally:
        conn.close()
    return aid


def mk_product(aid, name, target=None, brand="", tbl="products"):
    conn = get_personal_conn()
    try:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(%s)" % tbl)}
        f = ["owner_id", "product_name", "category", "price", "status"]
        v = [aid, name, "个护", 100.0, "active"]
        if "brand" in cols:
            f.append("brand"); v.append(brand)
        if "target_price" in cols and target is not None:
            f.append("target_price"); v.append(target)
        cur = conn.execute("INSERT INTO %s (%s) VALUES (%s)" % (tbl, ",".join(f), ",".join(["?"] * len(v))),
                           tuple(v))
        conn.commit()
        return int(cur.lastrowid)
    finally:
        conn.close()


def count_history(aid):
    conn = get_personal_conn()
    try:
        return conn.execute("SELECT COUNT(*) FROM price_history WHERE account_id=?", (aid,)).fetchone()[0]
    finally:
        conn.close()


def call(**kw):
    return record_price.invoke(kw)


@pytest.fixture()
def acct():
    aid = mk_account()
    ctxmod.set_owner_context(aid)
    yield {"aid": aid}
    ctxmod.set_owner_context(None)
    purge_account(aid)


# ---------------------------------------------------------------- ① 命中
def test_unique_name_hit_records(acct):
    pid = mk_product(acct["aid"], "米诺地尔泡沫剂", target=100.0)
    out = call(item_type="product", item_name="米诺地尔", price=89)
    assert "已记价" in out and "米诺地尔泡沫剂" in out and "89" in out
    assert "跌破目标价" in out, "跌破目标价要在返回里说清（模型据此告知用户）"
    assert count_history(acct["aid"]) == 1


def test_hit_by_id(acct):
    pid = mk_product(acct["aid"], "某耳机")
    out = call(item_type="product", item_id=pid, price=199)
    assert "已记价" in out and count_history(acct["aid"]) == 1


def test_company_product_hit(acct):
    mk_product(acct["aid"], "戒烟辅助贴", tbl="company_products")
    out = call(item_type="company_product", item_name="戒烟", price=15)
    assert "已记价" in out and "公司产品" in out


def test_delta_reported(acct):
    pid = mk_product(acct["aid"], "某耳机")
    call(item_type="product", item_id=pid, price=200)
    out = call(item_type="product", item_id=pid, price=180)
    assert "-10.0%" in out, "较上次跌幅要报出来"


# ---------------------------------------------------------------- ② 多候选 / 无候选（不许猜）
def test_multiple_candidates_ask_user_and_do_not_record(acct):
    mk_product(acct["aid"], "米诺地尔泡沫剂（蔓迪）")
    mk_product(acct["aid"], "米诺地尔喷雾剂（达霏欣）")
    out = call(item_type="product", item_name="米诺地尔", price=89)
    assert "先问用户" in out and "蔓迪" in out and "达霏欣" in out
    assert count_history(acct["aid"]) == 0, "★ 多候选时绝不能自己挑一个记下去"


def test_no_candidate_hints_and_does_not_record(acct):
    out = call(item_type="product", item_name="完全不存在的东西", price=10)
    assert "没找到" in out and count_history(acct["aid"]) == 0


def test_missing_name_and_id(acct):
    out = call(item_type="product", price=10)
    assert "请给 item_name" in out


# ---------------------------------------------------------------- ③ 越权与失败
def test_other_account_product_id_is_refused(acct):
    other = mk_account("other")
    try:
        opid = mk_product(other, "别人的耳机")
        out = call(item_type="product", item_id=opid, price=1)
        assert "没找到" in out
        assert count_history(acct["aid"]) == 0
    finally:
        purge_account(other)


def test_other_account_name_is_not_matched(acct):
    other = mk_account("other2")
    try:
        mk_product(other, "别人的米诺地尔")
        out = call(item_type="product", item_name="米诺地尔", price=1)
        assert "没找到" in out and count_history(acct["aid"]) == 0
    finally:
        purge_account(other)


@pytest.mark.parametrize("kw,expect", [
    # ★ 注意：LangChain 工具层会先用 pydantic 校验 price: float —— 传字符串根本进不到我的代码，
    #   所以这里验的是"进了代码之后"的边界（<=0）；非数字由 pydantic 拦，模型会收到校验错误。
    (dict(item_type="product", item_name="x", price=0), "price"),
    (dict(item_type="nope", item_name="x", price=1), "item_type 只能是"),
])
def test_friendly_failures(acct, kw, expect):
    assert expect in call(**kw)


def test_no_owner_context_is_friendly():
    ctxmod.set_owner_context(None)
    assert "没有账号上下文" in call(item_type="product", item_name="x", price=1)


def test_same_price_same_day_says_already_recorded(acct):
    pid = mk_product(acct["aid"], "某耳机")
    call(item_type="product", item_id=pid, price=150)
    out = call(item_type="product", item_id=pid, price=150)
    assert "已经记过" in out and count_history(acct["aid"]) == 1


# ---------------------------------------------------------------- ④ 接线
def test_tool_registered_at_both_assembly_points():
    s = open(os.path.join(ROOT, "api", "server.py"), encoding="utf-8").read().replace("\r\n", "\n")
    assert s.count("update_user_profile, record_price],") == 2, "两个装配点都要挂（M3/M4 老坑）"
    assert "record_price" in open(os.path.join(ROOT, "tools", "price_tool.py"), encoding="utf-8").read()


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
