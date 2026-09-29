# -*- coding: utf-8 -*-
"""M4-6 · 动态注入源的**注册表**（依赖倒置，方案 §3.2 / C5）。

问题：装配一次请求要注入 SOUL / MEMORY / 技能 / CLI 简报 —— 原本这些都在 `api/server.py` 里
各自取好再当参数传下去（`build_request_context(soul_text=…, memory_text=…, skills_text=…, cli_brief=…)`），
于是"新增一个注入源"要同时改 server + build_context 的签名 + 装配逻辑**三处**，漏一处就静默不注入。

做法（依赖倒置）：本模块**只提供注册与收集**，不认识任何一个具体来源；
「谁拥有文件谁注册」—— `api/context_providers.py` 在启动时注册 soul/memory/…。
这样绕开了铁律「`agent/build_context.py` 不 import `api.*`」：agent 侧定义接口，api 侧提供实现。

三条硬口径：
  · **顺序确定**：按注册顺序（dict 保序）→ 装配结果稳定可比对（M4-7 的等价性回归依赖这一点）；
  · **异常降级**：任一 provider 抛错 → 该源记为**空串**，绝不中断这次对话；
  · **不 import `api.*`**：保持分层，注册由 api 侧完成。
"""
from typing import Callable, NamedTuple


class SourceResult(NamedTuple):
    """一个注入源的结果：正文 + 是否"确实注入了内容"（供装配侧决定要不要推"已注入 N 条"之类提示）。"""
    text: str = ""
    injected: bool = False


_PROVIDERS: "dict[str, Callable[[int, str], SourceResult]]" = {}


def register(name: str, provider: Callable[[int, str], SourceResult]) -> None:
    """注册/覆盖一个注入源（同名再注册 = 覆盖，**保持原位置**，顺序不因覆盖而乱）。"""
    key = str(name or "").strip()
    if not key or provider is None:
        return
    _PROVIDERS[key] = provider


def names() -> tuple:
    """已注册的源名（**按注册顺序**）。"""
    return tuple(_PROVIDERS.keys())


def collect(account_id: int, question: str = "") -> "dict[str, str]":
    """按注册顺序收集所有源 → `{name: text}`（异常降级为空串，绝不抛）。"""
    out: "dict[str, str]" = {}
    for name, fn in list(_PROVIDERS.items()):
        text = ""
        try:
            r = fn(account_id, question)
            if isinstance(r, SourceResult):
                text = r.text or ""
            elif isinstance(r, str):
                text = r                    # 宽容：provider 直接返回字符串也认
            elif isinstance(r, (tuple, list)) and r:
                text = str(r[0] or "")
        except Exception:
            text = ""                       # ★ 降级：一个源坏了不影响这次对话
        out[name] = (text or "").strip() if isinstance(text, str) else ""
    return out


def injected_flags(account_id: int, question: str = "") -> "dict[str, bool]":
    """各源"是否注入了内容"（供装配侧推提示；与 collect 同一套顺序与降级口径）。"""
    out: "dict[str, bool]" = {}
    for name, fn in list(_PROVIDERS.items()):
        try:
            r = fn(account_id, question)
            out[name] = bool(r.injected) if isinstance(r, SourceResult) else bool(r)
        except Exception:
            out[name] = False
    return out


def clear() -> None:
    """仅供测试隔离使用。"""
    _PROVIDERS.clear()
