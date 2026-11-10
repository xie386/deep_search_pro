# -*- coding: utf-8 -*-
"""computer use 模式开关（v4.1-02）。

口径（见 `docs/v4.1/Verdict/02-悬浮球-屏幕共享.md` 第 1 条）：

* **按会话开**：开关状态由前端记在 `localStorage`，随 `/api/chat` 的 `computer_use=1` 传上来；
  后端只认请求里的这一个布尔值，本模块不读库、不读环境变量。
* **关闭即不存在**：`screen_capture` 工具在未开启时直接拒绝，提示词里也不会出现相关说明。
* **按轮生效**：每轮由 `_run_agent` 调 `begin_turn()` 建一份新状态，轮末 `end_turn()` 丢弃；
  计数器（本轮已截几张、上一帧哈希）都挂在这份状态上，跨请求不残留。

用 contextvar 而不是全局变量：`_run_agent` 在工作线程里跑，多个并发请求互不串号。
"""
from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass, field


@dataclass
class CuTurn:
    """一轮 computer use 的临时状态（只活在内存里，轮末丢弃）。"""

    on: bool = False
    shots: int = 0                 # 本轮已截张数
    next_cid: int = 1              # 帧号（单调递增，供模型/03 指代具体某一帧）
    last_hash: str = ""            # 上一帧内容哈希（同屏去重）
    last_cid: int = 0              # 上一帧帧号（复用时回给模型）
    last_meta: dict = field(default_factory=dict)
    # ---- v4.1-03 屏幕控制（见 Verdict/03） ----
    control: bool = False          # 本轮是否「看 + 动」（allow_control）
    actions: int = 0               # 本轮已执行的动作数（受 CU_MAX_ACTIONS_PER_TURN 约束）
    last_scale: float = 1.0        # 最近一帧的缩放比（图上像素 ÷ 物理像素）
    last_rect: tuple = ()          # 最近一帧的 screen_rect（物理像素 左/上/宽/高）
    thread_id: str = ""            # 本轮会话号（写审计用）


_OFF = CuTurn(on=False)
_TURN: ContextVar[CuTurn | None] = ContextVar("cu_turn", default=None)


def begin_turn(on: bool, control: bool = False, thread_id: str = "") -> CuTurn:
    """开一轮：新建状态并挂到当前上下文。`on=False` 时工具一律拒绝；
    `control=True` 表示本轮额外允许「动手」（屏幕控制，03 起）。"""
    turn = CuTurn(on=bool(on), control=bool(on) and bool(control), thread_id=str(thread_id or ""))
    _TURN.set(turn)
    return turn


def end_turn() -> None:
    """轮末清场：状态丢弃，计数器归零（没有任何东西落盘）。"""
    _TURN.set(None)


def current() -> CuTurn:
    return _TURN.get() or _OFF


def is_on() -> bool:
    return current().on


def can_control() -> bool:
    """本轮是否允许动手：既要「看」开着，也要「看 + 动」开着。"""
    cur = current()
    return bool(cur.on and cur.control)


def set_frame(cid: int, scale: float, rect: tuple) -> None:
    """截图工具把最近一帧的帧号/缩放比/屏幕矩形登记到轮内状态（03 的坐标换算与防旧帧依赖它）。"""
    cur = current()
    cur.last_cid = int(cid)
    cur.last_scale = float(scale or 1.0)
    cur.last_rect = tuple(rect or ())


def bump_action() -> int:
    """动作计数器 +1，返回执行后的值。"""
    cur = current()
    cur.actions += 1
    return cur.actions
