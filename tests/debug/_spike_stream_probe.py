# -*- coding: utf-8 -*-
"""临时 spike（`_` 前缀，可删）：判定 langgraph 的 token 级流式在本项目能不能用。

问题：`langchain/agents/factory.py:1406 _execute_model_sync` 是 `model_.invoke(messages)`，
**没有显式传 config** → `stream_mode="messages"`（靠回调透传）到底能不能收到 token ✗/✓？

做法：拿项目自己的 agent（与 /api/chat 同一个构建路径）跑一句**不需要工具**的话
（学 M6b 探针 #42「你是谁」= 0 次工具调用 ✓ 不触发网搜 ✓ 不碰数据库 ✓），
用 astream 收流，打印：① 有没有 (chunk, metadata) ② chunk 里有没有正文/思考 ③ 首块到达耗时。

用法：unset PYTHONPATH && .venv/Scripts/python.exe tests/debug/_spike_stream_probe.py
"""
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ.pop("PYTHONPATH", None)

import api.server as srv  # noqa: E402
from langchain_core.messages import HumanMessage  # noqa: E402

# 项目自己的 agent：api/server.py:185 的模块级全局单例（与 /api/chat 无自定义模型时同一对象）
agent = getattr(srv, "AGENT", None)
print("[spike] agent =", type(agent).__name__ if agent is not None else None)
if agent is None:
    raise SystemExit("没找到 srv.AGENT（看 api/server.py:185）")

q = "用两步推理算一下 17 × 23 等于多少，简要写步骤。"
t0 = time.time()
got = {"final_kwargs": {}, "final_text": "", "chunks": 0, "text": "", "reason": "", "first_ms": None, "modes": set(), "nodes": set()}
print("[spike] 开始 stream(stream_mode=['messages','updates']) …")
for item in agent.stream({"messages": [HumanMessage(content=q)]},
                          stream_mode=["messages", "updates"]):
    # langgraph 多 stream_mode 时返回 (mode, payload) 元组
    mode, payload = item if isinstance(item, tuple) and len(item) == 2 else ("?", item)
    got["modes"].add(mode)
    if got["first_ms"] is None:
        got["first_ms"] = int((time.time() - t0) * 1000)
    if mode == "updates" and isinstance(payload, dict):
        for _node, _out in payload.items():
            for _m in ((_out or {}).get("messages") or []):
                if getattr(_m, "additional_kwargs", None):
                    got["final_kwargs"] = dict(_m.additional_kwargs)
                if getattr(_m, "type", "") == "ai":
                    got["final_text"] = str(getattr(_m, "content", "") or "")
                    globals()["_agg_msg"] = _m
    if mode == "messages":
        chunk, meta = payload if isinstance(payload, tuple) else (payload, {})
        got["chunks"] += 1
        text = getattr(chunk, "content", "") or ""
        rc = (getattr(chunk, "additional_kwargs", {}) or {}).get("reasoning_content") or ""
        got["text"] += text if isinstance(text, str) else str(text)
        got["reason"] += rc
        if meta.get("langgraph_node"):
            got["nodes"].add(str(meta["langgraph_node"]))
        if got.setdefault("meta_dump", None) is None:
            got["meta_dump"] = {k: str(v)[:70] for k, v in (meta or {}).items()}
    if got["chunks"] <= 6 or got["chunks"] % 25 == 0:
        print(f"  +{int((time.time()-t0)*1000):>6}ms [{mode}] chunk#{got['chunks']} "
              f"text={repr((getattr(payload[0] if isinstance(payload, tuple) else payload, 'content', '') or ''))[:60]}")

print("\n===== 结论 =====")
print("流模式收到:", sorted(got["modes"]))
print("token 块数:", got["chunks"], "｜ 首块到达:", got["first_ms"], "ms ｜ 总耗时:", int((time.time() - t0) * 1000), "ms")
print("正文字符数:", len(got["text"]), "｜ 思考字符数:", len(got["reason"]))
print("节点标签:", sorted(got["nodes"])[:6])
print("正文前 80 字:", got["text"][:80].replace("\n", " "))
print("思考前 80 字:", got["reason"][:80].replace("\n", " "))
_um = None
for _node, _out in ({} if False else {}).items():
    pass
print("\n与成本表相关的两项（流式必须验 ✗）：")
print("  usage_metadata:", getattr(_agg_msg, "usage_metadata", None) if _agg_msg is not None else "(未取到)")
print("  response_metadata 键:", sorted((getattr(_agg_msg, "response_metadata", {}) or {}).keys())[:8] if _agg_msg is not None else "-")
print("\nmetadata 全貌（主图这一块）：")
for k, v in (got.get("meta_dump") or {}).items():
    print(f"    {k} = {v}")
print("\n聚合后（updates 里的最终 AIMessage）：")
print("  content 长度:", len(got["final_text"]), "｜ additional_kwargs 键:", sorted(got["final_kwargs"]))
_rk = got["final_kwargs"].get("reasoning_content") or ""
print("  聚合后的 reasoning_content 长度:", len(_rk), "｜ 前 60 字:", str(_rk)[:60].replace("\n", " "))
print("\n判定: ", "✅ stream_mode='messages' 可用（图级流式方案成立）"
      if got["chunks"] > 0 else "✗ 收不到 token（config 未透传）→ 只能走模型级自流（在 _generate 里包 stream）")
