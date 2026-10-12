"""工具池测评：**打真服务的 HTTP 集成测试**（v4.0 大点 03 · 阶段 5 脚本测试）。

对**正在运行的**服务（默认 http://localhost:8123）逐个打 11 个 /api/probe/* 接口。
★ 纪律：
  · 只走**读路径 + 守卫**，**不触发** run/judge/advise/commit 的真机与模型调用（不烧额度 ✓）；
  · token 用 `create_session()` 直接签发（**不碰密码** ✓），跑完删掉自己这条会话；
  · 不对账号数据做任何写入（save_cases 用**非法题集**与**临时题集**各一次，跑完清除）。

跑法： cd 项目根 && unset PYTHONPATH && .venv/Scripts/python.exe tests/test_out/probe_http_live.py
"""
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
os.environ.pop("PYTHONPATH", None)

from api.account import create_session                     # noqa: E402
from tools import probe_agent as pa                        # noqa: E402
from tools.schema_personal import get_personal_conn        # noqa: E402

BASE = os.getenv("PROBE_BASE", "http://localhost:8123").rstrip("/")
USER = os.getenv("PROBE_USER", "尼古喵喵").strip()
ok, bad = [], []


def check(name, cond, extra=""):
    (ok if cond else bad).append(name)
    print("%s %s%s" % ("  ✅" if cond else "  ❌", name, (" | " + str(extra)) if extra else ""))


def call(method, path, token=None, body=None, params=None):
    """发一个请求，返回 (状态码, 解析后的 JSON 或原文)。"""
    qs = dict(params or {})
    if token:
        qs["token"] = token
    url = BASE + path + ("?" + urllib.parse.urlencode(qs) if qs else "")
    data = None
    headers = {}
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    op = urllib.request.build_opener(urllib.request.ProxyHandler({}))     # 直连，不走代理 ✓
    try:
        with op.open(req, timeout=30) as r:
            raw = r.read().decode("utf-8", "replace")
            try:
                return r.status, json.loads(raw)
            except ValueError:
                return r.status, raw
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        try:
            return e.code, json.loads(raw)
        except ValueError:
            return e.code, raw
    except Exception as e:  # noqa: BLE001
        return 0, "%s: %s" % (type(e).__name__, e)


conn = get_personal_conn()
row = conn.execute("SELECT * FROM accounts WHERE username=?", (USER,)).fetchone()
conn.close()
if not row:
    print("✗ 账号不存在：%s" % USER)
    sys.exit(2)
acc = dict(row)
token = create_session(acc)          # 服务与本脚本共用一个 SQLite → 这个 token 服务端认 ✓
created_set = pa.load_cases(USER)    # 记住跑之前的题集，最后还原
print("基线：%s ｜ 服务 %s ｜ 池 %d 条" % (USER, BASE, len(acc)))
try:
    # ---- 服务可达 + 路由已注册 ----
    code, _ = call("GET", "/api/probe/overview")
    check("缺 token → 422（路由已注册、参数校验层拦下）", code == 422, code)
    code, _ = call("GET", "/api/probe/overview", token="无效token")
    check("无效 token → 401", code == 401, code)

    # ---- ① overview ----
    code, d = call("GET", "/api/probe/overview", token)
    check("overview 200", code == 200, code)
    if isinstance(d, dict):
        check("overview：池规模 > 0", d.get("pool_size", 0) > 0, d.get("pool_size"))
        check("overview：池版本是整数", isinstance(d.get("pool_version"), int), d.get("pool_version"))
        items = d.get("items") or []
        check("overview：工具条数 = 池规模", len(items) == d.get("pool_size"), len(items))
        check("overview：每条含 ref/name/source/体检灯",
              all(all(k in it for k in ("ref", "name", "source", "has_keywords", "has_desc", "warn"))
                  for it in items))
        # ★★ 这条是**服务端代码新鲜度**探针（2026-10-10）：Python 模块在进程启动时载入，
        #    改完 `tools/probe_runner.py` 不重启服务，接口返回的还是旧结构 —— 表现为
        #    「前端看不到 desc_state → 回退旧判断 → 照样显示『描述 缺』」。
        #    若这条失败：**重启服务**即可（不是代码问题）。
        _it = items[0] if items else {}
        check("overview：服务端已加载新代码（条目带 desc_state/kw_from；失败=服务需重启）",
              _it.get("desc_state") in ("user", "auto", "none") and bool(_it.get("kw_from")),
              "desc_state=%s kw_from=%s" % (_it.get("desc_state"), _it.get("kw_from")))
        check("overview：顶层带体检汇总（失败=服务需重启）",
              all(k in (d or {}) for k in ("problem_count", "auto_only", "derived_kw", "no_desc")),
              [k for k in ("problem_count", "auto_only", "derived_kw", "no_desc") if k not in (d or {})])
        check("overview：汇总与素材体检同口径（problem_count == health.problem_count）",
              d.get("problem_count") == (d.get("health") or {}).get("problem_count"),
              "%s vs %s" % (d.get("problem_count"), (d.get("health") or {}).get("problem_count")))
        check("overview：metrics 是对象（可能是空的 = 还没测过）", isinstance(d.get("metrics"), dict))
        check("overview：running 布尔", isinstance(d.get("running"), bool), d.get("running"))
        check("overview：sheet_count 整数", isinstance(d.get("sheet_count"), int), d.get("sheet_count"))
    else:
        check("overview 返回 JSON", False, str(d)[:120])

    # ---- ② sheets（严格格式过滤）----
    code, d = call("GET", "/api/probe/sheets", token)
    check("sheets 200 且是列表", code == 200 and isinstance(d.get("sheets"), list), code)
    if isinstance(d, dict) and d.get("sheets"):
        names = [s["name"] for s in d["sheets"]]
        import re
        check("sheets：文件名全部符合严格格式", all(re.match(r"^probe_打分表\d{8}_\d{6}\.md$", n) for n in names),
              names[:3])
        check("sheets：倒序", names == sorted(names, reverse=True))
        check("sheets：每项带 size/mtime", all("size" in s and "mtime" in s for s in d["sheets"]))
    else:
        check("sheets：目前为空（还没跑过正式测评）", True, "空列表 → 说明没有历史打分表")

    # ---- ③ cases ----
    code, d = call("GET", "/api/probe/cases", token)
    check("cases 200", code == 200 and "doc" in (d or {}), code)

    # ---- ④ status ----
    code, d = call("GET", "/api/probe/status", token)
    check("status 200 且 running=False（没在跑）", code == 200 and d.get("running") is False, code)
    check("status：带 gen 子对象（出题进度通道，空态也要是对象）",
          code == 200 and isinstance(d.get("gen"), dict), str(d.get("gen"))[:60])

    # ---- ⑤ sheet 的路径防护（真服务上也验一次）----
    code, d = call("GET", "/api/probe/sheet", token, params={"name": "../secret.md"})
    check("sheet：非法名 → 400", code == 400, code)
    code, d = call("GET", "/api/probe/sheet", token, params={"name": "probe_打分表20200101_000000.md"})
    check("sheet：不存在的合法名 → 404", code == 404, code)

    # ---- ⑥ 守卫：没有题集不许跑 / 没有记录不许判分与建议 ----
    if created_set:
        pa.clear_cases(USER)
    code, d = call("POST", "/api/probe/run", token, body={})
    check("run：无题集 → 400 且提示先出题", code == 400 and "题集" in str(d), "%s %s" % (code, str(d)[:60]))
    code, d = call("POST", "/api/probe/judge", token, body={})
    check("judge：无记录 → 400（未调模型）", code == 400, "%s %s" % (code, str(d)[:60]))
    code, d = call("POST", "/api/probe/advise", token, body={})
    check("advise：无记录 → 400（未调模型）", code == 400, "%s %s" % (code, str(d)[:60]))
    code, d = call("POST", "/api/probe/commit", token, body={})
    check("commit：无记录 → 400（未写库）", code == 400, "%s %s" % (code, str(d)[:60]))

    # ---- ⑦ 题集存取往返（非法拒绝 / 合法通过 / 读回一致）----
    code, d = call("POST", "/api/probe/save_cases", token, body={"doc": {"cases": []}})
    check("save_cases：空题集 → 400", code == 400 and "不合法" in str(d), "%s %s" % (code, str(d)[:60]))
    doc = {"name": "probe_http_live", "account": USER, "cases": [
        {"id": 1, "kind": "single", "q": "HTTP 集成测试用的题（不是真题）",
         "expect": {"kind": "tool", "ref": "mcp:pdd/search_goods"}, "chain": "x"}]}
    code, d = call("POST", "/api/probe/save_cases", token, body={"doc": doc})
    check("save_cases：合法题集 → 200", code == 200 and d.get("count") == 1, "%s %s" % (code, d))
    code, d = call("GET", "/api/probe/cases", token)
    check("cases：读回刚才那份", (d.get("count") == 1), d.get("count"))

    # ---- ⑧ 前端页面（服务端给的就是新版本吗）----
    code, txt = call("GET", "/")
    check("首页 200 且含新页签", code == 200 and "工具池测评" in str(txt), code)
    code, txt = call("GET", "/front/vendor/vue.global.prod.js")
    check("Vue 本地 vendor 可访问（离线前提）", code == 200 and len(str(txt)) > 10000, code)
finally:
    pa.clear_cases(USER)
    if created_set:
        pa.save_cases(USER, created_set)
    conn = get_personal_conn()
    conn.execute("DELETE FROM sessions WHERE token=?", (token,))     # 清掉本次测试签发的会话 ✓
    conn.commit()
    conn.close()
    print("\n（已还原题集、已删除本次测试签发的 token）")

print("\n" + "=" * 62)
print("通过 %d ／ 失败 %d" % (len(ok), len(bad)))
for n in bad:
    print("  -", n)
sys.exit(1 if bad else 0)
