# -*- coding: utf-8 -*-
"""统一执行网关（v3.0 M1-5；M2 接入 mcp 时只加一个 driver，不改本文件的判定链）。

Agent 侧**只多一个**工具入口 `invoke_tool(ref, args)`（方案 §4.2 裁决 C = 混合：
CLI 继续走 `run_shell_command`，新增这一个覆盖 api/mcp）。执行链每一步都可单独测：

  1) 解析 ref  →  (`api:<slug>#<op>` │ `mcp:<server>/<tool>`)
  2) 查能力    →  校验归属（account_id）/**已人工确认**/启用
  3) 参数校验   →  input_schema 的 required / type / enum / 未知参数
  4) 放行判定   →  `readonly_verdict()`：写能力必须"人工确认过"（确认界面里写方法默认不勾）
  5) 执行      →  api → `_runtime.api_driver`（mcp → M2 的 mcp_driver）
  6) 结果裁剪   →  结构化优先 + 文本上限（与 CLI 工具同量级）
  7) 审计      →  `data/audit/commands.jsonl`（与 CLI 同一条 jsonl、同字段名 + source/ref/args_digest）

**为什么参数摘要与机械细节写在 `@tool` docstring**：项目的提示词维护纪律——机械细节
只写一处（docstring），静态提示词只写"什么时候用它"。
"""
from __future__ import annotations

import hashlib
import json
import time

from langchain_core.tools import tool
from pydantic import BaseModel, Field, field_validator

from api.monitor import monitor
from tools.schema_personal import get_personal_conn

MAX_RESULT_CHARS = 6000          # 给模型的文本上限（与 CLI 工具同量级）


class InvokeRejected(Exception):
    """可读的拒绝原因（直接给模型看，不抛栈）。"""


def coerce_params(v):
    """把"差不多对"的参数写法归一化成 dict（★ 模型侧格式小错不该让整条能力废掉）。

    真机报障（2026-09-27）：模型会把 params 传成**JSON 字符串**（整段带引号的文本，而不是对象）→
    langchain 的入参校验直接抛 ``params: Input should be a valid dictionary``，用户看到的现象是
    "这两个工具用不了"。这类错没必要硬碰：字符串按 JSON 解析、None/空串当 ``{}``、其它类型才报错。
    """
    if v is None:
        return {}
    if isinstance(v, dict):
        return v
    if isinstance(v, str):
        s = v.strip()
        if not s:
            return {}
        try:
            d = json.loads(s)
        except Exception:
            raise InvokeRejected('params 需要是 JSON 对象，例如 {"term": "elden ring"}；'
                                 "收到的字符串不是合法 JSON：%s" % s[:80])
        if not isinstance(d, dict):
            raise InvokeRejected("params 需要是 JSON 对象，解析出来是 %s" % type(d).__name__)
        return d
    raise InvokeRejected("params 需要是 JSON 对象（dict），收到 %s" % type(v).__name__)


# ---------------------------------------------------------------- ref
def parse_ref(ref: str) -> tuple[str, str, str]:
    """`api:weather#getCurrent` → ('api','weather','getCurrent')；`mcp:fs/read_file` → ('mcp','fs','read_file')。"""
    r = (ref or "").strip()
    if ":" not in r:
        raise InvokeRejected("ref 必须形如 `api:<服务名>#<操作名>` 或 `mcp:<服务名>/<工具名>`（收到 %r）" % ref)
    source, rest = r.split(":", 1)
    source = source.strip().lower()
    if source == "api":
        if "#" not in rest:
            raise InvokeRejected("api 的 ref 必须形如 `api:<服务名>#<操作名>`（收到 %r）" % ref)
        slug, op = rest.split("#", 1)
        if not slug.strip() or not op.strip():
            raise InvokeRejected("api 的 ref 里服务名与操作名都不能为空（收到 %r）" % ref)
        return source, slug.strip(), op.strip()
    if source == "mcp":
        if "/" not in rest:
            raise InvokeRejected("mcp 的 ref 必须形如 `mcp:<服务名>/<工具名>`（收到 %r）" % ref)
        srv, toolname = rest.split("/", 1)
        if not srv.strip() or not toolname.strip():
            raise InvokeRejected("mcp 的 ref 里服务名与工具名都不能为空（收到 %r）" % ref)
        return source, srv.strip(), toolname.strip()
    raise InvokeRejected("不认识的来源前缀 %r（只支持 api / mcp）" % source)


# ---------------------------------------------------------------- 读能力 / 来源
def load_capability(account_id: int, ref: str) -> dict:
    conn = get_personal_conn()
    try:
        row = conn.execute(
            "SELECT * FROM tool_capabilities WHERE account_id=? AND ref=?", (int(account_id), ref)).fetchone()
    finally:
        conn.close()
    if not row:
        raise InvokeRejected("当前账号里没有这个能力：%s（可能未收录/已被删除）" % ref)
    cap = dict(row)
    if not int(cap.get("enabled") or 0):
        raise InvokeRejected("能力 %s 未启用（未启用/未通过人工确认的能力不可调用）" % ref)
    if not cap.get("confirmed_at"):
        raise InvokeRejected("能力 %s 还没经人工确认（在「工具来源」页勾选确认后才可调用）" % ref)
    return cap


def load_source(account_id: int, source_id) -> dict:
    if not str(source_id or "").isdigit():
        raise InvokeRejected("这条能力的 invoke_spec 缺少 source_id（来源未落库？）")
    conn = get_personal_conn()
    try:
        row = conn.execute("SELECT * FROM tool_sources WHERE id=? AND account_id=?",
                           (int(source_id), int(account_id))).fetchone()
    finally:
        conn.close()
    if not row:
        raise InvokeRejected("能力引用的来源不存在或不属于当前账号（source_id=%s）" % source_id)
    src = dict(row)
    if not int(src.get("enabled") or 0):
        raise InvokeRejected("来源「%s」已被停用" % (src.get("name") or src.get("slug") or source_id))
    try:
        src["config"] = json.loads(src.get("config_json") or "{}")
    except Exception:  # noqa: BLE001
        raise InvokeRejected("来源配置不是合法 JSON（config_json）")
    return src


def _json_or_empty(txt) -> dict:
    try:
        d = json.loads(txt or "{}")
        return d if isinstance(d, dict) else {}
    except Exception:  # noqa: BLE001
        return {}


# ---------------------------------------------------------------- 参数校验
_TYPE_OK = {
    "string": lambda v: isinstance(v, str),
    "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
    "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
    "boolean": lambda v: isinstance(v, bool),
    "array": lambda v: isinstance(v, list),
    "object": lambda v: isinstance(v, dict),
}


def validate_args(schema: dict, args: dict) -> list[str]:
    """按 JSON Schema 的 required / type / enum 校验（只做够用的子集，够挡模型常见错）。"""
    if not isinstance(args, dict):
        return ["args 必须是对象（形如 {\"city\": \"成都\"}）"]
    props = (schema or {}).get("properties") or {}
    errs: list[str] = []
    for name in (schema or {}).get("required") or []:
        if name not in args or args[name] in ("", None):
            errs.append("缺少必填参数 %s" % name)
    unknown = sorted(set(args) - set(props)) if props else []
    if unknown:
        errs.append("多传了不认识的参数：%s（可用：%s）"
                    % (", ".join(unknown), ", ".join(sorted(props)) or "无"))
    for name, val in args.items():
        sch = props.get(name) or {}
        want = sch.get("type")
        if want and want in _TYPE_OK and not _TYPE_OK[want](val):
            errs.append("参数 %s 类型应为 %s（收到 %s）" % (name, want, type(val).__name__))
        enum = sch.get("enum")
        if enum and val not in enum:
            errs.append("参数 %s 只能是 %s" % (name, "/".join(str(x) for x in enum)))
    return errs


# ---------------------------------------------------------------- 放行判定（三来源同一签名）
def readonly_verdict(source: str, cap: dict, args: dict) -> tuple[bool, str]:
    """放行判定。**与 CLI 的只读清单判定同一函数签名**，M2 的 mcp 直接复用。

    口径（方案 §4.4）：api 的 `read_only=0`（非 GET）只有在**人工确认**后才可能走到这里；
    `load_capability` 已把"未确认"挡在外面，所以这里只做一次显式复核（防调用方绕过）。
    """
    if source == "api":
        if int(cap.get("read_only", 1)) == 0 and not cap.get("confirmed_at"):
            return False, "这是会改外部状态的写操作，且没有人工确认记录 → 拒绝"
        return True, ""
    if source == "cli":
        return False, "CLI 能力请用 `run_shell_command` 调用（走只读清单放行）"
    if source == "mcp":
        # M2：口径与 api **完全一致** —— 写操作（含"未声明只读"）只有在人工确认后才可能走到这里
        # （`load_capability` 已挡掉未确认的），这里只做一次显式复核（防调用方绕过）。
        if int(cap.get("read_only", 1)) == 0 and not cap.get("confirmed_at"):
            return False, "这是会改外部状态的 MCP 工具（非只读），且没有人工确认记录 → 拒绝"
        return True, ""
    return False, "不认识的来源 %r" % source


# ---------------------------------------------------------------- 审计
def _audit_invoke(*, uname, source, ref, args, ok, status=None, ms=None, chars=0,
                  rejected="", error="", read_only=True) -> None:
    """与 CLI 同一条 jsonl、同字段名（新增 source/ref/args_digest/read_only）。"""
    try:
        from tools._runtime.shell_runtime import _audit
        digest = hashlib.sha1(json.dumps(args or {}, ensure_ascii=False, sort_keys=True)
                              .encode("utf-8")).hexdigest()[:16]
        _audit({"ts": time.strftime("%Y-%m-%d %H:%M:%S"), "user": uname, "tool": "invoke_tool",
                "source": source, "ref": ref, "args_digest": digest,
                "args_keys": sorted((args or {}).keys()) if isinstance(args, dict) else [],
                "read_only": bool(read_only), "ok": bool(ok), "status": status,
                "ms": ms, "chars": chars, "rejected": rejected, "error": error[:200]})
    except Exception:  # noqa: BLE001 - 审计失败绝不影响执行结果
        pass


# ---------------------------------------------------------------- 主入口
def invoke(ref: str, args: dict | None = None, *, account_id: int | None = None,
           via: str = "agent", transport=None) -> dict:
    """统一执行网关。返回 ``{"ok","text","source","ref","status","ms","rejected"}``。

    ``transport`` 仅供单测注入 `httpx.MockTransport`（网关本身不关心传输）。
    """
    t0 = time.time()
    # ★ 模型侧格式小错先归一（真机报障：params 被写成 JSON 字符串 → 裸注解会在校验阶段就炸）
    try:
        args = coerce_params(args)
    except InvokeRejected as e:
        return {"ok": False, "text": str(e), "source": "", "ref": ref, "status": None,
                "ms": 0, "rejected": "bad_params"}
    source, slug, name = "", "", ""
    uname = ""
    try:
        source, slug, name = parse_ref(ref)
    except InvokeRejected as e:
        return {"ok": False, "text": str(e), "source": "", "ref": ref, "status": None,
                "ms": 0, "rejected": "bad_ref"}

    if account_id is None:
        from api.context import get_owner_context
        account_id = get_owner_context()
    if not account_id:
        return {"ok": False, "text": "当前没有登录账号上下文，无法调用外部能力。",
                "source": source, "ref": ref, "status": None, "ms": 0, "rejected": "no_account"}

    try:
        cap = load_capability(int(account_id), ref)
        # 审计里带用户名（与 CLI 审计字段保持一致）
        uname = ""
        try:
            conn = get_personal_conn()
            try:
                row = conn.execute("SELECT username FROM accounts WHERE id=?", (int(account_id),)).fetchone()
                uname = (row["username"] if row else "") or ""
            finally:
                conn.close()
        except Exception:  # noqa: BLE001
            pass

        spec = _json_or_empty(cap.get("invoke_spec"))
        schema = _json_or_empty(cap.get("input_schema"))
        errs = validate_args(schema, args)
        if errs:
            raise InvokeRejected("参数不对：" + "；".join(errs))

        allow, why = readonly_verdict(cap.get("source") or source, cap, args)
        if not allow:
            raise InvokeRejected(why)

        # ★ 两种来源（api / mcp）**共用下面同一套收尾**：失败审计 → 组装正文 → 整段裁剪 → 成功审计。
        #   这是 M2 "复用 M1 五条链路"的落点：新来源只多一个"取数据"的分支，别的口径一模一样。
        src_kind = cap.get("source") or source
        src = load_source(int(account_id), spec.get("source_id"))
        cfg = dict(src.get("config") or {})

        if src_kind == "api":
            from tools._runtime import api_driver
            out = api_driver.call(cfg, spec, args, transport=transport)
            ms = out.get("elapsed_ms")
        elif src_kind == "mcp":
            from tools._runtime import mcp_driver
            tool_name = str(spec.get("tool") or spec.get("op") or "").strip()
            if not tool_name:
                raise InvokeRejected("这条 MCP 能力的 invoke_spec 里没有 tool（回「工具来源」重新发现一次再确认）")
            out = mcp_driver.call_tool(cfg, tool_name, args)
            ms = out.get("ms")
        else:
            raise InvokeRejected("来源 %r 的执行驱动还没实装（CLI 请用 run_shell_command）" % src_kind)

        if not out.get("ok"):
            text = "调用失败：%s" % (out.get("error") or "未知错误")
            _audit_invoke(uname=uname, source=src_kind, ref=ref, args=args, ok=False,
                          status=out.get("status"), ms=ms, rejected="%s_error" % src_kind, error=text,
                          read_only=bool(cap.get("read_only", 1)))
            return {"ok": False, "text": text, "source": src_kind, "ref": ref,
                    "status": out.get("status"), "ms": ms, "rejected": "%s_error" % src_kind}
        body = out.get("json")
        if body is None:
            txt = (out.get("text") or "").strip()
        else:
            # ★ 紧凑 JSON（不缩进）：`indent=1` 对模型没有信息价值，却白占字符。真机实测
            # douyinhot（52KB）：同样 6000 字符里能看到的条目从 5 条提到 6 条——**提升有限**，
            # 因为该接口的体积主要来自 `word_cover.url_list` 这类长 URL，不是缩进。
            txt = json.dumps(body, ensure_ascii=False, separators=(",", ":"))
        cut = False
        src_name = src.get("name") or src.get("slug")
        payload_head = ("⚠️ 这是**写操作**（非只读），已按你的要求执行。\n"
                        if int(cap.get("read_only", 1)) == 0 else "")

        def _compose(body_txt, truncated):
            suffix = ("\n%s（来源：%s；如需其他能力，继续用 invoke_tool 传对应 ref）"
                      % ("…（内容过长已截断）\n" if truncated else "", src_name))
            return payload_head + "`%s` 返回：\n%s%s" % (ref, body_txt, suffix)

        # ★ 上限 `MAX_RESULT_CHARS` 是对**模型看到的整段文本**生效的：只裁 payload、把前缀与尾注
        # 留在外面会撑破上限（真机实测 6085 > 6000）。这里按整段裁剪，并留兜底。
        text = _compose(txt, False)
        if len(text) > MAX_RESULT_CHARS:
            over = len(text) - MAX_RESULT_CHARS
            txt, cut = txt[:max(0, len(txt) - over)], True
            text = _compose(txt, True)
            if len(text) > MAX_RESULT_CHARS:
                text = text[:MAX_RESULT_CHARS]
        _audit_invoke(uname=uname, source=src_kind, ref=ref, args=args, ok=True,
                      status=out.get("status"), ms=ms, chars=len(text),
                      read_only=bool(cap.get("read_only", 1)))
        return {"ok": True, "text": text, "source": src_kind, "ref": ref,
                "status": out.get("status"), "ms": ms, "rejected": ""}

    except InvokeRejected as e:
        msg = str(e)
        _audit_invoke(uname=uname, source=source, ref=ref, args=args, ok=False, rejected=msg,
                      ms=int((time.time() - t0) * 1000))
        return {"ok": False, "text": msg, "source": source, "ref": ref, "status": None,
                "ms": int((time.time() - t0) * 1000), "rejected": msg}
    except Exception as e:  # noqa: BLE001
        msg = "调用异常：%s: %s" % (type(e).__name__, str(e)[:160])
        _audit_invoke(uname=uname, source=source, ref=ref, args=args, ok=False, rejected=msg,
                      ms=int((time.time() - t0) * 1000))
        return {"ok": False, "text": msg, "source": source, "ref": ref, "status": None,
                "ms": int((time.time() - t0) * 1000), "rejected": msg}


# ---------------------------------------------------------------- Agent 工具（机械细节只写这一处）
class InvokeArgs(BaseModel):
    """`invoke_tool` 的入参契约。

    ★ 为什么用**显式 schema** 而不是裸注解：裸 `dict | None` 时，模型把 params 写成 JSON 字符串
    会在 langchain 的入参校验阶段被直接拒掉（真机报障：`params: Input should be a valid dictionary`），
    我们写的宽容归一根本跑不到。`mode="before"` 校验器在类型检查**之前**接住这种写法，
    而对外暴露的 JSON Schema 仍然是 `params: object`（不鼓励模型写字符串）。
    """

    ref: str = Field(..., description="能力 ref，形如 api:<服务名>#<操作名>；照抄能力卡里的，不要自己编")
    # ★ 类型写成 `dict | str | None` 而不是裸 `dict`：`tool_call_schema` 是 langchain **自己生成**的，
    #   不保留我们的 `mode="before"` 校验器 —— 裸 dict 时模型传 JSON 字符串/`null` 依旧会被它拒
    #   （真机验证过：用户报 `params: Input should be a valid dictionary`）。所以 schema 层就放行
    #   这三种写法（描述仍然让模型传对象），实际归一由下方校验器 + 网关 `coerce_params` 兜底。
    params: dict | str | None = Field(default=None,
                                      description='参数对象，键名与卡片「参数: city(必填,path)」一致，'
                                                  '例 {"city": "成都"}；没有参数就传 {}')

    @field_validator("params", mode="before")
    @classmethod
    def _coerce(cls, v):
        try:
            return coerce_params(v)
        except InvokeRejected as e:
            raise ValueError(str(e))


@tool(args_schema=InvokeArgs)
def invoke_tool(ref: str, params: dict = None) -> str:
    """调用一条**已确认的 API / MCP 能力**（用户在「定制助手 → 工具来源」里配的）。

    :param ref: 能力标识，形如 `api:<服务名>#<操作名>`（例：`api:weather#getCurrent`）；
                注入块的能力卡里会带这个 ref，**照抄**即可，不要自己编。
    :param params: 参数对象，键名与卡片里「参数: city(必填,path)」一致，例：`{"city": "成都"}`。
                必填差不给会被拒；多传不认识的参数也会被拒。
                （参数名是 `params` 不是 `args`：langchain 的 `@tool` 把 `args` 当保留名，
                 会变成 `v__args` 那种模型看不懂的入参。）
    :return: 该能力的返回内容（文本/JSON），失败时是可直接照做的中文原因。

    用法要点：
      - 只有当用户需求**确实落在**某张能力卡的范围内时才调用，不要在闲聊里试探性调用；
      - 只读能力可直接调用；卡片上带「⚠️ 会改外部状态」的写能力，**先确认用户明确要求**再调用；
      - 一次调用只能取一个能力的数据；需要多个来源时按需多次调用。
    """
    monitor.report_tool(tool_name="调用 API/MCP 能力", args={"ref": str(ref)[:80]})
    out = invoke(str(ref), params or {})
    if out.get("ok"):
        monitor.report_task_result(str(out.get("text"))[:200])
    return str(out.get("text") or "")
