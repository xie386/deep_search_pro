# -*- coding: utf-8 -*-
"""v4.1-02 spike 2：走真实 create_deep_agent，看工具返回的图片是否仍然到达模型。"""
import base64, io, os, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
os.environ.setdefault("PYTHONUTF8", "1")

from PIL import Image
from langchain_core.tools import tool
from deepagents import create_deep_agent
from agent.llm import model

def red_jpeg() -> str:
    im = Image.new("RGB", (64, 64), (20, 90, 220))
    buf = io.BytesIO(); im.save(buf, "JPEG", quality=85)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode("ascii")

@tool
def look(reason: str) -> list:
    """看一眼屏幕，返回一张图。"""
    return [
        {"type": "text", "text": "已截取屏幕（capture #1，64x64）"},
        {"type": "image_url", "image_url": {"url": red_jpeg()}},
    ]

def main() -> int:
    agent = create_deep_agent(
        model=model,
        system_prompt="你必须调用 look 工具看一眼，再根据图片回答，不许猜。",
        tools=[look],
    )
    out = agent.invoke({"messages": [{"role": "user", "content": "看一眼：屏幕主体是什么颜色？只回答颜色名。"}]})
    msgs = out.get("messages", [])
    print("消息数:", len(msgs))
    for m in msgs:
        kind = getattr(m, "type", type(m).__name__)
        c = getattr(m, "content", "")
        shape = type(c).__name__ + (("/%d blocks" % len(c)) if isinstance(c, list) else "")
        print(" -", kind, shape, (str(c)[:60].replace("\n", " ") if not isinstance(c, list) else ""))
    final = msgs[-1].content if msgs else ""
    text = final if isinstance(final, str) else str(final)
    ok = ("蓝" in text) or ("blue" in text.lower())
    print("最终回答:", text[:100].replace("\n", " "))
    print("判定:", "deepagents 链路可用" if ok else "deepagents 链路没拿到图")
    return 0 if ok else 1

if __name__ == "__main__":
    raise SystemExit(main())
