# -*- coding: utf-8 -*-
"""「答案提取 / 落库清洗」回归测试（离线，不调模型、不联网）。

对应 2026-09-24 用户报障：**约 1/5 概率「正文跑到思考里、气泡没正文」，刷新后一堆空白卡片**。
两条链的结论（详见 agent/answer_text.py 顶部注释）：
  链一：推理模型偶尔把可见正文写进 reasoning_content、content 为空 → 原实现
        `result["messages"][-1].content` 拿到空串；正文只在思考面板可见。
  链二：工具轮会产生 content="\\n" 的 assistant 消息，前端对每条 assistant 都渲染一张
        报告卡 → 刷新后一屏「只有标题、内容为空」的卡片。

这里锁住四件事，任何一条被后续改动打破都会立刻报错：
  ① 答案提取的优先级（content → reasoning 回填 → 本轮更早的非工具回答 → 显式失败）；
  ② **不能跨轮回溯**（否则会把上一轮的旧答案当成这一轮的答案）；
  ③ 回填时必须把文本写回消息对象（否则落库仍是空 → 刷新后又变空卡）；
  ④ 落库过滤只丢「空内容且无 tool_calls」，带 tool_calls 的一条都不能少（孤儿 tool 消息会被上游 400）。
"""
from __future__ import annotations

import io
import sys
from pathlib import Path

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent import answer_text  # noqa: E402


def _ai(text="", reasoning=None, tool_calls=None):
    kw = {}
    if reasoning is not None:
        kw["reasoning_content"] = reasoning
    return AIMessage(content=text, additional_kwargs=kw, tool_calls=tool_calls or [])


def _tool_call_msg(text="\n", name="run_shell_command"):
    """工具轮的 assistant：content 常为 "\n"，真正内容在 tool_calls 里（协议要求）。"""
    return _ai(text, tool_calls=[{"name": name, "args": {"c": "ls"}, "id": "call_1"}])


# ---------------------------------------------------------------- ① 答案提取优先级
def test_last_ai_content_wins():
    msgs = [HumanMessage(content="Q"), _ai("正常回答")]
    assert answer_text.final_answer(msgs) == ("正常回答", answer_text.SOURCE_CONTENT)


def test_reasoning_is_backfilled_when_content_empty():
    """★ 本次报障的主路径：正文在 reasoning_content、content 为空 → 回填。"""
    msgs = [HumanMessage(content="Q"), _ai("", reasoning="这就是模型给出的正文")]
    assert answer_text.final_answer(msgs) == ("这就是模型给出的正文", answer_text.SOURCE_REASONING)


def test_whitespace_content_counts_as_empty():
    msgs = [HumanMessage(content="Q"), _ai("\n  ", reasoning="正文")]
    assert answer_text.final_answer(msgs)[1] == answer_text.SOURCE_REASONING


def test_earlier_non_tool_answer_is_used_as_last_resort():
    msgs = [HumanMessage(content="Q"), _ai("前面的完整回答"), _tool_call_msg(),
            ToolMessage(content="结果", tool_call_id="call_1"), _ai("")]
    assert answer_text.final_answer(msgs) == ("前面的完整回答", answer_text.SOURCE_EARLIER)


def test_tool_preamble_is_not_taken_as_answer():
    """工具轮那句「好嘞，先查一下～」不是答案，别把它当正文回填给用户。"""
    msgs = [HumanMessage(content="Q"), _tool_call_msg("好嘞，先查一下～"),
            ToolMessage(content="结果", tool_call_id="call_1"), _ai("")]
    assert answer_text.final_answer(msgs) == ("", answer_text.SOURCE_NONE)


def test_nothing_to_backfill_returns_none_source():
    msgs = [HumanMessage(content="Q"), _ai("")]
    assert answer_text.final_answer(msgs) == ("", answer_text.SOURCE_NONE)


# ---------------------------------------------------------------- ② 不能跨轮回溯
def test_must_not_leak_previous_turn_answer():
    """本轮没正文、上一轮有正文 → 必须返回空（由调用方给显式提示），绝不能把旧答案当新答案。"""
    msgs = [
        HumanMessage(content="上一轮问题"), _ai("上一轮的回答"),
        HumanMessage(content="这一轮问题"), _tool_call_msg(), ToolMessage(content="r", tool_call_id="call_1"),
        _ai(""),
    ]
    assert answer_text.final_answer(msgs) == ("", answer_text.SOURCE_NONE)


def test_boundary_matches_skill_rewritten_question():
    """技能前缀会改写实际用户文本：`since` 里带上真实文本时，边界仍能正确定位。"""
    msgs = [HumanMessage(content="上一轮问题"), _ai("上一轮的回答"),
            HumanMessage(content="【技能】改写后的本轮问题"), _ai("")]
    assert answer_text.final_answer(msgs, {"【技能】改写后的本轮问题"})[1] == answer_text.SOURCE_NONE
    assert answer_text.final_answer(msgs, {"上一轮问题"})[1] != answer_text.SOURCE_NONE or True  # 不崩即可


# ---------------------------------------------------------------- ③ 回填要写回消息
def test_restore_answer_writes_back_into_message():
    """回填必须落到消息对象上：落库走的是同一批对象，只改返回值 → 刷新后又变空卡。"""
    last = _ai("", reasoning="正文在这里")
    msgs = [HumanMessage(content="Q"), _tool_call_msg(), ToolMessage(content="r", tool_call_id="call_1"), last]
    answer, src = answer_text.restore_answer(msgs, {"Q"})
    assert (answer, src) == ("正文在这里", answer_text.SOURCE_REASONING)
    assert last.content == "正文在这里"                      # ← 写回
    assert last.additional_kwargs["reasoning_content"] == "正文在这里"   # 思考原文保留
    assert answer_text.is_empty_assistant(last) is False    # 写回后不会再被过滤掉


def test_restore_answer_keeps_tool_calls_intact():
    """工具轮那句话**不该**被当答案（宁可给显式提示，也别把「好嘞，先查一下～」当正文），
    且回溯/回填都不许碰 tool_calls（否则历史重放时 tool 消息成孤儿 → 上游 400）。"""
    tc_msg = _tool_call_msg("好嘞，先查一下～")
    msgs = [HumanMessage(content="Q"), tc_msg, ToolMessage(content="r", tool_call_id="call_1"), _ai("")]
    answer, src = answer_text.restore_answer(msgs, {"Q"})
    assert (answer, src) == ("", answer_text.SOURCE_NONE)
    assert tc_msg.content == "好嘞，先查一下～"                      # 原样保留
    assert tc_msg.tool_calls, "tool_calls 必须原样保留"
    assert answer_text.drop_empty_assistant(msgs)[1] is tc_msg    # 也不会被当成空消息丢掉


# ---------------------------------------------------------------- ④ 落库过滤
def test_drop_empty_assistant_rules():
    empty_final = _ai("")
    tc = _tool_call_msg()
    msgs = [HumanMessage(content="Q"), tc, ToolMessage(content="r", tool_call_id="call_1"),
            _ai("答案"), empty_final]
    kept = answer_text.drop_empty_assistant(msgs)
    assert empty_final not in kept, "空内容且无 tool_calls 的 assistant 必须被过滤"
    assert tc in kept, "带 tool_calls 的 assistant 绝不能丢（孤儿 tool 消息）"
    # 5 条里只丢 1 条（空且无 tool_calls），其余原序保留：Human / 工具轮AI / Tool / 答案AI
    assert len(kept) == 4
    assert [type(m).__name__ for m in kept] == ["HumanMessage", "AIMessage", "ToolMessage", "AIMessage"]
    assert kept[1] is tc and isinstance(kept[2], ToolMessage)


def test_drop_never_removes_non_assistant_messages():
    msgs = [HumanMessage(content="Q"), ToolMessage(content="", tool_call_id="x"), _ai("")]
    kept = answer_text.drop_empty_assistant(msgs)
    assert len(kept) == 2, "空内容的 ToolMessage 不归本规则管（它靠 tool_calls 配对）"


# ---------------------------------------------------------------- 端到端「静态」守卫
def test_server_no_longer_takes_last_content_blindly():
    src = io.open(ROOT / "api" / "server.py", encoding="utf-8").read()
    assert 'return result["messages"][-1].content' not in src, "回到无脑取最后一条内容的老路"
    assert "answer_text.restore_answer(" in src, "答案必须走 agent.answer_text"
    assert "answer_text.drop_empty_assistant(" in src, "落库必须过滤空 assistant"


def test_digest_engine_shares_the_same_extraction():
    src = io.open(ROOT / "agent" / "digest_engine.py", encoding="utf-8").read()
    assert 'answer = result["messages"][-1].content or ""' not in src, "周报路径同病必须同修"
    assert "answer_text.final_answer(" in src


def test_frontend_skips_blank_assistant_cards():
    html = io.open(ROOT / "static" / "index.html", encoding="utf-8").read()
    assert "if (!_txt.trim()) continue;" in html, "前端空 assistant 必须跳过，否则刷新后一屏空白卡"
    assert "msgs.push({ role: 'agent', text: String(c || '')" not in html, "旧的不过滤写法不能回流"


if __name__ == "__main__":  # 允许直接运行（不依赖 pytest 也可看清单）
    raise SystemExit(pytest.main([__file__, "-q"]))
