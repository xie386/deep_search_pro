# -*- coding: utf-8 -*-
"""M2-2 用例：MCP 驱动（`tools/_runtime/mcp_driver.py`）。**全离线**，不联网、不烧额度。

覆盖：
  - **进程内** transport：list / call / 结构化结果 / 写工具无只读提示
  - **stdio**：本地小脚本 server（真起子进程）→ list / call
  - 安全与配置：命令白名单拒绝 / transport 校验 / http url 校验 / 进程内必须带 server
  - 结果上限：超大返回被裁到 6000
  - ★ **惰性导入**：`import tools._runtime.mcp_driver` 不得把 `mcp` 拉进来（否则应用启动白 +5s）

运行：
    .venv/Scripts/python.exe -m pytest tests/test_mcp_driver.py -q
"""
import json
import os
import subprocess
import sys
import textwrap
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from tools._runtime import mcp_driver as drv  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def make_server(big_tool=False):
    """一个含"1 只读 + 1 写 +（可选）1 个超大返回"工具的进程内 server。"""
    from mcp.server import MCPServer
    srv = MCPServer("demo")

    @srv.tool(description="查天气（只读）", annotations={"readOnlyHint": True})
    def get_weather(city: str) -> dict:
        return {"city": city, "temp": 21}

    @srv.tool(description="下单（写操作）")
    def place_order(sku: str, qty: int = 1) -> dict:
        return {"ok": True, "sku": sku, "qty": qty}

    if big_tool:
        @srv.tool(description="超大返回")
        def huge() -> dict:
            return {"blob": "x" * 20000}
    return srv


def inproc(**over):
    cfg = {"transport": "inproc", "_server": make_server(big_tool=over.pop("big", False))}
    cfg.update(over)
    return cfg


# ---------------------------------------------------------------- 进程内
def test_list_tools_inproc():
    out = drv.list_tools(inproc())
    assert out["ok"] is True and out["ms"] >= 0
    names = [t["name"] for t in out["tools"]]
    assert names == ["get_weather", "place_order"]
    w = out["tools"][0]
    assert w["description"] == "查天气（只读）"
    assert w["input_schema"]["required"] == ["city"]
    assert w["input_schema"]["properties"]["city"]["type"] == "string"
    assert w["read_only_hint"] is True


def test_call_tool_returns_structured():
    out = drv.call_tool(inproc(), "get_weather", {"city": "成都"})
    assert out["ok"] is True and out["status"] == 200
    assert out["json"] == {"city": "成都", "temp": 21} or '"temp": 21' in out["text"]
    assert out["ms"] >= 0 and out["error"] == ""


def test_call_tool_passes_arguments():
    out = drv.call_tool(inproc(), "place_order", {"sku": "A-1", "qty": 3})
    assert out["ok"] is True and "A-1" in out["text"]


def test_write_tool_has_no_readonly_hint():
    """没有 annotations 的工具 → `read_only_hint is None`（**不能**当成只读）。

    MCP 规范明说 annotations 只是提示、客户端不得据此做安全决策；这里保留 None 让确认界面
    按"未知 = 需要人工确认"处理。
    """
    out = drv.list_tools(inproc())
    by = {t["name"]: t for t in out["tools"]}
    assert by["place_order"]["read_only_hint"] is None
    assert by["get_weather"]["read_only_hint"] is True


def test_result_is_capped():
    out = drv.call_tool(inproc(big=True), "huge", {})
    assert out["ok"] is True
    assert len(out["text"]) <= drv.MAX_RESULT_CHARS


def test_unknown_tool_is_reported_not_crashed():
    out = drv.call_tool(inproc(), "no_such_tool", {})
    assert out["ok"] is False and out["error"]


# ---------------------------------------------------------------- stdio（真起子进程）
STDIO_CODE = textwrap.dedent('''
    from mcp.server import MCPServer
    srv = MCPServer("stdio-demo")

    @srv.tool(description="回显（只读）", annotations={"readOnlyHint": True})
    def echo(text: str) -> dict:
        return {"echo": text}

    if __name__ == "__main__":
        srv.run()
''')


@pytest.fixture(scope="module")
def stdio_script(tmp_path_factory):
    p = tmp_path_factory.mktemp("mcp") / "server.py"
    p.write_text(STDIO_CODE, encoding="utf-8")
    return str(p)


def test_stdio_script_server(stdio_script):
    """真起一个 stdio 子进程 server：list + call 都要通。

    ★ 超时给足（60s）：子进程首先要 `import mcp`（实测 **5s** 上下，冷启动更久）——
    实测 15s 会失败、20s 起才稳，所以来源的 timeout 默认给 30s 是合理的，测试里更宽松。
    """
    cfg = {"transport": "stdio", "command": sys.executable, "args": [stdio_script],
           "timeout": 60, "allow_stdio_commands": [os.path.basename(sys.executable)]}
    out = drv.list_tools(cfg)
    assert out["ok"] is True, out["error"]
    assert [t["name"] for t in out["tools"]] == ["echo"]
    r = drv.call_tool(cfg, "echo", {"text": "hello-stdio"})
    assert r["ok"] is True and "hello-stdio" in r["text"], r


def test_stdio_refuses_unlisted_command():
    """安全边界（方案 §4.5 裁决 B）：命令不在来源白名单里 → 拒绝，且**不启动进程**。"""
    cfg = {"transport": "stdio", "command": "evil-binary", "args": [], "allow_stdio_commands": ["npx"]}
    out = drv.list_tools(cfg)
    assert out["ok"] is False and "白名单" in out["error"]
    r = drv.call_tool(cfg, "anything", {})
    assert r["ok"] is False and "白名单" in r["error"]


# ---------------------------------------------------------------- 配置校验 / 纯函数
def test_validate_cfg_steps():
    steps = drv.validate_cfg({"transport": "stdio", "command": "npx", "args": ["-y", "x"],
                              "allow_stdio_commands": ["npx"], "timeout": 30})
    assert all(s["ok"] for s in steps), steps
    bad = drv.validate_cfg({"transport": "stdio", "command": "npx", "args": ["-y"],
                            "allow_stdio_commands": ["uvx"]})
    assert [s for s in bad if not s["ok"]][0]["name"] == "命令在白名单内"
    assert drv.validate_cfg({})[0]["ok"] is False                     # transport 必填
    u = drv.validate_cfg({"transport": "http", "url": "ftp://x"})
    assert [s for s in u if not s["ok"]][0]["name"] == "url 合法"
    priv = drv.validate_cfg({"transport": "http", "url": "http://127.0.0.1:8000/mcp", "allow_private": False})
    assert any(s["name"] == "允许内网地址" and not s["ok"] for s in priv)


def test_params_errors_are_readable():
    with pytest.raises(drv.McpDriverError):
        drv.client_param({"transport": "stdio"}, 30)                        # 缺 command
    with pytest.raises(drv.McpDriverError):
        drv.client_param({"transport": "sse", "url": "http://x"}, 30)       # 不支持的 transport
    with pytest.raises(drv.McpDriverError):
        drv.client_param({"transport": "http", "url": "not-a-url"}, 30)
    out = drv.call_tool({"transport": "inproc"}, "t", {})               # 进程内缺 server
    assert out["ok"] is False and "inproc" in out["error"]
    assert drv.call_tool(inproc(), "", {})["ok"] is False               # 工具名空


def test_timeout_is_clamped():
    """`0` / 非数字 = **未配置** → 用默认值（不是"1 秒后超时"）；上限 600。"""
    assert drv._timeout({"timeout": 0}) == drv.DEFAULT_TIMEOUT
    assert drv._timeout({"timeout": -5}) == drv.DEFAULT_TIMEOUT
    assert drv._timeout({}) == drv.DEFAULT_TIMEOUT
    assert drv._timeout({"timeout": "abc"}) == drv.DEFAULT_TIMEOUT
    assert drv._timeout({"timeout": 0.2}) == 1.0
    assert drv._timeout({"timeout": 9999}) == 600.0
    assert drv._timeout({"timeout": 60}, 45) == 45


# ---------------------------------------------------------------- ★ 惰性导入（启动耗时）
def test_模块导入不拉起_mcp():
    """`import tools._runtime.mcp_driver` 不得把 `mcp` 拉进 sys.modules。

    实测 `import mcp` = **5.0s**（它带 httpx2/opentelemetry/jsonschema）。驱动必须在**调用时**才 import，
    否则每次启动都白付这 5 秒 —— 这条断言就是那个约定（方案 §M2-1 的"不影响既有启动耗时"）。
    """
    code = ("import sys; sys.path.insert(0, r'%s'); import tools._runtime.mcp_driver as d;"
            " print('mcp' in sys.modules, d.MAX_RESULT_CHARS)" % ROOT)
    p = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, cwd=ROOT, timeout=120)
    assert p.returncode == 0, p.stderr[-400:]
    assert p.stdout.strip() == "False 6000", p.stdout


def test_应用启动路径也不拉起_mcp():
    """`import api.server` 同样不得拉起 mcp（启动路径上真会付 5s 的就这一处）。"""
    code = ("import sys; sys.path.insert(0, r'%s'); import api.server;"
            " print('mcp' in sys.modules)" % ROOT)
    p = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, cwd=ROOT, timeout=180)
    assert p.returncode == 0, p.stderr[-400:]
    assert p.stdout.strip() == "False", p.stdout
