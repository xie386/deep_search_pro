"""E5（密钥「名入库、值走 .env」）单测。全离线：只在临时 .env 上写，不碰真实库/真 .env。"""
import os

import pytest


@pytest.fixture()
def sec(tmp_path, monkeypatch):
    import utils.settings as st
    import utils.secrets as sc

    env_file = tmp_path / ".env"
    env_file.write_text("# 注释要保留\nOLD_KEY=keepme\n", encoding="utf-8")
    monkeypatch.setattr(st, "ENV_PATH", env_file)
    monkeypatch.setattr(sc, "_write_env", st._write_env)
    for k in list(os.environ):
        if k.startswith(("LLM_KEY_", "TOOL_KEY_")):
            monkeypatch.delenv(k, raising=False)
    return sc, env_file


# ── 键名生成 ──────────────────────────────────────────────────────────────
def test_env_name_shape(sec):
    sc, _ = sec
    assert sc.env_name("llm", 3, "DeepSeek") == "LLM_KEY_DEEPSEEK_3"
    assert sc.env_name("llm", 7, "GLM-5.3 Flash") == "LLM_KEY_GLM_5_3_FLASH_7"   # 非法字符→下划线
    assert sc.env_name("tool", "1_my-api", "") == "TOOL_KEY_TOOL_1_my-api"   # 标签空 → 用 KIND 兜底
    assert sc.env_name("llm", 1, "") .startswith("LLM_KEY_LLM_")


# ── 写入 .env（复用 _write_env：保留注释 + 备份） ────────────────────────
def test_put_writes_env_and_sets_process_env(sec):
    sc, env_file = sec
    name = sc.put("llm", 3, "sk-abcdef123456", "DeepSeek")
    text = env_file.read_text(encoding="utf-8")
    assert "OLD_KEY=keepme" in text and "# 注释要保留" in text      # 既有内容不动
    assert name in text and "sk-abcdef123456" in text                # 新键写进去了
    assert os.environ.get(name) == "sk-abcdef123456"                 # 本进程立即可见
    assert list(env_file.parent.glob(".env.bak_*"))                  # 写前自动备份


def test_put_empty_raises(sec):
    sc, _ = sec
    with pytest.raises(ValueError):
        sc.put("llm", 1, "   ", "X")


# ── 读取：env 优先 / 老库明文兜底 ────────────────────────────────────────
def test_get_prefers_env(sec, monkeypatch):
    sc, _ = sec
    monkeypatch.setenv("LLM_KEY_X_1", "from-env")
    assert sc.get("LLM_KEY_X_1", legacy="from-db") == "from-env"


def test_get_falls_back_to_legacy(sec):
    sc, _ = sec
    assert sc.get(None, legacy="from-db") == "from-db"


def test_get_missing_env_returns_empty_not_legacy(sec):
    """键名在、但 .env 里没这个值 → 返回空（调用方给提示），不许拿明文列顶包。"""
    sc, _ = sec
    assert sc.get("LLM_KEY_NOT_SET_9", legacy="from-db") == ""


# ── 与 provider 出口的联动 ───────────────────────────────────────────────
def test_provider_out_masks_env_value(sec, monkeypatch):
    sc, _ = sec
    import api.customize as cust
    monkeypatch.setenv("LLM_KEY_DEEPSEEK_3", "sk-abcdef123456")
    row = {"id": 3, "provider_name": "DeepSeek", "model_name": "deepseek-chat",
           "base_url": "https://x/v1", "api_key": "", "secret_env": "LLM_KEY_DEEPSEEK_3",
           "is_active": 1, "price_in_cached": None, "price_in_uncached": None,
           "price_out": None, "supports_vision": 0}
    out_masked = cust._normalize_provider(row)                      # 列表回显
    assert out_masked["api_key"] == "****3456"                      # 掩码（值来自 .env）
    assert out_masked["secret_env"] == "LLM_KEY_DEEPSEEK_3"         # 键名可回传
    assert out_masked["key_configured"] is True
    out_raw = cust._normalize_provider(row, mask=False)             # 造模型取真值
    assert out_raw["api_key"] == "sk-abcdef123456"


def test_provider_out_legacy_plaintext_still_works(sec):
    sc, _ = sec
    import api.customize as cust
    row = {"id": 9, "provider_name": "老库", "model_name": "m", "base_url": "https://x/v1",
           "api_key": "sk-legacy0000", "secret_env": None, "is_active": 0,
           "price_in_cached": None, "price_in_uncached": None, "price_out": None,
           "supports_vision": 0}
    assert cust._normalize_provider(row, mask=False)["api_key"] == "sk-legacy0000"
    assert cust._normalize_provider(row)["api_key"] == "****0000"


# ── tool_sources 侧 ─────────────────────────────────────────────────────
def test_tool_auth_secret_resolves_env(sec, monkeypatch):
    sc, _ = sec
    import api.tools_sources as ts
    monkeypatch.setenv("TOOL_KEY_1_weather", "tok-9999")
    assert ts._auth_secret({"secret_env": "TOOL_KEY_1_weather"}) == "tok-9999"
    assert ts._auth_secret({"secret": "plain-1"}) == "plain-1"
    assert ts._auth_secret({}) == ""
    masked = ts._mask_secret(ts._auth_secret({"secret_env": "TOOL_KEY_1_weather"}))
    assert "***" in masked and masked != "tok-9999"      # 掩码了，但不等于明文
