"""语音转文字（v3.1 多媒体）：**主项目侧客户端** ✓ 引擎在独立进程里

**为什么不直接在项目里 import sherpa**（两条实测把设计逼成这样 ✓）：
  1) 模型**冷加载 8.5s** ✗ 而真正推理只要 **0.24s** ✓ → 必须**常驻**，不能每次重载（CLI 一次一进程 = 每次 9s ✗）；
  2) 项目 venv 里 chromadb 依赖的 `onnxruntime 1.29` 与 `sherpa-onnx-core` **自带的 ORT 撞名** ✗ ——
     实测：一 import sherpa 就 **段错误**（整个后端进程崩 ✗）；而 chromadb 是知识库在用的 ✗ 不能动它 ✓。
  → 结论：引擎跑在**它自己的干净 venv**（`asr` 工具 ✓ 与 voice_test 同一种"独立小项目"形状 ✓），
    以**常驻本地 HTTP 服务**形态存在（`asr serve` ✓）；本模块只负责：健康检查 / **自动拉起** / 转发 / 人话错误 ✓。

**音频不落盘** ✓：bytes 直接 POST 给本地引擎（127.0.0.1 ✓），出文字即弃 ✓（用户 2026-10-04 拍板 ✓）。
本模块**不做 HTTP 路由** ✗（那是 `api/server.py` 的事 ✓）、**不 import api** ✓（与 `tools/chat_images.py` 同一层 ✓）。

配置（环境变量 ✓ 与 `ZX_STREAM` 同款）：
  `ASR_ENABLED`（默认 1 ✓）· `ASR_PORT`（默认 8126 ✓）· `ASR_IDLE`（引擎空闲自退秒数 ✓ 默认 600 ✓）
  `ASR_LANG`（默认 zh ✓）· `ASR_SERVE_BIN`（`asr` 可执行文件路径 ✓ 默认自动探测 ✓）
"""
from __future__ import annotations

import os
import shutil
import subprocess
import threading
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
LOG_PATH = PROJECT_ROOT / "data" / "asr_serve.log"
DEFAULT_PORT = 8126
DEFAULT_IDLE = 600
HEALTH_WAIT_S = 45.0          # 首次拉起要等模型加载（实测 ~9s ✓ 给足余量 ✓）
REQ_TIMEOUT_S = 120.0

_SPAWN_LOCK = threading.Lock()
_LAST_ERR = ""


class VoiceAsrError(Exception):
    """给 API 层用的人话错误（前端直接展示 ✓）。"""


# ───────────────────────────── 配置 ─────────────────────────────
def _enabled() -> bool:
    return (os.getenv("ASR_ENABLED", "1") or "1").strip().lower() not in ("0", "false", "off", "no", "")


def _port() -> int:
    try:
        return int(os.getenv("ASR_PORT") or DEFAULT_PORT)
    except Exception:                                    # noqa: BLE001
        return DEFAULT_PORT


def _base() -> str:
    return "http://127.0.0.1:%d" % _port()


def _bin() -> str:
    """找 `asr` 可执行文件 ✓（uv tool 装在 ~/.local/bin ✓）。"""
    p = (os.getenv("ASR_SERVE_BIN") or "").strip()
    if p and Path(p).exists():
        return p
    for cand in (Path.home() / ".local" / "bin" / "asr.exe", Path.home() / ".local" / "bin" / "asr"):
        if cand.exists():
            return str(cand)
    return shutil.which("asr") or ""


def _client():
    """本机请求一律直连 ✗ 不走系统代理 ✓（项目通例 ✓）。"""
    import httpx
    return httpx.Client(trust_env=False, timeout=REQ_TIMEOUT_S)


# ───────────────────────────── 健康检查 / 自动拉起 ─────────────────────────────
def _health():
    try:
        with _client() as c:
            r = c.get(_base() + "/health", timeout=3.0)
            if r.status_code == 200:
                return r.json()
    except Exception:                                    # noqa: BLE001
        return None
    return None


def _spawn() -> bool:
    """拉起常驻引擎（幂等 ✓ 并发下只拉一个 ✓）。返回是否就绪 ✓。"""
    global _LAST_ERR
    exe = _bin()
    if not exe:
        _LAST_ERR = "没找到 `asr` 可执行文件（请在 asr_cli 目录跑 uv tool install ✓，或设 ASR_SERVE_BIN ✓）"
        return False
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1")
    env["ASR_PORT"] = str(_port())
    env["ASR_IDLE"] = os.getenv("ASR_IDLE") or str(DEFAULT_IDLE)
    creationflags = 0x08000000 if os.name == "nt" else 0      # CREATE_NO_WINDOW ✓ 不弹黑窗 ✓
    try:
        with open(LOG_PATH, "a", encoding="utf-8") as log:
            log.write("\n=== %s 拉起 asr serve port=%d ===\n" % (time.strftime("%F %T"), _port()))
            subprocess.Popen([exe, "serve", "--port", str(_port()), "--idle", env["ASR_IDLE"]],
                             stdout=log, stderr=subprocess.STDOUT, env=env,
                             creationflags=creationflags, close_fds=True)
    except Exception as e:                               # noqa: BLE001
        _LAST_ERR = "拉起语音引擎失败：%s: %s" % (type(e).__name__, e)
        return False
    t0 = time.time()
    while time.time() - t0 < HEALTH_WAIT_S:
        if _health():
            print("[asr] 引擎就绪（%.1fs ✓ 之后每次 ≈0.3s）" % (time.time() - t0))
            return True
        time.sleep(0.5)
    _LAST_ERR = "语音引擎启动超时（%.0fs ✗ 看 %s）" % (HEALTH_WAIT_S, LOG_PATH)
    return False


def ensure_engine(force: bool = False) -> bool:
    """确保引擎在跑 ✓（已在跑就直接返回 ✓）。"""
    if not _enabled():
        return False
    if not force and _health():
        return True
    with _SPAWN_LOCK:
        if not force and _health():
            return True
        return _spawn()


def warmup() -> None:
    """启动后台预热（像“天气预热”一样 ✓ 把 9s 冷加载挪到用户不感知的时刻 ✓）。"""
    if _enabled():
        ensure_engine()


# ───────────────────────────── 状态 / 转写 ─────────────────────────────
def status() -> dict:
    """给前端的轻量状态 ✓（不触发加载 ✗ 只报告 ✓）。"""
    h = _health()
    exe = _bin()
    return {"enabled": _enabled(), "engine_running": bool(h), "model": (h or {}).get("model", ""),
            "lang": (h or {}).get("lang", os.getenv("ASR_LANG") or "zh"), "port": _port(),
            "bin_ok": bool(exe), "bin": exe, "error": _LAST_ERR if not h else ""}


def transcribe(data: bytes, lang=None) -> dict:
    """音频字节（16k/mono/16bit WAV ✓ 或裸 PCM ✓）→ 文字 ✓。**音频不落盘** ✓。"""
    if not _enabled():
        raise VoiceAsrError("语音输入未启用（环境变量 ASR_ENABLED=0 ✗）")
    if not data:
        raise VoiceAsrError("收到空音频 ✗")
    if not ensure_engine():
        raise VoiceAsrError(_LAST_ERR or "语音引擎不可用 ✗")
    url = _base() + "/asr" + (("?lang=%s" % lang) if lang else "")
    try:
        with _client() as c:
            r = c.post(url, content=data, headers={"Content-Type": "application/octet-stream"})
    except Exception as e:                               # noqa: BLE001
        raise VoiceAsrError("语音引擎请求失败：%s: %s" % (type(e).__name__, e))
    try:
        body = r.json()
    except Exception:                                    # noqa: BLE001
        raise VoiceAsrError("语音引擎返回异常（HTTP %s ✗）" % r.status_code)
    if r.status_code != 200 or not body.get("ok"):
        raise VoiceAsrError(body.get("msg") or ("语音引擎出错（HTTP %s ✗）" % r.status_code))
    return {"text": body.get("text") or "", "audio_s": body.get("audio_s"), "ms": body.get("ms"),
            "engine": "sensevoice", "model": body.get("model") or "",
            "lang": lang or (os.getenv("ASR_LANG") or "zh")}
