"""ReasoningChatOpenAI：继承 ChatOpenAI，透传第三方 reasoning_content（A+ 方案）。

背景：
  langchain-openai 的 BaseChatOpenAI 只面向 OpenAI 官方 API 规范，第三方 provider
  的非标准字段 reasoning_content / reasoning_details 一律不提取（源码
  `_convert_delta_to_message_chunk` 只读 content/function_call/tool_calls）。
  官方 docstring 建议「Use a provider-specific subclass for full provider support」。

  本类照抄官方 langchain-deepseek 的 ChatDeepSeek 实现（继承 BaseChatOpenAI，
  重写两个方法），把 reasoning_content 塞进 additional_kwargs——单请求、不弃
  langchain，替代「底层 client 双请求旁路」方案。

用法：
  from agent.reasoning_model import ReasoningChatOpenAI
  model = ReasoningChatOpenAI(model=..., api_key=..., base_url=...)
  # invoke 后：result.additional_kwargs["reasoning_content"] 即思考内容（非流式全量）
  # astream 后：chunk.additional_kwargs["reasoning_content"] 为逐 delta 增量（需拼接）
"""
from typing import Any

import openai
from langchain_core.messages import AIMessageChunk
from langchain_core.outputs import ChatGenerationChunk, ChatResult
from langchain_openai import ChatOpenAI

from agent.cancel import check as _check_cancel


class ReasoningChatOpenAI(ChatOpenAI):
    """在 ChatOpenAI 基础上保留 reasoning_content（DeepSeek-R1 等思考模型）。"""

    # ------------------------------------------------------------------
    # 用户强制中断的唯一检查点（见 agent/cancel.py 的模块说明）：
    # 每个 agent 步骤都要过一次模型调用，故在真正发请求前检查中断标志，
    # raise AgentCancelled 后异常一路穿透 langgraph 到 invoke 调用方。
    # 覆盖关系：invoke → _generate；ainvoke/astream → langchain 默认把
    # _generate/_stream 丢线程池执行，所以这两个口子就够，不用再写 _agenerate。
    # ------------------------------------------------------------------
    def _generate(self, *args: Any, **kwargs: Any) -> ChatResult:
        _check_cancel()
        return super()._generate(*args, **kwargs)

    def _stream(self, *args: Any, **kwargs: Any):
        _check_cancel()
        return super()._stream(*args, **kwargs)

    # ------------------------------------------------------------------
    # 非流式：invoke/ainvoke 时，把 response.choices[0].message.reasoning_content
    # 塞进最终 AIMessage.additional_kwargs
    # ------------------------------------------------------------------
    def _create_chat_result(
        self,
        response: dict | openai.BaseModel,
        generation_info: dict | None = None,
    ) -> ChatResult:
        rtn = super()._create_chat_result(response, generation_info)
        choices = getattr(response, "choices", None)
        if not choices:
            return rtn
        message = getattr(choices[0], "message", None)
        # ① 原生 OpenAI 兼容端点：reasoning_content 是 message 的属性
        if message is not None and hasattr(message, "reasoning_content"):
            rc = getattr(message, "reasoning_content", None)
            if rc:
                rtn.generations[0].message.additional_kwargs["reasoning_content"] = rc
        # ② OpenRouter 等把思考放在 reasoning 字段（model_extra 里）
        elif message is not None and hasattr(message, "model_extra"):
            me = getattr(message, "model_extra", None)
            if isinstance(me, dict) and me.get("reasoning"):
                rtn.generations[0].message.additional_kwargs["reasoning_content"] = me[
                    "reasoning"
                ]
        return rtn

    # ------------------------------------------------------------------
    # 流式：astream 时，每个 chunk 的 delta.reasoning_content 塞进
    # generation_chunk.message.additional_kwargs（调用方须自行拼接累积）
    # ------------------------------------------------------------------
    def _convert_chunk_to_generation_chunk(
        self,
        chunk: dict,
        default_chunk_class: type,
        base_generation_info: dict | None,
    ) -> ChatGenerationChunk | None:
        generation_chunk = super()._convert_chunk_to_generation_chunk(
            chunk, default_chunk_class, base_generation_info
        )
        if (choices := chunk.get("choices")) and generation_chunk:
            if isinstance(generation_chunk.message, AIMessageChunk):
                delta = choices[0].get("delta", {}) or {}
                rc = delta.get("reasoning_content")
                if rc is not None:
                    generation_chunk.message.additional_kwargs["reasoning_content"] = rc
                elif (reasoning := delta.get("reasoning")) is not None:  # OpenRouter
                    generation_chunk.message.additional_kwargs["reasoning_content"] = (
                        reasoning
                    )
        return generation_chunk
