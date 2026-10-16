"""06 · 工作目录存储层：`account_workdirs` 表的增删改查与"当前启用"解析。

方案见规划文档 §06 ③ B1/E1；定案见 `Verdict/06-…md`（裁定 1 只做外部真实目录、裁定 2 默认条目叫「主页」）。

依赖方向（单向，不 import `shell_runtime`）：
    shell_runtime → workdir_store → workdir_guard / utils.paths / schema_personal
"""
from __future__ import annotations

import time
from pathlib import Path

from tools.schema_personal import ensure_tables, get_personal_conn
from tools.workdir_guard import seed_protect
from utils.paths import sandbox_dir   # 默认条目「主页」= 账号沙箱路径

DEFAULT_NAME = "主页"


def _now() -> int:
    return int(time.time())


def ensure_default(account_id: int | None, username: str | None) -> None:
    """懒建默认条目「主页」（指向账号沙箱），并在**指向别处时就地修回**；同时播种保护名单。

    ★ 2026-10-16 真机事故：库里出现过一条脏数据 —— account_id=3(尼古喵喵) 的「主页」
      指向了 `user_data/sandbox/u`（我的单测用 username='u' 碰了真实库留下的）。
      结果 CLI 面板显示的 cwd 是 `sandbox/u`，看起来像"串到别的账号沙箱"。
      这里加自愈：默认条目按定义 = 该账号沙箱，路径不符就改回来（只动 is_default 那一行）。
    """
    if not account_id:
        return
    ensure_tables()                          # 幂等建表（不依赖"启动时一定跑过"的脆弱假设）
    if not (username or "").strip():
        return                               # 没账号名不建也不修：避免把真实账号的主页指到公共沙箱
    d = sandbox_dir(username)
    seed_protect(d)
    conn = get_personal_conn()
    try:
        row = conn.execute(
            "SELECT id, name, path, is_default FROM account_workdirs"
            " WHERE account_id=? ORDER BY is_default DESC, id ASC LIMIT 1",
            (int(account_id),)).fetchone()
        if row:
            if row["is_default"] and row["name"] == DEFAULT_NAME:
                try:
                    cur = Path(row["path"]).resolve()
                except OSError:
                    cur = None
                if cur != d.resolve():
                    conn.execute("UPDATE account_workdirs SET path=?, updated_at=? WHERE id=?",
                                 (str(d), _now(), row["id"]))
                    conn.commit()
            return
        conn.execute(
            "INSERT INTO account_workdirs (account_id, name, path, is_active, is_default,"
            " created_at, updated_at) VALUES (?,?,?,1,1,?,?)",
            (int(account_id), DEFAULT_NAME, str(d), _now(), _now()))
        conn.commit()
    finally:
        conn.close()


def list_dirs(account_id: int, username: str | None = None) -> list[dict]:
    """列出该账号的工作目录（主页永远排第一），并保障默认条目存在。"""
    ensure_tables()
    ensure_default(account_id, username)
    conn = get_personal_conn()
    try:
        rows = conn.execute(
            "SELECT id, name, path, is_active, is_default FROM account_workdirs"
            " WHERE account_id=? ORDER BY is_default DESC, id ASC", (int(account_id),)).fetchall()
        return [{"id": r["id"], "name": r["name"], "path": r["path"],
                 "is_active": bool(r["is_active"]), "is_default": bool(r["is_default"])}
                for r in rows]
    finally:
        conn.close()


def active(account_id: int | None, username: str | None = None) -> tuple[Path, dict | None]:
    """解析当前启用的工作目录 → (路径, 条目)。任何异常都回落到账号沙箱（不让工作目录拖垮对话）。

    外部目录若已不存在：**回落沙箱**（不擅自重建用户盘上的目录）。
    """
    fallback = sandbox_dir(username)
    if not account_id:
        seed_protect(fallback)              # 无账号也要保证名单在位（否则写路径必撞 fail-closed）
        return fallback, None
    try:
        conn = get_personal_conn()
        try:
            row = conn.execute(
                "SELECT id, name, path, is_default FROM account_workdirs"
                " WHERE account_id=? AND is_active=1 ORDER BY id LIMIT 1", (int(account_id),)).fetchone()
        finally:
            conn.close()
        if not row:
            seed_protect(fallback)          # 首次落地时补种保护名单（见下）
            return fallback, None
        p = Path(row["path"])
        # ★ 自愈的"只读"一侧：默认条目「主页」按定义 = 该账号沙箱。指向别处（历史脏数据）时
        #   本次**直接回落沙箱**（不写库、不留副作用）；真正改库交给 ensure_default（开面板时）。
        bad_home = (bool(row["is_default"]) and row["name"] == DEFAULT_NAME
                    and bool((username or "").strip())
                    and p.resolve() != sandbox_dir(username).resolve())
        if not p.is_dir() or bad_home:
            seed_protect(fallback)
            return fallback, None
        seed_protect(p)                     # ★ 真机抓到：只在 ensure_default（打开过文件管理器）时播种，
        #   纯聊天用户从没开过 → 第一次写入必然撞"名单读不到 → fail-closed"（决策对、体验死路）。
        #   这里补成"**缺失才补种**"：名单读不到不再是死路；而"存在但解析失败"依旧 fail-closed（真异常）。
        return p, {"id": row["id"], "name": row["name"], "path": row["path"],
                   "is_default": bool(row["is_default"])}
    except Exception:                       # noqa: BLE001 —— 工作目录解析失败绝不能中断对话
        try:
            seed_protect(fallback)
        except Exception:                   # noqa: BLE001
            pass
        return fallback, None


def switch(account_id: int, workdir_id: int) -> dict:
    """切换启用（同一事务内先清零再置一，保证至多一行为 1）。"""
    conn = get_personal_conn()
    try:
        own = conn.execute("SELECT id, path FROM account_workdirs WHERE id=? AND account_id=?",
                           (int(workdir_id), int(account_id))).fetchone()
        if not own:
            return {"ok": False, "error": "该工作目录不属于当前账号"}
        if not workdir_id:
            return {"ok": False, "error": "无效的工作目录"}
        p = Path(own["path"])
        if not p.is_dir():
            return {"ok": False, "error": "目录不存在或不可访问：%s" % p}
        conn.execute("UPDATE account_workdirs SET is_active=0, updated_at=? WHERE account_id=?",
                     (_now(), int(account_id)))
        conn.execute("UPDATE account_workdirs SET is_active=1, updated_at=? WHERE id=?",
                     (_now(), int(workdir_id)))
        conn.commit()
        return {"ok": True, "path": str(p)}
    finally:
        conn.close()


def add_external(account_id: int, name: str, raw_path: str) -> dict:
    """新增一个外部真实目录条目（**不接管**：只在库里登记，不动用户盘）。"""
    s = (raw_path or "").strip().strip('"').strip("'")
    if not s:
        return {"ok": False, "error": "路径为空"}
    p = Path(s)
    try:
        p = p.resolve()
    except (OSError, ValueError) as e:
        return {"ok": False, "error": "路径无法解析：%s（%s）" % (s, e)}
    if not p.is_dir():
        return {"ok": False, "error": "目录不存在：%s" % p}
    if p == p.anchor or len(p.parts) <= 2:
        return {"ok": False, "error": "拒绝把磁盘根目录或盘符根当作工作目录：%s" % p}
    nm = (name or "").strip() or p.name or "外部目录"
    seed_protect(p)                          # 外部目录也播种保护名单（不覆盖已有文件）
    conn = get_personal_conn()
    try:
        dup = conn.execute("SELECT id FROM account_workdirs WHERE account_id=? AND path=?",
                           (int(account_id), str(p))).fetchone()
        if dup:
            return {"ok": False, "error": "该目录已在本账号的工作目录列表里"}
        cur = conn.execute(
            "INSERT INTO account_workdirs (account_id, name, path, is_active, is_default,"
            " created_at, updated_at) VALUES (?,?,?,0,0,?,?)",
            (int(account_id), nm[:60], str(p), _now(), _now()))
        conn.commit()
        return {"ok": True, "id": cur.lastrowid, "path": str(p), "name": nm[:60]}
    except Exception as e:                   # noqa: BLE001
        return {"ok": False, "error": "写入失败：%s" % e}
    finally:
        conn.close()


def remove(account_id: int, workdir_id: int) -> dict:
    """移除条目（主页不可删；**只删登记，不碰磁盘上的目录**）。"""
    conn = get_personal_conn()
    try:
        row = conn.execute("SELECT id, name, is_default, is_active FROM account_workdirs"
                           " WHERE id=? AND account_id=?", (int(workdir_id), int(account_id))).fetchone()
        if not row:
            return {"ok": False, "error": "该工作目录不属于当前账号"}
        if row["is_default"]:
            return {"ok": False, "error": "「主页」是默认工作目录，不能删除"}
        conn.execute("DELETE FROM account_workdirs WHERE id=?", (int(workdir_id),))
        if row["is_active"]:
            d = conn.execute("SELECT id FROM account_workdirs WHERE account_id=? AND is_default=1",
                             (int(account_id),)).fetchone()
            if d:
                conn.execute("UPDATE account_workdirs SET is_active=1 WHERE id=?", (d["id"],))
        conn.commit()
        return {"ok": True, "removed": row["name"]}
    finally:
        conn.close()
