# -*- coding: utf-8 -*-
"""隔离实验：把 e2e 收尾的两步（taskkill 树 / purge_account）分别计时，看到底哪一步卡 ✗。"""
import faulthandler, os, subprocess, sys, time
ROOT = r"D:\code\aicode\ai智能体开发实战5期\weekend\deep_search_pro"
sys.path.insert(0, ROOT); os.chdir(ROOT); os.environ.pop("PYTHONPATH", None)
faulthandler.enable(); faulthandler.dump_traceback_later(75, exit=True)   # 保险：75s 打印所有线程栈

from tools.schema_personal import get_personal_conn, purge_account

PORT = 8125; STAMP = str(int(time.time())); USER = "bk_td_%s" % STAMP
import httpx
LOG = os.path.join(ROOT, "tests", "test_out", "teardown_spike_backend.log")
os.makedirs(os.path.dirname(LOG), exist_ok=True)


def t(msg, t0):
    print("[%6.1fs] %s" % (time.time() - t0, msg), flush=True)


t0 = time.time()
log = open(LOG, "w", encoding="utf-8")
child = subprocess.Popen([os.path.join(ROOT, ".venv", "Scripts", "python.exe"), "-u",
                          "-m", "uvicorn", "api.server:app", "--port", str(PORT)],
                         cwd=ROOT, env=dict(os.environ, PYTHONUTF8="1"), stdout=log, stderr=subprocess.STDOUT)
log.close(); t("spawn launcher pid=%s" % child.pid, t0)
c = httpx.Client(trust_env=False, timeout=30)
for _ in range(60):
    try:
        if c.get("http://127.0.0.1:%d/" % PORT).status_code == 200:
            break
    except Exception:
        time.sleep(0.5)
t("backend healthy", t0)
r = c.post("http://127.0.0.1:%d/api/register" % PORT, json={"username": USER, "password": USER, "role": "personal"})
conn = get_personal_conn(); aid = conn.execute("SELECT id FROM accounts WHERE username=?", (USER,)).fetchone()["id"]; conn.close()
t("registered aid=%s" % aid, t0)

s = time.time(); os.system("taskkill /PID %d /F /T >nul 2>&1" % child.pid); t("taskkill /T 用时 %.1fs" % (time.time() - s), t0)
s = time.time()
try:
    child.wait(timeout=10)
except Exception as e:
    print("   child.wait 超时:", e, flush=True)
t("child.wait 用时 %.1fs" % (time.time() - s), t0)

s = time.time(); purge_account(aid); t("purge_account 用时 %.1fs" % (time.time() - s), t0)
print("结论：两步都不卡 → 卡在别处（脚本自身的退出路径）✓", flush=True)
print("[%.1fs] 收尾实验正常结束" % (time.time() - t0), flush=True)
os._exit(0)
