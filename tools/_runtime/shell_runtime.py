"""Shell 能力层：白名单命令执行器（CLI 面板 + Agent 工具共用同一套执行器）。

设计来源：`docs/v2.0/M4技能与工具.md` 附录 A（2026-09-10 决策记录修订版）。

安全模型（六道闸）：
  1. **白名单**：`ffmpeg / git / curl` 调外部程序；`ls / cat / grep / echo / pwd / mkdir`
     用 Python 内建实现——真实 Windows PATH 里 Git for Windows 只挂了 `PortableGit\\bin`，
     `usr\\bin` 那套 `ls/cat/grep/echo/pwd/mkdir` **全缺**，调外部 exe 必 FileNotFoundError。
     `python` 解释器**剔除**（等于给沙箱开后门，且解释器版本不可控）。
  2. **不开 shell**：subprocess 参数永远是 list，绝不做字符串拼接，`; rm -rf /` 只是普通参数。
  3. **参数级路径校验**：沙箱 cwd 挡不住 `cat ../../.env` / `curl file:///C:/Users/...`，
     所有"像路径"的参数 resolve 后必须落在 `data/sandbox/{username}/` 内。
  4. **命令级参数限制**：git 限安全子命令并禁 `-c/--exec-path/--git-dir/-C/!`；curl 只留
     GET 抓取（allow-list 选项，禁 -o/-d/-F/-T/-X/-H/-K 等写文件或自定义请求）；
     ffmpeg 禁 `concat:/file:/pipe:/data:` 协议与 `movie=/amovie=/subtitles=` 滤镜读文件。
  5. **资源上限**：每命令 `max_runtime`（默认 30s）+ 单流输出 1MB 截断。
  6. **审计**：每次调用（含被拒绝的）都追加 `data/audit/commands.jsonl`。
  7. **第三方 CLI 只读闸（M4c）**：用户在「第三方 CLI」页登记为已接入的 CLI（`tools/cli_registry.py`），
     其可执行文件进入**动态白名单**——但只有**只读 / 查询类子命令**放行（如 `lark-cli auth status`、
     `wecom-cli auth show`），写操作子命令与落盘参数一律拒绝；无可核对文档的包（`dws` / `gws`）清单为空，
     等于不放行。状态只按账号查库，查不到就不放行（fail closed）。

已知残余风险（诚实记录）：ffmpeg 能力过强，白名单式参数校验不可能穷尽它的读文件滤镜；
第三方 CLI 会读自己主目录下的凭证（如 `~/.config/wecom/credentials.enc`），
一旦放行就等于越过沙箱边界——所以只放行只读子命令，且必须用户先明确登记为已接入。
本模块定位「本地单机、账号自用」的中等强度防护，不承担多租户隔离职责。
"""
from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
import time
from pathlib import Path

from tools import cli_registry

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SANDBOX_ROOT = PROJECT_ROOT / "data" / "sandbox"
AUDIT_FILE = PROJECT_ROOT / "data" / "audit" / "commands.jsonl"

MAX_OUTPUT = 1_000_000          # 单流输出上限（字节），超出截断
DEFAULT_TIMEOUT = 30
MAX_LINE_LEN = 2000
ANON_USERNAME = "public"        # 拿不到账号时的兜底沙箱（CLI 面板必有 token，正常不走到这）

# ---------------------------------------------------------------- 白名单表
# kind: proc=外部程序 / builtin=Python 内建实现
ALLOWED_COMMANDS: dict[str, dict] = {
    "ffmpeg": {"kind": "proc", "desc": "音视频处理（转码 / 抽音 / 拼接）", "max_runtime": 300,
               "usage": "ffmpeg -i input.mp4 -vn -acodec libmp3lame out.mp3"},
    "git": {"kind": "proc", "desc": "版本控制（只读 + 本地提交，限沙箱目录）", "max_runtime": 15,
            "usage": "git init / git status / git log --oneline -5"},
    "curl": {"kind": "proc", "desc": "HTTP GET 抓取（禁上传 / 禁写文件）", "max_runtime": 30,
             "usage": "curl -s --max-time 15 https://example.com"},
    "ls": {"kind": "builtin", "desc": "列出沙箱目录", "max_runtime": 5, "usage": "ls [-a] [路径]"},
    "cat": {"kind": "builtin", "desc": "查看文件内容", "max_runtime": 5, "usage": "cat 文件 [文件...]"},
    "grep": {"kind": "builtin", "desc": "按内容搜文件", "max_runtime": 5,
             "usage": "grep [-i] [-n] 模式 [路径]"},
    "echo": {"kind": "builtin", "desc": "输出文本（不写文件）", "max_runtime": 5, "usage": "echo 文本"},
    "pwd": {"kind": "builtin", "desc": "显示当前沙箱目录", "max_runtime": 5, "usage": "pwd"},
    "mkdir": {"kind": "builtin", "desc": "在沙箱内建目录", "max_runtime": 5, "usage": "mkdir [-p] 目录"},
}


class ShellRejected(Exception):
    """参数没通过安全闸（不是执行失败，是压根没执行）。"""


# ---------------------------------------------------------------- 沙箱
def sandbox_dir(username: str | None) -> Path:
    """取（并按需创建）该账号的沙箱目录 data/sandbox/{username}/。"""
    name = _safe_username(username)
    d = (SANDBOX_ROOT / name).resolve()
    d.mkdir(parents=True, exist_ok=True)
    readme = d / "README.md"
    if not readme.exists():
        try:
            readme.write_text(
                "# CLI 沙箱目录\n\n"
                "「CLI 面板」与 Agent 的 `run_shell_command` 工具都只能在这个目录里读写文件。\n"
                "白名单命令：ffmpeg / git / curl / ls / cat / grep / echo / pwd / mkdir。\n"
                "参数里出现绝对路径或 `..` 逃出本目录会被直接拒绝。\n",
                encoding="utf-8")
        except OSError:
            pass
    return d


def _safe_username(username: str | None) -> str:
    """用户名进目录名前先消毒：只留字母数字与 _ - . 中文，防 `../` 与 Windows 保留名。"""
    raw = (username or "").strip() or ANON_USERNAME
    cleaned = re.sub(r"[^0-9A-Za-z_\u4e00-\u9fff.-]", "_", raw).strip("._-")
    cleaned = cleaned or ANON_USERNAME
    if cleaned.upper() in {"CON", "PRN", "AUX", "NUL", "COM1", "LPT1"}:
        cleaned = f"u_{cleaned}"
    return cleaned[:64]


def _resolve_in_sandbox(tok: str, sandbox: Path) -> Path:
    """把参数里的路径解析到沙箱内；越界一律 ShellRejected。"""
    if "\x00" in tok:
        raise ShellRejected("参数含空字节")
    if tok.startswith(("\\\\", "//")):
        raise ShellRejected(f"拒绝 UNC / 设备路径：{tok}")
    p = Path(tok)
    try:
        p = p.resolve() if p.is_absolute() else (sandbox / tok).resolve()
    except (OSError, ValueError) as e:
        raise ShellRejected(f"路径无法解析：{tok}（{e}）")
    if p != sandbox and sandbox not in p.parents:
        raise ShellRejected(
            f"路径逃逸沙箱：{tok}\n"
            f"说明：沙箱只允许访问 {sandbox} 内部的路径。\n"
            f"如果这是**初始化类命令**（要指向外部数据目录，如 `--db-dir D:\\...`）："
            f"请在你自己的终端跑一次即可，CLI 会把配置写进你的用户目录，面板随后直接读得到；\n"
            f"如果这是**查询命令**：说明该命令的参数必须落在沙箱内，请把该条目从只读清单里去掉。"
        )
    return p


_PATH_EXT = re.compile(
    r"\.(txt|md|json|jsonl|csv|tsv|log|mp3|mp4|wav|m4a|flac|aac|ogg|avi|mkv|mov|jpg|jpeg|png|gif|webp|bmp"
    r"|pdf|html|htm|py|js|tsv|xlsx|docx|srt|vtt|ass|zip)$", re.I)


def _looks_like_path(tok: str) -> bool:
    """判断一个参数是否"像路径"（选项 -x 不算，交给各命令自己的校验器）。"""
    if not tok or tok.startswith("-"):
        return False
    if tok.startswith(("~", ".")) or "/" in tok or "\\" in tok or ":" in tok:
        return True
    return bool(_PATH_EXT.search(tok))


def _guard_paths(args: list[str], sandbox: Path) -> None:
    """通用闸：所有像路径的参数都必须落在沙箱内（挡绝对路径 / ../ 逃逸）。"""
    for t in args:
        if _looks_like_path(t):
            _resolve_in_sandbox(t, sandbox)


# ---------------------------------------------------------------- 命令行解析
def parse_line(line: str) -> list[str]:
    """把一行命令拆成 token：支持引号，保留反斜杠（Windows 路径），不认任何 shell 语法。"""
    line = (line or "").strip()
    if not line or len(line) > MAX_LINE_LEN:
        raise ShellRejected("命令为空或过长（>2000 字符）")
    try:
        toks = shlex.split(line, posix=False)
    except ValueError as e:
        raise ShellRejected(f"引号不配对：{e}")
    out = []
    for t in toks:
        if len(t) >= 2 and t[0] == t[-1] and t[0] in ("'", '"'):
            t = t[1:-1]
        out.append(t)
    return out


# ---------------------------------------------------------------- 外部命令：参数限制
_GIT_SUBCMDS = {"status", "log", "diff", "show", "init", "add", "commit", "rev-parse",
                "ls-files", "branch", "tag", "remote", "stash", "describe", "shortlog",
                "blame", "count-objects", "checkout", "switch", "restore", "mv", "rm"}
_GIT_BAD = ("-c", "--exec-path", "--upload-pack", "--receive-pack", "--config-env",
            "--git-dir", "--work-tree", "-C", "--namespace", "--no-index")

_CURL_FLAGS = {"-s", "-S", "-i", "-I", "-L", "-v", "-g", "-k", "--compressed", "--noproxy",
               "--max-time", "-m", "--connect-timeout", "-A", "--user-agent", "-r", "--range"}
_CURL_VALUE_FLAGS = {"--noproxy", "--max-time", "-m", "--connect-timeout", "-A", "--user-agent",
                     "-r", "--range"}
_FFMPEG_BAD_PROTO = re.compile(r"^(concat|file|pipe|data|subfile|cache|sftp|tcp|udp|rtmp):", re.I)
_FFMPEG_BAD_FILTER = ("movie=", "amovie=", "subtitles=", "ass=", "subfile=")


def _check_git(args: list[str], sandbox: Path) -> None:
    sub = next((a for a in args if not a.startswith("-")), "")
    if not sub:
        raise ShellRejected("用法：git <子命令>，如 git status")
    if sub not in _GIT_SUBCMDS:
        raise ShellRejected(f"git 子命令 '{sub}' 不在安全清单：{'/'.join(sorted(_GIT_SUBCMDS))}")
    for a in args:
        if a.startswith("!"):
            raise ShellRejected("拒绝 git 别名 / shell 逃逸参数（! 前缀）")
        # `--git-dir=C:/...` 这种「等号写法」也必须拦：只比对裸选项名会漏网
        if a in _GIT_BAD or a.startswith(("-c", "-C")) or any(
                a.startswith(b + "=") for b in _GIT_BAD if b.startswith("--")):
            raise ShellRejected(f"git 参数被禁：{a}")


def _check_curl(args: list[str], sandbox: Path) -> None:
    urls, i = [], 0
    while i < len(args):
        a = args[i]
        if a.startswith("-"):
            name = a.split("=", 1)[0] if a.startswith("--") else a
            if name not in _CURL_FLAGS:
                raise ShellRejected(f"curl 选项 '{name}' 不在允许清单（只允许 GET 抓取）")
            # 值随选项走（`-m 15`）；`--max-time=15` 这种等号写法值已在同 token 里
            if name in _CURL_VALUE_FLAGS and "=" not in a:
                i += 1
        else:
            urls.append(a)
        i += 1
    if len(urls) != 1:
        raise ShellRejected("curl 需要一个 URL（多个 URL / 无 URL 均拒绝）")
    if not re.match(r"^https?://", urls[0], re.I):
        raise ShellRejected(f"只允许 http(s) URL：{urls[0]}")


def _check_ffmpeg(args: list[str], sandbox: Path) -> None:
    joined = " ".join(args)
    for bad in _FFMPEG_BAD_FILTER:
        if bad in joined:
            raise ShellRejected(f"ffmpeg 滤镜 {bad.strip('=')} 可读任意文件，已禁")
    for a in args:
        if a.startswith("-"):
            continue
        if _FFMPEG_BAD_PROTO.match(a):
            raise ShellRejected(f"ffmpeg 协议被禁：{a.split(':', 1)[0]}:")
        if re.match(r"^[a-z][a-z0-9+.-]*://", a, re.I) and not re.match(r"^https?://", a, re.I):
            raise ShellRejected(f"ffmpeg 只允许 http(s) 网络输入：{a}")


# ---------------------------------------------------------------- 内建命令实现
def _fmt_size(n: int) -> str:
    for unit in ("B", "K", "M", "G"):
        if n < 1024 or unit == "G":
            return f"{n}{unit}" if unit == "B" else f"{n:.0f}{unit}"
        n /= 1024
    return f"{n}B"


def _b_ls(args: list[str], sandbox: Path) -> str:
    show_all = any(re.fullmatch(r"-[a-zA-Z]*a[a-zA-Z]*", a or "") for a in args)
    rest = [a for a in args if not a.startswith("-")]
    target = _resolve_in_sandbox(rest[0], sandbox) if rest else sandbox
    if not target.exists():
        raise ShellRejected(f"路径不存在：{rest[0] if rest else '.'}")
    if target.is_file():
        return f"f {_fmt_size(target.stat().st_size):>8}  {target.name}"
    lines = []
    for p in sorted(target.iterdir(), key=lambda x: (x.is_file(), x.name.lower())):
        if not show_all and p.name.startswith("."):
            continue
        try:
            if p.is_dir():
                lines.append(f"d {'':>8}  {p.name}/")
            else:
                lines.append(f"f {_fmt_size(p.stat().st_size):>8}  {p.name}")
        except OSError:
            continue
        if len(lines) >= 200:
            lines.append("… （超过 200 项已截断）")
            break
    if not lines:
        lines.append("（空目录）")
    return "\n".join(lines)


def _b_cat(args: list[str], sandbox: Path) -> str:
    rest = [a for a in args if not a.startswith("-")]
    if not rest:
        raise ShellRejected("用法：cat 文件 [文件...]")
    out = []
    for a in rest:
        p = _resolve_in_sandbox(a, sandbox)
        if p.is_dir():
            raise ShellRejected(f"{a} 是目录，不能用 cat")
        if not p.exists():
            raise ShellRejected(f"文件不存在：{a}")
        if p.stat().st_size > MAX_OUTPUT:
            raise ShellRejected(f"文件过大（>{_fmt_size(MAX_OUTPUT)}）：{a}")
        try:
            txt = p.read_text(encoding="utf-8", errors="replace")
        except OSError as e:
            raise ShellRejected(f"读取失败：{a}（{e}）")
        if len(rest) > 1:
            out.append(f"===== {a} =====")
        out.append(txt)
    return "\n".join(out)


def _b_grep(args: list[str], sandbox: Path) -> str:
    flags = [a for a in args if a.startswith("-")]
    rest = [a for a in args if not a.startswith("-")]
    if not rest:
        raise ShellRejected("用法：grep [-i] [-n] 模式 [路径...]")
    pat, targets = rest[0], rest[1:] or ["."]
    icase = any("i" in f[1:] for f in flags)
    show_num = any("n" in f[1:] for f in flags)
    try:
        rx = re.compile(pat, re.I if icase else 0)
    except re.error:
        rx = re.compile(re.escape(pat), re.I if icase else 0)
    hits, files, lines = [], 0, []
    for t in targets:
        p = _resolve_in_sandbox(t, sandbox)
        if not p.exists():
            raise ShellRejected(f"路径不存在：{t}")
        cand = [p] if p.is_file() else [f for f in p.rglob("*") if f.is_file()]
        for f in cand:
            if files >= 200 or len(lines) >= 200:
                break
            try:
                if f.stat().st_size > 2_000_000:
                    continue
                files += 1
                for i, ln in enumerate(f.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
                    if rx.search(ln):
                        rel = f.relative_to(sandbox)
                        lines.append(f"{rel}:{i}:{ln}" if show_num else f"{rel}:{ln}")
                        if len(lines) >= 200:
                            break
            except (OSError, UnicodeError):
                continue
    if not lines:
        return f"（没搜到匹配「{pat}」的内容，已扫 {files} 个文件）"
    if len(lines) >= 200:
        lines.append("… （命中超过 200 条已截断）")
    return "\n".join(lines)


def _b_echo(args: list[str], sandbox: Path) -> str:
    return " ".join(a for a in args if a != "-n")


def _b_pwd(args: list[str], sandbox: Path) -> str:
    return str(sandbox)


def _b_mkdir(args: list[str], sandbox: Path) -> str:
    rest = [a for a in args if not a.startswith("-")]
    if not rest:
        raise ShellRejected("用法：mkdir [-p] 目录")
    done = []
    for a in rest:
        p = _resolve_in_sandbox(a, sandbox)
        try:
            p.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            raise ShellRejected(f"建目录失败：{a}（{e}）")
        done.append(str(p.relative_to(sandbox)) or ".")
    return "已创建：" + "、".join(done)


_BUILTINS = {"ls": _b_ls, "cat": _b_cat, "grep": _b_grep, "echo": _b_echo, "pwd": _b_pwd, "mkdir": _b_mkdir}


# ---------------------------------------------------------------- 审计
def _audit(entry: dict) -> None:
    try:
        AUDIT_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(AUDIT_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError:
        pass  # 审计写不进去也不能影响执行结果


# ---------------------------------------------------------------- 执行
def _execute_env(sandbox: Path) -> dict:
    """子进程环境：最小化 + **继承真实用户目录环境** + UTF-8 标准 IO。

    为什么必须继承真实用户目录（USERPROFILE/APPDATA/LOCALAPPDATA/…）：
    第三方 CLI 的**登录态与配置都存在真实用户目录**（如 `~/.bilibili-cli/credential.json`、
    `~/.wechat-cli/config.json`）。把 `~` 改指沙箱会让它们找不到凭据、误报「未登录」，
    逼用户在每个账号的沙箱里重复鉴权——与产品口径「你在自己终端配置一次即可」冲突。
    沙箱的写入边界由 **cwd**（相对路径落在沙箱）与**参数级路径校验**负责，
    不靠篡改用户目录；子进程本就以同一 OS 用户身份运行，改 `~` 并不是安全边界。

    `PYTHONIOENCODING=utf-8`：中文 Windows 子进程默认 GBK 写 stdout，
    遇到 emoji/生僻字会直接 UnicodeEncodeError（实测 wechat-cli 查含 emoji 的聊天记录崩）。
    """
    keep = ("PATH", "SystemRoot", "SystemDrive", "WINDIR", "COMSPEC", "PATHEXT",
            "TEMP", "TMP", "NUMBER_OF_PROCESSORS",
            # 用户目录相关：CLI 靠它们定位自己的凭据/配置/缓存（继承，不改变沙箱写入边界）
            "USERPROFILE", "HOMEDRIVE", "HOMEPATH", "HOME", "APPDATA", "LOCALAPPDATA",
            "USERNAME", "USERDOMAIN")
    env = {k: os.environ[k] for k in keep if os.environ.get(k)}
    env["PYTHONIOENCODING"] = "utf-8"
    return env



def _truncate(data: bytes) -> tuple[str, bool]:
    cut = len(data) > MAX_OUTPUT
    chunk = data[:MAX_OUTPUT]
    # 优先 GBK（微信/中文 Windows CLI 常见编码），失败回退 UTF-8
    try:
        txt = chunk.decode("gbk")
    except UnicodeDecodeError:
        txt = chunk.decode("utf-8", errors="replace")
    if cut:
        txt += f"\n… [输出超过 {_fmt_size(MAX_OUTPUT)} 已截断]"
    return txt, cut


def _result(ok: bool, *, stdout: str = "", stderr: str = "", error: str | None = None,
            returncode: int | None = None, elapsed: float = 0.0, command: str = "",
            args: list[str] | None = None, cwd: str = "", truncated: bool = False) -> dict:
    return {"ok": ok, "stdout": stdout, "stderr": stderr, "error": error,
            "returncode": returncode, "elapsed": round(elapsed, 3), "command": command,
            "args": args or [], "cwd": cwd, "truncated": truncated}


def execute(command: str, args: list[str] | None = None, timeout: int | None = None,
            username: str | None = None, account_id: int | None = None,
            adhoc: dict | None = None) -> dict:
    """执行一条白名单命令。返回结构化结果，永不抛异常（安全拒绝也走 ok=False）。

    `account_id` 决定「已接入的第三方 CLI」动态白名单；拿不到就不放行（fail closed）。

    `adhoc`：**仅供服务端「CLI 能力描述草稿」接口**用于「用户正在表单里编、还没保存」的 CLI
    （形态 `{bin: 解析后的只读规则}`）。它**不放宽任何口径**——仍然逐条走
    `cli_registry.readonly_verdict`（未列出的子命令、落盘参数照旧拒绝），只是把用户此刻
    表单里的清单当作该 CLI 的规则；调用方（`api/customize.cli_ability_generate`）只拿它跑
    `<bin> <cmd> --help`（只读探测）。Agent 侧的工具入口**不传**这个参数。
    """
    args = list(args or [])
    command = (command or "").strip()
    sandbox = sandbox_dir(username)
    meta = ALLOWED_COMMANDS.get(command)
    dyn = None if meta is not None else cli_registry.allowed_for_agent(account_id).get(command)
    if dyn is None and meta is None and adhoc and command in adhoc:
        dyn = {"readonly": adhoc[command], "adhoc": True}
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    uname = _safe_username(username)

    if meta is None and dyn is None:
        live = sorted(cli_registry.allowed_for_agent(account_id))
        hint = f"；已接入的第三方 CLI：{'/'.join(live)}" if live else ""
        msg = f"命令 '{command}' 不在白名单：{'/'.join(ALLOWED_COMMANDS)}{hint}"
        _audit({"ts": ts, "user": uname, "cmd": command, "args": args, "ok": False,
                "rejected": msg, "cwd": str(sandbox)})
        return _result(False, error=msg, command=command, args=args, cwd=str(sandbox))
    if len(args) > 64 or any(len(a) > 500 for a in args):
        msg = "参数过多或单个参数过长"
        _audit({"ts": ts, "user": uname, "cmd": command, "args": args, "ok": False,
                "rejected": msg, "cwd": str(sandbox)})
        return _result(False, error=msg, command=command, args=args, cwd=str(sandbox))

    # ---- 安全闸：先校验再执行，被拒不执行
    try:
        _guard_paths(args, sandbox)
        if dyn is not None:
            ok, why = cli_registry.readonly_verdict(args, dyn["readonly"])
            if not ok:
                raise ShellRejected(f"第三方 CLI `{command}`：{why}")
        elif command == "git":
            _check_git(args, sandbox)
        elif command == "curl":
            _check_curl(args, sandbox)
        elif command == "ffmpeg":
            _check_ffmpeg(args, sandbox)
    except ShellRejected as e:
        msg = f"⛔ {e}"
        _audit({"ts": ts, "user": uname, "cmd": command, "args": args, "ok": False,
                "rejected": str(e), "cwd": str(sandbox)})
        return _result(False, error=msg, command=command, args=args, cwd=str(sandbox))

    t0 = time.time()
    # ---- 内建实现
    if meta is not None and meta["kind"] == "builtin":
        try:
            out = _BUILTINS[command](args, sandbox)
            el = time.time() - t0
            _audit({"ts": ts, "user": uname, "cmd": command, "args": args, "ok": True,
                    "returncode": 0, "elapsed": round(el, 2), "cwd": str(sandbox), "engine": "builtin"})
            return _result(True, stdout=out, returncode=0, elapsed=el, command=command,
                           args=args, cwd=str(sandbox))
        except ShellRejected as e:
            el = time.time() - t0
            _audit({"ts": ts, "user": uname, "cmd": command, "args": args, "ok": False,
                    "rejected": str(e), "elapsed": round(el, 2), "cwd": str(sandbox), "engine": "builtin"})
            return _result(False, error=f"⛔ {e}", command=command, args=args,
                           cwd=str(sandbox), elapsed=el)
        except Exception as e:  # noqa: BLE001 - 内建实现兜底，任何异常都不该 500
            el = time.time() - t0
            _audit({"ts": ts, "user": uname, "cmd": command, "args": args, "ok": False,
                    "error": f"{type(e).__name__}: {e}", "elapsed": round(el, 2),
                    "cwd": str(sandbox), "engine": "builtin"})
            return _result(False, error=f"执行出错：{type(e).__name__}: {e}", command=command,
                           args=args, cwd=str(sandbox), elapsed=el)

    # ---- 外部程序
    exe = shutil.which(command)
    if not exe:
        msg = f"命令 '{command}' 未安装或不在 PATH"
        _audit({"ts": ts, "user": uname, "cmd": command, "args": args, "ok": False,
                "rejected": msg, "cwd": str(sandbox)})
        return _result(False, error=msg, command=command, args=args, cwd=str(sandbox))

    # ★ Windows：npm / pipx 在 PATH 上放的是 `.cmd` / `.bat` **垫片**（如 node_global\agently-cli.CMD），
    #   CreateProcess 不能直接执行它们——实测报 `[WinError 193] %1 不是有效的 Win32 应用程序`。
    #   所以经 `cmd.exe /c` 启动，**仍然传参数列表、shell=False、不做字符串拼接**；
    #   只有那两处 shell 元字符要额外挡（cmd.exe 会二次解析）：
    #     · `%` —— 命令行里也会做变量展开（`%PATH%`），且引号挡不住 → 直接拒绝；
    #     · `"` / 换行 —— cmd.exe 不认 list2cmdline 的 `\"` 转义，会破坏引号配对 → 拒绝。
    #   其余 `&` `|` `>` 等在被引号包住时是字面量（cmd.exe 遵守双引号），不额外处理。
    argv = [exe, *args]
    if os.name == "nt" and exe.lower().endswith((".cmd", ".bat")):
        bad = [a for a in args if ("%" in a) or ('"' in a) or ("\n" in a) or ("\r" in a)]
        if bad:
            msg = ("该 CLI 是 Windows 批处理垫片（%s），参数里不能带 `%%` / 双引号 / 换行"
                   "（cmd.exe 会二次解析，可能被注入）——请换一种写法" % os.path.basename(exe))
            _audit({"ts": ts, "user": uname, "cmd": command, "args": args, "ok": False,
                    "rejected": msg, "cwd": str(sandbox)})
            return _result(False, error=msg, command=command, args=args, cwd=str(sandbox))
        argv = [os.environ.get("COMSPEC") or "cmd.exe", "/c", exe, *args]

    # adhoc（服务端草稿接口）只给了 readonly、没有 max_runtime → 用默认上限兜底
    runtime_cap = int(meta["max_runtime"]) if meta is not None else int(dyn.get("max_runtime") or 30)
    limit = max(1, min(int(timeout or DEFAULT_TIMEOUT), runtime_cap))
    try:
        proc = subprocess.run(argv, cwd=str(sandbox), capture_output=True,
                              timeout=limit, shell=False, env=_execute_env(sandbox))
    except subprocess.TimeoutExpired:
        el = time.time() - t0
        _audit({"ts": ts, "user": uname, "cmd": command, "args": args, "ok": False,
                "error": f"timeout>{limit}s", "elapsed": round(el, 2), "cwd": str(sandbox)})
        return _result(False, error=f"命令超时（>{limit}s）已终止", command=command, args=args,
                       cwd=str(sandbox), elapsed=el)
    except OSError as e:
        el = time.time() - t0
        _audit({"ts": ts, "user": uname, "cmd": command, "args": args, "ok": False,
                "error": str(e), "elapsed": round(el, 2), "cwd": str(sandbox)})
        return _result(False, error=f"启动失败：{e}", command=command, args=args,
                       cwd=str(sandbox), elapsed=el)

    el = time.time() - t0
    stdout, cut1 = _truncate(proc.stdout or b"")
    stderr, cut2 = _truncate(proc.stderr or b"")
    entry = {"ts": ts, "user": uname, "cmd": command, "args": args, "ok": proc.returncode == 0,
             "returncode": proc.returncode, "elapsed": round(el, 2), "cwd": str(sandbox),
             "stdout_len": len(stdout), "stderr_len": len(stderr)}
    if dyn is not None:
        entry["third_party"] = dyn.get("cli_id") or ("adhoc:" + command)
    _audit(entry)
    return _result(proc.returncode == 0, stdout=stdout, stderr=stderr,
                   returncode=proc.returncode, elapsed=el, command=command, args=args,
                   cwd=str(sandbox), truncated=cut1 or cut2)


def run_line(line: str, username: str | None = None, timeout: int | None = None,
             account_id: int | None = None) -> dict:
    """CLI 面板入口：把一行命令拆 token 后走 execute。"""
    sandbox = sandbox_dir(username)
    try:
        toks = parse_line(line)
    except ShellRejected as e:
        _audit({"ts": time.strftime("%Y-%m-%d %H:%M:%S"), "user": _safe_username(username),
                "cmd": line[:200], "args": [], "ok": False, "rejected": str(e), "cwd": str(sandbox)})
        return _result(False, error=f"⛔ {e}", command=line[:200], cwd=str(sandbox))
    return execute(toks[0], toks[1:], timeout, username, account_id)


def allowed_commands(username: str | None = None, account_id: int | None = None) -> dict:
    """给前端 CLI 面板 / Agent 提示词用的白名单清单（不含内部字段）。

    `third_party` = 该账号已接入、且清单允许 Agent 只读代跑的第三方 CLI（M4c）。
    """
    sandbox = sandbox_dir(username)
    live = cli_registry.allowed_for_agent(account_id)
    third = []
    for bin_name, info in sorted(live.items()):
        third.append({
            "name": bin_name,
            "kind": "proc",
            "cli_id": info["cli_id"],
            "title": info["name"],
            "desc": f"{info['name']}（你配置的自定义 CLI）——仅放行只读/查询类子命令",
            "readonly": "；".join(info.get("agent_commands") or []),
            "detected": info.get("detected") or {"found": False},
            "usage": (info.get("agent_commands") or [""])[0],
            "commands": info.get("agent_commands") or [],
            "max_runtime": info["max_runtime"],
        })
    return {
        "ok": True,
        "sandbox": str(sandbox),
        "commands": [
            {"name": n, "kind": m["kind"], "desc": m["desc"], "usage": m["usage"],
             "max_runtime": m["max_runtime"]}
            for n, m in ALLOWED_COMMANDS.items()
        ],
        "third_party": third,
        "notes": [
            "参数按 list 传递，永不做 shell 拼接：`; rm -rf /` 只会被当成普通参数。",
            "所有路径参数必须落在沙箱目录内，绝对路径 / `..` 逃逸会被拒绝。",
            "沙箱是**只读**的：能看文件 / 抓网页回显 / 建目录，但不能往沙箱里写文件（要存内容请让 Agent 用 write_agent_doc）。",
            f"单条命令超时上限见各命令 max_runtime，输出超过 {_fmt_size(MAX_OUTPUT)} 截断。",
            "本地地址访问（如 http://localhost:8123）需带 --noproxy '*' 绕开系统代理。",
            "已接入的第三方 CLI（见「第三方 CLI」页）：只放行只读 / 查询类子命令，写操作与落盘参数一律拒绝。",
        ],
    }
