# -*- coding: utf-8 -*-
"""真机 e2e（**真起 uvicorn** 8124，跑完自己清）：增量是不是在生成过程中经 WS 送到前端。

`tests/live/stream_delta_verify.py` 验的是"流式聚合与最终正文一致" ✓，但**没有**验
「`monitor.report_delta` → `/ws/{thread}` → 前端」这条传输链 ✓ —— 若增量是整轮结束后才一起推的，
那和没有流式没区别 ✗ 用户依旧看不见过程 ✗。
判据：**收到增量的事件时间 < `/api/chat` 返回的时间** ✓（同一次运行内可证 ✓）。

⚠️ **为什么不用 TestClient**（三次踩坑，别再走回头路 ✓）：
  · 裸 TestClient 不跑 lifespan → `manager.loop` 未绑定 → 推送被静默丢弃 ✗（WS 连得上、零事件 ✓）；
  · 给 WS 另开一个 TestClient → 会话活在另一个事件循环里 ✗ → 跨循环推送同样静默失败 ✗；
  · WS 与 POST 共用一个 TestClient → **收尾必然卡死** ✗：`TestClient.__exit__` 关闭 portal/lifespan 时
    要等 reader 线程那条仍挂着的 WS 会话 ✗（实测断言全过后挂在 `_cm.__exit__` 上，被 timeout 杀掉 ✓
    而 `taskkill /T` / `purge_account` 本身**实测 0.2s / 0.0s 毫不卡** ✓ —— 怀疑方向别搞错 ✓）。
真起后端没有这层失真，而且这正是用户实际使用的形态 ✓

★ 默认问**会激发推理**的问题：思考增量出不出现**首先取决于问题是否激发推理** ✓
  （踩过：用"用一句话说明你是谁"跑 5 次 → 思考恒为 0 ✗ 差点误判成传输/供应商问题 ✓）。
★ `faulthandler` 兜底：收尾再出意外也能留下**所有线程的栈** ✓（不再只看到"被 timeout 杀掉"✗）。

用**临时账号**（零工具 → 不碰网搜 ✓ 不写用户数据 ✓）跑完 `purge_account` 回收 ✓
跑法：unset PYTHONPATH && .venv/Scripts/python.exe -u tests/live/stream_ws_e2e.py
"""
import faulthandler
import json
import os
import subprocess
import sys
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ.pop("PYTHONPATH", None)

import httpx                                                          # noqa: E402
from tools.schema_personal import get_personal_conn, purge_account     # noqa: E402

faulthandler.enable()
faulthandler.dump_traceback_later(int(os.getenv("STREAM_E2E_FAULT", "120")), exit=True)

PORT = int(os.getenv("STREAM_E2E_PORT", "8124"))   # 别用 8123：那可能是用户开着的服务 ✓
BASE = "http://127.0.0.1:%d" % PORT
STAMP = str(int(time.time()))
USER = "bk_stream_%s" % STAMP
PWD = "bk_stream_pwd_%s" % STAMP
Q = os.getenv("STREAM_E2E_Q", "用两步推理算一下 17 * 23 等于多少，简要写步骤。")
LOGP = os.path.join(ROOT, "tests", "test_out", "stream_e2e_backend.log")
ok = fail = 0
child = None
aid = None


def check(name, cond, extra=""):
    global ok, fail
    if cond:
        ok += 1; print("  \u2713 " + name, flush=True)
    else:
        fail += 1; print("  \u2717 %s  %s" % (name, extra), flush=True)


def client():
    """本机代理会拦 localhost ✗ 一律直连 ✓（项目通例）"""
    return httpx.Client(trust_env=False, timeout=180.0)


try:
    # ① 起后端（与用户跑的命令同款 ✓ 换端口避免撞用户的服务 ✓）
    os.makedirs(os.path.dirname(LOGP), exist_ok=True)
    env = dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8",
               PYTHONUNBUFFERED="1", ZX_STREAM=os.getenv("ZX_STREAM", "1"))
    child = subprocess.Popen([os.path.join(ROOT, ".venv", "Scripts", "python.exe"), "-u",
                              "-m", "uvicorn", "api.server:app", "--port", str(PORT)],
                             cwd=ROOT, env=env,
                             stdout=open(LOGP, "w", encoding="utf-8"),   # 落文件而不是 PIPE ✓
                             stderr=subprocess.STDOUT)
    t0 = time.time()
    up = False
    while time.time() - t0 < 60:
        if child.poll() is not None:
            print("\u2717 后端秒退 —— 看 tests/test_out/stream_e2e_backend.log", flush=True)
            sys.exit(1)
        try:
            with client() as c:
                if c.get(BASE + "/").status_code == 200:
                    up = True
                    break
        except Exception:
            time.sleep(0.5)
    check("后端在 %d 端口起来（自起，跑完自己关 \u2713）" % PORT, up)
    if not up:
        sys.exit(1)
    print("[e2e] 后端已就绪（%.1fs）\u2713" % (time.time() - t0), flush=True)

    # ② 临时账号 + 会话（注册不返回 token ✓ 要再登录 ✓）
    with client() as c:
        r = c.post(BASE + "/api/register", json={"username": USER, "password": PWD, "role": "personal"})
        assert r.status_code == 200, r.text[:200]
        conn = get_personal_conn()
        try:
            aid = conn.execute("SELECT id FROM accounts WHERE username=?", (USER,)).fetchone()["id"]
        finally:
            conn.close()          # ★ 先拿 aid：后面任何一步失败，finally 都能回收 ✓
        token = c.post(BASE + "/api/login",
                       json={"username": USER, "password": PWD}).json()["token"]
        _s = c.post("%s/api/chat/sessions?token=%s" % (BASE, token)).json() or {}
        thread_id = _s.get("thread_id") or _s.get("id") or _s.get("thread")
    print("[e2e] 临时账号 id=%s · 会话 %s \u2713" % (aid, thread_id), flush=True)

    # ③ WS 读线程（真 websockets 客户端 ✓ 自己一个事件循环 ✓ 与后端循环无关 ✓）
    events, stop = [], threading.Event()

    def reader():
        import asyncio
        import websockets

        async def go():
            try:
                async with websockets.connect("ws://127.0.0.1:%d/ws/%s" % (PORT, thread_id),
                                              proxy=None) as ws:
                    while not stop.is_set():
                        try:
                            raw = await asyncio.wait_for(ws.recv(), timeout=0.5)
                        except asyncio.TimeoutError:
                            continue
                        events.append((time.time(), json.loads(raw)))
            except Exception as e:      # noqa: BLE001
                print("  [ws] 读线程退出:", type(e).__name__, e, flush=True)

        asyncio.run(go())

    threading.Thread(target=reader, daemon=True).start()
    time.sleep(1.5)          # 等 WS 连上 ✓

    # ④ 发问：增量应在 POST 返回**之前**就到 ✓
    with client() as c:
        t1 = time.time()
        r = c.post("%s/api/chat?token=%s&thread_id=%s&question=%s" % (BASE, token, thread_id, Q))
        post_done = time.time()
        data = r.json() or {}
        time.sleep(1.5)      # WS 是异步推送 ✓ 留点余量 ✓
        stop.set()

    answer = data.get("answer") or ""
    deltas = [(ts, p.get("data") or {}) for ts, p in events if p.get("event") == "delta"]
    ans_d = [(ts, d) for ts, d in deltas if d.get("kind") == "answer"]
    rea_d = [(ts, d) for ts, d in deltas if d.get("kind") == "reasoning"]
    others = [p for _, p in events if p.get("event") != "delta"]
    stream_text = "".join(d.get("text") or "" for _, d in ans_d)
    reason_text = "".join(d.get("text") or "" for _, d in rea_d)

    print("[e2e] WS 事件 %d 条：增量 %d（正文 %d / 思考 %d）· 其他事件 %d · POST 耗时 %dms"
          % (len(events), len(deltas), len(ans_d), len(rea_d), len(others),
             int((post_done - t1) * 1000)), flush=True)
    if ans_d:
        print("[e2e] 首个正文增量比 POST 返回**早** %dms 到达" % int((post_done - ans_d[0][0]) * 1000), flush=True)
    print("[e2e] 流式正文 %d 字 · 接口 answer %d 字 · 思考 %d 字"
          % (len(stream_text), len(answer), len(reason_text)), flush=True)

    check("WS 收到增量事件", len(deltas) > 0)
    check("\u2605 增量在 /api/chat 返回**之前**到达（真流式，不是结束后一次性推）",
          bool(ans_d) and ans_d[0][0] < post_done)
    check("\u2605 流式聚合 == 接口返回的 answer（逐字）", stream_text.strip() == answer.strip(),
          "\n     流式: %r\n     接口: %r" % (stream_text[:60], answer[:60]))
    check("序号单调（前端可发现丢块/乱序）",
          [d.get("seq") for _, d in deltas] == sorted(d.get("seq") or 0 for _, d in deltas))
    check("接口返回体契约不变（answer/thread_id/cancelled/last_msg_id 都在）",
          all(k in (data or {}) for k in ("answer", "thread_id", "cancelled", "last_msg_id")), data)
    check("\u2605 思考增量也经 WS 到达（右栏那条链路）", len(rea_d) > 0,
          "默认问题（17*23 两步推理）应当激发 reasoning ✓ 若为 0 先换问题再怀疑传输 ✗")

finally:
    # 收尾：两步都实测**毫秒级** ✓（taskkill /T 0.2s · purge_account 0.0s —— 卡不到这里 ✓）
    if child is not None and child.poll() is None:
        # ★ `taskkill /T`：连**子孙进程**一起杀 ✓ —— venv 的 `python.exe` 是 launcher，
        #   真正跑 uvicorn 的是它再起的孙子 ✗ `terminate()` 只杀 launcher ✓
        subprocess.run(["taskkill", "/PID", str(child.pid), "/F", "/T"],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=20)
        try:
            child.wait(timeout=10)
        except Exception:
            pass
        print("[e2e] 自起的后端已关闭 \u2713", flush=True)
    if aid:
        try:
            purge_account(aid)
            print("[e2e] 临时账号已回收 \u2713", flush=True)
        except Exception as e:      # noqa: BLE001
            print("[e2e] 回收失败（可手动清理 bk_stream_* 账号）:", type(e).__name__, e, flush=True)
    # 端口兜底：万一还有持有者（孙子进程 ✗）一并清 ✓
    try:
        # ⚠️ netstat 在中文 Windows 上是 **GBK** 输出 ✗ 用 text=True 会让 subprocess 的收尾
        #   线程抛 UnicodeDecodeError（实测踩到 ✓ 结果：端口兜底静默没跑到 ✓）
        out = subprocess.run(["netstat", "-ano"], capture_output=True, timeout=15).stdout.decode(
            "gbk", "replace")
        for ln in out.splitlines():
            if "LISTENING" in ln and (":%d " % PORT) in ln:
                pid = ln.split()[-1].strip()
                subprocess.run(["taskkill", "/PID", pid, "/F", "/T"],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=15)
                print("[e2e] 端口 %d 仍有持有者（pid=%s）已清 \u2713" % (PORT, pid), flush=True)
                break
    except Exception:
        pass

print("\n结果：%d/%d%s" % (ok, ok + fail, "  \u2713 全过" if not fail else "  \u2717 有失败"), flush=True)
sys.stdout.flush()
os._exit(1 if fail else 0)          # ★ 强制退出：不让任何守护线程/异步资源拖住解释器 ✓
