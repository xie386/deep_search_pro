"""工具池测评（v4.0 大点 03）：路由与业务逻辑。

对应前端「🧩 技能与工具 → 🧪 工具池测评」第 6 个页签。**与主 Agent 体系无关** ✓：
出题 / 判分 / 建议三段都是独立小 Agent（`tools/probe_agent.py`），执行是产品模块（`tools/probe_runner.py`）。

按钮 → 接口（一一对应，见 `docs/v4.0/v4.0规划文档.md` §03 ③B/③C）：
    本账号工具池 + 数据大屏  → GET  /api/probe/overview
    历史打分表版本列表        → GET  /api/probe/sheets
    阅读某一份打分表          → GET  /api/probe/sheet
    🧪 生成真机题目（LLM）    → POST /api/probe/gen_cases      （产出先给用户过目 = 闸门 1）
    保存用户改过的题目        → POST /api/probe/save_cases
    读当前题集（供编辑）      → GET  /api/probe/cases
    ▶ 开始测评（真机跑）      → POST /api/probe/run            （后台线程；额度 tip 由前端提示）
    轮询进度（独立通道 ✓）    → GET  /api/probe/status         （不碰主 Agent 的 WS 语义）
    🧮 判分（Agent 预填）     → POST /api/probe/judge
    💡 词法建议（LLM）        → POST /api/probe/advise
    📥 写入大屏（人工终判后） → POST /api/probe/commit         （出打分表 + 覆盖式写指标）

★ 关键纪律（与规划文档 §03 的 ③E/③F/③I 一致）：
    · 判分只**预填**，`commit` 里带的是**人工终判**的分数 —— 指标只认它；
    · 打分表是**唯一保留的产物**（`output/{用户名}/probe_打分表*.md`），其余按水位线清理；
    · 指标表**每账号一行**（UPSERT），不做历史行、不做回退。
"""
from __future__ import annotations

import os
import threading
import time

from fastapi import HTTPException, status as http_status

from api.account import get_session
from tools import probe_agent as pa
from tools import probe_runner as pr

# 每账号同时只允许一轮测评（键 = account_id）
_RUNS: dict = {}
_LOCK = threading.Lock()


def _ctx(token: str) -> dict:
    sess = get_session(token)
    return {"account_id": int(sess.get("account_id") or 0),
            "username": str(sess.get("username") or "")}


def _acc(token: str) -> int:
    aid = _ctx(token)["account_id"]
    if not aid:
        raise HTTPException(http_status.HTTP_400_BAD_REQUEST, "缺少账号")
    return aid


# ============================================================ 概览 / 数据大屏 =======
def overview(token: str) -> dict:
    """本账号工具池逐条（含体检灯）+ 最新一轮指标（数据大屏）+ 历史份数。"""
    c = _ctx(token)
    pool = pr.pool_overview(c["account_id"])
    metrics = pr.get_metrics(c["account_id"])
    sheets = pr.list_sheets(c["username"])
    return {
        "pool_version": pool["pool_version"],
        "pool_size": pool["pool_size"],
        "items": pool["items"],
        "health": pool.get("health") or {},
        # ★ 体检汇总（与 capability_lint 同一口径）：前端「体检待补 / 仅自动摘要 / 关键词仅派生」三个计数
        "problem_count": pool.get("problem_count", 0),
        "soft_count": pool.get("soft_count", 0),
        "auto_only": pool.get("auto_only", 0),
        "no_desc": pool.get("no_desc", 0),
        "derived_kw": pool.get("derived_kw", 0),
        # ★ 当前生效的**测评模型名**（只回名字，绝不含 base_url / key ✓）——
        #   前端把它显示在顶部提示条里，用户不用去翻 .env 就知道测评在用哪个模型。
        #   ★ 一律走 `probe_agent.probe_model()`（**唯一权威**），不在接口层另写一套解析
        #     —— 判分可单独配 `PROBE_LLM_JUDGE_MODEL`，接口自造解析必然与出题侧跑偏。
        "probe_model": _model_name(""),
        "probe_judge_model": _model_name("judge"),
        "probe_judge_split": _model_name("judge") != _model_name(""),
        # ★ 单题墙钟上限（秒）——前端提示条要显示，用户才知道「卡住会自动中断」✓
        #   2026-10-11：实测某题连翻 11 页跑了 4 分钟，必须有上限
        "ask_timeout": int(pr.ask_timeout()),
        "metrics": metrics,
        "sheet_count": len(sheets),
        "latest_sheet": sheets[0]["name"] if sheets else None,
        "running": bool((_RUNS.get(c["account_id"]) or {}).get("running")),
    }


def _model_name(kind: str = "") -> str:
    """测评模型名（**只取名字**；出题/判分解析逻辑唯一权威 = `probe_agent.probe_model`）。

    解析失败（如缺依赖）时返回空串 —— 概览接口不该因为读不到模型名而 500 ✓
    """
    try:
        return str(getattr(pa.probe_model(kind), "model_name", "") or "")
    except Exception:  # noqa: BLE001
        return ""


def list_sheets(token: str) -> dict:
    """历史打分表版本列表（**严格格式过滤**：`output/` 下其余文件一律不进列表 ✓）。"""
    return {"sheets": pr.list_sheets(_ctx(token)["username"])}


def read_sheet(name: str, token: str) -> dict:
    """读一份打分表（路径防护在 `probe_runner.sheet_path` 内 ✓）。"""
    try:
        text = pr.read_sheet(_ctx(token)["username"], name)
    except ValueError as e:
        raise HTTPException(http_status.HTTP_400_BAD_REQUEST, str(e))
    except FileNotFoundError:
        raise HTTPException(http_status.HTTP_404_NOT_FOUND, "这份打分表不在了")
    return {"name": name, "md": text}


# ============================================================ 出题（闸门 1 之前）===
def gen_cases(token: str, *, model=None) -> dict:
    """🧪 生成真机题目：**后台线程 + 分批出题**，进度走 `status` 的 `gen` 字段（独立通道 ✓）。

    ★ 2026-10-10 改（用户实测**两次出题失败**、且看不到 Agent 在干什么）：
      ① 出题核心已**分批**（每批 6 个工具一次调用）→ 输出短、JSON 稳；
      ② 改成**后台任务 + 进度**：前端能显示「正在生成 单工具题·第 3/5 批：某某工具…」；
      ③ 失败时 `gen.error` 带**阶段名与可操作建议**，且模型的**原始输出已打印进服务日志**；
      ④ 结果（doc/errors）放在 `gen` 里，前端轮询取走后自行进「闸门 1：题目过目」。
    `errors` 非空 = 这份题集**还不合格**（每个 ref 至少 1 题、ref 必须逐字来自池、噪声题不许有期望…），
    前端应挡在执行之前。
    """
    c = _ctx(token)
    aid = c["account_id"]
    if not aid:
        raise HTTPException(http_status.HTTP_400_BAD_REQUEST, "缺少账号")
    with _LOCK:
        cur = _RUNS.get(aid) or {}
        if (cur.get("gen") or {}).get("running"):
            raise HTTPException(http_status.HTTP_409_CONFLICT, "本账号已有一轮出题在跑，等它结束")
        prev = pa.load_cases(c["username"])
        prev_qs = [x.get("q") for x in (prev.get("cases") or [])]
        _RUNS[aid] = dict(cur, gen={"running": True, "stage": "", "name": "准备中", "done": 0,
                                    "total": 0, "detail": "", "error": None, "doc": None,
                                    "errors": None, "count": 0, "started": time.time()})

    def _prog(stage, name, done, total, detail):
        st = _RUNS.get(aid) or {}
        g = dict(st.get("gen") or {})
        g.update({"stage": stage, "name": name, "done": done, "total": total, "detail": detail})
        st["gen"] = g
        _RUNS[aid] = st

    def _work():
        try:
            out = pa.gen_cases(aid, c["username"], prev_questions=prev_qs, model=model,
                               on_progress=_prog)
            st = _RUNS.get(aid) or {}
            g = dict(st.get("gen") or {})
            g.update({"doc": out["doc"], "errors": out["errors"], "count": out["count"],
                      "pool_size": out["pool_size"], "multi": out["multi"],
                      "warnings": out.get("warnings") or [], "failed": out.get("failed") or 0,
                      "concurrency": out.get("concurrency") or 1,
                      "salvaged": bool(out.get("salvaged"))})
            st["gen"] = g
            _RUNS[aid] = st
        except Exception as e:  # noqa: BLE001
            st = _RUNS.get(aid) or {}
            g = dict(st.get("gen") or {})
            g.update({"error": "%s: %s" % (type(e).__name__, e)})
            st["gen"] = g
            _RUNS[aid] = st
        finally:
            st = _RUNS.get(aid) or {}
            g = dict(st.get("gen") or {})
            g.update({"running": False, "finished": time.time()})
            st["gen"] = g
            _RUNS[aid] = st

    threading.Thread(target=_work, daemon=True).start()
    return {"started": True}


def save_cases(doc: dict, token: str) -> dict:
    """保存（用户过目/改过的）题集 → `user_data/probe_sets/<用户名>.json`（产品数据 ✓ 裁定 13；2026-10-13 归口）。

    ★ 2026-10-11 用户实测（保存被拒、且错误提示飘在视口外）：
      `expect = {"kind":"tool","ref":"bili search"}` 这类「CLI 名 + 子命令」写法会被校验器拦死
      （`ref 必须 api:/mcp: 开头`）→ **题集根本存不进磁盘**。
      → 保存前先跑一遍**与出题同款**的归一（`normalize_expects` / `normalize_refs_and_alts`），
        认不出的仍由校验器报错（**不猜** ✓）；改动条数回给前端显示，用户能看见改了什么。
    """
    c = _ctx(token)
    doc = dict(doc or {})
    cases = [x for x in (doc.get("cases") or []) if isinstance(x, dict)]
    doc["cases"] = cases
    normalized = 0
    try:
        from tools import probe_agent as _pa
        _cards, ents = _pa.card_block(c["account_id"])      # 池条目（与出题/体检**同源** ✓）
        normalized += _pa.normalize_expects(cases, ents)
        _na = _pa.normalize_refs_and_alts(cases, ents)
        normalized += int(_na.get("refs") or 0) + int(_na.get("alts") or 0)
    except Exception as _e:                                 # noqa: BLE001 - 归一失败不挡保存（交给校验器说）
        print("[probe] save_cases 归一跳过：%s: %s" % (type(_e).__name__, _e))
    errs = pr.validate_case_set(doc)
    if errs:
        raise HTTPException(http_status.HTTP_400_BAD_REQUEST, "题集不合法：%s" % "；".join(errs))
    # ★ 2026-10-12（裁定 38）：保存时**按当前题面现算**「没有具体对象」自检 ——
    #   这样用户改完题面再存，题单上方的琥珀条就跟着更新（旧题集没有这字段也不会漏提醒 ✓）
    try:
        from tools import probe_agent as _pa2
        doc["vague"] = _pa2.vague_check(cases)
    except Exception as _e:                                 # noqa: BLE001 - 自检失败不挡保存
        print("[probe] save_cases 无对象自检跳过：%s: %s" % (type(_e).__name__, _e))
    path = pa.save_cases(c["username"], doc)
    return {"ok": True, "count": len(cases), "path": os.path.basename(path),
            "normalized": normalized}


def get_cases(token: str) -> dict:
    """读当前题集（前端展示 / 编辑用）。没有则返回空。"""
    doc = pa.load_cases(_ctx(token)["username"])
    # ★ 2026-10-12（裁定 38）：旧题集没有 `vague` 字段 → **按当前题面现算一次**再返回，
    #   这样用户一打开旧题集就能看到「哪几道没有具体对象」（不必重新出题 ✓）
    if doc and not isinstance(doc.get("vague"), dict):
        try:
            from tools import probe_agent as _pa3
            doc = dict(doc)
            doc["vague"] = _pa3.vague_check(doc.get("cases") or [])
        except Exception as _e:                             # noqa: BLE001 - 自检失败不影响读题集
            print("[probe] get_cases 无对象自检跳过：%s: %s" % (type(_e).__name__, _e))
    return {"doc": {k: v for k, v in doc.items() if k != "_path"} if doc else {},
            "count": len(doc.get("cases") or [])}


# ============================================================ 执行（后台线程 + 轮询）==
def run(token: str, *, want: str = "full", ids=None, set_name: str | None = None) -> dict:
    """▶ 开始测评：**后台线程**跑（daemon），进度由 `status` 轮询（独立通道 ✓）。

    同一账号已有在跑的一轮时**拒绝**（避免两轮互相污染水位线）。
    额度消耗由前端在点击前提示（「对网络搜索额度消耗较大」，用户 2026-10-09 裁定）。
    """
    c = _ctx(token)
    aid = c["account_id"]
    if not aid:
        raise HTTPException(http_status.HTTP_400_BAD_REQUEST, "缺少账号")
    with _LOCK:
        cur = _RUNS.get(aid)
        if cur and cur.get("running"):
            raise HTTPException(http_status.HTTP_409_CONFLICT, "本账号已有一轮测评在跑，等它结束")
        doc = pa.load_cases(c["username"])
        if not doc:
            raise HTTPException(http_status.HTTP_400_BAD_REQUEST, "还没有题集 —— 先「生成真机题目」")
        cases = pr.cases_for(doc, want, ids=ids)
        if not cases:
            raise HTTPException(http_status.HTTP_400_BAD_REQUEST, "选不出题目（题集为空？）")
        _RUNS[aid] = {"running": True, "i": 0, "total": len(cases), "started": time.time(),
                      "q": "", "error": None, "raw_path": None, "username": c["username"],
                      "set_name": doc.get("name") or "inline"}

    def _progress(i, total, case):
        st = _RUNS.get(aid) or {}
        st.update({"i": i, "total": total, "q": str(case.get("q") or "")[:60]})
        _RUNS[aid] = st

    def _work():
        try:
            out = pr.run_cases(aid, c["username"], cases, set_doc=doc, on_progress=_progress,
                               token=token)
            st = _RUNS.get(aid) or {}
            st.update({"raw_path": out.get("raw_path"), "profile": out.get("profile"),
                       "gone": out.get("gone"), "stopped": out.get("stopped") or ""})
            _RUNS[aid] = st
        except Exception as e:  # noqa: BLE001
            st = _RUNS.get(aid) or {}
            st.update({"error": "%s: %s" % (type(e).__name__, e)})
            _RUNS[aid] = st
        finally:
            st = _RUNS.get(aid) or {}
            st.update({"running": False, "finished": time.time()})
            _RUNS[aid] = st

    threading.Thread(target=_work, daemon=True).start()
    return {"started": True, "total": len(cases), "set_name": doc.get("name") or "inline"}


def status(token: str) -> dict:
    """轮询进度（独立通道，不动主 Agent 的 WS 语义 ✓）。

    同时返回两件事：本轮**测评**（`running/i/total/q`）与**出题**（`gen` 子对象：
    `stage/name/done/total/detail/error/doc/errors`，见 `gen_cases`）。
    """
    aid = _acc(token)
    st = dict(_RUNS.get(aid) or {})
    if not st:
        return {"running": False, "total": 0, "i": 0, "gen": {}}
    st["elapsed"] = round(time.time() - float(st.get("started") or time.time()), 1)
    st.pop("started", None)
    g = dict(st.get("gen") or {})
    if g:
        g["elapsed"] = round(time.time() - float(g.get("started") or time.time()), 1)
        g.pop("started", None)
        st["gen"] = g
    return st


# ============================================================ 判分 / 建议 / 归档 ====
def _load_run_for(token: str, raw_path: str | None) -> dict:
    """取本轮原始记录：优先用传入路径（必须落在本账号 sandbox 内 ✓ 防穿越），否则取最近一轮。"""
    c = _ctx(token)
    if raw_path:
        base = os.path.abspath(os.path.join(pr.SANDBOX_DIR, c["username"]))
        full = os.path.abspath(os.path.join(base, os.path.basename(raw_path)))
        if not full.startswith(base) or not os.path.isfile(full):
            raise HTTPException(http_status.HTTP_400_BAD_REQUEST,
                                "找不到这一轮的原始记录（它是临时产物，可能已被清理、或已换了新的一轮）"
                                "—— 请重跑一次「▶ 开始测评」再判分")
        return pr.load_run(full)
    st = _RUNS.get(c["account_id"]) or {}
    if st.get("raw_path") and os.path.isfile(st["raw_path"]):
        return pr.load_run(st["raw_path"])
    d = os.path.join(pr.SANDBOX_DIR, c["username"])
    if os.path.isdir(d):
        # ★ 2026-10-11：必须用**严格** RAW_RE 匹配 —— 原来只判 `startswith("probe_run_")`，
        #   于是 `probe_run_xxx.json.e2e_hidden` / `.bak` / 半成品临时件都会被当成「本轮记录」
        #   送去判分（用户真机跑过一轮后，这个宽松匹配立刻露馅：e2e 守卫被残留喂中 ✗）
        files = sorted((f for f in os.listdir(d) if pr.RAW_RE.match(f)), reverse=True)
        if files:
            return pr.load_run(os.path.join(d, files[0]))
    raise HTTPException(http_status.HTTP_400_BAD_REQUEST, "没有可判分的测评记录 —— 先跑一轮")


def judge(req: dict, token: str) -> dict:
    """🧮 判分（**Agent 预填**）：逐题给 ✓/△/✗/未测 + 理由 + 置信。人工再二次打分做终判。"""
    run = _load_run_for(token, (req or {}).get("raw_path"))
    try:
        out = pa.judge_run(run)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(http_status.HTTP_502_BAD_GATEWAY, "判分失败：%s: %s" % (type(e).__name__, e))
    rows = [{"id": r.get("id"), "q": r.get("q"), "kind": r.get("kind"),
             "expect": pr.expected_brief(r.get("expect")),
             "calls": [c["brief"] for c in (r.get("calls") or [])],
             "unmeasured": bool(r.get("unmeasured"))} for r in (run.get("rows") or [])]
    return {"scores": {str(k): v for k, v in (out["scores"] or {}).items()}, "rows": rows,
            "run": {k: run.get(k) for k in ("ts", "set_name", "pool_version", "pool_size",
                                            "count", "elapsed_s", "ext_retrievals", "raw_path",
                                            "profile", "gone")}}


def advise(req: dict, token: str) -> dict:
    """💡 词法建议（只建议改素材 ✓）：输入 = 素材 + 本轮逐题结果 + 静态体检硬规则结论。"""
    c = _ctx(token)
    req = req or {}
    run = _load_run_for(token, req.get("raw_path"))
    scores = {int(k): v for k, v in (req.get("scores") or {}).items()}
    try:
        out = pa.advise(c["account_id"], run, scores)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(http_status.HTTP_502_BAD_GATEWAY, "建议生成失败：%s: %s" % (type(e).__name__, e))
    return {"text": out["text"]}


def commit(req: dict, token: str) -> dict:
    """📥 写入大屏：人工终判完 → 出**打分表**（唯一保留产物）+ **覆盖式**写指标（每账号一行）。

    请求体：`scores`（人工终判，`{题号: {verdict, note}}`）、可选 `raw_path`、可选 `advisor`（建议文本，一并写进打分表）。
    """
    c = _ctx(token)
    req = req or {}
    run = _load_run_for(token, req.get("raw_path"))
    scores = {int(k): v for k, v in (req.get("scores") or {}).items()}
    metrics = pr.build_metrics(run, scores)
    name = pr.new_sheet_name()
    metrics["sheet_name"] = name
    text = pr.build_sheet(run, scores=scores, metrics=metrics, advisor=req.get("advisor"))
    path = pr.write_sheet(c["username"], text, name)
    row = pr.commit_metrics(c["account_id"], metrics)
    return {"ok": True, "sheet": name, "path": os.path.relpath(path, os.path.dirname(pr.OUTPUT_DIR)),
            "metrics": row}
