# -*- coding: utf-8 -*-
"""v4.1-02 实现前 spike：工具返回的图片能否真正到达模型？

做法：一个 look 工具返回 [文字, image_url(data URL)]；用 ToolNode 执行后把结果回灌给模型，
问「图里是什么颜色」。答出「红」= 图片到达；否则说明框架把返回压成了字符串。
"""
import base64, io, os, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
os.environ.setdefault("PYTHONUTF8", "1")

from PIL import Image
from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import tool
from langgraph.prebuilt import ToolNode

from agent.llm import model

def red_jpeg() -> str:
    im = Image.new("RGB", (64, 64), (220, 20, 40))
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
    print("模型:", os.getenv("LLM_MODEL_MAX"), "@", os.getenv("OPENAI_BASE_URL"))
    m = model.bind_tools([look])
    sys_msg = SystemMessage("你必须调用 look 工具看一眼，再根据图片回答。不要猜。")
    user = HumanMessage("看一眼：屏幕主体是什么颜色？只回答颜色名。")
    ai = m.invoke([sys_msg, user])
    print("第一轮 tool_calls:", [getattr(c, "name", c) for c in (ai.tool_calls or [])])
    if not ai.tool_calls:
        print("模型没有调用工具，spike 无法判定")
        return 2
    # A) 直接调用工具，看返回值是否保留块结构（@tool 会不会把它压成字符串）
    raw = look.invoke({"reason": "spike"})
    print("工具原始返回:", type(raw).__name__, "| 块数:", len(raw) if isinstance(raw, list) else "n/a")

    # B) 手工构造 ToolMessage（与 ToolNode 的产物同形），验证模型侧能否读图
    tm = ToolMessage(content=raw, tool_call_id=ai.tool_calls[0]["id"], name="look")
    print("ToolMessage.content 类型:", type(tm.content).__name__)
    ai2 = m.invoke([sys_msg, user, ai, tm])
    text = ai2.content if isinstance(ai2.content, str) else str(ai2.content)
    print("模型回答:", text[:120].replace("\n", " "))
    ok = ("红" in text) or ("red" in text.lower())
    print("判定:", "图片到达模型" if ok else "图片没有到达（被压成文本或忽略）")
    return 0 if ok else 1

if __name__ == "__main__":
    raise SystemExit(main())
