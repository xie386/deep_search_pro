# -*- coding: utf-8 -*-
"""v4.1-02：屏幕共享工具的闸门（开关 / 张数 / 同屏去重 / 压缩口径）。"""
import base64, io
import pytest
from PIL import Image

from tools import screen_tool
from utils import cu_mode


def _jpeg(color=(220, 20, 40)):
    im = Image.new("RGB", (64, 64), color)
    buf = io.BytesIO(); im.save(buf, "JPEG", quality=85)
    return buf.getvalue()


def _meta(sha="h1"):
    return {"monitor": 0, "screen_rect": [0, 0, 2880, 1620], "image_size": [1456, 819],
            "scale": 0.5056, "bytes": 200000, "sha256": sha}


@pytest.fixture(autouse=True)
def _clean():
    cu_mode.end_turn()
    yield
    cu_mode.end_turn()


def test_off_refuses():
    cu_mode.begin_turn(False)
    out = screen_tool.screen_capture.invoke({"reason": "test"})
    assert isinstance(out, str) and "没有开启" in out


def test_on_returns_text_and_image(monkeypatch):
    cu_mode.begin_turn(True)
    monkeypatch.setattr(screen_tool, "_grab", lambda monitor=0: (_jpeg(), _meta("a")))
    out = screen_tool.screen_capture.invoke({"reason": "看报错"})
    assert isinstance(out, list) and len(out) == 2
    assert out[0]["type"] == "text" and "capture #1" in out[0]["text"]
    assert out[1]["type"] == "image_url"
    assert out[1]["image_url"]["url"].startswith("data:image/jpeg;base64,")
    assert cu_mode.current().shots == 1


def test_same_frame_is_deduped(monkeypatch):
    cu_mode.begin_turn(True)
    monkeypatch.setattr(screen_tool, "_grab", lambda monitor=0: (_jpeg(), _meta("same")))
    first = screen_tool.screen_capture.invoke({"reason": "a"})
    second = screen_tool.screen_capture.invoke({"reason": "b"})
    assert isinstance(first, list)
    assert isinstance(second, str) and "完全相同" in second
    assert cu_mode.current().shots == 2          # 计数照加，但不重复送图


def test_limit_refuses(monkeypatch):
    cu_mode.begin_turn(True)
    monkeypatch.setattr(screen_tool, "_max_shots", lambda: 1)
    monkeypatch.setattr(screen_tool, "_grab", lambda monitor=0: (_jpeg(), _meta("x")))
    assert isinstance(screen_tool.screen_capture.invoke({"reason": "1"}), list)
    out = screen_tool.screen_capture.invoke({"reason": "2"})
    assert isinstance(out, str) and "次数已用完" in out


def test_missing_mss(monkeypatch):
    cu_mode.begin_turn(True)

    def boom(monitor=0):
        raise ImportError("no mss")

    monkeypatch.setattr(screen_tool, "_grab", boom)
    out = screen_tool.screen_capture.invoke({"reason": "x"})
    assert isinstance(out, str) and "mss" in out


def test_zoom_region_maps_to_physical_and_skips_quota(monkeypatch):
    """局部放大：图坐标 → 物理坐标换算正确；不占截图额度，另计 zooms。"""
    turn = cu_mode.begin_turn(True, True)
    turn.last_cid, turn.last_scale, turn.last_rect = 1, 0.5, (0, 0, 2880, 1620)
    seen = {}

    def fake_grab(monitor=0, region=None):
        seen["region"] = region
        return _jpeg(), {"monitor": 0, "screen_rect": [200, 200, 120, 120],
                         "image_size": [360, 360], "scale": 3.0, "zoom": 3.0,
                         "bytes": 1000, "sha256": "z"}

    monkeypatch.setattr(screen_tool, "_grab", fake_grab)
    out = screen_tool.screen_capture.invoke({"reason": "看小图标", "region": [100, 100, 60, 60]})
    assert isinstance(out, list) and len(out) == 2
    assert seen["region"] == (200, 200, 120, 120)        # 图上 ÷ 0.5 → 物理
    assert "已放大第 1 块" in out[0]["text"] and "capture #2" in out[0]["text"]
    assert turn.shots == 0 and turn.zooms == 1           # ★ 不占截图额度
    assert turn.last_cid == 2 and turn.last_scale == 3.0  # 后续点击以放大帧为准


def test_zoom_requires_prior_frame(monkeypatch):
    cu_mode.begin_turn(True, True)
    out = screen_tool.screen_capture.invoke({"reason": "x", "region": [1, 1, 40, 40]})
    assert isinstance(out, str) and "先调用" in out


def test_zoom_cap(monkeypatch):
    turn = cu_mode.begin_turn(True, True)
    turn.last_cid, turn.last_scale, turn.last_rect = 1, 1.0, (0, 0, 100, 100)
    monkeypatch.setattr(screen_tool, "_max_zooms", lambda: 1)
    monkeypatch.setattr(screen_tool, "_grab", lambda monitor=0, region=None: (_jpeg(), _meta("a")))
    assert isinstance(screen_tool.screen_capture.invoke({"reason": "a", "region": [0, 0, 40, 40]}), list)
    out = screen_tool.screen_capture.invoke({"reason": "b", "region": [0, 0, 40, 40]})
    assert isinstance(out, str) and "放大次数已用完" in out


def test_zoom_rejects_tiny_region(monkeypatch):
    turn = cu_mode.begin_turn(True, True)
    turn.last_cid, turn.last_scale, turn.last_rect = 1, 1.0, (0, 0, 100, 100)
    out = screen_tool.screen_capture.invoke({"reason": "x", "region": [0, 0, 8, 8]})
    assert isinstance(out, str) and "太小" in out


def test_edge_and_quality_clamped(monkeypatch):
    from utils import settings
    monkeypatch.setattr(settings, "get", lambda k, d=None: {"SCREEN_SHOT_EDGE": "99999",
                                                           "SCREEN_SHOT_QUALITY": "1"}.get(k, d))
    assert screen_tool._edge() == 2048
    assert screen_tool._quality() == 50
