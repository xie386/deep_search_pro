# -*- coding: utf-8 -*-
"""账号安全（v4.0 大点 04 · 议题二）：绑定邮箱 + 一键注销账号。

用户口径（2026-10-13）：
  「新增一个设置模块，一键账号注销……在设置处新加账号绑定 QQ（邮箱）功能，
    如果注销账号，需要账号密码和 QQ 邮箱传的验证码。」

本模块实现那条链路：
  ① 绑定邮箱：发码到目标邮箱 → 回填验证码 → 落 `accounts.email`；
  ② 换绑 / 解绑：需**当前密码** + 发到**旧邮箱**的码（先解绑再绑定，路径单一、不易绕开）；
  ③ 注销账号：需**账号密码** + **绑定邮箱收到的验证码** + 输入确认词「注销账号」；
     执行前先把该账号的全部数据**快照到 `docs/v4.0/backups/account_delete_<账号>_<时间戳>/`**（本机惯例：
     改用户数据前先备份），再走 `purge_account`（库行 + 文件侧）——这正是"整账号清理"的复用点。

安全口径
--------
- 验证码：6 位数字，**只存哈希**；有效期 5 分钟；同用途 60 秒内不可重发；最多试 5 次即作废。
- 邮箱唯一：一个邮箱只能绑一个账号（防串号）。
- 密码校验复用 `api.account._hash_pwd`（同一套盐与算法，不另起炉灶）。
- 所有敏感动作落 `data/audit/account.jsonl`（**不记验证码与密码**）。
- 真实账号只读纪律：本模块的自动化验证一律用**临时账号**（用户既有裁定）。
"""
from __future__ import annotations

import hashlib
import re
import secrets
import shutil
import time
from pathlib import Path

from fastapi import HTTPException, status
from pydantic import BaseModel

from api.account import _hash_pwd, get_session
from tools.schema_personal import get_personal_conn, purge_account
from utils import audit, mailer
from utils import paths as _paths

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CODE_TTL = 300                 # 验证码有效期（秒）
RESEND_INTERVAL = 60           # 同用途重发间隔（秒）
MAX_ATTEMPTS = 5               # 单个验证码最多试错次数
CONFIRM_WORD = "注销账号"       # 注销时要求用户原样输入的确认词
_PEPPER = "zhixuan_verify_code_v1"

EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$")

PURPOSES = ("bind", "unbind", "delete")
PURPOSE_LABEL = {"bind": "绑定邮箱", "unbind": "解绑/换绑邮箱", "delete": "注销账号"}


# --------------------------------------------------------------------------- #
# 请求模型
# --------------------------------------------------------------------------- #
class EmailReq(BaseModel):
    email: str


class ConfirmReq(BaseModel):
    email: str
    code: str


class PasswordReq(BaseModel):
    password: str


class UnbindConfirmReq(BaseModel):
    password: str
    code: str


class DeleteConfirmReq(BaseModel):
    password: str
    email: str
    code: str
    confirm_text: str


# --------------------------------------------------------------------------- #
# 基础
# --------------------------------------------------------------------------- #
def _acct(token: str) -> dict:
    """校验登录 → 账号行（含 email）。"""
    sess = get_session(token)
    conn = get_personal_conn()
    try:
        r = conn.execute("SELECT id, username, role, email, email_bound_at, password_hash "
                         "FROM accounts WHERE id=?", (sess["account_id"],)).fetchone()
    finally:
        conn.close()
    if not r:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "账号不存在或登录已失效")
    return dict(r)


def _check_password(acct: dict, password: str) -> None:
    if not password:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "请输入账号密码")
    if acct.get("password_hash") != _hash_pwd(password):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "密码不正确")


def mask_email(email: str | None) -> str:
    """`abcd@qq.com` → `ab**@qq.com`（设置页展示用）。"""
    e = (email or "").strip()
    if "@" not in e:
        return ""
    name, dom = e.split("@", 1)
    keep = name[:2] if len(name) > 2 else name[:1]
    return "%s%s@%s" % (keep, "*" * max(0, len(name) - len(keep)), dom)


def _code_hash(account_id: int, purpose: str, code: str) -> str:
    return hashlib.sha256(("%s:%s:%s:%s" % (_PEPPER, account_id, purpose, code)).encode("utf-8")).hexdigest()


def _new_code() -> str:
    return "%06d" % secrets.randbelow(1000000)


def _raw_conn():
    return get_personal_conn()


def _issue_code(account_id: int, email: str, purpose: str) -> str:
    """生成并落库一条验证码；顺便做重发频率限制。返回明文码（只用于发信）。"""
    assert purpose in PURPOSES, "非法用途：%s" % purpose
    now = int(time.time())
    conn = _raw_conn()
    try:
        last = conn.execute(
            "SELECT created_at FROM verify_codes WHERE account_id=? AND purpose=? "
            "ORDER BY id DESC LIMIT 1", (account_id, purpose)).fetchone()
        if last and now - int(last["created_at"]) < RESEND_INTERVAL:
            wait = RESEND_INTERVAL - (now - int(last["created_at"]))
            raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS,
                                "发送太频繁，请 %d 秒后再试" % wait)
        # ★ 发信配额（24 小时滑窗）：撞上限就让用户看明白原因，而不是等 QQ 邮箱把信退回来
        over = mailer.quota_problem(account_id)
        if over:
            raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, over)
        conn.execute("UPDATE verify_codes SET used=1 WHERE account_id=? AND purpose=? AND used=0",
                     (account_id, purpose))
        code = _new_code()
        conn.execute(
            "INSERT INTO verify_codes(account_id, email, code_hash, purpose, created_at, expires_at, "
            "used, attempts) VALUES (?,?,?,?,?,?,0,0)",
            (account_id, email, _code_hash(account_id, purpose, code), purpose, now, now + CODE_TTL))
        conn.commit()
        return code
    finally:
        conn.close()


def _verify_code(account_id: int, email: str, purpose: str, code: str) -> None:
    """校验验证码：存在、未用、未过期、未超试错、匹配。通过后立即标记已用。"""
    code = (code or "").strip()
    if not code:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "请输入验证码")
    now = int(time.time())
    conn = _raw_conn()
    try:
        r = conn.execute(
            "SELECT id, email, code_hash, expires_at, attempts FROM verify_codes "
            "WHERE account_id=? AND purpose=? AND used=0 ORDER BY id DESC LIMIT 1",
            (account_id, purpose)).fetchone()
        if not r:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "请先获取验证码")
        if now > int(r["expires_at"]):
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "验证码已过期，请重新获取")
        if int(r["attempts"]) >= MAX_ATTEMPTS:
            conn.execute("UPDATE verify_codes SET used=1 WHERE id=?", (r["id"],))
            conn.commit()
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "验证码试错次数过多，请重新获取")
        if (r["email"] or "").strip().lower() != (email or "").strip().lower() \
                or r["code_hash"] != _code_hash(account_id, purpose, code):
            conn.execute("UPDATE verify_codes SET attempts=attempts+1 WHERE id=?", (r["id"],))
            conn.commit()
            left = MAX_ATTEMPTS - int(r["attempts"]) - 1
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "验证码不正确（还可试 %d 次）" % max(0, left))
        conn.execute("UPDATE verify_codes SET used=1 WHERE id=?", (r["id"],))
        conn.commit()
    finally:
        conn.close()


def _email_taken(email: str, exclude_aid: int) -> bool:
    conn = _raw_conn()
    try:
        r = conn.execute("SELECT id FROM accounts WHERE lower(email)=lower(?)", (email.strip(),)).fetchone()
    finally:
        conn.close()
    return bool(r) and int(r["id"]) != int(exclude_aid)


# --------------------------------------------------------------------------- #
# 对外：状态 / 绑定 / 解绑 / 注销
# --------------------------------------------------------------------------- #
def status_of(token: str) -> dict:
    a = _acct(token)
    email = (a.get("email") or "").strip()
    return {
        "username": a["username"],
        "bound": bool(email),
        "email_masked": mask_email(email),
        "email_bound_at": a.get("email_bound_at"),
        "can_delete": bool(email),           # 注销要求已绑定邮箱（用户口径）
        "can_send_mail": mailer.is_configured(),
        "mail_debug": mailer._debug_mode(),
    }


def send_bind_code(token: str, req: EmailReq) -> dict:
    a = _acct(token)
    email = (req.email or "").strip()
    if not EMAIL_RE.match(email):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "邮箱格式不正确")
    if _email_taken(email, a["id"]):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "这个邮箱已经绑定到其他账号了")
    code = _issue_code(a["id"], email, "bind")
    r = mailer.send_code(email, code, purpose="bind")
    audit.write_event("account", "bind_code_sent", by=a["username"], to=mask_email(email),
                      mode=r.get("mode"), ok=r.get("ok"), error=r.get("error"))
    if not r.get("ok"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "验证码发送失败：%s" % r.get("error"))
    return {"ok": True, "email_masked": mask_email(email), "mode": r.get("mode"),
            "outbox": r.get("outbox", ""), "ttl_s": CODE_TTL, "resend_after_s": RESEND_INTERVAL}


def confirm_bind(token: str, req: ConfirmReq) -> dict:
    a = _acct(token)
    email = (req.email or "").strip()
    if _email_taken(email, a["id"]):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "这个邮箱已经绑定到其他账号了")
    _verify_code(a["id"], email, "bind", req.code)
    conn = _raw_conn()
    try:
        conn.execute("UPDATE accounts SET email=?, email_bound_at=? WHERE id=?",
                     (email, int(time.time()), a["id"]))
        conn.commit()
    finally:
        conn.close()
    audit.write_event("account", "email_bound", by=a["username"], to=mask_email(email))
    return {"ok": True, "email_masked": mask_email(email)}


def send_unbind_code(token: str, req: PasswordReq) -> dict:
    a = _acct(token)
    _check_password(a, req.password)
    email = (a.get("email") or "").strip()
    if not email:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "当前账号还没有绑定邮箱")
    code = _issue_code(a["id"], email, "unbind")
    r = mailer.send_code(email, code, purpose="bind")     # 文案用"绑定邮箱"更中性
    audit.write_event("account", "unbind_code_sent", by=a["username"], to=mask_email(email),
                      mode=r.get("mode"), ok=r.get("ok"))
    if not r.get("ok"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "验证码发送失败：%s" % r.get("error"))
    return {"ok": True, "email_masked": mask_email(email), "mode": r.get("mode"),
            "outbox": r.get("outbox", "")}


def confirm_unbind(token: str, req: UnbindConfirmReq) -> dict:
    a = _acct(token)
    _check_password(a, req.password)
    email = (a.get("email") or "").strip()
    if not email:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "当前账号还没有绑定邮箱")
    _verify_code(a["id"], email, "unbind", req.code)
    conn = _raw_conn()
    try:
        conn.execute("UPDATE accounts SET email=NULL, email_bound_at=NULL WHERE id=?", (a["id"],))
        conn.commit()
    finally:
        conn.close()
    audit.write_event("account", "email_unbound", by=a["username"], was=mask_email(email))
    return {"ok": True}


def delete_request(token: str, req: PasswordReq) -> dict:
    """注销第一步：验密码 + 往绑定邮箱发码。"""
    a = _acct(token)
    _check_password(a, req.password)
    email = (a.get("email") or "").strip()
    if not email:
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            "注销需要邮箱验证码：请先在「设置 → 账号安全」绑定邮箱")
    code = _issue_code(a["id"], email, "delete")
    r = mailer.send_code(email, code, purpose="delete")
    audit.write_event("account", "delete_code_sent", by=a["username"], to=mask_email(email),
                      mode=r.get("mode"), ok=r.get("ok"))
    if not r.get("ok"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "验证码发送失败：%s" % r.get("error"))
    return {"ok": True, "email_masked": mask_email(email), "mode": r.get("mode"),
            "outbox": r.get("outbox", ""), "ttl_s": CODE_TTL,
            "confirm_word": CONFIRM_WORD, "note": "此项不可撤销；执行前会自动把该账号全部数据快照到备份目录。"}


def _snapshot_account(a: dict) -> str:
    """注销前把该账号的全部数据拷到 `docs/v4.0/backups/account_delete_<账号>_<时间戳>/`。"""
    stamp = time.strftime("%Y%m%d_%H%M%S")
    dest = PROJECT_ROOT / "docs" / "v4.0" / "backups" / ("account_delete_%s_%s" % (a["username"], stamp))
    dest.mkdir(parents=True, exist_ok=True)
    dirs = _paths.account_dirs_of(a["username"])
    copied = []
    for d in dirs:
        d = Path(d)
        if d.is_dir():
            try:
                shutil.copytree(d, dest / d.name)
                copied.append(d.name)
            except Exception as e:                                    # noqa: BLE001
                print("[account_sec] 备份目录失败 %s: %s" % (d, e))
    f = _paths.probe_set_path(a["username"])
    if f.is_file():
        try:
            shutil.copy2(f, dest / f.name)
            copied.append(f.name)
        except Exception:                                             # noqa: BLE001
            pass
    return str(dest), copied


def delete_confirm(token: str, req: DeleteConfirmReq) -> dict:
    """注销第二步：密码 + 绑定邮箱的验证码 + 确认词 → 备份 → 整体清除（不可撤销）。"""
    a = _acct(token)
    _check_password(a, req.password)
    email = (a.get("email") or "").strip()
    if not email:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "当前账号没有绑定邮箱，无法用验证码注销")
    if (req.email or "").strip().lower() != email.lower():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "输入的邮箱与绑定的不一致")
    if (req.confirm_text or "").strip() != CONFIRM_WORD:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "请原样输入确认词「%s」" % CONFIRM_WORD)
    _verify_code(a["id"], email, "delete", req.code)

    backup_dir, copied = _snapshot_account(a)          # ① 先备份
    removed = purge_account(a["id"])                   # ② 再清（库行 + 文件侧 + sessions）
    audit.write_event("account", "account_deleted", by=a["username"], role=a.get("role"),
                      backup=backup_dir, db_rows={k: v for k, v in removed.items() if k != "_files"},
                      dirs=",".join(removed.get("_files") or []))
    return {
        "ok": True,
        "deleted": a["username"],
        "backup": backup_dir,
        "backup_dirs": copied,
        "removed": {k: v for k, v in removed.items() if not k.startswith("_")},
        "removed_files": removed.get("_files") or [],
        "note": "账号与全部账号级数据已删除；快照留在上面的备份目录（需要时人工取回）。",
    }
