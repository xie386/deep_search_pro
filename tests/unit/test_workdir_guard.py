# -*- coding: utf-8 -*-
"""06 · 工作目录守卫与存储层单元测试 —— 全离线、全临时目录。

**为什么要有它**：这一层是「写能力」的安全内核，三条硬规则一旦松掉就是真事故：
  ① 保护名单 **fail-closed**（读不到就一律拒绝写）—— 照 03 清理链「水位线读不到就一行不删」的教训；
  ② 名单文件**自身**不可写不可删，`agents_docs/` 画像目录**读写都拒**；
  ③ 凭据类（*key*/*token*/*secret* 与点开头目录）**连读都拒绝**。
另外钉住：路径解析必须拒绝 `..` 逃逸 / UNC，回收站软删可还原，存储层「主页」条目幂等且不可删。
"""
import sys
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))


def _mk_workdir(tmp_path):
    """造一个带保护名单的工作目录。"""
    from tools import workdir_guard as g
    wd = tmp_path / "wd"
    wd.mkdir()
    (wd / "data").mkdir()
    (wd / "data" / "keep.css").write_text("x", encoding="utf-8")
    (wd / "report.md").write_text("hello", encoding="utf-8")
    (wd / ".wechat-cli").mkdir()
    (wd / "all_keys.json").write_text("{}", encoding="utf-8")
    (wd / g.PROTECT_FILE).write_text(
        "# 注释\n\nMEMORY.md\nsecret_dir/\ndata/*.csv\n", encoding="utf-8")
    return wd


# ------------------------------------------------------------ 保护名单语义
def test_protect_parse_skips_comments_and_blanks(tmp_path):
    from tools import workdir_guard as g
    wd = _mk_workdir(tmp_path)
    rules = g.parse_protect(wd)
    assert rules == ["MEMORY.md", "secret_dir/", "data/*.csv"]


def test_protect_missing_file_is_fail_closed(tmp_path):
    """名单读不到 → parse 返回 None；写操作一律拒绝（绝不"当无保护放行"）。"""
    from tools import workdir_guard as g
    wd = tmp_path / "bare"
    wd.mkdir()
    (wd / "a.txt").write_text("x", encoding="utf-8")
    assert g.parse_protect(wd) is None
    reason = g.assert_writable(wd / "a.txt", wd)
    assert reason and "fail-closed" in reason


def test_plain_name_matches_at_any_depth(tmp_path):
    from tools import workdir_guard as g
    wd = _mk_workdir(tmp_path)
    deep = wd / "data" / "MEMORY.md"
    deep.write_text("x", encoding="utf-8")
    assert g.protected_reason(deep, wd)
    assert g.protected_reason(wd / "report.md", wd) is None


def test_dir_prefix_is_recursive(tmp_path):
    from tools import workdir_guard as g
    wd = _mk_workdir(tmp_path)
    (wd / "secret_dir").mkdir()
    inner = wd / "secret_dir" / "sub" / "f.txt"
    (wd / "secret_dir" / "sub").mkdir()
    inner.write_text("x", encoding="utf-8")
    assert g.protected_reason(inner, wd)
    assert g.protected_reason(wd / "secret_dir", wd)


def test_glob_rule(tmp_path):
    from tools import workdir_guard as g
    wd = _mk_workdir(tmp_path)
    csv = wd / "data" / "b.csv"
    csv.write_text("x", encoding="utf-8")
    assert g.protected_reason(csv, wd)
    assert g.protected_reason(wd / "data" / "keep.css", wd) is None


# ------------------------------------------------------------ 归纳门禁
def test_protect_file_itself_is_never_writable(tmp_path):
    from tools import workdir_guard as g
    wd = _mk_workdir(tmp_path)
    reason = g.assert_writable(wd / g.PROTECT_FILE, wd)
    assert reason and "永远不可" in reason


def test_agents_docs_is_forbidden_both_ways(tmp_path):
    from tools import workdir_guard as g
    wd = _mk_workdir(tmp_path)
    p = tmp_path / "agents_docs" / "药喵" / "x.md"
    p.parent.mkdir(parents=True)
    p.write_text("x", encoding="utf-8")
    for fn in (g.assert_writable, g.check_readable):
        reason = fn(p, wd)
        assert reason and "专属" in reason


def test_credential_rejected_even_on_read(tmp_path):
    from tools import workdir_guard as g
    wd = _mk_workdir(tmp_path)
    assert g.is_credential(wd / "all_keys.json", wd) is True
    assert g.is_credential(wd / ".wechat-cli", wd) is True
    assert g.is_credential(wd / "sub" / "token.txt", wd) is True
    assert g.is_credential(wd / "report.md", wd) is False
    assert g.check_readable(wd / "all_keys.json", wd)          # 连读都拒
    assert g.check_readable(wd / ".wechat-cli", wd)


def test_credential_judges_relative_to_workdir(tmp_path):
    """★ 保护的是工作目录**内部**：父目录恰叫 my-secret-notes 不该把整棵树误杀。"""
    from tools import workdir_guard as g
    parent = tmp_path / "my-secret-notes"; parent.mkdir()
    f = parent / "report.md"; f.write_text("x", encoding="utf-8")
    assert g.is_credential(f) is True                    # 无 workdir → 按全路径保守判
    assert g.is_credential(f, parent) is False           # 相对工作目录 → 放行
    assert g.check_readable(f, parent) is None


def test_recycle_target_is_allowed(tmp_path):
    from tools import workdir_guard as g
    wd = _mk_workdir(tmp_path)
    tgt = wd / g.RECYCLE_DIR / "20260101_000000_report.md"
    tgt.parent.mkdir()
    tgt.write_text("x", encoding="utf-8")
    assert g.assert_writable(tgt, wd) is None


def test_missing_protect_list_is_seeded_by_active(monkeypatch, tmp_path):
    """★ 真机抓到：保护名单缺失不该是死路 —— active() 会补种（缺失才种）。"""
    from tools import workdir_guard as g, workdir_store as st, schema_personal as sp
    sb = tmp_path / "sb"; sb.mkdir()
    monkeypatch.setattr(st, "sandbox_dir", lambda name=None: sb)
    p, _ = st.active(None, "u")                      # account_id 为空 → 走回落分支
    assert p == sb and (sb / g.PROTECT_FILE).exists(), "首次落地应补种保护名单"
    # 补种后写普通文件放行、写名单本身仍拒绝
    assert g.assert_writable(sb / "a.md", sb) is None
    assert "永远不可" in g.assert_writable(sb / g.PROTECT_FILE, sb)


def test_unparsable_protect_list_still_fail_closed(tmp_path):
    """但"存在却解析不了"必须继续 fail-closed（真异常，不能被补种掩盖）。"""
    from tools import workdir_guard as g
    wd = tmp_path / "wd"; wd.mkdir()
    (wd / g.PROTECT_FILE).write_bytes(b"\xff\xfe\xff\xfe")     # 非 UTF-8 → 读不了
    assert g.parse_protect(wd) is None
    r = g.assert_writable(wd / "a.md", wd)
    assert r and "fail-closed" in r


def test_normal_file_passes(tmp_path):
    from tools import workdir_guard as g
    wd = _mk_workdir(tmp_path)
    assert g.assert_writable(wd / "report.md", wd) is None


# ------------------------------------------------------------ 路径解析
def test_resolve_inside_ok_and_escapes_rejected(tmp_path):
    from tools import workdir_guard as g
    wd = _mk_workdir(tmp_path)
    assert g.resolve_in_workdir("data/keep.css", wd) == (wd / "data" / "keep.css").resolve()
    for bad in ("../outside.txt", str(tmp_path / "elsewhere.txt"), "\\\\srv\\share\\x"):
        try:
            g.resolve_in_workdir(bad, wd)
            raise AssertionError("应当拒绝：%s" % bad)
        except ValueError:
            pass


# ------------------------------------------------------------ 回收站
def test_recycle_and_ttl_purge(tmp_path):
    from tools import workdir_guard as g
    import os, time
    wd = _mk_workdir(tmp_path)
    f = wd / "report.md"
    dst = g.recycle(wd, f)
    assert dst.exists() and not f.exists() and dst.parent.name == g.RECYCLE_DIR
    old = time.time() - 40 * 86400
    os.utime(dst, (old, old))
    assert g.purge_expired_trash(wd, 7) == 1
    assert not dst.exists()


# ------------------------------------------------------------ 存储层
def _fake_db(monkeypatch, tmp_path):
    """把 schema_personal 与 workdir_store 一起指到临时库 + 临时沙箱。"""
    from tools import schema_personal as sp, workdir_store as st, workdir_guard as g
    db = tmp_path / "t.db"
    monkeypatch.setattr(sp, "DB_PATH", str(db))
    monkeypatch.setattr(st, "get_personal_conn", sp.get_personal_conn)
    monkeypatch.setattr(st, "ensure_tables", sp.ensure_tables)
    monkeypatch.setattr(st, "seed_protect", g.seed_protect)
    sb = tmp_path / "sandbox" / "u"; sb.mkdir(parents=True)
    monkeypatch.setattr(st, "sandbox_dir", lambda name=None: sb)
    sp.ensure_tables()
    conn = sp.get_personal_conn()                 # account_workdirs 有 FK → 先造账号行
    conn.execute("INSERT INTO accounts (id, username, password_hash) VALUES (3,'u','x')")
    conn.commit(); conn.close()
    return sp, st, sb


def test_default_workdir_is_idempotent_and_undeletable(monkeypatch, tmp_path):
    sp, st, sb = _fake_db(monkeypatch, tmp_path)
    st.ensure_default(3, "u"); st.ensure_default(3, "u")            # 幂等
    rows = st.list_dirs(3, "u")
    assert len(rows) == 1 and rows[0]["name"] == "主页" and rows[0]["is_default"] and rows[0]["is_active"]
    assert (sb / "_保护名单.md").exists()                            # 建目录时播种
    assert st.remove(3, rows[0]["id"])["ok"] is False                # 主页不可删
    conn = sp.get_personal_conn()
    n = conn.execute("SELECT COUNT(*) FROM account_workdirs").fetchone()[0]
    conn.close()
    assert n == 1


def test_switch_and_active_follow(monkeypatch, tmp_path):
    sp, st, sb = _fake_db(monkeypatch, tmp_path)
    st.ensure_default(3, "u")
    ext = tmp_path / "ext"; ext.mkdir()
    r = st.add_external(3, "外部盘", str(ext))
    assert r["ok"], r
    assert str(st.active(3, "u")[0]) == str(sb)                      # 默认还在主页
    assert st.switch(3, r["id"])["ok"]
    assert str(st.active(3, "u")[0]) == str(ext.resolve())           # 切换后跟随
    rows = st.list_dirs(3, "u")
    assert sum(1 for x in rows if x["is_active"]) == 1               # 至多一个启用
    assert (ext / "_保护名单.md").exists()


def test_add_external_rejects_root_and_dupes(monkeypatch, tmp_path):
    sp, st, sb = _fake_db(monkeypatch, tmp_path)
    st.ensure_default(3, "u")
    assert not st.add_external(3, "C盘", "C:\\")["ok"]              # 拒盘符根
    ext = tmp_path / "ext2"; ext.mkdir()
    assert st.add_external(3, "e", str(ext))["ok"]
    assert not st.add_external(3, "e", str(ext))["ok"]               # 拒重复
    assert not st.add_external(3, "无", str(tmp_path / "nope"))["ok"]


def test_active_falls_back_to_sandbox_on_bad_path(monkeypatch, tmp_path):
    """外部目录后来被删掉 → 回落到沙箱，绝不抛异常中断对话。"""
    sp, st, sb = _fake_db(monkeypatch, tmp_path)
    st.ensure_default(3, "u")
    ext = tmp_path / "gone"; ext.mkdir()
    r = st.add_external(3, "稍后删掉", str(ext))
    st.switch(3, r["id"])
    import shutil; shutil.rmtree(ext)
    assert str(st.active(3, "u")[0]) == str(sb)


def test_active_survives_db_failure(monkeypatch, tmp_path):
    from tools import workdir_store as st, schema_personal as sp
    sb = tmp_path / "sb"; sb.mkdir()
    monkeypatch.setattr(st, "sandbox_dir", lambda name=None: sb)
    monkeypatch.setattr(st, "get_personal_conn",
                        lambda: (_ for _ in ()).throw(RuntimeError("db down")))
    assert str(st.active(3, "u")[0]) == str(sb)                       # 库坏了也回沙箱
