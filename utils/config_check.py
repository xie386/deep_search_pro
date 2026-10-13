"""启动配置自检（2026-10-14，04 补 · E6）。

**它解决什么**：过去「没配」和「配错」在启动时都是静默的 —— 用户往往等到某次提问报错
（还常是一个跟配置无关的栈）才知道少配了东西。本模块把 `.env` 里的配置分三类报出来：

| 类别 | 判据 | 表现 |
|---|---|---|
| **配错** | 填了、但格式明显不对（base_url 不是 http、端口不是数字、路径不存在） | 启动日志逐条 `ERROR`，写明「改成什么」 |
| **未配置** | 可选能力没配（网搜 / 发件箱 / 嵌入模型路径） | 一条 `WARN`，写明「哪个功能会不可用」 |
| **通过** | 其余已配置项 | 汇总成一行 INFO |

口径（E6）：**自检不阻止启动** —— 半配好的机器仍能起服务、进设置页；错误只在日志里喊出来。
用法：`from utils.config_check import check_config, log_config_check`。
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

__all__ = ["check_config", "log_config_check"]


def _s(key: str) -> str:
    return (os.getenv(key) or "").strip()


def check_config() -> dict:
    """扫一遍运行期配置，返回 `{"errors": [...], "warnings": [...], "ok": [...]}`。

    - `errors`：配错（填了但明显不对）；
    - `warnings`：未配置（对应功能不可用，但不影响启动）；
    - `ok`：已配置且通过了格式校验的键。
    """
    errors: list[str] = []
    warnings: list[str] = []
    ok: list[str] = []

    # ── 模型（必需项：没有它问答/周报都跑不了） ────────────────────────────
    base = _s("OPENAI_BASE_URL")
    if base:
        if base.startswith(("http://", "https://")):
            ok.append("OPENAI_BASE_URL")
        else:
            errors.append("OPENAI_BASE_URL 必须以 http:// 或 https:// 开头（现在是 %r）" % base[:48])
    if _s("OPENAI_API_KEY") and _s("LLM_MODEL_MAX"):
        ok.append("OPENAI_API_KEY + LLM_MODEL_MAX")
    else:
        warnings.append("未配置模型（OPENAI_API_KEY / LLM_MODEL_MAX）→ 问答与周报功能不可用")

    # ── 网络搜索 ────────────────────────────────────────────────────────
    if _s("TAVILY_API_KEY"):
        ok.append("TAVILY_API_KEY")
    else:
        warnings.append("未配置 TAVILY_API_KEY → 网络搜索子 Agent 不可用")

    # ── 邮件发件箱（QQ 键优先，旧 SMTP_* 兼容） ─────────────────────────
    if (_s("QQMAIL") and _s("QQMSTP")) or (_s("SMTP_USER") and _s("SMTP_AUTH_CODE")):
        ok.append("发件箱（QQMAIL + QQMSTP）")
    else:
        warnings.append("未配置发件箱（QQMAIL + QQMSTP）→ 绑定邮箱 / 注销验证码发不出去")

    # ── 嵌入 / 重排模型路径（本机化默认值已撤，只有 .env 说了算） ────────
    for key in ("EMBEDDING_MODEL_PATH", "RERANKER_MODEL_PATH"):
        v = _s(key)
        if v and Path(v).exists():
            ok.append(key)
        elif v:
            errors.append("%s 指向的目录不存在：%s（请改 .env 里的路径）" % (key, v))
        else:
            warnings.append("未配置 %s → 知识库（RAG）不可用" % key)

    # ── 端口 / 数值类：填了必须是数字 ────────────────────────────────────
    for key in ("ASR_PORT", "SMTP_PORT", "MYSQL_PORT"):
        v = _s(key)
        if v:
            if v.isdigit():
                ok.append(key)
            else:
                errors.append("%s 必须是数字端口（现在是 %r）" % (key, v))
    for key in ("MAIL_QUOTA_PER_ACCOUNT_DAY", "MAIL_QUOTA_GLOBAL_DAY"):
        v = _s(key)
        if v:
            if v.isdigit():
                ok.append(key)
            else:
                errors.append("%s 必须是非负整数（0 = 不限；现在是 %r）" % (key, v))

    # ── 脚本区变量填了要像样（它们不参与运行，但填错常被误当运行配置） ─────
    url = _s("APP_URL")
    if url and not url.startswith(("http://", "https://")):
        errors.append("APP_URL（仅脚本用）必须以 http:// 开头（现在是 %r）" % url[:48])

    return {"errors": errors, "warnings": warnings, "ok": ok}


def log_config_check(logger: logging.Logger | None = None) -> dict:
    """跑一次自检并按级别写日志；返回结果（供测试断言）。"""
    log = logger or logging.getLogger("config_check")
    report = check_config()
    for e in report["errors"]:
        log.error("配置自检（配错）：%s", e)
    for w in report["warnings"]:
        log.warning("配置自检（未配置）：%s", w)
    log.info(
        "配置自检：%d 项通过 · %d 项未配置（功能降级）· %d 项配错",
        len(report["ok"]), len(report["warnings"]), len(report["errors"]),
    )
    return report
