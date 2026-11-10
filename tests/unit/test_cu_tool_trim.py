# -*- coding: utf-8 -*-
"""v4.1-03：CU 模式下工具表的裁剪（只看 / 看+动 / 全关 三态）。"""
import pytest

from agent import cu_middleware
from utils import cu_mode


class _T:
    def __init__(self, name):
        self.name = name


TOOLS = [_T(n) for n in ("screen_capture", "mouse_click", "type_text", "press_key", "mouse_scroll",
                         "web_search_only", "run_shell_command", "workdir_list")]


@pytest.fixture(autouse=True)
def _clean():
    cu_mode.end_turn()
    yield
    cu_mode.end_turn()


def _names_after_gate():
    """跑一遍中间件，返回模型最终看到的工具名。"""
    mw = cu_middleware.ComputerUseGateMiddleware()
    seen = {}

    def handler(request):
        seen["names"] = [t.name for t in request.tools]
        return "ok"

    req = _make_request(TOOLS)
    mw.wrap_model_call(req, handler)
    return seen["names"]


def _make_request(tools):
    from langchain.agents.middleware.types import ModelRequest
    return ModelRequest(model=None, messages=[], tools=tools)


def test_cu_off_keeps_business_tools_only():
    cu_mode.begin_turn(False, False)
    names = _names_after_gate()
    assert "screen_capture" not in names and "mouse_click" not in names
    assert "web_search_only" in names and "run_shell_command" in names


def test_look_only_keeps_capture_only():
    cu_mode.begin_turn(True, False)
    names = _names_after_gate()
    assert names == ["screen_capture"]


def test_control_keeps_cu_five():
    cu_mode.begin_turn(True, True)
    names = _names_after_gate()
    assert set(names) == {"screen_capture", "mouse_click", "type_text", "press_key", "mouse_scroll"}
    assert "web_search_only" not in names      # CU 模式下业务工具一律不发
