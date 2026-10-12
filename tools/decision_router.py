# -*- coding: utf-8 -*-
"""决策模型精排（v4.0 大点 02 · 首批）

只做一件事：**在候选互相咬住时，请决策模型复核一次，改写候选顺序**。
- 不改 prompt 注入量、不改「要不要用工具」的判定（噪声归路由器）、不改任何执行链；
- 未启用 / 未触发 / 不可用（超时 / 无额度 / 形状错 / 限流 / 预算用尽）→ 一律返回 None，
  调用方行为**逐字等于现状**（铁律：本层只影响「工具怎么被想起」，不参与「能不能跑」）。
- **总开关**（2026-10-13 用户裁定）：账号级 DB 字段 `accounts.decision_router`，由**前端 AI 对话区的开关**
  切换；`.env` 只留模型与凭据，不存"开不开"。默认 0=关。
- **缓存**：键 = 账号 + 问题 + 池版本 + 素材指纹（含 keywords / abilities_user）+ 模板版本 + 模型；
  **时效 24 小时**（`DECISION_CACHE_TTL_H`），过期即作废并**从文件里删掉**。

方案与裁定：`docs/v4.0/v4.0规划文档.md` §02 ③ 段 · `docs/v4.0/Verdict/02-决策模型仲裁层.md`
标定数据：`docs/v4.0/research/02-Jev仲裁层-demo/jev_near_tie_calibration.json`
"""
from __future__ import annotations

import hashlib
import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path

# ---------------------------------------------------------------- 常量
DECISIONS_PATH = "/api/alpha/decisions"
STATE_TEMPLATE_VERSION = "v2"          # 改动 state 构造时必须 +1（计入缓存键）
#   v1 → v2（2026-10-12）：选项键由「内部键 source:ref」改为「显示键 e.ref」——
#   内部键对 ref 自身带来源前缀的条目会变成 mcp:mcp:... 双层，与实测/题库形态不一致。
ROOT = Path(__file__).resolve().parents[1]
AUDIT_PATH = Path(os.getenv("DECISION_AUDIT_PATH") or (ROOT / "data" / "audit" / "decision.jsonl"))
CACHE_PATH = Path(os.getenv("DECISION_CACHE_PATH") or (ROOT / "data" / "decision_cache.jsonl"))
_CACHE: dict[str, dict] | None = None
_SESSION = {"calls": 0, "hits": 0, "skips": 0}


# ---------------------------------------------------------------- 配置（默认全关）
def _env(name: str, default: str = "") -> str:
    return (os.getenv(name, default) or default).strip()


def switch_on(account_id: int | None) -> bool:
    """前端开关：账号级 DB 字段 `accounts.decision_router`（**默认 0=关**）。

    2026-10-13 用户裁定：开不开**不再写进 `.env`**（`.env` 只留模型与凭据），
    改由前端 AI 对话区的开关一键切换、按账号生效。读库失败一律当**关**（fail-safe）。
    """
    if not account_id:
        return False
    try:
        from tools.schema_personal import get_personal_conn, ensure_tables
        ensure_tables()
        conn = get_personal_conn()
        try:
            row = conn.execute("SELECT COALESCE(decision_router, 0) FROM accounts WHERE id=?",
                               (int(account_id),)).fetchone()
            return bool(row[0]) if row else False
        finally:
            conn.close()
    except Exception:  # noqa: BLE001
        return False


def provider(account_id: int | None = None) -> str:
    """总开关的取值：`decision`（前端开关已开）| `local`（默认，行为逐字等于现状）。"""
    return "decision" if switch_on(account_id) else "local"


def enabled(account_id: int | None = None) -> bool:
    return switch_on(account_id)


def trigger() -> str:
    """触发策略：near_tie（默认）| always | gray | off。"""
    t = _env("DECISION_TRIGGER", "near_tie").lower()
    return t if t in ("near_tie", "always", "gray", "off") else "near_tie"


def tie_threshold() -> float:
    """近并列门限（融合分间距）。标定起点 0.0005；见规划文档 §02 ③ 段第 1 条。"""
    try:
        return float(_env("DECISION_TIE_THRESHOLD", "0.0005"))
    except ValueError:
        return 0.0005


def top_k() -> int:
    try:
        return max(2, min(8, int(_env("DECISION_TOP_K", "3"))))
    except ValueError:
        return 3


def model() -> str:
    return _env("DECISION_MODEL_NAME", "")


def base_url() -> str:
    return _env("DECISION_MODEL_BASE_URL", "").rstrip("/")


def api_key() -> str:
    return _env("DECISION_MODEL_API_KEY", "")


def timeout_s() -> float:
    try:
        return max(1.0, float(_env("DECISION_TIMEOUT_S", "20")))
    except ValueError:
        return 20.0


def max_retries() -> int:
    try:
        return max(0, min(4, int(_env("DECISION_MAX_RETRIES", "2"))))
    except ValueError:
        return 2


def cache_enabled() -> bool:
    return _env("DECISION_CACHE_ENABLED", "1") not in ("0", "false", "no")


def allow_none() -> bool:
    """是否给决策模型 `__none__` 选项。默认否——噪声由路由器负责（裁定 3）。"""
    return _env("DECISION_ALLOW_NONE", "0") in ("1", "true", "yes")


def budget_per_day() -> int:
    try:
        return max(0, int(_env("DECISION_BUDGET_PER_DAY", "500")))
    except ValueError:
        return 500


def budget_per_session() -> int:
    try:
        return max(0, int(_env("DECISION_BUDGET_PER_SESSION", "200")))
    except ValueError:
        return 200


def endpoint() -> str:
    """base_url 去掉尾部 /api/v1 后接决策接口路径（沿用项目 .env 的口径）。"""
    b = base_url()
    if not b:
        return ""
    for suffix in ("/api/v1", "/v1"):
        if b.endswith(suffix):
            b = b[: -len(suffix)]
            break
    return b + DECISIONS_PATH


# ---------------------------------------------------------------- 审计 / 缓存（best-effort，绝不抛）
def _append(path: Path, entry: dict) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception:  # noqa: BLE001
        pass


def seen_audit_enabled() -> bool:
    """是否记录「路由来过」的痕迹（默认开）。关掉可设 `DECISION_AUDIT_SEEN=0`。"""
    return (os.getenv("DECISION_AUDIT_SEEN", "1") or "1").strip().lower() not in ("0", "false", "no", "off")


def audit(**entry) -> None:
    """写一条审计。**自动补时间戳**——`calls_today()` 与每日预算闸门都依赖它，
    早期版本漏了 `ts`，导致闸门恒判 0（真机 2026-10-12 实测发现）。"""
    entry.setdefault("ts", time.strftime("%Y-%m-%d %H:%M:%S"))
    _append(AUDIT_PATH, entry)


def _today() -> str:
    return time.strftime("%Y-%m-%d")


def calls_today() -> int:
    """今日已发生的真实调用数（读审计文件，不碰库）。

    严格比对：`event == "call"` 且 `ts` 以今日日期开头。**不用**「日期串出现在本行」这种宽松匹配，
    否则问题文本里恰好带日期（如"2026-10-13 的新闻"）就会被误计。
    """
    n = 0
    today = _today()
    try:
        if AUDIT_PATH.exists():
            for line in AUDIT_PATH.read_text(encoding="utf-8").splitlines():
                try:
                    rec = json.loads(line)
                except Exception:  # noqa: BLE001
                    continue
                if rec.get("event") == "call" and str(rec.get("ts") or "").startswith(today):
                    n += 1
    except Exception:  # noqa: BLE001
        return 0
    return n


def cache_ttl_h() -> float:
    """缓存时效（小时）。2026-10-13 用户裁定：**默认 24 小时，过期即作废并从文件里删掉**。
    设 0 → 不设时效（永不因时间失效）。"""
    try:
        return max(0.0, float(_env("DECISION_CACHE_TTL_H", "24")))
    except ValueError:
        return 24.0


def _ts_epoch(rec: dict) -> float | None:
    """把记录里的 `ts`（本地时间字符串）解析成 epoch 秒；解析不出返回 None。"""
    try:
        return time.mktime(time.strptime(str(rec.get("ts") or ""), "%Y-%m-%d %H:%M:%S"))
    except Exception:  # noqa: BLE001
        return None


def _fresh(rec: dict) -> bool:
    """记录是否还在时效内。无 `ts` / 解析不出 → **视为过期**（宁可不复用，也不复用来路不明的判断）。"""
    ttl = cache_ttl_h()
    if ttl <= 0:
        return True
    t = _ts_epoch(rec)
    return t is not None and (time.time() - t) <= ttl * 3600


def _cache_load() -> dict:
    """加载缓存索引（进程内只做一次）。**顺带把过期/坏行从文件里删掉**（真正落盘删除，不是只跳过）。"""
    global _CACHE
    if _CACHE is not None:
        return _CACHE
    idx: dict[str, dict] = {}
    dropped = 0
    try:
        if CACHE_PATH.exists():
            for line in CACHE_PATH.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                try:
                    it = json.loads(line)
                except Exception:  # noqa: BLE001
                    dropped += 1
                    continue
                k = it.get("key")
                if not k or not _fresh(it):
                    dropped += 1
                    continue
                idx[k] = it
    except Exception:  # noqa: BLE001
        idx = {}
    _CACHE = idx
    if dropped:
        _cache_compact(idx, dropped)
    return idx


def _cache_compact(idx: dict, dropped: int) -> None:
    """把还活着的记录重写成文件（临时文件 + 原子替换）。写不成就算了，绝不抛。"""
    try:
        tmp = CACHE_PATH.with_suffix(CACHE_PATH.suffix + ".tmp")
        tmp.parent.mkdir(parents=True, exist_ok=True)
        with tmp.open("w", encoding="utf-8") as f:
            for it in idx.values():
                f.write(json.dumps(it, ensure_ascii=False) + "\n")
        os.replace(tmp, CACHE_PATH)
        audit(event="cache_compact", kept=len(idx), dropped=dropped, ttl_h=cache_ttl_h())
    except Exception:  # noqa: BLE001
        pass


def cache_key(account_id: int | None, question: str, pool_version: int, ents_digest: str) -> str:
    """缓存键：账号 + 归一化问题 + 池版本 + 素材指纹 + **候选条数** + 模板版本 + 模型。

    ★ 2026-10-13（第二批）补 `top_k`：它决定**选项集**，改了它却复用旧答案＝拿旧问题的判断答新问题
    （与"只改关键词不失效"同类的静默复用）。补进去后，改 top_k 自动另起一套键。
    """
    raw = "|".join([str(account_id or 0), (question or "").strip(), str(pool_version),
                    ents_digest, "k%d" % top_k(), STATE_TEMPLATE_VERSION, model()])
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def entries_digest(entries) -> str:
    """候选池素材指纹（计入缓存键）。

    ★ 2026-10-13 用户裁定补两项：**`keywords` 与 `abilities_user`**。理由：喂给决策模型的卡片
    （`_card()`）里就含关键词与用户侧能力说明，只哈希 `name/abilities` 会出现
    「**卡片文本变了、缓存键没变**」→ 复用了旧判断（素材层改措辞正是本项目最常见的改动）。
    """
    h = hashlib.sha1()
    for e in entries:
        h.update(("%s|%s|%s|%s|%s|%s\n" % (e.source, e.ref, e.name, e.keywords,
                                          e.abilities, getattr(e, "abilities_user", "") or "")
                  ).encode("utf-8"))
    return h.hexdigest()[:12]


# ---------------------------------------------------------------- 触发判定
def should_refine(dec: dict) -> tuple[bool, dict]:
    """是否触发精排。返回 (是否触发, 说明)。纯函数，便于单测。

    一条省调用的硬规则：**灰区题（`confident=False`）不做精排**——它们本来就回退全量简报，
    池里所有工具都已给模型，候选顺序根本不参与渲染，精排没有增量（`always` 调试模式除外）。
    """
    t = trigger()
    if t == "off" or not dec:
        return False, {"trigger": "off"}
    order = dec.get("order") or []
    fused = dec.get("fused") or {}
    gap = None
    if len(order) >= 2:
        gap = float(fused.get(order[0], 0.0)) - float(fused.get(order[1], 0.0))
    if t == "always":
        return True, {"trigger": "always", "gap": gap}
    if not dec.get("confident"):
        return False, {"trigger": t, "gap": gap, "skip": "gray_renders_full_pool"}
    if t == "gray":
        return False, {"trigger": "gray", "gap": gap, "skip": "gray_renders_full_pool"}
    ok = gap is not None and gap < tie_threshold()
    return ok, {"trigger": "near_tie", "gap": gap, "threshold": tie_threshold()}


# ---------------------------------------------------------------- 候选卡片与问法
def _card(e, limit: int = 200) -> str:
    ab = " ".join((e.abilities or "").split())
    return "%s｜关键词：%s｜%s" % (e.name or e.ref, (e.keywords or "")[:140], ab[:limit])


def display_key(e) -> str:
    """给决策模型看的选项键 = `e.ref`（干净，且与调研期实测/题库 `expect` 同形）。

    注意：路由器内部键是 `f"{e.source}:{e.ref}"`，对 12306 这类 ref 自身已带来源前缀的
    条目会变成 `mcp:mcp:12306-mcp/...`（双层）。选项键用内部键会**双前缀**、且与实测形态不同，
    故这里统一用 `e.ref` 当显示键，回填时再用映射换回内部键。
    """
    return e.ref


def key_map(entries) -> dict:
    """内部键 → 显示键的映射（脚本/对账用）。

    ★ 为什么要有这个公共函数：内部键（`source:ref`）与题库/审计里的显示键（`ref`）不同形，
    测评与对账脚本各自手搓映射时**极容易漏换算** —— 本项目已经因此误判过三次
    （日志里出现 `mcp:mcp:...` 双层、或比对结果全 False）。
    """
    return {"%s:%s" % (e.source, e.ref): display_key(e) for e in entries}


def build_question(keys: list[str], entries_by_key: dict, disp_of: dict) -> dict:
    """一个 choice 问题；选项键 = 显示键（`e.ref`），便于与实测/题库同形比对。"""
    crit = {}
    for k in keys:
        e = entries_by_key.get(k)
        crit[disp_of.get(k, k)] = _card(e) if e else k
    if allow_none():
        crit["__none__"] = "这个问题不需要任何工具"
    return {"tool": {"type": "choice",
                     "instructions": "从候选里选出最合适的一个工具。",
                     "criteria": crit}}


def build_state(keys: list[str], entries_by_key: dict, question: str, disp_of: dict) -> dict:
    lines = []
    for k in keys:
        e = entries_by_key.get(k)
        lines.append("- %s: %s" % (disp_of.get(k, k), _card(e) if e else k))
    return {"候选工具（由路由召回，可能含错）": "\n".join(lines), "本轮用户消息": question}


# ---------------------------------------------------------------- 调用
def _call(questions: dict, state: dict) -> tuple[dict | None, str | None]:
    url = endpoint()
    key = api_key()
    mdl = model()
    if not url or not key or not mdl:
        return None, "config_missing"
    body = {"model": mdl, "state": state, "questions": questions}
    tries = max_retries() + 1
    for attempt in range(tries):
        req = urllib.request.Request(
            url, data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            headers={"Authorization": "Bearer " + key, "Content-Type": "application/json",
                     "HTTP-Referer": "https://localhost/dspro", "X-Title": "dspro-decision"})
        try:
            resp = urllib.request.urlopen(req, timeout=timeout_s())
            return json.loads(resp.read().decode("utf-8", "replace")), None
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")[:200]
            code = e.code
            retryable = code in (429, 500, 502, 503, 504)
            if retryable and attempt < tries - 1:
                time.sleep(2 + 3 * attempt)
                continue
            return None, "http_%s:%s" % (code, detail)
        except Exception as e:  # noqa: BLE001
            if attempt < tries - 1:
                time.sleep(2)
                continue
            return None, "%s:%s" % (type(e).__name__, str(e)[:120])
    return None, "exhausted"


# ---------------------------------------------------------------- 主入口
def maybe_refine(account_id: int | None, question: str, entries, dec: dict,
                 allow: bool = True) -> dict | None:
    """命中触发条件时调决策模型精排。

    `allow=False` = **测评请求**（03 路由质量测评）：无论开关状态一律不参与，且**不读也不写缓存**。
    为什么（2026-10-13 用户裁定）：03 测评量的是「路由器 + 素材层」的水平，决策层一开就等于换了仪器，
    数字与 01/03 的历史基线不可比 —— 自己给自己放水，所以测评期间强制关。

    返回**改写后的 dec**（`order` / `top` 已更新，其余字段原样；额外挂 `decision` 说明）；
    任何情形下不满足条件或不可用 → 返回 None（调用方按现状执行）。
    """
    if not allow:
        if enabled(account_id):           # 开关真开着才值得记一笔（否则与「没开」无法区分也没必要记）
            audit(event="seen", account_id=account_id, fired=False, eval_off=True,
                  question=(question or "")[:60])
        return None
    if not enabled(account_id) or not entries or not dec:
        return None
    fire, why = should_refine(dec)
    # ★ 可观测性：开关一开就留一条「路由来过」的痕迹（含是否触发、没触发的原因、间距）。
    #   没有它，「调用了但没触发」与「根本没走到这一步」在审计里长得一模一样
    #   （2026-10-12 真机实测踩到：用户提问后审计文件一条没变，无法判断是哪种）。
    if seen_audit_enabled():
        audit(event="seen", account_id=account_id, fired=bool(fire),
              question=(question or "")[:60], pool=len(entries), **why)
    if not fire:
        return None
    order = list(dec.get("order") or [])
    if len(order) < 2:
        return None

    by_key = {"%s:%s" % (e.source, e.ref): e for e in entries}
    k = top_k()
    keys = order[:k]
    disp_of = {kk: display_key(by_key.get(kk)) if by_key.get(kk) else kk for kk in order}
    internal_of = {v: kk for kk, v in disp_of.items()}
    pool_version = 0
    try:
        from tools import capability_pool as pool_mod
        pool_version = int(pool_mod.current_pool_version(account_id) or 0)
    except Exception:  # noqa: BLE001
        pool_version = 0
    ck = cache_key(account_id, question, pool_version, entries_digest(entries))

    # ★ 缓存**先查**（2026-10-13 用户裁定，第二批）：命中是**零成本**的（不调模型、不花额度），
    #   所以必须排在预算闸门**之前** —— 否则当日额度用尽时，连"不花钱的复用"也会被拒。
    #   命中也要过时效：进程长跑时内存里的索引会变旧。
    if cache_enabled():
        hit = _cache_load().get(ck)
        if hit and hit.get("choice") and _fresh(hit):
            _SESSION["hits"] += 1
            audit(event="cache_hit", account_id=account_id, key=ck, choice=hit["choice"])
            return _apply(dec, order, hit["choice"], why, {"cached": True, "key": ck})

    # 预算闸门（软门限：超了就当未触发，并记一条 skip）。只拦**真实调用** —— 缓存命中已在上面返回。
    if budget_per_session() and _SESSION["calls"] >= budget_per_session():
        _SESSION["skips"] += 1
        audit(event="skip", reason="budget_session", account_id=account_id)
        return None
    if budget_per_day() and calls_today() >= budget_per_day():
        _SESSION["skips"] += 1
        audit(event="skip", reason="budget_day", account_id=account_id)
        return None

    t0 = time.time()
    resp, err = _call(build_question(keys, by_key, disp_of), build_state(keys, by_key, question, disp_of))
    ms = int((time.time() - t0) * 1000)
    if err or not resp:
        from tools import failure_log
        try:
            failure_log.record("decision_model", err or "empty_response", account_id)
        except Exception:  # noqa: BLE001
            pass
        audit(event="error", account_id=account_id, error=err, ms=ms, **why)
        return None

    node = ((resp.get("answers") or {}).get("tool") or {})
    choice_disp = node.get("choice")
    probs = {str(a): float(b) for a, b in (node.get("probabilities") or {}).items()}
    _SESSION["calls"] += 1
    audit(event="call", account_id=account_id, choice=choice_disp, probs=probs, ms=ms,
          keys=[disp_of.get(x, x) for x in keys], pool_version=pool_version, **why)
    if not choice_disp:
        return None
    choice = internal_of.get(choice_disp, choice_disp)      # 显示键 → 内部键
    if cache_enabled():
        _CACHE.setdefault(ck, {"key": ck, "choice": choice, "disp": choice_disp, "probs": probs,
                               "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
                               # ★ 2026-10-13 用户裁定（第二批）：顺手存「问题原文 + 池版本」——
                               #   只存哈希键的话，事后无法从缓存文件反查这条对应哪句问题、哪个池。
                               "question": (question or "")[:120], "pool_version": pool_version})
        _append(CACHE_PATH, _CACHE[ck])
    return _apply(dec, order, choice, why, {"cached": False, "key": ck, "disp": choice_disp,
                                           "probs": probs, "ms": ms})


def _apply(dec: dict, order: list[str], choice: str, why: dict, extra: dict) -> dict | None:
    """把选中项提到第一位；未在候选内（或选了 __none__）→ 视为无效，返回 None。"""
    if choice == "__none__" or choice not in order:
        return None
    new_order = [choice] + [k for k in order if k != choice]
    out = dict(dec)
    out["order"] = new_order
    out["top"] = choice
    out["decision"] = dict(why, choice=choice, **extra)
    return out


def stats(account_id: int | None = None) -> dict:
    """给审计/调试用（不做任何判断逻辑）。"""
    return {"provider": provider(account_id), "enabled": enabled(account_id),
            "switch_source": "accounts.decision_router（前端开关）",
            "trigger": trigger(), "threshold": tie_threshold(), "top_k": top_k(),
            "model": model(), "cache": cache_enabled(), "cache_ttl_h": cache_ttl_h(),
            "session": dict(_SESSION), "calls_today": calls_today()}
