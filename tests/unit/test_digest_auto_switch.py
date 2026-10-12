# -*- coding: utf-8 -*-
"""周报自动生成：**账号级开关，默认关**（用户 2026-10-11 裁定）。

事故背景：用户把服务挂着过夜，调度器（APScheduler daily@9:00）把**所有**有启用订阅的账号的周报
都自动跑了一遍；那晚 VPN 是关的 → 每个账号都吐一大段 `APIConnectionError`（走代理连接被拒）✗。
裁定：自动任务必须由**账号显式开启**（`accounts.digest_auto`，默认 0=关）；订阅自己的 `enabled`
不再是充分条件。本文件钉死：调度器**只跑开关打开的账号**、且**没有开关列时安全跳过**（不误跑）。

跑法：`pytest tests/unit/test_digest_auto_switch.py -q`
"""
import os
import sqlite3
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
os.environ.pop("PYTHONPATH", None)

from api import digest_scheduler as ds      # noqa: E402

ok, bad = [], []


def check(name, cond, extra=""):
    (ok if cond else bad).append(name)
    print("%s %s%s" % ("  ✅" if cond else "  ❌", name, (" | " + str(extra)) if extra else ""))


def _db(with_col: bool = True) -> sqlite3.Connection:
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.execute("""CREATE TABLE accounts (
                     id INTEGER PRIMARY KEY, username TEXT,
                     digest_auto INTEGER NOT NULL DEFAULT 0)""" if with_col else
              """CREATE TABLE accounts (id INTEGER PRIMARY KEY, username TEXT)""")
    c.execute("""CREATE TABLE digest_subs (
                     id INTEGER PRIMARY KEY, owner_id INTEGER, scope TEXT DEFAULT 'personal',
                     name TEXT, keywords TEXT, schedule TEXT DEFAULT 'weekly',
                     lang TEXT DEFAULT 'zh', enabled INTEGER DEFAULT 1)""")
    # 甲：订阅启用 + 开关**关**（默认）→ 不该跑 ✗
    # 乙：订阅启用 + 开关**开** → 该跑 ✓
    # 丙：开关开但订阅停用 → 不跑 ✓
    if with_col:
        c.execute("INSERT INTO accounts (id, username, digest_auto) VALUES (1, 'kyle_off', 0)")
        c.execute("INSERT INTO accounts (id, username, digest_auto) VALUES (2, 'kyle_on', 1)")
        c.execute("INSERT INTO accounts (id, username, digest_auto) VALUES (3, 'kyle_disabled', 1)")
    else:                                   # 老库：连列都没有（夹具必须与 schema 一致 ✗ 否则报的是自己的错）
        c.execute("INSERT INTO accounts (id, username) VALUES (1, 'kyle_off')")
        c.execute("INSERT INTO accounts (id, username) VALUES (2, 'kyle_on')")
        c.execute("INSERT INTO accounts (id, username) VALUES (3, 'kyle_disabled')")
    c.execute("INSERT INTO digest_subs (id, owner_id, enabled, schedule) VALUES (11, 1, 1, 'daily')")
    c.execute("INSERT INTO digest_subs (id, owner_id, enabled, schedule) VALUES (12, 2, 1, 'daily')")
    c.execute("INSERT INTO digest_subs (id, owner_id, enabled, schedule) VALUES (13, 3, 0, 'daily')")
    c.commit()
    return c


def test_digest_auto_switch():
    ran = []
    fake = type(sys)("agent.digest_engine")
    fake.run_digest = lambda sid, aid: (ran.append((sid, aid)) or {"ok": True, "item_count": 1,
                                                                  "md_path": "/tmp/x.md"})
    sys.modules["agent.digest_engine"] = fake
    _saved_conn, _saved_ensure = ds.get_personal_conn, ds.ensure_tables
    _saved_notify = ds._notify_ws
    ds.ensure_tables = lambda: None
    ds._notify_ws = lambda *a, **k: None
    try:
        # 甲：默认关 → 一个都不跑
        ds.get_personal_conn = lambda: _db()
        ds.run_all_due(None)
        check("默认关：只跑开关打开的账号（关的那个不跑 ✓）", ran == [(12, 2)], ran)

        # 乙：没有 digest_auto 列（老库未迁移）→ 安全跳过，不误跑
        ran.clear()
        ds.get_personal_conn = lambda: _db(with_col=False)
        try:
            ds.run_all_due(None)
            check("老库无开关列 → 不跑（宁可不动，也不误跑）", ran == [], ran)
        except Exception as e:  # noqa: BLE001
            check("老库无开关列 → 不抛异常", False, "%s: %s" % (type(e).__name__, e))
    finally:
        ds.get_personal_conn, ds.ensure_tables = _saved_conn, _saved_ensure
        ds._notify_ws = _saved_notify
        sys.modules.pop("agent.digest_engine", None)

    print("\n" + "=" * 62)
    print("通过 %d ／ 失败 %d" % (len(ok), len(bad)))
    for n in bad:
        print("  -", n)
    assert not bad, "周报自动开关自测失败 %d 项：%s" % (len(bad), "；".join(bad))
