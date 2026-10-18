# -*- coding: utf-8 -*-
"""07 · 浏览器下载落点（2026-10-18）。

**要钉住的三件事**：
  ① 下载目录必须在**当前启用工作目录**内（`_downloads/browser_<时间戳>/`），取不到就退回默认（不报错）；
  ② 回执要能说清"下载了什么"（名字 + 体积），图片附 `[[img:…]]`；
  ③ runner 侧：清单扫描只认文件、空目录要删、非空要留。
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))


def test_human_sizes():
    from tools.browser_tool import _human
    assert _human(0) == "0 B"
    assert _human(70) == "70 B"
    assert _human(2048) == "2.0 KB"
    assert _human(148834) == "145.3 KB"
    assert _human(5 * 1024 * 1024) == "5.0 MB"


def test_download_dir_uses_ascii_stage_and_workdir_target(monkeypatch, tmp_path):
    """★ 真机 2026-10-18：Chrome 在含中文的目录下收不到下载（watchdog 报 did not complete）。

    所以 `_download_dir()` 给 Chrome 的是**项目内 ASCII 暂存目录**，最终路径落在账号工作目录的
    `_downloads/browser_<时间戳>/` —— 跑完由 `_move_downloads()` 搬过去。
    """
    from tools import browser_tool as bt
    monkeypatch.setattr("tools.workdir_tools._ctx", lambda: (tmp_path, "u", 3), raising=True)
    stage, rel = bt._download_dir()
    assert rel.startswith("_downloads/browser_")
    assert Path(stage).is_dir(), "暂存目录要建出来，别让 runner 去猜"
    # 真机：直接把 downloads_path 指到工作目录里不落盘 → 先落项目 _runtime/browser_dl 暂存，跑完再搬
    assert Path(stage).parent.name == "browser_dl"


def test_download_dir_falls_back_without_workdir(monkeypatch):
    from tools import browser_tool as bt
    monkeypatch.setattr("tools.workdir_tools._ctx", lambda: (_ for _ in ()).throw(RuntimeError("x")), raising=True)
    assert bt._download_dir() == ("", ""), "取不到工作目录要静默退回默认，不能打断浏览"


def test_runner_lists_files_and_drops_only_empty_dirs(tmp_path):
    """runner 的两个小工具：扫清单（不认目录）、删空目录、留非空目录。"""
    sys.path.insert(0, str(ROOT / "browser_venv" / "Lib" / "site-packages"))
    import importlib.util
    spec = importlib.util.spec_from_file_location("browser_runner_mod", ROOT / "scripts" / "browser_runner.py")
    mod = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(mod)          # 需要 browser_use；装不到就跳过这一条
    except Exception:
        return

    empty = tmp_path / "empty"
    empty.mkdir()
    mod._drop_if_empty(str(empty))
    assert not empty.exists()

    full = tmp_path / "full"
    (full / "sub").mkdir(parents=True)
    (full / "a.pdf").write_bytes(b"x" * 10)
    (full / "sub" / "b.png").write_bytes(b"y" * 5)
    got = sorted(x["name"] for x in mod._list_downloads(str(full)))
    assert got == ["a.pdf", "b.png"], "要递归扫到子目录里的文件"
    assert sum(x["bytes"] for x in mod._list_downloads(str(full))) == 15
    mod._drop_if_empty(str(full))
    assert full.exists(), "有文件的目录不许删"


def test_runner_marks_over_limit_files(tmp_path):
    """单文件 100 MB 阈值：超限只标记、不删文件（方案 §07 ③.1）。"""
    sys.path.insert(0, str(ROOT / "browser_venv" / "Lib" / "site-packages"))
    import importlib.util
    spec = importlib.util.spec_from_file_location("browser_runner_mod2", ROOT / "scripts" / "browser_runner.py")
    mod = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(mod)
    except Exception:
        return
    d = tmp_path / "dl"
    d.mkdir()
    (d / "small.bin").write_bytes(b"x" * 10)
    (d / "big.bin").write_bytes(b"x" * 32)
    got = {x["name"]: x for x in mod._list_downloads(str(d), max_bytes=16)}
    assert got["small.bin"]["over_limit"] is False
    assert got["big.bin"]["over_limit"] is True
    assert (d / "big.bin").exists(), "超限只标记，不许删源文件"


def test_move_downloads_into_workdir_without_overwrite(monkeypatch, tmp_path):
    """搬进工作目录：重名加后缀，不覆盖；搬完删暂存。"""
    from tools import browser_tool as bt
    monkeypatch.setattr("tools.workdir_tools._ctx", lambda: (tmp_path, "u", 3), raising=True)
    stage = tmp_path / "stage"
    stage.mkdir()
    (stage / "a.pdf").write_bytes(b"new" * 4)
    dst = tmp_path / "_downloads" / "browser_x"
    dst.mkdir(parents=True)
    (dst / "a.pdf").write_bytes(b"old")
    final, moved = bt._move_downloads(str(stage), "_downloads/browser_x")
    assert moved == 1 and final
    assert (dst / "a-2.pdf").read_bytes() == b"new" * 4, "重名要加后缀，不能覆盖"
    assert (dst / "a.pdf").read_bytes() == b"old"
    assert not stage.exists(), "搬完暂存目录要清掉"


def test_move_downloads_noop_without_dirs():
    from tools import browser_tool as bt
    assert bt._move_downloads("", "_downloads/x") == ("", 0)


def test_tool_docs_tell_model_how_to_download():
    """★ 2026-10-18 真机：用户要下载壁纸，模型拿到直链后去 `curl -o`（被白名单拒），
    完全不知道「让浏览任务点下载按钮」这条路。两处说明书必须写清。"""
    from tools import web_workflows as ww
    doc = ww.browse_only.description if hasattr(ww.browse_only, "description") else ww.browse_only.__doc__
    doc = doc or ""
    assert "点击下载" in doc and "_downloads" in doc
    assert "curl" in doc, "必须写清 curl 不能落盘"

    from tools import browser_tool as bt
    d2 = bt.browse_pages.__doc__ or ""
    assert "downloads" in d2 and "点击下载" in d2


def test_empty_download_note_for_download_task(monkeypatch):
    """要下载却一个文件都没收到时，给一句可执行的下一步提示。"""
    import json, subprocess, types
    from tools import browser_tool as bt

    class _R:
        returncode = 0
        stdout = json.dumps({"ok": True, "results": [{"url": "u", "title": "t", "content": "c"}], "downloads": []})
        stderr = ""
    monkeypatch.setattr(bt.subprocess, "run", lambda *a, **k: _R())
    monkeypatch.setattr(bt, "_download_dir", lambda: ("", ""))
    monkeypatch.setattr(bt, "_proxy", lambda: None)
    monkeypatch.setattr(bt, "_active_llm", lambda: {"model": "m", "api_key": "k", "base_url": "b", "vision": False})
    got = bt.browse_pages("点击下载按钮，把这张壁纸下下来", ["https://example.com"])
    assert "没有检测到下载文件" in (got.get("note") or "")


def test_move_downloads_skips_empty_stage(tmp_path, monkeypatch):
    """没下载到东西时不许在工作目录里建空 _downloads/browser_*（真机留了一串空目录）。"""
    from tools import browser_tool as bt
    monkeypatch.setattr("tools.workdir_tools._ctx", lambda: (tmp_path, "u", 3), raising=True)
    stage = tmp_path / "stage_empty"
    stage.mkdir()
    final, moved = bt._move_downloads(str(stage), "_downloads/browser_x")
    assert (final, moved) == ("", 0)
    assert not (tmp_path / "_downloads").exists(), "空下载不该建目标目录"
    assert not stage.exists(), "空暂存要清掉"


def test_download_task_detection():
    from tools.browser_tool import _is_download_task
    assert _is_download_task("点下载按钮，把图存下来")
    assert _is_download_task("click the Download button")
    assert _is_download_task("把这张图保存到本地")
    assert not _is_download_task("列出页面上的产品名称与价格")


def test_download_task_gets_step_floor_and_landing_note(monkeypatch):
    """下载任务：步数下限 12，且任务里写明最终落点（否则子 Agent 会把暂存绝对路径抄进回答）。"""
    import json
    from tools import browser_tool as bt
    seen = {}

    class _R:
        returncode = 0
        stdout = json.dumps({"ok": True, "results": [{"url": "u", "title": "t", "content": "c"}], "downloads": []})
        stderr = ""

    def fake_run(cmd, **kw):
        seen["req"] = json.loads(kw.get("input") or "{}")
        return _R()
    monkeypatch.setattr(bt.subprocess, "run", fake_run)
    monkeypatch.setattr(bt, "_download_dir", lambda: (str(bt._DL_STAGE_ROOT / "x"), "_downloads/browser_x"))
    monkeypatch.setattr(bt, "_proxy", lambda: None)
    monkeypatch.setattr(bt, "_active_llm", lambda: {"model": "m", "api_key": "k", "base_url": "b", "vision": False})
    bt.browse_pages("点击下载按钮，把这张壁纸下下来", ["https://example.com"], max_steps=5)
    assert seen["req"]["max_steps"] == 12, "下载任务步数下限 12"
    assert "落盘说明" in seen["req"]["task"] and "_downloads/browser_x" in seen["req"]["task"]
