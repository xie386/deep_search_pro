"""AI 会话持久化存储（M1 上下文工程化）——SQLite 读写会话与消息。

数据模型：
  conversations(account_id, thread_id, title, token_usage, message_count, ...)
  messages(conversation_id, turn_index, role, content, name, tool_call_id,
           tool_calls, additional_kwargs, ...)

职责：
  - 会话 CRUD：新建（UUID thread_id）、列表、读消息、改名、删除（级联）
  - 消息持久化：save_turn 保存一轮（user 提问 + assistant 回复 + 工具消息）
  - 历史读取：get_history 返回 LangChain BaseMessage 列表（给 build_context 用）
  - OpenAI 格式 dict ↔ LangChain BaseMessage 互转（content 存 JSON）

线程安全：每次操作短连接（sqlite3 连接不跨函数复用），写操作用
BEGIN IMMEDIATE 防并发写锁冲突（个人场景单任务串行，冲突概率极低）。
"""
from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timedelta

from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)

from tools.schema_personal import get_personal_conn

# ---------------------------------------------------------------------------
# OpenAI 格式 dict ↔ LangChain BaseMessage 互转
# ---------------------------------------------------------------------------


def message_to_lc(d: dict) -> BaseMessage | None:
    """把 messages 表一行（dict）转成 LangChain 消息。"""
    role = d.get("role")
    content = d.get("content") or ""
    # content 是 JSON 序列化的（可能含 tool_calls 结构），尝试解析
    try:
        content = json.loads(content) if content.startswith(("[", "{")) else content
    except (json.JSONDecodeError, AttributeError):
        pass
    if role == "user":
        return HumanMessage(content=content)
    if role == "assistant":
        tc = json.loads(d["tool_calls"]) if d.get("tool_calls") else []
        ak = {}
        if d.get("additional_kwargs"):
            try:
                ak = json.loads(d["additional_kwargs"])
            except json.JSONDecodeError:
                ak = {}
        return AIMessage(content=content, tool_calls=tc, additional_kwargs=ak)
    if role == "tool":
        return ToolMessage(content=str(content), tool_call_id=d.get("tool_call_id") or "")
    if role == "system":
        return SystemMessage(content=str(content))
    return None


def message_to_dict(m: BaseMessage) -> dict:
    """LangChain 消息 → messages 表行 dict（content/tool_calls/additional_kwargs 序列化）。"""
    row: dict = {"role": "assistant", "content": "", "name": None, "tool_call_id": None}
    if isinstance(m, HumanMessage):
        """
        注释文档
        isinstance(m, HumanMessage) 检查消息是否为用户输入类型，根据 content 类型判断是否需要 JSON 序列化。
        """
        row["role"] = "user"
        row["content"] = json.dumps(m.content, ensure_ascii=False) if isinstance(m.content, (list, dict)) else m.content
    elif isinstance(m, SystemMessage):
        row["role"] = "system"
        row["content"] = m.content
    elif isinstance(m, ToolMessage):
        row["role"] = "tool"
        row["content"] = str(m.content)
        row["tool_call_id"] = m.tool_call_id
    elif isinstance(m, AIMessage):
        row["role"] = "assistant"
        row["content"] = json.dumps(m.content, ensure_ascii=False) if isinstance(m.content, (list, dict)) else m.content
        tcs = getattr(m, "tool_calls", None) or []
        row["tool_calls"] = json.dumps([dict(tc) for tc in tcs], ensure_ascii=False) if tcs else None
        ak = m.additional_kwargs or {}
        row["additional_kwargs"] = json.dumps(ak, ensure_ascii=False) if ak else None
    else:
        row["role"] = "assistant"
        row["content"] = str(getattr(m, "content", ""))
    return row


# ---------------------------------------------------------------------------
# 会话 CRUD
# ---------------------------------------------------------------------------


def create_session(account_id: int, title: str = "新对话") -> dict:
    """新建空会话（UUID thread_id）。返回会话信息。"""
    thread_id = uuid.uuid4().hex
    conn = get_personal_conn()
    try:
        cur = conn.execute(
            "INSERT INTO conversations (account_id, thread_id, title) VALUES (?, ?, ?)",
            (account_id, thread_id, title or "新对话"),
        )
        conn.commit()
        return {"id": cur.lastrowid, "thread_id": thread_id, "title": title or "新对话"}
    finally:
        conn.close()


def list_sessions(account_id: int) -> list[dict]:
    """该账号全部会话（按 updated_at DESC，含消息数/活跃时间）。"""
    conn = get_personal_conn()
    try:
        rows = conn.execute(
            """SELECT id, thread_id, title, token_usage, message_count, created_at, updated_at
               FROM conversations WHERE account_id=? ORDER BY updated_at DESC""",
            (account_id,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_session(account_id: int, thread_id: str) -> dict | None:
    """取单个会话（校验归属）。"""
    conn = get_personal_conn()
    try:
        row = conn.execute(
            "SELECT id, thread_id, title, token_usage, message_count, created_at, updated_at "
            "FROM conversations WHERE account_id=? AND thread_id=?",
            (account_id, thread_id),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def rename_session(account_id: int, thread_id: str, title: str) -> bool:
    conn = get_personal_conn()
    try:
        cur = conn.execute(
            "UPDATE conversations SET title=?, updated_at=CURRENT_TIMESTAMP "
            "WHERE account_id=? AND thread_id=?",
            (title, account_id, thread_id),
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def delete_session(account_id: int, thread_id: str) -> bool:
    """硬删会话（messages 级联删除）。"""
    conn = get_personal_conn()
    try:
        cur = conn.execute(
            "DELETE FROM conversations WHERE account_id=? AND thread_id=?",
            (account_id, thread_id),
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 消息持久化 / 读取
# ---------------------------------------------------------------------------


def _next_turn_index(conn: sqlite3.Connection, conversation_id: int) -> int:
    row = conn.execute(
        "SELECT COALESCE(MAX(turn_index), -1) + 1 FROM messages WHERE conversation_id=?",
        (conversation_id,),
    ).fetchone()
    return row[0]


# M3：save_turn 把最后一条 assistant 消息 id 存到本模块 dict，供 api_chat 透传给前端
# （用于 kb 重复入库检测 —— 把消息 id 传给前端，导出报告入库时带回）
_LAST_MSG_ID: dict[str, int] = {}


def get_last_msg_id(thread_id: str) -> int | None:
    return _LAST_MSG_ID.get(thread_id)


def save_turn(
    account_id: int,
    thread_id: str,
    user_msg: str,
    assistant_messages: list[BaseMessage],
    token_usage: int = 0,
) -> bool:
    """保存一轮对话：user 提问（+自动标题）+ 该轮产生的 assistant/tool 消息。

    - 会话不存在则自动创建（兼容旧行为：无 session 时 thread_id 透传）
    - 会话标题为空时用首问前 20 字
    """
    conn = get_personal_conn()
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT id, title FROM conversations WHERE account_id=? AND thread_id=?",
            (account_id, thread_id),
        ).fetchone()
        if row is None:
            # 自动建会话（如旧版 thread_id 直传 / 首次发问）
            title = (user_msg or "新对话")[:20]
            cur = conn.execute(
                "INSERT INTO conversations (account_id, thread_id, title) VALUES (?, ?, ?)",
                (account_id, thread_id, title),
            )
            conversation_id = cur.lastrowid
        else:
            conversation_id = row["id"]
            if (not row["title"]) or row["title"] == "新对话":
                conn.execute(
                    "UPDATE conversations SET title=? WHERE id=?",
                    ((user_msg or "新对话")[:20], conversation_id),
                )

        # 写入 user 消息 + assistant 消息（同一轮 turn_index 递增）
        turn_base = _next_turn_index(conn, conversation_id)
        msgs = [HumanMessage(content=user_msg), *assistant_messages]
        last_assistant_msg_id = None
        for offset, m in enumerate(msgs):
            d = message_to_dict(m)
            cur = conn.execute(
                """INSERT INTO messages
                   (conversation_id, turn_index, role, content, name, tool_call_id, tool_calls, additional_kwargs)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (conversation_id, turn_base + offset, d["role"], d["content"],
                 d.get("name"), d.get("tool_call_id"), d.get("tool_calls"), d.get("additional_kwargs")),
            )
            if d["role"] == "assistant":
                last_assistant_msg_id = cur.lastrowid
        # 更新会话统计
        conn.execute(
            "UPDATE conversations SET token_usage=token_usage+?, message_count=message_count+?, "
            "updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (token_usage, len(msgs), conversation_id),
        )
        conn.commit()
        if last_assistant_msg_id is not None:
            _LAST_MSG_ID[thread_id] = last_assistant_msg_id
        return {"ok": True, "last_assistant_msg_id": last_assistant_msg_id}
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def delete_turn(account_id: int, thread_id: str, msg_id: int) -> dict:
    """删除 msg_id 所在的**一整轮问答**（问题 + 回答 + 该轮工具链）。

    前端「删除」按钮只传 AI 回复气泡的 msg_id，成对删除的配对规则由这里兜：
      - 轮次起点 = turn_index ≤ 目标 的最后一条 role='user'（即该轮用户的提问）
      - 轮次终点 = 下一条 role='user' 之前（该轮的全部 assistant/tool 消息）
    这样一次点击删掉的是「问题 + 回答」，而不会在前端留下孤立的提问。

    副作用：同步 conversations 的 message_count / updated_at，并失效
    `_LAST_MSG_ID` 缓存（M3 重复入库检测用，删掉后必须重算，否则前端会拿着
    已删除的 msg_id 去判重）。

    返回 {"ok": True, "deleted": n, "removed_ids": [...], "question": "前40字"}；
    消息不存在 / 不属于该账号该会话 → {"ok": False, "reason": ...}。
    """
    conn = get_personal_conn()
    try:
        conn.execute("BEGIN IMMEDIATE")
        conv = conn.execute(
            "SELECT id FROM conversations WHERE account_id=? AND thread_id=?",
            (account_id, thread_id),
        ).fetchone()
        if conv is None:
            return {"ok": False, "reason": "session_not_found"}
        cid = conv["id"]

        target = conn.execute(
            "SELECT id, turn_index, role FROM messages WHERE conversation_id=? AND id=?",
            (cid, msg_id),
        ).fetchone()
        if target is None:
            return {"ok": False, "reason": "message_not_found"}

        # 轮次起点：该轮的用户提问
        q = conn.execute(
            "SELECT id, turn_index, content FROM messages "
            "WHERE conversation_id=? AND role='user' AND turn_index<=? "
            "ORDER BY turn_index DESC LIMIT 1",
            (cid, target["turn_index"]),
        ).fetchone()
        start = q["turn_index"] if q else target["turn_index"]

        # 轮次终点：下一轮提问之前
        nxt = conn.execute(
            "SELECT MIN(turn_index) AS n FROM messages "
            "WHERE conversation_id=? AND role='user' AND turn_index>?",
            (cid, start),
        ).fetchone()
        end = (nxt["n"] - 1) if (nxt and nxt["n"] is not None) else (
            conn.execute(
                "SELECT MAX(turn_index) AS m FROM messages WHERE conversation_id=?",
                (cid,),
            ).fetchone()["m"] or start
        )

        ids = [r["id"] for r in conn.execute(
            "SELECT id FROM messages WHERE conversation_id=? AND turn_index BETWEEN ? AND ?",
            (cid, start, end),
        ).fetchall()]
        conn.execute(
            "DELETE FROM messages WHERE conversation_id=? AND turn_index BETWEEN ? AND ?",
            (cid, start, end),
        )
        conn.execute(
            "UPDATE conversations SET message_count=MAX(message_count-?, 0), "
            "updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (len(ids), cid),
        )
        # 失效并重算 M3 的「最后一条 assistant 消息 id」缓存
        if thread_id in _LAST_MSG_ID and _LAST_MSG_ID[thread_id] in ids:
            row = conn.execute(
                "SELECT id FROM messages WHERE conversation_id=? AND role='assistant' "
                "ORDER BY turn_index DESC LIMIT 1",
                (cid,),
            ).fetchone()
            if row:
                _LAST_MSG_ID[thread_id] = row["id"]
            else:
                _LAST_MSG_ID.pop(thread_id, None)
        conn.commit()
        return {
            "ok": True,
            "deleted": len(ids),
            "removed_ids": ids,
            "question": (q["content"][:40] if q else ""),
            "remaining": conn.execute(
                "SELECT COUNT(*) AS c FROM messages WHERE conversation_id=?", (cid,)
            ).fetchone()["c"],
        }
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def get_history(account_id: int, thread_id: str, max_messages: int = 200) -> list[BaseMessage]:
    """读取会话历史为 LangChain 消息列表（按 turn_index ASC，最多 max_messages 条）。"""
    conn = get_personal_conn()
    try:
        conv = conn.execute(
            "SELECT id FROM conversations WHERE account_id=? AND thread_id=?",
            (account_id, thread_id),
        ).fetchone()
        if conv is None:
            return []
        rows = conn.execute(
            """SELECT role, content, name, tool_call_id, tool_calls, additional_kwargs
               FROM messages WHERE conversation_id=? ORDER BY turn_index ASC LIMIT ?""",
            (conv["id"], max_messages),
        ).fetchall()
        out = []
        for r in rows:
            lc = message_to_lc(dict(r))
            if lc is not None:
                out.append(lc)
        return out
    finally:
        conn.close()


def get_messages_plain(account_id: int, thread_id: str) -> list[dict]:
    """前端读消息用：返回纯 dict 列表（不经 LangChain）。"""
    conn = get_personal_conn()
    try:
        conv = conn.execute(
            "SELECT id FROM conversations WHERE account_id=? AND thread_id=?",
            (account_id, thread_id),
        ).fetchone()
        if conv is None:
            return []
        rows = conn.execute(
            """SELECT id, turn_index, role, content, name, tool_call_id, tool_calls, created_at
               FROM messages WHERE conversation_id=? ORDER BY turn_index ASC""",
            (conv["id"],),
        ).fetchall()
        result = []
        for r in rows:
            d = dict(r)
            # content 若为 JSON 数组/对象则解析（前端展示工具结果结构）
            if isinstance(d["content"], str) and d["content"].startswith(("[", "{")):
                try:
                    d["content"] = json.loads(d["content"])
                except json.JSONDecodeError:
                    pass
            result.append(d)
        return result
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 清理（v2.0 预留）
# ---------------------------------------------------------------------------


def cleanup_old_sessions(days: int = 30, dry_run: bool = True) -> int:
    """清理 N 天前未活跃的会话（默认 dry_run 只统计）。"""
    conn = get_personal_conn()
    try:
        cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
        rows = conn.execute(
            "SELECT id, thread_id, title FROM conversations WHERE updated_at < ?",
            (cutoff,),
        ).fetchall()
        if not dry_run:
            for r in rows:
                conn.execute("DELETE FROM conversations WHERE id=?", (r["id"],))
            conn.commit()
        return len(rows)
    finally:
        conn.close()
