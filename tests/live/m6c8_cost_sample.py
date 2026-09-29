# -*- coding: utf-8 -*-
"""M6c-8 真机抽样：一轮**带工具调用**的真实会话 → 核对 token 账目与成本出口。

做法（全走本地 8124，不走系统代理）：
  1. 用测试账号（默认「尼古喵喵」）的随身 token 建一个新会话；
  2. 发一条**会调内部工具**的提问（走 `query_kb` / `read_agent_doc` 这类**只读**内部工具，
     **不碰 Tavily** —— 额度纪律，见 tests/README.md）；
  3. 交叉核对：会话里 assistant 消息条数（≈ 真实模型调用次数）vs `usage_events` 里 `kind='model'` 行数；
  4. 拉 `GET /api/cost/summary?days=30`，把成本卡要显示的数字打出来。

运行（服务已起在 8124）：
  .venv/Scripts/python.exe tests/live/m6c8_cost_sample.py [端口] [账号]
"""
import json
import os
import sys
import time
import urllib.request
from urllib.parse import quote

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from tools.schema_personal import get_personal_conn          # noqa: E402

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8124
USER = sys.argv[2] if len(sys.argv) > 2 else "尼古喵喵"
BASE = "http://127.0.0.1:%d" % PORT
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))   # ★ 本地服务必须绕系统代理


def call(path, payload=None, method="GET", timeout=300):
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    with OPENER.open(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def wait_ready(limit=240):
    t0 = time.time()
    while time.time() - t0 < limit:
        try:
            with OPENER.open(BASE + "/", timeout=5):
                return True
        except Exception:
            time.sleep(3)
    return False


def token_of(username):
    conn = get_personal_conn()
    try:
        row = conn.execute("SELECT a.id FROM accounts a WHERE a.username=?", (username,)).fetchone()
        if not row:
            return None, None
        aid = int(row[0])
        t = conn.execute("SELECT token FROM sessions WHERE account_id=? ORDER BY rowid DESC LIMIT 1",
                         (aid,)).fetchone()
        return aid, (t[0] if t else None)
    finally:
        conn.close()


def turn_facts(aid, tid, t0):
    """这一轮：assistant 消息数（≈模型调用次数）与 usage_events 明细。"""
    conn = get_personal_conn()
    try:
        conv = conn.execute("SELECT id FROM conversations WHERE thread_id=?", (tid,)).fetchone()
        cid = int(conv[0]) if conv else None
        n_asst = conn.execute("SELECT COUNT(*) FROM messages WHERE conversation_id=? AND role='assistant'",
                              (cid,)).fetchone()[0] if cid else 0
        rows = [dict(r) for r in conn.execute(
            "SELECT id, input_tokens, output_tokens, cached_tokens, uncached_tokens, token_source, cost,"
            " cost_note, provider_id, model_name FROM usage_events WHERE account_id=? AND thread_id=?"
            " AND kind='model' AND created_ts>=? ORDER BY id", (aid, tid, t0))]
        tools = [dict(r) for r in conn.execute(
            "SELECT tool_name, kind, COUNT(*) FROM usage_events WHERE account_id=? AND thread_id=?"
            " AND created_ts>=? AND kind<>'model' GROUP BY tool_name, kind", (aid, tid, t0))]
        return cid, n_asst, rows, tools
    finally:
        conn.close()


def main():
    print("=" * 68)
    print("M6c-8 真机抽样：账号=%s · 端口=%d" % (USER, PORT))
    if not wait_ready():
        print("✗ 服务未就绪（%ds 内）" % 240)
        return 2
    print("✓ 服务就绪")
    aid, tok = token_of(USER)
    if not aid or not tok:
        print("✗ 取不到账号/登录态")
        return 2
    print("✓ 账号 id=%d，用其最新登录态" % aid)

    sess = call("/api/chat/sessions?token=" + tok, {"title": "M6c-8 成本抽样"}, method="POST")
    tid = sess.get("thread_id") or sess.get("id")
    print("✓ 新会话 thread_id=%s" % tid)

    q = "先用知识库查一下「价格监控」相关的实现，然后用一句话总结"
    t0 = time.time() - 1
    print("→ 发问：%s" % q)
    # ★ /api/chat 是 **query 参数**（question/token/thread_id），不是 JSON body —— 我第一版按 body 发，被 422 挡下 ✓
    out = call("/api/chat?token=%s&thread_id=%s&question=%s"
               % (quote(tok), quote(tid), quote(q)), None, method="POST")
    ans = (out.get("answer") or out.get("content") or "")
    print("← 回答（前 180 字）：%s" % ans[:180].replace("\n", " "))
    print("   本轮耗时 %.1fs" % (time.time() - t0))

    cid, n_asst, rows, tools = turn_facts(aid, tid, t0)
    print("\n--- 交叉核对（G1：多工具轮不许漏计）---")
    print("会话 id=%s · assistant 消息 %d 条（≈ 真实模型调用次数）" % (cid, n_asst))
    # ★ 别拿它当"应相等"：本会话有 `task` 子 Agent，子 Agent 的模型调用**不进主会话消息**，
    #   但**必须计入账目**（M4 时代正是这批被整轮漏掉）。所以只判"不少于主会话可见的轮数"。
    print("usage_events kind='model' 行数 = %d %s（应 ≥ 主会话轮数；差值是子 Agent 的模型调用）"
          % (len(rows), "✓" if len(rows) >= n_asst else "✗ 少了"))
    for r in rows:
        print("   #%s in=%s out=%s cached=%s uncached=%s src=%s cost=%s note=%s provider=%s/%s"
              % (r["id"], r["input_tokens"], r["output_tokens"], r["cached_tokens"], r["uncached_tokens"],
                 r["token_source"], r["cost"], r["cost_note"], r["provider_id"], r["model_name"]))
    print("非模型用量（工具行）：%s" % (tools or "无"))
    tot_in = sum(int(r["input_tokens"] or 0) for r in rows)
    tot_out = sum(int(r["output_tokens"] or 0) for r in rows)
    print("合计：进 %d / 出 %d tokens" % (tot_in, tot_out))

    print("\n--- 成本出口（成本卡要显示的就是这些）---")
    summ = call("/api/cost/summary?days=30&token=" + tok)
    t = summ["total"]
    print("本月：调用 %s 次 · tokens 进 %s / 出 %s · 命中 %s / 未命中 %s"
          % (t["calls"], t["tokens"]["input"], t["tokens"]["output"],
             t["tokens"]["cached"], t["tokens"]["uncached"]))
    print("      金额 %s · cost_note=%r · 无用量调用 %s 次"
          % (("未配置单价" if t["cost"] is None else "¥%.4f" % t["cost"]), t["cost_note"],
             t["unavailable_calls"]))
    print("      说明：%s" % summ["note"])
    print("      按 provider 分组：%s" % [(g["provider_name"] or g["provider_id"], g["model_name"], g["calls"])
                                          for g in summ["by_provider"]])
    print("      按天：%s" % [(d["date"], d["calls"]) for d in summ["daily"]])
    print("=" * 68)
    return 0


if __name__ == "__main__":
    sys.exit(main())
