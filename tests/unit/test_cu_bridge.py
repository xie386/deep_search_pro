# -*- coding: utf-8 -*-
"""v4.1-02：手动起后端时常走的「桥接文件」路径。"""
import json
import os

from tools import screen_tool


def test_env_wins(monkeypatch):
    monkeypatch.setenv("ZX_CU_ENDPOINT", "http://127.0.0.1:6000")
    monkeypatch.setenv("ZX_CU_TOKEN", "tk")
    assert screen_tool._endpoint() == ("http://127.0.0.1:6000", "tk")


def test_bridge_with_live_pid(monkeypatch, tmp_path):
    monkeypatch.delenv("ZX_CU_ENDPOINT", raising=False)
    f = tmp_path / "_cu_bridge.json"
    f.write_text(json.dumps({"endpoint": "http://127.0.0.1:6001", "token": "tk2",
                             "pid": os.getpid()}), encoding="utf-8")
    monkeypatch.setattr(screen_tool, "_BRIDGE", f)
    assert screen_tool._endpoint() == ("http://127.0.0.1:6001", "tk2")


def test_bridge_with_dead_pid_ignored(monkeypatch, tmp_path):
    monkeypatch.delenv("ZX_CU_ENDPOINT", raising=False)
    f = tmp_path / "_cu_bridge.json"
    f.write_text(json.dumps({"endpoint": "http://127.0.0.1:6002", "token": "tk3",
                             "pid": 999999}), encoding="utf-8")
    monkeypatch.setattr(screen_tool, "_BRIDGE", f)
    monkeypatch.setattr(screen_tool, "_pid_alive", lambda pid: False)
    assert screen_tool._endpoint() == ("", "")


def test_missing_bridge_returns_empty(monkeypatch, tmp_path):
    monkeypatch.delenv("ZX_CU_ENDPOINT", raising=False)
    monkeypatch.setattr(screen_tool, "_BRIDGE", tmp_path / "nope.json")
    assert screen_tool._endpoint() == ("", "")
