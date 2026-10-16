"""06 · 自配写工具池：只读池 / 写池的选集、注入文本与归属设置。

定案见 `Verdict/06-…md` 裁定 6；方案见规划文档 §06 ③ G/H。

**口径（红线）**：池归属**只由用户显式设定**，默认只读池；入池凭据始终是"用户确认过"。
写池的工具**不进路由、不进 03 测评**，只能被用户在输入框用「/」选中后唤醒。
「/」第二组列出的"写工具集"= 归到写池的**来源**（一条 CLI 或一台 API/MCP 服务器各算一个）。
"""
from __future__ import annotations

from pydantic import BaseModel

from tools import capability_pool as cp
from tools import cli_registry as cr
from tools.schema_personal import get_personal_conn
from utils.settings import get_int


class PoolReq(BaseModel):
    """设置池归属的请求体。kind ∈ {cli, api, mcp}；ident = CLI 的 id 或来源的 slug/id。"""
    kind: str
    ident: str
    pool: str = "readonly"


def _caps() -> dict:
    """三组单轮上限（设置模块可调，均 ≤7）。"""
    return {
        "skills": min(7, max(1, get_int("PICK_MAX_SKILLS", 5))),
        "write_sets": min(7, max(1, get_int("PICK_MAX_WRITE_SETS", 5))),
        "workflows": min(7, max(1, get_int("PICK_MAX_WORKFLOWS", 5))),
    }


def write_sets(account_id: int | None) -> list[dict]:
    """归到写池的**来源**列表（「/」第二组的候选）。"""
    if not account_id:
        return []
    out = []
    for c in cr.list_clis(account_id):
        if (c.get("pool") or "readonly") == "write":
            out.append({"kind": "cli", "id": c["id"], "name": c["name"], "ref": c["bin"],
                        "write_rules": [" ".join(r) for r in (c.get("write_rules") or [])]})
    try:
        conn = get_personal_conn()
        try:
            for r in conn.execute(
                    "SELECT id, source, slug, name, pool FROM tool_sources "
                    "WHERE account_id=? AND pool='write' ORDER BY source, id", (int(account_id),)):
                out.append({"kind": r["source"], "id": r["id"], "name": r["name"], "ref": r["slug"],
                            "write_rules": []})
        finally:
            conn.close()
    except Exception:                       # noqa: BLE001 —— 老库/表缺失不该让菜单崩
        pass
    return out


def compose_write_sets_text(account_id: int | None, names: list[str] | None) -> str:
    """把本轮经「/」授权的写工具集编成一段说明书（拼在技能说明书后面注入）。

    没选中 → 返回空串（零影响）。选中的名字对不上任何写池来源 → 静默跳过（越权名不认）。
    """
    if not account_id or not names:
        return ""
    want = [n.strip() for n in names if n and n.strip()]
    if not want:
        return ""
    picks = [s for s in write_sets(account_id) if s["name"] in want or s["ref"] in want]
    if not picks:
        return ""
    lines = ["【本轮经「/」授权的写工具集】",
             "下列能力由用户**显式授权**本轮可用（平时不进路由池、不能被纯对话唤起）。",
             "其中标注了「允许的写子命令」的，只能跑列出的那些；未列出的照旧拒绝。"]
    for s in picks:
        head = "- %s（%s）" % (s["name"], s["ref"])
        if s["write_rules"]:
            lines.append(head + "；允许的写子命令：" + "、".join(s["write_rules"]))
        else:
            lines.append(head)
    lines.append("用法：需要改动外部状态时直接调用对应工具；完成或失败都如实回报。")
    return "\n".join(lines)


def summary(account_id: int | None) -> dict:
    """两池概览（写池汇总列表 + 上限），给前端「写工具池」面板用。"""
    if not account_id:
        return {"ok": False, "error": "未登录"}
    entries = cp.pool_entries(account_id, include_write_pool=True)
    ro = [e for e in entries if (getattr(e, "pool", "readonly") or "readonly") != "write"]
    wr = cp.write_pool_entries(account_id)
    # 全部**来源**（CLI / API 服务器 / MCP 服务器）及其归属 —— 面板据此显示切换控件。
    #   ★ 这一层是"来源级"归属：用户在这里把某条来源整体归到写池，单条能力的人工决定不受影响。
    sources = []
    for c in cr.list_clis(account_id):
        sources.append({"kind": "cli", "id": c["id"], "name": c["name"], "ref": c["bin"],
                        "pool": c.get("pool") or "readonly",
                        "live": c["state"] in ("installed", "authed"),
                        "cmds": len(c.get("rules") or []), "write_cmds": len(c.get("write_rules") or [])})
    try:
        conn = get_personal_conn()
        try:
            for r in conn.execute(
                    "SELECT id, source, slug, name, enabled, pool FROM tool_sources "
                    "WHERE account_id=? ORDER BY source, id", (int(account_id),)):
                sources.append({"kind": r["source"], "id": r["id"], "name": r["name"], "ref": r["slug"],
                                "pool": r["pool"] or "readonly", "live": bool(r["enabled"]),
                                "cmds": 0, "write_cmds": 0})
        finally:
            conn.close()
    except Exception:                       # noqa: BLE001
        pass
    return {
        "ok": True,
        "readonly_count": len(ro),
        "write_count": len(wr),
        "write_sets": write_sets(account_id),
        "write_entries": [{"source": e.source, "ref": e.ref, "name": e.name} for e in wr],
        "sources": sources,
        "caps": _caps(),
    }


def set_pool(account_id: int, kind: str, ident, pool: str) -> dict:
    """设置某条来源的池归属。kind ∈ {cli, api, mcp}；ident = CLI 的 id 或来源的 slug/id。

    ★ 只改**来源级**归属（不碰单条能力的人工决定）；改动后 `bump_pool_version` 让路由器缓存失效。
    """
    pool = "write" if str(pool).lower() == "write" else "readonly"
    kind = (kind or "").strip().lower()
    try:
        conn = get_personal_conn()
        try:
            if kind == "cli":
                cur = conn.execute("UPDATE user_clis SET pool=? WHERE account_id=? AND id=?",
                                   (pool, int(account_id), int(ident)))
            elif kind in ("api", "mcp"):
                cur = conn.execute("UPDATE tool_sources SET pool=?, updated_at=datetime('now','localtime') "
                                   "WHERE account_id=? AND source=? AND (slug=? OR CAST(id AS TEXT)=?)",
                                   (pool, int(account_id), kind, str(ident), str(ident)))
            else:
                return {"ok": False, "error": "kind 只能是 cli / api / mcp"}
            conn.commit()
            n = cur.rowcount
        finally:
            conn.close()
    except Exception as e:                   # noqa: BLE001
        return {"ok": False, "error": "写入失败：%s" % e}
    if not n:
        return {"ok": False, "error": "没找到这条来源（kind=%s ident=%s）" % (kind, ident)}
    try:
        cp.bump_pool_version(int(account_id))     # 路由器缓存按版本失效
    except Exception:                        # noqa: BLE001
        pass
    return {"ok": True, "kind": kind, "pool": pool, "changed": n}
