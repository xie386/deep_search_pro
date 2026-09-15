"""M2 请求装配管线化 · 端到端验证（真实服务 + 全局 Agent 链路）。

验证点：
  1. 记忆画像注入：MEMORY.md 写事实 → 问答引用（不经 agent 重建）
  2. 人格注入：SOUL 定义名字规则 → 问答按人格自报名
  3. 人格切换零重建：换激活人格（服务不重启）→ 新人格立即生效
  4. 无配置账号冒烟：行为不劣化

运行（前置：服务已启动）：
  .venv/Scripts/python.exe tests/m2_assembly_e2e_test.py --base http://127.0.0.1:8123
"""
import argparse
import os
import shutil
import sys
import time

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
sys.path = [p for p in sys.path if "hermes-agent" not in p and "hermes_agent" not in p]
os.environ.pop("PYTHONPATH", None)
os.environ.pop("HTTP_PROXY", None)
os.environ.pop("HTTPS_PROXY", None)

import requests

PASS = 0
FAIL = 0


def check(name: str, cond: bool, detail: str = ""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✅ {name}" + (f"  {detail}" if detail else ""))
    else:
        FAIL += 1
        print(f"  ❌ {name}" + (f"  {detail}" if detail else ""))


def chat(s, base, tok, tid, question, wait=3):
    """发问带基础重试（免费模型 429 时等待）。"""
    for attempt in range(3):
        r = s.post(base + "/api/chat", params={"token": tok, "question": question, "thread_id": tid}, timeout=600)
        if r.status_code == 200:
            return r.json().get("answer", "")
        if r.status_code == 429 or "429" in r.text:
            time.sleep(60)
            continue
        return ""
    return ""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--account", default="m2tester")
    ap.add_argument("--password", default="m2pass123")
    ap.add_argument("--role", default="company")
    ap.add_argument("--base", default="http://127.0.0.1:8123")
    args = ap.parse_args()

    s = requests.Session()
    s.trust_env = False
    BASE = args.base

    r = s.post(BASE + "/api/login", json={"username": args.account, "password": args.password, "role": args.role}, timeout=30)
    assert r.status_code == 200, f"登录失败: {r.text[:200]}"
    tok = r.json()["token"]
    print(f"== 登录: {args.account} ==")

    docs_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "agents_docs", args.account))
    backup = {}
    for f in ("SOUL.md", "MEMORY.md", "ACTIVE_SOUL"):
        p = os.path.join(docs_dir, f)
        if os.path.exists(p):
            backup[f] = open(p, encoding="utf-8").read()
    soul_dir = os.path.join(docs_dir, "SOUL")

    try:
        # ---------- 1. 记忆画像注入 ----------
        print("\n[1] 记忆画像注入（MEMORY.md 直接生效，无 agent 重建）")
        mem_p = os.path.join(docs_dir, "MEMORY.md")
        with open(mem_p, "a", encoding="utf-8") as f:
            f.write("\n- M2测试临时条目：用户生日是 8 月 30 日\n")
        tid = s.post(BASE + "/api/chat/sessions", params={"token": tok}, timeout=30).json()["thread_id"]
        a1 = chat(s, BASE, tok, tid, "我的生日是几月几号？只回答日期", wait=2)
        check("问答引用画像事实", "8月30日" in a1.replace(" ", "").replace("月30", "月30") or "8 月 30" in a1, a1[:60])
        s.delete(BASE + f"/api/chat/sessions/{tid}", params={"token": tok}, timeout=30)

        # ---------- 2. 人格注入 ----------
        print("\n[2] 人格注入（SOUL 定义身份规则）")
        p1 = "你是海盗船长杰克。当被问到「你叫什么名字」时，一律只回答：我是海盗船长杰克。"
        r = s.post(BASE + "/api/soul/create", params={"token": tok},
                   json={"name": "M2测试海盗", "content": p1}, timeout=30)
        check("创建人格 P1", r.status_code == 200, r.text[:80])
        r = s.post(BASE + "/api/soul/active", params={"token": tok}, json={"file": "SOUL/M2测试海盗.md"}, timeout=30)
        check("激活人格 P1", r.status_code == 200)
        tid = s.post(BASE + "/api/chat/sessions", params={"token": tok}, timeout=30).json()["thread_id"]
        a2 = chat(s, BASE, tok, tid, "你叫什么名字？", wait=2)
        check("问答按 P1 自报身份", "杰克" in a2, a2[:60])
        s.delete(BASE + f"/api/chat/sessions/{tid}", params={"token": tok}, timeout=30)

        # ---------- 3. 人格切换零重建（服务不重启） ----------
        print("\n[3] 切换人格 P2 → 立即生效（agent 零重建）")
        p2 = "你是管家阿尔弗雷德。当被问到「你叫什么名字」时，一律只回答：我是管家阿尔弗雷德。"
        r = s.post(BASE + "/api/soul/create", params={"token": tok},
                   json={"name": "M2测试管家", "content": p2}, timeout=30)
        check("创建人格 P2", r.status_code == 200)
        r = s.post(BASE + "/api/soul/active", params={"token": tok}, json={"file": "SOUL/M2测试管家.md"}, timeout=30)
        check("激活人格 P2（未重启服务）", r.status_code == 200)
        tid = s.post(BASE + "/api/chat/sessions", params={"token": tok}, timeout=30).json()["thread_id"]
        a3 = chat(s, BASE, tok, tid, "你叫什么名字？", wait=2)
        check("问答按 P2 自报身份（切换即生效）", "阿尔弗雷德" in a3, a3[:60])
        s.delete(BASE + f"/api/chat/sessions/{tid}", params={"token": tok}, timeout=30)

        # ---------- 4. 无配置账号冒烟 ----------
        print("\n[4] 无配置账号冒烟（无人格无记忆不劣化）")
        plain = args.account + "_plain"
        r = s.post(BASE + "/api/register", json={"username": plain, "password": "plain123456", "role": args.role}, timeout=30)
        if r.status_code not in (200, 409):
            check("注册无配置账号", False, r.text[:100])
        else:
            tok2 = s.post(BASE + "/api/login", json={"username": plain, "password": "plain123456", "role": args.role}, timeout=30).json()["token"]
            tid2 = s.post(BASE + "/api/chat/sessions", params={"token": tok2}, timeout=30).json()["thread_id"]
            a4 = chat(s, BASE, tok2, tid2, "你好，一句话自我介绍", wait=2)
            check("无配置账号正常问答", len(a4) > 5, a4[:60])
            s.delete(BASE + f"/api/chat/sessions/{tid2}", params={"token": tok2}, timeout=30)
    finally:
        # ---------- 清理：还原 SOUL/MEMORY/ACTIVE + 删测试人格 ----------
        for f, content in backup.items():
            with open(os.path.join(docs_dir, f), "w", encoding="utf-8") as fh:
                fh.write(content)
        for name in ("M2测试海盗.md", "M2测试管家.md"):
            p = os.path.join(soul_dir, name)
            if os.path.exists(p):
                os.remove(p)
        # 清掉记忆里的临时条目
        mem_p = os.path.join(docs_dir, "MEMORY.md")
        if os.path.exists(mem_p):
            lines = [l for l in open(mem_p, encoding="utf-8").read().split("\n") if "M2测试临时条目" not in l]
            open(mem_p, "w", encoding="utf-8").write("\n".join(lines))
        # 还原默认人格激活
        r2 = s.post(BASE + "/api/soul/active", params={"token": tok}, json={"file": "SOUL.md"}, timeout=30) if backup.get("ACTIVE_SOUL") == "SOUL.md" else None
        print("\n(测试数据已还原清理)")

    print(f"\n{'='*40}\nM2 端到端结果: ✅ {PASS} 通过, ❌ {FAIL} 失败")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
