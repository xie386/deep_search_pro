# -*- coding: utf-8 -*-
"""M6c-2：token 用量解析与金额折算（**纯函数、零依赖、不 import api.***）。

为什么单独一个模块：LangChain 的 usage 有**四种形态**，而"数错了"和"折算错了"是两类
不同的错账，必须能各自单测。这里只做两件事：

    extract_usage(raw, normalized) -> {input, output, cached, uncached, token_source}
    compute_cost(tok, prices)      -> (cost, cost_note, price_snapshot)

────────────────────────────────────────────────────────────────────
★ 三条来自方案的硬口径（M6c 方案 §3.1 / §3.2 / §0.1）：

1. **raw 优先于 normalized**（C2）。LangChain 只把 `prompt_tokens_details.cached_tokens`
   映射成归一化的 `input_token_details.cache_read`；而 **DeepSeek 用的是顶层
   `prompt_cache_hit_tokens` / `prompt_cache_miss_tokens`** —— 归一化字段**拿不到**，
   所以必须读 raw `response_metadata["token_usage"]`，归一化只作兜底。

2. **缺缓存明细时按"未命中"折算**（D3）。拿不到拆分并不意味着便宜：全记成未命中
   （`no_cache_detail`），宁高不低。**绝不按命中折算**（那会低估成本）。

3. **没有 usage 就不编造**（D4）。`unavailable` / `unknown_shape` 一律**四个数都不写**
   （不是写 0 —— 0 与"没数到"是两件事），也不折算，由 UI 显示「—」。
────────────────────────────────────────────────────────────────────
"""
import json

# 形态常量（同时也是 `usage_events.token_source` 的取值域）
SRC_REPORTED = "reported"                # 上游给了完整拆分（含缓存命中/未命中）
SRC_NO_CACHE_DETAIL = "no_cache_detail"  # 有总量、无缓存拆分 → 全按未命中折算
SRC_UNAVAILABLE = "unavailable"          # 上游压根没返回 usage
SRC_UNKNOWN_SHAPE = "unknown_shape"      # 返回了 usage，但字段形态不认识（厂商漂移）

# 折算需要用到的三处单价（单位固定：元 / 百万 tokens）
PRICE_KEYS = ("price_in_cached", "price_in_uncached", "price_out")

# 不折算的两种 token_source（D4/G4：没有可靠数字就不给金额）
_NO_COST_SOURCES = (SRC_UNAVAILABLE, SRC_UNKNOWN_SHAPE)


def _i(v):
    """温和转非负整数：拿不到 / 非法 / 负数 → None（不抛异常，不让解析拖垮记价）。"""
    if v is None or isinstance(v, bool):
        return None
    try:
        n = int(float(v))
    except (TypeError, ValueError):
        return None
    return n if n >= 0 else None


def _head(d):
    """形态未知时留一份原文前 200 字（方案要求：便于排查厂商字段漂移）。"""
    try:
        return json.dumps(d, ensure_ascii=False)[:200]
    except Exception:  # noqa: BLE001
        return str(d)[:200]


def _empty(source, raw_head=None):
    """四个数都不写（**不是 0**）：没见过就是没见过。"""
    out = {"input": None, "output": None, "cached": None, "uncached": None, "token_source": source}
    if raw_head is not None:
        out["raw_head"] = raw_head
    return out


def extract_usage(raw=None, normalized=None):
    """从 raw `response_metadata["token_usage"]` / 归一化 `usage_metadata` 解析四个数。

    返回 ``{"input":int|None, "output":int|None, "cached":int|None,
    "uncached":int|None, "token_source":str}``；形态未知时多一个 `raw_head`。

    四形态：
      ① DeepSeek：prompt_tokens / completion_tokens / prompt_cache_hit_tokens / prompt_cache_miss_tokens
      ② OpenAI ：prompt_tokens / completion_tokens / prompt_tokens_details.cached_tokens
      ③ 只有总量：有 prompt/completion、无缓存拆分 → cached=0，uncached=input
      ④ 无 usage：token_source=unavailable，四个数都不写
      兜底：形态不认识 → unknown_shape（同样不写数、不折算）
    """
    raw = raw if isinstance(raw, dict) else None
    normalized = normalized if isinstance(normalized, dict) else None

    # ---------------- ① 优先 raw（C2） ----------------
    if raw:
        inp = _i(raw.get("prompt_tokens"))
        if inp is None:
            inp = _i(raw.get("input_tokens"))
        out = _i(raw.get("completion_tokens"))
        if out is None:
            out = _i(raw.get("output_tokens"))
        # 字段**在但值不可用**（负数 / 非数字）＝ 脏数据 → 按形态未知处理：
        #   宁可显示「—」，也不要把负输入静默算成 0（那会把成本算漏）。
        dirty = (raw.get("prompt_tokens") is not None and inp is None) or (
            raw.get("completion_tokens") is not None and out is None)
        if dirty or (inp is None and out is None):
            return _empty(SRC_UNKNOWN_SHAPE, _head(raw))
        hit = _i(raw.get("prompt_cache_hit_tokens"))
        miss = _i(raw.get("prompt_cache_miss_tokens"))
        det = raw.get("prompt_tokens_details")
        det_cached = _i(det.get("cached_tokens")) if isinstance(det, dict) else None

        if hit is not None or miss is not None:
            # 形态①：DeepSeek 顶层命中/未命中。只有命中时用「总量 − 命中」补未命中。
            cached = hit if hit is not None else 0
            uncached = miss if miss is not None else max(0, (inp or 0) - cached)
            return {"input": inp, "output": out, "cached": cached, "uncached": uncached,
                    "token_source": SRC_REPORTED}
        if det_cached is not None:
            # 形态②：OpenAI 的 details.cached_tokens（不可能超过总量，钳一下）
            cached = min(det_cached, inp) if inp is not None else det_cached
            return {"input": inp, "output": out, "cached": cached,
                    "uncached": max(0, (inp or 0) - cached), "token_source": SRC_REPORTED}
        # 形态③：有总量无拆分 → 全按未命中（D3：宁高不低）
        return {"input": inp, "output": out, "cached": 0, "uncached": inp or 0,
                "token_source": SRC_NO_CACHE_DETAIL}

    # ---------------- ② raw 没有，用归一化兜底 ----------------
    if normalized:
        inp = _i(normalized.get("input_tokens"))
        out = _i(normalized.get("output_tokens"))
        if inp is None and out is None:
            return _empty(SRC_UNKNOWN_SHAPE, _head(normalized))
        det = normalized.get("input_token_details")
        cache_read = _i(det.get("cache_read")) if isinstance(det, dict) else None
        if cache_read is not None:
            cached = min(cache_read, inp) if inp is not None else cache_read
            return {"input": inp, "output": out, "cached": cached,
                    "uncached": max(0, (inp or 0) - cached), "token_source": SRC_REPORTED}
        return {"input": inp, "output": out, "cached": 0, "uncached": inp or 0,
                "token_source": SRC_NO_CACHE_DETAIL}

    # ---------------- ④ 什么都没有 ----------------
    return _empty(SRC_UNAVAILABLE)


def compute_cost(tok, prices=None):
    """折算金额。返回 ``(cost, cost_note, price_snapshot)``。

    规则（方案 §3.2）：
      · `unavailable` / `unknown_shape` → **不折算**，note 即该 token_source（D4/G4）
      · 三处单价**任一为 NULL / 缺失** → **不折算**，note=`no_price`（G5：留空 ≠ 免费）
      · 三处单价**填 0** → 是"明确免费"，折算为 **0.0**（note 为空）
      · `no_cache_detail` → 按未命中价折算 + note=`no_cache_detail`（D3）
      · 成功折算 → note 为 None；`price_snapshot` 是可追溯的单价 JSON（C3/G6）
    """
    tok = tok if isinstance(tok, dict) else {}
    src = tok.get("token_source") or SRC_UNAVAILABLE
    if src in _NO_COST_SOURCES:
        return None, src, None

    if not isinstance(prices, dict):
        return None, "no_price", None
    for k in PRICE_KEYS:
        if prices.get(k) is None:            # 任一留空 → 不折算（不是"按 0 算"）
            return None, "no_price", None
    try:
        p_cached = float(prices["price_in_cached"])
        p_uncached = float(prices["price_in_uncached"])
        p_out = float(prices["price_out"])
    except (TypeError, ValueError):
        return None, "no_price", None
    if min(p_cached, p_uncached, p_out) < 0:   # 负数单价是脏数据，宁可不折算
        return None, "no_price", None

    cached = tok.get("cached")
    cached = 0 if cached is None else int(cached)
    uncached = tok.get("uncached")
    if uncached is None:                       # 没有未命中数时退回输入总量，绝不按 0 算
        uncached = tok.get("input")
    uncached = 0 if uncached is None else int(uncached)
    out = tok.get("output")
    out = 0 if out is None else int(out)

    cost = (cached / 1e6) * p_cached + (uncached / 1e6) * p_uncached + (out / 1e6) * p_out
    note = "no_cache_detail" if src == SRC_NO_CACHE_DETAIL else None
    snapshot = json.dumps({"price_in_cached": p_cached, "price_in_uncached": p_uncached,
                           "price_out": p_out, "unit": "CNY/1M tokens",
                           "tokens": {"cached": cached, "uncached": uncached, "output": out}},
                          ensure_ascii=False)
    return round(cost, 6), note, snapshot
