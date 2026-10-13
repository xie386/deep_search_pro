"""工具池测评 · **独立测评 Agent**（v4.0 大点 03 产品模块，2026-10-10 立）。

**不纳入主 Agent 体系** ✓（与 `soul_writer` / `capability_writer` / `memory_suggester` 同类）：
只被测评模块（`api/probe.py`）调用，每次一件事、拿结构化输出、**不落库**（落库/落盘由调用方在闸门后做）。

三段提示词在 `prompt/prompts.yml` 的**顶层段**（写法沿用 `capability_writer` 的惯例）：
    · `probe_writer`  —— 出题并给预期答案（输入 = 路由器渲染的**同一份卡片文本** ✓）
    · `probe_judge`   —— 判分**预填**（人工再二次打分做终判；本段拿不到出题时的推理，避免同源偏差）
    · `probe_advisor` —— 词法改进建议（只建议改素材，不许建议改代码/阈值/堆关键词）

★ 调用方式与 `api/customize.py` 里那几个 writer 一致（`yaml` 读段 → `agent.llm.model.invoke`）。
"""
from __future__ import annotations

import json
import os
import re
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROMPTS = os.path.join(ROOT, "prompt", "prompts.yml")
# ★ 2026-10-13（议题一）：题集落盘归口 user_data/probe_sets/
from utils.paths import PROBE_SETS_DIR as _PROBE_SETS_DIR                        # noqa: E402
SET_DIR = str(_PROBE_SETS_DIR)                              # 生成的题集（产品数据，见裁定 13）

SECTIONS = ("probe_writer", "probe_judge", "probe_advisor")


# ============================================================ 提示词 / 调用 =========
def prompt_of(section: str) -> str:
    """读 `prompt/prompts.yml` 的顶层段（缺段即报错，别静默降级 ✓）。"""
    import yaml
    if section not in SECTIONS:
        raise ValueError("不是测评段：%s" % section)
    cfg = yaml.safe_load(open(PROMPTS, encoding="utf-8").read()) or {}
    sec = cfg.get(section) or {}
    text = str(sec.get("system_prompt") or "").strip()
    if not text:
        raise RuntimeError("prompts.yml 里缺少 %s.system_prompt" % section)
    return text


_MODEL_CACHE: dict = {}


def probe_model(kind: str = ""):
    """测评三段（出题/判分/建议）用的模型 —— **可独立于主 Agent 的 `LLM_MODEL_MAX`**。

    ★ 为什么单独开一个口（2026-10-10 用户实测）：主 Agent 用的可能是**思考型**模型，
      实测某一批「**思考 13154 字、耗时 373.7 秒、`content` 为空**」—— 预算全花在 reasoning 上。
      而测评三段是**结构化短输出**任务（每批几百字 JSON），根本不需要长思考，7 批串行还要 20 分钟。
      三个环境变量（都不设 = 行为与以前完全一致 ✓）：
        · `PROBE_LLM_MODEL`     —— 单独指定测评模型（默认 = `LLM_MODEL_MAX`）
        · `PROBE_LLM_TIMEOUT`   —— 单次调用超时秒数（默认 240）→ 慢模型**快速失败**，不要卡 6 分钟
        · `PROBE_LLM_MAXTOKENS` —— 单次输出上限（默认 8192）→ 太小会被「思考」吃光导致空内容
      若测评模型在**别的厂商**（不是 OpenRouter），再加两个（不填则用 `OPENAI_BASE_URL`/`OPENAI_API_KEY`）：
        · `PROBE_LLM_BASE_URL` / `PROBE_LLM_API_KEY`
    ★ **判分可单独配**（`kind="judge"`，2026-10-10 用户要求「出题判分模型」）：出题是**多批次×短输出**
      （要快、要稳吐 JSON），判分是**一次长输入 + 需要判断力**（要准）——两件事的最优模型常常不同：
        · `PROBE_LLM_JUDGE_MODEL` —— 判分专用（不填 = 沿用上面的 `PROBE_LLM_MODEL` ✓）
    """
    from dotenv import find_dotenv, load_dotenv
    load_dotenv(find_dotenv())                     # 与 agent/llm.py 同源（幂等）
    name = ""
    if kind == "judge":
        name = (os.getenv("PROBE_LLM_JUDGE_MODEL") or "").strip()
    name = name or (os.getenv("PROBE_LLM_MODEL") or os.getenv("LLM_MODEL_MAX") or "").strip()
    base = (os.getenv("PROBE_LLM_BASE_URL") or os.getenv("OPENAI_BASE_URL") or "").strip()
    key = (os.getenv("PROBE_LLM_API_KEY") or os.getenv("OPENAI_API_KEY") or "").strip()
    timeout = float(os.getenv("PROBE_LLM_TIMEOUT") or 240)
    maxtok = int(os.getenv("PROBE_LLM_MAXTOKENS") or 8192)
    ck = "%s|%s|%s|%s|%s" % (name, base, bool(key), timeout, maxtok)
    if ck not in _MODEL_CACHE:
        from agent.reasoning_model import ReasoningChatOpenAI   # 与 agent/llm.py 同一实现
        _MODEL_CACHE[ck] = ReasoningChatOpenAI(model=name, api_key=key, base_url=base,
                                              timeout=timeout, max_tokens=maxtok)
    return _MODEL_CACHE[ck]


def invoke(section: str, user_msg: str, *, model=None, stage: str = "", retries: int = None) -> str:
    """调一次模型（**独立小 Agent 用法**：system 段 + 一条 user 消息，取纯文本返回）。

    ★ 用户 2026-10-10 要求「把 Agent 正在生成的内容打印在后端命令行日志」→ 这里统一打印
      阶段名、字数、耗时与**原始输出全文**（出题失败时靠它定位，不再靠猜）。
    ★ **瞬时错误自动重试**（429 限流 / 5xx / 超时 / 连接抖动）：免费模型与免费额度下这是常态，
      实测 `poolside/laguna-xs:free` 就 429 过一次。认证类错误（401/403）**不重试**（重试也没用）。
    ★ 空内容单独报错：实测思考型 provider 会把预算全花在 reasoning 上、`content` 为空，
      原来只说「没有 JSON」，看不出是**根本没内容**还是**内容不含 JSON**。
    """
    if model is None:
        model = probe_model()
    if retries is None:                    # 免费模型会限流：默认重试 2 次（可用 `PROBE_LLM_RETRIES` 调）
        retries = int(os.getenv("PROBE_LLM_RETRIES") or 2)
    tag = stage or section
    transient = ("429", "500", "502", "503", "504", "timeout", "timed out", "connection",
                 "temporarily", "rate-limited", "overloaded", "unavailable")
    last = None
    resp = None
    for attempt in range(int(retries) + 1):
        t0 = time.time()
        try:
            resp = model.invoke([{"role": "system", "content": prompt_of(section)},
                                 {"role": "user", "content": user_msg}])
            break
        except Exception as e:  # noqa: BLE001
            last = e
            brief = "%s: %s" % (type(e).__name__, str(e)[:160])
            hit = any(t in brief.lower() for t in transient)
            if attempt < int(retries) and hit:
                wait = 2 + 4 * attempt
                _log("%s：调用失败（瞬时：%s）→ 等 %ds 重试第 %d 次" % (tag, brief, wait, attempt + 1))
                time.sleep(wait)
                continue
            raise ProbeGenError(
                "%s：模型调用失败（%s）—— 若是限流/抖动可稍后重试；若是超时，调 `PROBE_LLM_TIMEOUT`；"
                "若该 provider 不稳，用 `PROBE_LLM_MODEL`（配 `PROBE_LLM_BASE_URL`/`PROBE_LLM_API_KEY`）"
                "单独给测评换一个模型。" % (tag, brief)) from e
    txt = str(getattr(resp, "content", "") or "").strip()
    rc = (getattr(resp, "additional_kwargs", {}) or {}).get("reasoning_content") or ""
    _log("%s ← content %d 字 / reasoning %d 字 / %.1fs" % (tag, len(txt), len(rc), time.time() - t0))
    if not txt:
        raise ProbeGenError(
            "%s：模型返回**空内容**（%.1fs；思考字段 %d 字）—— 思考型模型把预算花在了 reasoning 上，"
            "或该 provider 与模型不兼容（内容落在 reasoning_content 而 content 为空）。"
            "最省事的办法：在 `.env` 里用 `PROBE_LLM_MODEL` 给测评单独换一个「快且稳吐 JSON」的模型；"
            "或调大 `PROBE_LLM_MAXTOKENS` 后重试。" % (tag, time.time() - t0, len(rc)))
    _log("%s 原始输出：\n%s" % (tag, txt))
    return txt


class ProbeGenError(RuntimeError):
    """出题/判分/建议段失败（消息面向用户，已含阶段与可操作建议）。"""


def _log(msg: str) -> None:
    """后端命令行日志（`[probe_agent]` 前缀，便于在服务日志里过滤）。"""
    try:
        print("[probe_agent] %s" % msg, flush=True)
    except Exception:  # noqa: BLE001 - 日志失败绝不影响主流程
        pass


def _strip_fences(t: str) -> str:
    t = str(t or "").strip()
    if t.startswith("```"):
        t = re.sub(r"^```[a-zA-Z]*\s*", "", t)
        t = re.sub(r"```\s*$", "", t).strip()
    return t


def _fixups(t: str):
    """常见畸形变体（**逐个尝试**，都是无副作用的替换）：
    尾逗号 / 中文引号 / 单引号 JSON（模型偶尔这么吐）/ 全角冒号。"""
    yield t
    yield re.sub(r",\s*([}\]])", r"\1", t)                                  # 尾逗号
    yield t.replace("“", '"').replace("”", '"')                            # 中文双引号
    if t.count('"') < 4:                                                    # 基本没用双引号 → 疑似单引号 JSON
        yield t.replace("'", '"')
    yield t.replace("：", ":")                                              # 全角冒号（仅在字符串外才有意义，试一下无妨）


def _scan_objects(t: str) -> list:
    """把文本里**所有括号配平**的 `{...}` 摘出来（任意层级；截断/畸形时的抢救素材）。

    用**栈**记录每个 `{` 的位置：遇到配对的 `}` 就取一次子串 —— 这样嵌套对象（如 case 里的
    `expect`）也能拿到；引号感知 + 转义感知，字符串里的花括号不算层级。
    ⚠️ 早期版本只取顶层对象，结果 `{"cases": [ {...}` 这种**外层未配平**的截断输出一个都取不到
       （2026-10-10 单测抓到）—— 栈式扫描才是对的做法。
    """
    out, stack, instr, esc = [], [], False, False
    for k, ch in enumerate(t):
        if instr:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                instr = False
            continue
        if ch == '"':
            instr = True
        elif ch == "{":
            stack.append(k)
        elif ch == "}" and stack:
            out.append(t[stack.pop():k + 1])
    return out


def json_of(text: str) -> dict:
    """从模型输出里取 JSON（容忍围栏、前后噪声、常见畸形；**截断时抢救已完整的对象**）。

    解析阶梯：整体 → 最外层花括号 → 若干常见畸形修复 → 最后按括号配平摘出所有完整对象。
    最后一步对「模型输出被 max_tokens 截断」尤其有用（能救回前面已完成的题）。
    """
    t = _strip_fences(text)
    if not t:
        raise ProbeGenError("模型返回空内容（没有可解析的文本）")
    i, j = t.find("{"), t.rfind("}")
    cands = ([t[i:j + 1]] if (i >= 0 and j > i) else []) + [t]
    for c in cands:
        for v in _fixups(c):
            try:
                out = json.loads(v)
                if isinstance(out, dict):
                    return out
            except Exception:  # noqa: BLE001 - 逐个候选试，全失败再走抢救
                pass
    objs = []
    for o in _scan_objects(t):
        try:
            d = json.loads(o)
        except Exception:  # noqa: BLE001
            continue
        if isinstance(d, dict) and "q" in d and ("id" in d or "kind" in d):
            objs.append(d)          # ★ 必须**同时**有 q 和 id/kind 才算一道题（排除嵌套的 expect 对象）
    if objs:
        _log("JSON 不完整/畸形 → 按括号配平抢救出 %d 个完整对象（截断前的部分）" % len(objs))
        return {"cases": objs, "_salvaged": True}
    raise ProbeGenError("模型输出里没有可用 JSON（前 200 字：%s）" % t[:200])


def invoke_json(section: str, user_msg: str, *, stage: str = "", retries: int = 1,
                model=None) -> dict:
    """调模型并**必须**拿到 JSON；两类偶发失败各**自修复一次**：**

    · **空内容**（思考型模型把预算花在 reasoning）→ 追加「直接给 JSON、不要思考/解释」再要一次；
    · **JSON 解析失败**（长输出被吐坏）→ 带着错误再要一次，并硬要求「只输出合法 JSON」。
    为什么要多花这一两次调用：实测失败是**偶发**的（同一模型换个说法就好），而失败一次就扔掉
    整轮出题、让用户去换模型，代价大得多。
    """
    raw = ""
    try:
        raw = invoke(section, user_msg, model=model, stage=stage)
    except ProbeGenError as e:
        if retries <= 0 or "空内容" not in str(e):
            raise
        _log("%s：返回空内容 → 用「不要思考、直接给 JSON」再试一次" % (stage or section))
        raw = invoke(section, user_msg + "\n\n【你上一次没有输出正文（预算可能全用在思考上了）。"
                     "请**直接输出 JSON**，不要思考过程、不要解释、不要代码围栏。】",
                     model=model, stage="%s·空内容重试" % (stage or section))
    try:
        return json_of(raw)
    except ProbeGenError as e:
        if retries <= 0:
            raise
        _log("%s：JSON 解析失败（%s）→ 带着错误重试一次" % (stage or section, e))
        fix = (user_msg + "\n\n【上一次输出不是合法 JSON（错误：%s）。请**只输出合法 JSON**"
               "（不要代码围栏、不要解释、不要开场白），并确保字符串里的引号已正确转义。】" % e)
        return json_of(invoke(section, fix, model=model, stage="%s·修复重试" % (stage or section)))


# ============================================================ 出题 =================
def card_block(account_id: int) -> tuple:
    """**路由器渲染给模型的同一份卡片文本**（出题的唯一材料来源 ✓ 不另拼字段）。

    返回 `(cards_text, entries)`。

    理由（见规划文档 §03 ③C）：若另拼字段，会出现「Agent 按自己想象的工具能力出题，
    而路由器根本看不到那些信息」的错位 —— 那样测出来的错分不清是路由的锅还是出题的锅。
    """
    from tools import capability_pool as cp
    ents = cp.pool_entries(int(account_id))
    return "\n".join(e.card_text for e in ents), ents


def canon_ref(ref: str) -> str:
    """把池里的 ref 归一成可比较的键。

    ★ 口径差：`api:<服务>#<操作>` / `mcp:<服务>/<工具>` 是它们**自己**的形态，
      而 **CLI 条目的 ref 就是裸可执行名**（见 `capability_pool.cli_entry`）——
      不归一会把每个 CLI 都误判成「没有被单工具题覆盖」。
    """
    r = str(ref or "")
    if r.startswith(("api:", "mcp:", "cli:")) or "#" in r or "/" in r:
        return r
    return "cli:%s" % r


def _coverage_missing(doc: dict, entries: list) -> list:
    """哪些 `ref` **还没有**单工具题覆盖（覆盖规则的可复用部分 ✓）。"""
    want = {canon_ref(getattr(e, "ref", "")) for e in entries}
    have = set()
    for c in doc.get("cases") or []:
        exp = c.get("expect") or {}
        if not exp or (c.get("kind") or "single") != "single":
            continue
        if exp.get("kind") == "tool":
            have.add(canon_ref(exp.get("ref")))
        elif exp.get("kind") == "cli":
            have.add("cli:%s" % exp.get("bin"))
    return sorted(want - have)


def coverage_errors(doc: dict, entries: list) -> list:
    """出题硬规则校验（超出 `validate_case_set` 的那部分）：每个 ref 至少 1 道单工具题 ✓。"""
    missing = _coverage_missing(doc, entries)
    return ["这些工具有没有被单工具题覆盖：%s" % "、".join(missing)] if missing else []


def _norm_key(s) -> str:
    """比较用的键：去空白/反引号/引号/括号，忽略大小写（模型写法五花八门）。"""
    return re.sub(r"[\s`'\"（）()【】\[\]：:]+", "", str(s or "")).lower()


def _pool_index(entries) -> tuple:
    """归一用的双映射：**(ref/裸名 → 条目, 中文名 → 条目)**（一次构建，多处复用 ✓）。"""
    by_ref, by_name = {}, {}
    for e in entries:
        ref = str(getattr(e, "ref", ""))
        by_ref[_norm_key(ref)] = e
        by_ref[_norm_key(ref.split("cli:")[-1])] = e        # `cli:weread` → 也认 `weread`
        if canon_ref(ref).startswith("cli:"):               # 模型写 `cli:bili` 也落到这个 CLI 上 ✓
            by_ref[_norm_key("cli:%s" % ref.split("cli:")[-1])] = e
        nm = str(getattr(e, "name", "") or "")
        if nm:
            by_name[_norm_key(nm)] = e
    return by_ref, by_name


def _split_cli_ref(raw: str) -> tuple:
    """把 `bili search` / `bili/search` / `cli:bili favorites` 拆成 `(cli名, [子命令…])`。

    ★ 2026-10-11 用户实测（保存题集被拒）：快模型会把「CLI 名 + 子命令」直接写进
      `expect = {"kind":"tool","ref":"bili search"}` —— 归一若只认「整个字符串等于池里的 ref」，
      这种写法就漏网 → 校验器按「ref 必须 api:/mcp: 开头」拦死 → **题集存不进磁盘、测评跑不了** ✗
    认不出 CLI 名时返回 `(首个词, 其余词)`，由调用方决定要不要采信（不猜 ✓）。
    """
    k = str(raw or "").strip()
    if k.startswith("cli:"):
        k = k[4:]
    parts = [p for p in re.split(r"[\s/]+", k) if p]
    return (parts[0], parts[1:]) if parts else ("", [])


def normalize_expects(cases: list, entries: list) -> int:
    """把模型写的 `expect` 归一到**池子里的真实 ref/bin**（返回修好的条数）。

    ★ 2026-10-10 换成快模型后，真机验证抓到**两类写法错误**（都会让硬规则把好题整批判废）：
      ① CLI 写成 `{"kind":"tool","ref":"weread"}`（协议要 `{"kind":"cli","bin":"weread"}`）；
      ② 更常见：把**中文显示名**写进 `bin`/`ref`（如 `bin="微信读书"`）而不是池里的 `ref`
         —— 覆盖判定（`cli:微信读书` ≠ `cli:weread`）因此永远对不上，**会触发补漏反复要题**
         （实测一轮就把 6 题膨胀到 23 题）。
    ★ 2026-10-11 第三类：**CLI 名 + 子命令**（`ref="bili search"`）→ 拆成
      `{"kind":"cli","bin":"bili","argv":["search"]}` ✓（用户实测：不认这种写法，保存直接被拒）
    做法：建立 **ref ↔ 名称** 双映射，命中就改写成池里的规范形态；**认不出的不猜**（留给硬规则报错 ✓）。
    """
    by_ref, by_name = _pool_index(entries)
    fixed = 0
    for c in cases or []:
        if not isinstance(c, dict):
            continue                                        # 防御：误传 doc / 脏数据不炸 ✓
        exp = c.get("expect")
        if not isinstance(exp, dict):
            continue                                        # 噪声题（expect=None）不碰 ✓
        _raw = str(exp.get("ref") or exp.get("bin") or "")
        key = _norm_key(_raw)
        e = by_ref.get(key) or by_name.get(key)
        argv_from_ref = None
        if e is None:                                       # ★ 再试「CLI 名 + 子命令」写法
            _head, _tail = _split_cli_ref(_raw)
            if _tail:
                _e2 = by_ref.get(_norm_key(_head)) or by_name.get(_norm_key(_head))
                if _e2 is not None and not str(getattr(_e2, "ref", "")).startswith(("api:", "mcp:")):
                    e, argv_from_ref = _e2, _tail           # 只有落在**CLI**上才采信（api:/mcp: 不拆 ✓）
        if e is None:
            continue                                        # 认不出的不猜
        ref = str(getattr(e, "ref", ""))
        want = ({"kind": "tool", "ref": ref} if ref.startswith(("api:", "mcp:"))
                else {"kind": "cli", "bin": ref, "argv": exp.get("argv") or argv_from_ref or []})
        if exp != want:
            c["expect"] = want
            fixed += 1
    return fixed


def normalize_refs_and_alts(cases: list, entries: list) -> dict:
    """归一多工具题的 `refs` 与所有题的 `alts`（都映射成**池内规范 ref**）。

    返回 `{"refs": 改了几条, "alts": 改了几条, "dropped": [认不出被丢弃的...]}`。
    ★ `alts` = 「备选可接受答案」（用户 2026-10-10 要求）：同一件事**别的工具也算对**时用它，
      `expected_brief` / 判分段已经会读它（v3.1 的既有管道），缺的是出题产出与界面可加。
    """
    by_ref, by_name = _pool_index(entries)
    res = {"refs": 0, "alts": 0, "dropped": [], "suit_only": []}

    def _canon(tok):
        e = by_ref.get(_norm_key(tok)) or by_name.get(_norm_key(tok))
        return str(getattr(e, "ref", "")) if e is not None else ""

    def _fix_list(raw, tag, cid):
        kept = []
        for t in raw:
            if isinstance(t, dict):                          # 模型有时吐 `[{"kind":"cli","bin":"weread"}]`
                t = t.get("ref") or t.get("bin") or ""
            if not isinstance(t, str):
                res["dropped"].append("%s 第 %s 条：非字符串项已丢弃" % (tag, cid))
                continue
            r = _canon(t)
            if r:
                if r not in kept:
                    kept.append(r)
                continue
            # ★ 认不出、但**长得像套装名**（`cli:x` / `mcp:server` / `api:svc`，无工具路径）：
            #   保留为**套装标记**而不是丢掉 —— 模型常写套装名当 refs，一丢多工具题就被判
            #   「refs 少于 2 个（无法验证跨套装）」（2026-10-10 真机实测踩到）。
            #   `refs` 只作证据/展示（判分读 `expect`），留个套装标记不影响打分，却让跨套装判据仍可验。
            if re.match(r"^(cli|mcp|api):[^/#\s]+$", t):
                if t not in kept:
                    kept.append(t)
                res["suit_only"].append("%s 第 %s 条：%s" % (tag, cid, t))
            else:
                res["dropped"].append("%s 第 %s 条：%s" % (tag, cid, t))
        return kept

    for c in cases or []:
        if not isinstance(c, dict):
            continue                                        # 防御：误传 doc / 脏数据不炸 ✓
        if isinstance(c.get("refs"), list):
            new = _fix_list(c["refs"], "refs", c.get("id"))
            if new != c["refs"]:
                res["refs"] += 1
            c["refs"] = new
        if isinstance(c.get("alts"), list):
            new = _fix_list(c["alts"], "alts", c.get("id"))
            if new != c["alts"]:
                res["alts"] += 1
            c["alts"] = new
    return res


def suit_of(ref: str) -> str:
    """**套装（来源）**：CLI 的子命令集 / API 的一个服务 / MCP 的一个服务器各算**一个套装**。

    与 `probe_runner.source_of` 同口径（`api:<服务>#<操作>` 取 `#` 前；`mcp:<服务器>/<工具>` 取 `/` 前）。
    为什么需要它（用户 2026-10-10 归因）：29 条是「套装工具」的 29 个**入口**，
    同一套装里换子命令/换操作**不算多工具题** —— 判据必须落在「套装」这一层。
    """
    r = canon_ref(ref)
    if r.startswith(("api:", "mcp:")):
        from tools import probe_runner as pr
        return pr.source_of(r)
    return "cli:%s" % r.split("cli:")[-1]


def suit_violations(doc: dict) -> dict:
    """**单/多工具题的套装判据**（用户 2026-10-10 两条裁定，一体两面）：

      · **单工具题** = 只牵涉**一个套装**（同一套装里可以调多条子命令/多次，例：
        先 `wechat-cli history` 再 `wechat-cli search` —— 仍是单工具题 ✓）；
      · **多工具题** = 必须牵涉 **≥2 个不同套装**。

    返回 `{"multi": [...], "single": [...]}`（空列表 = 该类通过）。判据只对**给了 `refs`** 的题生效
    （refs 是机器可读证据；没给就不猜，留给人工过目 ✓）。
    """
    out = {"multi": [], "single": []}
    for c in doc.get("cases") or []:
        if not isinstance(c, dict):
            continue
        kind = str(c.get("kind") or "single")
        refs = [str(x) for x in (c.get("refs") or []) if str(x).strip()]
        if kind == "noise":
            continue
        cid = c.get("id")
        if kind == "multi":
            if len(refs) < 2:
                out["multi"].append("第 %s 条标为多工具，但 `refs` 少于 2 个（无法验证跨套装）" % cid)
                continue
            suits = sorted({suit_of(r) for r in refs})
            if len(suits) < 2:
                out["multi"].append("第 %s 条标为多工具，但只涉及一个套装（%s）—— "
                                    "同一套装换子命令/换操作不算多工具题" % (cid, "、".join(suits)))
        else:                                               # single
            if not refs:
                continue
            suits = sorted({suit_of(r) for r in refs})
            if len(suits) > 1:
                out["single"].append("第 %s 条标为单工具，却牵涉多个套装（%s）—— "
                                     "单工具题只能用一个套装（同套装内可多条子命令）"
                                     % (cid, "、".join(suits)))
    return out


def multi_violations(doc: dict) -> list:
    """兼容入口：只要多工具题那部分（旧调用点与单测用）。"""
    return suit_violations(doc)["multi"]


NOISE_SEARCHY = re.compile(r"推荐|最新的?|新闻|多少钱|价格|报价|天气|排名|榜单|攻略|评测|几点|"
                           r"什么时候|谁是|走势|汇率|怎么样才能|哪里能买")


def noise_warnings(doc: dict) -> list:
    """噪声题若像「需要查资料」的问题，很可能**诱发网络搜索**（烧掉搜索额度，用户 2026-10-10 指出）。"""
    out = []
    for c in doc.get("cases") or []:
        if str(c.get("kind") or "") != "noise":
            continue
        m = NOISE_SEARCHY.search(str(c.get("q") or ""))
        if m:
            out.append("第 %s 条噪声题像「需要查资料」的问题（命中「%s」）—— 会诱发网络搜索、烧搜索额度；"
                       "建议换成闲聊/写作类" % (c.get("id"), m.group(0)))
    return out


def suits_brief(entries) -> str:
    """把池子按**套装**分组，交给出题 Agent（多工具题必须跨组，它得先看得见有哪些组 ✓）。"""
    groups = {}
    for e in entries:
        groups.setdefault(suit_of(getattr(e, "ref", "")), []).append(str(getattr(e, "ref", "")))
    return "\n".join("- 套装 `%s`：%s" % (k, "、".join(v)) for k, v in sorted(groups.items()))


SINGLE_BATCH = 6        # 单工具题每批的工具数（批小 → 输出短 → JSON 稳 + 进度更细）


# ---------------------------------------------------------------- 无对象自检（**确定性**，不调模型）
_VAGUE_LEAD = ("一种|一款|一个|一部|一本|一首|一张|一份|某个|某部|某本|某款|某家|那部|那本|那款|那家|"
               "这个|那个|这款|这本|那只|它|它们|他们|"
               "我在看的|我最近看的|我上次看的|我在吃的|我最近在吃的|我正在吃的|我用的|我买的|我收藏的|上次说的")
_VAGUE_CAT = ("新?药|药品|视频|电影|电视剧|短剧|剧|书|书籍|小说|文章|论文|文献|商品|东西|产品|表格|文件|文档|报告|"
              "材料|资料|地图|城市|地方|酒店|路线|车票|机票|游戏|歌|专辑|播客")
_VAGUE_RE = re.compile("(?:%s)[^，。；、！？]{0,8}?(?:%s)" % (_VAGUE_LEAD, _VAGUE_CAT))
# 纯起首代词（「它讲了什么」「这个是怎么回事」）——只在**句首**算，避免误伤「帮我查阿司匹林，它的相互作用」✓
_BARE_START_RE = re.compile(r"^(?:它|它们|他们|这|那|这个|那个|这款|那款)")
_ANCHOR_RE = re.compile(r"《[^》]{1,60}》|〈[^〉]{1,60}〉|[「『“‘\"'`][^」』”’\"'`]{1,60}[」』”’\"'`]"
                        r"|(?:BV[0-9A-Za-z]{6,})|(?:[A-Za-z]{1,6}\d{2,}[A-Za-z0-9]*)|(?:\d{3,})"
                        # 「那个叫阿司匹林的药」这类**具名短语**算点名了对象（实测误报，2026-10-12）
                        r"|(?:叫|叫做|名为)[^，。；、！？]{1,20}?(?:的|药|人|书|视频|商品)")


def vague_check(cases: list) -> dict:
    """**确定性**自检：题面里「有类别、没具体对象」的题（用户 2026-10-12）。

    真机取证：出题 Agent 出了「我正要开始吃一种新药，但担心它和我正在吃的降压药有冲突，先查查这药的
    说明书…」——**药名一个没给**，Agent 根本无从下手 ✗；同批还有「我看的那个 bili 视频讲了什么」这类，
    尼古喵喵那轮也有同款（「我看的那个 bili 视频讲了什么」）。
    判定口径（纯正则，**不调模型** ✓ 不烧额度）：
      · 命中「不定指代 + 类别词」——一种新药 / 我在吃的降压药 / 那个商品 / 一部电影 / 它…；**且**
      · 题面里**没有任何具体对象锚点**——书名号《》〈〉、引号串、「」、BV 号或字母数字编号、3 位以上数字。
      例：「帮我看下 BV1xx411c7mD 这个视频讲了什么」有锚点 → 不报 ✓；
          「我收藏的那个商品现在多少钱」无锚点 → 报 ✓（应写「我收藏的『奢莉 水果啤酒』」）。
    只**提醒**、不拦保存（人工过目才是闸门 ✓）。返回 `{"ids":[...], "why":{题号:理由}, "hits":{题号:命中片段}}`。
    """
    ids, why, hits = [], {}, {}
    for c in cases or []:
        try:
            cid = int(c.get("id"))
        except (TypeError, ValueError):
            continue
        q = str(c.get("q") or "")
        if not q:
            continue
        m = _VAGUE_RE.search(q) or _BARE_START_RE.search(q)
        if not m or _ANCHOR_RE.search(q):
            continue
        ids.append(cid)
        hits[cid] = m.group(0)
        why[cid] = ("只说「%s」，没给具体是哪个 —— 换成具体的药名/书名/视频号/商品名，"
                    "或把检索词直接写进题面（素材里确实没有就换题）" % m.group(0))
    return {"ids": ids, "why": why, "hits": hits}


def privacy_check(cases: list, entries: list, *, model=None) -> dict:
    """出题 Agent **自己再遍历一遍题目**，挑出「答案只能来自**用户个人数据**」的题（用户 2026-10-11）。

    为什么需要（真机取证）：题目「我最近在 B 站看过哪些 AI 生图视频」「我 B 站收藏里关于 python 的
    视频」—— 用户**根本没看过、也没收藏过** → Agent 逐页翻历史记录（审计日志：`bili favorites
    --page 1..11`，单题跑了 4 分钟）→ **时长爆炸** ✗
    判定口径（用户原话：「比如 xx 作家有哪些作品等需要工具查询但是与用户个人信息无关的信息不需要
    提醒」）：
      · **要提醒**：答案依赖「用户自己的数据」—— 聊天记录 / 收藏 / 书架与阅读进度 / 邮件 / 日程 / 订单…；
      · **不提醒**：需要工具查、但属于**公开信息**的 —— 「某作家有哪些作品」「火车票」「天气」「热榜」✓。
    返回 `{"ids": [...], "why": {题号: 理由}}`（失败时多一个 `error`，**绝不影响出题** ✓）。
    """
    valid = {int(c.get("id")) for c in cases if c.get("id") is not None}
    if not valid:
        return {"ids": [], "why": {}}
    from tools import probe_runner as pr
    lines = []
    for c in cases:
        e = c.get("expect")
        lines.append("#%s ｜ 题面：%s ｜ 期望工具：%s"
                     % (c.get("id"), str(c.get("q"))[:140],
                        pr.expected_brief(e) if e is not None else "（不调用任何工具）"))
    ask = ("【本轮的题目】\n%s\n\n"
           "【任务】逐题判断：**这道题的答案是不是只能来自「这位用户自己的数据」**"
           "（聊天记录 / 收藏 / 书架与阅读进度 / 邮件 / 日程 / 订单 这类**私有数据**）。\n"
           "· 只列**依赖用户个人数据**的题号；\n"
           "· **公开信息不要列**（例：「某作家有哪些作品」「火车票/车次」「天气」「抖音热榜」"
           "「某游戏的发行日期」—— 这些需要工具查，但与这位用户自己的数据无关）✗\n"
           "· 拿不准就**不列**（宁可少提醒 ✓）。\n\n"
           "【输出格式（只输出 JSON，不要围栏、不要解释）】\n"
           '{"cases":[{"id":3,"why":"问的是他自己的B站观看历史"},{"id":7,"why":"他自己的微信读书书架"}]}\n'
           "没有要提醒的就输出 {\"cases\":[]}" % "\n".join(lines))
    try:
        data = invoke_json("probe_writer", ask, stage="个人数据核查", model=model)
    except Exception as e:  # noqa: BLE001 - 核查失败绝不能影响出题 ✓
        return {"ids": [], "why": {}, "error": "%s: %s" % (type(e).__name__, str(e)[:160])}
    ids, why = [], {}
    for it in (data.get("cases") or []):
        try:
            i = int(it.get("id"))
        except (TypeError, ValueError):
            continue
        if i in valid and i not in ids:
            ids.append(i)
            why[i] = str(it.get("why") or "").strip()[:80]
    return {"ids": sorted(ids), "why": why}


def gen_cases(account_id: int, username: str, *, prev_questions=None, model=None,
              on_progress=None, concurrency=None) -> dict:
    """出题（**闸门 1 之前**）：**分批**出题 —— 返回 dict(doc, errors, count, multi, pool_size, stages)。

    ① 单工具题：池子切成每批 `SINGLE_BATCH` 个工具，**每批一次调用**（每个 `ref` 恰好 1 题）；
    ② 多工具题：`max(2, ⌈N/6⌉)` 条，一次调用；③ 噪声题：2 条，一次调用。

    ★ 为什么分批（用户 2026-10-10 实测**连续两次出题失败**）：原提示词要求**一次**吐 36 条题的
      长 JSON（单工具 N + 多工具 M + 噪声 2）—— 长输出 + 中文引号下模型极易吐坏
      （`JSONDecodeError: Expecting ',' delimiter`）或被 max_tokens 截断；分批后每批 JSON 只有
      几百字，**稳定性显著提高**，而且进度是**真的**（能显示正在出哪一批、覆盖哪些工具）。
    ★ 为什么**并发**（同日追修）：实测思考型模型**一批要 3~6 分钟**，7 批串行 ≈ 20 分钟。
      单工具题各批**互不依赖** → 用线程池并发跑（`PROBE_GEN_CONCURRENCY`，默认 3），墙钟降到 1~2 批的时间。
    ★ 为什么**部分成功也返回**（同日追修）：某批失败不该把已完成批次一起扔掉 —— 改成把失败批次
      记进 `warnings`，题目照常返回，硬规则校验会点名「哪些工具没被覆盖」，用户可手工补或只重跑该批。
      只有**全部批次都失败**才抛错。
    `on_progress(stage, name, done, total, detail)`：`stage ∈ single/multi/noise`。
    """
    import math
    import threading
    from concurrent.futures import ThreadPoolExecutor, as_completed

    cards, ents = card_block(int(account_id))
    n = len(ents)
    multi = max(2, math.ceil(n / 6))
    batches = [ents[i:i + SINGLE_BATCH] for i in range(0, n, SINGLE_BATCH)]
    total_steps = len(batches) + 3        # + 多工具 + 噪声 + **个人数据核查**（2026-10-11 加）
    workers = int(concurrency or os.getenv("PROBE_GEN_CONCURRENCY") or 3)
    workers = max(1, min(workers, max(1, len(batches))))
    lock = threading.Lock()
    state = {"done": 0, "active": []}
    cases, failures, salvaged = [], [], False

    def _running():
        with lock:
            return "、".join(state["active"])

    def _tick(stage, name, detail=None):
        """把当前快照推给前端：`done` 是**已完成步数**，`active` 是**正在跑的批**。"""
        with lock:
            done = state["done"]
        if on_progress:
            try:
                on_progress(stage, name, done, total_steps,
                            detail if detail is not None
                            else (("正在跑 %d 批：%s" % (len(state["active"]), _running()))
                                  if state["active"] else ""))
            except Exception:  # noqa: BLE001 - 进度回调失败绝不影响出题
                pass

    def _begin(label):
        with lock:
            state["active"].append(label)

    def _finish(label):
        with lock:
            if label in state["active"]:
                state["active"].remove(label)
            state["done"] += 1

    prev_block = ""
    if prev_questions:
        prev_block = ("\n\n【往期题面（**不要重复或换汤不换药**）】\n"
                      + "\n".join("- %s" % q for q in list(prev_questions)[:60]))

    def _batch_label(bi):
        names = "、".join((getattr(e, "name", "") or getattr(e, "ref", "")) for e in batches[bi - 1])
        return "第 %d/%d 批" % (bi, len(batches)), names

    def _single_batch(bi):
        """一批单工具题（供线程池调用；**不碰共享状态**，避免竞态 ✓）。"""
        label, names = _batch_label(bi)
        cards_b = "\n".join(e.card_text for e in batches[bi - 1])
        ask = ("【工具池卡片】本批共 %d 个工具（`ref` 必须逐字照抄）\n\n%s\n\n"
               "【本轮范围（**覆盖上文题量公式，以下为准**）】**只出单工具题**："
               "上面每个工具**恰好 1 条**；`kind` 一律 `single`；`expect` 指向该工具。"
               "若同一件事**还有别的工具也算对**，加 `alts`（字符串数组，写池内 ref 或可执行名）✓；"
               "并给 `refs`（本套装用到的工具，**≥1 个**）。"
               "**不要**出多工具题、不要出噪声题。%s" % (len(batches[bi - 1]), cards_b, prev_block))
        return invoke_json("probe_writer", ask, stage="单工具题 %s" % label, model=model)

    # ---- ① 单工具题：**并发**跑各批 ----
    if batches:
        for bi in range(1, len(batches) + 1):
            label, names = _batch_label(bi)
            _begin(label)
            _tick("single", "单工具题 %s" % label,
                  "本批工具：%s ｜ %s" % (names, _running()))
        got = {}
        with ThreadPoolExecutor(max_workers=workers) as ex:
            futs = {ex.submit(_single_batch, bi): bi for bi in range(1, len(batches) + 1)}
            for fut in as_completed(futs):
                bi = futs[fut]
                label, names = _batch_label(bi)
                try:
                    got[bi] = fut.result()
                except Exception as e:  # noqa: BLE001 - 单批失败不拖垮整轮
                    failures.append(("单工具题 %s" % label, str(e)))
                _finish(label)
                _tick("single", "单工具题", "第 %d/%d 批刚结束（共 %d 批并发 %d）"
                      % (bi, len(batches), len(batches), workers))
        for bi in sorted(got):
            salvaged = salvaged or bool(got[bi].get("_salvaged"))
            cases += [c for c in (got[bi].get("cases") or []) if isinstance(c, dict)]

    # ---- ② 多工具题 / ③ 噪声题（各一次，串行）----
    multi_ask = (
        "【工具池卡片】\n\n%s\n\n【本池的**套装**分组（同组内换子命令/换操作**不算**多工具题）】\n%s\n\n"
        "【本轮范围（**覆盖上文题量公式，以下为准**）】**只出多工具题 %d 条**："
        "一条题面里要求做**两件可并存的事**，且**必须跨「套装」**（上面不同组的工具协作）；"
        "`kind` 一律 `multi`；`expect` 写第一个动作的工具；"
        "**必须给 `refs`**：涉及的套装工具 ref 列表（**≥2 个、来自不同套装**，写池内 ref 或 CLI 可执行名）。"
        "**不要**出单工具题、不要出噪声题。%s" % (cards, suits_brief(ents), multi, prev_block))
    noise_ask = (
        "【工具池卡片】\n\n%s\n\n【本轮范围（**覆盖上文题量公式，以下为准**）】"
        "**只出噪声题 2 条**：内容是这个池子**确实答不了**的问题；"
        "★ 必须是**不需要查资料**的类型（闲聊 / 写作 / 情绪陪伴 / 常识），"
        "**绝不要**「推荐 / 最新 / 多少钱 / 天气 / 新闻 / 排名」这类**会诱发网络搜索**的问题"
        "（那会烧掉用户的搜索额度）；`kind` 一律 `noise`；`expect` 为 `null`。"
        "**不要**出单工具题、不要出多工具题。%s" % (cards, prev_block))
    for stage, name, ask in (("multi", "多工具题", multi_ask), ("noise", "噪声题", noise_ask)):
        _begin(name)
        _tick(stage, name, "本步单独跑：%s" % name)
        try:
            doc = invoke_json("probe_writer", ask, stage=name, model=model)
            salvaged = salvaged or bool(doc.get("_salvaged"))
            cases += [c for c in (doc.get("cases") or []) if isinstance(c, dict)]
        except Exception as e:  # noqa: BLE001
            failures.append((name, str(e)))
        _finish(name)
        _tick(stage, name, "%s 结束" % name)

    # ---- 合并：题面去重 + 连续重编号（校验器要求 1..N）----
    def _merge(seq):
        seen, out = set(), []
        for c in seq:
            q = str(c.get("q") or "").strip()
            if not q or q in seen:
                continue
            seen.add(q)
            out.append(c)
        for i, c in enumerate(out, 1):
            c["id"] = i                 # ★ 校验器要求 id 连续 1..N
        return out

    def _warn_text(lab, msg):
        """失败文案去重复前缀（`msg` 里常已带阶段名，直接拼会出现「第 2/2 批：第 2/2 批…」）。"""
        m = str(msg)
        for pre in (lab + "：", lab + "·"):
            if m.startswith(pre):
                m = m[len(pre):]
                break
        return "%s：%s" % (lab, m[:200])

    merged = _merge(cases)
    warnings = [_warn_text(lab, msg) for lab, msg in failures]
    if not merged:
        raise ProbeGenError("出题失败：所有批次都没拿到题目（%s）"
                            % ("；".join(warnings) if warnings else "无输出"))

    # ---- ④ 归一写法（弱模型常把 CLI 写成 kind=tool / 写中文名 → 硬规则会整批判废）----
    fixed = normalize_expects(merged, ents)
    nz = normalize_refs_and_alts(merged, ents)
    if fixed or nz["refs"] or nz["alts"] or nz["dropped"]:
        _log("归一：expect %d 条 · refs %d 条 · alts %d 条%s"
             % (fixed, nz["refs"], nz["alts"],
                ("；认不出被丢弃：%s" % "；".join(nz["dropped"])) if nz["dropped"] else ""))
    warnings += ["归一丢弃了认不出的答案（%s）—— 请人工核对或删掉该备选" % d for d in nz["dropped"]]
    if nz.get("suit_only"):
        warnings.append("有 refs 只给到**套装名**（%s）—— 已保留作套装证据，建议人工点开题集确认到具体工具"
                        % "；".join(nz["suit_only"][:3]))

    # ---- ⑤ 补漏：只对**可验证的**缺口出手（硬规则告诉你哪些 ref 还没被覆盖）----
    missing = _coverage_missing({"cases": merged}, ents)
    if missing:
        by_key = {canon_ref(getattr(e, "ref", "")): e for e in ents}
        sub = [by_key[k] for k in missing if k in by_key]
        if sub:
            names = "、".join((getattr(e, "name", "") or getattr(e, "ref", "")) for e in sub)
            _tick("single", "补漏", "为 %d 个未覆盖的工具补题：%s" % (len(sub), names))
            try:
                doc2 = invoke_json(
                    "probe_writer",
                    ("【工具池卡片】下面这些工具**还没有题**（`ref` 逐字照抄）\n\n%s\n\n"
                     "【本轮范围（**覆盖上文题量公式，以下为准**）】**只出单工具题**："
                     "上面每个工具**恰好 1 条**；`kind` 一律 `single`；"
                     "**CLI 类工具的 `expect` 必须写成 `{\"kind\":\"cli\",\"bin\":\"<可执行名>\","
                     "\"argv\":[]}`**；api/mcp 类才用 `{\"kind\":\"tool\",\"ref\":\"<ref>\"}`；"
                     "并给 `refs`（本套装用到的工具，≥1 个）。"
                     "**不要**出多工具题、不要出噪声题。%s"
                     % ("\n".join(e.card_text for e in sub), prev_block)),
                    stage="补漏（%d 个工具）" % len(sub), model=model)
                cases += [c for c in (doc2.get("cases") or []) if isinstance(c, dict)]
                merged = _merge(cases)
                normalize_expects(merged, ents)
                _log("补漏完成：补进 %d 条，现共 %d 题" % (len(doc2.get("cases") or []), len(merged)))
            except Exception as e:  # noqa: BLE001 - 补漏失败不影响已得到的题
                warnings.append("补漏失败：%s" % _warn_text("补漏", str(e))[:200])
            state["done"] = total_steps
            _tick("single", "补漏", "补漏结束（现共 %d 题）" % len(merged))

    # ---- ⑥ 套装判据（用户 2026-10-10 两条裁定，一体两面）----
    #     多工具题**必须跨套装**；单工具题**只能用一个套装**（同套装内可多条子命令）。
    #     违规就**重出一次**（把违规题交给模型、明确规则），仍不合格则记 warning 交人工过目。
    viol = suit_violations({"cases": merged})
    if viol["multi"] or viol["single"]:
        _log("套装判据不合规：多工具 %d 条 / 单工具 %d 条" % (len(viol["multi"]), len(viol["single"])))
        _tick("multi", "重出不合规题", "多工具 %d · 单工具 %d" % (len(viol["multi"]), len(viol["single"])))
        bad_ids = {c.get("id") for c in merged
                   if str(c.get("kind") or "") in ("multi", "single")
                   and "第 %s " % c.get("id") in " ".join(viol["multi"] + viol["single"])}
        bad = [c for c in merged if c.get("id") in bad_ids]
        bad_refs = []
        for c in bad:
            for r in (c.get("refs") or []):
                if r not in bad_refs:
                    bad_refs.append(r)
            exp = c.get("expect") or {}
            r0 = exp.get("ref") or exp.get("bin")
            if r0 and r0 not in bad_refs:
                bad_refs.append(r0)
        want_refs = [e for e in ents if str(getattr(e, "ref", "")) in set(bad_refs)]
        try:
            doc4 = invoke_json(
                "probe_writer",
                ("【工具池卡片】下面这些工具（`ref` 逐字照抄）\n\n%s\n\n"
                 "【本池的**套装**分组（同组内换子命令/换操作算**同一个**套装）】\n%s\n\n"
                 "【本次任务】**重出下面这些不合格的题**（题面必须改写、不要照抄旧题面）：\n%s\n\n"
                 "【规则】① **单工具题**（kind=`single`）只能牵涉**一个套装**，但**同套装内可以调多条子命令/多次**；"
                 "② **多工具题**（kind=`multi`）必须由**≥2 个不同套装**的工具协作；"
                 "③ 每题都要给 `refs`（单工具题：本题用到的本套装工具 ≥1 个；多工具题：≥2 个不同套装）；"
                 "④ 单工具题每个工具至少 1 条；⑤ 只输出 JSON。%s"
                 % ("\n".join(e.card_text for e in want_refs) if want_refs else "（见上）",
                    suits_brief(ents), "\n".join("- " + v for v in (viol["multi"] + viol["single"])),
                    prev_block)),
                stage="重出不合规题", model=model)
            good = [c for c in (doc4.get("cases") or [])
                    if isinstance(c, dict) and str(c.get("kind") or "") in ("single", "multi")]
            if good:
                merged = _merge([c for c in merged if c.get("id") not in bad_ids] + good)
                _log("重出完成：换掉 %d 条不合规题，补进 %d 条" % (len(bad), len(good)))
        except Exception as e:  # noqa: BLE001 - 重出失败不影响已得到的题
            warnings.append("不合规题重出失败：%s" % _warn_text("重出", str(e))[:200])
        _tick("multi", "重出不合规题", "结束")

    # 最后一次归一（补漏 / 重出加进来的题也要过一遍 ✓）
    normalize_expects(merged, ents)
    normalize_refs_and_alts(merged, ents)
    _sv = suit_violations({"cases": merged})
    warnings += (_sv["multi"] + _sv["single"])[:2]      # 若还不行，最多留两条提醒（不刷屏）
    warnings += noise_warnings({"cases": merged})

    kinds = {}
    for c in merged:
        k = str(c.get("kind") or "single")
        kinds[k] = kinds.get(k, 0) + 1
    if not kinds.get("noise"):
        warnings.append("噪声题 0 条（这一批失败了）—— 噪声假阳性这轮测不出来，建议重跑或手工补 2 条")
    if kinds.get("multi", 0) < multi:
        warnings.append("多工具题只有 %d 条（期望 %d）—— 多工具覆盖会偏弱" % (kinds.get("multi", 0), multi))
    out_doc = {"name": "probe_%s" % username, "account": username,
               "source": "probe_writer 分批生成（%s，并发 %d）" % (time.strftime("%F %T"), workers),
               "cases": merged}

    # ---- ⑦ 个人数据核查（用户 2026-10-11 要求）----
    #   出题 Agent **自己再遍历一遍题目**，挑出「答案只能来自**用户自己的数据**」的题，
    #   写进题单**最上方**的提示信息。为什么必须（真机取证）：「我最近在 B 站看过哪些 AI 生图视频」
    #   「我 B 站收藏里关于 python 的视频」—— 用户**根本没看过/没收藏过** → Agent 逐页翻历史
    #   （审计日志 `bili favorites --page 1..11`）→ 单题跑了 4 分钟、时长爆炸 ✗
    #   公开信息题（某作家有哪些作品 / 火车票 / 天气 / 热榜）**不提醒** ✓
    _begin("个人数据核查")
    _tick("privacy", "个人数据核查", "本步单独跑：个人数据核查（遍历题单，挑出依赖你个人数据的题）")
    priv = privacy_check(merged, ents, model=model)
    if priv.get("error"):
        warnings.append("个人数据核查失败：%s —— 请自行核对「答案依赖你自己数据」的题" % priv["error"][:140])
    elif priv.get("ids"):
        _ids = priv["ids"]
        warnings.insert(0, ("⚠ **第 %s 题依赖「你的个人数据」** —— 请先确认你**确实有**这些数据"
                            "（%s）；没有就删掉或改题，否则 Agent 会逐页翻找、单题耗时可能到上限 ✓")
                           % ("/".join(str(i) for i in _ids),
                              "；".join("第%s题：%s" % (i, priv["why"].get(i) or "你自己的数据")
                                       for i in _ids[:3])))
    out_doc["privacy"] = priv
    _finish("个人数据核查")
    _tick("privacy", "个人数据核查", "个人数据核查 结束（%d 条需核对）" % len(priv.get("ids") or []))

    # ---- ⑧ 无对象自检（**确定性**，不调模型、不加进度步，2026-10-12 用户追加）----
    #   真机取证：「我正要开始吃一种新药…」药名一个没给 → Agent 无从下手 ✗
    #   口径见 `vague_check`：命中「不定指代 + 类别词」且题面没有任何具体对象锚点 → 标黄提醒（不拦保存）
    vg = vague_check(merged)
    if vg.get("ids"):
        warnings.insert(0, ("⚠ **第 %s 题没有具体对象** —— 题面里要点名「要查的是什么」"
                            "（药名 / 书名 / BV 号 / 商品名 / 具体检索词）；"
                            "没有对象就改成具体检索词、或直接换题 ✓（%s）")
                           % ("/".join(str(i) for i in vg["ids"]),
                              "；".join("第%s题命中「%s」" % (i, vg["hits"].get(i) or "")
                                       for i in vg["ids"][:3])))
    out_doc["vague"] = vg

    from tools import probe_runner as pr
    errors = pr.validate_case_set(out_doc) + coverage_errors(out_doc, ents)
    _log("出题结束：%d 题 / 池 %d 条 / 多工具 %d / %d 步（并发 %d）%s%s"
         % (len(merged), n, multi, total_steps, workers, "（含抢救）" if salvaged else "",
            ("；失败 %d 批：%s" % (len(failures), "；".join(warnings))) if failures else
            ("；硬规则不过：" + "；".join(errors) if errors else " ✓")))
    return {"doc": out_doc, "errors": errors, "warnings": warnings, "failed": len(failures),
            "count": len(merged), "multi": multi, "pool_size": n, "stages": total_steps,
            "concurrency": workers, "salvaged": salvaged, "normalized": fixed,
            "alts": nz["alts"], "with_alts": len([c for c in merged if c.get("alts")]),
            "multi_ok": not suit_violations({"cases": merged})["multi"],
            "single_ok": not suit_violations({"cases": merged})["single"]}


def save_cases(username: str, doc: dict) -> str:
    """把题集存成 `user_data/probe_sets/<用户名>.json`（产品数据，**不写进仓库** ✓ 裁定 13；2026-10-13 归口）。"""
    os.makedirs(SET_DIR, exist_ok=True)
    path = os.path.join(SET_DIR, "%s.json" % username)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(doc, f, ensure_ascii=False, indent=1)
    return path


def load_cases(username: str) -> dict:
    path = os.path.join(SET_DIR, "%s.json" % username)
    if not os.path.isfile(path):
        return {}
    with open(path, "r", encoding="utf-8") as f:
        doc = json.load(f)
    doc["_path"] = path
    return doc


def clear_cases(username: str) -> bool:
    path = os.path.join(SET_DIR, "%s.json" % username)
    if os.path.isfile(path):
        os.remove(path)
        return True
    return False


# ============================================================ 判分（预填）===========
def judge_run(run: dict, *, model=None) -> dict:
    """Agent 判分**预填**（人工终判 ✓）。输入只给「题面 + 期望 + 实际调用序列」。

    ★ 刻意不给「出题时的推理 / 出题人是谁」—— 那是本段与出题段解耦的关键（防自出题自判分）。
    ★ 默认用**判分专用模型**（`PROBE_LLM_JUDGE_MODEL`，不填=沿用出题模型 ✓）：判分要判断力、出题要快。
    """
    from tools import probe_runner as pr
    if model is None:
        model = probe_model("judge")
    lines = []
    for r in run.get("rows") or []:
        exp = pr.expected_brief(r.get("expect"))
        calls = r.get("calls") or []
        seq = "\n".join("    %d. %s" % (i, c["brief"]) for i, c in enumerate(calls, 1)) \
            or "    （一次工具都没调）"
        flag = "（该题标记为未测：限流/故障）" if r.get("unmeasured") else ""
        lines += ["### 第 %s 条%s" % (r.get("id"), "｜类型：噪声题" if r.get("kind") == "noise" else ""),
                  "- 题面：%s" % r.get("q"),
                  "- 期望工具：%s" % exp,
                  "- 实际调用序列：", seq, ""]
    data = invoke_json("probe_judge", "\n".join(lines), stage="判分", model=model)
    out = {}
    for s in (data.get("scores") or []):
        try:
            out[int(s.get("id"))] = {"verdict": str(s.get("verdict") or "").strip(),
                                     "why": str(s.get("why") or "").strip(),
                                     "conf": str(s.get("conf") or "").strip()}
        except (TypeError, ValueError):
            continue
    return {"scores": out, "raw": ""}          # raw 不再回传（原始输出已进 `[probe_agent]` 日志）


# ============================================================ 词法建议 ==============
def _health_lines(items) -> list:
    """把「静态体检」条目渲染成给建议 AI 看的文本行。

    ★ 2026-10-12 真机 bug（用户报「建议生成失败：TypeError: sequence item 0: expected str instance,
      dict found」）：体检查出的 `problems` 是**字典列表**（`{code,severity,msg}`，见 capability_lint），
      而这里原来直接 `"；".join(h["problems"])` → 拼字典即炸 ✗。尼古喵喵的池子当时体检**无问题**，
      所以一直没暴露；药喵的 arXiv 工具名是英文标识符 → 池子第一次有体检问题就炸 ✗。
    这里按 `severity`（hard/soft → 硬/软）+ `msg` 渲染，并**同时容忍 str 老形状** ✓。
    """
    out = []
    for h in items or []:
        ps = []
        for p in (h.get("problems") or []):
            if isinstance(p, dict):
                sev = {"hard": "硬", "soft": "软"}.get(str(p.get("severity") or "hard").lower(), "硬")
                ps.append("[%s] %s" % (sev, str(p.get("msg") or p.get("code") or "").strip()))
            else:
                ps.append(str(p))
        out.append("- %s（%s）：%s" % (h.get("ref"), h.get("name") or "",
                                      "；".join([x for x in ps if x]) or "无"))
    return out


def advise(account_id: int, run: dict, scores=None, *, model=None) -> dict:
    """词法改进建议（只改素材 ✓）。输入 = 素材 + 本轮逐题结果 + 静态体检硬规则结论。"""
    from tools import probe_runner as pr
    from tools import capability_lint as lint
    pool = pr.pool_overview(int(account_id))
    parts = ["【工具池素材】", ""]
    for it in pool.get("items") or []:
        # ★ 2026-10-11（用户抓到的缺口）：CLI 的能力描述住在 `user_clis.abilities`
        #   （池里是镜像，`desc_state` 会显示 auto），原来这里只读 `abilities_user`
        #   → CLI 条目一律显示「描述：（空）」，词法建议**根本没法针对 CLI 能力描述提建议** ✗
        #   现在：① 读**生效描述** `desc_text`；② 明确标出「这条该改哪个框」（CLI=能力描述框）
        src = str(it.get("source") or "")
        is_cli = src.startswith("cli")
        desc = (it.get("desc_text") or it.get("abilities_user") or "").strip() or "（空）"
        parts += ["- ref：%s（%s）" % (it.get("ref"), "CLI 工具" if is_cli else "API/MCP 工具"),
                  "  名称：%s" % (it.get("name") or "（空）"),
                  "  keywords：%s" % (it.get("keywords", "").strip() or "（空）"),
                  "  %s：%s" % ("能力描述（改在「技能与工具 → 接入 CLI」的「能力描述」框）" if is_cli
                              else "工具描述（改在工具描述框）", desc)]
    parts += ["", "【静态体检结论（代码硬规则，不是模型判的）】"]
    health = pool.get("health") or {}
    items = health.get("items") or []
    if items:
        parts += _health_lines(items)
    else:
        parts.append("- 无（或未跑到体检）")
    parts += ["", "【本轮测评结果（人工终判为准）】", ""]
    scores = {int(k): v for k, v in (scores or {}).items()}
    for r in run.get("rows") or []:
        s = scores.get(int(r.get("id") or 0)) or {}
        parts += ["- 第 %s 条（%s）｜期望：%s｜实际：%s｜判定：%s｜备注：%s"
                  % (r.get("id"), r.get("kind"), pr.expected_brief(r.get("expect")),
                     " ／ ".join(c["brief"] for c in (r.get("calls") or [])) or "（未调用）",
                     s.get("verdict") or "（未填）", s.get("note") or "")]
    raw = invoke("probe_advisor", "\n".join(parts), model=model)
    return {"text": raw}
