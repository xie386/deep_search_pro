# -*- coding: utf-8 -*-
"""第四批素材改进（用户 2026-10-11 授权 + 逐条修正措辞）。

四条：
 1. Steam（searchGame / gameDetail）—— **用原语言游戏名搜、不要用非原语言译名**；
    ★ 用户修正：不写「别用中文译名」（存在中文游戏被英译/日译）、也不点名「英文/日文」（会造成只能用这两种语言的误解）
 2. 12306 站码（get-station-code-of-citys / -by-names）—— **把参数名写进描述**（`citys` 不是 `cityNames`）
 3. BNF Gallica 检索系列 —— 中文主题先译法语；**一次调用即可、翻页用 page、不要换工具反复试探**
 4. bili —— **同一条子命令报错不要一直重复试**：先静默放下，去做别的任务，做完再回头重试
    ★ 用户修正：不是「别重复试」这么简单 —— 工具本身对高频重复调用会报错，所以要「放下 → 先做别的 → 回头再试」

做法：api/mcp 走**正门** `api.tools_sources.cap_abilities_save`（自动 `_bump` 池版本 + `ensure_vectors_fresh`
重算向量）；CLI（bili）正门不支持 → 用**同一步等价**的直接写入 + 同样 bump + 重算（已在报告里标出这个缺口）。
改前已备份：docs/v4.0/backups/personal_before_material_round4_20261011_190533.db
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
os.environ.pop("PYTHONPATH", None)

from tools.schema_personal import get_personal_conn          # noqa: E402
from api.account import create_session                        # noqa: E402
from api import tools_sources as ts                           # noqa: E402
from tools import capability_pool as pool                      # noqa: E402

ACC = 3

STEAM_RULE = ("★ 用**这款游戏的原语言名称**（它原本发行时使用的语言）去搜，**不要用非原语言的译名**："
              "原语言是中文的游戏就用中文名搜，原语言是别的语言的游戏就用它的原语言名搜 —— 译名往往搜不到。"
              "某个写法搜不到时，换成它的**原语言名称**再搜一次，不要反复换译名去试。")
STEAM_DETAIL = ("★ 前置：先用 `searchGame` 按**游戏的原语言名称**拿到 appid（不要用非原语言的译名），"
                "再调本工具看详情与价格。")
PARAM_CITYS = ("★ 参数名就是 `citys`（**不是** `cityNames`）：多个城市用 `|` 分隔，例 `上海|广州`。"
               "参数名传错会失败，别反复试别的拼法。")
PARAM_STATIONS = ("★ 参数名就是 `stationNames`：多个车站名用英文逗号分隔，例 `北京南站,上海虹桥站`。")
BNF_RULE = ("★ 中文主题**先译成法语/英语**再检索（例：巴黎公社 → Commune de Paris 1871）。\n"
            "★ 检索**一次调用**就该有结果：要更多结果用 `page` 翻页，**不要换别的检索工具反复试探**"
            "（按书名/主题没搜到时，先换关键词重搜一次）。")
BILI_RULE = ("★ 同一条子命令**报错时不要一直重复试**（过高的重复调用频次本身就会让工具报错）："
             "先**静默放下一段时间**，去做别的要做的任务，**做完之后再回头重试**。")

APPEND = {
    "api:steam#searchGame": STEAM_RULE,
    "api:steam#gameDetail": STEAM_DETAIL,
    "mcp:12306-mcp/get-station-code-of-citys": PARAM_CITYS,
    "mcp:12306-mcp/get-station-code-by-names": PARAM_STATIONS,
    "mcp:bnf/natural_language_search": BNF_RULE,
    "mcp:bnf/advanced_search": BNF_RULE,
    "mcp:bnf/search_by_subject": BNF_RULE,
    "mcp:bnf/search_by_title": BNF_RULE,
    "mcp:bnf/search_by_author": BNF_RULE,
    "mcp:bnf/search_by_date": BNF_RULE,
    "mcp:bnf/search_by_document_type": BNF_RULE,
    "bili": BILI_RULE,
}

conn = get_personal_conn()
rows = {r["ref"]: dict(r) for r in conn.execute(
    "SELECT ref, source, COALESCE(abilities,'') ab, COALESCE(abilities_user,'') au "
    "FROM tool_capabilities WHERE account_id=?", (ACC,)).fetchall()}
conn.close()

api_items, cli_items = {}, {}
for ref, extra in APPEND.items():
    r = rows.get(ref)
    if not r:
        print("✗ 池里没有这个工具，跳过:", ref)
        continue
    cur = (r["au"] or "").strip() or (r["ab"] or "").strip()   # 手写优先；bili 没有手写 → 用自动摘要做底
    if extra.split("\n")[0][:20] in cur:
        print("· 已有这段，跳过:", ref)
        continue
    new = (cur + "\n" + extra).strip() if cur else extra.strip()
    (cli_items if r["source"] == "cli" else api_items)[ref] = new
    print("准备:", ref, "| 旧 %d 字 → 新 %d 字" % (len(cur), len(new)))

# --- api/mcp 走正门 ---
if api_items:
    conn = get_personal_conn()
    row = conn.execute("SELECT * FROM accounts WHERE id=?", (ACC,)).fetchone()
    conn.close()
    token = create_session(dict(row))
    by_slug = {}
    for ref, text in api_items.items():
        src, slug = ("api", ref.split("#")[0].split(":", 1)[1]) if ref.startswith("api:") \
            else ("mcp", ref.split("/")[0].split(":", 1)[1])
        by_slug.setdefault((src, slug), []).append({"ref": ref, "text": text})
    for (src, slug), items in by_slug.items():
        out = ts.cap_abilities_save(ts.CapAbilitiesReq(source=src, slug=slug, items=items), token)
        print("正门写入 %s/%s → saved %d / skipped %s" % (src, slug, len(out.get("saved") or []),
                                                        out.get("skipped")))
    conn = get_personal_conn()
    conn.execute("DELETE FROM sessions WHERE token=?", (token,))
    conn.commit()
    conn.close()

# --- CLI 没有正门 → 同一步等价写入（bump + 重算）---
if cli_items:
    conn = get_personal_conn()
    for ref, text in cli_items.items():
        conn.execute("UPDATE tool_capabilities SET abilities_user=?, updated_at=CURRENT_TIMESTAMP "
                     "WHERE account_id=? AND ref=?", (text, ACC, ref))
    conn.commit()
    conn.close()
    print("CLI 直写:", ", ".join(cli_items))

if api_items or cli_items:
    ts._bump(ACC)                    # 与正门同一步：能力池版本 +1（路由器缓存失效）
    try:
        pool.ensure_vectors_fresh(ACC)
        print("池版本已 bump + 向量已重算 ✓")
    except Exception as e:  # noqa: BLE001
        print("向量重算跳过（下次路由自愈）:", e)
