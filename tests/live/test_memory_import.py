# -*- coding: utf-8 -*-
"""M3-10 用例：一键导入整份 AI 建议（`/api/memory/import`，D8）。**离线**：临时账号 + 临时目录。

判据来源：M3 方案 §一 G2、D8（一键导入 · 默认 `protect_manual=true` · **导入前必快照**）、§5.6（确认面板计数）。

运行：.venv/Scripts/python.exe -m pytest tests/test_memory_import.py -q
"""
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
from tools.schema_personal import get_personal_conn, purge_account  # noqa: E402

BASE = """# 用户记忆画像（MEMORY）

<!-- memory_init: done -->

## USER PROFILE（用户画像）

- 身份与领域：大四学生（来源：人工 · 2026-09-13）
- 预算范围：暂无
- 关注渠道：小红书

## MEMORY（助手笔记）

- 笔记：不能被弄丢。
"""

# 一份"AI 建议"：1 条人工行更新（默认该被拦）+ 1 条新维度 + 1 条占位移除 + 1 条越界新维度
SUGG = [
    {"op": "update", "field": "身份与领域", "old": "大四学生", "new": "AI 应用开发工程师",
     "source": "会话", "date": "2026-09-27", "manual": True, "flagged": False},
    {"op": "add", "field": "消费偏好", "old": "", "new": "对降价敏感，生发护理预算 ≤100 元/瓶",
     "source": "关注#7", "date": "2026-09-27", "manual": False, "flagged": False},
    {"op": "remove", "field": "预算范围", "old": "暂无", "new": "", "source": "", "date": "",
     "manual": False, "flagged": False},
    {"op": "add", "field": "生日", "old": "", "new": "6月1日", "source": "会话",
     "date": "2026-09-27", "manual": False, "flagged": True},
]


def mk_account(tag):
    conn = get_personal_conn()
    uname = "m3im_%s_%d" % (tag, int(time.time() * 1000) % 1000000)
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
    p = cust._user_memory_path(uname)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(BASE, encoding="utf-8")
    yield {"aid": aid, "token": token, "uname": uname, "path": p}
    purge_account(aid)
    shutil.rmtree(p.parent, ignore_errors=True)


def by_field(path):
    return {r["norm"]: r for r in mp.parse_rows(mp.split_sections(path.read_text(encoding="utf-8"))["profile"])}


# ---------------------------------------------------------------- ① 必须显式确认
def test_requires_explicit_confirm(acct):
    before = acct["path"].read_text(encoding="utf-8")
    with pytest.raises(HTTPException) as e:
        cust.memory_import(acct["token"], cust.MemoryImportReq(items=SUGG, confirm=False))
    assert e.value.status_code == 400 and "确认" in str(e.value.detail)
    assert acct["path"].read_text(encoding="utf-8") == before, "没确认就一个字都不能写"


# ---------------------------------------------------------------- ② 整份应用 + 保人工行（D8 核心）
def test_import_applies_all_and_keeps_manual_rows(acct):
    r = cust.memory_import(acct["token"], cust.MemoryImportReq(items=SUGG, confirm=True))
    assert r["ok"] and r["wrote"] is True
    assert r["imported"] == 4 and r["dimensions"] == 3, "四条建议：1 条被人工行保护拦住，3 条落地"
    assert r["manual_kept"] == 1, "★ 确认面板要能告诉用户'其中 1 个是人工行'"
    got = by_field(acct["path"])
    assert got["身份与领域"]["fact"] == "大四学生", "★ 人工行默认不被一键导入覆盖"
    assert got["消费偏好"]["fact"].startswith("对降价敏感")
    assert "关注#7" not in acct["path"].read_text(encoding="utf-8"), "★ 来源 id 不得进画像文件"
    assert "预算范围" not in got, "占位行被移除（R3 迁移）"
    assert got["生日"]["fact"] == "6月1日", "越界维度也导入（flagged 只影响前端默认勾选）"
    whys = " ".join(s["why"] for s in r["skipped"])
    assert "人工行受保护" in whys


def test_overriding_protection_updates_manual_row(acct):
    r = cust.memory_import(acct["token"],
                           cust.MemoryImportReq(items=SUGG, protect_manual=False, confirm=True))
    assert r["wrote"] is True and by_field(acct["path"])["身份与领域"]["fact"] == "AI 应用开发工程师"
    assert r["manual_kept"] == 1


# ---------------------------------------------------------------- ③ 导入前必快照（D9）
def test_snapshot_before_import(acct):
    cust.memory_import(acct["token"], cust.MemoryImportReq(items=SUGG, confirm=True))
    snaps = ms.list_snapshots(acct["aid"])
    assert len(snaps) == 1 and snaps[0]["reason"] == ms.REASON_SUGGEST_IMPORT
    assert "身份与领域：大四学生" in snaps[0]["preview"], "★ 快照是'改动之前'的画像"


def test_no_change_no_snapshot(acct):
    """整份建议一条都没落地（全被保护拦住）→ 不写文件、不产生快照。"""
    before = acct["path"].read_text(encoding="utf-8")
    r = cust.memory_import(acct["token"], cust.MemoryImportReq(items=[
        {"op": "update", "field": "身份与领域", "new": "x", "source": "会话", "manual": True}], confirm=True))
    assert r["wrote"] is False and r["dimensions"] == 0
    assert acct["path"].read_text(encoding="utf-8") == before
    assert ms.count_snapshots(acct["aid"]) == 0


# ---------------------------------------------------------------- ④ 与 /apply 同一套语义
def test_import_does_not_duplicate_dimensions(acct):
    dup = list(SUGG) + [{"op": "add", "field": "消费偏好", "new": "对降价敏感，认品牌",
                         "source": "关注#7", "date": "2026-09-27"}]
    cust.memory_import(acct["token"], cust.MemoryImportReq(items=dup, confirm=True))
    rows = mp.parse_rows(mp.split_sections(acct["path"].read_text(encoding="utf-8"))["profile"])
    assert [r["norm"] for r in rows].count("消费偏好") == 1, "★ 就地更新：同一维度不产生第二行"
    assert by_field(acct["path"])["消费偏好"]["fact"] == "对降价敏感，认品牌"


def test_import_preserves_notes_and_meta(acct):
    r = cust.memory_import(acct["token"], cust.MemoryImportReq(items=SUGG, confirm=True))
    txt = acct["path"].read_text(encoding="utf-8")
    assert "笔记：不能被弄丢。" in txt and "memory_init: done" in txt
    meta = mp.parse_meta(txt)
    assert meta["refreshed"] == mp.today() and meta["sources"] == r["rows"]
    assert meta["initialized"] is True


def test_import_is_idempotent(acct):
    cust.memory_import(acct["token"], cust.MemoryImportReq(items=SUGG, confirm=True))
    first = acct["path"].read_text(encoding="utf-8")
    cust.memory_import(acct["token"], cust.MemoryImportReq(items=SUGG, confirm=True))
    assert acct["path"].read_text(encoding="utf-8") == first, "★ 重复导入同一份建议不该改变文件"


def test_import_and_apply_share_one_core():
    """两条写路径必须共用内核 —— 否则"保人工行/就地更新/改前快照"迟早分叉。"""
    src = open(os.path.join(ROOT, "api", "customize.py"), encoding="utf-8").read()
    assert src.count("_apply_items(rows, req.items, req.protect_manual)") == 2, "apply 与 import 各调一次"
    assert "def _apply_items(" in src


def test_endpoint_is_registered():
    src = open(os.path.join(ROOT, "api", "server.py"), encoding="utf-8").read()
    assert '"/api/memory/import"' in src and "cust.memory_import(token, req)" in src


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
