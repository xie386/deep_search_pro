# -*- coding: utf-8 -*-
"""验证：测评专用模型（`.env` 的 PROBE_LLM_MODEL）能把**整条出题链**跑通。

做三件事（都走**真实模型**，但只调 3 次、且用免费模型 → 零成本）：
  ① `probe_model()` 解析出的模型/base_url/超时/max_tokens 是否符合 `.env`；
  ② 用**真账号的 6 个真工具**（正好一批）跑一遍 `gen_cases` → 1 批单工具 + 多工具 + 噪声 = 3 次调用；
  ③ 打印耗时、题目、校验结论 —— 出题链自己会跑 `validate_case_set + coverage_errors`。
不写任何数据（`gen_cases` 只返回、不落盘；用户名用 `_verify_model`，也不 save）✓

跑法：`.venv/Scripts/python.exe tests/test_out/verify_probe_model.py`
"""
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

ACC = int(os.getenv("PROBE_ACCOUNT_ID") or 3)
N = int(os.getenv("VERIFY_TOOLS") or 6)

m = pa.probe_model()
print("① 模型解析：model=%s" % getattr(m, "model_name", None))
print("   base_url=%s" % getattr(m, "openai_api_base", None))
print("   timeout=%s  max_tokens=%s" % (getattr(m, "request_timeout", None),
                                        getattr(m, "max_tokens", None)))

ents = cp.pool_entries(ACC)[:N]
print("\n② 真机出题（%d 个真工具 → 1 批单工具 + 多工具 + 噪声 = 3 次调用；免费模型）" % len(ents))
pa.card_block = lambda acc: ("\n".join(e.card_text for e in ents), ents)
prog = []
t0 = time.time()
try:
    g = pa.gen_cases(ACC, "_verify_model", on_progress=lambda *a: prog.append(a))
    sec = time.time() - t0
    print("   ✅ 成功：%d 题 ／ 耗时 %.1fs ／ 并发 %s ／ 步数 %s"
          % (g["count"], sec, g.get("concurrency"), g.get("stages")))
    print("   校验：%s" % ("✓ 无错" if not g["errors"] else g["errors"]))
    print("   warnings：%s" % (g["warnings"] or "无"))
    print("   进度快照数：%d（最后一条：%s）" % (len(prog), str(prog[-1][4])[:70] if prog else "-"))
    print("\n③ 题目：")
    for c in g["doc"]["cases"]:
        exp = c.get("expect")
        exp_s = "不调工具" if exp is None else (exp.get("ref") or exp.get("bin") or exp)
        extra = ""
        if c.get("refs"):
            extra = " ｜ 套装 %s" % "、".join(sorted({pa.suit_of(r) for r in c["refs"]}))
        if c.get("alts"):
            extra += " ｜ 备选 %s" % "、".join(str(a) for a in c["alts"])
        print("   #%-2s %-6s %-38s → %s%s" % (c["id"], c["kind"], str(c["q"])[:36], exp_s, extra))
    print("\n④ 三条新规则的实测结论：")
    _mq = [c for c in g["doc"]["cases"] if str(c.get("kind")) == "multi"]
    _suits = [sorted({pa.suit_of(r) for r in (c.get("refs") or [])}) for c in _mq]
    print("   多工具题跨套装：%s（%s）"
          % ("✓ 全部 ≥2 个套装" if g.get("multi_ok") else "✗ 仍有不跨套装的",
             " ／ ".join("+".join(s) for s in _suits) or "无多工具题"))
    print("   单工具题不越界：%s" % ("✓ 全部只用一个套装" if g.get("single_ok") else "✗ 有单工具题牵涉多套装"))
    print("   备选答案 alts：%s 条题带 alts（归一中修正 %s 条）"
          % (g.get("with_alts"), g.get("alts")))
    _nw = [w for w in (g.get("warnings") or []) if "噪声" in w]
    print("   噪声题诱发搜索告警：%s" % (_nw or "无（噪声题都不是「需要查资料」型）"))
    print("   归一修正：%s 条 expect%s" % (g.get("normalized"), ""))
except Exception as e:  # noqa: BLE001
    print("   ❌ 失败（%.1fs）：%s: %s" % (time.time() - t0, type(e).__name__, e))
    sys.exit(1)
