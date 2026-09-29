# -*- coding: utf-8 -*-
"""M5c-1 用例：来源级「我确认这个服务全是只读」声明。**全离线**（本地 stdio 小 server）。

口径（写死在用例里）：
  ① 服务端**没声明**（`readOnlyHint` 缺省）→ 用户可以声明为只读；
  ② 服务端**明确声明 false** → **任何时候都不被这个开关覆盖**（明确说会改状态就按写处理）；
  ③ `sync_readonly=true` 才刷新**已确认**工具（默认受 M1「已确认行保留人工决定」保护）；
  ④ 「候选视图」与「确认闸」必须同一口径（否则又是"界面在骗人"）。

运行：
    .venv/Scripts/python.exe -m pytest tests/test_m5c_readonly_declare.py -q
"""
import json
import os
import sys
import textwrap
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from api import tools_sources as tsp                       # noqa: E402
from tools import capability_pool as cp                    # noqa: E402
from tools.schema_personal import get_personal_conn, purge_account  # noqa: E402

SLUG = "rodecl"
SERVER_CODE = textwrap.dedent('''
    from mcp.server import MCPServer
    srv = MCPServer("ro-demo")

    @srv.tool(description="查天气（明确只读）", annotations={"readOnlyHint": True})
    def ro_tool(city: str) -> dict:
        return {"city": city}

    @srv.tool(description="下单（明确声明非只读）", annotations={"readOnlyHint": False})
    def write_tool(sku: str) -> dict:
        return {"ok": True, "sku": sku}

    @srv.tool(description="悄悄查点什么（一个 hint 都不给）")
    def silent_tool(q: str = "x") -> dict:
        return {"q": q}

    if __name__ == "__main__":
        srv.run()
''')


@pytest.fixture(scope="module")
def srv_path(tmp_path_factory):
    p = tmp_path_factory.mktemp("m5c1") / "server.py"
    p.write_text(SERVER_CODE, encoding="utf-8")
    return str(p)


def mk_account(tag):
    conn = get_personal_conn()
    try:
        aid = int(conn.execute("INSERT INTO accounts (username, password_hash, role) VALUES (?,?,?)",
                               ("m5c1_%s_%d" % (tag, int(time.time() * 1000) % 100000), "x", "user")).lastrowid)
        token = "tok_%d_%d" % (aid, int(time.time() * 1000) % 100000)
        conn.execute("INSERT INTO sessions (token, account_id, login_at) VALUES (?,?,?)", (token, aid, time.time()))
        conn.commit()
    finally:
        conn.close()
    return aid, token


def save_req(srv_path, **over):
    req = tsp.ApiSourceReq(slug=SLUG, name="只读声明演示", source="mcp", transport="stdio",
                           command=sys.executable, args_json=json.dumps([srv_path]), timeout=60,
                           allow_stdio_commands=os.path.basename(sys.executable))
    for k, v in over.items():
        setattr(req, k, v)
    return req


@pytest.fixture()
def acct(srv_path):
    aid, token = mk_account("a")
    yield {"aid": aid, "token": token, "srv": srv_path}
    purge_account(aid)


def _items(token, slug=SLUG):
    d = tsp.discover(tsp.DiscoverReq(slug=slug, source="mcp"), token)
    assert d["ok"] is True, d.get("error")
    return {i["tool"]: i for i in d["items"]}


# ---------------------------------------------------------------- ① 默认（未声明 → 保守按写）
def test_default_undeclared_is_write(acct):
    tsp.source_save(save_req(acct["srv"]), acct["token"])
    by = _items(acct["token"])
    assert by["ro_tool"]["is_write"] is False and by["ro_tool"]["read_only_hint"] is True
    assert by["write_tool"]["is_write"] is True and by["write_tool"]["read_only_hint"] is False
    assert by["silent_tool"]["is_write"] is True and by["silent_tool"]["read_only_hint"] is None


# ---------------------------------------------------------------- ② 声明后：未声明的变只读，明确 false 的不动
def test_declare_readonly_only_covers_undeclared(acct):
    tsp.source_save(save_req(acct["srv"], assume_read_only=True), acct["token"])
    by = _items(acct["token"])
    assert by["ro_tool"]["is_write"] is False                       # 服务端说只读 → 只读
    assert by["silent_tool"]["is_write"] is False                   # ★ 未声明 + 用户声明 → 只读
    assert by["write_tool"]["is_write"] is True, "★ 服务端明确 false 的工具不得被开关覆盖"
    # 列表投影要回显开关（前端徽标要用）
    srcs = tsp.sources_list(acct["token"])["items"]
    assert [s for s in srcs if s["slug"] == SLUG][0]["config"]["assume_read_only"] is True


# ---------------------------------------------------------------- ③ 确认闸与候选视图同口径
def test_confirm_gate_matches_candidate_view(acct):
    tsp.source_save(save_req(acct["srv"], assume_read_only=True), acct["token"])
    by = _items(acct["token"])
    # silent_tool 现在是只读 → 不需要 approve_write
    out = tsp.confirm(tsp.ConfirmReq(slug=SLUG, source="mcp", items=[
        tsp.CandidateIn(ref="mcp:%s/silent_tool" % SLUG)]), acct["token"])
    assert out["confirmed"] == ["mcp:%s/silent_tool" % SLUG], out
    # write_tool 仍是写 → 必须显式勾选（界面显示的 is_write 与闸门一致）
    out2 = tsp.confirm(tsp.ConfirmReq(slug=SLUG, source="mcp", items=[
        tsp.CandidateIn(ref="mcp:%s/write_tool" % SLUG)]), acct["token"])
    assert out2["confirmed"] == [] and out2["skipped"]
    assert by["write_tool"]["is_write"] is True and out2["skipped"][0]["ref"].endswith("write_tool")


# ---------------------------------------------------------------- ④ 已确认工具的刷新：必须显式（sync_readonly）
def test_sync_readonly_refreshes_confirmed_rows(acct):
    # 先在不声明的情况下确认（此时 silent_tool 被当作写 → 老实勾了 approve_write）
    tsp.source_save(save_req(acct["srv"]), acct["token"])
    _items(acct["token"])
    tsp.confirm(tsp.ConfirmReq(slug=SLUG, source="mcp", items=[
        tsp.CandidateIn(ref="mcp:%s/silent_tool" % SLUG, approve_write=True)]), acct["token"])
    assert [e for e in cp.pool_entries(acct["aid"]) if e.ref.endswith("silent_tool")][0].read_only is False

    # 光保存开关（不带 sync）→ 已确认的那条**不动**（M1 铁律：已确认行保留人工决定）
    out = tsp.source_save(save_req(acct["srv"], assume_read_only=True), acct["token"])
    assert out.get("readonly_synced") == 0
    assert [e for e in cp.pool_entries(acct["aid"]) if e.ref.endswith("silent_tool")][0].read_only is False

    # 显式带上 sync_readonly → 只刷新**服务端没声明**的那一个（silent_tool）
    out = tsp.source_save(save_req(acct["srv"], assume_read_only=True, sync_readonly=True), acct["token"])
    assert out["readonly_synced"] == 1, out          # ★ 只算"未声明"的行：ro_tool 有 hint、write_tool 明确 false
    entries = {e.ref.split("/")[-1]: e for e in cp.pool_entries(acct["aid"])}
    assert entries["silent_tool"].read_only is True
    conn = get_personal_conn()
    try:
        row = conn.execute("SELECT read_only FROM tool_capabilities WHERE account_id=? AND ref=?",
                           (acct["aid"], "mcp:%s/write_tool" % SLUG)).fetchone()
    finally:
        conn.close()
    assert dict(row)["read_only"] == 0                # ★ 明确 false 的行始终没被改


# ---------------------------------------------------------------- ⑤ 取消开关 → 回到保守
def test_turn_off_reverts_to_conservative(acct):
    tsp.source_save(save_req(acct["srv"], assume_read_only=True, sync_readonly=True), acct["token"])
    by = _items(acct["token"])
    assert by["silent_tool"]["is_write"] is False
    out = tsp.source_save(save_req(acct["srv"], assume_read_only=False, sync_readonly=True), acct["token"])
    assert out["readonly_synced"] >= 1
    by2 = _items(acct["token"])
    assert by2["silent_tool"]["is_write"] is True, "取消声明后必须回到「未声明 = 写」的保守口径"
    assert by2["write_tool"]["is_write"] is True


# ---------------------------------------------------------------- ⑥ 纯函数层：边界写死
def test_is_write_row_boundary():
    f = tsp._is_write_row
    assert f("mcp", {"read_only_hint": None}, True) is False       # 未声明 + 有声明 → 只读
    assert f("mcp", {"read_only_hint": None}, False) is True       # 未声明 + 无声明 → 写
    assert f("mcp", {"read_only_hint": False}, True) is True       # ★ 明确 false 永不被覆盖
    assert f("mcp", {"read_only_hint": True}, False) is False
    # api 侧不受这个开关影响（写/只读仍由 HTTP 方法决定）
    assert f("api", {"method": "POST"}, True) is True
    assert f("api", {"method": "GET"}, False) is False
