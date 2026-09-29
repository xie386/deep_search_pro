# -*- coding: utf-8 -*-
"""M5c-2' 用例：API/MCP「工具级描述」（用户可编辑 + AI 预写）。**全离线，不调模型、不联网**。

口径（写死在用例里）：
  ① 用户可以手写**工具级**描述 → 存在 `abilities_user`（**不覆盖**自动摘要 `abilities`），
     「发现 / 导入工具」刷新自动摘要时**不会**冲掉手写内容；
  ② 读取优先级：`abilities_user` > `abilities`（自动摘要）；
  ③ 关键词可以写在**工具级**描述里以「关键词：」开头（AI 草稿就是这么产的）；
  ④ README 依据支持两种形态：**http 网址**与**本地文件路径**；本地读取要有边界（大小 / 二进制 / 不存在）；
  ⑤ 撰写 AI 的提示词里**绝不能**出现连接凭据（MCP 端点常把凭证放在路径段、命令行/env 可能带 token）。

运行：.venv/Scripts/python.exe -m pytest tests/test_capability_draft.py -q
"""
import io
import json
import os
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from api import tools_sources as tsp                        # noqa: E402
from tools import capability_draft as cd                    # noqa: E402
from tools import capability_pool as cp                     # noqa: E402
from tools import source_docs                               # noqa: E402
from tools.schema_personal import get_personal_conn, purge_account  # noqa: E402

CAPS = [
    {"ref": "mcp:city/search_city", "name": "search_city", "read_only": True, "confirmed_at": "t",
     "abilities": "Search a city", "abilities_user": "",
     "input_schema": {"type": "object", "properties": {"city_name": {"type": "string"}}, "required": ["city_name"]},
     "invoke_spec": {"tool": "search_city"}},
    {"ref": "mcp:city/city_weather", "name": "city_weather", "read_only": True, "confirmed_at": "t",
     "abilities": "Weather of a city", "abilities_user": "",
     "input_schema": {"type": "object", "properties": {"city_name": {"type": "string"}}}, "invoke_spec": {}},
]


# ---------------------------------------------------------------- ① 工具清单（给模型的事实表）
def test_tool_listing_carries_ref_params_and_current_text():
    t = cd.tool_listing(CAPS)
    assert "mcp:city/search_city" in t and "★city_name" in t          # 必填参数带 ★
    assert "Search a city" in t                                        # 现有描述要带上（在它基础上改）
    assert t.count("|") > 6


# ---------------------------------------------------------------- ② 草稿解析（容错是重点）
DRAFT_OK = """工具：mcp:city/search_city
描述：关键词：城市信息/地名查询。用户问某个城市的基本情况时用，能拿到所属国家、行政区、经纬度与人口。

工具：mcp:city/city_weather
描述：关键词：天气/气温。用户问某地天气时用，能拿到当前天气。
"""


def test_parse_normal_output():
    r = cd.parse_draft(DRAFT_OK, CAPS)
    assert [i["ref"] for i in r["items"]] == ["mcp:city/search_city", "mcp:city/city_weather"]
    assert "城市信息" in r["items"][0]["text"]
    assert r["warnings"] == []


def test_parse_accepts_name_or_tail_as_key():
    """模型常把标识写成工具名或尾段 —— 要能对上，不能因此丢描述。"""
    r = cd.parse_draft("工具：city_weather\n描述：关键词：天气。查天气。", CAPS)
    assert r["items"] and r["items"][0]["ref"] == "mcp:city/city_weather"


def test_parse_joins_folded_description_lines():
    r = cd.parse_draft("工具：mcp:city/search_city\n描述：关键词：城市。第一句。\n第二句续行。\n", CAPS)
    assert "第二句续行" in r["items"][0]["text"]


def test_parse_flags_unknown_and_missing():
    text = "工具：mcp:city/search_city\n描述：关键词：城市。查城市。\n\n工具：mcp:city/nope\n描述：这个词不存在。\n"
    r = cd.parse_draft(text, CAPS)
    assert [i["ref"] for i in r["items"]] == ["mcp:city/search_city"]
    joined = " ".join(r["warnings"])
    assert "清单里没有的工具" in joined and "nope" in joined          # 写了不存在的工具 → 告警
    assert "没被写到描述" in joined and "city_weather" in joined      # 漏写的工具 → 告警


def test_parse_keeps_first_when_duplicated():
    r = cd.parse_draft("工具：mcp:city/search_city\n描述：第一次。\n\n工具：mcp:city/search_city\n描述：第二次。", CAPS)
    assert len(r["items"]) == 1 and "第一次" in r["items"][0]["text"]
    assert any("两次" in w for w in r["warnings"])


def test_parse_empty_output_warns():
    r = cd.parse_draft("", CAPS)
    assert r["items"] == [] and any("没解析出任何可用描述" in w for w in r["warnings"])


def test_parse_ignores_code_fence_and_prose():
    text = "以下是描述：\n```\n工具：mcp:city/search_city\n描述：关键词：城市。查城市。\n```\n希望有帮助。"
    r = cd.parse_draft(text, CAPS)
    assert [i["ref"] for i in r["items"]] == ["mcp:city/search_city"]


# ---------------------------------------------------------------- ③ README 依据（两种形态 + 边界）
def test_resolve_empty_is_not_an_error():
    d = source_docs.resolve("")
    assert d["ok"] is False and "没填" in d["note"]


def test_read_local_readme(tmp_path):
    f = tmp_path / "README.md"
    f.write_text("# 城市服务\n\n按城市名查基本信息；另有天气接口。", encoding="utf-8")
    d = source_docs.resolve(str(f))
    assert d["ok"] is True and "城市服务" in d["text"] and d["source"] == "file"


def test_read_local_relative_to_base_dir(tmp_path):
    (tmp_path / "README.md").write_text("相对路径也能读到", encoding="utf-8")
    d = source_docs.resolve("README.md", base_dir=str(tmp_path))
    assert d["ok"] is True and "相对路径" in d["text"]


def test_read_local_rejects_missing_dir_and_binary(tmp_path):
    assert source_docs.resolve(str(tmp_path / "nope.md"))["ok"] is False
    assert "目录" in source_docs.resolve(str(tmp_path))["note"]
    png = tmp_path / "logo.png"
    png.write_bytes(b"\x89PNG\r\n\x1a\n")
    assert source_docs.resolve(str(png))["ok"] is False


def test_read_local_rejects_nul_content(tmp_path):
    f = tmp_path / "weird.txt"
    f.write_bytes(b"abc\x00def")
    d = source_docs.resolve(str(f))
    assert d["ok"] is False and "二进制" in d["note"]


def test_read_local_truncates_big_file(tmp_path):
    f = tmp_path / "big.txt"
    f.write_text("x" * (source_docs.MAX_FILE_BYTES + 5000), encoding="utf-8")
    d = source_docs.resolve(str(f))
    assert d["ok"] is True and "只读了前" in d["note"]


# ---------------------------------------------------------------- ④ 保存工具描述（DB 路径）
def mk_account(tag):
    conn = get_personal_conn()
    try:
        aid = int(conn.execute("INSERT INTO accounts (username, password_hash, role) VALUES (?,?,?)",
                               ("m5cd_%s_%d" % (tag, int(time.time() * 1000) % 100000), "x", "user")).lastrowid)
        token = "tok_%d_%d" % (aid, int(time.time() * 1000) % 100000)
        conn.execute("INSERT INTO sessions (token, account_id, login_at) VALUES (?,?,?)", (token, aid, time.time()))
        conn.execute("INSERT INTO tool_sources (account_id, source, slug, name, config_json, abilities, enabled) "
                     "VALUES (?,?,?,?,?,?,1)", (aid, "mcp", "city", "城市服务", "{}", "城市信息与天气"))
        for c in CAPS:
            conn.execute("INSERT INTO tool_capabilities (account_id, source, ref, name, abilities, read_only, "
                         "input_schema, invoke_spec, confirmed_at, enabled) VALUES (?,?,?,?,?,1,?,?,?,1)",
                         (aid, "mcp", c["ref"], c["name"], c["abilities"],
                          json.dumps(c["input_schema"]), json.dumps(c["invoke_spec"]), "2026-09-27 12:00:00"))
        conn.commit()
    finally:
        conn.close()
    return aid, token


@pytest.fixture()
def acct():
    aid, token = mk_account("a")
    yield {"aid": aid, "token": token}
    purge_account(aid)


def test_save_and_view_roundtrip(acct):
    r = tsp.cap_abilities_save(tsp.CapAbilitiesReq(
        source="mcp", slug="city",
        items=[{"ref": "mcp:city/search_city", "text": "关键词：城市信息。用户问城市基本情况时用。"}]), acct["token"])
    assert r["ok"] and len(r["saved"]) == 1 and not r["skipped"]
    # 两个读路径都拿到（候选列表视图与撰写 AI 的加载包共用一个视图函数，形状必须一样）
    sid = [s["id"] for s in tsp.sources_list(acct["token"])["items"] if s["slug"] == "city"][0]
    item = [i for i in tsp.candidates_list(sid, acct["token"])["items"]
            if i["ref"] == "mcp:city/search_city"][0]
    assert item["abilities_user"].startswith("关键词：城市信息")
    assert item["abilities"] == "Search a city", "★ 自动摘要也要回给前端（手写版单独一列）"
    got = tsp.load_source_and_caps(acct["aid"], "mcp", "city")[1]
    row = [c for c in got if c["ref"] == "mcp:city/search_city"][0]
    assert row["abilities_user"].startswith("关键词：城市信息")
    assert row["abilities"] == "Search a city", "★ 自动摘要必须原样保留（手写版单独一列）"


def test_save_empty_text_clears_override(acct):
    tsp.cap_abilities_save(tsp.CapAbilitiesReq(source="mcp", slug="city",
                                               items=[{"ref": "mcp:city/search_city", "text": "手写"}]), acct["token"])
    tsp.cap_abilities_save(tsp.CapAbilitiesReq(source="mcp", slug="city",
                                               items=[{"ref": "mcp:city/search_city", "text": ""}]), acct["token"])
    row = [c for c in tsp.load_source_and_caps(acct["aid"], "mcp", "city")[1]
           if c["ref"] == "mcp:city/search_city"][0]
    assert row["abilities_user"] == ""


def test_save_skips_foreign_refs(acct):
    r = tsp.cap_abilities_save(tsp.CapAbilitiesReq(
        source="mcp", slug="city",
        items=[{"ref": "mcp:other/whatever", "text": "x"}, {"ref": "mcp:city/city_weather", "text": "y"}]), acct["token"])
    assert [s["ref"] for s in r["saved"]] == ["mcp:city/city_weather"]
    assert r["skipped"] and "不属于当前账号" in r["skipped"][0]["why"]


# ---------------------------------------------------------------- ⑤ 能力池读取优先级（纯函数）
def _row(**kw):
    base = {"ref": "mcp:city/search_city", "name": "search_city", "abilities": "Search a city",
            "abilities_user": "", "enabled": 1, "confirmed_at": "t", "read_only": 1,
            "input_schema": "{}", "invoke_spec": "{}"}
    base.update(kw)
    return base


def test_entry_prefers_user_text_and_keeps_source_text():
    e = cp.mcp_entry(_row(abilities_user="关键词：城市信息/地名。用户问某城市基本情况时用。"),
                     {"slug": "city", "enabled": 1, "abilities": "城市信息与天气"})
    assert e.abilities_tool.startswith("关键词：城市信息")
    assert "城市信息与天气" in e.abilities, "来源级描述仍要在（M5c-2 已实测：砍掉它是负收益）"
    assert "Search a city" not in e.abilities, "手写的要顶掉自动摘要"


def test_keywords_come_from_tool_level_prefix():
    """AI 草稿以「关键词：」开头时，关键词要被提取出来（工具级优先于来源级）。"""
    e = cp.mcp_entry(_row(abilities_user="关键词：城市信息/地名查询。用户问城市时用。"),
                     {"slug": "city", "enabled": 1, "abilities": "关键词：来源级关键词。来源说明"})
    assert e.keywords == "城市信息/地名查询"


def test_user_text_enters_vector_and_card():
    e = cp.mcp_entry(_row(abilities_user="用户问某个城市的基本情况时用，能拿到国家与人口。"),
                     {"slug": "city", "enabled": 1, "abilities": "城市信息与天气"})
    assert "国家与人口" in cp._entry_text(e)      # 路由向量文本
    assert "国家与人口" in e.card_text            # 模型看到的卡片


# ---------------------------------------------------------------- ⑥ 接线不变量（防回退 / 防泄漏）
def test_server_exposes_both_endpoints():
    src = io.open(ROOT / "api" / "server.py", encoding="utf-8").read()
    assert '"/api/tools/sources/cap_abilities"' in src and '"/api/tools/sources/ability_draft"' in src
    seg = src[src.find('/api/tools/sources/ability_draft'):]
    seg = seg[:seg.find("@app.", 10)]
    assert "capability_ability_generate" in seg and "account_id=sess.get" in seg


def test_draft_never_sends_credentials_to_the_model():
    """★ MCP 端点常把部署凭证放在路径段里、命令行/env 可能带 token —— 一律不进模型上下文。"""
    import re as _re
    src = io.open(ROOT / "api" / "customize.py", encoding="utf-8").read()
    seg = src[src.find("def capability_ability_generate("):]
    seg = seg[:seg.find("# ============================ 人格生成 AI")]
    # ① 只允许读这三个 cfg 键：transport/command 用来判断「本地 stdio 还是远程 HTTP」，cwd 用来解析相对 README 路径
    keys = set(_re.findall(r'cfg\.get\("([^"]+)"\)', seg))
    assert keys and keys <= {"transport", "command", "cwd"}, "读了不该读的连接字段：%s" % (keys - {"transport", "command", "cwd"})
    assert {"transport", "command", "cwd"} <= keys, "形态判断与相对路径基准仍需读这三项"
    # ② 端点 URL / 密钥 / 环境变量 / 参数 JSON 一律不出现在这段代码里
    for bad in ("env_json", "auth_secret", "args_json", "base_url", "url_template"):
        assert bad not in seg, "提示词里混进了连接凭据相关字段：%s" % bad
    # ③ 提示词里唯一允许出现的地址是 README 的（用户自己选的文档来源，不是部署凭证）
    assert 'doc.get("url")' in seg
    assert seg.count('.get("url")') == seg.count('doc.get("url")'), "出现了非 README 的地址（可能是端点 URL）"
    assert "source_docs.resolve" in seg and "tool_listing" in seg and "parse_draft" in seg


def test_prompt_has_evidence_and_format_rules():
    yml = io.open(ROOT / "prompt" / "prompts.yml", encoding="utf-8").read()
    seg = yml[yml.find("capability_writer:"):yml.find("memory_initializer:")]
    for needle in ("官方介绍文案", "能力说明的正主", "逐字照抄", "不要凭工具名猜",
                   "关键词", "60 字以内", "接入细节一个字都不要写进能力描述"):
        assert needle in seg, needle


def test_rediscovery_cannot_wipe_user_text():
    """★ 核心承诺：重新「发现 / 导入工具」只写自动摘要列，**绝不碰** `abilities_user`。

    覆盖式写法会让用户手写的描述被下一次发现冲掉（与 M5c-1「人工决定不被自动动作覆盖」同一条教训），
    所以这里按"全文件只有一处能写这一列"来钉。
    """
    src = io.open(ROOT / "api" / "tools_sources.py", encoding="utf-8").read()
    assert src.count("abilities_user=") == 1, "只有 cap_abilities_save 能写这一列"
    assert "abilities_user=?" in src[src.find("def cap_abilities_save("):]
    for fn in ("def discover(", "def _discover_mcp(", "def confirm(", "def source_save("):
        i = src.find(fn)
        assert i > 0, fn
        body = src[i:]
        body = body[:body.find("\ndef ", 10)]
        assert "abilities_user" not in body, "%s 不该碰用户手写的描述" % fn


def test_frontend_wires_the_editor():
    html = io.open(ROOT / "front" / "index.html", encoding="utf-8").read()
    for needle in ("capOpen(s, 'mcp')", "capOpen(s, 'api')", "capDraft()", "capSave()",
                   "/api/tools/sources/ability_draft", "/api/tools/sources/cap_abilities",
                   "abilities_user"):
        assert needle in html, needle
    # README 字段：远程填网址、本地填文件路径（两种形态都要在提示里说清）
    assert "本地文件路径" in html and "网址" in html

# ---------------------------------------------------------------- ⑦ 第二个信息源：官方介绍文案
def test_build_msg_prefers_intro_and_forbids_integration_details():
    """★ 用户反馈的场景：第三方 README 只讲「怎么接入」，能力说明在社区文案里 → 文案当正主。"""
    src = {"source": "mcp", "slug": "pdd", "name": "拼多多精选", "abilities": "购物比价"}
    doc = {"ok": True, "text": "## 安装\nnpm i -g xxx\n## 鉴权\n填 token", "url": "https://x/readme"}
    msg, pre = cd.build_user_msg(src, CAPS, doc, "这里能搜商品、看详情、逛百亿补贴榜单", transport="远程 HTTP")
    assert "官方介绍文案" in msg and "这里能搜商品" in msg
    assert "能力说明优先看这份" in msg                    # 文案是正主
    assert "接入细节不要写进能力描述" in msg or "一个字都不要写进能力描述" in msg
    assert pre == [], "两份都有 → 不该有任何预检告警"


def test_build_msg_intro_only_is_enough():
    """只贴了官方文案（没有 README）→ 正常工作，且不再告警缺依据。"""
    src = {"source": "api", "slug": "xx", "name": "小小API", "abilities": ""}
    msg, pre = cd.build_user_msg(src, CAPS, {"ok": False, "note": "没填 README / 文档地址"},
                                 "IP 归属地查询、抖音热榜等一堆小工具的口子")
    assert "IP 归属地查询" in msg and pre == []


def test_build_msg_warns_when_both_missing():
    """两份都没有 → 仍然拼得出消息，但要明确告警（真正的拦截在后端，见下一条）。"""
    src = {"source": "api", "slug": "xx", "name": "某接口", "abilities": ""}
    msg, pre = cd.build_user_msg(src, CAPS, {"ok": False, "note": "没填"}, "")
    assert pre and "既没填" in pre[0] and "官方介绍文案" in pre[0]


def test_build_msg_caps_both_evidence_sources():
    src = {"source": "api", "slug": "x", "name": "n", "abilities": ""}
    big_intro = "字" * (cd.MAX_INTRO_CHARS + 2000)
    big_doc = {"ok": True, "text": "文" * (cd.MAX_README_CHARS + 2000), "url": "u"}
    msg, _ = cd.build_user_msg(src, CAPS, big_doc, big_intro)
    # 正文被截到上限（模板里也有几个「字」，所以断言区间而不是等值）
    assert cd.MAX_INTRO_CHARS <= msg.count("字") <= cd.MAX_INTRO_CHARS + 60
    assert msg.count("文") <= cd.MAX_README_CHARS + 60
    assert "已截断" in msg
    # 文案（能力说明的正主）的预算必须**比 README 更宽**（用户要求"字数限制放宽一点"）
    assert cd.MAX_INTRO_CHARS > cd.MAX_README_CHARS


def test_draft_refuses_when_no_evidence_source():
    """★ 两个信息源都空 → 后端直接拒绝，文案就是前端提示的那句。"""
    src = io.open(ROOT / "api" / "customize.py", encoding="utf-8").read()
    seg = src[src.find("def capability_ability_generate("):]
    seg = seg[:seg.find("# ============================ 人格生成 AI")]
    assert "没有填信息来源，无法 AI 预生成" in seg
    assert "if not doc_text and not intro_text:" in seg, "两个都没填时必须先拦下"
    i_guard = seg.find("if not doc_text and not intro_text:")
    i_call = seg.find("default_model.invoke")
    assert i_guard < i_call, "★ 拦截必须发生在调模型之前（不能先烧钱再报错）"


def test_intro_column_wired_end_to_end():
    """来源表新增 intro 列：建表 DDL + 老库 ALTER + 两条保存路径 + 加载给撰写 AI。"""
    sch = io.open(ROOT / "tools" / "schema_personal.py", encoding="utf-8").read()
    assert "intro       TEXT NOT NULL DEFAULT ''" in sch
    assert "ALTER TABLE tool_sources ADD COLUMN intro" in sch, "老库要幂等补列（否则保存直接报 no such column）"
    ts = io.open(ROOT / "api" / "tools_sources.py", encoding="utf-8").read()
    assert ts.count("intro=?") == 2, "api 与 mcp 两条保存路径都要写 intro"
    assert "intro: str = \"\"" in ts, "请求模型要有这个字段"
    assert "abilities, docs, intro, state, enabled, note" in ts or "abilities, docs, intro" in ts


def test_frontend_has_the_second_evidence_field():
    html = io.open(ROOT / "front" / "index.html", encoding="utf-8").read()
    assert html.count('v-model="tsForm.intro"') == 1 and html.count('v-model="mcpForm.intro"') == 1
    assert "intro: ''" in html and "intro: String(f.intro || '').trim()" in html
    assert "官方介绍文案" in html
    assert "没有填信息来源，无法 AI 预生成" in html, "前端也要给同一句话（本地预检，省一次空跑）"
    assert "capHasEvidence" in html


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
