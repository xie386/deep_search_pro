# -*- coding: utf-8 -*-
"""M5-4 用例：提醒通道（周报附录 + 待通知 + 回执 + 不重复弹）。

判据来源：M5 方案 §六 M5-4（≥10 项）+ §3.5（后端只落库 → 取走 → 回执）+ D5（去重）。

运行：.venv/Scripts/python.exe -m pytest tests/test_price_alerts.py -q
"""
import os
import re
import sys
import time

import pytest
from fastapi import HTTPException

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from api.price_api import AckReq, ack, alerts                     # noqa: E402
from tools.price_ledger import RULE_NEW_LOW, digest_appendix, record_price   # noqa: E402
from tools.schema_personal import ensure_tables, get_personal_conn, purge_account   # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ensure_tables()


def mk_account():
    conn = get_personal_conn()
    try:
        aid = int(conn.execute("INSERT INTO accounts (username, password_hash, role) VALUES (?,?,?)",
                               ("m5a_%d" % (int(time.time() * 1000) % 1000000), "x", "user")).lastrowid)
        token = "tk_%d_%d" % (aid, int(time.time() * 1000) % 1000000)
        conn.execute("INSERT INTO sessions (token, account_id, login_at) VALUES (?,?,?)",
                     (token, aid, time.time()))
        conn.commit()
    finally:
        conn.close()
    return aid, token


def mk_product(aid, target=100.0):
    conn = get_personal_conn()
    try:
        cur = conn.execute("INSERT INTO products (owner_id, product_name, category, price, status, target_price)"
                           " VALUES (?,?,?,?,?,?)", (aid, "米诺地尔泡沫剂", "个护", 120.0, "active", target))
        conn.commit()
        return int(cur.lastrowid)
    finally:
        conn.close()


def in_digest_flags(aid):
    conn = get_personal_conn()
    try:
        return [r[0] for r in conn.execute("SELECT COALESCE(in_digest,0) FROM price_alerts WHERE account_id=?",
                                           (aid,))]
    finally:
        conn.close()


@pytest.fixture()
def acct():
    aid, token = mk_account()
    pid = mk_product(aid)
    yield {"aid": aid, "token": token, "pid": pid}
    purge_account(aid)


# ---------------------------------------------------------------- ① 周报附录
def test_appendix_empty_without_alerts(acct):
    assert digest_appendix(acct["aid"]) == "", "没有提醒就不加空章节"


def test_appendix_lists_alerts_with_chinese_labels(acct):
    """Q5 A 后：板块叫「我的价格台账（本期变化）」，以台账为准，并声明与"报道里的价格"分家。"""
    record_price(acct["aid"], "product", acct["pid"], 99.0)          # 首次跌破目标价
    got = digest_appendix(acct["aid"], mark=False)
    assert got.startswith("\n## 📉 我的价格台账"), got[:50]
    assert "首次跌破目标价" in got and "99" in got
    assert "你自己记的价" in got and "报道里的价格" in got

def test_appendix_mask_prevents_duplicate_in_next_report(acct):
    """★ 只约束**提醒**：台账"本期变化"是事实，会照常出现（它不是被消费掉的提醒）。"""
    record_price(acct["aid"], "product", acct["pid"], 99.0)
    first = digest_appendix(acct["aid"], mark=True)
    assert first and in_digest_flags(acct["aid"]) == [1]
    second = digest_appendix(acct["aid"], mark=False)
    assert "首次跌破目标价" not in second, "★ 同一件事不在两份周报里重复出现"
    assert "我的价格台账" in second, "台账本期变化仍应出现"

def test_appendix_without_mark_keeps_it_pending(acct):
    """★ 报告没写成时不能标 in_digest（否则这条提醒永久丢了）—— 所以调用方先 mark=False。"""
    record_price(acct["aid"], "product", acct["pid"], 99.0)
    got = digest_appendix(acct["aid"], mark=False)
    assert got and in_digest_flags(acct["aid"]) == [0]
    assert digest_appendix(acct["aid"], mark=False), "还能再取到（没被吃掉）"


def test_appendix_is_account_scoped(acct):
    record_price(acct["aid"], "product", acct["pid"], 99.0)
    other, _ = mk_account()
    try:
        assert digest_appendix(other, mark=False) == ""
    finally:
        purge_account(other)


def test_digest_marks_only_after_writing_the_file():
    """静态契约：run_digest 必须"先 mark=False 拿附录 → 写盘 → 再 mark=True"。"""
    s = open(os.path.join(ROOT, "agent", "digest_engine.py"), encoding="utf-8").read().replace("\r\n", "\n")
    i_write = s.find('md_path.write_text(md, encoding="utf-8")')
    i_first = s.find("digest_appendix(owner_id, mark=False)")
    i_mark = s.find("mark=True")
    assert 0 < i_first < i_write < i_mark, "顺序错了：先取附录、写盘、再标记"


# ---------------------------------------------------------------- ② 接口（取走 + 回执）
def test_api_requires_valid_token(acct):
    with pytest.raises(HTTPException):
        alerts("bad-token")


def test_api_lists_pending(acct):
    record_price(acct["aid"], "product", acct["pid"], 99.0)
    d = alerts(acct["token"])
    assert d["count"] == 1 and d["items"][0]["rule"] == "below_target"


def test_api_ack_then_no_longer_pending(acct):
    record_price(acct["aid"], "product", acct["pid"], 99.0)
    aid_alert = alerts(acct["token"])["items"][0]["id"]
    assert ack(acct["token"], AckReq(alert_id=aid_alert))["acked"] == 1
    assert alerts(acct["token"])["count"] == 0, "★ ack 之后不再待通知（避免重复弹）"


def test_api_ack_is_idempotent(acct):
    record_price(acct["aid"], "product", acct["pid"], 99.0)
    aid_alert = alerts(acct["token"])["items"][0]["id"]
    ack(acct["token"], AckReq(alert_id=aid_alert))
    assert ack(acct["token"], AckReq(alert_id=aid_alert))["acked"] == 0


def test_api_ack_other_account_alert_is_404(acct):
    other, otoken = mk_account()
    try:
        opid = mk_product(other)
        record_price(other, "product", opid, 99.0)
        oalert = alerts(otoken)["items"][0]["id"]
        with pytest.raises(HTTPException) as e:
            ack(acct["token"], AckReq(alert_id=oalert))
        assert e.value.status_code == 404, "★ 别人的提醒不能静默成功"
    finally:
        purge_account(other)


def test_api_ack_missing_id_is_400(acct):
    with pytest.raises(HTTPException) as e:
        ack(acct["token"], AckReq(alert_id=0))
    assert e.value.status_code == 400


# ---------------------------------------------------------------- ③ 前端接线
def test_frontend_hooks_present():
    s = open(os.path.join(ROOT, "front", "index.html"), encoding="utf-8").read()
    assert "/api/price/alerts" in s and "/api/price/alerts/ack" in s
    assert "window.__zxDesktop.notify" in s, "桌面壳在时要走系统通知（外壳不自己发 HTTP）"
    assert "showToast(msg)" in s, "不在壳里时要有应用内兜底"
    assert "alert_id: a.id" in s, "回执要带 alert_id（后端字段名）"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
