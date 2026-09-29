# -*- coding: utf-8 -*-
"""M4-2 · 用量计数器中间件（v3.0 M4·C1/C2/C4）。

**挂在框架咽喉上，不靠工具自觉上报**：langchain 的 `AgentMiddleware.wrap_tool_call` /
`awrap_tool_call` 是**每一次工具调用**的必经之路（deepagents 的 `create_deep_agent(middleware=[...])`
即注册点）。为什么不用现成的 `monitor.report_tool`：那要每个工具**各自**调用一次 ——
新增工具/子 Agent 里跑的工具都会漏（网络搜索恰好在子 Agent 里跑，正是漏得最狠的地方，见 C2）。

口径（方案 §5.1）：
  · `kind` 五类：`internal` / `retrieval_external` / `shell` / `api` / `mcp`，**只有 `retrieval_external`
    有额度成本**（`COST_KINDS`）；
  · **异常/超时也计**（`ok=0`）—— 额度已经花掉了，不计就是自欺；异常本身照常向上抛（绝不吞掉工具错误）；
  · 上下文（账号/会话）由 **api 层注入读取函数**（`set_context_reader`）——本模块**不 import `api.*`**，
    保持 agent 层不反向依赖；没有上下文时**静默不计数**，绝不因为"记不上账"影响对话；
  · 任何落库异常都被吞掉：**计数器坏了不能拖垮主流程**。
  · **M6c-3**：再挂 `wrap_model_call` —— 每一次**模型调用**落一条 `kind='model'`，
    修掉"M4 只取最后一条 AIMessage 的 total_tokens → 一轮 7 次工具调用只记最后一次"的漏计。
    token 解析走 `agent/token_usage.py`（四形态 + 不编造），金额折算留到 M6c-4。
"""
import time

from langchain.agents.middleware import AgentMiddleware

KIND_INTERNAL = "internal"
KIND_RETRIEVAL_EXTERNAL = "retrieval_external"
KIND_SHELL = "shell"
KIND_API = "api"
KIND_MCP = "mcp"
KIND_MODEL = "model"     # ★ M6c-3：模型调用（每次一条；不花外部检索额度）

KINDS = (KIND_INTERNAL, KIND_RETRIEVAL_EXTERNAL, KIND_SHELL, KIND_API, KIND_MCP)
KIND_LABELS = {
    KIND_INTERNAL: "内部工具",
    KIND_RETRIEVAL_EXTERNAL: "外部检索",
    KIND_SHELL: "命令行",
    KIND_API: "API 工具",
    KIND_MCP: "MCP 工具",
}
# ★ 只有这一类花外部检索额度（M4 的核心诉求：回答"这次为什么烧了额度"）
COST_KINDS = (KIND_RETRIEVAL_EXTERNAL,)

# 工具名 → kind 的**精确**映射（可扩展：`EXTRA_TOOL_KINDS.update({...})`）。
# 子串规则兜底（见 classify）：tavily*/web_search*/news* → 外部检索，*shell* → 命令行。
_EXACT = {
    # ★ 2026-09-28 真机抽样发现的缺口：真实检索工具叫 `internet_search`（方案 §3.1 原话就是
    #   `retrieval_external : internet_search（Tavily）`），我原来只写了 tavily*/web_search* 等子串，
    #   结果**唯一花额度的那一类一条都没记上** —— 整个 M4 就是为回答"这次为什么烧了额度"而做的。
    "internet_search": KIND_RETRIEVAL_EXTERNAL,
    "invoke_tool": None,            # None = 看参数里的能力名（api:… / mcp:… / 其它）
    "query_kb": KIND_INTERNAL,
    "read_agent_doc": KIND_INTERNAL,
    "write_agent_doc": KIND_INTERNAL,
    "list_agent_docs": KIND_INTERNAL,
    "update_user_profile": KIND_INTERNAL,
    "run_shell_command": KIND_SHELL,
}
EXTRA_TOOL_KINDS: dict = {}

_EXTERNAL_HINTS = ("tavily", "web_search", "search_web", "websearch", "news_search", "rss")
_SHELL_HINTS = ("shell", "run_command", "exec_command")


def classify(tool_name: str, args: dict | None = None) -> tuple:
    """工具名(+参数) → `(kind, source)`。`source` 是"具体来源"，给界面显示用。

    判定顺序：精确表 → 扩展表 → **invoke_tool 看能力名前缀** → 子串规则 → 兜底 internal。
    """
    name = (tool_name or "").strip()
    args = args or {}
    if name in EXTRA_TOOL_KINDS:
        return str(EXTRA_TOOL_KINDS[name]), name
    if name in _EXACT and _EXACT[name] is not None:
        return _EXACT[name], name
    if name == "invoke_tool":
        cap = str(args.get("name") or args.get("ability") or "").strip()
        if cap.startswith("api:"):
            return KIND_API, cap
        if cap.startswith("mcp:"):
            return KIND_MCP, cap
        return KIND_INTERNAL, cap or name       # CLI 只读能力等
    low = name.lower()
    if any(h in low for h in _EXTERNAL_HINTS):
        return KIND_RETRIEVAL_EXTERNAL, name
    if any(h in low for h in _SHELL_HINTS):
        return KIND_SHELL, name
    return KIND_INTERNAL, name


# ---------------------------------------------------------------- 上下文（依赖倒置）
# 默认读取器返回 (None, None) → 未接入 api 层时**静默不计数**（"未设置上下文时不炸"）。
_CONTEXT_READER = None


def set_context_reader(fn) -> None:
    """api 层启动时注入：`fn() -> (account_id, thread_id)`。本模块不 import api.*（保持分层）。"""
    global _CONTEXT_READER
    _CONTEXT_READER = fn


# M6c-3：当前生效的模型配置（provider 维度记账）。默认返回 None → provider_id/model_name 留空。
_PROVIDER_RESOLVER = None


def set_provider_resolver(fn) -> None:
    """api 层启动时注入：`fn() -> dict|None`（当前账号生效的 `llm_providers` 行）。

    为什么按 provider 维度记（方案 C3）：`llm_providers` 支持多配置 + `is_active` 切换，
    只记"账号 + token"再按当前单价现算 → 用户换模型后**历史账目会被重算（错账）**。
    """
    global _PROVIDER_RESOLVER
    _PROVIDER_RESOLVER = fn


def _provider() -> dict:
    """读当前生效的模型配置；拿不到就返回 {}（不抛，记账不影响对话）。"""
    if _PROVIDER_RESOLVER is None:
        return {}
    try:
        got = _PROVIDER_RESOLVER()
        return dict(got) if got else {}
    except Exception:
        return {}


def _context() -> tuple:
    """`(account_id, thread_id, turn_index)`。

    ★ 读取器可以返回 2 元组（兼容 M4-3 的注册方式）或 3 元组（M4-7 起补上"本轮序号"）。
      `turn_index` 是 M4-4「本轮 vs 本会话」的**唯一依据** —— 表里没有它就算不出"本轮"。
    """
    if _CONTEXT_READER is None:
        return None, "", 0
    try:
        got = _CONTEXT_READER()
        aid = got[0] if len(got) > 0 else None
        tid = got[1] if len(got) > 1 else ""
        turn = got[2] if len(got) > 2 else 0
        return (int(aid) if aid else None), (str(tid) if tid else ""), int(turn or 0)
    except Exception:
        return None, "", 0       # 读取器坏了也不影响对话


# ---------------------------------------------------------------- 落库
def record(account_id: int, thread_id: str, kind: str, tool_name: str, source: str,
           ok: bool = True, latency_ms: int = 0, turn_index: int = 0, result_chars: int = 0) -> None:
    """写一条用量事件。**任何异常都被吞掉**（计数器坏了不能拖垮主流程）。"""
    if not account_id:
        return
    try:
        from tools.schema_personal import get_personal_conn
        now = time.time()
        conn = get_personal_conn()
        try:
            conn.execute(
                "INSERT INTO usage_events (account_id, thread_id, turn_index, kind, tool_name, source, ok,"
                " latency_ms, result_chars, created_at, created_ts) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (int(account_id), thread_id or "", int(turn_index or 0), kind, tool_name or "", source or "",
                 1 if ok else 0, int(latency_ms or 0), int(result_chars or 0),
                 time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now)), now))
            conn.commit()
        finally:
            conn.close()
    except Exception:
        pass


def _prices_of(provider: dict | None) -> dict | None:
    """把 provider 行里的三处单价抽出来（缺列/缺值 → None，交给 `compute_cost` 判 `no_price`）。"""
    if not provider:
        return None
    try:
        from agent.token_usage import PRICE_KEYS
        out = {k: provider.get(k) for k in PRICE_KEYS}
        return out if any(v is not None for v in out.values()) else None
    except Exception:
        return None


def record_model(account_id: int, thread_id: str, turn_index: int, usage: dict,
                 provider: dict | None = None, model_name: str = "", ok: bool = True,
                 latency_ms: int = 0) -> None:
    """写一条**模型调用**用量（`kind='model'`），并按**当时**的单价折算落库（M6c-4）。异常一律吞掉。

    ★ 金额**落库**而不是查询时现算（C3/G6）：`llm_providers` 支持多配置与切换，
      现算会让"换模型/改价"把历史账目一起改掉。重算只能走显式的 `recalc_costs()`。
    """
    if not account_id:
        return
    usage = usage or {}
    provider = provider or {}
    try:
        from agent.token_usage import compute_cost
        from tools.schema_personal import get_personal_conn
        cost, cost_note, snapshot = compute_cost(usage, _prices_of(provider))
        now = time.time()
        conn = get_personal_conn()
        try:
            conn.execute(
                "INSERT INTO usage_events (account_id, thread_id, turn_index, kind, tool_name, source, ok,"
                " latency_ms, result_chars, provider_id, model_name, input_tokens, output_tokens,"
                " cached_tokens, uncached_tokens, token_source, cost, cost_note, price_snapshot,"
                " created_at, created_ts)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (int(account_id), thread_id or "", int(turn_index or 0), KIND_MODEL, "",
                 str(provider.get("provider_name") or ""), 1 if ok else 0, int(latency_ms or 0), 0,
                 provider.get("id"), (model_name or provider.get("model_name") or "")[:200],
                 usage.get("input"), usage.get("output"), usage.get("cached"), usage.get("uncached"),
                 usage.get("token_source"), cost, cost_note, snapshot,
                 time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now)), now))
            conn.commit()
        finally:
            conn.close()
    except Exception:
        pass


def _usage_of_row(row) -> dict:
    """把库里一行还原成 `extract_usage` 的产物形状（重算要用同一套折算口径）。"""
    return {"input": row["input_tokens"], "output": row["output_tokens"],
            "cached": row["cached_tokens"], "uncached": row["uncached_tokens"],
            "token_source": row["token_source"]}


def _prices_for_row(conn, account_id: int, provider_id) -> dict | None:
    """按**行上记的 provider_id** 取当前单价；该配置已删则退回"账号当前生效"那套。"""
    try:
        row = None
        if provider_id:
            row = conn.execute("SELECT * FROM llm_providers WHERE id=? AND owner_id=?",
                               (int(provider_id), int(account_id))).fetchone()
        if row is None:
            row = conn.execute("SELECT * FROM llm_providers WHERE owner_id=?"
                               " ORDER BY is_active DESC, id DESC LIMIT 1", (int(account_id),)).fetchone()
        return _prices_of(dict(row)) if row is not None else None
    except Exception:
        return None


def recalc_costs(account_id: int, days: int = 30) -> dict:
    """★ 显式"按当前单价重算"（方案 §3.2 ⑤）。返回统计。

    范围铁律（G6）：只动**指定时间窗内、`kind='model'`、且当前取得到单价**的行；
    窗口外、工具行、无用量行（`unavailable`/`unknown_shape`）一律不碰；每行写 `recalculated_at`。
    """
    stats = {"scanned": 0, "updated": 0, "skipped_no_price": 0, "skipped_no_tokens": 0}
    if not account_id:
        return stats
    try:
        days = max(0, int(days))
    except (TypeError, ValueError):
        days = 30
    cut = time.time() - days * 86400
    try:
        from agent.token_usage import SRC_UNAVAILABLE, SRC_UNKNOWN_SHAPE, compute_cost
        from tools.schema_personal import get_personal_conn
        conn = get_personal_conn()
        try:
            # ★ list() 物化：循环里还要用同一个 cursor 查 provider（同 cursor 会作废外层结果集）
            rows = list(conn.execute(
                "SELECT id, provider_id, input_tokens, output_tokens, cached_tokens, uncached_tokens,"
                " token_source FROM usage_events WHERE account_id=? AND kind=? AND created_ts>=?"
                " ORDER BY id", (int(account_id), KIND_MODEL, cut)))
            for r in rows:                     # r 是 sqlite3.Row，但 `_usage_of_row` 用键取值 ✓
                stats["scanned"] += 1
                if (r["token_source"] or "") in (SRC_UNAVAILABLE, SRC_UNKNOWN_SHAPE):
                    stats["skipped_no_tokens"] += 1
                    continue
                prices = _prices_for_row(conn, account_id, r["provider_id"])
                cost, note, snap = compute_cost(_usage_of_row(r), prices)
                if cost is None and note == "no_price":
                    stats["skipped_no_price"] += 1
                    continue
                conn.execute("UPDATE usage_events SET cost=?, cost_note=?, price_snapshot=?,"
                             " recalculated_at=? WHERE id=?",
                             (cost, note, snap, time.strftime("%Y-%m-%d %H:%M:%S"), int(r["id"])))
                stats["updated"] += 1
            conn.commit()
        finally:
            conn.close()
    except Exception:
        pass
    return stats


def _ai_message(response):
    """从 `ModelResponse.result`（list[BaseMessage]）里挑出**本次模型调用**的那条 AIMessage。

    宽容处理三种形态：ModelResponse / AIMessage / 裸 list；挑不到就返回 None（→ 记 unavailable）。
    """
    try:
        if response is None:
            return None
        msgs = getattr(response, "result", None)
        if msgs is None:
            msgs = response if isinstance(response, (list, tuple)) else [response]
        cand = None
        for m in (msgs or []):
            if getattr(m, "usage_metadata", None) or getattr(m, "response_metadata", None):
                cand = m
        if cand is None:
            cand = (list(msgs)[-1] if msgs else None)
        return cand
    except Exception:
        return None


def _result_chars(result) -> int:
    """量"这次调用拉回来多少字符"（成本卡与排查都要）。宽容：拿不到就 0。"""
    try:
        c = getattr(result, "content", None)
        if c is None and isinstance(result, dict):
            c = result.get("content")
        if isinstance(c, str):
            return len(c)
        return len(str(c)) if c is not None else 0
    except Exception:
        return 0


class UsageCounterMiddleware(AgentMiddleware):
    """主 Agent 与**每个子 Agent spec**都要注册（子图不吃主图的 middleware —— C2）。"""

    def _after(self, request, t0: float, ok: bool, result=None):
        try:
            call = getattr(request, "tool_call", None) or {}
            name = call.get("name") if isinstance(call, dict) else getattr(call, "name", "")
            args = call.get("args") if isinstance(call, dict) else getattr(call, "args", {})
            kind, source = classify(name or "", args or {})
            aid, tid, turn = _context()
            record(aid, tid, kind, name or "", source, ok=ok,
                   latency_ms=int((time.perf_counter() - t0) * 1000),
                   turn_index=turn, result_chars=_result_chars(result))
        except Exception:
            pass

    def _after_model(self, t0: float, ok: bool, response=None):
        """★ 每次模型调用落一条（不再"只记最后一次工具调用之后的那个 total_tokens"）。"""
        try:
            aid, tid, turn = _context()
            if not aid:
                return                      # 没账号（未接入 api 层）→ 静默不计数
            from agent.token_usage import SRC_UNAVAILABLE, extract_usage
            msg = _ai_message(response)
            raw = getattr(msg, "response_metadata", None) or {}
            norm = getattr(msg, "usage_metadata", None)
            raw_usage = raw.get("token_usage") if isinstance(raw, dict) else None
            usage = extract_usage(raw_usage, norm)
            if not ok and usage.get("token_source") != SRC_UNAVAILABLE:
                usage = {"input": None, "output": None, "cached": None, "uncached": None,
                         "token_source": SRC_UNAVAILABLE}   # 调用失败：不编造 token
            model_name = ""
            if isinstance(raw, dict):
                model_name = str(raw.get("model_name") or raw.get("model") or "")
            record_model(aid, tid, turn, usage, provider=_provider(), model_name=model_name, ok=ok,
                         latency_ms=int((time.perf_counter() - t0) * 1000))
        except Exception:
            pass                            # 记账坏了不能拖垮对话

    # ---- 模型调用（M6c-3）：同步
    def wrap_model_call(self, request, handler):
        t0, ok, res = time.perf_counter(), True, None
        try:
            res = handler(request)
            return res
        except BaseException:
            ok = False
            raise                            # ★ 异常照常向上抛（绝不吞掉模型错误）
        finally:
            self._after_model(t0, ok, res)

    # ---- 模型调用（M6c-3）：异步（deepagents 在事件循环里跑时走这条）
    async def awrap_model_call(self, request, handler):
        t0, ok, res = time.perf_counter(), True, None
        try:
            res = await handler(request)
            return res
        except BaseException:
            ok = False
            raise
        finally:
            self._after_model(t0, ok, res)

    # ---- 工具调用：同步
    def wrap_tool_call(self, request, handler):
        t0, ok, res = time.perf_counter(), True, None
        try:
            res = handler(request)
            return res
        except BaseException:
            ok = False
            raise
        finally:
            self._after(request, t0, ok, res)

    # ---- 异步（deepagents 在事件循环里跑时走这条）
    async def awrap_tool_call(self, request, handler):
        t0, ok, res = time.perf_counter(), True, None
        try:
            res = await handler(request)
            return res
        except BaseException:
            ok = False
            raise
        finally:
            self._after(request, t0, ok, res)
