# -*- coding: utf-8 -*-
"""v4.1-01：面板拖动取 HWND 的兼容性（pythonnet 的 IntPtr 不能直接 int()）。"""
import importlib.util
from pathlib import Path

APP = Path(__file__).resolve().parents[2] / "front" / "desktop" / "app.py"


def _load():
    spec = importlib.util.spec_from_file_location("desktop_app_under_test", APP)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_hwnd_of_intptr_like():
    app = _load()

    class FakeHandle:          # 模拟 pythonnet 的 System.IntPtr
        def ToInt64(self):
            return 0x1234

    class FakeNative:
        Handle = FakeHandle()

    class FakeWin:
        native = FakeNative()

    assert app._hwnd_of(FakeWin()) == 0x1234


def test_hwnd_of_plain_int():
    app = _load()

    class FakeNative:
        Handle = 4321

    class FakeWin:
        native = FakeNative()

    assert app._hwnd_of(FakeWin()) == 4321


def test_set_panel_stores_window():
    """_set_panel 的形参名必须与函数体一致（曾因改成 win 导致 NameError: panel 未定义）。"""
    app = _load()
    app._set_panel("FENGPAN")
    assert app._PANEL == "FENGPAN"
    app._set_panel(None)
