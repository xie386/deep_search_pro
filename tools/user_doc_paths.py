# -*- coding: utf-8 -*-
"""M4-8 · 用户文档路径的**唯一事实源**（`agents_docs/{username}/…`）。

为什么单独一个模块（方案 §5.6 / C6）：MEMORY.md 的位置原本散着算 —— `api/customize._user_memory_path`、
`api/server._get_memory_text`、周报侧各自拼一次。**同一个文件被三处各拼一遍**，只要有一处写法不一致
（正是 M3 之前踩过的 `agents_docs/agents_docs/{user}/` 那个坑），就会出现"写进 A、读的是 B"。
这里把它收成一份：谁要路径都来问它，谁要正文都用 `read_memory_for_agent`。
"""
import os

# 项目根 = 本文件的上一级（tools/ → 项目根）
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
AGENTS_DOCS = os.path.join(PROJECT_ROOT, "agents_docs")

MEMORY_NAME = "MEMORY.md"
SOUL_NAME = "SOUL.md"


def user_dir(username: str) -> str:
    """`agents_docs/{username}`（**唯一**的拼接点）。"""
    return os.path.join(AGENTS_DOCS, str(username or "").strip())


def memory_path(username: str) -> str:
    return os.path.join(user_dir(username), MEMORY_NAME)


def soul_path(username: str) -> str:
    return os.path.join(user_dir(username), SOUL_NAME)


def skills_dir(username: str) -> str:
    return os.path.join(user_dir(username), "skills")


def read_text(path: str, default: str = "") -> str:
    """读文本（失败/不存在返回 default；**不抛** —— 读不到画像绝不该打断对话）。"""
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    except Exception:
        return default


def read_memory_for_agent(username: str) -> str:
    """**注入 Agent 用的**记忆正文（strip 后返回；读不到返回空串）。

    聊天注入与周报读取都走这里 —— 这就是"双方取到同一文件"的落点。
    """
    if not username:
        return ""
    return (read_text(memory_path(username)) or "").strip()


def ensure_user_dir(username: str) -> str:
    """确保 `agents_docs/{username}` 存在并返回它。

    ★ 为什么单独一个函数：路径函数保持**纯**（不偷偷建目录），需要落盘时显式调用它。
      2026-09-27 的教训：M4-8 把 `_user_memory_path` 收成纯路径后，`ensure_user_memory` 写文件
      直接 FileNotFoundError —— 原来那句"顺手 mkdir"藏在旧的 `_user_doc_dir` 里，收口时被一起收掉了。
    """
    d = user_dir(username)
    try:
        os.makedirs(d, exist_ok=True)
    except Exception:
        pass
    return d
