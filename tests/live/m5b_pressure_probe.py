# -*- coding: utf-8 -*-
"""M6b 加压探针 · **CLI 薄壳**（v4.0 大点 03 改造，2026-10-10）。

⚠️ 本文件**不再是执行器的实现**：「跑任意题集」的能力（解析 / 水位线 / 清理 / 提问 / 回读真值）
已抽成产品模块 **`tools/probe_runner.py`**（见 `docs/v4.0/Verdict/03-….md` 裁定 9）——
产品客户端（前端「工具池测评」第 6 个 tab）走的就是它。本文件只剩「开发用 CLI 外壳」：

  · 题集从 **JSON** 读（默认 `tests/fixtures/probe_sets/m5b_v2.json` = 原先写死的 43 题，**逐字未改**）；
  · 产出**保持旧格式不变**（v3.1 的资产链，别动 ✗）：
      `data/m5b_pressure_result.json`（原始记录）+ `tests/test_out/m6b_score_sheet*.md`（人工评分表）
      → 供 `tests/live/m6b_score_import.py` 解析成 `tests/fixtures/m5b_probe_baseline.json`，
        再由 `m6b_probe_drift.py` 比「调用序列变没变」✓
  · `SKIP_LLM=1`：离线自检（不调模型 ✓）

铁律（记录侧，不涉判定 ✓）：每条一问、独立新会话；序列从 `messages.tool_calls` 按会话读回
（不采信审计窗口 ✗）；四种调用形状都记；429/5xx 标 `unmeasured` ✓ 与「调错了」分开。

环境变量（与改造前完全一致 ✓）：M5B_USER / M5B_PWD / M5B_SET(core|full) / M5B_IDS(定向复跑)
    / M5B_ONLY(冒烟过滤，会打「不是完整题集」警示) / M5B_KEEP=1 / SKIP_LLM=1 / M5B_SET_FILE(换题集)
参数：`--desktop` 另存一份评分表到桌面；`--no-answer` 不写回答摘录。

运行：
    .venv/Scripts/python.exe tests/live/m5b_pressure_probe.py                    # core 16 条
    M5B_SET=full .venv/Scripts/python.exe tests/live/m5b_pressure_probe.py       # 全量 43 条
    SKIP_LLM=1 .venv/Scripts/python.exe tests/live/m5b_pressure_probe.py         # 离线自检（几秒）
"""
from __future__ import annotations

import json
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

from tools import capability_pool as cp                      # noqa: E402
from tools import probe_runner as pr                          # noqa: E402
from tools import tool_router as tr                           # noqa: E402
from tools.schema_personal import ensure_tables               # noqa: E402

RESULT = os.path.join(ROOT, "data", "m5b_pressure_result.json")
SHEET_DIR = os.path.join(ROOT, "tests", "test_out")
DESKTOP = os.path.join(os.path.expanduser("~"), "Desktop")

SKIP_LLM = os.getenv("SKIP_LLM") == "1"
ONLY = os.getenv("M5B_ONLY", "").strip()
IDS = os.getenv("M5B_IDS", "").strip()
WANT_SET = (os.getenv("M5B_SET", "core").strip().lower() or "core")
KEEP = os.getenv("M5B_KEEP") == "1"
USER = os.getenv("M5B_USER", "尼古喵喵").strip()
PWD = os.getenv("M5B_PWD", "123456")
SET_FILE = os.getenv("M5B_SET_FILE", "m5b_v2.json").strip() or "m5b_v2.json"
TO_DESKTOP = "--desktop" in sys.argv
WITH_ANSWER = "--no-answer" not in sys.argv

OPS: list = []          # 运维类检查（登录/能力池/清理）—— 与"题目对错"无关 ✓


def check(name, cond, extra=""):
    OPS.append((bool(cond), name))
    print("%s %s%s" % ("  ✅" if cond else "  ❌", name, (" | " + str(extra)) if extra else ""))


def ref_text_legacy(case: dict) -> str:
    """参考链路文案 —— **保持旧脚本的逐字格式**（`tests/live/m6b_score_import.py` 要吃它 ✓）。

    与 `probe_runner.ref_text` 的差别只在无 chain 时的写法（旧版直接打 argv 列表），
    故此处刻意保留旧写法，避免动到 v3.1 的解析链。
    """
    if case.get("chain"):
        return str(case["chain"])
    exp = case.get("expect")
    if not exp:
        return "不调用任何工具"
    return ("%s %s" % (exp["bin"], exp["argv"])) if exp["kind"] == "cli" else str(exp["ref"])


# ---------------------------------------------------------------- 评分表（旧格式，别动 ✗）
def write_sheet(rows: list, ts: str, doc: dict) -> str:
    tag = " ｜ ⚠️ 过滤运行" if ONLY else (" ｜ 定向复跑" if IDS else "")
    total, cores = len(doc["cases"]), sum(1 for c in doc["cases"] if c.get("core"))
    lines = ["# M6b 加压探针 · 人工评分表（%s）%s" % (ts, tag), ""]
    if IDS:
        lines += ["## 本轮为定向复跑（题号 %s）" % IDS,
                  "> 只跑这几条，**不是完整题集**；判分口径与全量一致（脚本只记录序列、对错由你打分）。", ""]
    if ONLY:
        lines += ["## ⚠️ 这不是完整题集 ✗",
                  "> 本次带 `M5B_ONLY=%s` → **只跑了 %d 条**（全量 %d / 核心 %d）✗"
                  % (ONLY, len(rows), total, cores),
                  "> 它是**单条冒烟**用的，别当正式评分表 ✗。正式跑请先 `Remove-Item Env:M5B_ONLY`（PS）"
                  "/ `unset M5B_ONLY`（bash）✓", ""]
    lines += ["", "> 账号 `%s` ｜ 题集 `%s` ｜ 共 %d 条 ｜ **脚本不判对错 ✗ 只记录调用序列** ✓"
              % (USER, WANT_SET, len(rows)),
             "> 打分方式：在每题的 `评分：` 后写 `✓`（完全正确）/ `△`（部分正确，见备注）/ `✗`（错）",
             "> 被判为「未测」（429/5xx 限流等）的题建议留空或写 `未测` ✓ 别记成能力问题。", "",
             "## 一、速览（先扫这张表）", "",
             "| # | 问句 | 调用数 | 来源数 | 耗时(s) | 状态 | 评分 |", "|---|---|---|---|---|---|---|"]
    for r in rows:
        st = "未测(%s)" % r["status"] if r["unmeasured"] else str(r["status"])
        lines.append("| %d%s | %s | %d | %d | %s | %s |  |"
                     % (r["id"], "★" if r["core"] else "", r["q"][:34],
                        len(r["calls"]), len(r["sources"]), r["dur_s"], st))
    lines += ["", "## 二、逐题明细（这里打分）", ""]
    for r in rows:
        st = "**未测（%s）** ✗ 别当能力问题" % r["status"] if r["unmeasured"] else str(r["status"])
        lines += ["### #%d%s ｜ %s" % (r["id"], " ★核心" if r["core"] else "", r["q"]),
                  "- 参考链路：`%s`" % r["ref_chain"],
                  "- 路由：`%s` ｜ 来源数：%d ｜ 调用数：%d ｜ 耗时：%ss ｜ 状态：%s"
                  % (r["mode"], len(r["sources"]), len(r["calls"]), r["dur_s"], st)]
        if r["calls"]:
            lines.append("- 实际调用序列：")
            for i, c in enumerate(r["calls"], 1):
                lines.append("  %d. `%s`" % (i, c["brief"]))
            lines.append("  （去重工具：%s）" % "、".join("`%s`" % t for t in r["tools_used"]))
        else:
            lines.append("- 实际调用序列：**（一次工具都没调）**")
        if WITH_ANSWER and r.get("answer"):
            lines.append("- 回答摘录：")
            lines.append("  > %s" % str(r["answer"]).replace("\n", " ")[:180])
        lines += ["- 评分：", "- 备注：", ""]
    lines += ["---", "",
              "## 三、评分口径建议（可自行调整 ✓）",
              "- `✓`：链路完整且都是必要步骤（比如 12306 走了「取时间 → 取站点码 → 查票」）",
              "- `△`：结果对但路径冗余/绕路，或漏了可选步骤（备注里写清是什么）",
              "- `✗`：用错来源/漏关键步骤导致拿不到结果（备注里写清错在哪）",
              "- 未测（429/5xx/超时）**不计入能力评价** ✓",
              "",
              "## 四、回填方式",
              "填完把本文件路径发我 ✓ 我用 `tests/live/m6b_score_import.py` 解析成机器可读基线"
              "（`tests/fixtures/m5b_probe_baseline.json`）→ 以后重跑只比**调用序列有没有变** ✓",
              ""]
    os.makedirs(SHEET_DIR, exist_ok=True)
    tag2 = ts.replace("-", "").replace(":", "").replace(" ", "_")
    path = os.path.join(SHEET_DIR, "m6b_score_sheet%s_%s.md" % ("_only" if ONLY else "", tag2))
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(lines))
    if TO_DESKTOP and os.path.isdir(DESKTOP):
        try:
            import shutil
            dst = os.path.join(DESKTOP, os.path.basename(path))
            shutil.copyfile(path, dst)
            print("评分表已复制到桌面：%s" % dst)
        except OSError as e:  # noqa: BLE001
            print("⚠️ 复制到桌面失败：%s" % e)
    return path


# ---------------------------------------------------------------- 离线自检
def selftest(doc: dict) -> int:
    j = json.dumps
    cases = doc["cases"]
    assert [c["id"] for c in cases] == list(range(1, len(cases) + 1)), "问句编号必须连续 ✗"
    assert sum(1 for c in cases if c.get("core")) == 16, "核心集必须 16 条 ✗"
    covered = set()
    for it in cases:
        exp = it.get("expect")
        if exp and exp["kind"] == "tool":
            covered.add(exp["ref"])
        elif exp:
            covered.add("cli:" + exp["bin"])
    assert len(covered) == 28, "单工具题必须覆盖 28 个工具，实际 %d ✗" % len(covered)
    assert pr.validate_case_set(doc) == [], pr.validate_case_set(doc)

    seq = j([{"name": "invoke_tool", "args": {"ref": "mcp:12306-mcp/get-current-date", "params": {}}},
             {"name": "invoke_tool",
              "args": {"ref": "mcp:12306-mcp/get-station-code-of-citys",
                       "params": {"citys": "成都|重庆"}}},
             {"name": "invoke_tool",
              "args": {"ref": "mcp:12306-mcp/get-tickets",
                       "params": {"date": "2026-10-03", "from": "成都", "to": "重庆"}}}])
    calls = pr.parse_calls(seq)
    assert [c["target"] for c in calls] == [
        "mcp:12306-mcp/get-current-date", "mcp:12306-mcp/get-station-code-of-citys",
        "mcp:12306-mcp/get-tickets"], "调用顺序必须按原始顺序保留 ✗"
    assert calls[1]["source"] == "mcp:12306-mcp", "MCP 来源必须按 `/` 切 ✗"
    assert "成都|重庆" in calls[1]["brief"], "参数摘要必须带上（人工打分要看参数）✗"
    assert pr.parse_calls("{坏 json") == [] and pr.parse_calls("null") == [] and pr.parse_calls("") == []
    cli_calls = pr.parse_calls(j([{"name": "run_shell_command",
                                   "args": {"command": "wechat-cli", "argv": ["history", "袁战"]}}]))
    assert cli_calls[0]["brief"] == "wechat-cli history 袁战", cli_calls
    assert cli_calls[0]["source"] == "cli:wechat-cli", cli_calls
    other = pr.parse_calls(j([{"name": "网络搜索工具", "args": {"q": "x"}}]))
    assert other[0]["source"] == "builtin:网络搜索工具", other
    assert pr.source_of("api:steam#searchGame") == "api:steam"
    assert pr.source_of("mcp:12306-mcp/get-tickets") == "mcp:12306-mcp"
    # ★ 关键：**没有任何判定函数** ✓ 题集里只允许记录 + 参考链路
    assert not hasattr(pr, "judge"), "v3 不该再有 judge() 自动判定 ✗"
    assert not hasattr(sys.modules[__name__], "judge"), "薄壳里也不许有 judge() ✗"
    print("\n✅ 自检通过：调用序列/顺序/参数摘要/来源解析/无自动判定/编号/%d 题覆盖 28 工具 "
          "全部正确 ✓（未调模型 ✓）" % len(cases))
    return 0


# ---------------------------------------------------------------- 主流程
def main() -> int:
    ensure_tables()
    doc = pr.load_case_set(SET_FILE)
    cases = doc["cases"]
    if SKIP_LLM:
        return selftest(doc)

    todo = pr.cases_for(doc, WANT_SET)
    if IDS:
        want = [x.strip() for x in IDS.replace("，", ",").split(",") if x.strip()]
        todo = pr.cases_for(doc, ids=want)
        miss = [x for x in want if not any(str(it["id"]) == x for it in todo)]
        if miss:
            ids_all = sorted((str(it["id"]) for it in cases), key=lambda v: int(v))
            print("✗ M5B_IDS 里有题号不存在：%s（本文件题号：%s）" % (",".join(miss), ",".join(ids_all)))
            return 2
        print("（M5B_IDS 定向复跑：%s → %d 条；跨 core/full 过滤）" % (IDS, len(todo)))
    if ONLY:
        hit = [it for it in todo if ONLY in it["q"] or ONLY in ref_text_legacy(it)
               or ONLY in str(it.get("expect"))]
        if not hit and WANT_SET != "full":
            print("（M5B_ONLY 命中不在 core 集里，已自动扩到全量集 ✓）")
            hit = [it for it in cases if ONLY in it["q"] or ONLY in ref_text_legacy(it)
                   or ONLY in str(it.get("expect"))]
        todo = hit
        if not todo:
            print("✗ M5B_ONLY=%s 没匹配到任何问句" % ONLY)
            return 2
    ts = time.strftime("%F %T")
    total, cores = len(cases), sum(1 for it in cases if it.get("core"))
    print("探针集：%s（%s）｜ 本轮 %d 条（全量 %d / 核心 %d）｜ 模式：**只记录、不判对错** ✓"
          % (WANT_SET, doc.get("name"), len(todo), total, cores))
    if ONLY:
        print("")
        print("  " + "!" * 68)
        print("  ⚠️ 本次是**过滤运行**（M5B_ONLY=%s）→ 只跑了 %d 条，**不是完整题集** ✗" % (ONLY, len(todo)))
        print("  ⚠️ 正式评分请先清掉它：PowerShell `Remove-Item Env:M5B_ONLY` ／ bash `unset M5B_ONLY`")
        print("  " + "!" * 68)
    if IDS:
        print("")
        print("  " + "-" * 68)
        print("  ▸ 本轮为**定向复跑**（题号 %s）→ %d 条；**不是完整题集**，判分口径与全量一致" % (IDS, len(todo)))
        print("  " + "-" * 68)
    print("账号：%s（真实账号 ✓ 跑完按水位线清掉本轮新建数据 ✓）" % USER)

    acc = int(pr.account_by_username(USER).get("id") or 0)
    if not acc:
        print("✗ 账号不存在：%s" % USER)
        return 2
    ents = cp.pool_entries(acc)
    print("\n  能力池：%d 条（该账号真实配置）" % len(ents))
    check("能力池规模与问题集口径相符（≥28 条）", len(ents) >= 28, len(ents))

    modes = {}
    for it in todo:
        r = tr.route_tools(acc, it["q"])
        modes[it["id"]] = (r.mode, [str(x) for x in r.hits])

    wm = pr.watermark(acc, USER)
    c = pr._client()
    token = c.post("/api/login", json={"username": USER, "password": PWD}).json().get("token")
    check("探针账号登录成功", bool(token))
    if not token:
        return 1

    rows = []
    print("\n[真机] 逐条记录（每条一问、独立新会话；只记调用序列 ✓）\n")
    for it in todo:
        mode, hits = modes[it["id"]]
        code, ans, tid, dur = pr.ask(c, token, it["q"])
        calls = pr.calls_of_thread(acc, tid)
        sources = sorted({x["source"] for x in calls if not x["source"].startswith("builtin:")})
        tools_used = []
        for x in calls:                                    # 去重但保持首次出现顺序 ✓
            if x["target"] not in tools_used:
                tools_used.append(x["target"])
        unmeasured = code in pr.UNMEASURED_STATUS or (code != 200 and not calls)
        print("  #%-2d %-28s [%s] 调用 %d 次 ｜ 来源 %d ｜ %s s ｜ 状态 %s%s"
              % (it["id"], it["q"][:28], mode, len(calls), len(sources), dur, code,
                 "  ⚠️ 未测（限流/故障）" if unmeasured else ""))
        for i, x in enumerate(calls, 1):
            print("       %d. %s" % (i, x["brief"][:110]))
        if not calls:
            print("       （一次工具都没调）")
        rows.append(dict(id=it["id"], q=it["q"], kind=it.get("kind") or "single",
                         core=bool(it.get("core")), mode=mode, hits=hits,
                         ref_chain=ref_text_legacy(it), ref_exp=it.get("expect"),
                         calls=calls, sources=sources, tools_used=tools_used,
                         n_calls=len(calls), dur_s=dur, status=code, unmeasured=unmeasured,
                         answer=(ans or "")[:600]))

    out = RESULT if not ONLY else RESULT.replace(".json", "_only.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(dict(ts=ts, user=USER, probe_set=WANT_SET, only=ONLY or None,
                       count=len(rows), scoring="manual（脚本不判对错 ✓）", rows=rows),
                  f, ensure_ascii=False, indent=2)
    print("\n原始记录已存：%s" % out)
    sheet = write_sheet(rows, ts, doc)
    print("人工评分表：%s" % sheet)
    slow = sorted([r for r in rows if r["dur_s"]], key=lambda r: -r["dur_s"])[:3]
    if slow:
        print("最慢三条：#%s" % "、".join("#%d(%ss)" % (r["id"], r["dur_s"]) for r in slow))
    print("未测（429/5xx/超时）：%s" % ([r["id"] for r in rows if r["unmeasured"]] or "无 ✓"))
    print("（本轮**不做任何通过/失败判定** ✓ 对错以你的评分表为准）")

    if KEEP:
        print("\n（M5B_KEEP=1 → 跳过清理 ✓ 本轮产物保留）")
    else:
        print("\n清理本轮产物：%s" % (pr.cleanup_run(wm) or "无需清理 ✓"))
    bad = [n for ok, n in OPS if not ok]
    print("\n" + "=" * 60)
    print("运维检查（与题目对错无关 ✓）：通过 %d / 失败 %d" % (len(OPS) - len(bad), len(bad)))
    for n in bad:
        print("  -", n)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
