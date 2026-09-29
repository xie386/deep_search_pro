"""M4 引擎独立验证：为 m2tester(company) 生成默认订阅 → 真实跑一次 digest → 检查产物。
用法：python tests/m4_engine_test.py（不需要服务运行；直接操作 SQLite + 真实 Tavily/LLM）
"""
import json, os, sys

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, _ROOT)
os.chdir(_ROOT)
sys.path = [p for p in sys.path if "hermes-agent" not in p]

from tools.schema_personal import ensure_tables, get_personal_conn
from services.digest_engine import build_batches, run_digest

ensure_tables()
conn = get_personal_conn()

# 1) 找 m2tester 账号 + 竞品
acc = conn.execute("SELECT id, username FROM accounts WHERE username='m2tester'").fetchone()
aid = acc[0]
print(f"[1] 账号: {acc[1]} (id={aid})")

# 用 me 库里的竞品（company_competitors）做关键词；没有则手工造两条
kws = [r[0] for r in conn.execute(
    "SELECT comp_name FROM company_competitors WHERE owner_id=? LIMIT 3", (aid,)).fetchall()]
if not kws:
    kws = ["至本洗面奶", "溪木源"]
print(f"    订阅关键词: {kws}")

# 2) 建订阅（存在则复用）
row = conn.execute("SELECT id FROM digest_subs WHERE owner_id=?", (aid,)).fetchone()
if row:
    sub_id = row[0]
    conn.execute("UPDATE digest_subs SET keywords=?, enabled=1 WHERE id=?",
                 (json.dumps(kws[:3], ensure_ascii=False), sub_id))
else:
    cur = conn.execute(
        "INSERT INTO digest_subs (owner_id, scope, name, keywords, schedule) VALUES (?,?,?,?,?)",
        (aid, "company", "竞品周报", json.dumps(kws[:3], ensure_ascii=False), "weekly"))
    sub_id = cur.lastrowid
conn.commit()
conn.close()
print(f"[2] 订阅 id={sub_id}")

# 3) 批次展开检查
from tools.schema_personal import get_personal_conn as gpc
c2 = gpc()
sub = {"keywords": json.dumps(kws[:3], ensure_ascii=False), "scope": "company"}
print("[3] 查询批次:", build_batches(sub))
c2.close()

# 4) 真实跑一次（Tavily+HN 检索 + LLM 筛选/写作，约1-3分钟）
print("[4] 开始执行 run_digest ...")
result = run_digest(sub_id, aid)
print("    结果:", {k: v for k, v in result.items() if k != "md_path"})
assert result.get("ok"), f"失败: {result.get('error')}"

# 5) 检查产物
md_path = result["md_path"]
content = open(md_path, encoding="utf-8").read()
print(f"\n[5] 报告已生成: {md_path}")
print(f"    长度 {len(content)} 字 | 精选 {result['item_count']} 条 | 扫描 {result['scanned']} 条候选")
print("--- 报告前 600 字 ---")
print(content[:600])
print("--- END ---")

# 6) digest_reports 已落库？
c3 = gpc()
r = c3.execute("SELECT title, item_count, status FROM digest_reports WHERE owner_id=? ORDER BY id DESC LIMIT 1",
               (aid,)).fetchone()
c3.close()
print(f"[6] 最新报告记录: {r}")
print("M4 ENGINE TEST DONE.")
