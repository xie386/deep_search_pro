# -*- coding: utf-8 -*-
"""M5 补丁用例：删除台账记录 + summary 的 history_count。

判据来源：用户实测反馈 2（要"删除台账"按钮）+ 3（列表没点开前显示 0 条）。
规矩：删台账**必须重算最近价**，否则卡片挂着已删掉的价（数据自相矛盾）。

运行：.venv/Scripts/python.exe -m pytest tests/unit/test_price_delete.py -q
"""
import os
import sys
import time

import pytest
from fastapi import HTTPException

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from api import price_api as pa                                            # noqa: E402
from tools.price_ledger import record_price                                # noqa: E402
from tools.schema_personal import ensure_tables, get_personal_conn, purge_account   # noqa: E402

ensure_tables()


def mk_account(tag="d"):
    conn = get_personal_conn()
    try:
        aid = int(conn.execute("INSERT INTO accounts (username, password_hash, role) VALUES (?,?,?)",
                               ("m5%s_%d" % (tag, int(time.time() * 1000) % 1000000), "x", "user")).lastrowid)
        tok = "tk%s%d" % (tag, aid)
        conn.execute("INSERT INTO sessions (token, account_id, login_at) VALUES (?,?,datetime('now'))",
                     (tok, aid))
        conn.commit()
    finally:
        conn.close()
    return aid, tok


def mk_product(aid, name="真露烧酒葡萄风味", price=10.9):
    conn = get_personal_conn()
    try:
        pid = int(conn.execute("INSERT INTO products (owner_id, product_name, price, price_kind)"
                               " VALUES (?,?,?,'simple')", (aid, name, price)).lastrowid)
        conn.commit()
    finally:
        conn.close()
    return pid


def hist_ids(aid, pid, itype="product"):
    conn = get_personal_conn()
    try:
        return [r[0] for r in conn.execute(
            "SELECT id FROM price_history WHERE account_id=? AND item_type=? AND item_id IS ?"
            " ORDER BY id DESC", (aid, itype, pid))]
    finally:
        conn.close()


def last_price(aid, pid, tbl="products"):
    conn = get_personal_conn()
    try:
        return conn.execute("SELECT last_price FROM %s WHERE id=?" % tbl, (pid,)).fetchone()[0]
    finally:
        conn.close()


def test_delete_own_history_row():
    """删自己的台账 → ok + 剩余条数正确。"""
    aid, tok = mk_account()
    try:
        pid = mk_product(aid)
        for p in (200, 180, 90):
            pa.record(tok, pa.RecordReq(item_type="product", item_id=pid, price=p))
        hid = hist_ids(aid, pid)[0]
        out = pa.delete_history(tok, pa.DelHistReq(item_type="product", item_id=pid, history_id=hid))
        assert out["ok"] is True and out["deleted"] == hid
        assert out["remaining"] == 2
        assert len(hist_ids(aid, pid)) == 2
    finally:
        purge_account(aid)


def test_delete_recomputes_last_price():
    """★ 删掉最新一条后，商品的 last_price 必须回落到上一条（不能挂在已删的价上）。"""
    aid, tok = mk_account()
    try:
        pid = mk_product(aid)
        for p in (200, 180, 90):
            pa.record(tok, pa.RecordReq(item_type="product", item_id=pid, price=p))
        assert last_price(aid, pid) == pytest.approx(90.0)
        pa.delete_history(tok, pa.DelHistReq(item_type="product", item_id=pid,
                                             history_id=hist_ids(aid, pid)[0]))
        assert last_price(aid, pid) == pytest.approx(180.0)
    finally:
        purge_account(aid)


def test_delete_all_then_last_price_none():
    """删光全部 → last_price 归 None（卡片显示"—"，不是旧值）。"""
    aid, tok = mk_account()
    try:
        pid = mk_product(aid)
        pa.record(tok, pa.RecordReq(item_type="product", item_id=pid, price=90))
        pa.delete_history(tok, pa.DelHistReq(item_type="product", item_id=pid,
                                             history_id=hist_ids(aid, pid)[0]))
        assert last_price(aid, pid) is None
    finally:
        purge_account(aid)


def test_cannot_delete_other_accounts_row():
    """越权：别人的台账 id → 404，且**对方那条还在**。"""
    a1, _ = mk_account("x")
    a2, t2 = mk_account("y")
    try:
        p1 = mk_product(a1)
        pa.record("tkx%d" % a1, pa.RecordReq(item_type="product", item_id=p1, price=88))
        hid = hist_ids(a1, p1)[0]
        p2 = mk_product(a2, name="我的商品")
        with pytest.raises(HTTPException) as e:
            pa.delete_history(t2, pa.DelHistReq(item_type="product", item_id=p2, history_id=hid))
        assert e.value.status_code == 404
        assert len(hist_ids(a1, p1)) == 1          # ★ 对方数据没被动
    finally:
        purge_account(a1)
        purge_account(a2)


def test_cannot_touch_other_accounts_product():
    """别人的商品 id → 404（连商品都对不上，别想着删）。"""
    a1, _ = mk_account("x")
    a2, t2 = mk_account("y")
    try:
        p1 = mk_product(a1)
        pa.record("tkx%d" % a1, pa.RecordReq(item_type="product", item_id=p1, price=88))
        with pytest.raises(HTTPException) as e:
            pa.delete_history(t2, pa.DelHistReq(item_type="product", item_id=p1,
                                                history_id=hist_ids(a1, p1)[0]))
        assert e.value.status_code == 404
    finally:
        purge_account(a1)
        purge_account(a2)


def test_missing_history_id_404():
    """不存在的台账 id → 404（不静默成功）。"""
    aid, tok = mk_account()
    try:
        pid = mk_product(aid)
        with pytest.raises(HTTPException) as e:
            pa.delete_history(tok, pa.DelHistReq(item_type="product", item_id=pid, history_id=99999999))
        assert e.value.status_code == 404
    finally:
        purge_account(aid)


def test_bad_item_type_rejected():
    """item_type 只认 product / company_product。"""
    aid, tok = mk_account()
    try:
        pid = mk_product(aid)
        with pytest.raises(HTTPException) as e:
            pa.delete_history(tok, pa.DelHistReq(item_type="user", item_id=pid, history_id=1))
        assert e.value.status_code == 400
    finally:
        purge_account(aid)


def test_summary_reports_history_count():
    """★ 列表未展开也要能显示条数（实测：没点开前显示 0 条）。"""
    aid, tok = mk_account()
    try:
        pid = mk_product(aid)
        for p in (200, 180, 90):
            pa.record(tok, pa.RecordReq(item_type="product", item_id=pid, price=p))
        items = [x for x in pa.summary(tok)["items"] if x["item_id"] == pid]
        assert len(items) == 1
        assert items[0]["history_count"] == 3
        # 未记过价的商品也要出现在列表里（0 条），否则用户切不到它
        pid2 = mk_product(aid, name="刚建的商品")
        items2 = [x for x in pa.summary(tok)["items"] if x["item_id"] == pid2]
        assert len(items2) == 1 and items2[0]["history_count"] == 0
    finally:
        purge_account(aid)


def test_delete_then_summary_count_drops():
    """删一条 → summary 里的条数同步下降（不是缓存值）。"""
    aid, tok = mk_account()
    try:
        pid = mk_product(aid)
        for p in (200, 180):
            pa.record(tok, pa.RecordReq(item_type="product", item_id=pid, price=p))
        pa.delete_history(tok, pa.DelHistReq(item_type="product", item_id=pid,
                                             history_id=hist_ids(aid, pid)[0]))
        items = [x for x in pa.summary(tok)["items"] if x["item_id"] == pid]
        assert items[0]["history_count"] == 1
        assert items[0]["last_price"] == pytest.approx(200.0)
    finally:
        purge_account(aid)
