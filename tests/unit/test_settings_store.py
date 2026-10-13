# -*- coding: utf-8 -*-
"""设置存储与发信通道的单测（v4.0 大点 04 · 议题三）——全离线，不碰真实 .env。

覆盖：
  ① 登记表完整性（每项都有 label/help/group/kind，select 有 choices，number 边界合理）；
  ② 取值兜底顺序：库 → .env → 默认值（`source_of` 要能说清值来自哪里）；
  ③ 校验：非法枚举 / 非数字 / 超界 必须报错且带人话原因；
  ④ 密钥永不回明文（掩码函数）；
  ⑤ 密钥写 `.env` 时**保留注释与其余行**、并生成备份（用临时 ENV_PATH 验证，绝不改项目真实 .env）。
"""
import os
import tempfile
from pathlib import Path

from utils import settings


def test_specs_are_well_formed():
    assert len(settings.SPECS) >= 20, "登记项太少，说明漏收"
    keys = [s["key"] for s in settings.SPECS]
    assert len(keys) == len(set(keys)), "键重复：%s" % keys
    for s in settings.SPECS:
        assert s.get("label"), s
        assert s.get("help"), s
        assert s.get("group") in settings.GROUP_LABELS, s
        assert s.get("kind") in ("switch", "number", "select", "text", "secret"), s
        if s["kind"] == "select":
            assert s.get("choices"), s
            assert s.get("default") in s["choices"], s
    # 议题三的点名要求：这些"没进 .env 的开关"必须都在表里
    for k in ("DECISION_CACHE_ENABLED", "DECISION_AUDIT_SEEN", "DECISION_ALLOW_NONE",
              "ZX_STREAM", "ZX_ASSEMBLY_SOURCES", "DS_DEBUG_PROMPTS"):
        assert k in keys, "缺开关：%s" % k


def test_get_falls_back_to_default_then_db():
    """取值顺序：库 → .env → 默认。

    ★ 2026-10-13 修正：本用例原来假设「库里没有该键」，但**真机库里可能有用户自己保存过的值**
      （设置页点一次「保存设置」就会整表落库）——那时 source_of 会是 db、用例假失败。
      现在开头先把用户那条快照下来并删除，结束时原样放回：单测只动自己的隔离态，不依赖用户数据。
    """
    from tools.schema_personal import get_personal_conn as _gpc
    settings.reload()
    key = "DECISION_TOP_K"
    _c = _gpc()
    try:
        _prev = _c.execute("SELECT value FROM app_settings WHERE key=?", (key,)).fetchone()
        _c.execute("DELETE FROM app_settings WHERE key=?", (key,))
        _c.commit()
    except Exception:
        _prev = None
    finally:
        _c.close()
    settings.reload()
    old_env = os.environ.pop(key, None)
    try:
        assert settings.get(key) == settings.SPEC_BY_KEY[key]["default"]
        assert settings.source_of(key) == "default"
        os.environ[key] = "6"
        settings.reload()
        assert settings.get(key) == "6" and settings.source_of(key) == "env"
        settings.save({key: "4"}, actor="unit")
        assert settings.get(key) == "4" and settings.source_of(key) == "db", "库值必须压过 .env"
        # 收尾：把库里这条删掉，避免影响别的用例
        from tools.schema_personal import get_personal_conn
        conn = get_personal_conn()
        conn.execute("DELETE FROM app_settings WHERE key=?", (key,))
        conn.commit()
        conn.close()
        settings.reload()
    finally:
        if old_env is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = old_env
        if _prev is not None:                      # 把用户原来那条放回去
            _c = _gpc()
            try:
                _c.execute("INSERT INTO app_settings(key, value, updated_at, updated_by) VALUES (?,?,?,?) "
                           "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
                           (key, _prev["value"], int(__import__("time").time()), "test-restore"))
                _c.commit()
            finally:
                _c.close()
        settings.reload()


def test_validation_rejects_bad_values():
    for key, bad in (("DECISION_TRIGGER", "whatever"), ("DECISION_TOP_K", "1"),
                     ("DECISION_TOP_K", "99"), ("DECISION_TIE_THRESHOLD", "abc"),
                     ("ZX_ASSEMBLY_SOURCES", "middle"), ("OPENAI_API_KEY", "x")):
        try:
            settings.save({key: bad}, actor="unit")
        except ValueError as e:
            assert str(e), "报错必须有人话说明：%s" % key
        else:
            raise AssertionError("%s=%r 本该被拒" % (key, bad))


def test_save_rejects_unknown_and_readonly():
    for key in ("NOT_A_REAL_KEY", "OPENAI_API_KEY"):
        try:
            settings.save({key: "1"}, actor="unit")
        except ValueError:
            pass
        else:
            raise AssertionError("%s 本该被拒" % key)


def test_switch_normalization_and_masking():
    assert settings._validate(settings.SPEC_BY_KEY["ZX_STREAM"], "true") == "1"
    assert settings._validate(settings.SPEC_BY_KEY["ZX_STREAM"], "off") == "0"
    assert settings._validate(settings.SPEC_BY_KEY["ZX_STREAM"], "") == "0"
    # 阈值不能被"是整数"取整丢掉（0.0005 那次教训）
    assert settings.masked("SMTP_AUTH_CODE") in ("", "****")  # 未配置时为空
    old = os.environ.get("SMTP_AUTH_CODE")
    os.environ["SMTP_AUTH_CODE"] = "abcdefghijklmn"
    settings.reload()
    try:
        m = settings.masked("SMTP_AUTH_CODE")
        assert m != "abcdefghijklmn" and m.startswith("abcd") and m.endswith("klmn") and "*" in m, m
    finally:
        if old is None:
            os.environ.pop("SMTP_AUTH_CODE", None)
        else:
            os.environ["SMTP_AUTH_CODE"] = old
        settings.reload()


def test_env_write_keeps_comments_and_backs_up(monkeypatch=None):
    """密钥写 .env：保留注释与其余行 + 生成 .bak；用临时文件验证，绝不碰项目真实 .env。"""
    d = Path(tempfile.mkdtemp(prefix="zx_env_"))
    envf = d / ".env"
    envf.write_text("# 注释要留着\nOPENAI_API_KEY=sk-old\n# 又一条注释\nSMTP_USER=a@qq.com\n", encoding="utf-8")
    real = settings.ENV_PATH
    settings.ENV_PATH = envf
    try:
        bak = settings._write_env({"SMTP_AUTH_CODE": "new-secret", "SMTP_USER": "b@qq.com"})
        body = envf.read_text(encoding="utf-8")
        assert "# 注释要留着" in body and "# 又一条注释" in body, "注释被吃掉了"
        assert "SMTP_AUTH_CODE=new-secret" in body, "新键没写入"
        assert "SMTP_USER=b@qq.com" in body and "a@qq.com" not in body, "既有键没被覆盖"
        assert bak and bak.exists(), "缺 .env 备份"
        assert "OPENAI_API_KEY=sk-old" in bak.read_text(encoding="utf-8"), "备份应是改前内容"
        # 幂等：再写一次不会重复追加键
        settings._write_env({"SMTP_AUTH_CODE": "again"})
        body2 = envf.read_text(encoding="utf-8")
        assert body2.count("SMTP_AUTH_CODE=") == 1, "重复写会追加重复键"
    finally:
        settings.ENV_PATH = real
        for k in ("SMTP_AUTH_CODE", "SMTP_USER"):
            os.environ.pop(k, None)
        settings.reload()
