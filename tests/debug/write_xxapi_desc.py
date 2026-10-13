# -*- coding: utf-8 -*-
"""一次性脚本（不入库）：给 `api:xxapi#douyinHot` / `api:xxapi#ipQuery` 补**工具级描述**。

为什么要补（用户 2026-10-10 交办，原话「API 工具的缺乏描述确实是我忘记写了，你帮我补一下」）：
  这两条只有 8/7 字的**名字复读式自动摘要**，而它们的**关键词**只能落到「来源级描述」——
  那条来源（小小API(抖音热搜榜)）写的是抖音热榜词，于是 `ipQuery`（查 IP 归属地）也被灌进了
  「抖音热榜/抖音热搜…」的短语，属于实打实的路由隐患（问 IP 归属地会往抖音靠）。

做法：**走产品接口** `POST /api/tools/sources/cap_abilities`（不直接改库）——它会
  ① 写 `abilities_user`（手写优先，自动摘要刷新不会冲掉）
  ② `_bump(account)` 池版本 +1
  ③ `capability_pool.ensure_vectors_fresh(account)` 让向量文本重算
描述第一行写「关键词：…」→ 池子取词的第 2 顺位命中（列空时），两条工具从此各拿各的词 ✓

纪律：先备份整库到 `docs/v4.0/backups/`（该目录已 gitignore），并记录两行的前后快照。
跑法：`.venv/Scripts/python.exe tests/test_out/write_xxapi_desc.py`
"""
import json
import os
import shutil
import sys
import time

import httpx

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
os.environ.pop("PYTHONPATH", None)

from api.account import create_session                    # noqa: E402
from tools.schema_personal import get_personal_conn       # noqa: E402

BASE = os.environ.get("APP_URL", "http://localhost:8123")
USER = os.environ.get("PROBE_USER", "尼古喵喵").strip()
SLUG, SRC = "xxapi", "api"

DESCS = {
    "api:xxapi#douyinHot": (
        "关键词：抖音热榜/抖音热搜/今天什么最火/实时热点榜单/抖音上最火的视频\n\n"
        "**想看此刻抖音上最火什么**时用它：取抖音实时热榜，返回热门视频与话题的排名列表。"
        "问到「抖音热搜、现在什么最火、实时热点榜」用它；只想查一个 IP 在哪儿走「查询IP归属地」。"
    ),
    "api:xxapi#ipQuery": (
        "关键词：IP归属地/查IP/这个IP是哪儿的/IP定位/运营商查询\n\n"
        "**手上只有一个 IP、想知道它在哪**时用它：查该 IP 的归属地（国家、省市、运营商）。"
        "问到「这个 IP 是哪儿的、IP 定位、归属地查询」用它；想看抖音热榜走「获取抖音实时热榜」。"
    ),
}


def call(method, path, token=None, body=None):
    qs = {"token": token} if token else {}
    url = BASE + path
    try:
        r = httpx.request(method, url, params=qs, json=body, timeout=60,
                          trust_env=False)                 # 本地直连，不走代理
        try:
            return r.status_code, r.json()
        except Exception:                                    # noqa: BLE001
            return r.status_code, r.text[:400]
    except Exception as e:                                   # noqa: BLE001
        return 0, "%s: %s" % (type(e).__name__, e)


ok, bad = [], []


def check(name, cond, extra=""):
    (ok if cond else bad).append(name)
    print("%s %s%s" % ("  ✅" if cond else "  ❌", name, (" | " + str(extra)) if extra else ""))


# ---------------- 0. 备份 ----------------
ts = time.strftime("%Y%m%d_%H%M%S")
bdir = os.path.join(ROOT, "docs", "v4.0", "backups")
os.makedirs(bdir, exist_ok=True)
src_db = os.path.join(ROOT, "data", "personal.db")
dst_db = os.path.join(bdir, "personal_before_xxapi_desc_%s.db" % ts)
shutil.copy2(src_db, dst_db)

conn = get_personal_conn()
acc = dict(conn.execute("SELECT * FROM accounts WHERE username=?", (USER,)).fetchone())
aid = int(acc["id"])
before = {}
for ref in DESCS:
    r = conn.execute("SELECT ref, name, COALESCE(abilities_user,'') AS au, COALESCE(abilities,'') AS ab, "
                     "COALESCE(keywords,'') AS kw FROM tool_capabilities WHERE account_id=? AND ref=?",
                     (aid, ref)).fetchone()
    before[r["ref"]] = {"name": r["name"], "abilities_user": r["au"], "abilities": r["ab"],
                        "keywords": r["kw"]}
conn.close()
check("备份已落盘（改数据纪律）", os.path.exists(dst_db), os.path.basename(dst_db))
check("两条工具改动前都没有手写描述", all(not v["abilities_user"] for v in before.values()),
      {k: len(v["abilities_user"]) for k, v in before.items()})

# ---------------- 1. 写 ----------------
token = create_session(acc)
code, res = call("POST", "/api/tools/sources/cap_abilities", token=token,
                 body={"source": SRC, "slug": SLUG,
                       "items": [{"ref": k, "text": v} for k, v in DESCS.items()]})
check("接口返回 200", code == 200, code)
check("两条都被 saved（没有 skipped）",
      isinstance(res, dict) and len(res.get("saved") or []) == 2 and not (res.get("skipped") or []),
      json.dumps(res.get("skipped"), ensure_ascii=False) if isinstance(res, dict) else res)

# ---------------- 2. 读回验证（走服务的读路径 + 口径层） ----------------
code, ov = call("GET", "/api/probe/overview", token=token)
items = {i["ref"]: i for i in (ov.get("items") or [])} if isinstance(ov, dict) else {}
for ref in DESCS:
    it = items.get(ref) or {}
    check("%s → 描述变「手写」" % ref, it.get("desc_state") == "user", it.get("desc_state"))
    check("%s → 关键词改从工具描述取（不再落到来源级）" % ref, it.get("kw_from") == "tool_desc",
          it.get("kw_from"))
    check("%s → 取到的词是自己的领域词" % ref,
          ("IP" in (it.get("keywords") or "")) if "ipQuery" in ref else ("抖音" in (it.get("keywords") or "")),
          (it.get("keywords") or "")[:40])

from tools import capability_lint as lint                        # noqa: E402
h = lint.health(aid)
check("素材体检：全池仍 0 条硬问题（没写坏）", h["problem_count"] == 0,
      "hard=%s codes=%s" % (h["hard_findings"], h["codes"]))
check("体检表汇总口径一致", ov.get("problem_count") == h["problem_count"],
      "%s vs %s" % (ov.get("problem_count"), h["problem_count"]))

# 记录前后快照（供实现记录引用）
after = {ref: {"desc_state": items.get(ref, {}).get("desc_state"),
               "kw_from": items.get(ref, {}).get("kw_from"),
               "keywords": items.get(ref, {}).get("keywords")} for ref in DESCS}
print("\n改动前：", json.dumps(before, ensure_ascii=False)[:300])
print("改动后：", json.dumps(after, ensure_ascii=False))

print("\n" + "=" * 62)
print("通过 %d ／ 失败 %d" % (len(ok), len(bad)))
for n in bad:
    print("  -", n)
sys.exit(1 if bad else 0)
