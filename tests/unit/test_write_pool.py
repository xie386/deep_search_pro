# -*- coding: utf-8 -*-
"""06 · 自配写工具池：选集、注入文本、归属设置 —— 全离线。

**为什么要有它**：写工具集是"写能力"的唯一授权入口，两条规矩必须钉住：
  ① 未选中 → 注入文本为空串（零影响），且运行期闸那边名单也为空（写工具不可用）；
  ② 名字对不上任何写池来源 → **静默跳过**（越权名不认：不能让用户靠猜名字把只读的东西叫出来）。
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from api import write_pool as wp  # noqa: E402


def test_caps_clamped_to_7(monkeypatch):
    monkeypatch.setattr(wp, "get_int", lambda k, d=0: 99)
    assert wp._caps() == {"skills": 7, "write_sets": 7, "workflows": 7}
    monkeypatch.setattr(wp, "get_int", lambda k, d=0: 0)
    assert wp._caps() == {"skills": 1, "write_sets": 1, "workflows": 1}


def test_compose_empty_when_nothing_selected():
    assert wp.compose_write_sets_text(3, []) == ""
    assert wp.compose_write_sets_text(3, ["", "  "]) == ""
    assert wp.compose_write_sets_text(None, ["x"]) == ""


def test_compose_ignores_unknown_names(monkeypatch):
    """越权名不认：名字对不上写池来源 → 空串（不会凭空给出授权说明）。"""
    monkeypatch.setattr(wp, "write_sets", lambda aid: [])
    assert wp.compose_write_sets_text(3, ["随便猜的名字"]) == ""


def test_compose_lists_picked_sets_with_write_cmds(monkeypatch):
    monkeypatch.setattr(wp, "write_sets", lambda aid: [
        {"kind": "cli", "id": 1, "name": "归档CLI", "ref": "arch", "write_rules": ["push", "tag"]},
        {"kind": "mcp", "id": 2, "name": "文件服务器", "ref": "fs", "write_rules": []},
    ])
    t = wp.compose_write_sets_text(3, ["归档CLI"])
    assert "本轮经「/」授权的写工具集" in t and "归档CLI" in t and "push、tag" in t
    assert "run_shell_command(command=\"arch\"" in t, "CLI 要给可执行调用方式"
    assert "文件服务器" not in t, "没选的不许出现在说明书里"


def test_write_sets_reads_cli_pool_and_sources(monkeypatch):
    import tools.cli_registry as cr
    monkeypatch.setattr(cr, "list_clis", lambda aid: [
        {"id": 1, "name": "只读CLI", "bin": "ro", "pool": "readonly"},
        {"id": 2, "name": "写CLI", "bin": "rw", "pool": "write", "write_rules": [["push"]]},
    ])
    class _Row(dict):
        def __getitem__(self, k): return dict.__getitem__(self, k)
    class _Conn:
        def execute(self, sql, *a, **k):
            if "FROM tool_capabilities" in (sql or ""):     # 新增：来源下已确认工具（本用例不关心）
                return []
            return [ _Row(id=7, source="mcp", slug="fs", name="文件服务器", pool="write") ]
        def close(self): pass
    monkeypatch.setattr(wp, "get_personal_conn", lambda: _Conn())
    got = wp.write_sets(3)
    names = [s["name"] for s in got]
    assert names == ["写CLI", "文件服务器"], names
    assert got[0]["write_rules"] == ["push"] and got[0]["kind"] == "cli" and got[1]["kind"] == "mcp"


def test_set_pool_only_accepts_known_kinds(monkeypatch):
    r = wp.set_pool(3, "bogus", "1", "write")
    assert r["ok"] is False and "kind" in r["error"]


def test_set_pool_bumps_version(monkeypatch):
    calls = {"bump": 0}
    class _Cur:
        rowcount = 1
        def fetchone(self): return None      # 级联查询：本用例不强求命中
    class _Conn:
        def execute(self, *a, **k): return _Cur()
        def commit(self): pass
        def close(self): pass
    monkeypatch.setattr(wp, "get_personal_conn", lambda: _Conn())
    monkeypatch.setattr(wp.cp, "bump_pool_version", lambda aid: calls.__setitem__("bump", calls["bump"] + 1) or 1)
    r = wp.set_pool(3, "cli", "1", "write")
    assert r["ok"] and calls["bump"] == 1, "改归属必须让路由器缓存失效"


def test_set_pool_reports_missing_source(monkeypatch):
    class _Cur:
        rowcount = 0
        def fetchone(self): return None
    class _Conn:
        def execute(self, *a, **k): return _Cur()
        def commit(self): pass
        def close(self): pass
    monkeypatch.setattr(wp, "get_personal_conn", lambda: _Conn())
    r = wp.set_pool(3, "api", "nope", "write")
    assert r["ok"] is False and "没找到" in r["error"]


def test_compose_tells_mcp_to_use_invoke_tool(monkeypatch):
    """★ 2026-10-18 真机：模型把 MCP 来源当 CLI 跑 `run_shell_command("mineru")` → 不在白名单。

    授权说明书必须写清：MCP 走 `invoke_tool(ref=…)`，并列出可用工具 ref。
    """
    monkeypatch.setattr(wp, "write_sets", lambda aid: [
        {"kind": "mcp", "id": 2, "name": "MinerU 文档解析", "ref": "mineru", "write_rules": [],
         "tools": ["mcp:mineru/parse_documents"]},
    ])
    t = wp.compose_write_sets_text(3, ["MinerU 文档解析"])
    assert "invoke_tool(" in t and "不能交给 run_shell_command" in t
    assert "mcp:mineru/parse_documents" in t
