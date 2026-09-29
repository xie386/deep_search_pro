# -*- coding: utf-8 -*-
"""M5c 路由文本与词法用例（**全离线、不读库、不联网**）。

钉住三件被基准实测证明有用/无用的改动：

① 向量文本口径 —— **保持「工具级 + 来源级」合并文本**。
   M5c-2 曾改成"只取工具级"，基准实测（28 条池 / 30 题）：
   precision@3 持平，但噪声假阳性 0.333 → 0.500、灰区 0.300 → 0.367 —— **负收益，已回退**。
② 泛化词剔除（`GENERIC_TOKENS` + `strip_generic`）—— 词法路打分前剔掉"今天/我/有没有"这类零区分度的词。
   实测：CLI 关键词里写着「**今天**B站在火什么」→「今天几号」竟让 `cli:bili` 拿 1.0 分，
   而短语权重 3.0 ≥ `LEX_MIN=2` → 一个泛化词命中就足以判"自信"。
③ 关键词派生（`derive_keywords`）—— 25 条 api/mcp 关键词为空时，从工具级描述兜底派生。
   实测：噪声假阳性 0.667 → 0.167、hit@3 0.708 → 0.792。

运行：.venv/Scripts/python.exe -m pytest tests/test_pool_route_text.py -q
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from tools import capability_pool as cp      # noqa: E402
from tools import tool_router as tr          # noqa: E402


def mcp_row(tool_ab: str, *, hint=True, keywords="", ref="mcp:pdd/search_goods", name="search_goods"):
    spec = {"server": "pdd", "tool": name, "read_only_hint": hint, "source_id": 7}
    return {"ref": ref, "name": name, "abilities": tool_ab, "keywords": keywords,
            "invoke_spec": json.dumps(spec, ensure_ascii=False),
            "input_schema": json.dumps({"type": "object", "properties": {"keyword": {"type": "string"}},
                                        "required": ["keyword"]}),
            "read_only": 1, "confirmed_at": "2026-09-27 11:00:00", "enabled": 1}


SRC_ROW = {"slug": "pdd", "enabled": 1, "abilities": "拼多多选品：搜商品比价、看详情、逛好价榜单"}


def legacy_text(e):
    """M5c-2 之前的 `_entry_text`（对比基准）。"""
    parts = [e.name]
    if e.keywords:
        parts.append(e.keywords)
    if e.abilities:
        parts.append(e.abilities)
    if not e.is_cli:
        ps = e.param_summary
        if ps:
            parts.append(ps)
    return "\n".join(parts).strip()


# ---------------------------------------------------------------- ① 向量文本口径（M5c-2 已回退）
def test_vector_text_keeps_source_level_description():
    """★ 向量文本必须**同时**含工具级与来源级（M5c-2 砍来源级 → 实测噪声假阳性 50%，已回退）。"""
    e = cp.mcp_entry(mcp_row("拼多多商品搜索 - 关键词搜索拼多多商品，支持筛选和排序"), SRC_ROW)
    vt = cp._entry_text(e)
    assert "拼多多商品搜索" in vt
    assert "逛好价榜单" in vt, "★ 来源级描述不能被砍 —— M5c-2 已实测回退（见模块 docstring）"
    assert vt == legacy_text(e), "必须与回退后的口径逐字一致"
    # 模型看到的卡片当然也两层都有
    assert "拼多多商品搜索" in e.card_text and "逛好价榜单" in e.card_text


def test_abilities_tool_field_still_populated():
    """`abilities_tool` 字段留着（不参与向量文本，但将来按来源/工具分层渲染还要用）。"""
    e = cp.mcp_entry(mcp_row("工具级描述"), SRC_ROW)
    assert e.abilities_tool == "工具级描述"
    assert "来源级" not in e.abilities_tool and "逛好价榜单" in e.abilities


def test_cli_entry_text_unchanged():
    """CLI 的钱路一个字都不动（M5b 有 content_hash 逐字冻结断言）。"""
    e = cp.cli_entry({"ref": "weread", "name": "微信读书", "keywords": "读书/书评",
                      "abilities": "查微信读书的书、书评与笔记",
                      "rules": [["weread", "search"]], "invokable": True})
    assert e.abilities_tool == ""
    assert cp._entry_text(e) == legacy_text(e), "★ CLI 的向量文本必须与 M5b 时代逐字一致"
    assert "参数: " not in cp._entry_text(e), "CLI 不加参数摘要（否则 content_hash 变）"


# ---------------------------------------------------------------- ② 泛化词剔除
def test_strip_generic_removes_zero_signal_words():
    assert cp.strip_generic("今天几号") == "几号"
    assert cp.strip_generic("今天B站在火什么") == "b站在火"
    assert cp.strip_generic("我的书架") == "书架"           # 领域词必须留下
    assert cp.strip_generic("阅读进度") == "阅读进度"
    assert cp.strip_generic("Search for documents in Gallica") == "documents gallica"


def test_lexical_no_false_hit_on_generic_word():
    """★ 回归钉子：「今天几号」不该让 bili 拿词法分（实测踩过 1.0 分 → 误路由）。"""
    cli = [cp.cli_entry({"bin": "bili", "name": "哔哩哔哩",
                         "abilities": "关键词：今天B站在火什么/书评。查 B 站视频。",
                         "rules": [["search"]], "live": 1})]
    assert cli[0].keywords == "今天B站在火什么/书评", "用例前提：关键词来自「关键词：」前缀"
    assert tr.lexical_scores("今天几号", cli)["cli:bili"] == 0.0, tr.lexical_scores("今天几号", cli)


def test_lexical_still_scores_real_domain_phrase():
    cli = [cp.cli_entry({"bin": "bili", "name": "哔哩哔哩",
                         "abilities": "关键词：书评/阅读进度。查 B 站视频。",
                         "rules": [["search"]], "live": 1})]
    assert tr.lexical_scores("有什么好的书评", cli)["cli:bili"] > 0


# ---------------------------------------------------------------- ③ 关键词派生
def test_derive_keywords_splits_chinese_run_on_generic_words():
    kw = cp.derive_keywords("查询指定城市的所有火车站名。例如：广州、北京。")
    assert "火车站名" in kw and "指定城市" in kw
    assert "所有" not in kw and "指定城市的所有" not in kw
    # ★ 2 字实体（广州/北京）按设计不进关键词表：它们的 2-gram 仍会计分（权重 1，不足以越过 LEX_MIN），
    #   而放进短语会带来「排序」那类误命中 —— 基准 A/B 实测：噪声假阳性 0.333 → 0.167。


def test_derive_keywords_strips_leading_verb_when_it_splits():
    """前导泛化词要剥掉 —— 但只在确实切出**多块**时用切开的结果。

    ⚠️ 「查询12306余票信息」→ 切开只剩一块「12306余票信息」，这种整段（>8 字）**按设计丢弃**：
    实测"只要切过就用切开的那块"会让 噪声假阳性 0.167 → 0.333、灰区 0.300 → 0.333
    （precision/recall/hit 不变）——多出来的关键词会改变向量文本、把个别条目顶成离群高分。
    """
    kw = cp.derive_keywords("查询指定城市的所有火车站名。")
    assert "查询" not in kw and "指定城市" in kw and "火车站名" in kw
    assert cp.derive_keywords("查询12306余票信息。") == "", "十来个字的整段不派生（见上面注释）"


def test_derive_keywords_drops_two_char_fragments():
    """★ 机械派生的 2 字片段太容易是通用词（「排序」曾让"写个快速排序"被误路由）。"""
    assert cp.DERIVED_MIN_CHARS >= 3
    kw = cp.derive_keywords("根据关键词搜索拼多多上的商品，支持按价格排序。")
    assert "排序" not in kw.split("/"), kw


def test_derive_keywords_english_description_keeps_domain_words():
    kw = cp.derive_keywords("Search for documents in the Gallica digital library by title.")
    assert "gallica" in kw.lower() and "title" in kw.lower()
    assert "documents" not in kw.lower()          # documents 是泛词


def test_entry_from_row_falls_back_to_derived_keywords():
    """没写关键词时用派生兜底；人工写了就以人工的为准。"""
    row = mcp_row("查询指定城市的所有火车站名。")
    e = cp.mcp_entry(row, SRC_ROW)
    assert "火车站名" in e.keywords
    e2 = cp.mcp_entry(mcp_row("查询指定城市的所有火车站名。", keywords="人工关键词"), SRC_ROW)
    assert e2.keywords == "人工关键词"


def test_generic_tokens_are_not_kept_as_standalone_phrases():
    assert cp.derive_keywords("今天 我的 有没有 什么 怎么") == ""
