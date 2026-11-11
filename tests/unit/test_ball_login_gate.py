"""登录态闸门：外壳必须先问后端「这个 token 认不认」，才允许球出现。

背景（2026-11-11 真机）：后端重启后旧 token 失效，页面 localStorage 里还留着 token，
外壳只看「token 非空」就把球放出来 —— 登录页上挂着一个球，点开是白板浮窗。
"""
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "front" / "desktop") not in sys.path:
    sys.path.insert(0, str(ROOT / "front" / "desktop"))


@pytest.fixture()
def appmod():
    import app  # noqa: PLC0415  (延迟导入：模块顶层会做路径准备)
    return app


def _serve(status: int, body: dict | None = None):
    class H(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            payload = json.dumps(body or {}).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *a):  # 静音
            pass

    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def test_token_rejected_when_backend_says_401(appmod):
    srv = _serve(401)
    try:
        appmod._ACTIVE_PORT = srv.server_address[1]
        assert appmod._verify_token("stale-token") is False
    finally:
        srv.shutdown()


def test_token_ok_when_backend_returns_200(appmod):
    srv = _serve(200, {"ok": True})
    try:
        appmod._ACTIVE_PORT = srv.server_address[1]
        assert appmod._verify_token("good-token") is True
    finally:
        srv.shutdown()


def test_network_error_does_not_kick_login(appmod):
    """后端没起来/端口不通时不能把登录态踢掉（和前端 verifySession 口径一致）。"""
    appmod._ACTIVE_PORT = 1          # 必然连不上
    assert appmod._verify_token("whatever") is True
