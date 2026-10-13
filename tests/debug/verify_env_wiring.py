# -*- coding: utf-8 -*-
"""验：**只用 `.env`**（不加任何环境变量覆盖）时，出题/判分是否真走 `.env` 里配的那个厂商与模型。

看四件事：
  ① `probe_model()` / `probe_model("judge")` 解析出的**模型名与 base_url**；
  ② 生产链路 `gen_cases`（真池 N 个工具）能否跑通、校验有无错、耗时多少；
  ③ `judge_run` 在**已知答案**样本上判对几条（1✓ 2✗ 3✗）；
  ④ 接口层 `api/probe.py::_model_name()` 透出的名字与解析结果一致（防接口自造解析跑偏）。

跑法：`.venv/Scripts/python.exe tests/test_out/verify_env_wiring.py`
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
# ★ 刻意**不设**任何 PROBE_* 覆盖：这里就是要验 `.env` 本身
for k in ("PROBE_LLM_MODEL", "PROBE_LLM_JUDGE_MODEL", "PROBE_LLM_BASE_URL", "PROBE_LLM_API_KEY"):
    os.environ.pop(k + "_OVERRIDE_FOR_TEST", None)

from tools import capability_pool as cp        # noqa: E402
from tools import probe_agent as pa            # noqa: E402
from api import probe as api_probe             # noqa: E402

N = int(os.getenv("VERIFY_TOOLS") or 12)
ACC = int(os.getenv("PROBE_ACCOUNT_ID") or 3)

g1 = pa.probe_model()
j1 = pa.probe_model("judge")
print("① 解析结果（.env）")
print("   出题模型：%s ｜ base_url=%s ｜ timeout=%s ｜ max_tokens=%s"
      % (g1.model_name, g1.openai_api_base, g1.request_timeout, g1.max_tokens))
print("   判分模型：%s %s" % (j1.model_name,
                             "（与出题不同 → 界面会单独显示）" if j1.model_name != g1.model_name else "（与出题相同）"))
print("   接口透出：probe_model=%s ｜ probe_judge_model=%s"
      % (api_probe._model_name(""), api_probe._model_name("judge")))
ok_api = (api_probe._model_name("") == g1.model_name and api_probe._model_name("judge") == j1.model_name)
print("   接口与解析一致：%s" % ("✓" if ok_api else "✗"))

ents = cp.pool_entries(ACC)[:N]
pa.card_block = lambda acc: ("\n".join(e.card_text for e in ents), ents)

print("\n② 出题（生产链路 gen_cases，%d 个真工具）" % len(ents))
t0 = time.time()
try:
    g = pa.gen_cases(ACC, "_verify_env")
    sec = time.time() - t0
    print("   %s %d 题 / %.1fs / 校验 %s / 多工具 %s / 单工具 %s / 失败批 %d"
          % ("✅" if not g["errors"] else "△", g["count"], sec,
             "✓ 无错" if not g["errors"] else g["errors"], "✓" if g["multi_ok"] else "✗",
             "✓" if g["single_ok"] else "✗", g["failed"]))
    if g["warnings"]:
        print("   warnings：%s" % [w[:70] for w in g["warnings"][:3]])
except Exception as e:  # noqa: BLE001
    print("   ❌ %.1fs %s: %s" % (time.time() - t0, type(e).__name__, str(e)[:200]))
    sys.exit(1)

ROWS = [
    {"id": 1, "kind": "single", "q": "帮我看看我最近在微信读书里读什么书",
     "expect": {"kind": "cli", "bin": "weread"}, "calls": [{"brief": "weread shelf recent → 3 本书"}]},
    {"id": 2, "kind": "single", "q": "查一下最近B站我收藏的视频",
     "expect": {"kind": "cli", "bin": "bili"}, "calls": [{"brief": "api:steam#searchGame 'bili' → 0 条"}]},
    {"id": 3, "kind": "noise", "q": "帮我写一首关于秋天的诗", "expect": None,
     "calls": [{"brief": "web_search '秋天的诗' → 10 条"}]},
]
print("\n③ 判分（judge_run；期望 #1 ✓ / #2 ✗ / #3 ✗）")
t0 = time.time()
try:
    j = pa.judge_run({"rows": ROWS})
    got = {k: v["verdict"] for k, v in sorted(j["scores"].items())}
    want = {1: "✓", 2: "✗", 3: "✗"}
    hit = sum(1 for k in want if got.get(k) == want[k])
    print("   %s 判对 %d/3 / %.1fs  实测 %s" % ("✅" if hit == 3 else "△", hit, time.time() - t0, got))
    for k, v in sorted(j["scores"].items()):
        print("       #%s %s —— %s" % (k, v["verdict"], v["why"][:60]))
except Exception as e:  # noqa: BLE001
    print("   ❌ %.1fs %s: %s" % (time.time() - t0, type(e).__name__, str(e)[:200]))
    sys.exit(1)
