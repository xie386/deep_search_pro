# -*- coding: utf-8 -*-
"""聊天增量输出（v3.1 流式）的离线契约：只推主图 · 节流 · 聚合逐字保真。

为什么要有它：流式最怕两件事 —— ① 把**子 Agent** 的 token 推进用户的聊天气泡 ✗；
② 增量聚合出来的正文与「非流式那条 invoke 的正文」**不一致** ✗（流式改写了内容）。
前者靠 `is_main_graph` 的 metadata 判据，后者靠 `DeltaSink.text` 与喂入序列的逐字相等 ✓。

运行：.venv/Scripts/python.exe -m pytest tests/unit/test_stream_sink.py -q
"""
import os
import sys
from types import SimpleNamespace

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from agent.stream_sink import DeltaSink, is_main_graph, iter_deltas  # noqa: E402


def _chunk(text="", reasoning=""):
    awk = {"reasoning_content": reasoning} if reasoning else {}
    return SimpleNamespace(content=text, additional_kwargs=awk)


# ---------------------------------------------------------------- 只推主图
def test_main_graph_meta_passes():
    """实测的主图形状（2026-10-02）：node=model、checkpoint_ns=model:<uuid> ✓"""
    meta = {"langgraph_node": "model", "langgraph_checkpoint_ns": "model:3c0cd4c3-a7bd-bddc-3afb"}
    assert is_main_graph(meta) is True


def test_subgraph_meta_is_filtered():
    """子 Agent 跑在子图里 → checkpoint_ns 含 '|' → 不推（否则串进聊天气泡 ✗）。"""
    for ns in ("tools:abc-123|model:def-456", "tools:abc|tools:def|model:ghi", "subagent:x|model:y"):
        assert is_main_graph({"langgraph_node": "model",
                              "langgraph_checkpoint_ns": ns}) is False, ns


def test_non_model_node_and_empty_meta_filtered():
    assert is_main_graph({"langgraph_node": "tools"}) is False
    assert is_main_graph({"langgraph_node": "model"}) is True      # 无 ns 视为根图 ✓
    assert is_main_graph({}) is False
    assert is_main_graph(None) is False


# ---------------------------------------------------------------- 增量提取
def test_iter_deltas_text_and_reasoning():
    assert iter_deltas(_chunk("你好")) == [("answer", "你好")]
    assert iter_deltas(_chunk(reasoning="想一想")) == [("reasoning", "想一想")]
    assert iter_deltas(_chunk("答", "想")) == [("answer", "答"), ("reasoning", "想")]
    assert iter_deltas(_chunk()) == []


def test_iter_deltas_skips_multimodal_content():
    """多模态 content 是 list（本轮贴的图）✗ 不是模型输出 → 不进增量 ✓"""
    assert iter_deltas(SimpleNamespace(content=[{"type": "text", "text": "x"}], additional_kwargs={})) == []


# ---------------------------------------------------------------- 节流 + 聚合保真
def test_sink_throttles_but_keeps_every_char():
    """节流只减少**推送次数** ✗ 不减少**字符**（一个都不能丢 ✓）。"""
    sent = []
    sink = DeltaSink("t1", emit=lambda k, t, s: sent.append((k, t)), min_interval=999.0, min_chars=10 ** 9)
    parts = [f"第{i}块" for i in range(50)]
    for p in parts:
        sink.feed("answer", p)
    assert sink.text == "".join(parts), "聚合文本必须逐字等于喂入序列"
    assert sink.emitted < len(parts), "节流没生效：每块都推了"
    sink.flush()
    assert "".join(t for k, t in sent if k == "answer") == "".join(parts), "flush 后总量仍须逐字相等"
    assert sink.counts["answer"] == 50


def test_sink_char_threshold_triggers_early_send():
    sent = []
    sink = DeltaSink("t2", emit=lambda k, t, s: sent.append((k, t)), min_interval=999.0, min_chars=5)
    sink.feed("answer", "12345")          # 攒够阈值 → 立即推 ✓
    assert sent == [("answer", "12345")], sent


def test_sink_flush_orders_reasoning_first():
    """同一时刻 flush：思考先于正文推（与生成顺序一致 ✓ 前端思考区先有内容 ✓）。

    ⚠️ 先用一块"热身"把节流窗口打开：**首块是立即推的**（设计如此 ✓ 第一眼就要看到字 ✓），
    不热身的话断言测到的是"首块立即推"，不是 flush 的内部顺序 ✗（第一版就是这么写错的 ✓）。
    """
    sent = []
    sink = DeltaSink("t3", emit=lambda k, t, s: sent.append((k, t)), min_interval=999.0, min_chars=10 ** 9)
    sink.feed("answer", "热身")
    sink.flush()
    sent.clear()
    sink.feed("reasoning", "思考")
    sink.feed("answer", "正文")
    assert sink.emitted >= 1, "热身块应立即推送"
    sink.flush()
    assert [k for k, _ in sent] == ["reasoning", "answer"], sent


def test_sink_seq_is_monotonic_and_emit_failure_is_swallowed():
    seen = []

    def boom(k, t, s):
        seen.append(s)
        raise RuntimeError("推送炸了")
    sink = DeltaSink("t4", emit=boom, min_interval=0.0, min_chars=1)
    sink.feed("answer", "a")      # 不抛（推送失败绝不影响这一轮 ✓）
    sink.feed("answer", "b")
    sink.flush()
    assert seen == sorted(seen) and len(seen) >= 2
    assert sink.text == "ab", sink.text        # 推送失败但字符仍在本地累计 ✓（中断时要用它 ✓）
    assert sink.emitted == 0     # 全部推送失败 → emitted 为 0（不谎报成功 ✓）
