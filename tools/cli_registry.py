"""自定义 CLI 接入配置（M4c，配置导向，不是厂商导向）。

设计口径（用户 2026-09-10 拍板）：
- **不预设厂商**。系统只认用户填的字段——就像模型配置填 base_url / model / key 一样，
  这里填「可执行名 + 安装/认证命令 + 文档 + 允许 Agent 代跑的只读命令」。
  内置的几家（飞书/企微/钉钉/Google）只是 `TEMPLATES` 里的**预填示例**，不享受任何特权：
  不进白名单、不参与判定；用户点一下只是把表单填好，可改可删。
- **只读清单必须人给**。哪些子命令是只读的无法自动推断（`doc search` 是读、`cache clear` 是写、
  `im +messages-send` 是写，同厂内部都没有统一命名规律）→ 系统不猜；清单留空 = 不放行 Agent 代跑。
- 与 Agent 的关系＝折中方案 C：已接入的 CLI 只放行**只读/查询类子命令**，写操作与落盘参数一律拒绝。

安全底线（自定义不等于放松）：
  ① 白名单 = 用户登记的表项 ② 参数级路径校验 ③ 沙箱 cwd ④ 超时 ⑤ 输出上限 ⑥ 审计
  ⑦ 只读闸；可执行名只允许纯名字（`[A-Za-z0-9._-]`，**不含路径分隔符**，
  否则等于把「随便执行任意程序」的能力交给用户输入）。
"""
from __future__ import annotations

import os
import json
import re
import shlex
import shutil
from pathlib import Path

from tools.schema_personal import get_personal_conn

# ---------------------------------------------------------------- 状态
STATES: dict[str, str] = {
    "none": "未接入",
    "installed": "已安装（未认证）",
    "authed": "已接入（已授权）",
}
LIVE_STATES = ("installed", "authed")   # 放行 Agent 代跑的状态（都表示「本机确实装了」）

# 只读命令里仍然禁止的「落盘 / 分发」类参数（沙箱只读，不允许把响应写成文件）
WRITE_FLAGS = ("-o", "--output", "--output-dir", "--output-qrcode", "--download", "--out")
# 无副作用的「帮助/版本」开关：能力描述取证要跑裸 `<bin> --help`，它比放行任一子命令更窄
HARMLESS_FLAGS = ("--help", "-h", "--version", "-v", "--usage", "--version-only")
DOCS_MODES = ("url", "path", "help")     # 能力描述依据三选一（单选）

MAX_NAME = 40
MAX_BIN = 64
MAX_CMD = 300
MAX_DOCS = 300
MAX_RULES = 40
MAX_RULE_TOKENS = 8
MAX_NOTE = 200
MAX_ABILITIES = 2000         # M5：能力描述长度默认上限（用户可在前端自选更高上限）；超长时 warn 而非截断
MAX_ABILITIES_HARD = 8000    # 硬上限（防止恶意/误操作）

_BIN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
# 「子命令样子」的 token：字母开头（可带 `/`、`+` 前缀，兼容 lark-cli 的 `+agenda` 与 `api call /store/search`）
_RULE_TOKEN_RE = re.compile(r"^[/+]?[A-Za-z][A-Za-z0-9._/-]*$")
_SHELL_META = (";", "|", "&", ">", "<", "`", "$", "\n", "\r", "\t")


class CliConfigError(ValueError):
    """用户填的配置不合法（拿去变成 HTTP 400）。"""


# ---------------------------------------------------------------- 预填示例（不是特权厂商）
def templates() -> list[dict]:
    """新建表单的预填示例：点一下只把表单填好，之后完全由用户拥有。"""
    return [
        {
            "key": "lark", "icon": "🐦", "name": "飞书 Lark CLI", "bin": "lark-cli",
            "install_cmd": "npm install -g @larksuite/cli",
            "auth_cmd": "lark-cli auth login --recommend",
            "docs": "https://github.com/larksuite/cli",
            "readonly": "auth status\nauth check\nauth scopes\nauth list\nschema\ncalendar +agenda\ncalendar calendars list\ncalendar events instance_view",
            "hint": "命令名取自官方 README.zh.md（认证章节 + 三层命令示例）。",
        },
        {
            "key": "wecom", "icon": "💬", "name": "企业微信 CLI", "bin": "wecom-cli",
            "install_cmd": "npm install -g @wecom/cli",
            "auth_cmd": "wecom-cli auth init",
            "docs": "https://github.com/WecomTeam/wecom-cli/blob/HEAD/docs/cli-reference.md",
            "readonly": "auth show\nschema list\nschema get\ncache status\nmessage aibot sessions list\ndoc search",
            "hint": "命令名取自官方 docs/cli-reference.md（auth / 内建命令 / 方法示例）。",
        },
        {
            "key": "dingtalk", "icon": "📌", "name": "钉钉 Workspace CLI", "bin": "dws",
            "install_cmd": "npm i -g dingtalk-workspace-cli",
            "auth_cmd": "dws --help", "docs": "",
            "readonly": "",
            "hint": "npm 包无 readme：**先在自己终端跑 `dws --help` 看子命令，再把只读的那几条填进来**；清单空 = 不放行 Agent 代跑。",
        },
        {
            "key": "google", "icon": "🔵", "name": "Google Workspace CLI", "bin": "gws",
            "install_cmd": "npm i -g @googleworkspace/cli",
            "auth_cmd": "gws --help", "docs": "https://www.npmjs.com/package/@googleworkspace/cli",
            "readonly": "",
            "hint": "npm 包无 readme：同上，以本机 `gws --help` 回显为准，别照抄臆造的子命令。",
        },
    ]


# ---------------------------------------------------------------- 字段校验
def _clean_text(v, limit: int, field: str, allow_multiline: bool = False) -> str:
    s = (v or "").strip()
    if len(s) > limit:
        raise CliConfigError(f"{field}过长（>{limit} 字符）")
    if not allow_multiline:
        if any(c in s for c in ("\n", "\r", "\t")):
            raise CliConfigError(f"{field}不能包含换行/制表符")
    else:
        s = s.replace("\r\n", "\n").replace("\r", "\n")
    return s


def validate_bin(bin_name: str) -> str:
    """可执行名：纯名字（不含路径分隔符）。含 `\\`/`/`/`:` 一律拒绝——那等于开放任意程序执行。"""
    s = (bin_name or "").strip()
    if not s:
        raise CliConfigError("可执行名不能为空")
    if len(s) > MAX_BIN:
        raise CliConfigError(f"可执行名过长（>{MAX_BIN} 字符）")
    if not _BIN_RE.match(s):
        raise CliConfigError("可执行名只允许字母/数字/`._-`，且不能以符号开头；"
                             "不要写路径（PATH 里叫什么就填什么，如 lark-cli）")
    return s


def _looks_like_bin(tok: str, bin_name: str) -> bool:
    """这个 token 是不是「可执行名」而不是子命令？

    判定：本机 PATH 里真的有这个名字 **且** 它与配置的 bin 有前后缀关系（`weread` vs `weread-agent-cli`）。

    ⚠️ 不能只要「PATH 里有同名文件」就算——实测本机 PATH 里真的有名为 `search` 的可执行文件，
    会把 `search`（正常子命令）误判成命令名、把规则清空。所以必须再加「与 bin 相关」这一条件。
    """
    t = (tok or "").lower()
    b = (bin_name or "").lower()
    if not tok or tok != tok.strip("-"):
        return False
    if b and t in (b, b + ".exe", b + ".cmd"):
        return True
    if not b:
        return False
    related = t in b or b in t
    return related and shutil.which(tok) is not None


def _split_rule(tokens: list[str], bin_name: str = "") -> tuple[list[str], list[str], str]:
    """把粘贴进来的一整条命令裁成「子命令路径」。

    返回 (路径 token, 被裁掉的 token, 提示语)。裁剪规则：
      ① 首个 token 若是可执行名（`weread notes export …`）→ 去掉（不是配置的 bin 但 PATH 里存在也去掉，并提示核对）
      ② 遇到 `--flag` / `-x` 就停（选项及其取值不进白名单）；
      ③ 遇到「不像子命令」的 token 就停（纯数字 / 含中文 / 含 `=` 等 → 那是取值，如 bookId、搜索词）。
    这样用户可以直接把官方文档里的整条命令粘进来，不必手工删参数。
    """
    toks = list(tokens or [])
    trimmed: list[str] = []
    note = ""
    if toks:
        head = toks[0]
        b = (bin_name or "").lower()
        if b and head.lower() in (b, b + ".exe", b + ".cmd"):
            toks = toks[1:]
        elif _looks_like_bin(head, bin_name):
            toks = toks[1:]
            note = ("清单里的 `%s` 是命令名、不是子命令，已去掉；但你填的可执行名是 `%s`——"
                    "如果本机命令名确实是 `%s`，建议把「可执行名」也改成 `%s`（否则检测不到、也跑不起来）"
                    % (head, bin_name or "(空)", head, head))
    path: list[str] = []
    for i, t in enumerate(toks):
        if t.startswith("-") or not _RULE_TOKEN_RE.match(t):
            trimmed = toks[i:]
            break
        path.append(t)
    return path, trimmed, note


def parse_rules(readonly_text: str, bin_name: str = "") -> tuple[list[list[str]], list[str]]:
    """把「每行一条」的只读清单解析成 token 规则列表，并做安全校验。

    返回 `(规则列表, 裁剪说明)`——说明用于前端提示「你粘的整条命令被裁成了哪条路径」。
    规则里只保留**子命令路径**（如 `notes export`、`calendar +agenda`）；运行时多给的参数由闸门另行放行/拒绝。
    """
    text = (readonly_text or "").replace("\r\n", "\n").replace("\r", "\n")
    rules: list[list[str]] = []
    notes: list[str] = []
    seen = set()
    raw_lines = 0
    for raw in text.split("\n"):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        raw_lines += 1
        if raw_lines > MAX_RULES:
            # 按**原始行数**限流：否则重复行去重后会绕过上限（实测踩坑）
            raise CliConfigError(f"只读清单最多 {MAX_RULES} 条（已写 {raw_lines} 行）")
        for meta in _SHELL_META:
            if meta in line:
                raise CliConfigError(f"只读清单里不允许出现 `{meta}`（不跑 shell，写了也不会生效）：{line}")
        try:
            toks = shlex.split(line, posix=False)
        except ValueError as e:
            raise CliConfigError(f"只读清单引号不配对：{line}（{e}）")
        toks = [t[1:-1] if len(t) >= 2 and t[0] == t[-1] and t[0] in ("'", '"') else t for t in toks]
        if not toks:
            continue
        if toks[0].startswith("-"):
            raise CliConfigError(f"只读清单要从子命令写起，不能以选项开头：{line}")
        path, trimmed, bin_note = _split_rule(toks, bin_name)
        if not path:
            raise CliConfigError(f"这行只有命令名/参数，没有子命令路径（例如 `notes top`）：{line}")
        if len(path) > MAX_RULE_TOKENS:
            raise CliConfigError(f"单条规则最多 {MAX_RULE_TOKENS} 段子命令：{line}")
        key = tuple(path)
        if key in seen:
            continue
        seen.add(key)
        rules.append(list(path))
        if trimmed or bin_note:
            notes.append("`%s` → 已裁成 `%s`（选项与取值不参与白名单）" % (line, " ".join(path)))
        if bin_note and bin_note not in notes:
            notes.append(bin_note)
    return rules, notes


def _optional_text(payload: dict, key: str, cur, limit: int, field: str) -> str:
    """可选文本字段的「缺省即保留」语义：键缺省或 None 时沿用原值（更新场景），显式空串才清空。"""
    if key not in payload or payload.get(key) is None:
        return (cur[key] or "") if cur is not None else ""
    return _clean_text(payload.get(key), limit, field)


def _validate_payload(payload: dict, *, existing_bins: set[str], cur=None) -> dict:
    name = _clean_text(payload.get("name"), MAX_NAME, "名称")
    if not name:
        raise CliConfigError("名称不能为空")
    bin_name = validate_bin(payload.get("bin"))
    install_cmd = _optional_text(payload, "install_cmd", cur, MAX_CMD, "安装命令")
    auth_cmd = _optional_text(payload, "auth_cmd", cur, MAX_CMD, "认证命令")
    # 能力描述的依据：三选一（单选）。缺省即保留原值（老配置默认 url）。
    raw_mode = payload.get("docs_mode", None)
    if raw_mode in (None, ""):
        docs_mode = ((cur["docs_mode"] if cur is not None else "") or "url")
    else:
        docs_mode = str(raw_mode).strip().lower()
    if docs_mode not in DOCS_MODES:
        raise CliConfigError("能力描述的依据只能选一种：url（网址）/ path（本机文件）/ help（跑 --help）")
    docs = _optional_text(payload, "docs", cur, MAX_DOCS, "文档来源")
    if docs_mode == "url":
        if docs and not re.match(r"^https?://", docs, re.I):
            raise CliConfigError("选了「网址」就得填 http(s):// 开头的地址（或改用「本机文件」）")
    elif docs_mode == "path":
        if docs and not re.match(r"^([A-Za-z]:[\\/]|/|\\\\|~[\\/])", docs):
            raise CliConfigError("选了「本机文件」请填绝对路径（如 D:\\code\\qqmail-cli\\README.md）")
    else:                                  # help：依据来自本机 --help，不存文档地址
        docs = ""
    # 只读清单：键缺省/None → 保留原清单；显式空串 → 清空（= 不放行 Agent 代跑）
    if "readonly" not in payload or payload.get("readonly") is None:
        raw_rules = (cur["readonly"] if cur is not None else "") or ""
    else:
        raw_rules = payload.get("readonly") or ""
    rules, notes = parse_rules(raw_rules, bin_name)
    # M5 能力描述：同样「缺省即保留」——只改个名字不该把路由描述清空；
    # 允许换行（表单里是 textarea，多行更好编辑），注入简报时会被压平成一行。
    if "abilities" not in payload or payload.get("abilities") is None:
        abilities = ((cur["abilities"] if cur is not None else "") or "")
    else:
        abilities = _clean_text(payload.get("abilities"), MAX_ABILITIES_HARD, "能力描述", allow_multiline=True)
    return {"name": name, "bin": bin_name, "install_cmd": install_cmd, "auth_cmd": auth_cmd,
            "docs": docs, "docs_mode": docs_mode, "rules": rules, "notes": notes,
            "abilities": abilities, "existing_bins": existing_bins}


# ---------------------------------------------------------------- 本机检测
def _npm_module_dirs() -> list[Path]:
    """npm 全局 node_modules 目录（纯文件系统检查，不执行任何第三方程序）。"""
    dirs: list[Path] = []
    exe = shutil.which("npm")
    if exe:
        dirs.append(Path(exe).resolve().parent / "node_modules")
    appdata = os.environ.get("APPDATA")
    if appdata:
        dirs.append(Path(appdata) / "npm" / "node_modules")
    return dirs


def detect_local(bin_name: str) -> dict:
    """检测本机是否装了某个可执行名：只 `shutil.which` + 看 npm 全局目录，不跑第三方程序。"""
    found = shutil.which(bin_name) if bin_name else None
    return {
        "found": bool(found),
        "path": found or "",
        "npm_global": str(any(d.is_dir() for d in _npm_module_dirs())) if bin_name else "",
        "node": bool(shutil.which("node")),
        "npm": bool(shutil.which("npm")),
    }


# ---------------------------------------------------------------- 读取（按账号隔离）
_ROW_COLS = ("id", "name", "bin", "install_cmd", "auth_cmd", "docs", "docs_mode", "readonly", "state", "note", "updated_at")


def list_clis(account_id: int | None) -> list[dict]:
    """该账号配置的 CLI 列表（含解析好的规则与本机检测）。"""
    if not account_id:
        return []
    try:
        conn = get_personal_conn()
        try:
            rows = conn.execute(
                "SELECT id, name, bin, install_cmd, auth_cmd, docs, docs_mode, readonly, state, note, updated_at, abilities "
                "FROM user_clis WHERE account_id=? ORDER BY id", (int(account_id),)).fetchall()
        finally:
            conn.close()
    except Exception:  # noqa: BLE001 - 老库没建表等情况不该让页面 500
        return []
    out = []
    for r in rows:
        try:
            rules, _notes = parse_rules(r["readonly"], r["bin"])
        except CliConfigError:
            rules = []          # 库里存了脏文本也不炸，按「无清单 = 不放行」处理
        out.append({
            "id": r["id"], "name": r["name"], "bin": r["bin"],
            "install_cmd": r["install_cmd"] or "", "auth_cmd": r["auth_cmd"] or "",
            "docs": r["docs"] or "", "readonly": r["readonly"] or "",
            "docs_mode": (r["docs_mode"] or "url") if "docs_mode" in r.keys() else "url",
            "abilities": (r["abilities"] or "") if "abilities" in r.keys() else "",
            "rules": rules,
            "agent_commands": [f"{r['bin']} " + " ".join(t) for t in rules],
            "state": r["state"] or "none",
            "state_label": STATES.get(r["state"] or "none", "未接入"),
            "note": r["note"] or "", "updated_at": r["updated_at"] or "",
            "detected": detect_local(r["bin"]),
            "live": (r["state"] in LIVE_STATES) and bool(rules),
        })
    return out


def get_cli(account_id: int | None, cli_id) -> dict | None:
    for c in list_clis(account_id):
        if str(c["id"]) == str(cli_id):
            return c
    return None


def _bin_of(account_id: int | None, cli_id) -> str | None:
    if not account_id or cli_id in (None, ""):
        return None
    try:
        conn = get_personal_conn()
        try:
            row = conn.execute("SELECT bin FROM user_clis WHERE account_id=? AND id=?",
                               (int(account_id), int(cli_id))).fetchone()
        finally:
            conn.close()
        return row["bin"] if row else None
    except Exception:  # noqa: BLE001
        return None


# ---------------------------------------------------------------- 写入
def save_cli(account_id: int | None, payload: dict) -> dict:
    """新建或更新一条 CLI 配置（`id` 为空=新建）。

    **局部更新友好**：`state` / `note` 键缺省（None 或空串）时，更新场景保留原值——
    否则「只改个可执行名」会顺手把接入登记清成未接入（实测踩坑）。
    """
    if not account_id:
        raise CliConfigError("未登录账号")
    cli_id = payload.get("id")
    conn = get_personal_conn()
    try:
        rows = conn.execute("SELECT id, bin FROM user_clis WHERE account_id=?",
                            (int(account_id),)).fetchall()
        existing = {r["bin"]: r["id"] for r in rows}
        cur = None
        if cli_id not in (None, ""):
            cur_id = int(cli_id)
            if not any(r["id"] == cur_id for r in rows):
                raise CliConfigError("要修改的 CLI 不存在（或不属于当前账号）")
            cur = get_cli(account_id, cur_id)      # 全字段 dict（用于「缺省即保留」）
        else:
            cur_id = None
        data = _validate_payload(payload, existing_bins=set(existing), cur=cur)
        dup = existing.get(data["bin"])
        if dup is not None and dup != cur_id:
            raise CliConfigError(f"可执行名 `{data['bin']}` 已被另一条配置占用")
        raw_note = payload.get("note", None)
        note = cur["note"] if (cur is not None and raw_note in (None, "")) else _clean_text(raw_note, MAX_NOTE, "备注")
        raw_state = payload.get("state", None)
        state = cur["state"] if (cur is not None and raw_state in (None, "")) else ((raw_state or "none").strip() or "none")
        if state not in STATES:
            raise CliConfigError(f"状态非法：{state}")
        readonly_text = "\n".join(" ".join(t) for t in data["rules"])
        abilities = data.get("abilities") or ""
        if cur_id is None:
            conn.execute(
                "INSERT INTO user_clis (account_id, name, bin, install_cmd, auth_cmd, docs, docs_mode, readonly, state, note, abilities, updated_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?, datetime('now','localtime'))",
                (int(account_id), data["name"], data["bin"], data["install_cmd"], data["auth_cmd"],
                 data["docs"], data["docs_mode"], readonly_text, state, note, abilities))
            cur_id = conn.execute("SELECT last_insert_rowid() AS i").fetchone()["i"]
        else:
            conn.execute(
                "UPDATE user_clis SET name=?, bin=?, install_cmd=?, auth_cmd=?, docs=?, docs_mode=?, readonly=?, state=?, note=?, abilities=?, "
                "updated_at=datetime('now','localtime') WHERE account_id=? AND id=?",
                (data["name"], data["bin"], data["install_cmd"], data["auth_cmd"], data["docs"],
                 data["docs_mode"], readonly_text, state, note, abilities, int(account_id), cur_id))
        conn.commit()
    finally:
        conn.close()
    item = get_cli(account_id, cur_id) or {}
    item["parse_notes"] = data.get("notes") or []
    # M5b：把变更同步进能力池（含向量），失败不影响配置写入
    _sync_pool(account_id)
    return item


def set_state(account_id: int | None, cli_id, state: str, note: str | None = None) -> dict:
    """只改登记状态（+ 可选备注），不动其它字段。"""
    if not account_id:
        raise CliConfigError("未登录账号")
    if state not in STATES:
        raise CliConfigError(f"状态非法：{state}（可选 {'/'.join(STATES)}）")
    cli = get_cli(account_id, cli_id)
    if not cli:
        raise CliConfigError("CLI 不存在（或不属于当前账号）")
    new_note = cli["note"] if note is None else _clean_text(note, MAX_NOTE, "备注")
    conn = get_personal_conn()
    try:
        conn.execute("UPDATE user_clis SET state=?, note=?, updated_at=datetime('now','localtime') "
                     "WHERE account_id=? AND id=?", (state, new_note, int(account_id), int(cli_id)))
        conn.commit()
    finally:
        conn.close()
    _sync_pool(account_id)      # M5b：状态变化会改变「是否可代跑」→ 池成员随之变化
    return get_cli(account_id, cli_id) or {}


def delete_cli(account_id: int | None, cli_id) -> bool:
    if not account_id:
        raise CliConfigError("未登录账号")
    conn = get_personal_conn()
    try:
        cur = conn.execute("DELETE FROM user_clis WHERE account_id=? AND id=?",
                           (int(account_id), int(cli_id)))
        conn.commit()
        ok = cur.rowcount > 0
    finally:
        conn.close()
    if ok:
        _sync_pool(account_id)      # M5b：从池里摘掉（含向量）
    return ok


def _sync_pool(account_id: int | None) -> None:
    """M5b：把 CLI 配置变更同步进能力池。**完全容错**——池是加速层，坏了也不能影响配置写入。"""
    try:
        from tools import capability_pool
        capability_pool.sync_cli_entry(account_id)
    except Exception as e:  # noqa: BLE001
        print(f"[M5b] 能力池同步失败（忽略）: {type(e).__name__}: {e}")


# ---------------------------------------------------------------- 给 Shell 能力层用
def allowed_for_agent(account_id: int | None) -> dict[str, dict]:
    """可放行给 Agent 的 CLI：{bin: {cli_id, name, readonly, max_runtime, detected}}。

    条件：状态 ∈ {installed, authed} 且只读清单非空。其余一律不放行（fail closed）。
    """
    out: dict[str, dict] = {}
    for c in list_clis(account_id):
        if c["state"] not in LIVE_STATES or not c["rules"]:
            continue
        out[c["bin"]] = {"cli_id": c["id"], "name": c["name"], "state": c["state"],
                         "readonly": [list(t) for t in c["rules"]],
                         "agent_commands": c["agent_commands"],
                         "detected": c["detected"],
                         "max_runtime": 30}
    return out


def by_bin(account_id: int | None, bin_name: str) -> dict | None:
    for c in list_clis(account_id):
        if c["bin"] == bin_name:
            return c
    return None


# ---------------------------------------------------------------- 给 Agent 的常驻简报
# 简报聚合预算：M5 起能力描述较长，3~4 个带描述的 CLI 要能整体放下；
# 超出预算的 CLI **降级成紧凑行，绝不整块消失**（工具从提示词里隐身 = 用户配了也白配）。
MAX_BRIEF_CHARS = 2600
MIN_CLI_SHARE = 300          # 单个 CLI 至少分到的额度（再少就只剩关键词行了）


def _compact_cli_block(c: dict) -> str:
    """紧凑形态：一行放下「名称 + 可执行名 + 命令数 + 能力描述首行（路由关键词）」。

    用于预算不足时的降级——保住最关键的路由信号（关键词），命令细节留给完整形态或技能文档。
    """
    first = ""
    for ln in (c.get("abilities") or "").splitlines():
        ln = ln.strip()
        if ln:
            first = " ".join(ln.split())
            break
    tail = ("；关键词：" + first[:160]) if first else ""
    return (f"- {c['name']}（可执行名 `{c['bin']}`）：共 {len(c['agent_commands'])} 条只读命令{tail}")


def agent_brief(account_id: int | None, max_clis: int = 8, max_cmds: int = 6,
                max_chars: int | None = None) -> str:
    """生成「可用命令行工具」简报（invoke 期常驻注入，一个 CLI 一块）。

    两种形态（M5 起）：
      ① **能力路由卡**（该 CLI 填了 `abilities`）：用户语言的能力描述在前（触发词 + 能力→命令映射），
         命令样例在后——解决「用户说人话、模型想不起该用哪个工具」的路由断点；
      ② **命令名简报**（M4c 原形态，`abilities` 为空时）：名字 + 可执行名 + 放行条数 + 示例命令。

    预算分配（三条口径，都是实测踩出来的）：
      - **公平分配**：`max_chars / CLI 数` 决定单个 CLI 的能力描述展示额度，
        防止一个超长描述把其它 CLI 挤没（实测：3 个 CLI 时 787 字的描述直接把第 3 个工具挤掉）；
      - **降级而非消失**：额度不够时该 CLI 输出**紧凑行**（名称 + 可执行名 + 命令数 + 关键词首行），
        绝不再输出「其余 N 个未列出」——工具从提示词里隐身等于没接入；
      - 返回空串表示没有可用的自定义 CLI（调用方不注这一段）。
    """
    items = [c for c in list_clis(account_id) if c["live"]][:max_clis]
    if not items:
        return ""
    budget = int(max_chars or MAX_BRIEF_CHARS)
    share = max(MIN_CLI_SHARE, budget // max(1, len(items)))     # 每个 CLI 的能力描述展示额度
    blocks, used = [], 0
    for c in items:
        short = [" ".join(t) for t in c["rules"]]      # 只列子命令路径（可执行名已在同块给出）
        sample = " / ".join(short[:max_cmds]) + (" / …" if len(short) > max_cmds else "")
        abilities = " ".join((c.get("abilities") or "").split())     # 压平换行，单块内更紧凑
        if abilities:
            shown = abilities if len(abilities) <= share else abilities[:share] + "…"
            block = (f"- {c['name']}（可执行名 `{c['bin']}`）：用户问到下列任一场景时，"
                     f"**先用它取真实数据**（这是用户自己账号里的私有数据，网搜/知识库都拿不到）；"
                     f"不要改用别的工具顶替，也不要凭你自己的知识作答——\n"
                     f"  {shown}\n"
                     f"  共 {len(c['agent_commands'])} 条只读命令，例如 {sample}")
        else:
            block = (f"- {c['name']}（可执行名 `{c['bin']}`）：可代跑 {len(c['agent_commands'])} 条只读命令，"
                     f"例如 {sample}")
        if used + len(block) > budget:
            block = _compact_cli_block(c)              # 降级：保住路由关键词，不整块丢弃
        blocks.append(block)
        used += len(block)
    return "\n".join(blocks)


# ---------------------------------------------------------------- M5：能力描述的文本解析
_ABILITY_SPLIT = ("；", ";", "\n", "\r", "|")
_ARROW_RE = re.compile(r"\s*(?:→|->|=>)\s*")
_PATH_SPLIT = ("或", "或者", "/", "，", ",", "再", "然后", "接着")   # 「先 <解析类命令> 再 <详情类命令>」取第一步


def _parse_ability_pairs(abilities: str) -> list[tuple[str, str]]:
    """从能力描述里抽出 (触发说法, 命令路径) 对。

    形如 `日程/会议/今天有什么。查今天→today 或 day；查本周→week；加会议→add --title`
    → [("查今天", "today"), ("加会议", "add")]。
    只做「箭头切分 + 取第一段命令」，不猜语义；抽不出来就返回空列表（调用方回退）。
    """
    pairs: list[tuple[str, str]] = []
    for seg in re.split("[" + re.escape("".join(_ABILITY_SPLIT)) + "]", abilities or ""):
        seg = seg.strip().strip("。.,，、")
        if not seg or not _ARROW_RE.search(seg):
            continue
        left, right = _ARROW_RE.split(seg, maxsplit=1)
        trigger = left.strip().strip("。.,，、：:")
        # 触发说法可能写成「正在读/在读」→ 取第一个
        for sep in ("/", "、", "，", ","):
            if sep in trigger:
                trigger = trigger.split(sep)[0].strip()
                break
        # 命令路径可能写成「shelf recent 或 book progress」→ 取第一个候选
        first = right.strip()
        for sep in _PATH_SPLIT:
            if sep in first:
                first = first.split(sep)[0].strip()
                break
        if trigger and first:
            pairs.append((trigger, first))
    return pairs


# ------------------------------------------- M5：能力描述草稿 —— help 真值与审计
# 背景（2026-09-24 用户报障）：能力描述撰写 AI 原先只拿到「命令名清单」，没有任何依据，只能望文生义
# —— 实测 13 条映射里 3 条错，且**全错在需要判断力的地方**（哪些命令要 ID、哪条是「书名→ID」的解析器）。
# 现在改成：文档（tools/cli_docs.py）当语义依据 + 这里的 **help 真值**当校验依据。

HELP_TIMEOUT = 10          # 单条 --help 最多等 10s（实测本机 CLI 都在 1s 内返回）
_USAGE_RE = re.compile(r"^\s*Usage:\s*(.+)$", re.M)
_POS_ARG_RE = re.compile(r"<([^>]{1,40})>")
_UPPER_ARG_RE = re.compile(r"(?<![A-Z0-9_])([A-Z][A-Z0-9_]{2,})(?![A-Z0-9_])")
_NOT_POSITIONAL = {"USAGE", "OPTIONS", "OPTION", "COMMAND", "COMMANDS", "ARGS", "ARG", "HELP", "FLAGS", "FILE"}


_FLAG_VALUE_RE = re.compile(
    r"--[A-Za-z][A-Za-z0-9-]*(?:=|\s+[<\[]|\s+(?:string|strings|int|integer|number|float|bool|boolean"
    r"|path|file|duration|array|list|value)\b)", re.I)
_FLAG_NAME_RE = re.compile(r"(--[A-Za-z][A-Za-z0-9-]*)")
_REQ_WORD_RE = re.compile(r"\b(required|requires?|mandatory)\b|必需|必填|必输")
_REQ_SECTION_RE = re.compile(r"^\s*(required\s+(flags|options|arguments)|必填|必需)", re.I)
_SECTION_HEAD_RE = re.compile(r"^\s*[A-Za-z\u4e00-\u9fff][\w \u4e00-\u9fff]*:\s*$")


def help_required_flags(help_text: str) -> list[str]:
    """从帮助正文里挑出「**必需**且**要取值**」的开关（如 `--id string  (required, get from +list)`）。

    为什么要它（2026-10-07 实测）：`classify_usage` 只读 Usage 行的**位置参数**，
    对「参数全用 flag 给」的 CLI（agently-cli 就是：`message +read [flags]` + `Requires --id`）
    会把明明需要输入的 `+read` 判成「无参数直调」，进而让审计报出「凭空造链」的**误报**。
    只用**帮助原文**里的证据判（写没写 required、开关要不要取值），不猜。
    """
    found: list[str] = []
    in_req = False
    for raw in (help_text or "").splitlines():
        line = raw.rstrip()
        if not line.strip():
            continue
        if _REQ_SECTION_RE.match(line):          # 形如 “Required Flags:”
            in_req = True
            continue
        if _SECTION_HEAD_RE.match(line) and not line.lstrip().startswith("-"):
            in_req = False                       # 换到别的小节（Flags: / Examples: …）
            continue
        if "--" not in line:
            continue
        if not (in_req or _REQ_WORD_RE.search(line)):
            continue
        m = _FLAG_VALUE_RE.search(line)
        if not m:                                # `--dry-run` 这种不带值的开关不算「输入」
            continue
        name = _FLAG_NAME_RE.search(m.group(0)).group(1)
        if name not in found:
            found.append(name)
    return found


def classify_usage(usage: str, help_text: str = "") -> str:
    """按命令行帮助的 Usage 行判断命令形态（`help_text` 用来补 flag 形式的必需输入）。

    四类，含义与**能不能直接调**一一对应：
      · `needs_id`   —— 有位置参数且参数名以 id 结尾（`<bookId>`）→ 需要先解析出 ID，
                       **必须作为两步链的第 2 步**（直调会报参数错）；
      · `free_text`  —— 有位置参数但是自由文本（`<title>` / `<keyword>`）→ 可以当两步链的第 1 步；
      · `needs_flag` —— 没有位置参数，但帮助里写明了**必需的值开关**（如 `--id string (required)`）
                       → 同样是两步链的第 2 步候选（2026-10-07 新增：旧判定把它误判成
                       `standalone`，于是审计对正确的两步链报「凭空造链」）；
      · `standalone` —— 既没有位置参数也没有必需的值开关 → 直接可调，
                       **不能当两步链的第 2 步**（前一步的产出喂不进去）。
    """
    u = (usage or "").strip()
    if not u:
        return "unknown"
    names = [x.strip() for x in _POS_ARG_RE.findall(u) if x.strip()]
    if not names:
        names = [x for x in _UPPER_ARG_RE.findall(u) if x not in _NOT_POSITIONAL]
    if not names:
        # 没有位置参数：再看帮助正文里有没有「必需且要取值」的开关（flag 风格的 CLI）
        if help_required_flags(help_text):
            return "needs_flag"
        return "standalone"
    for n in names:
        if re.search(r"id$", n, re.I):
            return "needs_id"
    return "free_text"


def collect_help(bin_name: str, cmds: list[str], *, username: str | None = None,
                 account_id: int | None = None, timeout: int = HELP_TIMEOUT) -> dict:
    """逐条跑 `<bin> <cmd> --help`，拿**本机真值**（走沙箱运行时，口径不放宽）。

    为什么复用运行时而不是自己起 subprocess：超时、输出截断、环境净化、cwd 沙箱、审计日志都在那里；
    而且它**照旧执行只读闸**（`adhoc` 只是把「用户此刻表单里的清单」当作该 CLI 的规则）。
    `--help` 不在写标志里；裸 `<bin> --help` 会被判「缺少子命令」→ 只能采**已放行子命令**的 help，
    这正好符合 M4c「清单 = 放行范围」的边界。
    """
    from tools._runtime import shell_runtime as rt          # 局部 import：运行时也 import 本模块，避免循环

    rules = [[t for t in c.split() if t] for c in cmds]
    adhoc = {bin_name: rules}
    out: dict[str, dict] = {}
    for c in cmds:
        tokens = c.split()
        got = {"ok": False, "usage": "", "desc": "", "kind": "unknown",
               "requires_flags": [], "help": "", "error": ""}
        for flag in ("--help", "-h"):                       # 少数 CLI 只认 -h
            res = rt.execute(bin_name, tokens + [flag], timeout=timeout, username=username,
                             account_id=account_id, adhoc=adhoc)
            text = ((res.get("stdout") or "") + "\n" + (res.get("stderr") or "")).strip()
            m = _USAGE_RE.search(text)
            if m:
                lines = [l.strip() for l in text.splitlines() if l.strip()]
                idx = next((i for i, l in enumerate(lines) if l.lower().startswith("usage:")), 0)
                got = {"ok": True, "usage": m.group(1).strip(),
                       "desc": lines[idx + 1] if idx + 1 < len(lines) else "",
                       "kind": classify_usage(m.group(1), text),
                       "requires_flags": help_required_flags(text),
                       "help": text[:1200],          # 供审计判「工具自己有没有写明这条链」
                       "error": ""}
                break
            got["error"] = res.get("error") or (res.get("stderr") or "").strip()[:120] or "没有输出 Usage 行"
        out[c] = got
    return out


def collect_help_digest(bin_name: str, cmds: list[str], *, username: str | None = None,
                        account_id: int | None = None, timeout: int = HELP_TIMEOUT,
                        max_cmds: int = 12, max_chars: int = 20000) -> dict:
    """把本机 `--help` 采成**文档级证据**（供「能力描述」在无 README 时当唯一事实来源）。

    为什么要它（2026-10-07 用户提出）：自研 CLI（qqmail）没上传 GitHub、闭源 CLI（agently-cli）
    官网只有一句介绍——`<bin> --help` 是**唯一**的方法信息源。原先只采「只读清单里那几条子命令」
    的 `--help`，而裸 `<bin> --help` 会被只读闸判「缺少子命令」直接拒（`readonly_verdict` 已放行）。

    采两段：① 裸 `<bin> --help`（多数 CLI 在这里列出全部子命令 + 一句话说明，信息密度最高）；
    ② 只读清单里每条命令的 `--help`（各命令的参数形态）。都走沙箱运行时（超时/截断/审计照旧），
    失败不抛异常——采不到就返回 ok=False 让上层优雅降级。
    """
    from tools._runtime import shell_runtime as rt          # 局部 import：运行时也 import 本模块

    rules = [[t for t in c.split() if t] for c in cmds]
    adhoc = {bin_name: rules}
    blocks: list[str] = []
    ran: list[str] = []
    failed: list[str] = []

    def _grab(argv: list[str]) -> str:
        res = rt.execute(bin_name, argv, timeout=timeout, username=username,
                         account_id=account_id, adhoc=adhoc)
        return ((res.get("stdout") or "") + "\n" + (res.get("stderr") or "")).strip()

    # ① 裸 --help：命令总览。注意**不看退出码**——不少 CLI 把帮助写到 stderr 并以非 0 退出
    #    （实测 argparse 系就是这么干的），有正文就算采到。
    head = _grab(["--help"]) or _grab(["-h"])
    if head:
        blocks.append("$ %s --help\n%s" % (bin_name, head))
        ran.append(bin_name)
    else:
        failed.append("%s --help" % bin_name)

    # ② 只读清单里每条命令的 --help（有上限，免得 40 条清单把接口拖慢）
    for c in (cmds or [])[:max_cmds]:
        text = _grab(c.split() + ["--help"])
        if text:
            blocks.append("$ %s %s --help\n%s" % (bin_name, c, text))
            ran.append(c)
        else:
            failed.append("%s %s --help" % (bin_name, c))

    joined = "\n\n".join(blocks)
    note = "本机 --help 采到 %d 段" % len(ran)
    if failed:
        note += "；%d 段没采到（%s）" % (len(failed), "、".join(failed[:3]))
    if len(joined) > max_chars:
        joined = joined[:max_chars] + "\n\n…（--help 输出过长，已截断）"
        note += "；正文超 %d 字符已截断" % max_chars
    if not joined.strip():
        note = "一条 --help 都没采到（可执行名对不对？装没装？）：" + note
    return {"ok": bool(joined.strip()), "text": joined, "ran": ran, "failed": failed, "note": note}


def suggest_resolver(kinds: dict[str, str], help_map: dict | None = None) -> str:
    """在清单里挑一条「最像解析器」的命令（两步链的第 1 步用）。

    两条路（都只用**帮助原文里的证据**，不猜）：
      ① 自由文本参数的命令里，挑 help 描述写着 resolve / 解析 / to id 的那条
         （`book resolve <title>` 的原文就是 "Resolve a book title to likely bookId matches"）；
      ② flag 风格 CLI（agently-cli 这类）：没有自由文本命令可挑，就反过来看
         「需要开关参数」那条命令的帮助里**自己提到了清单里的哪条命令**——
         `message +read --help` 写着 `Requires --id (message_id from +list output)`，
         于是第 1 步就是 `message +list`。

    优先：自由文本参数 + help 描述里出现 resolve / 解析 / to id 之类字样
    （`book resolve <title>` 的原文就是 "Resolve a book title to likely bookId matches"）。
    挑不到就退回第一条自由文本命令。
    """
    free = [c for c, k in (kinds or {}).items() if k == "free_text"]
    if not free:
        # ② flag 风格：去「需要开关参数」那条命令的帮助里反查它自己提到的清单内命令
        for c, k in (kinds or {}).items():
            if k != "needs_flag":
                continue
            docs = ((help_map or {}).get(c) or {}).get("help") or ""
            if not docs:
                continue
            for other in (kinds or {}):
                if other == c:
                    continue
                toks = [t for t in other.split() if len(t) > 1]
                if toks and all(t in docs for t in toks):
                    return other
        return ""
    hm = help_map or {}
    for c in free:
        desc = ((hm.get(c) or {}).get("desc") or "") + " " + ((hm.get(c) or {}).get("usage") or "")
        if re.search(r"resolve|resolution|解析|lookup|likely\s+id|\bid\s+matches?", desc, re.I):
            return c
    return free[0]


def _match_command(rhs: str, cmds: list[str]) -> str:
    """把映射右半边（可能带参数/前缀）匹配到清单里最长的一条命令路径。"""
    hit = ""
    for c in cmds:
        if rhs == c or rhs.startswith(c + " ") or rhs.startswith(c):
            if len(c) > len(hit):
                hit = c
    return hit


def audit_ability_draft(abilities: str, kinds: dict[str, str], help_map: dict | None = None) -> list[str]:
    """审计能力描述草稿，返回**给人看的中文警告**（空列表 = 没问题）。

    四条规则全部来自 `--help` 真值，不猜：
      R1 直调了需要 ID 的命令（`book progress`）→ 应写成「<解析命令> 再 book progress」；
      R2 两步链的第 1 步自己就需要 ID（链头直接报参数错）；
      R3 两步链的第 2 步是 standalone（**真的**什么输入都不需要 → 前一步产出喂不进去 = 凭空造链）；
      R4 两步链的第 1 步是 standalone（不产出可解析 ID）；
    两条都只对 `standalone` 报——「flag 形式给值的必需输入」（`needs_flag`）不算无参数，
    且若第 2 步的帮助**自己写明**了「从第 1 步那条命令取」的证据（如 `--id (message_id from +list output)`），
    R4 也跳过：工具自己承认这条链，不该按「凭空造链」提示用户（2026-10-07 实测两处误报）。
    配套 `coverage_note()` 提示「清单里哪些命令没被描述到」。
    """
    cmds = list((kinds or {}).keys())
    warns: list[str] = []
    for seg in re.split(r"[；;\n|]", abilities or ""):
        if not _ARROW_RE.search(seg):
            continue
        lhs, rhs = _ARROW_RE.split(seg, maxsplit=1)
        rhs = rhs.strip().strip("。.,，、")
        if not rhs:
            continue
        chain = bool(re.search(r"再|然后|接着", rhs))
        steps = [x.strip() for x in re.split(r"再|然后|接着", rhs)] if chain else [rhs]
        first = _match_command(steps[0], cmds)
        second = _match_command(steps[1], cmds) if len(steps) > 1 else ""
        trigger = lhs.strip().strip("。.,，、：:")[:24]
        if not first and not second:
            # 右半边匹配不到清单里任何命令 → 提醒（这条示例会被 ability_examples 丢掉）
            # ⚠️ 别用「首词是否出现在某条命令里」判：`shelf books` 与 `shelf list` 共享首词，会被漏掉
            shown = " ".join(steps[0].split()[:3])
            warns.append("「%s」右边的 `%s` 不在只读清单里（该示例会被丢弃，不会被教给 Agent）"
                         % (trigger, shown))
            continue
        k1 = (kinds or {}).get(first, "") if first else ""
        k2 = (kinds or {}).get(second, "") if second else ""
        if not chain and k1 in ("needs_id", "needs_flag"):
            sug = suggest_resolver(kinds, help_map)
            what = "需要 ID 的" if k1 == "needs_id" else "需要开关参数的"
            if sug:
                hint = "建议写成「%s 再 %s」" % (sug, first)
            elif k1 == "needs_id":
                hint = "它需要先拿到 ID 才能调"
            else:
                hint = "它需要先拿到该开关要的输入（如 `--id`）才能调"
            warns.append("「%s」直调了%s `%s`——%s" % (trigger, what, first, hint))
        elif chain and k1 == "needs_id":
            warns.append("「%s」两步链的第 1 步 `%s` 自己就需要 ID，会直接报参数错" % (trigger, first))
        elif chain and k1 == "needs_flag":
            warns.append("「%s」两步链的第 1 步 `%s` 自己就需要开关参数，会直接报参数错" % (trigger, first))
        elif chain and k2 == "standalone":
            warns.append("「%s」两步链的第 2 步 `%s` 不需要参数——第 1 步的产出喂不进去（凭空造链）"
                         % (trigger, second))
        elif chain and k1 == "standalone" and not _docs_link_steps(first, second, help_map):
            warns.append("「%s」两步链的第 1 步 `%s` 不产出可解析的 ID" % (trigger, first))
    return warns


def _docs_link_steps(first: str, second: str, help_map: dict | None) -> bool:
    """第 2 步的帮助原文里，是否**自己写明**了「从第 1 步取输入」。

    例：`message +read --help` 写着 `Requires --id (message_id from +list output)` ——
    `+list` 正是第 1 步。工具自己承认的链，不该被审计当成「凭空造链」。
    """
    if not second:
        return False
    docs = ((help_map or {}).get(second) or {}).get("help") or ""
    if not docs:
        return False
    toks = [t for t in (first or "").split() if len(t) > 1]
    return bool(toks) and all(t in docs for t in toks)


def coverage_note(abilities: str, cmds: list[str]) -> str:
    """草稿没覆盖到的命令（提示用户可补 help 文本再生成一次）。"""
    text = abilities or ""
    miss = [c for c in cmds if c not in text and c.split()[0] not in text]
    if not miss:
        return ""
    return "只读清单里有 %d 条命令没被描述到：%s" % (len(miss), "、".join(miss[:8]) + ("…" if len(miss) > 8 else ""))


def ability_examples(account_id: int | None, max_n: int = 10, max_per_cli: int = 10) -> list[str]:
    """M5：从各 CLI 的能力描述里抽「用户说法 → 工具调用」对照，供 few-shot 注入。

    ⚠️ 条数取「覆盖该 CLI 的**全部**映射」（默认上限 10）而不是抽样两三条——
    首轮探针实测：只给 3 条示例时，弱模型会把示例里的头两条命令认成「万能命令」，
    不管问什么都调它们（问「查本周安排」也调 `today`）；覆盖全量映射后，
    每条映射的显著度均等，模型才会按问题挑场景。

    ⚠️ 安全约束：示例里的 argv **必须能在该 CLI 的只读清单里前缀命中**，否则丢弃——
    绝不能生成一条实际会被拒绝的命令示例（那等于教模型去撞只有拒绝的墙）。
    """
    out: list[str] = []
    for c in list_clis(account_id):
        if not c["live"] or not (c.get("abilities") or "").strip():
            continue
        made = 0
        for trigger, path in _parse_ability_pairs(c["abilities"]):
            tokens = path.split()
            if not tokens or len(tokens) > MAX_RULE_TOKENS:
                continue
            ok, _why = readonly_verdict(tokens, c["rules"])     # 只挑真实放行的命令
            if not ok:
                continue
            argv = json.dumps(tokens, ensure_ascii=False)
            line = (f'用户问到「{trigger}」→ run_shell_command(command="{c["bin"]}", argv={argv})')
            if line in out:
                continue
            out.append(line)
            made += 1
            if made >= max_per_cli:
                break
        if len(out) >= max_n:
            break
    return out[:max_n]


def ability_examples_block(account_id: int | None, max_n: int = 10) -> str:
    """few-shot 示例区块（拼在 CLI 简报之后注入；无可用示例时返回空串）。"""
    ex = ability_examples(account_id, max_n=max_n)
    if not ex:
        return ""
    lines = ["【怎么把用户的话对应到工具调用（照这个格式）】"]
    lines += ex
    lines.append("**先在你的能力描述里找到与用户问题最贴近的那一条场景，再按该条给出的命令调用**；"
                 "不要总用同一条命令应付不同的问题。以上只是格式示范，实际能用的命令以本区块列出的为准，"
                 "没有对应能力就直说，不要编造。")
    return "\n".join(lines)


# ---------------------------------------------------------------- 只读闸
def command_path(argv: list[str]) -> list[str]:
    """取命令路径 = 开头连续的非选项 token（遇到第一个 `-` 开头参数就停）。"""
    path: list[str] = []
    for tok in argv or []:
        if tok.startswith("-"):
            break
        path.append(tok)
    return path


def readonly_verdict(argv: list[str], readonly: list[list[str]]) -> tuple[bool, str]:
    """判断这条 argv 是否命中「只读清单」。返回 (是否放行, 原因)。"""
    for tok in argv or []:
        name = tok.split("=", 1)[0] if tok.startswith("--") else tok
        if name in WRITE_FLAGS:
            return False, f"沙箱只读，禁止落盘参数 {name}"
    if not readonly:
        return False, "该 CLI 没填只读命令清单——没清单就不放行 Agent 代跑（到你自己的终端执行）"
    path = command_path(argv)
    if not path:
        # ★ 特殊放行：裸的「帮助 / 版本」调用没有副作用，而且是「能力描述」的重要证据来源
        #   （自研 / 闭源 CLI 没有 README，只有 --help）。它比放行任一子命令**更窄**：
        #   前提是该 CLI 已经填了只读清单（= 用户已登记放行范围），且参数里只能出现这几个开关。
        #   落盘参数（--output 等）在上面已先行拒绝，所以 `--help --output x` 依旧被挡。
        if readonly and argv and all(a in HARMLESS_FLAGS for a in argv):
            return True, "命中放行：裸 `--help`/`--version`（无副作用，供能力描述取证）"
        return False, "缺少子命令（例如 `auth status`）"
    for rule in readonly:
        if path[:len(rule)] == list(rule):
            return True, "命中只读清单：" + " ".join(rule)
    return False, ("子命令 `" + " ".join(path) + "` 不在只读清单内——写操作与未列出的命令一律拒绝；"
                   "如需执行请在你自己的终端跑，或把它加进该 CLI 的只读清单")


# ---------------------------------------------------------------- 老数据迁移
def migrate_legacy(account_id: int | None) -> int:
    """把 M4c 初版（内置厂商 `third_party_clis`）里已登记的记录搬进 `user_clis`（幂等）。

    仅搬「已安装 / 已接入」的条目（未登记没意义）；bin/名称/只读清单从模板补。
    """
    if not account_id:
        return 0
    tpl = {t["key"]: t for t in templates()}
    moved = 0
    try:
        conn = get_personal_conn()
        try:
            try:
                rows = conn.execute("SELECT cli_id, state, note FROM third_party_clis WHERE account_id=?",
                                    (int(account_id),)).fetchall()
            except Exception:  # noqa: BLE001 - 老库没这张表
                return 0
            have = {r["bin"] for r in conn.execute("SELECT bin FROM user_clis WHERE account_id=?",
                                                   (int(account_id),)).fetchall()}
            for r in rows:
                t = tpl.get(r["cli_id"])
                if not t or r["state"] not in LIVE_STATES or t["bin"] in have:
                    continue
                conn.execute(
                    "INSERT INTO user_clis (account_id, name, bin, install_cmd, auth_cmd, docs, readonly, state, note, updated_at) "
                    "VALUES (?,?,?,?,?,?,?,?,?, datetime('now','localtime'))",
                    (int(account_id), t["name"], t["bin"], t["install_cmd"], t["auth_cmd"],
                     t["docs"], t["readonly"], r["state"], r["note"] or ""))
                moved += 1
            if moved:
                conn.commit()
        finally:
            conn.close()
    except Exception:  # noqa: BLE001 - 迁移失败不能影响主流程
        return 0
    return moved
