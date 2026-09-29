# -*- coding: utf-8 -*-
"""M5-4 · 价格提醒的出口（方案 §3.5 的"后端只落库 + 标记待通知"）。

外壳/页面取走提醒 → 提示用户 → 回执 ack（避免重复弹）。**取走与回执都在这里**，
桌面壳自己不发 HTTP（M2 定的铁律：外壳只调页面 hook）。
"""
from typing import Optional

from fastapi import HTTPException, status
from pydantic import BaseModel

from tools.price_ledger import ack_alert, pending_alerts
from tools.schema_personal import get_personal_conn   # ★ 模块级：history/set_registered 直接用
import tools.price_display as _pd   # Q6：价格显示唯一口径（规则计价商品不显示数值字段）


class AckReq(BaseModel):
    alert_id: int


def alerts(token: str) -> dict:
    from api.customize import _require_account_id
    aid = _require_account_id(token)
    items = pending_alerts(aid)
    return {"items": items, "count": len(items)}


def ack(token: str, req: AckReq) -> dict:
    from api.customize import _require_account_id
    aid = _require_account_id(token)
    if not req.alert_id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "缺少 alert_id")
    r = ack_alert(int(req.alert_id), aid)
    if not r.get("acked"):
        # 已经 ack 过也算成功（幂等）；但**别人的提醒**必须是 404，不能静默成功
        from tools.schema_personal import get_personal_conn
        conn = get_personal_conn()
        try:
            own = conn.execute("SELECT COUNT(*) FROM price_alerts WHERE id=? AND account_id=?",
                               (int(req.alert_id), int(aid))).fetchone()[0]
        finally:
            conn.close()
        if not int(own):
            raise HTTPException(status.HTTP_404_NOT_FOUND, "找不到这条提醒")
    return {"ok": True, **r}


class RecordReq(BaseModel):
    item_type: str
    item_id: int
    price: float
    note: str = ""


def summary(token: str) -> dict:
    """价格页/工作台要的一块数据：监控中的商品（最近记价 / 较上次涨跌 / 目标价）+ 竞品价差 + 资讯价。

    ★ 资讯价单独一段、且**带 `kind='news'` 标记**：UI 必须把它显示成"资讯价（来源：周报 #N）"，
      不能混进"当前价"（G4/C1）—— 这是本功能最容易误导用户的地方。
    """
    from api.customize import _require_account_id, _username_by_token
    from tools.price_ledger import competitor_comparison
    from tools.price_mentions import list_price_mentions
    from tools.schema_personal import get_personal_conn
    aid = _require_account_id(token)
    conn = get_personal_conn()
    try:
        cur = conn.cursor()
        items = []
        for tbl, itype, label in (("products", "product", "收藏"),
                                  ("company_products", "company_product", "公司产品")):
            cols = {r[1] for r in cur.execute("PRAGMA table_info(%s)" % tbl)}
            if "last_price" not in cols:
                continue
            # ★ 必须先 list() 物化：循环体里用**同一个 cursor** 查 prev / 条数，会作废外层结果集，
            #   导致只返回第一个商品（实测"永远只看到一个商品、切换器不出现"的真根因）。
            for r in list(cur.execute("SELECT id, product_name, last_price, target_price,"
                                      " COALESCE(monitor_enabled,0)"
                                      " FROM %s WHERE owner_id=?" % tbl, (int(aid),))):
                pid, name, last, target, mon = r[0], r[1], r[2], r[3], r[4]
                prev = cur.execute("SELECT price FROM price_history WHERE account_id=? AND item_type=?"
                                   " AND item_id IS ? ORDER BY id DESC LIMIT 2", (int(aid), itype, pid)).fetchall()
                delta = 0.0
                if len(prev) == 2 and float(prev[1][0]):
                    delta = round((float(prev[0][0]) - float(prev[1][0])) / float(prev[1][0]) * 100, 2)
                _r = cur.execute("SELECT * FROM %s WHERE id=?" % tbl, (pid,)).fetchone()
                _kind = _pd.price_kind_of(_r)
                _disp = _pd.display_price(_r)
                # ★ 一个账号常有多个收藏/公司产品：**全部列出**，让卡片顶部的切换器能切到每个商品。
                #   （原来这里 `if last is None and target is None and not mon and not _kind: continue`
                #    跳过"没监控、没记过价"的商品 —— 单商品卡片时代合理，但导致切换器里看不到它们，
                #    实测用户反馈"我没有看到切换商品的按钮"。卡片只渲染当前选中的一个，不会变乱。）
                _cnt = cur.execute("SELECT COUNT(*) FROM price_history WHERE account_id=? AND item_type=?"
                                   " AND item_id IS ?", (int(aid), itype, pid)).fetchone()[0]
                items.append({"item_type": itype, "item_id": pid, "name": name, "label": label,
                              "history_count": int(_cnt),   # ★ 未展开也要能显示条数（实测报过"没点开显示 0 条"）
                              "last_price": last,
                              # ★ Q6：规则计价商品不显示数值字段（涨跌/目标价/跌破全部无意义）
                              "target_price": None if _kind == "rule" else target,
                              "delta_pct": 0.0 if _kind == "rule" else delta,
                              "below_target": (_kind != "rule" and last is not None
                                               and target is not None and float(last) <= float(target)),
                              "price_kind": _kind, "price_show": _disp.get("text", "")})
    finally:
        conn.close()
    return {"items": items, "competitors": competitor_comparison(aid),
            "mentions": list_price_mentions(aid, limit=20),
            "pending_alerts": len(pending_alerts(aid))}


def record(token: str, req: RecordReq) -> dict:
    """前端「更新价格」按钮：与对话记价走同一个事实源（source_type=manual）。"""
    from api.customize import _require_account_id, _username_by_token
    from tools.price_ledger import record_price
    aid = _require_account_id(token)
    try:
        r = record_price(aid, req.item_type, req.item_id, req.price,
                         source_type="manual", source_ref=_username_by_token(token), note=req.note or "")
    except ValueError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e))
    return r


class SourceReq(BaseModel):
    name: str
    url_template: str
    id: Optional[int] = None
    method: str = "GET"
    param_map: str = ""
    value_path: str = ""
    currency: str = "CNY"
    timeout_s: int = 15
    enabled: int = 0
    secret_env: str = ""


class CheckReq(BaseModel):
    item_type: str
    item_id: int
    sku: str = ""
    source_id: Optional[int] = None


def sources(token: str) -> dict:
    """价格源配置列表（**只回字段，不回任何凭证值** —— 凭证只存环境变量名）。"""
    from api.customize import _require_account_id
    from tools.price_source import list_sources
    aid = _require_account_id(token)
    return {"items": list_sources(aid), "stage": 2,
            "note": "阶段 2 预留：本版不内置任何价格源，新增源=填 URL 模板与取值映射"}


def save_source(token: str, req: SourceReq) -> dict:
    from api.customize import _require_account_id
    from tools.price_source import upsert_source
    aid = _require_account_id(token)
    try:
        sid = upsert_source(aid, req.model_dump())
    except ValueError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e))
    return {"ok": True, "id": sid}


def run_check(token: str, req: CheckReq) -> dict:
    """立即检查一个商品的价（走自配源；本版默认没有启用的源 → 明确回"未接源"）。"""
    from api.customize import _require_account_id
    from tools.price_source import check_price
    aid = _require_account_id(token)
    return check_price(aid, req.item_type, req.item_id, req.sku, req.source_id)


class RegisterReq(BaseModel):
    item_type: str
    item_id: int
    price: Optional[float] = None
    price_kind: Optional[str] = None
    price_text: Optional[str] = None
    # ★ 目标价 / 跌幅阈值：`None` = **不动**；> 0 = 设置；**0 或负数 = 清空**
    target_price: Optional[float] = None
    alert_drop_pct: Optional[float] = None


def history(token: str, item_type: str, item_id: int, limit: int = 50) -> dict:
    """某个对象的**全部台账历史价**（倒序）+ 总数 + 当前登记价 —— 供前端"可展开列表"用。

    ★ 只读；账号隔离；列表里的每一行都可能被用户拿来"设为登记价"。
    """
    from api.customize import _require_account_id
    from tools.price_ledger import _LAST_PRICE_TABLE
    aid = _require_account_id(token)
    if item_type not in _LAST_PRICE_TABLE:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "item_type 只能是 product 或 company_product")
    conn = get_personal_conn()
    try:
        cur = conn.cursor()
        rows = [dict(r) for r in cur.execute(
            "SELECT id, price, currency, source_type, source_ref, observed_at, note FROM price_history"
            " WHERE account_id=? AND item_type=? AND item_id IS ? ORDER BY id DESC LIMIT ?",
            (int(aid), item_type, int(item_id), int(limit)))]
        total = cur.execute("SELECT COUNT(*) FROM price_history WHERE account_id=? AND item_type=?"
                            " AND item_id IS ?", (int(aid), item_type, int(item_id))).fetchone()[0]
        # ★ 涨跌 = 「本行相对**更早一条**」：(本行 - 更早) / 更早 × 100
        #   踩过的坑：原来拿"更新的一条"当分母 → 出现 -900%/-100% 这种反向数（用户看图发现）
        for i, r in enumerate(rows):        # rows 是新→旧
            older = rows[i + 1] if i + 1 < len(rows) else None
            r["delta_pct"] = (round((r["price"] - older["price"]) / older["price"] * 100, 2)
                              if older and older["price"] else 0.0)
        tbl = _LAST_PRICE_TABLE[item_type]
        item = cur.execute("SELECT * FROM %s WHERE id=? AND owner_id=?" % tbl,
                           (int(item_id), int(aid))).fetchone()
        if item is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "这个对象不属于当前账号")
        disp = _pd.display_price(item)
    finally:
        conn.close()
    return {"items": rows, "total": int(total), "limit": int(limit),
            "registered": {"price_kind": disp["kind"], "price_show": disp["text"],
                           "numeric": disp["numeric"]}}


def set_registered(token: str, req: RegisterReq) -> dict:
    """「**设为登记价**」/ 手动改登记价 —— **只改配置**。

    ★ 不写台账、不触发提醒、不计入 usage_events 的记价语义（它走的是配置保存路径）。
    ★ 显式三态：kind='simple' 要数值；kind='rule' 要规则文本；两者都给=复杂价同时留参考数值。
    """
    from api.customize import _require_account_id
    from tools.price_ledger import _LAST_PRICE_TABLE
    aid = _require_account_id(token)
    if req.item_type not in _LAST_PRICE_TABLE:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "item_type 只能是 product 或 company_product")
    kind = (req.price_kind or "").strip() or None
    if kind not in (None, "simple", "rule"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "price_kind 只能是 simple 或 rule")
    if kind == "rule" and not (req.price_text or "").strip():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "复杂价格规则需要填写规则内容")
    if kind == "simple" and req.price is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "数值价需要填写价格")
    tbl = _LAST_PRICE_TABLE[req.item_type]
    conn = get_personal_conn()
    try:
        own = conn.execute("SELECT id FROM %s WHERE id=? AND owner_id=?" % tbl,
                           (int(req.item_id), int(aid))).fetchone()
        if not own:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "这个对象不属于当前账号")
        # ★ 价格三列：**都没提就不动**（只改目标价时不得把登记价清空 —— 实测踩过）
        _touch_price = not (req.price is None and req.price_kind is None and req.price_text is None)
        sets, args = [], []
        if _touch_price:
            fee = None if kind == "rule" else req.price   # 复杂价：数值列按拍板口径**置空**
            sets += ["price=?", "price_kind=?", "price_text=?"]
            args += [fee, kind, (req.price_text or "").strip() or None]
        if req.target_price is not None:                  # 目标价（0/负数 = 清空）
            sets.append("target_price=?")
            args.append(float(req.target_price) if float(req.target_price) > 0 else None)
        try:
            _cols = {r[1] for r in conn.execute("PRAGMA table_info(%s)" % tbl)}
        except Exception:
            _cols = set()
        if req.alert_drop_pct is not None and "alert_drop_pct" in _cols:   # 跌幅阈值（0/负数 = 清空）
            sets.append("alert_drop_pct=?")
            args.append(float(req.alert_drop_pct) if float(req.alert_drop_pct) > 0 else None)
        args.append(int(req.item_id))
        conn.execute("UPDATE %s SET %s WHERE id=?" % (tbl, ", ".join(sets)), tuple(args))
        conn.commit()
        row = conn.execute("SELECT * FROM %s WHERE id=?" % tbl, (int(req.item_id),)).fetchone()
        disp = _pd.display_price(row)
    finally:
        conn.close()
    return {"ok": True, "price_kind": disp["kind"], "price_show": disp["text"]}


class DelHistReq(BaseModel):
    item_type: str
    item_id: int
    history_id: int


def delete_history(token: str, req: DelHistReq) -> dict:
    """删一条台账记录（★ 只能删自己账号的）。

    ★ 删完**必须重算商品的最近价**：否则卡片还挂着已删掉的价（数据自相矛盾）。
    ★ 不动 `price_alerts`：提醒是"曾经发生过"的历史事实，删价不该抹掉它。
    """
    from api.customize import _require_account_id
    from tools.schema_personal import get_personal_conn
    aid = _require_account_id(token)
    if req.item_type not in ("product", "company_product"):
        raise HTTPException(400, "item_type 只能是 product / company_product")
    tbl = "products" if req.item_type == "product" else "company_products"
    conn = get_personal_conn()
    try:
        cur = conn.cursor()
        if not cur.execute("SELECT id FROM %s WHERE id=? AND owner_id=?" % tbl,
                           (int(req.item_id), int(aid))).fetchone():
            raise HTTPException(404, "没有这个商品（或不属于当前账号）")
        if not cur.execute("SELECT price FROM price_history WHERE id=? AND account_id=? AND item_type=?"
                           " AND item_id IS ?",
                           (int(req.history_id), int(aid), req.item_type, int(req.item_id))).fetchone():
            raise HTTPException(404, "没找到这条台账（可能已删除或不属于当前账号）")
        cur.execute("DELETE FROM price_history WHERE id=? AND account_id=?",
                    (int(req.history_id), int(aid)))
        last = cur.execute("SELECT price FROM price_history WHERE account_id=? AND item_type=? AND item_id IS ?"
                           " ORDER BY id DESC LIMIT 1",
                           (int(aid), req.item_type, int(req.item_id))).fetchone()
        cur.execute("UPDATE %s SET last_price=? WHERE id=?" % tbl,
                    (float(last[0]) if last else None, int(req.item_id)))
        conn.commit()
        left = cur.execute("SELECT COUNT(*) FROM price_history WHERE account_id=? AND item_type=? AND item_id IS ?",
                           (int(aid), req.item_type, int(req.item_id))).fetchone()[0]
        return {"ok": True, "deleted": int(req.history_id),
                "last_price": float(last[0]) if last else None, "remaining": int(left)}
    finally:
        conn.close()
