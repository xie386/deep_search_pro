"""M3 RAG 知识库 · 单元测试（pytest）。

纯逻辑层（无需 GPU 模型，秒级）：分块 / cleaner 清洗 / store 去重。
模型相关（入库/检索）走 tests/m3_kb_e2e_test.py（真 bge GPU + 起服务）。

运行：.venv/Scripts/python.exe -m pytest tests/test_knowledge_base.py -v
"""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
sys.path = [p for p in sys.path if "hermes-agent" not in p and "hermes_agent" not in p]
os.environ.pop("PYTHONPATH", None)

from rag_knowledge import cleaner
from rag_knowledge.kb_service import _overlap_chunk, _split_parents, _source_id
from rag_knowledge.kb_service import CHUNK_MAX


# ---------- 分块 ----------
def test_split_parents_by_heading():
    md = "# 标题A\n\n第一段内容，讲显卡。\n\n## 小节B\n\n第二段内容。"
    parents = _split_parents(md)
    assert len(parents) >= 2, "标题应切分为多个父块"
    assert any("标题A" in p for p in parents)
    assert any("小节B" in p for p in parents)


def test_overlap_chunk_bounds():
    long_text = "今天天气很好。" * 200  # 1400 字 > CHUNK_MAX(800)
    chunks = _overlap_chunk(long_text)
    assert len(chunks) > 1
    assert all(len(c) <= CHUNK_MAX + 10 for c in chunks), "单块不超上限(容差)"
    # 内容不丢失：拼接后去重字符应覆盖原文
    joined = "".join(chunks)
    assert len(joined) >= len(long_text) * 0.85, "重叠导致重复但不应丢内容"


# ---------- source_id ----------
def test_source_id_md5_stable():
    assert _source_id("abc") == _source_id("abc")
    assert _source_id("abc") != _source_id("abd")
    assert len(_source_id("x")) == 32


# ---------- cleaner（批注 8：AI 报告脏内容） ----------
def test_cleaner_removes_emoji():
    r = cleaner.clean_for_kb("✨ 摘要：\U0001F389 内容测试")
    assert "✨" not in r and "\U0001F389" not in r


def test_cleaner_table_to_text():
    dirty = "| 品牌 | 价格 |\n| --- | --- |\n| 真露 | 150元 |\n| 二锅头 | 30元 |"
    r = cleaner.clean_for_kb(dirty)
    assert "|" not in r, "表格竖线应去除"
    assert "真露" in r and "150元" in r, "单元格语义保留"


def test_cleaner_persona_opening_removed():
    dirty = "✨ 情报官周报～ 奴婢为您整理好了呢！(≧∇≦)ﾉ\n## 摘要\n正文内容。"
    r = cleaner.clean_for_kb(dirty)
    assert "奴婢" not in r, "人格口吻开场应删"
    assert "摘要" in r, "正文标题保留"


def test_cleaner_source_emoji_to_text():
    r = cleaner.clean_for_kb("📚 知识库片段\n🔍 网络来源")
    assert "📚" not in r and "知识库" in r


def test_cleaner_keeps_md_structure():
    r = cleaner.clean_for_kb("# 大标题\n\n**加粗内容** 正文。\n\n- 列表项")
    assert "# 大标题" in r, "标题保留（检索特征）"
    assert "列表项" in r


def test_cleaner_plain_content_unchanged():
    src = "纯文本内容没有脏东西。\n第二行正常。"
    assert cleaner.clean_for_kb(src) == src


# ---------- kb_store 去重（批注 11） ----------
def test_store_dedup(tmp_path, monkeypatch):
    from rag_knowledge import kb_store
    monkeypatch.setattr(kb_store, "user_db_dir", lambda u: tmp_path / u)
    rec = {"source_id": "abc123", "title": "t", "source_kind": "upload", "chunks": 3,
           "created_at": "2026-09-04 10:00:00"}
    assert kb_store.add_record("u1", rec)
    assert not kb_store.add_record("u1", rec), "同 source_id 二次添加应 False"
    assert kb_store.record_exists("u1", "abc123")
    assert kb_store.remove_record("u1", "abc123")
    assert not kb_store.record_exists("u1", "abc123")
