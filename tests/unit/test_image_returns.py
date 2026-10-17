# -*- coding: utf-8 -*-
"""接口返回图片 → 落到工作目录 `_tool_images/`（2026-10-18 用户报障）。"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))


def test_save_tool_image_writes_into_workdir(monkeypatch, tmp_path):
    from tools import capability_invoke as ci
    from tools._runtime import shell_runtime as sh

    monkeypatch.setattr(sh, "active_workdir", lambda *a, **k: tmp_path)
    png = b"\x89PNG\r\n\x1a\n" + b"\x01" * 16
    msg = ci._save_tool_image(3, "api:img/gen#avatar", png, "image/png")

    assert "[[img:_tool_images/" in msg
    rel = msg.split("[[img:", 1)[1].split("]]", 1)[0]
    f = tmp_path / rel
    assert f.is_file() and f.read_bytes() == png
    assert f.suffix == ".png" and f.parent.name == "_tool_images"


def test_save_tool_image_never_raises(monkeypatch):
    from tools import capability_invoke as ci
    from tools._runtime import shell_runtime as sh

    def _boom(*a, **k):
        raise RuntimeError("工作目录炸了")

    monkeypatch.setattr(sh, "active_workdir", _boom)
    msg = ci._save_tool_image(3, "x", b"abc", "image/png")
    assert "落盘失败" in msg
