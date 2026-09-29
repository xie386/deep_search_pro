"""前置工作验证：数据归属权 + /api/me/* CRUD。
场景：
  A. 新注册公司账号 -> overview 应全空（setup_done=false），看不到云协/飞书
  B. 填 profile + 加竞品/产品 -> overview 反映，且 /api/chat 工具只读自己的
  C. 新注册个人账号 -> 兴趣空；添加兴趣成功；看不到 alice 的耳机显卡
  D. alice 登录 -> 样例数据完好（回归）
用法：python tests/pre_work_test.py（需服务运行于 8123）
"""
import json, os, urllib.request, urllib.error, urllib.parse

os.environ.pop("HTTP_PROXY", None); os.environ.pop("HTTPS_PROXY", None)
os.environ.pop("http_proxy", None); os.environ.pop("https_proxy", None)
os.environ["NO_PROXY"] = "*"; os.environ["no_proxy"] = "*"
BASE = "http://127.0.0.1:8123"


def http(method, path, data=None):
    body = json.dumps(data).encode() if data is not None else None
    req = urllib.request.Request(BASE + path, data=body, method=method,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode())
        except Exception:
            return e.code, {}


def register_and_login(u, p, role):
    st, r = http("POST", "/api/login", {"username": u, "password": p})
    if st == 401:
        http("POST", "/api/register", {"username": u, "password": p, "role": role})
        st, r = http("POST", "/api/login", {"username": u, "password": p})
    assert st == 200, f"login failed {r}"
    return r["token"]


ok = fail = 0
def check(name, cond, extra=""):
    global ok, fail
    if cond: ok += 1; print(f"  ✅ {name}")
    else: fail += 1; print(f"  ❌ {name} {extra}")


print("[A] 新公司账号空置起步")
tok_c = register_and_login("pre_co_test", "p123456", "company")
st, ov = http("GET", f"/api/me/overview?token={tok_c}")
check("overview 可访问", st == 200)
check("竞品为空", ov.get("competitors") == [], str(ov.get("competitors"))[:80])
check("产品为空", ov.get("products") == [])
check("未完成设置 setup_done=false", ov.get("setup_done") is False)

print("[A2] 看不到 demo_company 的云协/飞书数据")
st, comps = http("GET", f"/api/me/competitors?token={tok_c}")
names = [c["comp_name"] for c in comps.get("items", [])]
check("无飞书/钉钉等样例", not any(n in names for n in ["飞书", "钉钉", "企业微信"]), str(names))

print("[B] 填写公司信息 + 增删")
qs = urllib.parse.urlencode({"token": tok_c})
st, _ = http("POST", f"/api/me/profile?{qs}", {"company_name": "测试科技有限公司"})
check("填写公司名", st == 200)
st, r = http("POST", f"/api/me/competitors?{qs}", {"comp_name": "某竞品A", "website": "https://a.com", "note": "测试"})
cid = r.get("id"); check("添加竞品", st == 200 and cid)
st, r = http("POST", f"/api/me/products?{qs}", {"product_name": "测试产品X", "category": "软件", "price": 199})
pid = r.get("id"); check("添加产品", st == 200 and pid)
st, ov = http("GET", f"/api/me/overview?token={tok_c}")
check("setup_done=true", ov.get("setup_done") is True)
check("竞品可见", [c["comp_name"] for c in ov["competitors"]] == ["某竞品A"])
st, _ = http("DELETE", f"/api/me/products/{pid}?{qs}")
check("删除产品", st == 200)
st, _ = http("DELETE", f"/api/me/competitors/{cid}?{qs}")
check("删除竞品", st == 200)

print("[C] 新个人账号空置 + 自助添加")
tok_p = register_and_login("pre_per_test", "p123456", "personal")
st, ov = http("GET", f"/api/me/overview?token={tok_p}")
check("兴趣为空(无显卡咖啡)", ov.get("interests") == [] and ov.get("watchlist") == [])
check("收藏为空(无Sony/Bose)", ov.get("products") == [], str(ov.get("products"))[:80])
qs_i = urllib.parse.urlencode({"token": tok_p})
st, r = http("POST", f"/api/me/interests?{qs_i}", {"interest_tag": "机械键盘"})
if st == 409:  # 重复运行残留，视为通过
    st = 200
check("添加兴趣(或已存在)", st == 200)
st, ov = http("GET", f"/api/me/overview?token={tok_p}")
check("新兴趣可见", any(i["interest_tag"] == "机械键盘" for i in ov["interests"]))

print("[D] alice 样例回归（直接查库，alice 密码不可知）")
import sqlite3
conn = sqlite3.connect(os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "data", "personal.db"))
n_int = conn.execute("SELECT COUNT(*) FROM interests WHERE owner_id=(SELECT id FROM accounts WHERE username='alice')").fetchone()[0]
conn.close()
check("alice 样例数据完好", n_int == 3, f"interests={n_int}")

print(f"\nRESULT: {ok} passed, {fail} failed")
exit(0 if fail == 0 else 1)
