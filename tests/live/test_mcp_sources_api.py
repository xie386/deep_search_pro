# -*- coding: utf-8 -*-
"""M2-4 用例：MCP 来源配置面（`api/tools_sources.py` + 池 + 调用网关）。**全离线**（本地 stdio 小 server）。

验收口径 = 方案 §M2-4「复用 M1 已跑通的五条链路」：
  ① 候选落库（未确认不入池）② 确认闸（写操作必须显式勾选）③ 入池（混合池里出现 MCP 卡）
  ④ 审计（同一份 commands.jsonl，source=mcp）⑤ 只读放行（未确认/未启用一律拒）
另外覆盖：配置校验拒绝、体检七步、列表投影（env 只回键名）、重复发现保留人工决定、删除连带清理、账号隔离。

运行：
    .venv/Scripts/python.exe -m pytest tests/test_mcp_sources_api.py -q
"""
import json
import os
import sys
import textwrap
import time

import pytest
from fastapi import HTTPException

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from api import tools_sources as tsp  # noqa: E402
from tools import capability_invoke as ci  # noqa: E402
from tools import capability_pool as cp  # noqa: E402
from tools.schema_personal import get_personal_conn, purge_account  # noqa: E402

SLUG = "mcpsrv"
SERVER_CODE = textwrap.dedent('''
    from mcp.server import MCPServer
    srv = MCPServer("m2-demo")

    @srv.tool(description="查天气（只读）", annotations={"readOnlyHint": True})
    def get_weather(city: str) -> dict:
        return {"city": city, "temp": 21}

    @srv.tool(description="下单（写；未声明只读）")
    def place_order(sku: str, qty: int = 1) -> dict:
        return {"ok": True, "sku": sku, "qty": qty}

    if __name__ == "__main__":
        srv.run()
''')


@pytest.fixture(scope="module")
def srv_path(tmp_path_factory):
    p = tmp_path_factory.mktemp("m2mcp") / "server.py"
    p.write_text(SERVER_CODE, encoding="utf-8")
    return str(p)


def _mk_account(tag):
    conn = get_personal_conn()
    try:
        aid = int(conn.execute("INSERT INTO accounts (username, password_hash, role) VALUES (?,?,?)",
                               ("m2src_%s_%d" % (tag, int(time.time() * 1000) % 100000), "x", "user")).lastrowid)
        token = "tok_%d_%d" % (aid, int(time.time() * 1000) % 100000)
        conn.execute("INSERT INTO sessions (token, account_id, login_at) VALUES (?,?,?)", (token, aid, time.time()))
        conn.commit()
    finally:
        conn.close()
    return aid, token


def save_req(srv_path, slug=SLUG, **over):
    """保存一只 stdio MCP 来源（用 venv 的 python 跑本地脚本 server）。"""
    req = tsp.ApiSourceReq(slug=slug, name="本地演示 MCP", source="mcp", transport="stdio",
                           command=sys.executable, args_json=json.dumps([srv_path]),
                           timeout=60, abilities="查询天气与下单（本地演示）",
                           allow_stdio_commands=os.path.basename(sys.executable))
    for k, v in over.items():
        setattr(req, k, v)
    return req


@pytest.fixture(scope="module")
def env(srv_path):
    """一条保存好并已体检通过的 MCP 来源 + 已发现候选（全程只做一次，省时间）。"""
    aid, token = _mk_account("main")
    out = tsp.source_save(save_req(srv_path), token)
    sid = int(out["id"])
    probe = tsp.source_test(sid, token)
    disc = tsp.discover(tsp.DiscoverReq(slug=SLUG, source="mcp"), token)
    yield {"aid": aid, "token": token, "sid": sid, "probe": probe, "disc": disc}
    purge_account(aid)


# ---------------------------------------------------------------- ① 保存与配置校验
def test_config_validation_rejects_bad_mcp_sources(srv_path):
    aid, token = _mk_account("bad")
    try:
        with pytest.raises(HTTPException):        # transport 缺失/不合法
            tsp.source_save(save_req(srv_path, transport="sse"), token)
        with pytest.raises(HTTPException):        # args 不是字符串数组
            tsp.source_save(save_req(srv_path, args_json='{"a":1}'), token)
        # ★ Windows 路径的反斜杠在 JSON 里是非法转义（用户实测踩过）→ 提示必须能看懂，不能只说"必须是数组"
        with pytest.raises(HTTPException) as ei:
            tsp.source_save(save_req(srv_path, args_json='["D:\\LLM\\bnf\\server.py"]'), token)
        assert "正斜杠" in str(ei.value.detail), ei.value.detail
        with pytest.raises(HTTPException):        # 命令不在白名单
            tsp.source_save(save_req(srv_path, command="evil-binary",
                                     allow_stdio_commands="npx"), token)
        with pytest.raises(HTTPException):        # http 来源 url 不合法
            tsp.source_save(tsp.ApiSourceReq(slug="h1", source="mcp", transport="http", url="ftp://x"), token)
        with pytest.raises(HTTPException):        # 来源类型只能是 api/mcp
            tsp.source_save(tsp.ApiSourceReq(slug="h2", source="ftp", transport="http",
                                             url="http://x"), token)
        # 合法的一条能存进去，且 list 里按来源投影（env 只回键名、不回值）
        req = save_req(srv_path, slug="okmcp", env_json='{"MY_TOKEN":"secret-value"}')
        sid = int(tsp.source_save(req, token)["id"])
        items = tsp.sources_list(token)["items"]
        mine = [s for s in items if int(s["id"]) == sid][0]
        assert mine["source"] == "mcp" and mine["config"]["transport"] == "stdio"
        assert mine["config"]["env_keys"] == ["MY_TOKEN"]
        assert "secret-value" not in json.dumps(mine, ensure_ascii=False)
        assert mine["config_json"] == ""           # 原始 JSON 不回前端
    finally:
        purge_account(aid)


# ---------------------------------------------------------------- ② 体检七步
def test_probe_reports_seven_steps(env):
    p = env["probe"]
    assert p["ok"] is True, p["steps"]
    assert len(p["steps"]) == 7 and all(s["ok"] for s in p["steps"])
    assert p["tool_count"] == 2
    conn = get_personal_conn()
    try:
        state = conn.execute("SELECT state FROM tool_sources WHERE id=?", (env["sid"],)).fetchone()
    finally:
        conn.close()
    assert dict(state)["state"] == "verified"


# ---------------------------------------------------------------- ③ 发现 → 候选（不入池）
def test_discover_creates_candidates_but_not_pooled(env):
    d = env["disc"]
    assert d["ok"] is True, d.get("error")
    refs = sorted(i["ref"] for i in d["items"])
    assert refs == ["mcp:%s/get_weather" % SLUG, "mcp:%s/place_order" % SLUG], refs
    by = {i["ref"]: i for i in d["items"]}
    assert by["mcp:%s/get_weather" % SLUG]["is_write"] is False
    assert by["mcp:%s/get_weather" % SLUG]["read_only_hint"] is True
    assert by["mcp:%s/place_order" % SLUG]["is_write"] is True          # 未声明只读 → 按写处理
    assert by["mcp:%s/place_order" % SLUG]["read_only_hint"] is None
    assert by["mcp:%s/get_weather" % SLUG]["tool"] == "get_weather"
    assert by["mcp:%s/get_weather" % SLUG]["param_summary"]             # 参数摘要来自 inputSchema
    assert cp.pool_entries(env["aid"]) == []                            # ★ 未确认 → 一条都不进池


def test_candidates_list_shape_matches_discover(env):
    a = tsp.candidates_list(env["sid"], env["token"])
    assert a["source"] == "mcp" and a["slug"] == SLUG
    keys_d = set(env["disc"]["items"][0])
    assert set(a["items"][0]) == keys_d, (set(a["items"][0]) ^ keys_d)


# ---------------------------------------------------------------- ④ 确认闸
def test_write_tool_requires_explicit_approval(env):
    out = tsp.confirm(tsp.ConfirmReq(slug=SLUG, source="mcp", items=[
        tsp.CandidateIn(ref="mcp:%s/place_order" % SLUG)]), env["token"])
    assert out["confirmed"] == [] and "需显式勾选" in out["skipped"][0]["why"]
    assert cp.pool_entries(env["aid"]) == []

    out = tsp.confirm(tsp.ConfirmReq(slug=SLUG, source="mcp", items=[
        tsp.CandidateIn(ref="mcp:%s/place_order" % SLUG, approve_write=True),
        tsp.CandidateIn(ref="mcp:%s/get_weather" % SLUG, name="本地天气")]), env["token"])
    assert sorted(out["confirmed"]) == ["mcp:%s/get_weather" % SLUG, "mcp:%s/place_order" % SLUG]


def test_confirmed_mcp_enters_mixed_pool(env):
    entries = cp.pool_entries(env["aid"])
    refs = sorted(e.ref for e in entries)
    assert refs == ["mcp:%s/get_weather" % SLUG, "mcp:%s/place_order" % SLUG], refs
    e = [x for x in entries if x.ref.endswith("get_weather")][0]
    assert e.source == "mcp" and e.enabled is True and e.confirmed is True
    assert "MCP 能力" in e.card_text and "invoke_tool(ref=" in e.card_text
    assert "本地天气" in e.name                                  # 人工改的名字生效
    assert "查询天气与下单" in e.card_text                        # 来源级"能力描述"也拼进卡片
    w = [x for x in entries if x.ref.endswith("place_order")][0]
    assert w.read_only is False and "会改外部状态" in w.card_text


# ---------------------------------------------------------------- ⑤ 真实调用（走驱动 + 审计）
def test_invoke_mcp_tool_end_to_end(env):
    r = ci.invoke("mcp:%s/get_weather" % SLUG, {"city": "成都"}, account_id=env["aid"])
    assert r["ok"] is True, r
    assert "成都" in r["text"] and "21" in r["text"]
    assert r["source"] == "mcp" and r["ref"].endswith("get_weather")
    # 参数校验仍然生效（必填缺失 → 拒绝，且不真的去连）
    bad = ci.invoke("mcp:%s/get_weather" % SLUG, {}, account_id=env["aid"])
    assert bad["ok"] is False and "缺少必填参数" in bad["rejected"]


def test_invoke_unconfirmed_mcp_is_rejected(srv_path):
    """未确认的候选（哪怕来源体检通过）也不能被调用 —— M1 的只读放行链路对 mcp 同样生效。"""
    aid, token = _mk_account("gate")
    try:
        sid = int(tsp.source_save(save_req(srv_path, slug="gatemcp"), token)["id"])
        tsp.discover(tsp.DiscoverReq(slug="gatemcp", source="mcp"), token)
        r = ci.invoke("mcp:gatemcp/get_weather", {"city": "成都"}, account_id=aid)
        assert r["ok"] is False and ("未启用" in r["rejected"] or "确认" in r["rejected"])
    finally:
        purge_account(aid)


def test_audit_jsonl_records_mcp_source(env):
    """审计：与 CLI 同一份 `data/audit/commands.jsonl`（只是 source 变了）。"""
    p = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "data", "audit", "commands.jsonl")
    if not os.path.exists(p):
        pytest.skip("审计文件不存在（本次环境未写审计）")
    with open(p, encoding="utf-8") as f:
        lines = [ln for ln in f.read().splitlines() if ln.strip()]
    hits = [json.loads(ln) for ln in lines[-80:] if '"source": "mcp"' in ln or '"source":"mcp"' in ln]
    assert hits, "审计里没有 source=mcp 的记录"
    assert any(h.get("tool") == "invoke_tool" and "mcp:" in str(h.get("ref")) for h in hits)


# ---------------------------------------------------------------- ⑥ 幂等 / 删除 / 隔离
def test_rediscover_keeps_manual_decisions(env):
    out = tsp.discover(tsp.DiscoverReq(slug=SLUG, source="mcp"), env["token"])
    assert out["ok"] is True and out["kept_confirmed"] == 2   # 两条都已确认 → 都保留
    entries = cp.pool_entries(env["aid"])
    assert any(e.name == "本地天气" for e in entries)          # 人工改名没被刷掉


def test_delete_mcp_source_removes_capabilities(env):
    aid, token = _mk_account("del")
    try:
        out = tsp.source_save(tsp.ApiSourceReq(slug="delmcp", source="mcp", transport="stdio",
                                               command="npx", args_json='["-y","x"]',
                                               allow_stdio_commands="npx"), token)
        sid = int(out["id"])
        tsp.source_delete(sid, token)
        assert tsp.sources_list(token)["items"] == []
    finally:
        purge_account(aid)


def test_owner_isolation(env):
    """别的账号看不见、也确认不了这只来源（越权隔离）。"""
    aid2, token2 = _mk_account("other")
    try:
        with pytest.raises(HTTPException):
            tsp.candidates_list(env["sid"], token2)
        assert tsp.sources_list(token2)["items"] == []
        # 拿别人的短名来确认 → 直接 404（比"静默跳过"更硬：连这条来源的存在都不确认给对方）
        with pytest.raises(HTTPException):
            tsp.confirm(tsp.ConfirmReq(slug=SLUG, source="mcp", items=[
                tsp.CandidateIn(ref="mcp:%s/get_weather" % SLUG, approve_write=True)]), token2)
    finally:
        purge_account(aid2)
