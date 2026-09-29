"""M3 冒烟测试：登录 -> /api/competitors 竞品清单 -> /api/export 导出 MD+PDF -> /api/download。
用法：python tests/m3_smoketest.py（需服务已启动：uvicorn api.server:app --port 8123）
"""
import json, os, urllib.request, urllib.error, urllib.parse

os.environ.pop("HTTP_PROXY", None); os.environ.pop("HTTPS_PROXY", None)
os.environ.pop("http_proxy", None); os.environ.pop("https_proxy", None)
os.environ["NO_PROXY"] = "*"; os.environ["no_proxy"] = "*"

BASE = "http://127.0.0.1:8123"
USER, PWD, ROLE = "m2tester", "m2pass123", "company"


def http(method, path, data=None, raw=False):
    url = BASE + path
    body = json.dumps(data).encode() if data is not None else None
    req = urllib.request.Request(url, data=body, method=method,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            payload = r.read()
            if raw:
                return r.status, len(payload), r.headers.get("Content-Type")
            return r.status, json.loads(payload.decode())
    except urllib.error.HTTPError as e:
        body = e.read()
        if raw:
            return e.code, len(body), e.headers.get("Content-Type")
        try:
            return e.code, json.loads(body.decode())
        except Exception:
            return e.code, {}
    except Exception as e:
        return -1, {"detail": f"{type(e).__name__}: {e}"}


# 1) 登录
st, login = http("POST", "/api/login", {"username": USER, "password": PWD})
if st == 401:
    http("POST", "/api/register", {"username": USER, "password": PWD, "role": ROLE})
    st, login = http("POST", "/api/login", {"username": USER, "password": PWD})
token = (login or {}).get("token")
print("[1] 登录:", st, login.get("account") if st == 200 else login)
assert token, "登录失败"

# 2) 竞品清单
st, comps = http("GET", f"/api/competitors?token={token}")
print("[2] /api/competitors:", st)
for it in (comps.get("items") or []):
    print(f"    - {it['comp_name']} ({it['category']}) 直接竞品={it['is_competitor']} 映射={it['mapped_product_ids']}")

# 3) 导出 MD + PDF
md_content = """# 测试简报\n\n## 摘要\n这是 **M3 导出** 的测试内容。\n\n| 维度 | 飞书 | 我们 |\n|---|---|---|\n| 定价 | 600 | 360 |\n"""
qs = urllib.parse.urlencode({"token": token, "title": "M3测试简报", "fmt": "both"})
st, exp = http("POST", f"/api/export?{qs}", {"content": md_content})
print("[3] /api/export:", st, exp)

# 4) 下载 PDF 验证
if exp.get("download_pdf"):
    p = exp["download_pdf"]
    sep = "&" if "?" in p else "?"
    q = p.split("?", 1)[1]
    # name 参数含中文，需重新编码
    from urllib.parse import parse_qsl
    enc_q = urllib.parse.urlencode(parse_qsl(q))
    st2, size, ctype = http("GET", p.split("?")[0] + "?" + enc_q, raw=True)
    print(f"[4] /api/download(pdf): status={st2} bytes={size} type={ctype}")
else:
    print("[4] 无 PDF（转换失败？）:", exp)

print("M3 SMOKE TEST DONE.")
