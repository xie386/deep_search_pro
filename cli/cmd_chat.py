# -*- coding: utf-8 -*-
"""dspro chat —— 命令行对话（与 web 版共用同一套装配管线与会话存储）。

    dspro chat                       # 进入对话（默认续用上次的 CLI 会话）
    dspro chat "帮我看下 1000 元档降噪耳机"   # 单轮问答后退出（适合脚本）
    dspro chat --new                 # 开一个新会话
    dspro chat --session <thread_id> # 续指定会话（用 :sessions 查 id）
    dspro chat --skills              # 列出可用技能后退出
    dspro chat --skill "竞品对比表"    # 本轮注入技能说明书（可重复）

要点（与 web 完全一致，因此人格/记忆/知识库/技能/自配 CLI 都生效）：
  - 复用 api/server.py 的 _run_agent：同一装配管线（SOUL + MEMORY + 技能 + CLI 简报）、
    同一份 SQLite 会话历史与 token 预算、同一套工具与子智能体；
  - 会话落在 conversations/messages 表（按账号隔离），web 端也能看到 CLI 的对话；
  - 进度行（工具调用/子智能体/思考）由 monitor 的 console sink 渲染到终端。
"""

import time

from cli import ui, __version__
from cli import session as ss
from cli.errors import CliError

_EXIT_WORDS = {":q", ":quit", "exit", "quit", ":exit"}


def _skills_dir(username: str):
    return ss.PROJECT_ROOT / "agents_docs" / username / "skills"


def _list_skills(username: str) -> list[str]:
    d = _skills_dir(username)
    return sorted(p.stem for p in d.glob("*.md")) if d.is_dir() else []


def _resolve_thread(username: str, account_id: int, new: bool, session_id: str | None) -> tuple[str, str]:
    """返回 (thread_id, 标题)。"""
    from agent import conversation_store as cs

    if session_id:
        s = cs.get_session(account_id, session_id)
        if not s:
            raise CliError(f"会话 {session_id} 不存在或不属于当前账号。用 dspro chat --sessions 查看。")
        return session_id, s.get("title") or session_id

    if not new:
        last = ss.get_value("last_thread_id") or ""
        if last:
            s = cs.get_session(account_id, last)
            if s:
                return last, s.get("title") or last

    title = "CLI 会话" if not new else time.strftime("CLI 会话 %m-%d %H:%M")
    s = cs.create_session(account_id, title=title)
    tid = s.get("thread_id") or s.get("id")
    return tid, title


def _print_sessions(account_id: int) -> None:
    from agent import conversation_store as cs
    rows = cs.list_sessions(account_id) or []
    ui.section("我的会话（web + CLI 共用）", "🗂")
    if not rows:
        ui.hint("（空）")
        return
    ui.table(["thread_id", "标题", "消息数", "最近更新"],
             [[r.get("thread_id") or r.get("id"), ui.trunc(r.get("title") or "-", 30),
               r.get("message_count", 0), (r.get("updated_at") or r.get("created_at") or "-")[:19]]
              for r in rows],
             max_width=[36, 30, 8, 20])
    ui.hint("续聊：dspro chat --session <thread_id>")


def _one_turn(question: str, thread_id: str, account_id: int, skill_names: list[str]) -> tuple[str, float]:
    """跑一轮：复用 api/server.py 的 _run_agent（同一装配管线）。"""
    from api.server import _run_agent          # 懒加载：list/digest 不需要这套重依赖
    t0 = time.time()
    answer = _run_agent(question, thread_id, account_id, skill_names or None)
    return answer, round(time.time() - t0, 1)


def run(question: str | None = None, new: bool = False, session_id: str | None = None,
        sessions_only: bool = False, skills_only: bool = False,
        skill_names: list[str] | None = None, quiet: bool = False) -> int:
    data = ss.require()
    account_id, username = data["account_id"], data["username"]

    if sessions_only:
        _print_sessions(account_id)
        return 0

    skills = _list_skills(username)
    if skills_only:
        ui.section(f"可用技能（{len(skills)} 个）", "🧰")
        if skills:
            for s in skills:
                print(f"  · {ui.c(s, 'bold')}")
            ui.hint("用法：dspro chat --skill \"技能名\"（可重复；仅本轮生效）")
        else:
            ui.hint("（空）到 web 端「技能与工具」页创建，或直接放到 agents_docs/%s/skills/" % username)
        return 0

    picked = list(skill_names or [])
    if picked:
        missing = [s for s in picked if s not in skills]
        if missing:
            raise CliError("技能不存在：%s。可用：%s（或 dspro chat --skills 查看）"
                           % ("、".join(missing), "、".join(skills) or "（无）"))

    thread_id, title = _resolve_thread(username, account_id, new, session_id)
    ss.set_value("last_thread_id", thread_id)

    from api.monitor import monitor
    if not quiet:
        try:
            monitor.set_console_sink(ui.progress)
        except Exception:
            pass

    try:
        if question:                        # 单轮模式
            ui.blank()
            ui.banner(__version__, f"{username} · 会话「{title}」")
            if picked:
                ui.info("本轮启用技能：" + "、".join(picked))
            print(ui.c("你 › ", "green", "bold") + question)
            ui.blank()
            try:
                answer, sec = _one_turn(question, thread_id, account_id, picked)
            except KeyboardInterrupt:
                ui.warn("已中断本轮（历史仍保留）")
                return 130
            print(ui.md_light(answer or "（无输出）"))
            ui.hint(f"用时 {sec}s · 会话 {thread_id}")
            return 0

        # 交互模式
        ui.blank()
        ui.banner(__version__, f"{username} · 会话「{title}」")
        ui.hint("直接输入问题回车；:new 新会话  :sessions 会话列表  :skills 技能列表  :q 退出")
        if picked:
            ui.info("本轮启用技能：" + "、".join(picked))
        while True:
            try:
                line = input(ui.c("\n你 › ", "green", "bold")).strip()
            except (EOFError, KeyboardInterrupt):
                ui.hint("\n再见 👋")
                return 0
            if not line:
                continue
            if line in _EXIT_WORDS:
                ui.hint("再见 👋")
                return 0
            if line == ":new":
                thread_id, title = _resolve_thread(username, account_id, True, None)
                ss.set_value("last_thread_id", thread_id)
                ui.ok(f"已新建会话「{title}」（{thread_id}）")
                continue
            if line == ":sessions":
                _print_sessions(account_id)
                continue
            if line == ":skills":
                print("  可用技能：" + ("、".join(skills) if skills else "（无）"))
                continue
            if line == ":help":
                ui.hint(":new 新会话  :sessions 会话列表  :skills 技能列表  :q 退出")
                continue

            try:
                answer, sec = _one_turn(line, thread_id, account_id, picked)
            except KeyboardInterrupt:
                ui.warn("\n已中断本轮（历史仍保留）")
                continue
            except Exception as e:                       # noqa: BLE001 - 单轮失败不退出对话
                ui.err(f"本轮失败：{type(e).__name__}: {e}")
                continue
            ui.blank()
            print(ui.md_light(answer or "（无输出）"))
            ui.hint(f"用时 {sec}s · 会话 {thread_id}")
    finally:
        if not quiet:
            try:
                monitor.set_console_sink(None)
            except Exception:
                pass
