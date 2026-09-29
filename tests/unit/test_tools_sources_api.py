# -*- coding: utf-8 -*-
"""M1-6 用例：工具来源配置面（`api/tools_sources.py`）。**全离线**（假 API + 临时账号 + 假 token）。

覆盖：来源保存（幂等 / 密钥留空不覆盖 / 参数校验）· 列表掩码 · `discover` 解析落候选但**不入池**
· `confirm` 只读直接入池 / **写方法必须显式勾选** · **重复 discover 保留人工决定** · 删除连带清理
· 体检（不联网的配置校验）· 越权隔离。

运行：
    .venv/Scripts/python.exe -m pytest tests/test_tools_sources_api.py -q
"""
import json
import os
import sys
import time

import pytest
from fastapi import HTTPException

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from api import tools_sources as tsp  # noqa: E402
from tools import capability_pool as cp  # noqa: E402
from tools.schema_personal import get_personal_conn, purge_account  # noqa: E402

SPEC_TEXT = json.dumps({
    "openapi": "3.0.0",
    "info": {"title": "Demo"},
    "servers": [{"url": "https://api.demo.com"}],
    "paths": {
        "/current/{city}": {"get": {"operationId": "getCurrent", "summary": "当前天气",
                                    "parameters": [{"name": "city", "in": "path", "required": True,
                                                    "schema": {"type": "string"}}]}},
        "/hook": {"post": {"operationId": "createHook", "summary": "建钩子"}},
    },
}, ensure_ascii=False)


def _mk_account(tag):
    conn = get_personal_conn()
    try:
        aid = int(conn.execute("INSERT INTO accounts (username, password_hash, role) VALUES (?,?,?)",
                               ("m1src_%s_%d" % (tag, int(time.time() * 1000) % 100000), "x", "user")).lastrowid)
        token = "tok_%d_%d" % (aid, int(time.time() * 1000) % 100000)
        conn.execute("INSERT INTO sessions (token, account_id, login_at) VALUES (?,?,?)", (token, aid, time.time()))
        conn.commit()
    finally:
        conn.close()
    return aid, token


@pytest.fixture()
def acct():
    aid, token = _mk_account("a")
    yield {"aid": aid, "token": token}
    purge_account(aid)


@pytest.fixture()
def other():
    aid, token = _mk_account("b")
    yield {"aid": aid, "token": token}
    purge_account(aid)


def _save(token, **over):
    req = dict(slug="weather", name="和风天气", base_url="https://api.demo.com",
               auth_type="header", auth_name="Authorization", auth_prefix="Bearer ",
               auth_secret="sk-secret-123456", abilities="查天气", timeout=10)
    req.update(over)
    return tsp.source_save(tsp.ApiSourceReq(**req), token)


# ---------------------------------------------------------------- 来源 CRUD
def test_save_creates_source(acct):
    out = _save(acct["token"])
    assert out["slug"] == "weather" and out["id"] > 0
    items = tsp.sources_list(acct["token"])["items"]
    assert len(items) == 1 and items[0]["config"]["base_url"] == "https://api.demo.com"


def test_list_masks_secret_and_hides_raw_json(acct):
    _save(acct["token"])
    s = tsp.sources_list(acct["token"])["items"][0]
    assert s["config"]["auth_secret_masked"].endswith("***56") or "***" in s["config"]["auth_secret_masked"]
    assert "sk-secret-123456" not in json.dumps(s, ensure_ascii=False)
    assert s["config_json"] == ""                      # 原始 JSON（含密钥）不外发


def test_save_is_idempotent_and_keeps_secret_when_blank(acct):
    _save(acct["token"])
    _save(acct["token"], name="改个名", auth_secret="")   # 密钥留空 = 不修改
    items = tsp.sources_list(acct["token"])["items"]
    assert len(items) == 1 and items[0]["name"] == "改个名"
    conn = get_personal_conn()
    try:
        cfg = json.loads(conn.execute("SELECT config_json FROM tool_sources WHERE account_id=?",
                                      (acct["aid"],)).fetchone()["config_json"])
    finally:
        conn.close()
    assert cfg["auth"]["secret"] == "sk-secret-123456"


@pytest.mark.parametrize("bad,why", [
    ({"slug": "有中文"}, "短名"),
    ({"base_url": "ftp://x"}, "base_url"),
    ({"headers_json": "[1,2]"}, "默认头"),
    ({"auth_type": "cookie"}, "鉴权类型"),
])
def test_save_validates_input(acct, bad, why):
    with pytest.raises(HTTPException) as e:
        _save(acct["token"], **bad)
    assert why in str(e.value.detail)


def test_delete_removes_source_and_capabilities(acct):
    sid = _save(acct["token"])["id"]
    tsp.discover(tsp.DiscoverReq(slug="weather", text=SPEC_TEXT), acct["token"])
    assert tsp.confirm(tsp.ConfirmReq(slug="weather", items=[tsp.CandidateIn(ref="api:weather#getCurrent")]),
                       acct["token"])["confirmed"]
    out = tsp.source_delete(sid, acct["token"])
    assert out["deleted_capabilities"] == 2
    assert tsp.sources_list(acct["token"])["items"] == []


# ---------------------------------------------------------------- 发现 → 候选 → 确认
def test_discover_stores_candidates_but_not_in_pool(acct):
    _save(acct["token"])
    res = tsp.discover(tsp.DiscoverReq(slug="weather", text=SPEC_TEXT), acct["token"])
    assert res["ok"] and len(res["items"]) == 2
    assert cp.pool_entries(acct["aid"]) == []          # ★ 未确认 → 不入池
    cands = tsp.candidates_list(_sid(acct), acct["token"])["items"]
    assert {c["ref"] for c in cands} == {"api:weather#getCurrent", "api:weather#createHook"}
    assert all(c["confirmed"] is False for c in cands)
    assert "参数: city(必填,path)" in next(c for c in cands if c["ref"].endswith("getCurrent"))["param_summary"]


def test_confirm_readonly_puts_capability_in_pool(acct):
    _save(acct["token"])
    tsp.discover(tsp.DiscoverReq(slug="weather", text=SPEC_TEXT), acct["token"])
    out = tsp.confirm(tsp.ConfirmReq(slug="weather", items=[tsp.CandidateIn(ref="api:weather#getCurrent")]),
                      acct["token"])
    assert out["confirmed"] == ["api:weather#getCurrent"] and out["skipped"] == []
    refs = {e.ref for e in cp.pool_entries(acct["aid"])}
    assert refs == {"api:weather#getCurrent"}          # 只有确认过的那条进池


def test_write_method_requires_explicit_approval(acct):
    _save(acct["token"])
    tsp.discover(tsp.DiscoverReq(slug="weather", text=SPEC_TEXT), acct["token"])
    # ① 不勾 approve_write → 跳过
    out = tsp.confirm(tsp.ConfirmReq(slug="weather", items=[
        tsp.CandidateIn(ref="api:weather#createHook")]), acct["token"])
    assert out["confirmed"] == [] and "需显式勾选" in out["skipped"][0]["why"]
    assert cp.pool_entries(acct["aid"]) == []
    # ② 勾了 → 按 read_only=0 入库并进池
    out = tsp.confirm(tsp.ConfirmReq(slug="weather", items=[
        tsp.CandidateIn(ref="api:weather#createHook", approve_write=True)]), acct["token"])
    assert out["confirmed"] == ["api:weather#createHook"]
    e = cp.pool_entries(acct["aid"])[0]
    assert e.read_only is False and "会改外部状态" in e.card_text


def test_confirm_rejects_foreign_ref(acct):
    _save(acct["token"])
    tsp.discover(tsp.DiscoverReq(slug="weather", text=SPEC_TEXT), acct["token"])
    out = tsp.confirm(tsp.ConfirmReq(slug="weather", items=[tsp.CandidateIn(ref="api:other#x")]), acct["token"])
    assert out["confirmed"] == [] and "不属于该来源" in out["skipped"][0]["why"]


def test_rediscover_keeps_human_decisions(acct):
    """重复粘贴同一份 spec：已确认的写能力不能被"刷"回只读或未确认。"""
    _save(acct["token"])
    tsp.discover(tsp.DiscoverReq(slug="weather", text=SPEC_TEXT), acct["token"])
    tsp.confirm(tsp.ConfirmReq(slug="weather", items=[
        tsp.CandidateIn(ref="api:weather#createHook", approve_write=True, name="我改的名字")]), acct["token"])
    res = tsp.discover(tsp.DiscoverReq(slug="weather", text=SPEC_TEXT), acct["token"])
    assert res["kept_confirmed"] == 1
    e = next(x for x in cp.pool_entries(acct["aid"], enabled_only=False) if x.ref == "api:weather#createHook")
    assert e.read_only is False and e.confirmed is True
    conn = get_personal_conn()
    try:
        row = conn.execute("SELECT name FROM tool_capabilities WHERE account_id=? AND ref=?",
                           (acct["aid"], "api:weather#createHook")).fetchone()
    finally:
        conn.close()
    assert row["name"] == "我改的名字"                  # 人工改的显示名不被覆盖


def test_discover_requires_existing_source(acct):
    with pytest.raises(HTTPException) as e:
        tsp.discover(tsp.DiscoverReq(slug="nope", text=SPEC_TEXT), acct["token"])
    assert "先保存这个来源" in str(e.value.detail)


def test_discover_bad_spec_is_readable(acct):
    _save(acct["token"])
    res = tsp.discover(tsp.DiscoverReq(slug="weather", text="<html>404</html>"), acct["token"])
    assert res["ok"] is False and "不像是 OpenAPI" in res["error"]


# ---------------------------------------------------------------- 体检与隔离
def test_source_test_validates_config_without_network(acct):
    sid = _save(acct["token"])["id"]
    res = tsp.source_test(sid, acct["token"], probe=False)      # 不联网
    assert res["ok"] is True and len(res["steps"]) >= 3
    assert {s["name"] for s in res["steps"]} >= {"base_url 合法", "鉴权配置完整"}


def test_source_test_flags_missing_base_url(acct):
    sid = _save(acct["token"])["id"]
    conn = get_personal_conn()
    try:
        conn.execute("UPDATE tool_sources SET config_json='{}' WHERE id=?", (sid,))
        conn.commit()
    finally:
        conn.close()
    res = tsp.source_test(sid, acct["token"], probe=False)
    assert res["ok"] is False
    assert any(s["name"] == "base_url 合法" and s["ok"] is False for s in res["steps"])


def test_sources_are_isolated_between_accounts(acct, other):
    _save(acct["token"])
    assert tsp.sources_list(other["token"])["items"] == []
    assert cp.pool_entries(other["aid"]) == []


def _sid(acct) -> int:
    return tsp.sources_list(acct["token"])["items"][0]["id"]


# ---------------------------------------------------------------- 两个入口的形状必须一致（真 bug 回归）
def test_candidate_view_shape_is_identical_across_endpoints(acct):
    """★ 真机报障逮到：`discover` 与 `candidates_list` 返回的形状必须**完全一致**。

    前端靠 `is_write` 决定"写操作要不要二次确认"、靠 `param_summary` 展示参数。`discover` 最初
    直接把解析器条目吐出去（只有 `read_only`，没有 `is_write`）→ 刚贴完文档那一步，POST 会显示成
    「只读」、写操作的勾选框也不会被禁用（服务端仍会拒，但界面在骗人）。
    """
    _save(acct["token"])
    res = tsp.discover(tsp.DiscoverReq(slug="weather", text=SPEC_TEXT), acct["token"])
    disc = {c["ref"]: c for c in res["items"]}
    live = {c["ref"]: c for c in tsp.candidates_list(_sid(acct), acct["token"])["items"]}
    assert set(disc) == set(live) == {"api:weather#getCurrent", "api:weather#createHook"}
    for ref, d in disc.items():
        assert set(d.keys()) == set(live[ref].keys()), "形状不一致：%s" % ref
        assert d["is_write"] == live[ref]["is_write"]
        assert d["read_only"] == live[ref]["read_only"]
    # 写方法（POST）两侧都必须标成写、且不谎报只读
    assert disc["api:weather#createHook"]["is_write"] is True
    assert disc["api:weather#createHook"]["read_only"] is False
    assert disc["api:weather#getCurrent"]["is_write"] is False
    assert "参数: city(必填,path)" in disc["api:weather#getCurrent"]["param_summary"]


def test_candidates_endpoint_backs_the_reopen_entry(acct):
    """「查看能力/待确认」入口的数据源：确认前 `confirmed=False`，确认后翻成 True，字段不缩水。"""
    _save(acct["token"])
    tsp.discover(tsp.DiscoverReq(slug="weather", text=SPEC_TEXT), acct["token"])
    before = {c["ref"]: c for c in tsp.candidates_list(_sid(acct), acct["token"])["items"]}
    assert before["api:weather#getCurrent"]["confirmed"] is False
    assert before["api:weather#getCurrent"]["enabled"] is False      # 未确认 = 未启用
    assert "param_summary" in before["api:weather#getCurrent"]
    key_before = set(before["api:weather#getCurrent"])
    tsp.confirm(tsp.ConfirmReq(slug="weather", items=[tsp.CandidateIn(ref="api:weather#getCurrent")]),
                acct["token"])
    after = {c["ref"]: c for c in tsp.candidates_list(_sid(acct), acct["token"])["items"]}
    assert after["api:weather#getCurrent"]["confirmed"] is True
    assert after["api:weather#getCurrent"]["enabled"] is True
    assert set(after["api:weather#getCurrent"]) == key_before       # 形状不随状态变化
    assert after["api:weather#createHook"]["confirmed"] is False    # 没确认的那条还在列表里


def test_rediscover_refreshes_abilities_but_keeps_human_decisions(acct):
    """★ 真机（2026-09-27）：改写文档里的 summary 后重贴，**已确认工具的描述也必须跟着更新**。

    否则"改进能力描述以提高路由命中率"这件事对已确认的工具完全无效（读到的仍是旧描述）。
    人写的那句"能力描述"在**来源**行、读取时拼接，所以工具行由文档派生，可以安全刷新。
    """
    _save(acct["token"])
    tsp.discover(tsp.DiscoverReq(slug="weather", text=SPEC_TEXT), acct["token"])
    tsp.confirm(tsp.ConfirmReq(slug="weather", items=[tsp.CandidateIn(ref="api:weather#getCurrent")]),
                acct["token"])
    improved = SPEC_TEXT.replace("当前天气", "当前天气（含气温与体感温度）")
    tsp.discover(tsp.DiscoverReq(slug="weather", text=improved), acct["token"])
    ent = [e for e in cp.pool_entries(acct["aid"]) if e.ref == "api:weather#getCurrent"]
    assert ent, "已确认工具不能因为重贴文档掉出池子"
    assert "含气温与体感温度" in ent[0].abilities                    # 描述跟着文档更新
    assert ent[0].confirmed is True and ent[0].enabled is True        # 人工决定保留
    assert ent[0].read_only is True
