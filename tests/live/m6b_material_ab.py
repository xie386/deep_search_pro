# -*- coding: utf-8 -*-
"""素材层 A/B 动态闸门（只读）：素材改动「值不值得入库」的离线判据。

三种模式：
  ① 默认          冻结池 + 草稿（`tests/fixtures/material_layer_draft.json`）—— 测「这批素材本身有没有效」
  ② --live-pool   真实账号池（当前库里的素材）—— 测「库里现状怎么样」
  ③ --live-pool --draft   真实池 + 草稿 —— 测「在现状之上再补这批素材会怎样」

输出三段：5 例回归的期望 ref 落位 · 30 题指标（precision@1 / precision@3 / recall@3 / hit@3 / 灰区分栏 /
噪声假阳性 / 注入量，与基线逐项对比）· 噪声题逐题体检。
★ 只读：不写数据库、不写夹具、不改冻结池。

★ 2026-10-09 口径收口后：**默认模式（冻结池＋草稿）就是「库里入库口径」**——判「素材层生效后的系统」
  看它，判「素材层有没有效」看两模式之差；口径三条（precision@3 结构上限 / 灰区分栏 / 冻结池=素材层前）
  见 `docs/v4.0/Done/01-度量口径收口工程·实现记录.md`。

★ 草稿口径 = 入库口径（2026-10-09 收口）：`material_layer_draft.json` 只保留**库里真正入库的那些条目**。
  裁决（任务书 §九 9）把 `api:xxapi#ipQuery` / `api:xxapi#douyinHot` /
  `mcp:12306-mcp/get-station-by-telecode` 定为「保持原状并关闭」（三者 `abilities_user` 在库里为空），
  故已从草稿中移除 —— 否则默认模式会把它们也算进去，得出「噪声假阳性 0.333」这类**与入库现状不符**的假差
  （2026-10-09 实测过：保留它们时 noise_fp=0.333，移除后回到 0.000）。将来若又给某条目做素材层，记得同步这里与库。

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


_ORIG_LOAD_ENTRIES = bench.load_entries        # ★ 必须在打补丁**之前**留好原引用


def loader(derive_kw=True):
    if LIVE:
        sys.path.insert(0, os.path.join(ROOT, "tests", "live"))
        import m6b_delta_rerun as dr
        _, _, ents = dr._live_entries()
    else:
        # ★ 走原函数，不能写 bench.load_entries —— 下面第 67 行会把那个名字换成 loader 自己，
        #   否则默认（冻结池）模式一进来就自递归 → RecursionError（2026-10-09 实跑踩到）
        ents = _ORIG_LOAD_ENTRIES(derive_kw)
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
