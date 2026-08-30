"""思考内容捕获（A+ 方案：ReasoningChatOpenAI 透传 + invoke 后提取）。

背景：
  项目模型已换成 agent.reasoning_model.ReasoningChatOpenAI（继承 ChatOpenAI），
  第三方模型的 reasoning_content（思考过程）会进每轮 AIMessage 的 additional_kwargs。
  本模块提供 report_thinking_from_messages()：agent.invoke 完成后遍历结果消息，
  提取各轮 AIMessage 的 reasoning_content，经 monitor.report_thinking 推送前端。

用法（agent.invoke 后调用）：
  from agent.thinking_capture import report_thinking_from_messages
  result = agent.invoke({"messages": [...]}, cfg)
  report_thinking_from_messages(result["messages"])

为什么不是 on_chat_model_end 回调（实时）：
  deepagents 底层用 langchain.agents.create_agent，其模型执行节点
  `_execute_model_sync` 只 `model_.invoke(messages)`——**不传 RunnableConfig**，
  因此 config 里注入的 callbacks 不会触发；而 `model.with_config(callbacks=...)`
  返回 RunnableBinding，会被 deepagents 的 resolve_model 当成字符串 spec 报错。
  故实时回调在 deepagents 层不可行，改在 invoke 完成后一次性提取（覆盖所有轮次）。

相比旧旁路方案：
  - 单请求（省一半 token）；不弃 langchain；
  - 覆盖所有轮次（旁路只覆盖首轮，工具调用后的思考拿不到）。
  代价：thinking 从「逐 token 实时流」降级为「回答完成后一次性全量」。
"""
from langchain_core.messages import AIMessage

from api.monitor import monitor


def report_thinking_from_messages(messages: list) -> list[str]:
    """从 agent 结果消息列表提取各轮 AIMessage 的 reasoning_content 并推送。

    :param messages: agent.invoke 返回的 result["messages"]（含所有轮次消息）
    :return: 捕获到的思考片段列表（供调用方记录/调试）
    """
    captured: list[str] = []
    for m in messages:
        if isinstance(m, AIMessage):
            rc = m.additional_kwargs.get("reasoning_content")
            if rc:
                captured.append(rc)
                monitor.report_thinking(rc)
    return captured
