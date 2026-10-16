"""工具池测评执行器（v4.0 大点 03 产品模块，2026-10-10 立）。

职责：把一份**题集 JSON** 在某个账号上跑一遍，**只记录、不判对错**（沿用 v3.1 探针制度 ✓）。

落位理由（见 `docs/v4.0/Verdict/03-….md` 裁定 9）：执行核心必须在**服务进程可导入的产品模块**里，
`tests/live/m5b_pressure_probe.py` 保留为 CLI 薄壳（`M5B_*` 环境变量接口与 `SKIP_LLM=1` 自检不变）。

**沿用的既有资产**（形态与口径逐条未改 ✓）：
    · `parse_calls`（调用序列解析，纯函数）      · `_source_of`（ref → 来源）
    · `ask`（一条一问、独立新会话）              · `calls_of_thread`（从库里读真值，不采信模型自述）
    · `watermark` / `cleanup_run`（水位线清理：只删本轮新建、且属于本账号的行与文件）
    · 评分表形态（`评分：` 由人工填 ✓ 脚本不判定 ✗）

**本模块新增/不同的地方**：
    1. 题集来自 JSON（`tests/fixtures/probe_sets/<name>.json`），不再写死；
    2. 快照文件按用户裁定命名 `probe_打分表YYYYMMDD_HHMMSS.md`，落 `output/{username}/`；
       `snapshot_files()` **跳过**该前缀（裁定 6-⑤：清理时按前缀排除）；
    3. 执行与判分**分离**：执行只把「原始记录」写进 `user_data/probe_artifacts/{username}/probe_run_*.json`
       （该目录本来就在清理清单里 ✓ 不新建目录）；判分完成后由 `write_sheet()` 产出正式打分表，
       临时产物随之被清理（用户裁定：「保留打完分的打分表…其余产物删除」）；
    4. `build_sheet()` 产出三段式快照（① 该版本有哪些工具 ② 该版本的工具描述词法 ③ 最终数据），
       末尾附一段机器可读 JSON（判分与大屏用，免去解析 md）。
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# ★ 2026-10-13（大点 04 · 议题一）：用户数据路径全部改问 `utils/paths.py`（唯一事实源）
from utils import paths as _upaths                                              # noqa: E402
OUTPUT_DIR = str(_upaths.OUTPUT_DIR)
# ★ 06：测评产物**不再写进沙箱**（沙箱白盒化后它们会摊在用户眼前；它们只是测评的临时产物）。
#   根目录换成 user_data/probe_artifacts/（与沙箱并列）。
ARTIFACTS_DIR = str(_upaths.PROBE_ARTIFACTS)
SET_DIR = os.path.join(ROOT, "tests", "fixtures", "probe_sets")

# ---- 快照文件（用户 2026-10-10 定的命名与过滤口径）---------------------------------
SHEET_PREFIX = "probe_打分表"
SHEET_RE = re.compile(r"^probe_打分表\d{8}_\d{6}\.md$")
# ★ 本轮**原始记录**（`user_data/probe_artifacts/{用户名}/probe_run_*.json`）：**判分 / 词法建议 / 写入大屏**都要读它。
#   2026-10-11 用户真机报「判分失败：找不到这一轮的原始记录」—— 根因就是水位线清理把刚写完的它
#   当「本轮新产物」删了（清理在本轮跑完**那一刻**执行，而判分在其**之后**）。
#   现在它不参与水位线清理 ✓，生命周期改由 `_prune_raw()` 管（只留最近一份）。
RAW_RE = re.compile(r"^probe_run_\d{8}_\d{6}\.json$")
SHEET_GLOB = SHEET_PREFIX + "*.md"

# ---- 未测状态（限流/故障，不计入能力评价 ✓）----------------------------------------
UNMEASURED_STATUS = (408, 429, 500, 502, 503, 504)
# 单题墙钟上限（秒）：超时就中断这一轮并记「未测」。审计日志取证：模型会对某题**反复翻页**
# （实测一题连跑 4 分钟、`bili favorites --page 1..11`），一轮几十题会被拖死 ✗
ASK_TIMEOUT_DEFAULT = 180


# ★ 2026-10-13：测评请求会强制关决策层（`probe=1`），这行提示只打一次，避免每道题刷屏
_EVAL_NOTE_PRINTED = False


def ask_timeout() -> float:
    """单题上限（秒）—— 每次现读 `.env`，别在 import 时定死（脚本/服务加载时机不同 ✓）。"""
    try:
        from dotenv import find_dotenv, load_dotenv
        load_dotenv(find_dotenv())
    except Exception:  # noqa: BLE001
        pass
    try:
        return float(os.getenv("PROBE_ASK_TIMEOUT") or ASK_TIMEOUT_DEFAULT)
    except (TypeError, ValueError):
        return float(ASK_TIMEOUT_DEFAULT)

# ---- 清理目标（同探针；`output/%s` 下**只删非打分表**的文件，见裁定 6-⑤）-----------
# ★ 由唯一事实源生成（此前写死 4 项、与探针/清理各存一份副本，见 research/04 §6.1(b)）
ARTIFACT_DIRS = tuple(str((_p / "%s").relative_to(ROOT)).replace(os.sep, "/")
                      for _p in _upaths.PROBE_ARTIFACT_DIRS)

# ---- 额度：只有 retrieval_external 有额度成本（见 usage_events 的 kind 分类）--------
KIND_RETRIEVAL_EXTERNAL = "retrieval_external"

KINDS = ("single", "multi", "noise")


# ============================================================ 纯函数（题集 / 解析）===
def load_case_set(path_or_name: str) -> dict:
    """读一份题集 JSON（可传完整路径，或只传文件名 / 名字）。

    查找顺序：① 原样路径 → ② `tests/fixtures/probe_sets/`（开发题集，随仓库走）
              → ③ `user_data/probe_sets/`（**按账号生成**的题集，产品数据、不随仓库走 ✓ 裁定 13；
                2026-10-13 议题一：由 `data/probe_sets/` 归口到此，路径一律问 `utils/paths.py`）。
    """
    path = path_or_name
    if not os.path.isfile(path):
        cands = [os.path.join(SET_DIR, path_or_name),
                 str(_upaths.PROBE_SETS_DIR / path_or_name)]
        cands = [c if c.endswith(".json") else c + ".json" for c in cands]
        path = next((c for c in cands if os.path.isfile(c)), cands[0])
    with open(path, "r", encoding="utf-8") as f:
        doc = json.load(f)
    errs = validate_case_set(doc)
    if errs:
        raise ValueError("题集不合法：%s" % "；".join(errs))
    doc["_path"] = path
    return doc


def validate_case_set(doc: dict) -> list:
    """题集体检（**硬规则**，与出题 Agent 的约束一致）。返回错误列表，空 = 通过。"""
    errs: list = []
    if not isinstance(doc, dict):
        return ["顶层必须是对象 ✗"]
    cases = doc.get("cases")
    if not isinstance(cases, list) or not cases:
        return ["cases 必须是非空数组 ✗"]
    seen_ids, seen_qs = set(), set()
    for i, c in enumerate(cases):
        tag = "第 %d 条" % (i + 1)
        if not isinstance(c, dict):
            errs.append("%s 不是对象 ✗" % tag)
            continue
        cid = c.get("id")
        if not isinstance(cid, int):
            errs.append("%s 缺整数 id ✗" % tag)
        elif cid in seen_ids:
            errs.append("id 重复：%s ✗" % cid)
        else:
            seen_ids.add(cid)
        q = str(c.get("q") or "").strip()
        if len(q) < 3:                      # 下限 3：合法短题如「你是谁」必须放过 ✓
            errs.append("%s 题面太短/为空 ✗" % tag)
        elif q in seen_qs:
            errs.append("%s 题面与前面重复 ✗" % tag)
        else:
            seen_qs.add(q)
        kind = c.get("kind") or "single"
        if kind not in KINDS:
            errs.append("%s kind 非法（%s）✗" % (tag, kind))
        # 期望：noise 必须为 None；其余必须有 exp
        exp = c.get("expect")
        if kind == "noise":
            if exp:
                errs.append("%s 噪声题不该有期望工具 ✗" % tag)
        elif not exp:
            errs.append("%s 单/多工具题缺 expect ✗" % tag)
        elif exp.get("kind") == "cli":
            if not exp.get("bin"):
                errs.append("%s expect.kind=cli 但缺 bin ✗" % tag)
        elif exp.get("kind") == "tool":
            if not str(exp.get("ref") or "").startswith(("api:", "mcp:")):
                errs.append("%s expect.ref 必须 api:/mcp: 开头（%s）✗" % (tag, exp.get("ref")))
        else:
            errs.append("%s expect.kind 非法（%s）✗" % (tag, exp.get("kind")))
        # `refs`（多工具题用：涉及的**套装工具**清单）与 `alts`（备选可接受答案）—— 只做**类型**检查；
        # 「refs 是否来自**不同套装**」是**池相关**规则，由出题侧 `probe_agent.multi_violations` 判定 ✓
        refs = c.get("refs")
        if refs is not None:
            if not (isinstance(refs, list) and all(isinstance(x, str) for x in refs)):
                errs.append("%s refs 必须是字符串数组 ✗" % tag)
            elif kind == "multi" and len([x for x in refs if str(x).strip()]) < 2:
                errs.append("%s 多工具题 refs 少于 2 个（无法验证跨套装）✗" % tag)
        alts = c.get("alts")
        if alts is not None:
            if not isinstance(alts, list):
                errs.append("%s alts 必须是数组 ✗" % tag)
            elif len(alts) > 5:
                errs.append("%s alts 最多 5 个（备选是兜底，不是堆答案）✗" % tag)
    return errs


def cases_for(doc: dict, want: str = "full", ids=None) -> list:
    """选题：`want=core` 只取核心集；`ids` 给定时按题号定向复跑。"""
    cases = list(doc.get("cases") or [])
    if ids:
        want_ids = {int(x) for x in ids}
        return [c for c in cases if c.get("id") in want_ids]
    if str(want).lower() == "core":
        return [c for c in cases if c.get("core")]
    return cases


def parse_calls(tool_calls_json) -> list:
    """把一条 assistant 消息的 `tool_calls` 解析成统一结构（纯函数 ✓，搬自探针）。

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
                        "source": source_of(ref),
                        "brief": "%s %s" % (ref, brief[:100])})
        else:
            out.append({"name": name, "target": name, "argv": [], "params": a,
                        "source": "builtin:%s" % name, "brief": name})
    for c in out:
        # ★ 必须转义换行（模型偶尔吐畸形参数，换行会把表里的编号行拆断 ✓ 探针踩过）
        c["brief"] = str(c["brief"]).replace("\r", "").replace("\n", "\\n")
    return out


def source_of(ref: str) -> str:
    """ref → 来源：`api:<服务>#<操作>` 取 `#` 前；`mcp:<服务>/<工具>` 取 `/` 前 ✓。"""
    ref = str(ref or "")
    if "#" in ref:
        return ref.split("#")[0]
    if "/" in ref:
        return ref.split("/")[0]
    return ref


def ref_text(case: dict) -> str:
    """参考链路的兜底文案（题面没写 chain 时用期望工具 ✓ 仅供人工对照）。"""
    if case.get("chain"):
        return str(case["chain"])
    exp = case.get("expect")
    if not exp:
        return "不调用任何工具"
    return ("%s %s" % (exp["bin"], " ".join(exp.get("argv") or []))) if exp["kind"] == "cli" \
        else str(exp["ref"])


def expected_brief(exp) -> str:
    """期望工具的短文案（写进打分表给人工对照）。"""
    if not exp:
        return "（不调用工具）"
    if exp.get("kind") == "cli":
        s = ("%s %s" % (exp["bin"], " ".join(exp.get("argv") or []))).strip()
    else:
        s = str(exp.get("ref") or "")
    alts = exp.get("alts") or []
    if alts:
        s += " ／ 可接受：%s" % "、".join(" ".join(a) if isinstance(a, list) else str(a) for a in alts)
    return s


# ============================================================ 账号 / 池 / 用量 =======
def _conn():
    from tools.schema_personal import get_personal_conn
    return get_personal_conn()


def account_by_username(username: str) -> dict:
    conn = _conn()
    try:
        row = conn.execute("SELECT * FROM accounts WHERE username=?", (username,)).fetchone()
        return dict(row) if row else {}
    finally:
        conn.close()


def account_by_id(acc_id: int) -> dict:
    conn = _conn()
    try:
        row = conn.execute("SELECT * FROM accounts WHERE id=?", (int(acc_id),)).fetchone()
        return dict(row) if row else {}
    finally:
        conn.close()


def pool_version(acc_id: int) -> int:
    conn = _conn()
    try:
        row = conn.execute("SELECT pool_version FROM tool_pool_meta WHERE account_id=?",
                           (int(acc_id),)).fetchone()
        return int(row[0]) if row else 0
    finally:
        conn.close()


def pool_overview(acc_id: int) -> dict:
    """该账号工具池概况（前端「工具池概览」块 + 打分表第一节共用）。

    每条含 ref / name / 来源 / 有无 keywords（含**来源**）/ 描述**三态** / **体检结论**。

    ★ 体检口径 = **完全复用 `capability_lint`**（素材体检的唯一权威），本函数不再自定标准。
      2026-10-10 实测踩到：我曾按「`abilities_user` 是否为空」判「有无描述」，结果把 4 个 CLI
      （描述按项目约定存在 `abilities` 列、**创建时必写**）与 12306 的 `get-station-by-telecode`
      （只有服务自带的自动摘要）全报成「描述 缺」，而素材体检那边判定它们**一条问题都没有** ——
      同一份数据两套标准，用户一眼就看出来两个模块对不上。
    描述三态（**仅供内部判定**）：`user` 手写（`abilities_user`）/ `auto` 只有自动摘要（`abilities`）/
    `none` 都没有。★ 用户 2026-10-10 裁定：**界面不做「手写/自动」分类标签**，只在 `none` 时报
    「缺乏描述」（关键词同理，取不到任何词才报「缺乏关键词」）—— 判定标准仍与素材体检一致，
    但不给用户增加无用的分类信息。
    关键词按**池子的真实取用优先级**（keywords 列 → 描述里的「关键词：」行 → 来源级描述 → 派生）
    给出，并标出 `derive` 这一弱态（弱态本身不报红，交由素材体检的 soft 提示呈现）。
    """
    from tools import capability_pool as cp
    from tools import capability_lint as lint

    ents = cp.pool_entries(int(acc_id))
    try:
        rows = lint.load_rows(int(acc_id))
        issues = lint.check_rows(rows)          # 权威体检结论（dict 列表，含 code/msg/severity）
        health = lint.health(int(acc_id))
    except Exception as e:  # noqa: BLE001
        rows, issues, health = [], {}, {"error": str(e)}
    lex = {str(r.get("ref") or ""): r for r in rows}

    items = []
    for e in ents:
        row = lex.get(e.ref) or {}
        # ★ 描述**三态**：手写(abilities_user) / 自动摘要(abilities) / 缺。
        #   CLI 那类的「能力描述」按项目约定存在 `abilities` 列（创建时必写）→ 它是**有效描述**，
        #   绝不能因为 abilities_user 空就报「缺描述」（2026-10-10 用户实测踩到）。
        au = str(row.get("abilities_user") or "").strip()
        ab = str(row.get("abilities") or "").strip()
        if not row:                                  # 未在素材表登记时的兜底（正常 29=29 走不到）
            au = str(getattr(e, "abilities_user", "") or "").strip()
            ab = str(getattr(e, "abilities", "") or "").strip()
        desc_state = "user" if au else ("auto" if ab else "none")
        kw_used, kw_from = (lint._effective_keywords(row) if row
                            else (str(getattr(e, "keywords", "") or ""), "entry"))
        warn = issues.get(e.ref) or []
        hard = [p for p in warn if str(p.get("severity") or "hard") != "soft"]
        items.append({
            "ref": e.ref,
            "name": str(row.get("name") or getattr(e, "name", "") or ""),
            # ★ CLI 条目的 ref 是裸可执行名 → 来源仍按调用侧的写法显示为 `cli:<bin>`
            #   （与 parse_calls 的 source 口径一致，打分表里两边能对上 ✓）
            "source": ("cli:%s" % e.ref) if not str(e.ref).startswith(("api:", "mcp:"))
            else source_of(e.ref),
            "has_keywords": bool(str(kw_used).strip()),
            "kw_from": kw_from,                      # column / tool_desc / source_desc / derive
            "has_desc": desc_state != "none",        # ★ 兼容旧字段：手写或自动摘要都算「有」
            "desc_state": desc_state,                # user / auto / none
            "desc_chars": len(au or ab),
            # ★ 词法原文一并带上：打分表要出「该版本的工具描述词法」**全量快照**
            "keywords": str(kw_used or ""),
            "abilities_user": au,                    # 只放**手写**那段（与素材表列同义）
            "desc_text": au or ab,                   # 池子**实际会用**的那段（手写优先，否则自动摘要）
            "warn": warn,                            # dict 列表（code/msg/severity）→ 前端渲染 msg
            "hard": len(hard), "soft": len(warn) - len(hard),
        })
    return {"pool_version": pool_version(acc_id), "pool_size": len(items), "items": items,
            "problem_count": len([i for i in items if i["hard"]]),          # 会被体检拦下的
            "soft_count": len([i for i in items if not i["hard"] and i["soft"]]),
            "auto_only": len([i for i in items if i["desc_state"] == "auto"]),   # 能跑但词法偏弱
            "no_desc": len([i for i in items if i["desc_state"] == "none"]),
            "derived_kw": len([i for i in items if i["has_keywords"] and i["kw_from"] == "derive"]),
            "health": health}


def ext_retrievals(acc_id: int, since_id: int = 0) -> int:
    """本轮之后的外部检索次数（额度实耗；kind='retrieval_external' 才有额度成本 ✓）。"""
    conn = _conn()
    try:
        return int(conn.execute(
            "SELECT COUNT(*) FROM usage_events WHERE account_id=? AND kind=? AND id>?",
            (int(acc_id), KIND_RETRIEVAL_EXTERNAL, int(since_id))).fetchone()[0])
    finally:
        conn.close()


# ============================================================ 水位线 / 清理 ==========
def _max_id(conn, table: str):
    """取 `MAX(id)`；**读不到就返回 None（不是 0！）** —— 这个区别是数据安全的关键。

    ★ 2026-10-11 严重事故（用户报「历史版本全没了」）：`cleanup_run` 用 `id > 水位线` 删本轮新数据，
      而水位线原来**读失败时静默返回 0** → `DELETE ... WHERE account_id=3 AND id>0` ⇒
      **把该账号的全部画像历史删光** ✗✗（取证：`sqlite_sequence(memory_snapshots)=1066` 而表 0 行；
      其它表都在正常增长 ⇒ 只有 snapshot 水位线取成了 0）。
      「读不到」和「确实是 0」必须分开：前者**不许删任何东西** ✓
    """
    try:
        return int(conn.execute("SELECT COALESCE(MAX(id), 0) FROM %s" % table).fetchone()[0])
    except Exception as e:  # noqa: BLE001
        print("[probe] 水位线读取失败（该表本轮一律不清理）: %s → %s: %s" % (table, type(e).__name__, e))
        return None


def snapshot_files(username: str, *, include_sheets: bool = False,
                   include_raw: bool = False) -> set:
    """本轮可见的产物文件（相对 ROOT）。

    ★ 三类文件默认**不参与**水位线/清理：
      · `include_sheets=False` → 跳过**打分表**（`probe_打分表*`；用户裁定 6-⑤：唯一保留产物）；
      · `include_raw=False`   → 跳过**本轮原始记录**（`probe_run_*.json`）—— 它写完紧接着就要被
        `判分/词法建议/写入大屏` 读，被清理掉就是「判分失败：找不到这一轮的原始记录」
        （2026-10-11 用户真机实测报回 ✗）。它的生命周期改由 `_prune_raw()` 负责；
      · **画像快照目录**（`profile_backup_*`）**永远跳过** —— 它是测试前画像的还原源，被清理掉就还原不了
        （同一类坑：清理只看「新不新」，不看「有没有用」）。
    """
    out = set()
    for pat in ARTIFACT_DIRS:
        base = os.path.join(ROOT, *(pat % username).split("/"))
        if not os.path.isdir(base):
            continue
        for dirpath, _dirs, files in os.walk(base):
            if any(seg.startswith("profile_backup_") for seg in dirpath.replace("\\", "/").split("/")):
                continue
            for f in files:
                if not include_sheets and SHEET_RE.match(f):
                    continue
                if not include_raw and RAW_RE.match(f):
                    continue
                out.add(os.path.relpath(os.path.join(dirpath, f), ROOT))
    return out


def _prune_raw(sandbox: str, keep: int = 1) -> list:
    """沙箱里只留最近 `keep` 份**原始记录**（按修改时间，删其余）→ 返回被删的文件名。

    ★ 为什么单独管：原始记录是**临时产物**（判分/建议/写入大屏用完即弃），但它**不能**交给水位线
      清理（那会在本轮跑完那一刻就删掉，判分就找不到记录 ✗）。所以：跑完只留最新一份，旧的自然淘汰。
    """
    try:
        files = [f for f in os.listdir(sandbox) if RAW_RE.match(f)]
    except OSError:
        return []
    files.sort(key=lambda f: os.path.getmtime(os.path.join(sandbox, f)), reverse=True)
    gone = []
    for f in files[int(keep):]:
        try:
            os.remove(os.path.join(sandbox, f))
            gone.append(f)
        except OSError:
            pass
    return gone


def profile_dir(username: str) -> str:
    """账号的 `agents_docs/{用户名}/`（人格/记忆画像/笔记都在这里）。"""
    return str(_upaths.user_dir(username))


def snapshot_profile(username: str) -> dict:
    """**测试前**把 `agents_docs/{用户名}/` 整目录快照进沙箱 → 返回清单（供跑完还原 ✓）。

    ★ 为什么整目录而不是只 MEMORY.md（用户 2026-10-11 拍板）：同目录还有笔记文件，成本极低。
    ★ 为什么不用 `memory_snapshots` 表（用户原方案一是「按次数回退」）：那张表**每账号只留最近 3 份**
      且混着 `init`/`manual_edit` 等其他来源 —— 回退次数多于 3 就没了，且容易退到错误版本。
      整目录快照 + 还原不受次数与来源限制 ✓
    """
    src = profile_dir(username)
    # ★ 目录名带**毫秒**：同一秒内连跑两次（评测里很可能）不至于把上一份快照静默覆盖掉
    stamp = "%s_%03d" % (time.strftime("%Y%m%d_%H%M%S"), int((time.time() % 1) * 1000))
    # 06：画像快照也搬出沙箱（与原始记录同一个根）
    rel_dir = os.path.join(str(_upaths.PROBE_ARTIFACTS.relative_to(ROOT)), username,
                           "profile_backup_%s" % stamp)
    dst = os.path.join(ROOT, rel_dir)
    files: dict = {}
    if os.path.isdir(src):
        for dirpath, _dirs, fs in os.walk(src):
            for f in fs:
                full = os.path.join(dirpath, f)
                rel = os.path.relpath(full, src).replace("\\", "/")
                out = os.path.join(dst, *rel.split("/"))
                os.makedirs(os.path.dirname(out), exist_ok=True)
                shutil.copy2(full, out)
                files[rel] = _sha256(full)
    os.makedirs(dst, exist_ok=True)
    return {"dir": rel_dir, "files": files, "count": len(files), "username": username}


def restore_profile(username: str, snap: dict) -> dict:
    """把 `agents_docs/{用户名}/` 还原回快照状态（**跑完立刻做**，用户 2026-10-11 拍板）。

    三件事：① 快照里有的文件 —— 内容/存在性对不上就写回（**原子写**）；② 快照里没有的（本轮新冒出来的）
    删掉；③ 记下来「改了什么」，回给本轮原始记录与界面提示，可人工核对 ✓
    """
    src = profile_dir(username)
    backup = os.path.join(ROOT, *str(snap.get("dir") or "").split("/"))
    files = snap.get("files") or {}
    res = {"restored": [], "removed": [], "unchanged": 0, "chars": 0, "ok": True}
    try:
        os.makedirs(src, exist_ok=True)
        for rel, want in files.items():
            full = os.path.join(src, *rel.split("/"))
            now = _sha256(full) if os.path.isfile(full) else ""
            if now == want:
                res["unchanged"] += 1
                continue
            back = os.path.join(backup, *rel.split("/"))
            if not os.path.isfile(back):                       # 备份缺了 —— 不猜，如实记账
                res["restored"].append("%s（备份缺失，未还原 ✗）" % rel)
                continue
            os.makedirs(os.path.dirname(full), exist_ok=True)
            with open(back, "r", encoding="utf-8") as f:
                text = f.read()
            tmp = full + ".restore_tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                f.write(text)
            os.replace(tmp, full)                              # ★ 原子写：不会留半截文件
            res["restored"].append(rel)
            res["chars"] += len(text)
        for dirpath, _dirs, fs in os.walk(src):                # 本轮新冒出来的文件 → 删
            for f in fs:
                full = os.path.join(dirpath, f)
                rel = os.path.relpath(full, src).replace("\\", "/")
                if rel not in files:
                    try:
                        os.remove(full)
                        res["removed"].append(rel)
                    except OSError:
                        pass
    except Exception as e:  # noqa: BLE001 — 还原失败要**如实报**，不能让测评整体崩掉
        res["ok"] = False
        res["error"] = "%s: %s" % (type(e).__name__, e)
    return res


def _sha256(path: str) -> str:
    h = hashlib.sha256()
    try:
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
    except OSError:
        return ""
    return h.hexdigest()


def _prune_backups(sandbox: str, keep: int = 1) -> list:
    """沙箱里只留最近 `keep` 个画像快照目录（同 `_prune_raw`：临时产物，旧的自然淘汰 ✓）。"""
    try:
        names = [d for d in os.listdir(sandbox)
                 if d.startswith("profile_backup_") and os.path.isdir(os.path.join(sandbox, d))]
    except OSError:
        return []
    names.sort(key=lambda d: os.path.getmtime(os.path.join(sandbox, d)), reverse=True)
    gone = []
    for d in names[int(keep):]:
        try:
            shutil.rmtree(os.path.join(sandbox, d))
            gone.append(d)
        except OSError:
            pass
    return gone


def watermark(acc_id: int, username: str) -> dict:
    """跑之前打水位线：本账号各表的 MAX(id) + 现有产物文件 + 起始时刻。"""
    conn = _conn()
    try:
        return {"acc": int(acc_id), "username": username, "conv": _max_id(conn, "conversations"),
                "usage": _max_id(conn, "usage_events"), "mem": _max_id(conn, "memory_snapshots"),
                "fail": _max_id(conn, "failure_events"), "ts": time.time(),
                "files": snapshot_files(username)}
    finally:
        conn.close()


def cleanup_run(wm: dict) -> dict:
    """只删**本轮水位线之后、且属于本账号**的行 + 本轮新出现的文件 ✓（既有数据一律不动 ✗）。"""
    acc = wm["acc"]
    conn = _conn()
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
        for tbl, key in (("usage_events", "usage"), ("failure_events", "fail")):
            # ★ 2026-10-11 事故修复（同 `_max_id` 的说明）：水位线**读不到（None）就不删**，
            #   绝不把「读失败」当成「从 0 开始」✗（那会把账号的历史整片删掉）
            # 另：`memory_snapshots`（画像历史版本）**已从清理名单里去掉了** —— 它是给用户看的
            #   历史数据，宁可留下本轮产生的几条（模块自己只留最近 3 份），也不能冒删光历史的风险 ✓
            if wm.get(key) is None:
                continue
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
    for rel in sorted(snapshot_files(wm["username"]) - wm["files"]):
        try:
            os.remove(os.path.join(ROOT, rel))
            gone.setdefault("files", []).append(rel)
        except OSError as e:  # noqa: BLE001
            gone.setdefault("stuck", []).append("%s（%s）" % (rel, e))
    return gone


# ============================================================ 执行（真机）============
def _client():
    """in-process TestClient（与前端走同一条 `/api/chat` 路径 ✓ 不起真服务）。"""
    from fastapi.testclient import TestClient
    from api import server
    return TestClient(server.app)


def _service_shutting_down() -> bool:
    """服务是否在关闭（拿不到就当没关 ✓ —— 探针不该因为探测失败而停）。"""
    try:
        from api import server as _srv
        return bool(_srv.is_shutting_down())
    except Exception:  # noqa: BLE001
        return False


def ask(client, token: str, question: str) -> tuple:
    """一条一问：新建会话 → 提问 → 返回 (状态码, 回答, thread_id, 耗时)。

    ★ 每道题都带 `probe=1`：后端据此**强制关闭「决策模型精排」**（2026-10-13 用户裁定）——
      测评量的是「路由器 + 素材层」的水平，带上决策层就换了仪器、与 01/03 的历史基线不可比。

    ★ 两条护栏（2026-10-11 用户实测「某一题卡死」后加，审计日志取证：一题连翻 11 页、跑了 4 分钟）：
      · **单题墙钟上限** `PROBE_ASK_TIMEOUT`（默认 180s）——超时就调 `/api/chat/cancel` **中断**这一轮，
        记 `408` 走「**未测**」口径（不计入能力评价 ✓，本题的调用序列仍留档供人工查看）；
      · **只对网络抖动类重试一次**（429/502/503/504）；`500`/`408` 重试注定同样结果、纯烧时间 ✗

    ★ 会话一律用 `kind=probe` 建（用户 2026-10-11 裁定）：测评会话**从不进会话库**，
      跑完（或开跑前）由 `conversation_store.purge_probe_sessions()` 全清 ✓
    """
    global _EVAL_NOTE_PRINTED
    if not _EVAL_NOTE_PRINTED:
        _EVAL_NOTE_PRINTED = True
        print("※ 本次测评每题下发 `probe=1` → 后端强制关闭「决策模型精排」（保证测的是路由器 + 素材层，与历史基线可比）",
              flush=True)
    t0 = time.time()
    try:
        tid = client.post("/api/chat/sessions",
                          params={"token": token, "kind": "probe"}).json()["thread_id"]
    except Exception as e:  # noqa: BLE001
        return 500, "建会话失败：%s: %s" % (type(e).__name__, e), "", round(time.time() - t0, 1)
    for attempt in (1, 2):
        box: dict = {}

        def _post():
            try:
                # ★ 不传 `timeout=`：TestClient 不支持它（会抛 StarletteDeprecationWarning 且**不生效** ✗
                #   用户真机日志里就有一条 —— 单题的真正护栏是下面的墙钟上限 `PROBE_ASK_TIMEOUT`）
                box["r"] = client.post("/api/chat",
                                       params={"token": token, "thread_id": tid, "question": question,
                                               "probe": "1"})    # ★ 测评请求：后端强制关决策层（裁定 6）
            except Exception as e:  # noqa: BLE001
                box["err"] = e

        th = threading.Thread(target=_post, daemon=True)
        th.start()
        _cap = ask_timeout()
        th.join(_cap)
        if th.is_alive():
            # ★ 超时：让 Agent 自己收手（AgentCancelled 会在下一个检查点穿透，落一条占位回答 ✓）
            print("     （第 %d 次超过 %ss 未返回 → 中断本轮：模型多半在反复调工具/翻页）"
                  % (attempt, int(_cap)), flush=True)
            try:
                client.post("/api/chat/cancel", params={"token": token, "thread_id": tid}, timeout=30)
            except Exception:  # noqa: BLE001
                pass
            th.join(30)                                   # 给它 30s 收尾（写库/落占位回答）
            return 408, "超时中断（单题上限 %ss）" % int(_cap), tid, round(time.time() - t0, 1)
        if "err" in box:
            return 500, "请求异常：%s: %s" % (type(box["err"]).__name__, box["err"]), tid, \
                round(time.time() - t0, 1)
        r = box.get("r")
        if r is not None and r.status_code == 200:
            return 200, (r.json().get("answer") or ""), tid, round(time.time() - t0, 1)
        code = r.status_code if r is not None else 0
        text = (r.text[:200] if r is not None else "")
        print("     （第 %d 次返回 %s，%s）" % (attempt, code, text[:80]), flush=True)
        if code in UNMEASURED_STATUS and code not in (429, 502, 503, 504):
            break                                          # 500/408：重试没意义 ✗
        time.sleep(20)
    return code, text, tid, round(time.time() - t0, 1)


# ★ 2026-10-11：工具调用的**耐久日志**（`api/monitor.py::report_tool` 落盘）。
#   用途：被中断的轮次（单题超时 → cancel）不会把 tool_calls 写进 `messages` 表 → 读库为空 →
#   打分表会把它记成「一次工具都没调用」（用户实测踩到，日志里明明有 run_shell_command）✗
TOOL_CALL_LOG = os.getenv("TOOL_CALL_LOG") or os.path.join(ROOT, "data", "audit", "tool_calls.jsonl")


def calls_from_log(thread_id: str) -> list:
    """从**耐久工具日志**读回某会话的调用（读不成/没有就返回空表 ✓ 绝不抛）。"""
    if not thread_id or not os.path.isfile(TOOL_CALL_LOG):
        return []
    seq: list = []
    try:
        with open(TOOL_CALL_LOG, "r", encoding="utf-8") as f:
            for line in f:
                try:
                    rec = json.loads(line)
                except (TypeError, ValueError):
                    continue                                  # 半截行（进程被杀）跳过 ✓
                if str(rec.get("thread_id") or "") != str(thread_id):
                    continue
                seq.append({"name": rec.get("tool"), "args": rec.get("args") or {}})
    except OSError:
        return []
    return parse_calls(json.dumps(seq, ensure_ascii=False))


def calls_of_thread(acc_id: int, thread_id: str) -> list:
    """按会话读回模型发出的调用（**不采信模型自述**，也不看审计窗口 ✓ 探针踩过串号）。

    ★ 两道来源（2026-10-11 加第二道）：① `messages.tool_calls`（跑完的轮次，**权威且保留重复调用**）；
      ② **耐久工具日志**（`data/audit/tool_calls.jsonl`）—— 只在①为空时兜底：
      被**中断**的轮次（超时/cancel）不落库，没有②就会被记成「一次工具都没调用」✗
    """
    return calls_of_thread_src(acc_id, thread_id)[0]


def calls_of_thread_src(acc_id: int, thread_id: str) -> tuple:
    """同上，但**带来源**（`"db"` / `"log"` / `""`）—— 界面/打分表要能说明「这条是从哪读到的」✓"""
    if not thread_id:
        return [], ""
    conn = _conn()
    try:
        rows = list(conn.execute(
            """SELECT m.tool_calls FROM messages m JOIN conversations v ON v.id = m.conversation_id
               WHERE v.account_id = ? AND v.thread_id = ?
                 AND m.tool_calls IS NOT NULL AND m.tool_calls != ''""",
            (int(acc_id), thread_id)))
    finally:
        conn.close()
    out: list = []
    for r in rows:
        out.extend(parse_calls(r["tool_calls"]))
    if out:
        return out, "db"
    fb = calls_from_log(thread_id)
    return (fb, "log") if fb else ([], "")


def run_cases(acc_id: int, username: str, cases: list, *, set_doc: dict | None = None,
              keep: bool = False, on_progress=None, client=None, token: str | None = None) -> dict:
    """跑一轮测评：**只记录、不判对错** ✓。

    返回 dict(ts, rows, watermark, gone, raw_path, ext_retrievals, elapsed_s, pool)。
    原始记录落 `user_data/probe_artifacts/{username}/probe_run_YYYYmmdd_HHMMSS.json` —— **判分/建议/写入大屏的输入**，
    跑完只留最近一份（`_prune_raw`），**不参与**水位线清理（否则判分就找不到这一轮 ✓ 2026-10-11 修）。
    """
    from tools import capability_pool as cp
    from agent import conversation_store as cs

    pool = pool_overview(acc_id)
    wm = watermark(acc_id, username)
    t_start = time.time()
    if client is None:
        client = _client()
    if not token:
        from api.account import create_session
        acc = account_by_id(acc_id)
        token = create_session(acc)

    # 06：本轮产物目录（原为沙箱；语义 = 测评产物，不是"Agent 的工作目录"）
    artifacts = os.path.join(ARTIFACTS_DIR, username)
    os.makedirs(artifacts, exist_ok=True)
    # ★ 开跑前清一次**上一轮残留**的测评会话（中途崩溃/被中断才会留下 —— 用户 2026-10-11 裁定：
    #   测评会话禁止进会话库，且真机全量测试后全删）
    stale = cs.purge_probe_sessions(int(acc_id))
    # ★ 测试前的**画像 + 笔记**整目录快照（跑完立刻还原；用户 2026-10-11 拍板方案四）
    prof = snapshot_profile(username)

    rows: list = []
    stopped = ""
    total = len(cases)
    try:
        for i, case in enumerate(cases, 1):
            # ★ 关服就收手（2026-10-11）：服务重启时继续发问会撞 `cannot schedule new futures
            #   after shutdown`（线程池已关）→ 整轮白跑。宁可**提前中止并如实记账**（已完成的行全留 ✓）
            if _service_shutting_down():
                stopped = "服务正在关闭/重启 —— 本轮在第 %d 题前提前中止" % i
                print("     ⚠ " + stopped, flush=True)
                break
            if on_progress:
                on_progress(i - 1, total, case)
            code, ans, tid, dur = ask(client, token, case["q"])
            calls, calls_src = calls_of_thread_src(acc_id, tid)
            sources = sorted({x["source"] for x in calls if not x["source"].startswith("builtin:")})
            used: list = []
            for x in calls:                               # 去重但保持首次出现顺序 ✓
                if x["target"] not in used:
                    used.append(x["target"])
            unmeasured = code in UNMEASURED_STATUS or (code != 200 and not calls)
            # ★ 2026-10-11：被**中断**的轮次（单题超时）要标出来 —— 它上面的调用是**中断前已发生**的，
            #   不是完整一轮（用户实测：日志里有 run_shell_command，旧口径却记「一次工具都没调用」✗）
            interrupted = (code == 408)
            rows.append({
                "id": case.get("id"), "q": case["q"], "kind": case.get("kind") or "single",
                "core": bool(case.get("core")), "expect": case.get("expect"),
                "ref_chain": ref_text(case), "calls": calls, "sources": sources,
                "tools_used": used, "n_calls": len(calls), "dur_s": dur, "status": code,
                "unmeasured": unmeasured, "interrupted": interrupted, "calls_src": calls_src,
                "answer": (ans or "")[:600],
            })
            if on_progress:
                on_progress(i, total, case)
            # ★ 撞上「线程池/事件循环已关闭」这类**进程级**故障就别再往下问了（后面必然同样失败）✗
            _low = str(ans).lower()
            if code == 500 and ("cannot schedule" in _low or "after shutdown" in _low):
                stopped = ("第 %d 题报「线程池已关闭（cannot schedule / after shutdown）」—— "
                           "本轮提前中止；已完成的行仍可使用 ✓" % i)
                print("     ⚠ " + stopped, flush=True)
                break
    finally:
        # ★ 收尾一律走 finally：**中途抛错也要**把数据清干净、把画像还原（否则一轮失败就留一地脏 ✓）
        gone = {} if keep else cleanup_run(wm)
        profile = restore_profile(username, prof)
        if stale.get("conversations"):
            gone["probe_sessions_stale"] = stale
        purged = cs.purge_probe_sessions(int(acc_id))     # 本轮的测评会话 → 全清（不进会话库 ✓）
        if purged.get("conversations"):
            gone["probe_sessions"] = purged
        pruned = _prune_raw(artifacts, keep=1)             # 原始记录只留最近一份
        if pruned:
            gone["raw_pruned"] = pruned
        bak = _prune_backups(artifacts, keep=1)            # 画像快照只留最近一份
        if bak:
            gone["profile_pruned"] = bak

    ts = time.strftime("%F %T")
    doc = {
        "ts": ts, "account_id": int(acc_id), "username": username,
        "set_name": (set_doc or {}).get("name") or "inline",
        "pool_version": pool["pool_version"], "pool_size": pool["pool_size"],
        # ★ 池快照（含词法原文）：打分表第一/二节要用**当时**的口径，不能事后重读（池会变 ✓）
        "pool": pool,
        "count": len(rows), "scoring": "manual（脚本不判对错 ✓）", "rows": rows,
        "ext_retrievals": ext_retrievals(acc_id, wm["usage"]),
        "elapsed_s": round(time.time() - t_start, 1),
        # ★ 提前中止的原因（空串 = 正常跑完）。两种：服务在关 / 撞上进程级故障（线程池已关）✓
        "stopped": stopped,
        # ★ 测试前的画像快照与**还原结果**（用户要能核对「测试有没有动我画像」✓）
        "profile": dict(profile, backup_dir=prof.get("dir"), backup_count=prof.get("count")),
        "watermark": {k: v for k, v in wm.items() if k != "files"},
        "gone": gone,
    }
    raw_path = os.path.join(artifacts, "probe_run_%s.json" % time.strftime("%Y%m%d_%H%M%S"))
    doc["raw_path"] = raw_path
    with open(raw_path, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=2)
    return doc


def load_run(raw_path: str) -> dict:
    """读回一轮的原始记录（判分/出表用）。"""
    with open(raw_path, "r", encoding="utf-8") as f:
        return json.load(f)


# ============================================================ 打分表（三段式快照）====
def _fmt_calls(calls: list, interrupted: bool = False) -> str:
    """调用列：正常列调用；**被中断**的轮次额外标注（否则会被误读成「完整的一轮就是这样」✗）"""
    if calls:
        t = " ／ ".join("`%s`" % c["brief"] for c in calls)
        return t + (" ／ ⏱ **超时中断**（上述为中断前已发生的调用，非完整轮次）" if interrupted else "")
    return ("**（超时中断：一次工具都没调）**" if interrupted else "**（一次工具都没调）**")


def build_sheet(run: dict, *, scores=None, metrics=None, advisor=None) -> str:
    """三段式快照（用户 2026-10-10 定，见规划文档 §03 ③F）：

    ① 该版本**有哪些工具** ② 该版本**工具描述的词法（全量）** ③ **最终数据**（指标 + 逐题判分）。
    末尾附一段机器可读 JSON（判分与大屏用，免去解析 md）。

    `scores`：{题号: {'verdict': '✓|△|✗|未测', 'note': str, 'agent': 预填文案}}（人工终判为准）
    `metrics`：`build_metrics()` 的产物；`advisor`：词法建议文本（probe_advisor 的产出）。
    """
    scores = {int(k): v for k, v in (scores or {}).items()}
    pool = run.get("pool") or {}
    items = pool.get("items") or []
    runs = run.get("rows") or []
    L = []
    L += ["# 工具池测评 · 打分表（%s）" % run.get("ts", ""), "",
          "> 账号 `%s` ｜ 池版本 `%s` ｜ 池规模 %d 条 ｜ 题集 `%s`（%d 题）｜ 耗时 %ss"
          % (run.get("username", ""), run.get("pool_version", 0), pool.get("pool_size", 0),
             run.get("set_name", ""), run.get("count", 0), run.get("elapsed_s", 0)),
          "> 额度：本轮**外部检索 %s 次**（tip：对网络搜索额度消耗较大）" % run.get("ext_retrievals", 0),
          "> 口径：**脚本只记录、不判对错** ✓；`判定：` 一栏**以人工终判为准**（Agent 预填仅参照）。", ""]

    # ① 该版本有哪些工具
    L += ["## 一、本版本的工具（%d 条）" % len(items), "",
          "| # | ref | 名称 | 来源 | keywords | 描述 | 体检 |", "|---|---|---|---|---|---|---|"]
    for i, it in enumerate(items, 1):
        L.append("| %d | `%s` | %s | %s | %s | %s | %s |"
                 % (i, it.get("ref", ""), it.get("name", ""), it.get("source", ""),
                    "✓" if it.get("has_keywords") else "✗ 缺",
                    "✗ 缺" if it.get("desc_state") == "none" else "✓",
                    "；".join((p.get("code") or p.get("msg") or "") if isinstance(p, dict)
                              else str(p or "") for p in (it.get("warn") or [])) or "✓"))
    L += ["", "> 只在**真缺**时标 `✗ 缺`：`keywords` 取不到任何词、`描述` 两样都没有（手写描述与服务自带摘要皆空）。",
          "  本列口径与「技能与工具」里的**素材体检**完全一致 ✓（建议先补素材再测 —— 见「先体检 → 再出题 → 再测评」）。", ""]

    # ② 该版本的工具描述词法（全量）
    L += ["## 二、本版本的工具描述词法（全量快照）", "",
          "> 这一段是**该轮当时的素材原文**：任何一份旧打分表都能据此完整复现当轮的池子口径 ✓", ""]
    for i, it in enumerate(items, 1):
        L += ["### %d. `%s`" % (i, it.get("ref", "")),
              "- 名称：%s" % (it.get("name", "") or "（空）"),
              "- keywords：%s" % (it.get("keywords", "").strip() or "（空）"),
              "- 描述：%s" % ((it.get("desc_text") or "").strip() or "（空）"), ""]

    # ③ 最终数据
    L += ["## 三、最终数据", ""]
    if metrics:
        L += ["| 指标 | 值 |", "|---|---|",
              "| 单工具题命中率 | %s |" % _rate(metrics.get("single_hit"), metrics.get("single_total")),
              "| 多工具题命中率 | %s |" % _rate(metrics.get("multi_hit"), metrics.get("multi_total")),
              "| 噪声越线数 | %s / %s |" % (metrics.get("noise_over", 0), metrics.get("noise_total", 0)),
              "| 判分分布 | ✓ %s ／ △ %s ／ ✗ %s ／ 未测 %s |"
              % (metrics.get("verdict_ok", 0), metrics.get("verdict_partial", 0),
                 metrics.get("verdict_bad", 0), metrics.get("verdict_unknown", 0)),
              "| 平均外部检索 | %s 次/题 |" % metrics.get("avg_ext_retrievals", 0),
              "| 池版本 / 题数 | %s / %s |" % (metrics.get("pool_version", 0), metrics.get("cases_total", 0)),
              "| 耗时 | %s s |" % metrics.get("elapsed_sec", 0), ""]
    else:
        L += ["> （未判分：本节在「判分 → 写入大屏」时补齐）", ""]
    L += ["### 逐题（判定以人工终判为准）", "",
          "| # | 类别 | 题面 | 期望 | 实际调用 | 判定 | 备注 |", "|---|---|---|---|---|---|---|"]
    for r in runs:
        s = scores.get(int(r.get("id") or 0)) or {}
        L.append("| %s%s | %s | %s | %s | %s | %s | %s |"
                 % (r.get("id"), " ★" if r.get("core") else "", r.get("kind"),
                    str(r.get("q"))[:40], expected_brief(r.get("expect")),
                    _fmt_calls(r.get("calls") or [], bool(r.get("interrupted"))),
                    s.get("verdict") or "", s.get("note") or ""))
    L += ["", "> 判定口径：`✓` 链路完整且必要；`△` 结果对但绕路/漏可选步（备注写清）；"
              "`✗` 用错来源或漏关键步；`未测`（429/5xx/超时）**不计入能力评价** ✓",
          "> ⏱ 标记 = 该题**被单题上限中断**（`PROBE_ASK_TIMEOUT`）：列出的调用是**中断前已发生**的"
          "（来自耐久工具日志 `data/audit/tool_calls.jsonl`，不看它会误读成「一次工具都没调用」✗）",
          ""]
    if advisor:
        L += ["## 四、词法改进建议（Agent 产出，待你确认后自行改素材）", "", str(advisor), ""]

    # 机器可读块（判分/大屏读它，免解析 md）
    payload = {"run": {k: run.get(k) for k in ("ts", "account_id", "username", "set_name",
                                               "pool_version", "pool_size", "count",
                                               "elapsed_s", "ext_retrievals")},
               "scores": {str(k): v for k, v in scores.items()},
               "metrics": metrics or {},
               "rows": [{"id": r.get("id"), "kind": r.get("kind"), "q": r.get("q"),
                         "expect": r.get("expect"), "tools_used": r.get("tools_used"),
                         "calls": [c["brief"] for c in (r.get("calls") or [])],
                         "unmeasured": r.get("unmeasured"), "dur_s": r.get("dur_s")} for r in runs]}
    L += ["", "<!-- 机器可读（判分 / 大屏 / 版本对比用；请勿手改） -->", "```json",
          json.dumps(payload, ensure_ascii=False, indent=1), "```", ""]
    return "\n".join(L)


def _rate(hit, total) -> str:
    try:
        hit, total = int(hit or 0), int(total or 0)
    except (TypeError, ValueError):
        return "—"
    return "—" if total <= 0 else "%d/%d（%.1f%%）" % (hit, total, 100.0 * hit / total)


def new_sheet_name(ts: str | None = None) -> str:
    """快照文件名：`probe_打分表YYYYMMDD_HHMMSS.md`（用户 2026-10-10 定；过滤器只认这个格式 ✓）。"""
    stamp = time.strftime("%Y%m%d_%H%M%S") if not ts else \
        "".join(ch for ch in ts if ch.isdigit())[:14]
    return "%s%s.md" % (SHEET_PREFIX, stamp)


def sheet_dir(username: str) -> str:
    d = os.path.join(OUTPUT_DIR, username)
    os.makedirs(d, exist_ok=True)
    return d


def write_sheet(username: str, text: str, name: str | None = None) -> str:
    """把打分表写进 `output/{username}/`（**该目录下唯一保留的测评产物** ✓）。"""
    name = name or new_sheet_name()
    path = os.path.join(sheet_dir(username), name)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    return path


def sheet_path(username: str, name: str) -> str:
    """按名取打分表路径（**只认严格格式**，防路径逃逸 ✓）。"""
    if not SHEET_RE.match(str(name or "")):
        raise ValueError("非法打分表名：%s（必须形如 probe_打分表20261005_221918.md）" % name)
    return os.path.join(sheet_dir(username), name)


def list_sheets(username: str) -> list:
    """历史打分表版本列表（倒序；`output/` 下**其余文件一律不进列表** ✓）。"""
    d = os.path.join(OUTPUT_DIR, username)
    if not os.path.isdir(d):
        return []
    out = []
    for f in os.listdir(d):
        if not SHEET_RE.match(f):
            continue
        p = os.path.join(d, f)
        try:
            st = os.stat(p)
        except OSError:
            continue
        out.append({"name": f, "size": int(st.st_size),
                    "mtime": time.strftime("%F %T", time.localtime(st.st_mtime))})
    out.sort(key=lambda x: x["name"], reverse=True)
    return out


def read_sheet(username: str, name: str) -> str:
    with open(sheet_path(username, name), "r", encoding="utf-8") as f:
        return f.read()


# ============================================================ 指标（账号级）=========
def build_metrics(run: dict, scores=None) -> dict:
    """把一轮结果折成**账号级指标**（字段与 `probe_metrics` 表一一对应 ✓）。

    口径（见规划文档 §03 ③E）：
      · `*_hit` 只算**人工终判为 ✓**（完全正确）；`△` 只进 `verdict_partial`；
      · `noise_over` = 噪声题里**调用了任何工具**的条数（越线）；
      · `verdict_unknown` = 未测（限流/故障）+ 未填判定；
      · 真值来自 `messages.tool_calls`（`run_cases` 已读回），对错来自传入的 `scores`。
    """
    scores = {int(k): v for k, v in (scores or {}).items()}
    rows = run.get("rows") or []
    m = {"pool_version": int(run.get("pool_version") or 0), "pool_size": int(run.get("pool_size") or 0),
         "cases_total": len(rows), "single_total": 0, "single_hit": 0, "multi_total": 0,
         "multi_hit": 0, "noise_total": 0, "noise_over": 0, "verdict_ok": 0, "verdict_partial": 0,
         "verdict_bad": 0, "verdict_unknown": 0,
         "avg_ext_retrievals": round(float(run.get("ext_retrievals") or 0) / max(len(rows), 1), 2),
         "elapsed_sec": float(run.get("elapsed_s") or 0)}
    for r in rows:
        kind = r.get("kind") or "single"
        v = (scores.get(int(r.get("id") or 0)) or {}).get("verdict")
        if r.get("unmeasured") or not v or str(v).startswith("未测"):
            m["verdict_unknown"] += 1
        elif v == "✓":
            m["verdict_ok"] += 1
        elif v == "△":
            m["verdict_partial"] += 1
        else:
            m["verdict_bad"] += 1
        if kind == "noise":
            m["noise_total"] += 1
            if (r.get("calls") or []):
                m["noise_over"] += 1
        elif kind == "multi":
            m["multi_total"] += 1
            m["multi_hit"] += 1 if v == "✓" else 0
        else:
            m["single_total"] += 1
            m["single_hit"] += 1 if v == "✓" else 0
    return m


METRIC_COLS = ("pool_version", "pool_size", "cases_total", "single_total", "single_hit",
               "multi_total", "multi_hit", "noise_total", "noise_over", "verdict_ok",
               "verdict_partial", "verdict_bad", "verdict_unknown", "avg_ext_retrievals",
               "elapsed_sec", "sheet_name")


def commit_metrics(acc_id: int, metrics: dict) -> dict:
    """写大屏（**每账号一行**，UPSERT；不做历史行、不做回退 ✓ 用户裁定）。"""
    from tools.schema_personal import ensure_tables
    ensure_tables()
    vals = [metrics.get(c) if metrics.get(c) is not None else (0 if c != "sheet_name" else None)
            for c in METRIC_COLS]
    conn = _conn()
    try:
        conn.execute(
            "INSERT INTO probe_metrics (account_id, %s, updated_at) "
            "VALUES (?%s, ?) ON CONFLICT(account_id) DO UPDATE SET %s, updated_at=?"
            % (", ".join(METRIC_COLS), ", ?" * len(METRIC_COLS),
               ", ".join("%s=excluded.%s" % (c, c) for c in METRIC_COLS)),
            [int(acc_id)] + vals + [time.strftime("%F %T")] + [time.strftime("%F %T")])
        conn.commit()
        row = conn.execute("SELECT * FROM probe_metrics WHERE account_id=?", (int(acc_id),)).fetchone()
        return dict(row) if row else {}
    finally:
        conn.close()


def get_metrics(acc_id: int) -> dict:
    """读该账号最新一轮指标（大屏用）。"""
    conn = _conn()
    try:
        row = conn.execute("SELECT * FROM probe_metrics WHERE account_id=?", (int(acc_id),)).fetchone()
        return dict(row) if row else {}
    finally:
        conn.close()
