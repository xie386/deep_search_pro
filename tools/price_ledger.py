# -*- coding: utf-8 -*-
"""M5-2 · 价格台账的**唯一事实源**（方案 §5.3）。

一件事：**把"记一个价"变成"写一行台账 + 按规则决定要不要提醒"**，读改写/去重/比较/溯源全在这里，
模型与前端都只给四元组（C4：与 M3 的 `update_user_profile` 同一取向）。

三条硬规则（都来自方案的裁决，别在别处重新实现）：
  · **来源可分**（C1/G4）：`source_type ∈ manual | api`；资讯价走 `price_mentions`，**永不入台账**
    （DB 层有 CHECK 兜着）；
  · **去重**（G2）：同 (对象, 同日, 同价) 不新增行，返回既有行 id；
  · **提醒去重**（D5/G3）：个人只看两种触发 —— 「**首次跌破目标价**」与「**创历史新低**」；
    未创新低不重复提醒。**首条记录不算创新低**（否则一记价就报警，等于噪声）。
"""
import re
import time

from tools import price_display as pdisp
from tools.schema_personal import get_personal_conn

ITEM_TYPES = ("product", "company_product", "competitor_product")
SOURCE_TYPES = ("manual", "api")            # ★ news 只进 price_mentions

RULE_NEW_LOW = "new_low"
RULE_BELOW_TARGET = "below_target"
RULE_COMPETITOR_GAP = "competitor_gap"      # M5-7 用
RULE_CHECK_ERROR = "check_error"            # M5-8 用（G8：源失败不静默）
RULE_DROP_PCT = "drop_pct"            # ②（拍板）：单次跌幅超该商品阈值

# 台账对象 → 冗余缓存 last_price 所在的表（竞品没有对应表，不更新）
_LAST_PRICE_TABLE = {"product": "products", "company_product": "company_products"}


def _today() -> str:
    return time.strftime("%Y-%m-%d")


def _fk(row, key=None):
    """取一行里的值。

    ★ 聚合列（`MIN(price)` / `COUNT(*)`）在 Row 里**没有**普通列名，用 `row[key]` 会 IndexError ——
      2026-09-28 实测就栽在这里（13 条用例一起红）。取不到就回退下标 0。
    """
    if row is None:
        return None
    if hasattr(row, "keys") and key:
        try:
            return row[key]
        except (IndexError, KeyError):
            pass
    return row[0]


def _target_of(cur, account_id: int, item_type: str, item_id: int):
    """取目标价（个人收藏 / 公司产品）。竞品的目标价取"它映射到的自家产品"的在 M5-7 处理。"""
    tbl = _LAST_PRICE_TABLE.get(item_type)
    if not tbl or not item_id:
        return None
    if _is_rule_priced(cur, account_id, item_type, item_id):
        return None                     # ★ Q6：规则计价商品没有"目标价跌破"这回事
    try:
        row = cur.execute("SELECT target_price FROM %s WHERE id=? AND (owner_id=? OR owner_id IS NULL)"
                          % tbl, (int(item_id), int(account_id))).fetchone()
    except Exception:
        return None
    return None if not row else _fk(row, "target_price")



def _drop_pct_of(cur, account_id: int, item_type: str, item_id: int):
    """② 取该商品的"跌幅提醒阈值 %"（`alert_drop_pct`；NULL/规则计价 → None = 不启用）。"""
    tbl = _LAST_PRICE_TABLE.get(item_type)
    if not tbl or not item_id:
        return None
    if _is_rule_priced(cur, account_id, item_type, item_id):
        return None                     # 规则计价商品不参与任何数值规则
    try:
        row = cur.execute("SELECT alert_drop_pct FROM %s WHERE id=? AND (owner_id=? OR owner_id IS NULL)"
                          % tbl, (int(item_id), int(account_id))).fetchone()
    except Exception:
        return None
    if not row or _fk(row) is None:
        return None
    try:
        v = float(_fk(row))
    except (TypeError, ValueError):
        return None
    return v if v > 0 else None

def _item_row(cur, account_id, item_type, item_id):
    """取"用户登记的那个对象"整行（Q6 判复杂价用）。没有对应表/不属于本人 → None。"""
    tbl = _LAST_PRICE_TABLE.get(item_type)
    if not tbl or not item_id:
        return None
    try:
        return cur.execute("SELECT * FROM %s WHERE id=? AND (owner_id=? OR owner_id IS NULL)"
                           % tbl, (int(item_id), int(account_id))).fetchone()
    except Exception:
        return None


def _is_rule_priced(cur, account_id, item_type, item_id) -> bool:
    """★ **只**判 `price_kind='rule'` —— 未定价（price/price_kind 皆空）的商品**照样能记价**
    （那正是最常用的场景：刚登记、还没价）。"""
    row = _item_row(cur, account_id, item_type, item_id)
    return pdisp.price_kind_of(row) == pdisp.KIND_RULE


def _prev_row(cur, account_id, item_type, item_id, before_id=None):
    sql = ("SELECT id, price, observed_at FROM price_history WHERE account_id=? AND item_type=?"
           " AND item_id IS ? ")
    args = [int(account_id), item_type, item_id]
    if before_id:
        sql += " AND id < ?"
        args.append(int(before_id))
    sql += " ORDER BY id DESC LIMIT 1"
    return cur.execute(sql, tuple(args)).fetchone()


def _log_alert(cur, account_id, item_type, item_id, rule, price, detail) -> int:
    cur.execute("INSERT INTO price_alerts (account_id, item_type, item_id, rule, price, detail, fired_at)"
                " VALUES (?,?,?,?,?,?,?)",
                (int(account_id), item_type, item_id, rule, float(price), detail,
                 time.strftime("%Y-%m-%d %H:%M:%S")))
    return int(cur.lastrowid)


def record_price(account_id: int, item_type: str, item_id, price, source_type: str = "manual",
                 source_ref: str = "", note: str = "", observed_at: str = "") -> dict:
    """记一个价 → `{ok, row_id, delta_pct, alert, deduped}`。异常输入抛 ValueError（工具层负责转成友好提示）。"""
    if item_type not in ITEM_TYPES:
        raise ValueError("item_type 必须是 %s 之一" % (ITEM_TYPES,))
    if source_type not in SOURCE_TYPES:
        raise ValueError("source_type 必须是 %s 之一（资讯价请走 price_mentions）" % (SOURCE_TYPES,))
    try:
        price = float(price)
    except (TypeError, ValueError):
        raise ValueError("price 必须是数字")
    if not (price > 0):
        raise ValueError("price 必须大于 0")
    if not account_id:
        raise ValueError("缺少 account_id")

    if item_id:
        conn0 = get_personal_conn()
        try:
            if _is_rule_priced(conn0.cursor(), account_id, item_type, item_id):
                _r = _item_row(conn0.cursor(), account_id, item_type, item_id)
                raise ValueError(pdisp.rule_refusal_text(_r))
        finally:
            conn0.close()

    obs = (observed_at or _today()).strip()
    day = obs[:10]
    conn = get_personal_conn()
    try:
        cur = conn.cursor()
        # ① 去重：同对象 + 同日 + 同价 → 不新增
        dup = cur.execute(
            "SELECT id FROM price_history WHERE account_id=? AND item_type=? AND item_id IS ?"
            " AND price=? AND substr(observed_at,1,10)=?", 
            (int(account_id), item_type, item_id, price, day)).fetchone()
        if dup:
            row_id = int(_fk(dup, "id"))
            return {"ok": True, "row_id": row_id, "delta_pct": 0.0, "alert": "",
                    "deduped": True, "reason": "同一天同一价格，不重复记账"}

        # ② 写台账 + 更新冗余 last_price
        prev = _prev_row(cur, account_id, item_type, item_id)
        # ★ ③（拍板）基准三级回退：台账上一条 → 该对象的 `last_price` → 登记价 `price`
        #   为什么：用户"刚登记一个商品、第一次报个价"是最常见场景，只查台账会让首次记价毫无基准。
        #   ★ 必须在**写台账之前**取值 —— 写完 last_price 就成新价了，等于拿自己当基准（已踩过一次）。
        prev_price = float(_fk(prev, "price")) if prev else None
        baseline, base_src = prev_price, "ledger" if prev else ""
        if baseline is None and item_id:
            _row = _item_row(cur, account_id, item_type, item_id)
            if _row is not None:
                _v = _fk(_row, "last_price")
                if _v is None and pdisp.price_kind_of(_row) == pdisp.KIND_SIMPLE:
                    _v = _fk(_row, "price")
                if _v is not None:
                    baseline, base_src = float(_v), "registered"
        cur.execute("INSERT INTO price_history (account_id, item_type, item_id, price, currency,"
                    " source_type, source_ref, observed_at, note) VALUES (?,?,?,?,?,?,?,?,?)",
                    (int(account_id), item_type, item_id, price, "CNY", source_type,
                     source_ref or "", obs, note or ""))
        row_id = int(cur.lastrowid)
        tbl = _LAST_PRICE_TABLE.get(item_type)
        if tbl and item_id:
            try:
                cur.execute("UPDATE %s SET last_price=? WHERE id=? AND (owner_id=? OR owner_id IS NULL)"
                            % tbl, (price, int(item_id), int(account_id)))
            except Exception:
                pass                       # 冗余缓存失败不该让记价失败

        # ③ 判提醒（D5：只两种触发）
        delta_pct = round((price - baseline) / baseline * 100, 2) if baseline else 0.0
        alert = ""
        pre_min = cur.execute("SELECT MIN(price) FROM price_history WHERE account_id=? AND item_type=?"
                              " AND item_id IS ? AND id < ?",
                              (int(account_id), item_type, item_id, row_id)).fetchone()
        prev_min = _fk(pre_min, "price") if pre_min else None
        target = _target_of(cur, account_id, item_type, item_id)

        # 创历史新低（★ 首条记录不算：没有可比的历史就没有"新低"）
        # ★ 判据 = 「**低于台账里的历史最低价**」（不是"比上一个价低"——用户拍板：商品多数时间上下浮动，
        #   只有"跌破历史最低"才配叫新低）。文案里把"此前最低"写出来，避免被误读成"比上次低"。
        if prev_min is not None and price < float(prev_min):
            _log_alert(cur, account_id, item_type, item_id, RULE_NEW_LOW, price,
                       "创历史新低：%s 元（此前最低 %s 元）" % (price, prev_min))
            alert = RULE_NEW_LOW
        # ★ ①（拍板选 A）首次跌破目标价：判据 =「**上一次记价在目标价哪一侧**」
        #   只看相邻两次是否跨界，不再问"历史上有没有更低的记录" —— 否则用户给一个有历史的商品
        #   **新设**目标价时，老数据会把"首次跌破"吃掉，导致该目标价**永远不提醒**（静默死路）。
        #   没有上一条时（首条记录）也算"跌破"：用户设了目标价、报了一个低于它的价，这就是有意义的事件。
        if target is not None and price <= float(target):
            if prev_price is None or prev_price > float(target):
                _log_alert(cur, account_id, item_type, item_id, RULE_BELOW_TARGET, price,
                           "首次跌破目标价 %s（%s → %s）"
                           % (target, "无历史" if prev_price is None else prev_price, price))
                alert = alert or RULE_BELOW_TARGET
        # ② 跌幅提醒（拍板：三条规则各自独立落库；返回字段只报**第一条**，顺序 new_low → below_target
        #   → drop_pct → competitor_gap）。★ 语义：**必须有一次"前一次观测"**（prev_price 来自台账），
        #   因为"跌幅"是两个观测之间的差；登记价只是配置，不构成"跌了"这个事件（那是 ⑥ 的"较登记价"文案）。
        drop_limit = _drop_pct_of(cur, account_id, item_type, item_id)
        if prev_price and drop_limit is not None:
            step = (price - prev_price) / prev_price * 100
            if step <= -abs(drop_limit):
                _log_alert(cur, account_id, item_type, item_id, RULE_DROP_PCT, price,
                           "较上次下跌 %.1f%%（超过设定阈值 %.1f%%：%s → %s 元）"
                           % (abs(step), drop_limit, prev_price, price))
                alert = alert or RULE_DROP_PCT
        # ④ 竞品价差（M5-7）：竞品价只跟"它映射到的自家产品"比，超阈值才提醒（D5：7 天最多一次）
        if item_type == "competitor_product":
            gap = _maybe_gap_alert(cur, account_id, item_id, price)
            alert = alert or gap
        conn.commit()
        # ★ 一次记价可能同时触发多条规则（例：竞品价既创新低、又拉大价差）——
        #   返回里的 `alert` 只报**第一条**（个人价规则优先），其余照样落进 price_alerts，别以为漏了。
        return {"ok": True, "row_id": row_id, "delta_pct": delta_pct, "alert": alert,
                "deduped": False, "observed_at": obs,
                # ⑦：把"上次多少"一起回给工具层（模型就不用猜/编上一价了）
                "prev_price": prev_price, "delta_base": baseline, "delta_base_src": base_src}
    finally:
        conn.close()


def pending_alerts(account_id: int, only_unacked: bool = True) -> list:
    """待通知的提醒（桌面壳取走 → 弹通知 → 回写 acked_at，避免重复弹）。"""
    sql = ("SELECT id, item_type, item_id, rule, price, detail, fired_at, acked_at FROM price_alerts"
           " WHERE account_id=?")
    if only_unacked:
        sql += " AND acked_at IS NULL"
    sql += " ORDER BY id DESC LIMIT 50"
    conn = get_personal_conn()
    try:
        return [dict(r) for r in conn.execute(sql, (int(account_id),))]
    finally:
        conn.close()


def ack_alert(alert_id: int, account_id: int) -> dict:
    """回执（幂等：已 ack 再 ack 不报错，也不改时间）。"""
    conn = get_personal_conn()
    try:
        cur = conn.cursor()
        cur.execute("UPDATE price_alerts SET acked_at=? WHERE id=? AND account_id=? AND acked_at IS NULL",
                    (time.strftime("%Y-%m-%d %H:%M:%S"), int(alert_id), int(account_id)))
        n = cur.rowcount
        conn.commit()
        return {"ok": True, "acked": int(n)}
    finally:
        conn.close()


def digest_appendix(account_id: int, mark: bool = False, days: int = 7) -> str:
    """Q5 A/E（拍板）：周报里的**价格板块以台账为本**，并给"台账价 vs 报道价"并排对照。

    用户口径（原话要点）：价格变化的依据应是**台账变化**；网搜给的是**多渠道/国际均价**，
    与"我在美团实际买的价"不是一回事，所以网搜只做**新闻报道里的价格（区间/渠道）**。

    · 数据来源 = **用户自己记的价**（对话 / 前端「更新价格」）→ 组内首次→末次 = 本期变化
    · 提醒（新低 / 首次跌破目标价 / 竞品价差）由台账判定，这里只做呈现
    · **并排对照**：若某对象在报道里也有价（`price_mentions.linked_item_id` 命中）→ 并列写出，
      让用户一眼看到"我的价 vs 报道价"的差距（真露烧酒 10.9 vs 国际均价 13 就是这个场景）
    · `mark=True` 才把已呈现的提醒标 `in_digest=1`（**必须在报告写盘之后**调用）
    """
    conn = get_personal_conn()
    try:
        cur = conn.cursor()
        last = cur.execute("SELECT created_at FROM digest_reports WHERE owner_id=?"
                           " ORDER BY id DESC LIMIT 1", (int(account_id),)).fetchone()
        since = (_fk(last) if last else "") or _dt_shift(-int(days))
        rows = [dict(r) for r in cur.execute(
            "SELECT item_type, item_id, price, observed_at, source_type, source_ref FROM price_history"
            " WHERE account_id=? AND substr(observed_at,1,10) >= substr(?,1,10) ORDER BY id",
            (int(account_id), str(since)[:10]))]
        alerts = [dict(r) for r in cur.execute(
            "SELECT id, item_type, item_id, rule, price, detail FROM price_alerts WHERE account_id=?"
            " AND in_digest IS NOT 1 ORDER BY id", (int(account_id),))]
        if not rows and not alerts:
            return ""
        # 按对象分组（首次→末次）
        groups = {}
        for r in rows:
            k = (r["item_type"], r["item_id"])
            g = groups.setdefault(k, {"first": r["price"], "last": r["price"], "n": 0})
            g["last"] = r["price"]
            g["n"] += 1
        # 名称（台账对象 → 中文名）
        for (t, iid) in list(groups.keys()) + [(a["item_type"], a["item_id"]) for a in alerts]:
            if (t, iid) not in groups:
                groups[(t, iid)] = {"first": None, "last": None, "n": 0}
            groups[(t, iid)]["name"] = f"{name_of(cur, account_id, t, iid)}（{_TYPE_CN.get(t, t)}）"
        by_item = {}
        for a in alerts:
            by_item.setdefault((a["item_type"], a["item_id"]), []).append(a)
        lines = ["", "## 📉 我的价格台账（本期变化）", "",
                 "> 数据来自**你自己记的价**（对话 / 前端「更新价格」）。报告正文里另外标注的"
                 "「报道里的价格」是**新闻口径**（可能是别的渠道/国际均价），两者分家、永不混用。", ""]
        lines.append("- 本期共记价 **%d** 次，覆盖 **%d** 个对象" % (len(rows), len(groups)))
        for (t, iid), g in groups.items():
            nm = g.get("name") or ("%s#%s" % (t, iid))
            if g["n"]:
                chg = ""
                if g["first"] and g["last"] and float(g["first"]) != float(g["last"]):
                    pct = (float(g["last"]) - float(g["first"])) / float(g["first"]) * 100
                    chg = "（%+.1f%%）" % pct
                line = "- **%s**：%s 元 → %s 元%s" % (nm, g["first"], g["last"], chg)
            else:
                line = "- **%s**" % nm
            for a in by_item.get((t, iid), []):
                line += " · %s" % (a["detail"] or a["rule"])
            line += _mention_side(cur, account_id, t, iid)
            lines.append(line)
        if mark and alerts:
            conn.executemany("UPDATE price_alerts SET in_digest=1 WHERE id=?", [(int(r["id"]),) for r in alerts])
            conn.commit()
        return "\n".join(lines).rstrip() + "\n"
    finally:
        conn.close()


def _mention_side(cur, account_id: int, item_type: str, item_id) -> str:
    """E（并排对照）：该对象在"报道里"出现过的价 —— 一律标注渠道，**不冒充当前价**。"""
    try:
        rows = list(cur.execute(
            "SELECT entity, price, price_text, currency, shop, seen_at FROM price_mentions"
            " WHERE account_id=? AND linked_item_id IS ? ORDER BY id DESC LIMIT 2",
            (int(account_id), item_id)))
    except Exception:
        return ""
    if not rows:
        return ""
    bits = []
    for r in rows:
        txt = _fk(r, "price_text") or ("%s %s" % (_fk(r, "price"), _fk(r, "currency")))
        shop = _fk(r, "shop") or "未标渠道"
        bits.append("报道 %s（%s）" % (txt, shop))
    return " ｜ " + "；".join(bits)


def _dt_shift(days: int) -> str:
    import datetime as _d
    return (_d.datetime.now() + _d.timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")


_TYPE_CN = {"product": "收藏", "company_product": "公司产品", "competitor_product": "竞品"}


def name_of(cur, account_id: int, item_type: str, item_id) -> str:
    """台账对象的显示名（竞品表用 comp_name；其余用 product_name）。"""
    tbl = _LAST_PRICE_TABLE.get(item_type)
    ncol = "product_name"
    if item_type == "competitor_product":
        tbl, ncol = "company_competitors", "comp_name"
    if not tbl or not item_id:
        return "#%s" % item_id
    try:
        row = cur.execute("SELECT %s FROM %s WHERE id=? AND (owner_id=? OR owner_id IS NULL)"
                          % (ncol, tbl), (int(item_id), int(account_id))).fetchone()
        return (_fk(row) if row else "") or ("#%s" % item_id)
    except Exception:
        return "#%s" % item_id


# ============================ M5-7：竞品对比（§3.3 / G5 / D5）============================
COMPETITOR_THRESHOLD_PCT = 10.0     # 默认：竞品比自家低超过 10% 才提醒（方案 §3.3）
COMPETITOR_ALERT_INTERVAL_DAYS = 7  # D5：竞品价差提醒每 7 天最多一次


def _parse_ids(raw) -> list:
    """`mapped_product_ids` 容忍三种写法：JSON 数组 / 逗号分隔 / 空。"""
    if raw is None:
        return []
    if isinstance(raw, (list, tuple)):
        return [int(x) for x in raw if str(x).strip()]
    txt = str(raw).strip()
    if not txt:
        return []
    try:
        import json as _json
        v = _json.loads(txt)
        if isinstance(v, (list, tuple)):
            return [int(x) for x in v if str(x).strip()]
        return [int(v)]
    except Exception:
        pass
    out = []
    for part in re.split(r"[,，\s]+", txt):
        if part.strip().isdigit():
            out.append(int(part))
    return out


def _latest_price(cur, account_id, item_type, item_id):
    row = cur.execute("SELECT price FROM price_history WHERE account_id=? AND item_type=? AND item_id IS ?"
                      " ORDER BY id DESC LIMIT 1", (int(account_id), item_type, item_id)).fetchone()
    return None if not row else float(_fk(row))


def _own_price(cur, account_id, product_id):
    """自家产品的"当前价"：优先用台账最新，其次用 `last_price`，再其次用登记价 `price`。"""
    _row0 = cur.execute("SELECT * FROM company_products WHERE id=? AND owner_id=?",
                        (int(product_id), int(account_id))).fetchone()
    if _row0 is not None and pdisp.price_kind_of(_row0) == pdisp.KIND_RULE:
        return None                     # ★ Q6：规则计价商品没有单一数值价 → 不参与价差

    got = _latest_price(cur, account_id, "company_product", product_id)
    if got is not None:
        return got
    row = cur.execute("SELECT COALESCE(last_price, price) FROM company_products WHERE id=?"
                      " AND owner_id=?", (int(product_id), int(account_id))).fetchone()
    return None if not row or _fk(row) is None else float(_fk(row))


def competitor_comparison(account_id: int) -> list:
    """竞品 vs 自家产品的价差视图（M5-6 的工作台/公司页直接用）。

    每项：`{competitor_id, competitor_name, competitor_price, own_product_id, own_product_name,
    own_price, gap_pct}`。**只算有价的**（两边都要有价才有意义）。
    """
    conn = get_personal_conn()
    try:
        cur = conn.cursor()
        out = []
        # ★ 先 list() 物化：循环体里 `_latest_price/_own_price/cur.execute` 都用同一个 cursor，
        #   不物化就会作废外层结果集 → 竞品对比只出第一个竞品（与 summary 同一个坑）。
        for c in list(cur.execute("SELECT id, comp_name, mapped_product_ids FROM company_competitors"
                                  " WHERE owner_id=? ORDER BY id", (int(account_id),))):
            cid, cname = int(_fk(c, "id")), _fk(c, "comp_name")
            cprice = _latest_price(cur, account_id, "competitor_product", cid)
            for pid in _parse_ids(_fk(c, "mapped_product_ids")):
                own = _own_price(cur, account_id, pid)
                if cprice is None or own in (None, 0):
                    continue
                row = cur.execute("SELECT product_name FROM company_products WHERE id=? AND owner_id=?",
                                  (int(pid), int(account_id))).fetchone()
                out.append({
                    "competitor_id": cid, "competitor_name": cname, "competitor_price": cprice,
                    "own_product_id": int(pid), "own_product_name": _fk(row) if row else "",
                    "own_price": own, "gap_pct": round((cprice - own) / own * 100, 2),
                })
        return out
    finally:
        conn.close()


def _maybe_gap_alert(cur, account_id, competitor_id, price) -> str:
    """竞品价比自家低超阈值 → 写一条 `competitor_gap`（**7 天内只提醒一次**，D5）。"""
    c = cur.execute("SELECT comp_name, mapped_product_ids FROM company_competitors WHERE id=? AND owner_id=?",
                    (int(competitor_id), int(account_id))).fetchone()
    if not c:
        return ""
    cname = _fk(c, "comp_name") or ("竞品#%s" % competitor_id)
    best = None
    for pid in _parse_ids(_fk(c, "mapped_product_ids")):
        own = _own_price(cur, account_id, pid)
        if own in (None, 0):
            continue
        gap = (price - own) / own * 100
        if best is None or gap < best[0]:
            best = (gap, pid, own)
    if best is None:
        return ""                       # 没关联自有产品或对方没价 → 不提醒（也绝不猜）
    gap_pct, pid, own = best
    if gap_pct > -COMPETITOR_THRESHOLD_PCT:
        return ""                       # 没低到阈值以内
    recent = cur.execute(
        "SELECT fired_at FROM price_alerts WHERE account_id=? AND item_type='competitor_product'"
        " AND item_id IS ? AND rule=? ORDER BY id DESC LIMIT 1",
        (int(account_id), int(competitor_id), RULE_COMPETITOR_GAP)).fetchone()
    if recent and _fk(recent):
        import datetime as _dt
        try:
            last = _dt.datetime.strptime(str(_fk(recent))[:19], "%Y-%m-%d %H:%M:%S")
            if (_dt.datetime.now() - last).days < COMPETITOR_ALERT_INTERVAL_DAYS:
                return ""               # ★ D5：7 天内不重复提醒
        except Exception:
            pass
    _log_alert(cur, account_id, "competitor_product", competitor_id, RULE_COMPETITOR_GAP, price,
               "竞品「%s」比我方（#%s，%s 元）低 %.1f%%" % (cname, pid, own, abs(gap_pct)))
    return RULE_COMPETITOR_GAP
