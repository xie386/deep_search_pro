"""知识库服务（M3）——每用户 Chroma 向量库：embedding / 入库 / 检索。

移植蜀道项目（`backend/src/ai/rag.py` + `knowledge.py` + `embeddings.py`）已跑通方案
（bge-large-zh GPU + BM25 稀疏 + 向量稠密 + RRF 融合 + 可选 reranker），按本项目
「每用户一库 + 快/全双模式」改造：

- 库目录：rag_knowledge/db/{username}/chroma/（PersistentClient 持久化）
- collection：kb_{username}（每用户一个，规模 ≤100 文档 → 索引开销可忽略）
- 父子分块：md 按标题/段落切父块（≤800 字，滑窗+15% 重叠），父块再切子块
  （向量检索子块，命中后注入父块完整文本——Small-to-Big）
- 检索双模式：
  🚀 fast：embed(GPU ~30ms) + chroma top30 + BM25 top30 → RRF top30 → 父块去重 top_k
  📚 full：fast 基础上 + bge-reranker 重排 → MMR 式父块配额 → top_k
- bge 官方查询前缀提升相关性；embedding 一律 GPU（批注 9）、lazy 常驻单例

lazy import torch 依赖，保证核心后端启动不受影响。
"""
from __future__ import annotations

import hashlib
import logging
import os
import re
from functools import lru_cache
from pathlib import Path
 
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 路径与常量
# ---------------------------------------------------------------------------

PROJECT_ROOT = Path(__file__).resolve().parents[1]
KB_ROOT = PROJECT_ROOT / "rag_knowledge"
DB_ROOT = KB_ROOT / "db"          # rag_knowledge/db/{username}/chroma（批注 2）
EMBEDDING_MODEL_PATH = os.getenv("EMBEDDING_MODEL_PATH") or r"D:\LLM\model\bge-large-zh"
RERANKER_MODEL_PATH = os.getenv("RERANKER_MODEL_PATH") or r"D:\LLM\model\bge-reranker-base"
DEVICE = os.getenv("EMBEDDING_DEVICE") or "cuda"   # 批注 9：一律 GPU
BGE_QUERY_PREFIX = "为这个句子生成表示以用于检索相关文章："

# 分块参数（蜀道 AI_SOLUTION §4.1 对齐，报告类文档）
CHUNK_MIN, CHUNK_MAX = 300, 800
OVERLAP_RATIO = 0.15

# 检索参数（蜀道 B2 调优值）
DENSE_K = 30      # 稠密召回数
SPARSE_K = 30     # 稀疏召回数
RRF_K = 60        # RRF 平滑常数
FUSE_TOPK = 30    # 融合后候选数
FAST_K = 4        # fast 模式最终父块数
FULL_K = 6        # full 模式最终父块数

# ---------------------------------------------------------------------------
# 模型加载（lazy 单例）
# ---------------------------------------------------------------------------


class _EmbeddingModel:
    """bge-large-zh 嵌入模型（GPU lazy 常驻单例，含 bge 查询前缀）。"""

    def __init__(self) -> None:
        self._model = None

    @property
    def model(self):
        if self._model is None:
            from sentence_transformers import SentenceTransformer  # lazy

            self._model = SentenceTransformer(EMBEDDING_MODEL_PATH, device=DEVICE)
            logger.info("bge-large-zh 已加载: %s (device=%s)", EMBEDDING_MODEL_PATH, DEVICE)
        return self._model

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        # normalize_embeddings=True：与 chroma cosine 匹配
        v = self.model.encode(texts, normalize_embeddings=True, show_progress_bar=False)
        return [x.tolist() for x in v]

    def embed_query(self, text: str) -> list[float]:
        v = self.model.encode([f"{BGE_QUERY_PREFIX}{text}"],
                              normalize_embeddings=True, show_progress_bar=False)
        return v[0].tolist()


@lru_cache
def embedding_model() -> _EmbeddingModel:
    return _EmbeddingModel()


@lru_cache
def reranker_model():
    """bge-reranker（CrossEncoder）——仅 full 模式加载（周报），对话不占用。"""
    from sentence_transformers import CrossEncoder  # lazy

    logger.info("bge-reranker 加载: %s (device=%s)", RERANKER_MODEL_PATH, DEVICE)
    return CrossEncoder(RERANKER_MODEL_PATH, device=DEVICE)


# ---------------------------------------------------------------------------
# 用户库管理
# ---------------------------------------------------------------------------


def user_db_dir(username: str) -> Path:
    d = DB_ROOT / username
    return d


# 每用户一库（collection 名只用 account_id —— chroma 命名只允许 [a-zA-Z0-9._-]，
# 中文 username 如「灵康科技」拼到 collection 名会触发 InvalidArgumentError）。
# 目录路径仍用 username（路径允许任意字符）。
def _collection_name(account_id: int) -> str:
    return f"kb_user_{account_id}"


def _resolve_account_id(username: str, account_id: int | None) -> int:
    """优先用调用方传入的 account_id；否则查库。"""
    if account_id is not None:
        return account_id
    try:
        from tools.schema_personal import get_personal_conn
        conn = get_personal_conn()
        row = conn.execute("SELECT id FROM accounts WHERE username=?", (username,)).fetchone()
        conn.close()
        return int(row["id"]) if row else 0
    except Exception:
        return 0


def get_collection(username: str, account_id: int | None = None):
    """每用户 Chroma collection（lazy 创建）。"""
    import chromadb  # lazy

    d = user_db_dir(username) / "chroma"
    d.mkdir(parents=True, exist_ok=True)
    client = chromadb.PersistentClient(path=str(d))
    # account_id 优先：路由关键；username 兜底（按 username 首次建库时给个临时 id）
    if account_id is None:
        try:
            from tools.schema_personal import get_personal_conn
            conn = get_personal_conn()
            row = conn.execute("SELECT id FROM accounts WHERE username=?", (username,)).fetchone()
            conn.close()
            account_id = int(row["id"]) if row else 0
        except Exception:
            account_id = 0
    return client.get_or_create_collection(
        name=_collection_name(account_id), metadata={"hnsw:space": "cosine"}
    )


def kb_exists(username: str, account_id: int | None = None) -> bool:
    """库是否已创建（批注 13：默认无库，首次操作才建）。"""
    import chromadb  # lazy

    d = user_db_dir(username) / "chroma"
    if not d.exists():
        return False
    try:
        client = chromadb.PersistentClient(path=str(d))
        names = client.list_collections()
        # 兼容新旧：旧 collection 名 kb_{username}（可能已存在）+ 新规则 kb_user_{id}
        if account_id is None:
            try:
                from tools.schema_personal import get_personal_conn
                conn = get_personal_conn()
                row = conn.execute("SELECT id FROM accounts WHERE username=?", (username,)).fetchone()
                conn.close()
                account_id = int(row["id"]) if row else 0
            except Exception:
                account_id = 0
        target = _collection_name(account_id)
        return any(c.name in (target, f"kb_{username}") for c in names)
    except Exception:
        return False


# ---------------------------------------------------------------------------
# 分块（蜀道 _overlap_split 移植：滑窗 + 15% 重叠）
# ---------------------------------------------------------------------------


def _split_sentences(text: str) -> list[str]:
    return [s.strip() for s in re.split(r"(?<=[。；;！？!?\n])", text) if s.strip()]


def _overlap_chunk(text: str) -> list[str]:
    """超长文本按句滑窗切块：单块 ≤CHUNK_MAX，相邻块尾部 15% 重叠。"""
    sentences = _split_sentences(text)
    chunks: list[str] = []
    cur = ""
    for s in sentences:
        if len(cur) + len(s) <= CHUNK_MAX:
            cur += s
            continue
        if cur:
            chunks.append(cur)
        overlap = cur[-int(CHUNK_MAX * OVERLAP_RATIO):] if cur else ""
        cur = overlap + s
        if len(cur) > CHUNK_MAX:  # 单句仍超长：硬切
            chunks.append(cur)
            cur = ""
    if cur:
        chunks.append(cur)
    return chunks


def _split_parents(md_text: str) -> list[str]:
    """md 按标题层级切父块：#/##/### 为边界；无标题段落聚合；块超长再滑窗。"""
    lines = md_text.split("\n")
    parents: list[str] = []
    cur_title = ""
    cur_body: list[str] = []

    def flush():
        nonlocal cur_title, cur_body
        text = (cur_title + "\n" if cur_title else "") + "\n".join(cur_body)
        text = text.strip()
        if len(text) >= CHUNK_MIN:
            parents.append(text)
        elif text:
            # 有标题的短块独立成块（标题是语义边界，并入他块会混义）；
            # 无标题的短块并入前一块（避免碎片）
            if cur_title:
                parents.append(text)
            elif parents:
                parents[-1] = parents[-1] + "\n" + text
            else:
                parents.append(text)
        cur_title, cur_body = "", []

    for ln in lines:
        if re.match(r"^#{1,3}\s+", ln):
            flush()
            cur_title = ln.strip()
        elif ln.strip():
            cur_body.append(ln)
    flush()

    # 超长父块滑窗切分（保留标题在首块）
    out: list[str] = []
    for p in parents:
        if len(p) > CHUNK_MAX:
            out.extend(_overlap_chunk(p))
        else:
            out.append(p)
    return [p for p in out if p.strip()]


def _split_children(parent_text: str) -> list[str]:
    """父块再切子块（≤CHUNK_MAX，重叠 15%）；父块本就不超长则子块=父块。"""
    if len(parent_text) <= CHUNK_MAX:
        return [parent_text]
    return _overlap_chunk(parent_text)


# ---------------------------------------------------------------------------
# 入库
# ---------------------------------------------------------------------------


def _source_id(content: str) -> str:
    return hashlib.md5(content.encode("utf-8")).hexdigest()


def ingest_text(username: str, title: str, content: str, source_kind: str = "upload",
               account_id: int | None = None) -> dict:
    """文本入库（账号隔离，md5 去重）。

    :return: {"ok": bool, "dup": bool, "source_id": str, "chunks": int, "title": str}
    """
    content = (content or "").strip()
    if not content:
        return {"ok": False, "dup": False, "error": "内容为空"}
    sid = _source_id(content)

    # 去重：同 md5 已在库 → 不重复入库（批注 11）
    from rag_knowledge.kb_store import record_exists
    if record_exists(username, sid):
        return {"ok": True, "dup": True, "source_id": sid,
                "title": title, "chunks": 0}

    col = get_collection(username, _resolve_account_id(username, account_id))
    parents = _split_parents(content)
    # 组装子块：每个父块切成子块向量化，metadata.parent_text 指向父块
    records: list[dict] = []      # chroma upsert 记录
    chunk_i = 0
    for p in parents:
        children = _split_children(p)
        for c in children:
            records.append({
                "id": f"{sid}_{chunk_i}",
                "text": c,
                "parent_text": p,
                "title": title,
            })
            chunk_i += 1
    if not records:
        return {"ok": False, "dup": False, "error": "无可入库内容（分块为空）"}

    # 向量化（GPU 批量）+ upsert
    texts = [r["text"] for r in records]
    vecs = embedding_model().embed_documents(texts)
    col.upsert(
        ids=[r["id"] for r in records],
        documents=texts,
        embeddings=vecs,
        metadatas=[{
            "source_id": sid,
            "source_kind": source_kind,
            "title": r["title"],
            "parent_text": r["parent_text"],
            "username": username,
        } for r in records],
    )
    # 入库记录
    from rag_knowledge.kb_store import add_record
    add_record(username, {
        "source_id": sid,
        "title": title,
        "source_kind": source_kind,
        "chunks": len(records),
        "created_at": __import__("datetime").datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    })
    _invalidate_bm25(username)
    return {"ok": True, "dup": False, "source_id": sid,
            "title": title, "chunks": len(records)}


def delete_doc(username: str, source_id: str, account_id: int | None = None) -> bool:
    """按 source_id 删除整篇文档及其子块。"""
    col = get_collection(username, _resolve_account_id(username, account_id))
    col.delete(where={"source_id": source_id})
    from rag_knowledge.kb_store import remove_record
    removed = remove_record(username, source_id)
    _invalidate_bm25(username)
    return removed


# ---------------------------------------------------------------------------
# BM25 语料（每用户缓存；入库/删除后失效）
# ---------------------------------------------------------------------------

_bm25_cache: dict[str, object] = {}
_corpus_cache: dict[str, tuple] = {}


def _invalidate_bm25(username: str):
    _bm25_cache.pop(username, None)
    _corpus_cache.pop(username, None)


def _tokenize(text: str) -> list[str]:
    import jieba  # lazy
    return [t for t in jieba.lcut(text) if t.strip() and len(t.strip()) > 1]


def _load_corpus(username: str, account_id: int | None = None) -> tuple[list[str], list[dict]]:
    """全量子块语料 + metadata（BM25 用，与 chroma 同源）。"""
    if username in _corpus_cache:
        return _corpus_cache[username]
    col = get_collection(username, _resolve_account_id(username, account_id))
    data = col.get(include=["documents", "metadatas"])
    corpus = [d or "" for d in data["documents"]]
    metas = data["metadatas"] or [{}] * len(corpus)
    _corpus_cache[username] = (corpus, metas)
    return corpus, metas


def _get_bm25(username: str, account_id: int | None = None):
    if username not in _bm25_cache:
        from rank_bm25 import BM25Okapi  # lazy
        corpus, _ = _load_corpus(username, account_id)
        _bm25_cache[username] = BM25Okapi([_tokenize(c) for c in corpus])
    return _bm25_cache[username]


# ---------------------------------------------------------------------------
# 检索（快/全双模式）
# ---------------------------------------------------------------------------


def _dense_search(username: str, question: str, account_id: int | None = None) -> list[dict]:
    col = get_collection(username, _resolve_account_id(username, account_id))
    vec = embedding_model().embed_query(question)
    res = col.query(query_embeddings=[vec], n_results=min(DENSE_K, col.count() or 1))
    out = []
    ids = (res.get("ids") or [[]])[0]
    docs = (res.get("documents") or [[]])[0]
    metas = (res.get("metadatas") or [[]])[0]
    for cid, d, m in zip(ids, docs, metas):
        out.append({"text": d, "metadata": m or {}, "chunk_id": cid})
    return out


def _sparse_search(username: str, question: str, account_id: int | None = None) -> list[dict]:
    bm25 = _get_bm25(username, account_id)
    corpus, metas = _load_corpus(username, account_id)
    if not corpus:
        return []
    scores = bm25.get_scores(_tokenize(question))
    ranked = sorted(enumerate(scores), key=lambda x: x[1], reverse=True)[:SPARSE_K]
    return [
        {"text": corpus[i], "metadata": metas[i] or {}, "bm25_score": float(s)}
        for i, s in ranked if s > 0
    ]


def _rrf_fuse(dense: list[dict], sparse: list[dict]) -> list[dict]:
    """Reciprocal Rank Fusion：1/(K+rank) 求和。"""
    scores: dict[str, float] = {}
    items: dict[str, dict] = {}
    for rank, hit in enumerate(dense):
        key = hit["text"]
        scores[key] = scores.get(key, 0.0) + 1.0 / (RRF_K + rank + 1)
        items[key] = hit
    for rank, hit in enumerate(sparse):
        key = hit["text"]
        scores[key] = scores.get(key, 0.0) + 1.0 / (RRF_K + rank + 1)
        items.setdefault(key, hit)
    fused = sorted(scores.items(), key=lambda x: x[1], reverse=True)[:FUSE_TOPK]
    return [items[key] for key, _ in fused]


def _parent_dedup(candidates: list[dict], top_k: int) -> list[dict]:
    """父块去重：同 parent_text 的子块只保留 RRF 分最高的，然后按分取 top_k。

    fast 模式用它（无 rerank）；full 模式先 rerank 再按此配额。
    """
    best: dict[str, dict] = {}
    for hit in candidates:
        parent = (hit.get("metadata", {}).get("parent_text") or hit["text"]).strip()
        if parent not in best:
            best[parent] = hit
        # 同父块后续候选保留（rrf 分更高才替换——候选已按 rrf 降序）
    return list(best.values())[:top_k]


def query(username: str, question: str, mode: str = "fast", top_k: int | None = None,
          account_id: int | None = None) -> list[dict]:
    """检索用户知识库。

    :param mode: "fast"（对话，无 rerank）| "full"（周报，rerank 重排）
    :return: [{text(父块全文), title, source_id}]
    """
    aid = _resolve_account_id(username, account_id)
    if not kb_exists(username, aid):
        return []
    col = get_collection(username, aid)
    if (col.count() or 0) == 0:
        return []
    k = top_k or (FULL_K if mode == "full" else FAST_K)

    dense = _dense_search(username, question, aid)
    if not dense:
        return []
    fused = _rrf_fuse(dense, _sparse_search(username, question, aid))
    if not fused:
        return []

    if mode == "full":
        try:
            reranker = reranker_model()
            pairs = [[question, (h.get("metadata", {}).get("parent_text") or h["text"])]
                     for h in fused]
            scores = reranker.predict(pairs)
            ranked = sorted(zip(fused, scores), key=lambda x: float(x[1]), reverse=True)
            fused = [h for h, _ in ranked]
        except Exception as e:
            logger.warning("rerank 失败降级 fast: %s", e)

    selected = _parent_dedup(fused, k)
    out = []
    for hit in selected:
        m = hit.get("metadata", {})
        parent = (m.get("parent_text") or hit["text"]).strip()
        out.append({
            "text": parent,
            "title": m.get("title", ""),
            "source_id": m.get("source_id", ""),
        })
    return out


# ---------------------------------------------------------------------------
# 组合（给工具/API 的友好返回）
# ---------------------------------------------------------------------------


def query_kb_friendly(username: str, question: str, mode: str = "fast", account_id: int | None = None) -> str:
    """检索并格式化为给 LLM 的片段文本（含 📚 来源）。"""
    hits = query(username, question, mode=mode, account_id=account_id)
    if not hits:
        return ""  # 调用方决定话术（空库/无命中区分由 kb_exists 处理）
    parts = []
    for i, h in enumerate(hits, 1):
        title = h["title"] or "未命名文档"
        parts.append(f"📚 知识库片段{i}（来自《{title}》）:\n{h['text']}")
    return "\n\n".join(parts)
