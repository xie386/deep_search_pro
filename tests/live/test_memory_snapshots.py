# -*- coding: utf-8 -*-
"""M3-9 用例：画像版本快照与一键还原（`tools/memory_snapshots.py` + `/api/memory/snapshots|restore`）。

判据来源：`docs/v3.0/M3-用户画像沉淀方案.md` §5.7（数据模型与保留策略）/ D9。
★ 纪律：**不动任何真实账号的 MEMORY.md** —— DB 层用例只传字符串；API 层用例用临时账号 + 临时用户目录，
   收尾把目录与账号一起清掉。

运行：.venv/Scripts/python.exe -m pytest tests/test_memory_snapshots.py -q
"""
import json
import os
import shutil
import sys
import time

import pytest
from fastapi import HTTPException

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

from api import customize as cust                       # noqa: E402
from tools import memory_profile as mp                  # noqa: E402
from tools import memory_snapshots as ms                # noqa: E402
from tools.schema_personal import get_personal_conn, purge_account   # noqa: E402

PROF = lambda tag: ("## USER PROFILE（用户画像）\n\n- 身份与领域：%s（来源：人工 · 2026-09-27）\n\n"
                    "## MEMORY（助手笔记）\n\n- 笔记 %s 不该进快照\n" % (tag, tag))

# 真实文件形态（带标题与老标记）—— restore/save 的用例都用它，贴近生产
REAL_FILE = lambda tag: ("# 用户记忆画像（MEMORY）\n\n<!-- memory_init: done -->\n\n" + PROF(tag))


def mk_account(tag):
    conn = get_personal_conn()
    uname = "m3snap_%s_%d" % (tag, int(time.time() * 1000) % 1000000)
    try:
        aid = int(conn.execute("INSERT INTO accounts (username, password_hash, role) VALUES (?,?,?)",
                               (uname, "x", "user")).lastrowid)
        token = "tok_%d_%d" % (aid, int(time.time() * 1000) % 1000000)
        conn.execute("INSERT INTO sessions (token, account_id, login_at) VALUES (?,?,?)", (token, aid, time.time()))
        conn.commit()
    finally:
        conn.close()
    return aid, token, uname


@pytest.fixture()
def acct():
    aid, token, uname = mk_account("a")
    yield {"aid": aid, "token": token, "uname": uname}
    purge_account(aid)
    shutil.rmtree(os.path.join(ROOT, "agents_docs", uname), ignore_errors=True)


# ---------------------------------------------------------------- ① 存什么 / 不存什么
def test_snapshot_keeps_profile_section_only(acct):
    sid = ms.snapshot(acct["aid"], PROF("v1"), ms.REASON_MANUAL_EDIT)
    assert sid > 0
    got = ms.get_snapshot(acct["aid"], sid)
    assert "身份与领域：v1" in got["content"]
    assert "不该进快照" not in got["content"], "★ 只版本化画像段（笔记段是流水，不是画像）"
    assert got["reason"] == "manual_edit" and got["chars"] == len(got["content"])


def test_empty_or_template_placeholder_profile_is_not_snapshotted(acct):
    """空画像不留档：空版本没有还原价值，还会白占 3 个名额。

    ★ 注意"空"的三种形态：真正的空、**模板里那句「（初始为空…）」**、以及没有画像段的文件。
    前两种都不该占名额 —— 但**自由文本形式的画像必须留档**（用户可能不按行格式写）。
    """
    assert ms.snapshot(acct["aid"], "## USER PROFILE（用户画像）\n\n（初始为空。可在「定制助手」页点击…）\n",
                       ms.REASON_INIT) == 0, "模板占位句不算画像内容"
    assert ms.snapshot(acct["aid"], "# 没有画像段的文件", ms.REASON_INIT) == 0
    assert ms.count_snapshots(acct["aid"]) == 0
    sid = ms.snapshot(acct["aid"], "## USER PROFILE（用户画像）\n\n用户喜欢先看结论，讨厌长铺垫。\n",
                      ms.REASON_MANUAL_EDIT)
    assert sid > 0, "自由文本（不按 - 维度： 写）也要留档，否则覆盖时真的会丢"


# ---------------------------------------------------------------- ② 保留策略：只留 3 份
def test_keep_only_latest_three(acct):
    ids = [ms.snapshot(acct["aid"], PROF("v%d" % i), ms.REASON_MANUAL_EDIT) for i in range(5)]
    assert ms.count_snapshots(acct["aid"]) == 3, "★ 写 5 次只剩 3 份（D9）"
    left = [r["id"] for r in ms.list_snapshots(acct["aid"])]
    assert left == sorted(ids[-3:], reverse=True), "留的必须是最近 3 份，且新的在前"
    assert ms.get_snapshot(acct["aid"], ids[0]) is None, "被裁掉的老版本要真的取不到"


def test_unknown_reason_falls_back(acct):
    sid = ms.snapshot(acct["aid"], PROF("v"), "who-knows")
    assert ms.get_snapshot(acct["aid"], sid)["reason"] == ms.REASON_MANUAL_EDIT


def test_chars_capped_and_preview(acct):
    long_fact = "很长的画像内容" * 2000
    sid = ms.snapshot(acct["aid"], "## USER PROFILE（用户画像）\n\n- 身份与领域：%s\n" % long_fact, ms.REASON_INIT)
    got = ms.get_snapshot(acct["aid"], sid)
    assert got["chars"] <= ms.MAX_SNAPSHOT_CHARS, "★ 8000 字护栏（与人工编辑上限一致）"
    row = ms.list_snapshots(acct["aid"])[0]
    assert row["preview"] and len(row["preview"]) <= 121, "列表里的短预览仍要限长"
    # ★ 2026-09-28 用户实测后改：列表**同时带全文 content** —— 只给 120 字预览"看不全"，
    #   前端点一下即可展开看全部。原来那条"不许带 content"的断言是旧契约，已废。
    assert row.get("content") and "身份与领域" in row["content"], "每份快照都要带全文"


# ---------------------------------------------------------------- ③ 账号隔离与清理
def test_account_isolation(acct):
    aid2, _, _ = mk_account("b")
    try:
        sid = ms.snapshot(acct["aid"], PROF("mine"), ms.REASON_INIT)
        assert ms.get_snapshot(aid2, sid) is None, "★ 别人的快照取不到（不泄露存在性）"
        ms.snapshot(aid2, PROF("theirs"), ms.REASON_INIT)
        assert [r["id"] for r in ms.list_snapshots(acct["aid"])] == [sid]
        assert ms.count_snapshots(aid2) == 1
    finally:
        purge_account(aid2)


def test_purge_account_cleans_snapshots(acct):
    """★ 计划里点名的风险：删账号必须连带清掉这张新表（靠 purge_account 自省表结构）。"""
    aid2, _, _ = mk_account("c")
    ms.snapshot(aid2, PROF("bye"), ms.REASON_INIT)
    assert ms.count_snapshots(aid2) == 1
    purge_account(aid2)
    assert ms.count_snapshots(aid2) == 0


# ---------------------------------------------------------------- ④ 接口层：列表 / 还原
def test_api_snapshots_and_restore_round_trip(acct):
    token = acct["token"]
    cust.ensure_user_memory(acct["uname"])
    p = cust._user_memory_path(acct["uname"])
    p.write_text(REAL_FILE("v1"), encoding="utf-8")

    # 人工保存 v2 → 应该先给 v1 留一份（manual_edit）
    cust.memory_save(token, cust.MemoryReq(content=REAL_FILE("v2")))
    assert p.read_text(encoding="utf-8").find("身份与领域：v2") > 0
    items = cust.memory_snapshots(token)["items"]
    assert len(items) == 1 and items[0]["reason"] == ms.REASON_MANUAL_EDIT
    assert "v1" in items[0]["preview"]
    assert cust.memory_snapshots(token)["keep"] == 3

    # 还原回 v1 → 当前 v2 会被先快照（可来回切），画像段变回 v1，笔记段与标记保留
    r = cust.memory_restore(token, cust.MemoryRestoreReq(snapshot_id=items[0]["id"]))
    assert r["ok"] and r["restored_from"] == items[0]["id"]
    now = p.read_text(encoding="utf-8")
    assert "身份与领域：v1" in now and "身份与领域：v2" not in now
    assert "## MEMORY（助手笔记）" in now and "memory_init: done" in now
    assert "memory_meta:" in now, "写回要顺带补上新元数据行（新旧并存）"
    assert r["meta"]["initialized"] is True
    after = cust.memory_snapshots(token)["items"]
    assert after[0]["reason"] == ms.REASON_RESTORE and "v2" in after[0]["preview"]

    # 再还原一次（回到 v2）→ 证明可以来回切
    target = [x for x in after if "v2" in x["preview"]][0]
    r2 = cust.memory_restore(token, cust.MemoryRestoreReq(snapshot_id=target["id"]))
    assert r2["ok"] and "身份与领域：v2" in p.read_text(encoding="utf-8")
    assert ms.count_snapshots(acct["aid"]) <= ms.KEEP_LATEST


def test_api_restore_rejects_foreign_or_missing(acct):
    token = acct["token"]
    with pytest.raises(HTTPException) as e:
        cust.memory_restore(token, cust.MemoryRestoreReq(snapshot_id=999999))
    assert e.value.status_code == 404
    aid2, token2, _ = mk_account("d")
    try:
        sid = ms.snapshot(aid2, PROF("theirs"), ms.REASON_INIT)
        with pytest.raises(HTTPException) as e2:
            cust.memory_restore(token, cust.MemoryRestoreReq(snapshot_id=sid))
        assert e2.value.status_code == 404, "★ 别人的快照不能还原（同一句 404，不泄露存在性）"
    finally:
        purge_account(aid2)


def test_initialized_accepts_both_markers(acct):
    """★ 兼容钉子：只写 `memory_meta`（没有老标记）的文件也必须被判为"已初始化" ——
    否则前端会重新显示「✨ 初始化用户画像」，用户一点就把现有画像整段覆盖掉。"""
    only_new = '<!-- memory_meta: init=2026-09-27 refreshed=2026-09-27 sources=2 -->\n\n' + PROF("v9")
    assert cust._memory_initialized(only_new) is True
    assert cust._memory_initialized(REAL_FILE("v9")) is True, "老标记当然仍要认"
    assert cust._memory_initialized("# 空文件\n\n<!-- memory_init: pending -->\n") is False


def test_api_snapshot_meta_shape(acct):
    d = cust.memory_snapshots(acct["token"])
    # ★ 兼容键 snapshots 与 items 必须同一份（2026-09-27 实测：前端读 snapshots、后端只回 items → 界面历史版本恒为 0）
    assert set(d) == {"items", "snapshots", "keep", "meta"}
    assert d["snapshots"] == d["items"]
    assert all("created_at_text" in it for it in d["items"])
    assert set(d["meta"]) == {"init", "refreshed", "sources", "initialized"}
    assert d["items"] == [], "新账号还没有历史版本"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
