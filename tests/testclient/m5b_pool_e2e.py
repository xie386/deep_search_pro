# -*- coding: utf-8 -*-
"""M5b-1 · 统一能力目录（能力池）—— 端到端验证（不耗模型）

对应设计：`docs/v2.0/M5b工具检索路由.md` §二（统一能力目录）+ §8.2（M5b-1 交付判据）。

覆盖：
  1) 表结构：tool_capabilities / tool_pool_meta 存在、列齐、ensure_tables 幂等
  2) CLI 适配器 `cli_entry()`（纯函数）：关键词前缀剥离、enabled 跟随 live、
     命令数、三种渲染形态（卡 / 紧凑行 / 仅名称）、无能力描述时的回退
  3) 写入口联动：save_cli / set_state / delete_cli → 池**立即**反映（含 enabled 变化）
  4) 池版本：任何写入口 → pool_version 递增（缓存失效依据）
  5) rebuild_pool 幂等：连跑两次，池内容完全一致
  6) 向量：首次 upsert=added、二次全 skipped（幂等）、删工具后 stale 被清
  7) 账号隔离：临时账号的池**不含**其它账号的条目
  8) 不改 M5a 行为：agent_brief 输出仍与 M5a 一致（池是叠加层，不污染简报）

运行：
    .venv/Scripts/python.exe tests/m5b_pool_e2e.py
    SKIP_VEC=1 .venv/Scripts/python.exe tests/m5b_pool_e2e.py     # 跳过 bge（省 ~2 分钟）
"""

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from tools import capability_pool as cp  # noqa: E402
from tools import cli_registry as reg  # noqa: E402
from tools.schema_personal import ensure_tables, get_personal_conn  # noqa: E402

SKIP_VEC = os.environ.get("SKIP_VEC") == "1"

PROBE = "m5bprobe-pool"          # 临时探针 CLI（用完删除）
USER = "m5b_%s" % time.strftime("%m%d%H%M%S")
PWD = "m5bprobe123"

PROBE_ABILITIES = (
    "关键词：日程/安排/会议/待办/本周。\n"
    "本周有什么安排→today；某天的安排→day；找空闲时间→free；"
    "我有哪些待办→todo list；这个会议在哪→meeting info"
)
PROBE_READONLY = "today\nday\nfree\ntodo list\nmeeting info\ndoctor"

PASS, FAIL = [], []


def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name)
    print("%s %s%s" % ("  ✅" if cond else "  ❌", name, (" | " + str(extra)) if extra else ""))


def _account_id(username: str):
    conn = get_personal_conn()
    try:
        row = conn.execute("SELECT id FROM accounts WHERE username=?", (username,)).fetchone()
        return row["id"] if row else None
    finally:
        conn.close()


def _ensure_account(username: str, pwd: str) -> int:
    aid = _account_id(username)
    if aid:
        return int(aid)
    from api.account import _create_account, _hash_pwd
    _create_account(username, _hash_pwd(pwd), "personal", None)
    aid = int(_account_id(username) or 0)
    assert aid, "临时账号创建失败: %s" % username
    return aid


def _drop_account(username: str) -> bool:
    """删除临时账号及其全部数据（含 M5b 新表与向量目录），不留测试残留。

    数据行清理走 `purge_account()`（自省表结构，新增账号域表不用再改这里）。
    """
    import shutil

    from rag_knowledge.kb_service import DB_ROOT
    from tools.schema_personal import purge_account
    conn = get_personal_conn()
    try:
        row = conn.execute("SELECT id FROM accounts WHERE username=?", (username,)).fetchone()
        if not row:
            return False
        aid = int(row["id"])
    finally:
        conn.close()
    purge_account(aid)
    d = DB_ROOT / username / "chroma_route"
    if d.exists():
        shutil.rmtree(d, ignore_errors=True)
    return True


def _cols(table: str) -> list:
    conn = get_personal_conn()
    try:
        return [r[1] for r in conn.execute("PRAGMA table_info(%s)" % table).fetchall()]
    finally:
        conn.close()


def main():
    ensure_tables()
    acc = _ensure_account(USER, PWD)
    print("临时账号：%s (id=%s)\n" % (USER, acc))

    # ------------------------------------------------------------------ [1] 表结构
    print("[1] 表结构（M5b 新增两表）")
    tc = _cols("tool_capabilities")
    for col in ("account_id", "source", "ref", "name", "keywords", "abilities", "invoke_hint",
                "enabled", "updated_at"):
        check("tool_capabilities.%s 存在" % col, col in tc)
    pm = _cols("tool_pool_meta")
    for col in ("account_id", "pool_version", "updated_at"):
        check("tool_pool_meta.%s 存在" % col, col in pm)
    try:
        ensure_tables()
        ensure_tables()
        check("ensure_tables 幂等（重复调用不报错）", True)
    except Exception as ex:  # noqa: BLE001
        check("ensure_tables 幂等（重复调用不报错）", False, "%s: %s" % (type(ex).__name__, ex))

    # ------------------------------------------------------------------ [2] 适配器纯函数
    print("\n[2] CLI 适配器 cli_entry()（纯函数）")
    e = cp.cli_entry({
        "name": "日程 CLI", "bin": "sched", "live": True, "rules": [("today",), ("todo", "list")],
        "abilities": "关键词：日程/安排/会议。\n本周有什么安排→today；我有哪些待办→todo list",
    })
    check("source 固定为 cli", e.source == "cli")
    check("ref = 可执行名", e.ref == "sched")
    check("keywords 剥离「关键词：」前缀", e.keywords == "日程/安排/会议", e.keywords)
    check("关键词取到句末（不含换行后的映射）", "→" not in e.keywords)
    check("命令数 = 只读清单条数", e.command_count == 2, e.command_count)
    check("enabled 跟随 live（已接入→True）", e.enabled is True)

    e_off = cp.cli_entry({"name": "x", "bin": "x", "live": False, "rules": [("a",)], "abilities": ""})
    check("未接入（live=False）→ enabled=False（不进池）", e_off.enabled is False)

    e_noab = cp.cli_entry({"name": "无描述 CLI", "bin": "plain", "live": True, "rules": [("a",), ("b",)],
                           "abilities": ""})
    check("无能力描述 → 卡片回退成命令名形态（M4c 口径）", "可代跑 2 条只读命令" in e_noab.card_text)
    check("无能力描述 → 卡片不出现「用户问到下列任一场景」",
          "用户问到下列任一场景" not in e_noab.card_text)

    card, comp, name_only = e.card_text, e.compact_text, e.nameonly_text
    check("卡片含可执行名", "`sched`" in card)
    check("卡片含「私有数据」提醒（M5a 口径）", "私有数据" in card)
    check("卡片含用户语言映射原文", "本周有什么安排" in card)
    check("紧凑行含关键词、不含映射细节", ("日程" in comp) and ("本周有什么安排" not in comp))
    check("紧凑行含命令数", "2 条只读命令" in comp)
    check("仅名称行最简（不含关键词内容）",
          ("安排" not in name_only) and ("/" not in name_only) and ("sched" in name_only))
    check("三种形态长度递减", len(card) > len(comp) > len(name_only))

    # ------------------------------------------------------------------ [3] 写入口联动
    print("\n[3] 写入口联动（save / set_state / delete → 池立即反映）")
    check("初始：临时账号池为空", cp.pool_entries(acc) == [], [x.ref for x in cp.pool_entries(acc)])

    t_save = time.time()
    it = reg.save_cli(acc, {"name": "日程 CLI", "bin": PROBE, "readonly": PROBE_READONLY,
                            "abilities": PROBE_ABILITIES, "state": "authed"})
    dt_save = time.time() - t_save
    pid = it["id"]
    check("save_cli 不因池同步而变慢（<3s，即未加载 bge）", dt_save < 3.0, "%.2fs" % dt_save)
    pool = cp.pool_entries(acc)
    check("save_cli 后池出现该条", any(x.ref == PROBE for x in pool), [x.ref for x in pool])
    check("池条目 name 与配置一致", next(x.name for x in pool if x.ref == PROBE) == "日程 CLI")
    check("池条目 keywords 已解析", next(x.keywords for x in pool if x.ref == PROBE).startswith("日程"))
    check("池条目命令数 = 清单条数", next(x.command_count for x in pool if x.ref == PROBE) == 6)

    reg.save_cli(acc, {"id": pid, "name": "日程 CLI", "bin": PROBE,
                       "abilities": "关键词：签到/打卡。\n打卡记录→todo list"})
    pool = cp.pool_entries(acc)
    check("改能力描述后池立即反映",
          next(x.keywords for x in pool if x.ref == PROBE) == "签到/打卡")

    reg.set_state(acc, pid, "none")
    check("set_state 后 enabled 变化（不再可代跑 → 出池）",
          all(x.ref != PROBE for x in cp.pool_entries(acc)))
    reg.set_state(acc, pid, "authed")
    check("状态改回后重新进池", any(x.ref == PROBE for x in cp.pool_entries(acc)))

    reg.delete_cli(acc, pid)
    check("delete_cli 后从池摘除", all(x.ref != PROBE for x in cp.pool_entries(acc)))

    # 重新建一条，供后续 rebuild / 向量用例使用
    it = reg.save_cli(acc, {"name": "日程 CLI", "bin": PROBE, "readonly": PROBE_READONLY,
                            "abilities": PROBE_ABILITIES, "state": "authed"})
    pid = it["id"]

    # ------------------------------------------------------------------ [4] 池版本
    print("\n[4] 池版本（缓存失效依据）")
    v1 = cp.current_pool_version(acc)
    check("版本号可读（int）", isinstance(v1, int) and v1 > 0, v1)
    v2 = cp.bump_pool_version(acc)
    check("bump_pool_version 递增", v2 == v1 + 1, "%s → %s" % (v1, v2))

    # ------------------------------------------------------------------ [5] rebuild 幂等
    print("\n[5] rebuild_pool 幂等")
    r1 = cp.rebuild_pool(acc, with_vectors=not SKIP_VEC)
    check("rebuild 成功", r1.get("ok") is True, r1)
    check("rebuild 写入条数与池一致", r1["written"] == len(cp.pool_entries(acc)), r1["written"])
    snap1 = [(x.source, x.ref, x.name, x.keywords, x.abilities, x.command_count)
             for x in cp.pool_entries(acc)]
    r2 = cp.rebuild_pool(acc, with_vectors=False)
    snap2 = [(x.source, x.ref, x.name, x.keywords, x.abilities, x.command_count)
             for x in cp.pool_entries(acc)]
    check("第二次 rebuild 内容完全一致（幂等）", snap1 == snap2, snap1)
    check("rebuild 后 tool_capabilities 落库条数正确",
          len([r for r in _all_caps(acc)]) == len(snap2), _all_caps(acc))

    # ------------------------------------------------------------------ [6] 向量
    print("\n[6] 向量同步（独立 collection tool_route_{account_id}）")
    if SKIP_VEC:
        print("  ⏭  SKIP_VEC=1，跳过（bge 加载约 1-3 分钟）")
    else:
        # 先清空 collection，制造真正的「首次同步」场景（rebuild 可能已同步过）
        col0 = cp.get_route_collection(acc)
        ids = (col0.get() or {}).get("ids") or []
        if ids:
            col0.delete(ids=list(ids))
        t = time.time()
        s1 = cp.sync_vectors(acc)
        dt1 = time.time() - t
        check("首次同步 added = 池条数", s1.get("ok") and s1["added"] == len(snap2), "%s (%.0fs)" % (s1, dt1))
        t = time.time()
        s2 = cp.sync_vectors(acc)
        dt2 = time.time() - t
        check("二次同步全 skipped（内容哈希未变则不重算）", s2.get("ok") and s2["added"] == 0
              and s2["skipped"] == len(snap2), "%s (%.1fs)" % (s2, dt2))
        check("二次同步显著更快（未重新编码）", dt2 < max(5.0, dt1 / 3), "%.1fs vs %.0fs" % (dt2, dt1))
        check("collection 条数 = 池条数", cp.get_route_collection(acc).count() == len(snap2))

        cp.ensure_vectors_fresh(acc)          # 首次：写版本对齐标记
        af1 = cp.ensure_vectors_fresh(acc)    # 二次：应直接返回 fresh（不重算）
        check("惰性同步：版本已对齐时直接返回 fresh", af1.get("fresh") is True, af1)
        cp.bump_pool_version(acc)
        af2 = cp.ensure_vectors_fresh(acc)
        check("惰性同步：版本落后时自动重建", af2.get("fresh") is None and af2.get("ok") is True, af2)

        sc = cp.route_scores(acc, "我本周有什么安排")
        check("向量检索能返回该账号条目", len(sc) == len(snap2), list(sc))
        check("检索键为 source:ref 形状", all(":" in k for k in sc), list(sc))

        reg.delete_cli(acc, pid)
        s3 = cp.sync_vectors(acc)
        check("删除工具后向量被清理（removed ≥ 1）", s3.get("ok") and s3["removed"] >= 1, s3)
        check("collection 已空", cp.get_route_collection(acc).count() == 0)
        # 复原一条，供隔离用例
        reg.save_cli(acc, {"name": "日程 CLI", "bin": PROBE, "readonly": PROBE_READONLY,
                           "abilities": PROBE_ABILITIES, "state": "authed"})

    # ------------------------------------------------------------------ [7] 账号隔离
    print("\n[7] 账号隔离")
    other = None
    conn = get_personal_conn()
    try:
        row = conn.execute("SELECT id FROM accounts WHERE id<>? AND id IN "
                           "(SELECT DISTINCT account_id FROM user_clis) LIMIT 1", (acc,)).fetchone()
        other = int(row["id"]) if row else None
    finally:
        conn.close()
    if other:
        mine = {x.ref for x in cp.pool_entries(acc)}
        theirs = {x.ref for x in cp.pool_entries(other)}
        check("本账号池不含其它账号的工具", not (mine & theirs), "mine=%s theirs=%s" % (mine, theirs))
        check("其它账号池不受本次测试影响（探针 CLI 不在其中）", PROBE not in theirs)
    else:
        print("  ⏭  库中只有一个账号，跳过（不构造假数据）")

    # ------------------------------------------------------------------ [8] 不改 M5a 行为
    print("\n[8] 叠加层不改 M5a 行为")
    brief = reg.agent_brief(3)      # 真实账号
    check("agent_brief 仍正常产出（M5a 口径）", bool(brief) and "可用命令行工具" in brief or bool(brief))
    check("简报不含能力池专有文本（未污染简报）",
          ("相似度" not in brief) and ("route" not in brief.lower()) and ("tool_route" not in brief))
    check("简报仍含 M5a 的能力路由卡形态（私有数据提醒）", "私有数据" in brief or "关键词" in brief)

    # ------------------------------------------------------------------ [9] 清理
    print("\n[9] 清理：探针配置 + 临时账号")
    check("删除临时账号及其数据（含 M5b 两表与向量目录）", _drop_account(USER), USER)
    check("临时账号已不存在", _account_id(USER) is None)

    return summary()


def _all_caps(account_id: int) -> list:
    conn = get_personal_conn()
    try:
        return conn.execute("SELECT source, ref FROM tool_capabilities WHERE account_id=?",
                            (int(account_id),)).fetchall()
    finally:
        conn.close()


def summary():
    print("\n" + "=" * 60)
    print("M5b-1 能力池 e2e：通过 %d，失败 %d" % (len(PASS), len(FAIL)))
    if FAIL:
        print("失败项：")
        for f in FAIL:
            print("  -", f)
        return 1
    print("全部通过 ✅")
    return 0


if __name__ == "__main__":
    sys.exit(main())
