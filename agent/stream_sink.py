"""聊天增量输出（v3.1）：把 agent 的 token 流按 delta 推给前端。

**它解决什么**：原来一轮回答是「整轮跑完一次性返回」（`asyncio.to_thread(_run_agent)` +
`await r.json()` ✗），用户看不到正在生成的内容 → 也就没有机会在「方向错了」的时候按中断 ✗。
本模块提供增量侧的三个纯函数 + 一个节流 sink，配合：

  · `api/server.py::_invoke_or_stream`（用 `agent.stream(stream_mode=['messages','values'])` 跑）
  · `api/monitor.py::report_delta`（走既有 WS `/ws/{thread}` 定向推送 ✓）
  · `front/index.html` 的 `applyStreamDelta`（增量渲染 + 最终回答覆盖 ✓）

**四条口径（都有实测依据 ✓）**：

  ① **只推主图**：实测主图 chunk 的 metadata 是 `langgraph_node='model'`、
     `langgraph_checkpoint_ns='model:<uuid>'`；langgraph 子图嵌套用 `|` 分隔
     （子 Agent 作为工具跑在子图里 → `'tools:<uuid>|model:<uuid>'`）→ 含 `|` 一律不推 ✗，
     否则子 Agent（网搜/DB/个人情报）的 token 会串进用户的聊天气泡 ✓
  ② **节流**：每 token 一次 WS 写是洪峰 ✗ → 默认「60ms 或累积 80 字符」推一次 ✓
     （首个增量立即推 ✓ 保证"第一眼就能看到字"✓）；
  ③ **两类增量**：`answer`（正文）/ `reasoning`（思考，来自 `additional_kwargs['reasoning_content']`，
     OpenRouter 的 `reasoning` 字段已由 `ReasoningChatOpenAI._convert_chunk_to_generation_chunk` 归一 ✓）；
  ④ **聚合保真**：sink 的 `.text` 必须与「非流式那条 `invoke` 的正文」**逐字一致** ✗
     —— 这是验收判据（`tests/live/stream_delta_verify.py` 真机比对 ✓），也是"流式不改内容"的保证 ✓。
"""
from __future__ import annotations

import time
from typing import Any, Callable

__all__ = ["MAIN_NODE", "DELTA_MIN_INTERVAL", "DELTA_MIN_CHARS", "is_main_graph", "iter_deltas", "DeltaSink"]

MAIN_NODE = "model"          # 主图的模型节点名（实测：metadata['langgraph_node']）
DELTA_MIN_INTERVAL = 0.06    # 节流：两次推送的最短间隔（秒）
DELTA_MIN_CHARS = 80         # 或某个 kind 攒够这么多字符就先推一次


def is_main_graph(meta: dict | None) -> bool:
    """这个 chunk 是不是**主图**产生的（子图的 token 不推 ✗）。

    依据（实测，2026-10-02）：主图 `langgraph_node='model'` 且
    `langgraph_checkpoint_ns='model:<uuid>'`（不含 `|`）；langgraph 的子图嵌套
    用 `|` 拼 `checkpoint_ns`（如 `tools:<uuid>|model:<uuid>`，子 Agent 就是这种）。
    两道条件都要求，任一不满足即视为子图 → 不推 ✓
    """
    m = meta or {}
    if str(m.get("langgraph_node") or "") != MAIN_NODE:
        return False
    ns = str(m.get("langgraph_checkpoint_ns") or m.get("checkpoint_ns") or "")
    return "|" not in ns


def iter_deltas(chunk: Any) -> list[tuple[str, str]]:
    """从一个模型输出 chunk 里取出增量：`[(kind, text)]`，kind ∈ {'answer','reasoning'}。

    只取**字符串**正文：多模态 content（list 形态）不进增量 ✗（那是本轮的输入图，不是输出 ✓）。
    """
    out: list[tuple[str, str]] = []
    text = getattr(chunk, "content", "") or ""
    if isinstance(text, str) and text:
        out.append(("answer", text))
    rc = (getattr(chunk, "additional_kwargs", {}) or {}).get("reasoning_content") or ""
    if isinstance(rc, str) and rc:
        out.append(("reasoning", rc))
    return out


class DeltaSink:
    """按 kind 缓冲 + 节流后推送的增量汇聚器。

    - `feed(kind, text)`：喂一个增量（不落盘、不判对错，只做转发 ✓）；
    - `flush()`：把缓冲里剩的全推出去（**本轮结束/中断时必须调用** ✓ 否则最后一句话会丢 ✗）；
    - `.text`：answer 类增量的累计全文（中断时用来保留"已流出的半截" ✓ 也是逐字比对的依据 ✓）；
    - `.counts` / `.emitted`：喂入与推送的块数（用于验证节流真的生效 ✓）。
    """

    def __init__(self, thread_id: str, *,
                 emit: Callable[[str, str, int], None] | None = None,
                 min_interval: float = DELTA_MIN_INTERVAL,
                 min_chars: int = DELTA_MIN_CHARS) -> None:
        self.thread_id = thread_id
        self.min_interval = float(min_interval)
        self.min_chars = int(min_chars)
        if emit is None:
            from api.monitor import monitor as _monitor

            def emit(kind: str, text: str, seq: int) -> None:      # noqa: E306 - 局部默认实现
                _monitor.report_delta(kind, text, seq)

        self._emit = emit
        self._buf: dict[str, str] = {"answer": "", "reasoning": ""}
        self._acc: dict[str, str] = {"answer": "", "reasoning": ""}
        self._counts: dict[str, int] = {"answer": 0, "reasoning": 0}
        self._last = 0.0
        self._seq = 0
        self._emitted = 0

    # ---------------------------------------------------------------- 写
    def feed(self, kind: str, text: str) -> None:
        if not text:
            return
        kind = kind if kind in self._buf else "answer"
        self._buf[kind] += text
        self._acc[kind] += text
        self._counts[kind] += 1
        now = time.monotonic()
        if len(self._buf[kind]) >= self.min_chars or (now - self._last) >= self.min_interval:
            self._send(kind, now)

    def flush(self) -> None:
        """把缓冲里剩下的推出去（正常结束与中断都会走到这里 ✓）。"""
        now = time.monotonic()
        for kind in ("reasoning", "answer"):      # 思考先推：保证时间顺序与生成顺序一致 ✓
            self._send(kind, now)

    def _send(self, kind: str, now: float) -> None:
        txt = self._buf.get(kind) or ""
        if not txt:
            return
        self._buf[kind] = ""
        self._seq += 1
        self._last = now
        try:
            self._emit(kind, txt, self._seq)
        except Exception:       # noqa: BLE001 - 推送失败绝不影响这一轮回答 ✓
            pass
        else:
            self._emitted += 1

    # ---------------------------------------------------------------- 读
    @property
    def text(self) -> str:
        """answer 类增量的累计全文（== 前端追加渲染出来的那段 ✓）。"""
        return self._acc["answer"]

    @property
    def reasoning(self) -> str:
        return self._acc["reasoning"]

    @property
    def counts(self) -> dict[str, int]:
        return dict(self._counts)

    @property
    def emitted(self) -> int:
        return self._emitted
