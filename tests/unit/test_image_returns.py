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


# ---------------------------------------------- 正文里出现图片地址（2026-10-18 用户提出）
def test_extract_image_urls_from_json_body():
    """图片类 API 多数返回的是**地址**：正文里要能挑出来，才谈得上下载。"""
    from tools import capability_invoke as ci
    body = ('{"code":200,"msg":"ok","data":"https://cdn.example.com/a/ZEaa0Q.jpg"}')
    assert ci._extract_image_urls(body) == ["https://cdn.example.com/a/ZEaa0Q.jpg"]


def test_extract_image_urls_dedup_and_limit():
    from tools import capability_invoke as ci
    u = "https://x.example.com/a.png"
    txt = " ".join([u, u, "https://x.example.com/b.webp?v=1", "https://x.example.com/c.gif",
                    "https://x.example.com/d.jpg"])
    got = ci._extract_image_urls(txt, limit=3)
    assert got[0] == u and len(got) == 3
    assert "https://x.example.com/b.webp?v=1" in got


def test_extract_image_urls_ignores_non_images():
    from tools import capability_invoke as ci
    assert ci._extract_image_urls("https://api.example.com/v1/json 和 https://a.com/x.html") == []


def test_private_hosts_are_refused_by_default():
    """SSRF 兜底：内网/回环不下（来源明确 allow_private 时才放行）。"""
    from tools import capability_invoke as ci
    assert ci._is_private_host("localhost") is True
    assert ci._is_private_host("127.0.0.1") is True
    assert ci._is_private_host("192.168.1.9") is True
    assert ci._is_private_host("10.0.0.7") is True
    assert ci._is_private_host("") is True
