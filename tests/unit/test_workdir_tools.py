# -*- coding: utf-8 -*-
"""06 · 内置写工具集与运行期闸单元测试 —— 全离线、全临时目录、不写真实审计。

**为什么要有它**：
  ① 写工具是**唯一**会改动用户文件的通道，必须有"未授权就不执行"的硬闸（不是提示词纪律）；
  ② 名字踩坑要钉住：`read_file` / `edit_file` 这类名字会被 deepagents 的
     `_ToolExclusionMiddleware` 按名剥掉（名单里就有这两个），所以工具名一律 `workdir_` 前缀；
  ③ 写路径必须过保护名单（fail-closed）与凭据红线，删只能进回收站。
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))


def _setup(monkeypatch, tmp_path, protect=None):
    """把工具的作用域指到临时工作目录；审计改成 no-op（绝不许写真实审计文件）。"""
    from tools import workdir_tools as wt, workdir_guard as g
    wd = tmp_path / "wd"; wd.mkdir()
    (wd / g.PROTECT_FILE).write_text(protect if protect is not None else "MEMORY.md\n", encoding="utf-8")
    monkeypatch.setattr(wt, "_ctx", lambda: (wd, "t_u", 3))
    monkeypatch.setattr(wt, "_audit", lambda *a, **k: None)
    # ★ 2026-10-16 事故：这里必须把**库**也隔离。之前用 (wd, "u", 3) 直接跑到 store.list_dirs()，
    #   而 store 用的是真实库 → 给 account_id=3（尼古喵喵）写进一条「主页」指向 user_data/sandbox/u，
    #   真机表现为 CLI 面板的 cwd 显示成 sandbox\u。教训：凡是要落库的入口，测试都要换临时库。
    from tools import workdir_store as st, schema_personal as sp
    db = tmp_path / "t.db"
    monkeypatch.delenv("PERSONAL_DB_PATH", raising=False)   # ★ 环境变量优先级高于 DB_PATH，必须先清
    monkeypatch.setattr(sp, "DB_PATH", str(db))
    assert sp.db_path() == str(db), "★ 隔离没生效（库路径没指到临时库）—— 绝不许碰真实库" 
    monkeypatch.setattr(st, "get_personal_conn", sp.get_personal_conn)
    monkeypatch.setattr(st, "ensure_tables", sp.ensure_tables)
    monkeypatch.setattr(st, "seed_protect", g.seed_protect)
    monkeypatch.setattr(st, "sandbox_dir", lambda name=None: wd)
    sp.ensure_tables()
    conn = sp.get_personal_conn()                 # account_workdirs 有 FK → 先造账号行
    conn.execute("INSERT INTO accounts (id, username, password_hash) VALUES (3,'t_u','x')")
    conn.commit(); conn.close()
    return wt, g, wd


def _call(tool, **kw):
    """@tool 包装的函数直接调用底层实现（不经过 Agent 运行时）。"""
    return tool.invoke(kw)


# ------------------------------------------------------- 授权口径（2026-10-17 用户裁定）
def test_builtin_write_tools_need_no_authorization(monkeypatch, tmp_path):
    """★ 内置写工具**不需要**「/」授权：纯文字提问就该能用。

    背景（用户 2026-10-17 报障）：原先内置写工具被运行期闸拦住，而「/」第二组只列**自配来源**、
    根本没有内置那一项 → 两条路都不通 → 对没配写池来源的账号等于完全不可用。
    现在内置写工具不过闸；该管的约束仍在别处：保护名单 fail-closed、凭据红线、路径逃逸、
    删除只进回收站（见下面的用例）。
    """
    from utils import write_auth
    write_auth.clear()
    assert not write_auth.authorized()                      # 本轮没有任何授权
    wt, g, wd = _setup(monkeypatch, tmp_path)
    _call(wt.workdir_create, path="没授权也能建.md", content="hello")     # 不拒、不抛
    assert (wd / "没授权也能建.md").read_text(encoding="utf-8") == "hello"
    _call(wt.workdir_delete, path="没授权也能建.md")
    assert not (wd / "没授权也能建.md").exists()             # 删除生效（且进的是回收站）


def test_write_auth_record_semantics():
    """授权记录本体（`utils/write_auth`）：set / clear 与"名字或引用"两种匹配。"""
    from utils import write_auth
    write_auth.clear()
    assert write_auth.authorized() == frozenset() and not write_auth.is_authorized("arch")
    write_auth.set_selected(["归档CLI", "arch", "", None])
    assert write_auth.is_authorized("归档CLI") and write_auth.is_authorized("arch")
    assert not write_auth.is_authorized("别的CLI") and not write_auth.is_authorized("")
    write_auth.clear()
    assert not write_auth.is_authorized("arch")


def test_write_gate_module_is_gone():
    """旧运行期闸模块已删除 —— 万一有人再 import，这里立刻炸出来（防悄悄回退口径）。"""
    import importlib
    import pytest as _pytest
    with _pytest.raises(ModuleNotFoundError):
        importlib.import_module("agent.write_gate")


# ------------------------------------------------------------ 工具行为
def test_create_read_list_roundtrip(monkeypatch, tmp_path):
    wt, g, wd = _setup(monkeypatch, tmp_path)
    assert "已创建" in _call(wt.workdir_create, path="notes/todo.md", content="第一行\n第二行")
    assert (wd / "notes" / "todo.md").read_text(encoding="utf-8") == "第一行\n第二行"
    out = _call(wt.workdir_read, path="notes/todo.md")
    assert "第一行" in out and "共 2 行" in out
    tree = _call(wt.workdir_list)
    assert "notes/todo.md" in tree


def test_create_refuses_overwrite(monkeypatch, tmp_path):
    wt, g, wd = _setup(monkeypatch, tmp_path)
    _call(wt.workdir_create, path="a.md", content="x")
    assert "已存在" in _call(wt.workdir_create, path="a.md", content="y")
    assert (wd / "a.md").read_text(encoding="utf-8") == "x"     # 原内容没被覆盖


def test_edit_append_replace_delete(monkeypatch, tmp_path):
    wt, g, wd = _setup(monkeypatch, tmp_path)
    _call(wt.workdir_create, path="a.md", content="alpha\nbeta\n")
    assert "已追加" in _call(wt.workdir_edit, path="a.md", op="append", content="gamma\n")
    assert "已替换" in _call(wt.workdir_edit, path="a.md", op="replace", anchor="beta", content="BETA")
    assert "已删除片段" in _call(wt.workdir_edit, path="a.md", op="delete", anchor="BETA")
    text = (wd / "a.md").read_text(encoding="utf-8")
    # delete 只删掉 anchor 那段本身，前后换行原样保留 —— 这就是"删减内容"的字面语义
    assert text == "alpha\n\ngamma\n"
    assert "找不到 anchor" in _call(wt.workdir_edit, path="a.md", op="replace", anchor="不存在", content="x")
    assert "op 只能是" in _call(wt.workdir_edit, path="a.md", op="explode")


def test_edit_occurrence(monkeypatch, tmp_path):
    wt, g, wd = _setup(monkeypatch, tmp_path)
    _call(wt.workdir_create, path="b.md", content="x\nx\n")
    _call(wt.workdir_edit, path="b.md", op="replace", anchor="x", content="y", occurrence=2)
    assert (wd / "b.md").read_text(encoding="utf-8") == "x\ny\n"


def test_delete_goes_to_recycle_not_gone(monkeypatch, tmp_path):
    wt, g, wd = _setup(monkeypatch, tmp_path)
    _call(wt.workdir_create, path="c.md", content="x")
    assert "已移入回收站" in _call(wt.workdir_delete, path="c.md")
    assert not (wd / "c.md").exists()
    items = g.trash_entries(wd)                       # 用正式 API 取（回收站里另有 _index.json）
    assert len(items) == 1 and items[0]["orig"] == "c.md" and items[0]["rel"] == "c.md"
    assert (wd / g.RECYCLE_DIR / items[0]["name"]).read_text(encoding="utf-8") == "x"   # 可还原


# ------------------------------------------------------------ 门禁交互
def test_protected_file_cannot_be_written_or_deleted(monkeypatch, tmp_path):
    wt, g, wd = _setup(monkeypatch, tmp_path, protect="MEMORY.md\n")
    (wd / "MEMORY.md").write_text("画像摘要", encoding="utf-8")
    assert "创建被拒绝" in _call(wt.workdir_create, path="MEMORY.md", content="篡改")
    assert "编辑被拒绝" in _call(wt.workdir_edit, path="MEMORY.md", op="replace", anchor="画像", content="改")
    assert "删除被拒绝" in _call(wt.workdir_delete, path="MEMORY.md")
    assert (wd / "MEMORY.md").read_text(encoding="utf-8") == "画像摘要"    # 一字未动
    assert "画像摘要" in _call(wt.workdir_edit, path="MEMORY.md", op="read")  # 退化为只读


def test_fail_closed_when_protect_list_missing(monkeypatch, tmp_path):
    """名单读不到 → 写一律拒绝（绝不"当无保护放行"）。"""
    wt, g, wd = _setup(monkeypatch, tmp_path)
    (wd / g.PROTECT_FILE).unlink()
    out = _call(wt.workdir_create, path="new.md", content="x")
    assert "fail-closed" in out and not (wd / "new.md").exists()


def test_protect_list_itself_cannot_be_touched(monkeypatch, tmp_path):
    wt, g, wd = _setup(monkeypatch, tmp_path)
    assert "永远不可" in _call(wt.workdir_edit, path=g.PROTECT_FILE, op="append", content="x")
    assert "永远不可" in _call(wt.workdir_delete, path=g.PROTECT_FILE)


def test_credential_not_readable_but_listed(monkeypatch, tmp_path):
    wt, g, wd = _setup(monkeypatch, tmp_path)
    (wd / "all_keys.json").write_text("{}", encoding="utf-8")
    assert "不提供查看" in _call(wt.workdir_read, path="all_keys.json")
    assert "凭据类" in _call(wt.workdir_list)


def test_path_escape_rejected(monkeypatch, tmp_path):
    wt, g, wd = _setup(monkeypatch, tmp_path)
    assert "逃出工作目录" in _call(wt.workdir_create, path="../evil.md", content="x")
    assert not (tmp_path / "evil.md").exists()
    assert "逃出工作目录" in _call(wt.workdir_read, path=str(tmp_path / "x.txt"))


def test_agents_docs_forbidden(monkeypatch, tmp_path):
    wt, g, wd = _setup(monkeypatch, tmp_path)
    (tmp_path / "agents_docs").mkdir()
    out = _call(wt.workdir_create, path="../agents_docs/x.md", content="x")
    assert "逃出工作目录" in out or "专属" in out


def test_size_gate(monkeypatch, tmp_path):
    wt, g, wd = _setup(monkeypatch, tmp_path)
    monkeypatch.setattr(wt, "_max_bytes", lambda: 16)
    assert "超过单次写入上限" in _call(wt.workdir_create, path="big.md", content="x" * 100)
    assert not (wd / "big.md").exists()
