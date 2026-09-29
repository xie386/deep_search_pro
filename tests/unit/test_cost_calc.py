# -*- coding: utf-8 -*-
"""M6c-4 用例：折算落库 + 显式「按当前单价重算」。

判据来源：M6c 方案 §3.2 ④⑤ + §六 M6c-4（≥10 项）+ **G6 反证**（换模型/改价不得篡改历史账）。
★ 本步的灵魂就是 G6：金额**落库**（`cost` + `price_snapshot`），查询时不现算；
  「按当前单价重算」必须是**显式动作**、只影响指定时间窗，并留下 `recalculated_at` 痕迹。

全离线：不连模型、不发网络请求。

运行：.venv/Scripts/python.exe -m pytest tests/unit/test_cost_calc.py -q
"""
import json
import os
import sys
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from agent import usage_counter as uc                                          # noqa: E402
from tools.schema_personal import ensure_tables, get_personal_conn, purge_account   # noqa: E402

ensure_tables()

RAW_HIT = {"prompt_tokens": 1000, "completion_tokens": 200,
           "prompt_cache_hit_tokens": 300, "prompt_cache_miss_tokens": 700}


class FakeMsg:
    def __init__(self, raw=None, model="deepseek-chat"):
        self.response_metadata = {"token_usage": raw, "model_name": model} if raw is not None else {}
        self.usage_metadata = None


class FakeResp:
    def __init__(self, msgs):
        self.result = msgs if isinstance(msgs, list) else [msgs]


def mk_account(tag="cc"):
    conn = get_personal_conn()
    try:
        aid = int(conn.execute("INSERT INTO accounts (username, password_hash, role) VALUES (?,?,?)",
                               ("m6c%s_%d" % (tag, int(time.time() * 1000) % 1000000), "x", "user")).lastrowid)
        conn.commit()
    finally:
        conn.close()
    return aid


def mk_provider(aid, cached=0.5, uncached=2.0, out=8.0, active=1, name="DeepSeek"):
    conn = get_personal_conn()
    try:
        pid = int(conn.execute(
            "INSERT INTO llm_providers (owner_id, provider_name, model_name, base_url, api_key, is_active,"
            " price_in_cached, price_in_uncached, price_out) VALUES (?,?,?,?,?,?,?,?,?)",
            (aid, name, "deepseek-chat", "u", "k", active, cached, uncached, out)).lastrowid)
        conn.commit()
    finally:
        conn.close()
    return pid


def set_price(pid, cached=None, uncached=None, out=None):
    conn = get_personal_conn()
    try:
        conn.execute("UPDATE llm_providers SET price_in_cached=?, price_in_uncached=?, price_out=? WHERE id=?",
                     (cached, uncached, out, pid))
        conn.commit()
    finally:
        conn.close()


def with_ctx(aid, tid="t-cc", turn=1):
    uc.set_context_reader(lambda: (aid, tid, turn))


def call_model(provider_row, raw=RAW_HIT):
    uc.set_provider_resolver(lambda: provider_row)
    uc.UsageCounterMiddleware().wrap_model_call(None, lambda r: FakeResp(FakeMsg(raw=raw)))


def model_rows(aid):
    conn = get_personal_conn()
    try:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM usage_events WHERE account_id=? AND kind='model' ORDER BY id", (aid,))]
    finally:
        conn.close()


def add_row(aid, ts, provider_id=None, token_source="reported", kind="model",
            inp=1000, out=200, cached=300, uncached=700, cost=None, note=None, snap=None):
    """直接插一行（用来构造"窗口外/工具行"等场景，不必等时间流逝）。"""
    conn = get_personal_conn()
    try:
        cur = conn.execute(
            "INSERT INTO usage_events (account_id, kind, provider_id, model_name, input_tokens,"
            " output_tokens, cached_tokens, uncached_tokens, token_source, cost, cost_note, price_snapshot,"
            " created_at, created_ts) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (aid, kind, provider_id, "deepseek-chat", inp, out, cached, uncached, token_source, cost, note,
             snap, time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts)), ts))
        conn.commit()
        return int(cur.lastrowid)
    finally:
        conn.close()


@pytest.fixture(autouse=True)
def _clean():
    yield
    uc.set_context_reader(None)
    uc.set_provider_resolver(None)


# --------------------------------------------------------------- 采集时折算落库

def test_cost_is_written_at_collection_time():
    """④：采集时就折算并落库（cost + cost_note + price_snapshot），不是查询时现算。"""
    aid = mk_account()
    try:
        pid = mk_provider(aid)
        with_ctx(aid)
        call_model({"id": pid, "provider_name": "DeepSeek", "model_name": "deepseek-chat",
                    "price_in_cached": 0.5, "price_in_uncached": 2.0, "price_out": 8.0})
        r = model_rows(aid)[0]
        expect = (300 / 1e6) * 0.5 + (700 / 1e6) * 2.0 + (200 / 1e6) * 8.0
        assert r["cost"] == pytest.approx(round(expect, 6))
        assert r["cost_note"] is None and r["recalculated_at"] is None     # 刚采集 = 按当时单价
        snap = json.loads(r["price_snapshot"])
        assert snap["price_in_cached"] == 0.5 and snap["unit"] == "CNY/1M tokens"
    finally:
        purge_account(aid)


def test_no_price_leaves_cost_null():
    """未配价 → 只数 token、金额留 NULL（G5：留空 ≠ 免费）。"""
    aid = mk_account()
    try:
        pid = mk_provider(aid, cached=None, uncached=None, out=None)
        with_ctx(aid)
        call_model({"id": pid, "provider_name": "未配价"})
        r = model_rows(aid)[0]
        assert r["cost"] is None and r["cost_note"] == "no_price"
        assert r["input_tokens"] == 1000 and r["price_snapshot"] is None
    finally:
        purge_account(aid)


def test_zero_price_is_free_not_unpriced():
    """三处填 0 = 明确免费 → cost 0.0（≠ NULL）。"""
    aid = mk_account()
    try:
        pid = mk_provider(aid, cached=0, uncached=0, out=0)
        with_ctx(aid)
        call_model({"id": pid, "provider_name": "免费模型", "price_in_cached": 0,
                    "price_in_uncached": 0, "price_out": 0})
        r = model_rows(aid)[0]
        assert r["cost"] == 0.0 and r["cost_note"] is None
    finally:
        purge_account(aid)


def test_no_cache_detail_uses_uncached_price_and_marks_note():
    """D3：缺缓存明细 → 按未命中价折算 + `no_cache_detail` 标注（UI 要显示这行小字）。"""
    aid = mk_account()
    try:
        pid = mk_provider(aid)
        with_ctx(aid)
        call_model({"id": pid, "price_in_cached": 0.5, "price_in_uncached": 2.0, "price_out": 8.0},
                   raw={"prompt_tokens": 1000, "completion_tokens": 0})
        r = model_rows(aid)[0]
        assert r["token_source"] == "no_cache_detail" and r["cost_note"] == "no_cache_detail"
        assert r["cost"] == pytest.approx(round((1000 / 1e6) * 2.0, 6))
    finally:
        purge_account(aid)


def test_unavailable_tokens_are_not_costed():
    """G4：没数到 token → 不折算（note = unavailable），不能凭空给金额。"""
    aid = mk_account()
    try:
        pid = mk_provider(aid)
        with_ctx(aid)
        call_model({"id": pid, "price_out": 8.0}, raw=None)
        r = model_rows(aid)[0]
        assert r["cost"] is None and r["cost_note"] == "unavailable"
    finally:
        purge_account(aid)


# --------------------------------------------------------------- ★ G6 反证

def test_changing_price_does_not_touch_history():
    """★ G6：先按旧价记 10 条 → 改单价 → **旧 10 条金额一动不动**（落库 + 快照的意义）。"""
    aid = mk_account()
    try:
        pid = mk_provider(aid, cached=0.5, uncached=2.0, out=8.0)
        row = {"id": pid, "price_in_cached": 0.5, "price_in_uncached": 2.0, "price_out": 8.0}
        with_ctx(aid)
        for _ in range(10):
            call_model(row)
        before = [r["cost"] for r in model_rows(aid)]
        assert len(before) == 10 and all(c is not None for c in before)

        set_price(pid, 9.0, 9.0, 9.0)                        # 用户把单价改得更贵
        after = [r["cost"] for r in model_rows(aid)]
        assert after == before, "改价把历史账目一起改了（G6 被破）"

        # 新的一条才用新价
        call_model({"id": pid, "price_in_cached": 9.0, "price_in_uncached": 9.0, "price_out": 9.0})
        newest = model_rows(aid)[-1]
        assert newest["cost"] == pytest.approx(round((1000 / 1e6) * 9.0 + (200 / 1e6) * 9.0, 6))
    finally:
        purge_account(aid)


# --------------------------------------------------------------- 显式重算

def test_recalc_updates_rows_inside_window_only():
    """重算只影响指定时间窗：窗外那两条必须原样不动。"""
    aid = mk_account()
    try:
        pid = mk_provider(aid, 1.0, 1.0, 1.0)
        now = time.time()
        old_id = add_row(aid, now - 10 * 86400, pid, cost=0.001, snap='{"old":true}')
        new_id = add_row(aid, now - 60, pid, cost=0.001, snap='{"old":true}')
        stats = uc.recalc_costs(aid, days=7)
        assert stats["scanned"] == 1 and stats["updated"] == 1
        rows = {r["id"]: r for r in model_rows(aid)}
        assert rows[old_id]["cost"] == 0.001                       # 窗外：金额不变
        assert rows[old_id]["price_snapshot"] == '{"old":true}'    # 窗外：连快照都不动
        assert rows[old_id]["recalculated_at"] is None                   # 窗外：连痕迹都不留
        expect = (300 / 1e6) * 1.0 + (700 / 1e6) * 1.0 + (200 / 1e6) * 1.0
        assert rows[new_id]["cost"] == pytest.approx(round(expect, 6))
        assert rows[new_id]["recalculated_at"] is not None                # 窗内：留痕迹
        assert rows[new_id]["price_snapshot"] != '{"old":true}'
    finally:
        purge_account(aid)


def test_recalc_skips_rows_without_price_or_tokens():
    """重算跳过：未配价（no_price）与无用量（unavailable/unknown_shape）。"""
    aid = mk_account()
    try:
        pid_unpriced = mk_provider(aid, None, None, None)
        now = time.time()
        add_row(aid, now - 60, pid_unpriced)
        add_row(aid, now - 60, pid_unpriced, token_source="unavailable",
                inp=None, out=None, cached=None, uncached=None)
        add_row(aid, now - 60, pid_unpriced, token_source="unknown_shape",
                inp=None, out=None, cached=None, uncached=None)
        stats = uc.recalc_costs(aid, days=7)
        assert stats["scanned"] == 3
        assert stats["skipped_no_tokens"] == 2 and stats["skipped_no_price"] == 1
        assert stats["updated"] == 0
        for r in model_rows(aid):
            assert r["cost"] is None and r["recalculated_at"] is None
    finally:
        purge_account(aid)


def test_recalc_is_account_scoped():
    """越权面：重算只能动自己的行。"""
    a1 = mk_account("x")
    a2 = mk_account("y")
    try:
        p1 = mk_provider(a1, 1.0, 1.0, 1.0)
        p2 = mk_provider(a2, 1.0, 1.0, 1.0)
        now = time.time()
        add_row(a1, now - 60, p1, cost=0.001)
        add_row(a2, now - 60, p2, cost=0.001)
        uc.recalc_costs(a1, days=7)
        assert model_rows(a1)[0]["cost"] != 0.001
        assert model_rows(a2)[0]["cost"] == 0.001, "重算动了别的账号的账"
        assert model_rows(a2)[0]["recalculated_at"] is None
    finally:
        purge_account(a1)
        purge_account(a2)


def test_recalc_ignores_tool_rows():
    """工具行不是模型账：不扫描、不折算（工具不花 token）。"""
    aid = mk_account()
    try:
        mk_provider(aid, 1.0, 1.0, 1.0)
        add_row(aid, time.time() - 60, None, kind="retrieval_external", token_source=None,
                inp=None, out=None, cached=None, uncached=None)
        stats = uc.recalc_costs(aid, days=7)
        assert stats == {"scanned": 0, "updated": 0, "skipped_no_price": 0, "skipped_no_tokens": 0}
    finally:
        purge_account(aid)


def test_recalc_falls_back_to_active_provider_when_row_provider_deleted():
    """行上记的 provider 已删 → 退回"账号当前生效"那套单价（不静默跳过）。"""
    aid = mk_account()
    try:
        p_old = mk_provider(aid, 0.5, 2.0, 8.0, name="旧的")
        add_row(aid, time.time() - 60, p_old, cost=0.001)
        conn = get_personal_conn()
        try:
            conn.execute("DELETE FROM llm_providers WHERE id=?", (p_old,))
            conn.commit()
        finally:
            conn.close()
        pid_new = mk_provider(aid, 1.0, 1.0, 1.0, name="新的")
        stats = uc.recalc_costs(aid, days=7)
        assert stats["updated"] == 1
        r = model_rows(aid)[0]
        expect = (300 / 1e6) * 1.0 + (700 / 1e6) * 1.0 + (200 / 1e6) * 1.0
        assert r["cost"] == pytest.approx(round(expect, 6))        # 用新那套单价折算成功
        assert r["recalculated_at"] is not None
    finally:
        purge_account(aid)


def test_recalc_days_zero_and_bad_args():
    """边界：days=0 只算"此刻之后"（刚插的行在窗外）；非法 days 退回默认 30。"""
    aid = mk_account()
    try:
        pid = mk_provider(aid, 1.0, 1.0, 1.0)
        add_row(aid, time.time() - 2, pid)
        assert uc.recalc_costs(aid, days=0)["scanned"] == 0
        assert uc.recalc_costs(aid, days="不是数字")["scanned"] == 1     # 退回 30 天
        assert uc.recalc_costs(None, days=7)["scanned"] == 0             # 没账号 → 空统计
    finally:
        purge_account(aid)
