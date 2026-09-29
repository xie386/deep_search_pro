# -*- coding: utf-8 -*-
"""M5b-2 阈值标定：造 8 个异构工具，量 z 分数 / 词法命中 / 注入量。

**为什么必须做这件事**：设计文档按绝对相似度写了 τ_high=0.50 / τ_low=0.35，
但实测发现 bge 对中文短问句的余弦相似度全落在 0.53~0.70、区分度只有 0.02~0.06——
绝对阈值会「全部命中」，等于没路由。这里在 8 工具规模下重新标定相对阈值（z + 词法）。

运行：
    .venv/Scripts/python.exe tests/_m5b_route_calib.py
    .venv/Scripts/python.exe tests/_m5b_route_calib.py --keep     # 保留临时账号便于手查
"""

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from tools import capability_pool as cp  # noqa: E402
from tools import cli_registry as reg  # noqa: E402
from tools import tool_router as tr  # noqa: E402
from tools.schema_personal import ensure_tables, get_personal_conn, purge_account  # noqa: E402

USER = "m5bcalib_%s" % time.strftime("%m%d%H%M%S")
PWD = "m5bcalib123"

# 8 个异构工具：领域互不重叠，能力描述都写成「关键词行 + 用户说法→命令」的两步式
TOOLS = [
    ("日程 CLI", "sched", "today\nweek\nfree\nmeeting info",
     "关键词：日程/安排/会议/待办/本周/空闲。\n本周有什么安排→week；今天有什么→today；找空闲→free；这个会议在哪→meeting info"),
    ("日志查询", "logq", "query\ntail\nstats",
     "关键词：日志/报错/异常/接口/请求量。\n查某接口的报错→query；看最新日志→tail；请求量统计→stats"),
    ("代码仓库", "ghcli", "issue list\npr list\nrepo view",
     "关键词：仓库/Issue/PR/合并请求/代码评审/开源项目。\n我有哪些 PR→pr list；仓库里的 issue→issue list；仓库概况→repo view"),
    ("天气 API", "wxapi", "now\nforecast",
     "关键词：天气/气温/下雨/空气质量/未来几天。\n现在天气→now；未来几天→forecast"),
    ("财经行情", "finq", "quote\nkline\nnews",
     "关键词：股票/基金/行情/涨跌/财报/大盘。\n某只股票现价→quote；走势→kline；财经新闻→news"),
    ("读书工具", "booky", "shelf recent\nsearch\nbook progress\nnotes notebooks",
     "关键词：读书/书架/在读/阅读进度/笔记/划线/书评。\n我正在读什么书→shelf recent；读到哪了→book progress；找书→search；我的笔记→notes notebooks"),
    ("视频平台", "vidz", "hot\ninfo\nsearch",
     "关键词：视频/B站/播放量/弹幕/视频讲什么/字幕。\n某视频讲了什么→info；热门视频→hot；找视频→search"),
    ("企业通讯", "imq", "sessions\nhistory\nunread",
     "关键词：聊天记录/消息/未读/群聊/联系人。\n和某人的聊天记录→history；未读消息→unread；会话列表→sessions"),
]

# 探针问句：每条都应命中一个明确目标（括号里是期望命中的工具）
QUESTIONS = [
    ("我本周还有什么安排", "sched"),
    ("帮我看看昨天接口有没有报错", "logq"),
    ("我有哪些还没合并的 PR", "ghcli"),
    ("现在外面天气怎么样", "wxapi"),
    ("宁德时代今天涨了多少", "finq"),
    ("我正在读什么书", "booky"),
    ("那个视频讲了什么", "vidz"),
    ("和妹妹的聊天记录", "imq"),
    ("我这个月读了多久", "booky"),
    ("今天大盘怎么样", "finq"),
    # 反例（不该被认为有明确目标 → 期望回退全量）
    ("你是谁", None),
    ("帮我写一首诗", None),
]


def _account_id(username):
    conn = get_personal_conn()
    try:
        row = conn.execute("SELECT id FROM accounts WHERE username=?", (username,)).fetchone()
        return int(row["id"]) if row else 0
    finally:
        conn.close()


def main():
    keep = "--keep" in sys.argv
    ensure_tables()
    from api.account import _create_account, _hash_pwd
    _create_account(USER, _hash_pwd(PWD), "personal", None)
    acc = _account_id(USER)
    print("临时账号 %s (id=%s)，造 %d 个工具\n" % (USER, acc, len(TOOLS)))
    for name, bin_, rules, ab in TOOLS:
        reg.save_cli(acc, {"name": name, "bin": bin_, "readonly": rules, "abilities": ab,
                           "state": "authed"})
    ents = cp.pool_entries(acc)
    print("池内条目：%d 个 | %s\n" % (len(ents), [e.ref for e in ents]))

    print("%-24s %-22s %-6s %-6s %-5s %s" % ("问句", "dense top3", "z_top", "lex_top", "命中", "模式/命中工具"))
    print("-" * 118)
    rows = []
    for q, expect in QUESTIONS:
        dense = cp.route_scores(acc, q)                 # 惰性同步在这里发生（首次加载 bge）
        dec = tr.decide(q, ents, dense)
        top3 = sorted(dec["dense"].items(), key=lambda kv: -kv[1])[:3]
        t3 = " ".join("%s=%.2f" % (k.split(":")[1], v) for k, v in top3)
        hit = dec["order"][0].split(":")[1] if dec["order"] else "-"
        rt = tr.route_tools(acc, q)
        mark = "✅" if (expect is None and rt.mode == "full") or (expect == hit and rt.mode == "routed") \
            else ("≈" if expect in rt.hits else "❌")
        print("%-24s %-22s %-6.2f %-6.1f %-5s %s %s %s"
              % (q, t3, dec["z_top"], dec["lex_top"], hit, mark, rt.mode,
                 ("→" + ",".join(rt.hits)) if rt.hits else ""))
        rows.append((q, expect, hit, dec["z_top"], dec["lex_top"], rt.mode, rt.hits,
                     len(rt.brief) + len(rt.examples)))

    print("\n--- 注入量对比（该 8 工具规模下）---")
    full_len = sum(len(x) for x in (reg.agent_brief(acc), reg.ability_examples_block(acc)))
    routed = [r for r in rows if r[5] == "routed"]
    print("全量形态：%d 字" % full_len)
    if routed:
        avg = sum(r[7] for r in routed) / len(routed)
        print("路由命中形态：平均 %d 字（%.0f%% of 全量），最小 %d / 最大 %d"
              % (avg, 100.0 * avg / max(1, full_len), min(r[7] for r in routed),
                 max(r[7] for r in routed)))
    print("回退全量条数：%d / %d" % (len(rows) - len(routed), len(rows)))

    z_ok = [r[3] for r in rows if r[1]]
    z_bad = [r[3] for r in rows if r[1] is None]
    print("\n--- z 分布 ---")
    print("应命中：min %.2f / avg %.2f / max %.2f" % (min(z_ok), sum(z_ok) / len(z_ok), max(z_ok)))
    if z_bad:
        print("反例：min %.2f / max %.2f" % (min(z_bad), max(z_bad)))
    print("\n当前阈值：Z_FULL=%.2f LEX_MIN=%d MAX_FULL=%d COMPACT=%d 预算=%d"
          % (tr.Z_STRONG, tr.LEX_MIN, tr.MAX_FULL_CARDS, tr.COMPACT_TOP_N, tr.TOTAL_BUDGET_CHARS))

    print("--- 完整卡张数 → 注入量（决策参考，不改默认值）---")
    for mf in (1, 2, 3):
        old = tr.MAX_FULL_CARDS
        tr.MAX_FULL_CARDS = mf
        try:
            lens = [len(r.brief) + len(r.examples) for r in
                    (tr.route_tools(acc, q) for q, _ in QUESTIONS) if r.mode == "routed"]
            if lens:
                print("MAX_FULL_CARDS=%d → 命中时平均 %d 字（%.0f%% of 全量）"
                      % (mf, sum(lens) / len(lens), 100.0 * sum(lens) / len(lens) / max(1, full_len)))
        finally:
            tr.MAX_FULL_CARDS = old

    if keep:
        print("\n--keep：保留账号 %s（id=%s）" % (USER, acc))
    else:
        purge_account(acc)
        import shutil

        from rag_knowledge.kb_service import DB_ROOT
        shutil.rmtree(DB_ROOT / USER / "chroma_route", ignore_errors=True)
        print("\n已清理临时账号 %s" % USER)
    return 0


if __name__ == "__main__":
    sys.exit(main())
