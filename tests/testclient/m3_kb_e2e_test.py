"""M3 RAG 知识库 · 端到端验证（真实服务 + bge GPU + 全局 Agent）。

验证点：默认无库 / 入库+md5 去重 / 检索命中 / .md-only 限制 / 评估脏检测 /
AI 对话经 query_kb 引用知识库 / digest 周报全模式必查（可选跑）。

运行（前置：服务已起 8123）：
  .venv/Scripts/python.exe tests/m3_kb_e2e_test.py
  （加 --with-digest 额外跑周报必查——耗时长，默认跳过）
"""
import argparse
import os
import sys
import time

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
sys.path = [p for p in sys.path if "hermes-agent" not in p and "hermes_agent" not in p]
os.environ.pop("PYTHONPATH", None)
os.environ.pop("HTTP_PROXY", None)
os.environ.pop("HTTPS_PROXY", None)

import requests

PASS = 0
FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✅ {name}")
    else:
        FAIL += 1
        print(f"  ❌ {name}  {detail}")


SEMI_REPORT = """# 2026 半导体行业报告（M3 e2e）

## 摘要
AI 芯片需求高增，HBM 内存供不应求，先进封装产能紧张。

## 关键数据
英伟达 H200 订单排到 2027；台积电 CoWoS 产能翻倍仍不够分；
国产替代：华为昇腾 910C 良率爬坡，预计 2026 Q4 规模出货。

## 观点
关注先进封装设备与 HBM 材料；逻辑代工竞争白热化。"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--account", default="m2tester")
    ap.add_argument("--password", default="m2pass123")
    ap.add_argument("--role", default="company")
    ap.add_argument("--base", default="http://127.0.0.1:8123")
    ap.add_argument("--with-digest", action="store_true", help="额外跑周报知识库必查（慢）")
    args = ap.parse_args()

    s = requests.Session()
    s.trust_env = False
    BASE = args.base

    r = s.post(BASE + "/api/login", json={"username": args.account, "password": args.password, "role": args.role}, timeout=30)
    assert r.status_code == 200, f"登录失败: {r.text[:200]}"
    tok = r.json()["token"]
    H = {"token": tok}
    print(f"== M3 端到端: {args.account} ==")

    # 0. 清理旧测试文档
    d0 = s.get(BASE + "/api/kb/status", params=H, timeout=30).json()
    for doc in (d0.get("docs") or []):
        if "M3 e2e" in doc["title"] or "半导体" in doc["title"]:
            s.delete(BASE + f"/api/kb/docs/{doc['source_id']}", params=H, timeout=30)

    # 1. 默认无库（批注 13）——用全新账号验证（老账号可能已被测试建过库）
    fresh = args.account + "_fresh"
    r = s.post(BASE + "/api/register", json={"username": fresh, "password": "fresh123456", "role": args.role}, timeout=30)
    if r.status_code in (200, 409):
        tok_f = s.post(BASE + "/api/login", json={"username": fresh, "password": "fresh123456", "role": args.role}, timeout=30).json()["token"]
        d = s.get(BASE + "/api/kb/status", params={"token": tok_f}, timeout=30).json()
        check("新账号默认无库", d.get("exists") is False, str(d))

    # 2. 入库（source_kind=export 模拟导出入库确认后）
    r = s.post(BASE + "/api/kb/ingest", params=H,
               json={"title": "半导体报告 M3 e2e", "content": SEMI_REPORT, "source_kind": "export"}, timeout=180)
    d = r.json()
    check("入库成功", r.status_code == 200 and d.get("ok"), str(d))

    # 3. 同内容去重（批注 11：重复导出不重复入库）
    r = s.post(BASE + "/api/kb/ingest", params=H,
               json={"title": "半导体报告 M3 e2e 重复", "content": SEMI_REPORT, "source_kind": "export"}, timeout=60)
    check("同内容去重", r.json().get("dup") is True)

    # 4. 语义检索（fast）
    r = s.post(BASE + "/api/kb/query", params=H, json={"question": "先进封装产能紧张，哪些环节受益？", "mode": "fast"}, timeout=120)
    hits = r.json().get("hits") or []
    check("语义检索命中", len(hits) > 0, str(hits)[:100])

    # 5. 上传限制 .md（批注 12）
    r = s.post(BASE + "/api/kb/upload", params=H, files={"file": ("a.txt", b"x", "text/plain")}, timeout=30)
    check("非 .md 拒绝", r.status_code == 400)

    # 6. 评估脏检测（批注 7/8）
    r = s.post(BASE + "/api/kb/evaluate", params=H,
               json={"title": "脏测试", "content": "✨奴婢给主人整理的呢～(≧∇≦)ﾉ\n| A | B |\n| --- | --- |\n| 1 | 2 |"}, timeout=120)
    d = r.json()
    check("脏内容评估", d.get("needs_clean") is True, str(d)[:100])

    # 7. AI 对话 query_kb（核心：主 agent 自主检索知识库）
    tid = s.post(BASE + "/api/chat/sessions", params=H, timeout=30).json()["thread_id"]
    r = s.post(BASE + "/api/chat", params={**H, "question": "我知识库里那篇半导体报告提到昇腾 910C 什么进展？",
                                           "thread_id": tid}, timeout=600)
    a = r.json().get("answer", "") if r.status_code == 200 else ""
    check("对话回答正常", r.status_code == 200 and len(a) > 10)
    check("回答引用知识库(910C)", "910C" in a or "良率" in a or "昇腾" in a, a[:150])
    s.delete(BASE + f"/api/chat/sessions/{tid}", params=H, timeout=30)

    # 7b. \u4e2d\u6587\u0075\u0073\u0065\u0072\u006e\u0061\u006d\u0065 入库回归（批注：chroma collection 名禁中文）
    # ⚠️ 必须用**全新**的中文账号：「默认无库」这条只在全新账号上成立。老账号（如「灵康科技有限公司」）
    #    可能早已被历史测试建过库——2026-09-16 实测它 09-06 遗留了一个空 collection（文档数 0 但
    #    exists=True），拿它当「新账号」用会让该断言假失败（不是代码回归，是测试设计脆弱）。
    cn_user = "中文测试公司_" + time.strftime("%m%d%H%M%S")
    cn_pwd = "cn123456"
    s.post(BASE + "/api/register", json={"username": cn_user, "password": cn_pwd, "role": "company"}, timeout=30)
    r = s.post(BASE + "/api/login", json={"username": cn_user, "password": cn_pwd, "role": "company"}, timeout=30)
    if r.status_code == 200:
        tok_cn = r.json()["token"]
        H_cn = {"token": tok_cn}
        # 默认无库（全新账号）
        check("\u4e2d\u6587 username \u9ed8\u8ba4\u65e0\u5e93", s.get(BASE + "/api/kb/status", params=H_cn, timeout=30).json().get("exists") is False)
        # 入库（不再报 InvalidArgumentError 500）
        rc = s.post(BASE + "/api/kb/ingest", params=H_cn,
                    json={"title": "中文测试", "content": "## 灵康科技\n中文入库测试内容。", "source_kind": "upload"}, timeout=180)
        check("\u4e2d\u6587 username \u5165\u5e93 200", rc.status_code == 200, str(rc.status_code))
        # 检索
        hits_cn = s.post(BASE + "/api/kb/query", params=H_cn,
                        json={"question": "灵康", "mode": "fast"}, timeout=120).json().get("hits") or []
        check("\u4e2d\u6587 username \u68c0\u7d22\u547d\u4e2d", len(hits_cn) > 0)
        # 清理：文档 + 临时账号（不留残留）
        for d in s.get(BASE + "/api/kb/status", params=H_cn, timeout=30).json().get("docs", []):
            s.delete(BASE + f"/api/kb/docs/{d['source_id']}", params=H_cn, timeout=30)
        try:
            from tools.schema_personal import get_personal_conn, purge_account
            _c = get_personal_conn()
            _row = _c.execute("SELECT id FROM accounts WHERE username=?", (cn_user,)).fetchone()
            _c.close()
            if _row:
                purge_account(_row["id"])
        except Exception as _e:  # noqa: BLE001
            print(f"  ⚠️ 临时中文账号清理失败：{_e}")

    # 8. digest 周报代码层必查（可选，慢）
    if args.with_digest:
        r = s.get(BASE + "/api/digest/subs", params=H, timeout=30)
        subs = [x for x in (r.json().get("items") or []) if x.get("enabled")]
        if subs:
            sid = subs[0]["id"]
            print(f"  [digest] 跑订阅 {sid}（周报全模式必查，约 1-3 分钟）…")
            r = s.post(BASE + "/api/digest/run", params=H,
                       json={"sub_ids": [sid]}, timeout=900)
            d = r.json()
            check("周报生成成功(含 KB 注入)", d.get("ok") is True, str(d)[:100])
        else:
            print("  [digest] 无启用订阅，跳过")

    # 9. 清理
    d = s.get(BASE + "/api/kb/status", params=H, timeout=30).json()
    for doc in (d.get("docs") or []):
        if "M3 e2e" in doc["title"] or "半导体" in doc["title"]:
            s.delete(BASE + f"/api/kb/docs/{doc['source_id']}", params=H, timeout=30)
    print("  测试数据已清理")

    print(f"\n== M3 端到端: ✅ {PASS} 通过, ❌ {FAIL} 失败 ==")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
