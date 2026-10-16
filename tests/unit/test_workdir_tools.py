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
    monkeypatch.setattr(wt, "_ctx", lambda: (wd, "u", 3))
    monkeypatch.setattr(wt, "_audit", lambda *a, **k: None)
    return wt, g, wd


def _call(tool, **kw):
    """@tool 包装的函数直接调用底层实现（不经过 Agent 运行时）。"""
    return tool.invoke(kw)


# ------------------------------------------------------------ 运行期闸
def _req(name, tcid="call_1"):
    class _R:
        tool_call = {"name": name, "id": tcid, "args": {}}
        def override(self, **kw):  # noqa: D102
            return self
    return _R()


def test_gate_denies_write_without_slash_authorization(monkeypatch):
    from agent import write_gate as wg
    wg.clear()
    asked = {"n": 0}
    def handler(_r):
        asked["n"] += 1
        return "SHOULD NOT RUN"
    out = wg.WriteGateMiddleware().wrap_tool_call(_req("workdir_create"), handler)
    assert asked["n"] == 0                                     # ★ 未授权 → handler 根本没被调用
    assert getattr(out, "status", "") == "error"
    assert "未授权改文件" in out.content and "「/」" in out.content


def test_gate_allows_when_authorized(monkeypatch):
    from agent import write_gate as wg
    wg.set_selected(["写工具集A"])
    try:
        out = wg.WriteGateMiddleware().wrap_tool_call(_req("workdir_edit"), lambda r: "OK")
        assert out == "OK"
    finally:
        wg.clear()


def test_gate_lets_read_tools_through(monkeypatch):
    from agent import write_gate as wg
    wg.clear()
    for name in ("workdir_list", "workdir_read"):
        assert wg.WriteGateMiddleware().wrap_tool_call(_req(name), lambda r: "OK") == "OK"


def test_gate_async_path_too(monkeypatch):
    import asyncio
    from agent import write_gate as wg
    wg.clear()
    async def handler(_r):
        return "SHOULD NOT RUN"
    async def run():
        return await wg.WriteGateMiddleware().awrap_tool_call(_req("workdir_delete"), handler)
    out = asyncio.run(run())
    assert getattr(out, "status", "") == "error"


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
    trash = list((wd / g.RECYCLE_DIR).iterdir())
    assert len(trash) == 1 and trash[0].read_text(encoding="utf-8") == "x"   # 可还原


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
