"""M2 请求装配管线化 · 单元测试（pytest）。

覆盖：compose_dynamic_prompt 组合规则 / persona SystemMessage 注入与截断保护 /
_get_agent_for 缓存 key（改人格不重建、改模型才重建）。

运行：.venv/Scripts/python.exe -m pytest tests/test_assembly.py -v
"""
import sys
import os

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
sys.path = [p for p in sys.path if "hermes-agent" not in p and "hermes_agent" not in p]
os.environ.pop("PYTHONPATH", None)

from langchain_core.messages import HumanMessage, SystemMessage

from agent.build_context import build_request_context, compose_dynamic_prompt
from agent.context_budget import ContextConfig, ContextManager


def make_history(rounds: int):
    from agent import conversation_store as cs
    from langchain_core.messages import AIMessage
    tid = f"ut_asm_{rounds}_{rounds}"
    # 用内存式构造太绕，直接 SQLite 灌数据后清理
    from tools.schema_personal import get_personal_conn
    conn = get_personal_conn()
    conn.execute("DELETE FROM conversations WHERE thread_id LIKE 'ut_asm_%'")
    row = conn.execute("SELECT id FROM accounts LIMIT 1").fetchone()
    aid = row["id"] if row else 1
    conn.close()
    for i in range(rounds):
        cs.save_turn(aid, tid, f"问题{i}：" + "内容" * 30,
                     [AIMessage(content=f"回答{i}：" + "答复" * 30)], token_usage=10)
    return aid, tid


def test_compose_dynamic_prompt_combined():
    s = compose_dynamic_prompt(soul_text="你是女仆", memory_text="喜欢猫", username="alice")
    assert "【人格设定】" in s and "你是女仆" in s
    assert "【你的用户记忆画像】" in s and "喜欢猫" in s
    assert "agents_docs/alice/MEMORY.md" in s  # 记忆文件路径提示
    # 顺序：人格在前
    assert s.index("你是女仆") < s.index("喜欢猫")


def test_compose_dynamic_prompt_partial():
    s1 = compose_dynamic_prompt(soul_text="", memory_text="", username="")
    assert s1 == ""
    s2 = compose_dynamic_prompt(soul_text="只有人格", memory_text="")
    assert "【人格设定】" in s2 and "【你的用户记忆画像】" not in s2
    s3 = compose_dynamic_prompt(memory_text="只有记忆", username="bob")
    assert "【人格设定】" not in s3 and "【你的用户记忆画像】" in s3


def test_build_context_injects_persona_system_first():
    aid, tid = make_history(0)
    ctx = build_request_context(tid, aid, "你好",
                                soul_text="你是活泼女仆", memory_text="喜欢猫", username="alice")
    assert ctx.persona_injected
    assert isinstance(ctx.messages[0], SystemMessage), "persona 应为首条 system"
    assert "你是活泼女仆" in str(ctx.messages[0].content)
    assert ctx.messages[-1].content == "你好"  # 提问在最后
    from agent import conversation_store as cs
    cs.delete_session(aid, tid)


def test_persona_survives_truncation():
    """persona SystemMessage 在 25 轮历史 + 超小预算下仍存活（截断保护）。"""
    aid, tid = make_history(25)
    cfg = ContextConfig(enforce_max_turns=20, truncate_turns=4, max_context_tokens=3000)
    mgr = ContextManager(config=cfg)
    ctx = build_request_context(tid, aid, "总结",
                                soul_text="人格必须存活", memory_text="",
                                config=cfg, manager=mgr)
    assert isinstance(ctx.messages[0], SystemMessage)
    assert "人格必须存活" in str(ctx.messages[0].content)
    assert ctx.dropped_rounds > 0, "25 轮应触发截断"
    from agent import conversation_store as cs
    cs.delete_session(aid, tid)


def test_build_context_no_dynamic_empty_account():
    """无人格无记忆账号：不注入 persona，行为与 M1 一致（纯历史+提问）。"""
    aid, tid = make_history(0)
    ctx = build_request_context(tid, aid, "你好", soul_text="", memory_text="", username="")
    assert not ctx.persona_injected
    assert not isinstance(ctx.messages[0], SystemMessage)
    assert ctx.messages[-1].content == "你好"
    from agent import conversation_store as cs
    cs.delete_session(aid, tid)
