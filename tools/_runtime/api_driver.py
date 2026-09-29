# -*- coding: utf-8 -*-
"""API 驱动：把「用户配的 API 来源 + 一条能力的 invoke_spec」变成一次真实 HTTP 调用。

设计口径见 `docs/v3.0/M1M2-API与MCP适配框架方案.md` §5.3（M1-2）。要点：

1. **参数按 bindings 分发**到 path / query / header / body 四个位置（OpenAPI 的
   `in` 语义），URL 里的 `{name}` 占位由 path 绑定替换。
2. **超时与体积上限**：默认 30s / 1MB（与 CLI 沙箱同一口径），超出只截断不报错。
3. **鉴权**：`auth.type = header | query | none` + 名称/前缀/密钥（密钥存在来源
   config_json 里，不进 tool_capabilities）。
4. **SSRF 口径（本文件的取舍，已与方案 §M1-2 的初稿不同，见 §风险）**：
   - `base_url` 是**用户自己填的**，因此**默认允许内网/环回**（自托管、公司内网 API、
     本地服务都是真实场景）；要收紧可设 `config.allow_private=false`。
   - **模型可控的参数值**若本身是一个 URL（http/https）→ 一律走
     `cli_docs._host_is_safe` 守卫（这是唯一可能被模型用来打内网的入口）。
   - **重定向**：不跟随；仅当 `Location` 与配置同主机时**手动跟随一次**
     （挡住"公网 URL 被 302 到内网"这类经典绕过，又不破坏合法的尾斜杠跳转）。
5. 代理兼容：沿用 `cli_docs` 的两臂做法——先 `trust_env=False` 直连，失败再走系统代理。
"""
from __future__ import annotations

import json
import time
from urllib.parse import unquote, urlparse

import httpx

DEFAULT_TIMEOUT = 30.0
DEFAULT_MAX_BYTES = 1024 * 1024
ALLOWED_LOCATIONS = ("path", "query", "header", "body")


class ApiDriverError(Exception):
    """参数/配置层面的错误（可读，直接给模型看）。"""


def _host_is_safe(host: str):
    """复用 cli_docs 的 SSRF 守卫（主机名解析后不能落在环回/私有/链路本地/保留网段）。"""
    from tools import cli_docs
    return cli_docs._host_is_safe(host)


def _looks_like_url(v) -> bool:
    return isinstance(v, str) and v.strip().lower().startswith(("http://", "https://"))


def _bindings(spec: dict) -> list[dict]:
    out = []
    for b in spec.get("bindings") or []:
        if not isinstance(b, dict):
            continue
        loc = (b.get("location") or "").strip()
        if loc and loc not in ALLOWED_LOCATIONS:
            raise ApiDriverError("能力的参数位置非法：%s（只支持 %s）" % (loc, "/".join(ALLOWED_LOCATIONS)))
        out.append({"name": b.get("name") or "", "location": loc, "required": bool(b.get("required"))})
    return out


def build_request(source_cfg: dict, spec: dict, args: dict) -> dict:
    """纯函数：算好 method / url / headers / params / body。不发起请求，便于单测。"""
    base_url = (source_cfg.get("base_url") or "").strip().rstrip("/")
    if not base_url:
        raise ApiDriverError("该来源还没填 base_url")
    if not base_url.lower().startswith(("http://", "https://")):
        raise ApiDriverError("base_url 必须以 http:// 或 https:// 开头")
    if not source_cfg.get("allow_private", True):
        safe, why = _host_is_safe(urlparse(base_url).hostname or "")
        if not safe:
            raise ApiDriverError("base_url 被安全策略拒绝：" + why)

    method = (spec.get("method") or "GET").strip().upper()
    tpl = spec.get("url_template") or "/"
    if _looks_like_url(tpl):
        raise ApiDriverError("url_template 必须是相对路径（主机名由 base_url 决定）")

    binds = _bindings(spec)
    known = {b["name"] for b in binds if b["name"]}
    given = {k: v for k, v in (args or {}).items() if v is not None}
    unknown = sorted(set(given) - known)
    if unknown:
        raise ApiDriverError("该能力不接受参数：%s（可用：%s）"
                             % (", ".join(unknown), ", ".join(sorted(known)) or "无"))
    missing = [b["name"] for b in binds if b["required"] and (b["name"] not in given or given[b["name"]] == "")]
    if missing:
        raise ApiDriverError("缺少必填参数：%s" % ", ".join(missing))

    headers = {str(k): str(v) for k, v in (source_cfg.get("headers") or {}).items()}
    params = dict(spec.get("static_query") or {})
    body = dict(spec.get("static_body") or {})
    # ★ `url_template` 里若自带 query（`/api/storesearch/?cc=cn`）必须并进 params ——
    #   httpx 传了 `params=` 会**替换**URL 里原有的 query（真机踩到：Steam storesearch 的 cc/l 写在
    #   路径里，传模型参数后被丢掉 → 接口返回空结果 `{"total":0,"items":[]}`）。
    #   放在绑定循环**之前**：模型显式给的参数仍然优先于模板里的固定值。
    if "?" in tpl:
        tpl, _sep, raw_q = tpl.partition("?")
        for kv in raw_q.split("&"):
            if not kv.strip():
                continue
            k, _e, v = kv.partition("=")
            if k.strip():
                params.setdefault(unquote(k.strip()), unquote(v.strip()))
    url = tpl

    for b in binds:
        name = b["name"]
        if not name or name not in given:
            continue
        val = given[name]
        # ★ 模型可控值若是 URL → 守卫（唯一可能被用来打内网的入口）
        if _looks_like_url(val):
            safe, why = _host_is_safe(urlparse(val.strip()).hostname or "")
            if not safe:
                raise ApiDriverError("参数 %s 的地址被安全策略拒绝：%s" % (name, why))
        loc = b["location"] or "query"
        if loc == "path":
            from urllib.parse import quote
            url = url.replace("{%s}" % name, quote(str(val), safe=""))
        elif loc == "query":
            params[name] = val
        elif loc == "header":
            headers[name] = str(val)
        else:
            body[name] = val

    # URL 模板里剩下的占位（没绑定的）直接报错，避免把 "{x}" 原样发出去
    if "{" in url and "}" in url:
        left = url[url.find("{"): url.find("}") + 1]
        raise ApiDriverError("URL 模板里的占位 %s 没有对应的参数绑定" % left)

    # 鉴权
    auth = source_cfg.get("auth") or {}
    atype = (auth.get("type") or "none").strip().lower()
    if atype in ("header", "query") and auth.get("secret"):
        val = "%s%s" % (auth.get("prefix") or "", auth["secret"])
        if atype == "header":
            headers[auth.get("name") or "Authorization"] = val
        else:
            params[auth.get("name") or "key"] = val

    full = base_url + (url if url.startswith("/") else "/" + url)
    return {"method": method, "url": full, "headers": headers, "params": params,
            "body": body, "body_mode": (spec.get("body_mode") or "json").strip().lower(),
            "timeout": float(source_cfg.get("timeout") or DEFAULT_TIMEOUT),
            "max_bytes": int(source_cfg.get("max_bytes") or DEFAULT_MAX_BYTES),
            "has_body": any(b["location"] == "body" for b in binds) or bool(spec.get("static_body"))}


def _send(client: httpx.Client, req: dict) -> httpx.Response:
    kwargs = {"headers": req["headers"], "params": req["params"]}
    if req["method"] not in ("GET", "HEAD", "DELETE"):
        if req["has_body"]:
            if req["body_mode"] == "form":
                kwargs["data"] = req["body"]
            else:
                kwargs["json"] = req["body"]
    return client.request(req["method"], req["url"], **kwargs)


def call(source_cfg: dict, spec: dict, args: dict, *, transport=None) -> dict:
    """执行一次 API 调用。``transport`` 仅供单测注入 `httpx.MockTransport`。"""
    t0 = time.time()
    try:
        req = build_request(source_cfg, spec, args)
    except ApiDriverError as e:
        return {"ok": False, "status": None, "text": "", "json": None, "bytes": 0,
                "truncated": False, "elapsed_ms": 0, "location": None, "error": str(e)}

    timeout = httpx.Timeout(req["timeout"])
    last_err = "未知错误"
    for tag, trust_env in (("direct", False), ("proxy", True)):
        try:
            with httpx.Client(timeout=timeout, follow_redirects=False, trust_env=trust_env,
                              transport=transport) as c:
                r = _send(c, req)
                # 同主机重定向手动跟随一次（跨主机一律不跟）
                hops = 0
                while r.status_code in (301, 302, 303, 307, 308) and hops < 2:
                    loc = r.headers.get("location") or ""
                    if not loc:
                        break
                    same_host = urlparse(loc).hostname in (None, "", urlparse(req["url"]).hostname)
                    if not same_host:
                        return {"ok": False, "status": r.status_code, "text": "", "json": None, "bytes": 0,
                                "truncated": False, "elapsed_ms": int((time.time() - t0) * 1000),
                                "location": loc,
                                "error": "对方要求重定向到另一个主机（%s）→ 为安全不跟随；请把 base_url 配成最终地址"
                                         % (urlparse(loc).hostname or loc)}
                    req["url"] = loc if _looks_like_url(loc) else req["url"]
                    r = _send(c, req)
                    hops += 1

                raw = b""
                for chunk in r.iter_bytes():
                    raw += chunk
                    if len(raw) >= req["max_bytes"]:
                        break
                # ★ 单个 chunk 就可能超过上限（如一次性返回的大响应）→ 必须真截断，
                #   只 break 不裁剪会让 bytes/text 仍是全量（实测被用例逮到）
                truncated = len(raw) > req["max_bytes"]
                if truncated:
                    raw = raw[: req["max_bytes"]]
                enc = r.encoding or "utf-8"
                try:
                    txt = raw.decode(enc, errors="replace")
                except (LookupError, TypeError):
                    txt = raw.decode("utf-8", errors="replace")
                data = None
                ctype = (r.headers.get("content-type") or "").lower()
                if "json" in ctype or txt.lstrip()[:1] in ("{", "["):
                    try:
                        data = json.loads(txt)
                    except Exception:  # noqa: BLE001
                        data = None
                ok = 200 <= r.status_code < 300
                return {"ok": ok, "status": r.status_code, "text": txt, "json": data,
                        "bytes": len(raw), "truncated": truncated,
                        "elapsed_ms": int((time.time() - t0) * 1000),
                        "location": r.headers.get("location"),
                        "source": tag,
                        "error": "" if ok else "HTTP %d（%s）" % (r.status_code, tag)}
        except ApiDriverError as e:
            return {"ok": False, "status": None, "text": "", "json": None, "bytes": 0,
                    "truncated": False, "elapsed_ms": int((time.time() - t0) * 1000),
                    "location": None, "error": str(e)}
        except httpx.TimeoutException:
            last_err = "请求超时（%ss）" % req["timeout"]
        except Exception as e:  # noqa: BLE001
            last_err = "%s（%s）: %s" % (tag, type(e).__name__, str(e)[:120])
    return {"ok": False, "status": None, "text": "", "json": None, "bytes": 0,
            "truncated": False, "elapsed_ms": int((time.time() - t0) * 1000),
            "location": None, "error": last_err}
