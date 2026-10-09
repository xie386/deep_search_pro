# -*- coding: utf-8 -*-
"""最终回答文本的提取与清洗（2026-09-24 用户报障修复）。

背景（实测约 1/5 概率）：推理模型（deepseek-flash 系）偶尔把**可见答案**写在
`additional_kwargs["reasoning_content"]` 里，而 `message.content` 为空（或只有一个换行）。
原实现直接 `result["messages"][-1].content` → 返回空串 → 前端气泡没正文，而思考面板里
却有"正文"（因为 `agent/thinking_capture.py` 会把 reasoning_content 推过去），
刷新后那条空 assistant 又被原样落库 → 显示成"只有标题、内容为空"的报告卡。

本模块给「怎么从消息列表里取出本轮答案」一个**唯一**实现，供 `api/server.py`（聊天）
与 `agent/digest_engine.py`（周报）共用，避免两处各写一份、各漏一边。

规则（按优先级）：
  1. 本轮最后一条 AIMessage 的 content（正常路径）；
  2. 若为空 → 该条的 reasoning_content（**回填**，2026-09-24 用户拍板：那就是模型给出的正文）；
  3. 仍为空 → 本轮内往前找最近一条「有正文且不是纯工具调用」的 AIMessage；
  4. 都没有 → 返回空串，并由调用方决定怎么提示（绝不静默返回空白）。

⚠️ 必须限定在**本轮**（`since`）范围内找：历史里全是往轮的问答，跨轮回溯会把
上一轮的旧答案当成这一轮的答案。
"""
from __future__ import annotations

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage

SOURCE_CONTENT = "content"
SOURCE_REASONING = "reasoning"   # 回填（模型把正文放进了思考字段）
SOURCE_EARLIER = "earlier"       # 本轮更早的一条非工具调用回答
SOURCE_NONE = "none"


def text_of(msg: BaseMessage) -> str:
    """把消息内容规范成字符串（兼容 content 是分块列表的情形）。"""
    c = getattr(msg, "content", "")
    if isinstance(c, list):
        parts = []
        for b in c:
            if isinstance(b, dict):
                parts.append(str(b.get("text") or b.get("content") or ""))
            else:
                parts.append(str(b))
        return "".join(parts)
    return str(c or "")


def reasoning_of(msg: BaseMessage) -> str:
    """取 reasoning_content（第三方推理模型经 ReasoningChatOpenAI 透传到 additional_kwargs）。"""
    try:
        return str((getattr(msg, "additional_kwargs", None) or {}).get("reasoning_content") or "")
    except Exception:  # noqa: BLE001
        return ""


def message_text(m: BaseMessage) -> str:
    """取一条消息的**纯文本**（多模态 content 是块列表 → 拼出文字块 ✓）。

    ★ v3.1 视觉：`content` 可能是 `[{type:text},{type:image_url}]` ✗，
      任何把它当字符串/集合成员用的地方都会 `TypeError: unhashable type: 'list'` ✓
      （用户实测崩在两处：本模块的边界匹配 + api/server.py 的落库边界 ✓）
      → 统一走这里 ✓，别再各自 `m.content in ...` ✗。
    """
    c = getattr(m, "content", "")
    if isinstance(c, str):
        return c
    if isinstance(c, list):
        return " ".join(str(b.get("text") or "") for b in c
                        if isinstance(b, dict) and b.get("type") == "text")
    return str(c or "")


def turn_messages(messages: list[BaseMessage], since: set[str] | None = None) -> list[BaseMessage]:
    """本轮的消段（上一条 HumanMessage 之后的部分）；`since` 用于匹配「装配后的最终用户文本」。"""
    if not messages:
        return []
    if not since:
        # 没有边界信息时，退化为「最后一条 HumanMessage 之后」
        for i in range(len(messages) - 1, -1, -1):
            if isinstance(messages[i], HumanMessage):
                return messages[i + 1:]
        return list(messages)
    for i in range(len(messages) - 1, -1, -1):
        m = messages[i]
        if isinstance(m, HumanMessage) and (message_text(m) in since):
            return messages[i + 1:]
    return list(messages)


def final_answer(messages: list[BaseMessage], since: set[str] | None = None) -> tuple[str, str]:
    """取本轮最终回答。返回 `(文本, 来源)`，来源见 SOURCE_* 常量。"""
    turn = [m for m in turn_messages(messages, since) if isinstance(m, AIMessage)]
    if not turn:
        return "", SOURCE_NONE

    last = turn[-1]
    txt = text_of(last)
    if txt.strip():
        return txt, SOURCE_CONTENT

    rc = reasoning_of(last)
    if rc.strip():
        return rc, SOURCE_REASONING

    # 本轮再往前找一条「有正文且不是纯工具调用」的回答（跳过工具轮的前言）
    for m in reversed(turn[:-1]):
        if getattr(m, "tool_calls", None):
            continue
        t = text_of(m)
        if t.strip():
            return t, SOURCE_EARLIER

    return "", SOURCE_NONE


def is_empty_assistant(msg: BaseMessage) -> bool:
    """「空内容且无 tool_calls」的 assistant 消息 —— 没有任何信息量，不该落库/不该渲染。

    ⚠️ 带 `tool_calls` 的**必须保留**：OpenAI 格式要求 tool 消息紧跟一条带 tool_calls 的
    assistant；丢掉它，重放历史时 tool 消息就成孤儿，下一轮请求会被上游 400 拒绝。
    """
    if not isinstance(msg, AIMessage):
        return False
    if getattr(msg, "tool_calls", None):
        return False
    return not text_of(msg).strip()


def restore_answer(messages: list[BaseMessage], since: set[str] | None = None) -> tuple[str, str]:
    """取答案，并在「回填/回溯」发生时**把文本写回该条消息的 content**。

    为什么要写回：落库走的是同一批消息对象，若只改返回值，回填出来的正文不会被持久化
    —— 于是"刷新后又变成空卡"。写回后 DB 与实时界面一致。

    :return: `(answer, source)`
    """
    turn = turn_messages(messages, since)
    answer, source = final_answer(messages, since)
    if source in (SOURCE_EARLIER,) and answer:
        # 往前回溯出来的答案：挂到本轮最后一条 AIMessage 上（保留其 reasoning 与 tool_calls）
        for m in reversed(turn):
            if isinstance(m, AIMessage):
                m.content = answer
                break
    elif source == SOURCE_REASONING and answer:
        # 回填：最后一条 AIMessage 的 content 为空、正文在 reasoning 里 → 写回 content
        for m in reversed(turn):
            if isinstance(m, AIMessage):
                m.content = answer
                break
    return answer, source


def drop_empty_assistant(messages: list[BaseMessage]) -> list[BaseMessage]:
    """落库前过滤：去掉「空内容且无 tool_calls」的 assistant（工具轮与空回复都不留痕）。"""
    return [m for m in messages if not is_empty_assistant(m)]
