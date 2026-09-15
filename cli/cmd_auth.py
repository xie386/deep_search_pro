# -*- coding: utf-8 -*-
"""dspro login / logout / whoami —— 登录鉴权。

    dspro login <用户名> <密码>          # 校验并写入登录态（data/dspro_session.json）
    dspro login <用户名> <密码> --register [--role company]
    dspro login -                        # 密码走隐藏输入（不回显、不进 argv/历史）
    dspro logout
    dspro whoami

为什么同时支持 `-` 隐藏输入：`dspro login u p` 会把密码留在 shell 历史与进程列表里，
本地单机够用，但提供更稳妥的写法更有必要。
"""

import getpass
import sys

from cli import ui
from cli.errors import CliError
from cli import session as ss


def login(username: str | None, password: str | None, register: bool = False,
          role: str = "personal", display_name: str | None = None) -> int:
    if not username or not username.strip():
        raise CliError("用法：dspro login <用户名> <密码>（密码想隐藏输入就写 `-`）")
    username = username.strip()

    if password in (None, "-"):
        password = getpass.getpass("密码（输入不回显）：")
    if not password:
        raise CliError("密码不能为空")

    if register:
        if role not in ("company", "personal"):
            raise CliError("--role 只能是 company 或 personal")
        acc = ss.create_account(username, password, role=role, display_name=display_name)
        ui.ok(f"账号已创建：{ui.c(acc['username'], 'bold')}（{acc['role']}）")
    else:
        acc = ss.verify_password(username, password)

    data = ss.start_session(acc)
    ui.title("登录成功", f"登录态写入 {ss.SESSION_FILE.relative_to(ss.PROJECT_ROOT)}")
    ui.kv([
        ("账号", ui.c(acc["username"], "bold")),
        ("角色", _role_label(acc["role"])),
        ("显示名", data.get("display_name") or acc["username"]),
        ("account_id", acc["id"]),
        ("登录时间", data.get("login_at", "-")),
    ])
    ui.hint("接下来：dspro list ｜ dspro digest ｜ dspro chat")
    return 0


def _role_label(role: str) -> str:
    return {"company": "公司账号（竞品情报）", "personal": "个人账号（选购决策）"}.get(role, role)


def logout() -> int:
    ss.require()
    who = ss.current().get("username", "")
    ss.clear()
    ui.ok(f"已退出登录（{who}）。登录态文件已删除。")
    return 0


def whoami() -> int:
    data = ss.require()
    role = data.get("role", "")
    subs = reports = 0
    conn = ss.db()
    try:
        subs = conn.execute("SELECT COUNT(*) FROM digest_subs WHERE owner_id=?",
                            (data["account_id"],)).fetchone()[0]
        reports = conn.execute("SELECT COUNT(*) FROM digest_reports WHERE owner_id=?",
                               (data["account_id"],)).fetchone()[0]
    finally:
        conn.close()

    ui.title("当前登录", "")
    ui.kv([
        ("账号", ui.c(data["username"], "bold")),
        ("角色", _role_label(role)),
        ("显示名", data.get("display_name") or data["username"]),
        ("account_id", data["account_id"]),
        ("登录时间", data.get("login_at", "-")),
        ("关注领域（订阅）", f"{subs} 个"),
        ("历史周报", f"{reports} 份"),
        ("默认城市", ss.env_default_city()),
    ])
    return 0
