# -*- coding: utf-8 -*-
"""M3-4 用例：应用画像建议（`/api/memory/apply`）。**离线**：临时账号 + 临时用户目录，不动真实账号文件。

判据来源：M3 方案 §一 G2（连续 3 次更新同一维度 → **恰好 1 行**）、G5（来源与日期）、§5.6（`−` 语义）、
D8（`protect_manual` 默认保护人工行）、D9（**改前必快照**）。

运行：.venv/Scripts/python.exe -m pytest tests/test_memory_apply.py -q
"""
import os
import shutil
import sys
import time

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

from api import customize as cust                       # noqa: E402
from tools import memory_profile as mp                  # noqa: E402
from tools import memory_snapshots as ms                # noqa: E402
from tools.schema_personal import get_personal_conn, purge_account  # noqa: E402

BASE = """# 用户记忆画像（MEMORY）

> 智能体会在对话中自行判断是否需要更新本文件。

<!-- memory_init: done -->

## USER PROFILE（用户画像）

- 身份与领域：大四学生（来源：人工 · 2026-09-13）
- 预算范围：暂无
- 关注渠道：小红书

## MEMORY（助手笔记）

- 笔记段必须原样保留，不能被写回动作弄丢。
"""


def mk_account(tag):
    conn = get_personal_conn()
    uname = "m3ap_%s_%d" % (tag, int(time.time() * 1000) % 1000000)
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


def rows_of(path):
    return mp.parse_rows(mp.split_sections(path.read_text(encoding="utf-8"))["profile"])


def by_field(path):
    return {r["norm"]: r for r in rows_of(path)}


# ---------------------------------------------------------------- ① G2：就地更新，不追加
def test_same_dimension_three_applies_stay_one_row(acct):
    """★ G2：连续 3 次应用同一维度 → **恰好 1 行**，值为最新。"""
    for val in ("≤100 元/瓶", "≤80 元/瓶", "≤120 元/瓶"):
        r = cust.memory_apply(acct["token"], cust.MemoryApplyReq(items=[
            {"op": "update", "field": "预算范围", "new": val, "source": "关注#7", "date": "2026-09-24"}]))
        assert r["ok"] and r["wrote"] is True
    rows = rows_of(acct["path"])
    assert len([x for x in rows if x["norm"] == "预算范围"]) == 1, "★ 同一维度只能有一行"
    assert by_field(acct["path"])["预算范围"]["fact"] == "≤120 元/瓶"
    assert len(rows) == 3, "老的三行（含占位）会变：占位仍在、新维度进来 —— 总数不该有重复维度"


def test_no_duplicate_dimension_lines(acct):
    cust.memory_apply(acct["token"], cust.MemoryApplyReq(items=[
        {"op": "add", "field": "消费偏好", "new": "对降价敏感", "source": "会话"},
        {"op": "add", "field": "消费偏好", "new": "对降价敏感，认品牌", "source": "会话"}]))
    norms = [r["norm"] for r in rows_of(acct["path"])]
    assert norms.count("消费偏好") == 1, "同一批里重复的维度也要合并成一行"
    assert by_field(acct["path"])["消费偏好"]["fact"] == "对降价敏感，认品牌"


# ---------------------------------------------------------------- ② 来源与日期（G5）
def test_source_stays_out_of_the_file(acct):
    """★ 口径变更（用户实测后）：来源 id 只用于建议面板溯源，**不进画像文件**（注入污染）。"""
    cust.memory_apply(acct["token"], cust.MemoryApplyReq(items=[
        {"op": "add", "field": "情报关注点", "new": "AI Agent 生态", "source": "兴趣#3", "date": "2026-09-24"}]))
    txt = acct["path"].read_text(encoding="utf-8")
    assert "兴趣#3" not in txt and "（来源" not in txt, "来源 id 不得进文件"
    assert by_field(acct["path"])["情报关注点"]["fact"] == "AI Agent 生态"


# ---------------------------------------------------------------- ③ 人工行保护（D8）
def test_manual_row_is_protected_by_default(acct):
    r = cust.memory_apply(acct["token"], cust.MemoryApplyReq(items=[
        {"op": "update", "field": "身份与领域", "new": "AI 应用开发工程师", "source": "会话"}]))
    assert r["applied"] == [] and r["skipped"], "★ 人工行默认不被自动流程改动"
    assert "人工行受保护" in r["skipped"][0]["why"]
    assert by_field(acct["path"])["身份与领域"]["fact"] == "大四学生"


def test_protect_manual_off_allows_update(acct):
    r = cust.memory_apply(acct["token"], cust.MemoryApplyReq(protect_manual=False, items=[
        {"op": "update", "field": "身份与领域", "new": "AI 应用开发工程师", "source": "会话"}]))
    assert r["wrote"] is True and by_field(acct["path"])["身份与领域"]["fact"] == "AI 应用开发工程师"


# ---------------------------------------------------------------- ④ 移除（− 语义）
def test_remove_only_when_selected_and_not_manual(acct):
    # 占位行可以删（R3 迁移）
    r1 = cust.memory_apply(acct["token"], cust.MemoryApplyReq(items=[
        {"op": "remove", "field": "预算范围", "old": "暂无"}]))
    assert r1["wrote"] is True and "预算范围" not in by_field(acct["path"])
    # 人工行不行
    r2 = cust.memory_apply(acct["token"], cust.MemoryApplyReq(items=[
        {"op": "remove", "field": "身份与领域"}]))
    assert r2["applied"] == [] and "人工行受保护" in r2["skipped"][0]["why"]
    # 不存在的维度：跳过而不是报错
    r3 = cust.memory_apply(acct["token"], cust.MemoryApplyReq(items=[
        {"op": "remove", "field": "根本不存在的维度"}]))
    assert r3["applied"] == [] and "已经没有这个维度" in r3["skipped"][0]["why"]


def test_unknown_op_and_empty_value_are_skipped(acct):
    r = cust.memory_apply(acct["token"], cust.MemoryApplyReq(items=[
        {"op": "rewrite", "field": "消费偏好", "new": "x"},
        {"op": "add", "field": "关注渠道", "new": "   "}]))
    assert r["applied"] == [] and r["wrote"] is False
    whys = " ".join(s["why"] for s in r["skipped"])
    assert "未知操作" in whys and "新值为空" in whys


# ---------------------------------------------------------------- ⑤ 空应用不写文件、不留快照
def test_no_op_apply_writes_nothing(acct):
    before = acct["path"].read_text(encoding="utf-8")
    r = cust.memory_apply(acct["token"], cust.MemoryApplyReq(items=[
        {"op": "update", "field": "身份与领域", "new": "x"}]))     # 人工行 → 被保护
    assert r["wrote"] is False
    assert acct["path"].read_text(encoding="utf-8") == before, "一条都没应用时文件必须原封不动"
    assert ms.count_snapshots(acct["aid"]) == 0, "没有改动就不该产生快照（别白占名额）"


# ---------------------------------------------------------------- ⑥ 快照（D9）+ 段落保真 + 元数据
def test_snapshot_before_write_and_sections_preserved(acct):
    cust.memory_apply(acct["token"], cust.MemoryApplyReq(items=[
        {"op": "add", "field": "消费偏好", "new": "对降价敏感", "source": "会话"}]))
    snaps = ms.list_snapshots(acct["aid"])
    assert len(snaps) == 1 and snaps[0]["reason"] == ms.REASON_SUGGEST_IMPORT
    assert "身份与领域：大四学生" in snaps[0]["preview"], "★ 快照要存**改动之前**的内容"
    txt = acct["path"].read_text(encoding="utf-8")
    assert "笔记段必须原样保留" in txt, "笔记段不能被写回动作弄丢"
    assert "> 智能体会在对话中自行判断是否需要更新本文件。" in txt, "文件头要保留"
    assert "memory_init: done" in txt


def test_meta_refreshed_and_sources(acct):
    r = cust.memory_apply(acct["token"], cust.MemoryApplyReq(items=[
        {"op": "add", "field": "消费偏好", "new": "对降价敏感", "source": "会话"}]))
    meta = mp.parse_meta(acct["path"].read_text(encoding="utf-8"))
    # 种子文件里没有 memory_meta → 首次写入时 init 取今天（这样前端"上次初始化"才有值可显示）
    assert meta["refreshed"] == mp.today() and meta["init"] == mp.today()
    assert meta["sources"] == r["rows"] == len(rows_of(acct["path"]))
    assert meta["initialized"] is True


def test_reapply_same_batch_is_idempotent(acct):
    items = [{"op": "add", "field": "消费偏好", "new": "对降价敏感", "source": "会话", "date": "2026-09-27"}]
    cust.memory_apply(acct["token"], cust.MemoryApplyReq(items=items))
    first = acct["path"].read_text(encoding="utf-8")
    cust.memory_apply(acct["token"], cust.MemoryApplyReq(items=items))
    assert acct["path"].read_text(encoding="utf-8") == first, "★ 重复应用同一批建议不该改变文件"
    assert len([r for r in rows_of(acct["path"]) if r["norm"] == "消费偏好"]) == 1


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
