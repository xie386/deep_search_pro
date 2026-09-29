# -*- coding: utf-8 -*-
"""M4-8 用例：记忆路径的唯一事实源（离线，临时账号；不动真实账号文件）。

判据来源：M4 方案 §六 M4-8（路径唯一事实源、api 与 digest 双方取到同一文件、旧调用点仍可用 ≥6 项）。

运行：.venv/Scripts/python.exe -m pytest tests/test_memory_paths.py -q
"""
import os
import re
import shutil
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from tools import user_doc_paths as paths                                  # noqa: E402
from tools.schema_personal import get_personal_conn, purge_account         # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def mk_account():
    conn = get_personal_conn()
    try:
        uname = "m4p_%d" % (int(time.time() * 1000) % 1000000)
        aid = int(conn.execute("INSERT INTO accounts (username, password_hash, role) VALUES (?,?,?)",
                               (uname, "x", "user")).lastrowid)
        conn.commit()
    finally:
        conn.close()
    return aid, uname


# ---------------------------------------------------------------- ① 唯一事实源
def test_paths_all_derive_from_one_root():
    u = "someone"
    assert paths.user_dir(u) == os.path.join(paths.AGENTS_DOCS, u)
    assert paths.memory_path(u) == os.path.join(paths.user_dir(u), "MEMORY.md")
    assert paths.soul_path(u).endswith("SOUL.md") and paths.skills_dir(u).endswith("skills")
    assert paths.AGENTS_DOCS == os.path.join(ROOT, "agents_docs"), "根目录必须是 项目根/agents_docs"


def test_customize_delegates_to_the_single_source():
    """api 侧的路径函数必须来自唯一事实源（否则又会写出"两处各拼一遍"的老坑）。"""
    from api import customize as cust
    assert str(cust._user_memory_path("xieky")) == paths.memory_path("xieky")


def test_no_other_module_builds_the_memory_path_by_hand():
    """静态反证：除唯一事实源外，不应再有别处直接拼 `MEMORY.md` 的路径。"""
    bad = []
    for rel in ("api/customize.py", "api/server.py", "agent/digest_engine.py"):
        s = open(os.path.join(ROOT, rel), encoding="utf-8").read().replace("\r\n", "\n")
        for m in re.finditer(r'["\']MEMORY\.md["\']', s):
            line = s[:m.start()].count("\n") + 1
            ctx = s.split("\n")[line - 1].strip()
            if ctx.startswith("#"):
                continue
            # 只抓**手工拼路径**（join / 拼接 / 除法），注释与 docstring 里的"提及"不算
            if ("os.path.join" in ctx or "_user_doc_dir" in ctx or " / " in ctx
                    or "+ \"" in ctx) and "user_doc_paths" not in ctx and "_paths." not in ctx:
                bad.append("%s:%d %s" % (rel, line, ctx[:70]))
    assert not bad, "这些地方还在自己拼路径：%s" % bad


# ---------------------------------------------------------------- ② 双方取到同一文件
def test_api_and_digest_side_read_the_same_file():
    aid, uname = mk_account()
    try:
        d = paths.user_dir(uname)
        os.makedirs(d, exist_ok=True)
        with open(paths.memory_path(uname), "w", encoding="utf-8") as f:
            f.write("# 用户记忆画像（MEMORY）\n\n- 身份与领域：测试\n")
        from api.server import _get_memory_text
        text, got_user = _get_memory_text(aid)
        assert got_user == uname
        assert text == paths.read_memory_for_agent(uname), "★ 聊天侧与周报侧必须读到同一份正文"
        assert "身份与领域" in text
    finally:
        purge_account(aid)
        shutil.rmtree(paths.user_dir(uname), ignore_errors=True)


def test_read_memory_is_quiet_when_missing():
    assert paths.read_memory_for_agent("绝不存在的用户_xyz") == ""
    assert paths.read_text(os.path.join(paths.AGENTS_DOCS, "nope", "MEMORY.md")) == ""
    assert paths.read_memory_for_agent("") == ""


def test_old_call_sites_still_work():
    """旧调用点仍可用：customize 侧返回的仍是 Path（`.read_text()/.exists()` 语义不变）。"""
    from api import customize as cust
    p = cust._user_memory_path("xieky")
    assert hasattr(p, "read_text") and hasattr(p, "exists")
    assert p.name == "MEMORY.md"


def test_relative_path_hint_matches_the_real_layout():
    """注入块里给模型的相对路径（`{user}/MEMORY.md`）必须与真实布局一致 —— 这是当年踩过的坑。"""
    u = "xieky"
    assert os.path.relpath(paths.memory_path(u), paths.AGENTS_DOCS) == os.path.join(u, "MEMORY.md")


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q"]))
