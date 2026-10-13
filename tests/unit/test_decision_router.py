# -*- coding: utf-8 -*-
"""决策模型精排（v4.0 大点 02 · 首批）的单测：全部离线，不触网、不碰库、不写项目目录。

覆盖：配置解析 / 触发判定（含"灰区不精排"这条省调用规则）/ 问题与 state 模板 /
顺序改写与非法选择 / 缓存键与缓存命中 / 预算闸门 / 未启用时逐字回退。
"""
from __future__ import annotations

import importlib
import json
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools import decision_router as dr  # noqa: E402


@pytest.fixture(autouse=True)
def _hide_db_settings(monkeypatch):
    """把「应用设置库(app_settings)」对本模块隐藏。

    ★ 2026-10-13：本模块声明「不碰库」，但设置项的取值顺序是 **库 → .env → 默认** ——
      真机库里一旦有用户在设置页保存过的 `DECISION_*` 值（点一次保存就会整表落库），
      这些用环境变量验钳位 / 触发 / 预算 / 缓存的用例就会全线假失败（实测 9 项）。
      隐藏库值后本模块只验语义，与用户真实数据无关。
    """
    from utils import settings as _s
    monkeypatch.setattr(_s, "_load_all", lambda: {})
    _s.reload()
    yield
    _s.reload()


class FakeEntry:
    def __init__(self, source, ref, name, keywords, abilities, abilities_user=""):
        self.source, self.ref, self.name = source, ref, name
        self.keywords, self.abilities = keywords, abilities
        self.abilities_user = abilities_user


@pytest.fixture()
def ents():
    return [
        FakeEntry("mcp", "mcp:12306-mcp/get-tickets", "火车票查询", "火车票/余票", "查余票"),
        FakeEntry("mcp", "mcp:city/search_city", "城市地理信息", "城市/经纬度", "查地理"),
        FakeEntry("cli", "bili", "B站", "视频/BV号", "找视频"),
    ]


@pytest.fixture(autouse=True)
def clean_env(tmp_path, monkeypatch):
    """每个用例用独立临时审计/缓存目录，并清掉可能残留的配置。"""
    for k in ("ZX_TOOL_ROUTE_PROVIDER", "DECISION_TRIGGER", "DECISION_TIE_THRESHOLD",
              "DECISION_TOP_K", "DECISION_MODEL_NAME", "DECISION_MODEL_BASE_URL",
              "DECISION_MODEL_API_KEY", "DECISION_CACHE_ENABLED", "DECISION_ALLOW_NONE",
              "DECISION_BUDGET_PER_DAY", "DECISION_BUDGET_PER_SESSION", "DECISION_AUDIT_SEEN"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("DECISION_AUDIT_PATH", str(tmp_path / "decision.jsonl"))
    monkeypatch.setenv("DECISION_CACHE_PATH", str(tmp_path / "cache.jsonl"))
    importlib.reload(dr)                 # 让模块重新读取被覆盖的路径
    dr._SESSION.update({"calls": 0, "hits": 0, "skips": 0})
    dr._CACHE = None
    yield


def _dec(gap_top1_top2=0.0001, confident=True):
    a, b, c = "mcp:mcp:12306-mcp/get-tickets", "mcp:mcp:city/search_city", "cli:bili"
    return {"order": [a, b, c],
            "fused": {a: 0.05, b: 0.05 - gap_top1_top2, c: 0.01},
            "top": a, "z_top": 4.0, "lex_top": 3.0, "confident": confident}


# ---------------------------------------------------------------- 配置
def test_defaults_are_off(monkeypatch):
    assert dr.provider() == "local"
    assert dr.enabled() is False
    assert dr.trigger() == "near_tie"
    assert dr.tie_threshold() == 0.0005
    assert dr.top_k() == 3
    assert dr.cache_enabled() is True
    assert dr.allow_none() is False


def test_endpoint_derivation(monkeypatch):
    monkeypatch.setenv("DECISION_MODEL_BASE_URL", "https://openrouter.ai/api/v1")
    assert dr.endpoint() == "https://openrouter.ai/api/alpha/decisions"
    monkeypatch.setenv("DECISION_MODEL_BASE_URL", "https://openrouter.ai/api/v1/")
    assert dr.endpoint() == "https://openrouter.ai/api/alpha/decisions"
    monkeypatch.setenv("DECISION_MODEL_BASE_URL", "")
    assert dr.endpoint() == ""


def test_top_k_clamped(monkeypatch):
    monkeypatch.setenv("DECISION_TOP_K", "99")
    assert dr.top_k() == 8
    monkeypatch.setenv("DECISION_TOP_K", "1")
    assert dr.top_k() == 2
    monkeypatch.setenv("DECISION_TOP_K", "abc")
    assert dr.top_k() == 3


# ---------------------------------------------------------------- 触发判定
def test_trigger_near_tie(monkeypatch):
    monkeypatch.setattr(dr, "switch_on", lambda account_id=None: True)
    fire, why = dr.should_refine(_dec(0.0001))
    assert fire is True and why["trigger"] == "near_tie"
    fire, _ = dr.should_refine(_dec(0.001))          # 相距较远 → 不触发
    assert fire is False
    monkeypatch.setenv("DECISION_TIE_THRESHOLD", "0.002")
    fire, _ = dr.should_refine(_dec(0.001))
    assert fire is True


def test_gray_questions_are_skipped(monkeypatch):
    """灰区题回退全量简报，候选顺序不参与渲染 → 精排无增量，必须跳过（省调用）。"""
    monkeypatch.setattr(dr, "switch_on", lambda account_id=None: True)
    fire, why = dr.should_refine(_dec(0.0000, confident=False))
    assert fire is False and why["skip"] == "gray_renders_full_pool"


def test_trigger_off_and_always(monkeypatch):
    monkeypatch.setattr(dr, "switch_on", lambda account_id=None: True)
    monkeypatch.setenv("DECISION_TRIGGER", "off")
    assert dr.should_refine(_dec())[0] is False
    monkeypatch.setenv("DECISION_TRIGGER", "always")
    assert dr.should_refine(_dec(0.5))[0] is True


def test_should_refine_handles_degenerate_dec():
    assert dr.should_refine({})[0] is False
    assert dr.should_refine({"order": ["only"], "fused": {}, "confident": True})[0] is False


# ---------------------------------------------------------------- 模板
def test_build_question_and_state(ents):
    by = {"%s:%s" % (e.source, e.ref): e for e in ents}
    disp_of = {k: dr.display_key(e) for k, e in by.items()}
    q = dr.build_question(list(by), by, disp_of)
    assert set(q["tool"]["criteria"]) == {e.ref for e in ents}     # 选项键 = e.ref（干净）
    assert "__none__" not in q["tool"]["criteria"]                 # 默认不给 none（噪声归路由器）
    st = dr.build_state(list(by), by, "成都到广州的票", disp_of)
    assert "成都到广州的票" in st["本轮用户消息"]
    assert "火车票查询" in st["候选工具（由路由召回，可能含错）"]


def test_build_question_with_none_option(ents, monkeypatch):
    monkeypatch.setenv("DECISION_ALLOW_NONE", "1")
    by = {"%s:%s" % (e.source, e.ref): e for e in ents}
    disp_of = {k: dr.display_key(e) for k, e in by.items()}
    assert "__none__" in dr.build_question(list(by), by, disp_of)["tool"]["criteria"]


# ---------------------------------------------------------------- 顺序改写
def test_apply_reorders_and_marks():
    dec = _dec()
    out = dr._apply(dec, list(dec["order"]), "cli:bili", {"trigger": "near_tie"}, {})
    assert out["order"][0] == "cli:bili" and out["top"] == "cli:bili"
    assert set(out["order"]) == set(dec["order"])          # 不增不减
    assert out["decision"]["choice"] == "cli:bili"
    assert dec["order"][0] == "mcp:mcp:12306-mcp/get-tickets"   # 原 dec 不被就地改


def test_apply_rejects_invalid_choice():
    dec = _dec()
    assert dr._apply(dec, list(dec["order"]), "not-in-list", {}, {}) is None
    assert dr._apply(dec, list(dec["order"]), "__none__", {}, {}) is None


# ---------------------------------------------------------------- 缓存
def test_cache_key_stability_and_sensitivity(ents):
    d = dr.entries_digest(ents)
    k1 = dr.cache_key(3, "问句", 7, d)
    assert k1 == dr.cache_key(3, " 问句 ", 7, d)           # 两端空白归一
    assert k1 != dr.cache_key(3, "问句", 8, d)             # 池版本变 → 失效
    assert k1 != dr.cache_key(4, "问句", 7, d)             # 账号变 → 失效
    assert k1 != dr.cache_key(3, "问句2", 7, d)


def test_cache_key_sensitive_to_top_k(ents, monkeypatch):
    """★ 第二批：top_k 决定**选项集**，必须进缓存键（否则改 top_k 会静默复用旧答案）。"""
    d = dr.entries_digest(ents)
    k3 = dr.cache_key(3, "问句", 7, d)
    monkeypatch.setenv("DECISION_TOP_K", "5")
    assert dr.cache_key(3, "问句", 7, d) != k3


# ---------------------------------------------------------------- 主入口（打桩 _call）
def test_maybe_refine_disabled_returns_none(ents, monkeypatch):
    """开关关着（默认）→ 一律 None，行为逐字等于现状。"""
    monkeypatch.setattr(dr, "switch_on", lambda account_id=None: False)
    assert dr.provider(3) == "local" and dr.enabled(3) is False
    assert dr.maybe_refine(3, "成都到广州的票", ents, _dec()) is None


def test_switch_source_is_account_field(ents, monkeypatch):
    """开关来源 = 账号级 DB 字段（前端按钮），**不是** .env；无账号 → 直接关，不碰库。"""
    assert dr.switch_on(None) is False and dr.switch_on(0) is False
    seen = []
    monkeypatch.setattr(dr, "switch_on", lambda account_id=None: (seen.append(account_id), True)[1])
    assert dr.provider(7) == "decision" and dr.enabled(7) is True
    assert seen == [7, 7]                       # provider 与 enabled 各转发一次
    monkeypatch.setattr(dr, "switch_on", lambda account_id=None: False)
    assert dr.provider(7) == "local" and dr.enabled(7) is False


def test_maybe_refine_full_flow_and_cache(ents, monkeypatch):
    monkeypatch.setattr(dr, "switch_on", lambda account_id=None: True)
    monkeypatch.setenv("DECISION_MODEL_NAME", "inception/mercury-decide:free")
    monkeypatch.setenv("DECISION_MODEL_BASE_URL", "https://openrouter.ai/api/v1")
    monkeypatch.setenv("DECISION_MODEL_API_KEY", "sk-or-test")
    calls = {"n": 0}

    def fake_call(questions, state):
        calls["n"] += 1
        return ({"answers": {"tool": {"type": "choice", "choice": "cli:bili",
                                      "probabilities": {"cli:bili": 0.9}}}}, None)

    monkeypatch.setattr(dr, "_call", fake_call)
    dec = _dec()
    out = dr.maybe_refine(3, "找个视频看看", ents, dec)
    assert out is not None and out["order"][0] == "cli:bili"
    assert calls["n"] == 1
    lines = [json.loads(x) for x in Path(dr.AUDIT_PATH).read_text(encoding="utf-8").splitlines()]
    assert lines[-1]["event"] == "call" and lines[-1]["choice"] == "cli:bili"

    out2 = dr.maybe_refine(3, "找个视频看看", ents, dec)      # 同问句同池 → 命中缓存
    assert out2 is not None and calls["n"] == 1              # 没有再调用
    assert dr._SESSION["hits"] == 1


def test_maybe_refine_error_is_fail_safe(ents, monkeypatch):
    monkeypatch.setattr(dr, "switch_on", lambda account_id=None: True)
    monkeypatch.setenv("DECISION_MODEL_NAME", "m")
    monkeypatch.setenv("DECISION_MODEL_BASE_URL", "https://x/api/v1")
    monkeypatch.setenv("DECISION_MODEL_API_KEY", "k")
    monkeypatch.setattr(dr, "_call", lambda q, s: (None, "http_402:no credits"))
    assert dr.maybe_refine(3, "问句", ents, _dec()) is None   # 不可用 → None（调用方走现状）
    last = json.loads(Path(dr.AUDIT_PATH).read_text(encoding="utf-8").splitlines()[-1])
    assert last["event"] == "error" and last["error"].startswith("http_402")


def test_budget_gate_blocks_calls(ents, monkeypatch):
    monkeypatch.setattr(dr, "switch_on", lambda account_id=None: True)
    monkeypatch.setenv("DECISION_MODEL_NAME", "m")
    monkeypatch.setenv("DECISION_MODEL_BASE_URL", "https://x/api/v1")
    monkeypatch.setenv("DECISION_MODEL_API_KEY", "k")
    monkeypatch.setenv("DECISION_BUDGET_PER_SESSION", "1")
    dr._SESSION["calls"] = 1
    called = {"n": 0}
    monkeypatch.setattr(dr, "_call", lambda q, s: (called.__setitem__("n", called["n"] + 1), None)[1])
    assert dr.maybe_refine(3, "问句", ents, _dec()) is None
    assert called["n"] == 0 and dr._SESSION["skips"] == 1


def test_audit_has_ts_and_calls_today_counts(ents, monkeypatch):
    """回归：审计必须带 ts，否则 calls_today 恒 0、每日预算闸门失效（真机实测踩到）。"""
    monkeypatch.setattr(dr, "switch_on", lambda account_id=None: True)
    monkeypatch.setenv("DECISION_MODEL_NAME", "m")
    monkeypatch.setenv("DECISION_MODEL_BASE_URL", "https://x/api/v1")
    monkeypatch.setenv("DECISION_MODEL_API_KEY", "k")
    monkeypatch.setattr(dr, "_call", lambda q, s: ({"answers": {"tool": {"type": "choice",
                                                                        "choice": "cli:bili"}}}, None))
    assert dr.calls_today() == 0
    dr.maybe_refine(3, "问句", ents, _dec())
    assert dr.calls_today() == 1
    rec = json.loads(Path(dr.AUDIT_PATH).read_text(encoding="utf-8").splitlines()[-1])
    assert rec["ts"].startswith(dr._today()) and rec["event"] == "call"
    # 问题文本里带日期**不许**被计成调用
    dr.audit(event="note", account_id=3, question="2026-10-13 的新闻")
    assert dr.calls_today() == 1


def test_seen_record_makes_unt_riggered_visible(ents, monkeypatch):
    """回归：只要开关开着，不触发也要留一条 seen —— 否则「没走到」与「没触发」无法区分。"""
    monkeypatch.setattr(dr, "switch_on", lambda account_id=None: True)
    monkeypatch.setenv("DECISION_MODEL_NAME", "m")
    monkeypatch.setenv("DECISION_MODEL_BASE_URL", "https://x/api/v1")
    monkeypatch.setenv("DECISION_MODEL_API_KEY", "k")
    called = {"n": 0}
    monkeypatch.setattr(dr, "_call", lambda q, s: (called.__setitem__("n", called["n"] + 1), None)[1])
    assert dr.maybe_refine(3, "相距很远的问句", ents, _dec(0.5)) is None       # 不触发
    rec = json.loads(Path(dr.AUDIT_PATH).read_text(encoding="utf-8").splitlines()[-1])
    assert rec["event"] == "seen" and rec["fired"] is False and rec["trigger"] == "near_tie"
    assert called["n"] == 0
    monkeypatch.setenv("DECISION_AUDIT_SEEN", "0")                            # 可关
    dr.audit(event="marker")
    assert dr.maybe_refine(3, "相距很远的问句", ents, _dec(0.5)) is None
    assert json.loads(Path(dr.AUDIT_PATH).read_text(encoding="utf-8").splitlines()[-1])["event"] == "marker"


def test_daily_budget_uses_audit(ents, monkeypatch):
    monkeypatch.setattr(dr, "switch_on", lambda account_id=None: True)
    monkeypatch.setenv("DECISION_MODEL_NAME", "m")
    monkeypatch.setenv("DECISION_MODEL_BASE_URL", "https://x/api/v1")
    monkeypatch.setenv("DECISION_MODEL_API_KEY", "k")
    monkeypatch.setenv("DECISION_BUDGET_PER_DAY", "1")
    dr.audit(event="call", account_id=3, choice="x")          # 今日已用掉 1 次
    called = {"n": 0}
    monkeypatch.setattr(dr, "_call", lambda q, s: (called.__setitem__("n", called["n"] + 1), None)[1])
    assert dr.maybe_refine(3, "问句", ents, _dec()) is None and called["n"] == 0


# ---------------------------------------------------------------- 缓存时效（24 小时）与素材指纹
def test_cache_ttl_default_is_24h():
    assert dr.cache_ttl_h() == 24.0


def test_expired_and_broken_cache_lines_are_deleted(ents, monkeypatch):
    """★ 2026-10-13 用户裁定：超过 24 小时的缓存**作废并从文件里删掉**（坏行一并清掉）。"""
    p = Path(dr.CACHE_PATH)
    old = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(time.time() - 25 * 3600))
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    p.write_text(json.dumps({"key": "old1", "choice": "cli:bili", "ts": old}, ensure_ascii=False) + "\n"
                 + json.dumps({"key": "new1", "choice": "cli:bili", "ts": now}, ensure_ascii=False) + "\n"
                 + "{坏行\n", encoding="utf-8")
    idx = dr._cache_load()
    assert set(idx) == {"new1"}                                   # 过期的 + 坏行的都不留
    lines = [x for x in p.read_text(encoding="utf-8").splitlines() if x.strip()]
    assert len(lines) == 1 and json.loads(lines[0])["key"] == "new1"   # 文件里真删了，不是只跳过
    ev = [json.loads(x) for x in Path(dr.AUDIT_PATH).read_text(encoding="utf-8").splitlines()]
    assert ev[-1]["event"] == "cache_compact" and ev[-1]["dropped"] == 2


def test_ttl_zero_means_never_expire(ents, monkeypatch):
    monkeypatch.setenv("DECISION_CACHE_TTL_H", "0")
    assert dr._fresh({"ts": "2020-01-01 00:00:00"}) is True
    assert dr._fresh({"ts": ""}) is True


def test_stale_memory_entry_is_not_reused(ents, monkeypatch):
    """长跑的服务里内存索引会变旧：命中也要过时效，过期就真调（不是复用旧判断）。"""
    monkeypatch.setattr(dr, "switch_on", lambda account_id=None: True)
    monkeypatch.setenv("DECISION_MODEL_NAME", "m")
    monkeypatch.setenv("DECISION_MODEL_BASE_URL", "https://x/api/v1")
    monkeypatch.setenv("DECISION_MODEL_API_KEY", "k")
    from tools import capability_pool as cp
    monkeypatch.setattr(cp, "current_pool_version", lambda a=None: 0)
    called = {"n": 0}
    monkeypatch.setattr(dr, "_call", lambda q, s: (
        called.__setitem__("n", called["n"] + 1),
        {"answers": {"tool": {"type": "choice", "choice": "cli:bili"}}}, None)[1:])
    ck = dr.cache_key(3, "问句", 0, dr.entries_digest(ents))
    old = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(time.time() - 25 * 3600))
    dr._CACHE = {ck: {"key": ck, "choice": "cli:bili", "ts": old}}     # 过期记录直接塞进内存
    dr.maybe_refine(3, "问句", ents, _dec())
    assert called["n"] == 1 and dr._SESSION["hits"] == 0               # 没有复用，真调了一次


def test_digest_covers_keywords_and_abilities_user():
    """★ 2026-10-13 用户裁定：素材指纹必须含 keywords 与 abilities_user（卡片文本变了就要失效）。"""
    a = [FakeEntry("cli", "bili", "B站", "视频/番剧", "找视频", "看B站视频")]
    b = [FakeEntry("cli", "bili", "B站", "视频/番剧", "找视频", "看B站视频")]
    k = [FakeEntry("cli", "bili", "B站", "视频/番剧/直播", "找视频", "看B站视频")]
    u = [FakeEntry("cli", "bili", "B站", "视频/番剧", "找视频", "看B站视频/直播")]
    assert dr.entries_digest(a) == dr.entries_digest(b)
    assert dr.entries_digest(a) != dr.entries_digest(k)
    assert dr.entries_digest(a) != dr.entries_digest(u)


# ---------------------------------------------------------------- 第二批：预算闸门顺序 / 缓存记录字段
def test_cache_hit_survives_exhausted_budget(ents, monkeypatch):
    """★ 第二批（2026-10-13 用户裁定）：额度用尽时，**零成本的缓存命中仍要放行**。

    回归：旧写法把预算闸门排在缓存查询**之前** —— 当日额度一满，连"不花钱的复用"也被拒。
    """
    monkeypatch.setattr(dr, "switch_on", lambda account_id=None: True)
    monkeypatch.setenv("DECISION_MODEL_NAME", "m")
    monkeypatch.setenv("DECISION_MODEL_BASE_URL", "https://x/api/v1")
    monkeypatch.setenv("DECISION_MODEL_API_KEY", "k")
    monkeypatch.setenv("DECISION_BUDGET_PER_SESSION", "1")
    from tools import capability_pool as cp
    monkeypatch.setattr(cp, "current_pool_version", lambda a=None: 0)
    called = {"n": 0}
    monkeypatch.setattr(dr, "_call", lambda q, s: (
        called.__setitem__("n", called["n"] + 1),
        {"answers": {"tool": {"type": "choice", "choice": "cli:bili"}}}, None)[1:])

    assert dr.maybe_refine(3, "问句", ents, _dec()) is not None          # 第 1 次：真调，额度用完
    assert called["n"] == 1 and dr._SESSION["calls"] == 1

    out = dr.maybe_refine(3, "问句", ents, _dec())                        # 同句 → 应走缓存，不被预算拦
    assert out is not None and out["order"][0] == "cli:bili"
    assert dr._SESSION["hits"] == 1 and called["n"] == 1

    assert dr.maybe_refine(3, "另一句", ents, _dec()) is None             # 新问题 → 额度用尽，必须拦
    assert called["n"] == 1 and dr._SESSION["skips"] == 1


def test_cache_record_carries_question_and_pool_version(ents, monkeypatch):
    """★ 第二批：缓存记录要能反查「哪句问题、哪个池版本」（只存哈希键做不到）。"""
    monkeypatch.setattr(dr, "switch_on", lambda account_id=None: True)
    monkeypatch.setenv("DECISION_MODEL_NAME", "m")
    monkeypatch.setenv("DECISION_MODEL_BASE_URL", "https://x/api/v1")
    monkeypatch.setenv("DECISION_MODEL_API_KEY", "k")
    from tools import capability_pool as cp
    monkeypatch.setattr(cp, "current_pool_version", lambda a=None: 77)
    # 桩：模型返回的是**显示键**（`e.ref`），内部键只在回填时用 —— 与真实调用同形
    monkeypatch.setattr(dr, "_call", lambda q, s: (
        {"answers": {"tool": {"type": "choice", "choice": "bili"}}}, None))
    dr.maybe_refine(3, "拼多多上搜一下蓝牙耳机", ents, _dec())
    rec = json.loads(Path(dr.CACHE_PATH).read_text(encoding="utf-8").splitlines()[-1])
    assert rec["question"] == "拼多多上搜一下蓝牙耳机" and rec["pool_version"] == 77
    assert rec["disp"] == "bili" and rec["choice"] == "cli:bili"


# ---------------------------------------------------------------- 测评期间强制关闭（2026-10-13 用户裁定 6）
def test_eval_request_bypasses_layer_even_when_switch_on(ents, monkeypatch):
    """03 测评请求（allow=False）必须强制关：开关开着也一样，且**不读不写缓存**。

    否则测评数字里混进了决策层的功劳，与 01/03 的历史基线不可比（等于自己给自己放水）。
    """
    monkeypatch.setattr(dr, "switch_on", lambda account_id=None: True)
    monkeypatch.setenv("DECISION_MODEL_NAME", "m")
    monkeypatch.setenv("DECISION_MODEL_BASE_URL", "https://x/api/v1")
    monkeypatch.setenv("DECISION_MODEL_API_KEY", "k")
    called = {"n": 0}
    monkeypatch.setattr(dr, "_call", lambda q, s: (called.__setitem__("n", called["n"] + 1), None)[1])
    assert dr.maybe_refine(3, "问句", ents, _dec(), allow=False) is None
    assert called["n"] == 0 and dr._SESSION["hits"] == 0          # 没调模型、也没命中缓存
    assert not Path(dr.CACHE_PATH).exists()                        # 缓存文件根本没被碰
    rec = json.loads(Path(dr.AUDIT_PATH).read_text(encoding="utf-8").splitlines()[-1])
    assert rec["event"] == "seen" and rec["eval_off"] is True and rec["fired"] is False


def test_eval_request_silent_when_switch_off(ents, monkeypatch):
    """开关没开时，测评请求不写任何东西（与「没开」区分不了，也没必要留痕）。"""
    monkeypatch.setattr(dr, "switch_on", lambda account_id=None: False)
    assert dr.maybe_refine(3, "问句", ents, _dec(), allow=False) is None
    assert not Path(dr.AUDIT_PATH).exists()
    assert dr.maybe_refine(3, "问句", ents, _dec()) is None        # 普通请求同样不受影响
