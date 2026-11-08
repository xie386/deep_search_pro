# -*- coding: utf-8 -*-
"""悬浮球的纯几何计算（零 Win32 依赖，可直接单测）。

口径（见 `docs/v4.1/Verdict/01-悬浮球外壳-对话入口.md` 第 3 条）：
**全流程逻辑像素**；需要物理像素时用实测倍率换算，绝不混用两套坐标。
"""
from __future__ import annotations

from typing import NamedTuple


class Rect(NamedTuple):
    x: int
    y: int
    w: int
    h: int

    @property
    def right(self) -> int:
        return self.x + self.w

    @property
    def bottom(self) -> int:
        return self.y + self.h

    @property
    def cx(self) -> int:
        return self.x + self.w // 2

    @property
    def cy(self) -> int:
        return self.y + self.h // 2


def scale_for_dpi(dpi: int) -> float:
    """96 DPI 为 1.0。"""
    return max(int(dpi or 96), 96) / 96.0


def logical_to_physical(v: float, scale: float) -> int:
    return int(round(v * scale))


def physical_to_logical(v: float, scale: float) -> int:
    return int(round(v / scale)) if scale else int(v)


def point_in_circle(lx: float, ly: float, size: int, inset: int = 1) -> bool:
    """圆心在方块正中、直径等于方块边长；inset 让边缘 1px 归到「圆外」。"""
    radius = max(size / 2.0 - inset, 1.0)
    c = size / 2.0
    return (lx - c) ** 2 + (ly - c) ** 2 <= radius * radius


def clamp_rect(rect: Rect, work: Rect) -> Rect:
    """把矩形夹进工作区（工作区比矩形还小时贴左上）。"""
    x = min(max(rect.x, work.x), max(work.x, work.right - rect.w))
    y = min(max(rect.y, work.y), max(work.y, work.bottom - rect.h))
    return Rect(x, y, rect.w, rect.h)


def default_ball_rect(size: int, work: Rect, margin: int = 24) -> Rect:
    """默认落点：工作区右下角，距边 margin。"""
    return clamp_rect(Rect(work.right - size - margin, work.bottom - size - margin, size, size), work)


def anchor_panel(ball: Rect, panel_w: int, panel_h: int, work: Rect, gap: int = 12):
    """面板优先在球的**朝屏幕内侧**一侧展开，垂直与球居中对齐。

    - 球在工作区左半 → 面板在球右侧；右半 → 左侧；
    - 该侧空间不够就翻到另一侧；仍不够就夹进工作区；
    - 返回 (面板矩形, 实际展开方向 "right"/"left")。
    """
    prefer_right = ball.cx <= work.cx
    side = "right" if prefer_right else "left"
    x = ball.right + gap if prefer_right else ball.x - gap - panel_w
    if x < work.x or x + panel_w > work.right:
        alt = ball.x - gap - panel_w if prefer_right else ball.right + gap
        if work.x <= alt and alt + panel_w <= work.right:
            x = alt
            side = "left" if prefer_right else "right"
    y = ball.cy - panel_h // 2
    return clamp_rect(Rect(x, y, panel_w, panel_h), work), side


def move_rect(rect: Rect, dx: int, dy: int, work: Rect | None = None) -> Rect:
    moved = Rect(rect.x + dx, rect.y + dy, rect.w, rect.h)
    return clamp_rect(moved, work) if work is not None else moved
