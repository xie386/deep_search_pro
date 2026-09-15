# -*- coding: utf-8 -*-
"""dspro · 登录态与账号访问。

设计（用户拍板）：
  - CLI **独立运行**，不经 HTTP：直接读写 data/personal.db 与 agent/digest 引擎；
  - 登录态落在**项目内** data/dspro_session.json（已在 .gitignore 内），随项目走；
  - 校验复用 api/account.py 的同一套实现（同一份盐 + 同一张 accounts 表），
    不复制一份哈希逻辑，避免两边口径漂移；
  - 文件里**不存密码**，只存「账号密码哈希的指纹」——密码被改动后指纹不匹配，
      旧会话自动失效，强制重新登录。
"""

import hashlib
import json
import os
import time
from pathlib import Path

from cli.errors import CliError

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SESSION_FILE = PROJECT_ROOT / "data" / "dspro_session.json"

DEFAULTS = {
    "default_city": "成都",     # 与 .env 的 WEATHER_CITY 保持一致
    "last_thread_id": "",       # CLI 会话上次使用的 thread_id（dspro chat 续聊）
}


# ---------------------------------------------------------------------------
# 会话文件读写
# ---------------------------------------------------------------------------
def load() -> dict:
    """读会话文件（不存在/损坏 → 返回空 dict，不抛）。"""
    try:
        if SESSION_FILE.is_file():
            data = json.loads(SESSION_FILE.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
    except Exception:
        pass
    return {}


def save(data: dict) -> None:
    SESSION_FILE.parent.mkdir(parents=True, exist_ok=True)
    SESSION_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def set_value(key: str, value) -> None:
    """更新会话文件里的单个键（不校验登录，供内部记录 thread_id / 默认城市等）。"""
    data = load()
    data[key] = value
    save(data)


def get_value(key: str, default=None):
    return load().get(key, DEFAULTS.get(key, default))


def clear() -> bool:
    if SESSION_FILE.is_file():
        SESSION_FILE.unlink()
        return True
    return False


# ---------------------------------------------------------------------------
# 账号访问（复用 api/account.py，避免哈希口径分叉）
# ---------------------------------------------------------------------------
def _accounts():
    from tools.schema_personal import ensure_tables
    ensure_tables()
    from api.account import _get_account        # noqa: PLC2701 —— 有意复用（同一套校验）
    return _get_account


def account_by_username(username: str) -> dict | None:
    return _accounts()((username or "").strip())


def account_by_id(account_id: int) -> dict | None:
    from tools.schema_personal import ensure_tables, get_personal_conn
    ensure_tables()
    conn = get_personal_conn()
    try:
        row = conn.execute(
            "SELECT id, username, password_hash, role, display_name FROM accounts WHERE id=?",
            (account_id,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def fingerprint(password_hash: str) -> str:
    """账号密码哈希的指纹（只用于「密码有没有被改过」的本地判断）。"""
    return hashlib.sha256(("dspro_fp:" + (password_hash or "")).encode("utf-8")).hexdigest()[:32]


def verify_password(username: str, password: str) -> dict:
    """用户名+密码校验，返回账号 dict。复用 api.account.login 的判定逻辑。

    登录语义（2026-09-16 起）：账号不存在 → 404（CLI 提示改用 --register）；密码错误 → 401。
    """
    from api.account import LoginReq, login
    from fastapi import HTTPException
    try:
        resp = login(LoginReq(username=username, password=password))
    except HTTPException as e:
        if getattr(e, "status_code", None) == 404:
            raise CliError("账号「%s」不存在；新用户请用：dspro login %s - --register" % (username, username))
        raise CliError(str(e.detail))
    acc = account_by_username(resp.account.username)
    if not acc:
        raise CliError("账号读取失败，请重试")
    return acc


def create_account(username: str, password: str, role: str = "personal", display_name: str | None = None) -> dict:
    """dspro login --register 用：注册新账号（角色 company/personal）。"""
    from api.account import RegisterReq, register
    from fastapi import HTTPException
    try:
        register(RegisterReq(username=username, password=password, role=role,
                             display_name=display_name or username))
    except HTTPException as e:
        raise CliError(str(e.detail))
    acc = account_by_username(username)
    if not acc:
        raise CliError("注册后读取账号失败，请重试")
    return acc


def start_session(acc: dict) -> dict:
    """写入登录态。"""
    data = load()
    data.update({
        "username": acc["username"],
        "account_id": acc["id"],
        "role": acc["role"],
        "display_name": acc.get("display_name") or acc["username"],
        "pwd_fp": fingerprint(acc.get("password_hash") or ""),
        "login_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    })
    for k, v in DEFAULTS.items():
        data.setdefault(k, v)
    save(data)
    return data


def current() -> dict | None:
    """当前登录态（未校验失效），未登录返回 None。"""
    data = load()
    return data if data.get("username") and data.get("account_id") else None


def require() -> dict:
    """取当前登录态并校验：未登录 / 密码已变更 → CliError（带明确指引）。"""
    data = current()
    if not data:
        raise CliError("尚未登录。请先执行：dspro login <用户名> <密码>")
    acc = account_by_id(int(data["account_id"]))
    if not acc:
        raise CliError("登录的账号已不存在（可能已被清理）。请重新登录：dspro login <用户名> <密码>")
    if fingerprint(acc.get("password_hash") or "") != data.get("pwd_fp"):
        raise CliError("登录已失效（该账号密码已变更）。请重新登录：dspro login <用户名> <密码>")
    if acc["username"] != data.get("username"):
        raise CliError("登录态与账号不匹配，请重新登录：dspro login <用户名> <密码>")
    return data


# ---------------------------------------------------------------------------
# 业务库连接
# ---------------------------------------------------------------------------
def db():
    """个人库连接（Row 工厂 + 外键），调用方负责 close。"""
    from tools.schema_personal import ensure_tables, get_personal_conn
    ensure_tables()
    return get_personal_conn()


def account_id() -> int:
    return int(require()["account_id"])


def env_default_city() -> str:
    """默认城市：优先 .env 的 WEATHER_CITY，其次会话文件，最后成都。"""
    from dotenv import load_dotenv
    load_dotenv(PROJECT_ROOT / ".env")
    return (os.getenv("WEATHER_CITY") or get_value("default_city") or "成都").strip()
