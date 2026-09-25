# -*- coding: utf-8 -*-
"""单文件 SPA 的 **HTML 入口绝不能缓存**（2026-09-25 桌面端报障固化）。

背景：桌面壳是 pywebview + `private_mode=False` + 固定 `storage_path`（"记住登录"要求这么设），
WebView2 因此**持久化 HTTP 缓存**；后端原先对 `index.html` 只发 `Last-Modified`/`ETag`、没有
`Cache-Control`，Chromium 便按启发式把它当成"还新鲜"，直接把旧 HTML 交给窗口 ——
用户看到的现象是「同一次前端改动，网页端刷新可见、桌面端连重启都看不到」。
实测缓存里那份 HTML 停在两次改动之间（有 015 表单样式、没有当时的「阅读」按钮），
时间线上窗口加载（13:10）明明晚于文件写入（13:01），所以**不是代码没生效、是缓存**。

修法 = 两道保险：
  ① 后端中间件给 `/` 与 `*.html` 打 `Cache-Control: no-store`（本文件验的就是它）；
  ② 桌面壳加载 URL 加一次性 nonce（`&_=<时间戳>`，见 tests/desktop_shell_e2e.py 的静态断言）。

运行：`.venv/Scripts/python.exe -m pytest tests/test_spa_cache_headers.py -q`
"""
import pytest
from fastapi.testclient import TestClient

import api.server as S

client = TestClient(S.app)


def test_root_html_is_never_cached():
    r = client.get("/")
    assert r.status_code == 200
    cc = r.headers.get("cache-control", "")
    assert "no-store" in cc, "HTML 入口必须禁止缓存，否则桌面 WebView2 会喂旧页面：" + cc
    assert "no-cache" in cc and "must-revalidate" in cc
    assert r.headers.get("pragma") == "no-cache"      # 老代理兜底
    assert "text/html" in r.headers.get("content-type", "")


def test_html_via_static_mount_also_no_store():
    r = client.get("/static/index.html")
    assert r.status_code == 200
    assert "no-store" in r.headers.get("cache-control", ""), r.headers.get("cache-control")


def test_non_html_assets_are_not_over_applied():
    """别把 no-store 糊到所有资源上：图片/静态 vendor 仍应可缓存（这些文件改了就是新 URL/新名字）。"""
    r = client.get("/static/desktop/assets/icon.png")
    assert r.status_code == 200
    assert "no-store" not in r.headers.get("cache-control", ""), \
        "静态图片不该被 no-store（过度应用会让每次开页面都重下资源）"


def test_json_api_is_not_over_applied():
    """API 响应也不该被打上 no-store —— 只处理 HTML 入口。"""
    r = client.get("/api/reports", params={"token": "bogus-token"})
    assert r.status_code in (401, 403)
    assert "no-store" not in r.headers.get("cache-control", ""), r.headers.get("cache-control")


def test_head_on_root_is_405_but_static_html_supports_head():
    """实测事实：FastAPI 的 `@app.get("/")` **不自动支持 HEAD**（返回 405）——钉住它，
    免得以后有人以为"HEAD 也能拿到 no-store"。而 StaticFiles 挂的 `/static/index.html` 支持 HEAD
    （代理/客户端有时用 HEAD 探新鲜度），那条路径同样要带 no-store。"""
    assert client.head("/").status_code == 405
    r = client.head("/static/index.html")
    assert r.status_code == 200
    assert "no-store" in r.headers.get("cache-control", ""), r.headers.get("cache-control")
