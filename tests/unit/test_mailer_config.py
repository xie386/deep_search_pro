# -*- coding: utf-8 -*-
"""utils/mailer.py 的配置解析 / 配额 / 错误归一化（v4.0 大点 04 · 议题二）。

口径（2026-10-13 用户拍板）：`.env` 里 `QQMAIL`（发件邮箱）+ `QQMSTP`（授权码）是项目级单发件箱，
收件方是各账号自己绑的邮箱；两个键不进前端、不进库、不回传、不回显。
"""
from __future__ import annotations

import os
import smtplib

from utils import mailer, settings


class _Env:
    """临时改环境变量（设置项走 os.getenv 兜底），退出时还原。

    ★ 同时把「应用设置库」隐藏掉：真机库里可能有**用户自己保存过的值**（设置页点过保存就有），
      而取值顺序是 **库 → .env → 默认** —— 不隐藏的话，`MAIL_DEBUG="1"` 之类的环境变量根本压不住，
      这一组用例会随用户的操作时红时绿（2026-10-13 实际踩到）。
    """

    def __init__(self, **kw):
        self.kw = kw
        self.old: dict = {}
        self._orig_load = None

    def __enter__(self):
        self._orig_load = settings._load_all
        settings._load_all = lambda: {}
        for k, v in self.kw.items():
            self.old[k] = os.environ.get(k)
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = str(v)
        settings.reload()
        return self

    def __exit__(self, *exc):
        for k, v in self.old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        if self._orig_load is not None:
            settings._load_all = self._orig_load
        settings.reload()
        return False


def test_qq_keys_configure_sender_and_defaults():
    """QQMAIL + QQMSTP 就够用：主机/端口/SSL 取 QQ 默认值。"""
    with _Env(QQMAIL="sender@qq.com", QQMSTP="authcode12345678", MAIL_DEBUG="0",
              SMTP_HOST=None, SMTP_PORT=None, SMTP_SSL=None, SMTP_USER=None, SMTP_AUTH_CODE=None):
        c = mailer._cfg()
        assert c["user"] == "sender@qq.com" and c["code"] == "authcode12345678"
        assert c["user_key"] == "QQMAIL" and c["code_key"] == "QQMSTP"
        assert c["host"] == "smtp.qq.com" and c["port"] == 465 and c["ssl"] is True
        assert mailer.is_configured() is True
        assert mailer._debug_mode() is False
        assert mailer.sender() == "sender@qq.com"


def test_qq_keys_win_over_legacy_keys():
    """同时存在且不一致时以 QQ 键为准（老键只做兼容，不再抢权）。"""
    with _Env(QQMAIL="new@qq.com", QQMSTP="newcode00000000",
              SMTP_USER="old@qq.com", SMTP_AUTH_CODE="oldcode0000000", MAIL_DEBUG="0"):
        c = mailer._cfg()
        assert c["user"] == "new@qq.com" and c["code"] == "newcode00000000"
        assert (c["user_key"], c["code_key"]) == ("QQMAIL", "QQMSTP")


def test_legacy_keys_still_work():
    """只配老键（SMTP_USER / SMTP_AUTH_CODE）时照旧可用 —— 兼容不能断。"""
    with _Env(QQMAIL=None, QQMSTP=None, SMTP_USER="old@qq.com", SMTP_AUTH_CODE="oldcode0000000",
              MAIL_DEBUG="0"):
        assert mailer.is_configured() is True
        assert mailer.sender() == "old@qq.com"


def test_non_qq_provider_can_be_overridden():
    """换企业邮：SMTP_HOST/PORT/SSL 仍可覆盖（不写死 QQ）。"""
    with _Env(QQMAIL="a@corp.com", QQMSTP="x" * 16, MAIL_DEBUG="0",
              SMTP_HOST="mail.corp.com", SMTP_PORT="587", SMTP_SSL="0"):
        c = mailer._cfg()
        assert (c["host"], c["port"], c["ssl"]) == ("mail.corp.com", 587, False)


def test_unconfigured_falls_back_to_debug_mode():
    """没配发件箱 → 自动进调试模式（不发信、写 outbox），界面状态也说得清。"""
    with _Env(QQMAIL=None, QQMSTP=None, SMTP_USER=None, SMTP_AUTH_CODE=None, MAIL_DEBUG=None):
        assert mailer.is_configured() is False
        assert mailer._debug_mode() is True
        assert "QQMAIL" in mailer.config_hint() and "QQMSTP" in mailer.config_hint()


def test_mail_debug_switch_forces_debug_even_if_configured():
    with _Env(QQMAIL="sender@qq.com", QQMSTP="authcode12345678", MAIL_DEBUG="1"):
        assert mailer.is_configured() is True
        assert mailer._debug_mode() is True


def test_error_normalization_never_leaks_auth_code():
    """授权码绝不能出现在给用户看的报错里（服务商回显也不许透传）。"""
    code = "supersecret12345"
    e = smtplib.SMTPDataError(550, b"5.7.1 rejected because auth supersecret12345 quota exceeded")
    msg = mailer._friendly(e, code)
    assert code not in msg, "授权码被回显了：%s" % msg
    assert "拒收" in msg or "限" in msg

    for exc, kw in ((smtplib.SMTPAuthenticationError(535, b"auth failed"), "授权码"),
                    (smtplib.SMTPRecipientsRefused({}), "收件邮箱"),
                    (smtplib.SMTPSenderRefused(550, b"nope", "x@y.z"), "发件箱"),
                    (smtplib.SMTPServerDisconnected("Connection unexpectedly closed"), "中断"),
                    (TimeoutError("timed out"), "超时")):
        out = mailer._friendly(exc, code)
        assert kw in out, "异常 %s 的文案没覆盖：%s" % (type(exc).__name__, out)
        assert code not in out
        assert "Traceback" not in out and "smtplib" not in out, "不该把内部类名/堆栈甩给用户：%s" % out


def test_send_rejects_bad_recipient():
    r = mailer.send("not-an-email", "s", "b")
    assert r["ok"] is False and r["mode"] == "none"


def test_send_debug_mode_writes_outbox(tmp_path, monkeypatch):
    """调试模式：不发信，落 outbox，回传 mode=debug。"""
    monkeypatch.setattr(mailer, "OUTBOX", tmp_path / "_mail_outbox.log")
    with _Env(QQMAIL=None, QQMSTP=None, SMTP_USER=None, SMTP_AUTH_CODE=None, MAIL_DEBUG="1"):
        r = mailer.send("to@qq.com", "主题", "正文")
    assert r["ok"] is True and r["mode"] == "debug"
    body = (tmp_path / "_mail_outbox.log").read_text(encoding="utf-8")
    assert "to@qq.com" in body and "正文" in body


def test_send_code_wording_covers_three_purposes(monkeypatch):
    """三种用途的邮件文案各自正确（绑/解绑/注销），且都带有效期说明。"""
    seen = {}
    monkeypatch.setattr(mailer, "send",
                        lambda to, subject, body: seen.setdefault("v", []).append((subject, body)) or {"ok": True})
    for p in ("bind", "unbind", "delete"):
        mailer.send_code("to@qq.com", "123456", purpose=p)
    got = dict(zip(("bind", "unbind", "delete"), seen["v"]))
    assert "绑定邮箱" in got["bind"][0] and "解绑邮箱" in got["unbind"][0] and "注销账号" in got["delete"][0]
    for subj, body in got.values():
        assert "123456" in body and "5 分钟" in body


def test_quota_zero_means_unlimited():
    with _Env(MAIL_QUOTA_PER_ACCOUNT_DAY="0", MAIL_QUOTA_GLOBAL_DAY="0"):
        assert mailer.quota_problem(1) == ""


def test_quota_blocks_when_reached(monkeypatch):
    """撞上限时给出人话原因（含数字），而不是把服务商的原始报错甩出来。"""
    with _Env(MAIL_QUOTA_PER_ACCOUNT_DAY="10", MAIL_QUOTA_GLOBAL_DAY="50"):
        monkeypatch.setattr(mailer, "quota_usage", lambda aid=None: {"account": 10, "global": 12})
        msg = mailer.quota_problem(7)
        assert "10" in msg and "24 小时" in msg

        monkeypatch.setattr(mailer, "quota_usage", lambda aid=None: {"account": 3, "global": 50})
        msg2 = mailer.quota_problem(7)
        assert "50" in msg2 and "本机" in msg2

        monkeypatch.setattr(mailer, "quota_usage", lambda aid=None: {"account": 3, "global": 12})
        assert mailer.quota_problem(7) == ""
