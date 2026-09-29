# -*- coding: utf-8 -*-
"""M6c-6 · 失败事件落库与汇总（M6c 方案 §3.4 落地 3）。

为什么单独一个模块（而不是把 INSERT 写在外壳里）：外壳 `front/desktop/app.py`
**一 import 就把整个桌面应用拉起来**（还会装崩溃日志器），根本没法单测 ✗ ——
所以"记一条失败 / 读汇总"都放这里，外壳只负责调用。

口径：
  · `account_id` **可空**：外壳失败（端口被占 / 后端崩溃）发生在**登录之前**，那时没有账号；
  · 任何异常都被吞掉（记录失败绝不能把外壳/服务再炸一次）；它**只用来观测**，不参与任何判据。
"""
import time

from tools.schema_personal import get_personal_conn


def record(code: str, detail: str = "", account_id=None) -> bool:
    """记一条失败事件。返回是否写成功（失败也不抛）。"""
    try:
        now = time.time()
        conn = get_personal_conn()
        try:
            conn.execute("INSERT INTO failure_events (account_id, code, detail, occurred_at)"
                         " VALUES (?,?,?,?)",
                         (int(account_id) if account_id else None, str(code or "UNKNOWN"),
                          str(detail or "")[:500],
                          time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now))))
            conn.commit()
            return True
        finally:
            conn.close()
    except Exception:
        return False


def summary(account_id=None, days: int = 7, limit: int = 50) -> dict:
    """失败汇总：按码计数 + 最近若干条原文。

    ★ 可见范围：自己的 + **系统级的（account_id IS NULL）** —— 进程级失败本来就不属于某个账号，
      但对使用者来说正是最该看到的那类（端口被占、后端起不来）。跨账号仍互相不可见。
    """
    try:
        days = max(0, int(days))
    except (TypeError, ValueError):
        days = 7
    cut = time.time() - days * 86400
    # 表里只有文本时间（'YYYY-mm-dd HH:MM:SS'），按它筛；同刻精度足够统计用
    where = "occurred_at>=?"
    since = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(cut))
    owner = "AND (account_id=? OR account_id IS NULL)" if account_id else ""
    args0 = [since] + ([int(account_id)] if account_id else [])
    conn = get_personal_conn()
    try:
        cur = conn.cursor()
        groups = list(cur.execute(
            "SELECT code, COUNT(*), MAX(occurred_at) FROM failure_events WHERE " + where + " " + owner +
            " GROUP BY code ORDER BY COUNT(*) DESC", tuple(args0)))
        recent = list(cur.execute(
            "SELECT id, code, detail, occurred_at, account_id FROM failure_events WHERE " + where + " " + owner +
            " ORDER BY id DESC LIMIT ?", tuple(args0 + [int(limit)])))
    finally:
        conn.close()
    return {
        "days": days,
        "since": since,
        "total": sum(int(g[1]) for g in groups),
        "by_code": [{"code": g[0], "count": int(g[1]), "last": g[2] or ""} for g in groups],
        "recent": [{"id": int(r[0]), "code": r[1], "detail": r[2] or "", "occurred_at": r[3] or "",
                    "account_id": r[4]} for r in recent],
    }
