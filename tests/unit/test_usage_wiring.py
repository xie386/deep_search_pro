# -*- coding: utf-8 -*-
"""M4-3 用例：计数器**挂到位**（静态接线 + 挂上去的对象真能计数）。

判据来源：M4 方案 §六 M4-3（"委派网络搜索助手跑 2 次 → 2 条 retrieval_external"、静态断言
3 个子 Agent 的 `spec["middleware"]` 非空、周报路径有 1 条）。

★ 关于那条功能反证：它要真起一个 deepagents 主图 + 调模型（还会联网烧 Tavily 额度）——
按额度纪律不在离线用例里跑，改由 **M4-9 真机抽样**完成（跑一轮会检索的会话，核对 usage_events）。
这里把它拆成两半，两边都钉住：① 接线是静态可断言的；② 挂上去的那个中间件对象**确实能计数**（用 M4-2 的口径跑）。

运行：.venv/Scripts/python.exe -m pytest tests/test_usage_wiring.py -q
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from agent import usage_counter as uc                                        # noqa: E402
from agent.subagents.db_agent import db_agent                                # noqa: E402
from agent.subagents.network_search_agent import network_search_agent        # noqa: E402
from agent.subagents.personal_agent import personal_agent                    # noqa: E402
from tools.schema_personal import ensure_tables, get_personal_conn, purge_account   # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ensure_tables()


def src(rel):
    with open(os.path.join(ROOT, rel), encoding="utf-8") as f:
        return f.read().replace("\r\n", "\n")


def mk_account():
    conn = get_personal_conn()
    try:
        aid = int(conn.execute("INSERT INTO accounts (username, password_hash, role) VALUES (?,?,?)",
                               ("m4w_%d" % (int(time.time() * 1000) % 1000000), "x", "user")).lastrowid)
        conn.commit()
    finally:
        conn.close()
    return aid


def count(aid):
    conn = get_personal_conn()
    try:
        return conn.execute("SELECT COUNT(*) FROM usage_events WHERE account_id=?", (aid,)).fetchone()[0]
    finally:
        conn.close()


# ---------------------------------------------------------------- ① 三个子 Agent 都挂了（C2）
def test_all_three_subagents_have_middleware():
    for spec, name in ((network_search_agent, "network_search"), (db_agent, "db"),
                       (personal_agent, "personal")):
        mw = spec.get("middleware")
        assert mw, "★ %s 子 Agent 没有 middleware —— 子图不吃主图的 middleware，会漏计" % name
        assert any(isinstance(x, uc.UsageCounterMiddleware) for x in mw), name


def test_subagent_middleware_object_actually_counts():
    """挂上去的那个对象真能计数（不是挂了个空壳）。"""
    aid = mk_account()
    uc.set_context_reader(lambda: (aid, "th-x"))
    try:
        mw = [x for x in network_search_agent["middleware"] if isinstance(x, uc.UsageCounterMiddleware)][0]

        class Req:
            tool_call = {"name": "tavily_search", "args": {}, "id": "1"}

        for _ in range(2):                       # = "委派网络搜索助手跑 2 次"
            mw.wrap_tool_call(Req(), lambda r: "ok")
        assert count(aid) == 2, "★ 跑 2 次检索 = 2 条（这正是'只挂主图会漏'的部分）"
    finally:
        uc.set_context_reader(None)
        purge_account(aid)


# ---------------------------------------------------------------- ② 主 Agent 两处 + 周报
def test_both_main_agent_assembly_points_wired():
    s = src("api/server.py")
    assert s.count("usage_counter.UsageCounterMiddleware()") == 2, \
        "★ 主 Agent 有两个装配点（全局 AGENT + 按账号自定义 Agent），两处都要挂"
    assert "usage_counter.set_context_reader" in s, "上下文读取器要在 api 层注册（依赖倒置）"


def test_digest_agent_wired():
    s = src("agent/digest_engine.py")
    assert "UsageCounterMiddleware()" in s, "周报自建 Agent 也会联网检索，必须挂（C3）"
    assert "create_deep_agent(" in s


def test_agent_layer_does_not_import_api():
    """依赖倒置：agent 层（计数器）不得 import api.*，上下文靠 api 侧注入。"""
    s = src("agent/usage_counter.py")
    import re as _re
    # 只看**代码行**（docstring 里那句"本模块不 import api.*"是说明文字，不是导入）
    assert not _re.search(r"^\s*(?:import api|from api)\b", s, _re.M), "agent 层不得 import api.*"


def test_kind_mapping_documented_for_wiring():
    """接线用的映射必须是"可扩展 + 有中文标签"的（界面要显示分类）。"""
    assert uc.KIND_LABELS[uc.KIND_RETRIEVAL_EXTERNAL] == "外部检索"
    assert hasattr(uc, "EXTRA_TOOL_KINDS")


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q"]))
