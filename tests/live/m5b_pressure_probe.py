# -*- coding: utf-8 -*-
"""M6b 加压探针 **v3 · 记录器**：只**记录**模型调了哪些工具、调用顺序与参数，**不判定对错**。

为什么推翻 v2 的自动判定（2026-10-01 用户两次实测反馈 ✓）：
  · v2 判「首调 == 期望工具」→ 遇到 **链式调用** 必然误判 ✗：
    12306 除 `get-current-date` 外几乎都要日期参数，还要**先把城市换成 station_code**，
    真实正确过程是「取时间 → 取站点码 → 查余票」这样一串 ✓ 只看首调永远判不对 ✗；
  · 而且这是**结构性问题**：别的 MCP/API 将来只要有多步前置，就得再补一条规则 ✗（打补丁没完没了）；
  · 所以：**脚本只出事实（调了哪些工具 / 什么顺序 / 什么参数 / 耗时），对错由人工打分** ✓。
    v2 那套「期望首调 / 多工具 ≥2 来源 / 合法前置表」**全部废弃** ✗（`PRE_BY_SOURCE`、`judge()` 已删除）。

脚本产出三样：
  ① 控制台：逐条打印「参考链路 → 实际调用序列（编号 + 参数摘要）+ 耗时 + 路由形态」；
  ② `data/m5b_pressure_result.json`：机器可读的原始记录（每题完整调用序列 ✓ 无评分字段 ✗）；
  ③ `tests/test_out/m6b_score_sheet_<时间戳>.md`：**人工评分表**（每题一块：参考链路 / 实际序列 /
     评分：__ / 备注：__），可加 `--desktop` 同时复制到你桌面方便批注 ✓。

铁律（记录侧，不涉判定 ✓）：
  ① 每条一问、**独立新会话**；
  ② 调用序列从 `messages.tool_calls` **按会话**读回（不采信审计窗口，会串别的进程 ✗）；
  ③ 四种调用形状都记：CLI `run_shell_command` / API·MCP `invoke_tool` / 其它内置工具名；
  ④ 429/5xx 这类「**没测到**」单独标 `unmeasured` ✓ 与「调错了」分开（人工打分时才不会被额度问题误导 ✓）。

运行（真机）：
    .venv/Scripts/python.exe tests/live/m5b_pressure_probe.py                        # core 16 条
    $env:M5B_SET="full"; .venv/Scripts/python.exe tests/live/m5b_pressure_probe.py    # 全量 43 条（PS）
    .venv/Scripts/python.exe tests/live/m5b_pressure_probe.py --desktop               # 顺带把评分表放桌面
    # 单条冒烟（★ 冒烟完**立刻清掉**环境变量，否则后面的正式跑会被悄悄过滤 ✗ 2026-10-01 踩过）
    $env:M5B_ONLY="get-tickets"; .venv/Scripts/python.exe tests/live/m5b_pressure_probe.py; Remove-Item Env:M5B_ONLY
自检（不调模型，几秒 ✓）：
    SKIP_LLM=1 .venv/Scripts/python.exe tests/live/m5b_pressure_probe.py

可选环境变量：M5B_USER / M5B_PWD（探针账号）、M5B_SET（core/full）、M5B_ONLY（冒烟）、
              M5B_KEEP=1（保留本轮产物）、SKIP_LLM=1（离线自检）。
"""

import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from fastapi.testclient import TestClient  # noqa: E402

from api import server  # noqa: E402
from tools import capability_pool as cp  # noqa: E402
from tools import tool_router as tr  # noqa: E402
from tools.schema_personal import ensure_tables, get_personal_conn  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
RESULT = os.path.join(ROOT, "data", "m5b_pressure_result.json")
SHEET_DIR = os.path.join(ROOT, "tests", "test_out")
DESKTOP = os.path.join(os.path.expanduser("~"), "Desktop")
SKIP_LLM = os.getenv("SKIP_LLM") == "1"
ONLY = os.getenv("M5B_ONLY", "").strip()
IDS = os.getenv("M5B_IDS", "").strip()   # 定向复跑：题号列表（逗号分隔）
WANT_SET = (os.getenv("M5B_SET", "core").strip().lower() or "core")
KEEP = os.getenv("M5B_KEEP") == "1"
USER = os.getenv("M5B_USER", "尼古喵喵").strip()
PWD = os.getenv("M5B_PWD", "123456")
TO_DESKTOP = "--desktop" in sys.argv
WITH_ANSWER = "--no-answer" not in sys.argv

# 跑完按「新增差分」清理的账号目录（只删本轮新出现的文件 ✓ 既有文件一律不动 ✗）
ARTIFACT_DIRS = ("agents_docs/%s", "output/%s", "pic/%s", "data/sandbox/%s")
UNMEASURED_STATUS = (408, 429, 500, 502, 503, 504)


def cli(bin_name, argv, alts=None):
    """参考（**仅供人工打分参照** ✓ 不再参与判定）：CLI 可执行名 + 期望 argv。"""
    return {"kind": "cli", "bin": bin_name, "argv": list(argv), "alts": alts or []}


def tool(ref, alts=None):
    """参考（**仅供人工打分参照** ✓ 不再参与判定）：API/MCP 的 ref。"""
    return {"kind": "tool", "ref": ref, "alts": alts or []}


def _source_of(ref: str) -> str:
    """ref → **来源**：`api:<服务>#<操作>` 取 `#` 前；`mcp:<服务>/<工具>` 取 `/` 前 ✓。

    ⚠️ 别用 `split('#')[0]` 一把梭 ✗ —— MCP 的 ref 用 `/` 分隔，那样切出来是整个 ref，
    会把同一来源的多个工具算成多个来源（v2 自检当场逮到过这个 bug ✓）。
    """
    ref = str(ref or "")
    if "#" in ref:
        return ref.split("#")[0]
    if "/" in ref:
        return ref.split("/")[0]
    return ref


# ---------------------------------------------------------------- 问题集 v2（题不变 ✓）
# `chain` = **参考链路**（给人工打分看的"标准动作" ✓ 不参与任何自动判定 ✗）
# 12306 的链路依据：除 get-current-date 外几乎所有命令都要日期参数，且余票类还要 station_code ✓
Q = [
    # ── A. CLI：微信读书 ───────────────────────────────────────────────
    dict(id=1, core=True, q="我最近在微信读书上看的是什么书", exp=cli("weread", ["shelf", "recent"])),
    dict(id=2, q="微信读书上《人类简史》这本书讲什么",
         exp=cli("weread", ["book", "resolve"]),
         chain="weread book resolve（书名→bookId）→ weread book info"),
    dict(id=3, q="我这个月在微信读书上读了多久", exp=cli("weread", ["readdata", "summary"])),
    dict(id=4, q="帮我看看我的读书笔记都在哪", exp=cli("weread", ["notes", "notebooks"])),
    # ── A. CLI：微信聊天数据 ────────────────────────────────────────────
    dict(id=5, q="我最近和谁聊过天", exp=cli("wechat-cli", ["sessions"])),
    dict(id=6, core=True, q="帮我找找我和袁战的聊天记录", exp=cli("wechat-cli", ["history"])),
    dict(id=7, q="微信里有没有我还没读的消息",
         exp=cli("wechat-cli", ["new-messages"], alts=[["unread"]])),
    # ── A. CLI：哔哩哔哩 ───────────────────────────────────────────────
    dict(id=8, q="B站我收藏的视频里有没有讲 Rust 的", exp=cli("bili", ["favorites"])),
    dict(id=9, core=True, q="B站今天有什么热门视频", exp=cli("bili", ["hot"], alts=[["rank"]])),
    dict(id=10, q="BV1xx411c7mD 这个视频讲了什么", exp=cli("bili", ["video"])),
    # ── B. api：Steam / 小小API ────────────────────────────────────────
    dict(id=11, core=True, q="Steam 上《空洞骑士》卖多少钱", exp=tool("api:steam#searchGame")),
    dict(id=12, q="Steam 上 appid 是 774361 的游戏什么时候发售", exp=tool("api:steam#gameDetail")),
    dict(id=13, core=True, q="抖音今天最热门的那条视频是什么内容", exp=tool("api:xxapi#douyinHot")),
    dict(id=14, q="114.114.114.114 这个 IP 是哪儿的", exp=tool("api:xxapi#ipQuery")),
    # ── C. mcp：12306（★ 复杂度就在这些题上 ✓ 参考链路写全）────────────────
    dict(id=15, core=True, q="帮我查一下后天成都到重庆还有没有票",
         exp=tool("mcp:12306-mcp/get-tickets"),
         chain="get-current-date（后天→具体日期）→ get-station-code-of-citys（成都/重庆→站点码）→ get-tickets"),
    dict(id=16, q="从成都坐火车去广州，有哪些需要中转的车次",
         exp=tool("mcp:12306-mcp/get-interline-tickets"),
         chain="get-current-date → get-station-code-of-citys（成都/广州）→ get-interline-tickets"),
    dict(id=17, core=True, q="G1033 这趟车中间都停哪些站",
         exp=tool("mcp:12306-mcp/get-train-route-stations"),
         chain="get-current-date → get-train-route-stations（车次 + 日期）"),
    dict(id=18, q="「上海虹桥」这个火车站的站点代码是多少",
         exp=tool("mcp:12306-mcp/get-station-code-by-names"),
         chain="get-station-code-by-names（站名→code，**不需要日期** ✓）"),
    dict(id=19, q="上海的车站代码是什么",
         exp=tool("mcp:12306-mcp/get-station-code-of-citys", alts=["mcp:12306-mcp/get-stations-code-in-city"]),
         chain="get-station-code-of-citys（城市→代表站 code ✓ 不需要日期）"),
    dict(id=20, q="成都有哪几个火车站",
         exp=tool("mcp:12306-mcp/get-stations-code-in-city", alts=["mcp:12306-mcp/get-station-code-of-citys"]),
         chain="get-stations-code-in-city（城市→该市全部车站 ✓ 不需要日期）"),
    dict(id=21, q="VNP 这个站码对应的是哪个车站",
         exp=tool("mcp:12306-mcp/get-station-by-telecode"),
         chain="get-station-by-telecode（telecode→车站详情 ✓ 不需要日期）"),
    dict(id=22, q="下周三具体是几月几号，我要买那天的票",
         exp=tool("mcp:12306-mcp/get-current-date"),
         chain="get-current-date（本身就这一件事 ✓）"),
    # ── D. mcp：法国国家图书馆（原文英文、彼此相似 → 正是要看路由怎么选 ✓）
    dict(id=23, core=True, q="法国国家图书馆里有没有《悲惨世界》", exp=tool("mcp:bnf/search_by_title")),
    dict(id=24, q="Gallica 上有没有雨果写的资料", exp=tool("mcp:bnf/search_by_author")),
    dict(id=25, q="Gallica 里关于巴黎公社的资料有哪些", exp=tool("mcp:bnf/search_by_subject")),
    dict(id=26, q="Gallica 里 1914 年的资料有哪些", exp=tool("mcp:bnf/search_by_date")),
    dict(id=27, q="Gallica 里的手稿类文献有哪些", exp=tool("mcp:bnf/search_by_document_type")),
    dict(id=28, q="用法语 CQL 语法在 Gallica 检索 dc.creator=Victor Hugo",
         exp=tool("mcp:bnf/advanced_search")),
    dict(id=29, q="用自然语言在 Gallica 上找关于巴黎公社的资料",
         exp=tool("mcp:bnf/natural_language_search")),
    dict(id=30, core=True, q="用法语资料帮我做一份关于雨果的研究报告",
         exp=tool("mcp:bnf/sequential_reporting")),
    # ── E. mcp：拼多多 ────────────────────────────────────────────────
    dict(id=31, core=True, q="拼多多上搜一下 iPhone 17 的价格", exp=tool("mcp:pdd/search_goods")),
    dict(id=32, q="拼多多百亿补贴现在有什么好东西", exp=tool("mcp:pdd/explore_deals")),
    dict(id=33, q="拼多多这个商品 goods_sign 是 E932cKJQRhht7tuxwfDAxrupzXBiZpWMgg_JQPw6VPiN3，"
                  "帮我看看详情和优惠券",
         exp=tool("mcp:pdd/get_goods_detail"),
         chain="get_goods_detail（题面已给 goods_sign ✓ 也可先 search_goods 拿 sign）"),
    # ── F. mcp：城市 / 路线 ───────────────────────────────────────────
    # ★ 2026-10-01 换题：原题「Shanghai 在哪个省」被模型凭**记忆**答对（知名城市，大概率已被训进参数里 ✓）
    #   → 路由根本没触发 ✗ 白测一道。换成**冷门城市 + 记忆答不出的字段**（人口/经纬度），
    #   数据源已离线核过：GeoNames 查得到（石渠县 人口 80834 / 经度 98.10 ✓）。
    dict(id=34, core=True, q="帮我查一下石渠县的地理信息：属于哪个省、经纬度和人口分别是多少",
         exp=tool("mcp:city/search_city"),
         chain="mcp:city/search_city（city_name=石渠县）→ 一级行政区 / 经纬度 / 人口"),
    dict(id=35, core=True, q="从成都开车到稻城亚丁大概要多久", exp=tool("mcp:route/plan_route")),
    # ── G. 多工具题（**人打分看的是"是否跨来源协作"** ✓ 不再自动要求 ≥2 来源 ✗）────
    dict(id=36, core=True, kind="multi",
         q="我想下周去广州，帮我查从成都出发的火车票，再规划一条从广州南站到珠江新城的路线",
         exp=tool("mcp:12306-mcp/get-tickets"),
         chain="get-current-date → get-station-code-of-citys → get-tickets（12306）＋ plan_route（route）"),
    dict(id=37, kind="multi",
         q="从成都坐火车去广州，中途要换乘哪些车？把车次和经停站都列出来",
         exp=tool("mcp:12306-mcp/get-interline-tickets"),
         chain="get-current-date → 站点码 → get-interline-tickets → get-train-route-stations（同源两工具）"),
    dict(id=38, core=True, kind="multi",
         q="帮我翻翻我和袁战的聊天记录，里面提到的那本书在微信读书上有没有",
         exp=cli("wechat-cli", ["history"], alts=[["search"]]),
         chain="wechat-cli history/search → weread search（跨源两工具）"),
    dict(id=39, kind="multi",
         q="Steam 上《空洞骑士》现在多少钱？顺便看看拼多多有没有卖周边的",
         exp=tool("api:steam#searchGame"),
         chain="api:steam#searchGame → mcp:pdd/search_goods（跨源两工具）"),
    dict(id=40, kind="multi",
         q="抖音今天最热门的那条视频讲了什么？顺便找找B站有没有相关视频",
         exp=tool("api:xxapi#douyinHot"),
         chain="api:xxapi#douyinHot → bili search（跨源两工具）"),
    dict(id=41, kind="multi",
         q="查一下 114.114.114.114 是哪儿的，然后帮我规划从那个城市到成都的路线",
         exp=tool("api:xxapi#ipQuery"),
         chain="api:xxapi#ipQuery → mcp:route/plan_route（跨源两工具）"),
    # ── H. 反例（**观察项** ✓ 人打分看"是否恰好没调工具"）──────────────────
    dict(id=42, core=True, kind="neg", q="你是谁", exp=None, chain="不调用任何工具"),
    dict(id=43, core=True, kind="neg", q="帮我写一首诗", exp=None, chain="不调用任何工具"),
]

OPS = []          # 运维类检查（登录/能力池/清理）—— 与"题目对错"无关 ✓


def check(name, cond, extra=""):
    OPS.append((bool(cond), name))
    print("%s %s%s" % ("  ✅" if cond else "  ❌", name, (" | " + str(extra)) if extra else ""))


# ---------------------------------------------------------------- 调用序列解析（纯函数 ✓）
def parse_calls(tool_calls_json) -> list:
    """把一条 assistant 消息的 `tool_calls` 解析成统一结构（纯函数 ✓）。

    返回 [{'name','target','argv','params','source','brief'}]：
      · CLI     → target = 可执行名，argv 有值，brief = 「bin argv…」
      · API/MCP → target = ref，params 有值，brief = 「ref {参数摘要}」
      · 其它内置工具 → target = 工具名，source = `builtin:<name>`
    """
    out: list = []
    try:
        calls = json.loads(tool_calls_json or "[]")
    except (TypeError, ValueError):
        return out
    if not isinstance(calls, list):
        return out
    for t in calls:
        if not isinstance(t, dict):
            continue
        name = str(t.get("name") or "")
        a = t.get("args") or {}
        if name == "run_shell_command":
            cmd = str(a.get("command") or "")
            argv = [str(x) for x in (a.get("argv") or [])]
            out.append({"name": name, "target": cmd, "argv": argv, "params": None,
                        "source": "cli:%s" % cmd,
                        "brief": ("%s %s" % (cmd, " ".join(argv))).strip()})
        elif name == "invoke_tool":
            ref = str(a.get("ref") or "")
            params = a.get("params") or {}
            brief = json.dumps(params, ensure_ascii=False) if params else "{}"
            out.append({"name": name, "target": ref, "argv": [], "params": params,
                        "source": _source_of(ref),
                        "brief": "%s %s" % (ref, brief[:100])})
        else:
            out.append({"name": name, "target": name, "argv": [], "params": a,
                        "source": "builtin:%s" % name, "brief": name})
    for c in out:
        # ★ 2026-10-02：**必须转义换行** ✗ —— 模型偶尔吐出带换行的畸形参数
        #   （实测 `#36` 那次 XML 泄漏），换行会把评分表里的编号行拆断、回填时丢半截，
        #   导致下次对比误报"序列变化" ✓
        c["brief"] = str(c["brief"]).replace("\r", "").replace("\n", "\\n")
    return out


def _ref_text(item: dict) -> str:
    """参考链路的兜底文案（题目没写 `chain` 时用期望工具 ✓ 仅供人工对照）。"""
    if item.get("chain"):
        return str(item["chain"])
    exp = item.get("exp")
    if not exp:
        return "不调用任何工具"
    return ("%s %s" % (exp["bin"], exp["argv"])) if exp["kind"] == "cli" else str(exp["ref"])


# ---------------------------------------------------------------- 账号 / 水位线 / 清理
def _account_id(username: str) -> int:
    conn = get_personal_conn()
    try:
        row = conn.execute("SELECT id FROM accounts WHERE username=?", (username,)).fetchone()
        return int(row["id"]) if row else 0
    finally:
        conn.close()


def _max_id(conn, table: str) -> int:
    try:
        return int(conn.execute("SELECT COALESCE(MAX(id), 0) FROM %s" % table).fetchone()[0])
    except Exception:  # noqa: BLE001
        return 0


def _snapshot_files() -> set:
    out = set()
    for pat in ARTIFACT_DIRS:
        base = os.path.join(ROOT, *(pat % USER).split("/"))
        if not os.path.isdir(base):
            continue
        for dirpath, _dirs, files in os.walk(base):
            for f in files:
                out.add(os.path.relpath(os.path.join(dirpath, f), ROOT))
    return out


def _watermark(acc: int) -> dict:
    conn = get_personal_conn()
    try:
        return {"acc": acc, "conv": _max_id(conn, "conversations"),
                "usage": _max_id(conn, "usage_events"), "mem": _max_id(conn, "memory_snapshots"),
                "fail": _max_id(conn, "failure_events"), "ts": time.time(),
                "files": _snapshot_files()}
    finally:
        conn.close()


def _cleanup_run(wm: dict) -> dict:
    """只删**本轮水位线之后、且属于本账号**的行 + 本轮新出现的文件 ✓（既有数据一律不动 ✗）。"""
    acc = wm["acc"]
    conn = get_personal_conn()
    gone: dict = {}
    try:
        conv_ids = [int(r[0]) for r in list(conn.execute(
            "SELECT id FROM conversations WHERE account_id=? AND id>?", (acc, wm["conv"])))]
        if conv_ids:
            ph = ",".join("?" * len(conv_ids))
            gone["messages"] = int(conn.execute(
                "SELECT COUNT(*) FROM messages WHERE conversation_id IN (%s)" % ph,
                conv_ids).fetchone()[0])
            conn.execute("DELETE FROM messages WHERE conversation_id IN (%s)" % ph, conv_ids)
            conn.execute("DELETE FROM conversations WHERE id IN (%s)" % ph, conv_ids)
            gone["conversations"] = len(conv_ids)
        for tbl, key in (("usage_events", "usage"), ("memory_snapshots", "mem"),
                         ("failure_events", "fail")):
            n = conn.execute("SELECT COUNT(*) FROM %s WHERE account_id=? AND id>?" % tbl,
                             (acc, wm[key])).fetchone()[0]
            if n:
                conn.execute("DELETE FROM %s WHERE account_id=? AND id>?" % tbl, (acc, wm[key]))
                gone[tbl] = int(n)
        n = conn.execute("SELECT COUNT(*) FROM sessions WHERE account_id=? AND login_at>=?",
                         (acc, wm["ts"])).fetchone()[0]
        if n:
            conn.execute("DELETE FROM sessions WHERE account_id=? AND login_at>=?", (acc, wm["ts"]))
            gone["sessions"] = int(n)
        conn.commit()
    finally:
        conn.close()
    for rel in sorted(_snapshot_files() - wm["files"]):
        try:
            os.remove(os.path.join(ROOT, rel))
            gone.setdefault("files", []).append(rel)
        except OSError as e:  # noqa: BLE001
            print("  ⚠️ 删不掉（请手动看）: %s → %s" % (rel, e))
    return gone


def _ask(c: TestClient, token: str, question: str) -> tuple:
    t0 = time.time()
    for attempt in (1, 2):
        t = c.post("/api/chat/sessions", params={"token": token}).json()["thread_id"]
        r = c.post("/api/chat", params={"token": token, "thread_id": t, "question": question},
                   timeout=900)
        if r.status_code == 200:
            return 200, (r.json().get("answer") or ""), t, round(time.time() - t0, 1)
        print("     （第 %d 次返回 %s，%s）" % (attempt, r.status_code, r.text[:80]))
        time.sleep(20)
    return r.status_code, r.text[:200], "", round(time.time() - t0, 1)


def _calls_of_thread(account_id: int, thread_id: str) -> list:
    """按会话读回模型发出的调用（不采信审计窗口，会串别的进程 ✓）。"""
    if not thread_id:
        return []
    conn = get_personal_conn()
    try:
        rows = list(conn.execute(
            """SELECT m.tool_calls FROM messages m JOIN conversations v ON v.id = m.conversation_id
               WHERE v.account_id = ? AND v.thread_id = ?
                 AND m.tool_calls IS NOT NULL AND m.tool_calls != ''""",
            (account_id, thread_id)))
    finally:
        conn.close()
    out: list = []
    for r in rows:
        out.extend(parse_calls(r["tool_calls"]))
    return out


# ---------------------------------------------------------------- 评分表（人工填 ✓）
def _write_sheet(rows: list, ts: str) -> str:
    tag = " ｜ ⚠️ 过滤运行" if ONLY else (" ｜ 定向复跑" if IDS else "")
    lines = ["# M6b 加压探针 · 人工评分表（%s）%s" % (ts, tag), ""]
    if IDS:
        lines += ["## 本轮为定向复跑（题号 %s）" % IDS,
                  "> 只跑这几条，**不是完整题集**；判分口径与全量一致（脚本只记录序列、对错由你打分）。", ""]
    if ONLY:
        lines += ["## ⚠️ 这不是完整题集 ✗",
                  "> 本次带 `M5B_ONLY=%s` → **只跑了 %d 条**（全量 43 / 核心 16）✗" % (ONLY, len(rows)),
                  "> 它是**单条冒烟**用的，别当正式评分表 ✗。正式跑请先 `Remove-Item Env:M5B_ONLY`（PS）"
                  "/ `unset M5B_ONLY`（bash）✓", ""]
    lines += ["", "> 账号 `%s` ｜ 题集 `%s` ｜ 共 %d 条 ｜ **脚本不判对错 ✗ 只记录调用序列** ✓"
              % (USER, WANT_SET, len(rows)),
             "> 打分方式：在每题的 `评分：` 后写 `✓`（完全正确）/ `△`（部分正确，见备注）/ `✗`（错）",
             "> 被判为「未测」（429/5xx 限流等）的题建议留空或写 `未测` ✓ 别记成能力问题。", "",
             "## 一、速览（先扫这张表）", "",
             "| # | 问句 | 调用数 | 来源数 | 耗时(s) | 状态 | 评分 |", "|---|---|---|---|---|---|---|"]
    for r in rows:
        st = "未测(%s)" % r["status"] if r["unmeasured"] else str(r["status"])
        lines.append("| %d%s | %s | %d | %d | %s | %s |  |"
                     % (r["id"], "★" if r["core"] else "", r["q"][:34],
                        len(r["calls"]), len(r["sources"]), r["dur_s"], st))
    lines += ["", "## 二、逐题明细（这里打分）", ""]
    for r in rows:
        st = "**未测（%s）** ✗ 别当能力问题" % r["status"] if r["unmeasured"] else str(r["status"])
        lines += ["### #%d%s ｜ %s" % (r["id"], " ★核心" if r["core"] else "", r["q"]),
                  "- 参考链路：`%s`" % r["ref_chain"],
                  "- 路由：`%s` ｜ 来源数：%d ｜ 调用数：%d ｜ 耗时：%ss ｜ 状态：%s"
                  % (r["mode"], len(r["sources"]), len(r["calls"]), r["dur_s"], st)]
        if r["calls"]:
            lines.append("- 实际调用序列：")
            for i, c in enumerate(r["calls"], 1):
                lines.append("  %d. `%s`" % (i, c["brief"]))
            lines.append("  （去重工具：%s）" % "、".join("`%s`" % t for t in r["tools_used"]))
        else:
            lines.append("- 实际调用序列：**（一次工具都没调）**")
        if WITH_ANSWER and r.get("answer"):
            lines.append("- 回答摘录：")
            lines.append("  > %s" % str(r["answer"]).replace("\n", " ")[:180])
        lines += ["- 评分：", "- 备注：", ""]
    lines += ["---", "",
              "## 三、评分口径建议（可自行调整 ✓）",
              "- `✓`：链路完整且都是必要步骤（比如 12306 走了「取时间 → 取站点码 → 查票」）",
              "- `△`：结果对但路径冗余/绕路，或漏了可选步骤（备注里写清是什么）",
              "- `✗`：用错来源/漏关键步骤导致拿不到结果（备注里写清错在哪）",
              "- 未测（429/5xx/超时）**不计入能力评价** ✓",
              "",
              "## 四、回填方式",
              "填完把本文件路径发我 ✓ 我用 `tests/live/m6b_score_import.py` 解析成机器可读基线"
              "（`tests/fixtures/m5b_probe_baseline.json`）→ 以后重跑只比**调用序列有没有变** ✓",
              ""]
    os.makedirs(SHEET_DIR, exist_ok=True)
    tag = ts.replace("-", "").replace(":", "").replace(" ", "_")
    path = os.path.join(SHEET_DIR, "m6b_score_sheet%s_%s.md" % ("_only" if ONLY else "", tag))
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    if TO_DESKTOP and os.path.isdir(DESKTOP):
        try:
            import shutil
            dst = os.path.join(DESKTOP, os.path.basename(path))
            shutil.copyfile(path, dst)
            print("评分表已复制到桌面：%s" % dst)
        except OSError as e:  # noqa: BLE001
            print("⚠️ 复制到桌面失败：%s" % e)
    return path


# ---------------------------------------------------------------- 离线自检
def _selftest() -> int:
    j = json.dumps
    seq = j([{"name": "invoke_tool", "args": {"ref": "mcp:12306-mcp/get-current-date", "params": {}}},
             {"name": "invoke_tool",
              "args": {"ref": "mcp:12306-mcp/get-station-code-of-citys",
                       "params": {"citys": "成都|重庆"}}},
             {"name": "invoke_tool",
              "args": {"ref": "mcp:12306-mcp/get-tickets",
                       "params": {"date": "2026-10-03", "from": "成都", "to": "重庆"}}}])
    calls = parse_calls(seq)
    assert [c["target"] for c in calls] == [
        "mcp:12306-mcp/get-current-date", "mcp:12306-mcp/get-station-code-of-citys",
        "mcp:12306-mcp/get-tickets"], "调用顺序必须按原始顺序保留 ✗"
    assert calls[1]["source"] == "mcp:12306-mcp", "MCP 来源必须按 `/` 切 ✗"
    assert "成都|重庆" in calls[1]["brief"], "参数摘要必须带上（人工打分要看参数）✗"
    assert parse_calls("{坏 json") == [] and parse_calls("null") == [] and parse_calls("") == []
    cli_calls = parse_calls(j([{"name": "run_shell_command",
                                "args": {"command": "wechat-cli", "argv": ["history", "袁战"]}}]))
    assert cli_calls[0]["brief"] == "wechat-cli history 袁战", cli_calls
    assert cli_calls[0]["source"] == "cli:wechat-cli", cli_calls
    other = parse_calls(j([{"name": "网络搜索工具", "args": {"q": "x"}}]))
    assert other[0]["source"] == "builtin:网络搜索工具", other
    assert _source_of("api:steam#searchGame") == "api:steam"
    assert _source_of("mcp:12306-mcp/get-tickets") == "mcp:12306-mcp"
    # ★ 关键：**没有任何判定函数** ✓ 题集里只允许记录 + 参考链路
    assert not hasattr(sys.modules[__name__], "judge"), "v3 不该再有 judge() 自动判定 ✗"
    assert [it["id"] for it in Q] == list(range(1, 44)), "问句编号必须 1..43 连续 ✗"
    assert sum(1 for it in Q if it.get("core")) == 16, "核心集必须 16 条 ✗"
    covered = set()
    for it in Q:
        if it["exp"] and it["exp"]["kind"] == "tool":
            covered.add(it["exp"]["ref"])
        elif it["exp"]:
            covered.add("cli:" + it["exp"]["bin"])
    assert len(covered) == 28, "单工具题必须覆盖 28 个工具，实际 %d ✗" % len(covered)
    print("\n✅ 自检通过：调用序列/顺序/参数摘要/来源解析/无自动判定/编号/覆盖 28 全部正确 ✓"
          "（未调模型 ✓）")
    return 0


# ---------------------------------------------------------------- 主流程
def main() -> int:
    ensure_tables()
    if SKIP_LLM:
        return _selftest()

    todo = [it for it in Q if WANT_SET == "full" or it.get("core")]
    if IDS:
        want = [x.strip() for x in IDS.replace("，", ",").split(",") if x.strip()]
        todo = [it for it in Q if str(it["id"]) in want]
        miss = [x for x in want if not any(str(it["id"]) == x for it in todo)]
        if miss:
            print("✗ M5B_IDS 里有题号不存在：%s（本文件题号：%s）"
                  % (",".join(miss), ",".join(sorted((str(it["id"]) for it in Q), key=lambda v: int(v)))))
            return 2
        print("（M5B_IDS 定向复跑：%s → %d 条；跨 core/full 过滤）" % (IDS, len(todo)))
    if ONLY:
        hit = [it for it in todo if ONLY in it["q"] or ONLY in _ref_text(it) or ONLY in str(it["exp"])]
        if not hit and WANT_SET != "full":
            print("（M5B_ONLY 命中不在 core 集里，已自动扩到全量集 ✓）")
            hit = [it for it in Q if ONLY in it["q"] or ONLY in _ref_text(it) or ONLY in str(it["exp"])]
        todo = hit
        if not todo:
            print("✗ M5B_ONLY=%s 没匹配到任何问句" % ONLY)
            return 2
    ts = time.strftime("%F %T")
    print("探针集：%s ｜ 本轮 %d 条（全量 43 / 核心 16）｜ 模式：**只记录、不判对错** ✓"
          % (WANT_SET, len(todo)))
    if ONLY:
        print("")
        print("  " + "!" * 68)
        print("  ⚠️ 本次是**过滤运行**（M5B_ONLY=%s）→ 只跑了 %d 条，**不是完整题集** ✗" % (ONLY, len(todo)))
        print("  ⚠️ 正式评分请先清掉它：PowerShell `Remove-Item Env:M5B_ONLY` ／ bash `unset M5B_ONLY`")
        print("  " + "!" * 68)
    if IDS:
        print("")
        print("  " + "-" * 68)
        print("  ▸ 本轮为**定向复跑**（题号 %s）→ %d 条；**不是完整题集**，判分口径与全量一致" % (IDS, len(todo)))
        print("  " + "-" * 68)
    print("账号：%s（真实账号 ✓ 跑完按水位线清掉本轮新建数据 ✓）" % USER)

    acc = _account_id(USER)
    if not acc:
        print("✗ 账号不存在：%s" % USER)
        return 2
    ents = cp.pool_entries(acc)
    print("\n  能力池：%d 条（该账号真实配置）" % len(ents))
    check("能力池规模与问题集口径相符（≥28 条）", len(ents) >= 28, len(ents))

    modes = {}
    for it in todo:
        r = tr.route_tools(acc, it["q"])
        modes[it["id"]] = (r.mode, [str(x) for x in r.hits])

    wm = _watermark(acc)
    c = TestClient(server.app)
    token = c.post("/api/login", json={"username": USER, "password": PWD}).json().get("token")
    check("探针账号登录成功", bool(token))
    if not token:
        return 1

    rows = []
    print("\n[真机] 逐条记录（每条一问、独立新会话；只记调用序列 ✓）\n")
    for it in todo:
        mode, hits = modes[it["id"]]
        code, ans, tid, dur = _ask(c, token, it["q"])
        calls = _calls_of_thread(acc, tid)
        sources = sorted({x["source"] for x in calls if not x["source"].startswith("builtin:")})
        tools_used = []
        for x in calls:                                    # 去重但保持首次出现顺序 ✓
            if x["target"] not in tools_used:
                tools_used.append(x["target"])
        unmeasured = code in UNMEASURED_STATUS or (code != 200 and not calls)
        print("  #%-2d %-28s [%s] 调用 %d 次 ｜ 来源 %d ｜ %s s ｜ 状态 %s%s"
              % (it["id"], it["q"][:28], mode, len(calls), len(sources), dur, code,
                 "  ⚠️ 未测（限流/故障）" if unmeasured else ""))
        for i, x in enumerate(calls, 1):
            print("       %d. %s" % (i, x["brief"][:110]))
        if not calls:
            print("       （一次工具都没调）")
        rows.append(dict(id=it["id"], q=it["q"], kind=it.get("kind") or "single",
                         core=bool(it.get("core")), mode=mode, hits=hits,
                         ref_chain=_ref_text(it), ref_exp=it["exp"],
                         calls=calls, sources=sources, tools_used=tools_used,
                         n_calls=len(calls), dur_s=dur, status=code, unmeasured=unmeasured,
                         answer=(ans or "")[:600]))

    out = RESULT if not ONLY else RESULT.replace(".json", "_only.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(dict(ts=ts, user=USER, probe_set=WANT_SET, only=ONLY or None,
                       count=len(rows), scoring="manual（脚本不判对错 ✓）", rows=rows),
                  f, ensure_ascii=False, indent=2)
    print("\n原始记录已存：%s" % out)
    sheet = _write_sheet(rows, ts)
    print("人工评分表：%s" % sheet)
    slow = sorted([r for r in rows if r["dur_s"]], key=lambda r: -r["dur_s"])[:3]
    if slow:
        print("最慢三条：#%s" % "、".join("#%d(%ss)" % (r["id"], r["dur_s"]) for r in slow))
    print("未测（429/5xx/超时）：%s"
          % ([r["id"] for r in rows if r["unmeasured"]] or "无 ✓"))
    print("（本轮**不做任何通过/失败判定** ✓ 对错以你的评分表为准）")

    if KEEP:
        print("\n（M5B_KEEP=1 → 跳过清理 ✓ 本轮产物保留）")
    else:
        print("\n清理本轮产物：%s" % (_cleanup_run(wm) or "无需清理 ✓"))
    bad = [n for ok, n in OPS if not ok]
    print("\n" + "=" * 60)
    print("运维检查（与题目对错无关 ✓）：通过 %d / 失败 %d" % (len(OPS) - len(bad), len(bad)))
    for n in bad:
        print("  -", n)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
