# -*- coding: utf-8 -*-
"""把选定的**硅基流动**出题/判分模型写进 `.env`（改前备份；只动 PROBE_LLM_* 段）。

选定依据（2026-10-10 实测，见 tests/test_out/pick_sf_result.json 与 sf_final_result.json）：
  · 出题 `Qwen/Qwen3-30B-A3B-Instruct-2507` —— 非推理（思考 0 字）、生产链路 17 题/37.4s/校验无错、
    且判分也 3/3（可兼作判分备选）
  · 判分 `Qwen/Qwen2.5-32B-Instruct` —— 稠密 32B、非推理、judge_run 3/3 且理由最准（4.6s）
  · 速度备选 `inclusionAI/Ling-flash-2.0`（出题 26.9s 最快，但偶发吐裸数组，靠重试救）
  · 最省备选 `Qwen/Qwen2.5-7B-Instruct` / `THUDM/GLM-4-9B-0414`（后者 3.5s 最快，但出题会多给题）
"""
import re
import shutil
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ENV = ROOT / ".env"
bak = ROOT / (".env.bak_" + time.strftime("%Y%m%d_%H%M%S"))
shutil.copy2(ENV, bak)

text = ENV.read_text(encoding="utf-8")

BLOCK = """# ---- 测评专用模型口（2026-10-10 起走**硅基流动**；不设则回落到 LLM_MODEL_MAX）----
# 出题：多批次×短输出，要快、要稳吐 JSON（实测非推理：思考 0 字 / 17 题 37.4s / 校验无错）
PROBE_LLM_MODEL=Qwen/Qwen3-30B-A3B-Instruct-2507
# 判分：一次长输入 + 需要判断力，可单独配（不填 = 沿用上面出题模型）
PROBE_LLM_JUDGE_MODEL=Qwen/Qwen2.5-32B-Instruct
# 备选（按需换）：inclusionAI/Ling-flash-2.0（出题最快 26.9s）｜ Qwen/Qwen2.5-7B-Instruct（最省）
#                THUDM/GLM-4-9B-0414（3.5s 最快，但出题会多给题）
#                ⚠ 别用推理型：Qwen3.5-*／Seed-OSS-36B-Instruct／Step-3.5-Flash／Hunyuan-A13B-Instruct
#                  （实测思考 1.4k~15k 字、单次 25~176 秒，会拖垮测评并可能返回空内容）
# 超时/输出上限/重试/并发（免费或限流场景可调）
PROBE_LLM_TIMEOUT=120
PROBE_LLM_MAXTOKENS=4096
PROBE_LLM_RETRIES=2
PROBE_GEN_CONCURRENCY=3
"""

# 只替换 PROBE_LLM_* 这几行（保留用户自己填的 BASE_URL / API_KEY 与顺序 ✓）
lines = text.split("\n")
keep = []
skip = {"PROBE_LLM_MODEL", "PROBE_LLM_TIMEOUT", "PROBE_LLM_MAXTOKENS",
        "PROBE_LLM_RETRIES", "PROBE_GEN_CONCURRENCY", "PROBE_LLM_JUDGE_MODEL"}
for ln in lines:
    k = ln.split("=", 1)[0].strip() if "=" in ln else ""
    if k in skip:
        continue
    keep.append(ln)
out = "\n".join(keep).rstrip("\n") + "\n\n" + BLOCK
ENV.write_text(out, encoding="utf-8")

print("已备份：%s" % bak.name)
for ln in ENV.read_text(encoding="utf-8").split("\n"):
    if ln.startswith("PROBE_LLM_BASE_URL"):
        print("保留（用户填的）：", ln)
    elif ln.startswith("PROBE_LLM_API_KEY"):
        print("保留（用户填的）： PROBE_LLM_API_KEY=***（不打印）")
    elif re.match(r"^(PROBE_LLM_MODEL|PROBE_LLM_JUDGE_MODEL|PROBE_LLM_TIMEOUT|PROBE_LLM_MAXTOKENS|"
                  r"PROBE_LLM_RETRIES|PROBE_GEN_CONCURRENCY)=", ln):
        print("写入：", ln)
