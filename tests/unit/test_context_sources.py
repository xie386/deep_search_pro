# -*- coding: utf-8 -*-
"""M4-6 用例：注入源注册表（离线，无 DB、无网络）。

判据来源：M4 方案 §六 M4-6（注册/覆盖/缺失 provider/抛错降级/顺序 ≥10 项）+ §3.2 三口径。

运行：.venv/Scripts/python.exe -m pytest tests/test_context_sources.py -q
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from agent import context_sources as cs          # noqa: E402
from agent.context_sources import SourceResult   # noqa: E402


@pytest.fixture(autouse=True)
def fresh():
    cs.clear()
    yield
    cs.clear()


# ---------------------------------------------------------------- ① 注册与顺序
def test_register_and_order_is_registration_order():
    for n in ("c", "a", "b"):
        cs.register(n, lambda aid, q: SourceResult(n))
    assert cs.names() == ("c", "a", "b"), "顺序必须**确定**（等价性回归依赖它）"


def test_collect_returns_all_registered_sources():
    cs.register("soul", lambda aid, q: SourceResult("人格"))
    cs.register("memory", lambda aid, q: SourceResult("记忆"))
    got = cs.collect(1, "问句")
    assert got == {"soul": "人格", "memory": "记忆"}


def test_re_register_overrides_and_keeps_position():
    cs.register("a", lambda aid, q: SourceResult("旧"))
    cs.register("b", lambda aid, q: SourceResult("B"))
    cs.register("a", lambda aid, q: SourceResult("新"))
    assert cs.names() == ("a", "b"), "覆盖不该改变位置（否则顺序不稳定）"
    assert cs.collect(1)["a"] == "新"


def test_register_rejects_empty_name_or_none_provider():
    cs.register("", lambda aid, q: SourceResult("x"))
    cs.register("ok", None)
    assert cs.names() == ()


# ---------------------------------------------------------------- ② 降级（绝不抛）
def test_provider_exception_degrades_to_empty_string():
    cs.register("good", lambda aid, q: SourceResult("有"))
    def boom(aid, q):
        raise RuntimeError("provider 炸了")
    cs.register("bad", boom)
    got = cs.collect(1, "q")
    assert got == {"good": "有", "bad": ""}, "★ 一个源坏了 → 该源空串，其它源照常"


def test_all_sources_broken_returns_all_empty():
    cs.register("x", lambda aid, q: 1 / 0)
    cs.register("y", lambda aid, q: None)
    assert cs.collect(1) == {"x": "", "y": ""}


def test_provider_receives_account_and_question():
    seen = {}
    cs.register("s", lambda aid, q: seen.update(aid=aid, q=q) or SourceResult(""))
    cs.collect(42, "成都天气")
    assert seen == {"aid": 42, "q": "成都天气"}


# ---------------------------------------------------------------- ③ 宽容与标记
def test_string_return_is_tolerated():
    cs.register("s", lambda aid, q: "  直接给字符串  ")
    assert cs.collect(1)["s"] == "直接给字符串", "provider 直接返回字符串也认（去掉首尾空白）"


def test_injected_flags_track_content():
    cs.register("with", lambda aid, q: SourceResult("有内容", True))
    cs.register("without", lambda aid, q: SourceResult("", False))
    cs.register("bad", lambda aid, q: 1 / 0)
    assert cs.injected_flags(1) == {"with": True, "without": False, "bad": False}


def test_empty_registry_collects_nothing():
    assert cs.collect(1, "q") == {} and cs.names() == ()


# ---------------------------------------------------------------- ④ 分层
def test_module_does_not_import_api():
    s = open(os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                          "agent", "context_sources.py"), encoding="utf-8").read()
    import re
    assert not re.search(r"^\s*(?:import api|from api)\b", s, re.M), "agent 层不得 import api.*"


def test_api_providers_register_soul_and_memory():
    from api import context_providers as cp
    cp.register_all()
    assert cs.names() == ("soul", "memory")
    got = cs.collect(0, "")          # 不存在的账号 → 降级为空串而不是抛错
    assert set(got) == {"soul", "memory"} and all(isinstance(v, str) for v in got.values())


def test_register_all_is_idempotent():
    from api import context_providers as cp
    cp.register_all()
    cp.register_all()
    assert cs.names() == ("soul", "memory"), "重复注册不该长出重复项"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
