# ============================================================
# 智选情报官 · Digest 调度器（M4-c）
# APScheduler 内嵌 FastAPI 进程：每日 9 点 / 每周一 9 点跑所有启用订阅。
# 由 api/server.py 在 startup 时 start()、shutdown 时 stop()。
#
# 推送定义（本地 Web 应用）：
#   ① 生成完成后经 WS monitor_event(type=report_ready) 实时提醒；
#   ② 报告列表页读 digest_reports（GET /api/reports）。
# ============================================================

import json
import os
from datetime import datetime

from apscheduler.schedulers.background import BackgroundScheduler

from tools.schema_personal import get_personal_conn, ensure_tables

scheduler = BackgroundScheduler(timezone="Asia/Shanghai")


def _notify_ws(username: str, title: str):
    """WS 实时提醒（M1 多会话适配）：推给 username 通道 + 该账号所有会话通道。

    v1.0 前端 WS 固定连 /ws/{username}，只推 username 即可收到；
    v2.0 M1 前端 WS 动态连当前会话（UUID）——若用户停在某会话视图，
    只推 username 会收不到。故遍历该账号全部 thread_id 逐个推送。
    """
    try:
        from api.monitor import monitor
        # 目标 thread 集合：username（v1.0 直发通道）+ 该账号所有会话 UUID
        targets = [username]
        try:
            conn = get_personal_conn()
            row = conn.execute(
                "SELECT id FROM accounts WHERE username=?", (username,)
            ).fetchone()
            if row:
                tids = conn.execute(
                    "SELECT thread_id FROM conversations WHERE account_id=?",
                    (row["id"],),
                ).fetchall()
                targets += [r["thread_id"] for r in tids]
            conn.close()
        except Exception as e:
            print(f"[digest-sched] 查询会话列表失败（仅推 username）: {e}")
        for tid in targets:
            try:
                monitor.set_current_thread(tid)
                monitor._emit("report_ready", f"📰 新报告已生成：{title}",
                              {"title": title})
            except Exception:
                pass  # 单个目标失败不影响其他
        monitor.clear_current_thread()
    except Exception as e:
        print(f"[digest-sched] WS 通知失败（不影响报告生成）: {e}")


def run_all_due(scope: str | None = None):
    """跑所有 enabled 订阅。scope=None 全部；'weekly' 仅周一触发时用。"""
    from agent.digest_engine import run_digest
    ensure_tables()
    conn = get_personal_conn()
    try:
        rows = conn.execute(
            "SELECT s.id, s.owner_id, s.scope, a.username "
            "FROM digest_subs s JOIN accounts a ON a.id = s.owner_id "
            "WHERE s.enabled=1" + (" AND s.schedule='weekly'" if scope == "weekly" else "")
        ).fetchall()
    finally:
        conn.close()

    print(f"[digest-sched] {datetime.now():%F %T} 触发，待处理 {len(rows)} 个订阅")
    for sub_id, owner_id, sub_scope, username in rows:
        result = run_digest(sub_id, owner_id)
        if result.get("ok"):
            title = f"{result.get('item_count', 0)}条 · {result.get('md_path', '')}"
            print(f"[digest-sched] ✓ {username}: {title}")
            _notify_ws(username, os.path.basename(result.get("md_path", "报告")))
        else:
            print(f"[digest-sched] ✗ {username}: {result.get('error')}")


def start():
    if scheduler.get_jobs():
        return  # 幂等
    # 每天 9:00 跑 daily 订阅；周一 9:00 额外跑 weekly 订阅
    scheduler.add_job(run_all_due, "cron", day="*", hour=9, minute=0,
                      kwargs={"scope": None}, id="digest_daily",
                      replace_existing=True, misfire_grace_time=3600)
    scheduler.add_job(run_all_due, "cron", day_of_week="mon", hour=9, minute=0,
                      kwargs={"scope": "weekly"}, id="digest_weekly",
                      replace_existing=True, misfire_grace_time=3600)
    scheduler.start()
    print("[digest-sched] APScheduler 已启动（daily@9:00 / weekly@Mon9:00，Asia/Shanghai）")


def stop():
    try:
        scheduler.shutdown(wait=False)
    except Exception:
        pass
