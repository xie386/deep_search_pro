# -*- coding: utf-8 -*-
"""M5c-3 · 离线路由基准（**纯本地**：本地 embedding，不调 LLM、不联网、不烧 Tavily）。

为什么要有它：M5b 的"标定 10/10"是 8 工具池时代、10 条题的结论；池子涨到 28 条后
"路由全对"这个说法既不可复现、也掩盖了两类真问题（**自信选错** 与 **噪声被路由**）。
本脚本给出四个可复现的数字：

    precision@3   top3 里有多少比例是可接受工具
    recall@3      top3 覆盖了可接受工具的比例（可接受集合按 3 封顶）
    hit@3         至少有 1 个正确的题占比
    灰区比例      判定为"没有明确目标"→ 回退全量的题占比（fail-safe，不是错误）
    噪声假阳性     噪声题里被"自信路由"的比例（★ 越低越好，这是 Jev 的主要理由之一）

用法（在项目根、`.venv` 下；先 `unset PYTHONPATH`）：

    .venv/Scripts/python.exe tests/m5c_router_bench.py                 # 跑基准（当前代码）
    .venv/Scripts/python.exe tests/m5c_router_bench.py --legacy        # ⚠️ 见下方 vector_text：现已与当前口径**等价**、跑不出差异 ✗（M5c-2 已回退）
    .venv/Scripts/python.exe tests/m5c_router_bench.py --write-baseline# 记录/更新基线
    ... --diff                                                          # 与基线比（默认会显示差异）

设计：**冻结池**（`tests/fixtures/router_pool_snapshot.json`，从真实账号一次性导出）+ **题库**
（`tests/fixtures/router_bench.json`，30 题带可接受 ref 集合）+ **基线**（`tests/fixtures/router_bench_baseline.json`）。
⚠️ 三份 fixture **必须随仓库提交**（曾经放在 `tests/data/`，被 `.gitignore` 的 `data/` 规则吞掉 → 复现会失败）。
冻结池让结果可复现（不受账号增删工具影响）；向量分**在内存里算**（不碰 Chroma、不需要账号）。
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ.pop("PYTHONPATH", None)

from tools import capability_pool as cp      # noqa: E402
from tools import tool_router as tr          # noqa: E402

SNAP = os.path.join(ROOT, "tests", "fixtures", "router_pool_snapshot.json")
BENCH = os.path.join(ROOT, "tests", "fixtures", "router_bench.json")
BASE = os.path.join(ROOT, "tests", "fixtures", "router_bench_baseline.json")


# ---------------------------------------------------------------- 冻结池
def load_entries(derive_kw: bool = True):
    """重建冻结池。`derive_kw=True` 时**镜像 `capability_pool._entry_from_row`** 的关键词兜底
    （工具/来源都没写关键词 → 从工具级描述派生），否则基准测不到这项改动。"""
    rows = json.load(open(SNAP, encoding="utf-8"))
    out = []
    for r in rows:
        kw = r.get("keywords", "") or ""
        if derive_kw and not kw:
            kw = cp.derive_keywords(r.get("abilities_tool") or r.get("abilities") or "")
        out.append(cp.CapabilityEntry(
            source=r["source"], ref=r["ref"], name=r["name"], keywords=kw,
            abilities=r.get("abilities", ""), abilities_tool=r.get("abilities_tool", ""),
            invoke_hint=r.get("invoke_hint", ""), enabled=bool(r.get("enabled", True)),
            rules=tuple(tuple(x) for x in (r.get("rules") or [])), invokable=bool(r.get("invokable", True)),
            input_schema=r.get("input_schema") or {}, invoke_spec=r.get("invoke_spec") or {},
            read_only=bool(r.get("read_only", True)), confirmed=bool(r.get("confirmed", True))))
    return out


def vector_text(e, legacy: bool) -> str:
    """取「送去 embed 的文本」。

    ⚠️ **2026-10-02 核对：`--legacy` 分支与当前口径已经等价** ✗ —— M5c-2 那次「只取工具级
    描述（`abilities_tool`）」实测是**负收益已回退**，而 `capability_pool._entry_text()` 用的正是
    这里 legacy 形态的合并文本（name + keywords + abilities + 参数摘要）。所以：
      · `--legacy` 现在**跑不出任何差异** ✗ 别再拿它做「M5c-2 前后」的归因 ✓；
      · 真正有效的归因开关是 `--no-derive-keywords`（关掉关键词派生兜底）与非 `--legacy` 的
        逐例 top3/z/词法对照 ✓。
    """
    if not legacy:
        return cp._entry_text(e)
    parts = [e.name]
    if e.keywords:
        parts.append(e.keywords)
    if e.abilities:
        parts.append(e.abilities)
    if not e.is_cli:
        ps = e.param_summary
        if ps:
            parts.append(ps)
    return "\n".join(parts).strip()


def cos(a, b) -> float:
    """余弦（Chroma 用 cosine 空间，`1 - distance` 就是它）。"""
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a)) or 1.0
    nb = math.sqrt(sum(y * y for y in b)) or 1.0
    return dot / (na * nb)


def route_once(question: str, entries, emb, legacy: bool, mat=None) -> dict:
    """一次路由（纯函数 + 内存向量）：返回 decide 结果、top3（真实 ref）与注入字符数。

    `mat` 传进来就复用（28 条条目的向量只算一次；每题只 embed 问句）。
    """
    if mat is None:
        mat = emb.embed_documents([vector_text(e, legacy) for e in entries])
    qv = emb.embed_query(question)
    dense = {f"{e.source}:{e.ref}": cos(qv, v) for e, v in zip(entries, mat)}
    dec = tr.decide(question, entries, dense)
    # decide 的键是 f"{source}:{ref}"，而 ref 自身已带来源前缀 → 剥掉一层才是真 ref
    top3 = [k.split(":", 1)[1] for k in dec["order"][:3]]
    if dec["confident"]:
        brief, _full_refs, _order = tr._render_routed(entries, dec["order"])
    else:
        brief = "\n".join(e.card_text for e in entries)
    return {"dec": dec, "top3": top3, "chars": len(brief or "")}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--legacy", action="store_true", help="用 M5c-2 之前的向量文本（对比用）")
    ap.add_argument("--no-derive-keywords", action="store_true",
                    help="不派生关键词兜底（用于归因：只看词法/向量那部分的改动）")
    ap.add_argument("--write-baseline", action="store_true", help="把本次结果写成基线")
    ap.add_argument("--no-diff", action="store_true", help="不与基线比较")
    args = ap.parse_args()

    entries = load_entries(not args.no_derive_keywords)
    qs = json.load(open(BENCH, encoding="utf-8"))["questions"]
    from rag_knowledge.kb_service import embedding_model
    emb = embedding_model()
    mat = emb.embed_documents([vector_text(e, args.legacy) for e in entries])   # 只算一次
    mode = "legacy（M5c-2 之前）" if args.legacy else "current（工具级文本优先）"
    print("=" * 100)
    print("M5c-3 离线路由基准｜冻结池 %d 条｜题 %d 道｜向量文本口径：%s" % (len(entries), len(qs), mode))
    print("=" * 100)

    rows, miss = [], []
    n_hit = n_conf = n_noise = n_noise_fp = 0
    p_sum = r_sum = 0.0
    for qi, item in enumerate(qs, 1):
        print("  [%2d/%d] %s" % (qi, len(qs), item["q"][:38]), flush=True)     # 进度（跑几分钟时能看到停在哪）
        res = route_once(item["q"], entries, emb, args.legacy, mat)
        dec, top3, exp = res["dec"], res["top3"], set(item["expect"])
        is_noise = item["cat"] == "噪声"
        if is_noise:
            n_noise += 1
            if dec["confident"]:
                n_noise_fp += 1
            ok = not dec["confident"]
        else:
            n_conf += 1
            inter = [t for t in top3 if t in exp]
            p = len(inter) / max(1, min(3, len(top3)))
            r = len(inter) / max(1, min(3, len(exp)))
            p_sum += p
            r_sum += r
            if inter:
                n_hit += 1
            else:
                miss.append((item["id"], item["q"], top3, sorted(exp)))
            ok = bool(inter)
        rows.append((item["id"], item["cat"], "✅" if ok else "❌",
                     "routed" if dec["confident"] else "灰区回退",
                     dec["z_top"], dec["lex_top"], res["chars"], top3[:2], item["q"]) )

    print("%-16s %-6s %-4s %-10s %-7s %-7s %-7s %s" % ("id", "类别", "", "结果", "z_top", "lex_top", "注入字", "问句"))
    print("-" * 100)
    for rid, cat, ok, mode_s, z, lex, chars, t3, q in rows:
        print("%-16s %-6s %-4s %-10s %-7s %-7s %-7s %s" % (rid, cat, ok, mode_s, z, lex, chars, q[:34]))
    print("-" * 100)

    m = {
        "precision@3": round(p_sum / max(1, n_conf), 3),
        "recall@3": round(r_sum / max(1, n_conf), 3),
        "hit@3": round(n_hit / max(1, n_conf), 3),
        "gray_ratio": round((len(qs) - n_conf - (n_noise - n_noise_fp)) / len(qs), 3),
        "noise_false_positive": round(n_noise_fp / max(1, n_noise), 3),
        "pool_size": len(entries), "questions": len(qs),
    }
    # 灰区比例 = 所有题里被判"无明确目标"的比例（噪声题里应路由的那种也算灰区，属正确行为）
    n_gray = sum(1 for r in rows if r[3] == "灰区回退")
    m["gray_ratio"] = round(n_gray / len(qs), 3)

    print("指标：", json.dumps(m, ensure_ascii=False))
    print("软判据（**能力目标**，仅记录不告警 ✓ 裁决①）: precision@3 ≥ 0.7 ｜ 噪声假阳性 ≤ 0.1 ｜ 灰区 ≤ 0.3\n"
          "硬判据（**告警依据** ✓）: 与基线对比**任一指标不得下降** ✓ —— 下降才告警 ✓（别再拿单次波动当能力退化 ✗）")
    if miss:
        print("\n未命中（需要看是不是真漏）：")
        for mid, q, top3, exp in miss:
            print("  ✗ %-16s %s\n      top3=%s\n      期望=%s" % (mid, q, top3, exp))

    if args.write_baseline:
        json.dump(m, open(BASE, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        print("\n✅ 已写基线 %s" % os.path.relpath(BASE, ROOT))
        return
    if not args.no_diff and os.path.exists(BASE):
        old = json.load(open(BASE, encoding="utf-8"))
        print("\n与基线对比（%s）:" % os.path.relpath(BASE, ROOT))
        for k in ("precision@3", "recall@3", "hit@3", "gray_ratio", "noise_false_positive"):
            o, n = old.get(k), m.get(k)
            arrow = "→" if o == n else ("↑" if n > o else "↓")
            good = ""
            if k in ("precision@3", "recall@3", "hit@3") and n != o:
                good = "  ✅改善" if n > o else "  ❌变差"
            if k in ("noise_false_positive",) and n != o:
                good = "  ✅改善" if n < o else "  ❌变差"
            print("  %-22s %-8s %s %-8s%s" % (k, o, arrow, n, good))


if __name__ == "__main__":
    main()
