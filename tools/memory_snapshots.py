# -*- coding: utf-8 -*-
"""M3-9 · 画像版本快照与还原（方案 §5.7 / D9，用户 2026-09-25 新增）。

为什么要它：画像有**四条写路径**（人工编辑 / AI 建议应用 / 一键导入 / agent 自主更新），
任何一条都可能把用户精心写的内容改掉 —— 而画像文件是唯一事实源、没有 git。
所以**每次改动之前先留一份**，只保留最近 3 份，且还原本身也是"先快照再还原"（可以来回切）。

口径（照方案 §5.7 的表）：
  · **内容范围**：只存 `## USER PROFILE` 段正文（笔记段不参与版本化 —— 它是流水笔记，不是画像）；
  · **保留策略**：每账号**只留最近 3 份**（写入后立刻裁剪）；
  · **快照时机**：① 人工保存 ② 逐条 apply / 一键导入 ③ agent `update_user_profile` ④ 一键还原 —— **全是"改之前"**；
  · **大小护栏**：`content` ≤ 8000 字（与人工编辑上限一致；超限截断并打日志）；
  · **账号隔离**：全带 `account_id`；`purge_account()` 自省表结构 → 新表**自动**被连带清理（无需改它）；
  · **不做**：完整版本树 / diff / git 化（只留 3 份全文）。
"""
from __future__ import annotations

import time

from tools.memory_profile import has_placeholder, parse_rows, split_sections
from tools.schema_personal import ensure_tables, get_personal_conn

MAX_SNAPSHOT_CHARS = 8000          # 与「人工编辑」上限一致（方案 §5.7 大小护栏）
KEEP_LATEST = 3                    # 只保留最近 3 份（D9）

# 快照原因（前端「🕘 历史画像」要展示它；新增写路径时在这里加一个值）
REASON_INIT = "init"               # 初始化画像
REASON_SUGGEST_IMPORT = "suggest_import"   # 一键导入 AI 建议
REASON_MANUAL_EDIT = "manual_edit"         # 人工编辑保存
REASON_AGENT_UPDATE = "agent_update"       # agent 用 update_user_profile 改的
REASON_RESTORE = "restore"                 # 还原（还原前对当前版本先快照）
REASONS = (REASON_INIT, REASON_SUGGEST_IMPORT, REASON_MANUAL_EDIT, REASON_AGENT_UPDATE, REASON_RESTORE)


def _profile_of(content: str) -> str:
    """取出 `## USER PROFILE` 段正文（这就是要版本化的东西）。"""
    return (split_sections(content or "").get("profile") or "").strip()[:MAX_SNAPSHOT_CHARS]


def _is_empty_profile(profile: str) -> bool:
    """"没有画像内容"的判定 —— 决定要不要占掉 3 个名额中的一个。

    · 空串：没内容；
    · **没有一行 `- 维度：…` 且整体是占位/空话**（如模板里那句「（初始为空…）」）：没内容；
    · 其余（含用户自由写的散文）**都算有内容** —— 判太严会让"覆盖前先留档"这个保护失效。
    """
    if not profile.strip():
        return True
    if parse_rows(profile):
        return False
    return has_placeholder(profile)


def snapshot(account_id: int, content: str, reason: str) -> int:
    """把 `content` 里的画像段存档，返回快照 id（0 = 没存：画像为空）。

    ★ 调用点全部是"**改之前**"：人工保存 / apply / 导入 / agent 更新 / 还原。
    画像段为空时不留存档（空版本没有还原价值，也免得 3 个名额被空版本占满）。
    """
    if not account_id:
        return 0
    profile = _profile_of(content)
    if _is_empty_profile(profile):
        return 0
    reason = (reason or "").strip() or REASON_MANUAL_EDIT
    if reason not in REASONS:
        reason = REASON_MANUAL_EDIT
    if len(profile) >= MAX_SNAPSHOT_CHARS:
        print("[memory] 快照被截断到 %d 字（原文更长）" % MAX_SNAPSHOT_CHARS)
    ensure_tables()
    conn = get_personal_conn()
    try:
        cur = conn.execute(
            "INSERT INTO memory_snapshots (account_id, content, reason, chars, created_at) VALUES (?,?,?,?,?)",
            (int(account_id), profile, reason, len(profile), time.strftime("%Y-%m-%d %H:%M:%S")))
        sid = int(cur.lastrowid)
        # 裁剪：只留最近 KEEP_LATEST 份（方案给的 SQL 口径）
        conn.execute(
            "DELETE FROM memory_snapshots WHERE account_id=? AND id NOT IN "
            "(SELECT id FROM memory_snapshots WHERE account_id=? ORDER BY id DESC LIMIT ?)",
            (int(account_id), int(account_id), KEEP_LATEST))
        conn.commit()
    finally:
        conn.close()
    return sid


def list_snapshots(account_id: int) -> list[dict]:
    """最近 3 份（新的在前）：`{id, created_at, reason, chars, preview, content}`（content=全文）。"""
    if not account_id:
        return []
    ensure_tables()
    conn = get_personal_conn()
    try:
        rows = [dict(r) for r in conn.execute(
            "SELECT id, content, reason, chars, created_at FROM memory_snapshots "
            "WHERE account_id=? ORDER BY id DESC LIMIT ?", (int(account_id), KEEP_LATEST))]
    finally:
        conn.close()
    for r in rows:
        text = " ".join((r.pop("content") or "").split())
        r["preview"] = text[:120] + ("…" if len(text) > 120 else "")
        r["content"] = text          # ★ 2026-09-28 用户实测：只给 120 字预览看不全，列表直接带全文（3 份 × ~1.3KB，可忽略）
    return rows


def get_snapshot(account_id: int, sid: int) -> dict | None:
    """取某一份的全文（还原用）。不属于该账号 → None（调用方回 404，不泄露存在性）。"""
    if not account_id or not sid:
        return None
    ensure_tables()
    conn = get_personal_conn()
    try:
        row = conn.execute("SELECT id, content, reason, chars, created_at FROM memory_snapshots "
                           "WHERE id=? AND account_id=?", (int(sid), int(account_id))).fetchone()
    finally:
        conn.close()
    return dict(row) if row else None


def count_snapshots(account_id: int) -> int:
    """该账号现存快照数（用例与前端徽标用；正常应 ≤3）。"""
    if not account_id:
        return 0
    ensure_tables()
    conn = get_personal_conn()
    try:
        return int(conn.execute("SELECT COUNT(*) FROM memory_snapshots WHERE account_id=?",
                                (int(account_id),)).fetchone()[0])
    finally:
        conn.close()
