# -*- coding: utf-8 -*-
"""悬浮球几何的纯函数单测（零窗口、秒级）。"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "front" / "desktop"))

import ball_geometry as g  # noqa: E402


def test_point_in_circle_corners_outside_center_inside():
    size = 64
    assert g.point_in_circle(32, 32, size) is True
    assert g.point_in_circle(2, 2, size) is False
    assert g.point_in_circle(62, 2, size) is False
    assert g.point_in_circle(2, 62, size) is False


def test_point_in_circle_edge_is_outside_due_to_inset():
    # inset=1 让半径收 1px：最外一圈像素（x=0）算圆外，往里一格算圆内
    assert g.point_in_circle(0, 32, 64) is False
    assert g.point_in_circle(2, 32, 64) is True


@pytest.mark.parametrize("dpi,scale", [(96, 1.0), (120, 1.25), (144, 1.5), (192, 2.0), (0, 1.0)])
def test_scale_for_dpi(dpi, scale):
    assert g.scale_for_dpi(dpi) == scale


def test_logical_physical_roundtrip():
    assert g.logical_to_physical(40, 2.0) == 80
    assert g.physical_to_logical(80, 2.0) == 40
    assert g.logical_to_physical(64, 1.5) == 96


def test_clamp_rect_keeps_inside_work_area():
    work = g.Rect(0, 0, 1920, 1040)
    r = g.clamp_rect(g.Rect(1900, 1000, 64, 64), work)
    assert (r.x, r.y) == (1856, 976)
    r2 = g.clamp_rect(g.Rect(-50, -50, 64, 64), work)
    assert (r2.x, r2.y) == (0, 0)


def test_default_ball_rect_bottom_right_margin():
    work = g.Rect(0, 0, 1920, 1040)
    r = g.default_ball_rect(64, work, margin=24)
    assert (r.x, r.y, r.w, r.h) == (1832, 952, 64, 64)


def test_anchor_panel_prefers_inner_side():
    work = g.Rect(0, 0, 1920, 1040)
    # 球在左侧 → 面板在右
    left_ball = g.Rect(24, 900, 64, 64)
    rect, side = g.anchor_panel(left_ball, 380, 560, work)
    assert side == "right"
    assert rect.x == left_ball.right + 12
    # 球在右侧 → 面板在左
    right_ball = g.Rect(1830, 900, 64, 64)
    rect2, side2 = g.anchor_panel(right_ball, 380, 560, work)
    assert side2 == "left"
    assert rect2.x == right_ball.x - 12 - 380


def test_anchor_panel_vertical_center_and_clamp():
    work = g.Rect(0, 0, 1920, 1040)
    ball = g.Rect(24, 10, 64, 64)          # 贴顶 → 面板要往下夹
    rect, _ = g.anchor_panel(ball, 380, 560, work)
    assert rect.y >= work.y
    assert rect.y + rect.h <= work.bottom


def test_anchor_panel_flips_when_no_room():
    # 窄工作区：右侧放不下 → 翻到左侧
    work = g.Rect(0, 0, 500, 800)
    ball = g.Rect(300, 400, 64, 64)
    rect, side = g.anchor_panel(ball, 380, 560, work)
    assert side == "left"
    assert rect.x >= work.x and rect.x + rect.w <= work.right


def test_move_rect_clamped():
    work = g.Rect(0, 0, 800, 600)
    r = g.move_rect(g.Rect(700, 500, 64, 64), 200, 200, work)
    assert (r.x, r.y) == (736, 536)


def test_parse_hotkey():
    from ball_window import parse_hotkey
    assert parse_hotkey("Ctrl+Alt+Space") == (0x0002 | 0x0001, 0x20)
    assert parse_hotkey("ctrl+shift+f5") == (0x0002 | 0x0004, 0x74)
    # 非法输入退回默认
    assert parse_hotkey("") == (0x0002 | 0x0001, 0x20)
    assert parse_hotkey("space") == (0x0002 | 0x0001, 0x20)
