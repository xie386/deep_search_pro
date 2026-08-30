"""验证产品/收藏 attributes 扩展字段链路：登录 -> 添加带描述的产品 -> 读回校验 -> 更新 -> 清理。
用法：python tests/m35_attrs_test.py（需服务运行中，且该账号 role=company）
"""
import json, os, urllib.request, urllib.parse

os.environ.pop("HTTP_PROXY", None); os.environ.pop("HTTPS_PROXY", None)
os.environ.pop("http_proxy", None); os.environ.pop("https_proxy", None)
os.environ["NO_PROXY"] = "*"; os.environ["no_proxy"] = "*"

BASE = "http://127.0.0.1:8123"


def http(method, path, data=None):
    req = urllib.request.Request(BASE + path,
                                 data=json.dumps(data).encode() if data else None,
                                 method=method, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        try: return e.code, json.loads(e.read().decode())
        except Exception: return e.code, {}


st, login = http("POST", "/api/login", {"username": "m2tester", "password": "m2pass123"})
token = login["token"]
print("[1] 登录:", st)

attrs = json.dumps({
    "description": "猫娘洗面奶：氨基酸配方温和洁面，专为敏感肌设计",
    "core_features": "氨基酸表活,无皂基,500ml",
    "target_customers": "20-35岁敏感肌女性",
}, ensure_ascii=False)

# 2) 添加带 attributes 的产品
qs = urllib.parse.urlencode({"token": token})
st, r = http("POST", f"/api/me/products?{qs}", {
    "product_name": "__测试产品_可删__", "category": "护肤", "price": 39.9, "attributes": attrs})
print("[2] 添加带attributes产品:", st, r)
pid = r.get("id")

# 3) 读回校验
st, ov = http("GET", f"/api/me/overview?{qs}")
prods = [p for p in ov.get("products", []) if p["id"] == pid]
back = prods[0] if prods else {}
parsed = json.loads(back["attributes"]) if back.get("attributes") else {}
print("[3] 读回 attributes:", parsed)
assert parsed.get("description", "").startswith("猫娘洗面奶"), "attributes 未落库！"

# 4) 更新（模拟前端保存行）
st, r2 = http("PUT", f"/api/me/products/{pid}?{qs}", {
    "product_name": "__测试产品_已更新__", "category": "护肤", "price": 49.9,
    "currency": "CNY", "status": "active",
    "attributes": json.dumps({"description": "更新后的描述", "core_features": "新版配方"},
                             ensure_ascii=False)})
print("[4] 更新:", st, r2)
st, ov = http("GET", f"/api/me/overview?{qs}")
p2 = [p for p in ov["products"] if p["id"] == pid][0]
print("    更新后:", json.loads(p2["attributes"], )["description"])

# 5) 清理
st, _ = http("DELETE", f"/api/me/products/{pid}?{qs}")
print("[5] 删除测试数据:", st)
print("ATTRS TEST DONE.")
