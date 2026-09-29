# -*- coding: utf-8 -*-
"""M5-5 · 周报「价格情报」的结构化抽取（方案 §3.4 / C5 / D6 / G6）。

**不额外调模型**：周报本来就是一次 LLM 轮次，这里只是让它在同一轮里**多写一个结构化块**，
代码层解析落库 —— 所以本模块**不许出现任何模型调用**（用例里有静态断言钉死这条）。

为什么必须与 `price_history` 分表（C1/G4）：**资讯里的价格 ≠ 当前售价**。
一条新闻说"某款跌到 89"，那是**报道口径**（可能过期、可能是促销价、可能不是同一个 SKU）；
把它当"当前价"展示或拿去触发目标价提醒，就是误导用户。所以：
  · 资讯价只进 `price_mentions`，带 `source_url` 与 `price_text` 原文；
  · 实体匹配只写"疑似关联"（`linked_item_*` + `match_score`），**不参与**提醒判定。

行契约（与 `SCAN_LINE_CONTRACT` 同风格：常量 → 提示词 → 解析正则**三处必须同步**）：
    价格情报：实体=<名称> | 价格=<数字> | 币种=<CNY> | 渠道=<来源> | URL=<链接>
字段用 key=value 而不是位置对位：模型少写一个渠道也不至于整行错位。
"""
import re
import time

from tools.schema_personal import get_personal_conn

# ★ 单一来源：改这里 ⇒ 必须同步改 prompts.yml 的 digest 段与 PRICE_LINE_RE
PRICE_LINE_CONTRACT = "价格情报：实体=<名称> | 价格=<数字> | 币种=<币种> | 渠道=<来源> | URL=<链接>"
PRICE_LINE_SAMPLE = "价格情报：实体=米诺地尔泡沫剂 | 价格=89 | 币种=CNY | 渠道=拼多多百亿补贴 | URL=https://example.com/p/1"
PRICE_MARK = "价格情报："
PRICE_LINE_RE = re.compile(r"价格情报\s*[:：]\s*(?P<body>.+)$")

_MISSING_OK = ("币种", "渠道", "URL")          # 这三个可以缺
_MUST = ("实体", "价格")

if not PRICE_LINE_RE.search(PRICE_LINE_SAMPLE):
    print("[price_mentions][契约告警] 行契约与解析正则不一致 —— 请同步 PRICE_LINE_CONTRACT/PRICE_LINE_RE")

_NUM_RE = re.compile(r"[-+]?\d+(?:\.\d+)?")


def _clean(s: str) -> str:
    return re.sub(r"\*\*?", "", (s or "").replace("**", "")).strip().strip("|").strip()


def parse_price_mentions(text: str) -> list:
    """从周报正文里抠出价格情报行 → `[{entity, price, price_text, currency, shop, source_url}]`。

    容错优先（G6）：加粗星号、全角冒号、缺字段、价格带货币符号 —— 都不该让整块解析失败，
    但**实体与价格缺任何一个就跳过该行**（宁可少一条，也不猜）。
    """
    out = []
    for raw in (text or "").replace("\r\n", "\n").split("\n"):
        m = PRICE_LINE_RE.search(_clean(raw))
        if not m:
            continue
        body = m.group("body")
        kv = {}
        for part in re.split(r"[|｜]", body):
            k, _, v = part.partition("=")
            if not _:
                k, _, v = part.partition("：")
            if k.strip():
                kv[_clean(k)] = _clean(v)
        entity = kv.get("实体", "")
        num = _NUM_RE.search(kv.get("价格", ""))
        if not entity or not num:
            continue                    # ★ 缺实体或缺价格 → 跳过，不猜
        out.append({"entity": entity, "price": float(num.group(0)), "price_text": kv.get("价格", ""),
                    "currency": kv.get("币种", "") or "CNY", "shop": kv.get("渠道", ""),
                    "source_url": kv.get("URL", "") or kv.get("url", "")})
    return out


def match_entity(account_id: int, entity: str) -> dict:
    """把资讯里的实体名对到用户自己的东西上 → `{linked_item_type, linked_item_id, match_score}`。

    只做"疑似关联"标记（G4）：名称完全一致 1.0 / 名称包含 0.7 / 品牌包含 0.5；对不上就是 0 且不链接。
    **绝不参与提醒判定** —— 资讯价不是当前价。
    """
    name = (entity or "").strip()
    if not name:
        return {"linked_item_type": "", "linked_item_id": None, "match_score": 0.0}
    tables = (("product", "products"), ("company_product", "company_products"),
              ("competitor_product", "company_competitors"))
    best = ("", None, 0.0)
    conn = get_personal_conn()
    try:
        for label, tbl in tables:
            cols = {r[1] for r in conn.execute("PRAGMA table_info(%s)" % tbl)}
            ncol = "product_name" if "product_name" in cols else "comp_name"
            bcol = "brand" if "brand" in cols else ("website" if "website" in cols else "''")
            rows = conn.execute("SELECT id, %s, %s FROM %s WHERE owner_id=?" % (ncol, bcol, tbl),
                                (int(account_id),))
            for r in rows:
                pid, pname, brand = r[0], (r[1] or ""), (r[2] or "")
                score = 0.0
                if pname and pname == name:
                    score = 1.0
                elif pname and (name in pname or pname in name):
                    score = 0.7
                elif brand and name and name in brand:
                    score = 0.5
                if score > best[2]:
                    best = (label, int(pid), score)
    finally:
        conn.close()
    return {"linked_item_type": best[0], "linked_item_id": best[1], "match_score": best[2]}


def collect_price_mentions(account_id: int, report_id, text: str) -> dict:
    """周报正文 → `price_mentions` 落库。**任何异常都不抛**（抽取失败不该让周报生成失败）。

    去重：（report_id, entity, price）已存在就不再插 —— 同一份周报被重复解析（重试）不该翻倍。
    """
    try:
        got = parse_price_mentions(text)
    except Exception as e:
        print("[price_mentions] 解析失败（忽略）:", e)
        return {"ok": False, "inserted": 0, "parsed": 0, "error": str(e)}
    if not got:
        return {"ok": True, "inserted": 0, "parsed": 0}
    seen_at = time.strftime("%Y-%m-%d %H:%M:%S")
    inserted = 0
    conn = get_personal_conn()
    try:
        for m in got:
            dup = conn.execute("SELECT id FROM price_mentions WHERE account_id=? AND report_id IS ?"
                               " AND entity=? AND price=?", (int(account_id), report_id,
                                                             m["entity"], m["price"])).fetchone()
            if dup:
                continue
            link = match_entity(account_id, m["entity"])
            conn.execute("INSERT INTO price_mentions (account_id, report_id, entity, price, price_text,"
                         " currency, shop, source_url, seen_at, linked_item_type, linked_item_id, match_score)"
                         " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                         (int(account_id), report_id, m["entity"], m["price"], m["price_text"],
                          m["currency"], m["shop"], m["source_url"], seen_at,
                          link["linked_item_type"], link["linked_item_id"], link["match_score"]))
            inserted += 1
        conn.commit()
    except Exception as e:
        print("[price_mentions] 落库失败（忽略）:", e)
        return {"ok": False, "inserted": inserted, "parsed": len(got), "error": str(e)}
    finally:
        conn.close()
    return {"ok": True, "inserted": inserted, "parsed": len(got)}


def list_price_mentions(account_id: int, limit: int = 50) -> list:
    """给前端/周报用：资讯价列表（**必须标注是新闻价**，不能当当前价展示）。"""
    conn = get_personal_conn()
    try:
        return [dict(r) for r in conn.execute(
            "SELECT id, entity, price, price_text, currency, shop, source_url, seen_at,"
            " linked_item_type, linked_item_id, match_score FROM price_mentions WHERE account_id=?"
            " ORDER BY id DESC LIMIT ?", (int(account_id), int(limit)))]
    finally:
        conn.close()
