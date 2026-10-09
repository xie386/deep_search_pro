# -*- coding: utf-8 -*-
"""工具来源配置（v3.0 M1-6 后端）：API 来源的 CRUD + OpenAPI 候选发现 + 人工确认入库。

口径（方案 §4.3 / §4.6 / §5.5）：
  - **录入方式只有"粘贴"**（用户裁决 O2：M1 不做"填 URL 由后端抓取"）；
  - **候选先落 `tool_capabilities(enabled=0, confirmed_at=NULL)` → 不入池**；
    「确认」才 `enabled=1 + confirmed_at=now`；
  - **写方法（非 GET/HEAD/OPTIONS）默认不勾**：必须显式 `approve_write=true` 才按 `read_only=0` 入库；
  - 重复粘贴同一份 spec 是**幂等更新**：已确认的行**保留人工决定**（read_only / confirmed_at / enabled
    不被覆盖），只刷新名称、schema、invoke_spec 等派生字段；
  - 任何写操作后 `bump_pool_version()`（路由器据此失效缓存）。

本模块只做业务逻辑；路由挂在 `api/server.py`（与 `api/customize.py` / `api/me_user_data.py` 同款）。
"""
from __future__ import annotations

import json
import re
import time
from typing import Optional

from fastapi import HTTPException, status
from pydantic import BaseModel

from tools import openapi_import
from tools.schema_personal import get_personal_conn


# ---------------------------------------------------------------- 基础
def _require_account_id(token: str) -> int:
    from api.account import get_session
    sess = get_session(token)
    aid = sess.get("account_id")
    if aid is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "登录状态缺少账号标识，请重新登录")
    return int(aid)


def _db():
    from tools.schema_personal import ensure_tables
    ensure_tables()
    return get_personal_conn()


def _json_or_empty(txt) -> dict:
    try:
        d = json.loads(txt or "{}")
        return d if isinstance(d, dict) else {}
    except Exception:  # noqa: BLE001
        return {}


def _bump(aid: int) -> None:
    """能力池版本 +1（路由器缓存失效）。失败不影响业务。"""
    try:
        from tools.capability_pool import bump_pool_version
        bump_pool_version(aid)
    except Exception as e:  # noqa: BLE001
        print("[tools] bump_pool_version 失败（忽略）: %s" % e)


def _mask_secret(secret: str) -> str:
    s = (secret or "").strip()
    if not s:
        return ""
    return s[:3] + "***" + s[-2:] if len(s) > 6 else "***"


# ---------------------------------------------------------------- 模型
class ApiSourceReq(BaseModel):
    """来源配置（配置导向：厂商差异都靠这些字段表达，代码里没有厂商名）。

    - `source='api'`：用 `base_url` / 鉴权 / 默认头（v3.0 M1）；
    - `source='mcp'`：用 `transport` + `command`/`args`（stdio）或 `url`（Streamable HTTP）（v3.0 M2）。
    """
    source: str = "api"              # api | mcp
    slug: str
    name: str = ""
    base_url: str = ""
    auth_type: str = "none"          # none | header | query
    auth_name: str = ""              # 如 Authorization / X-API-Key
    auth_prefix: str = ""            # 如 "Bearer "
    auth_secret: str = ""            # 密钥（列表接口永远掩码返回）
    headers_json: str = "{}"         # 默认头（JSON 文本）
    timeout: float = 30.0
    max_bytes: int = 1048576
    allow_private: bool = True       # 用户裁决：默认允许内网/环回（自托管场景）
    abilities: str = ""              # 这条来源整体的人话说明（进来源级能力卡）
    docs: str = ""                   # README / 文档地址：**网址或本地文件路径**
    intro: str = ""                  # ★ M5c-2' 官方介绍文案（第三方服务的 README 常只讲接入，能力说明在这里）
    note: str = ""
    # ---- MCP（v3.0 M2）：transport 决定用哪几个字段，其余留空 ----
    transport: str = ""              # stdio | http
    command: str = ""                # stdio：可执行文件（必须在下面的白名单内）
    args_json: str = "[]"            # stdio：JSON 数组
    env_json: str = "{}"             # stdio：额外环境变量（JSON 对象）
    cwd: str = ""                    # stdio：工作目录（留空 = 后端进程当前目录）
    url: str = ""                    # http：Streamable HTTP 端点
    allow_stdio_commands: str = ""   # 逗号分隔的命令白名单；留空 = 用内置默认
    # ---- M5c-1：来源级只读声明（**只对 mcp 生效**；api 的写/只读由 HTTP 方法判定，不受此开关影响）----
    # ★ 只允许覆盖"服务端没声明 read_only_hint"的工具；服务端明确 false 的一律不动（见 _is_write_row）
    assume_read_only: bool = False
    # ★ 是否**同时**刷新该来源下已确认工具的只读标记（这是一次显式动作，前端会弹确认框；
    #   不传时只影响后续「发现工具」的候选，不动已确认行 —— 符合 M1「已确认行保留人工决定」铁律）
    sync_readonly: bool = False


class DiscoverReq(BaseModel):
    slug: str                        # 往哪个来源下发现（必须先有来源）
    text: str = ""                   # api：粘贴的 OpenAPI 文本（JSON/YAML）；mcp 不用
    base_url: str = ""               # api 可选：覆盖 spec 里的 servers
    source: str = "api"              # api | mcp（决定"发现"怎么发现）
    timeout: float = 0               # mcp 可选：这次连接的超时（0 = 用来源配置）


_READ_METHODS = ("GET", "HEAD", "OPTIONS")

# ★ ref 形状两种来源不同：api 用 `api:<短名>#<操作>`，mcp 用 `mcp:<短名>/<工具名>`
#   （`tools/capability_invoke.parse_ref` 就是这么解析的）。所有 LIKE 查询必须走这两个助手，
#   否则 MCP 来源的能力在"候选/确认/删除"里会一条都匹配不到（M1 里这里是写死的 'api:%s#%'）。
def _ref_prefix(src: str, slug: str) -> str:
    return ("%s:%s/" if src == "mcp" else "%s:%s#") % (src, slug)


def _ref_like(src: str, slug: str) -> str:
    return _ref_prefix(src, slug) + "%"


def _is_write_row(src: str, spec: dict, assume_read_only: bool = False) -> bool:
    """这条能力是不是"会改外部状态"的操作 —— **按来源判**（服务端自证，不信客户端字段）。

    - `api`：由 `invoke_spec.method` 推导（GET/HEAD/OPTIONS 之外都算写）；**不受 assume_read_only 影响**；
    - `mcp`：由工具声明里的 `read_only_hint` 推导 —— **只有明确 true 才算只读**；
      `false` 与**未声明**（None）一律按"写"处理（规范明说 annotations 只是提示，
      客户端不得据此做安全决策 → 未知就要求人工确认）。

    ★ M5c-1：`assume_read_only=True` 是**来源级的人工声明**，只在"服务端没声明"（None）时生效：
    实测有服务（12306 / BNF / 拼多多）一个 hint 都不给，于是 19 个纯查询工具被一律当写操作，
    模型因此犹豫、整条工具链退化成二手网搜。**服务端明确说 false 的，绝不允许被这个开关覆盖。**
    """
    if src == "mcp":
        hint = (spec or {}).get("read_only_hint")
        if hint is None:
            return not bool(assume_read_only)      # 未声明：用户声明过 → 只读；否则保守按写
        return hint is not True                    # 明确 true → 只读；明确 false → 写（不可覆盖）
    return _is_write_spec(spec)


def _is_write_spec(spec: dict) -> bool:
    """是否写操作：**只由服务端从 `invoke_spec.method` 推导**（`read_only` 是派生值）。

    为什么不信客户端传的 `read_only`：那是 UI 提示，前端可被改；放行判定必须服务端自证
    （实测踩过：confirm 里用客户端字段判定，结果写能力被当成只读直接放行）。
    """
    return str((spec or {}).get("method") or "GET").strip().upper() not in _READ_METHODS


class CandidateIn(BaseModel):
    ref: str
    name: str = ""                    # 可改显示名
    approve_write: bool = False        # 写方法必须显式勾选才可按 read_only=0 入库


class CapAbilitiesReq(BaseModel):
    """M5c-2'：保存**工具级描述**（用户手写版；AI 草稿一键导入后也走这里）。"""
    source: str = ""                 # api | mcp；留空按 slug 找
    slug: str
    items: list[dict] = []           # [{"ref": "...", "text": "...", "name": "..."?}]；text 空串 = 清除手写、回到自动摘要；name 给了就一并改显示名（人话名字，2026-10-06 增）


class ConfirmReq(BaseModel):
    slug: str
    items: list[CandidateIn]
    source: str = ""                  # api | mcp；留空 = 按 slug 找（两种来源可以同名短名）


# ---------------------------------------------------------------- 来源 CRUD
def sources_list(token: str) -> dict:
    aid = _require_account_id(token)
    conn = _db()
    try:
        # ★ 两种来源一起返回（前端按 tab 过滤）：M1 时这里写死 source='api'，
        #   MCP 来源接进来后如果还过滤，MCP tab 会永远是空的。
        srows = [dict(r) for r in conn.execute(
            "SELECT * FROM tool_sources WHERE account_id=? AND source IN ('api','mcp') ORDER BY id", (aid,))]
        # ★ 2026-10-06：连带取出「面向路由的文本」三列 —— 来源列表要给前端回传**素材体检**结果，
        #   让用户一眼看到「这个来源有几条工具素材不合格、该用 AI 预写补」，而不是去跑测试脚本。
        caps = [dict(r) for r in conn.execute(
            "SELECT ref, name, enabled, read_only, confirmed_at, source, "
            "COALESCE(keywords,'') AS keywords, COALESCE(abilities,'') AS abilities, "
            "COALESCE(abilities_user,'') AS abilities_user FROM tool_capabilities "
            "WHERE account_id=? AND source IN ('api','mcp') ORDER BY ref", (aid,))]
    finally:
        conn.close()
    from tools import capability_lint as _cap_lint
    _material_issues = _cap_lint.check_rows(caps)
    for s in srows:
        cfg = _json_or_empty(s.get("config_json"))
        if s.get("source") == "mcp":
            s["config"] = {
                "transport": cfg.get("transport", ""), "command": cfg.get("command", ""),
                "args": cfg.get("args") or [], "cwd": cfg.get("cwd", ""),
                "env_keys": sorted((cfg.get("env") or {}).keys()),   # ★ 只回键名，不回值（可能含密钥）
                "url": cfg.get("url", ""), "timeout": cfg.get("timeout", 30),
                "allow_stdio_commands": cfg.get("allow_stdio_commands") or [],
                "allow_private": bool(cfg.get("allow_private", True)),
                "assume_read_only": bool(cfg.get("assume_read_only", False)),   # M5c-1
            }
        else:
            auth = cfg.get("auth") or {}
            s["config"] = {
                "base_url": cfg.get("base_url", ""), "auth_type": auth.get("type", "none"),
                "auth_name": auth.get("name", ""), "auth_prefix": auth.get("prefix", ""),
                "auth_secret_masked": _mask_secret(auth.get("secret", "")),
                "headers": cfg.get("headers") or {}, "timeout": cfg.get("timeout", 30),
                "max_bytes": cfg.get("max_bytes", 1048576),
                "allow_private": bool(cfg.get("allow_private", True)),
            }
        s["config_json"] = ""       # 不把原始 JSON（含密钥）发给前端
        mine = [c for c in caps if c["ref"].startswith(_ref_prefix(s["source"], s["slug"]))]
        s["capability_count"] = sum(1 for c in mine if c["confirmed_at"])
        s["candidate_count"] = sum(1 for c in mine if not c["confirmed_at"])
        s["capabilities"] = mine
        # ★ 素材体检（规则见 tools/capability_lint.py，与 tests/live/m6b_material_lint.py 同源）
        bad = [c for c in mine if c["ref"] in _material_issues]
        hard = [c for c in bad if any(p.get("severity", "hard") == "hard"
                                      for p in _material_issues[c["ref"]])]
        s["material"] = {"total": len(mine), "problem_count": len(hard),
                         "info_count": len(bad) - len(hard),
                         "items": [{"ref": c["ref"], "name": c["name"],
                                    "problems": _material_issues[c["ref"]]} for c in bad]}
    summary_bad = sum((s.get("material") or {}).get("problem_count") or 0 for s in srows)
    return {"items": srows,
            "material_summary": {"scanned": len(caps), "problem_count": summary_bad,
                                 "hint": ("" if not summary_bad else
                                          "有 %d 条工具的「面向路由的文本」不齐备：建议在该来源卡片上点"
                                          "「AI 预写工具级描述」，确认后保存。素材不齐会直接表现为"
                                          "「模型想不起这个工具 / 用错同源工具」。" % summary_bad)}}


def source_save(req: ApiSourceReq, token: str) -> dict:
    aid = _require_account_id(token)
    src_kind = (req.source or "api").strip().lower()
    if src_kind not in ("api", "mcp"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "来源类型只支持 api / mcp")
    slug = (req.slug or "").strip()
    # 注意：str.isalnum() 对中文返回 True → 必须显式用 ASCII 白名单（短名会进 ref，模型要照抄它）
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,40}", slug or ""):
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            "短名只能用 ASCII 字母/数字/连字符/下划线（≤41 字符，且以字母或数字开头）——它进 ref")
    if src_kind == "mcp":
        return _save_mcp_source(req, aid, slug)
    base_url = (req.base_url or "").strip().rstrip("/")
    if not base_url.lower().startswith(("http://", "https://")):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "base_url 必须以 http:// 或 https:// 开头")
    try:
        headers = json.loads(req.headers_json or "{}")
        if not isinstance(headers, dict):
            raise ValueError
    except Exception:  # noqa: BLE001
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "默认头必须是 JSON 对象，如 {\"X-Api-Version\": \"1\"}")
    auth_type = (req.auth_type or "none").strip().lower()
    if auth_type not in ("none", "header", "query"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "鉴权类型只支持 none / header / query")

    cfg = {"base_url": base_url, "headers": headers,
           "timeout": float(req.timeout or 30), "max_bytes": int(req.max_bytes or 1048576),
           "allow_private": bool(req.allow_private)}
    if auth_type != "none":
        cfg["auth"] = {"type": auth_type, "name": (req.auth_name or "").strip(),
                       "prefix": req.auth_prefix or "", "secret": (req.auth_secret or "").strip()}

    conn = _db()
    try:
        row = conn.execute("SELECT id, config_json FROM tool_sources WHERE account_id=? AND source='api' AND slug=?",
                           (aid, slug)).fetchone()
        if row:
            old_cfg = _json_or_empty(dict(row).get("config_json"))
            # 密钥留空 = 不修改（与模型配置同款交互）
            if "auth" in cfg and not cfg["auth"].get("secret"):
                old_secret = (old_cfg.get("auth") or {}).get("secret", "")
                if old_secret:
                    cfg["auth"]["secret"] = old_secret
                else:
                    cfg.pop("auth", None)
            conn.execute("UPDATE tool_sources SET name=?, config_json=?, abilities=?, docs=?, intro=?, note=?, "
                         "state='configured', updated_at=CURRENT_TIMESTAMP WHERE id=? AND account_id=?",
                         (req.name or slug, json.dumps(cfg, ensure_ascii=False), req.abilities, req.docs,
                          req.intro or "", req.note, int(dict(row)["id"]), aid))
            sid = int(dict(row)["id"])
        else:
            cur = conn.execute("INSERT INTO tool_sources (account_id, source, slug, name, config_json, "
                               "abilities, docs, intro, note, state, enabled) VALUES (?,?,?,?,?,?,?,?,?, 'configured', 1)",
                               (aid, "api", slug, req.name or slug, json.dumps(cfg, ensure_ascii=False),
                                req.abilities, req.docs, req.intro or "", req.note))
            sid = int(cur.lastrowid)
        conn.commit()
    finally:
        conn.close()
    _bump(aid)
    return {"id": sid, "slug": slug}


def _sync_readonly_for_source(conn, aid: int, src_kind: str, slug: str, assume_read_only: bool) -> int:
    """把该来源下**服务端没声明只读**的工具，按用户声明刷成只读/写。返回改动条数。

    边界（M5c-1 口径）：只改 `invoke_spec.read_only_hint is None` 的行；
    服务端明确 `false` 的行**一律不动**（明确说"我会改状态"就按写处理，用户不能一键绕过）。
    """
    rows = conn.execute("SELECT id, read_only, invoke_spec FROM tool_capabilities "
                        "WHERE account_id=? AND source=? AND ref LIKE ?",
                        (aid, src_kind, _ref_like(src_kind, slug))).fetchall()
    n = 0
    for r in rows:
        d = dict(r)
        spec = _json_or_empty(d.get("invoke_spec"))
        if spec.get("read_only_hint") is not None:
            continue                      # 服务端声明过 → 尊重服务端
        want = 1 if assume_read_only else 0
        if int(d.get("read_only") or 0) == want:
            continue                      # 已经是对的 → 不算改动（重复保存不重复报）
        conn.execute("UPDATE tool_capabilities SET read_only=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
                     (want, int(d["id"])))
        n += 1
    return n


def _save_mcp_source(req: "ApiSourceReq", aid: int, slug: str) -> dict:
    """保存一个 MCP 来源（stdio / Streamable HTTP）。

    与 api 同款两道关口：① 字段自身合法（JSON 能解析）；② **配置语义合法** —— 直接复用
    `mcp_driver.validate_cfg`（命令白名单 / url / 超时），保证"存得进去就一定能被驱动连上"。
    密钥口径同 api：`env_json` 留空 = 不修改已存的 env（前端永远只回键名，不回值）。
    """
    from tools._runtime import mcp_driver
    transport = (req.transport or "").strip().lower()
    if transport not in ("stdio", "http"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "MCP 来源的 transport 只支持 stdio / http")
    try:
        args = json.loads(req.args_json or "[]")
    except Exception as e:  # noqa: BLE001
        # ★ 与前端同款的提醒：Windows 路径原样贴进来时 `\L` `\m` `\b` 都不是合法的 JSON 转义
        #   （用户实测踩过，旧提示只说"必须是 JSON 数组"，一个字没提到反斜杠）
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            "args 不是合法 JSON：%s。Windows 路径请改用正斜杠，例如 "
                            "[\"D:/LLM/mcp_servers/bnf/bnf_server.py\"]（或用两个反斜杠转义）" % e)
    if not isinstance(args, list) or not all(isinstance(a, str) for a in args):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, 'args 必须是字符串数组的 JSON，如 ["-y","pkg@latest"]')
    try:
        env = json.loads(req.env_json or "{}")
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            "env 不是合法 JSON：%s。Windows 路径请改用正斜杠（或用两个反斜杠转义）" % e)
    if not isinstance(env, dict):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, 'env 必须是 JSON 对象，如 {"TOKEN":"xxx"}')
    allow = [x.strip() for x in (req.allow_stdio_commands or "").replace("，", ",").split(",") if x.strip()]
    cfg = {"transport": transport, "command": (req.command or "").strip() if transport == "stdio" else "",
           "args": args if transport == "stdio" else [], "env": env, "cwd": (req.cwd or "").strip(),
           "url": (req.url or "").strip(), "timeout": float(req.timeout or 30),
           "allow_stdio_commands": allow or list(mcp_driver.DEFAULT_STDIO_COMMANDS),
           "allow_private": bool(req.allow_private),
           "assume_read_only": bool(req.assume_read_only)}       # M5c-1：来源级只读声明
    bad = [s for s in mcp_driver.validate_cfg(cfg) if not s["ok"]]
    if bad:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "配置不通过：%s（%s）" % (bad[0]["name"], bad[0]["detail"]))

    conn = _db()
    synced = 0
    try:
        row = conn.execute("SELECT id, config_json FROM tool_sources WHERE account_id=? AND source='mcp' AND slug=?",
                           (aid, slug)).fetchone()
        if row:
            old_cfg = _json_or_empty(dict(row).get("config_json"))
            if not env and old_cfg.get("env"):
                cfg["env"] = old_cfg["env"]            # 留空 = 不修改
            conn.execute("UPDATE tool_sources SET name=?, config_json=?, abilities=?, docs=?, intro=?, note=?, "
                         "state='configured', updated_at=CURRENT_TIMESTAMP WHERE id=? AND account_id=?",
                         (req.name or slug, json.dumps(cfg, ensure_ascii=False), req.abilities, req.docs,
                          req.intro or "", req.note, int(dict(row)["id"]), aid))
            sid = int(dict(row)["id"])
        else:
            cur = conn.execute("INSERT INTO tool_sources (account_id, source, slug, name, config_json, "
                               "abilities, docs, intro, note, state, enabled) VALUES (?,?,?,?,?,?,?,?,?, 'configured', 1)",
                               (aid, "mcp", slug, req.name or slug, json.dumps(cfg, ensure_ascii=False),
                                req.abilities, req.docs, req.intro or "", req.note))
            sid = int(cur.lastrowid)
        # M5c-1：用户在弹窗里点了确认（sync_readonly）→ 把"服务端没声明只读"的那些工具一次性刷新。
        # 为什么要这一步：`discover` 按 M1 铁律**保留已确认行的人工决定**，所以光勾开关不会改变已确认的 19 条；
        # 这里是一次显式、可追溯的批量刷新（只碰 read_only_hint is None 的行）。
        if req.sync_readonly:
            synced = _sync_readonly_for_source(conn, aid, "mcp", slug, bool(req.assume_read_only))
        conn.commit()
    finally:
        conn.close()
    _bump(aid)
    return {"id": sid, "slug": slug, "source": "mcp", "readonly_synced": synced}


def source_delete(sid: int, token: str) -> dict:
    aid = _require_account_id(token)
    conn = _db()
    try:
        row = conn.execute("SELECT slug, source FROM tool_sources WHERE id=? AND account_id=?",
                           (int(sid), aid)).fetchone()
        if not row:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "来源不存在")
        r0 = dict(row)
        slug, src_kind = r0["slug"], r0.get("source") or "api"
        cur = conn.execute("DELETE FROM tool_capabilities WHERE account_id=? AND source=? AND ref LIKE ?",
                           (aid, src_kind, _ref_like(src_kind, slug)))
        n = cur.rowcount
        conn.execute("DELETE FROM tool_sources WHERE id=? AND account_id=?", (int(sid), aid))
        conn.commit()
    finally:
        conn.close()
    _bump(aid)
    return {"deleted_capabilities": n}


def source_test(sid: int, token: str, *, probe: bool = False) -> dict:
    """体检：先校验配置，再（可选）对 base_url 做一次可达性探测。**不调用任何业务操作**。"""
    aid = _require_account_id(token)
    conn = _db()
    try:
        row = conn.execute("SELECT * FROM tool_sources WHERE id=? AND account_id=?", (int(sid), aid)).fetchone()
    finally:
        conn.close()
    if not row:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "来源不存在")
    src = dict(row)
    cfg = _json_or_empty(src.get("config_json"))
    if (src.get("source") or "api") == "mcp":
        # MCP 走七步体检（M2-3）：配置解析 / 环境检测 / 握手 / tools-list / 异常容错 / 性能 / 汇总
        from tools import mcp_probe
        res = mcp_probe.probe(cfg, timeout=float(cfg.get("timeout") or 30))
        _save_state(aid, int(sid), "verified" if res.get("ok") else "failed")
        return {"ok": bool(res.get("ok")), "steps": res.get("steps") or [], "ms": res.get("ms", 0),
                "tool_count": len(res.get("tools") or [])}
    steps: list[dict] = []

    ok_url = str(cfg.get("base_url") or "").lower().startswith(("http://", "https://"))
    steps.append({"name": "base_url 合法", "ok": ok_url,
                  "detail": cfg.get("base_url") or "缺 base_url"})
    auth = cfg.get("auth") or {}
    auth_ok = (auth.get("type") in (None, "none")) or bool(auth.get("secret"))
    steps.append({"name": "鉴权配置完整", "ok": auth_ok,
                  "detail": ("无鉴权" if not auth.get("secret") else "%s: %s%s"
                             % (auth.get("type"), auth.get("name"), "（已填密钥）" if auth.get("secret") else ""))})
    steps.append({"name": "允许访问内网", "ok": True,
                  "detail": "是（用户裁决默认值）" if cfg.get("allow_private", True) else "否（已收紧）"})

    if probe and ok_url:
        base = str(cfg.get("base_url"))
        if not cfg.get("allow_private", True):
            from tools.cli_docs import _host_is_safe
            from urllib.parse import urlparse
            safe, why = _host_is_safe(urlparse(base).hostname or "")
            if not safe:
                steps.append({"name": "可达性探测", "ok": False, "detail": "被安全策略拒绝：" + why})
                _save_state(aid, int(sid), "failed")
                return {"ok": False, "steps": steps}
        import httpx
        last = "未探测"
        for tag, trust_env in (("直连", False), ("代理", True)):
            try:
                with httpx.Client(timeout=8.0, follow_redirects=False, trust_env=trust_env) as c:
                    r = c.get(base)
                steps.append({"name": "可达性探测", "ok": True,
                              "detail": "%s HTTP %d（%s）" % (tag, r.status_code, base)})
                last = ""
                break
            except Exception as e:  # noqa: BLE001
                last = "%s失败：%s" % (tag, type(e).__name__)
        if last:
            steps.append({"name": "可达性探测", "ok": False, "detail": last})

    ok = all(s["ok"] for s in steps)
    _save_state(aid, int(sid), "verified" if ok else "failed")
    return {"ok": ok, "steps": steps}


def _save_state(aid: int, sid: int, state: str) -> None:
    conn = _db()
    try:
        conn.execute("UPDATE tool_sources SET state=?, updated_at=CURRENT_TIMESTAMP WHERE id=? AND account_id=?",
                     (state, sid, aid))
        conn.commit()
    finally:
        conn.close()


def _auto_draft(aid: int, kind: str, slug: str) -> dict:
    """发现/导入工具后**自动跑一次**「工具级描述 AI 预写」，结果回给前端由用户确认（2026-10-06）。

    为什么不落库：这是"草稿"，必须过用户的眼睛（与 M5c-2' 的既有口径一致）。
    为什么自动跑：用户不会主动去点 —— 实测没人点就等于没有素材（脚本闸门对他也不存在）。
    **非致命**：没填 README/介绍文案（AI 预写会拒绝）、或模型调用失败，都只回 `why`，不影响"发现工具"本身。
    """
    from fastapi import HTTPException as _HE
    try:
        from api import customize as cz
        out = cz.capability_ability_generate(kind, slug, account_id=aid)
        return {"ok": True, "items": out.get("items") or [], "evidence": out.get("evidence") or {},
                "warnings": out.get("warnings") or []}
    except _HE as e:                     # 缺依据 / 账号问题 → 告诉用户该补什么
        return {"ok": False, "why": str(getattr(e, "detail", e))}
    except Exception as e:               # noqa: BLE001 - 自动动作绝不能让"发现工具"失败
        return {"ok": False, "why": "%s: %s" % (type(e).__name__, str(e)[:160])}


# ---------------------------------------------------------------- 发现 → 候选 → 确认
def discover(req: DiscoverReq, token: str) -> dict:
    aid = _require_account_id(token)
    slug = (req.slug or "").strip()
    src_kind = (req.source or "api").strip().lower()
    if src_kind not in ("api", "mcp"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "来源类型只支持 api / mcp")
    conn = _db()
    try:
        srow = conn.execute("SELECT * FROM tool_sources WHERE account_id=? AND source=? AND slug=?",
                            (aid, src_kind, slug)).fetchone()
    finally:
        conn.close()
    if not srow:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "先保存这个来源（短名 %s）再发现工具" % slug)
    src = dict(srow)
    sid = int(src["id"])
    cfg = _json_or_empty(src.get("config_json"))

    if src_kind == "mcp":
        payload = _discover_mcp(aid, slug, sid, cfg, req)
        if payload.get("ok"):
            payload["draft"] = _auto_draft(aid, "mcp", slug)
        return payload

    res = openapi_import.parse_spec(req.text or "", slug, req.base_url or cfg.get("base_url", ""))
    if not res["ok"]:
        return {"ok": False, "error": res["error"], "items": []}

    conn = _db()
    try:
        kept_confirmed = 0
        for c in res["candidates"]:
            exists = conn.execute("SELECT id, confirmed_at, read_only, enabled FROM tool_capabilities "
                                  "WHERE account_id=? AND source='api' AND ref=?", (aid, c["ref"])).fetchone()
            spec = dict(c["invoke_spec"])
            spec["source_id"] = sid
            if exists:
                e = dict(exists)
                if e.get("confirmed_at"):
                    kept_confirmed += 1
                    # 已确认的：**保留人工决定**（name / read_only / enabled / confirmed_at），
                    # 只刷新派生字段（invoke_spec / input_schema / invoke_hint / **abilities**；
                    # 后者来自文档的 summary —— 人写的"能力描述"在**来源**那一行，读取时会拼接）
                    conn.execute("UPDATE tool_capabilities SET "
                                 "abilities=?, "
                                 "invoke_hint=?, invoke_spec=?, input_schema=?, updated_at=CURRENT_TIMESTAMP "
                                 "WHERE id=?",
                                 (c["summary"], "%s %s" % (c["method"], c["url_template"]),
                                  json.dumps(spec, ensure_ascii=False), json.dumps(c["input_schema"], ensure_ascii=False),
                                  e["id"]))
                    continue
                conn.execute("UPDATE tool_capabilities SET name=?, abilities=?, invoke_hint=?, invoke_spec=?, "
                             "input_schema=?, read_only=1, updated_at=CURRENT_TIMESTAMP WHERE id=?",
                             (c["name"], c["summary"], "%s %s" % (c["method"], c["url_template"]),
                              json.dumps(spec, ensure_ascii=False), json.dumps(c["input_schema"], ensure_ascii=False),
                              e["id"]))
                continue
            # 新候选：enabled=0 + confirmed_at=NULL（不入池）→ 路由文本用 spec 的 summary/description；
            # read_only 是**派生值**（按 method 判），确认时再复核一次
            conn.execute(
                "INSERT INTO tool_capabilities (account_id, source, ref, name, keywords, abilities, invoke_hint, "
                "enabled, invoke_spec, input_schema, read_only, confirmed_at) VALUES (?,?,?,?,?,?,?,0,?,?,?,NULL)",
                (aid, "api", c["ref"], c["name"], "", (c.get("summary") or c["name"]),
                 "%s %s" % (c["method"], c["url_template"]),
                 json.dumps(spec, ensure_ascii=False), json.dumps(c["input_schema"], ensure_ascii=False),
                 0 if _is_write_spec(spec) else 1))
        conn.commit()
        # ★ 返回给前端的形状必须与 `candidates_list` **完全一致**（含 `is_write` / `param_summary`）：
        # 直接把解析器条目吐出去，会让"刚贴完文档"这一步的写操作显示成只读、勾选框不被禁用。
        # 状态一律以**库为准**（本次循环可能刚把某些行标成已确认/保留人工决定）。
        refs = [c["ref"] for c in res["candidates"]]
        state = {}
        if refs:
            q = ("SELECT ref, name, read_only, enabled, confirmed_at, input_schema, invoke_spec "
                 "FROM tool_capabilities WHERE account_id=? AND source='api' AND ref IN (%s)"
                 % ",".join("?" * len(refs)))
            for r in conn.execute(q, (aid, *refs)).fetchall():
                state[dict(r)["ref"]] = dict(r)
        warn = {c["ref"]: c.get("warnings") for c in res["candidates"]}
        items = [_cand_view(state[c["ref"]], warn.get(c["ref"])) if c["ref"] in state
                 else _cand_view({"ref": c["ref"], "name": c["name"], "invoke_spec": c["invoke_spec"],
                                  "input_schema": c["input_schema"], "read_only": c["read_only"],
                                  "enabled": 0, "confirmed_at": None}, warn.get(c["ref"]))
                 for c in res["candidates"]]
    finally:
        conn.close()
    _bump(aid)
    return {"ok": True, "error": "", "title": res["title"], "base_url": res["base_url"],
            "kept_confirmed": kept_confirmed, "items": items,
            "draft": _auto_draft(aid, "api", slug)}


def _discover_mcp(aid: int, slug: str, sid: int, cfg: dict, req) -> dict:
    """MCP 来源的"发现"：跑一次七步体检 → 把工具表落成候选（enabled=0 / confirmed_at=NULL）。

    为什么发现=体检：MCP 没有"文档"可解析，**服务端自己就是文档**（`tools/list`）——
    所以连上去列一遍工具，同时也就证明了它可用；连接失败就没有候选可谈。
    ★ 只读口径：`read_only_hint` **明确 true** 才算只读；false 与未声明一律按"写"存
      （规范说 annotations 只是提示 → 未知就要求人工确认）。
    """
    from tools import mcp_probe
    res = mcp_probe.probe(cfg, timeout=float(req.timeout or 0) or None)
    if not res.get("ok"):
        bad = [s for s in (res.get("steps") or []) if not s["ok"]]
        why = "；".join("%s：%s" % (s["name"], s["detail"]) for s in bad) or "未知原因"
        return {"ok": False, "error": "体检未通过 —— " + why, "items": [],
                "steps": res.get("steps") or [], "title": slug, "base_url": "", "kept_confirmed": 0}

    tools = res.get("tools") or []
    assume_ro = bool((cfg or {}).get("assume_read_only", False))      # M5c-1
    conn = _db()
    try:
        kept_confirmed = 0
        for t in tools:
            name = str(t.get("name") or "").strip()
            if not name:
                continue
            ref = "mcp:%s/%s" % (slug, name)
            spec = {"server": slug, "tool": name, "read_only_hint": t.get("read_only_hint"),
                    "source_id": sid}
            schema = t.get("input_schema") or {"type": "object", "properties": {}}
            desc = (t.get("description") or t.get("title") or name).strip()
            is_write = _is_write_row("mcp", spec, assume_ro)     # M5c-1：吃来源级只读声明
            exists = conn.execute("SELECT id, confirmed_at FROM tool_capabilities "
                                  "WHERE account_id=? AND source='mcp' AND ref=?", (aid, ref)).fetchone()
            if exists and dict(exists).get("confirmed_at"):
                # 已确认的：**保留人工决定**（name/enabled/confirmed_at/read_only），只刷新派生字段
                kept_confirmed += 1
                conn.execute("UPDATE tool_capabilities SET abilities=?, invoke_hint=?, invoke_spec=?, "
                             "input_schema=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
                             (desc, "MCP 工具 %s（%s）" % (name, src_kind_label(cfg)),
                              json.dumps(spec, ensure_ascii=False), json.dumps(schema, ensure_ascii=False),
                              int(dict(exists)["id"])))
                continue
            if exists:
                conn.execute("UPDATE tool_capabilities SET name=?, abilities=?, invoke_hint=?, invoke_spec=?, "
                             "input_schema=?, read_only=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
                             (name, desc, "MCP 工具 %s（%s）" % (name, src_kind_label(cfg)),
                              json.dumps(spec, ensure_ascii=False), json.dumps(schema, ensure_ascii=False),
                              0 if is_write else 1, int(dict(exists)["id"])))
                continue
            conn.execute(
                "INSERT INTO tool_capabilities (account_id, source, ref, name, keywords, abilities, invoke_hint, "
                "enabled, invoke_spec, input_schema, read_only, confirmed_at) VALUES (?,?,?,?,?,?,?,0,?,?,?,NULL)",
                (aid, "mcp", ref, name, "", desc, "MCP 工具 %s（%s）" % (name, src_kind_label(cfg)),
                 json.dumps(spec, ensure_ascii=False), json.dumps(schema, ensure_ascii=False),
                 0 if is_write else 1))
        conn.commit()
        refs = ["mcp:%s/%s" % (slug, t["name"]) for t in tools if str(t.get("name") or "").strip()]
        state = {}
        if refs:
            q = ("SELECT ref, name, read_only, enabled, confirmed_at, input_schema, invoke_spec "
                 "FROM tool_capabilities WHERE account_id=? AND source='mcp' AND ref IN (%s)"
                 % ",".join("?" * len(refs)))
            for r in conn.execute(q, (aid, *refs)).fetchall():
                state[dict(r)["ref"]] = dict(r)
    finally:
        conn.close()
    _bump(aid)
    items = [_cand_view(state[r], None, "mcp", assume_ro) for r in refs if r in state]
    return {"ok": True, "error": "", "title": slug, "base_url": "", "kept_confirmed": kept_confirmed,
            "items": items, "steps": res.get("steps") or [], "ms": res.get("ms", 0)}


def src_kind_label(cfg: dict) -> str:
    """给 invoke_hint / 卡片用的一行人话（stdio 显示命令行，http 显示 url）。"""
    t = (cfg or {}).get("transport")
    if t == "stdio":
        return " ".join([str((cfg or {}).get("command") or "")] + [str(a) for a in ((cfg or {}).get("args") or [])])[:120]
    return str((cfg or {}).get("url") or "")


def _cand_view(row: dict, warnings=None, source: str = "api", assume_read_only: bool = False) -> dict:
    """候选/能力的**统一视图** —— `discover` 与 `candidates_list` 必须返回同一个形状。

    ★ 为什么必须统一：前端靠 `is_write` 决定"写操作要不要二次确认"、靠 `param_summary` 展示参数。
    `discover` 最初直接把解析器的原始条目吐出去（只有 `read_only`，没有 `is_write`）→ 刚贴完文档
    那一步，POST/PUT 会显示成「只读」、写操作的勾选框也不会被禁用（服务端仍会拒，但界面在骗人）。

    M2 加了 `source`：mcp 的"是不是写操作"由 `read_only_hint` 判（不是 method），
    而且它是**工具的声明**而非接口 —— 所以额外回一个 `tool` 字段给前端展示。
    """
    from tools.capability_pool import api_entry, mcp_entry
    spec = _json_or_empty(row.get("invoke_spec"))
    src = "mcp" if (source or "api") == "mcp" else "api"
    build = mcp_entry if src == "mcp" else api_entry
    e = build({"ref": row.get("ref"), "name": row.get("name"),
               "input_schema": row.get("input_schema"), "invoke_spec": row.get("invoke_spec"),
               "read_only": row.get("read_only", 1), "confirmed_at": row.get("confirmed_at"),
               "enabled": row.get("enabled", 0)}, {"slug": "", "enabled": 1})
    is_write = _is_write_row(src, spec, assume_read_only)
    return {"ref": row.get("ref"), "name": row.get("name") or row.get("ref"),
            "confirmed": bool(row.get("confirmed_at")),
            "read_only": not is_write,          # ★ 派生值：未确认候选也不能谎报只读
            "is_write": is_write, "enabled": bool(row.get("enabled")),
            "source": src,
            "method": spec.get("method", ""), "url_template": spec.get("url_template", ""),
            "tool": spec.get("tool", ""), "read_only_hint": spec.get("read_only_hint"),
            "param_summary": e.param_summary,
            # ★ M5c-2'：工具描述（`abilities` = 自动摘要；`abilities_user` = 用户手写/AI 草稿导入的）
            "abilities": (row.get("abilities") or "").strip(),
            "abilities_user": (row.get("abilities_user") or "").strip(),
            "warnings": list(warnings or [])}


def candidates_list(sid: int, token: str) -> dict:
    """某来源下的能力（含未确认候选，供确认界面用）。"""
    aid = _require_account_id(token)
    conn = _db()
    try:
        srow = conn.execute("SELECT slug, source, config_json FROM tool_sources WHERE id=? AND account_id=?",
                            (int(sid), aid)).fetchone()
        if not srow:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "来源不存在")
        r0 = dict(srow)
        slug, src_kind = r0["slug"], r0.get("source") or "api"
        assume_ro = bool(_json_or_empty(r0.get("config_json")).get("assume_read_only", False))   # M5c-1
        rows = [dict(r) for r in conn.execute(
            "SELECT ref, name, enabled, read_only, confirmed_at, input_schema, invoke_spec, "
            "abilities, abilities_user FROM tool_capabilities "
            "WHERE account_id=? AND source=? AND ref LIKE ? ORDER BY ref", (aid, src_kind, _ref_like(src_kind, slug)))]
    finally:
        conn.close()
    return {"slug": slug, "source": src_kind, "items": [_cand_view(r, None, src_kind, assume_ro) for r in rows],
            "assume_read_only": assume_ro}


def confirm(req: ConfirmReq, token: str) -> dict:
    """人工确认：把勾选的候选变成"可被 Agent 调用"的能力。

    - 只读候选：直接确认（`enabled=1, confirmed_at=now`）；
    - **写方法候选**（`read_only=0`）：必须 `approve_write=true`，否则跳过并回报（默认不勾，见 §4.4）。
    """
    aid = _require_account_id(token)
    slug = (req.slug or "").strip()
    conn = _db()
    try:
        srow = conn.execute("SELECT id, enabled, source, slug, config_json FROM tool_sources WHERE account_id=? AND slug=?"
                            + (" AND source=?" if (req.source or "").strip() else ""),
                            ((aid, slug) if not (req.source or "").strip()
                             else (aid, slug, (req.source or "").strip().lower()))).fetchone()
        if not srow:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "来源不存在")
        sr0 = dict(srow)
        sid = int(sr0["id"])
        src_kind = sr0.get("source") or "api"
        assume_ro = bool(_json_or_empty(sr0.get("config_json")).get("assume_read_only", False))   # M5c-1
        confirmed, skipped = [], []
        for it in req.items:
            ref = (it.ref or "").strip()
            if not ref.startswith(_ref_prefix(src_kind, slug)):
                skipped.append({"ref": ref, "why": "ref 不属于该来源"})
                continue
            row = conn.execute("SELECT id, read_only, invoke_spec FROM tool_capabilities "
                               "WHERE account_id=? AND source=? AND ref=?",
                               (aid, src_kind, ref)).fetchone()
            if not row:
                skipped.append({"ref": ref, "why": "能力不存在（先「解析候选」）"})
                continue
            r = dict(row)
            try:
                _spec = json.loads(r.get("invoke_spec") or "{}")
            except Exception:  # noqa: BLE001
                _spec = {}
            is_write = _is_write_row(src_kind, _spec, assume_ro)   # ★ 服务端自证 + 来源级人工声明
            if is_write and not it.approve_write:
                skipped.append({"ref": ref, "why": "写操作（非只读）需显式勾选「我确认这是写操作」"})
                continue
            conn.execute("UPDATE tool_capabilities SET name=COALESCE(NULLIF(?,''), name), read_only=?, enabled=1, "
                         "confirmed_at=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
                         (it.name or "", 0 if is_write else 1, time.strftime("%Y-%m-%d %H:%M:%S"), r["id"]))
            confirmed.append(ref)
        conn.commit()
    finally:
        conn.close()
    _bump(aid)
    return {"confirmed": confirmed, "skipped": skipped}

# ============================ M5c-2'：工具级描述（用户可编辑 + AI 预写）============================
def load_source_and_caps(aid: int, source: str, slug: str) -> tuple[dict, list[dict]]:
    """取「来源行 + 它名下的工具行」——给能力撰写 AI 用（一套查询，避免两处口径分叉）。"""
    src_kind = "mcp" if (source or "").strip().lower() == "mcp" else "api"
    conn = _db()
    try:
        srow = conn.execute(
            "SELECT id, slug, name, source, config_json, abilities, docs, intro, state, enabled, note "
            "FROM tool_sources WHERE account_id=? AND source=? AND slug=?",
            (int(aid), src_kind, (slug or "").strip())).fetchone()
        if not srow:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "来源不存在")
        s = dict(srow)
        caps = [dict(r) for r in conn.execute(
            "SELECT ref, name, abilities, abilities_user, input_schema, invoke_spec, read_only, confirmed_at, enabled "
            "FROM tool_capabilities WHERE account_id=? AND source=? AND ref LIKE ? ORDER BY ref",
            (int(aid), src_kind, _ref_like(src_kind, s["slug"])))]
    finally:
        conn.close()
    return s, caps


def cap_abilities_save(req: CapAbilitiesReq, token: str) -> dict:
    """保存**工具级描述**（用户手写版）。`text` 传空串 = 清除覆盖、回到自动摘要。

    ★ 为什么单独存 `abilities_user` 而不覆盖 `abilities`：「发现 / 导入工具」会刷新自动摘要，
      覆盖式写法会把用户手写的内容冲掉（与 M5c-1「人工决定不能被自动动作覆盖」同一条教训）。
      读取时的优先级由 `capability_pool._entry_from_row` 决定（用户手写优先）。
    """
    aid = _require_account_id(token)
    slug = (req.slug or "").strip()
    if not slug:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "缺少 slug")
    src_kind = "mcp" if (req.source or "").strip().lower() == "mcp" else "api"
    prefix = _ref_prefix(src_kind, slug)
    saved, skipped = [], []
    conn = _db()
    try:
        for it in (req.items or []):
            ref = str((it or {}).get("ref") or "").strip()
            text = str((it or {}).get("text") or "").strip()[:600]
            if not ref:
                continue
            nm = str((it or {}).get("name") or "").strip()[:30]
            row = conn.execute(
                "SELECT id, ref, name FROM tool_capabilities WHERE account_id=? AND source=? AND ref=?",
                (aid, src_kind, ref)).fetchone()
            if not row:
                skipped.append({"ref": ref, "why": "这个工具不属于当前账号 / 来源"})
                continue
            if not str(row["ref"]).startswith(prefix):
                skipped.append({"ref": ref, "why": "ref 与来源不匹配"})
                continue
            if nm and nm != (row["name"] or ""):
                # ★ 人话名字：与描述同一处确认、同一处落库（名字也进词法与向量文本）
                conn.execute("UPDATE tool_capabilities SET abilities_user=?, name=?, updated_at=CURRENT_TIMESTAMP "
                             "WHERE id=?", (text, nm, row["id"]))
            else:
                conn.execute("UPDATE tool_capabilities SET abilities_user=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
                             (text, row["id"]))
            saved.append({"ref": ref, "text": text, "name": nm or (row["name"] or "")})
        conn.commit()
    finally:
        conn.close()
    if saved:
        _bump(aid)
        try:                     # 描述进了向量文本 → 让池版本失效后的重算自己发生
            from tools import capability_pool as pool
            pool.ensure_vectors_fresh(aid)
        except Exception as e:   # noqa: BLE001 - 重算失败不该让保存失败（下次路由会自愈）
            print("[tools_sources] 向量重算跳过:", e)
    return {"ok": True, "saved": saved, "skipped": skipped}
