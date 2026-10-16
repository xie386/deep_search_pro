"""06 · 内置写工具集：list / read / create / edit / delete，作用于**当前启用的工作目录**。

★ **工具名带 `workdir_` 前缀是必需的**（方案 §06 ③ D 原命名 `list_file`/`read_file`/… 据此修正）：
  `api/server.py` 的 `_EXCLUDED_FS_TOOLS` 交给 deepagents 的 `_ToolExclusionMiddleware`，
  它在 `wrap_model_call` 里做 `[t for t in request.tools if name not in excluded]` —— **按名字**剥掉
  框架内置文件系统工具，而名单里已经有 `read_file` 与 `edit_file`。同名会被一并剥掉、模型根本看不到。

★ **写操作两关**（缺一不可）：
  1. 路径关：`workdir_guard.resolve_in_workdir`（拒 `..` / 绝对路径逃逸 / UNC）；
  2. 门禁关：`workdir_guard.assert_writable`（保护名单 fail-closed、`agents_docs` 禁区、凭据红线）。
  另加体积闸：单次写入/读取上限 `WORKDIR_MAX_WRITE_MB`（设置项，默认 1MB）。

★ **没有"授权关"**（用户 2026-10-17 裁定，见 `Verdict/06` 对裁定 7 的修订）：内置写工具
  **不需要「/」授权** —— 纯文字提问即可用。原先的运行期闸只拦内置工具、而「/」第二组又只列
  **自配来源**，导致内置写工具两条路都不通、对没配写池来源的账号完全不可用。现在闸已删除；
  「/」授权名单只用于**自配来源**的写能力（CLI 写子命令，见 `tools/_runtime/shell_runtime.execute`）。

归属：`get_owner_context()` → account_id → 工作目录（`workdir_store.active`，默认账号沙箱）。
"""
from __future__ import annotations

from pathlib import Path

from langchain_core.tools import tool

from api.context import get_owner_context
from api.monitor import monitor
from tools import workdir_guard as guard
from tools import workdir_store
from utils import audit
from utils.settings import get_int

_MAX_LIST = 200          # 单次列目录条数上限
_MAX_SCAN = 2000         # 树扫描条目上限


def _owner() -> tuple[str | None, int | None]:
    """当前账号的 (username, account_id)。

    与 `tools/shell_executor.py::_owner` 同款（那份是私有的，这里不跨模块引用私有名）：
    查库失败退回"无账号"，此时工作目录回落公共沙箱、且写操作照样过门禁。
    """
    oid = get_owner_context()
    if oid is None:
        return None, None
    try:
        from tools.schema_personal import get_personal_conn
        conn = get_personal_conn()
        try:
            row = conn.execute("SELECT username FROM accounts WHERE id=?", (oid,)).fetchone()
        finally:
            conn.close()
        return (row["username"] if row else None), oid
    except Exception:                       # noqa: BLE001
        return None, None


def _ctx():
    """(工作目录, username, account_id)。"""
    username, account_id = _owner()
    wd, _entry = workdir_store.active(account_id, username)
    return Path(wd), username, account_id


def _max_bytes() -> int:
    try:
        return max(1, get_int("WORKDIR_MAX_WRITE_MB", 1)) * 1024 * 1024
    except Exception:                       # noqa: BLE001
        return 1024 * 1024


def _audit(action: str, path: str, ok_: bool, detail: str = "", account_id=None, workdir=None) -> None:
    try:
        audit.write_event("workdir", action, account_id=account_id, path=path,
                          ok=bool(ok_), detail=(detail or "")[:300], workdir=str(workdir or ""))
    except Exception:                       # noqa: BLE001
        pass


def _human(n: int) -> str:
    for unit, div in (("MB", 1024 * 1024), ("KB", 1024)):
        if n >= div:
            return "%.1f%s" % (n / div, unit)
    return "%dB" % n


@tool
def workdir_list(subdir: str = "") -> str:
    """列出当前工作目录（或其中某个子目录）的文件树。

    写操作（create/edit/delete）需要用户先在输入框用「/」选中写工具集；本工具只读，随时可用。

    Args:
        subdir: 相对工作目录的子目录，留空 = 工作目录根。

    Returns:
        文本形式的文件树（含体积），或中文错误说明。
    """
    wd, uname, aid = _ctx()
    monitor.report_tool("workdir_list", {"subdir": subdir or ".", "workdir": str(wd)})
    try:
        root = guard.resolve_in_workdir(subdir, wd) if subdir.strip() else wd
    except ValueError as e:
        return "列目录失败：%s" % e
    if not root.is_dir():
        return "列目录失败：%s 不是目录。（看保护名单请直接用 read 读 %s）" % (root, guard.PROTECT_FILE)
    lines, shown, truncated = [], 0, False
    scanned = 0
    for p in sorted(root.rglob("*")):
        scanned += 1
        if scanned > _MAX_SCAN:
            truncated = True
            break
        try:
            rel = p.relative_to(wd).as_posix()
        except ValueError:
            continue
        if p.is_dir():
            continue
        if shown >= _MAX_LIST:
            truncated = True
            break
        shown += 1
        tag = ""
        if guard.is_credential(p, wd):
            tag = "  [凭据类，不提供查看]"
            size = "—"
        else:
            try:
                size = _human(p.stat().st_size)
            except OSError:
                size = "?"
        lines.append("%-58s %8s%s" % (rel, size, tag))
    head = "工作目录 %s（当前启用）%s" % (wd, "，子目录 %s" % subdir if subdir.strip() else "")
    body = "\n".join(lines) if lines else "（空）"
    tail = "\n… 条目过多已截断（上限 %d 条）" % _MAX_LIST if truncated else ""
    return "%s\n%s%s" % (head, body, tail)


def _read_text(path: str, start: int = 1, lines: int = 0) -> str:
    """读工作目录里的文本文件（workdir_read 与 workdir_edit(op=read) 共用）。"""
    wd, uname, aid = _ctx()
    try:
        p = guard.resolve_in_workdir(path, wd)
    except ValueError as e:
        return "读取失败：%s" % e
    reason = guard.check_readable(p, wd)
    if reason:
        return "读取被拒绝：%s" % reason
    if not p.is_file():
        return "读取失败：%s 不是文件（或不存在）。用 workdir_list 看目录内容。" % path
    try:
        size = p.stat().st_size
    except OSError as e:
        return "读取失败：%s" % e
    if size > _max_bytes() * 4:
        return ("读取失败：%s 有 %s，超过单次读取上限（工作目录设置里的单次读写上限）。"
                "请分段读：指定 start 与 lines。" % (path, _human(size)))
    try:
        text = p.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        return "读取失败：%s" % e
    try:
        s = max(1, int(start or 1))
        n = int(lines or 0)
    except (TypeError, ValueError):
        s, n = 1, 0
    all_lines = text.splitlines()
    chunk = all_lines[s - 1:] if n <= 0 else all_lines[s - 1:s - 1 + n]
    out = "\n".join(chunk)
    if len(out) > _max_bytes():
        out = out[:_max_bytes()]
        out += "\n… 已按单次上限 %s 截断" % _human(_max_bytes())
    _audit("read", str(p), True, "%d lines" % len(chunk), aid, wd)
    return "文件 %s（共 %d 行，本次第 %d 行起 %d 行）：\n%s" % (
        path, len(all_lines), s, len(chunk), out)


@tool
def workdir_read(path: str, start: int = 1, lines: int = 0) -> str:
    """读取当前工作目录里某个文件的内容（整份或其中一段）。

    凭据类文件（含 key/token/secret 或点开头目录）与 agents_docs 画像目录**不提供读取**。

    Args:
        path: 相对工作目录的文件路径，如 "notes.md"、"data/2026.csv"。
        start: 起始行号（从 1 开始）。
        lines: 读取行数；0 = 读到结尾（受单次上限约束）。

    Returns:
        文件内容（超上限会截断并说明），或中文错误说明。
    """
    monitor.report_tool("workdir_read", {"path": path})
    return _read_text(path, start, lines)


@tool
def workdir_create(path: str, content: str) -> str:
    """在当前工作目录里新建一个文本文件（**已存在则拒绝**，避免误覆盖）。

    ★ 需要用户先在输入框用「/」选中写工具集，否则本轮会被运行期闸拒绝。

    Args:
        path: 相对工作目录的新文件路径，如 "notes/todo.md"。
        content: 文件内容（纯文本，UTF-8）。

    Returns:
        成功说明，或中文错误/拒绝说明。
    """
    wd, uname, aid = _ctx()
    monitor.report_tool("workdir_create", {"path": path, "len": len(content or "")})
    try:
        p = guard.resolve_in_workdir(path, wd)
    except ValueError as e:
        return "创建失败：%s" % e
    reason = guard.assert_writable(p, wd)
    if reason:
        _audit("create", str(p), False, reason, aid, wd)
        return "创建被拒绝：%s" % reason
    if p.exists():
        return "创建失败：%s 已存在。要改内容请用 workdir_edit（op=append 或 replace）。" % path
    blob = (content or "").encode("utf-8")
    if len(blob) > _max_bytes():
        return "创建失败：内容 %s 超过单次写入上限 %s（见工作目录设置）。" % (_human(len(blob)), _human(_max_bytes()))
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(blob)
    except OSError as e:
        _audit("create", str(p), False, str(e), aid, wd)
        return "创建失败：%s" % e
    _audit("create", str(p), True, "%s" % _human(len(blob)), aid, wd)
    return "已创建 %s（%s）" % (path, _human(len(blob)))


@tool
def workdir_edit(path: str, op: str, content: str = "", anchor: str = "", occurrence: int = 1) -> str:
    """修改当前工作目录里已有文件的内容（**增删改查合一**）。

    ★ 需要用户先在输入框用「/」选中写工具集（`op=read` 不需要）。
    ★ 触及**保护名单**里的文件时，只有 `op=read` 可用，其余操作会被拒绝。

    Args:
        path: 相对工作目录的文件路径。
        op: 操作，取 "read"（读内容）/ "append"（在末尾追加）/ "replace"（替换指定片段）/ "delete"（删除指定片段）。
        content: append/replace 要写入的文本；delete 忽略。
        anchor: replace/delete 用来定位的原文片段（必须能在文件里找到）。
        occurrence: anchor 出现多次时替换第几次（从 1 开始，默认第 1 次）。

    Returns:
        操作结果说明，或中文错误/拒绝说明。
    """
    wd, uname, aid = _ctx()
    monitor.report_tool("workdir_edit", {"path": path, "op": op})
    op = (op or "").strip().lower()
    if op not in ("read", "append", "replace", "delete"):
        return "编辑失败：op 只能是 read / append / replace / delete（收到 %r）" % op
    try:
        p = guard.resolve_in_workdir(path, wd)
    except ValueError as e:
        return "编辑失败：%s" % e
    if op == "read":
        return _read_text(path)          # 受保护文件在这里同样只读（保护名单只拦写）
    reason = guard.assert_writable(p, wd)
    if reason:
        _audit("edit", str(p), False, "%s | %s" % (op, reason), aid, wd)
        return "编辑被拒绝：%s" % reason
    if not p.is_file():
        return "编辑失败：%s 不是文件（或不存在）。要新建请用 workdir_create。" % path
    try:
        text = p.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        return "编辑失败：%s" % e
    if op == "append":
        blob = text + (content or "")
    else:
        if not anchor:
            return "编辑失败：op=%s 必须给 anchor（要定位的原文片段）。" % op
        if anchor not in text:
            return "编辑失败：在 %s 里找不到 anchor 片段。请先读文件确认原文（注意换行与空格）。" % path
        idx, seen = -1, 0
        k = max(1, int(occurrence or 1))
        while True:
            idx = text.find(anchor, idx + 1)
            if idx < 0:
                return "编辑失败：anchor 在 %s 里只出现不到 %d 次。" % (path, k)
            seen += 1
            if seen >= k:
                break
        if op == "replace":
            blob = text[:idx] + (content or "") + text[idx + len(anchor):]
        else:
            blob = text[:idx] + text[idx + len(anchor):]
    raw = blob.encode("utf-8")
    if len(raw) > _max_bytes():
        return "编辑失败：结果 %s 超过单次写入上限 %s。" % (_human(len(raw)), _human(_max_bytes()))
    try:
        p.write_bytes(raw)
    except OSError as e:
        _audit("edit", str(p), False, str(e), aid, wd)
        return "编辑失败：%s" % e
    _audit("edit", str(p), True, op, aid, wd)
    verb = {"append": "已追加", "replace": "已替换", "delete": "已删除片段"}[op]
    return "%s：%s（现 %s）" % (verb, path, _human(len(raw)))


@tool
def workdir_delete(path: str) -> str:
    """把当前工作目录里的文件或目录**移入回收站**（软删，可在文件管理器里还原）。

    ★ 需要用户先在输入框用「/」选中写工具集，否则本轮会被运行期闸拒绝。
    ★ 保护名单命中的文件、名单文件自身、`agents_docs` 画像目录一律拒绝。

    Args:
        path: 相对工作目录的路径（文件或目录）。

    Returns:
        成功说明（含回收站落点），或中文错误/拒绝说明。
    """
    wd, uname, aid = _ctx()
    monitor.report_tool("workdir_delete", {"path": path})
    try:
        p = guard.resolve_in_workdir(path, wd)
    except ValueError as e:
        return "删除失败：%s" % e
    if p == Path(wd).resolve():
        return "删除失败：不能删除工作目录根目录本身。"
    if not p.exists():
        return "删除失败：%s 不存在。" % path
    reason = guard.assert_writable(p, wd)
    if reason:
        _audit("delete", str(p), False, reason, aid, wd)
        return "删除被拒绝：%s" % reason
    try:
        dst = guard.recycle(wd, p)
    except OSError as e:
        _audit("delete", str(p), False, str(e), aid, wd)
        return "删除失败：%s" % e
    _audit("delete", str(p), True, "→ %s" % dst.name, aid, wd)
    return "已移入回收站：%s → %s/%s（可在文件管理器里还原）" % (path, guard.RECYCLE_DIR, dst.name)
