# -*- coding: utf-8 -*-
"""computer use 的工具闸（v4.1-02）。

为什么要在中间件层剥工具，而不是只让工具自己拒绝：
  裁定第 1 条的口径是「关闭时能力不存在」。主 Agent 是进程级单例，工具表在建的时候
  就固定了；只靠工具内部 return「未开启」的话，模型仍然能在工具清单里看到
  `screen_capture` 这张卡，等于「存在但被拦」，与口径不符。

做法：照搬 deepagents 自带的 `_ToolExclusionMiddleware`，但**每次模型调用时**按
本轮开关判断（开关是 contextvar，随本轮任务传播），关闭时把 `screen_capture` 从
`request.tools` 里剥掉 —— 模型连这张卡都看不到。
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from langchain.agents.middleware.types import AgentMiddleware

from utils import cu_mode

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from langchain.agents.middleware.types import (
        ExtendedModelResponse,
        ModelRequest,
        ModelResponse,
        ResponseT,
    )
    from langchain_core.messages import AIMessage


# 只看档的工具：关掉「computer use」就摘掉
GATED_TOOLS: frozenset[str] = frozenset({"screen_capture"})
# 动手档的工具：只有「看 + 动」两档都开才保留（03）
CONTROL_TOOLS: frozenset[str] = frozenset({"mouse_click", "type_text"})


def _debug(msg: str) -> None:
    """只在 ZX_CU_DEBUG=1 时打一行，平时完全静默（排查「工具到底有没有被摘掉」用）。"""
    import os
    if os.environ.get("ZX_CU_DEBUG") == "1":
        print("[CU] " + msg, flush=True)


def _dropped() -> frozenset[str]:
    """本轮该摘掉哪些工具：没开「看」→ 两组都摘；开了「看」没开「动」→ 只摘动手组。"""
    if not cu_mode.is_on():
        return GATED_TOOLS | CONTROL_TOOLS
    if not cu_mode.can_control():
        return CONTROL_TOOLS
    return frozenset()


def _tool_name(tool: Any) -> str | None:
    if isinstance(tool, dict):
        name = tool.get("name")
        return name if isinstance(name, str) else None
    name = getattr(tool, "name", None)
    return name if isinstance(name, str) else None


class ComputerUseGateMiddleware(AgentMiddleware[Any, Any, Any]):
    """关闭「computer use」时，把屏幕相关工具从模型工具表里摘掉。"""

    def wrap_model_call(
        self,
        request: "ModelRequest[Any]",
        handler: "Callable[[ModelRequest[Any]], ModelResponse[Any]]",
    ) -> "ModelResponse[Any]":
        drop = _dropped()
        if drop:
            before = len(request.tools)
            filtered = [t for t in request.tools if _tool_name(t) not in drop]
            if len(filtered) != before:
                _debug("同步路径：已摘掉 %s（%d → %d）" % ("/".join(sorted(drop)), before, len(filtered)))
                request = request.override(tools=filtered)
        else:
            _debug("同步路径：本轮工具表保留（%d 个，control=%s）"
                   % (len(request.tools), cu_mode.can_control()))
        return handler(request)

    async def awrap_model_call(
        self,
        request: "ModelRequest[Any]",
        handler: "Callable[[ModelRequest[Any]], Awaitable[ModelResponse[ResponseT]]]",
    ) -> "ModelResponse[ResponseT] | AIMessage | ExtendedModelResponse[ResponseT]":
        drop = _dropped()
        if drop:
            before = len(request.tools)
            filtered = [t for t in request.tools if _tool_name(t) not in drop]
            if len(filtered) != before:
                _debug("异步路径：已摘掉 %s（%d → %d）" % ("/".join(sorted(drop)), before, len(filtered)))
                request = request.override(tools=filtered)
        else:
            _debug("异步路径：本轮工具表保留（%d 个，control=%s）"
                   % (len(request.tools), cu_mode.can_control()))
        return await handler(request)
