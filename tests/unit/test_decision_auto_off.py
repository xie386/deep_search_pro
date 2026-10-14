# -*- coding: utf-8 -*-
"""决策模型的「致命错误自动降级」与「开关变更审计」单元测试 —— 全离线、不碰真库。

起因（2026-10-15 真机排查）：用户报「10-15 00:11 报余额不足，但 Jev 开关全程是关的」。
排查结论：那一刻库里 `accounts.decision_router` **是 1**（门禁 `maybe_refine:435` 是硬的、
全仓只有开关端点能改该字段），而模型侧没额度 → **每轮白试一次**；更要命的是
**开关的改动作没有任何审计留痕**，所以无从判断是谁、何时打开的。本文件钉住两条修补。
"""
import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))


class _FakeConn:
    def __init__(self):
        self.executed = []
        self.committed = False

    def execute(self, sql, params=()):
        self.executed.append((sql, params))

        class _Cur:
            rowcount = 1
        return _Cur()

    def commit(self):
        self.committed = True

    def close(self):
        pass


def _patch_db(monkeypatch, conn):
    import tools.schema_personal as sp
    monkeypatch.setattr(sp, "ensure_tables", lambda: None)
    monkeypatch.setattr(sp, "get_personal_conn", lambda: conn)


def test_fatal_error_turns_switch_off_and_audits(monkeypatch):
    """观察到的真实错误（`http_402:no credits`）必须触发自动关闭 + 双份留痕。"""
    from tools import decision_router as dr
    conn = _FakeConn()
    _patch_db(monkeypatch, conn)
    seen = []
    monkeypatch.setattr(dr, "audit", lambda **kw: seen.append(kw))
    assert dr._maybe_auto_off(3, "http_402:no credits") is True
    assert conn.executed and "decision_router=0" in conn.executed[0][0]
    assert conn.executed[0][1] == (3,) and conn.committed
    assert seen and seen[-1]["event"] == "auto_off", "自动关闭必须写审计（可回溯）"


def test_non_fatal_error_never_touches_the_db(monkeypatch):
    """普通错误（超时 / 形状不对）不关开关 —— 那些是瞬时问题，关了反而白降级。"""
    from tools import decision_router as dr
    conn = _FakeConn()
    _patch_db(monkeypatch, conn)
    for err in ("timeout", "empty_response", "json_invalid", ""):
        assert dr._maybe_auto_off(3, err) is False
    assert conn.executed == []


def test_no_account_means_no_write(monkeypatch):
    from tools import decision_router as dr
    conn = _FakeConn()
    _patch_db(monkeypatch, conn)
    assert dr._maybe_auto_off(None, "http_402:no credits") is False
    assert conn.executed == []


def test_db_failure_is_swallowed(monkeypatch):
    """库写失败也不能把整轮精排带崩（返回 False 即可）。"""
    from tools import decision_router as dr
    import tools.schema_personal as sp
    monkeypatch.setattr(sp, "ensure_tables", lambda: None)
    monkeypatch.setattr(sp, "get_personal_conn", lambda: (_ for _ in ()).throw(RuntimeError("db")))
    assert dr._maybe_auto_off(3, "http_402:no credits") is False


def test_audit_write_failure_does_not_block_the_switch_off(monkeypatch):
    """审计写失败时，开关该关还是要关（审计是记录，不是前置条件）。"""
    from tools import decision_router as dr
    conn = _FakeConn()
    _patch_db(monkeypatch, conn)
    monkeypatch.setattr(dr, "audit", lambda **kw: (_ for _ in ()).throw(OSError("x")))
    assert dr._maybe_auto_off(3, "http_402:no credits") is True
    assert conn.committed


def test_fatal_marks_cover_observed_errors():
    """回归钉子：真实遇到过的错误串必须在特征表里（改表时别把 402 漏掉）。"""
    from tools.decision_router import _FATAL_ERR_MARKS
    for err in ("http_402:no credits", "insufficient quota", "invalid_api_key"):
        assert any(m in err.lower() for m in _FATAL_ERR_MARKS), err


def test_switch_endpoint_writes_audit():
    """源码级：开关端点必须落审计（真机出现过「查不出谁开的开关」）。"""
    src = (ROOT / "api" / "server.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.AsyncFunctionDef) and n.name == "decision_router_set")
    seg = (ast.get_source_segment(src, fn) or "").replace("'", '"')
    assert 'write_event("decision", "switch"' in seg
