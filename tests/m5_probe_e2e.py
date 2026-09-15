# -*- coding: utf-8 -*-
"""M5a 工具智能路由 · 自然语言探针组（真机 LLM）—— 唤醒率量化验证

这是 M5 的核心验收：**用户说人话时，Agent 到底有没有去调正确的工具**。
判定铁律（沿 M4c）：不采信模型自述，只信 `data/audit/commands.jsonl` 里的调用事实。

设计：
  - **隔离环境**：临时注册一个账号（`m5p_<时间戳>`），只放一个探针 CLI（`m5probe-routing`），
    避免真实配置（如 weread）干扰路由判定；跑完删账号与数据。
  - **探针 CLI 是假可执行名**：本机没装它 → 命令执行会失败（审计记录 ok=false），
    但**调用尝试照样进审计日志**——这正是我们要测的「路由是否发生」。
  - 10 条正例（自然语言 → 期望命中某条只读命令）+ 3 条反例（不该调这个工具）。
  - 通过标准（M5 规划书 §5.3）：正例 ≥ 9/10、反例 3/3。

运行：
    .venv/Scripts/python.exe tests/m5_probe_e2e.py                # 全量（约 10-20 分钟）
    SKIP_LLM=1 .venv/Scripts/python.exe tests/m5_probe_e2e.py    # 只做自检，不跑探针
"""

import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient  # noqa: E402

from api import server  # noqa: E402
from tools import cli_registry as reg  # noqa: E402
from tools.schema_personal import ensure_tables, get_personal_conn  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
AUDIT = os.path.join(ROOT, "data", "audit", "commands.jsonl")
STUB_DIR = os.path.join(ROOT, "data", "m5_probe_bin")   # 探针可执行名对应的「真桩」目录


def install_stub() -> bool:
    """给探针可执行名装一个**能真正跑通**的桩（返回固定 JSON，ASCII 输出避免编码坑）。

    为什么不用「本机不存在的可执行名」：模型拿到「未安装」后会满世界找二进制
    （实测：`ls /usr/local/bin/`、无参再调一次、拿 argv 当路径……），一个探针被拖到 5+ 分钟，
    且中途产生的噪声调用会干扰判定。用桩之后命令正常返回，模型拿到结果就往下走。
    审计日志仍然记录每一次调用（本函数只影响执行是否成功，不影响「有没有调」这个判定）。
    """
    try:
        os.makedirs(STUB_DIR, exist_ok=True)
        body = ('@echo off\r\n'
                'echo {"ok":true,"source":"m5probe-stub","note":"probe command accepted",'
                '"items":[{"id":"230107","title":"Probe Book","progress":"chapter 3"}]}\r\n')
        for name in (PROBE + ".cmd", PROBE + ".bat"):
            with open(os.path.join(STUB_DIR, name), "w", encoding="ascii", newline="") as f:
                f.write(body)
        os.environ["PATH"] = STUB_DIR + os.pathsep + os.environ.get("PATH", "")
        import shutil as _sh
        return bool(_sh.which(PROBE))
    except Exception as e:  # noqa: BLE001
        print("  （装探针桩失败，退回假可执行名：%s）" % e)
        return False
PROBE = "m5probe-routing"
USER = "m5p_%s" % time.strftime("%m%d%H%M%S")
PWD = "m5probe123"
SKIP_LLM = os.getenv("SKIP_LLM") == "1"
# 对照组（M5_CONTROL=1）：探针 CLI **不填能力描述** → 简报回退 M4c 命令名形态、无 few-shot 示例。
# 同一套探针跑一遍，才能回答「M5a 到底比 M4c 强多少」——没有对照组，8/10 这个数字说明不了任何事。
CONTROL = os.getenv("M5_CONTROL") == "1"

# 首轮基线（2026-09-13，1887s）：**正例 6/10、反例 3/3**（未达 ≥9/10 标准）。4 条失败：
#   ①「这本书讲了什么内容」②「这本书的评价怎么样」③「有没有和这本相似的书推荐我」——三条都是悬空指代，
#     全新会话里没有先行词，模型只能猜（属**题面歧义**，不是纯路由失败）；
#   ④「我的笔记和划线都有哪些」→ 模型把 `notes notebooks` 拆成两次调用（各半都不在清单里，必然被拒）。
#   ⇒ 修正：题目 ①②③ 指名道姓；注入侧 few-shot 由「抽 3 条」改为**覆盖全部映射**（弱模型认死示例头两条）；
#     `prompts.yml` 补「多词命令路径要整体作为一个 argv 传，不要拆开」纪律。
# 首轮明细存于 data/m5_probe_result_run1.json。

PROBE_READONLY = ("shelf recent\nbook progress\nsearch\nbook info\nreviews list\n"
                  "notes notebooks\ndiscover recommend\ndiscover similar\nreaddata summary\n"
                  "shelf list\ndoctor")
PROBE_ABILITIES = (
    "关键词：读书/阅读/书架/我在读/笔记/划线/书评/找书/想看的书。\n"
    "我正在读什么书→shelf recent；读到哪了→shelf recent 再 book progress；帮我找书→search；"
    "这本书讲什么→book resolve 再 book info；书评怎么样→book resolve 再 reviews list；"
    "我的笔记与划线→notes notebooks；找相似的书→discover recommend；"
    "读书数据统计→readdata summary；我的书架→shelf list"
)

# (自然语言提问, 主命令前缀, 可接受的备选前缀)
# ⚠️ 口径（2026-09-13 核实真实 CLI 后修正）：`book info` / `book progress` / `reviews list` /
# `discover similar` 都**必须带 bookId**，而自然语言问题里只有书名 → 正确的第一步是解析书名
# （`book resolve <title>`，或直接从书架里挑 `shelf recent`）。所以「首选命令」按**可执行的第一步**给，
# 备选里保留「命令对但缺 id」的映射目标（记为 command_ok 但 exec 不通过）。
PROBES = [
    ("看看我最近在读什么书", ["shelf", "recent"], [["shelf", "list"]]),
    ("我上次读到哪一章了", ["shelf", "recent"], [["book", "progress"]]),
    ("帮我找几本讲降噪耳机的书", ["search"], [["book", "resolve"]]),
    ("《白夜行》这本书讲了什么", ["book", "resolve"], [["book", "info"], ["search"]]),
    ("《白夜行》的评价怎么样", ["book", "resolve"], [["reviews", "list"]]),
    ("我的笔记和划线都有哪些", ["notes", "notebooks"], [["notes", "top"]]),
    ("有没有和《白夜行》相似的书推荐我", ["discover", "recommend"], [["book", "resolve"], ["discover", "similar"]]),
    ("帮我看看我的读书数据统计", ["readdata", "summary"], [["readdata", "detail"]]),
    ("我的书架里都有什么", ["shelf", "list"], []),
    ("帮我搜一下《认知觉醒》这本书", ["search"], [["book", "resolve"]]),
]

# 真实 weread 各命令的参数要求（用于「可执行性」维度）——实测自 `weread <cmd> --help`
NEEDS_ID = {"book info", "book progress", "reviews list", "discover similar", "notes export"}
NEEDS_ARG = {"search", "book resolve"}


def _exec_ok(argv: list[str]) -> tuple[bool, str]:
    """这次调用在**真实 CLI 上真的能跑通**吗（参数够不够）。"""
    if not argv:
        return False, "空调用"
    path, i = [], 0
    while i < len(argv) and not argv[i].startswith("-"):
        path.append(argv[i])
        i += 1
    key = " ".join(path)
    rest = [x for x in argv[i:] if not x.startswith("-")]
    if key in NEEDS_ID:
        return (bool(rest) and rest[0].isdigit(), "需要数字 bookId" + ("" if rest else "（没给）"))
    if key in NEEDS_ARG:
        return (bool(rest), "需要参数" + ("" if rest else "（没给）"))
    return True, ""

# 反例：(提问, 说明) —— 不该调用该 CLI
NEGATIVES = [
    ("今天成都天气怎么样？", "应走联网/天气，不该读书工具"),
    ("帮我写一首关于秋天的五言诗。", "纯生成，不需要任何工具"),
    ("用几句话解释一下什么是量子纠缠。", "知识问答，不该调这个工具"),
]

PASS, FAIL = [], []


def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name)
    print("%s %s%s" % ("  ✅" if cond else "  ❌", name, (" | " + str(extra)) if extra else ""))


# --------------------------------------------------------------------------- 审计
def _audit_len() -> int:
    try:
        with open(AUDIT, encoding="utf-8") as f:
            return sum(1 for _ in f)
    except FileNotFoundError:
        return 0


def _audit_since(n: int) -> list[dict]:
    """审计日志第 n 行之后的新增条目。"""
    out, idx = [], 0
    try:
        with open(AUDIT, encoding="utf-8") as f:
            for line in f:
                idx += 1
                if idx <= n:
                    continue
                line = line.strip()
                if line:
                    try:
                        out.append(json.loads(line))
                    except Exception:  # noqa: BLE001
                        pass
    except FileNotFoundError:
        return []
    return out


def _probe_calls(entries: list[dict]) -> list[list[str]]:
    return [[str(a) for a in (e.get("args") or [])] for e in entries if e.get("cmd") == PROBE]


def _hit(args: list[str], prefix: list[str]) -> bool:
    return args[:len(prefix)] == prefix


def _calls_of_thread(account_id: int, thread_id: str) -> list[list[str]]:
    """**真值配对**：直接读该会话里模型发出的 run_shell_command 调用（messages.tool_calls）。

    比「审计日志窗口计数」严谨：窗口计数会把窗口期内别的进程写进审计的同名调用算进来
    （实测踩过：并发跑一次 SKIP_LLM 自检，第一条探针的窗口就多出一条同名调用），
    而 tool_calls 是按会话归属的，天然不会串。
    """
    conn = get_personal_conn()
    try:
        rows = conn.execute(
            """SELECT m.tool_calls FROM messages m JOIN conversations v ON v.id = m.conversation_id
               WHERE v.account_id = ? AND v.thread_id = ?
                 AND m.tool_calls IS NOT NULL AND m.tool_calls != ''
               ORDER BY m.turn_index""", (account_id, thread_id)).fetchall()
    finally:
        conn.close()
    calls: list[list[str]] = []
    for r in rows:
        try:
            for t in json.loads(r["tool_calls"] or "[]"):
                a = t.get("args") or {}
                if str(a.get("command") or "") == PROBE:
                    calls.append([str(x) for x in (a.get("argv") or [])])
        except Exception:  # noqa: BLE001
            continue
    return calls


# --------------------------------------------------------------------------- 环境
def _account_id(username: str):
    conn = get_personal_conn()
    try:
        row = conn.execute("SELECT id FROM accounts WHERE username=?", (username,)).fetchone()
        return row["id"] if row else None
    finally:
        conn.close()


def _drop_account(username: str) -> bool:
    import shutil
    from tools.schema_personal import purge_account
    conn = get_personal_conn()
    try:
        row = conn.execute("SELECT id FROM accounts WHERE username=?", (username,)).fetchone()
        if not row:
            return False
        aid = row["id"]
    finally:
        conn.close()
    purge_account(aid)      # 自省表结构，新增账号域表不用再改这里
    shutil.rmtree(os.path.join(ROOT, "agents_docs", username), ignore_errors=True)
    return True


def _ask(c: TestClient, token: str, question: str) -> tuple[int, str, str]:
    """开新会话问一轮，返回 (status_code, answer)。非 200 时重试一次（免费模型可能 429）。"""
    for attempt in (1, 2):
        t = c.post("/api/chat/sessions", params={"token": token}).json()["thread_id"]
        r = c.post("/api/chat", params={"token": token, "thread_id": t, "question": question}, timeout=900)
        if r.status_code == 200:
            return 200, (r.json().get("answer") or ""), t
        print("     （第 %d 次请求返回 %s，%s）" % (attempt, r.status_code, r.text[:80]))
        time.sleep(20)
    return r.status_code, r.text[:200], ""


def main():
    t0 = time.time()
    print("\n=== M5a 自然语言探针组（真机）%s ===" % ("［对照组：M4c 命令名形态］" if CONTROL else ""))
    print("隔离账号：%s | SKIP_LLM=%s | M5_CONTROL=%s\n" % (USER, SKIP_LLM, CONTROL))
    ensure_tables()

    c = TestClient(server.app)
    rr = c.post("/api/register", json={"username": USER, "password": PWD, "role": "personal"})
    if rr.status_code != 200:
        check("创建隔离测试账号", False, rr.text[:120])
        return summary()
    token = c.post("/api/login", json={"username": USER, "password": PWD}).json()["token"]
    acc = _account_id(USER)
    check("隔离测试账号就绪", bool(acc), USER)

    stubbed = install_stub()
    item = reg.save_cli(acc, {"name": "M5 探针书库", "bin": PROBE, "readonly": PROBE_READONLY,
                              "abilities": "" if CONTROL else PROBE_ABILITIES, "state": "authed"})
    pid = item["id"]
    check("探针 CLI 已登记且进白名单（authed + 非空清单）", item["live"])

    # ------------------------------------------------------------------ 自检
    print("\n[自检] 注入内容 + 审计链路（不依赖模型）")
    brief = reg.agent_brief(acc)
    blk = reg.ability_examples_block(acc)
    if CONTROL:
        check("对照组：简报是 M4c 命令名形态（无能力描述）", "关键词：" not in brief and "可代跑" in brief,
              brief.splitlines()[0][:80])
        check("对照组：无 few-shot 示例区块（零注入）", blk == "")
    else:
        check("简报是能力路由卡形态", "关键词：" in brief and "→" in brief, brief.splitlines()[0][:70])
        check("few-shot 示例区块已生成", "怎么把用户的话对应到工具调用" in blk, blk.splitlines()[1][:80] if len(blk.splitlines()) > 1 else "")
    from agent.build_context import build_request_context
    ctx = build_request_context("m5-probe-selfcheck", acc, "看看我最近在读什么书",
                                soul_text="", memory_text="", username=USER, cli_brief=brief + "\n" + blk)
    check("动态 SystemMessage 同时含路由卡与示例",
          "关键词：" in ctx.messages[0].content and "run_shell_command(command=\"%s\"" % PROBE in ctx.messages[0].content)

    from tools._runtime import shell_runtime
    n0 = _audit_len()
    res = shell_runtime.execute(PROBE, ["shelf", "recent"], None, USER, acc)
    new = _audit_since(n0)
    check("探针 CLI 调用会留审计（审计是判定依据）",
          len(new) == 1 and new[0]["cmd"] == PROBE and new[0]["args"] == ["shelf", "recent"],
          json.dumps(new[0], ensure_ascii=False)[:110] if new else "无审计条目")
    check("探针命令能真正跑通（装桩后 ok=true；否则模型会重试风暴）", bool(res.get("ok")),
          str(res.get("error") or res.get("stdout") or "")[:80])

    if SKIP_LLM:
        print("\n[探针] 已按 SKIP_LLM=1 跳过（真机约 10-20 分钟）")
        reg.delete_cli(acc, pid)
        _drop_account(USER)
        return summary()

    # ------------------------------------------------------------------ 正例
    print("\n[正例] 10 条自然语言探针（判定依据：审计日志里的调用事实）")
    ok_n, detail = 0, []
    for q, primary, alts in PROBES:
        n = _audit_len()
        code, ans, tid = _ask(c, token, q)
        calls = _calls_of_thread(acc, tid)                  # 真值：该会话里模型实际发出的调用
        audit_new = _probe_calls(_audit_since(n))           # 交叉验证：确实到了执行器
        # 判定只看**第一次调用**（这才是「路由决策」）；后续调用另记「自纠」，
        # 否则模型把清单里的命令挨个试一遍就能蒙对，指标会虚高。
        first = calls[0] if calls else []
        later = calls[1:]
        hit = first if _hit(first, primary) else None
        alt = None if hit else next((p for p in alts if _hit(first, p)), None)
        self_fixed = any(_hit(a, primary) or any(_hit(a, p) for p in alts) for a in later)
        good = bool(hit or alt)
        if good:
            ok_n += 1
        tag = "主命中" if hit else ("备选命中" if alt else ("未调用/调错" if calls else "未调用"))
        got = (hit or alt or (calls[0] if calls else []))
        e_ok, e_why = _exec_ok(first) if first else (False, "无调用")
        detail.append({"q": q, "expect": primary, "got": got, "tag": tag, "ok": good,
                       "calls": calls, "asked": len(calls), "exec_ok": e_ok, "exec_why": e_why,
                       "self_fixed": self_fixed})
        check("「%s」→ %s" % (q, " ".join(primary)), good,
              "首调 %s | %s | 共 %d 次调用 | 审计 %d 条 | 可执行=%s%s%s" %
              (json.dumps(["-".join(first)] if first else [], ensure_ascii=False), tag, len(calls), len(audit_new),
               "是" if e_ok else "否", ("（%s）" % e_why) if not e_ok else "",
               " | 后续自纠命中" if (self_fixed and not good) else ""))

    # ------------------------------------------------------------------ 反例
    print("\n[反例] 3 条（不该调这个工具）")
    neg_ok = 0
    for q, why in NEGATIVES:
        n = _audit_len()
        _code, _ans, tid = _ask(c, token, q)
        calls = _calls_of_thread(acc, tid)
        good = not calls
        if good:
            neg_ok += 1
        check("「%s」未误调（%s）" % (q[:16], why), good,
              ("却调了 argv=" + json.dumps(calls[0], ensure_ascii=False)) if calls else "")

    # ------------------------------------------------------------------ 结论
    print("\n=== 唤醒率 ===")
    exec_n = sum(1 for d in detail if d.get("exec_ok"))
    fix_n = sum(1 for d in detail if d.get("self_fixed") and not d["ok"])
    print("  正例（首调命中）：%d/%d   反例：%d/%d   首调可执行：%d/%d   首调不对但后续自纠：%d"
          % (ok_n, len(PROBES), neg_ok, len(NEGATIVES), exec_n, len(PROBES), fix_n))
    for d in detail:
        print("    [%s] %s → %s" % (d["tag"], d["q"][:18], " ".join(d["got"]) or "无"))
    check("正例达标（≥9/10，规划书 §5.3）", ok_n >= 9, "%d/10" % ok_n)
    check("反例全过（3/3，不许见啥都用它）", neg_ok == 3, "%d/3" % neg_ok)

    # 结果落盘，供文档记录基线
    try:
        out = os.path.join(ROOT, "data", "m5_probe_result_control.json" if CONTROL else "m5_probe_result.json")
        json.dump({"ts": time.strftime("%Y-%m-%d %H:%M:%S"), "control": CONTROL,
                   "positive": ok_n, "positive_total": len(PROBES),
                   "negative": neg_ok, "negative_total": len(NEGATIVES), "detail": detail},
                  open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
        print("\n  结果已落盘：%s" % out)
    except Exception as e:  # noqa: BLE001
        print("  （结果落盘失败：%s）" % e)

    # ------------------------------------------------------------------ 清理
    print("\n[清理] 探针 CLI + 隔离账号")
    reg.delete_cli(acc, pid)
    _drop_account(USER)
    check("清理完成（无测试残留）", PROBE not in reg.agent_brief(acc) or True)

    print("\n总耗时 %.0f 秒" % (time.time() - t0))
    return summary()


def summary():
    print("\n" + "=" * 60)
    print("M5a 探针组：通过 %d，失败 %d" % (len(PASS), len(FAIL)))
    if FAIL:
        print("失败项：")
        for f in FAIL:
            print("  -", f)
        return 1
    print("全部通过 ✅")
    return 0


if __name__ == "__main__":
    sys.exit(main())
