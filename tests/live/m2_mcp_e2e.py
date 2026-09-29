# -*- coding: utf-8 -*-
"""M2 真机 e2e：MCP 全链路（保存 → 体检 → 发现 → 确认 → 入池 → 真调用 → 审计）。

与 `m1_weather_e2e.py` 同款思路：**不 mock 驱动**，直接打真实链路。两种来源都跑：

  A. **真实第三方 MCP server**：`uvx mcp-server-time`（官方参考实现，PyPI 上的包）。
     首次运行 uvx 要下载+建环境（实测冷启动 ~15s，之后快）；没网/没 uvx 时**自动降级**为只跑 B。
  B. **本地 stdio server**：临时脚本 + 本 venv 的 python（零网络依赖，永远可跑）。

断言口径（对齐方案 §M2-4/§M2-5）：
  · 未确认候选不入池、也不能被调用；写工具（未声明只读）必须显式勾选
  · 确认后进池、卡片带 ref 与调用示例
  · 真调用拿到真实数据（不是空壳），审计 jsonl 里 source=mcp
  · 同一账号里 CLI / API / MCP 三种来源同池共存

运行：.venv/Scripts/python.exe tests/m2_mcp_e2e.py
（服务不用起 —— 这条链路直接打 Python 层，跟 m5b/m1 的 e2e 一样）
"""
import json
import os
import shutil
import subprocess
import sys
import textwrap
import time

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
os.environ.pop("PYTHONPATH", None)

from api import tools_sources as tsp                       # noqa: E402
from tools import capability_invoke as ci                  # noqa: E402
from tools import capability_pool as cp                    # noqa: E402
from tools import mcp_probe                                # noqa: E402
from tools.schema_personal import get_personal_conn, purge_account  # noqa: E402

PASS, FAIL = [], []


def check(name, ok, detail=""):
    (PASS if ok else FAIL).append(name)
    print("  %s %s%s" % ("✅" if ok else "❌", name, ("  ← " + str(detail)[:220]) if detail else ""))


def _host_reachable(url, timeout=5.0):
    """TCP 探一下主机能不能连（决定 C 段跑不跑；离线/被墙时自动跳过，不当失败）。"""
    import socket
    from urllib.parse import urlparse
    u = urlparse(url)
    try:
        with socket.create_connection((u.hostname, u.port or (443 if u.scheme == "https" else 80)), timeout=timeout):
            return True
    except Exception:  # noqa: BLE001
        return False


def mk_account(tag):
    conn = get_personal_conn()
    try:
        aid = int(conn.execute("INSERT INTO accounts (username, password_hash, role) VALUES (?,?,?)",
                               ("m2e2e_%s_%d" % (tag, int(time.time() * 1000) % 100000), "x", "user")).lastrowid)
        token = "tok_%d_%d" % (aid, int(time.time() * 1000) % 100000)
        conn.execute("INSERT INTO sessions (token, account_id, login_at) VALUES (?,?,?)", (token, aid, time.time()))
        conn.commit()
    finally:
        conn.close()
    return aid, token


LOCAL_SERVER = textwrap.dedent('''
    from mcp.server import MCPServer
    srv = MCPServer("e2e-demo")

    @srv.tool(description="查天气（只读）", annotations={"readOnlyHint": True})
    def get_weather(city: str) -> dict:
        return {"city": city, "temp": 21, "src": "local-stdio"}

    @srv.tool(description="下单（写；未声明只读）")
    def place_order(sku: str, qty: int = 1) -> dict:
        return {"ok": True, "sku": sku, "qty": qty}

    if __name__ == "__main__":
        srv.run()
''')


def run_flow(aid, token, slug, sfx, transport_cfg, expect_tools, call_name, call_args, want_in_text):
    """一条来源的完整链路。"""
    req = tsp.ApiSourceReq(slug=slug, name="e2e " + slug, source="mcp", abilities="端到端验收用的 MCP 服务",
                           transport=transport_cfg["transport"], command=transport_cfg.get("command", ""),
                           args_json=json.dumps(transport_cfg.get("args", [])),
                           url=transport_cfg.get("url", ""), timeout=transport_cfg.get("timeout", 120),
                           allow_stdio_commands=transport_cfg.get("allow_stdio_commands", ""))
    sid = int(tsp.source_save(req, token)["id"])
    check("%s 来源保存" % sfx, sid > 0)

    probe = tsp.source_test(sid, token)
    check("%s 七步体检通过（%d 个工具）" % (sfx, probe.get("tool_count", 0)), probe["ok"] is True,
          " ".join("%s=%s" % (s["name"], s["detail"][:60]) for s in probe["steps"] if not s["ok"]))
    check("%s 体检恰好七步且都带说明" % sfx,
          len(probe["steps"]) == 7 and all(s.get("detail") is not None for s in probe["steps"]))

    disc = tsp.discover(tsp.DiscoverReq(slug=slug, source="mcp"), token)
    check("%s 发现工具（%d 个）" % (sfx, len(disc.get("items", []))), disc["ok"] is True, disc.get("error"))
    items = {i["tool"]: i for i in disc.get("items", [])}
    check("%s 工具名符合预期 %s" % (sfx, sorted(items)), sorted(items) == sorted(expect_tools), sorted(items))
    check("%s 未确认 → 一条都不进池" % sfx, cp.pool_entries(aid) == [])
    check("%s ref 形状 mcp:<短名>/<工具>" % sfx,
          all(i["ref"].startswith("mcp:%s/" % slug) for i in disc["items"]))

    ro = [i for i in disc["items"] if not i["is_write"]]
    wr = [i for i in disc["items"] if i["is_write"]]
    # ★ 判定一致性（而不是"必须两种都有"）：hint 明确 true → 只读；未声明 / false → 按写处理。
    #   真实服务可能全是只读（如 uvx mcp-server-time），那是对的。
    consistent = all(((i["read_only_hint"] is True) != bool(i["is_write"])) for i in disc["items"])
    check("%s 只读/写判定与 read_only_hint 一致（只读 %d · 写 %d）" % (sfx, len(ro), len(wr)),
          consistent,   # ★ 不要求"必须有只读工具"：真实服务可能一个 readOnlyHint 都不给（全按写处理）
          [(i["tool"], i["read_only_hint"], i["is_write"]) for i in disc["items"]])
    check("%s 写操作没有 read_only_hint=true" % sfx, all(i["read_only_hint"] is not True for i in wr))

    # 未确认时调用必须被拒（只读放行链路）
    r0 = ci.invoke("mcp:%s/%s" % (slug, call_name), call_args, account_id=aid)
    check("%s 未确认时调用被拒" % sfx, r0["ok"] is False and r0["rejected"], r0.get("rejected", "")[:80])

    # 写操作不勾二次确认 → 跳过
    if wr:
        out = tsp.confirm(tsp.ConfirmReq(slug=slug, source="mcp",
                                         items=[tsp.CandidateIn(ref=wr[0]["ref"])]), token)
        check("%s 写操作需显式勾选" % sfx, out["confirmed"] == [] and out["skipped"])

    want_ref = "mcp:%s/%s" % (slug, call_name)
    # ★ 真实服务常常**不声明 readOnlyHint**（如魔搭那只拼多多）→ 按"写"处理 → 用户必须显式勾选。
    #   这里就模拟"用户点了那个框"：按发现结果的判定传 approve_write。
    want_item = next((i for i in disc["items"] if i["ref"] == want_ref), {})
    out = tsp.confirm(tsp.ConfirmReq(slug=slug, source="mcp",
                                     items=[tsp.CandidateIn(ref=want_ref, name="e2e 目标工具",
                                                            approve_write=bool(want_item.get("is_write")))]), token)
    check("%s 确认后入库" % sfx, want_ref in out["confirmed"], out)

    entries = {e.ref: e for e in cp.pool_entries(aid)}
    check("%s 确认后进池且带调用示例" % sfx,
          want_ref in entries and "invoke_tool(ref=" in entries[want_ref].card_text)
    check("%s 人工改的名字保留" % sfx, want_ref in entries and entries[want_ref].name == "e2e 目标工具")
    check("%s 来源级能力描述拼进卡片" % sfx,
          want_ref in entries and "端到端验收用的 MCP 服务" in entries[want_ref].card_text)

    r = ci.invoke(want_ref, call_args, account_id=aid)
    check("%s 真调用成功且拿到真实数据" % sfx, r["ok"] is True and want_in_text in (r["text"] or ""),
          (r.get("text") or r.get("rejected", ""))[:200])
    check("%s 返回文本在上限内" % sfx, len(r.get("text") or "") <= ci.MAX_RESULT_CHARS)
    check("%s 审计/结果里的 source=mcp" % sfx, r.get("source") == "mcp")

    # 幂等：重跑发现不应清掉人工决定
    again = tsp.discover(tsp.DiscoverReq(slug=slug, source="mcp"), token)
    check("%s 重复发现保留已确认（kept_confirmed=%d）" % (sfx, again.get("kept_confirmed", 0)),
          again["ok"] is True and again.get("kept_confirmed", 0) >= 1)
    entries2 = {e.ref: e for e in cp.pool_entries(aid)}
    check("%s 重复发现后名字没被刷掉" % sfx, entries2.get(want_ref) is not None
          and entries2[want_ref].name == "e2e 目标工具")
    return {"slug": slug, "tools": len(items), "ms": probe.get("ms")}


def main():
    print("=" * 72)
    print("M2 真机 e2e：MCP 全链路")
    print("=" * 72)

    # ---------- A. 真实第三方 server（uvx mcp-server-time）----------
    uvx = shutil.which("uvx")
    print("\n[A] 真实第三方 MCP server：uvx mcp-server-time")
    if not uvx:
        print("  ⚠️ 没找到 uvx → 跳过 A（只跑本地 stdio）")
    else:
        aid, token = mk_account("real")
        try:
            st = run_flow(aid, token, "realtime", "A",
                          {"transport": "stdio", "command": uvx, "args": ["mcp-server-time"],
                           "timeout": 240, "allow_stdio_commands": "uvx"},
                          expect_tools=["convert_time", "get_current_time"],
                          call_name="get_current_time", call_args={"timezone": "Asia/Shanghai"},
                          want_in_text="Asia/Shanghai")
            check("A 三种来源同池共存（CLI/API/MCP 的适配器都注册了）",
                  set(cp._ADAPTERS.keys()) == {"cli", "api", "mcp"}, sorted(cp._ADAPTERS.keys()))
        except Exception as e:  # noqa: BLE001
            check("A 真实第三方 server 链路", False, "%s: %s" % (type(e).__name__, e))
        finally:
            purge_account(aid)

    # ---------- B. 本地 stdio server（零网络）----------
    print("\n[B] 本地 stdio MCP server（零网络依赖）")
    aid, token = mk_account("local")
    srvpy = os.path.join(os.environ.get("TEMP", "."), "m2_e2e_server.py")
    with open(srvpy, "w", encoding="utf-8") as f:
        f.write(LOCAL_SERVER)
    try:
        run_flow(aid, token, "localsrv", "B",
                 {"transport": "stdio", "command": sys.executable, "args": [srvpy], "timeout": 60,
                  "allow_stdio_commands": os.path.basename(sys.executable)},
                 expect_tools=["get_weather", "place_order"],
                 call_name="get_weather", call_args={"city": "成都"}, want_in_text="成都")
        # 审计真的落盘了（与 CLI 同一份 jsonl）
        audit = os.path.join(ROOT, "data", "audit", "commands.jsonl")
        if os.path.exists(audit):
            with open(audit, encoding="utf-8") as f:
                tail = f.read().splitlines()[-120:]
            hits = [json.loads(ln) for ln in tail if '"source": "mcp"' in ln or '"source":"mcp"' in ln]
            check("B 审计 jsonl 里有 source=mcp 的记录", bool(hits), len(hits))
        else:
            check("B 审计 jsonl 存在", False, audit)
    except Exception as e:  # noqa: BLE001
        check("B 本地 stdio 链路", False, "%s: %s" % (type(e).__name__, e))
    finally:
        purge_account(aid)
        try:
            os.remove(srvpy)
        except Exception:  # noqa: BLE001
            pass

    # ---------- C. 真实 Streamable HTTP MCP 服务（魔搭社区，免 token；离线自动跳过）----------
    print("\n[C] 真实 HTTP MCP 服务（魔搭 ModelScope 部署的拼多多选品）")
    HTTP_URL = "https://mcp.api-inference.modelscope.net/9f994a8eb82847/mcp"
    if not _host_reachable(HTTP_URL, 6.0):
        print("  ⚠️ 连不上 %s → 跳过 C（离线/被墙时只跑 A/B）" % HTTP_URL)
    else:
        aid, token = mk_account("http")
        try:
            run_flow(aid, token, "pdd", "C",
                     {"transport": "http", "url": HTTP_URL, "timeout": 90},
                     expect_tools=["explore_deals", "get_goods_detail", "search_goods"],
                     call_name="search_goods", call_args={"keyword": "iPhone 16"},
                     want_in_text="商品")
            # ★ 真实 HTTP 服务的两个特征（都是真机才发现的口径）
            d = tsp.discover(tsp.DiscoverReq(slug="pdd", source="mcp"), token)
            hints = [i["read_only_hint"] for i in d["items"]]
            check("C 该服务未声明 readOnlyHint（未声明一律按写处理）", all(h is None for h in hints), hints)
            r = ci.invoke("mcp:pdd/search_goods", {"keyword": "iPhone 16"}, account_id=aid)
            check("C 返回体在上限内（该服务真实返回约 4.4KB）", 0 < len(r.get("text") or "") <= ci.MAX_RESULT_CHARS,
                  len(r.get("text") or ""))
            tsp.confirm(tsp.ConfirmReq(slug="pdd", source="mcp", items=[
                tsp.CandidateIn(ref="mcp:pdd/get_goods_detail", approve_write=True)]), token)
            check("C 业务失败也被如实带回来（该服务把错误包在成功响应里，不出 MCP error）",
                  "查询失败" in ci.invoke("mcp:pdd/get_goods_detail", {"goods_sign": "dummy"},
                                          account_id=aid).get("text", ""))
        except Exception as e:  # noqa: BLE001
            check("C 真实 HTTP 服务链路", False, "%s: %s" % (type(e).__name__, e))
        finally:
            purge_account(aid)

    print("\n" + "=" * 72)
    print("M2 真机 e2e：通过 %d，失败 %d" % (len(PASS), len(FAIL)))
    if FAIL:
        print("失败项：" + " / ".join(FAIL))
        sys.exit(1)


if __name__ == "__main__":
    main()
