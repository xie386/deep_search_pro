"""上下文预算管理（M1 上下文工程化核心）。

参考 AstrBot `astrbot/core/agent/context/`（token_counter / truncator / compressor /
manager + config 5 文件 818 行），按项目风格精简为单文件。处理对象是 LangChain
BaseMessage 列表（agent.invoke 的输入形态），conversation_store 负责
OpenAI 格式 dict ↔ LangChain 消息互转。

四大组件：
  TokenCounter       估算消息列表 token 数（启发式，零依赖，可换 tiktoken）
  ContextConfig      策略数据类（上限/轮数/压缩配置）
  ContextTruncator   按轮丢弃 + 修复孤立工具消息
  ContextManager     编排：fix → 计数 → 截断/压缩 → 返回

设计原则：
  - SystemMessage 永不丢弃（人格/记忆画像所在）
  - 工具消息跟随所属 user 轮次一起截断，不留孤立 tool 块
  - 默认只截断不压缩（个人场景够用）；LLM 压缩可选启用，超时自动降级截断
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Protocol

from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)

# ---------------------------------------------------------------------------
# ContextConfig：策略配置
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------- M3-6：记忆注入受预算
# 方案 §5.4：「SystemMessage 永不丢弃、记忆不在预算模型内」→ 改为**画像/笔记各自有硬上限**，
# 超限在**装配期**裁剪（`agent/build_context.py`）。数值只此一份来源，别在这里复制第二份。
# 注意：SystemMessage 仍然永不丢弃（人格与画像丢了比截断更糟），只是注入前先裁到预算内。
try:
    from agent.build_context import (NOTES_MAX_CHARS as MEMORY_NOTES_MAX_CHARS,
                                     PROFILE_MAX_CHARS as MEMORY_PROFILE_MAX_CHARS)
except Exception:                                    # 防御：极端导入顺序下不阻塞
    MEMORY_PROFILE_MAX_CHARS, MEMORY_NOTES_MAX_CHARS = 500, 300
MEMORY_INJECT_BUDGET = {"profile": MEMORY_PROFILE_MAX_CHARS, "notes": MEMORY_NOTES_MAX_CHARS}


@dataclass
class ContextConfig:
    """上下文预算策略（默认值适配免费模型窗口约 8K token）。"""

    max_context_tokens: int = 6000          # 超过此值触发压缩/截断（留余量）
    enforce_max_turns: int = 20             # 最多保留的对话轮数（-1=不限）
    truncate_turns: int = 4                 # 超限时一次性丢弃最早的 N 轮
    llm_compress_enabled: bool = False      # True=先 LLM 摘要再截断；False=只截断
    llm_compress_keep_recent_ratio: float = 0.3  # 摘要时保留最近 30% 消息原文
    llm_compress_instruction: str = (
        "请把以下 AI 对话历史压缩成结构化摘要，保留关键事实、用户偏好、已做出的结论，"
        "用简洁中文输出，不要遗漏用户表达过的稳定偏好。"
    )
    llm_compress_timeout: float = 15.0      # LLM 压缩硬超时（秒），超时降级截断


# ---------------------------------------------------------------------------
# TokenCounter：启发式估算（零依赖）
# ---------------------------------------------------------------------------


class TokenCounter(Protocol):
    def count(self, messages: list[BaseMessage]) -> int: ...


class EstimateTokenCounter:
    """启发式估算：中文 ~1.5 字/token、英文 ~0.75 词/token，工具调用附加开销。

    精度目标 ±20%（v1.0 经验值）。预留 custom_token_counter 钩子可换 tiktoken。
    """

    CJK_COST = 1.5          # 每中文字符约 1.5 token（deepseek 词表实测近似）
    EN_WORD_COST = 0.75     # 每英文单词约 0.75 token
    TOOL_CALL_OVERHEAD = 50  # 每条 assistant 带 tool_calls 的附加保险

    def _estimate_text(self, text: str) -> int:
        if not text:
            return 0
        cjk = sum(1 for ch in text if "\u4e00" <= ch <= "\u9fff")
        other = len(text) - cjk
        return int(cjk * self.CJK_COST + other * self.EN_WORD_COST / 4)

    def count(self, messages: list[BaseMessage]) -> int:
        total = 0
        for m in messages:
            content = m.content
            if isinstance(content, str):
                total += self._estimate_text(content)
            elif isinstance(content, list):  # 多模态块（图片等）
                for block in content:
                    if isinstance(block, dict) and block.get("text"):
                        total += self._estimate_text(block["text"])
            # tool_calls 附加开销（JSON 结构 token）
            tool_calls = getattr(m, "tool_calls", None) or getattr(m, "additional_kwargs", {}).get("tool_calls")
            if tool_calls:
                total += self.TOOL_CALL_OVERHEAD * len(tool_calls)
        return total


# ---------------------------------------------------------------------------
# ContextTruncator：按轮截断 + 修复孤立工具消息
# ---------------------------------------------------------------------------


class ContextTruncator:
    """按「对话轮」为单位丢弃最早消息，保留最近的 N 轮。

    轮的定义：以 HumanMessage 为轮起点，其后的 AIMessage / ToolMessage 归入
    同一轮（工具调用轮次常见 user → ai(tool_calls) → tool → ai 序列）。
    SystemMessage 永远保留在头部。
    """

    @staticmethod
    def _is_turn_start(m: BaseMessage) -> bool:
        return isinstance(m, HumanMessage)

    def fix_messages(self, messages: list[BaseMessage]) -> list[BaseMessage]:
        """修复孤立工具消息：移除没有对应 assistant.tool_calls 的 ToolMessage。"""
        assistant_tool_ids: set[str] = set()
        fixed: list[BaseMessage] = []
        for m in messages:
            if isinstance(m, AIMessage):
                for tc in getattr(m, "tool_calls", []) or []:
                    assistant_tool_ids.add(tc.get("id", ""))
                fixed.append(m)
            elif isinstance(m, ToolMessage):
                if m.tool_call_id in assistant_tool_ids:
                    fixed.append(m)
                # 孤立 tool 消息丢弃
            else:
                fixed.append(m)
        return fixed

    def truncate_by_turns(
        self, messages: list[BaseMessage], max_turns: int, drop_n: int = 4
    ) -> list[BaseMessage]:
        """若对话轮数超过 max_turns，从最早开始丢弃 drop_n 轮（含其工具消息）。

        system 永远保留；不足 drop_n 时丢弃到只剩 max_turns 轮为止。
        返回处理后的消息列表（保持原有顺序）。
        """
        if max_turns <= 0:
            return messages
        # 切轮：记录每个 user 消息的索引 = 一轮的起点
        turn_starts = [i for i, m in enumerate(messages) if self._is_turn_start(m)]
        if len(turn_starts) <= max_turns:
            return messages
        excess = len(turn_starts) - max_turns
        to_drop = min(excess, drop_n)
        # 丢弃从最早一轮到第 to_drop 轮起点之间的消息（system 保留）
        drop_end = turn_starts[to_drop] if to_drop < len(turn_starts) else len(messages)
        # system 消息一定在开头：找到第一个非 system 的位置作为保留起点
        first_non_sys = 0
        for i, m in enumerate(messages):
            if not isinstance(m, SystemMessage):
                first_non_sys = i
                break
        return messages[:first_non_sys] + messages[drop_end:]

    def truncate_by_tokens(
        self, messages: list[BaseMessage], token_counter: TokenCounter, max_tokens: int
    ) -> list[BaseMessage]:
        """token 超限时从最早轮开始丢弃，直到低于 max_tokens 的 80%（留余量）。

        每次至少丢弃 1 轮，最多丢弃到只剩最近 2 轮（保护当前问答上下文）。
        """
        if max_tokens <= 0:
            return messages
        if token_counter.count(messages) <= max_tokens:
            return messages
        budget = int(max_tokens * 0.8)
        # 逐轮丢弃最早轮，直到达标或只剩 2 轮
        while token_counter.count(messages) > budget:
            turn_starts = [i for i, m in enumerate(messages) if self._is_turn_start(m)]
            if len(turn_starts) <= 2:
                break
            first_sys_end = 0
            for i, m in enumerate(messages):
                if not isinstance(m, SystemMessage):
                    first_sys_end = i
                    break
            drop_end = turn_starts[1]  # 丢第 1 轮（第 0 轮起点前的都丢）
            messages = messages[:first_sys_end] + messages[drop_end:]
        return messages


# ---------------------------------------------------------------------------
# Compressor：压缩策略（Protocol + 两个实现）
# ---------------------------------------------------------------------------


class ContextCompressor(Protocol):
    def compress(
        self, messages: list[BaseMessage], keep_recent_ratio: float
    ) -> list[BaseMessage]: ...


class TruncateByTurnsCompressor:
    """兜底压缩：直接截断（无 LLM 依赖）。"""

    def __init__(self, truncator: ContextTruncator, config: ContextConfig):
        self._truncator = truncator
        self._config = config

    def compress(
        self, messages: list[BaseMessage], keep_recent_ratio: float = 0.3
    ) -> list[BaseMessage]:
        max_turns = max(2, int(self._config.enforce_max_turns * keep_recent_ratio))
        return self._truncator.truncate_by_turns(messages, max_turns=max_turns, drop_n=self._config.truncate_turns)


class LLMSummaryCompressor:
    """LLM 摘要压缩：把最早的部分合成一条 system 摘要，保留最近原文。

    llm 参数：可调用对象（model.invoke([...]) -> AIMessage），或 None（降级截断）。
    硬超时保护：llm_compress_timeout 秒内未完成则降级 TruncateByTurns。
    """

    def __init__(self, truncator: ContextTruncator, config: ContextConfig, llm=None):
        self._truncator = truncator
        self._config = config
        self._llm = llm  # 形如 model.invoke 的 callable，接收 list[BaseMessage]

    def compress(
        self, messages: list[BaseMessage], keep_recent_ratio: float = 0.3
    ) -> list[BaseMessage]:
        if self._llm is None:
            return TruncateByTurnsCompressor(self._truncator, self._config).compress(
                messages, keep_recent_ratio
            )
        # 拆 system / 摘要区 / 保留区
        sys_msgs = [m for m in messages if isinstance(m, SystemMessage)]
        rest = [m for m in messages if not isinstance(m, SystemMessage)]
        keep_n = max(2, int(len(rest) * keep_recent_ratio))
        to_summarize, keep_recent = rest[:-keep_n], rest[-keep_n:]
        if not to_summarize:
            return messages
        try:
            summary = self._summarize(to_summarize)
        except Exception as e:
            print(f"[context] LLM 压缩失败，降级截断: {type(e).__name__}: {e}")
            return TruncateByTurnsCompressor(self._truncator, self._config).compress(
                messages, keep_recent_ratio
            )
        return [*sys_msgs, HumanMessage(content=summary), *keep_recent]

    def _summarize(self, messages: list[BaseMessage]) -> str:
        if self._llm is None:
            raise RuntimeError("llm 未配置")
        transcript = "\n".join(
            f"{type(m).__name__}: {m.content}" if isinstance(m.content, str) else str(m.content)
            for m in messages
        )
        prompt = HumanMessage(
            content=f"{self._config.llm_compress_instruction}\n\n--- 对话历史 ---\n{transcript}"
        )
        start = time.time()
        resp = self._llm([prompt])  # 同步调用，超时由调用方 try/except + 计时兜底
        elapsed = time.time() - start
        if elapsed > self._config.llm_compress_timeout:
            raise TimeoutError(f"压缩超时 {elapsed:.1f}s")
        content = getattr(resp, "content", str(resp))
        return f"【历史摘要】{content}"


# ---------------------------------------------------------------------------
# ContextManager：编排
# ---------------------------------------------------------------------------


@dataclass
class ProcessedContext:
    messages: list[BaseMessage] = field(default_factory=list)
    dropped_rounds: int = 0        # 因轮数上限丢弃的轮数
    compressed: bool = False       # 是否触发过压缩
    original_count: int = 0        # 处理前消息数
    token_count: int = 0           # 处理后 token 估算


class ContextManager:
    """编排 fix → 计数 → 压缩/截断，返回处理后上下文。"""

    def __init__(
        self,
        config: ContextConfig | None = None,
        token_counter: TokenCounter | None = None,
        compressor: ContextCompressor | None = None,
    ):
        self.config = config or ContextConfig()
        self.token_counter = token_counter or EstimateTokenCounter()
        self.truncator = ContextTruncator()
        self.compressor = compressor or (
            LLMSummaryCompressor(self.truncator, self.config)
            if self.config.llm_compress_enabled
            else TruncateByTurnsCompressor(self.truncator, self.config)
        )

    def process(
        self, messages: list[BaseMessage], config: ContextConfig | None = None
    ) -> ProcessedContext:
        cfg = config or self.config
        original = len(messages)
        result = ProcessedContext(original_count=original)

        # 1. 修复孤立工具消息
        messages = self.truncator.fix_messages(messages)

        # 2. 轮数上限截断
        if cfg.enforce_max_turns > 0:
            turn_starts = [i for i, m in enumerate(messages) if isinstance(m, HumanMessage)]
            excess = len(turn_starts) - cfg.enforce_max_turns
            if excess > 0:
                dropped = min(excess, cfg.truncate_turns)
                messages = self.truncator.truncate_by_turns(
                    messages, cfg.enforce_max_turns, cfg.truncate_turns
                )
                result.dropped_rounds = dropped

        # 3. token 超限 → 压缩（若启用 LLM 摘要）或 token 截断
        tokens = self.token_counter.count(messages)
        if tokens > cfg.max_context_tokens:
            if isinstance(self.compressor, LLMSummaryCompressor) and cfg.llm_compress_enabled:
                messages = self.compressor.compress(messages, cfg.llm_compress_keep_recent_ratio)
                result.compressed = True
            else:
                messages = self.truncator.truncate_by_tokens(messages, self.token_counter, cfg.max_context_tokens)
                result.compressed = True

        result.messages = messages
        result.token_count = self.token_counter.count(messages)
        return result


# 模块级单例（项目风格：模块级复用）
default_config = ContextConfig()
default_token_counter = EstimateTokenCounter()
default_manager = ContextManager(config=default_config, token_counter=default_token_counter)
