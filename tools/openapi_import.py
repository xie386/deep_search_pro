# -*- coding: utf-8 -*-
"""OpenAPI 3.x → 候选能力（v3.0 M1-3）。

口径见 `docs/v3.0/M1M2-API与MCP适配框架方案.md` §4.3 ①档（用户裁决：**M1 只做"粘贴"**，
不做"填 URL 由后端抓取"）+ §5.2（invoke_spec / input_schema 的结构）。

本模块**纯函数、零副作用**：只把一段 spec 文本变成"候选能力"列表，不碰数据库、
不发网络请求。落库与确认由调用方（api 层）负责，这样导入逻辑可以完全离线单测。

支持的程度（够用即止，复杂特性明确降级并记 warnings）：
  - JSON 或 YAML；本地 `$ref`（`#/components/schemas/…`）递归内联（带去环与深度上限）
  - `operationId` 当操作名；缺失时按 `<method>_<path>` 兜底（稳定且可读）
  - path/query/header 参数 → bindings（**cookie 不支持**，降级记 warning）
  - `requestBody`（application/json / form-urlencoded）→ body bindings
  - 返回值 / 数组 / anyOf 等复杂 schema：原样带上（JSON Schema 本就允许嵌套）
"""
from __future__ import annotations

import json
import re
import urllib.parse

HTTP_METHODS = ("get", "post", "put", "patch", "delete", "head", "options")
READ_METHODS = ("GET", "HEAD", "OPTIONS")     # 只读判定：默认这些算只读
MAX_REF_DEPTH = 12


def _load(text: str) -> dict:
    t = (text or "").strip()
    if not t:
        raise ValueError("内容是空的")
    if t[0] in "{[":
        return json.loads(t)
    try:
        import yaml
        return yaml.safe_load(t)
    except ImportError:  # pragma: no cover
        raise ValueError("YAML 需要 pyyaml；请贴 JSON 格式的 OpenAPI")


def _inline_refs(node, root: dict, depth: int = 0, seen: tuple = ()):
    """把本地 $ref 递归内联成实际 schema（去掉 $ref，避免模型看到无法理解的东西）。"""
    if depth > MAX_REF_DEPTH:
        return {"type": "object", "description": "(引用层级过深，已截断)"}
    if isinstance(node, list):
        return [_inline_refs(x, root, depth + 1, seen) for x in node]
    if not isinstance(node, dict):
        return node
    ref = node.get("$ref")
    if isinstance(ref, str):
        if not ref.startswith("#/"):
            return {"type": "object", "description": "(外部引用不解析：%s)" % ref}
        if ref in seen:
            return {"type": "object", "description": "(循环引用：%s)" % ref.split("/")[-1]}
        cur = root
        for part in ref[2:].split("/"):
            part = part.replace("~1", "/").replace("~0", "~")
            if isinstance(cur, dict) and part in cur:
                cur = cur[part]
            else:
                return {"type": "object", "description": "(引用未找到：%s)" % ref}
        return _inline_refs(cur, root, depth + 1, seen + (ref,))
    out = {}
    for k, v in node.items():
        out[k] = _inline_refs(v, root, depth + 1, seen)
    return out


def _fallback_op(method: str, path: str) -> str:
    """没有 operationId 时的兜底名：`get_v1_current_city`（稳定 + 可读）。"""
    body = re.sub(r"[{}]", "", path).strip("/").replace("/", "_")
    body = re.sub(r"[^0-9A-Za-z_]+", "_", body).strip("_")
    return ("%s_%s" % (method.lower(), body)) if body else method.lower()


def _schema_of(param: dict) -> dict:
    s = param.get("schema") or {}
    if not isinstance(s, dict):
        s = {}
    if param.get("description") and "description" not in s:
        s = dict(s, description=param["description"])
    return s


def _split_static_query(path: str):
    """把 `路径?k=v&k2=v2` 拆成（纯路径, 固定 query 字典）。

    ★ 为什么支持这种写法（v3.0 M1 真机新增）：有些接口的"可选"参数其实是**承重的** —— Steam
    `storesearch` 不带 `cc/l` 直接返回空结果（真机实测 `{"total":0,"items":[]}`），而这两个值只有
    一个正确答案（国区/中文）。用户很自然会把固定参数写进路径，静默丢掉它才是更坏的选择；
    显式写法是操作级扩展位 `x-static-query`。两者最终都进 `invoke_spec.static_query`
    （由驱动与真实参数一起发，模型看不到、也不用填）。
    """
    if not isinstance(path, str) or "?" not in path:
        return path, {}
    raw_path, _sep, raw_q = path.partition("?")
    q = {}
    for kv in raw_q.split("&"):
        if not kv.strip():
            continue
        k, _e, v = kv.partition("=")
        if k.strip():
            q[urllib.parse.unquote(k.strip())] = urllib.parse.unquote(v.strip())
    return (raw_path or "/"), q


def parse_spec(text: str, slug: str, base_url: str = "") -> dict:
    """解析一份 OpenAPI 文本。

    :return: ``{"ok": bool, "error": str, "base_url": str, "title": str, "candidates": [...]}``
        candidate = ``{"ref","op","name","method","url_template","input_schema","read_only",
                       "invoke_spec","summary","tags","deprecated","warnings"}``
        （``invoke_spec`` 里**不含** source_id —— 落库时由调用方补上，见 §5.2）
    """
    try:
        spec = _load(text)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": "解析失败：%s: %s" % (type(e).__name__, str(e)[:120]),
                "base_url": base_url, "title": "", "candidates": []}
    if not isinstance(spec, dict) or not (spec.get("paths") or spec.get("openapi") or spec.get("swagger")):
        return {"ok": False, "error": "这不像是 OpenAPI 文档（缺 paths / openapi 字段）",
                "base_url": base_url, "title": "", "candidates": []}

    if not base_url:
        servers = spec.get("servers") or []
        if isinstance(servers, list) and servers and isinstance(servers[0], dict):
            base_url = str(servers[0].get("url") or "")
    title = str((spec.get("info") or {}).get("title") or "")

    candidates, seen_refs = [], set()
    for path, item in (spec.get("paths") or {}).items():
        if not isinstance(item, dict):
            continue
        path_params = [p for p in (item.get("parameters") or []) if isinstance(p, dict)]
        for method in HTTP_METHODS:
            op = item.get(method)
            if not isinstance(op, dict):
                continue
            warns, bindings, props, required = [], [], {}, []

            # ---- 参数：path 级 + operation 级（同名以 operation 为准）----
            merged: dict[tuple, dict] = {}
            for p in path_params + [x for x in (op.get("parameters") or []) if isinstance(x, dict)]:
                name, loc = p.get("name"), (p.get("in") or "")
                if name and loc:
                    merged[(name, loc)] = p
            for (name, loc), p in merged.items():
                if loc == "cookie":
                    warns.append("参数 %s 在 cookie 里，本项目不支持 → 已忽略" % name)
                    continue
                if loc not in ("path", "query", "header"):
                    warns.append("参数 %s 的位置 %s 不支持 → 已忽略" % (name, loc))
                    continue
                req = bool(p.get("required")) or loc == "path"
                bindings.append({"name": name, "location": loc, "required": req})
                props[name] = _schema_of(p)
                if req:
                    required.append(name)

            # ---- requestBody ----
            body_mode = "json"
            rb = op.get("requestBody") or {}
            if isinstance(rb, dict) and rb:
                rb = _inline_refs(rb, spec) if rb.get("$ref") else rb
                content = rb.get("content") or {}
                pick = None
                for ctype in ("application/json", "application/x-www-form-urlencoded"):
                    if ctype in content:
                        pick = (ctype, content[ctype])
                        break
                if pick is None and content:
                    pick = (list(content.keys())[0], content[list(content.keys())[0]])
                    warns.append("请求体类型 %s 未特殊支持 → 按 JSON 处理" % pick[0])
                if pick:
                    ctype, cdef = pick
                    body_mode = "form" if "form-urlencoded" in ctype else "json"
                    sch = _inline_refs((cdef or {}).get("schema") or {}, spec)
                    if sch.get("type") == "object" or "properties" in sch:
                        for pname, pschema in (sch.get("properties") or {}).items():
                            bindings.append({"name": pname, "location": "body",
                                             "required": pname in (sch.get("required") or [])})
                            props[pname] = pschema if isinstance(pschema, dict) else {}
                        for pname in (sch.get("required") or []):
                            if pname not in required:
                                required.append(pname)
                    else:
                        warns.append("请求体不是对象结构 → 无法拆成参数，已忽略")

            op_id = str(op.get("operationId") or "").strip() or _fallback_op(method, path)
            ref = "api:%s#%s" % (slug, op_id)
            if ref in seen_refs:
                n = 2
                while "%s_%d" % (ref, n) in seen_refs:
                    n += 1
                warns.append("操作名 %s 重复 → 本次用 %s_%d" % (op_id, op_id, n))
                op_id, ref = "%s_%d" % (op_id, n), "%s_%d" % (ref, n)
            seen_refs.add(ref)

            meth = method.upper()
            # ★ 固定 query 参数（v3.0 M1 真机新增）：有些接口的"可选"参数其实是**承重的** ——
            #   Steam `storesearch` 不带 cc/l 就返回空结果（真机实测 {"total":0,"items":[]}），
            #   而这两个值只有一个正确答案（国区/中文）。支持两种写法把它们固化进能力定义：
            #     ① 路径里直接带 query：`/api/storesearch/?cc=cn&l=schinese`（顺手，不报错）
            #     ② OpenAPI 扩展位 `"x-static-query": {"cc": "cn"}`（显式，推荐）
            #   合并进 `invoke_spec.static_query`，由驱动与真实参数一起发（模型看不到、也不用填）。
            tpl, static_q = _split_static_query(path)
            ext_q = op.get("x-static-query")
            if isinstance(ext_q, dict):
                static_q.update({str(k): v for k, v in ext_q.items()})
            if static_q:
                warns.append("已固定 query 参数：%s"
                             % ", ".join("%s=%s" % (k, v) for k, v in sorted(static_q.items())))
            candidates.append({
                "ref": ref, "op": op_id,
                "name": str(op.get("summary") or op.get("description") or op_id)[:80],
                "method": meth, "url_template": tpl,
                "input_schema": {"type": "object", "properties": props, "required": required},
                "read_only": meth in READ_METHODS,
                "invoke_spec": {"method": meth, "url_template": tpl, "bindings": bindings,
                                "body_mode": body_mode, "auth": "inherit",
                                "static_query": static_q},
                "summary": str(op.get("summary") or "")[:200],
                "tags": [str(t) for t in (op.get("tags") or [])][:5],
                "deprecated": bool(op.get("deprecated")),
                "warnings": warns,
            })
    if not candidates:
        return {"ok": False, "error": "文档里没有任何可用的操作（paths 为空或全是非法项）",
                "base_url": base_url, "title": title, "candidates": []}
    return {"ok": True, "error": "", "base_url": base_url, "title": title, "candidates": candidates}
