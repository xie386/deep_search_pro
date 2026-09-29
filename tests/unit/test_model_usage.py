# -*- coding: utf-8 -*-
"""M6c-3 用例：`UsageCounterMiddleware` 的**模型调用**计数（`kind='model'`，每次一条）。

判据来源：M6c 方案 §3.1/§5.2 + §六 M6c-3（≥10 项）+ G1。
★ 本步的核心反证（G1）：**一轮 7 次工具调用必须落 7 条 model 行**，不是 1 条 ——
  M4 的旧实现只取"最后一条 AIMessage 的 total_tokens"，多工具轮只记最后一次 ✗，本步就是修它。

全离线：不连模型、不发网络请求，用鸭子类型的假响应喂进中间件。

运行：.venv/Scripts/python.exe -m pytest tests/unit/test_model_usage.py -q
"""
import asyncio
import os
import sys
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from agent import usage_counter as uc                                          # noqa: E402
from agent.token_usage import SRC_NO_CACHE_DETAIL, SRC_REPORTED, SRC_UNAVAILABLE   # noqa: E402
from tools.schema_personal import ensure_tables, get_personal_conn, purge_account   # noqa: E402

ensure_tables()

# ★ 夹具照**框架真实形状**写：`response_metadata` 里 `token_usage` 与 `model_name` 是**同级兄弟**
#   （langchain_openai/chat_models/base.py:1399 与 :1871 都是 `{"token_usage": …, "model_name": …}`）。
#   第一版我把 model_name 塞进了 token_usage 里面 ✗ —— 夹具写错形状，测的就不是真实情况了。
DEEPSEEK_RAW = {"prompt_tokens": 1000, "completion_tokens": 200,
                "prompt_cache_hit_tokens": 300, "prompt_cache_miss_tokens": 700}


class FakeMsg:
    """最小 AIMessage 替身（中间件只用鸭子类型读两个属性）。"""

    def __init__(self, raw=None, normalized=None, model="deepseek-chat"):
        self.response_metadata = {}
        if raw is not None:
            self.response_metadata = {"token_usage": raw, "model_name": model}
        self.usage_metadata = normalized


class FakeToolMsg:
    """最小 ToolMessage 替身：真实工具结果是带 `.content` 的消息对象。"""

    def __init__(self, content):
        self.content = content


class FakeModelResponse:
    """最小 ModelResponse 替身：`.result` 是消息列表（框架真实结构）。"""

    def __init__(self, msgs):
        self.result = msgs if isinstance(msgs, list) else [msgs]


def mk_account(tag="mu"):
    conn = get_personal_conn()
    try:
        aid = int(conn.execute("INSERT INTO accounts (username, password_hash, role) VALUES (?,?,?)",
                               ("m6c%s_%d" % (tag, int(time.time() * 1000) % 1000000), "x", "user")).lastrowid)
        conn.commit()
    finally:
        conn.close()
    return aid


def with_ctx(aid, tid="t-mu", turn=3):
    uc.set_context_reader(lambda: (aid, tid, turn))


def rows(aid, kind="model"):
    conn = get_personal_conn()
    try:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM usage_events WHERE account_id=? AND kind=? ORDER BY id", (aid, kind))]
    finally:
        conn.close()


@pytest.fixture(autouse=True)
def _clean_ctx():
    yield
    uc.set_context_reader(None)
    uc.set_provider_resolver(None)


def _handler(response, boom=False):
    def h(request):
        if boom:
            raise RuntimeError("模型调用炸了（模拟上游 500）")
        return response
    return h


# --------------------------------------------------------------- G1 不漏计

def test_one_model_call_one_row():
    """一次模型调用 → 一条 model 行，token 四数正确。"""
    aid = mk_account()
    try:
        with_ctx(aid)
        mw = uc.UsageCounterMiddleware()
        mw.wrap_model_call(None, _handler(FakeModelResponse(FakeMsg(raw=DEEPSEEK_RAW))))
        rs = rows(aid)
        assert len(rs) == 1
        r = rs[0]
        assert r["kind"] == "model"
        assert (r["input_tokens"], r["output_tokens"], r["cached_tokens"], r["uncached_tokens"]) == (1000, 200, 300, 700)
        assert r["token_source"] == SRC_REPORTED
        assert r["ok"] == 1 and r["turn_index"] == 3
        assert r["cost"] is None                    # M6c-3 只落 token，折算在 M6c-4
    finally:
        purge_account(aid)


def test_seven_tool_rounds_write_seven_rows():
    """★ G1 反证：一轮 7 次模型调用（7 轮工具往返）→ **7 条**，不是 1 条。

    这正是 M4 的病根：旧实现只读最后一条 AIMessage 的 total_tokens，多工具轮只记最后一次。
    """
    aid = mk_account()
    try:
        with_ctx(aid)
        mw = uc.UsageCounterMiddleware()
        for _ in range(7):
            mw.wrap_model_call(None, _handler(FakeModelResponse(FakeMsg(raw=DEEPSEEK_RAW))))
        rs = rows(aid)
        assert len(rs) == 7, "多工具轮漏计（只记最后一次）"
        assert sum(r["input_tokens"] for r in rs) == 7000
        assert len({r["id"] for r in rs}) == 7
    finally:
        purge_account(aid)


def test_async_hook_also_records():
    """异步路径（deepagents 在事件循环里跑时走这条）同样记一条。"""
    aid = mk_account()
    try:
        with_ctx(aid, turn=5)

        async def run():
            mw = uc.UsageCounterMiddleware()

            async def h(request):
                return FakeModelResponse(FakeMsg(raw=DEEPSEEK_RAW))
            return await mw.awrap_model_call(None, h)
        asyncio.run(run())
        rs = rows(aid)
        assert len(rs) == 1 and rs[0]["turn_index"] == 5
    finally:
        purge_account(aid)


# --------------------------------------------------------------- 异常与不编造

def test_exception_writes_row_but_no_fabricated_tokens():
    """调用失败：写 ok=0、**不留 token 数字**（unavailable），且异常照常向上抛。"""
    aid = mk_account()
    try:
        with_ctx(aid)
        mw = uc.UsageCounterMiddleware()
        with pytest.raises(RuntimeError):
            mw.wrap_model_call(None, _handler(None, boom=True))
        rs = rows(aid)
        assert len(rs) == 1 and rs[0]["ok"] == 0
        assert rs[0]["token_source"] == SRC_UNAVAILABLE
        for c in ("input_tokens", "output_tokens", "cached_tokens", "uncached_tokens"):
            assert rs[0][c] is None
        assert rs[0]["cost"] is None
    finally:
        purge_account(aid)


def test_no_usage_metadata_is_recorded_as_unavailable():
    """上游没给 usage（部分中转站）→ unavailable，四个数为 NULL，但仍有一条（证明调用发生过）。"""
    aid = mk_account()
    try:
        with_ctx(aid)
        uc.UsageCounterMiddleware().wrap_model_call(None, _handler(FakeModelResponse(FakeMsg())))
        rs = rows(aid)
        assert len(rs) == 1 and rs[0]["token_source"] == SRC_UNAVAILABLE
        assert rs[0]["input_tokens"] is None
    finally:
        purge_account(aid)


def test_normalized_usage_is_fallback():
    """raw 缺席时用归一化兜底（并识别 input_token_details.cache_read）。"""
    aid = mk_account()
    try:
        with_ctx(aid)
        norm = {"input_tokens": 500, "output_tokens": 50, "input_token_details": {"cache_read": 100}}
        uc.UsageCounterMiddleware().wrap_model_call(None, _handler(FakeModelResponse(FakeMsg(normalized=norm))))
        r = rows(aid)[0]
        assert (r["cached_tokens"], r["uncached_tokens"]) == (100, 400)
        assert r["token_source"] == SRC_REPORTED
    finally:
        purge_account(aid)


def test_missing_cache_detail_marks_source():
    """只有总量 → no_cache_detail（M6c-4 会按"未命中"折算，且要在 UI 标注）。"""
    aid = mk_account()
    try:
        with_ctx(aid)
        raw = {"prompt_tokens": 900, "completion_tokens": 30}
        uc.UsageCounterMiddleware().wrap_model_call(None, _handler(FakeModelResponse(FakeMsg(raw=raw))))
        r = rows(aid)[0]
        assert r["token_source"] == SRC_NO_CACHE_DETAIL and r["uncached_tokens"] == 900
    finally:
        purge_account(aid)


# --------------------------------------------------------------- provider 维度

def test_provider_id_and_model_name_are_recorded():
    """C3：账目按 provider 维度 —— provider_id 与 model_name 都要落库。"""
    aid = mk_account()
    try:
        conn = get_personal_conn()
        try:
            pid = int(conn.execute(
                "INSERT INTO llm_providers (owner_id, provider_name, model_name, base_url, api_key, is_active)"
                " VALUES (?,?,?,?,?,1)", (aid, "DeepSeek", "deepseek-chat", "u", "k")).lastrowid)
            conn.commit()
        finally:
            conn.close()
        with_ctx(aid)
        uc.set_provider_resolver(lambda: {"id": pid, "provider_name": "DeepSeek", "model_name": "deepseek-chat"})
        uc.UsageCounterMiddleware().wrap_model_call(None, _handler(FakeModelResponse(FakeMsg(raw=DEEPSEEK_RAW))))
        r = rows(aid)[0]
        assert r["provider_id"] == pid and r["model_name"] == "deepseek-chat"
        assert r["source"] == "DeepSeek"            # 显示名（界面用）
    finally:
        purge_account(aid)


def test_resolver_missing_falls_back_to_response_model_name():
    """没有 provider 解析器 → provider_id 留空，但 model_name 从响应元数据兜底（仍可归因）。"""
    aid = mk_account()
    try:
        with_ctx(aid)
        uc.UsageCounterMiddleware().wrap_model_call(None, _handler(FakeModelResponse(FakeMsg(raw=DEEPSEEK_RAW))))
        r = rows(aid)[0]
        assert r["provider_id"] is None and r["model_name"] == "deepseek-chat"
    finally:
        purge_account(aid)


def test_resolver_that_raises_does_not_break_recording():
    """解析器自己抛异常 → 仍要记账（只是 provider 维度缺失），绝不能拖垮对话。"""
    aid = mk_account()
    try:
        with_ctx(aid)

        def boom():
            raise RuntimeError("解析器坏了")
        uc.set_provider_resolver(boom)
        uc.UsageCounterMiddleware().wrap_model_call(None, _handler(FakeModelResponse(FakeMsg(raw=DEEPSEEK_RAW))))
        r = rows(aid)[0]
        assert r["provider_id"] is None and r["input_tokens"] == 1000
    finally:
        purge_account(aid)


# --------------------------------------------------------------- 不干扰主流程

def test_context_absent_silently_counts_nothing():
    """未接入 api 层（没有上下文）→ 静默不计数（M4 的既定行为，不能被 M6c 改坏）。"""
    aid = mk_account()
    try:
        uc.set_context_reader(None)
        uc.UsageCounterMiddleware().wrap_model_call(None, _handler(FakeModelResponse(FakeMsg(raw=DEEPSEEK_RAW))))
        assert rows(aid) == []
    finally:
        purge_account(aid)


def test_response_is_returned_unchanged():
    """中间件只旁观：返回值原样透传（模型层的答案绝不能被记账改动）。"""
    aid = mk_account()
    try:
        with_ctx(aid)
        msg = FakeMsg(raw=DEEPSEEK_RAW)
        resp = FakeModelResponse(msg)
        out = uc.UsageCounterMiddleware().wrap_model_call(None, _handler(resp))
        assert out is resp and out.result[0] is msg
    finally:
        purge_account(aid)


def test_db_failure_does_not_raise(monkeypatch):
    """落库失败（例如库被锁）→ 静默吞掉，模型调用本身不受影响。"""
    aid = mk_account()
    try:
        with_ctx(aid)

        def boom(*a, **kw):
            raise RuntimeError("db is locked")
        monkeypatch.setattr(uc, "record_model", boom)
        out = uc.UsageCounterMiddleware().wrap_model_call(None, _handler(FakeModelResponse(FakeMsg(raw=DEEPSEEK_RAW))))
        assert out is not None                      # 没抛异常就是通过
    finally:
        purge_account(aid)


def test_tool_call_hook_still_works():
    """M6c-3 加了模型 hook，不能把 M4 的工具 hook 弄坏（同一次改动的相邻回归）。"""
    aid = mk_account()
    try:
        with_ctx(aid)
        mw = uc.UsageCounterMiddleware()

        class Req:
            tool_call = {"name": "internet_search", "args": {"query": "x"}}
        mw.wrap_tool_call(Req(), lambda r: FakeToolMsg("结果" * 10))   # 真实形状：带 content 的消息
        rs = rows(aid, kind="retrieval_external")
        assert len(rs) == 1 and rs[0]["tool_name"] == "internet_search"
        assert rs[0]["result_chars"] == 20          # "结果"×10 = 20 个字符（工具行照旧记字符数）
    finally:
        purge_account(aid)


# --------------------------------------------------------------- 架构不变量

def test_usage_counter_does_not_import_api():
    """铁律：`agent/` 层不 import `api.*`（本模块靠 set_context_reader/set_provider_resolver 倒置依赖）。"""
    import ast
    p = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                     "agent", "usage_counter.py")
    src = open(p, encoding="utf-8").read()
    mods = set()
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.Import):
            mods |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            mods.add(node.module.split(".")[0])
    assert "api" not in mods
