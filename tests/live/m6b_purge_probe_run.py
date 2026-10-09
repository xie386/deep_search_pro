# -*- coding: utf-8 -*-
"""M6b 探针「**中断残留**」清除器 **v2**（修 2026-10-02 事故 ✗）。

事故：v1 的 `--last-conv N` 是「该账号**最新 N 条会话**」→ 用户按 N=4 跑，把**两条真实会话
（1225 / 1278）连同探针残留一起删了** ✗。v2 的安全设计：

  ① **只认探针会话** ✓：标题必须与探针题集里的问句对得上（前缀匹配，标题被截到 30 字 ✓）；
  ② `--last-conv N` 现在只在**探针候选**里取最新 N 条 ✓ —— 真实会话**永远不会**被它选中 ✓；
  ③ 想删"不像探针"的会话，必须显式 `--allow-real` ✓（默认拒绝并列出它怕什么 ✗）；
  ④ 仍然**默认预演**（不加 `--purge` 不动数据 ✓），并打印每条会话的标题与消息数供你过目 ✓。

跑法（三 shell 一致）：
    .venv/Scripts/python.exe tests/live/m6b_purge_probe_run.py --last-conv 2           # 预演
    .venv/Scripts/python.exe tests/live/m6b_purge_probe_run.py --last-conv 2 --purge   # 执行
    .venv/Scripts/python.exe tests/live/m6b_purge_probe_run.py --since-conv-id 1469 --purge
环境变量：M5B_USER（默认 尼古喵喵）
"""

from __future__ import annotations

import importlib.util
import os
import sqlite3
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DB = os.path.join(ROOT, "data", "personal.db")
PROBE = os.path.join(ROOT, "tests", "live", "m5b_pressure_probe.py")
USER = os.getenv("M5B_USER", "尼古喵喵").strip()
PURGE = "--purge" in sys.argv
NO_SESSIONS = "--no-sessions" in sys.argv
ALLOW_REAL = "--allow-real" in sys.argv
SCAN = 60          # 默认扫描该账号最新 60 条，用于找「探针候选」


def _arg(name: str, default: int = 0) -> int:
    if name in sys.argv:
        i = sys.argv.index(name)
        if i + 1 < len(sys.argv):
            return int(sys.argv[i + 1])
    return default


def _probe_questions() -> list:
    """从探针脚本里读回题集（**单一事实来源** ✓ 不复制题面 ✗）。"""
    os.environ.setdefault("SKIP_LLM", "1")
    spec = importlib.util.spec_from_file_location("probe_for_purge", PROBE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return [it["q"] for it in mod.Q]


def _looks_like_probe(title: str, questions: list) -> bool:
    t = (title or "").strip()
    if not t:
        return False
    for q in questions:                      # 标题是问句前缀（被截到 30 字）✓
        if t == q or q.startswith(t) or t.startswith(q[:20]):
            return True
    return False


def main() -> int:
    since_id, last_conv = _arg("--since-conv-id", 0), _arg("--last-conv", 0)
    if not since_id and not last_conv:
        print("✗ 二选一：--last-conv N（只在**探针候选**里取最新 N 条 ✓）或 --since-conv-id ID")
        return 2
    questions = _probe_questions()
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    acc_row = conn.execute("SELECT id FROM accounts WHERE username=?", (USER,)).fetchone()
    if not acc_row:
        print("✗ 账号不存在：%s" % USER)
        return 2
    acc = int(acc_row["id"])

    recent = [dict(r) for r in conn.execute(
        "SELECT id, thread_id, title, message_count FROM conversations"
        " WHERE account_id=? ORDER BY id DESC LIMIT ?", (acc, SCAN))]
    cand = [c for c in recent if _looks_like_probe(c["title"], questions)]
    if last_conv:
        targets = cand[:last_conv]
        if len(targets) < last_conv:
            print("⚠️ 只找到 %d 条探针候选（你要 %d 条）—— 不瞎凑 ✓" % (len(targets), last_conv))
    else:
        targets = [c for c in recent if c["id"] >= since_id]
    if not targets:
        print("✓ 没有要清的会话（账号 %s 名下最新 %d 条里没有探针残留 ✓）" % (USER, len(recent)))
        return 0

    risky = [c for c in targets if not _looks_like_probe(c["title"], questions)]
    threads = [c["thread_id"] for c in targets]
    cids = [c["id"] for c in targets]
    ph = ",".join("?" * len(threads))
    ph2 = ",".join("?" * len(cids))
    n_msg = conn.execute("SELECT COUNT(*) FROM messages WHERE conversation_id IN (%s)" % ph2, cids).fetchone()[0]
    n_use = conn.execute("SELECT COUNT(*) FROM usage_events WHERE thread_id IN (%s)" % ph, threads).fetchone()[0]
    ts_row = conn.execute("SELECT MIN(created_ts) FROM usage_events WHERE thread_id IN (%s)" % ph, threads).fetchone()[0]
    n_sess = 0
    if ts_row is not None and not NO_SESSIONS:
        n_sess = conn.execute("SELECT COUNT(*) FROM sessions WHERE account_id=? AND login_at>=?",
                              (acc, float(ts_row))).fetchone()[0]

    print("账号：%s (id=%s) ｜ 模式：%s" % (USER, acc, "★ 真删" if PURGE else "预演（不动数据 ✓）"))
    print("探针候选（标题与题集对得上）：%d 条；本次命中 %d 条：" % (len(cand), len(targets)))
    for c in targets:
        mark = "★探针" if _looks_like_probe(c["title"], questions) else "⚠️不像探针"
        print("   id=%-6s 消息 %-4s %-10s %s" % (c["id"], c["message_count"], mark, (c["title"] or "")[:40]))
    print("将删除：messages %d · conversations %d · usage_events %d · sessions %d"
          % (n_msg, len(cids), n_use, n_sess))
    if risky:
        print("\n⚠️ 其中有 %d 条**不像探针会话** ✗：%s"
              % (len(risky), [(c["id"], (c["title"] or "")[:24]) for c in risky]))
        if not ALLOW_REAL:
            print("   → 默认**拒绝执行** ✓（真实会话不会被我误删）。确认无误要删就加 `--allow-real` ✓")
            return 3
    if not PURGE:
        print("\n确认无误后加 `--purge` 执行 ✓（先备份：见 docs/v3.1/backups/ 的 sqlite3 在线备份写法）")
        return 0

    conn.execute("DELETE FROM messages WHERE conversation_id IN (%s)" % ph2, cids)
    conn.execute("DELETE FROM conversations WHERE id IN (%s)" % ph2, cids)
    conn.execute("DELETE FROM usage_events WHERE thread_id IN (%s)" % ph, threads)
    if n_sess and not NO_SESSIONS:
        conn.execute("DELETE FROM sessions WHERE account_id=? AND login_at>=?", (acc, float(ts_row)))
    conn.commit()
    conn.close()
    print("\n✅ 已清除：messages %d · conversations %d · usage_events %d · sessions %d（memory_snapshots 不动 ✓ 那是回滚点）"
          % (n_msg, len(cids), n_use, n_sess))
    return 0


if __name__ == "__main__":
    sys.exit(main())
