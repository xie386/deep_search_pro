# -*- coding: utf-8 -*-
"""M5-8 用例：阶段 2 预留（配置导向 / 只读闸 / 失败不静默 / 隔离）。

判据来源：M5 方案 §六 M5-8（≥6 项）+ G7（**表里不出现厂商名**）+ G8（失败落 `check_error`、不静默）+ C3（只读闸、复用 M1 适配器口径）。

运行：.venv/Scripts/python.exe -m pytest tests/test_price_source_stub.py -q
"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from tools.price_ledger import record_price                                    # noqa: E402
from tools.price_source import (ERROR_RULE, build_url, check_price, extract_value,   # noqa: E402
                                list_sources, upsert_source)
from tools.schema_personal import ensure_tables, get_personal_conn, purge_account   # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ensure_tables()

SRC = {"name": "我的源", "url_template": "https://api.example.com/price?sku={sku}",
       "param_map": json.dumps({"sku": "sku"}), "value_path": "data.price", "enabled": 1,
       "timeout_s": 5}


def mk_account():
    conn = get_personal_conn()
    try:
        aid = int(conn.execute("INSERT INTO accounts (username, password_hash, role) VALUES (?,?,?)",
                               ("m5s_%d" % (int(time.time() * 1000) % 1000000), "x", "user")).lastrowid)
        conn.commit()
    finally:
        conn.close()
    return aid


def mk_own(aid, name="监控商品", price=100.0):
    conn = get_personal_conn()
    try:
        cur = conn.execute("INSERT INTO products (owner_id, product_name, category, price, status)"
                           " VALUES (?,?,?,?,?)", (aid, name, "个护", price, "active"))
        conn.commit()
        return int(cur.lastrowid)
    finally:
        conn.close()


def errors(aid):
    conn = get_personal_conn()
    try:
        return [r[0] for r in conn.execute("SELECT check_error FROM price_alerts WHERE account_id=?"
                                           " AND rule=?", (aid, ERROR_RULE))]
    finally:
        conn.close()


def ledger(aid):
    conn = get_personal_conn()
    try:
        return [dict(r) for r in conn.execute("SELECT price, source_type, source_ref FROM price_history"
                                              " WHERE account_id=? ORDER BY id", (aid,))]
    finally:
        conn.close()


# ---------------------------------------------------------------- ① 表与配置导向
def test_table_columns_exist():
    cols = {r[1] for r in get_personal_conn().execute("PRAGMA table_info(price_sources)")}
    for c in ("account_id", "name", "url_template", "param_map", "value_path", "timeout_s",
              "readonly", "enabled", "secret_env"):
        assert c in cols, "缺列 " + c


def test_no_vendor_name_anywhere():
    """★ G7：配置导向 —— 表结构与模块里**不许出现任何厂商名**（新增源=填字段，不改代码）。"""
    banned = ["taobao", "淘宝", "tmall", "天猫", "jd.com", "京东", "pinduoduo", "拼多多", "1688",
              "amazon", "亚马逊", "ebay", "shopee", "suning", "苏宁", "steam", "suning.com"]
    blobs = {}
    schema = open(os.path.join(ROOT, "tools", "schema_personal.py"), encoding="utf-8").read()
    i = schema.find("price_sources")
    blobs["schema(price_sources 附近)"] = schema[max(0, i - 400): i + 2000].lower()
    blobs["price_source.py"] = open(os.path.join(ROOT, "tools", "price_source.py"),
                                    encoding="utf-8").read().lower()
    for where, txt in blobs.items():
        for b in banned:
            assert b not in txt, "%s 里出现了厂商名 %r（G7：必须配置导向）" % (where, b)


def test_new_source_is_pure_configuration():
    aid = mk_account()
    try:
        sid = upsert_source(aid, SRC)
        got = [s for s in list_sources(aid) if int(s["id"]) == sid][0]
        assert got["url_template"].startswith("https://") and got["enabled"] == 1
        assert got["readonly"] == 1, "★ 新建源必须落成只读（C3）"
    finally:
        purge_account(aid)


def test_sources_default_disabled_and_no_credential_value():
    aid = mk_account()
    try:
        upsert_source(aid, dict(SRC, enabled=0, secret_env="MY_PRICE_KEY"))
        s = list_sources(aid)[0]
        assert s["enabled"] == 0, "★ 阶段 2 默认不启用"
        assert s["secret_env"] == "MY_PRICE_KEY"
        assert all(v != "MY_PRICE_KEY_VALUE" for v in s.values()), "★ 只存环境变量名，不存值"
    finally:
        purge_account(aid)


# ---------------------------------------------------------------- ② URL/取值映射（复用 M1 适配器口径）
def test_build_url_uses_config_not_code():
    assert build_url({"url_template": "https://h/p?sku={sku}"}, {"sku": "A9"}) == "https://h/p?sku=A9"
    src = {"url_template": "https://h/price/{code}", "param_map": json.dumps({"code": "sku"})}
    assert build_url(src, {"sku": "X1"}) == "https://h/price/X1", "参数位置由 param_map 决定"


def test_extract_value_nested_and_list():
    body = {"data": {"list": [{"price": 89}], "price": "¥1,299 元"}}
    assert extract_value(body, "data.price") == "¥1,299 元"
    assert extract_value(body, "data.list.0.price") == 89
    assert extract_value(body, "data.nope") is None
    assert extract_value(body, "") is None


# ---------------------------------------------------------------- ③ 成功路径
def test_check_success_records_to_ledger():
    aid = mk_account()
    try:
        pid = mk_own(aid)
        upsert_source(aid, SRC)
        r = check_price(aid, "product", pid, sku="A9",
                        fetch=lambda u, t, h: (200, json.dumps({"data": {"price": "88.5"}})))
        assert r["ok"] and r["price"] == 88.5 and "sku=A9" in r["url"]
        rows = ledger(aid)
        assert rows and rows[-1]["source_type"] == "api" and rows[-1]["source_ref"] == "我的源"
        conn = get_personal_conn()
        try:
            assert conn.execute("SELECT last_price FROM products WHERE id=?", (pid,)).fetchone()[0] == 88.5
        finally:
            conn.close()
    finally:
        purge_account(aid)


# ---------------------------------------------------------------- ④ 失败不静默（G8）
def test_timeout_is_reported_and_logged():
    aid = mk_account()
    try:
        pid = mk_own(aid)
        upsert_source(aid, SRC)

        def boom(u, t, h):
            raise TimeoutError("timed out")

        r = check_price(aid, "product", pid, sku="A9", fetch=boom)
        assert not r["ok"] and "TimeoutError" in r["reason"] and r["reason"]
        assert errors(aid) and "TimeoutError" in errors(aid)[0], "★ G8：失败必须落 check_error"
        assert ledger(aid) == [], "失败不该写台账"
    finally:
        purge_account(aid)


def test_missing_value_path_is_reported():
    aid = mk_account()
    try:
        pid = mk_own(aid)
        upsert_source(aid, dict(SRC, value_path="data.wrong.path"))
        r = check_price(aid, "product", pid, sku="A9",
                        fetch=lambda u, t, h: (200, json.dumps({"data": {"price": 88}})))
        assert not r["ok"] and "取值路径" in r["reason"]
        assert errors(aid), "★ 字段缺失也要落 check_error"
    finally:
        purge_account(aid)


def test_non_json_and_http_error_are_reported():
    aid = mk_account()
    try:
        pid = mk_own(aid)
        upsert_source(aid, SRC)
        r1 = check_price(aid, "product", pid, sku="A", fetch=lambda u, t, h: (200, "<html>oops</html>"))
        assert not r1["ok"] and "不是 JSON" in r1["reason"]
        r2 = check_price(aid, "product", pid, sku="A", fetch=lambda u, t, h: (503, "{}"))
        assert not r2["ok"] and "503" in r2["reason"]
        assert len(errors(aid)) == 2
    finally:
        purge_account(aid)


def test_no_enabled_source_says_so_and_logs_nothing():
    aid = mk_account()
    try:
        pid = mk_own(aid)
        r = check_price(aid, "product", pid, sku="A")
        assert not r["ok"] and "阶段 2 未接源" in r["reason"]
        assert errors(aid) == [], "★「还没接源」不是源坏了，不该落错误"
        upsert_source(aid, dict(SRC, enabled=0))
        assert "阶段 2 未接源" in check_price(aid, "product", pid, sku="A")["reason"]
    finally:
        purge_account(aid)


# ---------------------------------------------------------------- ⑤ 只读闸与隔离
def test_readonly_gate_rejects_writes():
    aid = mk_account()
    try:
        try:
            upsert_source(aid, dict(SRC, readonly=0))
            raise AssertionError("readonly=0 应当被拒")
        except ValueError as e:
            assert "只读" in str(e)
        # 即使库里有历史脏数据（readonly=0），检查时也拒绝
        sid = upsert_source(aid, SRC)
        conn = get_personal_conn()
        try:
            conn.execute("UPDATE price_sources SET readonly=0 WHERE id=?", (sid,))
            conn.commit()
        finally:
            conn.close()
        pid = mk_own(aid)
        r = check_price(aid, "product", pid, sku="A", fetch=lambda u, t, h: (200, "{}"))
        assert not r["ok"] and "只读" in r["reason"]
    finally:
        purge_account(aid)


def test_bad_url_template_rejected():
    aid = mk_account()
    try:
        for bad in ("ftp://x/p", "api.example.com/{sku}", ""):
            try:
                upsert_source(aid, dict(SRC, url_template=bad))
                raise AssertionError("应当拒掉 " + repr(bad))
            except ValueError as e:
                assert "http" in str(e)
    finally:
        purge_account(aid)


def test_sources_are_account_scoped():
    a, b = mk_account(), mk_account()
    try:
        sid = upsert_source(a, SRC)
        assert list_sources(b) == []
        try:
            upsert_source(b, dict(SRC, id=sid, name="改别人的"))
            raise AssertionError("跨账号改源应当被拒")
        except ValueError as e:
            assert "不属于当前账号" in str(e)
    finally:
        purge_account(a); purge_account(b)


def test_module_does_not_call_models():
    """价格检查是纯 HTTP 取值 + 台账写入，不许调模型（C5 的同类要求）。"""
    s = open(os.path.join(ROOT, "tools", "price_source.py"), encoding="utf-8").read()
    for bad in ("create_deep_agent", ".invoke(", "ChatOpenAI", "llm"):
        assert bad not in s, "不该出现 " + bad


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q"]))
