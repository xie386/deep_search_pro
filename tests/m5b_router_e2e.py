# -*- coding: utf-8 -*-
"""M5b-2 · 工具检索路由 —— 端到端验证（不耗模型）

对应设计：`docs/v2.0/M5b工具检索路由.md` §三（路由算法）+ §7.2（兜底三分支）。

覆盖：
  1) 快路径：池 ≤ FAST_PATH_MAX → mode=full，内容与 M5a `agent_brief` **逐字一致**（叠加层不改行为）
  2) 路由路径：8 工具规模 → mode=routed，命中集含期望工具（真 embedding，不调模型）
  3) 反例：无明确目标的问句 → 回退 full（保守，绝不猜）
  4) 兜底三分支：空池 / 向量不可用 / 池读取异常 → 全部回退 full（fail-safe）
  5) 预算护栏：完整卡 ≤ MAX_FULL_CARDS；注入量 ≤ 全量（且记录实际比例）
  6) **未命中工具不隐身**：brief 仍含每个工具的名称行
  7) few-shot 只给命中工具（其余工具示例被滤掉）
  8) 单元：RRF 必须剔除「无信号的排序器」（实测踩过的 bug：词法全 0 时把向量 top1 顶掉）
  9) 来源无关：手工构造 api/mcp 条目喂进路由器，路由逻辑一致（M5b-3/4 的前置验证）
 10) 接线：api/server.py 的 _run_agent 确实走 tool_router

运行：
    .venv/Scripts/python.exe tests/m5b_router_e2e.py
    SKIP_VEC=1 .venv/Scripts/python.exe tests/m5b_router_e2e.py   # 跳过需要 bge 的路由用例
"""

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools import capability_pool as cp  # noqa: E402
from tools import cli_registry as reg  # noqa: E402
from tools import tool_router as tr  # noqa: E402
from tools.schema_personal import ensure_tables, get_personal_conn, purge_account  # noqa: E402

SKIP_VEC = os.environ.get("SKIP_VEC") == "1"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

USER = "m5broute_%s" % time.strftime("%m%d%H%M%S")
PWD = "m5broute123"

# 8 个异构工具（与标定脚本同集合：领域互不重叠）
TOOLS = [
    ("日程 CLI", "sched", "today\nweek\nfree", "关键词：日程/安排/会议/待办/本周。\n本周有什么安排→week；今天有什么→today"),
    ("日志查询", "logq", "query\ntail\nstats", "关键词：日志/报错/异常/接口/请求量。\n查某接口的报错→query；看最新日志→tail"),
    ("代码仓库", "ghcli", "issue list\npr list", "关键词：仓库/Issue/PR/合并请求/代码评审。\n我有哪些 PR→pr list；仓库的 issue→issue list"),
    ("天气 API", "wxapi", "now\nforecast", "关键词：天气/气温/下雨/空气质量。\n现在天气→now；未来几天→forecast"),
    ("财经行情", "finq", "quote\nkline", "关键词：股票/基金/行情/涨跌/财报/大盘。\n某只股票现价→quote；走势→kline"),
    ("读书工具", "booky", "shelf recent\nsearch\nnotes notebooks",
     "关键词：读书/书架/在读/阅读进度/笔记/划线。\n我正在读什么书→shelf recent；找书→search；我的笔记→notes notebooks"),
    ("视频平台", "vidz", "hot\ninfo\nsearch", "关键词：视频/B站/播放量/弹幕/字幕。\n某视频讲了什么→info；热门视频→hot"),
    ("企业通讯", "imq", "sessions\nhistory\nunread", "关键词：聊天记录/消息/未读/群聊/联系人。\n和某人的聊天记录→history；未读消息→unread"),
]
HITS = [("我本周还有什么安排", "sched"), ("帮我看看昨天接口有没有报错", "logq"),
        ("我有哪些还没合并的 PR", "ghcli"), ("现在外面天气怎么样", "wxapi"),
        ("我正在读什么书", "booky"), ("那个视频讲了什么", "vidz"),
        ("和妹妹的聊天记录", "imq"), ("今天大盘怎么样", "finq")]
NEG = ["你是谁", "帮我写一首诗"]

PASS, FAIL = [], []


def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name)
    print("%s %s%s" % ("  ✅" if cond else "  ❌", name, (" | " + str(extra)) if extra else ""))


def _account_id(username):
    conn = get_personal_conn()
    try:
        row = conn.execute("SELECT id FROM accounts WHERE username=?", (username,)).fetchone()
        return int(row["id"]) if row else 0
    finally:
        conn.close()


def _drop_account(username):
    import shutil
    from rag_knowledge.kb_service import DB_ROOT
    conn = get_personal_conn()
    try:
        row = conn.execute("SELECT id FROM accounts WHERE username=?", (username,)).fetchone()
        if not row:
            return False
        aid = int(row["id"])
    finally:
        conn.close()
    purge_account(aid)
    shutil.rmtree(DB_ROOT / username, ignore_errors=True)
    return True


def main():
    ensure_tables()
    from api.account import _create_account, _hash_pwd

    # ------------------------------------------------------------------ [1] 空池 / 快路径
    print("[1] 空池与快路径（≤%d 个工具 → 与 M5a 逐字一致）" % tr.FAST_PATH_MAX)
    r0 = tr.route_tools(999999, "随便问问")
    check("不存在账号 → mode=full 且不报错", r0.mode == "full", r0.detail.get("reason"))
    check("不存在账号 → 注入为空（不是异常文本）", r0.brief == "")

    _create_account(USER, _hash_pwd(PWD), "personal", None)
    acc = _account_id(USER)
    print("  临时账号 %s (id=%s)" % (USER, acc))
    check("无工具账号 → mode=full", tr.route_tools(acc, "随便问问").mode == "full")

    reg.save_cli(acc, {"name": "日程 CLI", "bin": "sched", "readonly": "today\nweek",
                       "abilities": TOOLS[0][3], "state": "authed"})
    r1 = tr.route_tools(acc, "我本周还有什么安排")
    check("3 个以内工具 → 走快路径（mode=full）", r1.mode == "full", r1.detail.get("reason"))
    check("快路径内容与 agent_brief 逐字一致", r1.brief == reg.agent_brief(acc))
    check("快路径示例与 ability_examples_block 一致",
          r1.examples == reg.ability_examples_block(acc))

    # ------------------------------------------------------------------ [2][3] 路由路径
    for name, bin_, rules, ab in TOOLS[1:]:
        reg.save_cli(acc, {"name": name, "bin": bin_, "readonly": rules, "abilities": ab,
                           "state": "authed"})
    ents = cp.pool_entries(acc)
    check("池内 8 个工具（超过快路径阈值）", len(ents) == len(TOOLS), len(ents))

    routed_ok, routed_fb, wrong = [], [], []
    if SKIP_VEC:
        print("\n[2] 路由路径  ⏭ SKIP_VEC=1，跳过（需 bge）")
    else:
        print("\n[2] 路由路径（命中集 + 保守回退）")
        for q, expect in HITS:
            r = tr.route_tools(acc, q)
            if r.mode == "routed":
                if expect in r.hits:
                    routed_ok.append(q)
                else:
                    wrong.append((q, expect, r.hits))
            else:
                routed_fb.append(q)
        check("没有任何一条「路由走了但命中集不含期望工具」（最关键：不猜错）",
              not wrong, wrong)
        print("     路由命中 %d 条 / 保守回退全量 %d 条（回退= M5a 行为，不算错）"
              % (len(routed_ok), len(routed_fb)))
        check("路由命中覆盖 ≥ 6/8（有实际收益）", len(routed_ok) >= 6,
              "%d/%d" % (len(routed_ok), len(HITS)))

        print("\n[3] 反例：无明确目标 → 必须回退全量")
        neg_ok = 0
        for q in NEG:
            r = tr.route_tools(acc, q)
            if r.mode == "full":
                neg_ok += 1
            print("     %-12s → %s（z=%s lex=%s）" % (q, r.mode, r.detail.get("z_top"),
                                                      r.detail.get("lex_top")))
        check("反例全部回退全量（不猜）", neg_ok == len(NEG), "%d/%d" % (neg_ok, len(NEG)))

        # ------------------------------------------------------------ [5][6][7] 护栏
        print("\n[5] 预算护栏 / 未命中不隐身 / 示例过滤")
        q = HITS[0][0]
        r = tr.route_tools(acc, q)
        if r.mode == "routed":
            full = reg.agent_brief(acc)
            check("完整卡张数 ≤ MAX_FULL_CARDS", len(r.hits) <= tr.MAX_FULL_CARDS,
                  "%d ≤ %d" % (len(r.hits), tr.MAX_FULL_CARDS))
            check("注入量 ≤ 全量简报", len(r.brief) <= len(full), "%d ≤ %d" % (len(r.brief), len(full)))
            names = [t[0] for t in TOOLS]
            missing = [n for n in names if n not in r.brief]
            check("未命中工具仍有一行名称（不隐身）", not missing, missing)
            check("示例只保留命中工具的调用", all(('command="%s"' % h) in r.examples or not r.examples
                                                   for h in r.hits))
            other = [t[1] for t in TOOLS if t[1] not in r.hits]
            check("非命中工具的示例已被滤掉",
                  not any(('command="%s"' % o) in r.examples for o in other),
                  [o for o in other if ('command="%s"' % o) in r.examples])
            check("tokens_est 与实际注入量同量级",
                  abs(r.tokens_est - int((len(r.brief) + len(r.examples)) * 0.5)) <= 1)
        else:
            print("     （该问句回退了全量，护栏用例跳过）")

    # ------------------------------------------------------------------ [4] 兜底分支
    print("\n[4] 兜底（fail-safe：绝不让注入变空/变窄）")
    orig_scores = cp.route_scores
    try:
        cp.route_scores = lambda *a, **k: {}          # 模拟向量不可用
        # ① 无向量 + 有词法命中 → 仍可路由（词法那一路是精确信号，实测可靠）
        q_lex = HITS[0][0]
        r = tr.route_tools(acc, q_lex)
        check("向量不可用但词法命中 → 仍走路由（词法单路可用）",
              r.mode == "routed" and HITS[0][1] in r.hits, "%s | %s" % (r.mode, r.hits))
        # ② 无向量 + 无词法命中 → 回退全量（不猜）
        r = tr.route_tools(acc, "zzz 毫无线索的问题")
        check("向量不可用且无词法命中 → 回退全量", r.mode == "full", r.detail.get("reason"))
        check("回退时内容与 M5a 逐字一致", r.brief == reg.agent_brief(acc))

        def _boom(*a, **k):
            raise RuntimeError("池读取炸了")
        orig_entries = cp.pool_entries
        cp.pool_entries = _boom
        try:
            r = tr.route_tools(acc, "随便问问")
            check("池读取异常 → 回退全量且不抛出", r.mode == "full" and bool(r.brief),
                  r.detail.get("reason"))
        finally:
            cp.pool_entries = orig_entries
    finally:
        cp.route_scores = orig_scores

    r = tr.route_tools(acc, "")
    check("空问题 → 回退全量（没有问句就没有可检索的东西）", r.mode == "full", r.detail.get("reason"))

    # ------------------------------------------------------------------ [8] RRF 单元
    print("\n[8] 单元：RRF 必须剔除无信号的排序器")
    fake = [cp.CapabilityEntry(source="cli", ref="a", name="A", keywords="甲甲甲", abilities="关键词：甲甲甲"),
            cp.CapabilityEntry(source="cli", ref="b", name="B", keywords="乙乙乙", abilities="关键词：乙乙乙"),
            cp.CapabilityEntry(source="cli", ref="c", name="C", keywords="丙丙丙", abilities="关键词：丙丙丙")]
    # 向量：只有 b 有信号；词法：全 0（问句与任何关键词都不重叠）
    dec = tr.decide("zzz", fake, {"cli:a": 0.5, "cli:b": 0.7, "cli:c": 0.55})
    check("词法无信号时，向量 top1 保持第一（不被字母序顶掉）",
          dec["order"][0] == "cli:b", dec["order"])
    dec2 = tr.decide("甲甲甲", fake, {})          # 向量全 0 → 只用词法
    check("向量无信号时，词法命中者排第一", dec2["order"][0] == "cli:a", dec2["order"])
    check("两路都无信号 → 不置信（回退全量）",
          tr.decide("qqqq", fake, {})["confident"] is False)
    check("z 全相等（无可区分）→ 不置信", tr.decide("qqqq", fake,
          {"cli:a": 0.6, "cli:b": 0.6, "cli:c": 0.6})["confident"] is False)

    # ------------------------------------------------------------------ [9] 来源无关
    print("\n[9] 来源无关（api/mcp 条目用同一套路由逻辑）")
    mixed = list(ents) + [
        cp.CapabilityEntry(source="api", ref="money", name="汇率 API", keywords="汇率/美元/人民币/换汇",
                           abilities="关键词：汇率/美元/人民币。\n美元兑人民币→rate"),
        cp.CapabilityEntry(source="mcp", ref="github", name="GitHub MCP", keywords="仓库/分支/提交/PR",
                           abilities="关键词：仓库/分支/提交。\n列出分支→list branches"),
    ]
    if not SKIP_VEC:
        dense = cp.route_scores(acc, "美元兑人民币多少")
        # 真机上 api/mcp 条目由各自适配器同步向量；这里手工补上它的向量，
        # 才能公平检验「路由器不区分来源」（否则等价于要求它凭缺席的向量赢）
        dense = dict(dense); dense["api:money"] = max(dense.values() or [0.0]) + 0.2
        dec = tr.decide("美元兑人民币多少", mixed, dense)
        check("api 源条目能被排序到第一（来源无关）", dec["order"][0] == "api:money", dec["order"][:4])
        check("decide 对混合来源无差别处理（键含 source 前缀）",
              all(":" in k for k in dec["order"]), dec["order"][:3])
    r2 = tr.route_tools(acc, "列出仓库分支", entries=mixed)
    check("route_tools 接受外部条目（便于单测与后续来源接入）", r2.brief != "" and r2.mode in ("full", "routed"),
          r2.mode)

    # ------------------------------------------------------------------ [10] 接线
    print("\n[10] 接线：_run_agent 走 tool_router")
    src = open(os.path.join(ROOT, "api", "server.py"), encoding="utf-8").read()
    check("server.py 引入 tool_router", "from tools import tool_router" in src
          or "import tool_router" in src)
    check("server.py 调用 route_tools", "route_tools(" in src)
    check("server.py 不再直接拼 agent_brief（避免两套注入路径）",
          "cli_reg.agent_brief(" not in src)
    check("路由模式上报思考（可观测）", "工具路由" in src)

    # ------------------------------------------------------------------ [11] 清理
    print("\n[11] 清理")
    check("删除临时账号及其数据", _drop_account(USER))
    check("临时账号已不存在", _account_id(USER) == 0)

    return summary()


def summary():
    print("\n" + "=" * 60)
    print("M5b-2 工具检索路由 e2e：通过 %d，失败 %d" % (len(PASS), len(FAIL)))
    if FAIL:
        print("失败项：")
        for f in FAIL:
            print("  -", f)
        return 1
    print("全部通过 ✅")
    return 0


if __name__ == "__main__":
    sys.exit(main())
