# -*- coding: utf-8 -*-
"""M1-2 用例：API 驱动（`tools/_runtime/api_driver.py`）。

**全离线**：用 `httpx.MockTransport` 注入假响应，不联网、不耗任何额度。

覆盖：参数位置分发（path/query/header/body）· URL 模板占位与编码 · 鉴权注入 ·
未知/缺必填参数的可读拒绝 · 非法配置（绝对 url_template、残留占位）· SSRF 口径
（allow_private、模型可控的 URL 参数、同主机 vs 跨主机重定向）· 超时 · 体积截断 ·
状态码与 JSON 解包。

运行：
    .venv/Scripts/python.exe -m pytest tests/test_api_driver.py -q
"""
import json
import sys
import os

import httpx
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from tools._runtime import api_driver as drv  # noqa: E402


def _transport(payload, *, status=200, ctype="application/json", headers=None, raise_exc=None):
    """构造一个只回固定内容的 MockTransport。"""
    def handler(request: httpx.Request) -> httpx.Response:
        if raise_exc is not None:
            raise raise_exc
        body = payload if isinstance(payload, (bytes, str)) else json.dumps(payload, ensure_ascii=False)
        return httpx.Response(status, content=body.encode("utf-8") if isinstance(body, str) else body,
                              headers={"content-type": ctype, **(headers or {})})
    return httpx.MockTransport(handler)


def _cfg(**over):
    cfg = {"base_url": "https://api.example.com", "timeout": 5}
    cfg.update(over)
    return cfg


def _spec(**over):
    spec = {"method": "GET", "url_template": "/v1/city/{city}", "bindings": [
        {"name": "city", "location": "path", "required": True},
        {"name": "lang", "location": "query", "required": False},
    ]}
    spec.update(over)
    return spec


# ---------------------------------------------------------------- build_request（纯函数）
def test_build_basic_url_and_method():
    req = drv.build_request(_cfg(), _spec(), {"city": "chengdu"})
    assert req["url"] == "https://api.example.com/v1/city/chengdu"
    assert req["method"] == "GET"


def test_build_path_value_is_url_encoded():
    req = drv.build_request(_cfg(), _spec(), {"city": "成都 天府"})
    assert "成都%20" not in req["url"] and " " not in req["url"]      # 空格必须被编码
    assert "%E6%88%90%E9%83%BD" in req["url"]                        # 中文按 UTF-8 百分号编码


def test_build_query_binding_goes_to_params():
    req = drv.build_request(_cfg(), _spec(), {"city": "cd", "lang": "zh"})
    assert req["params"]["lang"] == "zh" and "lang" not in req["url"]


def test_build_header_binding_goes_to_headers():
    spec = _spec(url_template="/v1/ping", static_query={},
                 bindings=[{"name": "x-token", "location": "header", "required": False}])
    req = drv.build_request(_cfg(), spec, {"x-token": "abc"})
    assert req["headers"]["x-token"] == "abc"


def test_build_body_binding_json_mode():
    spec = {"method": "POST", "url_template": "/v1/ask", "body_mode": "json", "bindings": [
        {"name": "q", "location": "body", "required": True}]}
    req = drv.build_request(_cfg(), spec, {"q": "你好"})
    assert req["has_body"] and req["body"] == {"q": "你好"} and req["body_mode"] == "json"


def test_build_form_mode():
    spec = {"method": "POST", "url_template": "/v1/ask", "body_mode": "form", "bindings": [
        {"name": "q", "location": "body", "required": True}]}
    req = drv.build_request(_cfg(), spec, {"q": "x"})
    assert req["body_mode"] == "form"


def test_build_auth_header_injection_with_prefix():
    cfg = _cfg(auth={"type": "header", "name": "Authorization", "prefix": "Bearer ", "secret": "sk-1"})
    req = drv.build_request(cfg, _spec(), {"city": "cd"})
    assert req["headers"]["Authorization"] == "Bearer sk-1"


def test_build_auth_query_injection():
    cfg = _cfg(auth={"type": "query", "name": "key", "secret": "k1"})
    req = drv.build_request(cfg, _spec(), {"city": "cd"})
    assert req["params"]["key"] == "k1"


def test_build_rejects_unknown_param():
    with pytest.raises(drv.ApiDriverError) as e:
        drv.build_request(_cfg(), _spec(), {"city": "cd", "bogus": 1})
    assert "不接受参数" in str(e.value) and "bogus" in str(e.value)


def test_build_rejects_missing_required():
    with pytest.raises(drv.ApiDriverError) as e:
        drv.build_request(_cfg(), _spec(), {})
    assert "缺少必填参数" in str(e.value) and "city" in str(e.value)


def test_build_rejects_bad_location():
    spec = _spec(bindings=[{"name": "a", "location": "cookie", "required": False}])
    with pytest.raises(drv.ApiDriverError) as e:
        drv.build_request(_cfg(), spec, {})
    assert "参数位置非法" in str(e.value)


def test_build_rejects_leftover_placeholder():
    spec = _spec(url_template="/v1/{city}/{country}")
    with pytest.raises(drv.ApiDriverError) as e:
        drv.build_request(_cfg(), spec, {"city": "cd"})
    assert "没有对应的参数绑定" in str(e.value)


def test_build_rejects_absolute_url_template():
    spec = _spec(url_template="https://evil.example.com/steal")
    with pytest.raises(drv.ApiDriverError) as e:
        drv.build_request(_cfg(), spec, {"city": "cd"})
    assert "相对路径" in str(e.value)


def test_build_rejects_missing_base_url():
    with pytest.raises(drv.ApiDriverError) as e:
        drv.build_request({"base_url": ""}, _spec(), {"city": "cd"})
    assert "base_url" in str(e.value)


# ---------------------------------------------------------------- SSRF 口径
def test_allow_private_false_blocks_loopback_base_url():
    cfg = _cfg(base_url="http://127.0.0.1:8000", allow_private=False)
    with pytest.raises(drv.ApiDriverError) as e:
        drv.build_request(cfg, _spec(), {"city": "cd"})
    assert "安全策略拒绝" in str(e.value)


def test_loopback_base_url_allowed_by_default():
    """默认允许：base_url 是用户自己填的（自托管/内网 API 是真实场景）。"""
    cfg = _cfg(base_url="http://127.0.0.1:8000")
    req = drv.build_request(cfg, _spec(), {"city": "cd"})
    assert req["url"].startswith("http://127.0.0.1:8000/")


def test_model_supplied_url_arg_to_internal_host_is_blocked():
    """模型可控的参数值若是一个内网 URL → 拒绝（唯一可能被用来打内网的入口）。"""
    spec = _spec(url_template="/v1/fetch", bindings=[{"name": "target", "location": "query", "required": True}])
    with pytest.raises(drv.ApiDriverError) as e:
        drv.build_request(_cfg(), spec, {"target": "http://169.254.169.254/latest/meta-data"})
    assert "地址被安全策略拒绝" in str(e.value)


def test_call_blocks_cross_host_redirect():
    """公网 URL 被 302 到另一个主机 → 不跟随（经典 SSRF 绕过）。"""
    t = _transport("", status=302, headers={"location": "http://127.0.0.1:9200/secret"})
    out = drv.call(_cfg(), _spec(), {"city": "cd"}, transport=t)
    assert out["ok"] is False and "另一个主机" in out["error"]


def test_call_follows_same_host_redirect_once():
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(302, headers={"location": "https://api.example.com/v1/city/chengdu-ok"})
        return httpx.Response(200, json={"ok": True, "path": str(request.url)})

    out = drv.call(_cfg(), _spec(), {"city": "cd"}, transport=httpx.MockTransport(handler))
    assert out["ok"] is True and calls["n"] == 2 and "chengdu-ok" in (out["json"] or {}).get("path", "")


# ---------------------------------------------------------------- call（执行）
def test_call_success_unwraps_json():
    out = drv.call(_cfg(), _spec(), {"city": "cd"}, transport=_transport({"temp": 21, "city": "成都"}))
    assert out["ok"] and out["status"] == 200 and out["json"]["temp"] == 21 and not out["truncated"]


def test_call_non_json_body_keeps_text():
    t = _transport("hello", ctype="text/plain")
    out = drv.call(_cfg(), _spec(), {"city": "cd"}, transport=t)
    assert out["ok"] and out["json"] is None and out["text"] == "hello"


def test_call_truncates_oversize_body():
    cfg = _cfg(max_bytes=100)
    out = drv.call(cfg, _spec(), {"city": "cd"}, transport=_transport("x" * 5000, ctype="text/plain"))
    assert out["truncated"] is True and out["bytes"] == 100


def test_call_reports_http_error_status():
    out = drv.call(_cfg(), _spec(), {"city": "cd"}, transport=_transport({"err": "boom"}, status=500))
    assert out["ok"] is False and out["status"] == 500 and "HTTP 500" in out["error"]


def test_call_timeout_is_readable():
    t = _transport("", raise_exc=httpx.TimeoutException("timeout"))
    out = drv.call(_cfg(), _spec(), {"city": "cd"}, transport=t)
    assert out["ok"] is False and "超时" in out["error"]


def test_call_bad_args_returns_error_not_raise():
    """网关调用时不能抛异常：参数错也要变成可读的 ok=False。"""
    out = drv.call(_cfg(), _spec(), {}, transport=_transport({"a": 1}))
    assert out["ok"] is False and "缺少必填参数" in out["error"]


def test_call_records_source_arm():
    out = drv.call(_cfg(), _spec(), {"city": "cd"}, transport=_transport({"a": 1}))
    assert out.get("source") == "direct"      # 第一臂（直连）命中


def test_static_query_is_sent_alongside_bindings():
    """★ 固定 query（cc/l 这类）必须和模型传的参数一起发出去，且**不需要模型知道**。"""
    spec = _spec(url_template="/api/storesearch/", static_query={"cc": "cn", "l": "schinese"},
                 bindings=[{"name": "term", "location": "query", "required": True}])
    req = drv.build_request(_cfg(), spec, {"term": "elden ring"})
    assert req["params"]["cc"] == "cn" and req["params"]["l"] == "schinese"
    assert req["params"]["term"] == "elden ring"
    assert req["url"].endswith("/api/storesearch/")


def test_query_inside_url_template_is_merged_not_dropped():
    """★ 真机踩到：`url_template` 自带 query 时，httpx 的 `params=` 会把原有 query **替换**掉
    （Steam storesearch 的 cc/l 就这样丢了 → 接口返回空结果）。驱动必须把它并进 params。"""
    spec = _spec(url_template="/api/storesearch/?cc=cn&l=schinese",
                 bindings=[{"name": "term", "location": "query", "required": True}])
    req = drv.build_request(_cfg(), spec, {"term": "Blasphemous"})
    assert "?" not in req["url"] and req["url"].endswith("/api/storesearch/")
    assert req["params"]["cc"] == "cn" and req["params"]["l"] == "schinese"
    assert req["params"]["term"] == "Blasphemous"
    # 模型显式给的值优先于模板里的固定值
    spec2 = _spec(url_template="/a?cc=us", bindings=[{"name": "cc", "location": "query", "required": False}])
    assert drv.build_request(_cfg(), spec2, {"cc": "cn"})["params"]["cc"] == "cn"
