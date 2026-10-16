"""06 · 写工具运行期闸：未经「/」显式授权，本轮不许改工作目录。

定案见 `Verdict/06-…md` 裁定 7（写工具"始终挂载 + 运行期闸"）—— `api/server.py` 的 `AGENT`
是**模块级单例**、工具面在导入期固定，所以不做按轮装配，改成在这里拦。

放行规则（方案 §06 ③ D 的落地口径）：
  · `workdir_create` / `workdir_edit` / `workdir_delete` —— **必须**本轮在输入框用「/」选中了
    写工具集（前端 `write_sets=` 参数 → `api/server.py` 写入下面的 contextvar）；
  · `workdir_list` / `workdir_read` —— 只读，随时可用，不过闸。

拒绝时**如实返回中文说明**（照 `tools/writetofile.py` 的口径），并**不执行**原调用。
"""
from __future__ import annotations

from contextvars import ContextVar

from langchain.agents.middleware.types import AgentMiddleware
from langchain_core.messages import ToolMessage

#: 本轮被「/」选中授权的写工具集名（空 = 未授权）
selected_write_sets: ContextVar[frozenset] = ContextVar("selected_write_sets", default=frozenset())

WRITE_TOOLS = frozenset({"workdir_create", "workdir_edit", "workdir_delete"})

_DENY = ("⛔ 未授权改文件：本轮没有在输入框用「/」选中写工具集，`%s` 不予执行。\n"
         "说明：Agent 的写能力必须由你显式授权 —— 在聊天输入框敲「/」（或用输入框左侧的列表按钮）"
         "选中要用的写工具集后，再提出需求。只读工具（workdir_list / workdir_read）不受此限制。")


def _call_name(request) -> tuple[str, str]:
    tc = getattr(request, "tool_call", None)
    if isinstance(tc, dict):
        return str(tc.get("name") or ""), str(tc.get("id") or "")
    return (str(getattr(tc, "name", "") or ""), str(getattr(tc, "id", "") or ""))


def _deny(name: str, tcid: str) -> ToolMessage:
    return ToolMessage(content=_DENY % name, tool_call_id=tcid, status="error")


class WriteGateMiddleware(AgentMiddleware):
    """运行期闸：拦下未授权的写工具调用。"""

    def wrap_tool_call(self, request, handler):
        name, tcid = _call_name(request)
        if name in WRITE_TOOLS and not selected_write_sets.get():
            return _deny(name, tcid)
        return handler(request)

    async def awrap_tool_call(self, request, handler):
        name, tcid = _call_name(request)
        if name in WRITE_TOOLS and not selected_write_sets.get():
            return _deny(name, tcid)
        return await handler(request)


def set_selected(names) -> None:
    """由 api 层每轮开始时写入（照 usage_counter 的上下文注入方式）。"""
    selected_write_sets.set(frozenset(n for n in (names or []) if n))


def clear() -> None:
    selected_write_sets.set(frozenset())
