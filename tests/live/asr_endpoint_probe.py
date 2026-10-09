# -*- coding: utf-8 -*-
"""真机探针：POST /api/asr 语音转文字端点（v3.1 多媒体）。

判据（每一条都对应一个承诺 ✓）：
  ① /api/asr/status 报告模型在位 ✓；
  ② 真音频 → 返回非空文字 ✓ 且带耗时/时长 ✓（快响应：ms 应为百毫秒级 ✓）；
  ③ **不落盘** ✓：转写前后对比 data/ 与 test_out/ 的文件清单，不得新增音频类文件 ✓
     （音频只在内存 ✓ 出结果即弃 —— 用户 2026-10-04 拍板 ✓）；
  ④ 边界：过短音频 → 400 + 人话错误 ✓（前端直接展示 ✓）。

用**临时账号** ✓ 跑完回收 ✓（不碰尼古喵喵的数据 ✓）。
音频：默认用 `%USERPROFILE%\Downloads\米雪儿.wav` ✓（可用 ASR_AUDIO 覆盖 ✗）；不存在则跳过 ✓。
跑法：unset PYTHONPATH && .venv/Scripts/python.exe tests/live/asr_endpoint_probe.py
"""
import io as _io
import os
import sys
import time
import wave

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ.pop("PYTHONPATH", None)

import numpy as np                                                     # noqa: E402
from scipy.signal import resample_poly                                 # noqa: E402
from fastapi.testclient import TestClient                              # noqa: E402
import api.server as srv                                               # noqa: E402
from tools.schema_personal import get_personal_conn, purge_account      # noqa: E402

AUDIO = os.getenv("ASR_AUDIO") or os.path.join(os.path.expanduser("~"), "Downloads", "米雪儿.wav")
STAMP = str(int(time.time()))
USER = "bk_asr_%s" % STAMP
PWD = "bk_asr_pwd_%s" % STAMP
ok = fail = 0


def check(name, cond, extra=""):
    global ok, fail
    if cond:
        ok += 1; print("  \u2713 %s" % name, flush=True)
    else:
        fail += 1; print("  \u2717 %s  %s" % (name, extra), flush=True)


def to_wav16k_mono(path: str, seconds=None) -> bytes:
    """任意 WAV → 16k/mono/16bit WAV 字节（前端在浏览器里做同样的事 ✓ 这里等价复现 ✓）。"""
    with wave.open(path, "rb") as w:
        ch, sw, sr, n = w.getnchannels(), w.getsampwidth(), w.getframerate(), w.getnframes()
        raw = w.readframes(n)
    if sw == 1:
        d = (np.frombuffer(raw, dtype=np.uint8).astype(np.float32) - 128.0) / 128.0
    elif sw == 2:
        d = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    elif sw == 3:
        # ★ 24bit（本测试音频就是 24bit ✗ 一开始按 int32 读 → 读成噪声、还被截成 6.14s ✓ 修 ✓）
        b = np.frombuffer(raw, dtype=np.uint8).reshape(-1, 3).astype(np.int32)
        v = b[:, 0] | (b[:, 1] << 8) | (b[:, 2] << 16)
        v = np.where(v >= (1 << 23), v - (1 << 24), v)
        d = v.astype(np.float32) / 8388608.0
    else:
        d = np.frombuffer(raw, dtype=np.int32).astype(np.float32) / 2147483648.0
    if ch > 1:
        d = d.reshape(-1, ch).mean(axis=1)      # 多声道 → 单声道（必须在解码后统一做 ✓）
    if seconds:
        d = d[: int(sr * seconds)]
    if sr != 16000:
        d = resample_poly(d, 16000, sr).astype(np.float32)
    buf = _io.BytesIO()
    with wave.open(buf, "wb") as o:
        o.setnchannels(1); o.setsampwidth(2); o.setframerate(16000)
        o.writeframes((np.clip(d, -1, 1) * 32767).astype("<i2").tobytes())
    return buf.getvalue()


def snapshot():
    """data/ 与 tests/test_out/ 里的文件集合（用于验证"不落盘" ✓）。"""
    out = set()
    for base in ("data", os.path.join("tests", "test_out")):
        for r, _d, fs in os.walk(base):
            for f in fs:
                out.add(os.path.join(r, f))
    return out


if not os.path.exists(AUDIO):
    print("跳过：没有找到测试音频 %s（可用 ASR_AUDIO 指定 ✓）" % AUDIO)
    sys.exit(0)

_cm = TestClient(srv.app)
c = _cm.__enter__()                      # ★ 必须进 lifespan（预热线程与 loop 绑定都在里面 ✓）
aid = None
try:
    print("[probe] 音频：%s" % AUDIO)
    r = c.post("/api/register", json={"username": USER, "password": PWD, "role": "personal"})
    assert r.status_code == 200, r.text[:200]
    conn = get_personal_conn()
    try:
        aid = conn.execute("SELECT id FROM accounts WHERE username=?", (USER,)).fetchone()["id"]
    finally:
        conn.close()
    token = c.post("/api/login", json={"username": USER, "password": PWD}).json()["token"]
    print("[probe] 临时账号 id=%s ✓" % aid, flush=True)

    st = c.get("/api/asr/status", params={"token": token}).json()
    print("[probe] status: %s" % st, flush=True)
    check("① 开关默认开 ✓", bool(st.get("enabled")), st)
    check("① 找得到 asr 引擎可执行文件（status.bin_ok ✓）", bool(st.get("bin_ok")), st)
    if not st.get("engine_running"):
        print("  \u26a0 预热线程尚未起完（不判失败 ✓）—— 下面 transcribe 会走「自动拉起」路径 ✓", flush=True)

    wav = to_wav16k_mono(AUDIO)
    print("[probe] 待转写 WAV %d 字节（16k/mono/16bit ✓）" % len(wav), flush=True)
    before = snapshot()
    t0 = time.time()
    r = c.post("/api/asr", params={"token": token},
               files={"file": ("voice.wav", wav, "audio/wav")})
    wall = time.time() - t0
    body = r.json()
    print("[probe] HTTP %s · %s" % (r.status_code, str(body)[:300]), flush=True)
    check("② 返回 200 且 ok ✓", r.status_code == 200 and body.get("ok") is True, str(body)[:200])
    check("② 转出非空文字 ✓", bool((body.get("text") or "").strip()), str(body)[:200])
    check("② 带耗时与时长字段 ✓", isinstance(body.get("ms"), int) and body.get("audio_s"), str(body)[:200])
    print("[probe] 识别结果: %s" % (body.get("text") or ""), flush=True)
    print("[probe] 引擎耗时 %sms · 端到端 %.2fs（推理已常驻后 ms 应为百毫秒级 ✓）"
          % (body.get("ms"), wall), flush=True)

    after = snapshot()
    new_files = sorted(f for f in (after - before) if os.path.splitext(f)[1].lower() in
                       (".wav", ".webm", ".ogg", ".mp3", ".m4a", ".pcm", ".opus"))
    check("③ ★ 不落盘：没有新增任何音频文件 ✓", not new_files, new_files[:4])

    short = to_wav16k_mono(AUDIO, seconds=0.15)
    r2 = c.post("/api/asr", params={"token": token}, files={"file": ("s.wav", short, "audio/wav")})
    check("④ 过短音频 → 400 且人话错误 ✓", r2.status_code == 400 and "太短" in (r2.json().get("detail") or ""),
          "HTTP %s %s" % (r2.status_code, str(r2.json())[:160]))

    r3 = c.post("/api/asr", params={"token": "bad-token"}, files={"file": ("s.wav", wav, "audio/wav")})
    check("④ 坏 token → 401 ✓", r3.status_code == 401, "HTTP %s" % r3.status_code)
finally:
    try:
        _cm.__exit__(None, None, None)
    except Exception:
        pass
    try:
        if aid:
            purge_account(aid)
            print("[probe] 临时账号已回收 ✓", flush=True)
    except Exception as e:                                              # noqa: BLE001
        print("[probe] 回收失败（可手动清理 bk_asr_* ✓）: %s" % e, flush=True)

print("\n结果：%d/%d%s" % (ok, ok + fail, "  \u2713 全过" if not fail else "  \u2717 有失败"))
sys.stdout.flush()
os._exit(1 if fail else 0)
