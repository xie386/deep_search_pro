"""M5b · 统一能力目录（能力池）—— 路由用的「工具目录」单一事实源。

设计口径（见 `docs/v2.0/M5b工具检索路由.md` §二）：

  ① **统一契约**：路由与注入只认 `CapabilityEntry`（source/ref/name/keywords/abilities/invoke_hint），
     不感知来源是 CLI、API 还是 MCP——以后接第四种来源只需再写一个适配器，路由器一行不改。
  ② **派生而非迁移**：CLI 的能力描述已经存在 `user_clis.abilities`，**不搬家**；
     写入 `user_clis` 的同时 upsert 一条 `tool_capabilities(source='cli')`；
     `rebuild_pool()` 可从来源表整体重建（改完适配器逻辑跑一次即对齐）。
  ③ **单一事实源**：文本在 SQLite（`tool_capabilities`），向量在 Chroma 独立 collection
     `tool_route_{account_id}`（与 M3 的 `kb_user_*` 同款账号隔离），**向量只存引用不存文本**。
  ④ **版本失效**：任何写入口 → `bump_pool_version()`；路由器按 `(account_id, version)` 缓存，不用 TTL。

本模块**只做目录**，不做路由/不做注入（那是 `tools/tool_router.py` 的事）。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable

from tools.schema_personal import get_personal_conn

# ---------------------------------------------------------------- 常量
SOURCE_CLI = "cli"
SOURCE_API = "api"
SOURCE_MCP = "mcp"
SOURCES = (SOURCE_CLI, SOURCE_API, SOURCE_MCP)

VECTOR_COLLECTION = "tool_route_{account_id}"   # 路由专用 collection（与知识库隔离）
COMPACT_KEYWORD_LIMIT = 160                      # 紧凑行里关键词截断长度

_KEYWORDS_PREFIX = re.compile(r"^\s*关键词\s*[:：]\s*")


# ---------------------------------------------------------------- 统一契约
@dataclass(frozen=True)
class CapabilityEntry:
    """一条「可用于完成用户请求」的能力（不管它来自 CLI / API / MCP）。"""

    source: str
    ref: str
    name: str
    keywords: str = ""
    abilities: str = ""
    invoke_hint: str = ""
    enabled: bool = True
    # 来源侧的细节（渲染卡片时用；不进契约语义，其它来源可为空）
    rules: tuple[tuple[str, ...], ...] = ()      # CLI：只读命令路径
    invokable: bool = True                       # 是否真的可被 Agent 调用（清单为空等 → False）

    # ---------------- 渲染器：三种形态（路由器按相似度分层使用） ----------------
    @property
    def command_count(self) -> int:
        return len(self.rules)

    @property
    def first_keyword_line(self) -> str:
        """取能力描述里的第一行有效文本 —— 紧凑行的路由信号。

        注意去掉「关键词：」这类前缀（用户写法不统一：有的写 `关键词：A/B/C`，有的直接写 `A、B、C`）。
        """
        for ln in (self.abilities or "").splitlines():
            ln = " ".join(ln.split())
            if ln:
                return _KEYWORDS_PREFIX.sub("", ln).strip()
        return ""

    def _head(self) -> str:
        return f"- {self.name}（可执行名 `{self.ref}`）"

    @property
    def card_text(self) -> str:
        """完整能力卡 —— 相似度高时注入。"""
        sample = [t for t in self.rules[:6]]
        sample_txt = " / ".join(" ".join(t) for t in sample) + (" / …" if len(self.rules) > 6 else "")
        if not (self.abilities or "").strip():
            return (f"{self._head()}：可代跑 {self.command_count} 条只读命令"
                    + (f"，例如 {sample_txt}" if sample_txt else "（清单为空，暂不可代跑）"))
        flat = " ".join(self.abilities.split())
        return (f"{self._head()}：用户问到下列任一场景时，**先用它取真实数据**"
                f"（这是用户自己账号里的私有数据，网搜/知识库都拿不到）；"
                f"不要改用别的工具顶替，也不要凭你自己的知识作答——\n"
                f"  {flat}\n"
                f"  共 {self.command_count} 条只读命令，例如 {sample_txt}")

    @property
    def compact_text(self) -> str:
        """紧凑行 —— 相似度中等时注入（保住关键词，细节让位）。"""
        kw = self.first_keyword_line
        tail = ("；关键词：" + kw[:COMPACT_KEYWORD_LIMIT]) if kw else ""
        return f"{self._head()}：共 {self.command_count} 条只读命令{tail}"

    @property
    def nameonly_text(self) -> str:
        """仅名称行 —— 相似度低但**仍要可见**（工具从提示词里隐身 = 用户配了也白配）。"""
        return f"{self._head()}：共 {self.command_count} 条只读命令"


# ---------------------------------------------------------------- 适配器：CLI
def cli_entry(c: dict) -> CapabilityEntry:
    """把 `cli_registry.list_clis()` 的一条记录适配成能力条目（纯函数，便于单测）。"""
    rules = tuple(tuple(t) for t in (c.get("rules") or []))
    abilities = (c.get("abilities") or "").strip()
    keywords = ""
    m = _KEYWORDS_PREFIX.match(abilities)
    if m:
        # 关键词行取到句末（。/；/换行 之前）
        rest = abilities[m.end():]
        keywords = re.split(r"[。；;\n|]", rest, maxsplit=1)[0].strip()
    return CapabilityEntry(
        source=SOURCE_CLI,
        ref=str(c.get("bin") or ""),
        name=str(c.get("name") or c.get("bin") or ""),
        keywords=keywords,
        abilities=abilities,
        invoke_hint=f"{c.get('bin')} <子命令> [参数]",
        enabled=bool(c.get("live")),          # 未接入 / 清单为空 → 不进池（fail closed 口径）
        rules=rules,
        invokable=bool(rules),
    )


def _entries_from_clis(account_id: int) -> list[CapabilityEntry]:
    from tools import cli_registry as cli_reg
    return [cli_entry(c) for c in cli_reg.list_clis(account_id)]


# ---------------------------------------------------------------- 适配器登记表（接新来源只加一行）
_ADAPTERS = {
    SOURCE_CLI: _entries_from_clis,
    # SOURCE_API: _entries_from_apis,   # M5b-3 实装后接入
    # SOURCE_MCP: _entries_from_mcps,   # M5b-4 实装后接入
}


# ---------------------------------------------------------------- 读：池条目（派生视图，不读 tool_capabilities）
def pool_entries(account_id: int | None, *, enabled_only: bool = True) -> list[CapabilityEntry]:
    """当前账号的能力池条目。

    ⚠️ 直接**从来源适配器派生**（而非读 `tool_capabilities` 表）——保证「来源表是唯一事实源」：
    用户在 CLI 页改了能力描述，池立刻反映，不存在两张表不同步的窗口。
    `tool_capabilities` 表用于：① 向量文本变更检测（content hash）；② 未来的持久化/审计；
    api/mcp 实装后若来源表读取昂贵，可切到表读 + 版本号失效（接口不变）。
    """
    if not account_id:
        return []
    out: list[CapabilityEntry] = []
    for source, loader in _ADAPTERS.items():
        try:
            out.extend(loader(int(account_id)))
        except Exception as e:  # noqa: BLE001 - 单个来源失败不该让整池不可用
            print(f"[M5b] 能力来源 {source} 读取失败（跳过）: {type(e).__name__}: {e}")
    if enabled_only:
        out = [e for e in out if e.enabled]
    # 去重（同 source+ref 只留一条；来源适配器正常不会重复，防御性处理）
    seen: set[tuple[str, str]] = set()
    uniq: list[CapabilityEntry] = []
    for e in out:
        k = (e.source, e.ref)
        if k in seen:
            continue
        seen.add(k)
        uniq.append(e)
    return uniq


# ---------------------------------------------------------------- 版本（缓存失效）
def current_pool_version(account_id: int) -> int:
    try:
        conn = get_personal_conn()
        try:
            row = conn.execute("SELECT pool_version FROM tool_pool_meta WHERE account_id=?",
                               (int(account_id),)).fetchone()
            return int(row["pool_version"]) if row else 0
        finally:
            conn.close()
    except Exception:  # noqa: BLE001 - 老库没这张表等情况
        return 0


def bump_pool_version(account_id: int | None) -> int:
    """池内容可能变化时调用（工具增删改、状态变更、能力描述编辑）。返回新版本号。"""
    if not account_id:
        return 0
    try:
        conn = get_personal_conn()
        try:
            conn.execute(
                "INSERT INTO tool_pool_meta (account_id, pool_version, updated_at) "
                "VALUES (?, 1, datetime('now','localtime')) "
                "ON CONFLICT(account_id) DO UPDATE SET "
                "  pool_version = pool_version + 1, updated_at = datetime('now','localtime')",
                (int(account_id),))
            conn.commit()
            row = conn.execute("SELECT pool_version FROM tool_pool_meta WHERE account_id=?",
                               (int(account_id),)).fetchone()
            return int(row["pool_version"]) if row else 0
        finally:
            conn.close()
    except Exception as e:  # noqa: BLE001
        print(f"[M5b] 池版本递增失败（忽略）: {type(e).__name__}: {e}")
        return 0


# ---------------------------------------------------------------- 向量：独立 collection
def _account_id_of(username: str | None) -> int:
    if not username:
        return 0
    try:
        conn = get_personal_conn()
        try:
            row = conn.execute("SELECT id FROM accounts WHERE username=?", (username,)).fetchone()
            return int(row["id"]) if row else 0
        finally:
            conn.close()
    except Exception:  # noqa: BLE001
        return 0


def _username_of(account_id: int) -> str:
    """按 account_id 反查 username（路径用 username，与 M3 的 `DB_ROOT/{username}/` 约定一致）。"""
    try:
        conn = get_personal_conn()
        try:
            row = conn.execute("SELECT username FROM accounts WHERE id=?", (int(account_id),)).fetchone()
            return str(row["username"]) if row and row["username"] else f"account_{int(account_id)}"
        finally:
            conn.close()
    except Exception:  # noqa: BLE001
        return f"account_{int(account_id)}"


def get_route_collection(account_id: int):
    """路由专用 Chroma collection（`tool_route_{account_id}`，与知识库隔离）。

    - 目录：`DB_ROOT/{username}/chroma_route`（与 M3 同用户目录、独立子目录，互不干扰）；
    - collection 名只用 account_id（chroma 只允许 `[a-zA-Z0-9._-]`，中文 username 会报 InvalidArgumentError）。
    """
    import chromadb  # lazy

    from rag_knowledge.kb_service import DB_ROOT

    d = DB_ROOT / _username_of(int(account_id)) / "chroma_route"
    d.mkdir(parents=True, exist_ok=True)
    client = chromadb.PersistentClient(path=str(d))
    return client.get_or_create_collection(
        name=VECTOR_COLLECTION.format(account_id=int(account_id)),
        metadata={"hnsw:space": "cosine"})


def _entry_text(e: CapabilityEntry) -> str:
    """用于 embed 的文本：关键词 + 能力描述（**不含命令样例**，避免命令名干扰语义匹配）。"""
    parts = [e.name]
    if e.keywords:
        parts.append(e.keywords)
    if e.abilities:
        parts.append(e.abilities)
    return "\n".join(parts).strip()


def _content_hash(text: str) -> str:
    import hashlib
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def sync_vectors(account_id: int, entries: list[CapabilityEntry] | None = None) -> dict:
    """把池条目的向量同步进 collection（只对文本变过的条目重算）。返回统计信息。

    失败不抛异常（向量是**加速层**，缺了它路由器会退回关键词匹配）——返回 `{'ok': False, 'error': …}`。
    """
    if not account_id:
        return {"ok": False, "error": "no account_id"}
    entries = entries if entries is not None else pool_entries(account_id)
    stat = {"ok": True, "added": 0, "updated": 0, "removed": 0, "skipped": 0, "total": len(entries)}
    try:
        col = get_route_collection(account_id)
        existing = {}
        try:
            got = col.get(include=["metadatas"])
            for i, m in zip(got.get("ids") or [], got.get("metadatas") or []):
                existing[i] = (m or {}).get("content_hash", "")
        except Exception:  # noqa: BLE001 - 空 collection
            existing = {}

        from rag_knowledge.kb_service import embedding_model
        model = embedding_model()

        want: dict[str, tuple[CapabilityEntry, str, str]] = {}
        for e in entries:
            txt = _entry_text(e)
            if not txt:
                stat["skipped"] += 1
                continue
            vid = f"{e.source}:{e.ref}"
            want[vid] = (e, txt, _content_hash(txt))

        to_add_ids, to_add_txt, to_add_meta = [], [], []
        for vid, (e, txt, h) in want.items():
            if existing.get(vid) == h:
                stat["skipped"] += 1
                continue
            to_add_ids.append(vid)
            to_add_txt.append(txt)
            to_add_meta.append({"source": e.source, "ref": e.ref, "name": e.name, "content_hash": h})

        if to_add_ids:
            vecs = model.embed_documents(to_add_txt)
            col.upsert(ids=to_add_ids, documents=to_add_txt, embeddings=vecs, metadatas=to_add_meta)
            for vid in to_add_ids:
                if vid in existing:
                    stat["updated"] += 1
                else:
                    stat["added"] += 1

        stale = [i for i in existing if i not in want]
        if stale:
            col.delete(ids=stale)
            stat["removed"] = len(stale)
    except Exception as e:  # noqa: BLE001
        stat["ok"] = False
        stat["error"] = f"{type(e).__name__}: {e}"
    return stat


def _vec_state_path(account_id: int):
    """记录「collection 已同步到哪个池版本」的小状态文件（放在 collection 同目录）。"""
    import chromadb  # noqa: F401 - 仅为保持依赖顺序

    from rag_knowledge.kb_service import DB_ROOT

    d = DB_ROOT / _username_of(int(account_id)) / "chroma_route"
    d.mkdir(parents=True, exist_ok=True)
    return d / "_pool_version.json"


def ensure_vectors_fresh(account_id: int, entries: list[CapabilityEntry] | None = None) -> dict:
    """**惰性**向量同步：collection 的版本落后于池版本时才重建。

    为什么要惰性：向量同步会加载 bge（首次 1~3 分钟）。配置写入路径（保存 CLI）若连带做这件事，
    面板会卡住——所以写入口只落库 + 递增版本，真正的向量重建推迟到路由时刻按版本触发。
    """
    if not account_id:
        return {"ok": False, "error": "no account_id"}
    aid = int(account_id)
    ver = current_pool_version(aid)
    path = _vec_state_path(aid)
    stored = None
    try:
        if path.exists():
            import json as _json
            stored = (_json.loads(path.read_text(encoding="utf-8")) or {}).get("version")
    except Exception:  # noqa: BLE001
        stored = None
    if stored == ver and ver > 0:
        return {"ok": True, "fresh": True, "version": ver}
    stat = sync_vectors(aid, entries)
    if stat.get("ok"):
        try:
            import json as _json
            path.write_text(_json.dumps({"version": ver}), encoding="utf-8")
        except Exception:  # noqa: BLE001
            pass
    stat["version"] = ver
    return stat


def route_scores(account_id: int, question: str) -> dict[str, float]:
    """稠密相似度：{ 'source:ref': similarity }。失败返回 `{}`（调用方退回关键词匹配）。"""
    if not account_id or not (question or "").strip():
        return {}
    try:
        ensure_vectors_fresh(account_id)     # 池变了才重建（惰性）
        col = get_route_collection(account_id)
        if col.count() == 0:
            return {}
        from rag_knowledge.kb_service import embedding_model
        vec = embedding_model().embed_query(question)
        n = min(max(col.count(), 1), 50)
        res = col.query(query_embeddings=[vec], n_results=n)
        out: dict[str, float] = {}
        for i, dist in zip((res.get("ids") or [[]])[0], (res.get("distances") or [[]])[0]):
            # collection 是 cosine 空间 → 距离转相似度
            out[str(i)] = max(0.0, 1.0 - float(dist))
        return out
    except Exception as e:  # noqa: BLE001
        print(f"[M5b] 向量检索失败（退回关键词匹配）: {type(e).__name__}: {e}")
        return {}


# ---------------------------------------------------------------- 重建（幂等）
def rebuild_pool(account_id: int | None, *, with_vectors: bool = True) -> dict:
    """从各来源适配器重建池（幂等）：写 `tool_capabilities` 快照 + 递增版本（可选同步向量）。

    用途：改了适配器逻辑/排查不一致时跑一次即对齐；正常写入路径是 `sync_cli_entry()` 增量同步。
    `with_vectors=False` 时只落库（配置写入路径用这个口径，避免连带加载 bge 卡住界面）。
    """
    if not account_id:
        return {"ok": False, "error": "no account_id"}
    aid = int(account_id)
    entries = pool_entries(aid)
    written = 0
    try:
        conn = get_personal_conn()
        try:
            for e in entries:
                conn.execute(
                    "INSERT INTO tool_capabilities "
                    "(account_id, source, ref, name, keywords, abilities, invoke_hint, enabled, updated_at) "
                    "VALUES (?,?,?,?,?,?,?,?,datetime('now','localtime')) "
                    "ON CONFLICT(account_id, source, ref) DO UPDATE SET "
                    "  name=excluded.name, keywords=excluded.keywords, abilities=excluded.abilities, "
                    "  invoke_hint=excluded.invoke_hint, enabled=excluded.enabled, "
                    "  updated_at=datetime('now','localtime')",
                    (aid, e.source, e.ref, e.name, e.keywords, e.abilities, e.invoke_hint, 1 if e.enabled else 0))
                written += 1
            # 清掉来源已消失的条目
            keep = {(e.source, e.ref) for e in entries}
            rows = conn.execute("SELECT source, ref FROM tool_capabilities WHERE account_id=?",
                                (aid,)).fetchall()
            gone = [(r["source"], r["ref"]) for r in rows if (r["source"], r["ref"]) not in keep]
            for s, r in gone:
                conn.execute("DELETE FROM tool_capabilities WHERE account_id=? AND source=? AND ref=?",
                             (aid, s, r))
            conn.commit()
        finally:
            conn.close()
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}

    vec = sync_vectors(aid, entries) if with_vectors else {"ok": True, "skipped": "with_vectors=False"}
    ver = bump_pool_version(aid)
    return {"ok": True, "entries": len(entries), "written": written, "vectors": vec, "version": ver}


def sync_cli_entry(account_id: int | None) -> None:
    """CLI 写入口的增量同步钩子（save/delete/state 后调用）——UI 改了描述立即生效。

    **只落库 + 递增版本，不同步向量**（向量会加载 bge，界面会被冻住）；
    向量由 `ensure_vectors_fresh()` 在路由时刻按版本惰性重建。
    完全容错：任何异常都只打印，**绝不影响 CLI 配置本身的写入**。
    """
    if not account_id:
        return
    try:
        aid = int(account_id)
        entries = [e for e in _entries_from_clis(aid)]
        conn = get_personal_conn()
        try:
            keep = set()
            for e in entries:
                keep.add((e.source, e.ref))
                conn.execute(
                    "INSERT INTO tool_capabilities "
                    "(account_id, source, ref, name, keywords, abilities, invoke_hint, enabled, updated_at) "
                    "VALUES (?,?,?,?,?,?,?,?,datetime('now','localtime')) "
                    "ON CONFLICT(account_id, source, ref) DO UPDATE SET "
                    "  name=excluded.name, keywords=excluded.keywords, abilities=excluded.abilities, "
                    "  invoke_hint=excluded.invoke_hint, enabled=excluded.enabled, "
                    "  updated_at=datetime('now','localtime')",
                    (aid, e.source, e.ref, e.name, e.keywords, e.abilities, e.invoke_hint, 1 if e.enabled else 0))
            rows = conn.execute("SELECT source, ref FROM tool_capabilities WHERE account_id=? AND source=?",
                                (aid, SOURCE_CLI)).fetchall()
            for r in rows:
                if (r["source"], r["ref"]) not in keep:
                    conn.execute("DELETE FROM tool_capabilities WHERE account_id=? AND source=? AND ref=?",
                                 (aid, r["source"], r["ref"]))
            conn.commit()
        finally:
            conn.close()
        # 不在这里同步向量（会加载 bge 卡住面板）——见 ensure_vectors_fresh()
        bump_pool_version(aid)
    except Exception as e:  # noqa: BLE001
        print(f"[M5b] CLI 能力池同步失败（忽略，不影响配置）: {type(e).__name__}: {e}")


def pool_summary(account_id: int | None) -> dict:
    """池概览（前端/调试用）：条目数、各来源分布、版本号、向量同步状态。"""
    entries = pool_entries(account_id)
    by_source: dict[str, int] = {}
    for e in entries:
        by_source[e.source] = by_source.get(e.source, 0) + 1
    return {
        "count": len(entries),
        "by_source": by_source,
        "version": current_pool_version(int(account_id)) if account_id else 0,
        "refs": [f"{e.source}:{e.ref}" for e in entries],
    }
