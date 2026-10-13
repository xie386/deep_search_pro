"""密钥「名入库、值走 .env」的统一口径（04 补 · E5，2026-10-14 用户选 A 案）。

**为什么有这份模块**：`llm_providers.api_key` 与 `tool_sources.config.auth.secret` 过去把**明文密钥存进库**，
备份 / 导出一次 `personal.db` 就等于泄一次钥。项目里早有一条正确样板——`price_sources.secret_env`：
库里只存**环境变量名**，值只活在 `.env`。

**本模块的口径**：

| 环节 | 做法 |
|---|---|
| 存 | 库里只存 `secret_env`（键名）；值只写 `.env`（写前自动备份 `.env.bak_<时间戳>`，保留注释与其余行） |
| 取 | `get(name, legacy)`：有名 → 读 env（值缺了返回空串，由调用方给提示）；无名 → 退回 legacy 明文列（老库兼容） |
| 回传 | 接口一律返回**掩码**（列表）/ 真值（仅内部造模型时 `mask=False`）；键名可回传（不含值） |
| 日志 | 任何分支都不回显值 |

用法：
    name = put("llm", pid, "sk-xxx", label="DeepSeek")   # 写 .env（并同步本进程 os.environ），返回键名
    val  = get("LLM_KEY_DEEPSEEK_3", legacy=p["api_key"])
"""
from __future__ import annotations

import os
import re

from utils.settings import _write_env   # 复用既有的写 .env（自动备份 + 保留注释与其余行）

__all__ = ["PREFIX", "env_name", "put", "get"]

#: 各类密钥的键名前缀（改这里 = 改全项目的命名口径）
PREFIX = {"llm": "LLM_KEY", "tool": "TOOL_KEY", "price": "PRICE_KEY"}


def env_name(kind: str, ident: object, label: str = "") -> str:
    """生成稳定的键名，如 `LLM_KEY_DEEPSEEK_3`。

    规则：`<前缀>_<标签大写去非法字符>_<ident>`；标签空则用 KIND 兜底。
    （ident 用行 id，保证同名配置也不撞键 ✓）
    """
    pfx = PREFIX.get(kind, "APP_KEY")
    slug = re.sub(r"[^A-Za-z0-9]+", "_", str(label or "")).strip("_").upper()[:24]
    return "%s_%s_%s" % (pfx, slug or (kind or "KEY").upper(), ident)


def put(kind: str, ident: object, value: str, label: str = "") -> str:
    """把密钥写进 `.env`（写前自动备份），**并同步本进程 `os.environ`**，返回键名。

    为什么要同步 os.environ：用户在前端保存后往往**不重启后端**就想用新密钥 ——
    只写文件的话当前进程读不到旧值之外的东西。
    """
    v = (value or "").strip()
    if not v:
        raise ValueError("密钥为空，不写 .env")
    name = env_name(kind, ident, label)
    _write_env({name: v})
    os.environ[name] = v          # 当前进程立即可见（改 .env 文件不影响已跑的进程 ✓）
    return name


def get(name: str | None, legacy: str = "") -> str:
    """取真实密钥：`secret_env` 有名 → 读 env；无名 → 退回 legacy 明文（老库兼容）。"""
    if name:
        return (os.environ.get(str(name)) or "").strip()
    return (legacy or "").strip()
