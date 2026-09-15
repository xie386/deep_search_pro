# -*- coding: utf-8 -*-
"""dspro list —— 罗列账号信息。

    dspro list                  # 默认：个人信息/公司信息 + 周报生成模块的关注信息
    dspro list -db              # 只打印个人信息 / 公司信息
    dspro list -digest          # 只打印周报生成模块的所有关注信息（订阅）
    dspro list --json           # 机器可读（脚本/管道用）

"个人信息/公司信息" 按账号角色取：公司账号看公司档案+我司产品+关注竞品，
个人账号看兴趣+关注清单+收藏商品；另一侧若有数据会额外提示（防止"是不是丢了"的错觉）。
"""

import json

from cli import ui
from cli import session as ss

_SCOPE_LABEL = {"company": "竞品", "personal": "选购/兴趣"}


def _keywords(raw) -> str:
    """订阅关键词（JSON 数组文本）→ 逗号串。"""
    try:
        arr = json.loads(raw or "[]")
        if isinstance(arr, list):
            return "、".join(str(x) for x in arr)
        return str(arr)
    except Exception:
        return str(raw or "")


def _counts(conn, aid: int) -> dict:
    out = {}
    for key, sql in (
        ("interests", "SELECT COUNT(*) FROM interests WHERE owner_id=?"),
        ("watchlist", "SELECT COUNT(*) FROM watchlist WHERE owner_id=?"),
        ("products", "SELECT COUNT(*) FROM products WHERE owner_id=?"),
        ("company_products", "SELECT COUNT(*) FROM company_products WHERE owner_id=?"),
        ("company_competitors", "SELECT COUNT(*) FROM company_competitors WHERE owner_id=?"),
        ("profile", "SELECT COUNT(*) FROM company_profile WHERE owner_id=?"),
        ("subs", "SELECT COUNT(*) FROM digest_subs WHERE owner_id=?"),
        ("reports", "SELECT COUNT(*) FROM digest_reports WHERE owner_id=?"),
    ):
        out[key] = conn.execute(sql, (aid,)).fetchone()[0]
    return out


# ---------------------------------------------------------------------------
# 数据收集（人机两用）
# ---------------------------------------------------------------------------
def collect_db(conn, aid: int, role: str) -> dict:
    """按角色取「个人信息 / 公司信息」全量数据。"""
    out = {"role": role, "personal": {}, "company": {}}
    if role == "company":
        prof = conn.execute("SELECT * FROM company_profile WHERE owner_id=?", (aid,)).fetchone()
        out["company"]["profile"] = dict(prof) if prof else {}
        out["company"]["products"] = [dict(r) for r in conn.execute(
            "SELECT id, product_name, category, price, currency, status, attributes "
            "FROM company_products WHERE owner_id=? ORDER BY id", (aid,)).fetchall()]
        out["company"]["competitors"] = [dict(r) for r in conn.execute(
            "SELECT id, comp_name, category, website, is_competitor, note, mapped_product_ids "
            "FROM company_competitors WHERE owner_id=? ORDER BY id", (aid,)).fetchall()]
    else:
        out["personal"]["interests"] = [dict(r) for r in conn.execute(
            "SELECT id, interest_tag, description, keywords FROM interests WHERE owner_id=? ORDER BY id",
            (aid,)).fetchall()]
        out["personal"]["watchlist"] = [dict(r) for r in conn.execute(
            "SELECT id, brand, product, note FROM watchlist WHERE owner_id=? ORDER BY id",
            (aid,)).fetchall()]
        out["personal"]["products"] = [dict(r) for r in conn.execute(
            "SELECT id, product_name, brand, category, price, currency, status, attributes "
            "FROM products WHERE owner_id=? ORDER BY id", (aid,)).fetchall()]

    # 另一侧的数据量（仅提示，防止误以为丢失）
    other = "company" if role == "personal" else "personal"
    if other == "company":
        out["other_count"] = conn.execute(
            "SELECT (SELECT COUNT(*) FROM company_products WHERE owner_id=?) + "
            "(SELECT COUNT(*) FROM company_competitors WHERE owner_id=?) + "
            "(SELECT COUNT(*) FROM company_profile WHERE owner_id=?)",
            (aid, aid, aid)).fetchone()[0]
    else:
        out["other_count"] = conn.execute(
            "SELECT (SELECT COUNT(*) FROM interests WHERE owner_id=?) + "
            "(SELECT COUNT(*) FROM watchlist WHERE owner_id=?) + "
            "(SELECT COUNT(*) FROM products WHERE owner_id=?)",
            (aid, aid, aid)).fetchone()[0]
    return out


def collect_digest(conn, aid: int) -> dict:
    subs = [dict(r) for r in conn.execute(
        "SELECT id, scope, name, keywords, schedule, lang, enabled, last_run_at "
        "FROM digest_subs WHERE owner_id=? ORDER BY scope, id", (aid,)).fetchall()]
    reports = [dict(r) for r in conn.execute(
        "SELECT id, title, md_path, item_count, status, coverage_note, created_at "
        "FROM digest_reports WHERE owner_id=? ORDER BY id DESC LIMIT 5", (aid,)).fetchall()]
    return {"subs": subs, "reports": reports}


# ---------------------------------------------------------------------------
# 人读输出
# ---------------------------------------------------------------------------
def _print_overview(counts: dict) -> None:
    ui.section("总览", "📊")
    rows = [
        ("关注领域（订阅）", f"{counts['subs']} 个"),
        ("历史周报", f"{counts['reports']} 份"),
        ("兴趣", f"{counts['interests']} 条"),
        ("关注清单", f"{counts['watchlist']} 条"),
        ("收藏商品", f"{counts['products']} 条"),
        ("我司产品", f"{counts['company_products']} 条"),
        ("关注竞品", f"{counts['company_competitors']} 条"),
        ("公司档案", "已填" if counts["profile"] else "未填"),
    ]
    half = (len(rows) + 1) // 2
    lw = max(ui.dwidth(k) for k, _ in rows)                 # 左列按最长标签对齐
    rw = max([ui.dwidth(v) for _, v in rows[half:]] or [0])
    for a, b in zip(rows[:half], rows[half:] + [("", "")]):
        left = f"{ui.c(ui.pad(a[0], lw), 'grey')}  {ui.pad(a[1], rw)}"
        right = f"{ui.c(ui.pad(b[0], lw), 'grey')}  {b[1]}" if b[0] else ""
        print(f"  {left}    {right}")


def _print_personal(d: dict) -> None:
    ui.section("个人信息", "👤")
    its = d.get("interests") or []
    ui.info(f"兴趣领域（{len(its)} 条）")
    if its:
        ui.table(["#", "兴趣标签", "详细描述", "检索关键词"],
                 [[r["id"], r["interest_tag"] or "-", r["description"] or "-", r["keywords"] or "-"] for r in its],
                 max_width=[4, 16, 40, 24])
    else:
        ui.hint("（空）到 web 端「信息」页添加兴趣领域")

    wl = d.get("watchlist") or []
    ui.info(f"关注清单（{len(wl)} 条）")
    if wl:
        ui.table(["#", "品牌", "产品", "备注"],
                 [[r["id"], r["brand"] or "-", r["product"] or "-", r["note"] or "-"] for r in wl],
                 max_width=[4, 18, 30, 30])
    else:
        ui.hint("（空）")

    ps = d.get("products") or []
    ui.info(f"收藏商品（{len(ps)} 条）")
    if ps:
        ui.table(["#", "商品", "品牌", "品类", "价格", "状态"],
                 [[r["id"], r["product_name"], r["brand"] or "-", r["category"] or "-",
                   (f"{r['price']:g} {r['currency'] or 'CNY'}" if r["price"] is not None else "-"),
                   r["status"] or "-"] for r in ps],
                 max_width=[4, 32, 16, 14, 14, 8])
    else:
        ui.hint("（空）")


def _profile_rows(prof: dict) -> list:
    fields = [("company_name", "公司名称"), ("industry", "主营领域"), ("intro", "公司简介"),
              ("legal_rep", "法定代表人"), ("reg_capital", "注册资本"), ("founded_date", "成立时间"),
              ("employee_scale", "员工规模"), ("hq_city", "总部城市"), ("website", "官网"),
              ("business_scope", "经营范围"), ("contact", "联系方式"), ("address", "地址"),
              ("honors", "荣誉资质"), ("note", "备注")]
    return [(label, prof.get(key)) for key, label in fields if prof.get(key)]


def _print_company(d: dict) -> None:
    ui.section("公司信息", "🏢")
    prof = d.get("profile") or {}
    rows = _profile_rows(prof)
    ui.info(f"公司档案（{len(rows)} 个字段已填）")
    if rows:
        ui.kv([(k, ui.trunc(str(v), 90)) for k, v in rows])
    else:
        ui.hint("（未填写）到 web 端「信息」页完善公司资料")

    ps = d.get("products") or []
    ui.info(f"我司产品（{len(ps)} 条）")
    if ps:
        ui.table(["#", "产品", "品类", "价格", "状态"],
                 [[r["id"], r["product_name"], r["category"] or "-",
                   (f"{r['price']:g} {r['currency'] or 'CNY'}" if r["price"] is not None else "-"),
                   r["status"] or "-"] for r in ps],
                 max_width=[4, 34, 16, 14, 8])
    else:
        ui.hint("（空）")

    cps = d.get("competitors") or []
    ui.info(f"关注竞品（{len(cps)} 条）")
    if cps:
        ui.table(["#", "竞品", "品类", "官网", "关联产品", "备注"],
                 [[r["id"], r["comp_name"], r["category"] or "-", r["website"] or "-",
                   (r["mapped_product_ids"] or "-"), ui.trunc(r["note"] or "-", 28)] for r in cps],
                 max_width=[4, 26, 14, 26, 14, 28])
    else:
        ui.hint("（空）")


def _print_digest(dg: dict) -> None:
    subs = dg.get("subs") or []
    enabled = [s for s in subs if int(s.get("enabled") or 0) == 1]
    ui.section(f"周报生成模块 · 关注信息（{len(subs)} 个关注领域，启用 {len(enabled)} 个）", "📰")
    if subs:
        ui.table(["#", "关注领域（领域名）", "范围", "周期", "检索语言", "状态", "上次生成", "关注关键词"],
                 [[s["id"], s["name"] or "-", _SCOPE_LABEL.get(s.get("scope"), s.get("scope") or "-"),
                   {"daily": "每日", "weekly": "每周"}.get(s.get("schedule"), s.get("schedule") or "-"),
                   {"zh": "中文", "en": "英文", "both": "中英双语"}.get(s.get("lang"), s.get("lang") or "-"),
                   ui.c("启用", "green") if int(s.get("enabled") or 0) == 1 else ui.c("停用", "yellow"),
                   (s.get("last_run_at") or "从未")[:16],
                   _keywords(s.get("keywords"))] for s in subs],
                 max_width=[4, 22, 10, 8, 10, 8, 18, 46])
        ui.hint("生成：dspro digest            （全部启用领域各一份）")
        ui.hint("单领域：dspro digest \"领域名\"  （名称见上表「关注领域」列）")
    else:
        ui.hint("（空）到 web 端「情报周报」页新增订阅（关注领域）")

    reps = dg.get("reports") or []
    if reps:
        ui.info(f"最近周报（最多 5 份）")
        ui.table(["#", "标题", "条数", "状态", "生成时间"],
                 [[r["id"], ui.trunc(r["title"] or "-", 46), r["item_count"], r["status"] or "-",
                   (r["created_at"] or "-")[:19]] for r in reps],
                 max_width=[4, 46, 6, 8, 20])


# ---------------------------------------------------------------------------
# 命令入口
# ---------------------------------------------------------------------------
def run(db_only: bool = False, digest_only: bool = False, as_json: bool = False) -> int:
    data = ss.require()
    aid, role = data["account_id"], data.get("role", "personal")
    if db_only and digest_only:      # 两个都指定 = 都要（等价于默认）
        db_only = digest_only = False

    conn = ss.db()
    try:
        counts = _counts(conn, aid)
        db_info = collect_db(conn, aid, role) if not digest_only else None
        dg_info = collect_digest(conn, aid) if not db_only else None
    finally:
        conn.close()

    if as_json:
        payload = {"username": data["username"], "role": role, "counts": counts}
        if db_info is not None:
            payload["db"] = db_info
        if dg_info is not None:
            payload["digest"] = dg_info
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    ui.title(f"账号信息 · {data['username']}", f"角色：{role}  ·  account_id={aid}")
    _print_overview(counts)
    if db_info is not None:
        if role == "company":
            _print_company(db_info["company"])
        else:
            _print_personal(db_info["personal"])
        other = db_info.get("other_count") or 0
        if other:
            other_name = "公司模块" if role == "personal" else "个人模块"
            ui.hint(f"提示：该账号在{other_name}还有 {other} 条数据（本账号角色为 {role}，默认按角色展示）")
    if dg_info is not None:
        _print_digest(dg_info)
    ui.blank()
    return 0
