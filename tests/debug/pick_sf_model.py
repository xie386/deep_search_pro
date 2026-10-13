# -*- coding: utf-8 -*-
"""硅基流动（SiliconFlow）**出题/判分模型选型**：对每个候选模型各打两发真调用，报指标。

· **出题探针**：走 `probe_writer` 的真 system 段 + 3 个真工具卡片 → 要求只输出 JSON
  （看得分点：JSON 是否干净、耗多少秒、有没有把预算花在 reasoning 上）。
· **判分探针**：走 `probe_judge` 的真 system 段 + 一条题（题面/期望/实际调用）→ 要求输出
  `{"verdict":"...","reason":"..."}`。
· 用**生产同款** `ReasoningChatOpenAI`（`agent.reasoning_model`），与 `probe_agent.invoke` 一致。
· **不写任何数据、不改 .env**（选型的结论由人/助手另行落 .env）✓

跑法：`.venv/Scripts/python.exe tests/test_out/pick_sf_model.py [候选名,候选名,...]`
      （不传候选 = 用脚本内置清单）
"""
import io
import json
import os
import sys
import time
import contextlib
from concurrent.futures import ThreadPoolExecutor, as_completed

from dotenv import find_dotenv, load_dotenv

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
os.environ.pop("PYTHONPATH", None)
load_dotenv(find_dotenv())

from tools import capability_pool as cp        # noqa: E402
from tools import probe_agent as pa            # noqa: E402

BASE = (os.getenv("PROBE_LLM_BASE_URL") or "").rstrip("/")
KEY = os.getenv("PROBE_LLM_API_KEY") or ""
TIMEOUT = float(os.getenv("PROBE_LLM_TIMEOUT") or 120)
MAXTOK = int(os.getenv("PROBE_LLM_MAXTOKENS") or 4096)
ACC = int(os.getenv("PROBE_ACCOUNT_ID") or 3)
# `PICK_EXTRA_BODY='{"enable_thinking": false}'` → 透传给 provider（测「手动关思考」✓）
EXTRA = json.loads(os.getenv("PICK_EXTRA_BODY") or "{}")

# 候选：左列偏「出题」（快、便宜），右列偏「判分」（强、会判断）
CANDIDATES = [
    "Qwen/Qwen2.5-7B-Instruct",
    "Qwen/Qwen3-30B-A3B-Instruct-2507",
    "Qwen/Qwen3.5-9B",
    "Qwen/Qwen3.5-35B-A3B",
    "zai-org/GLM-5.3-Flash",
    "stepfun-ai/Step-3.5-Flash",
    "deepseek-ai/DeepSeek-V4-Flash",
    "tencent/Hunyuan-A13B-Instruct",
    "THUDM/GLM-4-9B-0414",
    "Qwen/Qwen3-32B",
    "zai-org/GLM-5.3",
    "deepseek-ai/DeepSeek-V4-Pro",
]

ents = cp.pool_entries(ACC)[:3]
CARDS = "\n".join(e.card_text for e in ents)

ASK_GEN = (
    "【工具池卡片】本批共 %d 个工具（`ref` 必须逐字照抄）\n\n%s\n\n"
    "【本轮范围】**只出单工具题**：上面每个工具**恰好 1 条**；`kind` 一律 `single`；"
    "CLI 类工具的 `expect` 写成 `{\"kind\":\"cli\",\"bin\":\"<可执行名>\",\"argv\":[]}`，"
    "api/mcp 类用 `{\"kind\":\"tool\",\"ref\":\"<ref>\"}`；并给 `refs`（本套装用到的工具，≥1 个）。"
    "只输出 JSON：{\"cases\":[{\"id\":1,\"kind\":\"single\",\"q\":\"…\",\"expect\":{…},"
    "\"refs\":[…],\"alts\":[],\"chain\":\"…\"}]}" % (len(ents), CARDS)
)

ASK_JUDGE = (
    "【题面】帮我看看我最近在微信读书里读什么书\n"
    "【期望工具】weread（微信读书 CLI）\n"
    "【该次实际调用】1. bili search '微信读书'  → 返回 0 条\n"
    "请**严格按系统提示里的 JSON 格式**输出判定（不要加任何解释文字）。"
)


def probe(model_name, section, user_msg):
    """打一发真调用，返回指标 dict（不抛异常，失败也记下来 ✓）。"""
    from agent.reasoning_model import ReasoningChatOpenAI
    out = {"ok": False, "json": None, "c": 0, "rc": 0, "sec": 0.0, "err": ""}
    t0 = time.time()
    try:
        m = ReasoningChatOpenAI(model=model_name, api_key=KEY, base_url=BASE,
                                timeout=TIMEOUT, max_tokens=MAXTOK,
                                model_kwargs=({"extra_body": EXTRA} if EXTRA else {}))
        with contextlib.redirect_stdout(io.StringIO()):
            resp = m.invoke([{"role": "system", "content": pa.prompt_of(section)},
                             {"role": "user", "content": user_msg}])
        txt = str(getattr(resp, "content", "") or "").strip()
        rc = (getattr(resp, "additional_kwargs", {}) or {}).get("reasoning_content") or ""
        out["c"], out["rc"] = len(txt), len(rc)
        if not txt:
            out["err"] = "空内容（reasoning %d 字）" % len(rc)
        else:
            try:
                out["json"] = json.loads(pa._strip_fences(txt))
                out["ok"] = True
            except Exception:  # noqa: BLE001
                out["err"] = "JSON 不合法：%s" % txt[:80].replace("\n", " ")
    except Exception as e:  # noqa: BLE001
        out["err"] = "%s: %s" % (type(e).__name__, str(e)[:110].replace("\n", " "))
    out["sec"] = time.time() - t0
    return out


def run(model_name):
    g = probe(model_name, "probe_writer", ASK_GEN)
    j = probe(model_name, "probe_judge", ASK_JUDGE)
    n = len((g["json"] or {}).get("cases") or []) if g["json"] else 0
    # 判分：**按生产 schema**（`{"scores":[{id,verdict,why,conf}]}`）算合规 ✓
    sc = ((j["json"] or {}).get("scores") or []) if j["json"] else []
    vd = (sc[0].get("verdict") if sc and isinstance(sc[0], dict) else None)
    if j["ok"] and not sc:
        vd = "schema✗"                                    # JSON 合法但**不是生产 schema**
    return {"model": model_name, "gen": g, "judge": j, "cases": n, "verdict": vd}


if __name__ == "__main__":
    if os.getenv("PICK_LIST"):
        import httpx
        r = httpx.get(BASE + "/models", headers={"Authorization": "Bearer " + KEY}, timeout=30.0)
        ids = sorted(m.get("id", "") for m in (r.json().get("data") or []))
        skip = ("embed", "rerank", "image", "-vl", "vl-", "ocr", "tts", "asr", "voice",
                "video", "audio", "sd", "flux", "kolors")
        chat = [i for i in ids if not any(s in i.lower() for s in skip)]
        print("全量可用对话/文本模型 %d 个（已剔除向量/重排/图像/语音类）：" % len(chat))
        for i in chat:
            print("   ", i)
        sys.exit(0)
    cands = [a for a in sys.argv[1:] if a.strip()] or CANDIDATES
    print("① 目标：base_url=%s ｜ 池 %d 个真工具 ｜ 超时 %ss ｜ max_tokens %s ｜ extra_body=%s"
          % (BASE, len(ents), TIMEOUT, MAXTOK, EXTRA or "无"))
    print("② 并行打真调用（%d 个候选 × 出题/判分各 1 发）…\n" % len(cands))
    t0 = time.time()
    rows = []
    out_json = os.path.join(os.path.dirname(os.path.abspath(__file__)), "pick_sf_result.json")
    with ThreadPoolExecutor(max_workers=6) as ex:
        futs = {ex.submit(run, c): c for c in cands}
        for f in as_completed(futs):                      # ★ 谁先完就先报谁（中断也留证据 ✓）
            try:
                r = f.result()
            except Exception as e:  # noqa: BLE001
                r = {"model": futs[f], "gen": {"ok": False, "err": "%s: %s" % (type(e).__name__, e),
                                               "sec": 0, "rc": 0},
                     "judge": {"ok": False, "err": "", "sec": 0, "rc": 0}, "cases": 0,
                     "verdict": None}
            rows.append(r)
            g, j = r["gen"], r["judge"]
            print("   %-40s 出题 %-2s %-4s %5.1fs 思考%-6d 判分 %-6s %s"
                  % (r["model"], "✓" if g["ok"] else "✗", r["cases"] or "-", g["sec"], g["rc"],
                     (r["verdict"] or ("✓" if j["ok"] else "✗")),
                     (g["err"] or j["err"] or "")[:60]), flush=True)
            with open(out_json, "w", encoding="utf-8") as fp:
                json.dump(rows, fp, ensure_ascii=False, indent=1)
    print("\n总耗时 %.1fs ｜ 明细已存 %s\n" % (time.time() - t0, out_json))
    rows.sort(key=lambda r: (not r["gen"]["ok"], r["gen"]["sec"]))
    print("%-40s %-6s %-5s %-7s %-7s %-6s %s" % ("模型", "出题", "题数", "耗时", "思考字", "判分", "备注"))
    print("-" * 136)
    for r in rows:
        g, j = r["gen"], r["judge"]
        note = (g["err"] or "")[:64]
        print("%-40s %-6s %-5s %-7.1f %-7d %-6s %s"
              % (r["model"], "✓" if g["ok"] else "✗", r["cases"] or "-", g["sec"], g["rc"],
                 (r["verdict"] or ("✓" if j["ok"] else "✗")), note))
    print("\n③ 判分侧细节：")
    for r in rows:
        j = r["judge"]
        print("   %-40s %-6s %6.1fs 思考 %-6d %s"
              % (r["model"], "✓" if j["ok"] else "✗", j["sec"], j["rc"], (j["err"] or "")[:64]))
