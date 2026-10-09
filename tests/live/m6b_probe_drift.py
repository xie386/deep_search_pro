# -*- coding: utf-8 -*-
"""M6b 漂移对比器 **v3**：重跑探针 → **比调用序列变没变**（不做任何对错判定 ✗）。

为什么推翻 v2 那套（2026-10-01 制度变更 ✓）：
  · v2 的「命中/未命中 + 中位数 − 2 题告警」建立在**自动判分**上 ✗ —— 而自动判分已被证明
    对**链式工具**必然误判（12306 要「时间 → 站点码 → 查票」一串 ✓），所以**整套弃用** ✗；
  · 现在的对错在**人工评分表**里（`m6b_score_import.py` 解析成基线 ✓），
    本脚本只干两件事：① 连跑 N 轮记录；② 与基线**逐题比调用序列**（多了/少了/顺序变了）✓。

判据口径（与 v2 的本质区别 ✓）：
  · **不判对错** ✗ —— 序列变了就报出来，是好是坏由人看（可能只是模型换了路径但仍正确 ✓）；
  · `未测`（429/5xx/超时）单列 ✓ 不参与序列对比（它不是"路径变了"，是"没测到" ✓）；
  · 基线里**没填评分**的题不参与对比 ✓（人工没确认的路径不能当标准）。

跑法：
    .venv/Scripts/python.exe tests/live/m6b_probe_drift.py --diff              # 拿最近一次记录比基线
    .venv/Scripts/python.exe tests/live/m6b_probe_drift.py --rounds 3 --gap 90 --yes   # 连跑 3 轮并逐轮比对
    .venv/Scripts/python.exe tests/live/m6b_probe_drift.py --selftest          # 离线自检
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PROBE = os.path.join(ROOT, "tests", "live", "m5b_pressure_probe.py")
RUN = os.path.join(ROOT, "data", "m5b_pressure_result.json")
BASELINE = os.path.join(ROOT, "tests", "fixtures", "m5b_probe_baseline.json")


def seq_key(calls) -> str:
    """把一次记录的调用序列压成可比字符串（用每题都有的 brief ✓）。

    ★ 2026-10-02：**必须先把真实换行归一成 `\\n` 字面量** ✗ —— 模型偶尔吐出带换行的畸形参数
    （实测 #36 的 XML 泄漏），评分表里那条会被拆行、回填时按续行并回来（含 `\\n` 标记），
    而记录 JSON 里是**真换行** → 两边不归一就会误报"序列变化" ✓。
    """
    def _n(s) -> str:
        return str(s).replace("\r", "").replace("\n", "\\n")
    return " || ".join(_n(c.get("brief") or c.get("target") or "") for c in (calls or []))


def compare(run: dict, base: dict) -> dict:
    """逐题比序列。返回 {changed, same, unmeasured, not_in_baseline, per_id}。"""
    brows = {int(r["id"]): r for r in base.get("rows", []) if r.get("score")}
    out = {"per_id": {}, "changed": [], "same": [], "unmeasured": [], "untested_base": []}
    for r in run.get("rows", []):
        i = int(r["id"])
        if r.get("unmeasured"):
            out["unmeasured"].append(i)
            continue
        b = brows.get(i)
        if not b:
            out["untested_base"].append(i)
            continue
        now, was = seq_key(r.get("calls")), b.get("seq_key") or ""
        if now == was:
            out["same"].append(i)
        else:
            out["changed"].append(i)
            out["per_id"][i] = {"q": r.get("q"), "score": b.get("score"),
                                "baseline": b.get("seq", []), "now": [c.get("brief") for c in
                                                                     (r.get("calls") or [])]}
    return out


def report(run: dict, base: dict, title: str) -> bool:
    c = compare(run, base)
    print("\n" + "=" * 72)
    print("%s（基线来自 %s，%d 题有人工评分 ✓）"
          % (title, os.path.basename(base.get("source_sheet", "-")), len(
              [r for r in base.get("rows", []) if r.get("score")])))
    print("  序列一致 %d 题：%s" % (len(c["same"]), c["same"] or "无"))
    print("  未测（429/5xx/超时，**不比对** ✓）%d 题：%s" % (len(c["unmeasured"]), c["unmeasured"] or "无"))
    print("  基线无评分（不比对 ✓）%d 题：%s" % (len(c["untested_base"]), c["untested_base"] or "无"))
    print("  ★ 序列变化 %d 题：%s" % (len(c["changed"]), c["changed"] or "无 ✓"))
    for i, d in c["per_id"].items():
        print("\n  #%d %s（基线评分 %s）" % (i, str(d["q"])[:44], d["score"]))
        print("     基线：%s" % "  →  ".join(d["baseline"]) if d["baseline"] else "     基线：（空）")
        print("     本次：%s" % ("  →  ".join(d["now"]) if d["now"] else "（一次工具都没调）"))
    return bool(c["changed"])


def run_round(i: int, timeout_s: int) -> dict:
    print("\n" + "=" * 72)
    print("第 %d 轮：%s" % (i, os.path.basename(PROBE)))
    print("=" * 72)
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)          # ★ Hermes 注入的 PYTHONPATH 会污染子进程 ✓
    env.pop("PYTHONIOENCODING", None)
    env["PYTHONUTF8"] = "1"
    t0 = time.time()
    try:
        subprocess.run([sys.executable, PROBE], cwd=ROOT, env=env, timeout=timeout_s)
    except subprocess.TimeoutExpired:
        print("⚠️ 本轮超时（%ss）→ 记未测 ✓" % timeout_s)
    dur = round(time.time() - t0, 1)
    if not os.path.exists(RUN):
        print("✗ 没找到记录文件 %s" % RUN)
        return {"ts": time.strftime("%F %T"), "duration_s": dur, "rows": []}
    with open(RUN, encoding="utf-8") as fh:
        d = json.load(fh)
    d["duration_s"] = dur
    d["round"] = i
    return d


def _selftest() -> int:
    base = {"source_sheet": "x.md", "rows": [
        {"id": 15, "score": "✓", "seq": ["a", "b", "c"], "seq_key": "a || b || c"},
        {"id": 18, "score": None, "seq": ["z"], "seq_key": "z"},
        {"id": 42, "score": "✓", "seq": [], "seq_key": ""}]}
    same = {"rows": [{"id": 15, "unmeasured": False, "calls": [{"brief": x} for x in ("a", "b", "c")]},
                     {"id": 18, "unmeasured": False, "calls": [{"brief": "OTHER"}]},
                     {"id": 42, "unmeasured": False, "calls": []}]}
    c = compare(same, base)
    assert c["same"] == [15, 42] and not c["changed"], c
    assert c["untested_base"] == [18], "基线没评分的题不许比对 ✗"
    diff = {"rows": [{"id": 15, "unmeasured": False, "calls": [{"brief": x} for x in ("a", "b")]},
                     {"id": 19, "unmeasured": True, "calls": []}]}
    c2 = compare(diff, base)
    assert c2["changed"] == [15] and c2["unmeasured"] == [19], c2
    assert c2["per_id"][15]["baseline"] == ["a", "b", "c"], c2
    print("\n✅ 自检通过：序列一致 / 变化 / 未测单列 / 基线未评分不比对 全部正确 ✓（未调模型 ✓）")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rounds", type=int, default=1, help="连跑几轮（默认 1 ✓）")
    ap.add_argument("--gap", type=int, default=90, help="轮间隔秒数（默认 90 ✓）")
    ap.add_argument("--timeout", type=int, default=3600, help="单轮超时秒数")
    ap.add_argument("--diff", action="store_true", help="只拿最近一次记录比基线（不跑）")
    ap.add_argument("--baseline", default=BASELINE)
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--yes", action="store_true", help="跳过长跑确认")
    args = ap.parse_args()

    if args.selftest:
        return _selftest()
    if not os.path.exists(args.baseline):
        print("✗ 还没有基线：先跑探针 → 人工评分 → `m6b_score_import.py <评分表.md>` ✓")
        return 2
    with open(args.baseline, encoding="utf-8") as fh:
        base = json.load(fh)

    if args.diff:
        if not os.path.exists(RUN):
            print("✗ 还没有本机记录：先跑一次探针 ✓")
            return 2
        with open(RUN, encoding="utf-8") as fh:
            run = json.load(fh)
        changed = report(run, base, "与基线对比（最近一次记录 %s）" % run.get("ts"))
        print("\n结论：%s（**序列变化不等于变差** ✓ 是好是坏看人：序列内容 + 你的评分口径）"
              % ("有 %d 题序列变了，需人工看" % len([x for x in [changed] if x]) if changed
                 else "序列完全一致 ✓"))
        return 1 if changed else 0

    if args.rounds > 1 and not args.yes:
        ans = input("连跑 %d 轮（每轮约 8~25 分钟）确认？(yes/NO) " % args.rounds).strip().lower()
        if ans not in ("y", "yes"):
            print("已取消 ✓")
            return 0
    results = []
    for i in range(1, args.rounds + 1):
        results.append(run_round(i, args.timeout))
        if i < args.rounds:
            print("间隔 %ds 后继续…" % args.gap)
            time.sleep(args.gap)
    worst = []
    for r in results:
        if r.get("rows"):
            if report(r, base, "第 %d 轮对比" % r.get("round", 0)):
                worst.append(r.get("round"))
    out = os.path.join(ROOT, "data", "m6b_drift_report.json")
    with open(out, "w", encoding="utf-8") as fh:
        json.dump({"ts": time.strftime("%F %T"), "baseline": args.baseline,
                   "rounds": [{k: v for k, v in r.items() if k != "rows"} for r in results],
                   "changed_rounds": worst}, fh, ensure_ascii=False, indent=1)
    print("\n汇总已存：%s" % os.path.relpath(out, ROOT))
    print("有序列变化的轮次：%s" % (worst or "无 ✓"))
    return 1 if worst else 0


if __name__ == "__main__":
    sys.exit(main())
