# -*- coding: utf-8 -*-
"""前端 setup 声明守卫（pytest 包装）。

把 `frontend_setup_exports_check.py` 的结论纳入 `pytest tests/unit`，重点是其中一条：

    return 里的每个名字，在 setup 内**必须真的有定义**。

★ 为什么单独立这条（2026-10-16 事故）：模板绑定检查只看「名字出现在 return 里」，`--fix`
  又会把缺的名字补进 return —— 于是「有引用、无定义」能一路绿灯。真机表现是 Vue 在
  setup 里 `ReferenceError`、整个 `setup()` 抛错、**页面白屏**（登录页当时只剩两个按钮）。
  单测不跑浏览器，静态检查不查定义，所以这层必须显式钉住。
"""
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
CHECKER = ROOT / "tests" / "unit" / "frontend_setup_exports_check.py"
INDEX = ROOT / "front" / "index.html"


def _run_checker():
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    env["PYTHONUTF8"] = "1"
    return subprocess.run([sys.executable, str(CHECKER)], cwd=str(ROOT),
                          capture_output=True, text=True, encoding="utf-8",
                          errors="replace", env=env)


def test_frontend_setup_exports_invariant_holds():
    """整套前端 setup 不变量（含"返回值必须有定义"）必须全绿。"""
    if not INDEX.exists():
        pytest.skip("找不到 front/index.html")
    p = _run_checker()
    out = (p.stdout or "") + (p.stderr or "")
    assert p.returncode == 0, "导出不变量未通过：\n%s" % out[-2500:]
    assert "失败 0" in out, "导出不变量报告有失败项：\n%s" % out[-2500:]


def test_frontend_setup_definitions_present():
    """★ 事故守卫：不许出现「有引用、无定义」（曾导致登录页整页白屏）。"""
    if not INDEX.exists():
        pytest.skip("找不到 front/index.html")
    sys.path.insert(0, str(CHECKER.parent))
    try:
        import frontend_setup_exports_check as chk  # noqa: E402
    finally:
        sys.path.pop(0)
    html = INDEX.read_text(encoding="utf-8")
    undef = chk.undefined_in_return(html)
    assert not undef, ("这些名字被 setup 的 return 引用、但在 setup 内没有定义（会让整个 "
                       "setup 抛错、页面白屏）：%s" % undef)
    ret, _span = chk.setup_return_names(html)
    assert len(ret) > 300, "setup 返回值数量异常（只有 %d 个），检查解析是否失效" % len(ret)


def test_views_not_trapped_in_left_column():
    """★ 2026-10-16 事故守卫二：各视图模板不得被困在 `.col-left` 里。

    真机表现：`.col-left` 少一个闭合时，`.col-mid`/`.col-right`、输入框，
    以及后面所有页面（知识库/报告/技能与工具/定制助手/设置）全都变成左栏的子节点 ——
    右栏被挤进左栏、其余页面只剩背景图。标签配平检查能抓住它，模板绑定检查抓不住。
    """
    if not INDEX.exists():
        pytest.skip("找不到 front/index.html")
    import re as _re
    html = INDEX.read_text(encoding="utf-8")
    VOID = {"br","hr","img","input","meta","link","source","area","base","col",
            "embed","param","track","wbr"}
    from html.parser import HTMLParser

    class Chain(HTMLParser):
        def __init__(self):
            super().__init__(convert_charrefs=False)
            self.stack, self.trapped = [], []

        def handle_starttag(self, tag, attrs):
            if tag in VOID:
                return
            d = dict(attrs)
            cls, vif = d.get("class", "") or "", d.get("v-if", "") or ""
            self.stack.append((tag, cls))
            if "col-left" in cls or ("col-left" in [c for _t, c in self.stack]):
                pass
            # 只看“视图根”：<template v-if="view==='xxx'"> 这种；不看偶然撞上的类名
            # （工作目录弹窗复用了 kb-modal 类名、且它本来就在 .col-left 里，属正常）
            if tag == "template" and "view" in vif.lower():
                if any("col-left" in cc for _t, cc in self.stack[:-1]):
                    self.trapped.append((vif, self.getpos()[0]))

        def handle_startendtag(self, *a):
            pass

        def handle_endtag(self, tag):
            if tag in VOID:
                return
            if self.stack and self.stack[-1][0] == tag:
                self.stack.pop()
            else:
                while self.stack and self.stack[-1][0] != tag:
                    self.stack.pop()
                if self.stack:
                    self.stack.pop()

    c = Chain()
    i = html.find('<div id="app"')
    c.feed(html[i:] if i > 0 else html)
    assert not c.trapped, ("这些视图被困在 .col-left 子树里（会导致右栏被挤进左栏、"
                           "其余页面只剩背景图）：%s" % c.trapped)
