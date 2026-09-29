# -*- coding: utf-8 -*-
"""M6c-6 · 外壳失败分类（M6c 方案 §3.4 Part B）。

**只收敛口径，不改行为**（方案 C5）：看护逻辑（只对自起后端生效、绝不动用户服务）、
收进托盘、退出判据一律原样保留 —— 这里只做「把散落的退出码字符串映射成枚举 + 人话 + 建议动作」。

零依赖（只有标准库）：外壳在**后端还没起来**时就要用它，不能拖任何项目内的重依赖。
"""
import re

UNKNOWN = "UNKNOWN"          # 兜底码（调用方别再自己写字面量）

# ---- 严重度 ----
INFO = "info"
WARN = "warn"
ERROR = "error"

# 码表：CODE → (severity, 用户可见中文, 建议动作)
CODES = {
    "USER_QUIT":                 (INFO,  "你主动退出了应用", "—"),
    "LAUNCHER_EXIT":             (INFO,  "（正常现象：venv 启动器先退出）", "不作为失败"),
    "BACKEND_PORT_TAKEN":        (ERROR, "后端端口被占用（10048）", "关掉占用该端口的程序，或让它自动换端口重试"),
    "BACKEND_START_FAILED":      (ERROR, "后端启动后立即退出", "看 backend.log 末尾（依赖/配置问题）"),
    "BACKEND_READY_TIMEOUT":     (WARN,  "后端就绪超时", "检查 .env 与网络后重试"),
    "BACKEND_CRASHED":           (ERROR, "后端运行中崩溃", "已自动重启；连续失败则停止并提示"),
    "BACKEND_RESTART_EXHAUSTED": (ERROR, "自动重启次数用尽", "需人工介入（附日志路径）"),
    "WINDOW_CLOSE_UNEXPECTED":   (WARN,  "窗口异常关闭", "已收进托盘 / 已退出（按托盘可用性）"),
    "WEBVIEW_INIT_FAILED":       (ERROR, "内嵌浏览器初始化失败", "检查 WebView2 运行时"),
    "UNKNOWN":                   (WARN,  "未知失败（保留了原文）", "把原文连同 backend.log 一起留档"),
}

# 现有字符串 → 枚举。顺序有意义：先精确前缀，再兜底。
_RULES = (
    (re.compile(r"^child_exit_port_taken", re.I), "BACKEND_PORT_TAKEN"),
    (re.compile(r"^child_exit_gone", re.I), "BACKEND_START_FAILED"),
    (re.compile(r"^child_exit", re.I), "BACKEND_START_FAILED"),
    (re.compile(r"^ready_timeout|^timeout", re.I), "BACKEND_READY_TIMEOUT"),
    (re.compile(r"^restart_exhausted", re.I), "BACKEND_RESTART_EXHAUSTED"),
    (re.compile(r"^crashed|^backend_crashed", re.I), "BACKEND_CRASHED"),
    (re.compile(r"^launcher_exit", re.I), "LAUNCHER_EXIT"),
    (re.compile(r"^user_quit|^用户退出", re.I), "USER_QUIT"),
    (re.compile(r"^webview|^webview_init", re.I), "WEBVIEW_INIT_FAILED"),
    (re.compile(r"^window_close|^窗口异常关闭", re.I), "WINDOW_CLOSE_UNEXPECTED"),
)


def classify(raw: str) -> str:
    """原始字符串/退出码 → 枚举名。**认不出来就 UNKNOWN**（不猜、不吞）。"""
    s = str(raw or "").strip()
    if not s:
        return "UNKNOWN"
    if s.upper() in CODES:          # 已经是枚举名（新调用点会直接传码名）→ 原样认
        return s.upper()
    for rx, code in _RULES:
        if rx.search(s):
            return code
    return "UNKNOWN"


def meta(code: str) -> tuple:
    """枚举名 → `(severity, 中文, 建议动作)`；未知枚举退回 UNKNOWN 的文案。"""
    return CODES.get(code, CODES["UNKNOWN"])


def describe(raw: str, detail: str = "") -> str:
    """人话一行：`CODE | 中文 | 建议：…`（日志与界面共用同一句，避免两处口径）。

    ★ 未知串必须**把原文带出来**（方案要求"降级为 UNKNOWN 并留原文"）——
      丢了原文就没法排查厂商/环境漂移，UNKNOWN 也就成了"什么都没说" ✗。
    """
    code = classify(raw)
    sev, zh, advice = meta(code)
    head = "%s | %s" % (code, zh)
    if advice and advice != "—":
        head += " | 建议：%s" % advice
    if code == UNKNOWN and str(raw or "").strip():
        head += " | 原文：%s" % str(raw).strip()[:200]
    if detail and str(detail) not in head:
        head += " | 详情：%s" % detail
    return head
