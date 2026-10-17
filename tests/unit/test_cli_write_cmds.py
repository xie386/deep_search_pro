# -*- coding: utf-8 -*-
"""06 · CLI「写命令清单」：填了自动进写池（2026-10-17 用户要求）。

口径：写能力永不被纯文字路由唤起 —— 进写池后只能靠「/」第二组显式选中；
清单留空 = 不进写池；清空清单**不自动降级**（用户手动设过的归属要保留）。
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))


def _db(monkeypatch, tmp_path):
    from tools import schema_personal as sp
    monkeypatch.delenv("PERSONAL_DB_PATH", raising=False)
    monkeypatch.setattr(sp, "DB_PATH", str(tmp_path / "t.db"))
    assert sp.db_path() == str(tmp_path / "t.db")
    sp.ensure_tables()
    conn = sp.get_personal_conn()
    conn.execute("INSERT INTO accounts (id, username, password_hash) VALUES (11,'t_cli','x')")
    conn.commit(); conn.close()


def test_write_cmds_auto_enter_write_pool(monkeypatch, tmp_path):
    _db(monkeypatch, tmp_path)
    from tools import cli_registry as reg
    monkeypatch.setattr(reg, "_sync_pool", lambda *a, **k: None)

    item = reg.save_cli(11, {"name": "OfficeCLI", "bin": "officecli",
                             "readonly": "view\nget\nquery\nvalidate",
                             "write_cmds": "create\nadd\nset\nremove\nbatch\n"
                                           "officecli set report.docx /body --prop text=hi"})
    _flat = [" ".join(w) for w in item["write_rules"]]
    for _c in ("create", "add", "set", "remove", "batch"):
        assert _c in _flat, (item["write_rules"], _flat)
    assert item["pool"] == "write", "填了写命令清单必须自动进写池"
    assert "create" in item["write_cmds"]

    # 清空写清单：写规则清空，但**归属不降级**（手动设过的要保留）
    item2 = reg.save_cli(11, {"id": item["id"], "name": "OfficeCLI", "bin": "officecli", "write_cmds": ""})
    assert item2["write_rules"] == []
    assert item2["pool"] == "write"


def test_no_write_cmds_stays_readonly(monkeypatch, tmp_path):
    _db(monkeypatch, tmp_path)
    from tools import cli_registry as reg
    monkeypatch.setattr(reg, "_sync_pool", lambda *a, **k: None)
    item = reg.save_cli(11, {"name": "只读工具", "bin": "rocli", "readonly": "list"})
    assert item["write_rules"] == [] and item["pool"] == "readonly"


def test_ability_draft_needs_at_least_one_list():
    from fastapi import HTTPException
    from api import customize as cust
    with pytest.raises(HTTPException) as e:
        cust.cli_ability_generate("X", "xcli", "", docs_mode="help")
    assert "只读命令清单" in str(e.value.detail) and "写命令清单" in str(e.value.detail)


def test_write_pool_cli_readonly_still_runnable(monkeypatch, tmp_path):
    """★ 2026-10-18 真机：写池 CLI 连只读子命令都被拒（面板报「命令 'officecli' 不在白名单」）。

    写池的正确语义：**不进路由、写能力要「/」授权**；只读查询照样能跑。
    """
    _db(monkeypatch, tmp_path)
    from tools import cli_registry as reg
    monkeypatch.setattr(reg, "_sync_pool", lambda *a, **k: None)
    reg.save_cli(11, {"name": "假工具", "bin": "rotool", "state": "authed",
                      "readonly": "view\nget", "write_cmds": "create\nset"})

    assert "rotool" not in reg.allowed_for_agent(11), "写池不进路由池（这条不能破）"
    e = reg.readonly_entry_for(11, "rotool")
    assert e and [list(t) for t in e["readonly"]] == [["view"], ["get"]]
    assert "write_rules" not in e and not e.get("authorized")

    from tools._runtime import shell_runtime as sh
    monkeypatch.setattr(sh, "active_workdir", lambda *a, **k: tmp_path)
    r = sh.execute("rotool", ["view", "a.docx"], username="t_cli", account_id=11)
    assert "不在白名单" not in (r.get("error") or ""), r
    # 写子命令：没经「/」授权 → 被第三方 CLI 闸拒掉（不是"不在白名单"，而是"不允许执行"）
    w = sh.execute("rotool", ["set", "a.docx"], username="t_cli", account_id=11)
    assert w.get("ok") is False and "第三方 CLI" in (w.get("error") or ""), w


def test_expand_selected_records_name_and_bin(monkeypatch, tmp_path):
    """★ 2026-10-18 真机：只记显示名 → 执行期按 bin 判授权永远 False（选了 AgentOffice 跑不了 officecli）。"""
    _db(monkeypatch, tmp_path)
    from tools import cli_registry as reg
    monkeypatch.setattr(reg, "_sync_pool", lambda *a, **k: None)
    reg.save_cli(11, {"name": "AgentOffice", "bin": "officecli", "state": "authed",
                      "readonly": "view", "write_cmds": "create"})
    from api import write_pool as wp
    got = wp.expand_selected(11, ["AgentOffice"])
    assert "AgentOffice" in got and "officecli" in got, got


def test_document_paths_not_treated_as_escape(monkeypatch, tmp_path):
    """officecli 的 `/`、`/slide[1]` 是文档内路径，不是磁盘路径（原来被判"路径逃逸沙箱"）。"""
    _db(monkeypatch, tmp_path)
    from tools import cli_registry as reg
    monkeypatch.setattr(reg, "_sync_pool", lambda *a, **k: None)
    reg.save_cli(11, {"name": "假工具", "bin": "rotool", "state": "authed",
                      "readonly": "view", "write_cmds": "add"})
    from tools._runtime import shell_runtime as sh
    from utils import write_auth
    monkeypatch.setattr(sh, "active_workdir", lambda *a, **k: tmp_path)
    monkeypatch.setattr(sh, "subprocess", sh.subprocess)   # 保持真实；rotool 不存在 → 走失败分支
    write_auth.set_selected(["rotool"])
    try:
        r = sh.execute("rotool", ["add", "a.pptx", "/", "--type", "slide"],
                       username="t_cli", account_id=11)
    finally:
        write_auth.set_selected([])
    err = r.get("error") or ""
    assert "路径逃逸" not in err, r
    assert "不在只读清单" not in err and "第三方 CLI" not in err, r
