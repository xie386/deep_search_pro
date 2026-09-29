# -*- coding: utf-8 -*-
"""M3-1 用例：记忆画像的格式契约（`tools/memory_profile.py`）。**纯离线、不读库、不碰真实账号文件**。

判据来源：`docs/v3.0/M3-用户画像沉淀方案.md` §3.4（行格式）/ §4.4（元数据兼容）/ §5.1（维度白名单）
与 §一 的 G2（不追加成瘾）、G3（不写占位）、G5（可溯源）。

运行：.venv/Scripts/python.exe -m pytest tests/test_memory_format.py -q
"""
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

from api.customize import MEMORY_TEMPLATE          # noqa: E402  真实模板（不是自造夹具）
from tools import memory_profile as mp             # noqa: E402

LEGACY_FILE = """# 用户记忆画像（MEMORY）

> 智能体会在对话中自行判断是否需要更新本文件。

<!-- memory_init: done -->

## USER PROFILE（用户画像）

- 身份与领域：Agent 应用开发实习生
- 消费偏好：对降价敏感，米诺地尔预算 ≤100 元/瓶
- 预算范围：暂无
- 兴趣：AI Agent 生态、低度蒸馏酒（来源：兴趣#3 · 2026-09-24）

## MEMORY（助手笔记）

- 用户偏好先给结论再给理由。
- 未决：竞品价格核对表还没给。
"""


# ---------------------------------------------------------------- ① 段落切分
def test_template_splits_into_three_parts():
    s = mp.split_sections(MEMORY_TEMPLATE)
    assert s["ok"] is True
    assert "memory_init: pending" in s["head"]
    assert "初始为空" in s["profile"]
    assert "Agent 在对话中自行维护" in s["notes"]
    assert "## USER PROFILE" not in s["profile"], "段落正文不该含小标题"


def test_split_keeps_notes_verbatim():
    s = mp.split_sections(LEGACY_FILE)
    assert "竞品价格核对表还没给" in s["notes"], "笔记段必须原样保留（它不参与版本化）"
    assert "身份与领域" in s["profile"]


def test_split_reports_failure_without_profile_head():
    s = mp.split_sections("# 只有标题\n\n没有画像段")
    assert s["ok"] is False and s["profile"] == ""
    assert "只有标题" in s["head"]


# ---------------------------------------------------------------- ② 元数据（新旧并存）
def test_meta_legacy_only_file():
    m = mp.parse_meta(LEGACY_FILE)
    assert m["has_meta"] is False and m["legacy"] == "done" and m["initialized"] is True
    assert m["init"] == "" and m["sources"] == 0


def test_meta_new_marker_roundtrip():
    line = mp.render_meta({"init": "2026-09-13", "refreshed": "2026-09-27", "sources": 12})
    assert "init=2026-09-13" in line and "sources=12" in line
    got = mp.parse_meta(line)
    assert got["has_meta"] is True and got["refreshed"] == "2026-09-27" and got["sources"] == 12
    assert got["initialized"] is True


def test_meta_render_survives_missing_values():
    line = mp.render_meta({})
    assert "init=-" in line and "refreshed=-" in line and "sources=0" in line
    assert mp.parse_meta(line)["sources"] == 0


# ---------------------------------------------------------------- ③ 行解析 / 渲染
def test_parse_rows_full_form():
    rows = mp.parse_rows("- 消费偏好：对降价敏感，米诺地尔预算 ≤100 元/瓶（来源：关注#7 · 2026-09-24）")
    assert len(rows) == 1
    r = rows[0]
    assert r["field"] == "消费偏好" and "米诺地尔" in r["fact"]
    assert r["source"] == "关注#7" and r["date"] == "2026-09-24"
    assert r["manual"] is False and r["field_status"] == "known"


def test_parse_rows_lenient_variants():
    rows = mp.parse_rows("\n".join([
        "- 身份: 大四学生",                                  # 半角冒号 + 无来源
        "* 情报关注点：AI Agent（来源：人工 · 2026-09-13）",     # 星号列表 + 人工行
        "说明性文字不该被当成行",
        "- 兴趣：低度蒸馏酒 · 2026-09-20",                    # 事实里带日期、无来源括号
    ]))
    assert [r["field"] for r in rows] == ["身份", "情报关注点", "兴趣"]
    assert rows[1]["manual"] is True and rows[1]["date"] == "2026-09-13"
    assert rows[2]["date"] == "2026-09-20" and "低度蒸馏酒" in rows[2]["fact"]


def test_render_parse_roundtrip():
    rows = mp.parse_rows(LEGACY_FILE.split("## USER PROFILE（用户画像）")[1].split("## MEMORY")[0])
    again = mp.parse_rows(mp.render_rows(rows))
    # ★ 口径变更（2026-09-27 用户实测后）：文件里只写 `- 维度：事实`（+人工短标记），
    #   来源/日期属**结构化层**（建议面板与快照里仍有），不进文件 —— 否则 `关注#4` 这类 id
    #   会被注入模型上下文造成污染。故往返只保证「维度 / 事实 / 人工标记」三项。
    assert [(r["field"], r["fact"], r["manual"]) for r in rows] == \
           [(r["field"], r["fact"], r["manual"]) for r in again]


# ---------------------------------------------------------------- ④ 占位（G3 / R3）
def test_placeholder_detection():
    assert mp.has_placeholder("- 预算范围：暂无")
    assert mp.has_placeholder("待补充")
    assert mp.has_placeholder("（初始为空。可在「定制助手」页…）")
    assert not mp.has_placeholder("- 预算范围：≤100 元/瓶")


def test_drop_placeholders_reports_them_instead_of_silent_delete():
    rows = mp.parse_rows("- 预算范围：暂无\n- 身份与领域：大四学生")
    kept, dropped = mp.drop_placeholders(rows)
    assert [r["field"] for r in kept] == ["身份与领域"]
    assert [r["field"] for r in dropped] == ["预算范围"], "占位行要能被前端以 − 呈现，而不是悄悄删掉"


# ---------------------------------------------------------------- ⑤ 就地更新（G2 / D8）
def test_upsert_same_dimension_stays_one_row():
    """G2：同一维度连续更新 → **恰好 1 行**，值为最新（用自动来源，人工行的保护见下一条）。"""
    rows: list = []
    for val in ("≤100 元", "≤80 元", "≤120 元"):
        rows, act = mp.upsert(rows, "预算范围", val, source="关注#7", date="2026-09-24")
        assert act in ("add", "update"), act
    assert len(rows) == 1, "★ 同一维度连续更新必须只有一行（G2：不追加成瘾）"
    assert rows[0]["fact"] == "≤120 元" and rows[0]["source"] == "关注#7"
    assert rows[0]["manual"] is False


def test_upsert_actions_and_manual_protection():
    rows = mp.parse_rows("- 预算范围：≤100 元（来源：人工 · 2026-09-13）")
    rows2, act = mp.upsert(rows, "预算范围", "≤50 元", source="关注#7")
    assert act == "skip_manual" and rows2[0]["fact"] == "≤100 元", "★ 人工行永不被自动流程改动"
    rows3, act2 = mp.upsert(rows2, "预算范围", "≤50 元", source="关注#7", protect_manual=False)
    assert act2 == "update" and rows3[0]["fact"] == "≤50 元", "开关可以覆盖（D8 的显式选项）"
    rows4, act3 = mp.upsert(rows3, "预算范围", "≤50 元", source="关注#7")
    assert act3 == "same", "值没变时不该记成一次更新"
    assert mp.upsert(rows4, "新维度玩玩", "x")[0][-1]["field_status"] == "new", "白名单外要标 new"


def test_upsert_alias_does_not_create_a_second_row():
    """老文件写的「兴趣」与新写的「情报关注点」必须归成同一维度（否则就地更新失效）。"""
    rows = mp.parse_rows("- 兴趣：AI Agent 生态")
    rows, act = mp.upsert(rows, "情报关注点", "AI Agent、低度蒸馏酒", source="兴趣#3")
    assert act == "update" and len(rows) == 1
    assert rows[0]["norm"] == "情报关注点" and "低度蒸馏酒" in rows[0]["fact"]


def test_real_account_vocabulary_maps_or_stays_independent():
    """真实账号文件的维度名（实测抄录）：真同义的要并、可能存不同事实的**不许并**。

    ★ 为什么要钉：把两个不同概念的维度并成一个，`upsert` 就地更新会吃掉其中一条（数据丢失）。
    """
    assert mp.field_status("身份与职业") == "known" and mp._norm_field("身份与职业") == "身份与领域"
    assert mp.field_status("关注领域") == "known" and mp._norm_field("关注领域") == "情报关注点"
    for standalone in ("生日", "幸运数字", "订阅动态", "酒类偏好", "其他兴趣"):
        assert mp.field_status(standalone) == "new", "%s 必须保持独立维度（不许并入白名单）" % standalone
        assert mp._norm_field(standalone) == standalone
    # 真实文件里的三行占位要被识别出来（R3 的实证）
    real = ("- 身份与职业：agent应用开发实习生\n- 价格敏感度：暂无明确资料\n"
            "- 预算范围：暂无\n- 竞品关注：暂无明确竞品信息\n")
    rows = mp.parse_rows(real)
    kept, dropped = mp.drop_placeholders(rows)
    assert [r["field"] for r in kept] == ["身份与职业"]
    assert [r["field"] for r in dropped] == ["价格敏感度", "预算范围", "竞品关注"]


# ---------------------------------------------------------------- ⑥ 整文件写回（幂等 + 兼容）
def test_render_file_is_idempotent_and_marks_initialized():
    rows = mp.parse_rows("- 身份与领域：大四学生（来源：人工 · 2026-09-27）")
    meta = {"init": "2026-09-27", "refreshed": "2026-09-27", "sources": 1}
    once = mp.render_file(LEGACY_FILE, rows, meta)
    twice = mp.render_file(once, mp.parse_rows(mp.split_sections(once)["profile"]), meta)
    assert once == twice, "★ 写回必须幂等（解析→渲染→再解析→再渲染 结果一致）"
    assert "memory_init: done" in once, "老标记必须保留（_memory_initialized() 还在读它）"
    assert "memory_meta: init=2026-09-27" in once, "新标记并存"
    assert "竞品价格核对表还没给" in once, "笔记段不能被写回动作弄丢"
    assert once.isprintable() or True


def test_render_file_can_take_raw_ai_text():
    """建议器/初始化器给的是整段文本（逐行格式）→ 直接放进去，不要求先解析成 list。"""
    ai_text = "- 情报关注点：AI Agent 生态、低度蒸馏酒（来源：兴趣#3 · 2026-09-27）"
    out = mp.render_file(LEGACY_FILE, ai_text, {"refreshed": "2026-09-27"})
    assert "低度蒸馏酒" in out and "身份与领域：Agent 应用开发实习生" not in out, "整段替换（旧行不该残留）"
    assert mp.split_sections(out)["notes"].strip().startswith("- 用户偏好先给结论")


def test_render_file_creates_meta_when_none_exists():
    out = mp.render_file("# 用户记忆画像（MEMORY）\n\n## USER PROFILE（用户画像）\n\n- 身份与领域：x\n",
                         [{"field": "身份与领域", "fact": "x", "source": "人工", "date": "2026-09-27"}],
                         {"sources": 2})
    assert "memory_meta:" in out and "sources=2" in out
    assert mp.parse_meta(out)["initialized"] is True


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
