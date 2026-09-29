# -*- coding: utf-8 -*-
"""M3-2 · 画像的证据收集（方案 §3.2 / D3）。

为什么需要它（对应根因 R1）：现有画像是"初始化一次的快照" —— 触发条件全是「用户**表达**」，
而收藏 / 关注 / 竞品 / 订阅 / 周报主题都躺在**数据库与文件**里，在对话里永远不会被"表达"，
所以画像只剩"聊到过的那点东西"。M3 要补的正是这一路：把**数据库事实 + 最近周报 + 最近会话**
收集成证据，交给建议器（`memory_suggester`，M3-3）去提炼。

三个来源（方案 D3）：
  1. **7 张结构化表**（沿用初始化器那套 `_collect` 口径，抽到这里共用，避免两处漂移）；
  2. **最近 3 篇周报**：`digest_reports` 的 title/created_at/item_count + 对应 md 文件的
     **标题与要点行**（**纯字符串抽取，不再调模型** —— 报告本身就是模型产物了）；
  3. **最近 N 条会话**：`messages` 里 role=user 的最近消息（压缩空白、截断）。

本模块只读、不写、不调模型、不碰网络 → 可整段离线回归。
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from tools.schema_personal import ensure_tables, get_personal_conn

PROJECT_ROOT = Path(__file__).resolve().parents[1]

MAX_REPORTS = 3                 # 只看最近 3 篇（D3）
MAX_SESSIONS = 30               # 会话条数上限
MAX_MSG_CHARS = 160             # 单条会话消息截断
MAX_SESSIONS_CHARS = 2000       # 会话证据总长
MAX_REPORT_CHARS = 2200         # 单篇周报要点总长
_BULLET_RE = re.compile(r"^\s*(?:[-*+]\s+|\d+[.、]\s*|#{1,6}\s+)")


def _text_of(content) -> str:
    """消息正文 → 纯文本。实测 `messages.content` 存的多是纯文本，但协议上允许是 JSON，两种都兜。"""
    raw = content or ""
    if not isinstance(raw, str):
        return ""
    s = raw.strip()
    if s.startswith("{") or s.startswith("["):
        try:
            j = json.loads(s)
        except Exception:  # noqa: BLE001
            return s
        if isinstance(j, dict):
            for key in ("content", "text", "value"):
                v = j.get(key)
                if isinstance(v, str) and v.strip():
                    return v.strip()
        if isinstance(j, str):
            return j.strip()
    return s


def _squash(text: str, cap: int = MAX_MSG_CHARS) -> str:
    t = re.sub(r"\s+", " ", str(text or "")).strip()
    return t[:cap] + ("…" if len(t) > cap else "")


def collect_tables(aid: int) -> dict:
    """7 张结构化表（与初始化器同一套口径）。返回 `{key: [dict, …]}`，键名就是来源名。"""
    ensure_tables()
    conn = get_personal_conn()
    try:
        q = lambda sql: [dict(r) for r in conn.execute(sql, (aid,))]  # noqa: E731
        # ★ 带 id：画像行要能写成「（来源：兴趣#3 · 日期）」，前端据此点回原始条目（G5 可溯源）
        return {
            "interests": q("SELECT id, interest_tag, description FROM interests WHERE owner_id=?"),
            "watchlist": q("SELECT id, brand, product, note FROM watchlist WHERE owner_id=?"),
            "products": q("SELECT id, product_name, brand, category, price FROM products WHERE owner_id=?"),
            "company_profile": q("SELECT * FROM company_profile WHERE owner_id=?"),
            "company_products": q("SELECT id, product_name, category, price FROM company_products WHERE owner_id=?"),
            "competitors": q("SELECT id, comp_name, category, note FROM company_competitors WHERE owner_id=?"),
            "subs": q("SELECT id, name, keywords FROM digest_subs WHERE owner_id=?"),
        }
    finally:
        conn.close()


def _ref(name: str, row: dict) -> str:
    """`兴趣#3` 这种可溯源引用（写进画像行的「来源」，也能让前端点回原始条目）。"""
    rid = row.get("id")
    return "%s#%s" % (name, rid) if rid not in (None, "") else name


def table_lines(tables: dict) -> list[str]:
    """7 张表 → 人话行，**每行带可溯源引用**（`兴趣#3：…`）。顺序沿用初始化器那套。"""
    out: list[str] = []
    for r in tables.get("interests") or []:
        out.append("%s：%s%s" % (_ref("兴趣", r), r.get("interest_tag") or "",
                                ("（%s）" % r["description"]) if r.get("description") else ""))
    for r in tables.get("watchlist") or []:
        out.append(("%s：%s %s" % (_ref("关注", r), r.get("brand") or "", r.get("product") or "")).strip()
                   + (("（%s）" % r["note"]) if r.get("note") else ""))
    for r in tables.get("products") or []:
        price = "，价格 %s" % r["price"] if r.get("price") else ""
        out.append("%s：%s%s（%s）%s" % (_ref("收藏", r), r.get("brand") or "", r.get("product_name") or "",
                                       r.get("category") or "未分类", price))
    for r in tables.get("company_profile") or []:
        out.append("%s：%s" % (_ref("公司", r), r.get("company_name") or "（未填写）"))
    for r in tables.get("company_products") or []:
        out.append("%s：%s（%s）" % (_ref("公司产品", r), r.get("product_name") or "",
                                   r.get("category") or "未分类"))
    for r in tables.get("competitors") or []:
        out.append("%s：%s" % (_ref("竞品", r), r.get("comp_name") or "")
                   + (("（%s）" % r["category"]) if r.get("category") else ""))
    for r in tables.get("subs") or []:
        out.append("%s：%s" % (_ref("订阅", r), r.get("name") or ""))
    return out


def report_bullets(md_path: str, *, cap: int = MAX_REPORT_CHARS) -> list[str]:
    """从周报 md 里抽**标题与要点行**（纯字符串，不调模型）。读不到就返回空列表，不抛。"""
    p = Path(str(md_path or "").replace("\\", "/"))
    if not p.is_absolute():
        p = PROJECT_ROOT / p
    try:
        text = p.read_text(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        return []
    out: list[str] = []
    for ln in text.replace("\r\n", "\n").split("\n"):
        s = ln.strip()
        if not s or not _BULLET_RE.match(s):
            continue
        item = _BULLET_RE.sub("", s).strip()
        if not item:
            continue
        out.append(item)
        if sum(len(x) for x in out) >= cap:
            break
    return out


def collect_reports(aid: int, *, limit: int = MAX_REPORTS) -> list[dict]:
    """最近 N 篇**生成成功**的周报：`{title, created_at, item_count, bullets, md_ok}`（新的在前）。"""
    ensure_tables()
    conn = get_personal_conn()
    try:
        rows = [dict(r) for r in conn.execute(
            "SELECT id, title, md_path, item_count, status, created_at FROM digest_reports "
            "WHERE owner_id=? AND COALESCE(status,'done')='done' ORDER BY id DESC LIMIT ?",
            (int(aid), int(limit)))]
    finally:
        conn.close()
    for r in rows:
        b = report_bullets(r.get("md_path") or "")
        r["bullets"] = b
        r["md_ok"] = bool(b)
    return rows


def collect_sessions(aid: int, *, limit: int = MAX_SESSIONS) -> list[str]:
    """最近 N 条**用户消息**（时间从旧到新），压缩空白 + 截断；超总长从最旧的一端丢。"""
    ensure_tables()
    conn = get_personal_conn()
    try:
        rows = [dict(r) for r in conn.execute(
            "SELECT m.content AS content FROM messages m JOIN conversations c ON c.id = m.conversation_id "
            "WHERE c.account_id=? AND m.role='user' ORDER BY m.id DESC LIMIT ?",
            (int(aid), int(limit)))]
    finally:
        conn.close()
    out: list[str] = []
    for r in reversed(rows):                      # 转成"从旧到新"，读起来是用户最近在关心什么
        t = _squash(_text_of(r.get("content")))
        if t:
            out.append(t)
    while out and sum(len(x) for x in out) > MAX_SESSIONS_CHARS:
        out.pop(0)                                # 总长超了丢最旧的（保留最近）
    return out


def collect(aid: int, *, reports: int = MAX_REPORTS, sessions: int = MAX_SESSIONS) -> dict:
    """一次性收齐三路证据。`counts` 里的 `rows` 就是元数据里的 `sources` 计数口径。"""
    tables = collect_tables(aid)
    lines = table_lines(tables)
    rep = collect_reports(aid, limit=reports)
    sess = collect_sessions(aid, limit=sessions)
    return {
        "tables": tables,
        "table_lines": lines,
        "reports": rep,
        "sessions": sess,
        "counts": {"rows": len(lines), "reports": len(rep), "sessions": len(sess),
                   "tables": sum(len(v or []) for v in tables.values())},
    }


def render_text(ev: dict) -> str:
    """证据 → 给模型看的文本块（**只给证据，不给结论**；措辞与写画像的提示词对齐）。"""
    parts: list[str] = []
    lines = ev.get("table_lines") or []
    parts.append("【系统里已有的结构化资料】\n%s" % ("\n".join(lines) if lines
                                                     else "（这个账号还没有任何结构化资料）"))
    reps = ev.get("reports") or []
    if reps:
        buf = []
        for r in reps:
            head = "- %s（%s，%s 条）" % (r.get("title") or "（无标题）",
                                          (r.get("created_at") or "")[:10], r.get("item_count") or 0)
            body = "\n".join("  · %s" % b for b in (r.get("bullets") or [])[:40])
            buf.append(head + ("\n" + body if body else "（正文读不到）"))
        parts.append("【最近 %d 篇周报的标题与要点（说明用户近期在关注什么）】\n%s" % (len(reps), "\n".join(buf)))
    else:
        parts.append("【最近周报】还没有生成过周报")
    sess = ev.get("sessions") or []
    if sess:
        parts.append("【用户最近说过的话（从旧到新，最多 %d 条）】\n%s"
                     % (len(sess), "\n".join("- %s" % s for s in sess)))
    else:
        parts.append("【最近会话】没有可用的对话记录")
    return "\n\n".join(parts)

# ---------------------------------------------------------------- 待更新信号（M3-5 / 方案 §3.3）
# ★ 与方案 §3.3 的差异（写在这里免得后人照方案找时间戳）：方案说"比较 memory_meta.refreshed 与各表/周报的时间"，
#   但**用户域那六张表压根没有 created_at 列**（实测 `PRAGMA table_info`：只有 digest_reports 有）——
#   临时加列也补不回历史数据。所以改用**计数水位**：把各来源条数写进 `memory_meta` 的 `counts=`，
#   下次刷新时与当前条数相减 → 得出"自上次刷新以来新增了几条"。无需改表、对老账号也立刻可用。
COUNT_KEYS = ("interests", "watchlist", "products", "company_profile",
              "company_products", "competitors", "subs", "reports")


def count_vector(aid: int) -> list[int]:
    """各来源条数的水位向量（顺序固定为 `COUNT_KEYS`）。"""
    tables = collect_tables(aid)
    vec = [len(tables.get(k) or []) for k in COUNT_KEYS if k != "reports"]
    conn = get_personal_conn()
    try:
        n = int(conn.execute("SELECT COUNT(*) FROM digest_reports WHERE owner_id=? AND COALESCE(status,'done')='done'",
                             (int(aid),)).fetchone()[0])
    finally:
        conn.close()
    return vec + [n]


def refresh_meta(aid: int, rows: int, prev: dict | None = None) -> dict:
    """写 meta 时用的口径：保留 `init`，`refreshed` 记为今天，并**记下当前计数水位**。

    所有写路径（初始化 / apply / 导入 / agent 更新 / 还原）都用它 → 水位口径不会分叉。
    """
    from tools.memory_profile import today
    prev = prev or {}
    return {"init": (prev.get("init") or "").strip() or today(),
            "refreshed": today(),
            "sources": int(rows),
            "counts": count_vector(aid)}


def staleness(aid: int, meta: dict) -> str:
    """【画像待更新】一行信号：**数字由服务端算好**，模型只做判断（方案 §3.3）。

    没有新增数据（或没有水位基线）→ 返回空串 —— 信号只在**真有新数据**时出现，避免每轮都吵。
    """
    prev = list((meta or {}).get("counts") or [])
    if not prev:
        return ""
    now = count_vector(aid)
    if len(prev) != len(now):
        return ""
    label = {"interests": "条兴趣", "watchlist": "个关注", "products": "件收藏",
             "company_profile": "条公司资料", "company_products": "个公司产品",
             "competitors": "个竞品", "subs": "条订阅", "reports": "篇周报"}
    bits = []
    for key, before, after in zip(COUNT_KEYS, prev, now):
        d = after - before
        if d > 0:
            bits.append("%d %s" % (d, label.get(key, key)))
    if not bits:
        return ""
    since = (meta or {}).get("refreshed") or "上次刷新"
    return ("【画像待更新】自上次刷新（%s）以来，系统里新增了 %s。"
            "如果与用户本轮的问题相关，请用 update_user_profile 把对应维度更新进画像；无关就别动。"
            % (since, "、".join(bits)))
