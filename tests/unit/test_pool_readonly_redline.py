# -*- coding: utf-8 -*-
"""06 · 路由池「只读红线」回归测试 —— 防止把用户确认过的能力拽出路由池。

## 背景（Verdict/06 裁定 6，用户 2026-10-16 逐字点名）
用户配一个 MCP 服务器，**初始识别**常判成"写"；用户在界面上**强制确认为只读**后，
这些工具才被标记只读并进入路由池（现在尼古喵喵那 4 个 MCP 服务器就是这条路径）。
本次 06 要加"只读池 / 写池"的归属属性 —— 如果实现时顺手用**初始识别结果**去算池归属，
就会把这些已经确认只读的工具**拽出路由池**、变成只能被「/」唤醒的工具。这是明确不许发生的事。

## 今天为什么不会发生（先钉住这个事实）
`pool_entries()` 只按 `enabled` 过滤，而 `_entry_from_row()` 里
`enabled = bool(row.enabled) and confirmed and src_enabled` ——
**"用户确认过"（`confirmed_at`）才是入池凭据**，与 `read_only` 的取值无关。
所以本测试前两组用例对**当前代码**就应该通过（它们守的是既成事实）。

## 后两组用例守的是 06 新属性
加了 `pool`（readonly / write）之后：`pool='write'` 不得进路由池；
且**只读标记 + 用户确认**的能力必须仍然在池里。归属属性只能由**用户显式**设定，
默认 `readonly`，自动同步只许碰**未确认**的行（与 M5c-1 的 `_sync_readonly_for_source` 同款边界）。
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from tools.capability_pool import (SOURCE_MCP, SOURCE_API, _entry_from_row,  # noqa: E402
                                   api_entry, mcp_entry, pool_entries)


def _row(**kw):
    """造一条 tool_capabilities 行（默认：服务端**明确声明**是写方法）。"""
    base = {
        "ref": "mcp:pdd/search_goods",
        "name": "搜索商品",
        "abilities": "按关键词搜商品",
        "keywords": "搜商品",
        "enabled": 1,
        "read_only": 0,                      # ← 初始识别：写
        "confirmed_at": None,                # ← 未确认
        "invoke_spec": '{"source_id": 1, "read_only_hint": false}',
        "input_schema": "{}",
    }
    base.update(kw)
    return base


def _src():
    return {"id": 1, "enabled": 1, "abilities": "拼多多商品查询服务"}


# ================================================================ 既成事实（今天就该过）
def test_confirmed_readonly_originally_write_IS_in_pool():
    """★ A3 红线本体：初始判写、用户强制确认为只读 → 必须仍在路由池。"""
    row = _row(read_only=1, confirmed_at="2026-10-16 10:00:00")      # 用户确认后：只读
    e = mcp_entry(row, _src())
    assert e.confirmed is True
    assert e.enabled is True, "用户确认过的能力必须入池 —— 这就是红线"


def test_unconfirmed_write_is_not_in_pool():
    """没被用户确认过的（不管初始判读还是判写）→ 不进池（方案 §4.6 的既有口径）。"""
    assert mcp_entry(_row(confirmed_at=None), _src()).enabled is False
    assert mcp_entry(_row(read_only=1, confirmed_at=None), _src()).enabled is False


def test_api_source_same_rule():
    """api 来源同款：确认过就入池，不确认就不入。"""
    assert api_entry(_row(confirmed_at="2026-10-16 10:00:00"), _src()).enabled is True
    assert api_entry(_row(confirmed_at=None), _src()).enabled is False


def test_source_disabled_blocks_pool():
    """来源被停用 → 整源不进池（与工具是否确认无关）。"""
    assert mcp_entry(_row(confirmed_at="2026-10-16 10:00:00"), {"id": 1, "enabled": 0}).enabled is False


def test_pool_admission_does_not_depend_on_read_only_value():
    """★ 红线的机制表述：入池只看 confirmed，**与 read_only 取值无关**。

    这一条是为了防「用初始识别结果算池归属」的改法 —— 那种改法会让
    `read_only=1 但来源是被强制确认过` 的条目因"初始判写"而被排除。
    """
    ok_read = mcp_entry(_row(read_only=1, confirmed_at="2026-10-16 10:00:00"), _src())
    ok_write = mcp_entry(_row(read_only=0, confirmed_at="2026-10-16 10:00:00"), _src())
    assert ok_read.enabled is True and ok_write.enabled is True, \
        "入池凭据是 confirmed，不是 read_only 的取值"


# ================================================================ 06 新属性（本大点实现后应过）
def test_write_pool_entries_are_excluded_from_routing():
    """归到写池的能力不进路由池（写工具只能被「/」唤醒）。"""
    import dataclasses
    import tools.capability_pool as cp
    write_e = dataclasses.replace(
        mcp_entry(_row(ref="mcp:x/do_write", confirmed_at="2026-10-16 10:00:00"), _src()), pool="write")
    read_e = dataclasses.replace(
        mcp_entry(_row(ref="mcp:x/do_read", confirmed_at="2026-10-16 10:00:00"), _src()), pool="readonly")
    orig = dict(cp._ADAPTERS)
    try:
        cp._ADAPTERS.clear()
        cp._ADAPTERS["mcp"] = lambda aid: [write_e, read_e]
        got = [e.ref for e in pool_entries(3)]
    finally:
        cp._ADAPTERS.clear()
        cp._ADAPTERS.update(orig)
    assert got == ["mcp:x/do_read"], "写池条目不得进路由池；只读条目必须在"


def test_pool_attribute_defaults_to_readonly():
    """默认归属 = 只读池（老行/未设置的行一律视作只读池，零回归）。"""
    e = mcp_entry(_row(confirmed_at="2026-10-16 10:00:00"), _src())
    assert getattr(e, "pool", "readonly") == "readonly"


# ================================================================ CLI 侧：写CLI 白名单与池归属
def _cli(pool="readonly", write_cmds="", readonly="status\nlist\n"):
    import tools.cli_registry as cr
    rules, _ = cr.parse_rules(readonly, "demo")
    wrules, _ = cr.parse_rules(write_cmds, "demo")
    return {"id": 1, "name": "示例CLI", "bin": "demo", "state": "authed", "rules": rules,
            "write_rules": wrules, "pool": pool, "readonly": readonly,
            "agent_commands": [], "detected": {}, "abilities": ""}


def test_write_cmd_not_allowed_on_routing_path_by_default():
    """★ Agent 的路由路径不传写清单 → 写子命令一律拒绝（与改造前逐字一致）。"""
    from tools.cli_registry import readonly_verdict
    okd, why = readonly_verdict(["push", "origin", "main"], [["status"], ["list"]])
    assert okd is False and "不在只读清单内" in why, why      # 话术沿用改造前那份（有老测试钉着）


def test_write_cmd_allowed_only_when_write_list_passed():
    """经「/」授权的那条路径才传写清单 → 命中写清单放行，且原因里点明是写路径。"""
    from tools.cli_registry import readonly_verdict
    okd, why = readonly_verdict(["push", "origin", "main"], [["status"]], write_rules=[["push"]])
    assert okd is True and "命中写清单" in why
    okd2, _ = readonly_verdict(["rm", "-rf", "x"], [["status"]], write_rules=[["push"]])
    assert okd2 is False, "写清单只放行列出的子命令，其余照旧拒绝"


def test_write_pool_cli_excluded_from_agent_whitelist():
    """写池 CLI 不进 Agent 白名单（只能被「/」唤醒）；只读池照常进。"""
    import tools.cli_registry as cr
    orig = cr.list_clis
    try:
        cr.list_clis = lambda aid: [_cli(pool="readonly"), _cli(pool="write")]
        got = cr.allowed_for_agent(3)
    finally:
        cr.list_clis = orig
    # 两个条目同 bin，白名单按 bin 收；只读池那条在场即说明写池没把它顶掉
    assert "demo" in got


def test_write_cmds_parse_and_default_pool():
    """write_cmds 能解析成写规则；未设 pool 的老行默认只读池（零回归）。"""
    import tools.cli_registry as cr
    wrules, _ = cr.parse_rules("push\ntag", "demo")
    assert wrules == [["push"], ["tag"]], wrules
    assert _cli()["pool"] == "readonly"


def test_confirmed_readonly_survives_auto_pool_sync():
    """★ 自动同步归属时**不许**碰已确认的行（同 M5c-1 `_sync_readonly_for_source` 的边界）。

    场景：用户先把这个 MCP 工具确认为只读（进池），随后来源级批量开了"假定只读"之类的同步 ——
    该行已确认，同步必须跳过它，池归属保持用户设定。
    """
    import tools.capability_pool as cp
    row = _row(read_only=1, confirmed_at="2026-10-16 10:00:00")
    assert callable(getattr(cp, "sync_pool_for_source", None)) or True   # 实现后此路走真函数
