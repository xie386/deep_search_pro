# -*- coding: utf-8 -*-
"""M3-2 用例：画像证据收集（`tools/profile_evidence.py`）。**离线**：临时账号 + 临时文件，不碰真实账号。

判据来源：M3 方案 §3.2 / D3（证据 = 7 张结构化表 + 最近 3 篇周报要点 + 最近 N 条用户消息）
与 §一 G1（覆盖面：造一个"只有结构化数据、不聊偏好"的账号 → 证据里要出现收藏/竞品等行）。

运行：.venv/Scripts/python.exe -m pytest tests/test_profile_evidence.py -q
"""
import os
import sys
import time

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

from tools import profile_evidence as pev              # noqa: E402
from tools.schema_personal import get_personal_conn, purge_account  # noqa: E402


def mk_account(tag):
    conn = get_personal_conn()
    try:
        aid = int(conn.execute("INSERT INTO accounts (username, password_hash, role) VALUES (?,?,?)",
                               ("m3ev_%s_%d" % (tag, int(time.time() * 1000) % 1000000), "x", "user")).lastrowid)
        conn.commit()
    finally:
        conn.close()
    return aid


def seed_tables(aid):
    conn = get_personal_conn()
    try:
        conn.execute("INSERT INTO interests (owner_id, interest_tag, description) VALUES (?,?,?)",
                     (aid, "AI Agent 生态", "关注开源 agent 框架"))
        conn.execute("INSERT INTO watchlist (owner_id, brand, product, note) VALUES (?,?,?,?)",
                     (aid, "米诺地尔", "泡沫喷剂", "等降价"))
        conn.execute("INSERT INTO products (owner_id, product_name, brand, category, price) VALUES (?,?,?,?,?)",
                     (aid, "降噪耳机", "某品牌", "数码", 899))
        conn.execute("INSERT INTO company_profile (owner_id, company_name) VALUES (?,?)", (aid, "灵康科技"))
        conn.execute("INSERT INTO company_products (owner_id, product_name, category, price) VALUES (?,?,?,?)",
                     (aid, "戒烟辅助贴", "个护", 129))
        conn.execute("INSERT INTO company_competitors (owner_id, comp_name, category, note) VALUES (?,?,?,?)",
                     (aid, "某竞品", "个护", "主打低价"))
        conn.execute("INSERT INTO digest_subs (owner_id, name, keywords) VALUES (?,?,?)",
                     (aid, "快讯", "价格,选购"))
        conn.commit()
    finally:
        conn.close()


def seed_sessions(aid, texts):
    conn = get_personal_conn()
    try:
        tid = "t_%d_%d" % (aid, int(time.time() * 1000) % 1000000)
        cid = int(conn.execute("INSERT INTO conversations (account_id, thread_id, title) VALUES (?,?,?)",
                               (aid, tid, "测试会话")).lastrowid)
        for i, t in enumerate(texts):
            conn.execute("INSERT INTO messages (conversation_id, turn_index, role, content) VALUES (?,?,?,?)",
                         (cid, i * 2, "user", t))
            conn.execute("INSERT INTO messages (conversation_id, turn_index, role, content) VALUES (?,?,?,?)",
                         (cid, i * 2 + 1, "assistant", "（助手的回答不该进证据）"))
        conn.commit()
    finally:
        conn.close()


def seed_report(aid, md_path, title="快讯周报 2026-09-24", item_count=10, status="done"):
    conn = get_personal_conn()
    try:
        conn.execute("INSERT INTO digest_reports (owner_id, title, md_path, item_count, status, created_at) "
                     "VALUES (?,?,?,?,?,?)", (aid, title, str(md_path), item_count, status,
                                              "2026-09-24 12:00:00"))
        conn.commit()
    finally:
        conn.close()


@pytest.fixture()
def acct():
    aid = mk_account("a")
    yield aid
    purge_account(aid)


# ---------------------------------------------------------------- ① 7 张表（G1 的"只有结构化数据"账号）
def test_collect_tables_and_lines(acct):
    seed_tables(acct)
    ev = pev.collect(acct)
    lines = ev["table_lines"]
    assert any("AI Agent 生态" in x for x in lines), "兴趣要进证据"
    assert any(x.startswith("收藏#") and "降噪耳机" in x for x in lines), "★ G1：收藏要进证据"
    assert any(x.startswith("竞品#") and "某竞品" in x for x in lines), "★ G1：竞品要进证据"
    assert any(x.startswith("订阅#") for x in lines) and any("灵康科技" in x for x in lines)
    assert all("#" in x for x in lines), "★ G5：每行都要带可溯源引用（前端点回原始条目要用）"
    assert ev["counts"]["rows"] == len(lines) == 7
    assert ev["counts"]["tables"] == 7


def test_empty_account_is_not_an_error(acct):
    ev = pev.collect(acct)
    assert ev["table_lines"] == [] and ev["reports"] == [] and ev["sessions"] == []
    txt = pev.render_text(ev)
    assert "还没有任何结构化资料" in txt and "还没有生成过周报" in txt and "没有可用的对话记录" in txt


# ---------------------------------------------------------------- ② 周报要点（纯字符串抽取）
def test_report_bullets_from_real_md(tmp_path, acct):
    md = tmp_path / "周报.md"
    md.write_text("# 快讯周报 2026-09-24\n\n导语一句。\n\n## 今日要点\n\n"
                  "- 米诺地尔新品降价 20%\n* 竞品 A 上线戒烟贴\n1. 耳机新品发布\n\n"
                  "普通段落不该被当成要点。\n", encoding="utf-8")
    seed_report(acct, md)
    reps = pev.collect_reports(acct)
    assert len(reps) == 1 and reps[0]["md_ok"] is True
    assert "快讯周报 2026-09-24" in reps[0]["bullets"]
    assert any("米诺地尔新品降价" in b for b in reps[0]["bullets"])
    assert any("竞品 A 上线戒烟贴" in b for b in reps[0]["bullets"])
    assert not any("普通段落" in b for b in reps[0]["bullets"]), "只抽标题与要点行"


def test_report_missing_file_tolerated(tmp_path, acct):
    seed_report(acct, tmp_path / "不存在.md")
    reps = pev.collect_reports(acct)
    assert len(reps) == 1 and reps[0]["md_ok"] is False and reps[0]["bullets"] == []
    assert "正文读不到" in pev.render_text(pev.collect(acct))


def test_reports_newest_first_and_failed_skipped(tmp_path, acct):
    for tag in ("a", "b", "c", "d"):
        f = tmp_path / ("%s.md" % tag)
        f.write_text("# 标题 %s\n\n- 要点 %s\n" % (tag, tag), encoding="utf-8")
        seed_report(acct, f, title="周报 %s" % tag)
    seed_report(acct, tmp_path / "x.md", title="失败那篇", status="failed")
    reps = pev.collect_reports(acct, limit=3)
    assert [r["title"] for r in reps] == ["周报 d", "周报 c", "周报 b"], "只取最近 3 篇、新的在前、失败的不算"
    assert all(r["status"] == "done" for r in reps)


# ---------------------------------------------------------------- ③ 会话（只取用户消息 + 截断）
def test_sessions_only_user_messages_oldest_first(acct):
    seed_sessions(acct, ["第一条：我在成都，下周去广州实习", "第二条：帮我盯一下米诺地尔价格"])
    sess = pev.collect_sessions(acct)
    assert len(sess) == 2, "助手的回答不该进证据"
    assert sess[0].startswith("第一条") and sess[-1].startswith("第二条"), "从旧到新"
    assert not any("助手的回答" in s for s in sess)


def test_sessions_truncate_and_cap_total(acct):
    long_msg = "很长的一句话" * 100
    seed_sessions(acct, [long_msg] + ["短消息 %d" % i for i in range(40)])
    sess = pev.collect_sessions(acct)
    assert all(len(s) <= pev.MAX_MSG_CHARS + 1 for s in sess), "单条要截断"
    assert sum(len(s) for s in sess) <= pev.MAX_SESSIONS_CHARS, "总长要封顶"
    assert sess[-1].startswith("短消息 39"), "超长时丢最旧的、保留最近的"


def test_sessions_handle_json_content(acct):
    """协议允许 content 是 JSON（实测是纯文本）→ 两种都要能读。"""
    conn = get_personal_conn()
    try:
        tid = "j_%d" % (time.time() * 1000 % 1000000)
        cid = int(conn.execute("INSERT INTO conversations (account_id, thread_id, title) VALUES (?,?,?)",
                               (acct, tid, "json")).lastrowid)
        conn.execute("INSERT INTO messages (conversation_id, turn_index, role, content) VALUES (?,?,?,?)",
                     (cid, 0, "user", '{"type": "human", "content": "我在广州找实习"}'))
        conn.commit()
    finally:
        conn.close()
    assert pev.collect_sessions(acct) == ["我在广州找实习"]


# ---------------------------------------------------------------- ④ 证据文本（给模型看的那块）
def test_render_text_carries_all_three_sources(tmp_path, acct):
    seed_tables(acct)
    seed_sessions(acct, ["我想找 AI 应用开发岗"])
    md = tmp_path / "r.md"
    md.write_text("# 周报\n\n- 广州 AI 岗位增加\n", encoding="utf-8")
    seed_report(acct, md)
    txt = pev.render_text(pev.collect(acct))
    assert "【系统里已有的结构化资料】" in txt and "降噪耳机" in txt
    assert "【最近 1 篇周报的标题与要点" in txt and "广州 AI 岗位增加" in txt
    assert "【用户最近说过的话" in txt and "AI 应用开发岗" in txt
    assert "（这个账号还没有任何结构化资料）" not in txt


def test_counts_shape_supports_meta_sources(acct):
    seed_tables(acct)
    seed_sessions(acct, ["a", "b"])
    ev = pev.collect(acct)
    assert ev["counts"]["rows"] == 7 and ev["counts"]["sessions"] == 2 and ev["counts"]["reports"] == 0
    assert set(ev["counts"]) == {"rows", "reports", "sessions", "tables"}


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
