# -*- coding: utf-8 -*-
"""M1-3 用例：OpenAPI → 候选能力（`tools/openapi_import.py`）。**全离线**。

三份"真实形态"的 spec 片段：① 带 $ref 与三类参数 ② 无 operationId + 已废弃 + cookie 参数
③ form-urlencoded + operationId 重名。外加输入容错与 YAML 支持。

运行：
    .venv/Scripts/python.exe -m pytest tests/test_openapi_import.py -q
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from tools import openapi_import as oi  # noqa: E402

SPEC_A = {
    "openapi": "3.0.3",
    "info": {"title": "Demo Weather", "version": "1.0.0"},
    "servers": [{"url": "https://api.demo.com/v1"}],
    "paths": {
        "/current/{city}": {
            "get": {
                "operationId": "getCurrent",
                "summary": "当前天气",
                "tags": ["weather"],
                "parameters": [
                    {"name": "city", "in": "path", "required": True,
                     "schema": {"type": "string"}, "description": "城市名"},
                    {"name": "lang", "in": "query", "required": False,
                     "schema": {"type": "string", "enum": ["zh", "en"]}},
                    {"name": "X-Trace", "in": "header", "required": False, "schema": {"type": "string"}},
                ],
                "responses": {"200": {"description": "ok"}},
            }
        },
        "/orders": {
            "post": {
                "operationId": "createOrder",
                "summary": "下单（写操作）",
                "requestBody": {"$ref": "#/components/requestBodies/Order"},
                "responses": {"201": {"description": "created"}},
            }
        },
    },
    "components": {
        "requestBodies": {
            "Order": {"content": {"application/json": {"schema": {"$ref": "#/components/schemas/Order"}}}}
        },
        "schemas": {
            "Order": {"type": "object", "required": ["sku", "qty"],
                      "properties": {"sku": {"type": "string"}, "qty": {"type": "integer"},
                                     "note": {"type": "string"}}}
        },
    },
}

SPEC_B = {
    "openapi": "3.0.0",
    "paths": {
        "/v1/items/{id}": {
            "parameters": [{"name": "id", "in": "path", "required": True, "schema": {"type": "integer"}}],
            "get": {"summary": "取详情",
                    "parameters": [{"name": "session", "in": "cookie", "required": False,
                                    "schema": {"type": "string"}}]},
            "delete": {"operationId": "delItem", "deprecated": True},
        },
        "/ping": {"get": {}},
    },
}

SPEC_C = {
    "openapi": "3.0.0",
    "paths": {
        "/a": {"post": {"operationId": "doIt", "requestBody": {"content": {
            "application/x-www-form-urlencoded": {"schema": {
                "type": "object", "required": ["x"], "properties": {"x": {"type": "string"}}}}}}}},
        "/b": {"post": {"operationId": "doIt", "requestBody": {"content": {
            "application/x-www-form-urlencoded": {"schema": {
                "type": "object", "properties": {"y": {"type": "string"}}}}}}}},
    },
}


def _parse(spec, slug="weather", base_url=""):
    return oi.parse_spec(json.dumps(spec, ensure_ascii=False), slug, base_url)


def _by_op(res, op):
    return next(c for c in res["candidates"] if c["op"] == op)


# ---------------------------------------------------------------- SPEC A
def test_a_counts_and_base_url_hint():
    res = _parse(SPEC_A)
    assert res["ok"] and len(res["candidates"]) == 2
    assert res["base_url"] == "https://api.demo.com/v1" and res["title"] == "Demo Weather"


def test_a_ref_format_is_slug_hash_op():
    res = _parse(SPEC_A)
    assert {c["ref"] for c in res["candidates"]} == {"api:weather#getCurrent", "api:weather#createOrder"}


def test_a_get_is_readonly_and_template_kept():
    c = _by_op(_parse(SPEC_A), "getCurrent")
    assert c["read_only"] is True and c["method"] == "GET" and c["url_template"] == "/current/{city}"


def test_a_bindings_locations_and_required():
    c = _by_op(_parse(SPEC_A), "getCurrent")
    b = {x["name"]: x for x in c["invoke_spec"]["bindings"]}
    assert b["city"]["location"] == "path" and b["city"]["required"] is True
    assert b["lang"]["location"] == "query" and b["lang"]["required"] is False
    assert b["X-Trace"]["location"] == "header"


def test_a_input_schema_required_and_props():
    c = _by_op(_parse(SPEC_A), "getCurrent")
    sch = c["input_schema"]
    assert sch["required"] == ["city"] and set(sch["properties"]) == {"city", "lang", "X-Trace"}
    assert sch["properties"]["lang"]["enum"] == ["zh", "en"]


def test_a_post_is_marked_write():
    c = _by_op(_parse(SPEC_A), "createOrder")
    assert c["read_only"] is False and c["method"] == "POST"


def test_a_ref_inlined_from_components():
    """$ref 必须被内联：模型不该看到 '#/components/schemas/Order' 这种引用。"""
    c = _by_op(_parse(SPEC_A), "createOrder")
    names = {b["name"] for b in c["invoke_spec"]["bindings"]}
    assert {"sku", "qty", "note"} <= names
    assert c["input_schema"]["required"] == ["sku", "qty"]
    blob = json.dumps(c, ensure_ascii=False)
    assert "$ref" not in blob and "components/schemas" not in blob


def test_a_auth_defaults_to_inherit():
    c = _by_op(_parse(SPEC_A), "getCurrent")
    assert c["invoke_spec"]["auth"] == "inherit" and c["invoke_spec"]["body_mode"] == "json"


# ---------------------------------------------------------------- SPEC B
def test_b_fallback_operation_name_when_missing():
    res = _parse(SPEC_B)
    ops = {c["op"] for c in res["candidates"]}
    assert "get_v1_items_id" in ops and "get_ping" in ops


def test_b_path_level_parameters_are_merged():
    """path 级 parameters 要并进每个操作（否则必填校验会漏 id）。"""
    c = _by_op(_parse(SPEC_B), "get_v1_items_id")
    b = {x["name"]: x for x in c["invoke_spec"]["bindings"]}
    assert b["id"]["location"] == "path" and b["id"]["required"] is True
    assert "id" in c["input_schema"]["required"]


def test_b_cookie_param_ignored_with_warning():
    c = _by_op(_parse(SPEC_B), "get_v1_items_id")
    names = {b["name"] for b in c["invoke_spec"]["bindings"]}
    assert "session" not in names
    assert any("cookie" in w for w in c["warnings"])


def test_b_deprecated_and_delete_write():
    c = _by_op(_parse(SPEC_B), "delItem")
    assert c["deprecated"] is True and c["read_only"] is False


# ---------------------------------------------------------------- SPEC C
def test_c_form_body_mode_and_required():
    res = _parse(SPEC_C)
    first = next(c for c in res["candidates"] if c["url_template"] == "/a")
    assert first["invoke_spec"]["body_mode"] == "form"
    assert first["input_schema"]["required"] == ["x"]


def test_c_duplicate_operation_id_is_suffixed():
    res = _parse(SPEC_C)
    refs = [c["ref"] for c in res["candidates"]]
    assert len(refs) == len(set(refs)) == 2 and any(r.endswith("_2") for r in refs)
    dup = next(c for c in res["candidates"] if c["url_template"] == "/b")
    assert any("重复" in w for w in dup["warnings"])


# ---------------------------------------------------------------- 输入容错
def test_empty_input_is_rejected_readably():
    res = oi.parse_spec("   ", "weather")
    assert res["ok"] is False and "空" in res["error"] and res["candidates"] == []


def test_html_input_is_rejected_as_not_openapi():
    res = oi.parse_spec("<html><body>404 Not Found</body></html>", "weather")
    assert res["ok"] is False and "不像是 OpenAPI" in res["error"]


def test_yaml_input_supported():
    y = """
openapi: 3.0.0
paths:
  /t:
    get:
      operationId: ping
      parameters:
        - name: q
          in: query
          required: true
          schema: {type: string}
"""
    res = oi.parse_spec(y, "svc")
    assert res["ok"] and res["candidates"][0]["ref"] == "api:svc#ping"
    assert res["candidates"][0]["input_schema"]["required"] == ["q"]


def test_slug_is_taken_from_caller_not_spec():
    res = oi.parse_spec(json.dumps(SPEC_A), "wf")
    assert all(c["ref"].startswith("api:wf#") for c in res["candidates"])


# ---------------------------------------------------------------- 固定 query 参数（真机回归 2026-09-27）
def test_path_query_becomes_static_query():
    """★ 真机：Steam `storesearch` 不带 cc/l 直接返回空结果（{"total":0,"items":[]}），
    而这两个值只有一个正确答案（国区/中文）。支持把固定参数写进路径 → 拆进 `static_query`，
    路径本身保持干净，模型也看不到这两个参数（不用它填）。"""
    spec = json.dumps({
        "openapi": "3.0.0", "info": {"title": "steam", "version": "1"},
        "paths": {"/api/storesearch/?cc=cn&l=schinese": {"get": {
            "operationId": "searchGame", "summary": "搜索游戏",
            "parameters": [{"name": "term", "in": "query", "required": True,
                            "schema": {"type": "string"}}]}}},
    })
    c = oi.parse_spec(spec, "steam")["candidates"][0]
    assert c["url_template"] == "/api/storesearch/"                      # 路径干净
    assert c["invoke_spec"]["static_query"] == {"cc": "cn", "l": "schinese"}
    assert "term" in c["input_schema"]["properties"] and "cc" not in c["input_schema"]["properties"]
    assert any("已固定 query 参数" in w for w in c["warnings"])


def test_x_static_query_extension_and_percent_decoding():
    spec = json.dumps({
        "openapi": "3.0.0", "info": {"title": "t", "version": "1"},
        "paths": {
            "/a?x=1%202&y=%E4%B8%AD": {"get": {"operationId": "a"}},          # 路径写法 + 解码
            "/b": {"get": {"operationId": "b", "x-static-query": {"cc": "cn"}}},   # 扩展位写法
            "/c": {"get": {"operationId": "c"}},                              # 不带固定参数
        },
    })
    c = {x["op"]: x for x in oi.parse_spec(spec, "t")["candidates"]}
    assert c["a"]["invoke_spec"]["static_query"] == {"x": "1 2", "y": "中"}
    assert c["a"]["url_template"] == "/a"
    assert c["b"]["invoke_spec"]["static_query"] == {"cc": "cn"}
    assert c["c"]["invoke_spec"]["static_query"] == {}
