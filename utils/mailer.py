# -*- coding: utf-8 -*-
"""发信（v4.0 大点 04 · 议题二）：账号绑定邮箱 / 注销账号的验证码通道。

设计口径（2026-10-13 用户拍板）
-------------------------------
- **项目级单发件箱**：`.env` 里 `QQMAIL`（发件邮箱地址）+ `QQMSTP`（该邮箱的 SMTP 授权码）。
  这两个键**只存在于 `.env`**：不进前端、不进数据库、接口不回传、日志与报错不回显。
  今后换发件箱 = 改这两行，不需要动任何账号数据。
- **账号级收件箱**：每个账号绑自己的邮箱（`accounts.email`，全局唯一，一邮箱只属一个账号）。
  发信永远是「固定的项目发件箱 → 该账号的目标邮箱」——与参考项目 VarifyServer 的
  `from: 固定邮箱 / to: 请求里的 email` 同一思路；区别是本项目补齐了它没有的
  重发冷却、试错上限、一次性作废、哈希存储与发信配额。
- **兼容旧键**：`SMTP_USER` / `SMTP_AUTH_CODE`（以及可选的 `SMTP_HOST` / `SMTP_PORT` / `SMTP_SSL`）
  继续可用；QQ 键优先级更高，两者同时存在且不一致时以 QQ 键为准并打一条警告（不打印值）。
- **调试模式**（`MAIL_DEBUG=1` 或发件箱未配置）：不发信，把邮件写进
  `tests/test_out/_mail_outbox.log`，接口回传 `mode=debug`。
- 真实发信失败不抛致命错，回传 `{ok: False, error}`；error 已归一成用户能懂的中文，
  且保证不回显授权码。
"""
from __future__ import annotations

import smtplib
import ssl
import time
from email.header import Header
from email.mime.text import MIMEText
from pathlib import Path

from utils import settings

PROJECT_ROOT = Path(__file__).resolve().parent.parent
OUTBOX = PROJECT_ROOT / "tests" / "test_out" / "_mail_outbox.log"

QQ_HOST = "smtp.qq.com"
QQ_PORT = 465

# 发件箱：优先 QQ 键（.env 里的项目级发件箱），旧键兜底
USER_KEYS = ("QQMAIL", "SMTP_USER")
CODE_KEYS = ("QQMSTP", "SMTP_AUTH_CODE")


# --------------------------------------------------------------------------- #
# 配置解析
# --------------------------------------------------------------------------- #
def _pick(keys: tuple) -> tuple:
    """按优先级取第一个非空值，返回 (值, 命中的键名)。"""
    for k in keys:
        v = settings.get(k)
        v = "" if v is None else str(v).strip()
        if v:
            return v, k
    return "", ""


def _cfg() -> dict:
    """解析当前发信配置（含来源键名，便于诊断）。"""
    user, user_key = _pick(USER_KEYS)
    code, code_key = _pick(CODE_KEYS)
    # 两个来源都配了且不一致 → 以 QQ 键为准，留一条警告（只说键名，不打印值）
    old_user = str(settings.get("SMTP_USER") or "").strip()
    old_code = str(settings.get("SMTP_AUTH_CODE") or "").strip()
    if user_key == "QQMAIL" and old_user and old_user != user:
        print("[mailer] 警告：QQMAIL 与 SMTP_USER 同时存在且不一致，以 QQMAIL 为准")
    if code_key == "QQMSTP" and old_code and old_code != code:
        print("[mailer] 警告：QQMSTP 与 SMTP_AUTH_CODE 同时存在且不一致，以 QQMSTP 为准")
    host = str(settings.get("SMTP_HOST") or "").strip() or QQ_HOST
    port = settings.get_int("SMTP_PORT", QQ_PORT) or QQ_PORT
    use_ssl = settings.get_bool("SMTP_SSL", port == 465)
    from_name = str(settings.get("SMTP_FROM_NAME") or "智选情报官").strip()
    return {"user": user, "user_key": user_key, "code": code, "code_key": code_key,
            "host": host, "port": port, "ssl": use_ssl, "from_name": from_name}


def sender() -> str:
    """当前项目发件箱地址（内部用；不要往接口回传）。"""
    return _cfg()["user"]


def is_configured() -> bool:
    """发件箱与授权码都齐了才算配好（主机/端口有 QQ 默认值）。"""
    c = _cfg()
    return bool(c["user"]) and bool(c["code"])


def _debug_mode() -> bool:
    return settings.get_bool("MAIL_DEBUG") or not is_configured()


def config_hint() -> str:
    """给界面用的一句话状态（不含任何敏感值）。"""
    if is_configured():
        return "已配置（发件箱来自 .env）"
    # ★ 必须用 get_bool：字符串 "0" 在 Python 里是真值，写成 settings.get() 会把
    #   「已配好但调试开关是 0」误判成调试模式（单测抓过一次）。
    if settings.get_bool("MAIL_DEBUG"):
        return "调试模式（不发信）"
    return "未配置：请在 .env 填 QQMAIL 与 QQMSTP"


# --------------------------------------------------------------------------- #
# 发信配额（24 小时滑窗统计发信次数）
# --------------------------------------------------------------------------- #
QUOTA_WINDOW = 24 * 3600


def quota_usage(account_id: int | None = None) -> dict:
    """统计 24 小时内的发信次数（账号级 / 全局）。表不存在时按 0 处理。"""
    since = int(time.time()) - QUOTA_WINDOW
    out = {"account": 0, "global": 0}
    try:
        from tools.schema_personal import get_personal_conn
        conn = get_personal_conn()
        try:
            if account_id is not None:
                r = conn.execute("SELECT COUNT(*) AS n FROM verify_codes WHERE account_id=? AND created_at>=?",
                                 (int(account_id), since)).fetchone()
                out["account"] = int(r["n"] if r else 0)
            r2 = conn.execute("SELECT COUNT(*) AS n FROM verify_codes WHERE created_at>=?", (since,)).fetchone()
            out["global"] = int(r2["n"] if r2 else 0)
        finally:
            conn.close()
    except Exception as e:                                            # noqa: BLE001
        print("[mailer] 配额统计失败（按 0 处理）: %s" % e)
    return out


def quota_problem(account_id: int) -> str:
    """超配额时返回给用户看的原因；没超返回空串。0 = 不限。"""
    per = settings.get_int("MAIL_QUOTA_PER_ACCOUNT_DAY", 10)
    glob = settings.get_int("MAIL_QUOTA_GLOBAL_DAY", 50)
    if per <= 0 and glob <= 0:
        return ""
    u = quota_usage(account_id)
    if per > 0 and u["account"] >= per:
        return "本账号 24 小时内已发送 %d 封验证码（上限 %d 封），请稍后再试。" % (u["account"], per)
    if glob > 0 and u["global"] >= glob:
        return "本机 24 小时内累计发送 %d 封验证码（上限 %d 封），请稍后再试。" % (u["global"], glob)
    return ""


def _friendly(e: Exception, code: str) -> str:
    """把 smtplib / 网络异常翻成用户能懂的中文，并确保不回显授权码。"""
    name = type(e).__name__
    detail = str(e)
    low = detail.lower()
    if isinstance(e, smtplib.SMTPAuthenticationError):
        msg = ("发件箱登录被拒：授权码不正确，或服务商拒绝这次登录。请确认 .env 里的 QQMSTP 是该邮箱"
               "「开启 SMTP 服务」后生成的授权码（不是邮箱登录密码），必要时重新生成一次。")
    elif isinstance(e, smtplib.SMTPRecipientsRefused):
        msg = "收件邮箱被服务商拒绝（地址可能不存在或已停用）。"
    elif isinstance(e, smtplib.SMTPSenderRefused):
        msg = "发件箱被拒绝：请确认该邮箱已开启 SMTP 服务。"
    elif isinstance(e, smtplib.SMTPDataError):
        msg = "邮件被服务商拒收（常见原因：当日发信量超限、内容被判为垃圾邮件）。稍后再试或换发件箱。"
    elif isinstance(e, smtplib.SMTPServerDisconnected):
        msg = "与邮件服务器的连接中断（网络不稳定或服务商限流）。"
    elif isinstance(e, ssl.SSLError):
        msg = "TLS/SSL 握手失败：端口与 SSL 设置不匹配（QQ 邮箱为 465 + SSL）。"
    elif isinstance(e, TimeoutError) or "timed out" in low:
        msg = "连接邮件服务器超时（网络不通或被代理拦截）。"
    elif isinstance(e, OSError) and any(x in detail for x in ("10060", "10061", "refused", "unreachable")):
        msg = "无法连接邮件服务器（端口不通，可能被网络或代理拦截）。"
    else:
        msg = "发送失败（%s）。" % name
    # 兜底：任何情况下都不让授权码出现在给用户看的文案里
    if code:
        msg = msg.replace(code, "***")
    return msg


def _write_outbox(to: str, subject: str, body: str) -> str:
    OUTBOX.parent.mkdir(parents=True, exist_ok=True)
    rec = ("\n===== %s =====\nto: %s\nsubject: %s\n\n%s\n"
           % (time.strftime("%Y-%m-%d %H:%M:%S"), to, subject, body))
    with open(OUTBOX, "a", encoding="utf-8", newline="\n") as f:
        f.write(rec)
    return str(OUTBOX)


def send(to: str, subject: str, body: str) -> dict:
    """发一封信。返回 {ok, mode, error?, outbox?}（mode = smtp | debug）。"""
    to = (to or "").strip()
    if not to or "@" not in to:
        return {"ok": False, "mode": "none", "error": "收件邮箱不合法"}
    if _debug_mode():
        p = _write_outbox(to, subject, body)
        print("[mailer] 调试模式：邮件未真实发送，已写入 %s" % p)
        return {"ok": True, "mode": "debug", "outbox": p}

    c = _cfg()
    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = Header(subject, "utf-8")
    msg["From"] = "%s <%s>" % (Header(c["from_name"], "utf-8").encode(), c["user"])
    msg["To"] = to
    try:
        if c["ssl"]:
            ctx = ssl.create_default_context()
            with smtplib.SMTP_SSL(c["host"], c["port"], timeout=20, context=ctx) as s:
                s.login(c["user"], c["code"])
                s.sendmail(c["user"], [to], msg.as_string())
        else:
            with smtplib.SMTP(c["host"], c["port"], timeout=20) as s:
                s.ehlo()
                try:
                    s.starttls(context=ssl.create_default_context())
                    s.ehlo()
                except smtplib.SMTPException:
                    pass                       # 服务端不支持 STARTTLS 就明文（本机/内网邮箱常见）
                s.login(c["user"], c["code"])
                s.sendmail(c["user"], [to], msg.as_string())
        return {"ok": True, "mode": "smtp"}
    except Exception as e:                                            # noqa: BLE001
        return {"ok": False, "mode": "smtp", "error": _friendly(e, c["code"])}


def send_code(to: str, code: str, purpose: str = "bind") -> dict:
    """发验证码邮件（统一文案）。purpose: bind=绑定邮箱 / delete=注销账号。"""
    what = {"bind": "绑定邮箱", "unbind": "解绑邮箱", "delete": "注销账号"}.get(purpose, "绑定邮箱")
    subject = "【智选情报官】%s 验证码" % what
    body = (
        "你的验证码是：%s\n\n"
        "用于：%s\n"
        "有效期：5 分钟（超时请重新获取）。\n\n"
        "如果不是你本人操作，请忽略本邮件，并考虑尽快修改密码。\n"
        "—— 智选情报官（本地部署）\n"
    ) % (code, what)
    return send(to, subject, body)
