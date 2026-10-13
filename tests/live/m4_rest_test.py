"""M4 REST 冒烟：登录 -> GET subs(自动生成默认) -> PUT 改关键词 -> POST run(真实跑1-3分钟) -> GET reports。
用法：python tests/m4_rest_test.py（需服务运行中）
"""
import json, os, urllib.request, urllib.error, urllib.parse

os.environ.pop("HTTP_PROXY", None); os.environ.pop("HTTPS_PROXY", None)
os.environ.pop("http_proxy", None); os.environ.pop("https_proxy", None)
os.environ["NO_PROXY"] = "*"; os.environ["no_proxy"] = "*"
BASE = "http://127.0.0.1:8123"


def http(method, path, data=None):
    req = urllib.request.Request(BASE + path,
                                 data=json.dumps(data).encode() if data else None,
                                 method=method, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=300) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        try: return e.code, json.loads(e.read().decode())
        except Exception: return e.code, {}


# ── 2026-10-13 修（前置清理）：不再使用已删除的共享测试号 m2tester，
#    改为**自建临时账号 + 退出时自动回收**（照 tests/live/asr_endpoint_probe.py 的做法）。
import atexit as _atexit, sys as _sys, time as _time
_sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from tools.schema_personal import get_personal_conn as _pc, purge_account as _purge

USER = os.getenv("PROBE_ACCOUNT") or "t_m4rest_%d" % int(_time.time())
PWD = os.getenv("PROBE_PASSWORD") or "t_pwd_%d" % int(_time.time())


def _cleanup():
    try:
        _c = _pc()
        _row = _c.execute("SELECT id FROM accounts WHERE username=?", (USER,)).fetchone()
        _c.close()
        if _row:
            _purge(_row[0]); print("[cleanup] 临时账号 %s 已回收" % USER)
    except Exception as _e:                                          # noqa: BLE001
        print("[cleanup] 回收失败（可手动清理 %s）: %s" % (USER, _e))


_atexit.register(_cleanup)
http("POST", "/api/register", {"username": USER, "password": PWD, "role": "company"})
st, login = http("POST", "/api/login", {"username": USER, "password": PWD})
token = login["token"]
print("[1] 登录:", st)

qs = "token=" + urllib.parse.quote(token)
# 2) 默认订阅
st, d = http("GET", f"/api/digest/subs?{qs}")
sub = d.get("sub", {})
print(f"[2] 订阅: scope={sub.get('scope')} name={sub.get('name')} kws={sub.get('keywords')} schedule={sub.get('schedule')}")

# 3) 更新订阅（缩短为2个词，省额度）
kws = json.dumps(json.loads(sub["keywords"])[:2], ensure_ascii=False)
st, d = http("PUT", f"/api/digest/subs?{qs}", {"keywords": kws, "schedule": "weekly", "enabled": True})
print("[3] 更新订阅:", st, {k: d.get('sub', {}).get(k) for k in ('keywords', 'schedule', 'enabled')})

# 4) 手动触发一次（真实检索+LLM，约1-3分钟）
print("[4] POST /api/digest/run ...")
st, d = http("POST", f"/api/digest/run?{qs}")
print("    结果:", st, {k: v for k, v in d.items() if k != 'md_path'} if st == 200 else d)

# 5) 报告列表
st, d = http("GET", f"/api/reports?{qs}")
items = d.get("items", [])
print(f"[5] 报告列表: {len(items)} 条")
for it in items[:3]:
    print(f"    - {it['title']} | {it['item_count']}条 | {it['created_at']} | md={bool(it.get('download_md'))}")
print("M4 REST TEST DONE.")
