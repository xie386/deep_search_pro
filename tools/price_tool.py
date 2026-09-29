# -*- coding: utf-8 -*-
"""M5-3 · 「记一个价」工具（模型可见，方案 §5.4）。

与 M3 的 `update_user_profile` 同一取向：**模型只给"哪个东西 + 多少钱"，其余全交给框架** ——
按名称模糊匹配 → 命中唯一才记账；**命中多条就返回候选让模型问用户**（绝不猜）。

为什么不让模型直接给 item_id：它在对话里听到的是"米诺地尔跌到 89 了"，不知道库里的主键；
硬要它报 id，它就会编一个（M1 期 Steam appid 那次的教训）。

机械细节只写在这一处：`@tool` 文档串就是给模型的说明书（项目纪律：不许在提示词里再抄一遍）。
"""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from langchain_core.tools import tool

from api.context import get_owner_context
from api.monitor import monitor
from tools import price_display as pdisp
from tools import price_ledger as ledger
from tools.schema_personal import get_personal_conn

# ★ ⑨（拍板）：把**竞品**也纳入对话记价 —— 否则 M5-7 的"竞品价差 + 7 天节流提醒"从产品界面
#   根本走不到（曾实测：item_type=competitor_product 被直接拒绝）。
# 每项：表名 / 中文标签 / **名称列**（竞品用 comp_name，没有 product_name）。
_TABLES = {
    "product": ("products", "收藏", "product_name"),
    "company_product": ("company_products", "公司产品", "product_name"),
    "competitor_product": ("company_competitors", "竞品", "comp_name"),
}


def _rows(aid, tbl, name, name_col="product_name", limit=6):
    """按名称模糊匹配（名称或品牌包含关键词），精确/前缀优先。"""
    kw = (name or "").strip()
    conn = get_personal_conn()
    try:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(%s)" % tbl)}
        ex = lambda c: c if c in cols else "NULL"          # noqa: E731
        # ★ 别把 `'' AS brand` 直接塞进 WHERE（非法 SQL：`'' AS brand LIKE ?`）。
        #   现实表可能缺 brand/target_price/last_price 中的任意几列 → 用 COALESCE 表达式取品牌。
        where_brand = "COALESCE(brand,'')" if "brand" in cols else "''"
        ncol = name_col if name_col in cols else "product_name"
        sql = ("SELECT id, %s, %s AS brand, %s AS target_price, %s AS last_price,"
               " %s AS price_kind, %s AS price_text, %s AS price"
               " FROM %s WHERE owner_id=? AND (%s LIKE ? OR %s LIKE ?)") % (
            ncol, ex("brand"), ex("target_price"), ex("last_price"), ex("price_kind"), ex("price_text"),
            ex("price"), tbl, ncol, where_brand)
        # ★ 按 SELECT 顺序取下标构造 dict：不依赖 Row 的键名（列名/别名一变就 KeyError，
        #   2026-09-28 实测栽过一次）。
        got = []
        for row in conn.execute(sql, (int(aid), "%%%s%%" % kw, "%%%s%%" % kw)):
            got.append({"id": row[0], "product_name": row[1], "brand": row[2],
                        "target_price": row[3], "last_price": row[4], "price_kind": row[5],
                        "price_text": row[6], "price": row[7]})
    finally:
        conn.close()

    def score(r):
        n = (r.get("product_name") or "")
        b = (r.get("brand") or "")
        if n == kw:
            return 0
        if n.startswith(kw):
            return 1
        if kw and kw in n:
            return 2
        if b and kw and kw in b:
            return 3
        return 4
    got.sort(key=lambda r: (score(r), r["id"] or 0))
    return got[:limit]


def _fmt(r, tbl_label):
    # ★ 候选字典里主键叫 item_id（台账层口径），不是 id —— 2026-09-28 实测因这个 KeyError 红了 1 条用例
    bits = ["#%s %s" % (r.get("item_id", r.get("id")), r.get("name") or r.get("product_name") or "?")]
    if r.get("brand"):
        bits.append("品牌 %s" % r["brand"])
    if pdisp.price_kind_of(r) == pdisp.KIND_RULE:
        # ★ Q6：规则计价商品不显示"最近记价"，直接亮规则原文 + 标记（否则模型会当成数值价）
        bits.append("★ %s（%s）" % (pdisp.RULE_TAG, pdisp.display_price_text(r)))
    elif r.get("last_price") is not None:
        bits.append("最近记价 %s" % r["last_price"])
    if r.get("target_price") is not None:
        bits.append("目标价 %s" % r["target_price"])
    return "（%s）%s" % (tbl_label, " · ".join(bits))


def _nearest(aid, item_type, item_name, limit=3, floor=0.34):
    """0 命中时给"最相近的几条"（difflib 相似度）—— 只读、不写、不猜。"""
    import difflib
    kw = (item_name or "").strip()
    if not kw:
        return []
    tps = [item_type] if item_type in _TABLES else list(_TABLES)
    out = []
    for tp in tps:
        tbl, _label, ncol = _TABLES[tp]
        for r in _rows(aid, tbl, "", ncol, limit=200):      # 空关键词 = 取该类型全部（用于相似度比较）
            nm = r.get("product_name") or ""
            ratio = difflib.SequenceMatcher(None, kw, nm).ratio()
            if ratio >= floor:
                out.append((ratio, tp, r))
    out.sort(key=lambda x: -x[0])
    res = []
    for ratio, tp, r in out[:limit]:
        r = dict(r)
        r["_sim"] = round(ratio * 100)
        res.append(r)
    return res


def _find(aid, item_type, item_id, item_name):
    """→ (candidates, err)。candidates 里每项含 table/item_type/item_id/name。"""
    types = [item_type] if item_type in _TABLES else list(_TABLES)
    out = []
    if item_id:
        for t in types:
            tbl, label, ncol = _TABLES[t]
            conn = get_personal_conn()
            try:
                row = conn.execute("SELECT id, product_name, target_price, last_price FROM %s"
                                   " WHERE id=? AND owner_id=?" % tbl, (int(item_id), int(aid))).fetchone()
            finally:
                conn.close()
            if row:
                d = dict(row) if hasattr(row, "keys") else {}
                out.append({"item_type": t, "table": tbl, "label": label,
                            "item_id": int(d.get("id", row[0])),
                            "name": d.get("product_name", row[1]),
                            "target_price": d.get("target_price", row[2]),
                            "last_price": d.get("last_price", row[3])})
            else:
                return [], "没找到 id=%s 的%s（也确认了它不属于你）" % (item_id, label)
        return out, ""
    if not (item_name or "").strip():
        return [], "请给 item_name（产品名）或 item_id"
    for t in types:
        tbl, label, ncol = _TABLES[t]
        for r in _rows(aid, tbl, item_name, ncol):
            out.append({"item_type": t, "table": tbl, "label": label, "item_id": r["id"],
                        "name": r["product_name"], "target_price": r["target_price"],
                        "last_price": r["last_price"]})
    return out, ""


@tool
def record_price(item_type: str, price: float, item_name: str = "", item_id: int = 0,
                 note: str = "") -> str:
    """记录一个商品的价格（写进价格台账，并按规则判断要不要提醒用户）。

    什么时候用：用户在对话里说出某个**自己收藏的商品**或**公司产品**的当前价格时
    （例："米诺地尔跌到 89 了"、"我司那款贴片竞品卖 85"）。

    参数怎么填：
      · `item_type`：`product`（个人收藏）或 `company_product`（公司产品）；
      · `item_name`：用户在对话里说的商品名（**原话里的名字**，不用你去查库）——
        系统会做模糊匹配；**如果匹配到多个，会要求你再问用户是哪一个**（此时别重复调用，也别猜）；
      · `item_id`：只有你**明确知道**库里 id 时才填（一般不用填，填错会记到别的商品上）；
      · `price`：数字，单位元；
      · `note`：可选备注（如"拼多多百亿补贴"）。

    返回：记成了什么（商品名 / 本次价 / 较上次涨跌 / 命中的提醒），或候选列表 / 失败原因 ——
    **如实转述给用户**，别自己加戏。目标价与"是否创新低"由系统判定，你不需要比较。
    """
    aid = get_owner_context()
    if not aid:
        return "记录失败：当前没有账号上下文（请让用户先登录再对话）"
    monitor.report_tool(tool_name="记录价格", args={"item_type": item_type, "price": price,
                                                  "name": (item_name or "")[:20]})
    try:
        price_v = float(price)
    except (TypeError, ValueError):
        return "记录失败：price 必须是数字（收到 %r）" % (price,)
    if item_type not in _TABLES:
        return ("记录失败：item_type 只能是 product（个人收藏）、company_product（公司产品）"
                "或 competitor_product（竞品）")
    if not (price_v > 0):
        # ★ 必须在**匹配之前**校验：否则"名字不存在"会先返回，用户拿到的是"没找到"而不是"价格不对"
        return "记录失败：price 必须大于 0（收到 %s）" % price_v

    cands, err = _find(aid, item_type, item_id, item_name)
    if err:
        return "记录失败：%s" % err
    if not cands:
        # ★ ⑩（拍板）：纯子串匹配有硬边界（库里"米诺地尔泡沫剂"，用户说"米诺地尔喷雾"→ 0 命中）。
        #   这里补"最相近的 3 个"让用户挑 —— **依旧不猜**：一个字都不写进台账。
        near = _nearest(aid, item_type, item_name)
        tail = ("请让用户确认名字，或先去「定制助手 / 个人收藏」里登记这个商品。")
        if near:
            tail += "\n最相近的几条（**让用户确认是不是其中之一，不要自己选**）：\n" + "\n".join(
                "  " + _fmt(x, _TABLES[item_type][1]) for x in near)
        return "没找到叫「%s」的%s。%s" % (item_name, _TABLES[item_type][1], tail)
    if len(cands) > 1:
        lines = ["匹配到多个%s，**先问用户是哪一个**，别自己挑：" % _TABLES[item_type][1]]
        lines += ["  " + _fmt(c, c["label"]) for c in cands]
        return "\n".join(lines)

    c = cands[0]
    try:
        r = ledger.record_price(aid, c["item_type"], c["item_id"], price_v,
                                source_type="manual", source_ref="agent", note=note or "")
    except ValueError as e:
        return "记录失败：%s" % e
    if r.get("deduped"):
        return "「%s」今天的这个价格（%s 元）已经记过了，没有重复记账。" % (c["name"], price_v)
    bits = ["已记价：「%s」（%s）%s 元" % (c["name"], c["label"], price_v)]
    _prev, _base = r.get("prev_price"), r.get("delta_base")
    if _prev is not None:
        # ⑦：把"上次多少 → 本次多少"直接给模型，压掉"凭空说出上一价"的幻觉面
        bits.append("上次 %s 元 → 本次 %s 元（%+.1f%%）" % (_prev, price_v, r.get("delta_pct") or 0.0))
    elif r.get("delta_base_src") == "registered" and _base is not None:
        # ③⑥：首次记价也有基准了 —— 拿"登记价"当基准，并**明说**是登记价（不是历史成交）
        bits.append("较登记价 %s 元 → 本次 %s 元（%+.1f%%）" % (_base, price_v, r.get("delta_pct") or 0.0))
    else:
        # ⑥：连登记价都没有 → 明确告诉模型"没有历史可比"，别顺着用户的话说"降了"
        bits.append("首次记录该商品价格，**暂无历史可比**")
    if c.get("target_price") is not None:
        bits.append("目标价 %s" % c["target_price"])
    if r.get("alert") == ledger.RULE_BELOW_TARGET:
        bits.append("★ 已跌破目标价，已为用户生成提醒")
    elif r.get("alert") == ledger.RULE_NEW_LOW:
        bits.append("★ 创历史新低，已为用户生成提醒")
    return "；".join(bits)
