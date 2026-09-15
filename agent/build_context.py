"""请求装配管线（M1/M2 上下文工程化）——每次 invoke 前拼装完整上下文。

承接 AstrBot 借鉴文档「请求装配管线化」：把动态内容（SOUL 人格 / MEMORY 记忆画像 /
未来 KB 片段）在每次 invoke 前拼装，替代 v1.0「改人格/记忆就重建整个 agent」。

装配管线（M2 版）：
    _apply_persona(SOUL.md) → _apply_memory(MEMORY.md) → _apply_kb(预留)
    → 历史(SQLite) → 上下文预算(截断/压缩) → ContextBundle

注入载体（Spike 验证 2026-09）：deepagents invoke 时 messages[0] 放 SystemMessage
可动态生效并覆盖构建时 system_prompt——因此动态内容拼成一条 SystemMessage 放
messages 首条；main 基础提示词仍由 agent 构建时承载（静态，不进动态消息）。
ContextTruncator 保护所有 SystemMessage → persona 消息永不因截断丢失。

本模块不 import api.* 层（避免反向依赖）：SOUL/MEMORY 文本由调用方（server 层）
读取后经 dynamic 参数传入。
"""
from __future__ import annotations

from dataclasses import dataclass, field

from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage

from agent import conversation_store
from agent.context_budget import (
    ContextConfig,
    ContextManager,
    ProcessedContext,
    default_config,
    default_manager,
)


@dataclass
class ContextBundle:
    """一次请求的完整上下文（invoke 直接可用）。"""

    messages: list[BaseMessage] = field(default_factory=list)  # [SystemMessage(动态), *历史, 提问]
    history_len: int = 0          # 处理前历史消息数
    dropped_rounds: int = 0       # 截断丢弃轮数
    compressed: bool = False      # 是否触发压缩
    new_user_msg: str = ""        # 本次用户提问（**原始文本**，用于落库/展示）
    final_user_msg: str = ""      # 装配后的实际用户文本（含技能前缀，用于定位本轮边界）
    skills_injected: bool = False # 本次是否注入了技能说明书
    cli_injected: bool = False    # 本次是否注入了「可用命令行工具」简报（M4c）
    persona_injected: bool = False  # 本次是否注入了动态 SystemMessage（调试/观测用）


def compose_dynamic_prompt(
    soul_text: str = "",
    memory_text: str = "",
    username: str = "",
    cli_brief: str = "",
) -> str:
    """拼装动态注入内容：人格(SOUL) + 记忆画像(MEMORY) + 可用命令行工具(M4c)。

    只含动态部分（人格在前、记忆画像居中、CLI 简报在后）；main 基础提示词由 agent 构建时
    承载，不在此重复（避免双 system 语义冲突）。
    对应 AstrBot 的 _apply_persona + _apply_memory 段。

    `cli_brief` 由 server 层从 `tools/cli_registry.agent_brief()` 取好传入（本模块不 import api.*）。
    注入目的是消除「明明配好了 CLI 却自称没有/凭知识编造」的幻觉——清单只在用户登记为
    已接入且填了只读命令时才有内容，不配 CLI 的用户零开销。
    """
    parts: list[str] = []
    if soul_text:
        parts.append(f"【人格设定】\n{soul_text}")
    if memory_text:
        mem_hint = (
            f"（记忆文件：agents_docs/{username}/MEMORY.md，可用读取/写入文档工具维护）"
            if username
            else ""
        )
        parts.append(f"【你的用户记忆画像】{mem_hint}\n{memory_text}")
    if cli_brief:
        parts.append(
            "【可用命令行工具（用户自配的 CLI，只读）】\n"
            f"{cli_brief}\n"
            "调用方式：`run_shell_command(command=\"<可执行名>\", argv=[...])`。"
            "只读清单内的命令才能执行，写操作与落盘参数会被拒绝；"
            "被拒绝时如实转述工具返回的错误原文，不要凭知识推断白名单内容。"
        )
    return "\n\n——\n\n".join(parts)


def compose_user_prompt(new_user_msg: str, skills_text: str = "") -> str:
    """把本轮技能说明书拼到用户提问之前（M4a：技能 = user message 前缀）。

    选择 user message 前缀而非 system 注入的原因：技能是「本轮一次性」语义，
    混进 system 会与人格/记忆的常驻语义混淆，且无法随轮次自然消失
    （历史里存的是原始提问，故下一轮自然不再带技能）。

    注入的是**最终文本**；落库/展示仍用原始提问（调用方各自取用）。
    """
    if not skills_text:
        return new_user_msg
    return f"{skills_text}\n\n———\n\n【用户提问】\n{new_user_msg}"


def build_request_context(
    thread_id: str,
    account_id: int,
    new_user_msg: str,
    *,
    soul_text: str = "",
    memory_text: str = "",
    username: str = "",
    skills_text: str = "",
    cli_brief: str = "",
    config: ContextConfig | None = None,
    manager: ContextManager | None = None,
) -> ContextBundle:
    """装配一次对话请求的完整上下文（M2 请求装配管线）。

    :param thread_id: 会话 ID（历史按此读取/落库）
    :param account_id: 账号 ID（数据隔离）
    :param new_user_msg: 本次用户提问（原始文本）
    :param soul_text: 激活人格 SOUL.md 内容（可空）
    :param memory_text: 记忆画像 MEMORY.md 内容（可空）
    :param username: 用户名（记忆提示里标注文件路径）
    :param skills_text: 本轮启用技能的说明书前缀文本（M4a，可空；由 server 层读取传入）
    :param cli_brief: 「可用命令行工具」简报（M4c，可空；由 server 层取 tools/cli_registry 传入）
    :return: ContextBundle.messages 可直接传给 agent.invoke
    """
    mgr = manager or default_manager
    cfg = config or default_config

    # 1. _apply_persona + _apply_memory：动态内容 → SystemMessage（messages 首条）
    dynamic_text = compose_dynamic_prompt(soul_text, memory_text, username, cli_brief or "")
    persona_msg = SystemMessage(content=dynamic_text) if dynamic_text else None

    # 2. 历史（SQLite 持久化）
    history = conversation_store.get_history(account_id, thread_id)

    # 3. _apply_skills（M4a）：技能前缀 + 提问 → 实际用户文本
    final_user_msg = compose_user_prompt(new_user_msg, skills_text)

    # 4. 装配 [persona?] + 历史 + 提问 → 上下文预算（截断/压缩，system 永不丢）
    new_msg = HumanMessage(content=final_user_msg)
    raw = ([persona_msg] if persona_msg else []) + [*history, new_msg]
    processed: ProcessedContext = mgr.process(raw, cfg)

    return ContextBundle(
        messages=processed.messages,
        history_len=len(history),
        dropped_rounds=processed.dropped_rounds,
        compressed=processed.compressed,
        new_user_msg=new_user_msg,
        final_user_msg=final_user_msg,
        skills_injected=bool(skills_text),
        cli_injected=bool(cli_brief),
        persona_injected=persona_msg is not None,
    )
