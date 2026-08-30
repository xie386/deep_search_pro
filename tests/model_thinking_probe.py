"""验证当前项目接入的大模型是否会输出「思考内容」(reasoning / thinking)。

用法（项目根目录）：
    .venv/Scripts/python.exe tests/model_thinking_probe.py

判断方法：
  1. 直接用项目同款 model（agent/llm.py 的 init_chat_model）发一个简单推理问题；
  2. 检查 AIMessage 的 .content 以及 .additional_kwargs / .response_metadata 里
     是否含 reasoning_content / reasoning / thinking 等思考字段；
  3. 同时用 provider 原生 client（openai）再发一次，看 raw 响应里有没有 thinking。
不打印任何密钥；只报告模型名（脱敏）与思考字段是否存在。
"""
import os
import sys

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, _ROOT)
os.chdir(_ROOT)
sys.path = [p for p in sys.path if "hermes-agent" not in p and "hermes_agent" not in p]

from dotenv import load_dotenv, find_dotenv
load_dotenv(find_dotenv())

MODEL = os.getenv("LLM_MODEL_MAX", "")
BASE_URL = os.getenv("OPENAI_BASE_URL", "")
KEY = os.getenv("OPENAI_API_KEY", "")


def redact(s: str, keep=6) -> str:
    if not s:
        return "<empty>"
    return s[:keep] + "***"


print(f"模型名(脱敏): {redact(MODEL, 5)}")
print(f"BaseURL(脱敏): {redact(BASE_URL, 12)}")

# ---------------------------------------------------------------------------
# 1) 用项目同款 langchain model 调一次
# ---------------------------------------------------------------------------
from agent.llm import model

probe_q = "请一步步思考：3 个苹果每个 2 元，买 4 组共多少钱？只给最终数字和简要推导。"
print("\n== [1] langchain init_chat_model 调用 ==")
resp = model.invoke(probe_q)
print("  content(前80字):", repr(resp.content[:80]))
kw = getattr(resp, "additional_kwargs", {}) or {}
meta = getattr(resp, "response_metadata", {}) or {}
print("  additional_kwargs keys:", list(kw.keys()))
print("  response_metadata keys:", list(meta.keys()))

thinking_fields = ["reasoning_content", "reasoning", "thinking", "thought"]
found_lc = {k: kw.get(k) for k in thinking_fields if k in kw}
# 有些模型把 reasoning 放到 response_metadata 内
found_meta = {}
for section in meta.values():
    if isinstance(section, dict):
        for k in thinking_fields:
            if k in section:
                found_meta[k] = section[k]
print("  langchain 层思考字段:", found_lc or found_meta or "无")

# ---------------------------------------------------------------------------
# 2) 用 openai 原生 client 看 raw 响应（更可靠地暴露 thinking）
# ---------------------------------------------------------------------------
print("\n== [2] openai 原生 client 调用 ==")
try:
    from openai import OpenAI
    client = OpenAI(api_key=KEY, base_url=BASE_URL)
    raw = client.chat.completions.create(
        model=MODEL,
        messages=[{"role": "user", "content": probe_q}],
        extra_body={"enable_thinking": True} if False else {},  # 不强制，先看默认行为
    )
    choice = raw.choices[0].message
    print("  content(前80字):", repr(choice.content[:80] if choice.content else None))
    rc = getattr(choice, "reasoning_content", None)
    print("  reasoning_content 字段:", repr(rc) if rc else "无(None)")
    # 完整 message 对象其余属性
    extra = {k: v for k, v in choice.__dict__.items() if k not in ("content", "role", "reasoning_content", "tool_calls") and v is not None}
    print("  其他非空字段:", list(extra.keys()) or "无")
except Exception as e:
    print("  openai 原生调用异常（不影响结论）:", repr(e)[:200])

print("\n结论：")
if found_lc or found_meta or rc:
    print("  → 该模型会输出思考内容（reasoning/thinking），但默认是否被智能体展示取决于 deepagents 是否透传。")
else:
    print("  → 默认响应中未携带 reasoning/thinking 字段；该模型当前配置下不向调用方暴露思考内容。")
