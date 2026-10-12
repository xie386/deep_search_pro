# -*- coding: utf-8 -*-
"""v4.0 大点 03：**测评隔离**（用户 2026-10-11 三条裁定）离线自测。

覆盖两件事（都不碰真账号数据 ✓）：
  ① **测评会话不进会话库**：`create_session(kind='probe')` 的会话不出现在 `list_sessions()`；
     `purge_probe_sessions()` 把它们连消息一起清掉（中断/崩溃留下的也能清）；
  ② **画像/笔记守得住**：`snapshot_profile()` 整目录快照 → `restore_profile()` 还原被改的文件、
     删掉本轮新冒出来的文件；快照目录**不参与**水位线清理（否则还原源被删就没得还原）。

跑法：`pytest tests/unit/test_probe_isolation.py -q`（也随 `pytest tests/unit` 一起跑 ✓）
"""
import os
import shutil
import sqlite3
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
os.environ.pop("PYTHONPATH", None)

from agent import conversation_store as cs      # noqa: E402
from tools import probe_runner as pr            # noqa: E402

ok, bad = [], []


def check(name, cond, extra=""):
    (ok if cond else bad).append(name)
    print("%s %s%s" % ("  ✅" if cond else "  ❌", name, (" | " + str(extra)) if extra else ""))


_TMPDB = os.path.join(ROOT, "tests", "test_out", "_probe_isolation_tmp.db")


def _new_conn():
    """每次开**新**连接（与 `get_personal_conn()` 的真实行为一致 —— 它会在用完后 close ✓）。

    ★ 不能用 `:memory:`：`create_session()` 会关掉连接，内存库随之消失（实测报
      `Cannot operate on a closed database`）。
    """
    c = sqlite3.connect(_TMPDB)
    c.row_factory = sqlite3.Row
    return c


def _init_db():
    if os.path.exists(_TMPDB):
        os.remove(_TMPDB)
    c = _new_conn()
    c.execute("""CREATE TABLE conversations (
                     id INTEGER PRIMARY KEY AUTOINCREMENT, account_id INTEGER NOT NULL,
                     thread_id TEXT NOT NULL UNIQUE, title TEXT, token_usage INTEGER DEFAULT 0,
                     message_count INTEGER DEFAULT 0, kind TEXT NOT NULL DEFAULT 'chat',
                     created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                     updated_at DATETIME DEFAULT CURRENT_TIMESTAMP)""")
    c.execute("""CREATE TABLE messages (
                     id INTEGER PRIMARY KEY AUTOINCREMENT, conversation_id INTEGER NOT NULL,
                     turn_index INTEGER NOT NULL, role TEXT, content TEXT)""")
    c.commit()
    c.close()


def test_probe_isolation():
    # ---------------- ① 测评会话不进会话库 ----------------
    _init_db()
    _real = cs.get_personal_conn
    cs.get_personal_conn = _new_conn                      # 临时库上验，不碰真库 ✓
    try:
        me = 7
        chat = cs.create_session(me, "我的正常会话")
        probe = cs.create_session(me, "新对话", kind=cs.PROBE_KIND)
        weird = cs.create_session(me, "新对话", kind="乱填的")
        check("建会话：默认 kind=chat", chat.get("kind") == cs.CHAT_KIND, chat.get("kind"))
        check("建会话：kind=probe 生效", probe.get("kind") == "probe", probe.get("kind"))
        check("建会话：**未知 kind 落回 chat**（不留能进库又会漏删的中间态）",
              weird.get("kind") == cs.CHAT_KIND, weird.get("kind"))
        names = [s["thread_id"] for s in cs.list_sessions(me)]
        check("会话库：只列 chat，**测评会话从不出现** ✓",
              chat["thread_id"] in names and probe["thread_id"] not in names, len(names))
        c2 = _new_conn()
        c2.execute("INSERT INTO messages (conversation_id, turn_index, role, content) VALUES (?,0,'user','x')",
                   (probe["id"],))
        c2.commit()
        c2.close()
        gone = cs.purge_probe_sessions(me)
        check("清理：测评会话连消息一起删",
              gone == {"conversations": 1, "messages": 1}, gone)
        left = [s["thread_id"] for s in cs.list_sessions(me)]
        check("清理后：正常会话仍在（只清 probe）",
              set(left) == {chat["thread_id"], weird["thread_id"]}, left)
        check("清理：没有 probe 会话时是幂等的",
              cs.purge_probe_sessions(me) == {"conversations": 0, "messages": 0})
    finally:
        cs.get_personal_conn = _real
        try:
            os.remove(_TMPDB)
        except OSError:
            pass

    # ---------------- ② 画像 / 笔记：快照 → 还原 ----------------
    scratch = "_probe_selftest_profile"
    pdir = pr.profile_dir(scratch)
    sbox = os.path.join(pr.SANDBOX_DIR, scratch)
    shutil.rmtree(pdir, ignore_errors=True)
    shutil.rmtree(sbox, ignore_errors=True)
    try:
        os.makedirs(pdir, exist_ok=True)
        memo = os.path.join(pdir, "MEMORY.md")
        notes = os.path.join(pdir, "notes.md")
        with open(memo, "w", encoding="utf-8") as f:
            f.write("# 用户记忆画像\n\n- 预算范围：≤100 元（人工）\n")
        with open(notes, "w", encoding="utf-8") as f:
            f.write("- 笔记：原样\n")
        before = open(memo, encoding="utf-8").read()

        snap = pr.snapshot_profile(scratch)
        check("快照：整个 agents_docs/{用户名}/ 都进沙箱（2 个文件）",
              snap["count"] == 2 and os.path.isfile(os.path.join(ROOT, *snap["dir"].split("/"), "MEMORY.md")),
              snap["count"])
        check("快照：进了沙箱的备份目录（相对路径落在 data/sandbox/）",
              snap["dir"].replace("\\", "/").startswith("data/sandbox/%s/profile_backup_" % scratch), snap["dir"])
        check("快照目录**不参与**水位线清理（否则还原源被删就还原不了）",
              not any("profile_backup_" in f for f in pr.snapshot_files(scratch)))

        # 模拟测评期间：agent 改了画像、追加了笔记、还新建了一个文件
        with open(memo, "w", encoding="utf-8") as f:
            f.write("# 用户记忆画像\n\n- 预算范围：≤999 元（会话 2026-10-11）\n非人工改的一行\n")
        with open(notes, "a", encoding="utf-8") as f:
            f.write("- 笔记：被追加了\n")
        extra = os.path.join(pdir, "agent_new.md")
        with open(extra, "w", encoding="utf-8") as f:
            f.write("测评期间新建\n")

        res = pr.restore_profile(scratch, snap)
        check("还原：被改的 MEMORY.md 回到测试前**逐字一致**",
              open(memo, encoding="utf-8").read() == before, res)
        check("还原：被追加的笔记也回到测试前",
              open(notes, encoding="utf-8").read() == "- 笔记：原样\n")
        check("还原：本轮**新冒出来**的文件被删掉", not os.path.exists(extra) and "agent_new.md" in res["removed"],
              res["removed"])
        check("还原：记账清晰（还原了 2 个文件 / 删了 1 个）",
              len(res["restored"]) == 2 and len(res["removed"]) == 1 and res["ok"] is True, res)
        check("还原：不留原子写的临时文件（*.restore_tmp）",
              not [f for f in os.listdir(pdir) if f.endswith(".restore_tmp")], os.listdir(pdir))

        # 再还原一次：没有任何改动 → 全部 unchanged（幂等 ✓）
        res2 = pr.restore_profile(scratch, snap)
        check("还原幂等：已一致时不改动", res2["restored"] == [] and res2["unchanged"] == 2, res2)

        # 快照目录只留最近一份
        snap2 = pr.snapshot_profile(scratch)
        gone_bak = pr._prune_backups(sbox, keep=1)
        left_bak = [d for d in os.listdir(sbox) if d.startswith("profile_backup_")]
        check("画像快照只留最近一份", len(left_bak) == 1 and len(gone_bak) == 1,
              (left_bak, gone_bak))
        check("留的是**最新**那份", left_bak and left_bak[0] == os.path.basename(snap2["dir"]), left_bak)
    finally:
        shutil.rmtree(pdir, ignore_errors=True)
        shutil.rmtree(sbox, ignore_errors=True)

    print("\n" + "=" * 62)
    print("通过 %d ／ 失败 %d" % (len(ok), len(bad)))
    for n in bad:
        print("  -", n)
    assert not bad, "测评隔离自测失败 %d 项：%s" % (len(bad), "；".join(bad))
