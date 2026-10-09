# -*- coding: utf-8 -*-
"""M6b · 定向复跑器：上一轮 △ 类（live）+ v1 遗留的离线 5 例（offline）。

为什么需要它
------------
全量 43 条里 **√ 类不必复跑**（耗额度、信息增量低）；修复后真正要看的只有两类：

    ① live    ：上一轮判 △ 的题（默认 #25 #27 #30 #35 #36）——走真实账号 + 真实模型
    ② offline ：v1 遗留的 5 例离线路由回归（`tests/fixtures/router_bench_regressions.json`）——纯本地零额度

制度（沿用探针 v3）：**脚本只记录、不判对错**。
    live 段产出人工评分表（你看调用序列自己打分，探针负责记账）；
    离线段产出 top3 对照（期望 ref 落在第几位 / 未入），也不做通过判定。

用法（项目根、`.venv` 下；建议先 `unset PYTHONPATH`）
---------------------------------------------------
    .venv/Scripts/python.exe tests/live/m6b_delta_rerun.py --selftest       # 离线自检（不联网、不调模型）
    .venv/Scripts/python.exe tests/live/m6b_delta_rerun.py --offline-only   # 只跑离线 5 例（零额度，约 1 分钟）
    .venv/Scripts/python.exe tests/live/m6b_delta_rerun.py --live-only --desktop   # 只跑 live 段（耗额度）
    .venv/Scripts/python.exe tests/live/m6b_delta_rerun.py --desktop        # 两段都跑（默认）

可选参数
--------
    --ids 25,27,30,35,36   live 段复跑的题号（默认即上一轮 △ 类）
    --desktop              评分表另存一份到桌面（与探针同款开关）
环境变量：M5B_USER / M5B_PWD（探针账号，默认 `尼古喵喵`）、M5B_KEEP=1（保留 live 产物，供人工复盘）
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ.pop("PYTHONPATH", None)

LIVE_DIR = os.path.join(ROOT, "tests", "live")
PROBE = os.path.join(LIVE_DIR, "m5b_pressure_probe.py")
REGS = os.path.join(ROOT, "tests", "fixtures", "router_bench_regressions.json")
SNAP = os.path.join(ROOT, "tests", "fixtures", "router_pool_snapshot.json")
OUT_DIR = os.path.join(ROOT, "tests", "test_out")

# 上一轮（2026-10-02 全量评分）判 △ 的题号 —— 下一轮的复跑对象
DEFAULT_IDS = ["25", "27", "30", "35", "36"]


# ---------------------------------------------------------------- 离线段
def _load_fixtures():
    regs = json.load(open(REGS, encoding="utf-8"))
    pool = json.load(open(SNAP, encoding="utf-8"))
    return regs, pool


def selftest() -> int:
    """不加载 embedding、不联网：只验夹具形状与 ref 可解析性，以及探针是否支持定向复跑。"""
    print("=== 离线段自检 ===")
    regs, pool = _load_fixtures()
    refs = {r.get("ref") for r in pool}
    cases = regs.get("cases") or []
    print("  回归例 %d 条 ｜ 冻结池 %d 条 ref" % (len(cases), len(refs)))
    bad = []
    for c in cases:
        for r in (c.get("expect") or []):
            if r not in refs:
                bad.append((c.get("id"), r))
    print("  expect 里的 ref 全部能在冻结池里解析：%s" % ("是" if not bad else "否 → %s" % bad))

    src = open(PROBE, encoding="utf-8", errors="replace").read()
    has_ids = "M5B_IDS" in src
    print("  探针支持定向复跑（M5B_IDS）：%s" % ("是" if has_ids else "否"))
    missing = [i for i in DEFAULT_IDS if ("dict(id=%s," % i) not in src]
    print("  默认复跑题号在探针题集里都能找到：%s" % ("是" if not missing else "否 → 缺 %s" % missing))
    print("  期望落位判定方式：脚本只记录 top3 与落位（不做通过判定）")

    # 纯逻辑自检：落位计算
    top3 = ["a", "b", "c"]
    assert {x: top3.index(x) + 1 for x in ["c", "z"] if x in top3} == {"c": 3}
    print("\n结果：%s" % ("自检通过" if not bad and has_ids and not missing else "自检发现上述问题"))
    return 0 if (not bad and has_ids and not missing) else 2


def _live_entries():
    """取**真实账号的当前能力池**（含刷新后的卡片）。只读数据库，不调模型。"""
    import sqlite3                            # noqa: PLC0415
    from tools import capability_pool as cp   # noqa: PLC0415
    user = os.getenv("M5B_USER", "尼古喵喵").strip()
    conn = sqlite3.connect(os.path.join(ROOT, "data", "personal.db"))
    row = conn.execute("SELECT id FROM accounts WHERE username=?", (user,)).fetchone()
    conn.close()
    if not row:
        raise SystemExit("✗ 账号不存在：%s" % user)
    return user, row[0], cp.pool_entries(row[0])


def offline(live_pool: bool = False) -> dict:
    """跑离线 5 例：复用 m5c_router_bench 的 top3 口径（纯本地 embedding）。

    `live_pool=True` 时用**真实账号的当前能力池**（能看到刷新后的工具描述），
    但它**不能与冻结基线直接比**（池子内容不同），只作为"卡片刷新后这 5 例怎么样"的观察。
    """
    import m5c_router_bench as bench          # noqa: PLC0415  ★ 放在函数内：它自己会 chdir
    from rag_knowledge.kb_service import embedding_model   # noqa: PLC0415

    regs, _ = _load_fixtures()
    pool_note = "冻结池（tests/fixtures/router_pool_snapshot.json）"
    if live_pool:
        user, acc, entries = _live_entries()
        pool_note = "真实账号池（%s id=%s，含刷新后的卡片）" % (user, acc)
    else:
        entries = bench.load_entries(True)
    emb = embedding_model()
    mat = emb.embed_documents([bench.vector_text(e, False) for e in entries])
    print("  条目 %d 条 ｜ 向量已算（纯本地，不碰 Chroma、不调模型）｜ 池：%s" % (len(entries), pool_note))

    rows = []
    for c in regs.get("cases") or []:
        res = bench.route_once(c["q"], entries, emb, False, mat)
        top3 = res["top3"]
        exp = c.get("expect") or []
        place = {r: (top3.index(r) + 1) for r in exp if r in top3}
        snap = c.get("top3") or []
        rows.append({
            "id": c.get("id"), "q": c.get("q"), "kind": c.get("kind"),
            "top3": top3, "expect": exp, "place": place,
            "snapshot_0930": snap, "changed": top3 != snap,
        })
    return {"ts": time.strftime("%F %T"), "rows": rows, "source": os.path.relpath(REGS, ROOT),
            "pool": pool_note, "live_pool": live_pool}


def _print_offline(rep: dict) -> None:
    print("\n=== 离线 5 例：当前 top3 与期望 ref 的落位（脚本只记录、不判对错）===")
    print("  能力池：%s%s" % (rep["pool"], "  ⚠️ 与冻结基线不可直接比" if rep.get("live_pool") else ""))
    for r in rep["rows"]:
        mark = "期望命中 %s" % ("、".join("%s@%d" % (k, v) for k, v in r["place"].items()) or "无")
        print("\n  · %s ｜ %s" % (r["id"], r["q"]))
        print("     期望 ref ：%s" % (r["expect"] or "—"))
        print("     当前 top3：%s" % r["top3"])
        print("     %s ｜ 与 09-30 快照相比：%s" % (mark, "有变化" if r["changed"] else "未变"))


def _write_offline(rep: dict, desktop: bool) -> str:
    os.makedirs(OUT_DIR, exist_ok=True)
    fn = os.path.join(OUT_DIR, "m6b_delta_offline_%s.md" % time.strftime("%Y%m%d_%H%M%S"))
    L = ["# M6b 定向复跑 · 离线 5 例（%s）" % rep["ts"], "",
         "> 来源：`%s`（v1 遗留的期望工具不入 top3 / 同源取错函数五例）" % rep["source"],
         "> 能力池：%s%s" % (rep["pool"], "（**与冻结基线不可直接比**）" if rep.get("live_pool") else ""),
         "> **脚本只记录、不判对错**：期望 ref 的落位由你判。", "",
         "| 例 | 问句 | 当前 top3 | 期望 ref 落位 | 与 09-30 快照 |", "|---|---|---|---|---|"]
    for r in rep["rows"]:
        place = "、".join("%s@%d" % (k, v) for k, v in r["place"].items()) or "未入 top3"
        L.append("| %s | %s | %s | %s | %s |" % (r["id"], r["q"][:30],
                                               "；".join(x.split("/")[-1] for x in r["top3"]),
                                               place, "有变化" if r["changed"] else "未变"))
    L += ["", "## 逐例明细", ""]
    for r in rep["rows"]:
        L += ["### %s ｜ %s" % (r["id"], r["q"]),
              "- 形态：%s" % r["kind"],
              "- 期望 ref：%s" % (", ".join("`%s`" % x for x in r["expect"]) or "—"),
              "- 当前 top3：%s" % ", ".join("`%s`" % x for x in r["top3"]),
              "- 09-30 快照 top3：%s" % ", ".join("`%s`" % x for x in r["snapshot_0930"]),
              "- 落位：%s" % ("、".join("%s@%d" % (k, v) for k, v in r["place"].items()) or "未入 top3"), ""]
    open(fn, "w", encoding="utf-8", newline="\n").write("\n".join(L))
    if desktop:
        import shutil
        dst = os.path.join(os.path.expanduser("~"), "Desktop", os.path.basename(fn))
        shutil.copy2(fn, dst)
        print("  离线对照已另存桌面：%s" % dst)
    return fn


# ---------------------------------------------------------------- live 段
def live(ids: list, desktop: bool) -> int:
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)
    env.pop("M5B_ONLY", None)              # 定向复跑用 M5B_IDS，不要再叠一层冒烟过滤
    env["M5B_SET"] = "full"
    env["M5B_IDS"] = ",".join(ids)
    cmd = [sys.executable, PROBE] + (["--desktop"] if desktop else [])
    print("=== live 段：定向复跑 %s ===" % ",".join(ids))
    print("  将执行：M5B_SET=full M5B_IDS=%s python %s%s"
          % (env["M5B_IDS"], os.path.relpath(PROBE, ROOT), " --desktop" if desktop else ""))
    print("  （真实账号 + 真实模型；跑完按水位线清理本轮新建数据；题集不是完整 43 条）\n")
    rc = subprocess.run(cmd, env=env, cwd=ROOT).returncode
    print("\n  live 段返回码：%s（评分表见上面探针输出；对错以你的打分为准）" % rc)
    return rc


# ---------------------------------------------------------------- 入口
def main() -> int:
    ap = argparse.ArgumentParser(description="M6b 定向复跑：上一轮 △ 类 + v1 离线 5 例")
    ap.add_argument("--ids", default=",".join(DEFAULT_IDS),
                    help="live 段复跑的题号，逗号分隔（默认 %s）" % ",".join(DEFAULT_IDS))
    ap.add_argument("--offline-only", action="store_true", help="只跑离线 5 例（零额度）")
    ap.add_argument("--live-only", action="store_true", help="只跑 live 段（耗模型额度）")
    ap.add_argument("--selftest", action="store_true", help="离线自检（不联网、不调模型）")
    ap.add_argument("--desktop", action="store_true", help="评分表/离线对照另存一份到桌面")
    ap.add_argument("--live-pool", action="store_true",
                    help="离线段改用真实账号的当前能力池（能测到刷新后的卡片；不可与冻结基线直接比）")
    args = ap.parse_args()

    if args.selftest:
        return selftest()

    ids = [x.strip() for x in args.ids.replace("，", ",").split(",") if x.strip()]
    rc = 0
    if not args.live_only:
        rep = offline(live_pool=args.live_pool)
        _print_offline(rep)
        fn = _write_offline(rep, args.desktop)
        print("\n  离线对照已存：%s" % os.path.relpath(fn, ROOT))
        print("  （离线五指标整体口径仍在 `tests/live/m5c_router_bench.py`，本段只做这 5 例的落位对照）")
    if not args.offline_only:
        if not args.live_only:
            print("\n" + "-" * 72)
        rc = live(ids, args.desktop)

    print("\n" + "=" * 72)
    print("提醒：两段都**只记录**——live 段的评分表由你打分，离线段只看期望 ref 落位；")
    print("      惯例上还要顺手跑一次 `tests/live/m5c_router_bench.py` 看整体五指标有没有退化。")
    return rc


if __name__ == "__main__":
    sys.exit(main())
