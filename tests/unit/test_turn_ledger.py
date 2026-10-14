# -*- coding: utf-8 -*-
"""在跑轮次台账（`agent/turn_ledger.py`）单元测试 —— 全离线、不碰真库。

覆盖真机踩到的那条路径：**进程被强杀 / 重启后，没跑完的那一轮要能补落中断占位**，
而不是凭空消失（用户看到「准备开始查」然后对话直接结束）。
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))


def _mod(monkeypatch, tmp_path):
    monkeypatch.setenv("TURN_LEDGER_PATH", str(tmp_path / "turn_inflight.json"))
    from agent import turn_ledger
    turn_ledger._TURNS.clear()
    return turn_ledger


def test_begin_writes_and_end_clears(monkeypatch, tmp_path):
    tl = _mod(monkeypatch, tmp_path)
    tl.begin(3, "t-abc", "帮我看下这个网址")
    assert tl.pending()["t-abc"]["account_id"] == 3
    assert tl.ledger_path().exists()
    tl.end("t-abc")
    assert tl.pending() == {}
    assert json.loads(tl.ledger_path().read_text(encoding="utf-8")) == {}


def test_no_thread_id_is_noop(monkeypatch, tmp_path):
    tl = _mod(monkeypatch, tmp_path)
    tl.begin(3, "", "x")
    tl.end("")
    assert not tl.ledger_path().exists()


def test_recover_writes_placeholder_and_is_idempotent(monkeypatch, tmp_path):
    tl = _mod(monkeypatch, tmp_path)
    tl.begin(3, "t-xyz", "sakuracat 官网在哪")
    tl.end.__wrapped__ if False else None                       # 不 end：模拟进程被强杀
    seen = []
    import agent.conversation_store as cs
    monkeypatch.setattr(cs, "save_turn",
                        lambda aid, tid, q, msgs, **kw: seen.append((aid, tid, q, msgs[0].content)) or True)
    done = tl.recover()
    assert [d["thread_id"] for d in done] == ["t-xyz"]
    aid, tid, q, text = seen[0]
    assert (aid, tid, q) == (3, "t-xyz", "sakuracat 官网在哪")
    assert "重启" in text and "未完成" in text
    assert tl.recover() == []                                   # 幂等：清完再调为空


def test_recover_tolerates_garbage(monkeypatch, tmp_path):
    tl = _mod(monkeypatch, tmp_path)
    p = tl.ledger_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("这不是 JSON", encoding="utf-8")
    assert tl.recover() == []                                   # 不抛、不炸主流程


def test_recover_skips_records_without_question(monkeypatch, tmp_path):
    tl = _mod(monkeypatch, tmp_path)
    p = tl.ledger_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"t1": {"account_id": 3, "question": ""}}), encoding="utf-8")
    assert tl.recover() == []


def test_flush_failure_never_raises(monkeypatch, tmp_path):
    """台账写不进去（如目录不可建）也必须静默 —— 问答主流程不能被它拖垮。"""
    tl = _mod(monkeypatch, tmp_path)
    monkeypatch.setenv("TURN_LEDGER_PATH", str(tmp_path / "no" / "such" / "deep" / "x.json"))
    monkeypatch.setattr(tl, "_flush", lambda: (_ for _ in ()).throw(OSError("x")))
    tl.begin(3, "t1", "q")                                      # 不抛即通过
    tl.end("t1")
