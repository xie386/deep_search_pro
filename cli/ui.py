# -*- coding: utf-8 -*-
"""dspro 终端输出工具：轻量 ANSI 颜色 + 东亚宽度表格，**零外部依赖**。

设计约束（用户拍板）：
  - 纯文本 + 轻量 ANSI，不引入 rich 等依赖；
  - 中文/emoji 宽度要按东亚宽度算，否则表格会歪；
  - 非 tty / 设了 NO_COLOR / 传 --no-color 时自动降级为纯文本。
"""

import os
import re
import sys
import unicodedata

# ---------------------------------------------------------------------------
# 颜色
# ---------------------------------------------------------------------------
_COLOR = True          # setup() 后确定

_C = {
    "reset": "\033[0m", "bold": "\033[1m", "dim": "\033[2m",
    "red": "\033[31m", "green": "\033[32m", "yellow": "\033[33m",
    "blue": "\033[34m", "magenta": "\033[35m", "cyan": "\033[36m",
    "grey": "\033[90m",
}


def setup(no_color_flag: bool = False) -> bool:
    """确定是否启用颜色：--no-color / NO_COLOR / 非 tty → 关闭。返回最终状态。"""
    global _COLOR
    if no_color_flag or os.environ.get("NO_COLOR"):
        _COLOR = False
    elif not sys.stdout.isatty():
        _COLOR = False
    else:
        _COLOR = True
        if os.name == "nt":      # Windows 10+ 默认支持 VT，老 conhost 手动开
            try:
                import ctypes
                k = ctypes.windll.kernel32
                k.SetConsoleMode(k.GetStdHandle(-11), 7)
            except Exception:
                pass
    return _COLOR


def color_on() -> bool:
    return _COLOR


def c(text: str, *styles: str) -> str:
    if not _COLOR or not styles:
        return text
    seq = "".join(_C.get(s, "") for s in styles)
    return f"{seq}{text}{_C['reset']}"


# ---------------------------------------------------------------------------
# 宽度 / 对齐（中文与 emoji 按 2 列）
# ---------------------------------------------------------------------------
_ANSI_RE = re.compile(r"\033\[[0-9;]*m")


def dwidth(s: str) -> int:
    """显示宽度：东亚全角/宽字符 + emoji 记 2，其余记 1；ANSI 转义不计入。"""
    s = _ANSI_RE.sub("", str(s))
    w = 0
    for ch in s:
        if unicodedata.combining(ch):
            continue
        if unicodedata.east_asian_width(ch) in ("W", "F"):
            w += 2
        elif ord(ch) >= 0x1F300:          # emoji 区（W 判定不到的部分）
            w += 2
        else:
            w += 1
    return w


def pad(s: str, width: int, align: str = "left") -> str:
    s = str(s)
    gap = max(0, width - dwidth(s))
    if align == "right":
        return " " * gap + s
    if align == "center":
        left = gap // 2
        return " " * left + s + " " * (gap - left)
    return s + " " * gap


def trunc(s: str, width: int, ellipsis: str = "…") -> str:
    """按显示宽度截断（保留完整字符，避免半个字符）。"""
    s = str(s)
    if dwidth(s) <= width:
        return s
    out, w = "", 0
    limit = width - dwidth(ellipsis)
    for ch in s:
        cw = dwidth(ch)
        if w + cw > limit:
            break
        out += ch
        w += cw
    return out + ellipsis


# ---------------------------------------------------------------------------
# 结构元素
# ---------------------------------------------------------------------------
def title(text: str, sub: str = "") -> None:
    line = f"  {text}  "
    print()
    print(c("━" * dwidth(line), "green"))
    print(c(line, "green", "bold"))
    print(c("━" * dwidth(line), "green"))
    if sub:
        print(c(sub, "grey"))


def section(text: str, icon: str = "") -> None:
    head = f"{icon} {text}" if icon else text
    print()
    print(c(head, "cyan", "bold"))
    print(c("─" * max(8, dwidth(head)), "grey"))


def kv(pairs, indent: str = "  ") -> None:
    """键值对输出（键按最长的对齐）。"""
    pairs = [(str(k), "" if v is None else str(v)) for k, v in pairs]
    if not pairs:
        return
    w = max(dwidth(k) for k, _ in pairs)
    for k, v in pairs:
        print(f"{indent}{c(pad(k, w), 'grey')}  {v}")


def table(headers, rows, max_width=None, aligns=None) -> None:
    """朴素表格（东亚宽度安全）。max_width: 单列最大显示宽度（超出截断）。"""
    headers = [str(h) for h in headers]
    rows = [[("" if v is None else str(v)) for v in r] for r in rows]
    ncol = len(headers)
    if max_width:
        cells = [headers] + rows
        widths = []
        for i in range(ncol):
            cap = max_width[i] if isinstance(max_width, (list, tuple)) else max_width
            col = [dwidth(r[i]) if i < len(r) else 0 for r in cells]
            widths.append(min(cap, max(col)) if col else 3)
    else:
        widths = [max(dwidth(h), *[dwidth(r[i]) if i < len(r) else 0 for r in rows]) for i, h in enumerate(headers)]

    def fmt(row, style=None):
        out = []
        for i in range(ncol):
            cell = row[i] if i < len(row) else ""
            cell = trunc(cell, widths[i]) if dwidth(cell) > widths[i] else cell
            al = (aligns[i] if aligns and i < len(aligns) else "left")
            out.append(pad(cell, widths[i], al))
        line = c(" │ ", "grey").join(out)
        return c(line, style) if style else line

    print("  " + fmt(headers, "bold"))
    print("  " + c("─┼─".join("─" * w for w in widths), "grey"))
    for r in rows:
        print("  " + fmt(r))
    print()


def blank() -> None:
    print()


def ok(text: str) -> None:
    print(f"  {c('✔', 'green')} {text}")


def warn(text: str) -> None:
    print(f"  {c('!', 'yellow')} {text}")


def err(text: str) -> None:
    print(f"  {c('✘', 'red')} {text}", file=sys.stderr)


def info(text: str) -> None:
    print(f"  {c('·', 'grey')} {text}")


def hint(text: str) -> None:
    print(c(f"  {text}", "grey"))


def progress(event_type: str, message: str, data: dict) -> None:
    """monitor 事件 → 终端进度行（dspro chat / digest 用）。"""
    if event_type == "tool_start":
        name = data.get("tool_name") or message
        print(f"  {c('⚙', 'magenta')} {c('调用工具', 'grey')} {c(str(name), 'magenta')}")
    elif event_type == "assistant_call":
        name = data.get("assistant_name") or message
        print(f"  {c('🤝', 'blue')} {c('调度助手', 'grey')} {c(str(name), 'blue')}")
    elif event_type == "thinking":
        text = (data.get("text") or "").strip()
        if text:
            print(f"  {c('· ' + trunc(text, 150), 'grey')}")
    elif event_type == "task_result":
        print(f"  {c('✓', 'green')} {c('子任务完成', 'grey')}")
    elif event_type == "session_dir":
        return
    else:
        print(f"  {c('·', 'grey')} {message}")


# ---------------------------------------------------------------------------
# 轻量 Markdown → 终端
# ---------------------------------------------------------------------------
def md_light(text: str) -> str:
    """把 markdown 渲染成终端友好的样子（不清洗内容，只做视觉处理）。"""
    out = []
    for raw in str(text).splitlines():
        line = raw.rstrip()
        if not line.strip():
            out.append("")
            continue
        m = re.match(r"^(#{1,6})\s+(.*)$", line)
        if m:
            lvl, txt = len(m.group(1)), m.group(2)
            out.append("")
            out.append("  " + (c(txt, "cyan", "bold") if lvl <= 2 else c(txt, "cyan")))
            if lvl <= 2:
                out.append("  " + c("─" * min(70, dwidth(txt) + 4), "grey"))
            continue
        if re.match(r"^\s*(-{3,}|\*{3,})$", line):
            out.append("  " + c("─" * 60, "grey"))
            continue
        # 引用块
        if line.lstrip().startswith(">"):
            out.append("  " + c("│ " + line.lstrip()[1:].strip(), "grey"))
            continue
        body = line
        body = re.sub(r"\*\*([^*]+)\*\*", lambda m2: c(m2.group(1), "bold"), body)
        body = re.sub(r"`([^`]+)`", lambda m2: c(m2.group(1), "yellow"), body)
        out.append("  " + body)
    return "\n".join(out)


def banner(version: str, who: str = "") -> None:
    """CLI 顶部标识。"""
    tail = f"   {c(who, 'grey')}" if who else ""
    print(c("dspro", "green", "bold") + c(f" v{version}", "grey") + c("  · 智选情报官命令行版", "grey") + tail)
