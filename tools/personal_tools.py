# ============================================================
# 智选情报官 · 个人侧 DB 工具（M1.5）
# 对应公司侧 tools/db_tools.py，但操作本地 SQLite 个人库。
# 工具命名与公司侧保持平行：list_personal_tables / get_personal_data / query_personal_db
# ============================================================

import os
from dotenv import load_dotenv
from api.monitor import monitor
from langchain_core.tools import tool

# 复用 personal_db 的连接与初始化
from tools.schema_personal import ensure_tables, get_personal_conn, DB_PATH
from api.context import get_owner_context

load_dotenv()


def _ensure_db():
    """确保库文件与表存在（幂等建表，绝不破坏数据）。"""
    ensure_tables()

def _owner_filter() -> str:
    """返回当前账号的 owner_id 过滤片段与参数。

    数据归属权（M4 前置）：Agent 工具只读「当前登录用户」的数据。
    若 owner 未设置（如脚本直调），返回空过滤——仅限本地调试场景。
    """
    oid = get_owner_context()
    return (" WHERE owner_id = ?", [oid]) if oid is not None else ("", [])


def _row_to_csv(rows, columns):
    """把查询结果转 CSV 字符串（列头 + 数据），与公司侧工具输出风格一致。"""
    header = ",".join(columns)
    lines = [header]
    for r in rows:
        lines.append(",".join("" if v is None else str(v) for v in r))
    return "\n".join(lines)


@tool
def list_personal_tables() -> str:
    """列出个人库中所有可用的表（accounts/products/interests/watchlist）。用于先了解库结构。"""
    _ensure_db()
    monitor.report_tool(tool_name="个人库表名查询", args={})
    try:
        conn = get_personal_conn()
        cur = conn.cursor()
        cur.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
        tables = [row[0] for row in cur.fetchall()]
        conn.close()
        return f"可用的表有：{', '.join(tables)}" if tables else "没有可用的表"
    except Exception as e:
        return f"查询出现异常：{str(e)}"


@tool
def get_personal_data(table_name: str) -> str:
    """读取个人库指定表的数据（前100行）。先调用 list_personal_tables 确认表名。
    返回 CSV 格式：首行为列头，之后每行一条数据，列间逗号分隔，行间换行。"""
    _ensure_db()
    monitor.report_tool(tool_name="个人库表数据查询", args={"table_name": table_name})
    try:
        conn = get_personal_conn()
        cur = conn.cursor()
        where, params = _owner_filter()
        cur.execute(f"SELECT * FROM {table_name}{where} LIMIT 100", params)
        rows = cur.fetchall()
        cols = [d[0] for d in cur.description] if cur.description else []
        conn.close()
        if not rows:
            return f"数据表：{table_name} 为空没有数据！"
        return _row_to_csv(rows, cols)
    except Exception as e:
        return f"查询出现异常：{str(e)}"


@tool
def query_personal_db(query: str) -> str:
    """执行自定义 SQL 查询个人库（只读 SELECT）。注意：attributes 等为 JSON 文本列，
    如需读取内部字段可用 json_extract(attributes, '$.battery')。表名与结构请先通过 list_personal_tables 确认。"""
    _ensure_db()
    monitor.report_tool(tool_name="个人库自定义查询", args={"query": query})
    try:
        oid = get_owner_context()
        if oid is None:
            return "未登录上下文，拒绝执行个人库自定义查询（数据归属权保护）。"
        # 拦截指向其他账号 owner_id 的查询条件（归属权保护）
        import re as _re
        bad = [m for m in _re.findall(r"owner_id\s*=\s*(\d+)", query) if int(m) != int(oid)]
        if bad:
            return f"查询中包含指向其他账号(owner_id={bad})的条件，已拒绝。"
        conn = get_personal_conn()
        cur = conn.cursor()
        cur.execute(query)
        rows = cur.fetchall()
        cols = [d[0] for d in cur.description] if cur.description else []
        conn.close()
        if not rows:
            return f"查询没有结果，sql为：{query}！"
        return _row_to_csv(rows, cols)
    except Exception as e:
        return f"查询出现异常：{str(e)}"
