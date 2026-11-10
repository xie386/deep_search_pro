# -*- coding: utf-8 -*-
"""v4.1-04：背景图的两个作用域（主窗 / 浮窗）与当前选择的读写。

只在临时目录里验证，不碰真实 user_data/pic。
"""
import io
import json

import pytest
from fastapi import HTTPException

from api import customize as cust


class _FakeUpload:
    def __init__(self, name: str, data: bytes = b"\x89PNG\r\n\x1a\nfake"):
        self.filename = name
        self.file = io.BytesIO(data)


@pytest.fixture()
def pic(tmp_path, monkeypatch):
    monkeypatch.setattr(cust, "PIC_DIR", tmp_path / "pic")
    monkeypatch.setattr(cust, "_username_by_token", lambda token: "u1")
    return tmp_path / "pic" / "u1"


def test_upload_main_goes_to_root(pic):
    d = cust.bg_upload("t", _FakeUpload("a.png"), "main")
    assert d["url"].startswith("/pic/u1/") and "/subbackground/" not in d["url"]
    assert (pic / d["name"]).is_file()


def test_upload_sub_goes_to_subdir(pic):
    d = cust.bg_upload("t", _FakeUpload("b.jpg"), "sub")
    assert d["url"].startswith("/pic/u1/subbackground/")
    assert (pic / "subbackground" / d["name"]).is_file()


def test_list_is_scoped_and_skips_state_file(pic):
    cust.bg_upload("t", _FakeUpload("a.png"), "main")
    sub = cust.bg_upload("t", _FakeUpload("b.png"), "sub")
    cust.bg_state_set("t", sub["name"], 70, "sub")

    main_list = cust.bg_list("t", "main")["images"]
    sub_list = cust.bg_list("t", "sub")["images"]
    assert [i["name"] for i in main_list] == [i["name"] for i in main_list if i["name"] in {main_list[0]["name"]}]
    assert any(i["name"] == sub["name"] for i in sub_list)
    assert all(i["name"] != ".active.json" for i in sub_list)
    assert len(main_list) == 1 and len(sub_list) == 1


def test_state_roundtrip(pic):
    sub = cust.bg_upload("t", _FakeUpload("c.png"), "sub")
    cust.bg_state_set("t", sub["name"], 45, "sub")
    st = cust.bg_state_get("t", "sub")
    assert st["name"] == sub["name"] and st["opacity"] == 45
    assert st["url"].endswith("/subbackground/" + sub["name"])
    raw = json.loads((pic / "subbackground" / ".active.json").read_text(encoding="utf-8"))
    assert raw == {"name": sub["name"], "opacity": 45}


def test_state_rejects_unknown_name(pic):
    with pytest.raises(HTTPException):
        cust.bg_state_set("t", "nope.png", 60, "sub")


def test_state_defaults_when_missing(pic):
    st = cust.bg_state_get("t", "sub")
    assert st["name"] == "" and st["url"] == "" and st["opacity"] == 60


def test_delete_active_clears_choice_but_keeps_opacity(pic):
    sub = cust.bg_upload("t", _FakeUpload("d.png"), "sub")
    cust.bg_state_set("t", sub["name"], 80, "sub")
    cust.bg_delete("t", sub["name"], "sub")
    st = cust.bg_state_get("t", "sub")
    assert st["name"] == "" and st["opacity"] == 80


def test_scope_alias_and_default(pic):
    assert cust._norm_scope(None) == "main"
    assert cust._norm_scope("SUB") == "sub"
    assert cust._norm_scope("whatever") == "main"
