# -*- coding: utf-8 -*-
"""真机验证：聊天流式输出（v3.1）—— 同一次运行内「流式聚合 == 最终正文」+ 中断可省 token。

为什么不是"跑两次比对两段文本"✗：模型是**不确定**的，两次跑出的话本来就会不一样 ——
那样比的是随机性，不是流式正确性 ✗。所以判据是**同一次运行内**：

  ① 流式聚合出来的正文 == 这次运行最终提取到的正文（逐字 ✓ 这是"流式不改内容"的保证）；
  ② 增量在**完成之前**就到达了（TTFT < 总耗时 ✓ 否则等于没流）；
  ③ 思考增量也有（模型不吐思考时只警告 ✗ 不算失败 ✓ 供应商差异）；
  ④ 最终状态里 `usage_metadata` 仍在（成本表不受影响 ✓）；
  ⑤ `ZX_STREAM=0` 时回退到一次性 invoke，行为与改造前一致 ✓。

用一句**不需要工具**的话（不碰网搜、不花 Tavily ✓ 不写数据库 ✓）。
跑法：unset PYTHONPATH && .venv/Scripts/python.exe tests/live/stream_delta_verify.py
"""
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ.pop("PYTHONPATH", None)

import api.server as srv                                          # noqa: E402
from api.monitor import monitor                                   # noqa: E402
from agent import answer_text                                     # noqa: E402
from langchain_core.messages import HumanMessage                   # noqa: E402

Q = "用两步推理算一下 17 * 23 等于多少，简要写步骤。"
ok = fail = 0


def check(name, cond, extra=""):
    global ok, fail
    if cond:
        ok += 1
        print(f"  ✓ {name}")
    else:
        fail += 1
        print(f"  ✗ {name}  {extra}")


def _answer(state):
    ans, src = answer_text.restore_answer(state["messages"], {Q})
    return ans, src


print("=" * 90)
print("① 流式路径（默认开）")
events = []
_orig = monitor.report_delta
monitor.report_delta = lambda kind, text, seq=0: events.append((time.time(), kind, text))
t0 = time.time()
try:
    state, streamed = srv._invoke_or_stream(srv.AGENT, {"messages": [HumanMessage(Q)]}, {}, "verify-stream")
finally:
    monitor.report_delta = _orig
t1 = time.time()
ans, src = _answer(state)
stream_text = "".join(t for _, k, t in events if k == "answer")
reason_text = "".join(t for _, k, t in events if k == "reasoning")
first_ms = int((events[0][0] - t0) * 1000) if events else -1
first_ans_ms = int(next((ts for ts, k, _ in events if k == "answer"), 0) - t0) * 1000 if any(k == "answer" for _, k, _ in events) else -1
total_ms = int((t1 - t0) * 1000)
um = None
for m in reversed(state["messages"]):
    if getattr(m, "usage_metadata", None):
        um = m.usage_metadata
        break

print(f"  增量块 {len(events)}（正文 {sum(1 for _, k, _ in events if k=='answer')} / 思考 "
      f"{sum(1 for _, k, _ in events if k=='reasoning')}）· 首块 {first_ms}ms · 首个正文块 {first_ans_ms}ms · 总 {total_ms}ms")
print(f"  流式正文 {len(stream_text)} 字 · 最终正文 {len(ans)} 字（来源 {src}）· 思考 {len(reason_text)} 字")
print(f"  usage_metadata: {um}")
check("走的是流式路径", streamed is True)
check("增量在完成前到达（TTFT < 总耗时）", 0 <= first_ms < total_ms, f"{first_ms} vs {total_ms}")
check("有正文增量", len(stream_text) > 0)
check("★ 流式聚合 == 最终正文（逐字）", stream_text.strip() == ans.strip(),
      f"\n     流式: {stream_text[:60]!r}\n     最终: {ans[:60]!r}")
check("usage_metadata 仍在（成本表不受影响）", bool(um and um.get("total_tokens")))
check("最终正文非空", len(ans.strip()) > 0)
if not reason_text:
    print("  ⚠ 本轮模型没吐 reasoning 增量（供应商/模型差异 ✗ 不算失败）")

print("\n" + "=" * 90)
print("② ZX_STREAM=0 回退路径（应与改造前逐字一致）")
os.environ["ZX_STREAM"] = "0"
events2 = []
monitor.report_delta = lambda kind, text, seq=0: events2.append((time.time(), kind, text))
t2 = time.time()
try:
    state2, streamed2 = srv._invoke_or_stream(srv.AGENT, {"messages": [HumanMessage(Q)]}, {}, "verify-nostream")
finally:
    monitor.report_delta = _orig
    os.environ.pop("ZX_STREAM", None)
ans2, src2 = _answer(state2)
print(f"  总 {int((time.time()-t2)*1000)}ms · 正文 {len(ans2)} 字（来源 {src2}）")
check("回退路径不发任何增量", len(events2) == 0, events2[:2])
check("回退路径 streamed=False", streamed2 is False)
check("回退路径仍拿到正文", len(ans2.strip()) > 0)

print("\n" + "=" * 90)
print(f"结果：{ok}/{ok+fail}" + ("  ✓ 全过" if not fail else "  ✗ 有失败"))
sys.exit(1 if fail else 0)
