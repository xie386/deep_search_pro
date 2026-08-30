# ============================================================
# 智选情报官 · 用户数据自主管理 API（前置工作 /api/me/*）
# 定位：人主动维护自己的业务数据（增删改）；Agent 工具只读消费。
# 归属权：全部按 token session 中的 account_id 隔离，跨账号不可见。
# 覆盖：公司（profile/products/competitors）+ 个人（interests/watchlist/products）
# ============================================================

from typing import Optional

from fastapi import HTTPException, Query, status
from pydantic import BaseModel

from api.account import get_session
from tools.schema_personal import ensure_tables, get_personal_conn


# ---------------------------------------------------------------------------
# Pydantic 模型
# ---------------------------------------------------------------------------
class ProfileReq(BaseModel):
    company_name: Optional[str] = None
    intro: Optional[str] = None            # 公司简介
    legal_rep: Optional[str] = None        # 法定代表人
    reg_capital: Optional[str] = None      # 注册资本
    founded_date: Optional[str] = None     # 成立时间
    employee_scale: Optional[str] = None   # 员工规模
    website: Optional[str] = None
    hq_city: Optional[str] = None          # 总部城市
    industry: Optional[str] = None         # 主营领域
    business_scope: Optional[str] = None   # 经营范围/业务描述
    contact: Optional[str] = None          # 联系方式
    address: Optional[str] = None
    honors: Optional[str] = None           # 荣誉资质(JSON数组文本)
    note: Optional[str] = None


class CompanyProductReq(BaseModel):
    product_name: str
    category: Optional[str] = None
    price: Optional[float] = None
    currency: str = "CNY"
    attributes: Optional[str] = None       # JSON 文本
    status: str = "active"


class CompetitorReq(BaseModel):
    comp_name: str
    category: Optional[str] = None
    website: Optional[str] = None
    is_competitor: bool = True
    note: Optional[str] = None
    mapped_product_ids: Optional[str] = None   # JSON 数组文本，如 "[1,4]"


class InterestReq(BaseModel):
    interest_tag: str
    description: Optional[str] = None      # 爱好详细描述
    keywords: Optional[str] = None         # 检索关键词(JSON数组文本)，供快讯订阅


class WatchlistReq(BaseModel):
    brand: Optional[str] = None
    product: Optional[str] = None
    note: Optional[str] = None


class PersonalProductReq(BaseModel):
    product_name: str
    brand: Optional[str] = None
    category: Optional[str] = None
    price: Optional[float] = None
    attributes: Optional[str] = None


def _require_account_id(token: str) -> int:
    sess = get_session(token)
    aid = sess.get("account_id")
    if aid is None:
        # 兼容旧 session（服务重启前的 token 不该存在；防御处理）
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "登录状态缺少账号标识，请重新登录")
    return aid


def _db():
    ensure_tables()
    return get_personal_conn()


def _rows_to_dicts(cur, rows):
    cols = [d[0] for d in cur.description] if cur.description else []
    return [dict(zip(cols, r)) for r in rows]


# ---------------------------------------------------------------------------
# 公司：profile
# ---------------------------------------------------------------------------
def profile_get(token: str) -> dict:
    aid = _require_account_id(token)
    conn = _db()
    try:
        cur = conn.execute("SELECT * FROM company_profile WHERE owner_id = ?", (aid,))
        row = cur.fetchone()
        if not row:
            return {k: None for k in (
                "company_name", "intro", "legal_rep", "reg_capital", "founded_date",
                "employee_scale", "website", "hq_city", "industry",
                "business_scope", "contact", "address", "honors", "note")} | {"initialized": False}
        d = dict(row)
        d.pop("id", None); d.pop("owner_id", None)
        d["initialized"] = True
        return d
    finally:
        conn.close()


def profile_upsert(req: ProfileReq, token: str) -> dict:
    aid = _require_account_id(token)
    conn = _db()
    try:
        fields = ["company_name", "intro", "legal_rep", "reg_capital", "founded_date",
                  "employee_scale", "website", "hq_city", "industry",
                  "business_scope", "contact", "address", "honors", "note"]
        vals = [getattr(req, f) for f in fields]
        cols_sql = ", ".join(["owner_id"] + fields)
        ph = ", ".join(["?"] * (len(fields) + 1))
        upd = ", ".join(f"{f}=excluded.{f}" for f in fields)
        conn.execute(
            f"INSERT INTO company_profile ({cols_sql}) VALUES ({ph}) "
            f"ON CONFLICT(owner_id) DO UPDATE SET {upd}",
            [aid] + vals,
        )
        conn.commit()
    finally:
        conn.close()
    return {"ok": True}


# ---------------------------------------------------------------------------
# 公司：products
# ---------------------------------------------------------------------------
def cproducts_list(token: str) -> list:
    aid = _require_account_id(token)
    conn = _db()
    try:
        cur = conn.execute("SELECT * FROM company_products WHERE owner_id = ? ORDER BY id", (aid,))
        return _rows_to_dicts(cur, cur.fetchall())
    finally:
        conn.close()


def cproducts_add(req: CompanyProductReq, token: str) -> dict:
    aid = _require_account_id(token)
    conn = _db()
    try:
        cur = conn.execute(
            "INSERT INTO company_products (owner_id, product_name, category, price, currency, attributes, status) "
            "VALUES (?,?,?,?,?,?,?)",
            (aid, req.product_name, req.category, req.price, req.currency, req.attributes, req.status),
        )
        conn.commit()
        return {"id": cur.lastrowid}
    finally:
        conn.close()


def cproducts_update(pid: int, req: CompanyProductReq, token: str) -> dict:
    aid = _require_account_id(token)
    conn = _db()
    try:
        cur = conn.execute(
            "UPDATE company_products SET product_name=?, category=?, price=?, currency=?, attributes=?, status=? "
            "WHERE id=? AND owner_id=?",
            (req.product_name, req.category, req.price, req.currency, req.attributes, req.status, pid, aid),
        )
        conn.commit()
        if cur.rowcount == 0:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "产品不存在或不属于当前账号")
    finally:
        conn.close()
    return {"ok": True}


def cproducts_delete(pid: int, token: str) -> dict:
    aid = _require_account_id(token)
    conn = _db()
    try:
        cur = conn.execute("DELETE FROM company_products WHERE id=? AND owner_id=?", (pid, aid))
        conn.commit()
        if cur.rowcount == 0:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "产品不存在或不属于当前账号")
    finally:
        conn.close()
    return {"ok": True}


# ---------------------------------------------------------------------------
# 公司：competitors
# ---------------------------------------------------------------------------
def competitors_list(token: str) -> list:
    aid = _require_account_id(token)
    conn = _db()
    try:
        cur = conn.execute(
            "SELECT id, comp_name, category, website, is_competitor, note, mapped_product_ids "
            "FROM company_competitors WHERE owner_id = ? ORDER BY id", (aid,))
        return _rows_to_dicts(cur, cur.fetchall())
    finally:
        conn.close()


def competitors_add(req: CompetitorReq, token: str) -> dict:
    aid = _require_account_id(token)
    conn = _db()
    try:
        cur = conn.execute(
            "INSERT INTO company_competitors (owner_id, comp_name, category, website, is_competitor, note, mapped_product_ids) "
            "VALUES (?,?,?,?,?,?,?)",
            (aid, req.comp_name, req.category, req.website, int(req.is_competitor), req.note, req.mapped_product_ids),
        )
        conn.commit()
        return {"id": cur.lastrowid}
    finally:
        conn.close()


def competitors_delete(cid: int, token: str) -> dict:
    aid = _require_account_id(token)
    conn = _db()
    try:
        cur = conn.execute("DELETE FROM company_competitors WHERE id=? AND owner_id=?", (cid, aid))
        conn.commit()
        if cur.rowcount == 0:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "竞品不存在或不属于当前账号")
    finally:
        conn.close()
    return {"ok": True}


# ---------------------------------------------------------------------------
# 个人：interests / watchlist / products
# ---------------------------------------------------------------------------
def competitors_update(cid: int, req: CompetitorReq, token: str) -> dict:
    aid = _require_account_id(token)
    conn = _db()
    try:
        cur = conn.execute(
            "UPDATE company_competitors SET comp_name=?, category=?, website=?, "
            "is_competitor=?, note=?, mapped_product_ids=? WHERE id=? AND owner_id=?",
            (req.comp_name, req.category, req.website, int(req.is_competitor),
             req.note, req.mapped_product_ids, cid, aid),
        )
        conn.commit()
        if cur.rowcount == 0:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "竞品不存在或不属于当前账号")
    finally:
        conn.close()
    return {"ok": True}


def interests_list(token: str) -> list:
    aid = _require_account_id(token)
    conn = _db()
    try:
        cur = conn.execute("SELECT id, interest_tag, description, keywords FROM interests WHERE owner_id = ? ORDER BY id", (aid,))
        return _rows_to_dicts(cur, cur.fetchall())
    finally:
        conn.close()


def interests_add(req: InterestReq, token: str) -> dict:
    aid = _require_account_id(token)
    tag = req.interest_tag.strip()
    if not tag:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "兴趣标签不能为空")
    conn = _db()
    try:
        dup = conn.execute("SELECT 1 FROM interests WHERE owner_id=? AND interest_tag=?", (aid, tag)).fetchone()
        if dup:
            raise HTTPException(status.HTTP_409_CONFLICT, "该兴趣已存在")
        cur = conn.execute(
            "INSERT INTO interests (owner_id, interest_tag, description, keywords) VALUES (?,?,?,?)",
            (aid, tag, req.description, req.keywords),
        )
        conn.commit()
        return {"id": cur.lastrowid}
    finally:
        conn.close()


def interests_delete(iid: int, token: str) -> dict:
    aid = _require_account_id(token)
    conn = _db()
    try:
        cur = conn.execute("DELETE FROM interests WHERE id=? AND owner_id=?", (iid, aid))
        conn.commit()
        if cur.rowcount == 0:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "兴趣不存在或不属于当前账号")
    finally:
        conn.close()
    return {"ok": True}


def interests_update(iid: int, req: InterestReq, token: str) -> dict:
    aid = _require_account_id(token)
    tag = req.interest_tag.strip()
    if not tag:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "兴趣标签不能为空")
    conn = _db()
    try:
        cur = conn.execute(
            "UPDATE interests SET interest_tag=?, description=?, keywords=? "
            "WHERE id=? AND owner_id=?",
            (tag, req.description, req.keywords, iid, aid),
        )
        conn.commit()
        if cur.rowcount == 0:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "兴趣不存在或不属于当前账号")
    finally:
        conn.close()
    return {"ok": True}


def watchlist_update(wid: int, req: WatchlistReq, token: str) -> dict:
    aid = _require_account_id(token)
    conn = _db()
    try:
        cur = conn.execute(
            "UPDATE watchlist SET brand=?, product=?, note=? WHERE id=? AND owner_id=?",
            (req.brand, req.product, req.note, wid, aid),
        )
        conn.commit()
        if cur.rowcount == 0:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "关注项不存在或不属于当前账号")
    finally:
        conn.close()
    return {"ok": True}


def pproducts_update(pid: int, req: PersonalProductReq, token: str) -> dict:
    aid = _require_account_id(token)
    conn = _db()
    try:
        cur = conn.execute(
            "UPDATE products SET product_name=?, brand=?, category=?, price=?, attributes=? "
            "WHERE id=? AND owner_id=?",
            (req.product_name, req.brand, req.category, req.price, req.attributes, pid, aid),
        )
        conn.commit()
        if cur.rowcount == 0:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "商品不存在或不属于当前账号")
    finally:
        conn.close()
    return {"ok": True}


def watchlist_list(token: str) -> list:
    aid = _require_account_id(token)
    conn = _db()
    try:
        cur = conn.execute("SELECT * FROM watchlist WHERE owner_id = ? ORDER BY id", (aid,))
        return _rows_to_dicts(cur, cur.fetchall())
    finally:
        conn.close()


def watchlist_add(req: WatchlistReq, token: str) -> dict:
    aid = _require_account_id(token)
    if not (req.brand or req.product):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "品牌与商品至少填一项")
    conn = _db()
    try:
        cur = conn.execute(
            "INSERT INTO watchlist (owner_id, brand, product, note) VALUES (?,?,?,?)",
            (aid, req.brand, req.product, req.note),
        )
        conn.commit()
        return {"id": cur.lastrowid}
    finally:
        conn.close()


def watchlist_delete(wid: int, token: str) -> dict:
    aid = _require_account_id(token)
    conn = _db()
    try:
        cur = conn.execute("DELETE FROM watchlist WHERE id=? AND owner_id=?", (wid, aid))
        conn.commit()
        if cur.rowcount == 0:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "关注项不存在或不属于当前账号")
    finally:
        conn.close()
    return {"ok": True}


def pproducts_list(token: str) -> list:
    aid = _require_account_id(token)
    conn = _db()
    try:
        cur = conn.execute("SELECT * FROM products WHERE owner_id = ? ORDER BY id", (aid,))
        return _rows_to_dicts(cur, cur.fetchall())
    finally:
        conn.close()


def pproducts_add(req: PersonalProductReq, token: str) -> dict:
    aid = _require_account_id(token)
    conn = _db()
    try:
        cur = conn.execute(
            "INSERT INTO products (owner_id, product_name, brand, category, price, attributes) VALUES (?,?,?,?,?,?)",
            (aid, req.product_name, req.brand, req.category, req.price, req.attributes),
        )
        conn.commit()
        return {"id": cur.lastrowid}
    finally:
        conn.close()


def pproducts_delete(pid: int, token: str) -> dict:
    aid = _require_account_id(token)
    conn = _db()
    try:
        cur = conn.execute("DELETE FROM products WHERE id=? AND owner_id=?", (pid, aid))
        conn.commit()
        if cur.rowcount == 0:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "商品不存在或不属于当前账号")
    finally:
        conn.close()
    return {"ok": True}


# ---------------------------------------------------------------------------
# 总览（左栏一次拉全 + 完成度）
# ---------------------------------------------------------------------------
def overview(token: str) -> dict:
    sess = get_session(token)
    role = sess["role"]
    data: dict = {"role": role}
    if role == "company":
        data["profile"] = profile_get(token)
        data["products"] = cproducts_list(token)
        data["competitors"] = competitors_list(token)
        p = data["profile"]
        data["setup_done"] = bool(p.get("company_name"))
    else:
        data["interests"] = interests_list(token)
        data["watchlist"] = watchlist_list(token)
        data["products"] = pproducts_list(token)
        data["setup_done"] = len(data["interests"]) > 0 or len(data["watchlist"]) > 0 or len(data["products"]) > 0
    return data
# ============================ 批量导入（剪贴板粘贴，一行一个）============================
class BatchReq(BaseModel):
    lines: str  # 每行一个；竞品行=竞品名；收藏行=商品名[,品牌[,参考价]]


def competitors_batch_add(req: BatchReq, token: str) -> dict:
    """批量添加竞品：每行一个竞品名（去空/去重，≤50 条）"""
    aid = _require_account_id(token)
    names = []
    for ln in req.lines.splitlines():
        name = ln.strip().strip('\u200b').strip()
        if name and name not in names:
            names.append(name)
    if not names:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "没有有效行：每行填一个竞品名")
    if len(names) > 50:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "单次最多 50 条")
    conn = _db()
    try:
        cur = conn.executemany(
            "INSERT INTO company_competitors (owner_id, comp_name, category, website, is_competitor, note, mapped_product_ids) "
            "VALUES (?,?,NULL,NULL,1,NULL,'[]')",
            [(aid, n) for n in names])
        conn.commit()
        return {"ok": True, "added": cur.rowcount if hasattr(cur, "rowcount") else len(names)}
    finally:
        conn.close()


def collection_batch_add(req: BatchReq, token: str) -> dict:
    """批量添加个人收藏：每行「商品名[,品牌[,参考价]]」逗号分隔（去空/去重，≤50 条）"""
    aid = _require_account_id(token)
    items = []
    seen = set()
    for ln in req.lines.splitlines():
        line = ln.strip().strip('\u200b').strip()
        if not line:
            continue
        parts = [p.strip() for p in line.split(",")]
        name = parts[0]
        if not name or name in seen:
            continue
        seen.add(name)
        brand = parts[1] if len(parts) > 1 and parts[1] else None
        price = None
        if len(parts) > 2 and parts[2]:
            try:
                price = float(parts[2])
            except ValueError:
                price = None
        items.append((name, brand, price))
    if not items:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "没有有效行：每行填 商品名[,品牌[,参考价]]")
    if len(items) > 50:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "单次最多 50 条")
    conn = _db()
    try:
        cur = conn.executemany(
            "INSERT INTO products (owner_id, product_name, brand, category, price, currency, attributes, status) "
            "VALUES (?,?,?,NULL,?,'CNY',NULL,'active')",
            [(aid, n, b, p) for n, b, p in items])
        conn.commit()
        return {"ok": True, "added": len(items)}
    finally:
        conn.close()
