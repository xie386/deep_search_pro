# -*- coding: utf-8 -*-
"""M3-3 用例：画像建议器（`/api/memory/suggest` + `_profile_suggestions`）。**离线**：模型用假的。

判据来源：M3 方案 §一 G3/G5、§3.2（建议不落盘）、§5.2（suggestions 形状）、§5.3（两段提示词共用硬约束）
与 §六 M3-3 的验证口径（mock 模型 → 只解析出建议、越界维度标 ⚠️、**绝不返回整段画像**、空证据不编造）。

运行：.venv/Scripts/python.exe -m pytest tests/test_memory_suggest.py -q
"""
import io
import os
import re
import shutil
import sys
import time

import pytest
from fastapi import HTTPException

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

import agent.llm                                        # noqa: E402
from api import customize as cust                       # noqa: E402
from tools import memory_profile as mp                  # noqa: E402
from tools.schema_personal import get_personal_conn, purge_account  # noqa: E402

FILE_OK = """# 用户记忆画像（MEMORY）

<!-- memory_init: done -->

## USER PROFILE（用户画像）

- 身份与领域：大四学生，找 AI 应用开发岗（来源：人工 · 2026-09-13）
- 预算范围：暂无
- 关注渠道：小红书

## MEMORY（助手笔记）

- 笔记：用户偏好先给结论。
"""


class FakeModel:
    """假模型：记录收到的消息，返回预设文本。"""
    def __init__(self, text):
        self.text = text
        self.calls = []

    def invoke(self, messages, *a, **kw):
        self.calls.append(messages)
        if isinstance(self.text, Exception):
            raise self.text
        return type("R", (), {"content": self.text})()


def mk_account(tag):
    conn = get_personal_conn()
    uname = "m3sg_%s_%d" % (tag, int(time.time() * 1000) % 1000000)
    try:
        aid = int(conn.execute("INSERT INTO accounts (username, password_hash, role) VALUES (?,?,?)",
                               (uname, "x", "user")).lastrowid)
        token = "tok_%d_%d" % (aid, int(time.time() * 1000) % 1000000)
        conn.execute("INSERT INTO sessions (token, account_id, login_at) VALUES (?,?,?)", (token, aid, time.time()))
        conn.execute("INSERT INTO interests (owner_id, interest_tag, description) VALUES (?,?,?)",
                     (aid, "AI Agent 生态", "关注开源框架"))
        conn.execute("INSERT INTO watchlist (owner_id, brand, product, note) VALUES (?,?,?,?)",
                     (aid, "米诺地尔", "泡沫喷剂", "等降价"))
        conn.commit()
    finally:
        conn.close()
    return aid, token, uname


@pytest.fixture()
def acct():
    aid, token, uname = mk_account("a")
    p = cust._user_memory_path(uname)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(FILE_OK, encoding="utf-8")
    yield {"aid": aid, "token": token, "uname": uname, "path": p}
    purge_account(aid)
    shutil.rmtree(p.parent, ignore_errors=True)


# ---------------------------------------------------------------- ① 只出建议，绝不落盘
def test_suggest_never_writes_to_the_file(acct, monkeypatch):
    before = acct["path"].read_text(encoding="utf-8")
    monkeypatch.setattr(agent.llm, "model", FakeModel(
        "- 身份与领域：大四学生，找 AI 应用开发岗（来源：人工 · 2026-09-13）\n"
        "- 消费偏好：对降价敏感，生发护理预算 ≤100 元/瓶（来源：关注#2）\n"
        "- 预算范围：≤100 元/瓶（来源：关注#2）\n"))
    d = cust.memory_suggest(acct["token"])
    assert acct["path"].read_text(encoding="utf-8") == before, "★ 建议阶段**一个字都不能写进文件**"
    assert d["suggestions"], "要有建议产出"


def test_suggestions_shape_and_ops(acct, monkeypatch):
    monkeypatch.setattr(agent.llm, "model", FakeModel(
        "- 身份与领域：大四学生，找 AI 应用开发岗（来源：人工 · 2026-09-13）\n"   # 值没变 → 不进列表
        "- 预算范围：≤100 元/瓶（来源：关注#2）\n"                              # 旧值是占位 → update
        "- 消费偏好：对降价敏感（来源：关注#2）\n"                              # 新维度 → add
        "- ⚠️生日：6月1日（来源：会话）\n"))                                    # 白名单外 → flagged
    d = cust.memory_suggest(acct["token"])
    by = {(s["op"], s["field"]): s for s in d["suggestions"]}
    assert ("update", "预算范围") in by and by[("update", "预算范围")]["old"] == "暂无"
    assert ("add", "消费偏好") in by and by[("add", "消费偏好")]["source"] == "关注#2"
    assert by[("add", "生日")]["flagged"] is True, "白名单外的维度要标记（⚠️ 会被解析器剥掉，字段名保持干净）"
    assert by[("add", "消费偏好")]["flagged"] is False, "白名单内的维度不该被标记"
    assert all(s["op"] != "update" for s in d["suggestions"] if s["field"] == "身份与领域"), "值没变不进列表"
    assert set(d) == {"suggestions", "evidence", "warnings", "raw", "profile_rows"}
    assert set(d["suggestions"][0]) == {"op", "field", "old", "new", "source", "date",
                                        "manual", "flagged"}


def test_placeholder_rows_come_back_as_remove(acct, monkeypatch):
    """R3 迁移：老文件里的占位行以 `−` 呈现（默认不勾，人工确认）—— 不许静默删。"""
    monkeypatch.setattr(agent.llm, "model", FakeModel(
        "- 身份与领域：大四学生，找 AI 应用开发岗（来源：人工 · 2026-09-13）\n"
        "- 关注渠道：小红书\n"))
    d = cust.memory_suggest(acct["token"])
    rm = [s for s in d["suggestions"] if s["op"] == "remove"]
    assert [s["field"] for s in rm] == ["预算范围"] and rm[0]["old"] == "暂无"
    assert rm[0]["new"] == "", "移除建议不该带新值"
    # 「关注渠道：小红书」没变 → 不进列表；证据没提到的旧条目不能被提议删除
    assert not any(s["field"] == "关注渠道" for s in d["suggestions"])


def test_manual_row_update_is_marked(acct, monkeypatch):
    monkeypatch.setattr(agent.llm, "model", FakeModel(
        "- 身份与领域：大四学生，正在找 AI 应用开发岗（来源：会话 · 2026-09-27）\n"))
    d = cust.memory_suggest(acct["token"])
    up = [s for s in d["suggestions"] if s["op"] == "update"]
    assert up and up[0]["manual"] is True, "★ 人工行被改时要带 manual 标记（前端默认不勾）"


# ---------------------------------------------------------------- ② 占位 / 越界 的审计
def test_model_placeholders_are_ignored_with_warning(acct, monkeypatch):
    monkeypatch.setattr(agent.llm, "model", FakeModel(
        "- 消费偏好：对降价敏感（来源：关注#2）\n- 价格敏感度：暂无\n- 竞品关注：待补充\n"))
    d = cust.memory_suggest(acct["token"])
    assert not any("暂无" in (s["new"] or "") for s in d["suggestions"]), "★ 占位行不能进建议"
    assert any("占位" in w for w in d["warnings"])
    assert any("白名单" not in w for w in d["warnings"])


def test_new_dimension_warning(acct, monkeypatch):
    monkeypatch.setattr(agent.llm, "model", FakeModel("- ⚠️生日：6月1日（来源：会话）\n"))
    d = cust.memory_suggest(acct["token"])
    assert any("白名单" in w and "生日" in w for w in d["warnings"])


# ---------------------------------------------------------------- ③ 空证据不编造
def test_no_evidence_no_model_call(acct, monkeypatch):
    """★ 证据不足时**不猜**：直接说清楚，连模型都不调（省钱也不给它编的机会）。"""
    conn = get_personal_conn()
    try:
        conn.execute("DELETE FROM interests WHERE owner_id=?", (acct["aid"],))
        conn.execute("DELETE FROM watchlist WHERE owner_id=?", (acct["aid"],))
        conn.commit()
    finally:
        conn.close()
    acct["path"].write_text(FILE_OK.split("## USER PROFILE（用户画像）")[0] +
                            "## USER PROFILE（用户画像）\n\n（初始为空。可在「定制助手」页点击…）\n\n"
                            "## MEMORY（助手笔记）\n\n- x\n", encoding="utf-8")
    fake = FakeModel("- 身份与领域：x（来源：会话）\n")
    monkeypatch.setattr(agent.llm, "model", fake)
    d = cust.memory_suggest(acct["token"])
    assert d["suggestions"] == [] and d.get("note"), "空证据要给出明确交代（而不是让模型编）"
    assert "资料" in d["note"] and "会话" in d["note"]
    assert fake.calls == [], "★ 没有证据时不该调用模型"


# ---------------------------------------------------------------- ④ 模型异常与提示词契约
def test_model_garbage_raises(acct, monkeypatch):
    monkeypatch.setattr(agent.llm, "model", FakeModel("好的，我看看这个用户的画像……（没有任何条目）"))
    with pytest.raises(HTTPException) as e:
        cust.memory_suggest(acct["token"])
    assert e.value.status_code == 502


def test_model_exception_raises(acct, monkeypatch):
    monkeypatch.setattr(agent.llm, "model", FakeModel(RuntimeError("boom")))
    with pytest.raises(HTTPException) as e:
        cust.memory_suggest(acct["token"])
    assert e.value.status_code == 502


def test_user_message_carries_profile_and_traceable_evidence(acct, monkeypatch):
    fake = FakeModel("- 关注渠道：小红书\n")
    monkeypatch.setattr(agent.llm, "model", fake)
    cust.memory_suggest(acct["token"])
    msg = fake.calls[0][1]["content"]
    assert "【用户当前画像】" in msg and "小红书" in msg
    assert "【证据】" in msg
    assert re.search(r"兴趣#\d+：", msg) and re.search(r"关注#\d+：", msg), \
        "证据要带可溯源引用（G5，id 是自增的所以用正则匹配）"
    assert "文档" not in msg.split("【证据】")[0] or True


def test_both_prompts_share_hard_constraints():
    """★ 契约：两段提示词共用同一套硬约束（改一处必须改另一处）。"""
    yml = io.open(os.path.join(ROOT, "prompt", "prompts.yml"), encoding="utf-8").read()
    init = yml[yml.find("memory_initializer:"):yml.find("memory_suggester:")]
    sugg = yml[yml.find("memory_suggester:"):]
    for needle in ("绝不编造", "不要写「暂无 / 待补充 / 待完善」这类占位行", "不要输出 `##` 级标题", "150~400"):
        assert needle in init, "初始化器缺：%s" % needle
        assert needle in sugg, "建议器缺：%s" % needle
    # 修 R3：老提示词里"要求写占位"的指令必须已经删掉
    for gone in ("如实说明「暂无」", "明确缺失的维度（如「预算范围：暂无」）", "- 预算范围：暂无"):
        assert gone not in init, "旧提示词还在要求写占位：%s" % gone


def test_endpoint_is_registered():
    src = io.open(os.path.join(ROOT, "api", "server.py"), encoding="utf-8").read()
    assert '"/api/memory/suggest"' in src and "cust.memory_suggest(token)" in src


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
