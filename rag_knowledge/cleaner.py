"""入库前内容清洗（M3，批注 7/8）——针对 AI 导出报告的"检索脏内容"。

与 `api/voice_tts.py::_clean_tts_text`（朗读友好）同思路但**目标不同**：
KB 检索需要保留 Markdown 结构语义（标题/加粗/列表是有用的检索特征），
只清除会污染向量与 BM25 的噪音：

- emoji/符号（AI 回复里人格口吻大量装饰）——来源标记类 emoji 转纯文本（📚→知识库）
- 表格竖线符 → 转"字段：值"文本行（保留表格语义，去分隔符噪音）
- 引用块符/代码围栏等纯排版噪音
- 人格化开场/收尾（如"✨ 情报官周报～奴婢为您整理好了…"）→ evaluator 建议，cleaner 尽力删首尾寒暄
 
不清理：标题层级（#/##）、加粗、列表符、正文内容——这些是检索特征。
"""
from __future__ import annotations

import re

# 来源标记 emoji → 纯文本（保留语义）
_SOURCE_EMOJI = {
    "📚": "知识库", "🔍": "网络", "🌐": "网络", "🗄️": "数据库",
    "📰": "新闻", "📊": "数据", "📈": "趋势",
}
# 其余 emoji/装饰符号删除范围
_EMOJI_RE = re.compile(
    r"[\U0001F000-\U0001FAFF\u2600-\u27BF\u2B00-\u2BFF\uFE0F\u200D\u2700-\u27BF]"
)


def _strip_lines(lines: list[str]) -> list[str]:
    """删除纯装饰行：纯 emoji/符号行、分隔线、引用块空壳。"""
    out = []
    for ln in lines:
        s = ln.strip()
        if not s:
            continue
        if re.fullmatch(r"[-_=*]{3,}", s):        # --- 分隔线
            continue
        if re.fullmatch(r"[:：]?\s*[😂😅😊🙏✨🎉❤️🔥]+", s) or _EMOJI_RE.fullmatch(s):
            continue
        out.append(ln)
    return out


def _table_to_text(line: str) -> str:
    """md 表格行 → '字段：值' 文本（保留单元格语义，去竖线）。"""
    cells = [c.strip() for c in line.split("|")]
    cells = [c for c in cells if c]
    if not cells:
        return ""
    # 分隔行（| --- | --- |）跳过
    if all(re.fullmatch(r":?-+:?", c) for c in cells):
        return ""
    # 表头行可能是纯标题词；这里统一转顿号分隔文本（检索无差别）
    return "；".join(cells) + "。"


def clean_for_kb(text: str) -> str:
    """入库前清洗主入口：返回清洗后文本。"""
    if not text:
        return text
    lines = text.split("\n")
    out: list[str] = []
    for ln in lines:
        if "|" in ln and ln.strip().startswith("|"):
            # 表格行转文本
            t = _table_to_text(ln)
            if t:
                out.append(t)
            continue
        out.append(ln)
    text = "\n".join(_strip_lines(out))

    # 来源 emoji → 文字
    for emo, word in _SOURCE_EMOJI.items():
        text = text.replace(emo, word)
    # 其余 emoji 删除（保留字母数字中文与基础标点）
    text = _EMOJI_RE.sub("", text)

    # 引用块符（> 前缀）去符号留内容
    text = re.sub(r"^\s*>\s?", "", text, flags=re.M)

    # 人格口吻开场白删除（尽力而为，配合 evaluator 建议）：
    # 开头的问候/寒暄行特征：非 md 标题、整行 ≤50 字、无句号结尾（报告正文从标题或长句开始）
    # 颜文字 (≧∇≦)ﾉ 等可能残留在行尾，先删掉常见颜文字括号段再判断
    text = re.sub(r"[（(][^）)]*[）)]", "", text)          # 颜文字/括号装饰
    text = re.sub(r"[〜～~]+$", "", text, flags=re.M)     # 行尾波浪号
    lines = text.split("\n")
    _TONE_HINT = re.compile(r"[～~]|奴婢|小女|主人|本官|喵|嗨|哈喽|您好呀|大家好")
    body_start = 0
    for i, ln in enumerate(lines[:4]):
        s = ln.strip()
        # 口吻开场行：非标题、≤50 字、无句号结尾、且含口吻特征（自称/波浪号/语气）
        if (s and not s.startswith("#") and len(s) <= 50
                and not re.search(r"[。.!?！？]$", s) and _TONE_HINT.search(s)):
            body_start = i + 1
        else:
            break
    if body_start:
        lines = lines[body_start:]
    text = "\n".join(lines).strip()

    # 合并多余空行
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()
