# -*- coding: utf-8 -*-
"""一次性迁移脚本：把散落的账号级用户数据归口到 `user_data/`（v4.0 大点 04 · 议题一）。

用法
----
    python scripts/migrate_user_data.py                 # dry-run：只打印将要发生的事（默认）
    python scripts/migrate_user_data.py --apply         # 真搬（先自动备份，再搬、改库、自检）
    python scripts/migrate_user_data.py --restore <备份目录>   # 回滚：从备份搬回 + 还原库值

迁移映射（2026-10-13 用户拍板；知识库保持旧层级）
------------------------------------------------
    agents_docs/         → user_data/agents_docs/
    output/              → user_data/output/
    pic/                 → user_data/pic/
    rag_knowledge/db/    → user_data/rag_knowledge/db/
    data/sandbox/        → user_data/sandbox/
    data/probe_sets/     → user_data/probe_sets/
    data/tts_ref/        → user_data/tts_ref/
    data/personal.db     → 不动（用户指定暂留 data/）

连带改库
--------
    digest_reports.md_path 存的是**相对项目根**的路径（`agent/digest_engine.py:288`），
    实测 34 行形如 `output\\<账号>\\<标题>_<时间>.md`，前缀统一改写为 `user_data\\output\\`。
    （`messages.*` 里出现的 `agents_docs/…` 字样属**历史聊天正文**，是当时事实，不改写。）

务必先停服
----------
    Windows 下 Chroma 会持有 `rag_knowledge/db/**` 的 sqlite 句柄，服务不停则搬不动。
    默认会先探测端口（默认 8123），端口仍监听就拒绝执行；确需强跑加 `--allow-running`。
"""
from __future__ import annotations

import argparse
import os
import shutil
import socket
import sqlite3
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "data" / "personal.db"

# (旧相对路径, 新相对路径)
MOVES = [
    ("agents_docs", "user_data/agents_docs"),
    ("output", "user_data/output"),
    ("pic", "user_data/pic"),
    ("rag_knowledge/db", "user_data/rag_knowledge/db"),
    ("data/sandbox", "user_data/sandbox"),
    ("data/probe_sets", "user_data/probe_sets"),
    ("data/tts_ref", "user_data/tts_ref"),
]

# 库值改写：(表, 列, 旧前缀, 新前缀)
DB_FIXES = [("digest_reports", "md_path", "output\\", "user_data\\output\\")]


def _size(p: Path) -> tuple:
    if p.is_file():
        return 1, p.stat().st_size
    n = s = 0
    for dp, _dns, fns in os.walk(p):
        for fn in fns:
            try:
                s += (Path(dp) / fn).stat().st_size
            except Exception:
                pass
            n += 1
    return n, s


def _mb(n: int) -> str:
    return "%.2f MB" % (n / 1048576.0)


def preflight(port: int, allow_running: bool) -> bool:
    print("== 0) 前置检查 ==")
    ok = True
    s = socket.socket()
    s.settimeout(0.6)
    busy = s.connect_ex(("127.0.0.1", port)) == 0
    s.close()
    print("   端口 %d ：%s" % (port, "仍在监听（服务没停）" if busy else "已释放"))
    if busy and not allow_running:
        print("   ✗ 拒绝执行：请先停掉服务（Windows 下 Chroma 的 sqlite 句柄会让目录搬不动）。")
        ok = False
    for old, _new in MOVES:
        p = ROOT / old
        print("   %-22s %s" % (old + "/", "存在" if p.exists() else "不存在（跳过）"))
    print("   %-22s %s" % ("user_data/", "已存在（将被复用）" if (ROOT / "user_data").exists() else "将新建"))
    return ok


def dry_run():
    print("\n== 1) 将要搬家（dry-run，未动任何文件）==")
    tot = 0
    for old, new in MOVES:
        src, dst = ROOT / old, ROOT / new
        if not src.exists():
            print("   %-24s → %-38s （源不存在，跳过）" % (old, new)); continue
        n, s = _size(src)
        tot += s
        subs = sorted(x.name for x in src.iterdir())[:6]
        print("   %-24s → %-38s %4d 文件 / %s   %s" % (old, new, n, _mb(s), subs))
    print("   合计 %s（备份将落 docs/v4.0/backups/user_data_move_<时间戳>/）" % _mb(tot))
    print("\n== 2) 将要改库 ==")
    c = sqlite3.connect(str(DB))
    for tb, col, oldp, newp in DB_FIXES:
        n = c.execute("SELECT COUNT(*) FROM %s WHERE %s LIKE ?" % (tb, col), (oldp + "%",)).fetchone()[0]
        print("   %s.%s：%d 行  `%s…` → `%s…`" % (tb, col, n, oldp, newp))
    c.close()
    print("\n== 3) 之后还需人工做的 ==")
    print("   ① 代码改造：全部取路径处改为 import utils.paths（见 research/04 §6.5(c) 清单）")
    print("   ② .gitignore 增 `user_data/`；规则文档与目录树附录同步")
    print("   ③ 起服后按 §6.5(f) 十项验证清单点检")


def backup() -> Path:
    stamp = time.strftime("%Y%m%d_%H%M%S")
    bdir = ROOT / "docs" / "v4.0" / "backups" / ("user_data_move_%s" % stamp)
    bdir.mkdir(parents=True, exist_ok=True)
    print("\n== 1) 备份 → %s ==" % bdir.relative_to(ROOT))
    for old, _new in MOVES:
        src = ROOT / old
        if not src.exists():
            continue
        key = old.replace("/", "__")
        shutil.copytree(src, bdir / key)
        n, s = _size(bdir / key)
        print("   %-24s → %-30s %4d 文件 / %s" % (old, key, n, _mb(s)))
    # 库值原样导出（回滚要用）
    c = sqlite3.connect(str(DB))
    rows = c.execute("SELECT id, md_path FROM digest_reports").fetchall()
    c.close()
    out = bdir / "digest_reports_md_path_before.tsv"
    out.write_text("\n".join("%s\t%s" % (i, m or "") for i, m in rows), encoding="utf-8")
    print("   digest_reports.md_path 原值 %d 行 → %s" % (len(rows), out.name))
    return bdir


def move() -> list:
    print("\n== 2) 搬目录 ==")
    done = []
    for old, new in MOVES:
        src, dst = ROOT / old, ROOT / new
        if not src.exists():
            print("   %-24s （源不存在，跳过）" % old); continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        assert not dst.exists(), "目标已存在，请先人工处理：%s" % dst
        # 入库的占位文件（.gitkeep）在原目录里，搬家前记下来，搬完在新目录补一份
        keeps = [str(p.relative_to(src)) for p in src.rglob(".gitkeep")]
        shutil.move(str(src), str(dst))
        for k in keeps:
            tgt = dst / k
            tgt.parent.mkdir(parents=True, exist_ok=True)
            if not tgt.exists():
                tgt.write_text("", encoding="utf-8")
        n, s = _size(dst)
        print("   %-24s → %-38s %4d 文件 / %s%s" % (old, new, n, _mb(s),
              ("  （补回 .gitkeep: %s）" % ", ".join(keeps)) if keeps else ""))
        done.append((old, new))
    return done


def fix_db() -> None:
    print("\n== 3) 改库（digest_reports.md_path）==")
    c = sqlite3.connect(str(DB))
    for tb, col, oldp, newp in DB_FIXES:
        n = c.execute("SELECT COUNT(*) FROM %s WHERE %s LIKE ?" % (tb, col), (oldp + "%",)).fetchone()[0]
        sql = "UPDATE {t} SET {c} = ? || SUBSTR({c}, ?) WHERE {c} LIKE ?".format(t=tb, c=col)
        cur = c.execute(sql, (newp, len(oldp) + 1, oldp + "%"))
        after = c.execute("SELECT COUNT(*) FROM %s WHERE %s LIKE ?" % (tb, col), (newp + "%",)).fetchone()[0]
        print("   %s.%s：%d 行改写 → 新前缀 %d 行（rowcount=%d）" % (tb, col, n, after, cur.rowcount))
        assert after == n, "改写行数不一致，请立即用备份还原"
    c.commit()
    c.close()


def verify() -> None:
    print("\n== 4) 自检 ==")
    ok = True
    for old, new in MOVES:
        o, n = ROOT / old, ROOT / new
        if o.exists():
            print("   ✗ 旧路径仍在：%s" % old); ok = False
        if not n.exists():
            print("   ✗ 新路径缺失：%s" % new); ok = False
    c = sqlite3.connect(str(DB))
    bad = c.execute("SELECT COUNT(*) FROM digest_reports WHERE md_path LIKE 'output\\%'").fetchone()[0]
    good = c.execute("SELECT COUNT(*) FROM digest_reports WHERE md_path LIKE 'user_data\\output\\%'").fetchone()[0]
    c.close()
    print("   digest_reports：待改写残留 %d 行 / 新前缀 %d 行" % (bad, good))
    if bad:
        ok = False
    print("   user_data/ 子目录：" + str(sorted(x.name for x in (ROOT / "user_data").iterdir())) if (ROOT / "user_data").exists() else "   user_data/ 不存在")
    print("   结果：%s" % ("通过" if ok else "有问题，见上"))
    if not ok:
        sys.exit(3)


def restore(bdir: str) -> None:
    b = Path(bdir)
    assert b.is_dir(), "备份目录不存在：%s" % b
    print("== 回滚：从 %s ==" % b)
    for old, new in MOVES:
        key = old.replace("/", "__")
        src, dst = b / key, ROOT / old
        if not src.exists():
            continue
        if dst.exists():
            print("   %-24s 目标已存在，跳过（请人工确认）" % old); continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(dst))
        print("   %-24s ← %s" % (old, key))
        # 清掉新根下的对应目录（已搬回旧位置）
        newp = ROOT / new
        if newp.exists():
            shutil.rmtree(newp, ignore_errors=True)
    f = b / "digest_reports_md_path_before.tsv"
    if f.exists():
        c = sqlite3.connect(str(DB))
        for line in f.read_text(encoding="utf-8").splitlines():
            if "\t" not in line:
                continue
            rid, mp = line.split("\t", 1)
            c.execute("UPDATE digest_reports SET md_path=? WHERE id=?", (mp or None, int(rid)))
        c.commit()
        c.close()
        print("   digest_reports.md_path 已按备份还原")
    print("   回滚完成；代码侧请用 git 还原。")


def main():
    ap = argparse.ArgumentParser(description="账号级用户数据归口迁移（user_data/）")
    ap.add_argument("--apply", action="store_true", help="真执行（默认 dry-run）")
    ap.add_argument("--restore", metavar="备份目录", help="从备份回滚")
    ap.add_argument("--port", type=int, default=8123, help="服务端口（用于停服自检）")
    ap.add_argument("--allow-running", action="store_true", help="端口仍监听也强跑（危险）")
    a = ap.parse_args()

    if a.restore:
        restore(a.restore); return
    if not preflight(a.port, a.allow_running):
        sys.exit(2)
    if not a.apply:
        dry_run(); print("\n（dry-run 结束；确认无误后加 --apply 执行）"); return
    backup()
    move()
    fix_db()
    verify()
    print("\n迁移完成。下一步：代码改造 + .gitignore/文档同步 + 起服验证（见 research/04 §6.5）。")


if __name__ == "__main__":
    main()
