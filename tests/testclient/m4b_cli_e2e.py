# -*- coding: utf-8 -*-
"""M4b CLI 面板 —— 端到端验证（TestClient 直调真实路由 + 真机 Agent 调用）

覆盖链路：
  1) 登录拿 token
  2) GET  /api/shell/allowed     白名单 9 条 + 沙箱路径 + 说明
  3) POST /api/shell/exec        正常执行（pwd / echo / mkdir / ls / cat / grep）
  4) POST /api/shell/exec        安全矩阵（路径逃逸 / 非白名单 / 管道 / 绝对路径 / curl 写文件）
  5) 外部命令：ffmpeg -version / git status（真实二进制定位）
  6) 审计：data/audit/commands.jsonl 每次调用（含被拒）都有记录
  7) 真机端到端：POST /api/chat 让 Agent 用 run_shell_command 在沙箱建目录 → 磁盘上真出现
  8) GET  /                     首页含 CLI 面板结构（tab / term-out / term-in / 接口名）

运行：
  cd <项目根> && unset PYTHONPATH && .venv/Scripts/python.exe tests/m4b_cli_e2e.py
"""
import json
import os
import sys
from pathlib import Path

# 本机 127.0.0.1:7897 代理会让本地自测串味，测试脚本一律清掉
for _k in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "ALL_PROXY", "all_proxy"):
    os.environ.pop(_k, None)

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient  # noqa: E402

from api.server import app  # noqa: E402

USER = "尼古喵喵"
PWD = "123456"
MARK = "m4b_e2e_probe"          # Agent 端到端要建的沙箱目录名
AUDIT = ROOT / "data" / "audit" / "commands.jsonl"

ok_cnt = [0]
fail = []


def check(label, cond, extra=""):
    if cond:
        ok_cnt[0] += 1
        print("  [OK]   " + label)
    else:
        fail.append(label)
        print("  [FAIL] " + label + ("  |  " + str(extra)[:400] if extra else ""))


def main():
    c = TestClient(app)

    print("\n== 1. 登录 ==")
    r = c.post("/api/login", json={"username": USER, "password": PWD})
    check("POST /api/login 200", r.status_code == 200, r.text[:300])
    if r.status_code != 200:
        return
    token = r.json()["token"]
    print("  token 前缀：" + token[:12] + "...")

    print("\n== 2. GET /api/shell/allowed（白名单） ==")
    r = c.get("/api/shell/allowed", params={"token": token})
    check("GET /api/shell/allowed 200", r.status_code == 200, r.text[:300])
    d = r.json()
    names = [x["name"] for x in d.get("commands", [])]
    print("  白名单：%s" % " / ".join(names))
    check("白名单正好 9 条（3 外部 + 6 内建）", len(names) == 9, names)
    check("含 ffmpeg / git / curl（外部程序）", all(k in names for k in ("ffmpeg", "git", "curl")), names)
    check("含 ls / cat / grep / echo / pwd / mkdir（Python 内建）",
          all(k in names for k in ("ls", "cat", "grep", "echo", "pwd", "mkdir")), names)
    check("python 已从白名单剔除（沙箱后门）", "python" not in names, names)
    check("返回沙箱绝对路径且指向 data/sandbox/", "sandbox" in (d.get("sandbox") or "").replace("\\", "/"),
          d.get("sandbox"))
    check("返回用法说明 notes（面板底部展示）", bool(d.get("notes")), d.get("notes"))
    for x in d.get("commands", []):
        if not all(k in x for k in ("name", "desc", "usage", "kind", "max_runtime")):
            check("白名单项字段完整（前端要渲染 title/按钮）", False, x)
            break
    else:
        check("白名单项字段完整（前端要渲染 title/按钮）", True)

    sandbox = Path(d["sandbox"])

    def exe(line, **kw):
        return c.post("/api/shell/exec", params={"token": token}, json={"command": line}, timeout=120, **kw)

    print("\n== 3. 正常执行（走真实路由） ==")
    r = exe("pwd")
    j = r.json()
    check("POST /api/shell/exec pwd 200 且 ok", r.status_code == 200 and j.get("ok"), r.text[:300])
    check("pwd 输出就是该账号沙箱目录", j.get("stdout", "").strip().replace("/", "\\") == str(sandbox),
          (j.get("stdout"), str(sandbox)))

    r = exe("echo hello 智选情报官")
    j = r.json()
    check("echo 中文原样回显（编码链路 OK）", j.get("ok") and "智选情报官" in j.get("stdout", ""), r.text[:200])

    r = exe("mkdir -p %s/sub" % MARK)
    j = r.json()
    check("mkdir 成功", j.get("ok"), r.text[:300])
    check("目录真实落到磁盘沙箱里", (sandbox / MARK / "sub").is_dir(), str(sandbox / MARK))

    r = exe("ls -a")
    j = r.json()
    check("ls 列出刚建的目录", j.get("ok") and MARK in j.get("stdout", ""), (j.get("stdout") or "")[:300])
    check("ls 隐藏文件开关生效（-a 应能看到 . 开头条目或至少不报错）", j.get("ok"), j.get("error"))

    r = exe("cat README.md")
    j = r.json()
    check("cat 读沙箱内文件", j.get("ok") and "沙箱" in j.get("stdout", ""), (j.get("stdout") or "")[:200])

    r = exe("grep -n 沙箱 README.md")
    j = r.json()
    check("grep 命中并带行号", j.get("ok") and "沙箱" in j.get("stdout", ""), (j.get("stdout") or "")[:200])

    print("\n== 4. 安全矩阵（必须被拒，且不得执行） ==")
    cases = [
        ("cat ../../.env", "相对路径逃逸"),
        ("cat ..\\..\\.env", "Windows 反斜杠逃逸"),
        ("cat C:/Users/ZQK/.ssh/id_rsa", "绝对路径越界"),
        ("cat C:\\Windows\\win.ini", "绝对路径越界（反斜杠）"),
        ("python -c print(1)", "非白名单命令 python"),
        ("rm -rf /", "非白名单命令 rm"),
        ("ls | grep a", "管道不当成 shell 语义"),
        ("curl -o evil.txt https://example.com", "curl 禁写文件（-o）"),
        ("curl -X POST https://example.com", "curl 禁自定义请求方法（-X）"),
        ("git -c core.sshCommand=calc status", "git 禁 -c 注入"),
        ("git --git-dir=C:/Users status", "git 禁改仓库根"),
    ]
    for line, why in cases:
        r = exe(line)
        j = r.json()
        soft = line.startswith("ls |")
        if soft:
            check("被拒 / 未执行：%s（%s）" % (line, why), r.status_code == 200 and not j.get("ok"), r.text[:300])
        else:
            check("被拒：%s（%s）" % (line, why),
                  r.status_code == 200 and not j.get("ok") and bool(j.get("error")), r.text[:300])
    check("越界尝试没有真读到 .env", "OPENAI" not in (exe("cat ../../.env").json().get("stdout") or ""))

    print("\n== 5. 外部程序（真实二进制） ==")
    r = exe("ffmpeg -version")
    j = r.json()
    if "ffmpeg" not in (j.get("error") or "") and j.get("returncode") is not None:
        check("ffmpeg 定位成功并执行（-version）", "ffmpeg version" in (j.get("stdout") or ""), r.text[:200])
    else:
        check("ffmpeg 定位成功并执行（-version）", False, j.get("error"))

    r = exe("git status")
    j = r.json()
    check("git 执行（非仓库返回码也算执行成功链路通）", j.get("returncode") is not None, r.text[:200])

    r = exe("git init")
    j = r.json()
    check("git init 允许（本地子命令）", j.get("ok"), r.text[:200])
    if j.get("ok"):
        r2 = exe("git status")
        check("git status 在 init 后的沙箱里可用", r2.json().get("returncode") == 0, r2.text[:200])

    print("\n== 6. 审计日志 ==")
    check("审计文件存在", AUDIT.is_file(), str(AUDIT))
    if AUDIT.is_file():
        lines = AUDIT.read_text(encoding="utf-8").strip().splitlines()
        tail = [json.loads(x) for x in lines[-30:] if x.strip()]
        check("审计有记录", len(tail) > 0, len(tail))
        check("审计记录含被拒条目（rejected 字段）", any("rejected" in t for t in tail),
              [t.get("rejected") for t in tail if "rejected" in t][:3])
        check("审计记录归属当前账号", any(t.get("user") == USER for t in tail), set(t.get("user") for t in tail))
        check("审计含 cwd（沙箱路径）", all(t.get("cwd") for t in tail), tail[-1])

    print("\n== 7. 真机端到端：Agent 调 run_shell_command（真实大模型，稍慢） ==")
    tprobe = c.post("/api/chat/sessions", params={"token": token}).json()["thread_id"]
    q = ("请调用 run_shell_command 工具，在 CLI 沙箱里创建一个名为 %s 的目录，"
         "然后用 ls 确认它已存在，最后一句话告诉我结果。" % MARK)
    r = c.post("/api/chat", params={"token": token, "thread_id": tprobe, "question": q}, timeout=900)
    check("POST /api/chat 200（真机）", r.status_code == 200, r.text[:400])
    if r.status_code == 200:
        ans = r.json().get("answer", "")
        print("  回答前 160 字：" + ans[:160].replace("\n", " / "))
        check("沙箱目录真的被 Agent 建出来了", (sandbox / MARK).is_dir(), str(sandbox / MARK))
        # 工具调用痕迹（监控里应能看到 shell 相关工具名）
        try:
            mon = c.get("/api/chat/sessions/%s" % tprobe, params={"token": token}).json()
            blob = json.dumps(mon, ensure_ascii=False)
        except Exception as e:  # noqa: BLE001
            blob = str(e)
        check("会话历史里能看到 shell 工具痕迹或目录结果",
              ("run_shell_command" in blob) or (MARK in blob), blob[:200])

    print("\n== 8. 首页结构（CLI 面板） ==")
    r = c.get("/")
    check("GET / 200", r.status_code == 200, r.status_code)
    html = r.text
    for frag in ("CLI 面板", "term-out", "term-in", "/api/shell/exec", "shExec", "shAllowed",
                 "shHist", "term-chip"):
        check("首页含 %s" % frag, frag in html, "")

    # 清理：Agent 建的探针目录（保留 README.md 沙箱本体）
    probe = sandbox / MARK
    if probe.is_dir():
        import shutil
        shutil.rmtree(probe, ignore_errors=True)
        print("\n  已清理探针目录 %s" % probe)

    print("\n================ M4b CLI 面板 e2e 结果 ================")
    print("通过 %d 项，失败 %d 项" % (ok_cnt[0], len(fail)))
    if fail:
        print("失败清单：")
        for f in fail:
            print("  - " + f)
    print("=======================================================")
    sys.exit(1 if fail else 0)


if __name__ == "__main__":
    main()
