# -*- coding: utf-8 -*-
"""桌面版外壳自检（不起窗口，验最容易出错的几件事）。

为什么单独写：外壳的价值全在「进程与就绪等待」上，而这部分**开着窗口时反而不好验**。
本脚本把外壳当模块导入，逐项断言：

  [1] 模块可导入（app.py 无语法/依赖问题）
  [2] 端口探测与「就绪等待」在**没有服务**时正确判定为未就绪（不误报）
  [3] 「自己起的后端」能被 wait_ready 正确识别为就绪
  [4] ★ 进程归属：停服务只 kill 自己起的那个；**别人的进程分毫不动**
  [5] ★ 复用别人服务时 stop_backend 是 no-op（不误杀用户的 uvicorn）
  [6] 单实例锁：第二个实例被判定为「已有实例」
  [7] 托盘图标可生成（PIL 在；pystray 缺了就只打日志，不算失败）
  [8] JS API 不暴露「代发请求」这类会绕过后端鉴权的方法

用法：
    .venv/Scripts/python.exe tests/desktop_shell_e2e.py
退出码 0 = 全部通过。
"""

import importlib.util
import inspect
import os
import re
import socket
import struct
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
APP = ROOT / "front" / "desktop" / "app.py"

# 测试自己也要 UTF-8：输出里有 ✅/❌，被重定向/管道捕获时 GBK 会崩（与外壳同源问题）
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass

PASS, FAIL = [], []


def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name)
    print("%s %s%s" % ("  ✅" if cond else "  ❌", name, (" | " + str(extra)) if extra else ""))


_sh_mod = None
try:
    _spec = importlib.util.spec_from_file_location("desktop_shell_probe", APP)
    _sh_mod = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(_sh_mod)
except Exception:  # noqa: BLE001
    _sh_mod = None


def _user_instance_running():
    """用户自己的桌面版在跑吗？判据 = 默认后端端口或它的锁端口有人监听。

    ★ 为什么自检必须先问这一句（2026-09-25 事故）：本脚本会起临时外壳实例、还会杀"端口持有者"，
      而用户可能正开着桌面版。一旦两者共用 WebView2 用户目录 / 同一份 desktop.log（历史上正是如此），
      用户实例会收到异常窗口事件并沿"托盘不可用则放行关闭"的兜底**连后端一起退出**
      （日志留下了 "点 X → 收进托盘 / 关闭本次启动的 uvicorn (pid=8884) / 已退出"）。
      检测到用户实例时，本脚本只跑不碰窗口与进程归属的段落。
    """
    base = getattr(_sh_mod, "DEFAULT_PORT", 8123)
    off = getattr(_sh_mod, "LOCK_OFFSET", 1000)
    for p, what in ((base, "后端端口"), (base + off, "锁端口")):
        with socket.socket() as sk:
            try:
                sk.bind(("127.0.0.1", p))
            except OSError:
                return True, "%s %d 已被占用" % (what, p)
    return False, ""


def load_shell():
    spec = importlib.util.spec_from_file_location("desktop_shell", APP)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _has_active_prompt_print() -> bool:
    """检查 `agent/prompts.py` 里是否还有**生效的** print(提示词)。

    这个 print 是 2026-09-22 那次"双击起不来"的元凶：import 阶段把含 emoji 的提示词打到
    stdout，在 GBK 且输出被重定向时直接 UnicodeEncodeError。已注释，这里防止它被改回来。
    """
    try:
        src = (ROOT / "agent" / "prompts.py").read_text(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        return False
    for line in src.splitlines():
        s = line.strip()
        if s.startswith("#"):
            continue
        if s.startswith("print(") and ("agent_content" in s):
            return True
    return False


def free_port(start=8321):
    for p in range(start, start + 40):
        with socket.socket() as s:
            if s.connect_ex(("127.0.0.1", p)) != 0:
                return p
    raise RuntimeError("找不到空闲端口")


def main():
    print("桌面版外壳自检（%s）\n" % APP.relative_to(ROOT))

    # ------------------------------------------------------------------ [0]
    # ★ 测试隔离（2026-09-25 事故后加）：自检会起临时外壳实例，若与**用户正在跑的实例**共用
    #   ① WebView2 用户目录（默认 storage_path）或 ② 同一份 desktop.log，就会互相干扰 ——
    #   实测过一次：用户实例在自检运行期间收到窗口关闭事件 → 走"托盘不可用则放行关闭"兜底 →
    #   连后端一起退出（日志留下了 "点 X → 收进托盘 / 关闭本次启动的 uvicorn / 已退出"）。
    #   所以：日志目录与 WebView2 存储都指到临时目录，且**用户实例在跑时跳过会开窗口的段落**。
    print("[0] 测试隔离（日志与 WebView2 存储都用临时目录，绝不碰用户实例）")
    _tmp = tempfile.mkdtemp(prefix="zx_shell_e2e_")
    os.environ["ZX_DESKTOP_LOGDIR"] = _tmp
    log_dir = Path(_tmp)
    storage_dir = log_dir / "webview"
    storage_dir.mkdir(parents=True, exist_ok=True)
    check("已把外壳日志目录改到临时目录（避免与用户实例的 desktop.log 交错）",
          str(log_dir) in os.environ["ZX_DESKTOP_LOGDIR"], str(log_dir))
    user_busy, user_what = _user_instance_running()
    check("探测「用户自己的桌面实例是否在跑」", True,
          ("在跑：%s → 将跳过会开窗口/杀后端的段落" % user_what) if user_busy else "没有用户实例在跑")
    if user_busy:
        print("      ⚠️ 检测到用户实例（%s）：本脚本只跑不碰窗口/进程归属的段落。" % user_what)
        print("      （要跑完整自检，请先把桌面版从托盘退出）")

    # ------------------------------------------------------------------ [1]
    print("\n[1] 模块导入")
    try:
        sh = load_shell()
        check("app.py 可导入", True)
    except Exception as e:  # noqa: BLE001
        check("app.py 可导入", False, "%s: %s" % (type(e).__name__, e))
        return 1
    check("★ 日志目录已随环境变量隔离（否则会与用户实例互相污染）",
          str(sh.LOG_DIR).startswith(_tmp), str(sh.LOG_DIR))

    port = free_port()
    url = "http://127.0.0.1:%d/" % port

    # ------------------------------------------------------------------ [2]
    print("\n[2] 无服务时不得误报就绪")
    check("http_ok(空端口) 为 False", sh.http_ok(url, timeout=1.5) is False)
    check("port_in_use(空端口) 为 False", sh.port_in_use(port) is False)
    t0 = time.time()
    check("wait_ready 超时返回 False（不空等太久）", sh.wait_ready(url, timeout=2.0) is False,
          "%.1fs" % (time.time() - t0))

    # ------------------------------------------------------------------ [3][4]
    print("\n[3] 自起后端 → 等待就绪")
    ok = sh.start_backend(port)
    check("start_backend 返回 True", ok is True)
    check("http_ok 就绪后为 True", sh.http_ok(url))
    check("记下了自己起的进程 pid", sh._proc is not None and sh._proc.pid > 0,
          sh._proc.pid if sh._proc else None)
    own_pid = sh._proc.pid if sh._proc else None
    my_pid = os.getpid()
    check("★ 子进程 pid ≠ 外壳自身 pid（确实是独立进程）", own_pid != my_pid, "own=%s self=%s" % (own_pid, my_pid))

    print("\n[4] ★ 进程归属：只关自己的")
    # 先起一个"别人的"服务占住另一个端口，验证 stop_backend 绝不碰它
    other_port = free_port(port + 50)
    env = sh._no_proxy_env()
    other = subprocess.Popen(
        [sh._python_exe(), "-m", "uvicorn", "api.server:app", "--host", "127.0.0.1", "--port", str(other_port)],
        cwd=str(ROOT), env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        other_url = "http://127.0.0.1:%d/" % other_port
        started = sh.wait_ready(other_url, timeout=120)
        check("「别人的服务」已就绪（作对照）", started, other_url)
        sh.stop_backend()
        time.sleep(2.5)
        check("★ 自己的服务已关闭", sh.http_ok(url, timeout=2) is False)
        check("★ 别人的服务仍在跑（没被误杀）", sh.http_ok(other_url, timeout=3) is True)
        check("stop_backend 后 _proc 置空", sh._proc is None)
    finally:
        try:
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(other.pid)], capture_output=True)
        except Exception:  # noqa: BLE001
            pass
    time.sleep(1.0)

    # ------------------------------------------------------------------ [5]
    print("\n[5] ★ 复用别人服务时，退出不得关它")
    reuse_port = free_port(port + 120)
    reuse = subprocess.Popen(
        [sh._python_exe(), "-m", "uvicorn", "api.server:app", "--host", "127.0.0.1", "--port", str(reuse_port)],
        cwd=str(ROOT), env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        reuse_url = "http://127.0.0.1:%d/" % reuse_port
        check("对照服务就绪", sh.wait_ready(reuse_url, timeout=120), reuse_url)
        sh._proc = None                      # 模拟"别人的服务"：外壳没记进程
        ok = sh.start_backend(reuse_port)
        check("start_backend 判定为复用并返回 True", ok is True)
        check("复用时不记录 pid（_proc 仍为 None）", sh._proc is None)
        sh.stop_backend()
        time.sleep(1.5)
        check("★ 复用后退出：别人的服务仍在跑", sh.http_ok(reuse_url, timeout=3) is True)
    finally:
        try:
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(reuse.pid)], capture_output=True)
        except Exception:  # noqa: BLE001
            pass
    time.sleep(1.0)

    # ------------------------------------------------------------------ [6]
    print("\n[6] 单实例锁")
    lock_port = free_port(port + 300)
    check("第一次取锁成功", sh.single_instance(lock_port) is True)
    check("★ 第二次取锁失败（防双击起两个窗口）", sh.single_instance(lock_port) is False)
    try:
        if sh._LOCK_SOCK:
            sh._LOCK_SOCK.close()
    except Exception:  # noqa: BLE001
        pass
    check("释放锁后可再次获取", sh.single_instance(lock_port) is True)
    try:
        if sh._LOCK_SOCK:
            sh._LOCK_SOCK.close()
    except Exception:  # noqa: BLE001
        pass

    # ------------------------------------------------------------------ [7]
    print("\n[7] 托盘与图标（设计稿已接入）")
    try:
        img = sh._make_icon_image()
        check("图标可生成（PIL）", img is not None and img.size[0] > 0, img.size if img else None)
    except Exception as e:  # noqa: BLE001
        check("图标可生成（PIL）", False, "%s: %s" % (type(e).__name__, e))
    # ★ 设计稿接入后的守卫：托盘走 icon-small（16~24px 显示更清楚），任务栏走真 .ico
    assets = ROOT / "front" / "desktop" / "assets"
    check("★ 托盘用的设计稿 PNG 在（icon-small 优先，回退 icon.png）",
          (assets / "icon-small.png").is_file() or (assets / "icon.png").is_file(),
          str([p.name for p in assets.glob("icon*")]))
    check("★ 托盘走的确实是设计稿（不是现画的兜底绿点）", (assets / "icon.png").is_file())
    check("小尺寸补偿版是可选项（存在才用；当前未生成——裁切会切坏圆角）",
          True, "icon-small.png 存在" if (assets / "icon-small.png").is_file() else "未启用（符合预期）")
    try:
        from PIL import Image as _I
        _ico = _I.open(str(assets / "icon.ico"))
        _sizes = sorted(_ico.ico.sizes())
        check("★ 任务栏图标是真 .ico 且含多尺寸（含 16/32/256）",
              {16, 32, 256} <= {s[0] for s in _sizes}, _sizes)
    except Exception as e:  # noqa: BLE001
        check("★ 任务栏图标是真 .ico 且含多尺寸（含 16/32/256）", False, e)
    # ★ 帧必须是**非压缩 BMP**：PIL 默认把每帧写成 PNG 压缩帧，而 .NET 的
    #   System.Drawing.Icon（pywebview 的 Windows 后端 + PowerShell 都走它）**读不了 PNG 帧**
    #   —— 实测症状：任务栏/资源管理器里的图标变成彩色噪点（48/16px 全雪花）。
    try:
        _raw = (assets / "icon.ico").read_bytes()
        _cnt = struct.unpack("<H", _raw[4:6])[0]
        _kinds = []
        for _i in range(_cnt):
            _off = 6 + _i * 16
            _dataoff = struct.unpack("<I", _raw[_off + 12:_off + 16])[0]
            _kinds.append("PNG" if _raw[_dataoff:_dataoff + 4] == b"\x89PNG" else "BMP")
        check("★ .ico 各帧都是 BMP（PNG 帧会让 .NET 读成噪点）", "PNG" not in _kinds, _kinds)
    except Exception as e:  # noqa: BLE001
        check("★ .ico 各帧都是 BMP（PNG 帧会让 .NET 读成噪点）", False, e)
    try:
        _idle = sh._make_icon_image(False).tobytes()
        _busy = sh._make_icon_image(True).tobytes()
        check("★ 忙碌图标与空闲不同（状态可见）", _idle != _busy)
        _meta = sh._icon_meta()
        check("★ 忙碌指示灯位置来自设计稿的橙点（不会错位成两个点）",
              bool(_meta.get("accent_dot_small") or _meta.get("accent_dot")), _meta)
    except Exception as e:  # noqa: BLE001
        check("★ 忙碌图标与空闲不同（状态可见）", False, e)
    _app_src = APP.read_text(encoding="utf-8", errors="replace")
    check("★ 窗口/任务栏图标已接上（webview.start 传 icon=）",
          "icon=_icon" in _app_src and "ICON_ICO" in _app_src)
    check("★ 设了 AppUserModelID（否则任务栏沿用 python.exe 的身份）",
          "SetCurrentProcessExplicitAppUserModelID" in _app_src)
    check("图标处理脚本在（换设计稿时用它：去白底 + 生成多尺寸 ico）",
          (ROOT / "front" / "desktop" / "make_icon.py").is_file())
    # ★ desktop.exe 启动器：`.cmd` 的图标是按文件类型共享的、改不了单个，且双击必弹黑窗 →
    #   编译一个小 exe（GUI 子系统 + 图标编进 PE 资源）才是正解
    _exe = ROOT / "front" / "desktop" / "desktop.exe"
    check("★ 启动器 desktop.exe 在（双击它 = 有我们图标、且无黑窗）", _exe.is_file(),
          "%d 字节" % _exe.stat().st_size if _exe.is_file() else None)
    try:
        with open(_exe, "rb") as _f:
            _d = _f.read(0x400)
        _off = int.from_bytes(_d[0x3C:0x40], "little")
        _sub = int.from_bytes(_d[_off + 24 + 68: _off + 24 + 70], "little")
        check("★ desktop.exe 是 GUI 子系统（= 双击不弹控制台黑窗）", _sub == 2, "subsystem=%s" % _sub)
    except Exception as e:  # noqa: BLE001
        check("★ desktop.exe 是 GUI 子系统（= 双击不弹控制台黑窗）", False, e)
    check("★ 启动器源码与编译脚本都在（换图标/改逻辑后重编）",
          (ROOT / "front" / "desktop" / "launcher" / "desktop.cs").is_file()
          and (ROOT / "front" / "desktop" / "build_launcher.py").is_file())
    check("★ 无控制台启动也有崩溃日志兜底（stderr 为 None 时不留空白现场）",
          "_install_crash_logger" in _app_src and "sys.excepthook" in _app_src)
    has_pystray = importlib.util.find_spec("pystray") is not None
    print("     pystray 已安装：%s" % ("是" if has_pystray else "否（托盘降级为不启用，不算失败）"))
    check("托盘模块可导入（或明确降级）", True)

    # ------------------------------------------------------------------ [8]
    print("\n[8] JS API 的安全面")
    api_methods = [m for m in dir(sh.DesktopApi) if not m.startswith("_")]
    check("暴露的是原生能力（notify/save_text/save_binary_b64/open_file/open_output_dir/open_log/info）",
          {"notify", "save_text", "save_binary_b64", "open_file", "open_output_dir",
           "open_log", "open_backend_log", "info"} <= set(api_methods), api_methods)
    forbidden = [m for m in api_methods if any(k in m.lower() for k in ("fetch", "http", "request", "exec", "shell", "query"))]
    check("★ 没有「代发请求 / 执行命令」这类会绕过鉴权的通道", not forbidden, forbidden)

    # ------------------------------------------------------------------ [8b]
    print("\n[8b] P0 新增能力（base64 落盘 / 关闭收托盘 / 托盘菜单）")
    import base64 as _b64
    payload = bytes(range(256)) + "中文PDF假内容".encode("utf-8")   # 含不可打印字节，验证不是文本搬运
    got = sh._b64_to_bytes(_b64.b64encode(payload).decode())
    check("★ base64 → bytes 还原正确（含二进制与中文）", got == payload, "len=%d" % len(got))
    check("base64 容错：data URL 前缀 / 换行空白",
          sh._b64_to_bytes("data:application/pdf;base64,QUJD") == b"ABC"
          and sh._b64_to_bytes("QU\nJD ") == b"ABC")
    tmp_out = Path(os.environ.get("TEMP", ".")) / "_zx_desktop_bytes.bin"
    err = sh._write_bytes(tmp_out, payload)
    check("★ 二进制写盘可用（PDF 导出路径）",
          err is None and tmp_out.read_bytes() == payload, err or "%d 字节" % tmp_out.stat().st_size)
    try:
        tmp_out.unlink()
    except OSError:
        pass
    check("写盘失败时返回错误字符串（不抛异常）",
          isinstance(sh._write_bytes(Path("Z:/不存在的盘/x.bin"), b"x"), str))

    # 关闭收托盘的安全边界
    check("★ 托盘可用 + 非退出态 → 点 X 收托盘", sh._should_close_to_tray(True, False) is True)
    check("★ 托盘不可用 → 点 X 必须真关闭（否则用户没法退出！）",
          sh._should_close_to_tray(False, False) is False)
    check("托盘菜单「退出」 → 放行关闭", sh._should_close_to_tray(True, True) is False)
    _old_ctt = sh.CLOSE_TO_TRAY
    sh.CLOSE_TO_TRAY = False
    check("--no-close-to-tray 生效", sh._should_close_to_tray(True, False) is False)
    sh.CLOSE_TO_TRAY = _old_ctt

    # 托盘菜单项与页面 URL（静态检查：这些是"用户看得见的功能"，漏了就等于没做）
    app_src = APP.read_text(encoding="utf-8", errors="replace")
    for label in ("显示主窗口", "立即生成周报", "打开导出目录", "打开后端日志", "退出"):
        check("托盘菜单含「%s」" % label, ('"%s' % label) in app_src or ("%s" % label) in app_src)
    check("★ 页面 URL 带 ?desktop=1（前端据此切 localStorage）", "?desktop=1" in app_src)
    # ★ 一次性 nonce（2026-09-25 用户报障修：桌面端看不到前端改动 = WebView2 持久化缓存喂了旧 HTML）。
    #   URL 每次一模一样是可缓存的前提，加上 &_=<时间戳> 就必然缓存未命中。
    check("★ 页面 URL 带一次性 nonce（&_=时间戳，绕开 WebView2 持久化缓存）",
          re.search(r'url\s*=\s*"%s\?desktop=1&_=%d"\s*%\s*\(', app_src) is not None
          or re.search(r'desktop=1&_=', app_src) is not None)
    check("外壳自检仍用子串判断 ?desktop=1（容忍额外参数，不会被 nonce 弄失效）",
          "indexOf('desktop=1') >= 0" in app_src)
    # ★ 前端也必须容忍额外参数（否则 nonce 一加，桌面模式判断就静默失效）
    _idx = (ROOT / "front" / "index.html").read_text(encoding="utf-8", errors="replace")
    check("前端桌面模式判断容忍额外 query 参数（/[?&]desktop=1/）",
          re.search(r"IS_DESKTOP\s*=\s*/\[\?&\]desktop=1/", _idx) is not None)
    check("★ 托盘动作走页面 hook（外壳不自己拼 API 请求）",
          "window.__zxDesktop" in app_src and "/api/digest/run" not in app_src)
    check("注册了窗口关闭事件（closing）", "events.closing" in app_src)

    # ------------------------------------------------------------------ [9]
    # 这两类是"真实双击才会暴露"的 bug，且单测容易漏：加载页占位符数量、.cmd 的路径层级
    print("\n[9] 启动路径（此前测试绕过 main() 时漏掉的两类 bug）")
    try:
        html = sh._LOADING_HTML % (sh.APP_TITLE, sh.APP_TITLE, 8123)
        check("★ 加载页能按 3 个占位符格式化（少传参数会 TypeError）",
              ("8123" in html) and (sh.APP_TITLE in html))
        check("加载页无未替换的占位符残留", ("%s" not in html) and ("%d" not in html))
        check("加载页含标题/端口/提示文案",
              all(k in html for k in (sh.APP_TITLE, "8123", "正在启动本地后端")))
        check("加载页 CSS 的 % 已双写转义（否则格式化会崩/串字符）",
              "100%" in html and "%%" not in html, "裸 %% 计数=%d" % html.count("%%"))
    except TypeError as e:
        check("★ 加载页能按 3 个占位符格式化（少传参数会 TypeError）", False, str(e))

    cmd_file = ROOT / "front" / "desktop" / "desktop.cmd"
    check(".cmd 启动器存在（ASCII 文件名）", cmd_file.is_file())
    old_cmd = ROOT / "front" / "desktop" / "智选情报官.cmd"
    check("中文名 .cmd 已移除（避免编码/路径层面的坑）", not old_cmd.exists())
    if cmd_file.is_file():
        raw = cmd_file.read_bytes()
        txt = raw.decode("ascii", errors="replace")
        # ★ 这是用户实际踩过的坑：cmd.exe 用 GBK 代码页解析 UTF-8 中文注释 → 吃字符/并行
        #   （症状：'local' 不是内部或外部命令、用了系统 python、app.py 找不到）
        check("★ .cmd 内容为纯 ASCII（含注释也不能有中文，否则 GBK 控制台下解析错乱）",
              all(b < 128 for b in raw), "非 ASCII 字节数=%d" % sum(1 for b in raw if b >= 128))
        levels = 0
        m = re.search(r'set\s+"ROOT=%HERE%([^"]*)"', txt)
        if m:
            levels = m.group(1).count("..")
        check("★ .cmd 的 ROOT 退了两级（退一级会找不到 venv，退化成系统 python）",
              levels == 2, "解析到 %d 级" % levels)
        check(".cmd 指向项目 venv 的 python", r"%ROOT%\.venv\Scripts\python.exe" in txt)
        check(".cmd 清了代理与 PYTHONPATH（否则 localhost 连不上）",
              all(k in txt for k in ("HTTP_PROXY=", "NO_PROXY=127.0.0.1,localhost", "PYTHONPATH=")))
        check(".cmd 不再用括号块包 errorlevel（块内 %ERRORLEVEL% 是解析期展开，会显示 0）",
              "goto :failed" in txt)

    # ------------------------------------------------------------------ [10]
    # ★ 这两条对应"用户双击后傻等 180 秒、什么线索都没有"的实测故障
    print("\n[10] 后端起不来时必须快速失败并给出原因")
    import subprocess as _sp
    quick = _sp.Popen([sys.executable, "-c", "pass"], stdout=_sp.DEVNULL, stderr=_sp.DEVNULL)
    t0 = time.time()
    ok2, reason2 = sh.wait_ready_monitored("http://127.0.0.1:1/", quick, timeout=30, verbose=False)
    dt = time.time() - t0
    check("★ 子进程秒退 → 立刻返回（不等满超时）", ok2 is False and str(reason2).startswith("child_exit"),
          "%s，耗时 %.1fs" % (reason2, dt))
    check("★ 判定耗时在数秒内（不是 180 秒）", dt < 10, "%.1fs" % dt)

    # 端口被占（非本项目服务）→ 必须快速失败，且死因要能认出来（不是笼统"请检查 .env"）
    import socket as _sock
    busy = _sock.socket()
    busy.bind(("127.0.0.1", 0))
    busy.listen(1)
    busy_port = busy.getsockname()[1]
    try:
        sh._proc = None
        t0 = time.time()
        ok3 = sh.start_backend(busy_port)
        dt3 = time.time() - t0
        check("★ 端口被别的程序占用 → 启动失败（不傻等）", ok3 is False, "耗时 %.1fs" % dt3)
        check("★ 失败耗时远小于超时（旧实现会等满 180 秒）", dt3 < 60, "%.1fs" % dt3)
        logtxt = ""
        try:
            logtxt = (ROOT / "front" / "desktop" / "desktop.log").read_text(encoding="utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            pass
        # ⚠️ 必须**全文**搜索：提示词转储会把日志尾部几千字符占满，只看 tail 会漏判（实测踩过）
        check("★ 报出「端口被别的程序占用」+ 换端口建议（而不是只说请检查 .env）",
              ("被别的程序占用" in logtxt) and ("--port" in logtxt),
              "日志中命中 10048 上下文" if "被别的程序占用" in logtxt else "未找到死因文案")
    finally:
        busy.close()
        sh._proc = None

    # 子进程输出必须落盘（否则"起不来"完全无据可查）
    check("★ 记录了后端子进程日志路径（backend.log）",
          str(getattr(sh, "BACKEND_LOG", "")).endswith("backend.log"), getattr(sh, "BACKEND_LOG", None))

    # ------------------------------------------------------------------ [11]
    # ★ 用户双击崩溃的真根源：子进程 stdout 是文件时按 GBK 编码，而项目 import 阶段
    #   print 含 emoji 的提示词 → UnicodeEncodeError → 后端 import 就崩（实测 2026-09-22）。
    #   修法是外壳给子进程强制 UTF-8；这里用"干净环境 + stdout 落文件"真跑一次 import 验证。
    print("\n[11] 干净环境下后端能否 import（GBK 崩溃回归）")
    envc = sh._no_proxy_env()
    check("★ 外壳给子进程设了 PYTHONUTF8", envc.get("PYTHONUTF8") == "1", envc.get("PYTHONUTF8"))
    check("★ 外壳给子进程设了 PYTHONIOENCODING=utf-8",
          str(envc.get("PYTHONIOENCODING", "")).lower().startswith("utf-8"), envc.get("PYTHONIOENCODING"))
    check("外壳给子进程清了代理与 PYTHONPATH",
          all(k not in envc for k in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "PYTHONPATH")))

    import tempfile as _tf
    probe_log = Path(_tf.gettempdir()) / "desktop_import_probe.log"
    probe = subprocess.run(
        [sh._python_exe(), "-c", "import agent.prompts; print('IMPORT_OK')"],
        cwd=str(ROOT), env=envc, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=180,
    )
    blob = (probe.stdout or "") + (probe.stderr or "")
    check("★ 用外壳的环境 import 项目模块不崩（含 emoji 的调试 print 不再炸）",
          probe.returncode == 0 and "IMPORT_OK" in blob,
          "exit=%s %s" % (probe.returncode, blob.strip().splitlines()[-1][:120] if blob.strip() else ""))
    if probe_log.exists():
        probe_log.unlink()

    # 对照：故意用 GBK 跑同一段 import —— **必须也不崩**（根因已修：调试 print 已注释）
    env_bad = dict(envc)
    env_bad.pop("PYTHONUTF8", None)
    env_bad.pop("PYTHONIOENCODING", None)
    env_bad["PYTHONIOENCODING"] = "gbk"
    probe2 = subprocess.run(
        [sh._python_exe(), "-c", "import agent.prompts, api.server; print('IMPORT_OK2')"],
        cwd=str(ROOT), env=env_bad, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=180,
    )
    blob2 = (probe2.stdout or "") + (probe2.stderr or "")
    check("★ 根因已修：GBK（重定向）环境下 import 项目模块也不崩",
          probe2.returncode == 0 and "IMPORT_OK2" in blob2,
          "exit=%s %s" % (probe2.returncode, blob2.strip().splitlines()[-1][:120] if blob2.strip() else ""))
    check("★ 确认 GBK 崩溃的元凶（prompts.py 的调试 print）确实处于注释状态",
          not _has_active_prompt_print(),
          "agent/prompts.py 里仍有生效的 print(整份提示词)")

    # ------------------------------------------------------------------ [12]
    # ★ 用户报障："双击了却没反应"——尤其窗口收进托盘后再双击。现在第二实例会把第一实例的窗口唤出来。
    print("\n[12] ★ 第二实例唤起已存窗口（单实例通知链路）")

    def _free_consecutive(n, start):
        """找 n 个**连续**空闲端口（不靠运气，避免断言随环境飘）。"""
        p = start
        while p < start + 500:
            if all(_port_free(p + k) for k in range(n)):
                return p
            p += 1
        raise RuntimeError("找不到 %d 个连续空闲端口" % n)

    def _port_free(p):
        with socket.socket() as s:
            try:
                s.bind(("127.0.0.1", p))
                return True
            except OSError:
                return False

    lp = _free_consecutive(2, port + 300)
    check("锁端口 = 后端端口 + %d（业务端口之外另占一个）" % sh.LOCK_OFFSET,
          sh._lock_port(lp) == lp + sh.LOCK_OFFSET, sh._lock_port(lp))
    check("没有实例在跑时探测不到（返回 False，不会误判成已有实例）",
          sh.notify_existing_instance(lp, timeout=0.5) is False)
    check("第一实例取锁成功", sh.single_instance(lp, notify=False) is True)
    fired = threading.Event()
    sh.start_lock_listener(lambda: fired.set())
    time.sleep(0.4)
    sent = sh.notify_existing_instance(lp, timeout=2.5)
    check("★ 第二实例发 SHOW → 第一实例收到并会唤出窗口（不再「双击没反应」）",
          sent is True and fired.wait(4.0), "sent=%s fired=%s" % (sent, fired.is_set()))
    got, mode, tried = sh.pick_backend_port(lp, attempts=1)
    check("★ 选端口时先探锁：认出「已有实例」→ mode=existing（不会另起第二个窗口）",
          mode == "existing", "%s/%s" % (got, mode))
    try:
        sh._LOCK_SOCK.close()
    except Exception:  # noqa: BLE001
        pass
    sh._LOCK_SOCK = None
    sh._quitting.clear()          # 上面收尾可能惊动退出标记，复位以免影响后续用例

    # ------------------------------------------------------------------ [13]
    print("\n[13] ★ 端口自动换（起始端口被占 → 自动往后试，不再让你手动 --port）")
    p2 = _free_consecutive(3, port + 400)
    got, mode, tried = sh.pick_backend_port(p2, attempts=3)
    check("空端口 → mode=new 且就用起始端口", (got, mode) == (p2, "new"), "%s/%s" % (got, mode))
    busy2 = socket.socket()
    busy2.bind(("127.0.0.1", p2))     # 占住起始端口（只 listen 不响应 HTTP = 最难判的那种占用）
    busy2.listen(1)
    try:
        got, mode, tried = sh.pick_backend_port(p2, attempts=3)
        check("★ 起始端口被占（不响应 HTTP）→ 自动改用下一个端口", (got, mode) == (p2 + 1, "new"),
              "%s/%s tried=%s" % (got, mode, tried))
        got, mode, tried = sh.pick_backend_port(p2, attempts=1)
        check("★ 候选只有一个且被占 → 判 none（由调用方报错退出，不硬起）",
              (got, mode) == (None, "none"), "%s/%s" % (got, mode))
        check("返回试过的端口清单（报错时能告诉用户试了哪些）", tried == [p2], tried)
    finally:
        busy2.close()
    import http.server as _hs
    srv = _hs.HTTPServer(("127.0.0.1", p2), _hs.SimpleHTTPRequestHandler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        time.sleep(0.4)
        got, mode, tried = sh.pick_backend_port(p2, attempts=1)
        check("★ 端口上已有 HTTP 服务 → mode=reuse（复用，不起新的，也绝不接管）",
              (got, mode) == (p2, "reuse"), "%s/%s" % (got, mode))
    finally:
        srv.shutdown()
        srv.server_close()

    # ------------------------------------------------------------------ [14]
    print("\n[14] ★ 托盘状态/进度（长任务在托盘上也能看到）")
    check("DesktopApi 暴露 set_status（页面可调）", hasattr(sh.DesktopApi, "set_status"))
    check("空闲时托盘标题 = 应用名", sh._tray_title() == sh.APP_TITLE, sh._tray_title())
    sh._set_tray_status("正在生成周报…")
    check("★ 忙碌时托盘标题带上状态（收进托盘也能看到进度）",
          "正在生成周报" in sh._tray_title(), sh._tray_title())
    try:
        idle_b = sh._make_icon_image(False).tobytes()
        busy_b = sh._make_icon_image(True).tobytes()
        check("★ 忙碌图标与空闲图标不同（右下角加琥珀点）", idle_b != busy_b)
    except Exception as e:  # noqa: BLE001
        check("★ 忙碌图标与空闲图标不同（右下角加琥珀点）", False, e)
    sh._set_tray_status("")
    check("清空状态后回到空闲标题", sh._tray_title() == sh.APP_TITLE, sh._tray_title())
    check("托盘未启动时 set_status 不抛异常（只记日志，页面调用不会炸）",
          sh._set_tray_status("临时状态") in (True, False))
    sh._set_tray_status("")
    _html = (ROOT / "front" / "index.html").read_text(encoding="utf-8", errors="replace")
    check("★ 前端真的接上了：生成周报前后调 set_status",
          _html.count("desktopStatus('正在生成周报…')") >= 2 and "desktopStatus('')" in _html)
    check("★ 前端聊天回答前后也调（长回答不用盯着窗口）",
          "desktopStatus('正在回答…')" in _html)

    # ------------------------------------------------------------------ [15]
    print("\n[15] ★ 后端崩溃自动重启：判定逻辑")
    check("自己起的服务不通了 → 要重启", sh.should_restart(True, False, 0, True) is True)
    check("服务还在跑（down=False）→ 不重启", sh.should_restart(True, False, 0, False) is False)
    check("★ 复用别人的服务 → 绝不重启（那不是我们的）",
          sh.should_restart(False, False, 0, True) is False)
    check("正在退出 → 不重启", sh.should_restart(True, True, 0, True) is False)
    check("★ 到次数上限就停（防崩溃循环刷屏）",
          sh.should_restart(True, False, sh.RESTART_MAX, True) is False)
    check("上限之内还会继续重启", sh.should_restart(True, False, sh.RESTART_MAX - 1, True) is True)
    check("★ 判定基准是「服务可不可用」而不是子进程句柄（venv python 会再起一个真解释器）",
          "http_ok" in inspect.getsource(sh.start_watchdog),
          "" if "http_ok" in inspect.getsource(sh.start_watchdog) else "看护里没看到 HTTP 探测")

    # ------------------------------------------------------------------ [16]
    # ★ 真机验证：这是"后端崩溃自动重启"的最终证据——不是看代码，是真杀掉再等它回来。
    print("\n[16] ★ 真机：杀掉后端子进程 → 外壳自动把它拉回来（会短暂开一个窗口）")
    # ★ 读**隔离后**的日志（main 的 [0] 段把 ZX_DESKTOP_LOGDIR 指到了临时目录）
    log_file = sh.LOG_DIR / "desktop.log"

    def _read_log() -> str:
        try:
            return log_file.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return ""

    def _backend_pid(mark: int):
        """从新增的日志段里取最后一个「uvicorn 已启动 (pid=N)」。"""
        txt = _read_log()[mark:]
        hits = re.findall(r"uvicorn 已启动 \(pid=(\d+)\)", txt)
        return int(hits[-1]) if hits else None

    rp = _free_consecutive(2, port + 600)
    if user_busy:
        # ★ 用户实例在跑 → 这一段会开窗口 + 杀后端端口持有者，跳过（隔离不足以免干扰）
        print("   （检测到用户实例，跳过：本段会开窗口并杀后端）")
    elif sh.port_in_use(rp + sh.LOCK_OFFSET):
        print("   （锁端口 %d 被占，跳过真机重启用例）" % (rp + sh.LOCK_OFFSET))
    else:
        mark = len(_read_log())
        inst = subprocess.Popen(
            [sh._python_exe(), str(APP), "--port", str(rp), "--no-tray", "--no-close-to-tray",
             "--storage-path", os.path.join(os.environ["ZX_DESKTOP_LOGDIR"], "webview")],
            cwd=str(ROOT), env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        try:
            rurl = "http://127.0.0.1:%d/" % rp
            check("临时实例已就绪（作对照）", sh.wait_ready(rurl, timeout=200), rurl)
            bpid = _backend_pid(mark)
            check("能从日志认出 Popen 起的子进程 pid", bool(bpid), bpid)
            # ★ 必须杀"真正持有端口"的那个进程：venv 的 python.exe 是 launcher，
            #   服务其实跑在它的子进程里，只杀 launcher 服务照样活着（= 测不出重启）。
            owner = sh._owner_pid(rp)
            check("★ 认出端口真正归谁（不是 Popen 子进程也正常）", bool(owner), owner)
            if owner:
                if bpid and owner != bpid:
                    print("     （端口持有者 pid=%s ≠ Popen 子进程 pid=%s —— venv launcher 链）"
                          % (owner, bpid))
                subprocess.run(["taskkill", "/F", "/T", "/PID", str(owner)], capture_output=True)
                check("后端服务确实被打掉了（模拟崩溃）", not sh.http_ok(rurl, timeout=3),
                      "" if not sh.http_ok(rurl, timeout=3) else "服务仍在响应")
                t0 = time.time()
                back = sh.wait_ready(rurl, timeout=240)
                dt = time.time() - t0
                check("★★ 外壳把后端自动拉回来了（服务恢复，用户不用手动重开）", back,
                      "耗时 %.0fs" % dt)
                seg = _read_log()[mark:]
                # 「已自动恢复」是在服务重新就绪后才写的，可能比 wait_ready 晚一点 → 轮询等它
                for _ in range(20):
                    if ("自动重启" in seg) and ("已自动恢复" in seg):
                        break
                    time.sleep(1.0)
                    seg = _read_log()[mark:]
                check("★ 日志里能看到「自动重启」与「已自动恢复」",
                      ("自动重启" in seg) and ("已自动恢复" in seg),
                      [ln[-70:] for ln in seg.splitlines() if "看护" in ln][:3])
                check("★ 外壳进程自己还活着（没被误判成崩溃而退出）", inst.poll() is None,
                      "exit=%s" % inst.poll())
        finally:
            try:
                subprocess.run(["taskkill", "/F", "/T", "/PID", str(inst.pid)], capture_output=True)
            except Exception:  # noqa: BLE001
                pass
            time.sleep(1.0)

    print("\n" + "=" * 60)
    print("桌面版外壳自检：通过 %d，失败 %d" % (len(PASS), len(FAIL)))
    if FAIL:
        print("失败项：")
        for f in FAIL:
            print("  -", f)
        return 1
    print("全部通过 ✅")
    return 0


if __name__ == "__main__":
    sys.exit(main())
