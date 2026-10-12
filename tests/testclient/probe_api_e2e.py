"""工具池测评 API 的**端到端冒烟**（v4.0 大点 03；不调模型、不跑真机、不留垃圾）。

跑法： cd 项目根 && unset PYTHONPATH && .venv/Scripts/python.exe tests/test_out/probe_api_smoke.py
覆盖：11 个接口的注册与**读路径**、以及三个**守卫**（无题集不许跑 / 无记录不许判分 / 打分表名防穿越）。
真机路径（run/judge/advise/commit 的实际执行）留到阶段 5「bug 修复 → 实际客户端测试」。
"""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
os.environ.pop("PYTHONPATH", None)

from fastapi.testclient import TestClient                    # noqa: E402

from api import server                                       # noqa: E402
from api.account import create_session                       # noqa: E402
from tools import probe_agent as pa                          # noqa: E402
from tools import probe_runner as pr                         # noqa: E402
from tools.schema_personal import get_personal_conn          # noqa: E402

USER = os.getenv("PROBE_SMOKE_USER", "尼古喵喵").strip()

ok, bad = [], []


def check(name, cond, extra=""):
    (ok if cond else bad).append(name)
    print("%s %s%s" % ("  ✅" if cond else "  ❌", name, (" | " + str(extra)) if extra else ""))


conn = get_personal_conn()
row = conn.execute("SELECT * FROM accounts WHERE username=?", (USER,)).fetchone()
conn.close()
if not row:
    print("✗ 账号不存在：%s" % USER)
    sys.exit(2)
acc = dict(row)
token = create_session(acc)
c = TestClient(server.app)
q = {"token": token}

# ★★ 2026-10-11 **事故修复**（用户真实数据被本冒烟覆盖）★★
#   本脚本原先**直接在真实账号上做写操作**（题集存取往返 + 往他沙箱里造原始记录）→ 后果两条：
#     ① 用户自己过目改过的 43 题题集被覆盖成「单元冒烟用的题（不是真题）」1 条 ✗✗
#     ② 造进去的假记录 + `_prune_raw(keep=1)` 把他那轮真机测评的原始记录顶掉了 ✗
#   现在：**所有写操作改到一台临时账号**上做（跑完连账号一起 purge ✓），
#   真实账号只跑**只读**路径，并在结尾用「只读指纹」复核它一模一样、没被动过 ✓
import time as _t                                                    # noqa: E402
from api.account import _create_account, _hash_pwd                   # noqa: E402
TMP_USER = "probe_e2e_%s" % _t.strftime("%Y%m%d_%H%M%S")
_create_account(TMP_USER, _hash_pwd("probe-e2e-pass"), "personal", None)
_conn = get_personal_conn()
_trow = _conn.execute("SELECT * FROM accounts WHERE username=?", (TMP_USER,)).fetchone()
_conn.close()
tmp_acc = dict(_trow)
tok2 = create_session(tmp_acc)
q2 = {"token": tok2}
tmp_sbox = os.path.join(pr.SANDBOX_DIR, TMP_USER)                    # 临时账号的沙箱（随便写 ✓）
_real_sbox = os.path.join(pr.SANDBOX_DIR, USER)


def _real_fingerprint():
    """真实账号的只读指纹：题集条数 + 沙箱里的原始记录名。跑完必须逐字不变 ✓"""
    cnt = len((pa.load_cases(USER) or {}).get("cases") or [])
    raws = sorted(f for f in (os.listdir(_real_sbox) if os.path.isdir(_real_sbox) else [])
                  if pr.RAW_RE.match(f))
    return cnt, raws


_real_before = _real_fingerprint()
chat_tid = None                            # 本冒烟造的会话（测完删掉 ✓）
try:
    # ---- 注册与读路径 ----
    paths = [r.path for r in server.app.routes if str(getattr(r, "path", "")).startswith("/api/probe")]
    need = ["/api/probe/overview", "/api/probe/sheets", "/api/probe/sheet", "/api/probe/cases",
            "/api/probe/gen_cases", "/api/probe/save_cases", "/api/probe/run", "/api/probe/status",
            "/api/probe/judge", "/api/probe/advise", "/api/probe/commit"]
    check("11 个接口已注册", all(p in paths for p in need),
          sorted(set(need) - set(paths)) or len(paths))

    r = c.get("/api/probe/overview", params=q)
    d = r.json()
    check("overview 200", r.status_code == 200, r.status_code)
    check("overview：池规模 > 0", d.get("pool_size", 0) > 0, d.get("pool_size"))
    check("overview：带池版本", isinstance(d.get("pool_version"), int), d.get("pool_version"))
    check("overview：条目含体检灯字段", all(k in (d.get("items") or [{}])[0]
                                            for k in ("ref", "name", "source", "warn")),
          list((d.get("items") or [{}])[0])[:6])
    # ★ 2026-10-10 抓到过一类 bug：接口层若「白名单投影」条目字段，新加的字段会被悄悄吃掉，
    #   前端于是拿不到 desc_state 而回退到旧判断（照样显示「描述 缺」）。这里把字段钉死 ✓
    _it = (d.get("items") or [{}])[0]
    check("overview：条目含描述三态字段 desc_state（user/auto/none）",
          _it.get("desc_state") in ("user", "auto", "none"), _it.get("desc_state"))
    check("overview：条目含关键词来源 kw_from",
          _it.get("kw_from") in ("column", "tool_desc", "source_desc", "derive", "entry"),
          _it.get("kw_from"))
    check("overview：条目含 hard/soft 问题计数", isinstance(_it.get("hard"), int)
          and isinstance(_it.get("soft"), int), "%s/%s" % (_it.get("hard"), _it.get("soft")))
    check("overview：顶层带体检汇总（problem_count/auto_only/derived_kw/no_desc）",
          all(k in d for k in ("problem_count", "auto_only", "derived_kw", "no_desc", "soft_count")),
          [k for k in ("problem_count", "auto_only", "derived_kw", "no_desc", "soft_count") if k not in d])
    check("overview：汇总口径与素材体检一致（与 health 同源）",
          d.get("problem_count") == (d.get("health") or {}).get("problem_count"),
          "%s vs %s" % (d.get("problem_count"), (d.get("health") or {}).get("problem_count")))
    check("overview：metrics 是对象", isinstance(d.get("metrics"), dict))
    check("overview：带当前测评模型名（不含 key）",
          isinstance(d.get("probe_model"), str) and "sk-" not in str(d.get("probe_model")),
          d.get("probe_model"))
    check("overview：running=False（当前没跑）", d.get("running") is False)
    check("overview：带单题上限 ask_timeout（秒；前端提示条显示，2026-10-11 加）",
          isinstance(d.get("ask_timeout"), int) and d["ask_timeout"] > 0, d.get("ask_timeout"))

    r = c.get("/api/probe/sheets", params=q)
    check("sheets 200 且是列表", r.status_code == 200 and isinstance(r.json().get("sheets"), list))
    r = c.get("/api/probe/cases", params=q)
    check("cases 200", r.status_code == 200 and "doc" in r.json())
    r = c.get("/api/probe/status", params=q)
    check("status 200（未在跑）", r.status_code == 200 and r.json().get("running") is False)
    # ★ 出题改成后台任务后，进度与结果都走 status.gen（前端轮询它）—— 空态也必须是**对象**，
    #   否则前端 `s.gen || {}` 虽能兜底，但契约就松了 ✓
    check("status：带 gen 子对象（出题进度通道）", isinstance(r.json().get("gen"), dict),
          r.json().get("gen"))

    # ---- 读单份打分表的路径防护（不存在的名字 / 非法名字）----
    r = c.get("/api/probe/sheet", params=dict(q, name="../secret.md"))
    check("sheet：非法名 → 400", r.status_code == 400, r.status_code)
    r = c.get("/api/probe/sheet", params=dict(q, name="probe_打分表20200101_000000.md"))
    check("sheet：不存在的合法名 → 404", r.status_code == 404, r.status_code)

    # ---- 守卫 1：没有题集不许跑（★ 用**临时账号** —— 真实账号的题集一根汗毛都不动 ✓）----
    r = c.post("/api/probe/run", params=q2)
    check("run：没有题集 → 400", r.status_code == 400 and "题集" in r.text, r.text[:80])

    # ---- 守卫 2：没有记录不许判分 ----
    #   ★ 临时账号天生没有记录 → 不需要再去挪真人账号的既有记录（旧写法就是在这里动了他的沙箱 ✗）
    os.makedirs(tmp_sbox, exist_ok=True)
    r = c.post("/api/probe/judge", params=q2, json={})
    check("judge：没有记录 → 400", r.status_code == 400, r.text[:80])

    # ---- 守卫 2 的另一面：**本轮记录在 → 判分必须能通** ----
    #   ★ 2026-10-11 用户真机 bug：跑完一轮点「判分」报「找不到这一轮的原始记录」——
    #     根因是水位线清理把本轮刚写的 `probe_run_*.json` 当新产物删了。这里造一份真记录钉死它 ✓
    #     （造在**临时账号**的沙箱里 ✓）
    raw = os.path.join(tmp_sbox, "probe_run_20260101_000000.json")
    with open(raw, "w", encoding="utf-8") as f:
        json.dump({"ts": "2026-01-01 00:00:00", "account_id": tmp_acc["id"], "username": TMP_USER,
                   "set_name": "probe_smoke", "pool_version": 1, "pool_size": 1, "count": 1,
                   "rows": [{"id": 1, "q": "冒烟题（不是真题）", "kind": "single",
                             "expect": {"kind": "tool", "ref": "mcp:pdd/search_goods"},
                             "calls": [{"brief": "mcp:pdd/search_goods → 3 条"}], "unmeasured": False}]},
                  f, ensure_ascii=False)
    _real_judge = pa.judge_run
    pa.judge_run = lambda run: {"scores": {1: {"verdict": "✓", "why": "命中", "conf": "高"}}, "raw": ""}
    try:
        r = c.post("/api/probe/judge", params=q2, json={"raw_path": raw})
        jd = r.json() if r.status_code == 200 else {}
        check("judge：本轮记录在 → 200 且回逐题行",
              r.status_code == 200 and (jd.get("rows") or [{}])[0].get("q") == "冒烟题（不是真题）",
              r.text[:90])
        check("judge：scores 的键是**字符串题号**（前端按 str 取）",
              "1" in (jd.get("scores") or {}), jd.get("scores"))
        r = c.post("/api/probe/judge", params=q, json={"raw_path": "../../../etc/passwd"})
        check("judge：越权/不存在的路径 → 400（防穿越）", r.status_code == 400, r.status_code)
    finally:
        pa.judge_run = _real_judge
        try:
            os.remove(raw)
        except OSError:
            pass

    # ---- 题集存取往返（用不合法/合法各一次）★ 全程在**临时账号**上 ✓ ----
    r = c.post("/api/probe/save_cases", params=q2, json={"doc": {"cases": []}})
    check("save_cases：空题集 → 400", r.status_code == 400, r.text[:60])
    doc = {"name": "probe_smoke", "account": TMP_USER, "cases": [
        {"id": 1, "kind": "single", "q": "单元冒烟用的题（不是真题）",
         "expect": {"kind": "tool", "ref": "mcp:pdd/search_goods"}, "chain": "x"}]}
    r = c.post("/api/probe/save_cases", params=q2, json={"doc": doc})
    check("save_cases：合法题集 → 200", r.status_code == 200, r.text[:80])
    r = c.get("/api/probe/cases", params=q2)
    check("cases：读回刚才那份", r.json().get("count") == 1, r.json().get("count"))

    # ---- ★ 2026-10-11 用户实测现场还原：`expect.ref="bili search"` 这种「CLI 名+子命令」写法 ----
    #   原来会被校验器（`ref 必须 api:/mcp: 开头`）拦死 → **题集根本存不进磁盘**。
    #   现在保存前先跑与出题同款的归一 → 应能存进去，并把改动条数回给前端 ✓
    class _E:                                    # 池条目的最小形状（ref/name）
        def __init__(self, ref, name):
            self.ref, self.name = ref, name

    _orig_card = pa.card_block
    pa.card_block = lambda aid: ("卡片", [_E("bili", "哔哩哔哩"), _E("mcp:pdd/search_goods", "拼多多查商品")])
    try:
        bad_doc = {"name": "probe_badref", "account": TMP_USER, "cases": [
            {"id": 1, "kind": "single", "q": "单元冒烟：我 B 站关注了谁",
             "expect": {"kind": "tool", "ref": "bili feed"}},
            {"id": 2, "kind": "single", "q": "单元冒烟：B站热搜",
             "expect": {"kind": "tool", "ref": "cli:bili hot"}},
            {"id": 3, "kind": "single", "q": "单元冒烟：查商品",
             "expect": {"kind": "tool", "ref": "mcp:pdd/search_goods"}},
        ]}
        r = c.post("/api/probe/save_cases", params=q2, json={"doc": bad_doc})
        sd = r.json() if r.status_code == 200 else {}
        check("save_cases：`ref='bili feed'` 这类写法 → **200**（保存前自动归一，不再拦死 ✓）",
              r.status_code == 200 and sd.get("normalized", 0) >= 2, (r.status_code, r.text[:90]))
        r2 = c.get("/api/probe/cases", params=q2)
        _cs = (r2.json().get("doc") or {}).get("cases") or []
        check("save_cases：落盘的是**规范形态**（cli/bili + argv）✓",
              _cs and _cs[0].get("expect") == {"kind": "cli", "bin": "bili", "argv": ["feed"]}
              and _cs[1].get("expect") == {"kind": "cli", "bin": "bili", "argv": ["hot"]},
              _cs[:2])
        check("save_cases：mcp 那条原样不动 ✓",
              len(_cs) > 2 and _cs[2].get("expect") == {"kind": "tool", "ref": "mcp:pdd/search_goods"},
              _cs[2] if len(_cs) > 2 else None)
    finally:
        pa.card_block = _orig_card

    # ---- v4.0 大点 03：**测评会话不进会话库**（用户 2026-10-11 裁定）----
    #   测评每条题都真建一个会话（要读它的工具调用序列），但那不是用户的正常会话：
    #   `kind=probe` 的会话不出现在会话列表里，且跑完由测评侧全清 ✓
    r = c.post("/api/chat/sessions", params=dict(q2, kind="probe"))
    pk = r.json() if r.status_code == 200 else {}
    check("sessions：kind=probe 建会话 → 200 且回 kind", r.status_code == 200 and pk.get("kind") == "probe",
          r.text[:80])
    probe_tid = pk.get("thread_id")
    r = c.post("/api/chat/sessions", params=dict(q2, kind="bogus"))
    check("sessions：非法 kind → 400（不给能进库又会漏删的中间态）", r.status_code == 400, r.status_code)
    r = c.post("/api/chat/sessions", params=q2)                      # 默认 chat
    ck = r.json() if r.status_code == 200 else {}
    chat_tid = ck.get("thread_id")
    lst = c.get("/api/chat/sessions", params=q2).json().get("items") or []
    tids = [s["thread_id"] for s in lst]
    check("会话列表：测评会话**不出现**、正常会话在 ✓",
          bool(probe_tid) and probe_tid not in tids and chat_tid in tids, len(tids))
    check("会话列表：条目带 kind 字段", all("kind" in s for s in lst), (lst[0] if lst else None))

    # ---- 未登录一律拒绝 ----
    # ★ 口径：**缺** token → FastAPI 参数校验层 422（项目所有接口都这样）；
    #   token **无效** → 401。这里两种都验一下，别把 422 误当成漏了鉴权。
    r = c.get("/api/probe/overview")
    check("缺 token → 422（参数校验层）", r.status_code == 422, r.status_code)
    r = c.get("/api/probe/overview", params={"token": "无效token"})
    check("无效 token → 401", r.status_code == 401, r.status_code)

    # ---- explore / judge 的入参校验不触发 LLM（空 body 也要能到守卫层）----
    r = c.post("/api/probe/advise", params=q2, json={})
    check("advise：没有记录 → 400（未调模型）", r.status_code == 400, r.text[:60])

    # ---- ★ 真实账号**零改动**复核（本冒烟只读它，写完的东西全在临时账号上）----
    _real_after = _real_fingerprint()
    check("真实账号零改动：题集条数 + 沙箱原始记录 与开跑前**逐字一致** ✓",
          _real_after == _real_before, "前 %s → 后 %s" % (_real_before, _real_after))
finally:
    from tools.schema_personal import purge_account            # 临时账号连数据一起清掉 ✓
    from agent import conversation_store as _cs
    try:
        _cs.purge_probe_sessions(tmp_acc["id"])
        if chat_tid:
            _cs.delete_session(tmp_acc["id"], chat_tid)
    except Exception:  # noqa: BLE001
        pass
    conn = get_personal_conn()
    conn.execute("DELETE FROM sessions WHERE token IN (?, ?)", (token, tok2))
    conn.commit()
    conn.close()
    try:
        purge_account(tmp_acc["id"])
    except Exception as _e:  # noqa: BLE001
        print("  （临时账号清理跳过：%s）" % _e)
    try:                                                       # 只清临时账号的沙箱目录
        import shutil
        shutil.rmtree(tmp_sbox, ignore_errors=True)
    except Exception:  # noqa: BLE001
        pass
    try:                                                       # 临时账号的题集文件也要删（purge_account 不管它 ✗）
        _tmp_set = os.path.join(ROOT, "data", "probe_sets", "%s.json" % TMP_USER)
        if os.path.isfile(_tmp_set):
            os.remove(_tmp_set)
    except Exception:  # noqa: BLE001
        pass

print("\n" + "=" * 62)
print("通过 %d ／ 失败 %d" % (len(ok), len(bad)))
for n in bad:
    print("  -", n)
sys.exit(1 if bad else 0)
