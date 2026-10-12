# -*- coding: utf-8 -*-
"""M5c-3 · 离线路由基准（**纯本地**：本地 embedding，不调 LLM、不联网、不烧 Tavily）。

为什么要有它：M5b 的"标定 10/10"是 8 工具池时代、10 条题的结论；池子涨到 28 条后
"路由全对"这个说法既不可复现、也掩盖了两类真问题（**自信选错** 与 **噪声被路由**）。
本脚本给出的数（**2026-10-09 口径收口后的现行口径**）：

    precision@1   top1 是不是可接受工具 —— 「第一个想起的」对不对（**可达**，主力闸门）
    precision@3   top3 里有多少比例是可接受工具 —— 衡量「别乱塞」（★ 本题库有结构上限，见下）
    recall@3      top3 覆盖了可接受工具的比例（可接受集合按 3 封顶）
    hit@3         至少有 1 个正确的题占比 —— 最粗的「有没有想起来」
    灰区比例      判定为"没有明确目标"→ 回退全量的题占比（fail-safe，不是错误）
                  ★ 分两栏：`gray_ratio`（全题，含**噪声题的正确回退**）/
                    `gray_ratio_nonnoise`（非噪声题，才是真「路由没把握」）
    噪声假阳性     噪声题里被"自信路由"的比例（★ 越低越好）
    注入量        按**实际注入形态**统计：完整卡数 + 注入字符（整池全量为参考）

★ 口径三条（2026-10-09 用户裁决收口，详见 `docs/v4.0/Done/01-度量口径收口工程·实现记录.md`）：
  ① **`precision@3` 在本题库有结构上限**（脚本自己算出并打印）——题集里大量题目只有 1 个可接受
     工具、而 top3 恒 3 条 → 单题封顶 1/3。所以「precision@3 ≥ 0.7」**不是可达水位**，
     已作废：不再当软判据、也不再当 Jev 立项线；
  ② **灰区必须分栏看**：噪声题回退全量是**正确行为**，混算会把「灰区」永久顶在 20% 以上，看着像
     26.7% 的路由失败；
  ③ **冻结池 = 素材层前口径**：本脚本永远跑冻结池（可复现），所以它反映的是**没有素材层**的数；
     素材层生效后的数走 `tests/live/m6b_material_ab.py`（同一套指标，草稿纯内存替换进冻结池）。

用法（在项目根、`.venv` 下；先 `unset PYTHONPATH`）：

    .venv/Scripts/python.exe tests/live/m5c_router_bench.py                 # 跑基准（当前代码）
    .venv/Scripts/python.exe tests/live/m5c_router_bench.py --legacy        # ⚠️ 见下方 vector_text：现已与当前口径**等价**、跑不出差异 ✗（M5c-2 已回退）
    .venv/Scripts/python.exe tests/live/m5c_router_bench.py --write-baseline# 记录/更新基线
    ... --diff                                                              # 与基线比（默认会显示差异）

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

# 硬判据的方向（与打印/对比共用一份，别再各写一遍）
METRICS_UP = ("precision@1", "precision@3", "recall@3", "hit@3")        # 越高越好
METRICS_DOWN = ("noise_false_positive",)                               # 越低越好
METRICS_REPORT_ONLY = ("precision@3_ceiling", "gray_ratio", "gray_ratio_nonnoise",
                       "avg_inject_chars_conf", "full_inject_chars")   # 只记录，不参与告警


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
    """一次路由（纯函数 + 内存向量）：返回 decide 结果、top3（真实 ref）、注入字符数与完整卡数。

    `mat` 传进来就复用（28 条条目的向量只算一次；每题只 embed 问句）。
    `full_refs` = 真正给了**完整卡**的那些条目（`_render_routed` 里预算不够会降级成紧凑行，
    所以这个长度已经反映了降级，不要另外复算）。
    """
    if mat is None:
        mat = emb.embed_documents([vector_text(e, legacy) for e in entries])
    qv = emb.embed_query(question)
    dense = {f"{e.source}:{e.ref}": cos(qv, v) for e, v in zip(entries, mat)}
    dec = tr.decide(question, entries, dense)
    # decide 的键是 f"{source}:{ref}"，而 ref 自身已带来源前缀 → 剥掉一层才是真 ref
    top3 = [k.split(":", 1)[1] for k in dec["order"][:3]]
    if dec["confident"]:
        brief, full_refs, _order = tr._render_routed(entries, dec["order"])
    else:
        brief = "\n".join(e.card_text for e in entries)
        full_refs = []
    return {"dec": dec, "top3": top3, "chars": len(brief or ""), "full_refs": full_refs}


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
    full_chars = sum(len(e.card_text) for e in entries)                          # 整池全量的注入量（参考）
    mode = "legacy（M5c-2 之前）" if args.legacy else "current（工具级文本优先）"
    n_non = sum(1 for x in qs if x["cat"] != "噪声")
    print("=" * 100)
    print("M5c-3 离线路由基准｜冻结池 %d 条（= 素材层前口径）｜题 %d 道（非噪声 %d）｜向量文本口径：%s"
          % (len(entries), len(qs), n_non, mode))
    print("=" * 100)

    rows, miss, top1_miss = [], [], []
    n_hit = n_conf = n_noise = n_noise_fp = n_gray = n_gray_conf = n_p1 = 0
    p_sum = r_sum = 0.0
    chars_conf: list[int] = []
    for qi, item in enumerate(qs, 1):
        print("  [%2d/%d] %s" % (qi, len(qs), item["q"][:38]), flush=True)     # 进度（跑几分钟时能看到停在哪）
        res = route_once(item["q"], entries, emb, args.legacy, mat)
        dec, top3, exp = res["dec"], res["top3"], set(item["expect"])
        is_noise = item["cat"] == "噪声"
        confident = bool(dec["confident"])
        if not confident:
            n_gray += 1
        if is_noise:
            n_noise += 1
            if confident:
                n_noise_fp += 1
            ok = not confident
            shape = "—"
        else:
            n_conf += 1
            if not confident:
                n_gray_conf += 1
            chars_conf.append(res["chars"])
            shape = ("%d卡" % len(res["full_refs"])) if confident else "全量"
            inter = [t for t in top3 if t in exp]
            p = len(inter) / max(1, min(3, len(top3)))
            r = len(inter) / max(1, min(3, len(exp)))
            p_sum += p
            r_sum += r
            p1 = 1 if (top3 and top3[0] in exp) else 0
            n_p1 += p1
            if not p1:
                top1_miss.append((item["id"], item["q"], top3[0] if top3 else "—", sorted(exp)))
            if inter:
                n_hit += 1
            else:
                miss.append((item["id"], item["q"], top3, sorted(exp)))
            ok = bool(inter)
        rows.append((item["id"], item["cat"], "✅" if ok else "❌",
                     "routed" if confident else "灰区回退",
                     dec["z_top"], dec["lex_top"], res["chars"], shape, top3[:2], item["q"]) )

    print("%-16s %-6s %-4s %-10s %-7s %-7s %-7s %-6s %s"
          % ("id", "类别", "", "结果", "z_top", "lex_top", "注入字", "形态", "问句"))
    print("-" * 100)
    for rid, cat, ok, mode_s, z, lex, chars, shape, t3, q in rows:
        print("%-16s %-6s %-4s %-10s %-7s %-7s %-7s %-6s %s" % (rid, cat, ok, mode_s, z, lex, chars, shape, q[:34]))
    print("-" * 100)

    # precision@3 的结构上限：单题上限 = min(|可接受|,3)/3（top3 恒 3 条）→ 由题库自己决定，不写死
    n_one = sum(1 for x in qs if x["cat"] != "噪声" and len(x["expect"]) <= 1)
    ceiling = sum(min(len(x["expect"]), 3) for x in qs if x["cat"] != "噪声") / (3.0 * max(1, n_conf))
    m = {
        "precision@1": round(n_p1 / max(1, n_conf), 3),
        "precision@3": round(p_sum / max(1, n_conf), 3),
        "precision@3_ceiling": round(ceiling, 3),
        "recall@3": round(r_sum / max(1, n_conf), 3),
        "hit@3": round(n_hit / max(1, n_conf), 3),
        "gray_ratio": round(n_gray / len(qs), 3),
        "gray_ratio_nonnoise": round(n_gray_conf / max(1, n_conf), 3),
        "noise_false_positive": round(n_noise_fp / max(1, n_noise), 3),
        "avg_inject_chars_conf": int(sum(chars_conf) / max(1, len(chars_conf))),
        "full_inject_chars": full_chars,
        "pool_size": len(entries),
        "questions": len(qs),
    }

    print("指标：", json.dumps(m, ensure_ascii=False))
    print("口径（2026-10-09 收口，见 docs/v4.0/Done/01-度量口径收口工程·实现记录.md）：\n"
          "  · precision@3 本题库**结构上限 %.3f**（非噪声 %d 道里 %d 道只有 1 个可接受工具，而 top3 恒 3 条 → 单题封顶 1/3）\n"
          "    → 「precision@3 ≥ 0.7」**不是可达水位**，已作废（不再当软判据／立项线）；可达的主力信号是 precision@1\n"
          "  · 灰区分栏：gray_ratio 含「噪声题的正确回退」，gray_ratio_nonnoise 才是真「路由没把握」\n"
          "  · 冻结池 = 素材层前口径；素材层生效后的数走 tests/live/m6b_material_ab.py"
          % (ceiling, n_conf, n_one))
    print("软判据（**能力目标**，仅记录不告警）: precision@1 ≥ 0.7 ｜ hit@3 ≥ 0.9 ｜ 噪声假阳性 ≤ 0.1 ｜ 非噪声灰区 ≤ 0.2\n"
          "硬判据（**告警依据**）: 与基线比 precision@1 / precision@3 / recall@3 / hit@3 **不得下降**，噪声假阳性**不得上升** ✓（灰区与注入量只记录 ✗ 不告警）\n"
          "Jev 立项线（2026-10-09 重写，原「precision@3 < 0.7」作废）: **非噪声灰区题 ≥ 5 道**（gray_ratio_nonnoise ≥ 0.2）**或 噪声假阳性 > 0.1** —— 二者都是 Jev 能治的量")
    if top1_miss:
        print("\ntop1 不在可接受集合（precision@1 的失分点，逐题看是否真该排第一）：")
        for mid, q, t1, exp in top1_miss:
            print("  · %-16s %s\n      top1=%s\n      可接受=%s" % (mid, q, t1, exp))
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
        for k in ("precision@1", "precision@3", "precision@3_ceiling", "recall@3", "hit@3",
                  "gray_ratio", "gray_ratio_nonnoise", "noise_false_positive",
                  "avg_inject_chars_conf", "full_inject_chars"):
            o, n = old.get(k), m.get(k)
            if o is None or n is None:
                print("  %-22s %-8s → %-8s（基线里没有这一项，未参与告警）" % (k, o, n))
                continue
            arrow = "→" if o == n else ("↑" if n > o else "↓")
            good = ""
            if k in METRICS_UP and n != o:
                good = "  ✅改善" if n > o else "  ❌变差"
            if k in METRICS_DOWN and n != o:
                good = "  ✅改善" if n < o else "  ❌变差"
            print("  %-22s %-8s %s %-8s%s" % (k, o, arrow, n, good))


if __name__ == "__main__":
    main()
