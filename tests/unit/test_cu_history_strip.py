# -*- coding: utf-8 -*-
"""v4.1-02：截图不得进历史（每帧 ~345KB base64，重放会撑爆上下文）。"""
from langchain_core.messages import HumanMessage, ToolMessage
from api.server import _strip_tool_images


def _tool_msg():
    return ToolMessage(
        content=[{"type": "text", "text": "已截取第 1 帧"},
                 {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + "A" * 5000}}],
        tool_call_id="call_1", name="screen_capture")


def test_strips_image_blocks():
    out = _strip_tool_images([_tool_msg()])
    content = out[0].content
    assert isinstance(content, list)
    assert all(blk.get("type") != "image_url" for blk in content)
    assert "不存入历史" in content[-1]["text"]
    assert len(str(content)) < 500          # 不再是几万字符


def test_keeps_text_and_other_messages():
    human = HumanMessage(content="普通提问")
    tm = ToolMessage(content="纯文本工具结果", tool_call_id="c2", name="kb")
    out = _strip_tool_images([human, tm])
    assert out[0].content == "普通提问"
    assert out[1].content == "纯文本工具结果"


def test_list_without_images_untouched():
    tm = ToolMessage(content=[{"type": "text", "text": "只有文字"}], tool_call_id="c3", name="x")
    out = _strip_tool_images([tm])
    assert out[0].content == [{"type": "text", "text": "只有文字"}]
