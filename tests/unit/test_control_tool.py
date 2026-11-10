# -*- coding: utf-8 -*-
"""v4.1-03：屏幕控制工具的闸门与坐标换算（不起真窗口，全部可单测）。"""
import pytest

from tools import control_tool
from tools._runtime import cu_input
from utils import cu_mode


@pytest.fixture(autouse=True)
def _clean():
    cu_mode.end_turn()
    yield
    cu_mode.end_turn()


def _turn(control=True, cid=3, scale=0.5, rect=(0, 0, 2880, 1620)):
    cu_mode.begin_turn(True, control, "th-test")
    cu_mode.current().last_cid = cid
    cu_mode.set_frame(cid, scale, rect)
    return cu_mode.current()


# ── 闸门 ────────────────────────────────────────────────────────────
def test_off_refuses():
    cu_mode.begin_turn(False, False)
    out = control_tool.mouse_click.invoke({"x": 1, "y": 1, "capture_id": 1, "reason": "t"})
    assert "没有开启" in out


def test_look_only_refuses_control():
    cu_mode.begin_turn(True, False)
    out = control_tool.type_text.invoke({"text": "hi", "capture_id": 1})
    assert "屏幕控制" in out and "没有开" in out


def test_no_frame_refuses():
    cu_mode.begin_turn(True, True)
    out = control_tool.mouse_click.invoke({"x": 1, "y": 1, "capture_id": 1, "reason": "t"})
    assert "先调用 screen_capture" in out


def test_stale_capture_id_refuses():
    _turn(cid=5)
    out = control_tool.mouse_click.invoke({"x": 1, "y": 1, "capture_id": 2, "reason": "t"})
    assert "屏幕已变化" in out and "#5" in out


def test_action_limit(monkeypatch):
    turn = _turn()
    turn.actions = 20
    monkeypatch.setattr(control_tool, "_limit", lambda: 20)
    out = control_tool.mouse_click.invoke({"x": 1, "y": 1, "capture_id": 3, "reason": "t"})
    assert "次数已用完" in out


def test_whitelist_rejects_when_empty(monkeypatch):
    _turn()
    monkeypatch.setattr(cu_input, "whitelist", lambda: set())
    out = control_tool.mouse_click.invoke({"x": 10, "y": 10, "capture_id": 3, "reason": "t"})
    assert "白名单为空" in out


def test_click_ok_bumps_counter(monkeypatch):
    turn = _turn()
    calls = {}
    monkeypatch.setattr(cu_input, "check_target", lambda x, y: (True, "notepad.exe"))
    monkeypatch.setattr(cu_input, "click", lambda x, y, double=False: calls.update(x=x, y=y, d=double))
    monkeypatch.setattr(cu_input, "audit", lambda *a, **k: None)
    monkeypatch.setattr(cu_input, "foreground_process", lambda: "notepad.exe")
    out = control_tool.mouse_click.invoke({"x": 100, "y": 50, "capture_id": 3, "reason": "点搜索框"})
    assert "已在图上 (100, 50)" in out and "notepad.exe" in out
    assert calls == {"x": 200, "y": 100, "d": False}    # scale=0.5, offset=0 → 物理 = 图上 ÷ 0.5
    assert turn.actions == 1


def test_press_key_ok(monkeypatch):
    turn = _turn()
    got = {}
    monkeypatch.setattr(cu_input, "check_foreground", lambda: (True, "哔哩哔哩.exe"))
    monkeypatch.setattr(cu_input, "press", lambda key, times=1: got.update(key=key, n=times))
    monkeypatch.setattr(cu_input, "audit", lambda *a, **k: None)
    monkeypatch.setattr(cu_input, "foreground_process", lambda: "哔哩哔哩.exe")
    out = control_tool.press_key.invoke({"key": "enter", "capture_id": 3, "reason": "提交搜索"})
    assert "已按下 enter" in out and "哔哩哔哩.exe" in out
    assert got == {"key": "enter", "n": 1}
    assert turn.actions == 1


def test_press_key_rejects_unknown(monkeypatch):
    _turn()
    out = control_tool.press_key.invoke({"key": "f8", "capture_id": 3})
    assert "不支持的按键" in out


def test_click_reports_landing_window(monkeypatch):
    _turn()
    monkeypatch.setattr(cu_input, "check_target", lambda x, y: (True, "哔哩哔哩.exe"))
    monkeypatch.setattr(cu_input, "click", lambda x, y, double=False: None)
    monkeypatch.setattr(cu_input, "audit", lambda *a, **k: None)
    monkeypatch.setattr(cu_input, "foreground_process", lambda: "哔哩哔哩.exe")
    monkeypatch.setattr(cu_input, "window_text_at", lambda x, y: "哔哩哔哩 - 搜索")
    out = control_tool.mouse_click.invoke({"x": 10, "y": 10, "capture_id": 3})
    assert "落点所在窗口：哔哩哔哩 - 搜索" in out


def test_type_ok_and_empty(monkeypatch):
    turn = _turn()
    monkeypatch.setattr(cu_input, "check_foreground", lambda: (True, "notepad.exe"))
    monkeypatch.setattr(cu_input, "type_text", lambda t: None)
    monkeypatch.setattr(cu_input, "audit", lambda *a, **k: None)
    monkeypatch.setattr(cu_input, "foreground_process", lambda: "notepad.exe")
    out = control_tool.type_text.invoke({"text": "小约翰可汗", "capture_id": 3, "reason": "搜索"})
    assert "已输入 5 个字符" in out
    assert turn.actions == 1
    assert "没有要输入" in control_tool.type_text.invoke({"text": "", "capture_id": 3})


# ── 坐标与白名单 ────────────────────────────────────────────────────
def test_to_physical_with_scale_and_offset():
    cu_mode.begin_turn(True, True)
    cu_mode.set_frame(1, 0.5056, (100, -50, 2880, 1620))
    assert control_tool._to_physical(0, 0) == (100, -50)
    x, y = control_tool._to_physical(506, 506)
    assert (x, y) == (100 + 1001, -50 + 1001)


def test_whitelist_parsing(monkeypatch):
    from utils import settings
    monkeypatch.setattr(settings, "get", lambda k, d=None: "Notepad.exe\n\n chrome \r\n")
    names = cu_input.whitelist()
    assert "notepad.exe" in names and "chrome.exe" in names


# ── 帧元数据由 screen_capture 登记 ──────────────────────────────────
def test_screen_capture_registers_frame(monkeypatch):
    import base64, io
    from PIL import Image
    from tools import screen_tool

    cu_mode.begin_turn(True, True)
    buf = io.BytesIO()
    Image.new("RGB", (64, 64), (10, 20, 30)).save(buf, "JPEG", quality=85)
    meta = {"monitor": 0, "screen_rect": [0, 0, 2880, 1620], "image_size": [1456, 819],
            "scale": 0.5056, "bytes": 1000, "sha256": "z"}
    monkeypatch.setattr(screen_tool, "_grab", lambda monitor=0: (buf.getvalue(), meta))
    screen_tool.screen_capture.invoke({"reason": "看"})
    cur = cu_mode.current()
    assert cur.last_cid == 1 and abs(cur.last_scale - 0.5056) < 1e-9
    assert tuple(cur.last_rect) == (0, 0, 2880, 1620)
