# -*- coding: utf-8 -*-
"""本轮「/」授权的写工具集（中立上下文记录）。

**为什么放在 utils/ 而不是 agent/**：写入方是 `api/`（每轮对话开始 set），读取方既有 `agent/`、
也有 `tools/_runtime/`（自配 CLI 的写子命令要在执行期放行）—— 放中立处才不会让 `tools/` 反向依赖
`agent/`（那样会绕成环）。

**口径（用户 2026-10-17 裁定，见 `Verdict/06` 对裁定 7 的修订）**：

  · **内置写工具**（`workdir_create` / `workdir_edit` / `workdir_delete`）**不需要授权** ——
    纯文字提问即可用（"帮我在工作目录建个文件"直接就能做）。它们仍受保护名单 fail-closed、
    凭据红线、路径逃逸三重约束，删除也只进回收站可还原；这些与"授权"无关，照旧。
    为什么改：内置写工具原本被运行期闸拦住，而「/」第二组只列**自配来源**、根本没有内置那一项 ——
    于是它两条路都不通，对没配写池来源的账号等于完全不可用（用户 2026-10-17 报障）。
  · **自配来源的写能力**：`api` / `mcp` 照旧按"人工确认"（`confirmed_at`）放行；
    **CLI 的写子命令**只有**本轮被「/」选中的那一条**才放行（`write_rules` 只在授权时传下去）。
"""
from __future__ import annotations

from contextvars import ContextVar

#: 本轮经「/」选中的写工具集（名字或 CLI 的 bin）；空 = 本轮没有授权任何自配写工具集
selected: ContextVar[frozenset] = ContextVar("authorized_write_sets", default=frozenset())


def set_selected(names) -> None:
    """由 api 层每轮开始时写入（与 `usage_counter` 的上下文注入方式一致）。"""
    selected.set(frozenset(str(n).strip() for n in (names or []) if n and str(n).strip()))


def clear() -> None:
    selected.set(frozenset())


def authorized() -> frozenset:
    return selected.get()


def is_authorized(token) -> bool:
    """`token` 可为自配写工具集的**名字**或**引用**（CLI 的 bin）。"""
    t = str(token or "").strip()
    return bool(t) and t in selected.get()
