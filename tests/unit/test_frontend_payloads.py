# -*- coding: utf-8 -*-
"""前端「保存」请求体的字段来源守卫（2026-11-10 真机 bug 的回归用例）。

★ 为什么要有它：
  模型选型页保存「已有配置」时，`supports_vision` 被写成了 `newProv.supports_vision`
  （新建表单的对象），于是无论用户勾没勾，都会把库里那行重置成 0——
  界面一直显示「已勾」，后端却认为不支持图片，最后在屏幕共享处报「未开启支持图片」。
  这类错误只改一个变量名，测试不跑真实点击完全看不出来，所以这里做静态对表：
  **保存已有配置的 payload，字段必须取自被保存的那一行 `p`，不能碰 `newProv`。**
"""
import re
from pathlib import Path

INDEX = Path(__file__).resolve().parents[2] / "front" / "index.html"


def _func_body(name: str) -> str:
    """从 index.html 里抠出 `async function <name>(...) {...}` 的函数体。"""
    src = INDEX.read_text(encoding="utf-8")
    m = re.search(r"async function %s\s*\([^)]*\)\s*\{" % re.escape(name), src)
    assert m, "没找到函数 %s" % name
    i = m.end() - 1
    depth, quote = 0, None
    while i < len(src):
        ch = src[i]
        if quote:
            if ch == "\\":
                i += 2
                continue
            if ch == quote:
                quote = None
        elif ch in "\"'`":
            quote = ch
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return src[m.end():i]
        i += 1
    raise AssertionError("函数 %s 的大括号没有配平" % name)


def test_save_provider_uses_row_object():
    body = _func_body("saveProvider")
    assert "supports_vision: !!p.supports_vision" in body, \
        "保存已有配置时，supports_vision 必须取自本行 p"
    assert "newProv" not in body, \
        "saveProvider 里不该出现新建表单的 newProv（会把已有配置的勾选状态覆盖掉）"


def test_create_provider_still_uses_new_form():
    body = _func_body("addProvider")
    assert "newProv.value" in body, "新建配置的字段应取自 newProv"
    assert "supports_vision: !!p.supports_vision" in body, \
        "新建配置时，supports_vision 应取自新建表单（局部变量 p = newProv.value）"
