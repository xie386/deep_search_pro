# -*- coding: utf-8 -*-
"""会话还原器：把**误删的会话**从既有备份库搬回当前库（默认预演 ✓ 不动数据）。

背景（2026-10-02 事故）：清除脚本 v1 的 `--last-conv N` 语义太宽 ✗，把真实会话
（1225 / 1278）连同探针残留一起删了。已修 v2 ✓；本脚本负责**把误删的补回来**。

安全性设计：
  ① **默认预演**，`--purge` 才真写 ✓；
  ② 只 **INSERT 当前库里已不存在**的 id ✓（id 被占用就跳过并报告 ✗ 绝不覆盖现有数据）；
  ③ 只从你点名的备份库读（**只读模式打开** ✓ 备份文件不会被改）；
  ④ 顺带按 `thread_id` 把该会话的 `usage_events` 一起补回 ✓（用量统计也对得上）。

跑法：
    .venv/Scripts/python.exe tests/live/m6b_restore_convs.py --from docs/v3.1/backups/personal_before_m5b_cleanup_20261001.db --ids 1225,1278
    ... 确认后加 --purge
"""

from __future__ import annotations

import os
import sqlite3
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DB = os.path.join(ROOT, "data", "personal.db")


def _arg(name: str, default: str = "") -> str:
    if name in sys.argv:
        i = sys.argv.index(name)
        if i + 1 < len(sys.argv):
            return sys.argv[i + 1]
    return default


def main() -> int:
    src, ids_txt = _arg("--from"), _arg("--ids")
    purge = "--purge" in sys.argv
    if not src or not ids_txt:
        print("✗ 用法：m6b_restore_convs.py --from <备份.db> --ids 1225,1278 [--purge]")
        return 2
    if not os.path.isabs(src):
        src = os.path.join(ROOT, src)
    if not os.path.exists(src):
        print("✗ 备份不存在：%s" % src)
        return 2
    ids = [int(x) for x in ids_txt.replace(" ", "").split(",") if x]

    bak = sqlite3.connect("file:%s?mode=ro" % src.replace("\\", "/"), uri=True)
    bak.row_factory = sqlite3.Row
    live = sqlite3.connect(DB)
    live.row_factory = sqlite3.Row

    plan = []
    for cid in ids:
        row = bak.execute("SELECT * FROM conversations WHERE id=?", (cid,)).fetchone()
        if not row:
            print("⚠️ 备份里没有会话 id=%s（跳过 ✓）" % cid)
            continue
        msgs = list(bak.execute("SELECT * FROM messages WHERE conversation_id=?", (cid,)))
        exists = live.execute("SELECT id FROM conversations WHERE id=?", (cid,)).fetchone()
        plan.append((dict(row), [dict(m) for m in msgs], bool(exists)))

    print("备份：%s（只读 ✓）" % os.path.relpath(src, ROOT) if src.startswith(ROOT) else "备份：%s" % src)
    print("当前库：%s ｜ 模式：%s\n" % (os.path.relpath(DB, ROOT), "★ 真写" if purge else "预演（不动数据 ✓）"))
    for row, msgs, exists in plan:
        print("  id=%-6s 消息 %-4s 标题「%s」%s"
              % (row["id"], len(msgs), (row.get("title") or "")[:34],
                 "  ⚠️ 当前库已存在同 id → **跳过**（绝不覆盖 ✓）" if exists else "  ✓ 可还原"))
    todo = [(r, m) for r, m, e in plan if not e]
    if not todo:
        print("\n没有可还原的会话（要么已存在、要么备份里没有）✓")
        return 0
    n_use = 0
    for row, _m in todo:
        n_use += len(list(bak.execute("SELECT id FROM usage_events WHERE thread_id=?", (row["thread_id"],))))
    print("\n将写入：conversations %d · messages %d · usage_events %d（其余表不动 ✓）"
          % (len(todo), sum(len(m) for _r, m in todo), n_use))
    if not purge:
        print("\n确认无误后加 `--purge` 执行 ✓（建议先对当前库做一次在线备份：见 docs/v3.1/backups/ 的写法）")
        return 0

    for row, msgs in todo:
        cols = [k for k in row.keys()]
        live.execute("INSERT INTO conversations (%s) VALUES (%s)"
                     % (",".join(cols), ",".join("?" * len(cols))), [row[c] for c in cols])
        for m in msgs:
            mcols = [k for k in m.keys()]
            live.execute("INSERT INTO messages (%s) VALUES (%s)"
                         % (",".join(mcols), ",".join("?" * len(mcols))), [m[c] for c in mcols])
        for u in list(bak.execute("SELECT * FROM usage_events WHERE thread_id=?", (row["thread_id"],))):
            ud = dict(u)
            ucols = [k for k in ud.keys()]
            live.execute("INSERT OR IGNORE INTO usage_events (%s) VALUES (%s)"
                         % (",".join(ucols), ",".join("?" * len(ucols))), [ud[c] for c in ucols])
    live.commit()
    live.close()
    bak.close()
    print("\n✅ 已还原 %d 条会话（含消息与用量）✓ 复核：m6b_check_leftovers.py" % len(todo))
    return 0


if __name__ == "__main__":
    sys.exit(main())
