"""关键技术验证：ChatOpenAI（langchain）在 非流式 / 流式 / extra_body 下
能否捕获模型的 reasoning_content 思考内容。决定前端展示思考的可行方案。

用法：.venv/Scripts/python.exe tests/thinking_capture_probe.py
不打印密钥。
"""
import os, sys
_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, _ROOT); os.chdir(_ROOT)
sys.path = [p for p in sys.path if "hermes-agent" not in p and "hermes_agent" not in p]

from dotenv import load_dotenv, find_dotenv
load_dotenv(find_dotenv())
MODEL = os.getenv("LLM_MODEL_MAX", ""); KEY = os.getenv("OPENAI_API_KEY", ""); BASE = os.getenv("OPENAI_BASE_URL", "")
print(f"模型(脱敏): {MODEL[:5]}*** base: {BASE[:14]}***")

from langchain_openai import ChatOpenAI
from langchain_core.callbacks import BaseCallbackHandler

probe = "3 个苹果每个 2 元，买 4 组共多少钱？请先思考再回答。"

class ThinkCapture(BaseCallbackHandler):
    def __init__(self):
        self.chunks_reasoning = []
        self.final_reasoning = None
    def on_llm_new_token(self, token, **kw):
        chunk = kw.get("chunk")
        if chunk is not None:
            ak = getattr(chunk, "additional_kwargs", {}) or {}
            rc = ak.get("reasoning_content")
            if rc:
                self.chunks_reasoning.append(rc)
    def on_llm_end(self, response, **kw):
        try:
            gen = response.generations[0][0]
            msg = getattr(gen, "message", None)
            if msg is not None:
                ak = getattr(msg, "additional_kwargs", {}) or {}
                self.final_reasoning = ak.get("reasoning_content")
                if self.final_reasoning is None:
                    rm = getattr(msg, "response_metadata", {}) or {}
                    self.final_reasoning = rm.get("reasoning_content")
        except Exception as e:
            print("  on_llm_end 异常:", e)

# ---- 实验1：非流式 ----
print("\n== 实验1: 非流式 invoke（项目同款配置）==")
try:
    m = ChatOpenAI(model=MODEL, api_key=KEY, base_url=BASE)
    cb = ThinkCapture()
    r = m.invoke(probe, config={"callbacks":[cb]})
    ak = getattr(r, "additional_kwargs", {}) or {}
    print("  content:", repr(r.content[:60]))
    print("  additional_kwargs keys:", list(ak.keys()))
    print("  捕获到 thinking:", bool(cb.final_reasoning), "| 长度:", len(cb.final_reasoning) if cb.final_reasoning else 0)
except Exception as e:
    print("  异常:", repr(e)[:200])

# ---- 实验2：流式 ----
print("\n== 实验2: 流式 stream ==")
try:
    m2 = ChatOpenAI(model=MODEL, api_key=KEY, base_url=BASE)
    cb2 = ThinkCapture()
    parts = []
    for chunk in m2.stream(probe, config={"callbacks":[cb2]}):
        ak2 = getattr(chunk, "additional_kwargs", {}) or {}
        if ak2.get("reasoning_content"):
            cb2.chunks_reasoning.append(ak2["reasoning_content"])
        if chunk.content:
            parts.append(chunk.content)
    total_thinking = "".join(cb2.chunks_reasoning)
    print("  最终 content:", repr("".join(parts)[:60]))
    print("  流式 chunk 捕获 thinking 段数:", len(cb2.chunks_reasoning))
    print("  thinking 总长度:", len(total_thinking))
    print("  thinking 预览:", repr(total_thinking[:80]))
except Exception as e:
    print("  异常:", repr(e)[:200])

# ---- 实验3: extra_body 强制 thinking ----
print("\n== 实验3: 非流式 + extra_body enable_thinking ==")
try:
    m3 = ChatOpenAI(model=MODEL, api_key=KEY, base_url=BASE,
                    model_kwargs={"extra_body": {"enable_thinking": True}})
    cb3 = ThinkCapture()
    r3 = m3.invoke(probe, config={"callbacks":[cb3]})
    ak3 = getattr(r3, "additional_kwargs", {}) or {}
    print("  additional_kwargs keys:", list(ak3.keys()))
    print("  捕获到 thinking:", bool(cb3.final_reasoning), "| 长度:", len(cb3.final_reasoning) if cb3.final_reasoning else 0)
except Exception as e:
    print("  异常:", repr(e)[:200])
