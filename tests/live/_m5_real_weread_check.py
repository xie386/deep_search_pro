# -*- coding: utf-8 -*-
"""M5a 真实场景复核（真实账号 + 真实 weread CLI，临时脚本 `_` 前缀）

探针组用的是**假可执行名**——它只能证明「路由发生了」，证明不了「拿到真数据」。
本脚本跑真实场景，三层证据一次到位：

  ① 注入证据：打印该账号实际会收到的能力路由卡 + few-shot 示例（冻结文本）
  ② 路由证据：问一句自然语言（如「看看我最近在读什么书」），
     从库里读该会话的 `messages.tool_calls`（**真值配对**，不看模型自述）
  ③ 执行证据：调用必须命中只读清单；且回答里应出现**只可能来自 CLI 的真实数据**

默认只读不写；跑完删除自己创建的会话（不留测试痕迹）。

运行：
    .venv/Scripts/python.exe tests/_m5_real_weread_check.py
    .venv/Scripts/python.exe tests/_m5_real_weread_check.py --question "我的笔记和划线都有哪些"
"""

import argparse
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

from fastapi.testclient import TestClient  # noqa: E402

from api import server  # noqa: E402
from tools import cli_registry as reg  # noqa: E402
from tools.schema_personal import get_personal_conn  # noqa: E402

USER, PWD = "尼古喵喵", "123456"
BIN = "weread"
PASS, FAIL = [], []


def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name)
    print("%s %s%s" % ("  ✅" if cond else "  ❌", name, (" | " + str(extra)) if extra else ""))


def calls_of_thread(account_id: int, thread_id: str):
    conn = get_personal_conn()
    try:
        rows = conn.execute(
            """SELECT m.tool_calls FROM messages m JOIN conversations v ON v.id = m.conversation_id
               WHERE v.account_id = ? AND v.thread_id = ?
                 AND m.tool_calls IS NOT NULL AND m.tool_calls != ''
               ORDER BY m.turn_index""", (account_id, thread_id)).fetchall()
    finally:
        conn.close()
    out = []
    for r in rows:
        try:
            for t in json.loads(r["tool_calls"] or "[]"):
                a = t.get("args") or {}
                out.append((str(a.get("command") or ""), [str(x) for x in (a.get("argv") or [])]))
        except Exception:  # noqa: BLE001
            continue
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--question", default="看看我最近在读什么书")
    ap.add_argument("--account", default=USER)
    ap.add_argument("--password", default=PWD)
    ap.add_argument("--with-draft", action="store_true",
                    help="额外真机验一次「✨ 按只读清单生成草稿」接口（多一次 LLM 调用）")
    args = ap.parse_args()

    print("\n=== M5a 真实场景复核（真实账号 + 真实 CLI）===")
    print("账号：%s | 提问：%s\n" % (args.account, args.question))

    c = TestClient(server.app)
    r = c.post("/api/login", json={"username": args.account, "password": args.password})
    if r.status_code != 200:
        check("登录", False, r.text[:120])
        return summary()
    j = r.json()
    token, username = j["token"], j["account"]["username"]
    acc = j["account"].get("id")
    if acc is None:
        conn = get_personal_conn()
        try:
            acc = conn.execute("SELECT id FROM accounts WHERE username=?", (username,)).fetchone()["id"]
        finally:
            conn.close()
    check("登录", True, username)

    # ---- ① 注入证据 ----
    print("\n[1] 注入证据（模型实际会收到的文本）")
    items = reg.list_clis(acc)
    live = [x for x in items if x["live"]]
    check("该账号有已接入的 CLI", bool(live), [x["bin"] for x in live])
    weread = next((x for x in live if x["bin"] == BIN), None)
    if not weread:
        check("存在 %s 且已接入" % BIN, False, "（该账号没配 weread，脚本无法继续）")
        return summary()
    check("%s 已接入且清单非空（%d 条只读命令）" % (BIN, len(weread["rules"])), bool(weread["rules"]))
    check("%s 已填能力描述（M5 路由卡的前提）" % BIN, bool((weread.get("abilities") or "").strip()),
          "%d 字" % len(weread.get("abilities") or ""))

    brief = reg.agent_brief(acc)
    block = reg.ability_examples_block(acc)
    wb = [b for b in brief.split("\n- ") if BIN in b]
    check("简报里 %s 走的是能力路由卡形态" % BIN, bool(wb) and "关键词：" in brief)
    check("简报里含「私有数据优先」决断指令", "私有数据" in brief and "不要改用别的工具" in brief)
    ex = reg.ability_examples(acc, max_n=20, max_per_cli=20)
    check("few-shot 示例覆盖全部映射", len(ex) >= 5, "%d 条" % len(ex))
    print("\n  ── 该账号 %s 的路由卡 ──" % BIN)
    for line in (wb[0].splitlines() if wb else []):
        print("   ", line[:110])
    print("  ── 示例（前 3 条）──")
    for line in ex[:3]:
        print("   ", line[:110])

    # ---- ② 路由证据（真机一轮） ----
    print("\n[2] 路由证据（真机一轮对话）")
    tid = c.post("/api/chat/sessions", params={"token": token}).json()["thread_id"]
    rr = c.post("/api/chat", params={"token": token, "thread_id": tid, "question": args.question}, timeout=900)
    check("对话请求成功（200）", rr.status_code == 200, rr.status_code)
    answer = (rr.json().get("answer") or "") if rr.status_code == 200 else ""
    calls = calls_of_thread(acc, tid)
    wa = [(cmd, argv) for cmd, argv in calls if cmd == BIN]
    check("模型真的调用了 %s" % BIN, bool(wa), json.dumps([a for _, a in wa], ensure_ascii=False)[:140])

    # ---- ③ 执行证据 ----
    print("\n[3] 执行证据（命中只读清单 + 真数据）")
    ok_whitelist = all(reg.readonly_verdict(argv, weread["rules"])[0] for _, argv in wa) if wa else False
    check("所有调用都命中只读白名单（没有越权/没有白撞）", ok_whitelist,
          "; ".join("%s → %s" % ("readonly_verdict", reg.readonly_verdict(argv, weread["rules"])) for _, argv in wa[:2]))
    check("回答非空且不是「工具不可用」式话术",
          bool(answer) and ("未安装" not in answer) and ("不在" not in answer or "白名单" not in answer),
          answer[:80].replace("\n", " "))
    print("\n  ── 回答节选 ──")
    print("  " + answer[:600].replace("\n", "\n  "))

    # ---- ④（可选）AI 草稿接口真机验证 ----
    if args.with_draft:
        print("\n[4] AI 草稿接口（真机一次 LLM 调用）")
        rd = "\n".join(" ".join(r) for r in weread["rules"])
        dr = c.post("/api/cli/ability_draft", params={"token": token},
                    json={"name": weread["name"], "bin": weread["bin"], "readonly": rd}, timeout=180)
        check("POST /api/cli/ability_draft 返回 200", dr.status_code == 200, dr.text[:120])
        content = (dr.json().get("content") or "") if dr.status_code == 200 else ""
        check("草稿非空且含关键词行", "关键词" in content, content[:60].replace("\n", " "))
        # 草稿里出现的命令路径必须都在只读清单里（AI 有可能编造 → 这里盯死）
        bad = []
        for seg in re.split(r"[；;\n]", content):
            if "→" in seg or "->" in seg:
                right = re.split(r"→|->", seg, 1)[1]
                first = re.split(r"或|/|，|,", right)[0].strip()
                toks = first.split()
                if toks and not reg.readonly_verdict(toks, weread["rules"])[0]:
                    bad.append(first)
        check("草稿里的命令路径都真实存在于只读清单（不编造）", not bad, bad[:3])
        print("\n  ── 草稿全文 ──")
        print("  " + content[:500].replace("\n", "\n  "))

    # 清掉本次会话（不留痕迹）
    try:
        c.delete("/api/chat/sessions/%s" % tid, params={"token": token})
        check("清理本次验证会话", True)
    except Exception as e:  # noqa: BLE001
        check("清理本次验证会话", False, str(e)[:80])

    return summary()


def summary():
    print("\n" + "=" * 60)
    print("真实场景复核：通过 %d，失败 %d" % (len(PASS), len(FAIL)))
    if FAIL:
        print("失败项：")
        for f in FAIL:
            print("  -", f)
        return 1
    print("全部通过 ✅")
    return 0


if __name__ == "__main__":
    sys.exit(main())
