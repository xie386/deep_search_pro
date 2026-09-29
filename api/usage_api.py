# -*- coding: utf-8 -*-
"""M4-4 · 用量出口 `GET /api/usage`（方案 §3.1 的 [出口]）。

回答的是那个具体问题：**"这次为什么烧了额度"** —— 所以分「本轮 / 本会话」两层，
并只把 `retrieval_external` 计成"消耗额度"（其余 kind 是内部工具/命令行/API/MCP，不花外部检索额度）。
"""
from fastapi import HTTPException, status

from agent import usage_counter as uc
from tools.schema_personal import get_personal_conn


# ★ M6c-5：模型调用聚合（tokens / 金额 / 无用量次数）。工具行不含在内（它们不花 token）。
_MODEL_SQL = ("SELECT COUNT(*), COALESCE(SUM(input_tokens),0), COALESCE(SUM(output_tokens),0),"
              " COALESCE(SUM(cached_tokens),0), COALESCE(SUM(uncached_tokens),0), SUM(cost),"
              " SUM(CASE WHEN token_source IN ('unavailable','unknown_shape') THEN 1 ELSE 0 END),"
              " SUM(CASE WHEN cost IS NULL AND token_source NOT IN ('unavailable','unknown_shape')"
              "     THEN 1 ELSE 0 END)"
              " FROM usage_events WHERE account_id=? AND thread_id=? AND kind='model'")


def _model_block(row) -> dict:
    """模型用量块：`tokens` / `cost` / `cost_note` / `unavailable_calls`（方案 §5.3 的四个新字段）。"""
    if row is None:
        return {"model_calls": 0, "tokens": {"input": 0, "output": 0, "cached": 0, "uncached": 0},
                "cost": None, "cost_note": "no_price", "unavailable_calls": 0}
    uncosted = int(row[7] or 0)
    cost = None if row[5] is None else round(float(row[5]), 6)
    return {
        "model_calls": int(row[0] or 0),
        "tokens": {"input": int(row[1] or 0), "output": int(row[2] or 0),
                   "cached": int(row[3] or 0), "uncached": int(row[4] or 0)},
        "cost": cost,
        "cost_note": "no_price" if (cost is None and uncosted > 0) else "",
        "unavailable_calls": int(row[6] or 0),
    }


def _fmt(kind_rows, model_row=None) -> dict:
    """kind 聚合 → 「中文标签: 次数」，顺带把"花额度的次数"单独拎出来。"""
    counts = {r[0]: int(r[1]) for r in kind_rows}
    kinds = {uc.KIND_LABELS.get(k, k): v for k, v in counts.items()}
    return dict({
        "total": sum(counts.values()),
        "by_kind": kinds,
        "cost_calls": sum(v for k, v in counts.items() if k in uc.COST_KINDS),
    }, **_model_block(model_row))


def usage_report(token: str, thread_id: str = "") -> dict:
    # 延迟导入：避免 api.customize ←→ 本模块的循环
    from api.customize import _require_account_id
    aid = _require_account_id(token)
    tid = (thread_id or "").strip()

    conn = get_personal_conn()
    try:
        if tid:
            # 越权校验：thread 属于别人 → 403（不能靠"过滤后返回 0"来假装安全）
            try:
                # conversations.thread_id 是**独立 TEXT 列**（id 是自增主键），越权校验要按它查
                row = conn.execute("SELECT account_id FROM conversations WHERE thread_id=?",
                                   (tid,)).fetchone()
            except Exception:
                row = None
            if row is not None:
                owner = row[0] if not hasattr(row, "keys") else row["account_id"]
                if int(owner) != int(aid):
                    raise HTTPException(status.HTTP_403_FORBIDDEN, "这个会话不属于当前账号")

        if not tid:
            return {"thread_id": "", "this_turn": _fmt([]), "this_session": _fmt([])}

        sess = list(conn.execute(
            "SELECT kind, COUNT(*) FROM usage_events WHERE account_id=? AND thread_id=? GROUP BY kind",
            (aid, tid)))
        last_turn = conn.execute(
            "SELECT MAX(turn_index) FROM usage_events WHERE account_id=? AND thread_id=?", (aid, tid)).fetchone()[0]
        turn, m_turn = [], None
        if last_turn is not None:
            turn = list(conn.execute(
                "SELECT kind, COUNT(*) FROM usage_events WHERE account_id=? AND thread_id=? AND turn_index=?"
                " GROUP BY kind", (aid, tid, int(last_turn))))
            m_turn = conn.execute(_MODEL_SQL + " AND turn_index=?", (aid, tid, int(last_turn))).fetchone()
        m_sess = conn.execute(_MODEL_SQL, (aid, tid)).fetchone()
    finally:
        conn.close()
    return {"thread_id": tid, "this_turn": _fmt(turn, m_turn), "this_session": _fmt(sess, m_sess),
            "turn_index": int(last_turn or 0), "threshold": 3, "estimated_cost": True,
            "cost_note_text": "金额为估算值（你填的单价 × 数到的 token），不是账单"}
