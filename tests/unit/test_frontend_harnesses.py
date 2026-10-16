# -*- coding: utf-8 -*-
"""前端 harness 总闸：把 tests/harness/*.js 全部纳入 pytest，静默红的守卫再也藏不住。

## 为什么要有它（2026-10-16 守卫清扫）
这些 harness 是纯 Node 脚本，只能手敲 `node tests/harness/<名字>.js` 跑 —— 于是**不在任何
自动化入口里**。实测发现三条早已静默变红（`probe_front_logic`、`m1c_tools_sources_frontend`、
`m5_price_frontend`），谁都没发现：等于守卫没在守。本用例把它们全部纳入 `pytest tests/unit`，
红一条就红一片，逼着人当场处理。

判定口径（两条都要过，缺一不可）：
  ① 退出码为 0；
  ② 输出里没有「失败 N」（N ≥ 1）—— 因为部分 harness **失败了也 exit 0**（实测 m5_price 就是），
     只看退出码会漏。

`node` 不存在时跳过（不把环境缺失算成失败）；逐条报出失败 harness 的输出尾部便于定位。
"""
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
HARNESS_DIR = ROOT / "tests" / "harness"

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="本机没有 node，跳过前端 harness")


def _harnesses() -> list[Path]:
    return sorted(HARNESS_DIR.glob("*.js"))


def test_harness_dir_not_empty():
    """防"目录改名/搬走后本用例静默变成 0 条"——那就是把总闸本身拆了。"""
    assert _harnesses(), "tests/harness 下没有 .js —— 目录搬了？总闸会静默失效"


@pytest.mark.parametrize("js", _harnesses(), ids=lambda p: p.stem)
def test_harness_passes(js: Path):
    p = subprocess.run(["node", str(js)], cwd=str(ROOT), capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=180)
    out = (p.stdout or "") + (p.stderr or "")
    tail = "\n".join(out.strip().splitlines()[-12:])
    assert p.returncode == 0, "harness 非零退出：%s\n%s" % (js.name, tail)
    m = re.search(r"失败\s*(\d+)\s*项", out) or re.search(r"(\d+)\s*失败", out)
    assert not (m and int(m.group(1)) > 0), "harness 报了失败项：%s\n%s" % (js.name, tail)
