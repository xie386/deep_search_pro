# -*- coding: utf-8 -*-
"""06 · 工作目录接口层单元测试 —— 全离线、全临时目录、审计 no-op。

**为什么要有它**：用户侧文件管理器是**唯一**能让用户直接改工作目录里文件的入口，
它必须与 Agent 侧写工具**同一套门禁**（路径隔离 / 保护名单 / 凭据红线 / agents_docs 拒），
并且删除只软删进回收站、保存要挡并发冲突（mtime）。
"""
import sys
from pathlib import Path

import pytest
from fastapi import HTTPException

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))


@pytest.fixture()
def env(monkeypatch, tmp_path):
    """把接口层的作用域指到临时工作目录；审计改成 no-op（绝不写真实审计文件）。"""
    from api import workdir_api as api
    from tools import workdir_guard as g
    wd = tmp_path / "wd"; wd.mkdir()
    (wd / "data").mkdir()
    (wd / "a.md").write_text("第一行\n第二行\n", encoding="utf-8")
    (wd / "data" / "t.csv").write_text("x,y\n1,2\n", encoding="utf-8")
    (wd / "all_keys.json").write_text("{}", encoding="utf-8")
    (wd / "MEMORY.md").write_text("画像", encoding="utf-8")
    (wd / g.PROTECT_FILE).write_text("# 保护名单\nMEMORY.md\n", encoding="utf-8")
    monkeypatch.setattr(api, "_aid", lambda t: 3)
    monkeypatch.setattr(api, "_uname", lambda t: "u")
    monkeypatch.setattr(api, "_ctx", lambda t: (3, "u", wd, {"id": 1, "name": "主页"}))
    monkeypatch.setattr(api, "_audit", lambda *a, **k: None)
    return api, g, wd


def _codes(fn, *a, **k):
    with pytest.raises(HTTPException) as e:
        fn(*a, **k)
    return e.value.status_code


# ------------------------------------------------------------ 条目
def test_list_reports_active(env):
    api, g, wd = env
    out = api.list_("tk")
    assert out["ok"] and out["active_path"] == str(wd)


def test_add_requires_confirm_and_reveals_risks(env, tmp_path):
    api, g, wd = env
    ext = tmp_path / "ext"; ext.mkdir()
    r = api.add_("tk", api.AddReq(name="外部", path=str(ext)))          # 未确认
    assert r["ok"] is False and r["need_confirm"] and len(r["risks"]) == 3


# ------------------------------------------------------------ 目录树
def test_tree_flags_credential_and_protected(env):
    api, g, wd = env
    out = api.tree("tk")
    names = {e["name"]: e for e in out["entries"]}
    assert out["is_root"] and names["data"]["is_dir"]
    assert names["all_keys.json"]["credential"] is True                 # 只标记，不给内容
    assert names[g.PROTECT_FILE]["protected"] is True


def test_tree_rejects_escape(env):
    api, g, wd = env
    assert _codes(api.tree, "tk", "../..") == 400


# ------------------------------------------------------------ 读 / 预览
def test_read_text_and_flags(env):
    api, g, wd = env
    out = api.read_("tk", "a.md")
    assert out["kind"] == "text" and "第一行" in out["text"] and out["editable"] is True


def test_read_credential_forbidden(env):
    api, g, wd = env
    assert _codes(api.read_, "tk", "all_keys.json") == 403


def test_read_escape_and_missing(env, tmp_path):
    api, g, wd = env
    assert _codes(api.read_, "tk", "../x.txt") == 400
    assert _codes(api.read_, "tk", "nope.md") == 404


# ------------------------------------------------------------ 写 / 建 / 改名
def test_write_and_mtime_conflict(env):
    api, g, wd = env
    out = api.read_("tk", "a.md")
    r = api.write_("tk", api.WriteReq(path="a.md", content="改过\n", mtime=out["mtime"]))
    assert r["ok"] and (wd / "a.md").read_text(encoding="utf-8") == "改过\n"
    assert _codes(api.write_, "tk", api.WriteReq(path="a.md", content="x", mtime=out["mtime"])) == 409


def test_write_protected_and_credential_denied(env):
    api, g, wd = env
    assert _codes(api.write_, "tk", api.WriteReq(path="MEMORY.md", content="篡改")) == 403
    assert _codes(api.write_, "tk", api.WriteReq(path="all_keys.json", content="{}")) == 403
    assert (wd / "MEMORY.md").read_text(encoding="utf-8") == "画像"


def test_create_and_conflict(env):
    api, g, wd = env
    assert api.create_("tk", api.CreateReq(path="sub/new.md", content="hi"))["ok"]
    assert (wd / "sub" / "new.md").read_text(encoding="utf-8") == "hi"
    assert _codes(api.create_, "tk", api.CreateReq(path="a.md", content="x")) == 409


def test_rename_both_ends_guarded(env):
    api, g, wd = env
    assert api.rename_("tk", api.RenameReq(path="a.md", target="b.md"))["ok"]
    assert not (wd / "a.md").exists() and (wd / "b.md").exists()
    assert _codes(api.rename_, "tk", api.RenameReq(path="b.md", target="MEMORY.md")) == 403


# ------------------------------------------------------------ 删除与回收站
def test_delete_to_trash_then_restore(env):
    api, g, wd = env
    r = api.delete_("tk", api.PathReq(path="data/t.csv"))
    assert r["ok"] and not (wd / "data" / "t.csv").exists()
    items = api.trash_("tk", api.TrashReq(action="list"))["items"]
    assert len(items) == 1 and items[0]["rel"] == "data/t.csv"          # 记着原相对路径
    api.trash_("tk", api.TrashReq(action="restore", name=r["trash_name"]))
    assert (wd / "data" / "t.csv").read_text(encoding="utf-8") == "x,y\n1,2\n"   # 原位还原


def test_delete_protected_denied_and_root_denied(env):
    api, g, wd = env
    assert _codes(api.delete_, "tk", api.PathReq(path="MEMORY.md")) == 403
    assert _codes(api.delete_, "tk", api.PathReq(path=g.PROTECT_FILE)) == 403
    assert _codes(api.delete_, "tk", api.PathReq(path=".")) == 400


def test_trash_purge(env):
    api, g, wd = env
    r = api.delete_("tk", api.PathReq(path="a.md"))
    assert api.trash_("tk", api.TrashReq(action="purge"))["purged"] == 1
    assert api.trash_("tk", api.TrashReq(action="list"))["items"] == []
    assert _codes(api.trash_, "tk", api.TrashReq(action="weird")) == 400
