# -*- coding: utf-8 -*-
"""M5b-2 加压探针：**8 个工具**规模下的真机自然语言路由验证（要调模型，慢）。

对应设计：`docs/v2.0/M5b工具检索路由.md` §7.1（加压探针矩阵）。
M5a 的探针（`tests/m5_probe_e2e.py`）是 3 个 CLI 规模；这里加压到 8 个，回答
「工具变多之后，路由是否仍然把对的工具唤起来」以及「不回退成乱调」。

判定铁律（沿用 M5a 方法论，不采信模型自述）：
  ① **只看第一次调用**（首调）——后续重试不算命中；
  ② **真值配对**：从 `messages.tool_calls` 按会话取调用，不用审计窗口计数（会串别的进程）；
  ③ 真桩：每个可执行名都装一个能跑通的 `.cmd` 桩，避免模型「找不到二进制」引发重试风暴；
  ④ 反例：与任何工具都无关的问题，**不该调任何工具**。

运行（真机，约 20~40 分钟）：
    .venv/Scripts/python.exe tests/m5b_pressure_probe.py
自检（不调模型，几秒）：
    SKIP_LLM=1 .venv/Scripts/python.exe tests/m5b_pressure_probe.py
"""

import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from fastapi.testclient import TestClient  # noqa: E402

from api import server  # noqa: E402
from tools import capability_pool as cp  # noqa: E402
from tools import cli_registry as reg  # noqa: E402
from tools import tool_router as tr  # noqa: E402
from tools.schema_personal import ensure_tables, get_personal_conn, purge_account  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
STUB_DIR = os.path.join(ROOT, "data", "m5b_probe_bin")
RESULT = os.path.join(ROOT, "data", "m5b_pressure_result.json")
SKIP_LLM = os.getenv("SKIP_LLM") == "1"

USER = "m5bp_%s" % time.strftime("%m%d%H%M%S")
PWD = "m5bpress123"
# 单条重跑：M5B_ONLY=m5bp-vid（匹配 bin 或问句子串）——迭代一条探针不用再等 30 分钟
ONLY = os.getenv("M5B_ONLY", "").strip()

# 8 个工具（可执行名 / 展示名 / 只读清单 / 能力描述 / 探针问句 / 期望的首调前缀）
TOOLS = [
    dict(bin="m5bp-sched", name="日程 CLI", rules="today\nweek\nfree",
         ab="关键词：日程/安排/会议/待办/本周/空闲。\n本周有什么安排→week；今天有什么→today；找空闲时间→free",
         q="我本周还有什么安排", want=["week"]),
    dict(bin="m5bp-logq", name="日志查询", rules="query\ntail\nstats",
         ab="关键词：日志/报错/异常/接口/请求量/线上。\n某接口的报错→query；最新日志→tail；请求量统计→stats",
         q="帮我看看昨天用户接口有没有报错", want=["query"]),
    dict(bin="m5bp-gh", name="代码仓库", rules="issue list\npr list\nrepo view",
         ab="关键词：仓库/Issue/PR/合并请求/代码评审/开源。\n我有哪些 PR→pr list；仓库的 issue→issue list",
         q="我有哪些还没合并的 PR", want=["pr", "list"]),
    dict(bin="m5bp-wx", name="天气 API", rules="now\nforecast",
         ab="关键词：天气/气温/下雨/空气质量/未来几天。\n现在天气→now；未来几天→forecast",
         q="现在外面天气怎么样", want=["now"]),
    dict(bin="m5bp-fin", name="财经行情", rules="quote\nkline\nnews",
         ab="关键词：股票/基金/行情/涨跌/财报/大盘/指数。\n某只股票现价→quote；走势→kline；财经新闻→news",
         q="今天大盘怎么样", want=["quote"]),
    dict(bin="m5bp-book", name="读书工具", rules="shelf recent\nsearch\nnotes notebooks\nbook progress",
         ab="关键词：读书/书架/在读/阅读进度/笔记/划线/书评。\n我正在读什么书→shelf recent；"
            "读到哪了→shelf recent 再 book progress；找书→search；我的笔记→notes notebooks",
         q="我正在读什么书", want=["shelf", "recent"]),
    dict(bin="m5bp-vid", name="视频平台", rules="hot\ninfo\nsearch",
         ab="关键词：视频/B站/播放量/弹幕/字幕/视频讲什么/BV号。\n某个视频讲了什么→info；热门视频→hot；找视频→search",
         # ⚠️ 题面必须自足：首版写「那个视频讲了什么」，全新会话里「那个」没有先行词 →
         # 模型合理地**向用户澄清**而不是调工具（实测踩过，与 M5a 第二轮同类问题）。
         # 现在点名到具体视频；`info` 需要视频 ID，所以「先 search 找视频」也是正确的第一步。
         q="B站那个讲降噪耳机的视频讲了什么", want=["info"], alts=[["search"]]),
    dict(bin="m5bp-im", name="企业通讯", rules="sessions\nhistory\nunread",
         ab="关键词：聊天记录/消息/未读/群聊/联系人/同事。\n和某人的聊天记录→history；未读消息→unread",
         q="帮我看看和牢妹的聊天记录", want=["history"]),
]
NEG = ["你是谁", "帮我写一首诗"]

PASS, FAIL = [], []


def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name)
    print("%s %s%s" % ("  ✅" if cond else "  ❌", name, (" | " + str(extra)) if extra else ""))


def install_stubs() -> int:
    """给每个可执行名装一个能跑通的桩（ASCII 输出，避免编码坑）。"""
    os.makedirs(STUB_DIR, exist_ok=True)
    body = ('@echo off\r\n'
            'echo {"ok":true,"source":"m5b-stub","note":"probe command accepted",'
            '"items":[{"id":"1","title":"probe item"}]}\r\n')
    n = 0
    for t in TOOLS:
        for ext in (".cmd", ".bat"):
            with open(os.path.join(STUB_DIR, t["bin"] + ext), "w",
                      encoding="ascii", newline="") as f:
                f.write(body)
        n += 1
    os.environ["PATH"] = STUB_DIR + os.pathsep + os.environ.get("PATH", "")
    return n


# --------------------------------------------------------------------------- 真值读取
def _calls_of_thread(account_id: int, thread_id: str) -> list[tuple[str, list[str]]]:
    """按会话读回模型发出的调用：(可执行名, argv)。不采信审计窗口（会串别的进程）。"""
    conn = get_personal_conn()
    try:
        rows = conn.execute(
            """SELECT m.tool_calls FROM messages m JOIN conversations v ON v.id = m.conversation_id
               WHERE v.account_id = ? AND v.thread_id = ?
                 AND m.tool_calls IS NOT NULL AND m.tool_calls != ''""",
            (account_id, thread_id)).fetchall()
    finally:
        conn.close()
    out: list[tuple[str, list[str]]] = []
    for r in rows:
        try:
            for t in json.loads(r["tool_calls"] or "[]"):
                a = t.get("args") or {}
                cmd = str(a.get("command") or "")
                if cmd.startswith("m5bp-"):
                    out.append((cmd, [str(x) for x in (a.get("argv") or [])]))
        except Exception:  # noqa: BLE001
            continue
    return out


def _ask(c: TestClient, token: str, question: str) -> tuple[int, str, str]:
    for attempt in (1, 2):
        t = c.post("/api/chat/sessions", params={"token": token}).json()["thread_id"]
        r = c.post("/api/chat", params={"token": token, "thread_id": t, "question": question},
                   timeout=900)
        if r.status_code == 200:
            return 200, (r.json().get("answer") or ""), t
        print("     （第 %d 次返回 %s，%s）" % (attempt, r.status_code, r.text[:80]))
        time.sleep(20)
    return r.status_code, r.text[:200], ""


def _account_id(username):
    conn = get_personal_conn()
    try:
        row = conn.execute("SELECT id FROM accounts WHERE username=?", (username,)).fetchone()
        return int(row["id"]) if row else 0
    finally:
        conn.close()


def _cleanup(username):
    import shutil
    from rag_knowledge.kb_service import DB_ROOT
    conn = get_personal_conn()
    try:
        row = conn.execute("SELECT id FROM accounts WHERE username=?", (username,)).fetchone()
        aid = int(row["id"]) if row else 0
    finally:
        conn.close()
    if aid:
        purge_account(aid)
        shutil.rmtree(DB_ROOT / username, ignore_errors=True)
    shutil.rmtree(STUB_DIR, ignore_errors=True)


def main():
    ensure_tables()
    n = install_stubs()
    print("探针桩目录：%s（%d 个可执行名）" % (STUB_DIR, n))
    from api.account import _create_account, _hash_pwd
    _create_account(USER, _hash_pwd(PWD), "personal", None)
    acc = _account_id(USER)
    print("隔离账号：%s (id=%s) | SKIP_LLM=%s\n" % (USER, acc, SKIP_LLM))

    for t in TOOLS:
        reg.save_cli(acc, {"name": t["name"], "bin": t["bin"], "readonly": t["rules"],
                           "abilities": t["ab"], "state": "authed"})
    ents = cp.pool_entries(acc)
    check("8 个探针工具全部进池", len(ents) == len(TOOLS), len(ents))

    # ---- 路由侧（不调模型）：这些问题会走路由还是回退全量
    modes = {}
    for q in [t["q"] for t in TOOLS] + NEG:
        r = tr.route_tools(acc, q)
        modes[q] = (r.mode, r.hits)
    routed = [q for q, (m, _) in modes.items() if m == "routed"]
    print("  路由形态：%d 条走路由 / %d 条回退全量" % (len(routed), len(modes) - len(routed)))
    check("该规模下确实触发了路由（不是全量）", len(routed) >= 6, len(routed))

    c = TestClient(server.app)
    token = c.post("/api/login", json={"username": USER, "password": PWD}).json().get("token")
    check("临时账号登录成功", bool(token))
    if not token:
        _cleanup(USER)
        return summary()

    rows = []
    if SKIP_LLM:
        print("\n[自检] SKIP_LLM=1：只验夹具与被判逻辑（不调模型）")
        conn = get_personal_conn()
        try:
            tid = c.post("/api/chat/sessions", params={"token": token}).json()["thread_id"]
            conn.execute(
                "INSERT INTO messages (conversation_id, turn_index, role, content, tool_calls) "
                "SELECT id, 99, 'assistant', 'x', ? FROM conversations "
                "WHERE account_id=? AND thread_id=?",
                (json.dumps([{"name": "run_shell_command",
                              "args": {"command": TOOLS[0]["bin"], "argv": TOOLS[0]["want"]}}]),
                 acc, tid))
            conn.commit()
        finally:
            conn.close()
        got = _calls_of_thread(acc, tid)
        check("真值配对能读出该会话的调用", got and got[0][0] == TOOLS[0]["bin"], got)
        check("首调判定正确", got[0][1] == TOOLS[0]["want"], got[0])
        check("被判逻辑不会把别的会话算进来",
              _calls_of_thread(acc, "no-such-thread") == [])
        _cleanup(USER)
        return summary()

    print("\n[真机] 逐条探针（每条一次新会话；只看首调）\n")
    todo = [t for t in TOOLS
            if not ONLY or ONLY in t["bin"] or ONLY in t["q"]]
    if ONLY:
        print("  M5B_ONLY=%s → 只跑 %d 条：%s\n"
              % (ONLY, len(todo), [t["bin"] for t in todo]))
    for t in todo:
        mode, hits = modes[t["q"]]
        code, ans, tid = _ask(c, token, t["q"])
        calls = _calls_of_thread(acc, tid)
        first = calls[0] if calls else None
        alts = [tuple(a) for a in (t.get("alts") or [])]
        primary = bool(first and first[0] == t["bin"] and first[1][:len(t["want"])] == t["want"])
        # ⚠️ 比较要用 list(a) 对齐类型：argv 是 list、备选写成 tuple，
        #    `["search","x"][:2] in [("search",)]` 恒为 False（实测踩过，会把备选命中误判成失败）
        alt_hit = bool(first and first[0] == t["bin"]
                       and any(first[1][:len(a)] == list(a) for a in alts))
        ok = primary or alt_hit
        if primary:
            state = "✅ 首调命中（首选）"
        elif alt_hit:
            state = "✅ 首调命中（备选，命令对但需前置解析）"
        elif not calls:
            state = "⛔ 没调工具"
        else:
            state = "❌ 首调是别的工具"
        print("  %-24s [%s] %s" % (t["q"], mode, state))
        print("     期望 %s %s%s ｜ 实得 %s"
              % (t["bin"], t["want"], ("（或备选 %s）" % (alts,) if alts else ""),
                 (first[0] + " " + " ".join(first[1])) if first else "（无）"))
        rows.append(dict(q=t["q"], mode=mode, hits=hits, expect=t["bin"], want=t["want"],
                         got=first, ok=ok, primary=primary, alt=alt_hit, status=code,
                         answer=(ans or "")[:200]))
        check("探针「%s」首调命中 %s" % (t["q"], t["bin"]), ok,
              (first[0] if first else "无调用"))

    print("\n[真机] 反例（与任何工具无关）\n")
    neg_todo = [q for q in NEG if not ONLY or ONLY in q]
    if ONLY and not neg_todo:
        print("  （M5B_ONLY 指定正例，跳过反例——反例无需重验）")
    for q in neg_todo:
        code, ans, tid = _ask(c, token, q)
        calls = _calls_of_thread(acc, tid)
        ok = not calls
        print("  %-22s %s" % (q, "✅ 没调工具" if ok else ("❌ 调了 " + str(calls[:1]))))
        rows.append(dict(q=q, mode=modes[q][0], hits=modes[q][1], expect=None, got=None,
                         ok=ok, status=code, answer=(ans or "")[:200]))
        check("反例「%s」不调工具" % q, ok, calls[:1])

    pos = [r for r in rows if r["expect"]]
    neg = [r for r in rows if not r["expect"]]
    print("\n=== 加压探针结果：正例 %d/%d，反例 %d/%d ==="
          % (len([r for r in pos if r["ok"]]), len(pos), len([r for r in neg if r["ok"]]), len(neg)))
    out = RESULT if not ONLY else RESULT.replace(".json", "_only.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(dict(ts=time.strftime("%F %T"), user=USER, only=ONLY or None, rows=rows), f,
                  ensure_ascii=False, indent=2)
    print("明细已存：%s" % out)
    if ONLY:
        check("单条重跑：该探针通过", all(r["ok"] for r in pos), pos)
    else:
        check("加压正例通过率 ≥ M5a 基线（≥9/10 折算到 8 条即 ≥7/8）",
              len([r for r in pos if r["ok"]]) >= 7, "%d/%d" % (len([r for r in pos if r["ok"]]), len(pos)))

    _cleanup(USER)
    check("清理临时账号与桩目录", _account_id(USER) == 0)
    return summary()


def summary():
    print("\n" + "=" * 60)
    print("M5b 加压探针：通过 %d，失败 %d" % (len(PASS), len(FAIL)))
    if FAIL:
        print("失败项：")
        for f in FAIL:
            print("  -", f)
        return 1
    print("全部通过 ✅")
    return 0


if __name__ == "__main__":
    sys.exit(main())
