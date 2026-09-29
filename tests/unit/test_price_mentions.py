# -*- coding: utf-8 -*-
"""M5-5 用例：周报价格情报结构化（契约 / 容错解析 / 实体匹配 / 不额外调模型）。

判据来源：M5 方案 §六 M5-5（≥10 项：契约自检、脏输出容错、实体匹配、**断言不增加模型调用**）+ C5/G6/G4。

运行：.venv/Scripts/python.exe -m pytest tests/test_price_mentions.py -q
"""
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from tools.price_mentions import (PRICE_LINE_RE, PRICE_LINE_SAMPLE, collect_price_mentions,   # noqa: E402
                                  list_price_mentions, match_entity, parse_price_mentions)
from tools.schema_personal import ensure_tables, get_personal_conn, purge_account   # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ensure_tables()


def mk_account():
    conn = get_personal_conn()
    try:
        aid = int(conn.execute("INSERT INTO accounts (username, password_hash, role) VALUES (?,?,?)",
                               ("m5m_%d" % (int(time.time() * 1000) % 1000000), "x", "user")).lastrowid)
        conn.commit()
    finally:
        conn.close()
    return aid


def mk_own(aid, name="米诺地尔泡沫剂", brand="蔓迪"):
    conn = get_personal_conn()
    try:
        cur = conn.execute("INSERT INTO products (owner_id, product_name, brand, category, price, status)"
                           " VALUES (?,?,?,?,?,?)", (aid, name, brand, "个护", 120.0, "active"))
        conn.commit()
        return int(cur.lastrowid)
    finally:
        conn.close()


def mk_comp(aid, name="至本洗面奶"):
    conn = get_personal_conn()
    try:
        cur = conn.execute("INSERT INTO company_competitors (owner_id, comp_name, is_competitor)"
                           " VALUES (?,?,?)", (aid, name, 1))
        conn.commit()
        return int(cur.lastrowid)
    finally:
        conn.close()


LINE = "价格情报：实体=米诺地尔泡沫剂 | 价格=89 | 币种=CNY | 渠道=拼多多百亿补贴 | URL=https://example.com/p/1"


# ---------------------------------------------------------------- ① 契约自检（G6）
def test_contract_self_check():
    assert PRICE_LINE_RE.search(PRICE_LINE_SAMPLE), "★ 行契约常量与解析正则必须一致"
    assert "实体=" in PRICE_LINE_SAMPLE and "价格=" in PRICE_LINE_SAMPLE


def test_prompt_and_contract_are_in_sync():
    """提示词里的写法必须与常量一致（改一处忘了另一处 = 静默抽不到）。"""
    s = open(os.path.join(ROOT, "prompt", "prompts.yml"), encoding="utf-8").read()
    assert "价格情报：实体=<名称>" in s and "价格情报：实体=" in s
    assert "整块不写" in s, "必须写明「没有就别写」，否则模型会硬凑"


# ---------------------------------------------------------------- ② 解析与容错
def test_parse_normal_line():
    got = parse_price_mentions("## 报告\n" + LINE + "\n")
    assert len(got) == 1
    m = got[0]
    assert m["entity"] == "米诺地尔泡沫剂" and m["price"] == 89.0
    assert m["currency"] == "CNY" and m["shop"] == "拼多多百亿补贴"
    assert m["source_url"].startswith("https://")


def test_parse_tolerates_dirty_output():
    dirty = "**- 价格情报**： 实体 = 米诺地尔泡沫剂 ｜ 价格 = ¥89.9 元 ｜ 渠道=京东"
    got = parse_price_mentions(dirty)
    assert got and got[0]["price"] == 89.9, "加粗/全角竖线/货币符号都要能容错"


def test_parse_skips_incomplete_lines():
    assert parse_price_mentions("价格情报：价格=89") == [], "缺实体 → 跳过（不猜）"
    assert parse_price_mentions("价格情报：实体=某物") == [], "缺价格 → 跳过"
    assert parse_price_mentions("这行没有价格情报") == []


def test_parse_without_block_is_empty():
    assert parse_price_mentions("普通周报正文，没有任何价格行") == []


# ---------------------------------------------------------------- ③ 实体匹配（只标「疑似"，不参与提醒）
def test_match_exact_name():
    aid = mk_account()
    try:
        pid = mk_own(aid)
        got = match_entity(aid, "米诺地尔泡沫剂")
        assert got["linked_item_type"] == "product" and got["linked_item_id"] == pid
        assert got["match_score"] == 1.0
    finally:
        purge_account(aid)


def test_match_partial_and_brand():
    aid = mk_account()
    try:
        mk_own(aid, "米诺地尔泡沫剂（蔓迪）", brand="蔓迪")
        assert match_entity(aid, "米诺地尔")["match_score"] == 0.7, "名称包含 → 0.7"
        assert match_entity(aid, "蔓迪")["match_score"] >= 0.5, "品牌命中也要算（0.5 起）"
    finally:
        purge_account(aid)


def test_match_competitor_table():
    aid = mk_account()
    try:
        cid = mk_comp(aid)
        got = match_entity(aid, "至本洗面奶")
        assert got["linked_item_type"] == "competitor_product" and got["linked_item_id"] == cid
    finally:
        purge_account(aid)


def test_match_unknown_entity_is_zero():
    aid = mk_account()
    try:
        mk_own(aid)
        got = match_entity(aid, "完全不相干的东西")
        assert got["match_score"] == 0.0 and got["linked_item_id"] is None, "★ 对不上就不链接，绝不硬塞"
    finally:
        purge_account(aid)


def test_match_is_account_scoped():
    a, b = mk_account(), mk_account()
    try:
        mk_own(a, "只属于A的商品")
        assert match_entity(b, "只属于A的商品")["match_score"] == 0.0
    finally:
        purge_account(a); purge_account(b)


# ---------------------------------------------------------------- ④ 落库、去重与隔离
def test_collect_inserts_and_links():
    aid = mk_account()
    try:
        mk_own(aid)
        r = collect_price_mentions(aid, 12, "正文\n" + LINE)
        assert r["ok"] and r["inserted"] == 1
        rows = list_price_mentions(aid)
        assert rows[0]["entity"] == "米诺地尔泡沫剂" and rows[0]["source_url"].endswith("/p/1")
        assert rows[0]["linked_item_type"] == "product"
    finally:
        purge_account(aid)


def test_collect_is_idempotent_per_report():
    aid = mk_account()
    try:
        mk_own(aid)
        collect_price_mentions(aid, 12, LINE)
        r2 = collect_price_mentions(aid, 12, LINE)
        assert r2["inserted"] == 0, "同一份周报重复解析（重试）不该翻倍"
        assert len(list_price_mentions(aid)) == 1
    finally:
        purge_account(aid)


def test_collect_never_raises_on_garbage():
    aid = mk_account()
    try:
        r = collect_price_mentions(aid, None, None)
        assert r["ok"] and r["parsed"] == 0
        r2 = collect_price_mentions(aid, None, "价格情报：实体=|价格=abc")
        assert r2["parsed"] == 0
    finally:
        purge_account(aid)


def test_no_extra_model_call_in_the_parsing_path():
    """★ C5 硬要求：抽取是**纯解析**，不许出现任何模型调用（否则周报就多烧一次额度）。"""
    s = open(os.path.join(ROOT, "tools", "price_mentions.py"), encoding="utf-8").read()
    for bad in ("create_deep_agent", "invoke(", ".invoke", "model.", "llm", "ChatOpenAI", "requests."):
        assert bad not in s, "解析路径里不该出现 %r（C5：不额外调模型）" % bad


def test_digest_wires_the_parser_after_writing():
    s = open(os.path.join(ROOT, "agent", "digest_engine.py"), encoding="utf-8").read().replace("\r\n", "\n")
    i_write = s.find('md_path.write_text(md, encoding="utf-8")')
    i_parse = s.find("collect_price_mentions(owner_id")
    assert 0 < i_write < i_parse, "应当在正文写盘之后再解析（解析的是最终正文）"


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q"]))
