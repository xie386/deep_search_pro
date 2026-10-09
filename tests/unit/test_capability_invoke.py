# -*- coding: utf-8 -*-
"""M1-5 用例：统一执行网关（`tools/capability_invoke.py`）。**全离线**（假 API + 临时账号）。

覆盖：ref 解析 · 四类拒绝（未知 ref / 未确认 / 未启用 / 缺必填）· 类型与枚举校验 ·
写能力放行判定 · 成功路径（MockTransport）· 结果文本 · **审计行字段** · CLI ref 引导。

运行：
    .venv/Scripts/python.exe -m pytest tests/test_capability_invoke.py -q
"""
import json
import os
import sys
import time

import httpx
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from tools import capability_invoke as ci  # noqa: E402
from tools.schema_personal import get_personal_conn, purge_account  # noqa: E402

SPEC = {"method": "GET", "url_template": "/current/{city}", "bindings": [
    {"name": "city", "location": "path", "required": True},
    {"name": "lang", "location": "query", "required": False}], "auth": "inherit"}
SCHEMA = {"type": "object", "properties": {
    "city": {"type": "string"}, "lang": {"type": "string", "enum": ["zh", "en"]}},
    "required": ["city"]}


def _transport(payload, status=200):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json=payload)
    return httpx.MockTransport(handler)


@pytest.fixture()
def env():
    """临时账号 + 一条来源 +（默认）一条已确认的只读能力。结束时清理。"""
    conn = get_personal_conn()
    aid = None
    try:
        uname = "m1inv_%d" % int(time.time())
        aid = int(conn.execute("INSERT INTO accounts (username, password_hash, role) VALUES (?,?,?)",
                               (uname, "x", "user")).lastrowid)
        sid = int(conn.execute(
            "INSERT INTO tool_sources (account_id, source, slug, name, config_json, state, enabled) "
            "VALUES (?,?,?,?,?,?,1)",
            (aid, "api", "weather", "和风天气",
             json.dumps({"base_url": "https://api.demo.com", "timeout": 5}), "verified")).lastrowid)
        spec = dict(SPEC, source_id=sid)
        conn.execute(
            "INSERT INTO tool_capabilities (account_id, source, ref, name, keywords, abilities, invoke_hint, "
            "enabled, invoke_spec, input_schema, read_only, confirmed_at) VALUES (?,?,?,?,?,?,?,1,?,?,1,?)",
            (aid, "api", "api:weather#getCurrent", "当前天气", "天气", "查实时天气", "GET /current/{city}",
             json.dumps(spec, ensure_ascii=False), json.dumps(SCHEMA, ensure_ascii=False), "2026-09-25 10:00:00"))
        conn.commit()
    finally:
        conn.close()
    yield {"aid": aid}
    purge_account(aid)


def _audit_last():
    from tools._runtime.shell_runtime import AUDIT_FILE
    line = open(AUDIT_FILE, encoding="utf-8").read().strip().splitlines()[-1]
    return json.loads(line)


# ---------------------------------------------------------------- ref 解析
def test_parse_ref_api_and_mcp():
    assert ci.parse_ref("api:weather#getCurrent") == ("api", "weather", "getCurrent")
    assert ci.parse_ref("mcp:fs/read_file") == ("mcp", "fs", "read_file")


@pytest.mark.parametrize("bad", ["", "weather", "api:weather", "api:#x", "mcp:fs", "ftp:a#b"])
def test_parse_ref_rejects_bad_forms(bad):
    with pytest.raises(ci.InvokeRejected):
        ci.parse_ref(bad)


def test_unknown_ref_rejected_readably(env):
    out = ci.invoke("api:weather#nope", {}, account_id=env["aid"])
    assert out["ok"] is False and "没有这个能力" in out["text"] and out["rejected"] == "bad_ref" or out["rejected"]


def test_cli_ref_points_to_run_shell_command():
    """invoke_tool 不处理 CLI：判定函数必须把它引导回 run_shell_command。"""
    ok, why = ci.readonly_verdict("cli", {}, {})
    assert ok is False and "run_shell_command" in why


# ---------------------------------------------------------------- 四类拒绝
def test_unconfirmed_capability_rejected(env):
    conn = get_personal_conn()
    try:
        conn.execute("UPDATE tool_capabilities SET confirmed_at=NULL WHERE account_id=?", (env["aid"],))
        conn.commit()
    finally:
        conn.close()
    out = ci.invoke("api:weather#getCurrent", {"city": "cd"}, account_id=env["aid"])
    assert out["ok"] is False and "人工确认" in out["text"]


def test_disabled_capability_rejected(env):
    conn = get_personal_conn()
    try:
        conn.execute("UPDATE tool_capabilities SET enabled=0 WHERE account_id=?", (env["aid"],))
        conn.commit()
    finally:
        conn.close()
    out = ci.invoke("api:weather#getCurrent", {"city": "cd"}, account_id=env["aid"])
    assert out["ok"] is False and "未启用" in out["text"]


def test_missing_required_arg_rejected(env):
    out = ci.invoke("api:weather#getCurrent", {}, account_id=env["aid"])
    assert out["ok"] is False and "缺少必填参数 city" in out["text"]


def test_wrong_type_arg_rejected(env):
    """★ 2026-10-06 起「数字/字符串互转」不再算错（见 test_stringy_args_are_coerced）；
    这条改成用**真正无法转换**的类型（对象传给 string 参数）来断言拒绝。"""
    out = ci.invoke("api:weather#getCurrent", {"city": {"a": 1}}, account_id=env["aid"])
    assert out["ok"] is False and "类型应为 string" in out["text"]


def test_stringy_args_are_coerced():
    """模型爱把标量写成字符串（"15" / "true"）或把数组写成 JSON 字符串（"[1,2]"）。

    真机实测（2026-10-05 23:25 那轮）19 次调用里 9 次因这类类型不符被直接拒，
    模型只能用原生类型重发 → 一次查询变成三连发。故先宽容转换、再校验；
    转不了的仍报错（且错误信息保持可行动）。
    """
    schema = {"properties": {"max_results": {"type": "integer"}, "full_metadata": {"type": "boolean"},
                             "date": {"type": "string"}, "ids": {"type": "array"}}}
    args, notes = ci.coerce_args(schema, {"max_results": "15", "full_metadata": "true",
                                          "date": 1871, "ids": "[1,2]"})
    assert args == {"max_results": 15, "full_metadata": True, "date": "1871", "ids": [1, 2]}
    assert len(notes) == 4
    assert ci.validate_args(schema, args) == []
    # 原生类型不动，也不产生说明
    same, none_notes = ci.coerce_args(schema, {"max_results": 20, "full_metadata": True})
    assert same == {"max_results": 20, "full_metadata": True} and none_notes == []
    # 转不了的原样留下，校验照样报错
    bad, _ = ci.coerce_args(schema, {"max_results": "abc", "full_metadata": "许"})
    assert bad == {"max_results": "abc", "full_metadata": "许"}
    assert "类型应为 integer" in "；".join(ci.validate_args(schema, bad))


def test_enum_violation_rejected(env):
    out = ci.invoke("api:weather#getCurrent", {"city": "cd", "lang": "fr"}, account_id=env["aid"])
    assert out["ok"] is False and "只能是 zh/en" in out["text"]


def test_unknown_arg_rejected(env):
    out = ci.invoke("api:weather#getCurrent", {"city": "cd", "bogus": 1}, account_id=env["aid"])
    assert out["ok"] is False and "不认识的参数" in out["text"]


def test_write_capability_without_confirmation_is_rejected():
    allow, why = ci.readonly_verdict("api", {"read_only": 0, "confirmed_at": None}, {})
    assert allow is False and "写操作" in why


def test_write_capability_after_confirmation_is_allowed():
    allow, why = ci.readonly_verdict("api", {"read_only": 0, "confirmed_at": "2026-09-25"}, {})
    assert allow is True and why == ""


def test_disabled_source_rejected(env):
    conn = get_personal_conn()
    try:
        conn.execute("UPDATE tool_sources SET enabled=0 WHERE account_id=?", (env["aid"],))
        conn.commit()
    finally:
        conn.close()
    out = ci.invoke("api:weather#getCurrent", {"city": "cd"}, account_id=env["aid"])
    assert out["ok"] is False and "已被停用" in out["text"]


# ---------------------------------------------------------------- 成功路径 + 审计
def test_success_path_returns_body_and_writes_audit(env):
    out = ci.invoke("api:weather#getCurrent", {"city": "成都"},
                    account_id=env["aid"], transport=_transport({"temp": 21, "city": "成都"}))
    assert out["ok"] is True and out["status"] == 200
    assert '"temp":21' in out["text"] and "api:weather#getCurrent" in out["text"]   # 紧凑 JSON（无缩进）
    row = _audit_last()
    assert row["tool"] == "invoke_tool" and row["source"] == "api" and row["ref"] == "api:weather#getCurrent"
    assert row["ok"] is True and row["read_only"] is True and len(row["args_digest"]) == 16
    assert row["args_keys"] == ["city"] and row["status"] == 200


def test_result_text_never_exceeds_cap(env):
    """★ 真机逮到：上限只裁 payload、前缀与尾注留在外面 → 整段被你撑破（实测 6085 > 6000）。
    上限是对**模型看到的整段文本**生效的。"""
    big = {"items": [{"i": i, "pad": "x" * 200} for i in range(400)]}       # 远超 6000 字符
    out = ci.invoke("api:weather#getCurrent", {"city": "成都"},
                    account_id=env["aid"], transport=_transport(big))
    assert out["ok"] is True
    assert len(out["text"]) <= ci.MAX_RESULT_CHARS, len(out["text"])
    assert "…（内容过长已截断）" in out["text"]
    assert "如需其他能力" in out["text"]            # 尾注仍在（不是被砍掉）
    assert _audit_last()["chars"] == len(out["text"])


def test_api_http_error_is_reported(env):
    out = ci.invoke("api:weather#getCurrent", {"city": "cd"},
                    account_id=env["aid"], transport=_transport({"e": "boom"}, status=500))
    assert out["ok"] is False and "调用失败" in out["text"] and out["status"] == 500
    assert _audit_last()["ok"] is False


def test_rejection_is_audited(env):
    ci.invoke("api:weather#getCurrent", {}, account_id=env["aid"])
    row = _audit_last()
    assert row["ok"] is False and "缺少必填参数" in row["rejected"]


def test_write_warning_prefix_when_not_readonly(env):
    conn = get_personal_conn()
    try:
        conn.execute("UPDATE tool_capabilities SET read_only=0 WHERE account_id=?", (env["aid"],))
        conn.commit()
    finally:
        conn.close()
    out = ci.invoke("api:weather#getCurrent", {"city": "cd"},
                    account_id=env["aid"], transport=_transport({"ok": True}))
    assert out["ok"] is True and "写操作" in out["text"]
    assert _audit_last()["read_only"] is False


def test_tool_wrapper_returns_text(env):
    """Agent 侧入口：`invoke_tool` 必须返回字符串（模型可读），不抛异常。

    这里直接调被 `@tool` 包装的原函数（`.func`）——langchain 的分派对 `dict` 参数有额外约定，
    测的是"包装体不会抛异常、且返回 str"这件事。
    """
    txt = ci.invoke_tool.func("api:weather#nope", {})
    assert isinstance(txt, str) and txt.strip()
    # 工具的入参契约（模型看到的那一份）——必须是 ref + params，
    # 不能出现 langchain 的保留名 v__args（那是把参数名写成 `args` 导致的，实测踩过）
    assert set(ci.invoke_tool.args) == {"ref", "params"}
    assert set(ci.invoke_tool.tool_call_schema.model_fields) == {"ref", "params"}


def test_no_account_context_is_readable():
    out = ci.invoke("api:weather#getCurrent", {"city": "cd"}, account_id=None)
    assert out["ok"] is False and ("登录账号" in out["text"] or "没有这个能力" in out["text"])


# ---------------------------------------------------------------- params 写法宽容（真机报障回归）
def test_params_accepts_json_string(env):
    """★ 真机报障（2026-09-27）：模型把 params 传成 **JSON 字符串** —— 裸注解会在 langchain 的
    入参校验阶段就抛 `params: Input should be a valid dictionary`，用户看到的是"这两个工具用不了"。
    网关必须把这种"差不多对"的写法接住。"""
    out = ci.invoke("api:weather#getCurrent", '{"city": "成都"}',
                    account_id=env["aid"], transport=_transport({"temp": 21}))
    assert out["ok"] is True and '"temp":21' in out["text"], out


def test_params_none_or_blank_is_empty(env):
    """None / 空串当 `{}`：应继续走**参数校验**（报缺 city），而不是报 params 类型错。"""
    for blank in (None, "", "   "):
        out = ci.invoke("api:weather#getCurrent", blank, account_id=env["aid"],
                        transport=_transport({"temp": 21}))
        assert out["ok"] is False and "city" in out["text"], (blank, out)
        assert "params 需要是 JSON 对象" not in out["text"]


def test_params_bad_type_gives_readable_reason(env):
    out = ci.invoke("api:weather#getCurrent", ["city"], account_id=env["aid"], transport=_transport({"temp": 21}))
    assert out["ok"] is False and "params 需要是 JSON 对象" in out["text"] and "list" in out["text"], out
    out2 = ci.invoke("api:weather#getCurrent", "{不是json", account_id=env["aid"], transport=_transport({"temp": 21}))
    assert out2["ok"] is False and "不是合法 JSON" in out2["text"], out2


def test_tool_call_schema_accepts_string_params_and_still_says_object():
    """工具对外的 JSON Schema 仍是 `params: object`（不鼓励模型写字符串），
    但 `mode="before"` 校验器接得住字符串（langchain 那一层不再抛 Input should be a valid dictionary）。"""
    assert "object" in json.dumps(ci.invoke_tool.args["params"], ensure_ascii=False)   # 仍告诉模型"传对象"
    sch = ci.invoke_tool.tool_call_schema
    # ★ 关键：**不再抛 ValidationError**。`tool_call_schema` 是 langchain 重新生成的模型，不保留我们的
    #   `mode="before"` 校验器 → 字符串会原样通过；真正的归一在**工具函数体**（网关 `coerce_params`），
    #   由 `test_params_accepts_json_string` 钉住。
    m = sch.model_validate({"ref": "api:weather#getCurrent", "params": '{"city": "成都"}'})
    assert m.params in ('{"city": "成都"}', {"city": "成都"})
    assert sch.model_validate({"ref": "x"}).params is None        # 不传 → None（工具体当 {}）
    assert sch.model_validate({"ref": "x", "params": None}).params is None    # null 也放行
    with pytest.raises(Exception) as e:
        sch.model_validate({"ref": "x", "params": ["city"]})
    assert "params" in str(e.value)

def test_clip_payload_keeps_whole_items():
    """★ 2026-10-06：截断按**完整条目**裁（旧实现硬切字符，模型看到半个 JSON）。"""
    items = [{"id": i, "title": "x" * 40} for i in range(50)]
    txt, cut = ci.clip_payload(items, 600)
    assert cut is True and txt.endswith("…（后续条目未列出）")
    n = txt.count('"id"')
    assert n >= 1
    body = json.loads(txt.replace("…（后续条目未列出）", ""))
    assert len(body) == n and body[-1]["id"] == n - 1


def test_clip_payload_trims_inner_record_list():
    """顶层对象里带记录列表（如 {"records": [...]}）→ 只裁那个列表并说明。"""
    body = {"total": 99, "records": [{"i": i, "t": "y" * 30} for i in range(40)]}
    txt, cut = ci.clip_payload(body, 700)
    assert cut is True
    out = json.loads(txt)
    assert out["total"] == 99 and len(out["records"]) < 40
    assert "_local_note" in out


def test_clip_payload_passthrough_when_small():
    txt, cut = ci.clip_payload({"a": 1}, 1000)
    assert cut is False and json.loads(txt) == {"a": 1}


def test_paging_hint_only_when_schema_has_paging_args():
    hint = ci._paging_hint({"properties": {"query": {"type": "string"}, "page": {"type": "integer"}}})
    assert "page" in hint and "不要把同一个查询原样再发一次" in hint
    assert ci._paging_hint({"properties": {"query": {"type": "string"}}}) == ""
