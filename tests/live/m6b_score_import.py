# -*- coding: utf-8 -*-
"""M6b 评分表回填器：把**人工填好的评分表**（`.md`）解析成机器可读基线。

为什么要有它（2026-10-01 制度变更 ✓）：
  探针 v3 只记录调用序列、**不判对错** → 对错在**你的评分表**里。
  但"下次有没有漂移"这件事更适合机器比：把评分表解析成
  `tests/fixtures/m5b_probe_baseline.json`（**每题的调用序列 + 你的评分 + 备注**）✓，
  以后重跑只用 `m6b_probe_drift.py` 比**调用序列变没变** ✓（不再需要任何自动判分 ✗）。

你在评分表里要填的（其余行不用动 ✓）：
    - 评分：✓ / △ / ✗ / 未测        ← 只认行首这个 `- 评分：`
    - 备注：随便写（△ 与 ✗ 建议写清哪里不对）

跑法：
    .venv/Scripts/python.exe tests/live/m6b_score_import.py "tests/test_out/m6b_score_sheet_2026-10-01_23xx.md"
    .venv/Scripts/python.exe tests/live/m6b_score_import.py "C:/Users/ZQK/Desktop/m6b_score_sheet_….md" --out tests/fixtures/m5b_probe_baseline.json
"""

from __future__ import annotations

import json
import os
import re
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DEFAULT_OUT = os.path.join(ROOT, "tests", "fixtures", "m5b_probe_baseline.json")
# ★ 评分符号归一（2026-10-02）：用户习惯写 √（U+221A）而不是 ✓（U+2713）✗ 两者不同码位
#   → 统一在这里认下来 ✓（同时认 ×/✕ 当 ✗）；回填时归一成 ✓/△/✗/未测 四档 ✓
SCORE_SYNONYM = {"√": "✓", "v": "✓", "V": "✓", "对": "✓", "ok": "✓", "OK": "✓",
                 "×": "✗", "x": "✗", "X": "✗", "✕": "✗", "错": "✗",
                 "半": "△", "部分": "△", "o": "△", "O": "△"}
OK_SCORES = ("✓", "△", "✗", "未测") + tuple(SCORE_SYNONYM)

_HEAD = re.compile(r"^### #(\d+)(\s*★核心)?\s*｜\s*(.*)$")
_KV = {"评分": "score", "备注": "note", "参考链路": "ref_chain", "路由": "meta"}


def _rel(p: str) -> str:
    """安全相对路径：**跨盘符**（评分表常在 C: 桌面、项目在 D:）时 relpath 会抛 ValueError ✗。"""
    try:
        return os.path.relpath(p, ROOT)
    except ValueError:
        return p


def parse(path: str) -> list:
    rows, cur = [], None
    with open(path, encoding="utf-8") as fh:
        for raw in fh:
            line = raw.rstrip("\n")
            m = _HEAD.match(line.strip())
            if m:
                if cur:
                    rows.append(cur)
                cur = {"id": int(m.group(1)), "core": bool(m.group(2)),
                       "q": m.group(3).strip(), "score": None, "note": "", "seq": []}
                continue
            if cur is None:
                continue
            s = line.strip()
            if s.startswith("- 评分："):
                raw = s.split("：", 1)[1].strip()
                cur["score"] = SCORE_SYNONYM.get(raw, raw) or None   # ★ 归一成 ✓/△/✗/未测 ✓
            elif s.startswith("- 备注："):
                cur["note"] = s.split("：", 1)[1].strip()
            elif s.startswith("- 参考链路："):
                cur["ref_chain"] = s.split("：", 1)[1].strip().strip("`")
            elif re.match(r"^\d+\.\s+`", s):                       # 实际调用序列第 N 条
                # ★ 2026-10-02：模型偶尔吐出带换行的畸形参数 → 评分表里这条会被拆成多行 ✗
                #   这里按"开头有编号、结尾有反引号"判定完整；否则把续行并回来 ✓
                if s.count("`") >= 2:
                    cur["seq"].append(s.split("`")[1].strip())
                else:
                    cur["seq"].append(s.split("`")[1] if "`" in s else s)
                    cur["_open"] = True
            elif cur.get("_open") and s:
                cur["seq"][-1] = cur["seq"][-1] + "\\n" + s.replace("`", "").strip()
                if s.endswith("`"):
                    cur["_open"] = False
    if cur:
        rows.append(cur)
    return rows


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if not args:
        print("✗ 用法：m6b_score_import.py <评分表.md> [--out 路径]")
        return 2
    sheet = args[0]
    out = DEFAULT_OUT
    if "--out" in sys.argv:
        out = sys.argv[sys.argv.index("--out") + 1]
    if not os.path.exists(sheet):
        print("✗ 找不到评分表：%s" % sheet)
        return 2

    rows = parse(sheet)
    if not rows:
        print("✗ 没解析出任何题（确认文件是探针生成的评分表 ✓）")
        return 2
    filled = [r for r in rows if r["score"]]
    bad = [r for r in rows if r["score"] and r["score"] not in OK_SCORES]
    verdict = {k: len([r for r in rows if (r["score"] or "") == k]) for k in ("✓", "△", "✗", "未测")}
    payload = {"ts": time.strftime("%F %T"), "source_sheet": sheet, "probe": "v3-recorder",
               "scoring": "manual", "count": len(rows),
               "scored": len(filled), "verdict": verdict,
               "rows": [dict(id=r["id"], core=r["core"], q=r["q"], score=r["score"],
                             note=r["note"], ref_chain=r.get("ref_chain", ""), seq=r["seq"],
                             n_calls=len(r["seq"]),
                             seq_key=" || ".join(r["seq"])) for r in rows]}
    os.makedirs(os.path.dirname(out), exist_ok=True)
    if os.path.exists(out) and "--force" not in sys.argv:
        bak = out + ".bak%d" % int(time.time())
        os.replace(out, bak)
        print("（已把旧基线挪到 %s ✓）" % _rel(bak))
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=1)

    print("评分表：%s" % sheet)
    print("解析出 %d 题 ｜ 已填 %d 题（✓ %d · △ %d · ✗ %d · 未测 %d）"
          % (len(rows), len(filled), verdict["✓"], verdict["△"], verdict["✗"], verdict["未测"]))
    if bad:
        print("⚠️ 认不出的评分写法（已原样保留，建议改成 ✓/△/✗/未测）：%s"
              % sorted({r["score"] for r in bad}))
    if len(filled) < len(rows):
        print("⚠️ 还有 %d 题没填评分 —— 基线仍然可用，但没填的题**不会进入序列对比** ✓"
              % (len(rows) - len(filled)))
    misses = [r for r in rows if r["score"] == "✗"]
    if misses:
        print("\n✗ 清单（建议并入「路由质量修复」观察清单 ✓）：")
        for r in misses:
            print("  #%d %s%s" % (r["id"], r["q"][:40], ("｜ " + r["note"]) if r["note"] else ""))
    print("\n✅ 基线已写：%s" % _rel(out))
    print("   下一步：.venv/Scripts/python.exe tests/live/m6b_probe_drift.py --diff   # 拿最近一次记录比基线")
    return 0


if __name__ == "__main__":
    sys.exit(main())
