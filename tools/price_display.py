# -*- coding: utf-8 -*-
"""Q6 · **价格显示的唯一口径**（复杂价格规则，2026-09-29 拍板）。

为什么必须有它：本会话已经吃过"同一字段两套口径"的亏（**登记价 `price` vs 台账价
`last_price`** —— 用户看到"库里还是老价格"就是这么来的）。复杂价引入后，若每个调用点各写
一次 `if kind == 'rule'`，漏一处就是"把规则文本当数字"或反之的静默错误。

**显式三态**：
  · `price_kind='simple'` + `price` 有值  → 经典数值价
  · `price_kind='rule'`  + `price_text`   → 规则文本价（API 按量计费、租房阶梯价…）
  · `price_kind` 为 NULL + 两者皆空       → **未定价**

**数值链路必须跳过 `rule`**：台账记价、涨跌、目标价、跌幅提醒、竞品价差、历史新低 ——
对规则计价商品**都不适用**，用 `is_numeric_priced()` 显式挡住，不要对文本做 float。
"""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

KIND_SIMPLE = "simple"
KIND_RULE = "rule"
RULE_TAG = "按规则计价"
UNPRICED_TEXT = "未定价"


def _g(row, key, default=None):
    """兼容 sqlite3.Row / dict / None。"""
    if row is None:
        return default
    try:
        if hasattr(row, "keys"):
            return row[key] if key in row.keys() else default
        return row.get(key, default)
    except Exception:
        return default


def price_kind_of(row) -> str:
    """`'simple'` / `'rule'` / `''`（未定价）。"""
    k = (_g(row, "price_kind") or "").strip()
    if k in (KIND_SIMPLE, KIND_RULE):
        return k
    return KIND_SIMPLE if _g(row, "price") is not None else ""


def numeric_price(row):
    """取数值价；`rule` 或未定价一律 None（**不要**对规则文本做 float）。"""
    if price_kind_of(row) != KIND_SIMPLE:
        return None
    v = _g(row, "price")
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def is_numeric_priced(row) -> bool:
    """数值链路（台账/涨跌/目标价/价差/新低）能不能处理这个对象。"""
    return numeric_price(row) is not None


def display_price(row, currency: str = "") -> dict:
    """前端 / 报告 / 工具返回统一走这里 → `{kind, text, numeric, show_numeric}`。"""
    kind = price_kind_of(row)
    if kind == KIND_RULE:
        txt = (_g(row, "price_text") or "").strip() or "（按规则计价，但还没填写规则）"
        return {"kind": KIND_RULE, "text": txt, "numeric": None, "show_numeric": False}
    num = numeric_price(row)
    if num is None:
        return {"kind": "", "text": UNPRICED_TEXT, "numeric": None, "show_numeric": False}
    cur = (_g(row, "currency") or currency or "CNY")
    unit = "元" if str(cur).upper() in ("CNY", "RMB") else str(cur)
    return {"kind": KIND_SIMPLE, "text": "%s %s" % (num, unit), "numeric": num, "show_numeric": True}


def display_price_text(row, currency: str = "") -> str:
    return display_price(row, currency)["text"]


def rule_refusal_text(row) -> str:
    """对规则计价商品的**人话**回答（工具层直接回这句给模型）。"""
    return ("该商品是%s的（%s），没有单一数值价，无法比较涨跌。"
            "要我记一个**具体渠道**的价吗（例如某平台某天的实际成交价）？"
            % (RULE_TAG, display_price_text(row)))


def skip_numeric_reason(row) -> str:
    kind = price_kind_of(row)
    if kind == KIND_RULE:
        return "%s（%s）：不参与台账/涨跌/目标价/价差" % (RULE_TAG, display_price_text(row))
    if kind == "":
        return "未定价：没有可比较的数值价"
    return ""
