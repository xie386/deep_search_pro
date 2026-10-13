# -*- coding: utf-8 -*-
"""设置页后端（v4.0 大点 04 · 议题三）。

三个动作：
  · `GET  /api/settings`            读全部设置（分组；密钥只回掩码）
  · `POST /api/settings`            保存（开关/参数进库、密钥写 .env）
  · `POST /api/settings/test_mail`  用当前 SMTP 配置发一封测试邮件（议题二的通道自检）

口径见 `utils/settings.py` 的文件头；本模块只做「校验登录 → 转调 → 审计 → 报错格式化」。
"""
from __future__ import annotations

from typing import Dict

from fastapi import HTTPException, status
from pydantic import BaseModel

from api.account import get_session
from utils import audit, mailer, settings


class SettingsSaveReq(BaseModel):
    values: Dict[str, object] = {}


class TestMailReq(BaseModel):
    # 留空 = 发到项目发件箱自己（.env 里的 QQMAIL）——发件箱地址不往界面暴露
    to: str = ""


def _who(token: str) -> str:
    """校验登录并返回用户名（无 token / 失效 → 401）。"""
    return get_session(token)["username"]


def get_all(token: str) -> dict:
    _who(token)
    return settings.all_values()


def save(token: str, req: SettingsSaveReq) -> dict:
    who = _who(token)
    vals = {k: v for k, v in (req.values or {}).items()}
    if not vals:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "没有要保存的设置项")
    try:
        res = settings.save(vals, actor=who)
    except ValueError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e))
    except Exception as e:                                            # noqa: BLE001
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "保存失败：%s" % e)
    # ★ 审计只记「改了哪些键」，绝不记值（密钥可能在其中）
    audit.write_event("account", "settings_save", by=who, keys=",".join(res["changed"]),
                      db_keys=",".join(k for k in res["saved"] if k not in res["env_keys"]),
                      env_keys=",".join(res["env_keys"]), env_backup=res.get("env_backup") or "")
    return {"ok": True, **res}


def test_mail(token: str, req: TestMailReq) -> dict:
    who = _who(token)
    to = (req.to or "").strip()
    if not to:
        to = mailer.sender()                       # 留空 → 发给自己（自检用）
    if "@" not in to:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "收件邮箱不合法（留空则发到项目发件箱自己）")
    r = mailer.send(to, "【智选情报官】测试邮件",
                    "这是一封来自「智选情报官 · 设置页」的测试邮件。\n\n"
                    "收到它说明 SMTP 配置可用，绑定邮箱 / 注销账号的验证码可以正常发出。\n")
    audit.write_event("account", "test_mail", by=who, to=to, mode=r.get("mode"), ok=r.get("ok"),
                      error=r.get("error"))
    if not r.get("ok"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "发送失败：%s" % r.get("error"))
    if r.get("mode") == "debug":
        why = ("（.env 里还没有 QQMAIL / QQMSTP）" if not mailer.is_configured()
               else "（当前打开了「邮件调试模式」）")
        hint = "调试模式：邮件没有真发出去，内容写进了 %s%s" % (r.get("outbox"), why)
    else:
        hint = "已通过 SMTP 发出"
    return {"ok": True, "mode": r.get("mode"), "outbox": r.get("outbox", ""),
            "hint": hint, "mail_state": mailer.config_hint()}
