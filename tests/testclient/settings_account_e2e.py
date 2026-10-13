# -*- coding: utf-8 -*-
"""账号安全（议题二）的进程内端到端：绑定邮箱 → 改开关 → 注销账号。

纪律（用户既有裁定）：**绝不在真实账号上做写操作** —— 本脚本自建临时账号、跑完自清
（`atexit` 兜底，异常退出也回收）；邮件走调试模式（验证码落在 test_out 的 outbox 里，
等价于"用户去邮箱里读到那 6 位数字"），不依赖真实 SMTP 凭据。

跑法：`.venv/Scripts/python.exe tests/testclient/settings_account_e2e.py`
"""
import atexit
import json
import os
import re
import sys
import time
from pathlib import Path

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

from fastapi.testclient import TestClient                                        # noqa: E402

from api.server import app                                                        # noqa: E402
from tools import schema_personal as sp                                           # noqa: E402
from utils import settings                                                         # noqa: E402
from utils import mailer                                                           # noqa: E402

OK, BAD = [], []


def check(name, cond, extra=""):
    (OK if cond else BAD).append(name)
    print("%s %s%s" % ("  [OK]  " if cond else "  [FAIL]", name, (" | %s" % extra) if extra else ""))


client = TestClient(app)
STAMP = int(time.time())
USER = "t_sec_%d" % STAMP
PWD = "t_pwd_%d" % STAMP
EMAIL = "t_sec_%d@example.com" % STAMP
AID = [None]


def _purge():
    """自清：本脚本这一轮建过的**全部**临时账号 + 账号目录 + 验证码行。

    ★ 2026-10-14 修正：原来只回收 AID[0]，而第二个账号（USER2，用于邮箱唯一性用例）只在
      正常走到清场时才被回收 —— 中途异常退出就会把它留在真实库里（实测漏过一次，
      正是调研 D6 那条「测试不得向真实库建号、用完必须自清」要求防住的情况）。
      改成按本轮 STAMP 前缀扫全部临时账号，崩溃也不会漏。
    """
    try:
        conn = sp.get_personal_conn()
        rows = [dict(x) for x in conn.execute(
            "SELECT id, username FROM accounts WHERE username LIKE ?", ("t_sec%_%d" % STAMP,))]
        conn.close()
        for r_ in rows:
            sp.purge_account(r_["id"])
            print("[cleanup] 已回收临时账号 %s" % r_["username"])
        for u in [r_["username"] for r_ in rows] + [USER, "t_sec2_%d" % STAMP]:
            for sub in ("agents_docs", "output", "pic", "sandbox", "tts_ref", os.path.join("rag_knowledge", "db")):
                __import__("shutil").rmtree(os.path.join(ROOT, "user_data", sub, u), ignore_errors=True)
    except Exception as e:                                                        # noqa: BLE001
        print("[cleanup] 回收失败：%s" % e)


atexit.register(_purge)

# 调试模式 + 独立 outbox：不真发信、也不污染项目里的那份 outbox
_real_outbox = mailer.OUTBOX
mailer.OUTBOX = Path(ROOT) / "tests" / "test_out" / ("_mail_outbox_%d.log" % STAMP)
settings.save({"MAIL_DEBUG": "1"}, actor="e2e")

print("== 0) 建临时账号 ==")
r = client.post("/api/register", json={"username": USER, "password": PWD, "role": "personal"})
check("注册临时账号", r.status_code == 200, r.status_code)
conn = sp.get_personal_conn()
row = conn.execute("SELECT id FROM accounts WHERE username=?", (USER,)).fetchone()
conn.close()
AID[0] = row[0]
tok = client.post("/api/login", json={"username": USER, "password": PWD}).json()["token"]
check("登录拿到 token", bool(tok))


def _last_code(purpose_marker: str) -> str:
    """从调试 outbox 里读最近一封邮件的 6 位验证码（等价于用户去邮箱读）。"""
    body = mailer.OUTBOX.read_text(encoding="utf-8") if mailer.OUTBOX.exists() else ""
    ms = re.findall(r"你的验证码是：(\d{6})", body)
    return ms[-1] if ms else ""


print("\n== 1) 安全状态（未绑定）==")
d = client.get("/api/me/security", params={"token": tok}).json()
check("未绑定时 bound=False", d.get("bound") is False, d)
check("未绑定时 can_delete=False（注销要求邮箱验证码）", d.get("can_delete") is False, d)
check("邮件通道处于调试模式", d.get("mail_debug") is True, d)

print("\n== 2) 绑定邮箱（发码 → 从调试邮箱读码 → 确认）==")
r = client.post("/api/me/email/send_code", params={"token": tok}, json={"email": EMAIL})
check("发绑定验证码 200", r.status_code == 200, r.text[:120])
code = _last_code("bind")
check("调试邮箱里读到了 6 位码", bool(code), code)
r = client.post("/api/me/email/confirm", params={"token": tok}, json={"email": EMAIL, "code": "000000"})
check("错误验证码被拒", r.status_code == 400, r.status_code)
r = client.post("/api/me/email/confirm", params={"token": tok}, json={"email": EMAIL, "code": code})
check("正确验证码绑定成功", r.status_code == 200, r.text[:120])
d = client.get("/api/me/security", params={"token": tok}).json()
check("状态显示已绑定且掩码正确", d.get("bound") and "@" in d.get("email_masked", "")
      and EMAIL not in d.get("email_masked", ""), d)
check("现在允许注销", d.get("can_delete") is True, d)

print("\n== 3) 邮箱唯一性 ==")
USER2 = "t_sec2_%d" % STAMP
client.post("/api/register", json={"username": USER2, "password": PWD, "role": "personal"})
tok2 = client.post("/api/login", json={"username": USER2, "password": PWD}).json()["token"]
r = client.post("/api/me/email/send_code", params={"token": tok2}, json={"email": EMAIL})
check("同一邮箱绑第二个账号被拒", r.status_code == 400, r.text[:120])
conn = sp.get_personal_conn()
row2 = conn.execute("SELECT id FROM accounts WHERE username=?", (USER2,)).fetchone()
conn.close()

print("\n== 4) 设置页：读分组 / 改开关 / 校验非法值 ==")
d = client.get("/api/settings", params={"token": tok}).json()
groups = {g["id"]: g for g in d.get("groups", [])}
check("读到 3 个分组（密钥组已撤下）", set(groups) == {"switches", "decision", "mail"}, list(groups))
# ★ 2026-10-13 口径变更（邮件裁定 5 + 视觉改造）：密钥一律只在 .env —— 接口里连「只读掩码」都不再有
check("接口不再下发密钥组（密钥只在 .env）", "secrets" not in groups, list(groups))
_raw = json.dumps(d, ensure_ascii=False)
check("接口回传里不含任何密钥字段名",
      not any(k in _raw for k in ("API_KEY", "AUTH_CODE", "SMTP_USER", "TAVILY")), "")
check("每项都带 help（设置页要显示说明）", all(it["help"] for g in groups.values() for it in g["items"]))
r = client.post("/api/settings", params={"token": tok}, json={"values": {"DECISION_TOP_K": "99"}})
check("非法参数被拒（超界）", r.status_code == 400, r.text[:120])
r = client.post("/api/settings", params={"token": tok}, json={"values": {"ZX_STREAM": "0"}})
check("保存开关 200", r.status_code == 200, r.text[:120])
settings.reload()
check("开关值已落库并生效", settings.source_of("ZX_STREAM") == "db" and settings.get("ZX_STREAM") == "0")
r = client.post("/api/settings", params={"token": tok}, json={"values": {"ZX_STREAM": "1"}})
settings.reload()
check("开关改回后生效", settings.get("ZX_STREAM") == "1")

print("\n== 5) 测试发信 ==")
r = client.post("/api/settings/test_mail", params={"token": tok}, json={"to": EMAIL})
check("测试邮件（调试模式）200", r.status_code == 200 and r.json().get("mode") == "debug", r.text[:120])

print("\n== 6) 注销账号（密码 + 邮箱验证码 + 确认词）==")
r = client.post("/api/me/delete/send_code", params={"token": tok}, json={"password": "wrong"})
check("错误密码被拒", r.status_code == 400, r.status_code)
r = client.post("/api/me/delete/send_code", params={"token": tok}, json={"password": PWD})
check("正确密码 → 发出注销验证码", r.status_code == 200, r.text[:140])
r = client.post("/api/me/delete/confirm", params={"token": tok},
                json={"password": PWD, "email": EMAIL, "code": _last_code("delete"), "confirm_text": "随便写"})
check("确认词不对被拒", r.status_code == 400, r.status_code)
r = client.post("/api/me/delete/confirm", params={"token": tok},
                json={"password": PWD, "email": "other@example.com", "code": _last_code("delete"),
                      "confirm_text": "注销账号"})
check("邮箱与绑定不一致被拒", r.status_code == 400, r.status_code)
r = client.post("/api/me/delete/confirm", params={"token": tok},
                json={"password": PWD, "email": EMAIL, "code": "000000", "confirm_text": "注销账号"})
check("错误验证码被拒", r.status_code == 400, r.status_code)
r = client.post("/api/me/delete/confirm", params={"token": tok},
                json={"password": PWD, "email": EMAIL, "code": _last_code("delete"), "confirm_text": "注销账号"})
check("三要素齐备 → 注销成功", r.status_code == 200, r.text[:200])
res = r.json() if r.status_code == 200 else {}
check("返回了备份目录", bool(res.get("backup")), res.get("backup"))
check("返回了被清除的目录清单", len(res.get("removed_files") or []) >= 1, res.get("removed_files"))
check("数据库里账号已消失", not any(x["username"] == USER for x in [dict(z) for z in sp.get_personal_conn().execute("SELECT username FROM accounts")]))
check("账号目录已清（user_data 下无残留）", not any(
    os.path.isdir(os.path.join(ROOT, "user_data", sub, USER))
    for sub in ("agents_docs", "output", "pic", "sandbox", "tts_ref", os.path.join("rag_knowledge", "db"))))
check("旧 token 已失效（会话随账号清除）",
      client.get("/api/me/security", params={"token": tok}).status_code == 401)
# 回滚本脚本动过的设置（MAIL_DEBUG / ZX_STREAM 恢复默认，避免影响真机）
settings.save({"MAIL_DEBUG": "0"}, actor="e2e")
settings.reload()

print("\n== 7) 清场 ==")
sp.purge_account(row2[0])                       # ★ 走官方回收（会话/验证码等账号域行一起清）
conn = sp.get_personal_conn()
conn.execute("DELETE FROM verify_codes WHERE account_id IN (SELECT id FROM accounts WHERE username LIKE 't_sec%')")
conn.commit()
conn.close()
for u in (USER, USER2):
    for sub in ("agents_docs", "output", "pic", "sandbox", "tts_ref", os.path.join("rag_knowledge", "db")):
        p = os.path.join(ROOT, "user_data", sub, u)
        if os.path.isdir(p):
            __import__("shutil").rmtree(p, ignore_errors=True)
AID[0] = None
mailer.OUTBOX = _real_outbox

print("\n================ 设置 / 账号安全 e2e 结果 ================")
print("通过 %d 项，失败 %d 项" % (len(OK), len(BAD)))
if BAD:
    print("失败项：%s" % BAD)
print("=========================================================")
sys.exit(1 if BAD else 0)
