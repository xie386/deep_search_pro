"""知识库工具（M3）——挂载给主 Agent/定制 Agent 的工具封装。

工具设计（M3 文档 §4.3，批注 3 方案）：
  - query_kb：AI 助手对话时由主 Agent 自主调用（fast 快模式，GPU ~30ms embed，
    无 rerank，整链路 <0.5s，不拖慢对话）——**不做子 Agent**（AstrBot B-4 借鉴）
  - upload_to_kb：文本入库（md5 去重）——设计上 Agent 一般不直接调
    （入库走前端确认流程：evaluator 评估 → 用户同意）；保留给未来 Agent 主动沉淀场景

账号归属：get_owner_context() → 同时拿 account_id + username，传给 rag_knowledge
（collection 名用 account_id 命名，中文 username 也能用）。
"""
from __future__ import annotations

from langchain_core.tools import tool

from api.context import get_owner_context
from api.monitor import monitor

_ctx_cache: dict[int, tuple[str, int]] = {}


def _current_owner() -> tuple[str | None, int | None]:
    """当前 owner context：返回 (username, account_id)。缓存避免每调查库。"""
    oid = get_owner_context()
    if oid is None:
        return None, None
    if oid in _ctx_cache:
        return _ctx_cache[oid]
    try:
        from tools.schema_personal import get_personal_conn
        conn = get_personal_conn()
        try:
            row = conn.execute("SELECT username FROM accounts WHERE id=?", (oid,)).fetchone()
        finally:
            conn.close()
        name = row["username"] if row else None
        cache = (name, int(oid)) if name else (None, None)
        if cache[0]:
            _ctx_cache[oid] = cache
        return cache
    except Exception:
        return None, None


@tool
def query_kb(question: str) -> str:
    """检索当前用户个人知识库（本机向量库，含用户导出的报告与上传的资料）。

    当你判断用户的提问可能与自己知识库中的私有资料相关时调用（如"我之前收藏的/导出的/
    上传的资料里…"）。返回带 📚 来源标注的知识库片段。检索不到会明确说明，不会编造。

    注意：
    - 网络可查的公开信息**不需要**调本工具（走网络搜索助手）——知识库存的是用户私有沉淀
    - 本工具是快速模式（<0.5s），不会明显拖慢回答
    """
    monitor.report_tool(tool_name="知识库检索 query_kb", args={"question": question[:50]})
    username, aid = _current_owner()
    if not username:
        return "知识库检索失败：无法确定当前用户（请登录后使用）。"
    from rag_knowledge.kb_service import kb_exists, query_kb_friendly
    if not kb_exists(username, aid):
        return "你的知识库还没有创建：可在「知识库」页面创建并上传资料（或导出报告时选择入库）。"
    hits = query_kb_friendly(username, question, mode="fast", account_id=aid)
    if not hits:
        return "知识库中未检索到与该问题相关的内容（如需引用请确认资料已入库）。"
    return hits


@tool
def upload_to_kb(title: str, content: str) -> str:
    """把一段文本加入当前用户个人知识库（自动去重：同内容只存一份）。

    注意：通常入库应走前端「知识库」页（有质量评估与确认）；本工具用于明确的沉淀场景。
    """
    monitor.report_tool(tool_name="知识库入库 upload_to_kb", args={"title": title[:30]})
    username, aid = _current_owner()
    if not username:
        return "知识库入库失败：无法确定当前用户（请登录后使用）。"
    from rag_knowledge.kb_service import ingest_text
    r = ingest_text(username, title or "未命名", content, source_kind="agent", account_id=aid)
    if not r.get("ok"):
        return f"入库失败：{r.get('error', '未知错误')}"
    if r.get("dup"):
        return f"该内容已在知识库中（《{r['title']}》），未重复添加。"
    return f"已入库《{r['title']}》（{r['chunks']} 个片段），之后可用自然语言检索到它。"
    
