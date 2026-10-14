# -*- coding: utf-8 -*-
"""启动冒烟（04 补 · E6 事故的回归闸，2026-10-14）。

**它防什么**：`api/server.py` 的 `@app.on_event("startup")` 钩子**只在 lifespan 真正启动时执行** ——
模块照样 import 成功、`tests/unit` 全绿，但钩子里一个没导入的名字（实测：`logging`）会让**服务直接起不来**：
    NameError: name 'logging' is not defined
    ERROR:    Application startup failed. Exiting.

所以：**动过 startup 段就必须有一步真把 app 跑起来的验证**。本用例进 `TestClient` 的上下文管理器
（进 = 跑 startup，出 = 跑 shutdown），启动异常会在这里直接冒出来，而不是等到用户重启服务。

运行：.venv/Scripts/python.exe -m pytest tests/testclient/test_app_startup_smoke.py -q
"""
import ast
import builtins
import logging
import os
import sys
from pathlib import Path

from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

SERVER_PY = Path(__file__).resolve().parents[2] / "api" / "server.py"


def _import_names(node) -> set:
    """一个 import 节点绑定了哪些名字（`import a.b` 绑 `a`；`as x` 绑 `x`）。"""
    if isinstance(node, ast.Import):
        return {(a.asname or a.name.split(".")[0]) for a in node.names}
    if isinstance(node, ast.ImportFrom):
        return {(a.asname or a.name) for a in node.names}
    return set()


def test_app_startup_runs_without_error(caplog):
    from api.server import app

    with caplog.at_level(logging.INFO, logger="uvicorn.error"):
        with TestClient(app) as client:          # ★ 进 with = 跑 startup；抛异常即失败
            assert client.get("/docs").status_code == 200      # 服务活着
    msgs = [rec.getMessage() for rec in caplog.records]
    assert any("配置自检" in m for m in msgs), "启动段没跑到配置自检（钩子可能没执行）"


def test_startup_hook_reports_missing_names_early():
    """回归：把 startup 钩子里用到的名字逐个在模块命名空间里查一遍。

    比上面的冒烟更快、更直接 —— 未导入的名字（ModuleNotFoundError 之外的 NameError 类）在这里就暴露。
    """
    tree = ast.parse(SERVER_PY.read_text(encoding="utf-8"), str(SERVER_PY))
    hook = next((n for n in ast.walk(tree)
                 if isinstance(n, ast.AsyncFunctionDef) and n.name == "_startup"), None)
    assert hook is not None, "api/server.py 里找不到 _startup 钩子（测试需同步）"
    bound: set = set()
    for sub in ast.walk(tree):                     # 全模块扫绑定，宁可宽松（少误报）
        if isinstance(sub, (ast.Import, ast.ImportFrom)):
            bound |= _import_names(sub)
        elif isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            bound.add(sub.name)
        elif isinstance(sub, ast.arg):
            bound.add(sub.arg)
        elif isinstance(sub, ast.Name) and isinstance(sub.ctx, ast.Store):
            bound.add(sub.id)
        elif isinstance(sub, ast.ExceptHandler) and sub.name:
            # `except X as name:` —— 名字挂在 ExceptHandler.name（字符串），不是 ast.Name 节点；
            # 漏了它会把合法的 `except Exception as _e:` 误报成未绑定（2026-10-15 踩到）。
            bound.add(sub.name)

    used = {n.id for n in ast.walk(hook) if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)}
    unknown = sorted(n for n in used - bound if not hasattr(builtins, n))
    assert not unknown, "startup 钩子里用到但没有绑定的名字（会 NameError 起不来）：%s" % unknown
