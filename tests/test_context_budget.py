"""M1 上下文工程化 · context_budget 单元测试（pytest）。

覆盖：TokenCounter 估算 / fix_messages 孤立工具修复 / truncate_by_turns 轮截断 /
truncate_by_tokens token 截断 / ContextManager 编排 / LLM 压缩成功与降级。

运行：cd backend(项目根) && .venv/Scripts/python.exe -m pytest tests/test_context_budget.py -v
"""
import sys, os

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
sys.path = [p for p in sys.path if "hermes-agent" not in p and "hermes_agent" not in p]
os.environ.pop("PYTHONPATH", None)

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from agent.context_budget import (
    ContextConfig,
    ContextManager,
    ContextTruncator,
    EstimateTokenCounter,
    LLMSummaryCompressor,
)

trunc = ContextTruncator()
tc = EstimateTokenCounter()


def make_conv(rounds: int = 25):
    msgs = [SystemMessage(content="你是助手")]
    for i in range(rounds):
        msgs.append(HumanMessage(content=f"问题{i}：" + "今天天气怎么样？" * 5))
        msgs.append(AIMessage(content=f"回答{i}：" + "今天晴天。" * 8))
    return msgs


def _user_cnt(msgs):
    return sum(1 for m in msgs if isinstance(m, HumanMessage))


# ---------- TokenCounter ----------
def test_token_counter_estimate():
    msgs = [HumanMessage(content="中文" * 100), AIMessage(content="english " * 50)]
    n = tc.count(msgs)
    assert n > 0
    # 中文 200 字 ≈ 300 token；英文 300 词 ≈ 225 token；合计 ~525 ± 30%
    assert 350 < n < 700, f"估算偏差过大: {n}"


def test_token_counter_tool_overhead():
    msgs = [
        HumanMessage(content="q"),
        AIMessage(content="", tool_calls=[{"name": "t", "args": {}, "id": "c1", "type": "tool_call"}]),
    ]
    with_tool = tc.count(msgs)
    without = tc.count([HumanMessage(content="q")])
    assert with_tool >= without + 50 * 1  # 每条 tool_call 至少 +50


# ---------- fix_messages ----------
def test_fix_orphan_tool_messages():
    msgs = [
        HumanMessage(content="q"),
        AIMessage(content="", tool_calls=[{"name": "t", "args": {}, "id": "call1", "type": "tool_call"}]),
        ToolMessage(content="res", tool_call_id="call1"),
        ToolMessage(content="孤儿", tool_call_id="ghost"),
        AIMessage(content="a"),
    ]
    fixed = trunc.fix_messages(msgs)
    assert len(fixed) == 4, "孤立 tool 未清理"
    assert all(not (isinstance(m, ToolMessage) and m.tool_call_id == "ghost") for m in fixed)


# ---------- truncate_by_turns ----------
def test_truncate_by_turns_keeps_system_and_recent():
    conv = make_conv(25)
    out = trunc.truncate_by_turns(conv, max_turns=20, drop_n=4)
    assert _user_cnt(out) == 21, "25 轮丢 4 轮应剩 21 轮"
    assert isinstance(out[0], SystemMessage), "system 必须保留且在最前"
    # 最近几轮保留
    assert "问题24" in out[-2].content or _user_cnt(out) >= 2


def test_truncate_by_turns_noop_within_limit():
    conv = make_conv(5)
    out = trunc.truncate_by_turns(conv, max_turns=20, drop_n=4)
    assert len(out) == len(conv), "轮数内不应触发"


# ---------- truncate_by_tokens ----------
def test_truncate_by_tokens():
    small = ContextConfig(max_context_tokens=300, enforce_max_turns=-1)
    conv = make_conv(10)
    before = tc.count(conv)
    assert before > 300
    out = trunc.truncate_by_tokens(conv, tc, 300)
    after = tc.count(out)
    assert after <= 300 * 0.8 + 60, f"token 截断未达标: {before} -> {after}"
    assert isinstance(out[0], SystemMessage)


# ---------- ContextManager ----------
def test_manager_noop_few_rounds():
    mgr = ContextManager(config=ContextConfig(enforce_max_turns=20, max_context_tokens=10**6))
    proc = mgr.process(make_conv(5))
    assert proc.dropped_rounds == 0 and not proc.compressed
    assert len(proc.messages) == 11  # 1 system + 10 消息


def test_manager_truncates_long():
    mgr = ContextManager(config=ContextConfig(enforce_max_turns=20, truncate_turns=4, max_context_tokens=10**6))
    proc = mgr.process(make_conv(25))
    assert proc.dropped_rounds == 4
    assert _user_cnt(proc.messages) == 21
    assert proc.original_count == 51


def test_manager_token_limit_triggers():
    mgr = ContextManager(config=ContextConfig(max_context_tokens=300, enforce_max_turns=-1))
    conv = make_conv(10)
    proc = mgr.process(conv)
    assert proc.compressed and tc.count(proc.messages) < tc.count(conv)


# ---------- LLM 压缩 ----------
def test_llm_summary_success():
    cfg = ContextConfig(llm_compress_enabled=True, max_context_tokens=200, enforce_max_turns=-1)

    def good_llm(messages):
        return AIMessage(content="摘要：用户问了 10 个问题")

    mgr = ContextManager(config=cfg)
    mgr.compressor = LLMSummaryCompressor(mgr.truncator, cfg, llm=good_llm)
    proc = mgr.process(make_conv(10))
    assert any("历史摘要" in str(m.content) for m in proc.messages), "应生成摘要消息"
    assert proc.compressed


def test_llm_summary_fallback_on_error():
    cfg = ContextConfig(llm_compress_enabled=True, max_context_tokens=400, enforce_max_turns=-1)

    def bad_llm(messages):
        raise RuntimeError("模拟 LLM 失败")

    mgr = ContextManager(config=cfg)
    mgr.compressor = LLMSummaryCompressor(mgr.truncator, cfg, llm=bad_llm)
    proc = mgr.process(make_conv(10))
    # 降级为截断：不崩、消息被压缩
    assert proc.compressed
    assert tc.count(proc.messages) < tc.count(make_conv(10))
