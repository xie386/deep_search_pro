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


st, login = http("POST", "/api/login", {"username": "m2tester", "password": "m2pass123"})
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
