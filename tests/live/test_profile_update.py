# -*- coding: utf-8 -*-
"""M3-5 用例：结构化画像写入工具 + 待更新信号 + 提示词契约。**离线**：临时账号，不动真实画像。

判据来源：M3 方案 §4.1（工具签名与语义）、§3.3（待更新信号）、D9（改前快照）、§六 M3-5（tools 单测 + prompt 契约）。

运行：.venv/Scripts/python.exe -m pytest tests/test_profile_update.py -q
"""
import os
import shutil
import sys
import time

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

from api import context as ctxmod                        # noqa: E402
from tools import memory_profile as mp                   # noqa: E402
from tools import memory_snapshots as ms                 # noqa: E402
from tools import profile_evidence as pev                # noqa: E402
from tools.profile_update import update_user_profile     # noqa: E402
from tools.schema_personal import get_personal_conn, purge_account  # noqa: E402

BASE = """# 用户记忆画像（MEMORY）

<!-- memory_meta: init=2026-09-13 refreshed=2026-09-20 sources=3 counts=1,0,0,0,0,0,0,0 -->

## USER PROFILE（用户画像）

- 身份与领域：大四学生（来源：人工 · 2026-09-13）
- 预算范围：≤100 元/瓶

## MEMORY（助手笔记）

- 笔记：不能丢。
"""


@pytest.fixture()
def acct():
    conn = get_personal_conn()
    uname = "m3pu_%d" % (int(time.time() * 1000) % 1000000)
    try:
        aid = int(conn.execute("INSERT INTO accounts (username, password_hash, role) VALUES (?,?,?)",
                               (uname, "x", "user")).lastrowid)
        conn.commit()
    finally:
        conn.close()
    path = os.path.join(ROOT, "agents_docs", uname, "MEMORY.md")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(BASE)
    ctxmod.set_owner_context(aid)
    yield {"aid": aid, "uname": uname, "path": path}
    ctxmod.set_owner_context(None)
    purge_account(aid)
    shutil.rmtree(os.path.dirname(path), ignore_errors=True)


def rows_of(acct):
    with open(acct["path"], encoding="utf-8") as f:
        return mp.parse_rows(mp.split_sections(f.read())["profile"])


def call(field, value="", op="upsert", source="会话"):
    return update_user_profile.invoke({"op": op, "field": field, "value": value, "source": source})


# ---------------------------------------------------------------- ① upsert 语义（修 R2 的核心）
def test_upsert_in_place_no_duplicate_rows(acct):
    r1 = call("预算范围", "≤120 元/瓶")
    r2 = call("预算范围", "≤150 元/瓶")
    r3 = call("预算范围", "≤180 元/瓶")
    assert "已就地更新" in r1 and "已就地更新" in r3
    rows = rows_of(acct)
    assert [r["norm"] for r in rows].count("预算范围") == 1, "★ 同一维度连续更新三次 → 恰好一行（方案 G2）"
    assert [r for r in rows if r["norm"] == "预算范围"][0]["fact"] == "≤180 元/瓶"


def test_new_dimension_is_appended(acct):
    call("消费偏好", "认品牌，先看测评再看价格")
    assert any(r["norm"] == "消费偏好" for r in rows_of(acct))
    assert len(rows_of(acct)) == 3, "原有 2 条 + 新增 1 条"


def test_source_out_of_file_but_manual_marker_roundtrips(acct):
    """★ 口径变更：来源不写进文件（注入污染），但**人工短标记必须能往返** —— 否则 D8 保护失效。"""
    call("品类偏好", "数码 > 个护", source="收藏#12")
    txt = open(acct["path"], encoding="utf-8").read()
    assert "收藏#12" not in txt and "（来源" not in txt
    assert [r for r in rows_of(acct) if r["norm"] == "品类偏好"][0]["fact"] == "数码 > 个护"
    line = mp.render_row({"field": "身份与领域", "fact": "手写的", "manual": True})
    assert line.endswith("（人工）")
    back = mp.parse_rows(line + "\n")[0]
    assert back["manual"] is True and back["fact"] == "手写的"


# ---------------------------------------------------------------- ② 人工行不可动摇（D8）
def test_manual_row_never_overwritten(acct):
    out = call("身份与领域", "AI 应用开发实习生")
    assert "人工" in out and "没改动" in out
    assert [r for r in rows_of(acct) if r["norm"] == "身份与领域"][0]["fact"] == "大四学生"


def test_manual_row_not_removed(acct):
    out = call("身份与领域", op="remove")
    assert "人工" in out and any(r["norm"] == "身份与领域" for r in rows_of(acct))


# ---------------------------------------------------------------- ③ 边界
def test_remove_non_manual_row(acct):
    assert "已删除" in call("预算范围", op="remove")
    assert not any(r["norm"] == "预算范围" for r in rows_of(acct))


def test_remove_missing_row(acct):
    assert "没改动" in call("不存在的维度", op="remove")


def test_empty_value_is_refused(acct):
    assert "没改动" in call("品类偏好", "   ")
    assert not any(r["norm"] == "品类偏好" for r in rows_of(acct))


def test_without_owner_context(acct):
    ctxmod.set_owner_context(None)
    assert "没有账号上下文" in call("品类偏好", "数码")
    ctxmod.set_owner_context(acct["aid"])


# ---------------------------------------------------------------- ④ 改前快照 + 水位（D9 / §3.3）
def test_snapshot_before_write(acct):
    call("品类偏好", "数码")
    snaps = ms.list_snapshots(acct["aid"])
    assert len(snaps) == 1 and snaps[0]["reason"] == ms.REASON_AGENT_UPDATE
    assert "预算范围：≤100 元/瓶" in snaps[0]["preview"], "★ 快照必须是改动之前的画像"


def test_meta_watermark_refreshed(acct):
    call("品类偏好", "数码")
    with open(acct["path"], encoding="utf-8") as f:
        meta = mp.parse_meta(f.read())
    assert meta["refreshed"] == mp.today() and meta["sources"] == 3 and meta["init"] == "2026-09-13"
    assert len(meta["counts"]) == 8, "★ 计数水位的位数 = COUNT_KEYS（供下次算'新增了什么'）"


def test_notes_section_preserved(acct):
    call("品类偏好", "数码")
    with open(acct["path"], encoding="utf-8") as f:
        assert "笔记：不能丢。" in f.read()


# ---------------------------------------------------------------- ⑤ 待更新信号（服务端算数字）
def test_staleness_empty_when_nothing_new(acct):
    with open(acct["path"], encoding="utf-8") as f:
        meta = mp.parse_meta(f.read())
    assert pev.staleness(acct["aid"], meta) == "", "没有新增数据 → 不出信号（免得每轮都吵）"


def test_staleness_counts_new_rows(acct):
    conn = get_personal_conn()
    try:
        for kw in ("数码好物", "个护好物"):
            conn.execute("INSERT INTO interests (owner_id, interest_tag, description, keywords) VALUES (?,?,?,?)",
                         (acct["aid"], kw, "测试新增", ""))
        conn.commit()
    finally:
        conn.close()
    with open(acct["path"], encoding="utf-8") as f:
        meta = mp.parse_meta(f.read())
    sig = pev.staleness(acct["aid"], meta)
    # BASE 的水位是 counts=1,0,...（当时已有 1 条兴趣）→ 现在库里 2 条 → 差 = 1，恰好验证"按水位相减"
    assert "【画像待更新】" in sig and "1 条兴趣" in sig, "★ 数字由服务端算好：水位差 1 条兴趣"


def test_refresh_meta_keeps_init():
    m = pev.refresh_meta(0, 5, {"init": "2026-01-01", "sources": 1})
    assert m["init"] == "2026-01-01" and m["sources"] == 5 and m["refreshed"] == mp.today()


# ---------------------------------------------------------------- ⑥ 接线与提示词契约
def test_tool_registered_on_both_main_agents():
    """两个主 Agent 装配点都要挂「画像写入」。

    2026-09-29 修：原断言写死 `update_user_profile],`（M3 期只有它一个工具），M5-3 在同一行又加了
    `record_price` 后该字符串不复存在 → 用例静默失效。改成**按装配点块解析**，对新工具免疫。
    """
    src = open(os.path.join(ROOT, "api", "server.py"), encoding="utf-8").read().replace("\r\n", "\n")
    anchors = [i for i, l in enumerate(src.split("\n"))
               if "read_agent_doc, write_agent_doc, list_agent_docs, query_kb" in l]
    assert len(anchors) == 2, "应当有**两个**主 Agent 装配点，实际 %d" % len(anchors)
    lines = src.split("\n")
    for a in anchors:
        block = "\n".join(lines[a:a + 3])
        assert "update_user_profile" in block, "装配点缺「画像写入」：\n" + block
        assert "record_price" in block, "装配点缺「记价」：\n" + block
def test_prompt_contract_mentions_structured_tool():
    yml = open(os.path.join(ROOT, "prompt", "prompts.yml"), encoding="utf-8").read()
    seg = yml[yml.find("# 记忆维护（必须执行）"):]
    seg = seg[:seg.find("\n\n\n")] if "\n\n\n" in seg else seg
    assert "update_user_profile" in seg and "就地更新" in seg
    assert "【画像待更新】" in seg, "§3.3 的待更新信号必须在提示词里有去处"
    assert "不写占位" in seg or "不要写占位" in seg
    assert "写前先用【读取文档工具】看当前内容" not in seg, "★ 旧的'读全文再重写'口径已废（R2 根因）"


def test_signal_wired_into_injection():
    bc = open(os.path.join(ROOT, "agent", "build_context.py"), encoding="utf-8").read()
    assert "profile_signal" in bc and "parts.append(profile_signal)" in bc
    sv = open(os.path.join(ROOT, "api", "server.py"), encoding="utf-8").read()
    assert "_pev.staleness(" in sv and "profile_signal=profile_signal," in sv


def test_tool_docstring_has_mechanical_rules():
    doc = update_user_profile.description or update_user_profile.__doc__ or ""
    assert "同一维度就地更新" in doc and "人工" in doc and 'op="remove"' in doc.replace("'", '"')


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
