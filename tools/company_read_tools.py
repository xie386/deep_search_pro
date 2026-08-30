# ============================================================
# 智选情报官 · 公司侧只读工具（前置工作：数据归属权改造）
# 数据源切换：MySQL 全局表 → 本地 SQLite 按账号隔离（owner_id）。
# 定位：Agent 只读消费；增删改走 REST /api/me/*（人主动维护）。
# 工具名保持与旧 db_tools 平行，db_agent 无缝替换。
# ============================================================

from langchain_core.tools import tool

from api.context import get_owner_context
from api.monitor import monitor
from tools.schema_personal import ensure_tables, get_personal_conn


def _owner_filter() -> tuple[str, list]:
    oid = get_owner_context()
    return (" WHERE owner_id = ?", [oid]) if oid is not None else ("", [])


def _row_to_csv(rows, columns) -> str:
    header = ",".join(columns)
    lines = [header]
    for r in rows:
        lines.append(",".join("" if v is None else str(v) for v in r))
    return "\n".join(lines)


@tool
def list_company_tables() -> str:
    """列出公司业务库中当前账号可用的表（company_profile/company_products/company_competitors）。
    用于先了解库结构。注意：只能看到当前登录公司账号自己的数据。"""
    ensure_tables()
    monitor.report_tool(tool_name="公司库表名查询", args={})
    return "可用的表有：company_profile, company_products, company_competitors"


@tool
def get_company_data(table_name: str) -> str:
    """读取公司库指定表的数据（前100行，仅限当前登录公司的数据）。
    可用表：company_profile（我司信息）、company_products（我司产品）、
    company_competitors（关注竞品）。返回 CSV 格式。"""
    _ALLOWED = {"company_profile", "company_products", "company_competitors"}
    if table_name not in _ALLOWED:
        return f"表名必须是 {_ALLOWED} 之一"
    ensure_tables()
    monitor.report_tool(tool_name="公司库表数据查询", args={"table_name": table_name})
    try:
        conn = get_personal_conn()
        cur = conn.cursor()
        where, params = _owner_filter()
        cur.execute(f"SELECT * FROM {table_name}{where} LIMIT 100", params)
        rows = cur.fetchall()
        cols = [d[0] for d in cur.description] if cur.description else []
        conn.close()
        if not rows:
            return f"数据表：{table_name} 为空没有数据！（可能尚未填写公司信息，请先在左栏维护）"
        return _row_to_csv(rows, cols)
    except Exception as e:
        return f"查询出现异常：{str(e)}"


@tool
def query_company_db(query: str) -> str:
    """执行自定义 SELECT 查询公司库（仅限当前登录公司的数据）。
    attributes / mapped_product_ids 为 JSON 文本列，可用 json_extract(attributes,'$.xxx')。
    表结构请先通过 list_company_tables 确认。"""
    ensure_tables()
    monitor.report_tool(tool_name="公司库自定义查询", args={"query": query})
    try:
        import re as _re
        oid = get_owner_context()
        if oid is None:
            return "未登录上下文，拒绝执行公司库自定义查询（数据归属权保护）。"
        # 只允许 SELECT；拦截指向其他账号 owner_id 的条件
        if not _re.match(r"\s*select\b", query, _re.I):
            return "仅允许 SELECT 查询。"
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
