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
