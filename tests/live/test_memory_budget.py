# -*- coding: utf-8 -*-
"""M3-6 用例：记忆注入的两段预算（画像 ≤500 / 笔记 ≤300）与裁剪口径。**纯文本，无 DB、无网络。**

判据来源：M3 方案 §5.4（装配期裁剪而非下游句子过滤）、§4.3（笔记取前 N + 时效兜底 + v1 只用词法）、
§一 G6（注入不得因画像膨胀而失控）。

运行：.venv/Scripts/python.exe -m pytest tests/test_memory_budget.py -q
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import agent.build_context as bc                          # noqa: E402
import agent.context_budget as cb                         # noqa: E402

PROFILE_BIG = "\n".join([
    "- 身份与领域：AI 应用开发方向的大四学生，正在做深度搜索与工具路由项目",
    "- 消费偏好：" + "认品牌、先看测评再看价格、对降价敏感，" * 10,
    "- 情报关注点：" + "AI Agent 开源框架、MCP 生态、向量检索与路由质量，" * 8,
    "- 竞品关注：" + "同类个人情报助手产品，" * 8,
    "- 关注渠道：小红书、B站、微信读书、少数派、即刻、知乎",
    "- 其他稳定事实：" + "零散事实占位。" * 20,
])
NOTES_BIG = "\n".join(["- 笔记 %d：%s" % (i, "内容" * 12) for i in range(1, 9)])


def memtext(profile=PROFILE_BIG, notes=NOTES_BIG):
    return ("# 用户记忆画像（MEMORY）\n\n## USER PROFILE（用户画像）\n\n%s\n\n## MEMORY（助手笔记）\n\n%s\n"
            % (profile, notes))


# ---------------------------------------------------------------- ① 硬上限
def test_profile_capped_at_500():
    out = bc._trim_profile(PROFILE_BIG)
    assert len(out) <= bc.PROFILE_MAX_CHARS, "画像段硬上限 500 字"
    assert out.strip() and all(l.startswith("-") for l in out.split("\n")), "只保留完整的条目行"


def test_notes_capped_at_300_and_items():
    out = bc._trim_notes(NOTES_BIG, bc.NOTES_MAX_CHARS, question="")
    assert len(out) <= bc.NOTES_MAX_CHARS
    assert len(out.split("\n")) <= bc.NOTES_MAX_ITEMS, "笔记最多 5 条（§4.3）"


def test_injection_never_explodes():
    """整份超大 MEMORY 注入 → 画像+笔记合计不超过两段预算（+ 标题行）。"""
    text = bc.compose_dynamic_prompt("", memtext(), "xieky", "")
    assert bc.PROFILE_MAX_CHARS + bc.NOTES_MAX_CHARS + 200 >= len(text)
    assert "身份与领域" in text and "消费偏好" in text, "优先维度必须在"


# ---------------------------------------------------------------- ② 优先级与时效
def test_priority_dimensions_survive_trimming():
    """超预算时**先裁低优先维度**：情报相关维度留下，且确实发生了裁剪（不追求"低优先必须全丢"——
    放得下就该填满预算，硬丢反而是浪费）。"""
    out = bc._trim_profile(PROFILE_BIG)
    assert "消费偏好" in out and "情报关注点" in out, "★ 情报相关维度优先保留"
    assert len(out) <= bc.PROFILE_MAX_CHARS
    assert len(out.split("\n")) < len(PROFILE_BIG.split("\n")), "确实裁掉了条目"


def test_recent_notes_kept_regardless_of_question():
    out = bc._trim_notes(NOTES_BIG, bc.NOTES_MAX_CHARS, question="成都今天天气怎么样")
    assert "笔记 8" in out, "★ 时效兜底：最近 3 条无条件保留（防'刚记的事看不到'）"


def test_relevant_note_beats_irrelevant_older_one():
    """构造：无关条 A 与相关条 Z **争同一个名额**（最近 3 条已被"时效兜底"占住）→ 相关者胜。

    顺序 A · Z · R1 · R2 · R3 · R4 → 最近 3 条 = R2/R3/R4（钉住），剩下 A 与 Z 争一个位置。
    """
    lines = ["- 笔记 A：完全无关的历史记录", "- 笔记 Z：用户在研究 BnF 巴黎公社馆藏检索",
             "- 笔记 R1：普通内容", "- 笔记 R2：普通内容", "- 笔记 R3：普通内容", "- 笔记 R4：普通内容"]
    pinned = lines[3:]
    limit = sum(len(l) for l in pinned) + len(lines[1]) + len(lines[2])   # 只够再塞一条
    out = bc._trim_notes("\n".join(lines), limit, question="BnF 的巴黎公社馆藏能查到吗")
    assert "BnF" in out, "★ 与问句相关的笔记优先拿到名额（v1 词法）"
    assert "笔记 A" not in out, "无关的更早条目被挤掉"
    assert "笔记 R4" in out, "最近 3 条无条件保留（时效兜底）"


def test_split_memory():
    p, n = bc._split_memory(memtext())
    assert p.startswith("- 身份与领域") and n.startswith("- 笔记 1")
    assert "## " not in p and "## " not in n


def test_split_memory_falls_back_for_nonstandard_text():
    """★ 真 bug 回归：非标准排版的记忆文本**绝不能整段被吞**（等于悄悄删掉用户的记忆）。

    实测被 `test_assembly.py::test_compose_dynamic_prompt_combined` 的老契约挡下来过：那段文本
    没有 `## USER PROFILE` 标题 → `split_sections` 两边都空 → 注入结果只剩一行提示。"""
    p, n = bc._split_memory("喜欢猫，晚上喝美式\n不喜欢开会")
    assert "喜欢猫" in p, "非标准格式要整段当画像原样注入"
    assert n == ""


def test_untouched_when_within_budget():
    """★ 没超预算就**原样返回**（含标题与顺序）—— 别无缘无故重排，那会破坏既有注入契约。"""
    block = "## USER PROFILE（用户画像）\n\n- 身份与领域：大四学生\n- 预算范围：≤100 元/瓶"
    assert bc._trim_profile(block) == block.strip()
    notes = "## MEMORY（助手笔记）\n\n- 笔记 1：短\n- 笔记 2：也短"
    assert bc._trim_notes(notes, 300, question="") == notes.strip()


def test_notes_heading_survives_trimming():
    """裁剪后也要留 `## MEMORY（助手笔记）` 标题 —— 否则模型分不清"画像事实"与"助手流水笔记"。"""
    text = bc.compose_dynamic_prompt("", memtext(), "xieky", "")
    assert "## MEMORY（助手笔记）" in text
    assert "- 身份与领域" in text


# ---------------------------------------------------------------- ③ 装配期（不是下游过滤）
def test_build_request_context_carries_signal():
    ctx = bc.build_request_context("t1", 1, "随便问问", memory_text=memtext(),
                                   username="xieky", profile_signal="【画像待更新】自上次刷新以来，新增了 1 条兴趣。")
    text = ctx.system_text if hasattr(ctx, "system_text") else str(ctx)
    assert "【画像待更新】" in text, "信号要真的进到装配结果里"
    assert "【你的用户记忆画像】" in text
    assert len([l for l in text.split("\n") if l.startswith("- ") and "内容内容" in l]) <= bc.NOTES_MAX_ITEMS


def test_budget_constants_single_source():
    assert cb.MEMORY_INJECT_BUDGET == {"profile": 500, "notes": 300}
    assert cb.MEMORY_PROFILE_MAX_CHARS == bc.PROFILE_MAX_CHARS
    assert cb.MEMORY_NOTES_MAX_CHARS == bc.NOTES_MAX_CHARS, "★ 预算数值只有一份来源，不许复制"


def test_no_downstream_string_filtering_of_profile():
    """下游（周报/摘要）不许再按内容做字符串过滤 —— 预算已在装配期生效。"""
    src = open(os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                            "agent", "build_context.py"), encoding="utf-8").read()
    assert "PROFILE_MAX_CHARS" in src and "_trim_profile(" in src and "_trim_notes(" in src
    assert "暂无" not in src, "装配期不按内容猜该丢什么"


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q"]))
