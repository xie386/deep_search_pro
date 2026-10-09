# -*- coding: utf-8 -*-
"""素材层 A/B 动态闸门（只读）：素材改动「值不值得入库」的离线判据。

三种模式：
  ① 默认          冻结池 + 草稿（`tests/fixtures/material_layer_draft.json`）—— 测「这批素材本身有没有效」
  ② --live-pool   真实账号池（当前库里的素材）—— 测「库里现状怎么样」
  ③ --live-pool --draft   真实池 + 草稿 —— 测「在现状之上再补这批素材会怎样」

输出三段：5 例回归的期望 ref 落位 · 30 题五指标（与基线逐项对比）· 噪声题逐题体检。
★ 只读：不写数据库、不写夹具、不改冻结池。

⚠️ 噪声假阳性是**硬判据**，但它在池内余弦标准差极小（≈0.01）时**不稳定**——
   小改动会让它 0↔4 跳变。所以不要只看布尔值，要结合逐题体检里的 z 值判断。

用法：.venv/Scripts/python.exe tests/live/m6b_material_ab.py [--live-pool] [--draft]
"""
import dataclasses
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "tests", "live"))
os.chdir(ROOT)
import m5c_router_bench as bench                     # noqa: E402
from tools import tool_router as TR                  # noqa: E402
from rag_knowledge.kb_service import embedding_model  # noqa: E402

ARGS = sys.argv[1:]
LIVE = "--live-pool" in ARGS
USE_DRAFT = ("--draft" in ARGS) or (not LIVE)
DRAFT_PATH = os.path.join(ROOT, "tests", "fixtures", "material_layer_draft.json")
DRAFT = json.load(open(DRAFT_PATH, encoding="utf-8")) if USE_DRAFT else {}
SRC_PHRASE = {
    "city": "查询选定城市的详细地理信息",
    "bnf": "查法国国家图书馆（Gallica）数字馆藏：按书名/作者/主题/年代/文献类型检索，返回书目元数据",
    "12306-mcp": "查看车票信息,查看当天从出发点到目的地的车票剩余,路程需要中转车票购买规划",
    "route": "根据目的地和出发地进行交通工具使用、切换选择,策划最佳旅游/出差路线",
}


def _apply_draft(entries):
    out = []
    for e in entries:
        d = DRAFT.get(e.ref)
        if not d:
            out.append(e)
            continue
        slug = e.ref.split("/")[0].replace("mcp:", "")
        sp = SRC_PHRASE.get(slug, "")
        ab = (d["abilities_user"] + "；" + sp) if sp else d["abilities_user"]
        out.append(dataclasses.replace(e, name=d["name"], keywords=d["keywords"],
                                       abilities=ab, abilities_tool=d["abilities_user"]))
    return out


def loader(derive_kw=True):
    if LIVE:
        sys.path.insert(0, os.path.join(ROOT, "tests", "live"))
        import m6b_delta_rerun as dr
        _, _, ents = dr._live_entries()
    else:
        ents = bench.load_entries(derive_kw)
    return _apply_draft(ents) if USE_DRAFT else ents


bench.load_entries = loader            # ★ 让 bench.main() 也走同一口径
ents = loader(True)
emb = embedding_model()
mat = emb.embed_documents([bench.vector_text(e, False) for e in ents])
print("模式：%s%s ｜ 条目 %d 条 ｜ 草稿生效 %d / %d" % (
    "真实账号池" if LIVE else "冻结池", "＋草稿" if USE_DRAFT else "（不加草稿）",
    len(ents), sum(1 for e in ents if e.ref in DRAFT), len(DRAFT)))

regs = json.load(open(os.path.join(ROOT, "tests", "fixtures", "router_bench_regressions.json"), encoding="utf-8"))
print("\n=== 5 例回归 ===")
for c in regs["cases"]:
    r = bench.route_once(c["q"], ents, emb, False, mat)
    t3, exp = r["top3"], c.get("expect") or []
    place = {x: t3.index(x) + 1 for x in exp if x in t3}
    print("  · %-14s 命中：%s" % (c["id"], "、".join("%s@%d" % (k.split("/")[-1], v) for k, v in place.items()) or "无"))

qs = json.load(open(os.path.join(ROOT, "tests", "fixtures", "router_bench.json"), encoding="utf-8"))["questions"]
print("\n=== 噪声题逐题体检 ===")
for q in [x["q"] for x in qs if x.get("cat") == "噪声"]:
    dec = bench.route_once(q, ents, emb, False, mat)["dec"]
    d = dec["dense"]
    mu = sum(d.values()) / len(d)
    sd = (sum((v - mu) ** 2 for v in d.values()) / len(d)) ** 0.5
    top = max(d, key=lambda k: d[k])
    print("  · %-26s confident=%-5s top1=%-30s z=%.2f" % (
        q, dec["confident"], top.split("/")[-1], (d[top] - mu) / sd if sd else 0))

sys.argv = ["m5c_router_bench"]
bench.main()
