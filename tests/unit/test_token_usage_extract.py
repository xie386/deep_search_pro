# -*- coding: utf-8 -*-
"""M6c-2 用例：`agent/token_usage.py` —— `extract_usage` 四形态 + `compute_cost` 折算规则。

判据来源：M6c 方案 §3.1/§3.2 与 G2/G3/G4/G5；夹具对应真实厂商响应形态（**全离线，不发请求**）。
真实形态证据见方案 §二：DeepSeek 的缓存字段**只在 raw** 里（`prompt_cache_hit_tokens` /
`prompt_cache_miss_tokens`），LangChain 只把 OpenAI 系的 `prompt_tokens_details.cached_tokens`
归一化成 `input_token_details.cache_read`。

运行：.venv/Scripts/python.exe -m pytest tests/unit/test_token_usage_extract.py -q
"""
import ast
import io
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from agent.token_usage import (SRC_NO_CACHE_DETAIL, SRC_REPORTED, SRC_UNAVAILABLE,   # noqa: E402
                               SRC_UNKNOWN_SHAPE, compute_cost, extract_usage)

# --------------------------------------------------------------- 真实形态夹具

DEEPSEEK_RAW = {"prompt_tokens": 1000, "completion_tokens": 200, "total_tokens": 1200,
                "prompt_cache_hit_tokens": 300, "prompt_cache_miss_tokens": 700}
OPENAI_RAW = {"prompt_tokens": 1000, "completion_tokens": 200, "total_tokens": 1200,
              "prompt_tokens_details": {"cached_tokens": 256}}
PLAIN_RAW = {"prompt_tokens": 1000, "completion_tokens": 200, "total_tokens": 1200}
NORMALIZED = {"input_tokens": 1000, "output_tokens": 200, "total_tokens": 1200,
              "input_token_details": {"cache_read": 128}}
PRICES = {"price_in_cached": 0.5, "price_in_uncached": 2.0, "price_out": 8.0}


# --------------------------------------------------------------- ① 四形态

def test_shape1_deepseek_uses_top_level_cache_fields():
    """形态①：DeepSeek 顶层 hit/miss —— 归一化字段拿不到，必须能解析出来。"""
    u = extract_usage(DEEPSEEK_RAW, None)
    assert u["input"] == 1000 and u["output"] == 200
    assert u["cached"] == 300 and u["uncached"] == 700
    assert u["token_source"] == SRC_REPORTED
    assert u["cached"] + u["uncached"] == u["input"]


def test_shape1_only_hit_field_derives_miss():
    """只有命中数时：未命中 = 总量 − 命中（不给 0，也不编造）。"""
    u = extract_usage({"prompt_tokens": 1000, "completion_tokens": 10,
                       "prompt_cache_hit_tokens": 400}, None)
    assert (u["cached"], u["uncached"]) == (400, 600)
    assert u["token_source"] == SRC_REPORTED


def test_shape2_openai_details_cached_tokens():
    """形态②：OpenAI 的 prompt_tokens_details.cached_tokens。"""
    u = extract_usage(OPENAI_RAW, None)
    assert (u["cached"], u["uncached"], u["input"]) == (256, 744, 1000)
    assert u["token_source"] == SRC_REPORTED


def test_shape3_total_only_goes_to_uncached():
    """形态③：只有总量、无缓存拆分 → 全记未命中 + `no_cache_detail`（D3：宁高不低）。"""
    u = extract_usage(PLAIN_RAW, None)
    assert (u["cached"], u["uncached"]) == (0, 1000)
    assert u["token_source"] == SRC_NO_CACHE_DETAIL


def test_shape4_no_usage_writes_nothing():
    """形态④：连 usage 都没有 → `unavailable`，**四个数都不写**（不是 0）。"""
    u = extract_usage(None, None)
    assert u["token_source"] == SRC_UNAVAILABLE
    assert u["input"] is None and u["output"] is None
    assert u["cached"] is None and u["uncached"] is None


def test_empty_dict_is_unavailable_not_zero():
    """空 dict 也算"没数到"，不是 0 token。"""
    u = extract_usage({}, {})
    assert u["token_source"] == SRC_UNAVAILABLE and u["input"] is None


def test_unknown_shape_keeps_raw_head():
    """形态未知 → `unknown_shape` + 留原文前 200 字（便于排查厂商字段漂移）。"""
    u = extract_usage({"weird_field": 123, "note": "上游改字段了"}, None)
    assert u["token_source"] == SRC_UNKNOWN_SHAPE
    assert u["input"] is None and u["output"] is None            # 四个数都不写
    assert u["cached"] is None and u["uncached"] is None
    assert "weird_field" in u["raw_head"] and len(u["raw_head"]) <= 200


# --------------------------------------------------------------- ② raw 优先 / 归一化兜底

def test_raw_wins_over_normalized():
    """C2：raw 存在时以 raw 为准（归一化可能把缓存字段丢掉）。"""
    u = extract_usage(DEEPSEEK_RAW, NORMALIZED)
    assert (u["cached"], u["uncached"]) == (300, 700)          # raw 的 300/700，不是归一化的 128


def test_normalized_fallback_uses_cache_read():
    """raw 缺席时用归一化兜底（input_token_details.cache_read）。"""
    u = extract_usage(None, NORMALIZED)
    assert (u["cached"], u["uncached"], u["input"]) == (128, 872, 1000)
    assert u["token_source"] == SRC_REPORTED


def test_normalized_without_cache_read_is_no_cache_detail():
    """归一化里没有 cache_read → 同样算"缺缓存明细"。"""
    u = extract_usage(None, {"input_tokens": 500, "output_tokens": 50})
    assert u["token_source"] == SRC_NO_CACHE_DETAIL and u["uncached"] == 500


# --------------------------------------------------------------- ③ 脏输入不炸

def test_dirty_inputs_do_not_raise():
    """字符串数字能认；bool / 负数 / 非 dict / 超量缓存 都不许炸。"""
    assert extract_usage({"prompt_tokens": "1000", "completion_tokens": "200"})["input"] == 1000
    assert extract_usage({"prompt_tokens": -5, "completion_tokens": 1})["token_source"] == SRC_UNKNOWN_SHAPE
    assert extract_usage([1, 2, 3], None)["token_source"] == SRC_UNAVAILABLE
    assert extract_usage("oops", None)["token_source"] == SRC_UNAVAILABLE
    u = extract_usage({"prompt_tokens": 100, "completion_tokens": 1,
                       "prompt_tokens_details": {"cached_tokens": 999}})
    assert u["cached"] == 100 and u["uncached"] == 0            # 缓存数不可能超过总量 → 钳住


def test_input_only_or_output_only_still_parsed():
    """只有输入或只有输出也算"认得形态"，不该掉进 unknown_shape。"""
    assert extract_usage({"prompt_tokens": 10})["input"] == 10
    assert extract_usage({"completion_tokens": 3})["output"] == 3


# --------------------------------------------------------------- ④ 折算规则

def test_cost_reported_three_price_formula():
    """正常折算：命中/未命中/输出 各按自己的价算（单位：元/百万 tokens）。"""
    u = extract_usage(DEEPSEEK_RAW, None)
    cost, note, snap = compute_cost(u, PRICES)
    expect = (300 / 1e6) * 0.5 + (700 / 1e6) * 2.0 + (200 / 1e6) * 8.0
    assert cost == pytest.approx(round(expect, 6)) and note is None
    s = json.loads(snap)
    assert s["price_in_cached"] == 0.5 and s["unit"] == "CNY/1M tokens"
    assert s["tokens"] == {"cached": 300, "uncached": 700, "output": 200}


def test_cost_no_price_when_any_price_missing():
    """G5：三处单价任一留空 → 不折算（**留空 ≠ 免费**），token 照样有。"""
    u = extract_usage(DEEPSEEK_RAW, None)
    for bad in (None, {}, {"price_in_cached": 0.5, "price_in_uncached": 2.0},
                {"price_in_cached": None, "price_in_uncached": 0.0, "price_out": 0.0}):
        cost, note, snap = compute_cost(u, bad)
        assert cost is None and note == "no_price" and snap is None
    assert u["input"] == 1000                                   # token 不受影响


def test_cost_zero_prices_is_explicitly_free():
    """①-b：三处填 0 = 明确免费 → 折算成 0.0（不是 None）。"""
    u = extract_usage(DEEPSEEK_RAW, None)
    cost, note, snap = compute_cost(u, {"price_in_cached": 0, "price_in_uncached": 0, "price_out": 0})
    assert cost == 0.0 and note is None and snap is not None


def test_cost_no_cache_detail_uses_uncached_price():
    """D3：缺缓存明细 → 按未命中价折算（宁高不低）+ note 标注。"""
    u = extract_usage(PLAIN_RAW, None)
    cost, note, _ = compute_cost(u, PRICES)
    assert cost == pytest.approx(round((1000 / 1e6) * 2.0 + (200 / 1e6) * 8.0, 6))
    assert note == "no_cache_detail"


def test_cost_skipped_when_tokens_unavailable():
    """G4：没数到 token 就不给金额（note 即 token_source）。"""
    for src in (SRC_UNAVAILABLE, SRC_UNKNOWN_SHAPE):
        cost, note, snap = compute_cost({"token_source": src, "input": None}, PRICES)
        assert cost is None and note == src and snap is None


def test_cost_missing_uncached_falls_back_to_input_not_zero():
    """没有未命中数时退回输入总量 —— **绝不能按 0 算**（那会把成本算漏）。"""
    cost, note, _ = compute_cost({"token_source": SRC_REPORTED, "input": 1000, "output": 0,
                                  "cached": None, "uncached": None}, PRICES)
    assert cost == pytest.approx(round((1000 / 1e6) * 2.0, 6))


def test_cost_negative_price_is_not_priced():
    """负单价是脏数据 → 不折算（宁可显示未配置，也不给负金额）。"""
    u = extract_usage(DEEPSEEK_RAW, None)
    assert compute_cost(u, {"price_in_cached": -1, "price_in_uncached": 1, "price_out": 1})[1] == "no_price"


def test_cost_snapshot_is_reproducible():
    """折算与快照都不依赖当前时间/环境（纯函数：同样的入参 → 同样的结果）。"""
    u = extract_usage(OPENAI_RAW, None)
    a = compute_cost(u, PRICES)
    b = compute_cost(u, PRICES)
    assert a == b


# --------------------------------------------------------------- ⑤ 架构不变量

def test_module_does_not_import_api():
    """★ 铁律：`agent/` 层不许 import `api.*`（否则 build_context 那类模块会拖起整个服务）。

    手法：**AST 解析**取真实的 import，不靠字符串包含 —— 第一版用 `"import api" not in src`，
    结果被模块自己的 docstring（那句"不 import api"）判成失败 ✗，注释/文档串是会骗人的。
    """
    src = io.open(os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__)))), "agent", "token_usage.py"), encoding="utf-8").read()
    tree = ast.parse(src)
    mods = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            mods |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                mods.add(node.module.split(".")[0])
    assert "api" not in mods, "agent 层不许 import api"
    assert mods == {"json"}, "本模块应是零依赖纯函数（只 import json），实际：%s" % mods
