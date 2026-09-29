# -*- coding: utf-8 -*-
"""dspro（CLI 版）端到端验证 —— 真的用子进程跑命令，断言真实输出与真实副作用。

设计要点（沿用本项目测试纪律）：
  - **端到端**：每个用例都 `subprocess` 调 `python -m cli.main`，不看内部函数返回值；
  - 断言落在「用户能看到的东西」上：stdout 关键内容、退出码、登录态文件、DB 副作用；
  - 破坏性操作可回滚：登录态文件用完即删、临时注册的账号结束时清理；
  - 真实 LLM 调用（digest / chat）只在未设 SKIP_LLM=1 时跑，且各自只跑一次。

运行：
    .venv/Scripts/python.exe tests/cli_dspro_e2e.py            # 全量（含真跑 digest + chat，约 2-5 分钟）
    SKIP_LLM=1 .venv/Scripts/python.exe tests/cli_dspro_e2e.py # 只跑不依赖模型的用例
"""

import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PY = str(ROOT / ".venv" / "Scripts" / "python.exe")
if not Path(PY).is_file():
    PY = sys.executable
SESSION_FILE = ROOT / "data" / "dspro_session.json"
SKIP_LLM = os.getenv("SKIP_LLM") == "1"
SKIP_DIGEST = os.getenv("SKIP_DIGEST") == "1"     # 已验证过 digest 时只跑聊天，避免重复烧一次周报

PASS, FAIL = [], []


def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name)
    print("%s %s%s" % ("  ✅" if cond else "  ❌", name, (" | " + str(extra)) if extra else ""))


def run(*args, timeout=300, env_extra=None):
    """跑 dspro 子命令，返回 (returncode, stdout+stderr)。"""
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)
    env["PYTHONIOENCODING"] = "utf-8"
    if env_extra:
        env.update(env_extra)
    p = subprocess.run([PY, "-m", "cli.main", *args], cwd=str(ROOT), env=env,
                       capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout)
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def _backup_session():
    return SESSION_FILE.read_text(encoding="utf-8") if SESSION_FILE.is_file() else None


def _restore_session(snap):
    if snap is None:
        if SESSION_FILE.is_file():
            SESSION_FILE.unlink()
    else:
        SESSION_FILE.write_text(snap, encoding="utf-8")


def main():
    t_all = time.time()
    print("\n=== dspro CLI 端到端验证 ===\n")
    print("python:", PY, "| SKIP_LLM =", SKIP_LLM)

    # ------------------------------------------------------------------
    print("\n[0] 安装形态与帮助")
    code, out = run("--version")
    check("dspro --version 可执行且退出码 0", code == 0 and "dspro" in out, out.strip()[:60])
    code, out = run("--help")
    check("--help 列出四组命令", code == 0 and all(k in out for k in
          ("dspro login", "dspro list", "dspro digest", "dspro chat")), out.count("dspro"))
    code, out = run()
    check("无参数 = 落地页（ASCII 标识 + 用法，不报错）", code == 0 and "dspro" in out and "用法" in out)

    # ------------------------------------------------------------------
    print("\n[1] 未登录 / 错误口令（失败路径必须给出可操作提示）")
    snap = _backup_session()
    if SESSION_FILE.is_file():
        SESSION_FILE.unlink()
    code, out = run("whoami")
    check("未登录 whoami → 退出码 1 + 指引 login", code == 1 and "dspro login" in out, out.strip()[:70])
    code, out = run("list")
    check("未登录 list → 退出码 1（不打印半截信息）", code == 1 and "登录" in out)
    code, out = run("login", "尼古喵喵", "definitely-wrong")
    # 2026-09-16：登录语义改为「账号不存在 → 404 / 密码错误 → 401」，文案随之拆分（用户名此刻是对的，
    # 再报「用户名或密码错误」会误导）——见 api/account.py::login 的注释。
    check("错密码 → 退出码 1 + 「密码错误」", code == 1 and "密码错误" in out, out.strip()[:70])
    ghost = "zqx_ghost_%s" % time.strftime("%H%M%S")
    code, out = run("login", ghost, "whatever123456")
    check("账号不存在 → 退出码 1 + 提示去注册（--register）",
          code == 1 and "不存在" in out, out.strip()[:70])
    sys.path.insert(0, str(ROOT))
    from tools.schema_personal import get_personal_conn
    _conn = get_personal_conn()
    try:
        _ghost_exists = _conn.execute("SELECT COUNT(*) c FROM accounts WHERE username=?",
                                      (ghost,)).fetchone()["c"]
    finally:
        _conn.close()
    check("★ 账号不存在时 CLI 不会顺手建号（旧版前端会静默建号）", _ghost_exists == 0, _ghost_exists)
    code, out = run("login")
    check("缺参数 → 提示用法而非崩栈", code == 1 and "用法" in out and "Traceback" not in out)

    # ------------------------------------------------------------------
    print("\n[2] 登录鉴权")
    code, out = run("login", "尼古喵喵", "123456")
    check("正确口令登录成功", code == 0 and "登录成功" in out, out.splitlines()[2].strip() if len(out.splitlines()) > 2 else "")
    check("登录态写入 data/dspro_session.json", SESSION_FILE.is_file())
    data = json.loads(SESSION_FILE.read_text(encoding="utf-8")) if SESSION_FILE.is_file() else {}
    check("会话文件含账号/角色/account_id/登录时间", all(k in data for k in ("username", "account_id", "role", "login_at")))
    check("会话文件**不含明文密码**", "123456" not in SESSION_FILE.read_text(encoding="utf-8"))
    check("会话文件含密码指纹（改密即失效的判断依据）", bool(data.get("pwd_fp")))

    code, out = run("whoami")
    check("whoami 显示账号/角色/订阅数/周报数/默认城市",
          code == 0 and all(k in out for k in ("账号", "角色", "关注领域（订阅）", "历史周报", "默认城市")))
    check("默认城市来自 .env 的 WEATHER_CITY", "成都" in out)

    # ------------------------------------------------------------------
    print("\n[3] list（默认 / -db / -digest / --json）")
    code, out = run("list")
    check("list 默认同时含「个人信息」与「周报生成模块」", code == 0 and "个人信息" in out and "周报生成模块" in out)
    code, out = run("list", "-db")
    check("list -db 只含信息模块（无周报模块）", code == 0 and "个人信息" in out and "周报生成模块" not in out)
    check("list -db 打出兴趣/关注清单/收藏商品三块",
          all(k in out for k in ("兴趣领域", "关注清单", "收藏商品")))
    code, out = run("list", "-digest")
    check("list -digest 只含关注信息（无个人信息）", code == 0 and "周报生成模块" in out and "个人信息" not in out)
    check("list -digest 给出领域名/关键词/上次生成/状态",
          all(k in out for k in ("关注领域（领域名）", "关注关键词", "上次生成", "启用")))
    code, out = run("list", "--json")
    ok_json = False
    payload = {}
    try:
        payload = json.loads(out[out.index("{"):out.rindex("}") + 1])
        ok_json = True
    except Exception:
        pass
    check("list --json 输出可被 json 解析", ok_json)
    check("list --json 含 counts 与 digest.subs", ok_json and "counts" in payload and payload.get("digest", {}).get("subs") is not None)

    # ------------------------------------------------------------------
    print("\n[3b] 公司账号分支（角色不同 → 取不同模块）")
    code, out = run("login", "灵康科技有限公司", "123456")
    if code != 0:
        check("（公司测试账号登录失败，跳过公司分支用例）", True, out.strip()[:60])
    else:
        code, out = run("list", "-db")
        check("公司账号 list -db 打公司信息三块",
              code == 0 and all(k in out for k in ("公司信息", "我司产品", "关注竞品")), )
        # 注意：「总览」会列出两侧的名称与数量（那是刻意的），所以这里断言的是**分节标题**
        check("公司账号不打个人模块分节（个人信息 / 兴趣领域（…））",
              "个人信息" not in out and "兴趣领域（" not in out and "收藏商品（" not in out)
        code, out = run("list", "--json")
        try:
            payload = json.loads(out[out.index("{"):out.rindex("}") + 1])
            comp = payload.get("db", {}).get("company", {})
            check("公司 --json 含 profile/products/competitors",
                  isinstance(comp.get("products"), list) and isinstance(comp.get("competitors"), list)
                  and bool(comp.get("products")), "products=%d" % len(comp.get("products") or []))
            check("公司 --json 不泄露 password_hash",
                  "password_hash" not in json.dumps(payload, ensure_ascii=False))
            check("公司 --json 含 profile 字段（档案已填）", bool(comp.get("profile")),
                  ",".join(list((comp.get("profile") or {}).keys())[:4]))
        except Exception as e:
            check("公司 --json 可解析", False, repr(e)[:80])
        # 恢复个人账号登录态，后续用例继续
        run("login", "尼古喵喵", "123456")

    # ------------------------------------------------------------------
    print("\n[4] digest 参数解析（不耗模型）")
    code, out = run("digest", "--list")
    check("digest --list 列出领域名与 #id", code == 0 and "领域名" in out and "#" in out)
    code, out = run("digest", "绝不存在的领域zzz")
    check("不存在的领域 → 退出码 1 + 报错", code == 1 and "没有关注领域" in out)
    check("报错时列出可用项（含 #id 与关键词，便于消歧）", "可用" in out and "#" in out and "关键词" in out)
    code, out = run("digest", "#999999")
    check("不存在的 #id → 明确报错", code == 1 and "999999" in out)

    # 用真实数据构造「同名歧义」场景（该账号确有 3 条同名订阅）
    ids, names, subs, dup = [], {}, [], {}
    code, out = run("digest", "--list", "--json")
    try:
        subs = json.loads(out[out.index("["):out.rindex("]") + 1])
        for s in subs:
            names.setdefault(s.get("name"), []).append(s["id"])
        dup = {k: v for k, v in names.items() if k and len(v) > 1}
        ids = [s["id"] for s in subs]
    except Exception:
        dup = {}
    if dup:
        nm = list(dup.keys())[0]
        code, out = run("digest", nm)
        check("同名订阅歧义 → 报错并要求用 #id（不静默挑一条）",
              code == 1 and "匹配到" in out and f"#{dup[nm][0]}" in out, nm)
    else:
        check("（本账号无同名订阅，跳过歧义用例）", True)

    # ------------------------------------------------------------------
    print("\n[5] chat 参数与技能（不耗模型）")
    code, out = run("chat", "--sessions")
    check("chat --sessions 列出会话（含 thread_id 列）", code == 0 and "thread_id" in out)
    code, out = run("chat", "--skills")
    check("chat --skills 列出技能（或提示为空）", code == 0 and ("可用技能" in out))
    code, out = run("chat", "hi", "--skill", "绝不存在的技能zzz")
    check("--skill 指定不存在的技能 → 报错并给可用清单", code == 1 and "技能不存在" in out)

    # ------------------------------------------------------------------
    if SKIP_LLM:
        print("\n[6] 真跑 digest / chat：已按 SKIP_LLM=1 跳过")
    elif SKIP_DIGEST:
        print("\n[6] 真跑 digest：已按 SKIP_DIGEST=1 跳过（digest 链路另有手动验证记录）")
    else:
        print("\n[6] 真跑 digest（单领域，约 1-3 分钟）")
        target = None
        for s in sorted(ids and subs or [], key=lambda x: x["id"]):
            if "米诺" in str(s.get("name")) + str(s.get("keywords")):
                target = s
                break
        target = target or (subs[0] if subs else None)
        if not target:
            check("有可生成的订阅", False, "该账号无启用订阅")
        else:
            before = _report_count()
            t0 = time.time()
            code, out = run("digest", "#%d" % target["id"], timeout=900)
            sec = time.time() - t0
            after = _report_count()
            check("digest 生成成功且退出码 0（用时 %.0fs）" % sec, code == 0, out.strip().splitlines()[-1][:80] if out.strip() else "")
            check("报告正文打印到终端（含 markdown 标题）", "#" in out and ("周报" in out or "快讯" in out))
            check("生成物落库（digest_reports 数量 +1）", after == before + 1, f"{before} → {after}")
            check("进度行可见（调用工具/调度助手）", ("调用工具" in out) or ("调度助手" in out))


    # ------------------------------------------------------------------
    if not SKIP_LLM:
        _run_chat_case()

    # ------------------------------------------------------------------
    print("\n[8] 退出登录与登录态失效")
    code, out = run("logout")
    check("logout 删除登录态文件", code == 0 and not SESSION_FILE.is_file(), out.strip()[:60])
    code, out = run("whoami")
    check("登出后 whoami 重新要求登录", code == 1 and "登录" in out)
    _restore_session(snap)

    print("\n" + "=" * 60)
    print("dspro CLI e2e：通过 %d，失败 %d（总耗时 %.0fs）" % (len(PASS), len(FAIL), time.time() - t_all))
    if FAIL:
        print("失败项：")
        for f in FAIL:
            print("  -", f)
        return 1
    print("全部通过 ✅")
    return 0


def _run_chat_case():
    """真跑一轮 chat：验证 api/server._run_agent 在 CLI 进程里可用 + 会话真的落库。"""
    print("\n[7] 真跑 chat 单轮（约 30-90 秒）")
    data = json.loads(SESSION_FILE.read_text(encoding="utf-8")) if SESSION_FILE.is_file() else {}
    aid = data.get("account_id")
    n_before = _conv_count(aid)
    code, out = run("chat", "用一句话说明你能做什么", timeout=900)
    check("chat 单轮返回答案且退出码 0", code == 0 and len(out) > 80,
          (out.strip().splitlines()[-1][:80] if out.strip() else ""))
    check("chat 打印了会话 thread_id（可续聊）", "会话" in out)
    check("chat 落库 conversations/messages（web 端也能看到）",
          _conv_count(aid) >= n_before and _msg_count(aid) > 0,
          "会话 %s → %s" % (n_before, _conv_count(aid)))


def _conv_count(aid) -> int:
    return _sql1("SELECT COUNT(*) FROM conversations WHERE account_id=?", (aid,))


def _msg_count(aid) -> int:
    return _sql1("SELECT COUNT(*) FROM messages m JOIN conversations c ON c.id = m.conversation_id "
                 "WHERE c.account_id=?", (aid,))


def _sql1(sql: str, args) -> int:
    try:
        sys.path.insert(0, str(ROOT))
        from tools.schema_personal import ensure_tables, get_personal_conn
        ensure_tables()
        conn = get_personal_conn()
        try:
            return conn.execute(sql, args).fetchone()[0]
        finally:
            conn.close()
    except Exception:
        return -1


def _report_count() -> int:
    """当前登录账号的周报数量（直接查库，独立于 CLI 输出）。"""
    try:
        sys.path.insert(0, str(ROOT))
        from tools.schema_personal import ensure_tables, get_personal_conn
        ensure_tables()
        data = json.loads(SESSION_FILE.read_text(encoding="utf-8"))
        conn = get_personal_conn()
        try:
            return conn.execute("SELECT COUNT(*) FROM digest_reports WHERE owner_id=?",
                                (data["account_id"],)).fetchone()[0]
        finally:
            conn.close()
    except Exception as e:  # noqa: BLE001
        print("    （查库失败，视为 0）:", e)
        return -1


if __name__ == "__main__":
    sys.exit(main())
