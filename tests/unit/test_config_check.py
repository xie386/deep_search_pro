"""配置自检单测（04 补 · E6）。全离线：只 monkeypatch 环境变量。"""
import importlib
import logging
import os

import pytest


@pytest.fixture()
def cc(monkeypatch):
    # 隔离：清掉所有可能干扰的键，再按用例设置
    for k in ("OPENAI_API_KEY", "LLM_MODEL_MAX", "OPENAI_BASE_URL", "TAVILY_API_KEY",
              "QQMAIL", "QQMSTP", "SMTP_USER", "SMTP_AUTH_CODE",
              "EMBEDDING_MODEL_PATH", "RERANKER_MODEL_PATH",
              "ASR_PORT", "SMTP_PORT", "MYSQL_PORT",
              "MAIL_QUOTA_PER_ACCOUNT_DAY", "MAIL_QUOTA_GLOBAL_DAY", "APP_URL"):
        monkeypatch.delenv(k, raising=False)
    from utils import config_check
    importlib.reload(config_check)
    return config_check


def test_all_empty_is_warnings_not_errors(cc):
    r = cc.check_config()
    assert r["errors"] == []
    assert any("模型" in w for w in r["warnings"])
    assert any("TAVILY" in w for w in r["warnings"])
    assert any("发件箱" in w for w in r["warnings"])


def test_bad_base_url_is_error(cc, monkeypatch):
    monkeypatch.setenv("OPENAI_BASE_URL", "api.openai.com/v1")
    r = cc.check_config()
    assert any("OPENAI_BASE_URL" in e for e in r["errors"])


def test_good_base_url_passes(cc, monkeypatch):
    monkeypatch.setenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
    r = cc.check_config()
    assert "OPENAI_BASE_URL" in r["ok"]


def test_missing_model_dir_is_warning_present_but_bogus_is_error(cc, monkeypatch):
    r = cc.check_config()
    assert any("EMBEDDING_MODEL_PATH" in w for w in r["warnings"])
    monkeypatch.setenv("EMBEDDING_MODEL_PATH", r"D:/no/such/dir/bge-large-zh")
    r2 = cc.check_config()
    assert any("EMBEDDING_MODEL_PATH" in e and "不存在" in e for e in r2["errors"])


def test_numeric_fields(cc, monkeypatch):
    monkeypatch.setenv("SMTP_PORT", "abc")
    monkeypatch.setenv("MAIL_QUOTA_PER_ACCOUNT_DAY", "-3")
    r = cc.check_config()
    assert any("SMTP_PORT" in e for e in r["errors"])
    assert any("MAIL_QUOTA_PER_ACCOUNT_DAY" in e for e in r["errors"])
    monkeypatch.setenv("SMTP_PORT", "465")
    monkeypatch.setenv("MAIL_QUOTA_PER_ACCOUNT_DAY", "10")
    r2 = cc.check_config()
    assert "SMTP_PORT" in r2["ok"] and "MAIL_QUOTA_PER_ACCOUNT_DAY" in r2["ok"]


def test_mail_keys_prefer_qq_but_legacy_ok(cc, monkeypatch):
    monkeypatch.setenv("SMTP_USER", "a@b.com")
    monkeypatch.setenv("SMTP_AUTH_CODE", "x")
    r = cc.check_config()
    assert any("发件箱" in o for o in r["ok"])
    assert not any("发件箱" in w for w in r["warnings"])


def test_log_config_check_emits_levels(cc, monkeypatch, caplog):
    monkeypatch.setenv("OPENAI_BASE_URL", "not-a-url")
    with caplog.at_level(logging.INFO, logger="config_check"):
        rep = cc.log_config_check(logging.getLogger("config_check"))
    assert rep["errors"]
    msgs = [rec.getMessage() for rec in caplog.records]
    assert any("配错" in m for m in msgs)          # ERROR 级
    assert any("配置自检" in m for m in msgs)        # INFO 汇总
