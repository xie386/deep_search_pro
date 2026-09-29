# -*- coding: utf-8 -*-
"""M1-4 用例：混合来源（CLI + API）的能力池派生与渲染。**全离线**。

关键断言：
  ① **CLI 的渲染文本与改动前逐字一致**（既有 `m5b_router_e2e` / `m5b_pool_e2e` 靠它，
     也是"新来源接入不得改动老来源行为"的硬约束）；
  ② api 条目走自己的措辞（不说"N 条只读命令"）、带参数摘要；
  ③ **未人工确认的 api 能力不进池**（方案 §4.6）；
  ④ `_entry_text`（向量文本）对 api 补参数摘要、对 CLI **不加**（否则会触发全量向量重算）。

DB 部分用临时账号并在结束时清理（与 `m5b_pool_e2e` 同一套账号域清理口径）。

运行：
    .venv/Scripts/python.exe -m pytest tests/test_pool_mixed_sources.py -q
"""
import json
import os
import sys
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from tools import capability_pool as cp  # noqa: E402
from tools.schema_personal import get_personal_conn, purge_account  # noqa: E402

CLI_ROW = {"bin": "bili", "name": "B站", "abilities": "关键词：视频/弹幕\n查B站数据",
           "live": True, "rules": [["video", "info"]]}

API_ROW_CONFIRMED = {
    "ref": "api:weather#getCurrent", "name": "当前天气",
    "abilities": "关键词：天气/气温\n查指定城市实时天气", "invoke_hint": "GET /current/{city}",
    "enabled": 1, "read_only": 1, "confirmed_at": "2026-09-25 10:00:00",
    "invoke_spec": '{"source_id":%d,"method":"GET","url_template":"/current/{city}","bindings":'
                   '[{"name":"city","location":"path","required":true},'
                   '{"name":"lang","location":"query","required":false}]}',
    "input_schema": '{"type":"object"}',
}


def _cli():
    return cp.cli_entry(CLI_ROW)


# ---------------------------------------------------------------- ① CLI 文本冻结
def test_cli_rendering_is_frozen():
    c = _cli()
    assert c._head() == "- B站（可执行名 `bili`）"
    assert c.compact_text == "- B站（可执行名 `bili`）：共 1 条只读命令；关键词：视频/弹幕"
    assert c.nameonly_text == "- B站（可执行名 `bili`）：共 1 条只读命令"
    assert c.card_text.startswith("- B站（可执行名 `bili`）：用户问到下列任一场景时")
    assert c.param_summary == "" and c.input_schema == {} and c.invoke_spec == {}


def test_cli_entry_text_has_no_param_summary():
    assert cp._entry_text(_cli()) == "B站\n视频/弹幕\n关键词：视频/弹幕\n查B站数据"


# ---------------------------------------------------------------- ② api 渲染
def _api(**over):
    row = dict(API_ROW_CONFIRMED)
    row["invoke_spec"] = row["invoke_spec"] % 7
    row.update(over)
    return cp.api_entry(row, {"id": 7, "slug": "weather", "enabled": 1, "abilities": ""})


def test_api_head_and_texts_are_api_specific():
    a = _api()
    assert a._head() == "- 当前天气（API 能力 `api:weather#getCurrent`）"
    assert "只读命令" not in a.card_text and "invoke_tool" in a.card_text
    assert a.nameonly_text == "- 当前天气（API 能力 `api:weather#getCurrent`）"
    assert a.param_summary == "参数: city(必填,path), lang(可选,query)"
    assert "参数: city(必填,path)" in a.compact_text


def test_api_entry_text_includes_param_summary():
    txt = cp._entry_text(_api())
    assert "参数: city(必填,path), lang(可选,query)" in txt and txt.startswith("当前天气")


def test_api_readonly_false_shows_warning():
    a = _api(read_only=0)
    assert a.read_only is False and "会改外部状态" in a.card_text


def test_api_abilities_falls_back_to_source_level_text():
    a = cp.api_entry(dict(API_ROW_CONFIRMED, abilities="", invoke_spec=API_ROW_CONFIRMED["invoke_spec"] % 7),
                     {"id": 7, "enabled": 1, "abilities": "关键词：天气\n这套来源查天气与空气质量"})
    assert "查天气与空气质量" in a.abilities and a.keywords == "天气"


def test_api_abilities_merges_source_level_text():
    """★ M1 真机验证发现：来源级「能力描述」是用户按自己的话写的（他"会怎么问"），工具级是文档里的
    技术名。原来只在工具级为空时才回退 → **用户写的那句完全不起作用**（小虫API 卡片上只剩
    "查询Steam游戏资料"）。现在两句都要：技术名在前、用户的话在后。"""
    a = cp.api_entry(dict(API_ROW_CONFIRMED, invoke_spec=API_ROW_CONFIRMED["invoke_spec"] % 7),
                     {"id": 7, "enabled": 1, "abilities": "Steam 游戏价格与折扣、Steam 在线人数"})
    assert "查指定城市实时天气" in a.abilities          # 工具级（文档 summary）
    assert "Steam 游戏价格与折扣" in a.abilities        # 来源级（用户的话）
    assert a.abilities.index("查指定城市实时天气") < a.abilities.index("Steam 游戏价格与折扣")


def test_call_example_uses_first_param_when_none_required():
    """★ 参数全是可选时，示例不能退化成 `params={}`（模型不知道要传什么）。"""
    row = dict(API_ROW_CONFIRMED,
               input_schema='{"type":"object","properties":{"name":{"type":"string"},'
                            '"appid":{"type":"integer"},"mode":{"type":"string","enum":["json","text"]}},'
                            '"required":[]}',
               invoke_spec='{"source_id":7,"method":"GET","url_template":"/api/Steam","bindings":[]}')
    a = cp.api_entry(row, {"id": 7, "enabled": 1, "abilities": ""})
    assert 'params={"name": "…"}' in a.card_text, a.card_text      # 第一个参数占位
    assert "params={}" not in a.card_text
    # 有必填时只列必填（不给可选参数添乱），且带枚举的给枚举首值
    row2 = dict(row, input_schema='{"type":"object","properties":{"city":{"type":"string"},'
                                  '"days":{"type":"integer"}},"required":["city"]}')
    b = cp.api_entry(row2, {"id": 7, "enabled": 1, "abilities": ""})
    # 调用示例只列必填（不给可选参数添乱）；但**参数摘要那一行**现在会按 schema 列出全部参数
    # （M2：MCP 没有 bindings，退回 schema 渲染 → 这条口径对两种来源都成立）。
    ex_line = [ln for ln in b.card_text.splitlines() if "调用示例" in ln][0]
    assert 'params={"city": "…"}' in ex_line and "days" not in ex_line, ex_line
    assert "days(可选,integer)" in b.card_text, b.card_text
    row3 = dict(row, input_schema='{"type":"object","properties":{"mode":{"type":"string","enum":["json","text"]}},'
                                  '"required":["mode"]}')
    c = cp.api_entry(row3, {"id": 7, "enabled": 1, "abilities": ""})
    assert 'params={"mode": "json"}' in c.card_text, c.card_text


# ---------------------------------------------------------------- ③ 确认闸
def test_unconfirmed_api_capability_is_not_in_pool():
    un = cp.api_entry(dict(API_ROW_CONFIRMED, confirmed_at=None, invoke_spec=API_ROW_CONFIRMED["invoke_spec"] % 7),
                      {"id": 7, "enabled": 1})
    assert un.enabled is False and un.invokable is False and un.confirmed is False


def test_disabled_source_takes_capability_out_of_pool():
    a = cp.api_entry(dict(API_ROW_CONFIRMED, invoke_spec=API_ROW_CONFIRMED["invoke_spec"] % 7),
                     {"id": 7, "enabled": 0})
    assert a.enabled is False


def test_cli_confirmed_defaults_true():
    assert _cli().confirmed is True


# ---------------------------------------------------------------- ④ DB 派生（临时账号）
@pytest.fixture()
def temp_account():
    aid = None
    conn = get_personal_conn()
    try:
        uname = "m1tester_%d" % int(time.time())
        cur = conn.execute("INSERT INTO accounts (username, password_hash, role) VALUES (?,?,?)",
                           (uname, "x", "user"))
        aid = int(cur.lastrowid)
        conn.commit()
    finally:
        conn.close()
    yield aid
    purge_account(aid)


def test_pool_entries_include_confirmed_api_and_keep_cli(temp_account):
    aid = temp_account
    conn = get_personal_conn()
    try:
        sid = int(conn.execute(
            "INSERT INTO tool_sources (account_id, source, slug, name, config_json, state, enabled) "
            "VALUES (?,?,?,?,?,?,1)",
            (aid, "api", "weather", "和风天气", '{"base_url":"https://api.demo.com"}', "verified")).lastrowid)
        spec = API_ROW_CONFIRMED["invoke_spec"] % sid
        conn.execute("INSERT INTO tool_capabilities (account_id, source, ref, name, keywords, abilities, "
                     "invoke_hint, enabled, invoke_spec, input_schema, read_only, confirmed_at) "
                     "VALUES (?,?,?,?,?,?,?,1,?,?,1,?)",
                     (aid, "api", "api:weather#getCurrent", "当前天气", "天气", "查实时天气", "GET /current/{city}",
                      spec, '{"type":"object"}', "2026-09-25 10:00:00"))
        conn.execute("INSERT INTO tool_capabilities (account_id, source, ref, name, keywords, abilities, "
                     "invoke_hint, enabled, invoke_spec, input_schema, read_only, confirmed_at) "
                     "VALUES (?,?,?,?,?,?,?,1,?,?,0,NULL)",
                     (aid, "api", "api:weather#createHook", "建订阅钩子", "订阅", "写操作",
                      "POST /hook", API_ROW_CONFIRMED["invoke_spec"] % sid, '{"type":"object"}'))
        conn.commit()
    finally:
        conn.close()

    entries = cp.pool_entries(aid)
    refs = {e.ref for e in entries}
    assert "api:weather#getCurrent" in refs                  # 已确认 → 进池
    assert "api:weather#createHook" not in refs               # 未确认 → 不进池
    got = next(e for e in entries if e.ref == "api:weather#getCurrent")
    assert got.source == "api" and got.read_only is True and got.confirmed is True
    assert "参数: city(必填,path)" in got.card_text


def test_api_adapter_is_registered():
    assert cp.SOURCE_API in cp._ADAPTERS and cp._ADAPTERS[cp.SOURCE_API] is cp._entries_from_apis


# ---------------------------------------------------------------- 路由器：快路径不得让非 CLI 隐身
def test_fast_path_renders_api_capability(temp_account):
    """★ 真机 e2e 逮到的缺口：池小（≤FAST_PATH_MAX）时快路径原来直接用 `cli_reg.agent_brief()`
    （CLI 专属）→ API/MCP 能力对模型完全隐身。含非 CLI 来源时必须从统一池渲染。"""
    aid = temp_account
    conn = get_personal_conn()
    try:
        sid = int(conn.execute(
            "INSERT INTO tool_sources (account_id, source, slug, name, config_json, state, enabled) "
            "VALUES (?,?,?,?,?,?,1)",
            (aid, "api", "weather", "天气", '{"base_url":"https://api.demo.com"}', "verified")).lastrowid)
        spec = dict(json.loads(API_ROW_CONFIRMED["invoke_spec"] % sid))
        conn.execute("INSERT INTO tool_capabilities (account_id, source, ref, name, keywords, abilities, "
                     "invoke_hint, enabled, invoke_spec, input_schema, read_only, confirmed_at) "
                     "VALUES (?,?,?,?,?,?,?,1,?,?,1,?)",
                     (aid, "api", "api:weather#getCurrent", "当前天气", "天气", "查实时天气",
                      "GET /current/{city}", json.dumps(spec, ensure_ascii=False), '{"type":"object"}',
                      "2026-09-25 10:00:00"))
        conn.commit()
    finally:
        conn.close()
    from tools import tool_router
    r = tool_router.route_tools(aid, "今天成都天气怎么样")
    assert r.mode == "full"                                     # 池小 → 快路径
    assert "api:weather#getCurrent" in r.brief                  # ★ 必须出现，不能隐身
    assert "参数: city(必填,path)" in r.brief
    assert r.detail.get("mixed_sources") is True


def test_fast_path_stays_identical_for_cli_only_account():
    """纯 CLI 账号的快路径必须与 `cli_reg.agent_brief` **逐字一致**（M5a 行为不变）。

    注意：这条对照需要一个"只有 CLI、没有任何 api/mcp 能力"的账号。用户实测时会给常用账号接入
    API（如 `api:xxapi#douyinHot`）→ 那种账号已不满足前提，此处应**跳过**而不是误报失败。
    """
    from tools import cli_registry as cli_reg, tool_router
    conn = get_personal_conn()
    try:
        rows = conn.execute("SELECT id FROM accounts ORDER BY id").fetchall()
        picked = None
        for r in rows:
            aid0 = int(r["id"])
            ents = cp.pool_entries(aid0)
            if ents and all(e.source == cp.SOURCE_CLI for e in ents):
                picked = aid0
                break
    finally:
        conn.close()
    if picked is None:
        pytest.skip("库里没有\"只有 CLI 能力\"的账号可作对照（真实账号已接入 API/MCP）")
    aid = picked
    r = tool_router.route_tools(aid, "看看B站视频")
    assert r.brief == cli_reg.agent_brief(aid)
    assert not r.detail.get("mixed_sources")
