# -*- coding: utf-8 -*-
"""M6c-5 · 成本出口（M6c 方案 §5.3）。

两个端点：
  · `GET /api/cost/summary?days=30` —— 按 `provider · model` 汇总调用数/tokens/金额/无用量次数 + 按天趋势
  · `POST /api/cost/recalc`          —— **显式**"按当前单价重算"，只影响指定时间窗（G6）

★ 口径（与 M6c 方案一致，别在这里另立一套）：
  · 金额一律是**估算值**（用户填的单价 × 我们数到的 token），**不是账单**，不含工具与检索成本；
  · `cost=None` 且 `uncosted_calls>0` → 前端显示「未配置单价」（**留空 ≠ 免费**；填 0 才是免费的 ¥0.00）；
  · `token_source='unavailable'` 的调用**不计金额**，但要报出次数（"N 次调用无用量数据"）。
"""
import time

from fastapi import HTTPException, status
from pydantic import BaseModel

from agent import usage_counter as uc
from tools.schema_personal import get_personal_conn

# 汇总行的 SQL 片段（provider·model 维度）。`COALESCE` 保证空库也返回 0 而不是 NULL。
_AGG_COLS = ("COUNT(*), COALESCE(SUM(input_tokens),0), COALESCE(SUM(output_tokens),0),"
             " COALESCE(SUM(cached_tokens),0), COALESCE(SUM(uncached_tokens),0), SUM(cost),"
             " SUM(CASE WHEN token_source IN ('unavailable','unknown_shape') THEN 1 ELSE 0 END),"
             " SUM(CASE WHEN token_source='no_cache_detail' THEN 1 ELSE 0 END),"
             " SUM(CASE WHEN cost IS NULL AND token_source NOT IN ('unavailable','unknown_shape')"
             "     THEN 1 ELSE 0 END)")


class RecalcReq(BaseModel):
    # ★ 只做显式重算：不传 days 默认 30；窗口外一行都不许动（G6）
    days: int = 30


def _blank() -> dict:
    return {"calls": 0, "tokens": {"input": 0, "output": 0, "cached": 0, "uncached": 0},
            "cost": None, "unavailable_calls": 0, "no_cache_detail_calls": 0, "uncosted_calls": 0,
            "cost_note": "no_price"}


def _row_to_block(row, off: int = 0) -> dict:
    """一行聚合 → 返回结构。`off` 是**前导列数**（分组查询前面还带了 provider_id/model_name 或 date，
    不把它偏掉就会去 int() 一个字符串 —— 这条是被用例当场抓出来的 ✗）。

    `cost_note` 是给前端的**文案开关**：no_price = 未配置单价。
    """
    if row is None:
        return _blank()
    calls = int(row[off] or 0)
    uncosted = int(row[off + 8] or 0)
    cost = None if row[off + 5] is None else round(float(row[off + 5]), 6)
    return {
        "calls": calls,
        "tokens": {"input": int(row[off + 1] or 0), "output": int(row[off + 2] or 0),
                   "cached": int(row[off + 3] or 0), "uncached": int(row[off + 4] or 0)},
        "cost": cost,
        "unavailable_calls": int(row[off + 6] or 0),
        "no_cache_detail_calls": int(row[off + 7] or 0),
        "uncosted_calls": uncosted,
        # 有调用、但一分钱都没折算出来 → 说明单价没配（G5：留空 ≠ 免费）
        "cost_note": "no_price" if (cost is None and uncosted > 0) else "",
        "estimated": True,          # 金额是估算值（前端文案：不是账单）
    }


def cost_summary(token: str, days: int = 30) -> dict:
    """按 provider·model 汇总 + 按天趋势。跨账号一律不做（个人应用，隔离优先）。"""
    from api.customize import _require_account_id
    aid = _require_account_id(token)
    try:
        days = max(0, int(days))
    except (TypeError, ValueError):
        days = 30
    cut = time.time() - days * 86400
    conn = get_personal_conn()
    try:
        cur = conn.cursor()
        # ★ list() 物化：下面还要用同一个 cursor 跑别的查询
        by_provider = list(cur.execute(
            "SELECT provider_id, model_name, " + _AGG_COLS +
            " FROM usage_events WHERE account_id=? AND kind='model' AND created_ts>=?"
            " GROUP BY provider_id, model_name ORDER BY 1", (int(aid), cut)))
        daily = list(cur.execute(
            "SELECT substr(created_at,1,10) AS d, " + _AGG_COLS +
            " FROM usage_events WHERE account_id=? AND kind='model' AND created_ts>=?"
            " GROUP BY d ORDER BY d", (int(aid), cut)))
        total = cur.execute(
            "SELECT " + _AGG_COLS + " FROM usage_events WHERE account_id=? AND kind='model'"
            " AND created_ts>=?", (int(aid), cut)).fetchone()
        # 名字只是为了显示：provider_id 可能为空（未接线/未选配置）
        names = {r[0]: r[1] for r in cur.execute(
            "SELECT id, provider_name FROM llm_providers WHERE owner_id=?", (int(aid),))}
    finally:
        conn.close()
    return {
        "days": days,
        "since": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(cut)),
        "total": _row_to_block(total),
        # 前导列：by_provider = (provider_id, model_name) → off=2；daily = (date,) → off=1
        "by_provider": [dict(_row_to_block(r, 2), provider_id=r[0], model_name=r[1] or "",
                             provider_name=names.get(r[0], "")) for r in by_provider],
        "daily": [dict(_row_to_block(r, 1), date=r[0] or "") for r in daily],
        "estimated": True,
        "note": "金额为估算值（你填的单价 × 数到的 token），不含工具与检索成本，不是账单",
    }


def recalc(token: str, days: int = 30) -> dict:
    """★ 显式"按当前单价重算"（G6）：只影响指定时间窗；token 数**永不被重算改动**。"""
    from api.customize import _require_account_id
    aid = _require_account_id(token)
    try:
        days = max(0, int(days))
    except (TypeError, ValueError):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "days 要是整数天数")
    stats = uc.recalc_costs(aid, days)
    return dict({"ok": True, "days": days, "days_capped": False}, **stats)
