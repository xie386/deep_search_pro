"""Shell 工具层：把 CLI 面板那套白名单执行器暴露给 Agent。

能力层在 `tools/_runtime/shell_runtime.py`（与 CLI 面板共用同一执行器，
所以「Agent 能跑什么」和「用户在面板里能敲什么」完全一致，不会两套规则漂移）。

归属：`get_owner_context()` → account_id → username → 沙箱 `data/sandbox/{username}/`；
account_id 同时决定「已接入的第三方 CLI」动态白名单（M4c）。

注意参数名必须是 `argv` 而不是 `args`：`BaseTool.args` 是 langchain 的属性，
工具函数用 `args` 做参数名会与之冲突，调用时报
`TypeError: run_shell_command() got an unexpected keyword argument 'v__args'`。
"""
from __future__ import annotations

from langchain_core.tools import tool

from api.context import get_owner_context
from api.monitor import monitor
from tools._runtime import shell_runtime


def _owner() -> tuple[str | None, int | None]:
    """当前账号的 (username, account_id)——沙箱目录与第三方 CLI 登记都按账号隔离。"""
    oid = get_owner_context()
    if oid is None:
        return None, None
    try:
        from tools.schema_personal import get_personal_conn
        conn = get_personal_conn()
        try:
            row = conn.execute("SELECT username FROM accounts WHERE id=?", (oid,)).fetchone()
        finally:
            conn.close()
        return (row["username"] if row else None), oid
    except Exception:  # noqa: BLE001 - 查库失败退回公共沙箱、且不放行第三方 CLI
        return None, None


@tool
def run_shell_command(command: str, argv: list) -> str:
    """在沙箱目录里执行一条白名单内的本地命令（CLI 面板同款执行器）。

    **可用命令**（严格匹配，大小写敏感）：
    - `ffmpeg` + argv：音视频处理，如 ["-i","in.mp4","-vn","-acodec","libmp3lame","out.mp3"]
    - `git` + argv：版本控制，限安全子命令（init/status/log/diff/add/commit 等）
    - `curl` + argv：HTTP **GET** 抓取，如 ["-s","--max-time","15","https://example.com"]
    - `ls` / `cat` / `grep` / `echo` / `pwd` / `mkdir` + argv：沙箱内文件操作
      （如 ["cat","notes.md"]、["grep","-i","报价","docs"]）

    **用户自配的 CLI（M4c）**：用户在「自定义 CLI」页配置并登记为已接入的 CLI 会进入动态白名单，
    其**可执行名与允许的只读子命令清单会写在系统提示词的【可用命令行工具（用户自配的 CLI，只读）】区块里**——
    照那份清单调用即可，别凭猜测；写操作子命令与落盘参数一律拒绝，未登记/未配置的 CLI 不可调用。

    **必须知道的限制**：
    - argv 是参数**列表**，每段一个元素；不要拼成一整条字符串，命令里不能有管道 / 重定向 / `;`
    - 所有路径参数只能落在沙箱目录内（`data/sandbox/{用户名}/`），绝对路径与 `..` 会被拒绝
    - `python`、`rm`、`mv`、`chmod`、`wget` 等**不在白名单**，调用会被拒绝
    - `curl` 只能 GET，不能下载到文件、不能自定义请求方法或 header
    - 单条命令有超时上限（ffmpeg 300s / curl 30s / git 15s / 文件命令 5s）

    Args:
        command: 白名单命令名，如 "ls"、"curl"、"ffmpeg"、"lark-cli"
        argv: 参数列表，每个元素一段参数，如 ["-s","https://example.com"]、["auth","status"]

    Returns:
        文本结果：成功给 stdout，失败给「⛔ 原因」或 stderr。输出超 1MB 会截断。
    """
    username, account_id = _owner()
    arg_list = [str(a) for a in (argv or [])]
    monitor.report_tool("run_shell_command",
                        f"{command} {' '.join(arg_list)}"[:120])
    res = shell_runtime.execute(command, arg_list, None, username, account_id)
    if res.get("ok"):
        out = res.get("stdout") or ""
        return out if out.strip() else "（命令执行成功，无输出）"
    detail = res.get("error") or res.get("stderr") or "未知错误"
    return f"命令执行失败：{detail}"
