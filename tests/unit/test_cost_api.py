# -*- coding: utf-8 -*-
"""M6c-5 用例：成本出口（`/api/usage` 扩展 + `/api/cost/summary` + `/api/cost/recalc` + 单价读写）。

判据来源：M6c 方案 §5.3 接口表 + §六 M6c-5（≥8 项）+ G5/G6/G8/G9。
★ 两个最容易半途而废的点，本文件专门钉住：
  1. 单价**必须出现在 provider 出参里**（只改表不改出参 → 前端永远拿不到）；
  2. `llm_provider_update` 有两条 SQL 分支（带新 key / 留空 key）—— **两条都要带上单价** ✗。

全离线：直接调函数，不发真实网络请求。

运行：.venv/Scripts/python.exe -m pytest tests/unit/test_cost_api.py -q
"""
import os
import sys
import time

import pytest
from fastapi import HTTPException

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from api import cost_api, customize, usage_api                                   # noqa: E402
from tools.schema_personal import ensure_tables, get_personal_conn, purge_account   # noqa: E402

ensure_tables()


def mk_account(tag="ca", with_token=True):
    conn = get_personal_conn()
    try:
        aid = int(conn.execute("INSERT INTO accounts (username, password_hash, role) VALUES (?,?,?)",
                               ("m6c%s_%d" % (tag, int(time.time() * 1000) % 1000000), "x", "user")).lastrowid)
        tok = "tk%s%d" % (tag, aid)
        if with_token:
            conn.execute("INSERT INTO sessions (token, account_id, login_at) VALUES (?,?,datetime('now'))",
                         (tok, aid))
        conn.commit()
    finally:
        conn.close()
    return aid, tok


def mk_thread(aid, tid="th-m6c5"):
    conn = get_personal_conn()
    try:
        conn.execute("INSERT INTO conversations (account_id, thread_id, title, message_count)"
                     " VALUES (?,?,?,?)", (aid, tid, "测试会话", 4))
        conn.commit()
    finally:
        conn.close()
    return tid


def add_model_row(aid, tid="th-m6c5", provider_id=None, turn=0, cost=None, note=None,
                  token_source="reported", inp=1000, out=200, cached=300, uncached=700, ts=None):
    ts = time.time() if ts is None else ts
    conn = get_personal_conn()
    try:
        conn.execute(
            "INSERT INTO usage_events (account_id, thread_id, turn_index, kind, provider_id, model_name,"
            " input_tokens, output_tokens, cached_tokens, uncached_tokens, token_source, cost, cost_note,"
            " created_at, created_ts) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (aid, tid, turn, "model", provider_id, "deepseek-chat", inp, out, cached, uncached,
             token_source, cost, note, time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts)), ts))
        conn.commit()
    finally:
        conn.close()


def add_tool_row(aid, tid="th-m6c5"):
    conn = get_personal_conn()
    try:
        conn.execute("INSERT INTO usage_events (account_id, thread_id, kind, tool_name, source, ok,"
                     " latency_ms, result_chars, created_at, created_ts) VALUES (?,?,?,?,?,?,?,?,?,?)",
                     (aid, tid, "retrieval_external", "internet_search", "网络搜索", 1, 100, 50,
                      time.strftime("%Y-%m-%d %H:%M:%S"), time.time()))
        conn.commit()
    finally:
        conn.close()


def mk_provider(aid, cached=0.5, uncached=2.0, out=8.0, name="DeepSeek", token=None):
    """建一套模型配置（带三处单价）。token 要按调用方的 tag 传 —— 原来硬编码 tkca 前缀 ✗，
    别的 tag（如 tkx）会直接 401，还看不出是自己拼错了。"""
    req = customize.LlmProviderReq(provider_name=name, model_name="deepseek-chat", base_url="https://x",
                                   api_key="sk-secret-key-1234", is_active=False,
                                   price_in_cached=cached, price_in_uncached=uncached, price_out=out)
    return customize.llm_provider_add(req, token or ("tkca%d" % aid))["provider"]


# --------------------------------------------------------------- ① /api/usage 扩展

def test_usage_report_carries_tokens_and_cost():
    """本轮/本会话都要带 tokens 与金额（G8）。"""
    aid, tok = mk_account()
    try:
        tid = mk_thread(aid)
        add_model_row(aid, tid, turn=0, cost=0.0123)
        add_model_row(aid, tid, turn=0, cost=0.0007)
        rep = usage_api.usage_report(tok, tid)
        for scope in ("this_turn", "this_session"):
            blk = rep[scope]
            assert blk["model_calls"] == 2
            assert blk["tokens"] == {"input": 2000, "output": 400, "cached": 600, "uncached": 1400}
            assert blk["cost"] == pytest.approx(0.013)      # 0.0123 + 0.0007
            assert blk["unavailable_calls"] == 0
        assert rep["estimated_cost"] is True and "估算值" in rep["cost_note_text"]
    finally:
        purge_account(aid)


def test_usage_report_says_no_price_not_free():
    """G5：没配单价但有调用 → `cost_note='no_price'`（金额为 None，**不是 0**）。"""
    aid, tok = mk_account()
    try:
        tid = mk_thread(aid)
        add_model_row(aid, tid, cost=None, note="no_price")
        blk = usage_api.usage_report(tok, tid)["this_session"]
        assert blk["cost"] is None and blk["cost_note"] == "no_price"
        assert blk["tokens"]["input"] == 1000            # token 照常显示
    finally:
        purge_account(aid)


def test_usage_report_counts_unavailable_calls():
    """G4：无用量数据的调用要报次数（前端显示「N 次调用无用量数据」）。"""
    aid, tok = mk_account()
    try:
        tid = mk_thread(aid)
        add_model_row(aid, tid, cost=None, note="unavailable", token_source="unavailable",
                      inp=None, out=None, cached=None, uncached=None)
        add_model_row(aid, tid, cost=0.001)
        blk = usage_api.usage_report(tok, tid)["this_session"]
        assert blk["unavailable_calls"] == 1 and blk["model_calls"] == 2
        assert blk["cost_note"] == ""                    # 有一条折算成功 → 不是"未配价"
    finally:
        purge_account(aid)


def test_usage_report_foreign_thread_is_403():
    """越权：别人的会话 id → 403（不能靠"过滤后返回 0"假装安全）。"""
    a1, t1 = mk_account("x")
    a2, t2 = mk_account("y")
    try:
        tid = mk_thread(a1)
        with pytest.raises(HTTPException) as e:
            usage_api.usage_report(t2, tid)
        assert e.value.status_code == 403
        assert usage_api.usage_report(t1, tid)["this_session"]["total"] == 0   # 自己的能看
    finally:
        purge_account(a1)
        purge_account(a2)


def test_usage_report_tool_rows_do_not_join_model_block():
    """工具行不算模型账（它们不花 token）。"""
    aid, tok = mk_account()
    try:
        tid = mk_thread(aid)
        add_tool_row(aid, tid)
        rep = usage_api.usage_report(tok, tid)["this_session"]
        assert rep["cost_calls"] == 1 and rep["model_calls"] == 0 and rep["cost"] is None
    finally:
        purge_account(aid)


# --------------------------------------------------------------- ② /api/cost/summary

def test_cost_summary_groups_by_provider_and_model():
    """G8：按 provider·model 汇总，并给按天趋势。"""
    aid, tok = mk_account()
    p = mk_provider(aid, token=tok)
    try:
        tid = mk_thread(aid)
        add_model_row(aid, tid, provider_id=p["id"], cost=0.01)
        add_model_row(aid, tid, provider_id=p["id"], cost=0.02)
        out = cost_api.cost_summary(tok, days=30)
        assert out["total"]["calls"] == 2 and out["total"]["cost"] == pytest.approx(0.03)
        assert len(out["by_provider"]) == 1
        g = out["by_provider"][0]
        assert g["provider_id"] == p["id"] and g["provider_name"] == "DeepSeek"
        assert g["model_name"] == "deepseek-chat" and g["tokens"]["input"] == 2000
        assert len(out["daily"]) == 1 and out["daily"][0]["calls"] == 2
        assert out["estimated"] is True and "不是账单" in out["note"]
    finally:
        purge_account(aid)


def test_cost_summary_empty_is_zero_not_error():
    """空数据：零值结构（不炸、不返回 None）。"""
    aid, tok = mk_account()
    try:
        out = cost_api.cost_summary(tok, days=7)
        assert out["total"]["calls"] == 0 and out["total"]["cost"] is None
        assert out["by_provider"] == [] and out["daily"] == []
    finally:
        purge_account(aid)


def test_cost_summary_window_excludes_old_rows():
    """窗口外不计入（同日口径与 M6c-4 的重算一致）。"""
    aid, tok = mk_account()
    try:
        tid = mk_thread(aid)
        add_model_row(aid, tid, cost=0.5, ts=time.time() - 40 * 86400)
        add_model_row(aid, tid, cost=0.1)
        assert cost_api.cost_summary(tok, days=30)["total"]["cost"] == pytest.approx(0.1)
        assert cost_api.cost_summary(tok, days=90)["total"]["cost"] == pytest.approx(0.6)
    finally:
        purge_account(aid)


# --------------------------------------------------------------- ③ /api/cost/recalc

def test_recalc_endpoint_updates_window_and_keeps_tokens():
    """显式重算：金额变、**token 数不动**、写 `recalculated_at`（G6 的另一半）。"""
    aid, tok = mk_account()
    p = mk_provider(aid, 0.5, 2.0, 8.0)
    try:
        tid = mk_thread(aid)
        add_model_row(aid, tid, provider_id=p["id"], cost=0.001)
        before = usage_api.usage_report(tok, tid)["this_session"]["tokens"]
        out = cost_api.recalc(tok, days=7)
        assert out["ok"] is True and out["updated"] == 1 and out["scanned"] == 1
        conn = get_personal_conn()
        try:
            r = conn.execute("SELECT cost, recalculated_at, input_tokens, output_tokens FROM usage_events"
                             " WHERE account_id=? AND kind='model'", (aid,)).fetchone()
        finally:
            conn.close()
        expect = (300 / 1e6) * 0.5 + (700 / 1e6) * 2.0 + (200 / 1e6) * 8.0
        assert r[0] == pytest.approx(round(expect, 6))
        assert r[1] is not None and (r[2], r[3]) == (1000, 200)      # token 没被动过
        assert usage_api.usage_report(tok, tid)["this_session"]["tokens"] == before
    finally:
        purge_account(aid)


def test_recalc_endpoint_rejects_bad_days():
    """`days` 非整数 → 400（不静默按默认值糊过去）。"""
    aid, tok = mk_account()
    try:
        with pytest.raises(HTTPException) as e:
            cost_api.recalc(tok, days="十天")
        assert e.value.status_code == 400
    finally:
        purge_account(aid)


# --------------------------------------------------------------- ④ 单价读写

def test_provider_prices_roundtrip_in_and_out():
    """★ 单价必须**出现在出参里**（只改表不改出参 = 前端拿不到）。"""
    aid, tok0 = mk_account()
    try:
        p = mk_provider(aid, 0.5, 2.0, 8.0, token=tok0)
        assert p["price_in_cached"] == 0.5 and p["price_out"] == 8.0
        lst = customize.llm_providers_list("tkca%d" % aid)["providers"][0]
        assert lst["price_in_uncached"] == 2.0
    finally:
        purge_account(aid)


def test_provider_update_sets_prices_on_both_key_paths():
    """★ update 的两条 SQL 分支（带新 key / 留空 key）**都要生效**。"""
    aid, tok = mk_account()
    try:
        p = mk_provider(aid, 1.0, 1.0, 1.0, token=tok)
        # 分支一：留空 api_key（掩码串 = 不改 key）
        req1 = customize.LlmProviderReq(provider_name="DeepSeek", model_name="deepseek-chat",
                                        base_url="https://x", api_key="", price_in_cached=3.0,
                                        price_in_uncached=4.0, price_out=5.0)
        got = customize.llm_provider_update(p["id"], req1, tok)["provider"]
        assert (got["price_in_cached"], got["price_in_uncached"], got["price_out"]) == (3.0, 4.0, 5.0)
        # 分支二：带新 key
        req2 = customize.LlmProviderReq(provider_name="DeepSeek", model_name="deepseek-chat",
                                        base_url="https://x", api_key="sk-new-key-9999",
                                        price_in_cached=6.0, price_in_uncached=7.0, price_out=8.0)
        got2 = customize.llm_provider_update(p["id"], req2, tok)["provider"]
        assert (got2["price_in_cached"], got2["price_in_uncached"], got2["price_out"]) == (6.0, 7.0, 8.0)
        assert got2["api_key"].endswith("9999")          # key 真换了，且仍是掩码
    finally:
        purge_account(aid)


def test_negative_price_rejected_but_zero_is_free():
    """负数单价 → 400；**0 是合法值**（明确免费）。"""
    aid, tok = mk_account()
    try:
        with pytest.raises(HTTPException) as e:
            customize.llm_provider_add(customize.LlmProviderReq(
                provider_name="坏数据", model_name="m", base_url="u", api_key="k", price_out=-1), tok)
        assert e.value.status_code == 400
        p = mk_provider(aid, 0.0, 0.0, 0.0)
        assert p["price_out"] == 0.0 and p["price_in_cached"] == 0.0
    finally:
        purge_account(aid)


def test_provider_update_foreign_id_is_404():
    """越权：别人的 provider id → 404。"""
    a1, t1x = mk_account("x")
    a2, tok2 = mk_account("y")
    try:
        p1 = mk_provider(a1, token=t1x)
        req = customize.LlmProviderReq(provider_name="改别人的", model_name="m", base_url="u", api_key="k")
        with pytest.raises(HTTPException) as e:
            customize.llm_provider_update(p1["id"], req, tok2)
        assert e.value.status_code == 404
    finally:
        purge_account(a1)
        purge_account(a2)
