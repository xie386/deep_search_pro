# -*- coding: utf-8 -*-
"""智选情报官 · 桌面版（pywebview 外壳）

设计口径（与 Web 版的关系）：
    **后端零改动、现有前端零改动。** 外壳做三件事：
      ① 找不到服务就 spawn `python -m uvicorn api.server:app` 起一个（用项目自己的 .venv）；
      ② 轮询等就绪，然后把窗口加载到 **后端自己托管的 `http://127.0.0.1:PORT/`**；
      ③ 托盘常驻 + 原生文件对话框（导出/导入）+ 系统通知 + 退出时清理自己起的服务。

★ 关键约束：窗口必须加载 `http://127.0.0.1:PORT/`（后端托管的页面），**不能**用 file:// 打开
  本地 static/index.html —— 那样页面与 API 变成跨源，而后端没有 CORS 中间件，所有请求会被
  浏览器拦掉。同源加载还让 sessionStorage（登录态）、WebSocket（工具进度）全部原样可用，
  这才是「后端不变」的前提。

★ 进程归属：只 kill **自己 spawn 的** uvicorn。若端口上已有用户在跑的服务（用户自己也开着
  uvicorn，或在 IDE 里跑着），直接复用，退出时绝不碰它。

用法：
    .venv/Scripts/python.exe static/desktop/app.py            # 直接跑
    .venv/Scripts/python.exe static/desktop/app.py --port 8124 # 换端口
    双击 static/desktop/智选情报官.cmd                         # 免敲命令

依赖：pywebview（Win 上用 pythonnet + Edge WebView2，Win11 自带运行时）。
"""

import argparse
import json
import os
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]      # static/desktop/app.py -> 项目根
HERE = Path(__file__).resolve().parent
# ★ 日志目录可被环境变量改掉（2026-09-25）：自检脚本会起临时外壳实例，若与用户正在跑的实例
#   **共用同一份 desktop.log**，两边日志会交错，而自检要按日志解析"本次实例的后端 pid" ——
#   一旦读到用户的 pid，后续 taskkill 就可能误伤它。测试用 ZX_DESKTOP_LOGDIR 指到自己的临时目录。
LOG_DIR = Path(os.environ["ZX_DESKTOP_LOGDIR"]) if os.environ.get("ZX_DESKTOP_LOGDIR") else HERE
BACKEND_LOG = LOG_DIR / "backend.log"           # 外壳起的后端子进程输出（排查"起不来"的唯一线索）

APP_TITLE = "智选情报官"
DEFAULT_PORT = 8123
READY_TIMEOUT = 180          # 后端 import 约 20s、就绪实测 55~85s，给足余量
POLL_INTERVAL = 0.6
CLOSE_TO_TRAY = True         # 点窗口 X → 收进托盘而不是退出（托盘不可用时自动放行关闭）
PORT_ATTEMPTS = 5            # 端口自动换：从起始端口往后最多试几个（8123 → 8127）
LOCK_OFFSET = 1000           # 单实例锁端口 = 后端端口 + 1000
RESTART_BACKOFF = (2.0, 5.0, 15.0)   # 后端崩溃自动重启的退避（秒）
RESTART_MAX = 3              # 自动重启次数上限（防崩溃循环刷屏）
WATCH_INTERVAL = 3.0         # 看护线程巡检间隔（秒）

# ── 无代理 opener：本机系统代理（127.0.0.1:7897）会拦 localhost，必须绕过 ──
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))

# ── 状态（托盘菜单/退出逻辑共用）──
_proc: subprocess.Popen | None = None      # 只有"自己起的"服务才会被赋值
_webview_proc = None
_tray_icon = None
_quitting = threading.Event()
_really_quit = threading.Event()           # 托盘菜单「退出」会置位：此时点 X 才真的关窗口
_SAVE_DIR_OVERRIDE: str = ""               # 测试模式：原生「另存为」直接写这个目录（不弹窗）
_OWN_BACKEND = False                       # 后端是"本外壳起的"(True) 还是"复用别人现成的"(False)
_backend_owner = None                      # 真正监听端口的进程 pid（可能是 Popen 子进程的"孙子"）
_tray_status = ""                          # 托盘提示里的附加状态（如"正在生成周报…"）


def _default_save_dir() -> str:
    """原生「另存为」对话框的默认目录：用户的「下载」目录优先，退到项目 output/。"""
    for cand in (Path.home() / "Downloads", ROOT / "output"):
        try:
            if cand.is_dir():
                return str(cand)
        except OSError:
            pass
    return str(ROOT)


def _ask_save_path(filename: str, file_types=("所有文件 (*.*)",)):
    """拿到「要写入的路径」。返回 (path 或 None, cancelled)。

    测试模式（`--selftest-save-dir`）下直接拼目录、**不弹对话框**——否则自动化测试会卡在
    无人点击的原生对话框上；生产路径永远走原生「另存为」。
    """
    name = filename or "导出.bin"
    if _SAVE_DIR_OVERRIDE:
        d = Path(_SAVE_DIR_OVERRIDE)
        try:
            d.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            log("[导出] 建测试保存目录失败：%s" % e)
        return str(d / name), False
    import webview
    win = webview.active_window()
    path = win.create_file_dialog(
        webview.SAVE_DIALOG, directory=_default_save_dir(),
        save_filename=name, file_types=tuple(file_types or ("所有文件 (*.*)",)),
    )
    if not path:
        return None, True
    return (path if isinstance(path, str) else path[0]), False

# ★ 加载页：后端就绪前展示（项目主题色 + 跳动圆点，让用户知道"它还活着"）
_LOADING_HTML = """<!doctype html><html lang="zh"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>%s · 启动中</title><style>
:root{--c1:#54aa69;--c2:#7bc48e;--bg:#0f1419;--fg:#e5e7eb;--muted:#9ca3af}
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:"Segoe UI",system-ui,"Microsoft YaHei",sans-serif;background:var(--bg);color:var(--fg);
display:flex;flex-direction:column;align-items:center;justify-content:center;height:100vh;overflow:hidden}
.logo{font-size:3.2rem;font-weight:700;color:var(--c1);margin-bottom:.4rem;letter-spacing:.04em}
.sub{color:var(--muted);font-size:.95rem;margin-bottom:2.4rem}
.bar{width:200px;height:4px;background:rgba(255,255,255,.08);border-radius:4px;overflow:hidden;margin-bottom:1.2rem}
.bar>i{display:block;height:100%%;width:35%%;background:linear-gradient(90deg,var(--c1),var(--c2));
animation:slide 1.4s ease-in-out infinite}
@keyframes slide{0%%{transform:translateX(-120%%)}100%%{transform:translateX(320%%)}}
.hint{color:var(--muted);font-size:.82rem;text-align:center;line-height:1.7}
.hint b{color:var(--c2)}
.dots{display:inline-block;width:1.2em;text-align:left}
</style></head><body>
<div class="logo">%s</div>
<div class="sub">正在启动本地后端（端口 %d）…</div>
<div class="bar"><i></i></div>
<div class="hint">首次启动约需 <b>20–90 秒</b>（加载模型 + 向量库）<br>
启动完成后窗口会自动切换到正式页面<span class="dots" id="d"></span></div>
<script>
var d=document.getElementById('d'),n=0;
setInterval(function(){n=(n+1)%%4;d.textContent='.'.repeat(n)},450);
</script></body></html>"""


# ══════════════════════════════════════════════════════════════════════
# 工具
# ══════════════════════════════════════════════════════════════════════
def _b64_to_bytes(b64: str) -> bytes:
    """base64 → bytes（前端把文件字节 base64 后传进来）。容错处理 data URL 前缀与空白。"""
    import base64
    s = (b64 or "").strip()
    if "," in s and s.split(",", 1)[0].startswith("data:"):
        s = s.split(",", 1)[1]          # 去掉 data:...;base64, 前缀
    s = "".join(s.split())
    if not s:
        return b""
    pad = (-len(s)) % 4
    return base64.b64decode(s + ("=" * pad))


def _write_bytes(path, data: bytes):
    """写二进制文件；成功返回 None，失败返回错误字符串（便于单测，不依赖对话框）。"""
    try:
        with open(str(path), "wb") as f:
            f.write(data or b"")
        return None
    except Exception as e:  # noqa: BLE001
        return "%s: %s" % (type(e).__name__, e)


def _should_close_to_tray(tray_active: bool, really_quit: bool) -> bool:
    """点窗口 X 时是否改为「收进托盘」。

    ★ 关键：**托盘不可用时必须放行关闭**，否则用户没有退出的手段（窗口关不掉 = 只能杀进程）。
    """
    return bool(CLOSE_TO_TRAY and tray_active and not really_quit)


def _force_utf8_stdio():
    """把**外壳自己**的 stdout/stderr 切到 UTF-8。

    ★ 为什么（2026-09-22 实测）：外壳的日志里有 ✅/❌ 这类字符。当它的输出不是控制台
      （被重定向到文件、被管道捕获、被别的程序当子进程启动）时，Python 按系统 locale 选编码
      ——中文机 = GBK → 编不出这些字符 → **外壳自己抛 UnicodeEncodeError 挂掉**。
      控制台里跑看不出来（控制台走 UTF-16 直写），一旦重定向就现形。
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:  # noqa: BLE001  (某些包装流没有 reconfigure)
            pass


def _no_proxy_env() -> dict:
    """给子进程一份干净的环境：清代理 + 清 PYTHONPATH + **强制 UTF-8 输出**。

    ★ 为什么必须强制 UTF-8（2026-09-22 实测崩溃）：
      子进程的 stdout 是**文件**（backend.log）时，Python 按系统 locale 选编码——中文机上
      是 GBK。而项目 import 阶段会 print 含 emoji 的内容（`agent/prompts.py` 的调试 print
      里有 🔍），GBK 编不出来 → `UnicodeEncodeError` → **后端在 import 阶段就崩**，
      表现为"双击起不来"，而手工在终端跑却没事（终端环境常带 PYTHONUTF8/PYTHONIOENCODING）。
      这里显式设上，外壳起的后端就不再依赖用户环境。
    """
    env = dict(os.environ)
    for k in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
        env.pop(k, None)
    env["NO_PROXY"] = "127.0.0.1,localhost"
    env.pop("PYTHONPATH", None)        # 跨 venv 必清（Hermes venv 泄漏会让项目 pydantic_core 错配）
    env["PYTHONUTF8"] = "1"            # Python UTF-8 模式：stdout/文件默认 UTF-8
    env["PYTHONIOENCODING"] = "utf-8"  # 双保险（覆盖 stdout/stderr 编码）
    return env


def can_bind(port: int, host: str = "127.0.0.1") -> bool:
    """端口是否真的空着（**用 bind 探测，而不是 connect 探测**）。

    ★ 为什么不用 connect_ex：只 listen 不 accept 的端口，backlog（默认 1）会被探测连接塞满，
      之后 connect 会被**拒绝** → 误判成"端口空闲"（实测踩过：`http_ok` 的探测请求先占满
      backlog，紧接着的 port_in_use 就返回 False，于是在占用端口上硬起后端 → 报 10048 失败，
      而不是自动换端口）。bind 问的是"地址能不能用"，与连接队列无关，判得准。
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind((host, port))
            return True
        except OSError:
            return False


def port_in_use(port: int, host: str = "127.0.0.1") -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.4)
        return s.connect_ex((host, port)) == 0


def http_ok(url: str, timeout: float = 3.0) -> bool:
    try:
        with _OPENER.open(url, timeout=timeout) as r:
            return 200 <= r.status < 400
    except (urllib.error.URLError, OSError, ValueError):
        return False


def _owner_pid(port: int):
    """谁在监听这个端口（netstat -ano 解析；拿不到返回 None）。

    ★ 为什么需要它（2026-09-22 实测）：`.venv\\Scripts\\python.exe` 是个 launcher，会**再起一个
      真解释器**来跑 uvicorn —— 于是 Socket 是"孙子进程"持有的，Popen 拿到的那个句柄一旦退出，
      我们既会误判"后端起不来"，退出时也可能**杀不掉真正的服务，留下孤儿**。
    """
    try:
        out = subprocess.run(
            ["netstat", "-ano"], capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=15,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout
    except Exception:  # noqa: BLE001
        return None
    want = "127.0.0.1:%d" % port
    found = None
    for line in out.splitlines():
        parts = line.split()
        if len(parts) >= 5 and parts[0].upper().startswith("TCP") and parts[1] == want and parts[3] == "LISTENING":
            try:
                found = int(parts[4])
            except ValueError:
                continue
    return found


def wait_ready(url: str, timeout: float = READY_TIMEOUT, cancel: threading.Event | None = None,
               verbose: bool = False) -> bool:
    """轮询直到页面可访问（服务就绪）。verbose=True 时打印进度点（给人看的启动反馈）。"""
    t0 = time.time()
    if verbose:
        print("  等待后端就绪", end="", flush=True)
    while time.time() - t0 < timeout:
        if cancel is not None and cancel.is_set():
            if verbose:
                print()
            return False
        if http_ok(url):
            if verbose:
                print(" ✅（%.1fs）" % (time.time() - t0))
            return True
        time.sleep(POLL_INTERVAL)
        if verbose:
            print(".", end="", flush=True)
    if verbose:
        print()
    return False


def _python_exe() -> str:
    """优先用项目自己的 venv（依赖齐全），否则退回当前解释器。"""
    for cand in (ROOT / ".venv" / "Scripts" / "python.exe", ROOT / ".venv" / "bin" / "python"):
        if cand.is_file():
            return str(cand)
    return sys.executable


# ══════════════════════════════════════════════════════════════════════
# 后端进程管理
# ══════════════════════════════════════════════════════════════════════
def _tail(path, n: int = 15, max_line: int = 400) -> str:
    """读文件末尾若干行（排查后端为什么没起来）。

    过滤超长行：后端 import 时会 print 一大坨提示词（单行几万字符），会把真正有用的
    末尾信息挤出日志尾部——所以跳过超长行并注明跳过了多少。
    """
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except Exception as e:  # noqa: BLE001
        return "(读不到日志：%s)" % e
    if not lines:
        return "(空)"
    kept, skipped = [], 0
    for line in reversed(lines):
        if len(line) > max_line:
            skipped += 1
            continue
        kept.append(line)
        if len(kept) >= n:
            break
    kept.reverse()
    note = "（已跳过 %d 行超长输出，如提示词转储）\n" % skipped if skipped else ""
    return note + "\n".join(kept)


def _log_has(path, needles) -> bool:
    """在**整个**日志里找关键字。

    ⚠️ 不能只看尾部：子进程崩溃时 stdout 缓冲可能把更早的 traceback 排在后面，
       bind 错误（10048）常常落在倒数几十行之外——只看 tail 会漏判（实测）。
    """
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        return False
    return any(n in text for n in needles)


def _kill_tree(pid: int):
    """整棵树杀（Windows 用 taskkill /T）。失败只记日志。"""
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)],
                           capture_output=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        else:
            os.kill(pid, 15)
    except Exception as e:  # noqa: BLE001
        log("[后端] 杀 pid=%d 失败：%s" % (pid, e))


def wait_ready_monitored(url: str, proc: "subprocess.Popen | None", timeout: float = READY_TIMEOUT,
                         cancel: threading.Event | None = None, verbose: bool = True,
                         port: int | None = None, child_gone_grace: float = 90.0):
    """轮询就绪，**同时监控自己起的子进程**。返回 (ok, reason)。

    ★ 为什么必须监控子进程：只看 HTTP 的话，子进程启动即崩（端口被占、.env 错、依赖缺失）
      会变成「傻等 180 秒然后说"请检查 .env"」——用户拿不到任何线索（实测踩过）。
      子进程一旦退出就立刻返回，附后端日志尾部，几秒内给出真正原因。

    ★ 但"子进程退出"**不等于**"服务没起来"（2026-09-22 实测第二次）：venv 的 `python.exe`
      是个 launcher，会再起一个真解释器跑 uvicorn —— launcher 先退出（退出码 1）、服务随后才
      就绪。所以这里加一层判断：
        - 子进程没了 **但端口有人在监听** → 服务在起，继续等（最多 child_gone_grace 秒）
        - 子进程没了 **且端口没人听**     → 真死了，立刻失败（保持"秒级报死因"的体验）
    """
    if port is None:
        try:
            port = int(url.rsplit(":", 1)[1].strip("/"))
        except (ValueError, IndexError):
            port = None
    t0 = time.time()
    last_note = 0.0
    gone_at = None
    while time.time() - t0 < timeout:
        if cancel is not None and cancel.is_set():
            return False, "cancelled"
        if http_ok(url):
            return True, "ready"
        if proc is not None and proc.poll() is not None:
            held = False
            if port is not None:
                if port_in_use(port):
                    held = True                     # 有人能连上 → 端口上确实有东西
                elif port >= 1024 and not can_bind(port):
                    held = True                     # bind 不下来也可能是 backlog 塞满（低端口权限不足会误判）
            # ★ 但"端口上有东西"也可能是**别人的**程序占着（我们的子进程因此 bind 失败退出）。
            #   这种情况必须立刻失败并报死因（保持"秒级报错"），不能再等宽限期。
            if held and _log_has(BACKEND_LOG, ("10048", "attempting to bind", "只允许使用一次")):
                return False, "child_exit_port_taken:%s" % proc.returncode
            if gone_at is None:
                gone_at = time.time()
                if verbose and held:
                    print("  （子进程已退出，但 %s 有人在监听 → 服务在起，继续等）" % port, flush=True)
            if not held:
                return False, "child_exit:%s" % proc.returncode
            if time.time() - gone_at > child_gone_grace:
                return False, "child_exit_gone:%s" % proc.returncode
        now = time.time() - t0
        if verbose and now - last_note >= 10:
            last_note = now
            print("  已等待 %ds…（后端进程存活，仍在启动）" % int(now), flush=True)
        time.sleep(POLL_INTERVAL)
    return False, "timeout"


def start_backend(port: int, timeout: float = READY_TIMEOUT) -> bool:
    """起后端。返回 True=已就绪（自己起的或复用的都算）。timeout=等待就绪上限秒数。"""
    global _proc, _OWN_BACKEND
    base = f"http://127.0.0.1:{port}/"

    # 端口已有服务 → 复用（可能是用户自己开的），**不接管、不 kill**
    if http_ok(base):
        log("[后端] 检测到 %s 已有服务在跑 → 直接复用（退出时不会关闭它）" % base)
        _OWN_BACKEND = False        # ★ 别人的进程：看护线程不会去重启它
        return True

    # 端口被占但不响应 HTTP → 只警告，不阻断。
    # ★ 为什么不在这里直接 return False：`port_in_use` 用 connect_ex 探测，对"只 listen
    #   不 accept"的端口不可靠（backlog 被探测连接塞满时会误判为空闲）；而且用户自己那台
    #   还在 import 阶段的 uvicorn 也符合"端口没在响应"——直接判死会把正常情况判成故障。
    #   真相由子进程给出：它 bind 失败会立刻退出，退出码/日志里就是 10048。
    if port_in_use(port):
        log("[后端] ⚠️ 端口 %d 看起来被占用（但不响应 HTTP）——仍尝试启动，若真被占会立刻报错" % port)

    log("[后端] 未检测到服务，正在启动 uvicorn（首次约 20~90 秒）…")
    creationflags = 0
    if os.name == "nt":
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    # ★ 子进程输出落到 backend.log：否则它崩了完全无据可查（DEVNULL 的教训）
    try:
        lf = open(BACKEND_LOG, "w", encoding="utf-8")
    except OSError:
        lf = None
    _proc = subprocess.Popen(
        [_python_exe(), "-m", "uvicorn", "api.server:app", "--host", "127.0.0.1", "--port", str(port)],
        cwd=str(ROOT),
        env=_no_proxy_env(),
        stdout=lf if lf else subprocess.DEVNULL,
        stderr=subprocess.STDOUT if lf else subprocess.DEVNULL,
        creationflags=creationflags,
    )
    if lf:
        lf.close()          # 子进程持有自己的句柄，父进程可以关
    _OWN_BACKEND = True     # ★ 这是本外壳的进程：崩了要自动重启
    log("[后端] uvicorn 已启动 (pid=%d)，等待就绪…（日志：%s）" % (_proc.pid, BACKEND_LOG.name))
    ok, reason = wait_ready_monitored(base, _proc, timeout=timeout, cancel=_quitting, verbose=True, port=port)
    if ok:
        global _backend_owner
        _backend_owner = _owner_pid(port)       # 端口真正归谁（多数情况下是子进程的"孙子"）
        log("[后端] 就绪 ✅%s" % ("（端口由 pid=%d 持有）" % _backend_owner if _backend_owner else ""))
        return True

    # ★ 失败时也要认一下"端口持有者"：launcher 已退出、真服务还占着端口的话，
    #   光杀 _proc.pid（已死的 pid）打不掉它 → 留成孤儿（下次启动就撞端口）。
    _orphan = _owner_pid(port)
    if _orphan and (_proc is None or _orphan != _proc.pid):
        _backend_owner = _orphan
        log("[后端] 发现端口仍被 pid=%d 持有（launcher 已退出）→ 退出时会一并清理" % _orphan)

    if reason.startswith("child_exit"):
        tail = _tail(BACKEND_LOG, 25)
        log("[后端] ❌ 后端进程启动后立即退出（退出码 %s）——这不是「等得不够久」，是根本没起来。"
            % reason.split(":", 1)[1])
        # 从子进程日志里认出真正的死因（比笼统说"请检查 .env"有用得多）——全文件搜，不看 tail
        if _log_has(BACKEND_LOG, ("10048", "attempting to bind", "只允许使用一次")):
            log("[后端] 死因：端口 %d 被别的程序占用（不是本项目服务）。" % port)
            log("[后端] → 换端口：desktop.cmd --port 8124")
            log("[后端] → 或查是谁占着：netstat -ano | findstr :%d" % port)
        elif _log_has(BACKEND_LOG, ("ModuleNotFoundError", "ImportError")):
            log("[后端] 死因：依赖缺失/导入失败（看下面日志里的模块名）")
        else:
            log("[后端] 常见原因：端口被占 / .env 缺配置 / 依赖缺失（下面日志里有真正原因）")
    else:
        log("[后端] ❌ 等待超时（%ds）；后端进程仍存活，说明启动特别慢或卡在某一步。" % READY_TIMEOUT)
    log("[后端] ——— backend.log 末尾 ———\n%s\n[后端] ———（完整日志：%s）———" % (_tail(BACKEND_LOG), BACKEND_LOG))
    return False


def stop_backend():
    """只关自己起的服务；用户自己的进程绝不碰。

    ★ 为什么不能只看 `_proc.poll()`（2026-09-22 实测）：venv 的 `python.exe` 是 launcher，
      真正的 uvicorn 是它的**子进程**——只要 launcher 退出过，`poll()` 就非 None，旧写法会
      直接 return，**把真正在跑的服务留成孤儿**（端口一直占着，下次启动还得手动清）。
      现在按"我们记录过的 pid"整棵树杀：Popen 子进程 + 端口持有者。
    """
    global _proc, _backend_owner
    if _proc is None and (_backend_owner is None or not _OWN_BACKEND):
        return
    pids = []
    if _proc is not None:
        try:
            pids.append(int(_proc.pid))
        except Exception:  # noqa: BLE001
            pass
    if _OWN_BACKEND and _backend_owner:
        try:
            p = int(_backend_owner)
            if p not in pids and p != os.getpid():
                pids.append(p)
        except Exception:  # noqa: BLE001
            pass
    for pid in pids:
        log("[后端] 关闭本次启动的 uvicorn (pid=%d)…" % pid)
        _kill_tree(pid)
    _proc = None
    _backend_owner = None


# ══════════════════════════════════════════════════════════════════════
# 日志（外壳自己的日志，写到 LOG_DIR/desktop.log；LOG_DIR 默认 static/desktop/，测试可用
#  环境变量 ZX_DESKTOP_LOGDIR 改到临时目录，避免与用户正在跑的实例共用同一份日志）
# ══════════════════════════════════════════════════════════════════════
_LOG_LOCK = threading.Lock()


def log(msg: str):
    line = "%s %s" % (time.strftime("%H:%M:%S"), msg)
    try:
        print(line, flush=True)
    except Exception:  # noqa: BLE001
        # 无控制台启动时（desktop.exe 走 GUI 子系统 / pythonw）stdout 可能是 None —— 不能因此崩
        pass
    try:
        with _LOG_LOCK:
            with open(LOG_DIR / "desktop.log", "a", encoding="utf-8") as f:
                f.write(time.strftime("%Y-%m-%d ") + line + "\n")
    except OSError:
        pass


def _install_crash_logger():
    """把未捕获异常写进 desktop.log。

    ★ 为什么需要（2026-09-22）：用 `desktop.exe`（GUI 子系统）启动时进程**没有控制台**，
      stderr 可能是 None —— Python 打印 traceback 也没地方去，崩了就等于"双击没反应"，
      一点线索都没有。这里统一落盘（同时保留给真正的 stderr，如果有的话）。
    """
    import traceback

    def _hook(exc_type, exc, tb):
        text = "".join(traceback.format_exception(exc_type, exc, tb))
        try:
            with _LOG_LOCK:
                with open(LOG_DIR / "desktop.log", "a", encoding="utf-8") as f:
                    f.write("%s [未捕获异常] %s\n%s\n"
                            % (time.strftime("%Y-%m-%d %H:%M:%S"), exc_type.__name__, text))
        except OSError:
            pass
        try:
            if sys.stderr is not None:
                sys.stderr.write(text)
        except Exception:  # noqa: BLE001
            pass

    sys.excepthook = _hook


_install_crash_logger()


# ══════════════════════════════════════════════════════════════════════
# JS API（页面上按需调用；现有前端未调用也完全无影响）
# ══════════════════════════════════════════════════════════════════════
class DesktopApi:
    """暴露给页面的原生能力：pywebview.api.<method>()。

    注意：**不实现"代发 HTTP 请求"**——那会把登录 token 搬到 127.0.0.1:PORT 之外的通道上，
    绕过后端自己的鉴权路径。前端该直接 fetch 后端（同源），这里只提供网络做不到的事。
    """

    def notify(self, title: str, message: str = ""):
        """系统通知（周报生成完这类后台事件，比网页 toast 靠谱）。"""
        _notify(title or APP_TITLE, message or "")
        return True

    def set_status(self, text: str = ""):
        """把「正在忙什么」写到托盘提示上（空串=回到空闲）。

        前端在长任务（目前是生成周报）开始/结束时调；窗口收进托盘时用户也能看到进度。
        """
        return _set_tray_status(text)

    def save_text(self, filename: str = "导出.md", content: str = "") -> dict:
        """原生「另存为」对话框写文本文件（导出回答/周报用）。"""
        try:
            p, cancelled = _ask_save_path(filename or "导出.md",
                                          ("Markdown (*.md)", "文本 (*.txt)", "所有文件 (*.*)"))
            if cancelled:
                return {"ok": False, "cancelled": True}
            with open(p, "w", encoding="utf-8") as f:
                f.write(content or "")
            log("[导出] 已保存 %s（%d 字符）" % (p, len(content or "")))
            return {"ok": True, "path": p}
        except Exception as e:  # noqa: BLE001
            log("[导出] 失败：%s" % e)
            return {"ok": False, "error": str(e)}

    def save_binary_b64(self, filename: str = "导出.bin", b64: str = "") -> dict:
        """原生「另存为」写**二进制**文件（导出 PDF 等）。b64 = base64 字符串。

        为什么是 base64：JS 侧拿不到原始字节可直接跨进程传递的形式，base64 是零依赖的通用做法。
        """
        try:
            data = _b64_to_bytes(b64)
            p, cancelled = _ask_save_path(filename or "导出.bin")
            if cancelled:
                return {"ok": False, "cancelled": True}
            err = _write_bytes(p, data)
            if err:
                log("[导出] 写文件失败：%s" % err)
                return {"ok": False, "error": err}
            log("[导出] 已保存 %s（%d 字节）" % (p, len(data)))
            return {"ok": True, "path": p, "bytes": len(data)}
        except Exception as e:  # noqa: BLE001
            log("[导出] 失败：%s" % e)
            return {"ok": False, "error": str(e)}

    def open_output_dir(self, username: str = "") -> dict:
        """在资源管理器里打开导出目录（`output/<账号>/`），打开不了就退到 `output/`。"""
        try:
            base = ROOT / "output"
            target = base
            if username:
                cand = base / username
                if cand.is_dir():
                    target = cand
            if not target.is_dir():
                return {"ok": False, "error": "目录不存在：%s" % target}
            os.startfile(str(target))  # noqa: S606
            log("[导出目录] 已打开 %s" % target)
            return {"ok": True, "path": str(target)}
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": str(e)}

    def open_file(self) -> dict:
        """原生「打开文件」对话框（返回路径，具体上传仍由页面自己 POST）。"""
        try:
            import webview
            win = webview.active_window()
            path = win.create_file_dialog(
                webview.OPEN_DIALOG, directory=_default_save_dir(), allow_multiple=False,
                file_types=("Markdown (*.md)", "文本 (*.txt)", "所有文件 (*.*)"),
            )
            if not path:
                return {"ok": False, "cancelled": True}
            p = path if isinstance(path, str) else path[0]
            return {"ok": True, "path": p}
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": str(e)}

    def open_log(self):
        """打开外壳日志（排查"双击没反应"时用）。"""
        try:
            os.startfile(str(LOG_DIR / "desktop.log"))  # noqa: S606
            return True
        except Exception:
            return False

    def open_backend_log(self):
        """打开后端日志（排查"后端起不来"时用）——这是外壳起的后端子进程的原始输出。"""
        try:
            if not BACKEND_LOG.exists():
                return False
            os.startfile(str(BACKEND_LOG))  # noqa: S606
            return True
        except Exception:
            return False

    def info(self) -> dict:
        """外壳信息（页面可用来显示"桌面版"标识）。"""
        return {
            "app": APP_TITLE,
            "desktop": True,
            "backend_managed": _proc is not None,   # True=服务是本外壳起的
            "pid": _proc.pid if _proc is not None else None,
        }


# ══════════════════════════════════════════════════════════════════════
# 系统通知（Windows 原生气泡；失败静默降级）
# ══════════════════════════════════════════════════════════════════════
def _notify(title: str, message: str):
    def _run():
        # 首选 PowerShell 的 WinForms 气泡（零额外依赖）
        ps = (
            "Add-Type -AssemblyName System.Windows.Forms;"
            "$n=New-Object System.Windows.Forms.NotifyIcon;"
            "$n.Icon=[System.Drawing.SystemIcons]::Information;"
            "$n.Visible=$true;"
            "$n.ShowBalloonTip(6000, $env:ZQK_N_TITLE, $env:ZQK_N_MSG, 'Info');"
            "Start-Sleep -Seconds 7; $n.Dispose()"
        )
        env = _no_proxy_env()
        env["ZQK_N_TITLE"] = title
        env["ZQK_N_MSG"] = message
        try:
            subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                           capture_output=True, env=env, timeout=20,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            log("[通知] %s — %s" % (title, message))
        except Exception as e:  # noqa: BLE001
            log("[通知] 发送失败（忽略）：%s" % e)

    threading.Thread(target=_run, daemon=True).start()


# ══════════════════════════════════════════════════════════════════════
# 托盘（pystray 可选：没装就退化，只打日志，不影响使用）
# ══════════════════════════════════════════════════════════════════════
ICON_PNG = HERE / "assets" / "icon.png"           # 设计稿处理后的大图
ICON_SMALL = HERE / "assets" / "icon-small.png"   # 小尺寸光学补偿版（托盘 16~24px 用这版）
ICON_ICO = HERE / "assets" / "icon.ico"           # 任务栏/窗口要的真 .ico（多尺寸）
ICON_META = HERE / "assets" / "icon-meta.json"    # 设计稿里"橙色圆点"的位置（忙碌指示灯叠它）


def _icon_meta() -> dict:
    try:
        return json.loads(ICON_META.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}


def _make_icon_image(busy: bool = False):
    """托盘图标：优先用设计稿（assets/icon*.png），没有就画个项目主题色圆点。

    `icon-small.png`（小尺寸加强版）是**可选项**：当前没生成它 —— 实测裁边放大到 8% 会把
    圆角切出缺口，得不偿失（见 make_icon.py 的 SMALL_CROP 注释）。有就用、没有就退回原图。

    busy=True 时在**设计稿自带的那个橙点位置**上加亮（白圈 + 琥珀点）——位置从
    `icon-meta.json` 读，所以不会和设计里的圆点错位成"两个点"。
    """
    from PIL import Image, ImageDraw
    size = 64
    src = ICON_SMALL if ICON_SMALL.is_file() else ICON_PNG
    img = None
    if src.is_file():
        try:
            img = Image.open(src).convert("RGBA").resize((size, size), Image.LANCZOS)
        except Exception as e:  # noqa: BLE001  图坏了就用现画的兜底
            log("[托盘] 读图标 %s 失败：%s" % (src.name, e))
            img = None
    if img is None:
        img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        d0 = ImageDraw.Draw(img)
        d0.ellipse((4, 4, size - 4, size - 4), fill=(84, 170, 105, 255))       # 项目主色（松绿）
        d0.ellipse((18, 18, size - 18, size - 18), fill=(255, 255, 255, 235))  # 中心留白 → 像一颗信号点
    if busy:
        meta = _icon_meta()
        dot = meta.get("accent_dot_small" if src is ICON_SMALL else "accent_dot") or {}
        cx = int(float(dot.get("cx", 0.86)) * size)
        cy = int(float(dot.get("cy", 0.86)) * size)
        r = max(6, int(float(dot.get("r", 0.05)) * size * 1.7))     # 比设计稿那个点亮一点、大一点
        r = min(r, size // 3)
        d = ImageDraw.Draw(img)
        d.ellipse((cx - r - 2, cy - r - 2, cx + r + 2, cy + r + 2), fill=(255, 255, 255, 255))  # 白圈描边
        d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=(240, 173, 78, 255))                   # 琥珀点（亮起）
    return img


def _set_app_user_model_id():
    """任务栏身份：不设的话壳会被当成 python.exe 归组（图标与名称都跟着 Python 走）。"""
    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("xieky.zxintel.desktop")
    except Exception as e:  # noqa: BLE001
        log("[窗口] 设置 AppUserModelID 失败（任务栏会沿用 Python 的身份）：%s" % e)


def _tray_title() -> str:
    """托盘提示文字：空闲=应用名；忙碌=应用名 · 状态（鼠标悬停可见）。"""
    return ("%s · %s" % (APP_TITLE, _tray_status)) if _tray_status else APP_TITLE


def _set_tray_status(text: str) -> bool:
    """更新托盘状态（提示文字 + 忙碌小点）。页面通过 pywebview.api.set_status() 调。

    ★ 为什么要它：周报生成要几十秒到几分钟，用户多半把窗口收进托盘/最小化了——
      托盘提示变「正在生成周报…」+ 图标出现琥珀点，就不用盯着窗口猜"它到底在干活吗"。
    """
    global _tray_status
    _tray_status = (text or "").strip()
    title = _tray_title()
    icon = _tray_icon
    if icon is None:
        log("[托盘] 状态 → %s（托盘未启用，仅记录）" % title)
        return False
    try:
        icon.title = title                      # pystray 支持运行时改标题（刷新 tooltip）
    except Exception as e:  # noqa: BLE001
        log("[托盘] 改提示文字失败：%s" % e)
    try:
        icon.icon = _make_icon_image(busy=bool(_tray_status))   # 忙碌时换成带琥珀点的图标
    except Exception as e:  # noqa: BLE001
        log("[托盘] 换忙碌图标失败：%s" % e)
    log("[托盘] 状态 → %s" % title)
    return True


def start_tray(on_show, on_quit) -> bool:
    """起托盘。返回是否成功（没装 pystray 时返回 False）。"""
    global _tray_icon
    try:
        import pystray
        from pystray import Menu, MenuItem
    except ImportError:
        log("[托盘] 未安装 pystray → 跳过托盘（pip install pystray 后可启用）")
        return False
    try:
        _tray_icon = pystray.Icon(
            "zx_intel", _make_icon_image(), _tray_title(),
            menu=Menu(
                MenuItem("显示主窗口", lambda: on_show(), default=True),
                Menu.SEPARATOR,
                MenuItem("立即生成周报", lambda: _tray_run_digest()),
                MenuItem("打开导出目录", lambda: _tray_open_output()),
                MenuItem(lambda item: _tray_account_label(), None, enabled=False),
                Menu.SEPARATOR,
                MenuItem("发送测试通知", lambda: _notify(APP_TITLE, "托盘通知正常工作 ✅")),
                MenuItem("打开日志", lambda: os.startfile(str(LOG_DIR / "desktop.log"))),  # noqa: S606
                MenuItem("打开后端日志", lambda: os.startfile(str(BACKEND_LOG)) if BACKEND_LOG.exists() else None),  # noqa: S606
                Menu.SEPARATOR,
                MenuItem("退出（并关闭本外壳启动的服务）", lambda: on_quit()),
            ),
        )
        threading.Thread(target=_tray_icon.run, daemon=True).start()
        log("[托盘] 已启动（右键图标=菜单，双击=显示窗口，点窗口 X=收进托盘）")
        return True
    except Exception as e:  # noqa: BLE001
        log("[托盘] 启动失败（忽略）：%s" % e)
        return False


# ══════════════════════════════════════════════════════════════════════
# 托盘动作（都通过页面的 hook 走前端既有逻辑，外壳不自己拼 API 请求）
# ══════════════════════════════════════════════════════════════════════
def _page_eval(js_expr: str, default=None):
    """在当前窗口里求值（托盘线程调用，失败一律返回 default）。"""
    try:
        import webview
        win = webview.active_window()
        if win is None:
            return default
        return win.evaluate_js(js_expr)
    except Exception:  # noqa: BLE001
        return default


def _tray_account_label() -> str:
    info = _page_eval("(window.__zxDesktop && window.__zxDesktop.account) ? "
                      "JSON.stringify(window.__zxDesktop.account()) : ''", "")
    try:
        d = json.loads(info) if info else {}
    except Exception:  # noqa: BLE001
        d = {}
    if d.get("token"):
        role = "公司" if d.get("role") == "company" else "个人"
        return "账号：%s（%s）" % (d.get("username") or "?", role)
    return "账号：未登录"


def _tray_run_digest():
    """托盘「立即生成周报」→ 调页面的 __zxDesktop.runDigest()。

    为什么走页面而不是外壳自己发请求：前端已有「订阅选择 + 生成 + 刷新列表 + 提示」的完整逻辑，
    外壳自己拼请求就得重新实现一遍（还会把 token 搬到页面之外）。
    """
    has_hook = _page_eval("!!(window.__zxDesktop && window.__zxDesktop.runDigest)", False)
    if not has_hook:
        _notify(APP_TITLE, "页面还没就绪（或未登录），请先打开主窗口")
        return
    _notify(APP_TITLE, "已开始生成周报，完成后会通知你")
    _page_eval("window.__zxDesktop.runDigest()")
    log("[托盘] 已触发「立即生成周报」")


def _tray_open_output():
    """托盘「打开导出目录」→ 问页面当前账号，再打开 output/<账号>。"""
    uname = _page_eval("(window.__zxDesktop && window.__zxDesktop.account) ? "
                       "(window.__zxDesktop.account().username || '') : ''", "") or ""
    try:
        api = DesktopApi()
        r = api.open_output_dir(uname)
        if not r.get("ok"):
            _notify(APP_TITLE, "打不开导出目录：%s" % r.get("error"))
    except Exception as e:  # noqa: BLE001
        log("[托盘] 打开导出目录失败：%s" % e)


# ══════════════════════════════════════════════════════════════════════
# 单实例锁 + 「第二实例唤起已存窗口」
# ══════════════════════════════════════════════════════════════════════
_LOCK_SOCK = None


def _lock_port(port: int) -> int:
    """锁端口 = 后端端口 + 1000（用另一个端口当锁，简单可靠、不占业务端口）。"""
    return port + LOCK_OFFSET


def notify_existing_instance(port: int, timeout: float = 2.0) -> bool:
    """已有实例在跑？有就**通知它把窗口抬出来**。True=确实通知到了。

    ★ 为什么要有这个（2026-09-22 用户报障）：原来第二次双击只是"直接退出"，用户看到的是
      **双击了却没反应**（尤其窗口收进托盘后再双击）——看起来像程序坏了。现在第二实例会给
      第一实例发一行 `SHOW`，第一实例把窗口 show+restore 抬到前台，然后第二实例自己退出。
    """
    try:
        with socket.create_connection(("127.0.0.1", _lock_port(port)), timeout=timeout) as c:
            c.sendall(b"SHOW\n")
            try:
                c.recv(32)          # 等一句 ack（无 ack 也不影响：对方已收到）
            except OSError:
                pass
        return True
    except OSError:
        return False


def single_instance(port: int, notify: bool = True) -> bool:
    """True=本进程拿到锁（可继续）；False=已有实例在跑（已尝试通知它显示窗口，本进程应退出）。"""
    global _LOCK_SOCK
    if notify and notify_existing_instance(port):
        log("[单实例] 已有桌面实例在运行 → 已通知它把窗口显示出来，本次退出")
        return False
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    # ★ 刻意**不设** SO_REUSEADDR：Windows 上它允许抢占已 bind 的端口，
    #   会让「第二次 bind 应该失败」变成成功 → 单实例锁直接失效。
    try:
        s.bind(("127.0.0.1", _lock_port(port)))
        s.listen(8)
        _LOCK_SOCK = s
        return True
    except OSError:
        s.close()
        return False


def start_lock_listener(on_show) -> None:
    """起一个守护线程监听锁端口：收到 `SHOW` 就把窗口抬到前台。"""
    def _serve():
        sock0 = _LOCK_SOCK
        log("[单实例] 开始监听「显示窗口」请求（锁端口 %s；再次双击 desktop.cmd 会唤出本窗口）"
            % (sock0.getsockname()[1] if sock0 is not None else "?"))
        while not _quitting.is_set():
            sock = _LOCK_SOCK
            if sock is None:
                return
            try:
                conn, _ = sock.accept()
            except OSError:
                return                      # 锁被关掉（退出中）
            try:
                data = conn.recv(64)
                try:
                    conn.sendall(b"OK\n")
                except OSError:
                    pass
            except OSError:
                data = b""
            finally:
                try:
                    conn.close()
                except OSError:
                    pass
            if b"SHOW" in data:
                log("[单实例] 收到「显示窗口」请求 → 唤出窗口")
                try:
                    on_show()
                except Exception as e:  # noqa: BLE001
                    log("[单实例] 唤出窗口失败：%s" % e)
    threading.Thread(target=_serve, daemon=True, name="lock-listener").start()


# ══════════════════════════════════════════════════════════════════════
# 端口选择：自动换（8123 被占就往后试）
# ══════════════════════════════════════════════════════════════════════
def pick_backend_port(start: int, attempts: int = PORT_ATTEMPTS):
    """挑后端端口。返回 (port|None, mode, tried)。

    mode：
      'existing' —— 已有桌面实例（锁端口在监听），**已通知它显示窗口**，调用方应直接退出
      'reuse'    —— 该端口已有 HTTP 服务在跑（多半是用户自己开的）→ 复用它，不起新的
      'new'      —— 该端口空着 → 起自己的后端
      'none'     —— 试过的端口全不可用

    顺序很关键：**先探锁**，否则会把"已有桌面实例"误判成"可复用的服务"从而起第二个窗口。
    """
    tried = []
    for p in range(start, start + max(1, attempts)):
        tried.append(p)
        if notify_existing_instance(p, timeout=0.6):
            return p, "existing", tried
        base = "http://127.0.0.1:%d/" % p
        if http_ok(base, timeout=1.5):
            log("[后端] 检测到 %s 已有服务在跑 → 复用（退出时不会关闭它）" % base)
            return p, "reuse", tried
        # ★ 两个探测互补，缺一不可：
        #   - can_bind：防"只 listen 不 accept 的端口被 http_ok 塞满 backlog → connect 被拒"
        #     这种误判（实测踩过，会在占用端口上硬起后端）
        #   - not port_in_use：防对方 socket 设了 SO_REUSEADDR 时 Windows 允许抢占 bind
        if can_bind(p) and not port_in_use(p):
            if p != start:
                log("[端口] 起始端口 %d 被占用（不响应 HTTP）→ 自动改用 %d" % (start, p))
            return p, "new", tried
        log("[端口] %d 被占用（不响应 HTTP）→ 试下一个" % p)
    return None, "none", tried


# ══════════════════════════════════════════════════════════════════════
# 后端崩溃自动重启
# ══════════════════════════════════════════════════════════════════════
def should_restart(own_backend: bool, quitting: bool, attempts: int, down: bool) -> bool:
    """看护的判定（独立成函数是为了能直接单测）。

    ★ `down` 的语义是**服务不通了**，不是"子进程没了"——因为 venv 的 python.exe 会再起一个
      真解释器跑 uvicorn，Popen 句柄的生死**不代表**服务生死（2026-09-22 实测）。
    ★ 只重启**自己起的**后端：复用用户自己开的服务时，既不该重启它、也不该动它。
    """
    if quitting or not own_backend or not down:
        return False
    return attempts < RESTART_MAX


def start_watchdog(window, port: int, miss_limit: int = 3) -> None:
    """常驻看护：后端服务真的不通了 → 退避重启（最多 RESTART_MAX 次）。

    ★ 判定基准是**服务可不可用**（HTTP），不是子进程句柄：
      venv 的 `python.exe` 是 launcher，真正的 uvicorn 是它的子进程——只盯句柄会把
      "活得好好儿的服务"当成崩溃（实测误判过一次）。
      连续 `miss_limit` 次（约 3×3=9 秒）探测不通才动手，避免网络抖动/GC 停顿误判。
    """
    base = "http://127.0.0.1:%d/" % port

    def _watch():
        attempts = 0
        misses = 0
        announced_down = False
        while not _quitting.is_set():
            time.sleep(WATCH_INTERVAL)
            if _quitting.is_set():
                break
            if not _OWN_BACKEND:
                continue                       # 复用的别人的服务：不介入
            if http_ok(base, timeout=2.0):
                misses = 0
                announced_down = False
                continue
            misses += 1
            if misses < miss_limit:
                continue
            if not should_restart(_OWN_BACKEND, _quitting.is_set(), attempts, True):
                if attempts >= RESTART_MAX and not announced_down:
                    announced_down = True
                    log("[看护] 后端不可用，且自动重启次数用尽（%d 次）→ 不再尝试" % RESTART_MAX)
                    _notify(APP_TITLE, "后端已停止且自动重启未成功，请点「打开后端日志」查看原因")
                continue
            attempts += 1
            delay = RESTART_BACKOFF[min(attempts - 1, len(RESTART_BACKOFF) - 1)]
            log("[看护] ⚠️ 服务已连续 %d 次探测不通 → %.0fs 后自动重启（第 %d/%d 次）"
                % (misses, delay, attempts, RESTART_MAX))
            _notify(APP_TITLE, "后端意外停止，正在自动重启（第 %d 次）…" % attempts)
            time.sleep(delay)
            if _quitting.is_set():
                return
            log("[看护] 正在重启后端…")
            if start_backend(port, timeout=READY_TIMEOUT):
                log("[看护] ✅ 后端已自动恢复")
                _notify(APP_TITLE, "后端已自动恢复（页面即将刷新）")
                time.sleep(1.0)
                try:
                    window.evaluate_js("location.reload()")
                except Exception as e:  # noqa: BLE001
                    log("[看护] 刷新页面失败（不影响后端恢复）：%s" % e)
                attempts = 0                   # 恢复成功 → 计数清零，下次还能再来一轮
                misses = 0
    threading.Thread(target=_watch, daemon=True, name="backend-watchdog").start()


# ══════════════════════════════════════════════════════════════════════
# 自检模式（tests/desktop_window_e2e.py 用它走**真实 main() 路径**）
# ══════════════════════════════════════════════════════════════════════
def _start_selftest(window, url: str, port: int, wait_seconds: int, login: str = "", wipe: bool = False,
                    download: str = "", badtoken: bool = False):
    """等页面就绪 → 采集 DOM/同源/JS API → 打印 SELFTEST_JSON → 自动关窗。

    为什么要这个：此前所有测试都自建窗口，**绕过了 main()**，导致"加载页占位符少传参数"
    这种只在真实启动路径上出现的问题测不出来。自检走完整启动流程。

    可选：login="用户名:密码" 时先在页面上真登录一次（用来验证「记住登录」跨进程持久化），
    wipe=True 时先清掉本地存储（跑基准轮用）。
    """

    def _collect_and_exit():
        base = "http://127.0.0.1:%d/" % port
        result = {"selftest": True, "port": port}
        try:
            # 等后端就绪（窗口此时显示加载页），再等 SPA 挂载
            if not wait_ready(base, timeout=wait_seconds):
                result["error"] = "后端未在 %ds 内就绪" % wait_seconds
                print("SELFTEST_JSON " + json.dumps(result, ensure_ascii=False), flush=True)
                try:
                    window.destroy()
                except Exception:
                    pass
                return
            time.sleep(8)
            result["backend_managed"] = _proc is not None
            result["backend_pid"] = _proc.pid if _proc is not None else None
            result["title"] = window.evaluate_js("document.title")
            result["origin"] = window.evaluate_js("location.origin")
            result["href"] = window.evaluate_js("location.href")
            result["has_vue"] = window.evaluate_js("typeof Vue !== 'undefined'")
            result["app_html_len"] = window.evaluate_js(
                "(document.querySelector('#app')||{}).innerHTML ? document.querySelector('#app').innerHTML.length : 0")
            result["body_head"] = (window.evaluate_js("(document.body.innerText||'').slice(0,60)") or "")
            result["loading_page_gone"] = window.evaluate_js("!document.body.innerText.includes('正在启动本地后端')")

            # ── 桌面适配相关的可观测项 ──
            result["desktop_flag_in_url"] = window.evaluate_js("(location.search||'').indexOf('desktop=1') >= 0")
            result["hook_zxDesktop"] = window.evaluate_js("!!window.__zxDesktop")
            result["hook_runDigest"] = window.evaluate_js("!!(window.__zxDesktop && window.__zxDesktop.runDigest)")
            result["uses_local_storage"] = window.evaluate_js(
                "!!(window.__zxDesktop && window.__zxDesktop.usesLocalStorage)")
            result["has_save_binary"] = window.evaluate_js(
                "!!(window.pywebview && window.pywebview.api && window.pywebview.api.save_binary_b64)")

            # storage 探针（sessionStorage 供对照；localStorage 用来看跨进程持久化）
            window.evaluate_js("sessionStorage.setItem('desktop_probe','ok')")
            result["storage_ok"] = window.evaluate_js("sessionStorage.getItem('desktop_probe')")

            if wipe:
                window.evaluate_js("try{localStorage.clear();sessionStorage.clear()}catch(e){}")
                # ★ 清完必须**刷新**：SPA 在 setup 阶段就把 token 读进内存了，
                #   只清存储不刷新的话页面依旧显示"已登录的工作台"，后面的登录表单
                #   根本找不到（自动化测试会看到 logged_in=true 而存储里没 token 的鬼状态）。
                window.evaluate_js("location.reload()")
                for _ in range(60):                     # 等页面重新挂载 + 登录卡出现（最长 30s）
                    if window.evaluate_js("!!document.querySelector('.login-form input')"):
                        break
                    time.sleep(0.5)
                time.sleep(1.5)                         # 给 Vue 挂载与样式一点时间
                result["wiped"] = True
                result["reloaded_after_wipe"] = True

            if login and ":" in login:
                u, p = login.split(":", 1)
                # 记录表单是否真找到：否则"没找到表单"会伪装成"登录失败"，排查时误导人
                result["login_form"] = window.evaluate_js(
                    "(()=>{const a=document.querySelector(\".login-form input[placeholder*='如 alice']\");"
                    "const b=document.querySelector(\".login-form input[placeholder='密码']\");"
                    "if(!a||!b) return 'no-form';"
                    "a.value=%s;a.dispatchEvent(new Event('input',{bubbles:true}));"
                    "b.value=%s;b.dispatchEvent(new Event('input',{bubbles:true}));return 'filled';})()"
                    % (json.dumps(u), json.dumps(p)))
                time.sleep(0.5)
                result["login_clicked"] = window.evaluate_js(
                    "(()=>{const s=document.querySelector('.login-form button.bubbles');"
                    "if(s) s.click(); return 'clicked';})()")
                for _ in range(20):          # 等进入工作台（最长 ~10s）
                    if window.evaluate_js("document.querySelectorAll('.nav-tab').length > 0"):
                        break
                    time.sleep(0.5)

            result["logged_in"] = window.evaluate_js("document.querySelectorAll('.nav-tab').length > 0")
            result["token_in_local_storage"] = window.evaluate_js("!!localStorage.getItem('zx_token')")
            result["token_in_session_storage"] = window.evaluate_js("!!sessionStorage.getItem('zx_token')")

            # ── 下载/导出落盘探针（走页面真实的 saveFromUrl，验证「点击下载」整条链路）──
            if download:
                tok = window.evaluate_js(
                    "localStorage.getItem('zx_token') || sessionStorage.getItem('zx_token') || ''") or ""
                if not tok:
                    result["download_result"] = "ERR:no-token（未登录则无法取文件）"
                else:
                    from urllib.parse import quote
                    probe_url = "/api/download?token=%s&name=%s" % (tok, quote(download))
                    ev_dl = threading.Event()
                    js = ("window.__zxDesktop.saveFromUrl(%s, %s)"
                          ".then(r=>JSON.stringify(r)).catch(e=>'ERR:'+e)"
                          % (json.dumps(probe_url), json.dumps(download)))
                    window.evaluate_js(js, lambda v: (result.__setitem__("download_result", v), ev_dl.set()))
                    ev_dl.wait(30)
                    if _SAVE_DIR_OVERRIDE:
                        saved = Path(_SAVE_DIR_OVERRIDE) / download
                        try:
                            result["download_saved_bytes"] = saved.stat().st_size
                            result["download_saved_head"] = saved.read_text(
                                encoding="utf-8", errors="replace")[:40]
                        except OSError as e:
                            result["download_saved_error"] = str(e)

            # ── 托盘状态链路（任务二）：页面调 set_status → 外壳状态真的变了 ──
            try:
                window.evaluate_js(
                    "(window.pywebview && window.pywebview.api && window.pywebview.api.set_status)"
                    " ? window.pywebview.api.set_status('自检状态探针') : null")
                time.sleep(0.4)
                result["tray_status_probe"] = _tray_status
                result["tray_status_api_ok"] = (_tray_status == "自检状态探针")
                window.evaluate_js("window.pywebview.api.set_status('')")
                time.sleep(0.3)
                result["tray_status_reset"] = (_tray_status == "")
            except Exception as e:  # noqa: BLE001
                result["tray_status_error"] = str(e)

            # ── 登录态有效性探针（★ 2026-09-23 用户报障修的回归）——
            #    光"进了工作台"不算记住登录：必须能用存着的 token **真的读到数据**。
            #    后端重启后 token 失效的表现就是"界面已登录、数据全是 0"，所以这里如实取一遍。
            _tok_probe = window.evaluate_js(
                "localStorage.getItem('zx_token') || sessionStorage.getItem('zx_token') || ''") or ""
            if _tok_probe:
                _ev_ov = threading.Event()
                _js_ov = ("(async()=>{const r=await fetch('/api/me/overview?token='+encodeURIComponent(%s));"
                          "let n='';try{const d=await r.json();"
                          "n=[(d.interests||[]).length,(d.watchlist||[]).length,(d.products||[]).length].join(',');"
                          "}catch(e){}return r.status+'|'+n;})()" % json.dumps(_tok_probe))
                window.evaluate_js(_js_ov, lambda v: (result.__setitem__("overview", v), _ev_ov.set()))
                _ev_ov.wait(20)
            else:
                result["overview"] = "no-token"

            stamp = time.strftime("%H%M%S")
            result["ls_prev"] = window.evaluate_js("localStorage.getItem('__desktop_probe') || ''")
            window.evaluate_js("localStorage.setItem('__desktop_probe', '%s')" % stamp)
            result["ls_wrote"] = stamp

            result["api_type"] = window.evaluate_js("typeof window.pywebview")
            result["api_methods"] = window.evaluate_js(
                "JSON.stringify(Object.keys((window.pywebview&&window.pywebview.api)||{}))")

            # 异步结果要用 evaluate_js 的 callback（它不 await Promise）
            ev_api, ev_fetch = threading.Event(), threading.Event()
            window.evaluate_js(
                "window.pywebview.api.info().then(r=>JSON.stringify(r)).catch(e=>'ERR:'+e)",
                lambda v: (result.__setitem__("api_info", v), ev_api.set()))
            ev_api.wait(10)
            window.evaluate_js(
                "fetch('/api/weather?token=none&city=chengdu').then(r=>String(r.status)).catch(e=>'ERR:'+e)",
                lambda v: (result.__setitem__("fetch_status", v), ev_fetch.set()))
            ev_fetch.wait(10)

            # ── 失效 token 探针（★ 2026-09-23 用户报障的另一半）──────────────
            # 把 localStorage 里的 token 换成假值 → 刷新 → 必须**回到登录页并提示**，
            # 而不是老代码那样"以为已登录、各接口 401 静默吞掉、数据全是 0"。
            if badtoken:
                # 注意：这里刻意用页面自己的 location.reload()，**不用** window.load_url()——
                # pywebview 的 load_url 走 CoreWebView2，页面已就绪时可能抛
                # "WebViewException: Main window failed to start"（实测踩到）。
                # setTimeout 让它先返回再刷，避免同步刷新把 JS 上下文撕掉导致 evaluate_js 报错。
                window.evaluate_js(
                    "localStorage.setItem('zx_token','ffffffffffffffffffffffffffffffff');"
                    "setTimeout(function(){location.reload();}, 150); true")
                _deadline = time.time() + 30
                _form = False
                while time.time() < _deadline:            # 等页面重新就绪（别盲睡）
                    time.sleep(1.0)
                    try:
                        _form = bool(window.evaluate_js(
                            "!!document.querySelector('input[type=password]')"))
                    except Exception:  # noqa: BLE001
                        _form = False
                    if _form:
                        break
                result["bad_login_form"] = _form
                result["bad_token_left"] = window.evaluate_js("localStorage.getItem('zx_token') || ''")
                result["bad_text"] = window.evaluate_js(
                    "(document.getElementById('app')||document.body).innerText.slice(0,200)")
        except Exception as e:  # noqa: BLE001
            result["error"] = "%s: %s" % (type(e).__name__, e)
        print("SELFTEST_JSON " + json.dumps(result, ensure_ascii=False), flush=True)
        log("[自检] 采集完成，自动关闭窗口")
        try:
            window.destroy()
        except Exception:
            pass

    threading.Thread(target=_collect_and_exit, daemon=True).start()


# ══════════════════════════════════════════════════════════════════════
# 主流程
# ══════════════════════════════════════════════════════════════════════
def main():
    ap = argparse.ArgumentParser(description="%s 桌面版（pywebview 外壳）" % APP_TITLE)
    ap.add_argument("--port", type=int, default=int(os.environ.get("ZQK_DESKTOP_PORT", DEFAULT_PORT)))
    ap.add_argument("--no-tray", action="store_true", help="不起托盘")
    ap.add_argument("--debug", action="store_true", help="打开 devtools")
    ap.add_argument("--selftest", type=int, default=0, metavar="SECONDS",
                    help="自检模式：按正常流程启动，等页面就绪后采集 DOM/同源/JS API 结果、"
                         "打印一行 SELFTEST_JSON 并自动关闭（供 tests/desktop_window_e2e.py 调用）")
    ap.add_argument("--wait", type=int, default=READY_TIMEOUT, metavar="SECONDS",
                    help="等待后端就绪的上限秒数（默认 %d；机器慢可调大，例如 --wait 420）" % READY_TIMEOUT)
    ap.add_argument("--no-close-to-tray", action="store_true",
                    help="点窗口 X 直接退出（默认收进托盘；托盘不可用时自动放行关闭）")
    ap.add_argument("--port-attempts", type=int, default=PORT_ATTEMPTS, metavar="N",
                    help="端口自动换：从起始端口往后最多试几个（默认 %d；被占就自动改用下一个）" % PORT_ATTEMPTS)
    ap.add_argument("--no-restart", action="store_true",
                    help="关闭「后端崩溃自动重启」（默认开启，最多 %d 次）" % RESTART_MAX)
    ap.add_argument("--selftest-login", default="", metavar="USER:PWD",
                    help="自检模式附加：先在页面上真登录一次（验证「记住登录」跨进程持久化）")
    ap.add_argument("--selftest-wipe", action="store_true",
                    help="自检模式附加：先清空本地存储（跑基准轮用）")
    ap.add_argument("--selftest-badtoken", action="store_true",
                    help="自检附加：把 localStorage 里的 token 换成假值再刷新，验证「失效即回登录页」")
    ap.add_argument("--selftest-save-dir", default="", metavar="DIR",
                    help="自检模式附加：原生「另存为」不弹窗、直接写该目录（自动化测试用，避免卡在对话框）")
    ap.add_argument("--selftest-download", default="", metavar="NAME",
                    help="自检模式附加：登录后调页面的 saveFromUrl() 下载 output/<账号>/<NAME>，"
                         "验证「点击下载 → 落盘」整条链路（配合 --selftest-login 与 --selftest-save-dir）")
    ap.add_argument("--storage-path", default="", metavar="DIR",
                    help="WebView 数据目录（localStorage 等）默认 data/desktop_webview；"
                         "**测试必须传自己的临时目录**，否则 --selftest-wipe 会清掉用户真实桌面版的登录态")
    args = ap.parse_args()
    _force_utf8_stdio()          # ★ 必须最早调用：自己的输出里有 ✅/❌，重定向时 GBK 会崩
    global CLOSE_TO_TRAY, _SAVE_DIR_OVERRIDE
    CLOSE_TO_TRAY = not args.no_close_to_tray
    _SAVE_DIR_OVERRIDE = args.selftest_save_dir or ""

    log("=" * 62)
    log("%s 桌面版启动（pid=%d，起始端口 %d）" % (APP_TITLE, os.getpid(), args.port))
    log("解释器：%s" % sys.executable)          # 排查「.cmd 找不到 venv → 用了系统 python」时看这行
    log("工作目录：%s" % ROOT)

    # ── ① 单实例：已有实例就唤出它的窗口；③ 端口被占就自动往后换 ──
    port, mode, tried = pick_backend_port(args.port, attempts=args.port_attempts)
    if mode == "existing":
        log("[单实例] 试过的端口：%s —— 已有桌面实例在跑，已通知它显示窗口" % tried)
        return 0
    if port is None:
        log("[端口] ❌ 试过的端口都不可用：%s" % tried)
        log("[端口] → 换起始端口：desktop.cmd --port 8200")
        log("[端口] → 或查是谁占着：netstat -ano | findstr :%d" % args.port)
        return 3
    if not single_instance(port):
        # 上面探测通过、这里 bind 失败 → 并发双击的竞态，说明另一个实例刚起来
        log("[单实例] 抢锁失败（另一个实例刚启动）→ 本次退出")
        return 0

    base_url = "http://127.0.0.1:%d/" % port
    # ★ 页面 URL 带 ?desktop=1：前端靠它判断"我在桌面壳里"（URL 在 setup 阶段同步可读，
    #   而 window.pywebview 是异步注入的，用 URL 判断才不会踩时序）
    # ★ 再加一个**一次性 nonce**（2026-09-25 桌面端报障修）：URL 每次一模一样的话，WebView2 的
    #   持久化缓存（private_mode=False + 固定 storage_path 的代价）可能直接拿旧 HTML 交差，表现是
    #   「前端改了、网页端可见、桌面端重启也看不到」。换 nonce = 必然缓存未命中。
    #   前端用 /[?&]desktop=1/ 判断，能容忍额外参数；外壳自检也是 indexOf('desktop=1') 子串判断。
    url = "%s?desktop=1&_=%d" % (base_url, int(time.time()))
    log("[端口] 使用 %d（%s）" % (port, {"new": "本外壳自己起后端", "reuse": "复用已有服务"}.get(mode, mode)))

    try:
        import webview
    except ImportError:
        log("缺少 pywebview：请先执行  uv pip install pywebview")
        return 2

    # ★ 后端就绪后才开正式窗口；期间先展示一个带进度的加载页（避免白屏让人觉得卡死）
    # _LOADING_HTML 有 3 个占位符：<title> / logo / 端口
    html_loading = _LOADING_HTML % (APP_TITLE, APP_TITLE, port)
    window = webview.create_window(
        APP_TITLE, html_loading, width=1280, height=820, min_size=(1000, 640),
        js_api=DesktopApi(), text_select=True,
    )
    if args.debug:
        try:
            window.events.loaded += lambda: window.evaluate_js("")
        except Exception:
            pass

    ok = start_backend(port, timeout=args.wait)
    if ok:
        # ★ 把窗口导航到正式 URL（同源后端托管页）—— 等一下让正在加载的 loading 页完全消失
        def _nav_to_real():
            import time as _t
            _t.sleep(0.3)
            try:
                window.load_url(url)
            except Exception:
                pass
        threading.Thread(target=_nav_to_real, daemon=True).start()
    else:
        _notify(APP_TITLE, "后端启动失败，请查看日志")
        log("后端未就绪，退出。")
        stop_backend()
        return 3

    def on_show():
        try:
            window.show()
            window.restore()
        except Exception:  # noqa: BLE001
            pass

    def on_quit():
        _really_quit.set()               # ★ 先在托盘退出路径置位，X 才会真的关窗
        _quitting.set()
        try:
            if _tray_icon is not None:
                _tray_icon.stop()
        except Exception:  # noqa: BLE001
            pass
        try:
            window.destroy()
        except Exception:  # noqa: BLE001
            pass
        stop_backend()

    def on_closing():
        """点窗口 X 的处理：默认收进托盘（与主流桌面应用一致）。返回 False 取消关闭。"""
        tray_active = _tray_icon is not None
        if _should_close_to_tray(tray_active, _really_quit.is_set()):
            log("[窗口] 点 X → 收进托盘（托盘菜单「退出」才真正退出）")
            try:
                window.hide()
            except Exception:  # noqa: BLE001
                pass
            if not getattr(on_closing, "_hinted", False):
                on_closing._hinted = True
                _notify(APP_TITLE, "已收进托盘（右键托盘图标可退出或生成周报）")
            return False
        return True

    try:
        window.events.closing += on_closing
    except Exception as e:  # noqa: BLE001
        log("[窗口] 注册关闭事件失败（点 X 将直接退出）：%s" % e)

    if not args.no_tray:
        start_tray(on_show, on_quit)

    # ① 第二实例双击 → 通过锁端口把本窗口唤到前台
    start_lock_listener(on_show)
    # ④ 后端运行中崩了 → 退避自动重启（只针对本外壳自己起的后端）
    if not args.no_restart:
        start_watchdog(window, port)
    else:
        log("[看护] 已按 --no-restart 关闭自动重启")

    if args.selftest:
        _start_selftest(window, url, port, args.selftest,
                        login=args.selftest_login, wipe=args.selftest_wipe, badtoken=args.selftest_badtoken,
                        download=args.selftest_download)

    log("[窗口] 加载 %s（同源：页面与 API 同端口，登录态/WebSocket 原样可用）" % url)
    # ★ private_mode=False + 固定 storage_path：否则 pywebview 跑在「隐私模式」下，
    #   localStorage 每次进程结束都被清空 → 「记住登录」形同虚设（实测第 2 轮读不到第 1 轮的值）。
    #   storage_path 可覆盖：**测试必须用自己的临时目录**（否则自检里的清存储会顺手清掉
    #   用户真实桌面版的登录态，还会被上一轮的残留 token 干扰）。
    _storage = Path(args.storage_path) if args.storage_path else (ROOT / "data" / "desktop_webview")
    try:
        _storage.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        log("[窗口] 建 webview 数据目录失败（登录态可能不持久）：%s" % e)
    log("[窗口] WebView 数据目录：%s" % _storage)
    # ★ 任务栏身份（不设的话图标/名称都跟着 python.exe 走）
    _set_app_user_model_id()
    # ★ 窗口/任务栏图标：pywebview 文档说 icon 只支持 GTK/QT，但 Windows 的 WinForms 后端
    #   其实实现了（webview/platforms/winforms.py）——不传就从 sys.executable 抠 python.exe 的
    #   图标，这正是"任务栏显示 Python 图标"的原因。传的是真 .ico（System.Drawing.Icon 不认 PNG）。
    _icon = str(ICON_ICO) if ICON_ICO.is_file() else None
    if _icon:
        log("[窗口] 应用图标：%s" % ICON_ICO.name)
    try:
        webview.start(debug=args.debug, private_mode=False, storage_path=str(_storage), icon=_icon)   # 阻塞至窗口关闭
    finally:
        _quitting.set()
        try:
            if _tray_icon is not None:
                _tray_icon.stop()
        except Exception:  # noqa: BLE001
            pass
        stop_backend()
        log("已退出。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
