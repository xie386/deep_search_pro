# -*- coding: utf-8 -*-
"""桌面版「真开窗口 + 真实启动路径」验证（调 `app.py --selftest`）。

⚠️ 为什么调 `--selftest` 而不是自己 create_window：
   上一版测试自建窗口，**绕过了 main()** —— 于是「加载页占位符少传参数 → 启动即 TypeError」
   这类只在真实启动路径上炸的问题测不出来（用户双击时正是这么炸的）。
   现在测试只做三件事：起外壳进程 → 解析它自己打印的 `SELFTEST_JSON` → 断言，
   与用户双击走的是**同一段代码**。

本测试跑**两轮**，专门验证 P0 的「记住登录」：
  第 1 轮：`--selftest-wipe --selftest-login 临时账号:密码` —— 清空存储 → 在页面上真登录
          → 断言「进了工作台」且 token 落在 **localStorage**（不是关窗即丢的 sessionStorage）
  第 2 轮：不带任何登录参数 —— 断言**没登录也直接进工作台**，
          且读到第 1 轮写入的 localStorage 探针值 → 这就是「关掉桌面版再打开，不用重新登录」

同时断言：同源地址（非 file://）/ 加载页已切走 / `?desktop=1` 生效 / `__zxDesktop` hook 可用 /
`save_binary_b64` 存在 / 退出后外壳自起的服务已清理。

用法：
    .venv/Scripts/python.exe tests/desktop_window_e2e.py
"""

import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
APP = ROOT / "front" / "desktop" / "app.py"
_PY = ROOT / ".venv" / "Scripts" / "python.exe"
PY = str(_PY) if _PY.is_file() else sys.executable
sys.path.insert(0, str(ROOT))

# 测试自己也要 UTF-8：输出里有 ✅/❌，被重定向/管道捕获时 GBK 会崩
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass

PASS, FAIL = [], []
SUF = time.strftime("%m%d%H%M%S")
# ★ 用**既有账号**测试（用户 2026-09-22 指定）：不再新建/清理临时账号，
#   免得库里越攒越多的测试账号。尼古喵喵 = 个人账号，output/ 里有真实周报可用。
TEST_USER = os.environ.get("ZX_TEST_USER", "尼古喵喵")
TEST_PWD = os.environ.get("ZX_TEST_PWD", "123456")


def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name)
    print("%s %s%s" % ("  ✅" if cond else "  ❌", name, (" | " + str(extra)) if extra else ""))


def free_port(start=8500):
    for p in range(start, start + 60):
        with socket.socket() as s:
            if s.connect_ex(("127.0.0.1", p)) != 0:
                return p
    raise RuntimeError("no free port")


def child_env():
    """模拟"双击"时的干净环境：清代理/PYTHONPATH，并**故意去掉 UTF-8 变量**。"""
    env = dict(os.environ)
    for k in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy", "PYTHONPATH"):
        env.pop(k, None)
    env["NO_PROXY"] = "127.0.0.1,localhost"
    for k in ("PYTHONUTF8", "PYTHONIOENCODING"):
        env.pop(k, None)
    return env


def run_shell(port, extra_args=(), timeout=420):
    """跑一轮外壳自检，返回 (SELFTEST_JSON dict 或 None, 输出, 耗时)。"""
    t0 = time.time()
    try:
        proc = subprocess.run(
            [PY, str(APP), "--selftest", "120", "--no-tray", "--port", str(port), *extra_args],
            cwd=str(ROOT), env=child_env(), capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=timeout,
        )
        out = (proc.stdout or "") + (proc.stderr or "")
    except subprocess.TimeoutExpired as e:
        out = (e.stdout or "") if isinstance(e.stdout, str) else ""
        out += "\n[TIMEOUT]"
    data = None
    for line in out.splitlines():
        if line.startswith("SELFTEST_JSON "):
            try:
                data = json.loads(line[len("SELFTEST_JSON "):])
            except json.JSONDecodeError:
                pass
    return data, out, time.time() - t0


def account_id_of(username):
    """按用户名取账号 id（回收会话行用；取不到返回 None）。"""
    try:
        from tools.schema_personal import get_personal_conn
        conn = get_personal_conn()
        try:
            row = conn.execute("SELECT id FROM accounts WHERE username=?", (username,)).fetchone()
            return row["id"] if row else None
        finally:
            conn.close()
    except Exception as e:  # noqa: BLE001
        print("  (取账号 id 失败：%s)" % e)
        return None


def account_count():
    """当前账号数（用来证明测试**没有**新建/删除账号）。"""
    try:
        from tools.schema_personal import get_personal_conn
        conn = get_personal_conn()
        try:
            return conn.execute("SELECT COUNT(*) c FROM accounts").fetchone()["c"]
        finally:
            conn.close()
    except Exception as e:  # noqa: BLE001
        print("  (读账号数失败：%s)" % e)
        return None


def pick_probe_report(username):
    """挑一份**已有的**周报来验下载（没有才临时造一份，用完删掉）。

    返回 (文件名, 内容, 是否临时创建)。
    """
    d = ROOT / "output" / username
    try:
        if d.is_dir():
            cands = sorted((p for p in d.glob("*.md") if p.is_file()),
                           key=lambda p: p.stat().st_mtime, reverse=True)
            if cands:
                return cands[0].name, cands[0].read_text(encoding="utf-8", errors="replace"), False
    except OSError as e:
        print("  (找已有周报失败：%s)" % e)
    name = "_桌面自检探针_%s.md" % SUF
    text = "# 桌面自检探针\n\n此文件由测试临时创建，用于验证「点下载 → 原生另存为 → 真落盘」。\n"
    try:
        d.mkdir(parents=True, exist_ok=True)
        (d / name).write_text(text, encoding="utf-8")
        return name, text, True
    except OSError as e:
        print("  (写探针周报失败：%s)" % e)
        return None, "", False


def main():
    port = free_port()
    url = "http://127.0.0.1:%d/" % port
    print("桌面窗口验证（真实启动路径，两轮）：端口 %d，账号 %s（既有账号，不新建）\n" % (port, TEST_USER))

    # ★ 用既有账号：只记录账号数，跑完断言**没变**（证明测试没建号也没删号）
    T_START = time.time()            # 会话行回收基准（只删本次测试产生的）
    acc_before = account_count()

    # 「下载落盘」探针：直接用该账号 output/ 里**已有的周报**（没有才临时造一份）
    probe_name, probe_text, probe_tmp = pick_probe_report(TEST_USER)
    if not probe_name:
        print("找不到可下载的周报，终止")
        return 1
    probe_file = ROOT / "output" / TEST_USER / probe_name
    save_dir = Path(tempfile.mkdtemp(prefix="zx_dl_"))
    # ★ 测试专用的 WebView 数据目录：绝不能用用户真实桌面版那个，
    #   否则 --selftest-wipe 会把用户自己「记住登录」的状态清掉（还会被残留 token 干扰测试）。
    storage_dir = Path(tempfile.mkdtemp(prefix="zx_wv_"))
    real_storage = ROOT / "data" / "desktop_webview"
    real_before = sorted(p.name for p in real_storage.iterdir()) if real_storage.is_dir() else []
    print("   下载探针周报：%s%s" % (probe_name, "（测试临时创建）" if probe_tmp else "（账号里已有）"))
    print("   测试保存目录：%s" % save_dir)
    print("   测试 WebView 数据目录：%s\n" % storage_dir)

    try:
        # ───────────────────── 第 1 轮：清空存储 + 在页面上真登录 ─────────────────────
        print("[第 1 轮] 清空本地存储 → 页面上真登录 → 看登录态落在哪")
        d1, out1, t1 = run_shell(port, ("--selftest-wipe", "--selftest-login", "%s:%s" % (TEST_USER, TEST_PWD),
                                        "--selftest-save-dir", str(save_dir),
                                        "--selftest-download", probe_name,
                                        "--storage-path", str(storage_dir)))
        print("   耗时 %.0fs" % t1)
        if d1 is None:
            check("第 1 轮打印 SELFTEST_JSON", False, out1[-700:])
            print("\n" + "=" * 60)
            print("桌面窗口验证：通过 %d，失败 %d" % (len(PASS), len(FAIL)))
            for f in FAIL:
                print("  -", f)
            return 1
        check("第 1 轮启动路径无异常", "error" not in d1, d1.get("error"))
        # 先确认"诊断前提"成立：清了存储必须刷新过、登录表单真的要能填上、按钮真点到了。
        # 否则后面那些断言可能是在"页面其实没登录"的假前提下通过的。
        check("清存储后已刷新页面（否则 SPA 内存里还留着旧 token，登录表单根本不存在）",
              d1.get("reloaded_after_wipe") is True)
        check("★ 登录表单找到并填写成功", d1.get("login_form") == "filled", d1.get("login_form"))
        check("★ 登录按钮真的点到了", d1.get("login_clicked") == "clicked", d1.get("login_clicked"))
        print("   关键字段：" + json.dumps({k: d1.get(k) for k in
              ("desktop_flag_in_url", "uses_local_storage", "logged_in", "token_in_local_storage",
               "token_in_session_storage", "hook_zxDesktop", "hook_runDigest", "has_save_binary",
               "ls_prev", "ls_wrote")}, ensure_ascii=False))
        check("★ 页面 URL 带 ?desktop=1", d1.get("desktop_flag_in_url") is True)
        check("★ 桌面版切到 localStorage", d1.get("uses_local_storage") is True)
        check("★ 页面上真登录成功（进了工作台）", d1.get("logged_in") is True)
        check("★ 登录态落在 localStorage（关键：sessionStorage 关窗就没了）",
              d1.get("token_in_local_storage") is True)
        check("确认没写进 sessionStorage（否则关窗即丢）",
              d1.get("token_in_session_storage") is False)
        check("__zxDesktop hook 已挂上", d1.get("hook_zxDesktop") is True)
        check("托盘要用的 runDigest hook 可用", d1.get("hook_runDigest") is True)
        check("原生 save_binary_b64 已暴露（导出走另存为）", d1.get("has_save_binary") is True)
        # 图标：真实启动路径里必须走到"应用图标：icon.ico"这一行（否则任务栏还是 Python 图标）
        check("★ 真实启动路径里接上了应用图标（日志有「应用图标：icon.ico」）",
              "应用图标" in (out1 or "") and "icon.ico" in (out1 or ""),
              [ln.strip()[-40:] for ln in (out1 or "").splitlines() if "应用图标" in ln][:1])

        # ───────── 下载落盘：点「下载周报」走的整条链路必须真的写出文件（本次报障点）─────────
        print("\n[下载落盘] 页面的 saveFromUrl → 外壳原生保存 → 检查文件是否真的存在")
        dres = d1.get("download_result")
        print("   download_result = %s" % dres)
        printed = {}
        try:
            printed = json.loads(dres) if isinstance(dres, str) else (dres or {})
        except (json.JSONDecodeError, TypeError):
            printed = {}
        check("★ 下载链路返回 ok（不再是点了没反应）", printed.get("ok") is True, dres)
        check("★ 走的是原生保存分支（native=true，不是浏览器式下载）",
              printed.get("native") is True, dres)
        saved = save_dir / probe_name
        check("★★ 文件真的落盘到保存目录（本次报障的核心）", saved.exists(),
              "期望 %s" % saved)
        if saved.exists():
            got = saved.read_text(encoding="utf-8")
            check("★ 落盘内容与源文件一致（不是空文件/半截文件）", got == probe_text,
                  "拿到 %d 字节：%r" % (len(got), got[:30]))
            check("★ 落盘字节与源文件**二进制完全一致**",
                  saved.read_bytes() == probe_file.read_bytes(),
                  "%d vs %d 字节" % (saved.stat().st_size, probe_file.stat().st_size))
            check("★ 外壳侧也确认写成功（bytes 与源文件一致）",
                  d1.get("download_saved_bytes") == probe_file.stat().st_size,
                  "%s vs 源文件 %s" % (d1.get("download_saved_bytes"), probe_file.stat().st_size))
        else:
            check("★ 落盘内容与源文件一致（不是空文件/半截文件）", False, d1.get("download_saved_error"))
            check("★ 外壳侧也确认写成功（bytes 与源文件一致）", False, "文件不存在")

        # ───────── ★ 登录态有效性：token 必须真能读到数据（后端重启后失效的回归）─────────
        print("\n[登录态有效性] 用页面里存着的 token 拉一次 /api/me/overview")
        ov1 = str(d1.get("overview") or "")
        print("   第 1 轮 overview = %s" % ov1)
        check("★ 第 1 轮能用 token 真读到数据（HTTP 200）", ov1.startswith("200|"), ov1)
        c1 = ov1.split("|", 1)[1] if "|" in ov1 else ""
        check("★ 第 1 轮账号数据非空（兴趣/关注/收藏）",
              bool(c1) and c1 != "0,0,0", c1)

        # ───────────────── 托盘状态链路（任务二：长任务在托盘上也能看到进度） ─────────────────
        print("\n[托盘状态] 页面调 set_status → 外壳状态真的变了")
        check("★ 页面调 pywebview.api.set_status 后外壳状态被改写",
              d1.get("tray_status_api_ok") is True, d1.get("tray_status_probe"))
        check("★ 传空串后回到空闲状态", d1.get("tray_status_reset") is True,
              d1.get("tray_status_error"))

        # ───────────────── 第 2 轮：什么都不做，看是否还登录着（记住登录） ─────────────────
        print("\n[第 2 轮] 不登录、不清存储 → 应该直接进工作台（= 关掉再打开不用重登）")
        # 末尾附带「失效 token」探针：把 localStorage 里的 token 换成假值再刷新，
        # 必须回到登录页并提示（老代码的表现是"以为已登录、数据全是 0"）
        d2, out2, t2 = run_shell(port, ("--storage-path", str(storage_dir),
                                        "--selftest-badtoken"))
        print("   耗时 %.0fs" % t2)
        if d2 is None:
            check("第 2 轮打印 SELFTEST_JSON", False, out2[-700:])
        else:
            check("第 2 轮启动路径无异常", "error" not in d2, d2.get("error"))
            check("★★ 记住登录：第 2 轮未登录也直接进工作台",
                  d2.get("logged_in") is True, "logged_in=%s" % d2.get("logged_in"))
            check("★ localStorage 跨进程持久化（第 2 轮读到第 1 轮写的值）",
                  bool(d1.get("ls_wrote")) and d2.get("ls_prev") == d1.get("ls_wrote"),
                  "round1 wrote=%s, round2 read=%s" % (d1.get("ls_wrote"), d2.get("ls_prev")))
            # ★★ 本次报障的核心回归：第 2 轮是**全新的后端进程**（旧实现下 token 必然失效），
            #    必须仍能用存着的 token 读到**同样的数据**，而不是"进了工作台但全是 0"。
            ov2 = str(d2.get("overview") or "")
            print("   第 2 轮 overview = %s" % ov2)
            check("★★ 第 2 轮（新后端进程）token 仍然有效（HTTP 200，不是 401）",
                  ov2.startswith("200|"), ov2)
            check("★★ 第 2 轮读到的数据与第 1 轮一致（数据不是 0）",
                  ov2.split("|", 1)[1] == c1 and c1 not in ("", "0,0,0"),
                  "round1=%s round2=%s" % (c1, ov2.split("|", 1)[1] if "|" in ov2 else "?"))
            check("第 2 轮仍是桌面模式（?desktop=1 + localStorage）",
                  d2.get("desktop_flag_in_url") is True and d2.get("uses_local_storage") is True)

            # ── ★★ 报障的另一半：token 真失效时不能"假装已登录"（否则又是一堆 0）──
            print("\n[失效登录态] 存一个假 token → 刷新 → 必须回登录页并提示")
            check("★★ 假 token 刷新后回到登录页（出现密码输入框）",
                  d2.get("bad_login_form") is True, d2.get("bad_login_form"))
            check("★★ 失效的 token 被前端清掉（不再留着冒充登录态）",
                  not d2.get("bad_token_left"), repr(d2.get("bad_token_left")))
            _bt = str(d2.get("bad_text") or "")
            check("★★ 页面上给出「登录已失效」的提示（不是静默显示 0）",
                  "失效" in _bt or "重新登录" in _bt, _bt.replace("\n", " ")[:120])

        # ───────────────────────────── 通用项（用第 1 轮数据） ────────────────────────────
        check("窗口标题正确", "智选情报官" in str(d1.get("title")), d1.get("title"))
        check("★ 加载的是后端同源地址（非 file://）",
              str(d1.get("origin", "")).startswith("http://127.0.0.1:%d" % port), d1.get("origin"))
        check("★ 加载页已切走（不再停在启动中提示）", d1.get("loading_page_gone") is True)
        check("Vue 已加载（页面真渲染）", d1.get("has_vue") is True)
        check("SPA 已挂载（#app 有内容）", (d1.get("app_html_len") or 0) > 500, d1.get("app_html_len"))
        check("★ 后端由外壳自己拉起（干净环境也能起）", d1.get("backend_managed") is True,
              "pid=%s" % d1.get("backend_pid"))
        check("window.pywebview 已注入", d1.get("api_type") == "object", d1.get("api_type"))
        check("原生 JS API 方法可见", "notify" in str(d1.get("api_methods")), d1.get("api_methods"))
        check("★ 原生 JS API 可真实调用（info 里 desktop=true）",
              "desktop" in str(d1.get("api_info", "")), d1.get("api_info"))
        check("★ 页面能同源访问后端接口（fetch /api/* 拿到状态码）",
              str(d1.get("fetch_status", "")).isdigit(), d1.get("fetch_status"))

        # 退出后：外壳自己起的服务应当被清理
        time.sleep(2.5)
        op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        gone = True
        try:
            op.open(url, timeout=3).close()
            gone = False
        except Exception:  # noqa: BLE001
            gone = True
        check("★ 退出后外壳自己起的服务已关闭", gone, url)

        # 测试自身的安全性：不能碰用户真实桌面版的 WebView 数据目录
        real_after = sorted(p.name for p in real_storage.iterdir()) if real_storage.is_dir() else []
        check("★ 测试没动用户真实桌面版的存储目录（--storage-path 隔离生效）",
              real_before == real_after, "before=%s after=%s" % (real_before, real_after))
    finally:
        # 只清测试自己造的东西：临时探针周报（如果有）+ 临时保存目录/数据目录
        try:
            if probe_tmp and probe_file and probe_file.exists():
                probe_file.unlink()
        except OSError as e:
            print("  (清理临时探针周报失败：%s)" % e)
        try:
            shutil.rmtree(str(save_dir), ignore_errors=True)
            shutil.rmtree(str(storage_dir), ignore_errors=True)
        except OSError:
            pass
        # ★ 用既有账号的代价检查：账号总数必须**没变**（没建号、也没删号）
        acc_after = account_count()
        check("★ 既没新建也没删除账号（账号总数不变）",
              acc_before is not None and acc_after == acc_before,
              "%s → %s" % (acc_before, acc_after))
        # ★ 会话行同样不留残留（2026-09-23 会话落库后才有的东西）：本轮登录会往 sessions
        #   表写行，测试结束要回收——只删「测试开始之后、这个账号」的行，绝不碰别的。
        try:
            from tools.schema_personal import get_personal_conn
            _aid = account_id_of(TEST_USER)
            _c = get_personal_conn()
            try:
                _n = _c.execute(
                    "SELECT COUNT(*) AS n FROM sessions WHERE account_id=? AND login_at>=?",
                    (_aid, T_START)).fetchone()["n"]
                _c.execute("DELETE FROM sessions WHERE account_id=? AND login_at>=?",
                           (_aid, T_START))
                _c.commit()
            finally:
                _c.close()
            print("  (回收本轮测试登录产生的会话行：%d 行)" % _n)
            check("★ 测试登录产生的会话行已回收（不留残留）", _n >= 1,
                  "account_id=%s 回收 %s 行" % (_aid, _n))
        except Exception as e:  # noqa: BLE001
            print("  (会话行回收失败：%s)" % e)

    print("\n" + "=" * 60)
    print("桌面窗口验证：通过 %d，失败 %d" % (len(PASS), len(FAIL)))
    if FAIL:
        print("失败项：")
        for f in FAIL:
            print("  -", f)
        return 1
    print("全部通过 ✅")
    return 0


if __name__ == "__main__":
    sys.exit(main())
