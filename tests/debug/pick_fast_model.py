# -*- coding: utf-8 -*-
"""挑一个「快 + 严格输出 JSON」的模型给测评用（一次性脚本，不入库）。

背景（用户 2026-10-10 交办）：`.env` 的 `LLM_MODEL_MAX=nvidia/nemotron-3.5-lightning:free` 是**思考型**，
实测「思考 13154 字 / 373.7 秒 / 正文为空」，而测评三段需要「短、快、严格 JSON」。
本脚本不新增任何凭据：候选全部走**同一把 OpenRouter key + 同一个 base_url**（与 `.env` 现有配置一致 ✓）。

对每个候选做 2 轮**真实**调用（与出题同形的任务：3 张工具卡 → 只要 JSON），记录：
  延迟 / content 是否为空 / JSON 是否可解析 / 解析出几条 / 输出长度 / 是否产出思考字段。
跑法：`.venv/Scripts/python.exe tests/test_out/pick_fast_model.py`
"""
import json
import os
import sys
import time

import httpx
from dotenv import find_dotenv, load_dotenv

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
os.environ.pop("PYTHONPATH", None)
load_dotenv(find_dotenv())

CANDIDATES = [
    "google/gemini-2.5-flash-lite",        # 便宜 + 以 JSON 见长（我的首选）
    "openai/gpt-4.1-nano",                 # nano：快、JSON 稳
    "inclusionai/ling-3.1-flash",          # **免费**（价格 0/0）
]
if len(sys.argv) > 1:                      # 用法：脚本 "id1,id2" [超时秒] [轮数]
    CANDIDATES = [x.strip() for x in sys.argv[1].split(",") if x.strip()]
TIMEOUT = float(sys.argv[2]) if len(sys.argv) > 2 else 120
ROUNDS = int(sys.argv[3]) if len(sys.argv) > 3 else 2

SYS = ("你是工具池测评出题助手。只输出 JSON，不要解释、不要代码围栏。"
       "输出形如 {\"cases\":[{\"id\":1,\"kind\":\"single\",\"q\":\"<口语题面>\","
       "\"expect\":{\"kind\":\"tool\",\"ref\":\"<逐字照抄卡片里的 ref>\"}}]}")
CARDS = ("【工具池卡片】本批共 3 个工具\n"
         "· ref=mcp:pdd/search_goods ｜ 名称=拼多多搜商品 ｜ keywords=拼多多搜商品/拼多多上多少钱\n"
         "· ref=mcp:city/search_city ｜ 名称=城市地理信息 ｜ keywords=城市在哪个省/经纬度/人口\n"
         "· ref=weread ｜ 名称=微信读书 ｜ keywords=搜索/找书/阅读进度\n")
USER = CARDS + "\n【本轮范围】只出单工具题：上面每个工具恰好 1 条，kind 一律 single。"

BASE = os.getenv("OPENAI_BASE_URL").rstrip("/")
KEY = os.getenv("OPENAI_API_KEY") or ""


def call(model, timeout=120):
    t0 = time.time()
    try:
        r = httpx.post(BASE + "/chat/completions",
                       headers={"Authorization": "Bearer " + KEY, "Content-Type": "application/json"},
                       json={"model": model, "max_tokens": 1024, "temperature": 0.2,
                             "messages": [{"role": "system", "content": SYS},
                                          {"role": "user", "content": USER}]},
                       timeout=timeout)
    except Exception as e:  # noqa: BLE001
        try:
            r = httpx.post(BASE + "/chat/completions",
                           headers={"Authorization": "Bearer " + KEY, "Content-Type": "application/json"},
                           json={"model": model, "max_tokens": 1024, "temperature": 0.2,
                                 "messages": [{"role": "system", "content": SYS},
                                              {"role": "user", "content": USER}]},
                           timeout=timeout, trust_env=False)
        except Exception as e2:  # noqa: BLE001
            return {"model": model, "err": "%s / %s" % (e, e2), "sec": time.time() - t0}
    sec = time.time() - t0
    if r.status_code != 200:
        return {"model": model, "err": "HTTP %s %s" % (r.status_code, r.text[:180]), "sec": sec}
    d = r.json()
    ch = (d.get("choices") or [{}])[0]
    msg = ch.get("message") or {}
    txt = (msg.get("content") or "").strip()
    rc = (msg.get("reasoning_content") or msg.get("reasoning") or "")
    n, why = 0, ""
    try:
        t = txt
        if t.startswith("```"):
            t = t.split("```")[1]
            t = t.split("\n", 1)[1] if "\n" in t else t
        i, j = t.find("{"), t.rfind("}")
        obj = json.loads(t[i:j + 1])
        n = len(obj.get("cases") or [])
    except Exception as e:  # noqa: BLE001
        why = "%s: %s" % (type(e).__name__, e)
    return {"model": model, "sec": sec, "chars": len(txt), "think": len(rc),
            "cases": n, "why": why, "usage": (d.get("usage") or {}).get("total_tokens"),
            "head": txt[:90].replace("\n", " ")}


print("候选实测（同一把 key / 同一 base_url；每模型 2 轮）\n" + "=" * 100)
rows = []
for m in CANDIDATES:
    for rnd in range(1, ROUNDS + 1):
        r = call(m, timeout=TIMEOUT)
        rows.append(r)
        if r.get("err"):
            print("❌ %-34s 第%d轮  %5.1fs  %s" % (m, rnd, r["sec"], r["err"]))
        else:
            flag = "✅" if (r["cases"] == 3 and not r["why"]) else ("⚠️" if r["cases"] else "❌")
            print("%s %-34s 第%d轮 %6.2fs  content %4d 字  思考 %5d 字  JSON 题数 %s  tokens %s  %s"
                  % (flag, m, rnd, r["sec"], r["chars"], r["think"], r["cases"] or r["why"][:40],
                     r["usage"], r["head"][:52]))
print("=" * 100)
ok = {}
for r in rows:
    if not r.get("err") and not r["why"] and r["cases"]:
        ok.setdefault(r["model"], []).append(r["sec"])
for m, secs in ok.items():
    print("可用：%-34s 平均 %.2fs（%d/%d 轮成功）" % (m, sum(secs) / len(secs), len(secs), 2))
