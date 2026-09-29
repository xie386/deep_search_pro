# -*- coding: utf-8 -*-
"""M1 真机 e2e：**用真实公开 API（Open-Meteo，免密钥）跑通"配置 → 发现 → 确认 → 调用"全链路**。

为什么选 Open-Meteo：项目天气卡本来就用它（`api/server.py` 的 `_wx_json`），是"已在用、可信、
免密钥"的真实来源，不需要任何新账号或额度。

流程（全部真调，不桩）：
  1) 起一个**临时后端**（端口 8124+，绝不动用户在跑的服务；结束必杀）
  2) 从库里取一个**既有有效会话 token**（不猜密码、不新建测试账号）
  3) 建来源 → 粘贴 OpenAPI → 解析候选 → 确认（只读）
  4) 断言：候选确认前**不入池**、确认后**入池**并被路由器注入
  5) 真调用 `invoke_tool`（走 api_driver → Open-Meteo HTTPS）→ 断言拿到气温
  6) 审计行断言（`data/audit/commands.jsonl` 末行）
  7) 清理：删掉本脚本建的来源（连带能力）→ 池回到"只有 3 条 CLI"→ 杀掉临时后端

运行（需要本机 .venv）：
    unset PYTHONPATH && .venv/Scripts/python.exe tests/m1_weather_e2e.py
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

PASS, FAIL = [], []


def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name)
    print("%s %s%s" % ("  ✅" if cond else "  ❌", name, (" | " + str(extra)) if extra else ""))


def _requests():
    import urllib.request as ur
    return ur


def http_json(url, *, method="GET", payload=None, timeout=30):
    """绕开系统代理直连本机（项目约定：测试访问本地服务必须 trust_env=False）。"""
    import urllib.request as ur
    from urllib.error import HTTPError
    data = None
    headers = {}
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = ur.Request(url, data=data, headers=headers, method=method)
    opener = ur.build_opener(ur.ProxyHandler({}))
    try:
        with opener.open(req, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        try:
            return e.code, json.loads(body)
        except Exception:  # noqa: BLE001
            return e.code, {"raw": body[:200]}


SPEC_TEXT = json.dumps({
    "openapi": "3.0.0",
    "info": {"title": "Open-Meteo Forecast", "version": "1.0.0"},
    "servers": [{"url": "https://api.open-meteo.com"}],
    "paths": {
        "/v1/forecast": {
            "get": {
                "operationId": "getForecastCurrent",
                "summary": "按经纬度查当前天气（气温/风速/天气码）",
                "parameters": [
                    {"name": "latitude", "in": "query", "required": True, "schema": {"type": "number"}},
                    {"name": "longitude", "in": "query", "required": True, "schema": {"type": "number"}},
                    {"name": "current", "in": "query", "required": True, "schema": {"type": "string"}},
                ],
                "responses": {"200": {"description": "ok"}},
            }
        }
    },
}, ensure_ascii=False)

SLUG = "openmeteo"
REF = "api:%s#getForecastCurrent" % SLUG


def pick_free_port(start=8124, tries=6):
    import socket
    for p in range(start, start + tries):
        s = socket.socket()
        try:
            s.bind(("127.0.0.1", p))
            s.close()
            return p
        except OSError:
            s.close()
    raise RuntimeError("找不到空闲端口")


def pick_token():
    """库里取一个既有有效会话（不猜密码、不建新账号）。"""
    from tools.schema_personal import get_personal_conn
    conn = get_personal_conn()
    try:
        row = conn.execute("SELECT s.token, a.id AS aid, a.username FROM sessions s "
                           "JOIN accounts a ON a.id = s.account_id "
                           "WHERE a.username = ? ORDER BY s.login_at DESC LIMIT 1", ("尼古喵喵",)).fetchone()
    finally:
        conn.close()
    if not row:
        raise RuntimeError("库里没有 尼古喵喵 的有效会话 token（先用网页登录一次）")
    return dict(row)["token"], int(dict(row)["aid"]), dict(row)["username"]


def baseline_cli(account_id):
    from tools.schema_personal import get_personal_conn
    conn = get_personal_conn()
    try:
        return conn.execute("SELECT COUNT(*) n FROM tool_capabilities WHERE account_id=? AND source='cli'",
                            (account_id,)).fetchone()["n"]
    finally:
        conn.close()


def wait_ready(port, token, timeout=150):
    """就绪判据：本机端口能连 + `/api/tools/sources` 有 HTTP 响应（不是 000）。"""
    import urllib.request as ur
    from urllib.error import HTTPError
    t0 = time.time()
    url = "http://127.0.0.1:%d/api/tools/sources?token=%s" % (port, token)
    while time.time() - t0 < timeout:
        try:
            opener = ur.build_opener(ur.ProxyHandler({}))
            with opener.open(url, timeout=5) as r:
                if r.status == 200:
                    return True, time.time() - t0
        except HTTPError as e:
            if e.code in (401, 403, 404):      # 服务活着（404 = 老代码，后面会判）
                return True, time.time() - t0
        except Exception:  # noqa: BLE001
            pass
        time.sleep(2)
    return False, time.time() - t0


def main() -> int:
    print("=== M1 真机 e2e：Open-Meteo（真实公开 API，免密钥）===\n")
    port = pick_free_port()
    token, aid, username = pick_token()
    base_cli = baseline_cli(aid)
    from tools import capability_pool as _cp0
    base_api = len([e for e in _cp0.pool_entries(aid, enabled_only=False) if e.source == "api"])
    print("  账号：%s (id=%d)｜临时后端端口：%d｜接前 CLI 能力数：%d\n" % (username, aid, port, base_cli))

    logf = ROOT / "tests" / "test_out" / "m1_e2e_backend.log"
    logf.parent.mkdir(parents=True, exist_ok=True)
    log = open(str(logf), "w", encoding="utf-8")
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)
    py = str(ROOT / ".venv" / "Scripts" / "python.exe")
    proc = subprocess.Popen([py, "-m", "uvicorn", "api.server:app", "--host", "127.0.0.1",
                             "--port", str(port), "--log-level", "warning"],
                            cwd=str(ROOT), stdout=log, stderr=subprocess.STDOUT, env=env)
    try:
        ok, secs = wait_ready(port, token)
        check("临时后端已就绪（%ds）" % int(secs), ok)
        if not ok:
            return 1

        api = "http://127.0.0.1:%d" % port
        q = "?token=%s" % token

        # ---- 1) 建来源 ----
        st, d = http_json(api + "/api/tools/source/save" + q, method="POST", payload={
            "slug": SLUG, "name": "Open-Meteo 天气", "base_url": "https://api.open-meteo.com",
            "auth_type": "none", "abilities": "查指定经纬度的实时天气与气温（免密钥）", "timeout": 20})
        check("建来源：POST /api/tools/source/save", st == 200 and d.get("slug") == SLUG, "HTTP %s" % st)

        st, lst = http_json(api + "/api/tools/sources" + q)
        src = next((x for x in lst.get("items", []) if x["slug"] == SLUG), None)
        check("来源列表可见 + state=configured", st == 200 and src and src["state"] == "configured")

        # ---- 2) 体检（不联网，只校验配置）----
        st, t = http_json(api + "/api/tools/source/%d/test%s" % (src["id"], q), method="POST")
        check("体检：配置校验通过", st == 200 and t.get("ok") is True, t.get("steps"))

        # ---- 3) 发现候选 ----
        st, disc = http_json(api + "/api/tools/discover" + q, method="POST",
                             payload={"slug": SLUG, "text": SPEC_TEXT})
        check("解析 OpenAPI → 候选 %d 个" % len(disc.get("items", [])),
              st == 200 and disc.get("ok") and len(disc.get("items", [])) == 1)

        st, cands = http_json(api + "/api/tools/source/%d/candidates" % src["id"] + q)
        items = cands.get("items", [])
        check("候选**未确认前不进池**（enabled/confirmed 状态正确）",
              len(items) == 1 and items[0]["confirmed"] is False and items[0]["read_only"] is True)
        check("候选带参数摘要", "latitude" in (items[0]["param_summary"] or ""), items[0]["param_summary"])
        # ★ 两个入口的形状必须一致：前端靠 `is_write` 决定"写操作要不要二次确认"、靠 `param_summary`
        # 展示参数。曾经 discover 直接吐解析器条目（没有 is_write）→ 刚贴完文档那一步写操作会显示成
        # 「只读」且勾选框不被禁用（服务端仍会拒，但界面在骗人）。
        disc_item = (disc.get("items") or [{}])[0]
        check("discover 与 /candidates 返回形状一致（含 is_write / param_summary）",
              set(disc_item) == set(items[0]) and disc_item.get("is_write") is False
              and disc_item.get("confirmed") is False,
              "差异字段=%s" % sorted(set(disc_item) ^ set(items[0])))

        from tools import capability_pool as cp
        # 用户自己可能已经接过 API（如 xxapi）→ 只断言"本用例这条 ref"还没进池，别动别人的
        check("本用例这条 ref 此刻还没进池", REF not in [e.ref for e in cp.pool_entries(aid)], REF)

        # ---- 4) 确认 → 入池 ----
        st, cf = http_json(api + "/api/tools/confirm" + q, method="POST",
                           payload={"slug": SLUG, "items": [{"ref": REF, "name": "当前天气", "approve_write": False}]})
        check("确认只读能力", st == 200 and cf.get("confirmed") == [REF], cf.get("skipped"))

        entries = cp.pool_entries(aid)
        api_e = [e for e in entries if e.ref == REF]
        check("确认后**入池**", len(api_e) == 1, REF)
        check("能力卡带 invoke_tool 用法与参数", api_e and "invoke_tool" in api_e[0].card_text
              and "latitude" in api_e[0].card_text)
        check("能力卡带**可直接照抄的调用示例**（减少模型「该怎么调」的推理）",
              api_e and ('invoke_tool(ref="%s", params={' % REF) in api_e[0].card_text,
              (api_e[0].card_text.splitlines()[-1] if api_e else ""))
        check("混合池：CLI 能力数不变（%d）" % base_cli,
              len([e for e in entries if e.source == "cli"]) == base_cli)

        # ---- 5) 路由器能命中（注入块里出现这条能力）----
        try:
            from tools import tool_router
            r = tool_router.route_tools(aid, "成都今天天气多少度")
            brief = (r.brief or "") + "\n" + (r.examples or "")
            check("路由器把这条能力注入给模型", REF in brief or "Open-Meteo" in brief or "当前天气" in brief,
                  "mode=%s hits=%s" % (r.mode, getattr(r, "hits", None)))
        except Exception as e:  # noqa: BLE001
            check("路由器把这条能力注入给模型", False, "路由异常：%s: %s" % (type(e).__name__, e))

        # ---- 6) 真调用（走 api_driver → 真实 HTTPS）----
        from api.context import set_owner_context
        from tools.capability_invoke import invoke
        set_owner_context(aid)
        out = invoke(REF, {"latitude": 30.66, "longitude": 104.06, "current": "temperature_2m"}, account_id=aid)
        check("真调用 Open-Meteo 成功（HTTP 200）", out["ok"] and out.get("status") == 200,
              (out.get("text") or "")[:120].replace("\n", " "))
        check("返回体里有温度数据", "temperature" in (out.get("text") or ""))
        check("返回文本带来源与 ref 提示", REF in (out.get("text") or "") and "来源：" in (out.get("text") or ""))

        # ---- 7) 审计行 ----
        audit = ROOT / "data" / "audit" / "commands.jsonl"
        last = {}
        if audit.exists():
            lines = [ln for ln in audit.read_text(encoding="utf-8").strip().splitlines() if ln.strip()]
            last = json.loads(lines[-1]) if lines else {}
        check("审计行：tool=invoke_tool / source=api / ok=True",
              last.get("tool") == "invoke_tool" and last.get("source") == "api" and last.get("ok") is True
              and last.get("ref") == REF, {k: last.get(k) for k in ("source", "ref", "status", "ok")})
        check("审计行含 args_digest 与耗时", bool(last.get("args_digest")) and last.get("ms") is not None)

        # ---- 8) 清理（删来源连带能力）----
        st, dl = http_json(api + "/api/tools/source/%d%s" % (src["id"], q), method="DELETE")
        check("删来源连带清能力（%s 条）" % dl.get("deleted_capabilities"), st == 200)
        entries = cp.pool_entries(aid, enabled_only=False)
        check("本用例这条 ref 已从池里删掉（别人的不动）",
              REF not in [e.ref for e in cp.pool_entries(aid)], REF)
        check("池回到基线（CLI %d / API %d）" % (base_cli, base_api),
              len([e for e in entries if e.source == "api"]) == base_api
              and len([e for e in entries if e.source == "cli"]) == base_cli)
    finally:
        try:
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                           capture_output=True, timeout=30)
            print("\n  [清理] 已杀掉临时后端 pid=%d" % proc.pid)
        except Exception as e:  # noqa: BLE001
            print("\n  [清理] 杀临时后端失败：%s" % e)
        try:
            log.close()
        except Exception:  # noqa: BLE001
            pass

    print("\n" + "=" * 60)
    print("M1 真机 e2e：通过 %d，失败 %d" % (len(PASS), len(FAIL)))
    if FAIL:
        print("失败项：")
        for f in FAIL:
            print("  -", f)
        return 1
    print("全部通过 ✅")
    return 0


if __name__ == "__main__":
    sys.exit(main())
