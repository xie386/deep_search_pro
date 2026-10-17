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
