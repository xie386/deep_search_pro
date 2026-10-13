# -*- coding: utf-8 -*-
"""硅基流动选型**决赛**：走**生产链路**（不是探针），出题与判分各验一遍。

① 出题：对每个候选跑真 `gen_cases`（12 个真工具 = 2 批单工具 + 多工具 + 噪声 + 可能的补漏/重出），
   报：是否成功、耗时、题数、校验错、warnings、多工具跨套装、单工具不越界（**判据真跑**）。
② 判分：用 `judge_run`（真解析路径 `{"scores":[…]}`）判 3 条**已知答案**的样本 →
   期望 #1 命中 ✓、#2 用错工具 ✗、#3 噪声题却调了搜索 ✗（假阳性）。
不写任何数据（`gen_cases` 只返回；用户名 `_verify_sf` 也不落盘）✓

跑法：`.venv/Scripts/python.exe tests/test_out/verify_sf_final.py`
"""
import json
import os
import sys
import time

from dotenv import find_dotenv, load_dotenv

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
os.environ.pop("PYTHONPATH", None)
load_dotenv(find_dotenv())

from tools import capability_pool as cp        # noqa: E402
from tools import probe_agent as pa            # noqa: E402

GEN_CANDS = ["Qwen/Qwen3-30B-A3B-Instruct-2507", "inclusionAI/Ling-flash-2.0",
             "Qwen/Qwen2.5-14B-Instruct"]
JUDGE_CANDS = ["Qwen/Qwen2.5-32B-Instruct", "Qwen/Qwen3-30B-A3B-Instruct-2507",
               "inclusionAI/Ling-flash-2.0"]
N = int(os.getenv("VERIFY_TOOLS") or 12)
ACC = int(os.getenv("PROBE_ACCOUNT_ID") or 3)

ents = cp.pool_entries(ACC)[:N]
pa.card_block = lambda acc: ("\n".join(e.card_text for e in ents), ents)

ROWS = [
    {"id": 1, "kind": "single", "q": "帮我看看我最近在微信读书里读什么书",
     "expect": {"kind": "cli", "bin": "weread"},
     "calls": [{"brief": "weread shelf recent → 3 本书"}]},              # 期望 ✓
    {"id": 2, "kind": "single", "q": "查一下最近B站我收藏的视频",
     "expect": {"kind": "cli", "bin": "bili"},
     "calls": [{"brief": "api:steam#searchGame 'bili' → 0 条"}]},        # 期望 ✗
    {"id": 3, "kind": "noise", "q": "帮我写一首关于秋天的诗", "expect": None,
     "calls": [{"brief": "web_search '秋天的诗' → 10 条"}]},              # 期望 ✗（噪声题假阳性）
]

out = {"gen": [], "judge": []}
HERE = os.path.dirname(os.path.abspath(__file__))

print("① 出题决赛（%d 个真工具；生产链路 gen_cases）" % N)
for m in GEN_CANDS:
    os.environ["PROBE_LLM_MODEL"] = m
    pa._MODEL_CACHE.clear()
    t0 = time.time()
    try:
        g = pa.gen_cases(ACC, "_verify_sf")
        sec = time.time() - t0
        rec = {"model": m, "ok": True, "sec": sec, "count": g["count"],
               "errors": g["errors"], "warnings": g["warnings"][:3], "failed": g["failed"],
               "multi_ok": g["multi_ok"], "single_ok": g["single_ok"],
               "with_alts": g.get("with_alts"), "normalized": g.get("normalized")}
        print("   %-34s ✅ %2d 题 / %5.1fs / 校验 %s / 多工具 %s / 单工具 %s"
              % (m, g["count"], sec, "✓" if not g["errors"] else "✗%d" % len(g["errors"]),
                 "✓" if g["multi_ok"] else "✗", "✓" if g["single_ok"] else "✗"), flush=True)
        if g["errors"]:
            print("        校验错：%s" % g["errors"][:2])
        if g["warnings"]:
            print("        warnings：%s" % [w[:60] for w in g["warnings"][:2]])
    except Exception as e:  # noqa: BLE001
        rec = {"model": m, "ok": False, "sec": time.time() - t0,
               "err": "%s: %s" % (type(e).__name__, str(e)[:160])}
        print("   %-34s ❌ %5.1fs %s" % (m, rec["sec"], rec["err"]), flush=True)
    out["gen"].append(rec)
    with open(os.path.join(HERE, "sf_final_result.json"), "w", encoding="utf-8") as fp:
        json.dump(out, fp, ensure_ascii=False, indent=1)

print("\n② 判分决赛（judge_run 真解析；期望 #1 ✓ / #2 ✗ / #3 ✗）")
for m in JUDGE_CANDS:
    os.environ["PROBE_LLM_JUDGE_MODEL"] = m
    pa._MODEL_CACHE.clear()
    t0 = time.time()
    try:
        j = pa.judge_run({"rows": ROWS})
        sc, sec = j["scores"], time.time() - t0
        got = {k: v["verdict"] for k, v in sorted(sc.items())}
        want = {1: "✓", 2: "✗", 3: "✗"}
        hit = sum(1 for k in want if got.get(k) in (want[k], {"✓": "hit", "✗": "miss"}[want[k]]))
        rec = {"model": m, "ok": True, "sec": sec, "got": got,
               "want": want, "hit": hit, "why": {k: v["why"][:40] for k, v in sc.items()}}
        print("   %-34s %s 判对 %d/3 / %4.1fs  实测 %s"
              % (m, "✅" if hit == 3 else "△", hit, sec, got), flush=True)
    except Exception as e:  # noqa: BLE001
        rec = {"model": m, "ok": False, "sec": time.time() - t0,
               "err": "%s: %s" % (type(e).__name__, str(e)[:160])}
        print("   %-34s ❌ %4.1fs %s" % (m, rec["sec"], rec["err"]), flush=True)
    out["judge"].append(rec)
    with open(os.path.join(HERE, "sf_final_result.json"), "w", encoding="utf-8") as fp:
        json.dump(out, fp, ensure_ascii=False, indent=1)

os.environ.pop("PROBE_LLM_MODEL", None)
os.environ.pop("PROBE_LLM_JUDGE_MODEL", None)
print("\n明细：%s" % os.path.join(HERE, "sf_final_result.json"))
