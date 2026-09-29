# -*- coding: utf-8 -*-
"""M4-6 · 把这些"注入源"注册进 `agent.context_sources`（api 侧提供实现，方案 §3.2 / C5）。

本文件 import 一次即完成注册（`api/server.py` 启动时 import）。
记忆读取走 `tools/user_doc_paths.read_memory_for_agent` —— **与周报侧同一个函数**（C6/M4-8）。
"""
from agent import context_sources as cs
from agent.context_sources import SourceResult


def _soul(aid: int, question: str = "") -> SourceResult:
    try:
        import api.customize as cust
        text = cust.get_soul_content(aid) or ""
    except Exception:
        text = ""
    return SourceResult(text.strip(), bool(text.strip()))


def _username_of(aid: int) -> str:
    """账号 id → 用户名（自己查，**不要**用 hasattr 试探别人的私有方法）。

    ★ 2026-09-27 实测教训：我第一版写成 `hasattr(cust, "_username_by_account_id")` 的防御式取值 ——
      那个方法根本不存在 → 记忆**被静默注成空串** → `m2_assembly_e2e_test` 的「问答引用画像事实」直接失败。
      防御式写法掩盖了"方法不存在"，属于最坏的一类 bug（不报错、只是少注入）。
    """
    from tools.schema_personal import get_personal_conn
    conn = get_personal_conn()
    try:
        row = conn.execute("SELECT username FROM accounts WHERE id=?", (int(aid),)).fetchone()
    finally:
        conn.close()
    if not row:
        return ""
    return row[0] if not hasattr(row, "keys") else row["username"]


def _memory(aid: int, question: str = "") -> SourceResult:
    try:
        from tools import user_doc_paths as paths
        text = paths.read_memory_for_agent(_username_of(aid))
    except Exception as e:
        print("[M4-7] memory 源读取失败（降级为空）:", e)
        text = ""
    return SourceResult((text or "").strip(), bool((text or "").strip()))


def register_all() -> None:
    """幂等：可重复调用（例如测试里 clear 之后重建）。"""
    cs.register("soul", _soul)
    cs.register("memory", _memory)
    # skills / cli_brief 需要"本轮请求特有"的入参（选中的技能名、路由结果），
    # provider 签名只有 (account_id, question) → 本轮仍由 server 显式传入（M4-7 再决定是否纳入）。
    # 这里**不注册空 provider**：宁可少一个源，也不让装配侧误以为"已经收集过了"。
