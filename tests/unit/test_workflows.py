# -*- coding: utf-8 -*-
"""06 · workflows（定义层）：名字/内容校验与 CRUD —— 全离线、全临时目录。

**为什么要有它**：workflow 是被**引擎逐步执行**的东西，坏定义不该等到跑起来才炸 ——
所以「创建即校验」是硬规矩（YAML 能解析、steps 非空且每步有 title、步数与引用数有上限）。
名字校验与技能同规格（三层：黑名单 + 白名单 + Windows 保留名），因为两者存在同一个目录树下。
"""
import sys
from pathlib import Path

import pytest
from fastapi import HTTPException

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from api import workflows as wf  # noqa: E402

GOOD = """name: 竞品官网取价
desc: 先定位官网再进站取价
steps:
  - id: 1
    title: 定位竞品官网
    use: [db_agent]
    output: 官网 URL 列表
  - id: 2
    title: 进站取价
    use: [search_then_browse]
    output: 价格与来源
"""


# ---------------------------------------------------------------- 名字校验
@pytest.mark.parametrize("bad", ["", "   ", ".", "..", "con", "NUL", "x" * 40])
def test_bad_names_rejected(bad):
    with pytest.raises(HTTPException) as e:
        wf.wf_safe_name(bad)
    assert e.value.status_code == 400


def test_traversal_is_neutralized_not_rejected(monkeypatch, tmp_path):
    """★ 穿越名**不拒**而是去路径化（与技能同款）：真正要守的不变量是**产物落在 workflows 目录内**。"""
    d = _env(monkeypatch, tmp_path)
    p = wf._wf_path("u", "../../etc/passwd")
    assert p.parent == d and p.name == "passwd.yml", p
    p2 = wf._wf_path("u", "..\\..\\windows\\system32\\evil")
    assert p2.parent == d and p2.name == "evil.yml", p2


def test_name_keeps_chinese_and_strips_extension():
    assert wf.wf_safe_name("竞品官网取价.yml") == "竞品官网取价"
    assert wf.wf_safe_name("a/b.yaml") == "b"


# ---------------------------------------------------------------- 内容校验
def test_good_definition_normalized():
    v = wf.validate_wf(GOOD)
    assert v["name"] == "竞品官网取价" and v["desc"].startswith("先定位")
    assert [s["title"] for s in v["steps"]] == ["定位竞品官网", "进站取价"]
    assert v["steps"][1]["use"] == ["search_then_browse"]


def test_use_accepts_comma_string():
    v = wf.validate_wf("steps:\n  - title: 一步\n    use: a, b\n")
    assert v["steps"][0]["use"] == ["a", "b"]


def test_use_defaults_to_empty():
    v = wf.validate_wf("steps:\n  - title: 一步\n")
    assert v["steps"][0]["use"] == []


@pytest.mark.parametrize("bad,frag", [
    ("- 1\n- 2\n", "顶层"),
    ("name: x\n", "steps"),
    ("steps: []\n", "steps"),
    ("steps:\n  - 字符串\n", "必须是映射"),
    ("steps:\n  - id: 1\n", "缺 title"),
    ("steps:\n  - title: t\n    use: {a: 1}\n", "use"),
    ("steps:\n  - title: t\n    use: [a,b,c,d,e,f,g]\n", "过多"),
    ("name: [\n", "YAML 解析失败"),
])
def test_bad_definitions_rejected(bad, frag):
    with pytest.raises(HTTPException) as e:
        wf.validate_wf(bad)
    assert e.value.status_code == 400 and frag in str(e.value.detail)


def test_too_many_steps_and_too_long_rejected():
    steps = "".join("  - title: 第%d步\n" % i for i in range(wf.WF_MAX_STEPS + 1))
    with pytest.raises(HTTPException):
        wf.validate_wf("steps:\n" + steps)
    with pytest.raises(HTTPException):
        wf.validate_wf("x" * (wf.WF_MAX_LEN + 1))


def test_summary_marks_broken_file_instead_of_raising():
    s = wf.wf_summary("name: [\n")
    assert s["ok"] is False and s["error"] and s["steps"] == 0
    assert wf.wf_summary(GOOD)["ok"] is True and wf.wf_summary(GOOD)["steps"] == 2


def test_builtin_template_is_valid():
    """「插入模板」给的东西必须自己能通过校验（否则用户点一下就报错）。"""
    v = wf.validate_wf(wf.wf_template())
    assert len(v["steps"]) == 2 and v["name"]


# ---------------------------------------------------------------- CRUD
def _env(monkeypatch, tmp_path):
    monkeypatch.setattr(wf, "_username_by_token", lambda t: "u")
    d = tmp_path / "workflows"; d.mkdir()
    monkeypatch.setattr(wf, "_user_wf_dir", lambda user: d)
    return d


def test_crud_roundtrip(monkeypatch, tmp_path):
    d = _env(monkeypatch, tmp_path)
    assert wf.wf_create("tk", wf.WfReq(name="取价流程", content=GOOD))["ok"]
    assert wf.wf_create("tk", wf.WfReq(name="取价流程", content=GOOD)) if False else True
    with pytest.raises(HTTPException) as e:                     # 重名 409
        wf.wf_create("tk", wf.WfReq(name="取价流程", content=GOOD))
    assert e.value.status_code == 409
    assert (d / "取价流程.yml").exists()
    got = wf.wf_list("tk")
    assert got["workflows"][0]["name"] == "竞品官网取价" and got["workflows"][0]["steps"] == 2
    assert wf.wf_get("tk", "取价流程")["content"] == GOOD
    assert wf.wf_save("tk", wf.WfReq(name="取价流程", content=GOOD.replace("竞品官网取价", "改过的")))["ok"]
    assert "改过的" in wf.wf_get("tk", "取价流程")["content"]
    assert wf.wf_delete("tk", "取价流程")["ok"]
    assert wf.wf_list("tk")["workflows"] == []
    with pytest.raises(HTTPException) as e:                     # 删不存在 → 404
        wf.wf_delete("tk", "取价流程")
    assert e.value.status_code == 404


def test_create_rejects_invalid_definition(monkeypatch, tmp_path):
    """★ 创建即校验：坏定义不许落盘（否则引擎跑到一半才炸）。"""
    d = _env(monkeypatch, tmp_path)
    with pytest.raises(HTTPException):
        wf.wf_create("tk", wf.WfReq(name="坏的", content="steps:\n  - 没有title\n"))
    assert list(d.glob("*.yml")) == []


def test_validate_wf_accepts_write_sets():
    v = wf.validate_wf("name: w\nwrite: [GitHub Issues, AgentOffice]\nsteps:\n  - title: t\n")
    assert v["write"] == ["GitHub Issues", "AgentOffice"]


def test_validate_wf_write_defaults_to_empty():
    assert wf.validate_wf("name: w\nsteps:\n  - title: t\n")["write"] == []


def test_validate_wf_rejects_too_many_write_sets():
    import pytest
    with pytest.raises(Exception) as ei:
        wf.validate_wf("name: w\nwrite: [a,b,c,d,e,f,g,h]\nsteps:\n  - title: t\n")
    assert "写工具集过多" in str(getattr(ei.value, "detail", ei.value))
