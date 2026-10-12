# -*- coding: utf-8 -*-
"""`/api/digest/auto`（账号级「每天自动生成周报」开关）端到端冒烟。

跑法： cd 项目根 && unset PYTHONPATH && .venv/Scripts/python.exe tests/testclient/digest_auto_e2e.py

★ 安全：**不碰真实 personal.db** —— 用 SQLite backup API 把库热备到临时目录，再把
  `PERSONAL_DB_PATH` 指过去（必须在 import api.server **之前**设置，`schema_personal.DB_PATH`
  是导入期读的）。测试对真实库**只读**，并复核它没被改过 ✓
覆盖：默认关 → 打开 → 读回 → 关掉（写后读回验证）+ 真实库零改动。
"""
import os
import sqlite3
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
os.environ.pop("PYTHONPATH", None)

REAL_DB = os.path.join(ROOT, "data", "personal.db")
TMP = tempfile.mkdtemp(prefix="digest_auto_e2e_")
COPY = os.path.join(TMP, "personal_copy.db")

ok, bad = [], []


def check(name, cond, extra=""):
    (ok if cond else bad).append(name)
    print("%s %s%s" % ("  ✅" if cond else "  ❌", name, (" | " + str(extra)) if extra else ""))


if not os.path.exists(REAL_DB):
    print("✗ 找不到 %s" % REAL_DB)
    sys.exit(2)

# 1) 热备：把真实库完整拷一份（含 WAL 里未落盘的提交）
src = sqlite3.connect(REAL_DB)
dst = sqlite3.connect(COPY)
try:
    src.backup(dst)
finally:
    dst.close()
    src.close()
os.environ["PERSONAL_DB_PATH"] = COPY          # ★ 必须在 import api.server 之前

import api.account as acct                     # noqa: E402
from fastapi.testclient import TestClient      # noqa: E402

import api.server as server                    # noqa: E402
import tools.schema_personal as sp             # noqa: E402

check("隔离到位：实际用的是临时库副本（不是真实 personal.db）",
      os.path.abspath(sp.DB_PATH) == os.path.abspath(COPY), sp.DB_PATH)

# 2) 真实库的开关值（只读，用于最后复核没被动过）
def _real_auto(username):
    c = sqlite3.connect(REAL_DB)
    try:
        r = c.execute("SELECT COALESCE(digest_auto, 0) FROM accounts WHERE username=?",
                      (username,)).fetchone()
        return None if r is None else int(r[0])
    except sqlite3.OperationalError:            # 真实库还没迁移这列也算「没被动」
        return "no_column"
    finally:
        c.close()


conn = sp.get_personal_conn()
row = conn.execute("SELECT * FROM accounts WHERE username IS NOT NULL AND username<>'' "
                   "ORDER BY id LIMIT 1").fetchone()
conn.close()
if not row:
    print("✗ 副本里没有可用账号")
    sys.exit(2)
username = row["username"]
before_real = _real_auto(username)
token = acct.create_session(dict(row))
c = TestClient(server.app)
qs = {"token": token}

# 3) 默认关 → 打开 → 读回 → 关掉
st, d = c.get("/api/digest/auto", params=qs), None
check("GET /api/digest/auto 可用（默认读取）", st.status_code == 200, st.status_code)
check("默认**关**（未设置过的账号 auto=false ✓ 用户要求的默认值）",
      st.json().get("auto") is False, st.json())

r = c.post("/api/digest/auto", params=qs, json={"auto": True})
check("POST 打开 → 返回 auto=true", r.status_code == 200 and r.json().get("auto") is True,
      (r.status_code, r.json()))

back = c.get("/api/digest/auto", params=qs).json().get("auto")
check("写后读回：GET 立刻是 true（不是只信返回值 ✓）", back is True, back)

_dbval = sp.get_personal_conn().execute(
    "SELECT COALESCE(digest_auto, 0) FROM accounts WHERE id=?", (row["id"],)).fetchone()[0]
check("落库正确：副本 accounts.digest_auto = 1", int(_dbval) == 1, _dbval)

r = c.post("/api/digest/auto", params=qs, json={"auto": False})
check("POST 关闭 → false，且读回 false",
      r.status_code == 200 and r.json().get("auto") is False
      and c.get("/api/digest/auto", params=qs).json().get("auto") is False, r.json())

r = c.post("/api/digest/auto", params=qs, json={})       # 缺字段 = 关（安全默认）
check("异常入参：缺 auto 字段 → 按「关」处理（不报错、不放行 ✓）",
      r.status_code == 200 and r.json().get("auto") is False, (r.status_code, r.json()))

# 4) 调度器确实按账号开关过滤（本文件用副本库，安全）
_c = sp.get_personal_conn()
try:
    _c.execute("UPDATE digest_subs SET enabled=1 WHERE owner_id=?", (row["id"],))
    if not _c.execute("SELECT COUNT(*) FROM digest_subs WHERE owner_id=?",
                      (row["id"],)).fetchone()[0]:
        _c.execute("INSERT INTO digest_subs (owner_id, scope, name, keywords, schedule, lang, enabled) "
                   "VALUES (?, 'personal', 'e2e探针订阅', '[\"测试\"]', 'daily', 'zh', 1)", (row["id"],))
    _c.commit()
finally:
    _c.close()
import api.digest_scheduler as ds              # noqa: E402
_picked = []
_ds_mod = sys.modules.get("agent.digest_engine")
_fake = type(sys)("agent.digest_engine")
_fake.run_digest = lambda sid, aid: (_picked.append(aid) or {"ok": True, "item_count": 0, "md_path": ""})
sys.modules["agent.digest_engine"] = _fake
try:
    ds.run_all_due(None)                        # 此刻开关是「关」
    check("调度器：开关关着 → 该账号**不**自动生成 ✓", row["id"] not in _picked, _picked)
    c.post("/api/digest/auto", params=qs, json={"auto": True})
    _picked.clear()
    ds.run_all_due(None)                        # 打开后
    check("调度器：开关打开 → 该账号被自动生成（订阅已启用 ✓）", row["id"] in _picked, _picked)
finally:
    if _ds_mod is not None:
        sys.modules["agent.digest_engine"] = _ds_mod
    else:
        sys.modules.pop("agent.digest_engine", None)

# 5) 复核：真实库一个字节都没被这个测试改过
after_real = _real_auto(username)
check("真实库未被改动（只读复核 ✓ 用户数据零风险）", before_real == after_real,
      (before_real, after_real))

print("\n" + "=" * 62)
print("通过 %d ／ 失败 %d" % (len(ok), len(bad)))
for n in bad:
    print("  -", n)
print("临时副本：%s（可删）" % TMP)
assert not bad, "digest_auto e2e 失败 %d 项：%s" % (len(bad), "；".join(bad))
