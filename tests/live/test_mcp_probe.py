# -*- coding: utf-8 -*-
"""M2-3 用例：MCP 来源七步体检（`tools/mcp_probe.py`）。**全离线**。

四条路径（方案 §M2-3 要求）：① 正常 ② 命令不存在 ③ 协议不兼容（假 server 满口胡话）
④ 超时（假 server 只睡不说话）—— 重点是 ③④ **必须快速失败**，不能把请求挂到天荒地老。

运行：
    .venv/Scripts/python.exe -m pytest tests/test_mcp_probe.py -q
"""
import os
import sys
import textwrap
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from tools import mcp_probe  # noqa: E402


def make_server():
    from mcp.server import MCPServer
    srv = MCPServer("probe-demo")

    @srv.tool(description="查天气（只读）", annotations={"readOnlyHint": True})
    def get_weather(city: str) -> dict:
        return {"city": city, "temp": 21}

    @srv.tool(description="下单（写）")
    def place_order(sku: str, qty: int = 1) -> dict:
        return {"ok": True, "sku": sku, "qty": qty}
    return srv


def steps_by_name(res):
    return {s["name"]: s for s in res["steps"]}


# ---------------------------------------------------------------- ① 正常
def test_probe_inproc_all_seven_steps_pass():
    res = mcp_probe.probe({"transport": "inproc", "_server": make_server()})
    assert res["ok"] is True, res["steps"]
    names = [s["name"] for s in res["steps"]]
    assert names == ["① 配置解析", "② 环境检测", "③ 协议握手", "④ tools/list", "⑤ 异常容错",
                     "⑥ 性能指标", "⑦ 汇总"], names
    assert all(s["ok"] for s in res["steps"])
    assert [t["name"] for t in res["tools"]] == ["get_weather", "place_order"]
    by = steps_by_name(res)
    assert "2 个工具" in by["④ tools/list"]["detail"]
    assert by["③ 协议握手"]["ms"] >= 0 and by["④ tools/list"]["ms"] >= 0
    # 汇总要把"未标记只读"的数量讲清楚（写工具没 annotations → None）
    assert "明确只读 1" in by["⑦ 汇总"]["detail"] and "未标记 1" in by["⑦ 汇总"]["detail"]


def test_probe_toleration_step_reports_clean_rejection():
    """⑤ 异常容错：进程内 server 对未知工具会干净拒绝 → 该步通过且写明原因。"""
    res = mcp_probe.probe({"transport": "inproc", "_server": make_server()})
    s = steps_by_name(res)["⑤ 异常容错"]
    assert s["ok"] is True and s["detail"]


def test_probe_stops_at_config_step():
    """配置不合法 → 第 ① 步就失败收摊（不去连、不浪费超时）。"""
    res = mcp_probe.probe({"transport": "stdio", "command": "npx", "allow_stdio_commands": ["uvx"]})
    assert res["ok"] is False and len(res["steps"]) == 1
    assert res["steps"][0]["name"] == "① 配置解析" and "白名单" in res["steps"][0]["detail"]


# ---------------------------------------------------------------- ② 命令不存在
def test_probe_missing_command(tmp_path):
    res = mcp_probe.probe({"transport": "stdio", "command": "definitely-not-a-real-command-xyz",
                           "args": [], "timeout": 10,
                           "allow_stdio_commands": ["definitely-not-a-real-command-xyz"]})
    by = steps_by_name(res)
    assert res["ok"] is False
    assert by["① 配置解析"]["ok"] is True          # 配置本身合法（命令名在白名单里）
    assert by["② 环境检测"]["ok"] is False and "找不到" in by["② 环境检测"]["detail"]
    assert "③ 协议握手" not in by                  # 没往下走
    assert res["ms"] < 5000


def test_probe_missing_cwd(tmp_path):
    cfg = {"transport": "stdio", "command": sys.executable.replace("\\", "/"), "args": [],
           "cwd": str(tmp_path / "no-such-dir"), "timeout": 10,
           "allow_stdio_commands": [os.path.basename(sys.executable)]}
    res = mcp_probe.probe(cfg)
    assert steps_by_name(res)["② 环境检测"]["ok"] is False


# ---------------------------------------------------------------- ③ 协议不兼容 / ④ 超时（都必须快速失败）
BAD_SCRIPT = textwrap.dedent('''
    import sys, time
    sys.stdout.write("我不是 MCP 服务，我只会说话\\n")
    sys.stdout.flush()
    time.sleep(0.5)          # 然后闭嘴（不退出，也不说话）
''')


@pytest.fixture(scope="module")
def bad_script(tmp_path_factory):
    p = tmp_path_factory.mktemp("badmcp") / "bad.py"
    p.write_text(BAD_SCRIPT, encoding="utf-8")
    return str(p)


def test_probe_protocol_incompatible_fails_fast(bad_script):
    """满口胡话的"server"：握手必须失败，且**不拖满超时**（这里给 8s 超时，理应几秒内收摊）。"""
    t0 = time.time()
    res = mcp_probe.probe({"transport": "stdio", "command": sys.executable, "args": [bad_script],
                           "timeout": 8, "allow_stdio_commands": [os.path.basename(sys.executable)]})
    el = time.time() - t0
    by = steps_by_name(res)
    assert res["ok"] is False and by["③ 协议握手"]["ok"] is False
    assert by["③ 协议握手"]["detail"]
    assert el < 30, "不该拖这么久：%.1fs" % el


SILENT_SCRIPT = textwrap.dedent('''
    import time
    time.sleep(300)          # 只睡，不吭声 —— 握不上手
''')


@pytest.fixture(scope="module")
def silent_script(tmp_path_factory):
    p = tmp_path_factory.mktemp("silentmcp") / "silent.py"
    p.write_text(SILENT_SCRIPT, encoding="utf-8")
    return str(p)


def test_probe_timeout_is_bounded(silent_script):
    """装死 300s 的 server + 5s 超时 → 必须在超时附近失败返回（不是 300s）。"""
    t0 = time.time()
    res = mcp_probe.probe({"transport": "stdio", "command": sys.executable, "args": [silent_script],
                           "timeout": 5, "allow_stdio_commands": [os.path.basename(sys.executable)]})
    el = time.time() - t0
    assert res["ok"] is False and "③ 协议握手" in steps_by_name(res)
    assert steps_by_name(res)["③ 协议握手"]["ok"] is False
    assert el < 45, "超时没生效：%.1fs" % el


# ---------------------------------------------------------------- http 分支（不打真网络：只验"连不上"）
def test_probe_http_unreachable_port():
    res = mcp_probe.probe({"transport": "http", "url": "http://127.0.0.1:9/mcp", "timeout": 5})
    by = steps_by_name(res)
    assert res["ok"] is False and by["② 环境检测"]["ok"] is False
    assert "连不上" in by["② 环境检测"]["detail"]


def test_probe_http_bad_url_stops_early():
    res = mcp_probe.probe({"transport": "http", "url": "ftp://example.com"})
    assert res["ok"] is False and len(res["steps"]) == 1


# ---------------------------------------------------------------- ⑥ 证：stdio 起不来时要看到子进程真正的报错
CRASH_SCRIPT = textwrap.dedent('''
    import sys
    sys.stderr.write("ModuleNotFoundError: No module named 'mcp.server.fastmcp'\\n")
    sys.stderr.write("（提示：This is mcp 2.x, pin mcp<2）\\n")
    sys.exit(1)
''')


@pytest.fixture(scope="module")
def crash_script(tmp_path_factory):
    p = tmp_path_factory.mktemp("crashmcp") / "crash.py"
    p.write_text(CRASH_SCRIPT, encoding="utf-8")
    return str(p)


def test_stdio_failure_surfaces_child_stderr(crash_script):
    """★ 子进程一启动就崩时，③ 步必须把**子进程 stderr 的尾巴**带出来。

    真机踩过：某只老 MCP 服务因为它自己的 requirements 钉得太松（装成 mcp 2.x）直接崩，
    SDK 只报 `ExceptionGroup: unhandled errors in a TaskGroup` —— 真正的原因全在子进程 stderr 里，
    没有这一步取证就只能靠猜。所以这里钉住:失败详情里必须出现那句真实报错。
    """
    res = mcp_probe.probe({"transport": "stdio", "command": sys.executable, "args": [crash_script],
                           "timeout": 8, "allow_stdio_commands": [os.path.basename(sys.executable)]})
    by = steps_by_name(res)
    assert res["ok"] is False and by["③ 协议握手"]["ok"] is False
    detail = by["③ 协议握手"]["detail"]
    assert "ModuleNotFoundError" in detail and "mcp.server.fastmcp" in detail, detail
    assert "mcp<2" in detail, detail


def test_stdio_failure_without_output_says_so(tmp_path):
    """子进程一声不吭就退出（没有输出）→ 也要说清楚，不能只留一句 ExceptionGroup。"""
    p = tmp_path / "mute.py"
    p.write_text("import sys\nsys.exit(3)\n", encoding="utf-8")
    res = mcp_probe.probe({"transport": "stdio", "command": sys.executable, "args": [str(p)],
                           "timeout": 8, "allow_stdio_commands": [os.path.basename(sys.executable)]})
    detail = steps_by_name(res)["③ 协议握手"]["detail"]
    assert "子进程" in detail, detail
