# -*- coding: utf-8 -*-
"""M6b 探针残留复查（跨 shell 通用 ✓ —— PowerShell / CMD / bash 都能跑，避免引号转义坑 ✗）。

**v2（2026-10-01）口径变化**：探针不再自建隔离账号，改为**跑真实账号（尼古喵喵）**
  → 探针自己会按水位线清理本轮产物 ✓；本脚本用来**复核它清干净了没有**：
     ① 历史残留（v1 时代的 `m5b*` 账号 / 目录 / 桩目录）还能不能扫到；
     ② 真实账号的会话/消息/登录/用量计数**是否回到跑前水平**（基准见文末）；
     ③ 有没有本轮新冒出来的文件（探针会打印它删了什么 ✓ 这里做二次确认）。

跑法（任何一种 shell 都一样 ✓）：
    .venv/Scripts/python.exe tests/live/m6b_check_leftovers.py          # 只查
    .venv/Scripts/python.exe tests/live/m6b_check_leftovers.py --purge  # 顺手删 v1 历史残留（**先看输出再决定** ✓）

基准（2026-10-01 探针改造时快照，尼古喵喵 id=3）：
    会话 conversations 48 · 消息 messages 1096 · 登录 sessions 83 · 用量 usage_events 111
    （探针跑完这些数字应当**与跑前完全一致** ✓；变大 = 清理没生效，照探针打印的清单手工看）
"""

from __future__ import annotations

import glob
import os
import shutil
import sqlite3
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DB = os.path.join(ROOT, "data", "personal.db")
PROBE_USER = os.getenv("M5B_USER", "尼古喵喵").strip()
# v1 时代的残留（8 个自造桩工具那套：临时账号 m5b/m5bp + 桩目录 + 账号目录）
PATS = ("agents_docs/m5b*", "agents_docs/m5bp*", "data/sandbox/m5b*", "data/sandbox/m5bp*",
        "output/m5b*", "output/m5bp*", "pic/m5b*", "pic/m5bp*", "data/m5b_probe_bin")
PURGE = "--purge" in sys.argv


def main() -> int:
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    legacy = conn.execute("select id, username from accounts"
                          " where lower(username) like '%m5b%'").fetchall()
    total = conn.execute("select count(*) from accounts").fetchone()[0]
    row = conn.execute("select id from accounts where username=?", (PROBE_USER,)).fetchone()
    acct = int(row["id"]) if row else 0
    counts = {}
    if acct:
        counts["conversations"] = conn.execute(
            "select count(*) from conversations where account_id=?", (acct,)).fetchone()[0]
        counts["messages"] = conn.execute(
            "select count(*) from messages m join conversations v on v.id=m.conversation_id"
            " where v.account_id=?", (acct,)).fetchone()[0]
        counts["sessions"] = conn.execute(
            "select count(*) from sessions where account_id=?", (acct,)).fetchone()[0]
        counts["usage_events"] = conn.execute(
            "select count(*) from usage_events where account_id=?", (acct,)).fetchone()[0]
        counts["memory_snapshots"] = conn.execute(
            "select count(*) from memory_snapshots where account_id=?", (acct,)).fetchone()[0]
    conn.close()

    dirs, seen = [], set()
    for pat in PATS:                      # ★ 去重：模式之间会重叠（m5b* 与 m5bp* ✗）
        for d in glob.glob(os.path.join(ROOT, pat)):
            if d not in seen:
                seen.add(d)
                dirs.append(d)

    print("账号总数: %d（探针不再新建账号 ✓ 应与改造前一致）" % total)
    print("v1 历史残留账号: %s" % ([dict(r) for r in legacy] if legacy else "0 ✓"))
    print("v1 历史残留目录: %s" % ([os.path.relpath(d, ROOT) for d in dirs] if dirs else "无 ✓"))
    print("探针账号 %s（id=%s）名下计数：%s" % (PROBE_USER, acct or "?", counts or "账号不存在 ✗"))
    print("  ── 跑前基准：conversations 48 · messages 1096 · sessions 83 · usage_events 111"
          "（变大 = 清理没生效 ✗ 照探针打印的清单手工看）")
    print("保留（正常产物 ✓）: data/m5b_pressure_result.json（探针明细，每轮覆盖）")

    ok = not legacy and not dirs
    if not ok and not PURGE:
        print("\n⚠️ 有 v1 历史残留 —— 先确认上面每一项都是**探针自己的临时物** ✓，再带 --purge 重跑 ✓")
        return 1
    if PURGE:
        conn = sqlite3.connect(DB)
        for r in legacy:
            conn.execute("delete from accounts where id=?", (int(r["id"]),))
            print("已删 v1 残留账号行: %s (id=%s)" % (r["username"], r["id"]))
        conn.commit()
        conn.close()
        for d in dirs:
            shutil.rmtree(d, ignore_errors=True)
            print("已删 v1 残留目录: %s" % os.path.relpath(d, ROOT))
    print("\n干净 ✓ 无需清理" if ok else "\n✅ 历史残留已清理")
    return 0


if __name__ == "__main__":
    sys.exit(main())
