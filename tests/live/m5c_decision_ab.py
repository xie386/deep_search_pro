# -*- coding: utf-8 -*-
"""决策模型精排 · 离线对账（v4.0 大点 02 首批）

同一批题、同一份池，跑两遍并比指标：
  基线 = 路由器 `decide()` 的 top1
  精排 = 在 `decide()` 之后过一遍 `decision_router.maybe_refine()`

两轨：冻结池 28＋素材层草稿（入库口径）· 真机池 29＋素材（发布口径）。
只读：不写库、不写夹具；真调决策模型（仅近并列题，免费路由）。
"""
from __future__ import annotations

import importlib
import json
import math
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests" / "live"))
os.chdir(ROOT)
os.environ.pop("PYTHONPATH", None)

from dotenv import load_dotenv                      # noqa: E402
load_dotenv(ROOT / ".env")

import dataclasses                                   # noqa: E402
import m5c_router_bench as bench                     # noqa: E402
from rag_knowledge.kb_service import embedding_model  # noqa: E402
from tools import decision_router as dr              # noqa: E402
from tools import tool_router as tr                  # noqa: E402

OUT = ROOT / "docs" / "v4.0" / "research" / "02-Jev仲裁层-demo" / "jev_impl_ab.json"
# ★ 离线对账**绝不写服务在用的审计/缓存文件**（2026-10-12 踩过：把真机记录和我自己跑的数据混在一起，
#   分不清哪条是用户的、哪条是脚本的）。用本模块自己的审计/缓存路径（`decision_router` 支持环境变量覆盖）。
(dr_dir := ROOT / "docs" / "v4.0" / "research" / "02-Jev仲裁层-demo").mkdir(parents=True, exist_ok=True)
os.environ["DECISION_AUDIT_PATH"] = str(dr_dir / "jev_impl_audit.jsonl")
os.environ["DECISION_CACHE_PATH"] = str(dr_dir / "jev_impl_cache.jsonl")
importlib.reload(dr)
os.environ.setdefault("DECISION_AUDIT_SEEN", "1")
# ★ 总开关现在是**账号级 DB 字段**（前端按钮，用户 2026-10-13 裁定）——离线对账在本进程内强制打开，
#   不写库、不动账号字段；否则账号开关是 0，脚本会静默地「在决策层关闭状态下」跑出无意义的对照。
dr.switch_on = lambda account_id=None: True
DRAFT = json.loads((ROOT / "tests/fixtures/material_layer_draft.json").read_text(encoding="utf-8"))
QS = json.loads((ROOT / "tests/fixtures/router_bench.json").read_text(encoding="utf-8"))["questions"]
SRC = {"city": "查询选定城市的详细地理信息",
       "bnf": "查法国国家图书馆（Gallica）数字馆藏：按书名/作者/主题/年代/文献类型检索，返回书目元数据",
       "12306-mcp": "查看车票信息,查看当天从出发点到目的地的车票剩余,路程需要中转车票购买规划",
       "route": "根据目的地和出发地进行交通工具使用、切换选择,策划最佳旅游/出差路线"}


def apply_draft(entries):
    out = []
    for e in entries:
        d = DRAFT.get(e.ref)
        if not d:
            out.append(e); continue
        sp = SRC.get(e.ref.split("/")[0].replace("mcp:", ""), "")
        ab = (d["abilities_user"] + "；" + sp) if sp else d["abilities_user"]
        out.append(dataclasses.replace(e, name=d["name"], keywords=d["keywords"],
                                       abilities=ab, abilities_tool=d["abilities_user"]))
    return out


def live_entries():
    import m6b_delta_rerun as m6b
    _, _, ents = m6b._live_entries()
    return ents


def dense_of(emb, ents, mat, question):
    qv = emb.embed_query(question)
    return {"%s:%s" % (e.source, e.ref): bench.cos(qv, v) for e, v in zip(ents, mat)}


os.environ["ZX_TOOL_ROUTE_PROVIDER"] = "decision"
emb = embedding_model()
print("provider=%s ｜ trigger=%s ｜ 门限=%s ｜ top_k=%d ｜ 模型=%s"
      % (dr.provider(), dr.trigger(), dr.tie_threshold(), dr.top_k(), dr.model() or "(未设，将走 config_missing)"),
      flush=True)

RES = {}
for tag, ents in (("冻结池+草稿", apply_draft(bench.load_entries(True))),
                  ("真机池29+素材", apply_draft(live_entries()))):
    # 显示键（e.ref）↔ 内部键（source:ref）的映射：题库 expect 与决策模型看的都是显示键。
    # （坑：内部键对 12306 这类 ref 自身带前缀的条目会变成 mcp:mcp:... 双层，直接与 expect 比对会全 False）
    disp = {"%s:%s" % (e.source, e.ref): e.ref for e in ents}
    mat = emb.embed_documents([bench.vector_text(e, False) for e in ents])
    rows = []
    t0 = time.time()
    for q in QS:
        dense = dense_of(emb, ents, mat, q["q"])
        dec = tr.decide(q["q"], ents, dense)
        base_top = (dec["order"][0] if dec["order"] else None)
        base_disp = disp.get(base_top, base_top)
        fired, why = dr.should_refine(dec)
        t1 = time.time()
        refined = dr.maybe_refine(3, q["q"], ents, dec)
        ms = int((time.time() - t1) * 1000)
        new_internal = (refined or dec)["order"][0] if dec["order"] else None
        new_disp = disp.get(new_internal, new_internal)
        exp = set(q.get("expect") or [])
        rows.append({"id": q["id"], "cat": q["cat"], "expect": sorted(exp),
                     "base": base_disp, "new": new_disp,
                     "base_ok": base_disp in exp, "new_ok": new_disp in exp,
                     "fired": bool(fired), "why": why, "ms": ms,
                     "changed": base_disp != new_disp,
                     "confident": bool(dec.get("confident")),
                     "gap": (why or {}).get("gap")})
    nn = [r for r in rows if r["cat"] != "噪声"]
    ns = [r for r in rows if r["cat"] == "噪声"]
    m = {"轨": tag,
         "基线_p@1": round(sum(1 for r in nn if r["base_ok"]) / len(nn), 3),
         "精排后_p@1": round(sum(1 for r in nn if r["new_ok"]) / len(nn), 3),
         "基线命中": "%d/%d" % (sum(1 for r in nn if r["base_ok"]), len(nn)),
         "精排后命中": "%d/%d" % (sum(1 for r in nn if r["new_ok"]), len(nn)),
         "触发题数": sum(1 for r in rows if r["fired"]),
         "被改写的题": [r["id"] for r in rows if r["changed"]],
         "救回": [r["id"] for r in rows if r["fired"] and not r["base_ok"] and r["new_ok"]],
         "弄坏": [r["id"] for r in rows if r["fired"] and r["base_ok"] and not r["new_ok"]],
         "噪声题被动到的次数": sum(1 for r in ns if r["changed"]),
         "耗时_s": round(time.time() - t0, 1)}
    RES[tag] = m
    print("\n=== %s ===" % tag)
    for k in ("基线命中", "精排后命中", "触发题数", "救回", "弄坏", "被改写的题", "噪声题被动到的次数", "耗时_s"):
        print("   %-14s %s" % (k, m[k]))
    for r in rows:
        if r["changed"]:
            print("      %-16s %s → %s ｜ %s ｜ gap=%s" % (r["id"], r["base"] or "-", r["new"] or "-",
                                                           r["why"].get("trigger"), r["gap"]))

# ---- 测评口复核（模拟 /api/chat?probe=1 → route_tools(allow_decision=False)）----
#   2026-10-13 用户裁定 6：03 测评必须强制关决策层，哪怕开关开着；且不读不写缓存。
def _audit_lines() -> int:
    return len([x for x in dr.AUDIT_PATH.read_text(encoding="utf-8").splitlines() if x.strip()]) if dr.AUDIT_PATH.exists() else 0


_a0 = _audit_lines()
_saved_switch = dr.switch_on
dr.switch_on = lambda account_id=None: True          # 开关假装开着
_r = tr.route_tools(3, QS[0]["q"], allow_decision=False)
dr.switch_on = _saved_switch
_a1 = _audit_lines()
print("\n=== 测评口复核（开关开着 + allow_decision=False）===")
print("   模式=%s ｜ 详情 decision=%s（应为 None）" % (_r.mode, _r.detail.get("decision")))
print("   审计新增 %d 条（应为 1 条，且是 seen + eval_off）" % (_a1 - _a0))
_last = json.loads(dr.AUDIT_PATH.read_text(encoding="utf-8").splitlines()[-1]) if _a1 > _a0 else {}
print("   最后一条:", {k: _last.get(k) for k in ("event", "fired", "eval_off") if _last.get(k) is not None})
RES["测评口复核"] = {"mode": _r.mode, "decision": _r.detail.get("decision"),
                     "audit_added": _a1 - _a0, "last_audit": _last}

RES["运行统计"] = dr.stats()
RES["延迟"] = {"调用次数": RES["运行统计"]["session"]["calls"], "缓存命中": RES["运行统计"]["session"]["hits"]}
OUT.write_text(json.dumps(RES, ensure_ascii=False, indent=1), encoding="utf-8")
print("\n运行统计：", json.dumps(RES["运行统计"], ensure_ascii=False))
print("落盘 →", OUT)
