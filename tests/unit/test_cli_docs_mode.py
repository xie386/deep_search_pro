# -*- coding: utf-8 -*-
"""能力描述「依据三选一」用例（2026-10-07）：`url` 网址 / `path` 本机文件 / `help` 跑 --help。

背景：接入 qqmail（自研、未开源）与 agently-cli（闭源、公测期官网无介绍）时发现——
AI 预写只会去读 CLI 的介绍网页，而这两类工具根本没有可读文档，`--help` 是唯一方法信息源。
于是把「依据」做成显式三选一，并补上取证所需的两个前提。

覆盖：
  · 只读闸对**裸 `--help`/`--version`** 的窄放行 —— 放行它，但仍挡落盘参数、仍挡空命令行、仍需清单；
  · `docs_mode` 校验 —— 网址必须 http(s)、本机文件必须绝对路径、help 模式不存地址、
    非法模式报错、缺省即保留（老配置不改也照旧按网址读）；
  · Windows `.cmd`/`.bat` 垫片经 cmd.exe 启动（原先直接执行报 `WinError 193`，
    npm 装出来的 CLI 全跑不起来）；
  · `collect_help_digest` 端到端（裸 --help + 各只读命令的 --help 拼成证据）。

运行：
    .venv/Scripts/python.exe -m pytest tests/unit/test_cli_docs_mode.py -q
"""
import os
import shutil
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from tools import cli_registry as reg            # noqa: E402
from tools._runtime import shell_runtime as rt   # noqa: E402

RULES = [["+me"], ["message", "+list"]]


# ------------------------------------------------ 只读闸：裸 --help 的窄放行
@pytest.mark.parametrize("argv", [["--help"], ["-h"], ["--version"], ["-v"], ["--usage"]])
def test_bare_help_flags_are_allowed(argv):
    ok, why = reg.readonly_verdict(argv, RULES)
    assert ok, why


@pytest.mark.parametrize("argv", [
    ["--help", "--output", "x"],
    ["--output", "x"],
    ["--version", "--output-dir", "d"],
])
def test_write_flags_still_blocked_even_with_help(argv):
    ok, why = reg.readonly_verdict(argv, RULES)
    assert not ok and "只读" in why


def test_bare_help_still_needs_a_readonly_list():
    """没填只读清单 = 不放行 Agent 代跑，裸 --help 也不能例外。"""
    ok, why = reg.readonly_verdict(["--help"], [])
    assert not ok and "只读命令清单" in why


def test_empty_argv_still_rejected():
    ok, why = reg.readonly_verdict([], RULES)
    assert not ok and "缺少子命令" in why


def test_normal_subcommand_gate_unchanged():
    assert reg.readonly_verdict(["message", "+list"], RULES)[0]
    ok, why = reg.readonly_verdict(["message", "+send"], RULES)
    assert not ok and "不在只读清单" in why


# ------------------------------------------------ docs_mode 校验
def _payload(**kw):
    base = {"name": "测试工具", "bin": "tcli", "docs_mode": "url"}
    base.update(kw)
    return base


def test_mode_url_requires_http():
    d = reg._validate_payload(_payload(docs="https://example.com/README.md"), existing_bins=set())
    assert d["docs_mode"] == "url" and d["docs"].startswith("https://")


def test_mode_url_rejects_local_path():
    with pytest.raises(reg.CliConfigError):
        reg._validate_payload(_payload(docs=r"D:\code\qqmail-cli\README.md"), existing_bins=set())


def test_mode_path_requires_absolute():
    with pytest.raises(reg.CliConfigError):
        reg._validate_payload(_payload(docs_mode="path", docs="README.md"), existing_bins=set())
    d = reg._validate_payload(_payload(docs_mode="path", docs=r"D:\code\qqmail-cli\README.md"),
                              existing_bins=set())
    assert d["docs_mode"] == "path" and d["docs"].endswith("README.md")


def test_mode_help_clears_docs():
    """help 模式的依据来自本机 `--help`，不该再把一个网址留在库里当文档。"""
    d = reg._validate_payload(_payload(docs_mode="help", docs="https://example.com/README.md"),
                              existing_bins=set())
    assert d["docs_mode"] == "help" and d["docs"] == ""


def test_unknown_mode_rejected():
    with pytest.raises(reg.CliConfigError):
        reg._validate_payload(_payload(docs_mode="internet"), existing_bins=set())


def test_mode_is_inherited_from_current_when_absent():
    """老配置 / 老前端不带 docs_mode：更新时保留原值，新建时默认 url（向后兼容）。"""
    # cur 的形状与 get_cli() 返回一致（「缺省即保留」会去读其中的原值）
    cur = {"id": 1, "name": "x", "bin": "tcli", "install_cmd": "", "auth_cmd": "",
           "docs": "", "docs_mode": "help", "readonly": "", "abilities": "",
           "note": "", "state": "none"}
    d = reg._validate_payload({"name": "x", "bin": "tcli"}, existing_bins={"tcli"}, cur=cur)
    assert d["docs_mode"] == "help"
    d2 = reg._validate_payload({"name": "y", "bin": "tcli2"}, existing_bins=set())
    assert d2["docs_mode"] == "url"


# ------------------------------------------------ .cmd 垫片 + --help 取证（真跑）
def test_cmd_shim_runs_and_help_digest_works():
    """Windows 上 npm/pipx 装出来的 CLI 在 PATH 里是 `.cmd` 垫片，必须经 cmd.exe 启动。"""
    if os.name != "nt":
        pytest.skip("只在 Windows 验证 .cmd 垫片")
    with tempfile.TemporaryDirectory() as td:
        shim = os.path.join(td, "tshim.cmd")
        # 纯 ASCII：中文写进 .cmd 会被 cmd.exe 按 GBK 解码成乱码
        with open(shim, "w", encoding="ascii", newline="\r\n") as f:
            f.write("@echo off\r\n"
                    "if \"%~1\"==\"--help\" (\r\n"
                    "  echo Usage: tshim ^<bookId^>\r\n"
                    "  echo   Resolve a book title to bookId matches.\r\n"
                    "  exit /b 0\r\n"
                    ")\r\n"
                    "echo Usage: tshim ^<title^>\r\n"
                    "exit /b 0\r\n")
        old = os.environ.get("PATH", "")
        os.environ["PATH"] = td + os.pathsep + old
        try:
            assert shutil.which("tshim"), "垫片没进 PATH，测试环境有问题"
            adhoc = {"tshim": [["go"]]}
            # ① 裸 --help：原先这条路径直接 WinError 193，现在应跑通（只读闸已放行）
            res = rt.execute("tshim", ["--help"], timeout=20, adhoc=adhoc)
            text = (res.get("stdout") or "") + (res.get("stderr") or "")
            assert res["ok"], res
            assert "Usage: tshim" in text, text
            # ② 落盘参数依旧被挡（放行 help 不等于放宽只读闸）
            bad = rt.execute("tshim", ["--help", "--output", "x"], timeout=20, adhoc=adhoc)
            assert not bad["ok"] and "只读" in (bad.get("error") or "")
            # ③ collect_help_digest 端到端：裸 --help + 只读清单里每条命令的 --help
            d = reg.collect_help_digest("tshim", ["go"], timeout=20)
            assert d["ok"] and "$ tshim --help" in d["text"] and "$ tshim go --help" in d["text"], d
            assert d["failed"] == [], d
            # ④ 采到的 usage 能喂给形态判定（`<bookId>` → needs_id）
            assert reg.classify_usage("tshim <bookId>") == "needs_id"
        finally:
            os.environ["PATH"] = old
