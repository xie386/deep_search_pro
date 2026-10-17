# -*- coding: utf-8 -*-
"""「/」选择随消息落库 → 刷新后气泡标签还在（2026-10-17 用户提的标签需求）。

为什么单独立：原来 user 消息的 `additional_kwargs` **不落库**（`message_to_dict` 只给 assistant 存），
workflow 那一轮更是**提前 return 根本没存** → 刷新后既看不到"用过什么"，连回答都没了。
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))


def _fake_db(monkeypatch, tmp_path):
    from tools import schema_personal as sp
    monkeypatch.delenv("PERSONAL_DB_PATH", raising=False)
    monkeypatch.setattr(sp, "DB_PATH", str(tmp_path / "t.db"))
    assert sp.db_path() == str(tmp_path / "t.db")
    sp.ensure_tables()
    conn = sp.get_personal_conn()
    conn.execute("INSERT INTO accounts (id, username, password_hash) VALUES (7,'t_picks','x')")
    conn.commit(); conn.close()
    return sp


def test_picks_roundtrip(monkeypatch, tmp_path):
    from langchain_core.messages import AIMessage
    _fake_db(monkeypatch, tmp_path)
    from agent import conversation_store as cs

    picks = {"skills": ["竞品对比"], "write_sets": ["归档CLI"], "workflows": ["Steam降价比价与视频"]}
    r = cs.save_turn(7, "th-picks", "请按这个工作流做一份报告",
                     [AIMessage(content="已按 workflow 跑完 2 步。")], picks=picks)
    assert r["ok"] is True

    rows = cs.get_messages_plain(7, "th-picks")
    user = [x for x in rows if x["role"] == "user"][0]
    assert user["picks"] == picks, "user 消息必须带回本轮「/」的选择"
    assert user["text" if "text" in user else "content"] == "请按这个工作流做一份报告"
    assistant = [x for x in rows if x["role"] == "assistant"][0]
    assert "picks" not in assistant


def test_no_picks_still_works(monkeypatch, tmp_path):
    from langchain_core.messages import AIMessage
    _fake_db(monkeypatch, tmp_path)
    from agent import conversation_store as cs
    cs.save_turn(7, "th2", "普通问题", [AIMessage(content="普通回答")])
    user = [x for x in cs.get_messages_plain(7, "th2") if x["role"] == "user"][0]
    assert "picks" not in user


def test_workflow_branch_saves_turn_before_returning():
    """★ 2026-10-17 真机：workflow 分支**提前 return**，落库在它后面 → 这一轮根本没存。

    这里静态钉住顺序：拿到 `_wf_res` 之后、`return` 之前，必须有 `cs.save_turn(... picks=_picks)`。
    """
    src = (ROOT / "api" / "server.py").read_text(encoding="utf-8")
    i = src.index("_wf_res = _wfr.run_workflow(")
    j = src.index("return _wf_ans", i)
    seg = src[i:j]
    assert "_save_turn_safe(" in seg, "workflow 轮必须落库（否则刷新后回答与标签全没了）"
    assert "picks=_picks" in seg, "落库时要带本轮「/」的选择（气泡标签靠它）"


def test_error_branch_saves_turn_too():
    """★ 2026-10-17 真机：500 那条分支原来**一条都不落库** → 报错会话一刷新就消失、没法溯源。"""
    src = (ROOT / "api" / "server.py").read_text(encoding="utf-8")
    i = src.index("except Exception as e:  # 不吞掉错误，给出可读信息")
    j = src.index("raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR", i)
    seg = src[i:j]
    assert "_save_turn_safe(" in seg, "报错轮要把提问+报错落库（否则刷新后消失）"
    assert "picks=" in seg, "报错轮也要带本轮「/」的选择"


def test_save_turn_safe_never_raises(monkeypatch):
    """★ 2026-10-17：`cs` 作用域坑导致落库抛 UnboundLocalError，被 except 吞成一行日志 → 静默丢轮。

    这个助手**就地 import**，且任何失败都只返回 False、不影响回复。
    """
    from api import server
    import agent.conversation_store as csmod

    def _boom(*a, **k):
        raise RuntimeError("database is locked")

    monkeypatch.setattr(csmod, "save_turn", _boom)
    assert server._save_turn_safe(1, "th", "q", "a", picks={"skills": ["x"]}) is False


def test_save_turn_safe_writes_row(monkeypatch, tmp_path):
    from tools import schema_personal as sp
    monkeypatch.delenv("PERSONAL_DB_PATH", raising=False)
    monkeypatch.setattr(sp, "DB_PATH", str(tmp_path / "t.db"))
    sp.ensure_tables()
    conn = sp.get_personal_conn()
    conn.execute("INSERT INTO accounts (id, username, password_hash) VALUES (9,'t_safe','x')")
    conn.commit(); conn.close()

    from api import server
    assert server._save_turn_safe(9, "th-safe", "问题", "回答", picks={"workflows": ["W"]}) is True
    from agent import conversation_store as cs
    rows = cs.get_messages_plain(9, "th-safe")
    assert [r["role"] for r in rows] == ["user", "assistant"]
    assert rows[0]["picks"] == {"workflows": ["W"]}
