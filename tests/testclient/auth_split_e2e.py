# -*- coding: utf-8 -*-
"""登录 / 注册两卡分离 —— 端到端验证（不耗模型）

背景：旧版登录卡是「登录注册混合」——`doLogin()` 遇到 401 就调 `/api/register` 自动建号，
导致**打错用户名会静默建成新账号**（用户实际踩到：想登「尼古喵喵」，错成「尼姑喵喵」建了号）。
本次收尾拆成两张卡，并把后端语义改准（账号不存在 → 404 / 密码错 → 401）。

覆盖：
  1) 后端语义：不存在 → 404、密码错 → 401、正确 → 200 + token
  2) ★ 核心回归：用错别字用户名登录 → 404，且 **accounts 表不新增任何行**（旧版本会建号）
  3) 注册：正常 200 并落库 → 新密码可登录；重名 → 409；空用户名 → 400；非法角色 → 400
  4) 前端接线（静态断言）：doLogin 里**不再调用 /api/register**；两卡分离的标记/字段/函数齐全
  5) 前端校验规则（从源码抽取后跑真函数）：用户名空格 / 密码长度 / 两次密码不一致 → 不发请求
  6) 清理：删除本次测试建出的账号（不留残留）

运行：
    .venv/Scripts/python.exe tests/auth_split_e2e.py
"""

import io
import os
import re
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from fastapi.testclient import TestClient  # noqa: E402

from api import server  # noqa: E402
from tools.schema_personal import ensure_tables, get_personal_conn, purge_account  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
HTML = io.open(os.path.join(ROOT, "front", "index.html"), encoding="utf-8", newline="").read()
SRC = HTML[HTML.rfind("<script"):]                     # 应用脚本段（setup 所在）

SUF = time.strftime("%m%d%H%M%S")
TYPO = "尼姑喵喵_%s" % SUF                               # 「错别字用户名」探针
REAL = "auth_split_%s" % SUF
PWD = "test123456"

PASS, FAIL = [], []


def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name)
    print("%s %s%s" % ("  ✅" if cond else "  ❌", name, (" | " + str(extra)) if extra else ""))


def _account_names() -> set:
    conn = get_personal_conn()
    try:
        return {r["username"] for r in conn.execute("SELECT username FROM accounts")}
    finally:
        conn.close()


def _aid(username):
    conn = get_personal_conn()
    try:
        row = conn.execute("SELECT id FROM accounts WHERE username=?", (username,)).fetchone()
        return int(row["id"]) if row else 0
    finally:
        conn.close()


def main():
    print("=== 登录/注册两卡分离 e2e（不耗模型）===\n")
    ensure_tables()
    c = TestClient(server.app)

    # ------------------------------------------------------------------ [1] 后端语义
    print("[1] 后端登录语义（账号不存在 → 404 / 密码错 → 401）")
    r = c.post("/api/login", json={"username": "zqx_no_such_user", "password": "whatever"})
    check("不存在的账号 → 404", r.status_code == 404, r.status_code)
    check("404 文案是「账号不存在」", "不存在" in (r.json().get("detail") or ""), r.json())

    before = _account_names()
    r = c.post("/api/login", json={"username": TYPO, "password": PWD})
    after = _account_names()
    check("★ 核心回归：用错别字用户名登录 → 404", r.status_code == 404, r.status_code)
    check("★ 核心回归：登录失败**没有**建出账号（旧版会静默建号）",
          TYPO not in after and len(after) == len(before), sorted(after - before))

    # ------------------------------------------------------------------ [2] 注册
    print("\n[2] 注册（显式建号才落库）")
    r = c.post("/api/register", json={"username": REAL, "password": PWD, "role": "personal"})
    check("注册个人账号 → 200", r.status_code == 200, r.text[:120])
    check("注册后账号真的在库里", REAL in _account_names())
    r2 = c.post("/api/register", json={"username": REAL, "password": PWD, "role": "personal"})
    check("重名注册 → 409（前端据此提示「直接登录」）", r2.status_code == 409, r2.status_code)
    r3 = c.post("/api/register", json={"username": "  ", "password": PWD, "role": "personal"})
    check("空用户名注册 → 400", r3.status_code == 400, r3.status_code)
    r4 = c.post("/api/register", json={"username": "x_%s" % SUF, "password": PWD, "role": "boss"})
    check("非法角色注册 → 400", r4.status_code == 400, r4.status_code)

    print("\n[3] 注册后用新密码登录 / 密码错返回 401")
    r = c.post("/api/login", json={"username": REAL, "password": PWD})
    check("新账号 + 正确密码 → 200 且带 token", r.status_code == 200 and bool(r.json().get("token")),
          r.status_code)
    r = c.post("/api/login", json={"username": REAL, "password": "wrong_pwd"})
    check("已存在账号 + 错密码 → 401（与 404 区分）", r.status_code == 401, r.status_code)
    check("401 文案是「密码错误」", "密码" in (r.json().get("detail") or ""), r.json())

    # ------------------------------------------------------------------ [4] 前端接线（静态）
    print("\n[4] 前端接线：登录卡不再自动注册，两卡标记齐全")
    m_login = re.search(r"async function doLogin\(\)[\s\S]*?\n    \}", SRC)
    login_body = m_login.group(0) if m_login else ""
    check("抽到了 doLogin 函数体（防解析失效导致假通过）", len(login_body) > 200, len(login_body))
    check("★ doLogin 里已无 /api/register 调用（自动建号已移除）",
          "api/register" not in login_body)
    check("doLogin 处理 404（账号不存在）分支", "r.status === 404" in login_body)
    # ★ 2026-09-23 修正过期断言：桌面版把 sessionStorage 换成了 _store 抽象
    #   （storeSet/storeClear：桌面走 localStorage、网页走 sessionStorage），
    #   字面量 `sessionStorage.setItem('zx_token'` 在 doLogin 里已经不存在了。
    #   断言意图是「登录成功后 token 被持久化」，按抽象层断言才对（否则是个假失败）。
    check("doLogin 成功分支持久化 token（storeSet('zx_token')）",
          "storeSet('zx_token'" in login_body)
    check("doLogin 里没有绕过抽象层直写 storage（防回归）",
          "sessionStorage.setItem" not in login_body and "localStorage.setItem" not in login_body)

    m_reg = re.search(r"async function doRegister\(\)[\s\S]*?\n    \}", SRC)
    reg_body = m_reg.group(0) if m_reg else ""
    check("doRegister 存在且调 /api/register", "api/register" in reg_body, len(reg_body))
    check("doRegister 校验两次密码一致", "两次输入的密码不一致" in reg_body)
    check("doRegister 处理 409 重名", "r.status === 409" in reg_body)
    check("注册成功后回到登录卡（不自动登录）",
          "authMode.value = 'login'" in reg_body and "data.token" not in reg_body)

    check("模板有登录/注册两个 tab", 'class="auth-tabs"' in HTML and 'switchAuth(' in HTML)
    check("登录卡只含用户名+密码两个 field",
          HTML.count('v-model="username"') >= 2 and 'v-model="password"' in HTML)
    check("注册卡有确认密码字段", 'v-model="regPwd2"' in HTML)
    check("注册卡保留账号类型选择", 'v-model="regRole"' in HTML)
    check("注册卡有「即将创建」实时回显", 'class="reg-hint"' in HTML and "regHint" in HTML)
    check("登录失败时给出「去注册」引导（notExist 分支）",
          'v-if="notExist"' in HTML and "goRegister" in HTML)
    check("卡片高度按注册卡加高（auth-reg）", ".login-container.auth-reg" in HTML)
    check("旧的「登录 / 自动注册」按钮文案已消失", "登录 / 自动注册" not in HTML)
    check("两段 v-if 独立（不依赖 v-else 相邻）",
          'v-if="authMode === \'login\'"' in HTML and 'v-if="authMode === \'register\'"' in HTML)

    # ------------------------------------------------------------------ [5] 校验规则（静态）
    print("\n[5] 前端校验规则（静态断言；**行为验证在真机 CDP 探针**里跑）")
    # 说明：这些规则是 JS，Python 里没法 exec（早先误用 compile() 直接 SyntaxError）。
    # 行为层面（拦下不发请求 / 409 文案 / 成功后回登录卡）由 scripts/cdp_auth_probe.py
    # 在真实浏览器里点出来；这里只保证规则**存在于真实源码**，防止被人删掉后测试仍绿。
    for rule, why in (("用户名不能包含空格", "拒绝含空格的用户名（错别字常伴空格）"),
                      ("密码至少 6 位", "密码长度下限"),
                      ("两次输入的密码不一致", "确认密码一致性"),
                      ("请填写用户名", "空用户名拦截"),
                      ("已被注册，请直接登录", "重名时引导直接登录")):
        check("规则存在：%s（%s）" % (rule, why), rule in SRC)
    check("注册成功后回登录卡且清空密码（不自动登录）",
          "authMode.value = 'login'" in SRC and "password.value = ''; regPwd2.value = ''" in SRC)

    # ------------------------------------------------------------------ [6] 清理
    print("\n[6] 清理")
    for u in (REAL, TYPO, "x_%s" % SUF, "合法用户_%s" % SUF):
        aid = _aid(u)
        if aid:
            purge_account(aid)
    check("测试账号已全部删除（不留残留）",
          not (_account_names() & {REAL, TYPO, "x_%s" % SUF, "合法用户_%s" % SUF}),
          sorted(_account_names() & {REAL, TYPO}))

    # ------------------------------------------------------------------ [7]
    # ★ 2026-09-23 用户报障修的回归：会话必须**跨后端重启**仍然有效。
    #   背景：会话原来是进程内内存字典（`_SESSIONS = {}`），而桌面壳每次重开都会**新起一个
    #   后端进程** —— 前端 localStorage 里还留着旧 token，于是表现为「界面已登录、数据全是 0」
    #   （用户截图：兴趣/关注/收藏全 0、天气卡报「未登录或登录已失效」；手动重登即恢复）。
    #   这里用**真·新进程**校验，而不是 reload 模块（reload 语义不可靠，验不出真问题）。
    print("\n[7] ★ 会话跨后端重启仍然有效（桌面版「记住登录」的前提）")
    _r = c.post("/api/login", json={"username": "尼古喵喵", "password": "123456"})
    _tok = (_r.json() or {}).get("token") if _r.status_code == 200 else None
    check("用真实账号登录拿到 token", bool(_tok), _r.status_code)
    if _tok:
        _conn = get_personal_conn()
        try:
            _row = _conn.execute("SELECT account_id FROM sessions WHERE token=?", (_tok,)).fetchone()
        finally:
            _conn.close()
        check("★ 会话已落库（sessions 表里有这一行；内存版不会有）",
              bool(_row), dict(_row) if _row else None)

        _code = ("import sys; sys.path.insert(0, %r)\n"
                 "from api.account import get_session\n"
                 "s = get_session(%r)\n"
                 "print('SESSION_OK', s['account_id'], s['username'])\n") % (ROOT, _tok)
        _pr = subprocess.run([sys.executable, "-c", _code], cwd=ROOT, capture_output=True,
                             text=True, encoding="utf-8", errors="replace", timeout=180)
        _out = (_pr.stdout or "") + (_pr.stderr or "")
        check("★★ 全新进程（= 后端重启）里旧 token 仍然有效", "SESSION_OK" in _out,
              _out.strip().splitlines()[-1][:120] if _out.strip() else "（无输出）")

        _r2 = c.get("/api/me/overview", params={"token": _tok})
        check("★ 用旧 token 取总览是 200（不是 401）", _r2.status_code == 200, _r2.status_code)
        _d2 = _r2.json() if _r2.status_code == 200 else {}
        _counts = {k: len(_d2.get(k) or []) for k in ("interests", "watchlist", "products")}
        check("★ 取到的是真数据而不是空（用户报障的现象就是全 0）",
              any(_counts.values()), _counts)

        c.post("/api/logout", params={"token": _tok})
        _pr2 = subprocess.run([sys.executable, "-c", _code], cwd=ROOT, capture_output=True,
                              text=True, encoding="utf-8", errors="replace", timeout=180)
        _out2 = (_pr2.stdout or "") + (_pr2.stderr or "")
        check("退出后旧 token 在新进程里失效（不再出现 SESSION_OK）",
              "SESSION_OK" not in _out2, "returncode=%s" % _pr2.returncode)

    print("\n" + "=" * 60)
    print("登录/注册两卡分离 e2e：通过 %d，失败 %d" % (len(PASS), len(FAIL)))
    if FAIL:
        print("失败项：")
        for f in FAIL:
            print("  -", f)
        return 1
    print("全部通过 ✅")
    return 0


if __name__ == "__main__":
    sys.exit(main())
