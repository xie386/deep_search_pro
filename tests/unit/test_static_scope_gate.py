# -*- coding: utf-8 -*-
"""静态作用域闸：未定义名（F821）/ 局部变量先用后赋值（F823）。

**为什么单独立**：`_run_agent` 里同一个坑踩了三次 —— `write_sets`、`workflows`、`cs`
都是"在这一段用、却在后面才定义/import"，运行时才炸（还被 except 吞成一行日志，
表现为"这一轮莫名其妙没落库/不生效"）。ruff 的 F821/F823 正好覆盖这一类，
用一条 pytest 把它变成提交前必过的闸。
"""
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent


def test_no_undefined_names_or_use_before_assignment():
    # 固定用**项目 venv** 里的 ruff（本机还有个全局 ruff.EXE，它不吃 `-m`，别混用）
    py = ROOT / ".venv" / "Scripts" / "python.exe"
    cmd = [str(py), "-m", "ruff", "check",
           "--select", "F821,F823", "--output-format", "concise",
           "api", "agent", "tools", "utils"]
    r = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    if "No module named ruff" in (r.stderr or "") or r.returncode == 127:
        pytest.skip("未安装 ruff（`uv pip install ruff` 后本闸生效）")
    assert r.returncode == 0, ("存在未定义名/先用后赋值的变量（运行时才会炸，且常被 except 吞掉）：\n"
                               + (r.stdout or r.stderr))
