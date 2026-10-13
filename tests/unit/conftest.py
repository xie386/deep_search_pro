"""tests/unit 的公共隔离（04 补 · E5/D6 红线）。

**为什么有这份文件**：E5 把「保存模型密钥 / 工具来源密钥」改成**写 `.env`** 之后，单测只要建临时账号
+ 保存一次配置，就会往**真实 `.env`** 里追加 `LLM_KEY_*` 行 —— 实测第一轮把 27 行测试残留写进了真 `.env`。

规矩（与 `文件存放规则.md` §四「测试账号与测试数据口径」一致）：**单测不得写真实 `.env`、不得改真实环境变量**。
本 fixture 把 `utils.settings.ENV_PATH` 重定向到临时文件，并在用例结束时清掉新增的密钥类环境变量。

★ 实现注意：本 fixture **刻意不请求任何 fixture**（用 `unittest.mock.patch` + `tempfile` 自己管）——
第一版请求了 `tmp_path`/`monkeypatch`，结果改变了其它模块用例的 fixture 拆除顺序，
把 `test_usage_counter.py` 的 `purge_account` 拖到了它自己的 monkeypatch 之后（报 `库炸了`）。
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest


@pytest.fixture(autouse=True)
def _isolate_env_file():
    import utils.settings as st

    tmpdir = tempfile.mkdtemp(prefix="env_iso_")
    p = Path(tmpdir) / ".env"
    # ★ 语义：**副本**而非空文件 —— 读配置的用例行为不变（空文件会让「缺配置」分支被意外走到），
    #   写入只落在副本上，真实 .env 永不被单测写。
    try:
        p.write_text(st.ENV_PATH.read_text(encoding="utf-8"), encoding="utf-8")
    except Exception:
        p.write_text("# 单测临时 .env\n", encoding="utf-8")
    before = set(os.environ)
    with patch.object(st, "ENV_PATH", p):
        try:
            yield p
        finally:
            for k in set(os.environ) - before:
                if k.startswith(("LLM_KEY_", "TOOL_KEY_", "PRICE_KEY_")):
                    os.environ.pop(k, None)
