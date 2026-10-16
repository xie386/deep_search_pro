"""v4.0 大点 03：probe_runner 的**离线自测**（不调模型、不联网、不改账号数据 ✓）。

跑法：`pytest tests/unit/test_probe_runner.py -q`（也随 `pytest tests/unit` 一起跑 ✓）。
覆盖：题集读入/校验/选题、调用序列解析（口径与探针一致）、期望文案、三段式打分表与机器可读块、
      版本列表与路径防护、账号级指标折算、`probe_metrics` 的 UPSERT（**临时库上验，不碰真库**）。
"""
import json
import os
import sqlite3
import sys
import tempfile
import shutil
import time

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
os.environ.pop("PYTHONPATH", None)

from tools import probe_runner as pr  # noqa: E402
from tools import capability_pool as cp  # noqa: E402
from tools import capability_lint as lint  # noqa: E402

ok = []
bad = []


def check(name, cond, extra=""):
    (ok if cond else bad).append(name)
    print("%s %s%s" % ("  ✅" if cond else "  ❌", name, (" | " + str(extra)) if extra else ""))


def test_probe_runner_offline():
    # ---------------- 1. 题集：读入 / 校验 / 选题 ----------------
    doc = pr.load_case_set("m5b_v2")
    check("题集可读（m5b_v2）", doc.get("name") == "m5b_v2", doc.get("_path"))
    cases = doc["cases"]
    check("43 题 / 编号 1..43", [c["id"] for c in cases] == list(range(1, 44)))
    check("核心集 16 条", sum(1 for c in cases if c["core"]) == 16)
    check("kind 口径 single/multi/noise",
          {c["kind"] for c in cases} == {"single", "multi", "noise"},
          sorted({c["kind"] for c in cases}))
    check("cases_for(core) 只留核心", all(c["core"] for c in pr.cases_for(doc, "core")))
    check("cases_for(ids) 定向复跑", [c["id"] for c in pr.cases_for(doc, ids=[1, 5])] == [1, 5])

    # 校验器：坏题集必须被拦下
    bad_docs = {
        "重复 id": {"cases": [{"id": 1, "q": "aaaa", "expect": {"kind": "tool", "ref": "mcp:x/y"}},
                              {"id": 1, "q": "bbbb", "expect": {"kind": "tool", "ref": "mcp:x/y"}}]},
        "noise 带期望": {"cases": [{"id": 1, "q": "aaaa", "kind": "noise",
                                    "expect": {"kind": "tool", "ref": "mcp:x/y"}}]},
        "单工具题缺 expect": {"cases": [{"id": 1, "q": "aaaa"}]},
        "ref 前缀非法": {"cases": [{"id": 1, "q": "aaaa", "expect": {"kind": "tool", "ref": "steam#x"}}]},
        "kind 非法": {"cases": [{"id": 1, "q": "aaaa", "kind": "x", "expect": None}]},
        "题面重复": {"cases": [{"id": 1, "q": "aaaa", "expect": {"kind": "tool", "ref": "mcp:x/y"}},
                               {"id": 2, "q": "aaaa", "expect": {"kind": "tool", "ref": "mcp:x/y"}}]},
    }
    for label, d in bad_docs.items():
        check("校验器拦下：%s" % label, bool(pr.validate_case_set(d)))
    tmpbad = os.path.join(tempfile.gettempdir(), "probe_bad.json")
    with open(tmpbad, "w", encoding="utf-8") as f:
        json.dump(bad_docs["重复 id"], f)
    try:
        pr.load_case_set(tmpbad)
        check("load_case_set 对坏题集抛错", False)
    except ValueError:
        check("load_case_set 对坏题集抛错", True)

    # ---------------- 2. 调用序列解析（口径与探针一致）----------------
    seq = json.dumps([
        {"name": "invoke_tool", "args": {"ref": "mcp:12306-mcp/get-current-date", "params": {}}},
        {"name": "invoke_tool", "args": {"ref": "mcp:12306-mcp/get-station-code-of-citys",
                                         "params": {"citys": "成都|重庆"}}},
        {"name": "invoke_tool", "args": {"ref": "mcp:12306-mcp/get-tickets",
                                         "params": {"date": "2026-10-03"}}},
    ])
    calls = pr.parse_calls(seq)
    check("顺序保留", [c["target"] for c in calls] ==
          ["mcp:12306-mcp/get-current-date", "mcp:12306-mcp/get-station-code-of-citys",
           "mcp:12306-mcp/get-tickets"])
    check("MCP 来源按 / 切", calls[1]["source"] == "mcp:12306-mcp")
    check("参数摘要带上", "成都|重庆" in calls[1]["brief"])
    check("坏 json / null / 空串 → 空", pr.parse_calls("{坏 json") == [] and
          pr.parse_calls("null") == [] and pr.parse_calls("") == [])
    cli_calls = pr.parse_calls(json.dumps([{"name": "run_shell_command",
                                           "args": {"command": "wechat-cli", "argv": ["history", "袁战"]}}]))
    check("CLI brief 正确", cli_calls[0]["brief"] == "wechat-cli history 袁战", cli_calls[0])
    check("CLI 来源正确", cli_calls[0]["source"] == "cli:wechat-cli")
    other = pr.parse_calls(json.dumps([{"name": "网络搜索工具", "args": {"q": "x"}}]))
    check("内置工具归 builtin:", other[0]["source"] == "builtin:网络搜索工具")
    check("source_of api", pr.source_of("api:steam#searchGame") == "api:steam")
    check("source_of mcp", pr.source_of("mcp:12306-mcp/get-tickets") == "mcp:12306-mcp")
    check("换行被转义（评分表不断行）",
          "\n" not in pr.parse_calls(json.dumps([{"name": "invoke_tool",
                                                  "args": {"ref": "mcp:x/y", "params": {"a": "l1\nl2"}}}]))[0]["brief"])

    # ---------------- 3. 期望文案 ----------------
    check("期望：单工具", pr.expected_brief({"kind": "tool", "ref": "mcp:pdd/search_goods"}) == "mcp:pdd/search_goods")
    check("期望：CLI + 可接受替代",
          "可接受" in pr.expected_brief({"kind": "cli", "bin": "bili", "argv": ["hot"], "alts": [["rank"]]}))
    check("期望：噪声题", pr.expected_brief(None) == "（不调用工具）")

    # ---------------- 4. 打分表：三段式 + 机器可读块 ----------------
    fake_run = {
        "ts": "2026-10-10 02:00:00", "account_id": 3, "username": "尼古喵喵", "set_name": "m5b_v2",
        "pool_version": 301, "pool_size": 2, "count": 2, "elapsed_s": 12.5, "ext_retrievals": 4,
        "pool": {"items": [
            {"ref": "mcp:pdd/search_goods", "name": "搜商品", "source": "mcp:pdd",
             "has_keywords": True, "has_desc": True, "keywords": "拼多多 搜商品", "abilities_user": "找货", "warn": []},
            {"ref": "mcp:pdd/explore_deals", "name": "逛榜单", "source": "mcp:pdd",
             "has_keywords": False, "has_desc": True, "keywords": "", "abilities_user": "看榜单", "warn": ["缺关键词"]},
        ]},
        "rows": [
            {"id": 1, "kind": "single", "core": True, "q": "拼多多上搜一下 iPhone 17 的价格",
             "expect": {"kind": "tool", "ref": "mcp:pdd/search_goods"}, "calls": calls, "unmeasured": False, "dur_s": 6.0},
            {"id": 42, "kind": "noise", "core": True, "q": "你是谁", "expect": None,
             "calls": [], "unmeasured": False, "dur_s": 3.0},
        ],
    }
    scores = {1: {"verdict": "✓", "note": "链路对"}, 42: {"verdict": "✓", "note": "恰好没调工具"}}
    m = pr.build_metrics(fake_run, scores)
    check("指标：池版本带入", m["pool_version"] == 301)
    check("指标：单工具 1/1", (m["single_hit"], m["single_total"]) == (1, 1))
    check("指标：噪声越线 0", (m["noise_over"], m["noise_total"]) == (0, 1))
    check("指标：判分分布 ✓2", (m["verdict_ok"], m["verdict_partial"], m["verdict_bad"]) == (2, 0, 0))
    check("指标：平均外部检索 = 4/2", m["avg_ext_retrievals"] == 2.0)

    m2 = pr.build_metrics(fake_run, {})   # 没填判定 → 全算未测
    check("指标：未填判定计入未测", m2["verdict_unknown"] == 2 and m2["single_hit"] == 0)

    m3 = pr.build_metrics({**fake_run, "rows": [dict(fake_run["rows"][1], calls=calls)]}, {})
    check("指标：噪声越线计数", m3["noise_over"] == 1)

    text = pr.build_sheet(fake_run, scores=scores, metrics=m, advisor="- 给 search_goods 补 keywords")
    check("打分表：三段齐", all(s in text for s in ("## 一、本版本的工具", "## 二、本版本的工具描述词法",
                                                  "## 三、最终数据", "## 四、词法改进建议")))
    check("打分表：含池版本与题集", "池版本 `301`" in text and "m5b_v2" in text)
    check("打分表：含额度 tip", "额度" in text and "外部检索 4 次" in text)
    js = text.split("```json")[1].split("```")[0]
    payload = json.loads(js)
    check("打分表：机器可读块可解析", payload["metrics"]["pool_version"] == 301 and
          str(payload["scores"]["1"]["verdict"]) == "✓" and payload["rows"][0]["calls"])

    # ---------------- 5. 版本列表 / 读回 / 命名与路径防护 ----------------
    scratch = "_probe_selftest_user"
    sdir = pr.sheet_dir(scratch)
    try:
        before = {s["name"] for s in pr.list_sheets(scratch)}
        name = "probe_打分表20261005_221918.md"
        p = pr.write_sheet(scratch, text, name)
        check("打分表落 output/{用户名}/", os.path.isfile(p) and os.path.dirname(p) == sdir, p)
        # 干扰文件：不该进列表
        with open(os.path.join(sdir, "普通报告.md"), "w", encoding="utf-8") as f:
            f.write("x")
        with open(os.path.join(sdir, "probe_打分表_格式不对.md"), "w", encoding="utf-8") as f:
            f.write("x")
        names = {s["name"] for s in pr.list_sheets(scratch)}
        check("列表：只认严格格式", names - before == {name}, sorted(names - before))
        check("读回一致", pr.read_sheet(scratch, name) == text)
        for evil in ("../secret.md", "probe_打分表20261005_221918.md/../../x", "a.md"):
            try:
                pr.sheet_path(scratch, evil)
                check("路径防护拦下 %s" % evil, False)
            except ValueError:
                check("路径防护拦下 %s" % evil, True)
        check("文件名生成格式", pr.SHEET_RE.match(pr.new_sheet_name()) is not None, pr.new_sheet_name())
        check("打分表不进清理水位线", not any(pr.SHEET_RE.match(f) for f in pr.snapshot_files(scratch)))
    finally:
        shutil.rmtree(sdir, ignore_errors=True)

    # ---------------- 6. 指标 UPSERT（临时库上验，不碰账号数据）----------------
    tmpdb = os.path.join(tempfile.gettempdir(), "probe_metrics_test.db")
    if os.path.exists(tmpdb):
        os.remove(tmpdb)


    def _new_conn():
        c = sqlite3.connect(tmpdb)
        c.row_factory = sqlite3.Row
        return c


    conn = _new_conn()
    ddl = open(os.path.join(ROOT, "tools", "schema_personal.py"), encoding="utf-8").read()
    ddl = ddl[ddl.index("CREATE TABLE IF NOT EXISTS probe_metrics"):]
    ddl = ddl[:ddl.index(");") + 2]
    conn.execute(ddl)
    conn.commit()
    # ★ 单测**必须还原**这两个全局桩（否则会污染同进程里后跑的其它用例 —— 实测：
    #   与本文件同进程、后跑 test_probe_agent.py 时，残留的临时库连接会让它 sqlite3.OperationalError ✗）
    import tools.schema_personal as _sp
    _real_conn, _real_ensure = pr._conn, _sp.ensure_tables
    _sp.ensure_tables = lambda: None          # 单测**不迁移真库** ✓
    pr._conn = lambda: _new_conn()            # 每次开新连接（commit_metrics 会关它 ✓），不改真库
    pr_m = dict(m, sheet_name="probe_打分表20261005_221918.md")
    r1 = pr.commit_metrics(1, pr_m)
    check("UPSERT 写入成功", r1.get("sheet_name") == "probe_打分表20261005_221918.md", r1.get("sheet_name"))
    pr_m2 = dict(pr_m, single_hit=0, verdict_ok=1)
    pr.commit_metrics(1, pr_m2)
    chk = _new_conn()
    n = chk.execute("SELECT COUNT(*) FROM probe_metrics").fetchone()[0]
    chk.close()
    row = pr.get_metrics(1)
    check("每账号只留一行（重复提交只更新）", n == 1 and row["single_hit"] == 0 and row["verdict_ok"] == 1,
          "rows=%s single_hit=%s verdict_ok=%s" % (n, row["single_hit"], row["verdict_ok"]))
    pr._conn, _sp.ensure_tables = _real_conn, _real_ensure      # ★ 还原全局桩（防污染后续用例）
    try:
        os.remove(tmpdb)
    except OSError:
        pass

    # ---------------- 8. 体检口径：**必须与 capability_lint 完全一致** ----------------
    # ★ 2026-10-10 用户实测踩到：我曾按「abilities_user 是否为空」判「有无描述」，把 4 个 CLI
    #   （描述按约定存在 `abilities` 列、创建时必写）与 12306 的 get-station-by-telecode
    #   （只有服务自带的自动摘要）全报成「描述 缺」，而素材体检判定它们**一条问题都没有** ——
    #   同一份数据两套标准，用户一眼看出两个模块对不上。下面 3 条正常夹具**逐字取自真库**
    #   （覆盖三种真实形态），第 4 条是「该被拦下」的反例。
    fake_rows = [
        {"ref": "weread", "name": "微信读书", "keywords": "搜索/找书",
         "abilities": "关键词：搜索/找书。翻书架、查进度。", "abilities_user": "", "src_abilities": ""},
        {"ref": "mcp:12306-mcp/get-station-by-telecode", "name": "站码查车站", "keywords": "",
         "abilities": "通过车站的 station_telecode 查询车站的详细信息。", "abilities_user": "",
         "src_abilities": "查看车票信息,查看当天从出发点到目的地的车票剩余"},
        {"ref": "mcp:pdd/search_goods", "name": "拼多多搜商品", "keywords": "拼多多搜商品",
         "abilities": "自动摘要", "abilities_user": "**已经想好要买某样东西**时用它", "src_abilities": ""},
        {"ref": "api:demo#get_weather", "name": "get_weather", "keywords": "", "abilities": "",
         "abilities_user": "", "src_abilities": ""},
    ]

    class _E(object):                       # 池条目的最小替身
        def __init__(self, ref, name):
            self.ref, self.name = ref, name
            self.keywords = self.abilities = self.abilities_user = ""

    _saved_pe, _saved_lr = cp.pool_entries, lint.load_rows
    cp.pool_entries = lambda a: [_E("weread", "微信读书"), _E("api:demo#get_weather", "get_weather"),
                                 _E("mcp:12306-mcp/get-station-by-telecode", "站码查车站"),
                                 _E("mcp:pdd/search_goods", "拼多多搜商品")]
    lint.load_rows = lambda a, conn=None: [dict(r) for r in fake_rows]
    try:
        ov = pr.pool_overview(3)
        by = {i["ref"]: i for i in ov["items"]}
    finally:
        cp.pool_entries, lint.load_rows = _saved_pe, _saved_lr      # ★ 还原（防污染后续用例）
    check("体检口径：CLI 的描述在 abilities 列 → 算「有描述」并标 auto（不再误报「缺」）",
          by["weread"]["has_desc"] and by["weread"]["desc_state"] == "auto", by["weread"]["desc_state"])
    check("体检口径：只有自动摘要 → auto（12306 那条的真相）",
          by["mcp:12306-mcp/get-station-by-telecode"]["desc_state"] == "auto")
    check("体检口径：手写描述 → user", by["mcp:pdd/search_goods"]["desc_state"] == "user")
    check("体检口径：关键词按池子优先级取（来源级兜底要认得出）",
          by["mcp:12306-mcp/get-station-by-telecode"]["has_keywords"]
          and by["mcp:12306-mcp/get-station-by-telecode"]["kw_from"] == "source_desc",
          by["mcp:12306-mcp/get-station-by-telecode"]["kw_from"])
    check("体检口径：desc_text 取「池子实际会用」那段（手写优先，否则自动摘要）",
          by["weread"]["desc_text"].startswith("关键词：")
          and by["mcp:pdd/search_goods"]["desc_text"].startswith("**已经想好"))
    check("体检口径：不登记手写字段 ≠ 缺描述（4 条里只有反例是真缺）",
          ov["no_desc"] == 1 and by["api:demo#get_weather"]["desc_state"] == "none")
    check("体检口径：反例被拦下（hard≥2 / problem_count=1）",
          ov["problem_count"] == 1 and by["api:demo#get_weather"]["hard"] >= 2,
          "hard=%s" % by["api:demo#get_weather"]["hard"])
    check("体检口径：warn 是 dict 列表（含 msg）→ 前端不再渲染 str(dict) 乱码",
          all(isinstance(p, dict) and "msg" in p for i in ov["items"] for p in (i["warn"] or [])))
    check("体检口径：汇总字段齐（problem_count / soft_count / auto_only / no_desc / derived_kw）",
          all(k in ov for k in ("problem_count", "soft_count", "auto_only", "no_desc", "derived_kw")))

    # ---------------- 9. 原始记录的生命周期（★ 2026-10-11 用户真机 bug）----------------
    # 用户实测：点「开始测评」无误 → 接着点「判分」报「找不到这一轮的原始记录」。
    # 根因：`cleanup_run` 在本轮**跑完那一刻**就执行，把刚写完的 `probe_run_*.json` 当「本轮新产物」
    #       删掉了；而判分/词法建议/写入大屏都在**之后**才读它 → 三步全废 ✗。
    # 修法：原始记录**不进水位线清理**，生命周期只由 `_prune_raw()` 管（跑完只留最近一份）。
    def _mem_conn():
        c2 = sqlite3.connect(":memory:")
        c2.row_factory = sqlite3.Row
        for ddl in ("CREATE TABLE conversations (id INTEGER PRIMARY KEY, account_id INTEGER)",
                    "CREATE TABLE messages (id INTEGER PRIMARY KEY, conversation_id INTEGER)",
                    "CREATE TABLE usage_events (id INTEGER PRIMARY KEY, account_id INTEGER)",
                    "CREATE TABLE memory_snapshots (id INTEGER PRIMARY KEY, account_id INTEGER)",
                    "CREATE TABLE failure_events (id INTEGER PRIMARY KEY, account_id INTEGER)",
                    "CREATE TABLE sessions (id INTEGER PRIMARY KEY, account_id INTEGER, login_at REAL)"):
            c2.execute(ddl)
        c2.commit()
        return c2

    # ★ 2026-10-15：本块**不能**复用上面那块的 scratch 目录 —— 两块共用同一个
    #   `_probe_selftest_user` 时，pytest-randomly 调换执行顺序后，上一块留下的
    #   `probe_run_*.json` 会污染本块「只留最近一份」的计数，导致随机顺序下偶发失败。
    scratch2 = "_probe_selftest_user_b"
    sbox = os.path.join(pr.ARTIFACTS_DIR, scratch2)     # 06：产物目录（原 SANDBOX_DIR）
    os.makedirs(sbox, exist_ok=True)
    junk = os.path.join(sbox, "junk_本轮新产物.txt")
    _saved = (pr._conn, pr.pool_overview, pr.ask, pr.calls_of_thread,
               pr.calls_of_thread_src, pr.ext_retrievals)
    pr._conn = lambda: _mem_conn()
    pr.pool_overview = lambda a: {"pool_version": 1, "pool_size": 0, "items": []}
    pr.ask = lambda client, token, q: (200, "答案", "tid-1", 1.0)
    pr.calls_of_thread = lambda a, t: []
    pr.calls_of_thread_src = lambda a, t: ([], "")      # ★ run_cases 现在读这个（带来源）
    pr.ext_retrievals = lambda a, since: []
    try:
        # （甲）**清理只删该删的**：按生产的真实顺序 —— 先打水位线 → 再造「本轮新产物」→ 清理
        wm9 = pr.watermark(3, scratch2)
        raw9 = os.path.join(sbox, "probe_run_20260101_120000.json")
        for p2 in (junk, raw9):
            with open(p2, "w", encoding="utf-8") as f:
                f.write("{}")
        gone9 = pr.cleanup_run(wm9)
        check("清理：本轮新产物照样删（junk）", not os.path.exists(junk), gone9.get("files"))
        check("清理：**原始记录不能删**（删了判分就找不到这一轮）", os.path.isfile(raw9), gone9)
        os.remove(raw9)

        # （乙）整轮跑通后：记录仍在（这是用户报的那条 bug 的正面回归 ✓）
        case9 = {"id": 1, "q": "最近我在微信读书看什么书", "kind": "single",
                 "expect": {"kind": "cli", "bin": "weread"}}
        out9 = pr.run_cases(3, scratch2, [case9], client=object(), token="t")
        check("跑一轮：原始记录**跑完仍在**（否则判分必失败）",
              os.path.isfile(out9["raw_path"]), out9["raw_path"])
        check("跑一轮：原始记录不进水位线快照",
              not any(pr.RAW_RE.match(f) for f in pr.snapshot_files(scratch2)))
        check("跑一轮：记录能读回（判分入口的输入）",
              pr.load_run(out9["raw_path"])["rows"][0]["q"] == case9["q"])
        pr.run_cases(3, scratch2, [case9], client=object(), token="t")
        left9 = sorted(f for f in os.listdir(sbox) if pr.RAW_RE.match(f))
        check("再跑一轮：沙箱里原始记录只留最近一份", len(left9) == 1, left9)
    finally:
        (pr._conn, pr.pool_overview, pr.ask, pr.calls_of_thread,
         pr.calls_of_thread_src, pr.ext_retrievals) = _saved
        shutil.rmtree(sbox, ignore_errors=True)

    # ---------------- 10. 单题护栏（★ 2026-10-11 用户实测「某一题卡死」后加）----------------
    # 取证：审计日志显示某一题连翻 11 页（`bili favorites --page 1..11`）、跑了 4 分钟后 500 ✗
    class _Resp(object):
        def __init__(self, code, body=None, text=""):
            self.status_code = code
            self._body = body or {}
            self.text = text or json.dumps(self._body, ensure_ascii=False)

        def json(self):
            return self._body

    class _FakeClient(object):
        """最小 TestClient 替身：按 URL 给不同响应，并记录每次调用 ✓"""

        def __init__(self, chat_resp=None, chat_sleep=0.0):
            self.calls = []
            self.chat_resp = chat_resp
            self.chat_sleep = chat_sleep

        def post(self, url, params=None, timeout=None, **kw):
            self.calls.append((url, dict(params or {})))
            if url.endswith("/api/chat/sessions"):
                return _Resp(200, {"thread_id": "tid-1"})
            if url.endswith("/api/chat/cancel"):
                return _Resp(200, {"ok": True})
            if url.endswith("/api/chat"):
                if self.chat_sleep:
                    time.sleep(self.chat_sleep)
                return self.chat_resp or _Resp(200, {"answer": "好的"})
            return _Resp(404, text="no")

    _sv_to = os.environ.get("PROBE_ASK_TIMEOUT")
    # 复刻第 9 节的桩（第 9 节 finally 已还原，这里必须**重新打**：否则会走真库/真池 ✗）
    _saved10c = (pr._conn, pr.pool_overview, pr.calls_of_thread,
                 pr.calls_of_thread_src, pr.ext_retrievals)
    pr._conn = lambda: _mem_conn()
    pr.pool_overview = lambda a: {"pool_version": 1, "pool_size": 0, "items": []}
    pr.calls_of_thread = lambda a, t: []
    pr.calls_of_thread_src = lambda a, t: ([], "")      # ★ run_cases 现在读这个（带来源）
    pr.ext_retrievals = lambda a, since: []
    try:
        os.environ["PROBE_ASK_TIMEOUT"] = "1"
        fc = _FakeClient()
        code10, ans10, tid10, _ = pr.ask(fc, "tok", "看看B站视频")
        check("单题：正常路径返回 200 + 答案 + thread_id",
              code10 == 200 and ans10 == "好的" and tid10 == "tid-1", (code10, ans10, tid10))
        check("单题：会话用 kind=probe 建（不进会话库 ✓）",
              any(u.endswith("/api/chat/sessions") and p.get("kind") == "probe" for u, p in fc.calls))

        fc2 = _FakeClient(chat_sleep=5)                     # 超过 1s 上限 → 中断
        code10b, ans10b, _, _ = pr.ask(fc2, "tok", "翻页翻不完的题")
        check("单题超时 → 408（走「未测」口径，不污染能力评价）", code10b == 408, (code10b, ans10b))
        check("单题超时 → 真的调了 /api/chat/cancel 中断本轮",
              any(u.endswith("/api/chat/cancel") for u, _ in fc2.calls), [u for u, _ in fc2.calls])
        check("单题超时 → 不重试（避免 2× 超时把整轮拖死）",
              sum(1 for u, _ in fc2.calls if u.endswith("/api/chat")) == 1,
              [u for u, _ in fc2.calls])

        fc3 = _FakeClient(chat_resp=_Resp(500, text='{"detail":"Agent 执行出错：…cannot schedule…"}'))
        code10c, ans10c, _, _ = pr.ask(fc3, "tok", "会报 500 的题")
        check("单题 500 → 不重试（同一路径重试注定同样结果）",
              code10c == 500 and sum(1 for u, _ in fc3.calls if u.endswith("/api/chat")) == 1,
              (code10c, [u for u, _ in fc3.calls]))

        # 关服/撞上进程级故障 → **提前中止整轮**（已完成的行留着，别白跑 ✓）
        case10 = {"id": 1, "q": "任一题", "kind": "single",
                  "expect": {"kind": "cli", "bin": "weread"}}
        _saved10 = (pr._service_shutting_down,)
        pr._service_shutting_down = lambda: True
        try:
            wm10 = pr.watermark(3, scratch2)
            out10 = pr.run_cases(3, scratch2, [case10], client=object(), token="t")
        finally:
            pr._service_shutting_down = _saved10[0]
        check("关服中开跑 → 一题都不发、如实记账 `stopped`",
              out10["count"] == 0 and "关闭" in (out10.get("stopped") or ""), out10.get("stopped"))
        check("关服中止：原始记录照常落盘（含 stopped 字段，供事后核对 ✓）",
              os.path.isfile(out10["raw_path"])
              and "stopped" in pr.load_run(out10["raw_path"]), out10["raw_path"])
        # 撞上「线程池已关闭」的 500 → 也提前中止（第 2 题不再发问）
        seq10 = []

        def _ask10(client, token, q):
            seq10.append(q)
            return (500, '{\n"detail":"RuntimeError: cannot schedule new futures after shutdown"}', "t", 1.0)

        _saved10b = (pr._service_shutting_down, pr.ask)
        pr._service_shutting_down = lambda: False
        pr.ask = _ask10
        try:
            out10b = pr.run_cases(3, scratch2, [case10, dict(case10, id=2, q="第二题")],
                                  client=object(), token="t")
        finally:
            (pr._service_shutting_down, pr.ask) = _saved10b
        check("撞上「线程池已关闭」→ 只发第 1 题就中止，并写明原因",
              seq10 == ["任一题"] and out10b["count"] == 1 and "cannot schedule" in (out10b.get("stopped") or ""),
              (seq10, out10b.get("stopped")))
    finally:
        (pr._conn, pr.pool_overview, pr.calls_of_thread,
         pr.calls_of_thread_src, pr.ext_retrievals) = _saved10c
        shutil.rmtree(os.path.join(pr.ARTIFACTS_DIR, scratch2), ignore_errors=True)
        if _sv_to is None:
            os.environ.pop("PROBE_ASK_TIMEOUT", None)
        else:
            os.environ["PROBE_ASK_TIMEOUT"] = _sv_to

    # ---- 11. 被中断的轮次也要有调用痕迹（用户 2026-10-11 实测：被打分表记成「一次工具都没调用」）----
    #   中断轮次不落 `messages.tool_calls` → 读库为空 → 必须用**耐久工具日志**兜底 ✓
    _tk11 = tempfile.mkdtemp(prefix="probe_toolcall_")
    _log11 = os.path.join(_tk11, "tool_calls.jsonl")
    _saved_log11 = (pr.TOOL_CALL_LOG, pr._conn)
    try:
        pr.TOOL_CALL_LOG = _log11
        with open(_log11, "w", encoding="utf-8") as f:
            f.write(json.dumps({"ts": "2026-10-11 18:00:00", "thread_id": "tid-中断轮",
                                "tool": "run_shell_command",
                                "args": {"command": "bili", "argv": ["favorites", "161158619", "--page", "3"]}},
                               ensure_ascii=False) + "\n")
            f.write(json.dumps({"ts": "2026-10-11 18:00:07", "thread_id": "tid-中断轮",
                                "tool": "invoke_tool", "args": {"ref": "mcp:pdd/search_goods",
                                                                "params": {"keyword": "机械键盘"}}},
                               ensure_ascii=False) + "\n")
            f.write("{半截行（进程被杀）\n")                      # 坏行不许炸 ✓
            f.write(json.dumps({"ts": "2026-10-11 18:00:08", "thread_id": "别人的会话",
                                "tool": "run_shell_command", "args": {"command": "ls"}},
                               ensure_ascii=False) + "\n")
        _calls11 = pr.calls_from_log("tid-中断轮")
        check("耐久日志：能按会话读回调用（2 条，坏行/别人的会话都跳过 ✓）",
              len(_calls11) == 2, _calls11)
        check("耐久日志：CLI 调用解析成 brief（bili favorites …）",
              _calls11 and _calls11[0]["target"] == "bili" and "--page" in _calls11[0]["brief"],
              _calls11[0] if _calls11 else None)
        check("耐久日志：MCP 调用解析成 ref + 参数摘要",
              len(_calls11) > 1 and _calls11[1]["target"] == "mcp:pdd/search_goods"
              and "机械键盘" in _calls11[1]["brief"], _calls11[1] if len(_calls11) > 1 else None)

        class _EmptyDB11:                                    # 读库为空（中断轮 = 没落库）
            def execute(self, *a, **k):
                return []
            def close(self):
                pass

        pr._conn = lambda: _EmptyDB11()
        _cs11, _src11 = pr.calls_of_thread_src(3, "tid-中断轮")
        check("读库为空 → 落到**耐久日志**（来源标 log ✓）", len(_cs11) == 2 and _src11 == "log",
              (_src11, len(_cs11)))
        check("**没痕迹时**才回落到日志：另一会话读不到东西（不串号 ✓）",
              pr.calls_of_thread_src(3, "不存在的会话") == ([], ""),
              pr.calls_of_thread_src(3, "不存在的会话"))

        # 监控侧：`report_tool` 必须真的把调用落进这个文件（否则上面全白搭）
        from api import monitor as _mon11
        _sv_mon11 = _mon11.TOOL_CALL_LOG
        _mon11.TOOL_CALL_LOG = _log11
        try:
            _m11 = _mon11.ToolMonitor()
            _m11.set_current_thread("tid-监控")
            _m11.report_tool("run_shell_command", {"command": "bili", "argv": ["hot"]})
        finally:
            _mon11.TOOL_CALL_LOG = _sv_mon11
        _cx11 = pr.calls_from_log("tid-监控")
        check("监控侧：`report_tool` 落耐久日志（测评侧才读得到 ✓）",
              len(_cx11) == 1 and _cx11[0]["target"] == "bili" and "hot" in _cx11[0]["brief"], _cx11)

        # 端到端：**408 的中断轮** → 行里带 `interrupted`、调用从耐久日志读回（读库为空）
        _case11 = {"id": 9, "q": "在B站找我收藏的提示词教学视频", "kind": "single",
                   "expect": {"kind": "cli", "bin": "bili"}}
        _sv11b = (pr.ask, pr.pool_overview, pr.ext_retrievals, pr.watermark, pr.cleanup_run)
        pr.ask = lambda c, t, q: (408, "超时中断（单题上限 300s）", "tid-中断轮", 301.0)
        pr.pool_overview = lambda a: {"pool_version": 1, "pool_size": 2, "items": []}
        pr.ext_retrievals = lambda a, s: 0
        pr.watermark = lambda a, u: {"usage": 0, "files": {}}
        pr.cleanup_run = lambda w: {}
        try:
            _out11 = pr.run_cases(3, "_probe_selftest_user", [_case11], client=object(), token="t")
        finally:
            (pr.ask, pr.pool_overview, pr.ext_retrievals, pr.watermark, pr.cleanup_run) = _sv11b
        _r11 = (_out11.get("rows") or [{}])[0]
        check("中断轮（408）：行里 `interrupted=True` + 调用来自**耐久日志**（不再是空表 ✓）",
              _r11.get("interrupted") is True and len(_r11.get("calls") or []) == 2
              and _r11.get("calls_src") == "log",
              (_r11.get("interrupted"), _r11.get("calls_src"), len(_r11.get("calls") or [])))
        check("中断轮（408）：仍然记「未测」（不计入能力评价 ✓ 口径不变）",
              _r11.get("unmeasured") is True, _r11.get("unmeasured"))
        _sheet11 = pr.build_sheet(_out11, scores={9: {"verdict": "未测", "note": ""}})
        check("出表：中断题列出调用 + ⏱ 标注（**不再**写「一次工具都没调用」✓）",
              "bili favorites" in _sheet11 and "⏱" in _sheet11
              and "（一次工具都没调）** | 未测" not in _sheet11.split("| 9 ")[-1][:200],
              None)
    finally:
        pr.TOOL_CALL_LOG, pr._conn = _saved_log11
        shutil.rmtree(_tk11, ignore_errors=True)

    check("出表：被中断的题**不再写「一次工具都没调」**，而是列出中断前的调用 + ⏱ 标注",
          ("⏱" in pr._fmt_calls([{"brief": "bili favorites 161158619 --page 3"}], True)
           and "超时中断" in pr._fmt_calls([{"brief": "x"}], True)
           and "超时中断" in pr._fmt_calls([], True)), None)
    check("出表：非中断题口径不变（空调用仍是「一次工具都没调」）",
          "一次工具都没调" in pr._fmt_calls([]) and "超时中断" not in pr._fmt_calls(
              [{"brief": "bili hot"}]), None)

    # ---- 15. 事故回归：水位线读不到时**一行都不许删**（2026-10-11「历史版本全没了」）----
    #   病灶：`_max_id` 读失败静默返回 0 → `DELETE ... WHERE account_id=3 AND id>0` ⇒ 账号历史被清空 ✗
    _d15 = tempfile.mkdtemp(prefix="probe_wm_safety_")
    _p15 = os.path.join(_d15, "t.db")
    def _open15():                       # cleanup_run 每次都 close 连接 → 桩必须**每次新开**（文件库 ✓）
        c2 = sqlite3.connect(_p15)
        c2.row_factory = sqlite3.Row
        return c2
    _c15 = _open15()
    _c15.row_factory = sqlite3.Row
    for _ddl in ("CREATE TABLE conversations (id INTEGER PRIMARY KEY, account_id INTEGER)",
                 "CREATE TABLE messages (id INTEGER PRIMARY KEY, conversation_id INTEGER)",
                 "CREATE TABLE usage_events (id INTEGER PRIMARY KEY, account_id INTEGER)",
                 "CREATE TABLE memory_snapshots (id INTEGER PRIMARY KEY, account_id INTEGER)",
                 "CREATE TABLE failure_events (id INTEGER PRIMARY KEY, account_id INTEGER)",
                 "CREATE TABLE sessions (id INTEGER PRIMARY KEY, account_id INTEGER, login_at REAL)"):
        _c15.execute(_ddl)
    _c15.execute("INSERT INTO memory_snapshots (id, account_id) VALUES (1,3),(2,3)")
    _c15.execute("INSERT INTO usage_events (id, account_id) VALUES (1,3),(2,3)")
    _c15.execute("INSERT INTO failure_events (id, account_id) VALUES (1,3)")
    _c15.commit()
    _saved15 = pr._conn
    pr._conn = _open15
    try:
        check("水位线：`_max_id` 读不到时返回 **None**（不是 0 ✓ 这是数据安全的关键）",
              pr._max_id(_c15, "不存在的表") is None, pr._max_id(_c15, "不存在的表"))
        _w15 = {"acc": 3, "conv": None, "usage": None, "mem": None, "fail": None, "ts": 9e9,
                "files": set(), "username": "_probe_selftest_user"}
        _g15 = pr.cleanup_run(_w15)
        _c15r = sqlite3.connect(_p15)                 # cleanup 会关连接 → 重新开一个读 ✓
        _n15 = {t: _c15r.execute("SELECT COUNT(*) FROM %s WHERE account_id=3" % t).fetchone()[0]
                for t in ("usage_events", "failure_events", "memory_snapshots")}
        _c15r.close()
        check("水位线读不到 → **一行都不删**（usage/failure/snapshots 全都完好 ✓）",
              all(v > 0 for v in _n15.values()) and "usage_events" not in _g15, (_g15, _n15))
        _g15b = pr.cleanup_run({"acc": 3, "conv": 9e9, "usage": 0, "mem": 0, "fail": 0, "ts": 9e9,
                                "files": set(), "username": "_probe_selftest_user"})
        _c15b = sqlite3.connect(_p15)
        _n15b = _c15b.execute("SELECT COUNT(*) FROM memory_snapshots WHERE account_id=3").fetchone()[0]
        _c15b.close()
        check("清理**永不触碰 memory_snapshots**（画像历史是用户数据 ✓ 事故回归）",
              "memory_snapshots" not in _g15b and _n15b == 2, (_g15b, _n15b))
    finally:
        pr._conn = _saved15
        _c15.close()
        shutil.rmtree(_d15, ignore_errors=True)

    print("\n" + "=" * 62)
    print("通过 %d ／ 失败 %d" % (len(ok), len(bad)))
    for n in bad:
        print("  -", n)
    assert not bad, "离线自测失败 %d 项：%s" % (len(bad), "；".join(bad))



# ── 2026-10-13 修（前置清理）：本用例会在真实的 agents_docs/ 与 data/sandbox/ 下建临时用户目录，
#    跑完必须自清 —— 此前只建不删，前置清理后被单测反复重建（"清理被重新污染"的根因之一）。
import atexit as _atexit

_SCRATCH_USERS = ("_probe_selftest_user", "_probe_selftest_user_b")


def _cleanup_scratch_dirs():
    import shutil
    from pathlib import Path
    _root = Path(__file__).resolve().parents[2]
    for _u in _SCRATCH_USERS:
        from utils import paths as _upaths            # 账号目录清单的唯一事实源
        for _p in _upaths.account_dirs_of(_u):
            try:
                if _p.exists():
                    shutil.rmtree(_p)
                    print("[cleanup] 已删除临时目录 " + str(_p.relative_to(_root)))
            except Exception as _e:                                     # noqa: BLE001
                print("[cleanup] 删除失败 " + str(_p) + ": " + str(_e))


_atexit.register(_cleanup_scratch_dirs)
