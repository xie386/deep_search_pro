"""06 · 工作目录守卫：保护名单 / 凭据红线 / 路径解析 / 回收站。

定案见 `docs/v4.0/Verdict/06-Agent工作目录前端可编辑可视化与写能力开发.md`
（裁定 3 保护名单 fail-closed、裁定 5 画像目录专属边界），方案见规划文档 §06 ③ C。

三条硬规则：
  1. **fail-closed**：`_保护名单.md` 读不到或解析失败 → **一律拒绝写**；
  2. 名单文件**自身**永远不可写、不可删；`agents_docs/`（画像目录）是 `readtofile` /
     `writetofile` 专属，本模块对其**读写都拒**；
  3. 凭据类（名字含 key / token / secret / credential / password，以及**以 `.` 开头**的
     目录与文件）**连读都拒绝**。

本模块**不 import `tools/_runtime/shell_runtime.py`**（反向依赖，那边要 import 本模块做
`mkdir` 守卫），只依赖标准库。
"""
from __future__ import annotations

import fnmatch
import os
import re
import shutil
import time
from pathlib import Path

PROTECT_FILE = "_保护名单.md"
RECYCLE_DIR = ".回收站"
FORBIDDEN_DIRS = ("agents_docs",)          # 画像目录专属（Verdict/06 裁定 5）

PROTECT_SEED = """# 保护名单（Agent 不可修改本文件；一行一条，# 开头是注释）
#
# 写法一：纯文件名       —— 工作目录下任意层级出现这个文件名，一律保护
# 写法二：以 / 结尾      —— 递归保护该目录及其下全部内容
# 写法三：含 * 或 ?      —— 按相对路径做 glob 匹配，如 data/*.csv
#
# 命中保护的文件：Agent 的写工具一律拒绝（edit_file 退化为只能读与定位）；
# 你自己在前端删除受保护文件是允许的，但会二次确认。
MEMORY.md
"""

_CRED_RE = re.compile(r"(key|token|secret|credential|password|passwd)", re.I)


# ---------------------------------------------------------------- 保护名单
def protect_path(workdir: Path | str) -> Path:
    return Path(workdir) / PROTECT_FILE


def seed_protect(workdir: Path | str) -> bool:
    """建工作目录时播种保护名单；已存在则不动。返回是否新建。"""
    p = protect_path(workdir)
    if p.exists():
        return False
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(PROTECT_SEED, encoding="utf-8")
        return True
    except OSError:
        return False


def parse_protect(workdir: Path | str) -> list[str] | None:
    """读保护名单规则。**读不到 / 解析失败返回 None**（调用方按 fail-closed 处理）。"""
    p = protect_path(workdir)
    try:
        raw = p.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    rules: list[str] = []
    for line in raw.splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        rules.append(s.replace("\\", "/"))
    return rules


def _match_rule(rel: str, name: str, rules: list[str]) -> str | None:
    """rel = 相对工作目录的正斜杠路径；name = 文件名。返回命中的规则原文。"""
    for rule in rules:
        if rule.endswith("/"):                                # 目录前缀：递归
            pref = rule.rstrip("/")
            if rel == pref or rel.startswith(pref + "/") or name == pref:
                return rule
        elif any(ch in rule for ch in "*?["):                 # glob
            if fnmatch.fnmatch(rel, rule) or fnmatch.fnmatch(name, rule):
                return rule
        else:                                                 # 纯文件名（任意层级）
            if name == rule or rel == rule:
                return rule
    return None


def protected_reason(target: Path | str, workdir: Path | str) -> str | None:
    """命中保护名单则返回中文理由，否则 None。名单缺失/解析失败 → 返回理由（fail-closed）。"""
    wd = Path(workdir).resolve()
    t = Path(target).resolve()
    try:
        rel = t.relative_to(wd).as_posix()
    except ValueError:
        return "目标不在工作目录内"
    rules = parse_protect(wd)
    if rules is None:
        return ("保护名单（%s）读不到或无法解析 —— 按 fail-closed 一律拒绝写。"
                "请在文件管理器里检查该文件是否存在、编码是否为 UTF-8。" % PROTECT_FILE)
    hit = _match_rule(rel, t.name, rules)
    if hit:
        return "命中保护名单规则 `%s`（改名单请在工作目录里直接编辑 %s，Agent 无权修改它）" % (hit, PROTECT_FILE)
    return None


# ---------------------------------------------------------------- 凭据红线
def is_credential(target: Path | str, workdir: Path | str | None = None) -> bool:
    """凭据类：名字含 key/token/secret/credential/password，或以 `.` 开头的目录/文件。

    ★ 有 `workdir` 时**只看相对工作目录的部分** —— 否则用户把工作目录放在
    `D:\\my-secret-notes\\` 这类父目录下，整棵树都会被误判成凭据。
    """
    p = Path(target)
    if workdir is not None:
        try:
            p = Path(target).resolve().relative_to(Path(workdir).resolve())
        except ValueError:
            pass                                  # 不在工作目录内 → 退回全路径（更保守）
    for part in p.parts:
        if part in ("/", "\\", ""):
            continue
        if part.startswith("."):
            return True
        if _CRED_RE.search(part):
            return True
    return False


def is_forbidden_dir(target: Path | str) -> bool:
    """画像目录（agents_docs）专属，读写都拒（Verdict/06 裁定 5）。"""
    return any(part in FORBIDDEN_DIRS for part in Path(target).parts)


# ---------------------------------------------------------------- 统一判定
def _in_recycle(target: Path | str, workdir: Path | str) -> bool:
    try:
        return Path(target).resolve().relative_to((Path(workdir) / RECYCLE_DIR).resolve()) is not None
    except ValueError:
        return False


def assert_writable(target: Path | str, workdir: Path | str) -> str | None:
    """写操作统一门禁。None = 放行；否则返回中文拒绝理由。"""
    t = Path(target)
    wd = Path(workdir)
    if is_forbidden_dir(t):
        return ("%s 是专属文件夹，其他写工具不得越界；要改画像请用 write_agent_doc。"
                % "/".join(FORBIDDEN_DIRS))
    if t.name == PROTECT_FILE:
        return "%s 是保护名单本身，永远不可被写工具修改或删除。" % PROTECT_FILE
    if _in_recycle(t, wd):                     # 软删目标在回收站内 —— 放行（否则删不进回收站）
        return None
    if is_credential(t, wd):
        return "该路径属于凭据类（含 key/token/secret 或点开头目录），写工具不得触碰。"
    return protected_reason(t, wd)


def check_readable(target: Path | str, workdir: Path | str) -> str | None:
    """读操作门禁：凭据类与画像目录连读都拒；保护名单文件本身可读（要看规则）。"""
    t = Path(target)
    if is_forbidden_dir(t):
        return "%s 是专属文件夹，不在工作目录里，无法查看。" % "/".join(FORBIDDEN_DIRS)
    if is_credential(t, workdir) and t.name != PROTECT_FILE:
        return "该路径属于凭据类（含 key/token/secret 或点开头目录），不提供查看与预览。"
    return None


# ---------------------------------------------------------------- 路径解析
def resolve_in_workdir(raw: str, workdir: Path | str) -> Path:
    """把用户/Agent 给的相对路径解析到工作目录内；越界抛 ValueError（中文说明）。

    与 `shell_runtime._resolve_in_sandbox` 同款校验（空字节 / UNC / resolve / parents），
    这里独立实现是为了**保持单向依赖**：shell_runtime 要 import 本模块做 mkdir 守卫。
    """
    wd = Path(workdir).resolve()
    s = (raw or "").strip().strip('"').strip("'")
    if not s:
        raise ValueError("路径为空")
    if "\x00" in s:
        raise ValueError("路径含空字节")
    if s.startswith(("\\\\", "//")):
        raise ValueError("拒绝 UNC / 设备路径：%s" % s)
    p = Path(s)
    try:
        p = p.resolve() if p.is_absolute() else (wd / s).resolve()
    except (OSError, ValueError) as e:
        raise ValueError("路径无法解析：%s（%s）" % (s, e))
    if p != wd and wd not in p.parents:
        raise ValueError("路径逃出工作目录：%s\n工作目录只允许访问 %s 内部的路径。" % (s, wd))
    return p


# ---------------------------------------------------------------- 回收站
INDEX_FILE = "_index.json"


def _trash_dir(workdir: Path | str) -> Path:
    return Path(workdir) / RECYCLE_DIR


def _load_index(trash: Path) -> dict:
    """回收站索引：记下每个条目**原在工作目录里的相对路径**（删自子目录也能原位还原）。"""
    try:
        import json
        raw = (trash / INDEX_FILE).read_text(encoding="utf-8")
        d = json.loads(raw)
        return d if isinstance(d, dict) else {}
    except Exception:                       # noqa: BLE001 —— 索引坏了不阻断删除
        return {}


def _save_index(trash: Path, d: dict) -> None:
    try:
        import json
        trash.mkdir(parents=True, exist_ok=True)
        (trash / INDEX_FILE).write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
    except OSError:
        pass


def recycle(workdir: Path | str, target: Path | str) -> Path:
    """软删：移入 `.回收站/{时间戳}_{原名}` 并在索引里记下原相对路径，返回落点。"""
    wd = Path(workdir)
    trash = _trash_dir(wd)
    trash.mkdir(parents=True, exist_ok=True)
    src = Path(target)
    try:
        rel = src.resolve().relative_to(wd.resolve()).as_posix()
    except ValueError:
        rel = src.name
    ts = time.strftime("%Y%m%d_%H%M%S")
    dst = trash / ("%s_%s" % (ts, src.name))
    n = 1
    while dst.exists():
        dst = trash / ("%s_%s(%d)" % (ts, src.name, n))
        n += 1
    shutil.move(str(src), str(dst))
    idx = _load_index(trash)
    idx[dst.name] = {"rel": rel, "orig": src.name, "ts": ts,
                     "is_dir": bool(dst.is_dir()), "deleted_at": int(time.time())}
    _save_index(trash, idx)
    return dst


def trash_entries(workdir: Path | str) -> list[dict]:
    """回收站条目（新→旧）。索引缺失时按文件名反推，保证列表可用。"""
    trash = _trash_dir(workdir)
    if not trash.is_dir():
        return []
    idx = _load_index(trash)
    out = []
    for child in trash.iterdir():
        if child.name == INDEX_FILE:
            continue
        meta = idx.get(child.name) or {}
        try:
            st = child.stat()
        except OSError:
            continue
        out.append({"name": child.name, "rel": meta.get("rel") or child.name.split("_", 2)[-1],
                    "orig": meta.get("orig") or child.name.split("_", 2)[-1],
                    "is_dir": child.is_dir(), "size": 0 if child.is_dir() else st.st_size,
                    "deleted_at": int(meta.get("deleted_at") or st.st_mtime)})
    out.sort(key=lambda x: x["deleted_at"], reverse=True)
    return out


def trash_restore(workdir: Path | str, name: str, rel_target: str | None = None) -> Path:
    """把回收站条目还原回工作目录（默认还原到原相对路径）。"""
    wd = Path(workdir)
    trash = _trash_dir(wd)
    src = trash / Path(name).name                   # 只取文件名，防目录穿越
    if not src.exists():
        raise FileNotFoundError("回收站里没有条目：%s" % name)
    idx = _load_index(trash)
    meta = idx.get(src.name) or {}
    rel = (rel_target or meta.get("rel") or meta.get("orig") or src.name).replace("\\", "/").lstrip("/")
    dst = (wd / rel).resolve()
    if dst != wd.resolve() and wd.resolve() not in dst.parents:
        raise ValueError("还原目标逃出工作目录：%s" % rel)
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists():
        stem, suf = dst.stem, dst.suffix
        k = 1
        while dst.exists():
            dst = dst.parent / ("%s(还原%d)%s" % (stem, k, suf))
            k += 1
    shutil.move(str(src), str(dst))
    idx.pop(src.name, None)
    _save_index(trash, idx)
    return dst


def trash_purge(workdir: Path | str, name: str | None = None) -> int:
    """彻底删除：给 name 删单个，不给则清空回收站。返回删除条数。"""
    trash = _trash_dir(workdir)
    if not trash.is_dir():
        return 0
    idx = _load_index(trash)
    targets = [trash / Path(name).name] if name else [c for c in trash.iterdir() if c.name != INDEX_FILE]
    n = 0
    for tgt in targets:
        if not tgt.exists() or tgt.name == INDEX_FILE:
            continue
        try:
            shutil.rmtree(tgt) if tgt.is_dir() else tgt.unlink()
            idx.pop(tgt.name, None)
            n += 1
        except OSError:
            continue
    _save_index(trash, idx)
    return n


def purge_expired_trash(workdir: Path | str, ttl_days: int) -> int:
    """清理回收站里超过 ttl_days 天的条目，返回清理条数。"""
    trash = Path(workdir) / RECYCLE_DIR
    if not trash.is_dir():
        return 0
    cutoff = time.time() - max(1, int(ttl_days)) * 86400
    idx = _load_index(trash)
    n = 0
    for child in trash.iterdir():
        if child.name == INDEX_FILE:
            continue
        try:
            if child.stat().st_mtime < cutoff:
                shutil.rmtree(child) if child.is_dir() else child.unlink()
                idx.pop(child.name, None)
                n += 1
        except OSError:
            continue
    _save_index(trash, idx)
    return n
