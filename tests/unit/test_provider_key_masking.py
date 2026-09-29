# -*- coding: utf-8 -*-
"""回归用例：**自定义模型配置的密钥不能被掩码串污染**（2026-09-30 用户实测定位）。

事故还原：
  · `get_active_provider()` 原来对所有调用方都套 `_normalize_provider()`，而后者内部 `_mask_key(...)` ✗；
  · 造模型时 `api_key=<掩码串>` → 上游报 `Your api key: ****f98f is invalid`；
  · 掩码**保留尾部 4 位**，所以报错里看着像真 key，且"同一条 key 放 .env 能用、放自定义配置就 401"
    —— 用户 A/B 实测才把它揪出来。

规矩：**掩码只属于回显层**。凡是"拿去联网用"的路径（造模型、调价折算取配置等）必须拿原文。

运行：.venv/Scripts/python.exe -m pytest tests/unit/test_provider_key_masking.py -q
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from api import customize                                                      # noqa: E402
from tools.schema_personal import ensure_tables, get_personal_conn, purge_account   # noqa: E402

ensure_tables()

RAW_KEY = "sk-9f8e7d6c5b4a39281706f5e4d3c2b1a0"          # 35 位，形状同 DeepSeek 真 key


def mk_account(tag="pk"):
    conn = get_personal_conn()
    try:
        aid = int(conn.execute("INSERT INTO accounts (username, password_hash, role) VALUES (?,?,?)",
                               ("m6c%s_%d" % (tag, int(time.time() * 1000) % 1000000), "x", "user")).lastrowid)
        tok = "tk%s%d" % (tag, aid)
        conn.execute("INSERT INTO sessions (token, account_id, login_at) VALUES (?,?,datetime('now'))",
                     (tok, aid))
        conn.commit()
    finally:
        conn.close()
    return aid, tok


def add_provider(token, active=True):
    req = customize.LlmProviderReq(provider_name="Deepseek", model_name="deepseek-flash",
                                   base_url="https://api.deepseek.com", api_key=RAW_KEY,
                                   is_active=active)
    return customize.llm_provider_add(req, token)["provider"]


def test_active_provider_returns_raw_key_for_building_model():
    """★ 核心：造模型取配置时必须拿到**原样密钥**（否则掩码串会被发给上游 → 401）。"""
    aid, tok = mk_account()
    try:
        add_provider(tok)
        got = customize.get_active_provider(aid)
        assert got is not None
        assert got["api_key"] == RAW_KEY, "取到的是掩码串（会被当成密钥发给上游）✗"
        assert "*" not in got["api_key"] and len(got["api_key"]) == len(RAW_KEY)
    finally:
        purge_account(aid)


def test_list_endpoint_still_masks_key():
    """回显层必须继续掩码（前端不该看到明文）。"""
    aid, tok = mk_account()
    try:
        add_provider(tok)
        lst = customize.llm_providers_list(tok)["providers"][0]
        assert "*" in lst["api_key"] and lst["api_key"] != RAW_KEY
        assert lst["api_key"].endswith(RAW_KEY[-4:]), "掩码应保留尾部 4 位（便于用户核对）"
    finally:
        purge_account(aid)


def test_add_update_response_is_masked_but_db_keeps_raw():
    """新增/更新的**出参**是掩码（回显），但**库里存的**必须是原文。"""
    aid, tok = mk_account()
    try:
        out = add_provider(tok)
        assert "*" in out["api_key"]                     # 出参掩码
        conn = get_personal_conn()
        try:
            stored = conn.execute("SELECT api_key FROM llm_providers WHERE owner_id=?", (aid,)).fetchone()[0]
        finally:
            conn.close()
        assert stored == RAW_KEY                         # 库里原文
    finally:
        purge_account(aid)


def test_masked_key_is_not_re_saved_and_wipes_real_key():
    """前端回显掩码后再保存（不带新 key）→ **不许把掩码串写进库**（否则真 key 被毁，用户要重新申请）。"""
    aid, tok = mk_account()
    try:
        p = add_provider(tok)
        masked = p["api_key"]
        # 模拟前端"直接保存"：把掩码串原样提交
        req = customize.LlmProviderReq(provider_name="Deepseek", model_name="deepseek-flash",
                                       base_url="https://api.deepseek.com", api_key=masked,
                                       is_active=True)
        customize.llm_provider_update(p["id"], req, tok)
        conn = get_personal_conn()
        try:
            stored = conn.execute("SELECT api_key FROM llm_providers WHERE id=?", (p["id"],)).fetchone()[0]
        finally:
            conn.close()
        assert stored == RAW_KEY, "掩码串被写进了库（真 key 被毁）✗"
    finally:
        purge_account(aid)
