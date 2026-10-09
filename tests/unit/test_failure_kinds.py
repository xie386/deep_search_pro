# -*- coding: utf-8 -*-
"""M6c-6 用例：失败分类（`exit_codes.py` 口径 + `failure_log` 落库/汇总 + `/api/failures` + 静态不变量）。

判据来源：M6c 方案 §3.4（Part B）+ §六 M6c-6（≥10 项）+ G7。
★ 两条关键口径：
  1. **只收敛口径、不改行为**（C5）—— 看护逻辑（只对自起后端生效、绝不动用户服务）必须原样保留；
  2. **静态不变量**：枚举名不许在外壳里当字面量写死（否则明年又是散字符串）—— 与"死路由不变量"同手法。

全离线：不启动外壳、不连模型（外壳 `app.py` **一 import 就会拉起整个桌面应用**，所以这里只做 AST 静态检查 ✗）。

运行：.venv/Scripts/python.exe -m pytest tests/unit/test_failure_kinds.py -q
"""
import ast
import os
import sys
import time

import pytest
from fastapi import HTTPException

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "front", "desktop"))     # exit_codes 是外壳的兄弟模块

import exit_codes                                                             # noqa: E402
from api import failure_api                                                    # noqa: E402
from tools import failure_log                                                  # noqa: E402
from tools.schema_personal import ensure_tables, get_personal_conn, purge_account   # noqa: E402

ensure_tables()

APP_PY = os.path.join(ROOT, "front", "desktop", "app.py")


def mk_account(tag="fk"):
    conn = get_personal_conn()
    try:
        aid = int(conn.execute("INSERT INTO accounts (username, password_hash, role) VALUES (?,?,?)",
                               ("m6c%s_%d" % (tag, int(time.time() * 1000) % 1000000), "x", "user")).lastrowid)
        tok = "tk%s%d" % (tag, aid)
        conn.execute("INSERT INTO sessions (token, account_id, login_at) VALUES (?,?,datetime('now'))",
                     (tok, aid))
        conn.commit()
    finally:
        conn.close()
    return aid, tok


# --------------------------------------------------------------- ① 码表与分类

def test_every_code_has_severity_label_and_advice():
    """码表完整性：严重度合法、中文与建议都不为空（否则界面/日志会露出半句）。"""
    for code, (sev, zh, advice) in exit_codes.CODES.items():
        assert sev in (exit_codes.INFO, exit_codes.WARN, exit_codes.ERROR), code
        assert zh and zh.strip(), code
        assert advice and advice.strip(), code
        assert code == code.upper() and " " not in code, code


def test_all_existing_shell_strings_classify():
    """G7：**现有全部退出码字符串**都要能映射到枚举（这是"收敛"的底线）。"""
    cases = {
        "child_exit_port_taken:1": "BACKEND_PORT_TAKEN",
        "child_exit:1": "BACKEND_START_FAILED",
        "child_exit_gone:0": "BACKEND_START_FAILED",
        "ready_timeout:180": "BACKEND_READY_TIMEOUT",
        "restart_exhausted:3": "BACKEND_RESTART_EXHAUSTED",
        "webview_init_failed": "WEBVIEW_INIT_FAILED",
        "user_quit": "USER_QUIT",
        "BACKEND_CRASHED": "BACKEND_CRASHED",          # 已经是枚举名 → 原样认
    }
    for raw, want in cases.items():
        assert exit_codes.classify(raw) == want, raw


def test_unknown_string_degrades_with_raw_kept():
    """认不出来 → UNKNOWN，但**保留原文**（不能把证据丢掉）。"""
    raw = "weird_failure_42: 端口 9999 炸了"
    assert exit_codes.classify(raw) == "UNKNOWN"
    line = exit_codes.describe(raw)
    assert "UNKNOWN" in line and "weird_failure_42" in line


def test_empty_and_none_classify_to_unknown():
    assert exit_codes.classify("") == "UNKNOWN"
    assert exit_codes.classify(None) == "UNKNOWN"
    assert exit_codes.classify("   ") == "UNKNOWN"


def test_describe_shape_is_user_readable():
    """一行人话：码名 | 中文 | 建议：…（日志与界面共用，避免两处口径）。"""
    line = exit_codes.describe("child_exit_port_taken:1", "退出码 1")
    assert line.startswith("BACKEND_PORT_TAKEN |")
    assert "端口被占用" in line and "建议：" in line and "退出码 1" in line
    assert "不作为失败" not in exit_codes.describe("child_exit:1")     # info 码的建议不会串到 error 码上


# --------------------------------------------------------------- ② 静态不变量（G7）

def test_enum_names_are_not_hardcoded_in_shell():
    """★ 静态不变量：枚举名只许来自 `exit_codes`，不许在外壳里写成字面量。"""
    src = open(APP_PY, encoding="utf-8").read()
    lits = set()
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            lits.add(node.value)
    leaked = sorted(lits & set(exit_codes.CODES.keys()))
    assert not leaked, "外壳里出现了裸的失败码字面量：%s（应走 exit_codes）" % leaked


def test_shell_routes_failures_through_log_failure():
    """两条失败路径都要经过 `_log_failure`（分类 + 落库），且看护逻辑没被改动。"""
    src = open(APP_PY, encoding="utf-8").read()
    assert "def _log_failure(" in src
    assert src.count("_log_failure(") >= 3          # 定义 1 次 + 调用 ≥2 处
    assert "import exit_codes" in src
    # C5：看护/退出相关关键字原样保留（只改口径，不动行为）
    for keep in ("_OWN_BACKEND", "def stop_backend(", "只关自己起的服务"):
        assert keep in src, keep


# --------------------------------------------------------------- ③ 落库与汇总

def test_record_writes_event_with_nullable_account():
    """外壳失败发生在登录前 → `account_id` 写 NULL（列必须可空，方案 §5.1 原文本就可空）。"""
    assert failure_log.record("BACKEND_PORT_TAKEN", "10048 端口被占") is True
    conn = get_personal_conn()
    try:
        r = conn.execute("SELECT account_id, code, detail FROM failure_events"
                         " ORDER BY id DESC LIMIT 1").fetchone()
    finally:
        conn.close()
    assert r[0] is None and r[1] == "BACKEND_PORT_TAKEN" and "10048" in r[2]


def test_record_never_raises(monkeypatch):
    """记库失败不许把外壳再炸一次（返回 False 即可）。"""
    import tools.schema_personal as sp

    def boom():
        raise RuntimeError("db is locked")
    monkeypatch.setattr(failure_log, "get_personal_conn", boom, raising=False)
    assert failure_log.record("USER_QUIT") is False


def test_summary_counts_by_code_and_window():
    """按码计数 + 时间窗过滤（"最近 7 天它崩了几次"）。"""
    aid, tok = mk_account()
    try:
        assert failure_log.record("BACKEND_CRASHED", "x", aid) is True
        assert failure_log.record("BACKEND_CRASHED", "y", aid) is True
        assert failure_log.record("WEBVIEW_INIT_FAILED", "z", aid) is True
        out = failure_log.summary(aid, days=7)
        # 只断言**本用例自己造的码**：系统级（account_id IS NULL）的行按设计也可见 ✓
        got = {g["code"]: g["count"] for g in out["by_code"]}
        assert got["BACKEND_CRASHED"] == 2 and got["WEBVIEW_INIT_FAILED"] == 1
        assert out["recent"]
        # 把两条改成 40 天前 → 窗口收紧后就看不到了
        conn = get_personal_conn()
        try:
            conn.execute("UPDATE failure_events SET occurred_at=? WHERE account_id=?",
                         (time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(time.time() - 40 * 86400)), aid))
            conn.commit()
        finally:
            conn.close()
        got7 = {g["code"]: g["count"] for g in failure_log.summary(aid, days=7)["by_code"]}
        got90 = {g["code"]: g["count"] for g in failure_log.summary(aid, days=90)["by_code"]}
        assert "BACKEND_CRASHED" not in got7 and got90["BACKEND_CRASHED"] == 2   # 窗口收紧 → 看不见
    finally:
        purge_account(aid)


def test_summary_scope_is_own_plus_system_only():
    """可见范围：自己的 + 系统级（NULL）；**别人的看不到**。"""
    a1, _ = mk_account("x")
    a2, _ = mk_account("y")
    try:
        failure_log.record("BACKEND_CRASHED", "别人的", a2)
        failure_log.record("BACKEND_PORT_TAKEN", "系统级", None)
        out = failure_log.summary(a1, days=7)
        codes = {g["code"] for g in out["by_code"]}
        assert "BACKEND_PORT_TAKEN" in codes            # 系统级的能看到
        assert "BACKEND_CRASHED" not in codes           # 别人的看不到
    finally:
        purge_account(a1)
        purge_account(a2)


def test_failures_endpoint_shape_and_bad_args():
    """出口：带码表文案（前端不必内置一份）；非法 days → 400。"""
    aid, tok = mk_account()
    try:
        failure_log.record("BACKEND_CRASHED", "崩", aid)
        out = failure_api.failures(tok, days=7)
        got = {g["code"]: g["count"] for g in out["by_code"]}
        assert got["BACKEND_CRASHED"] == 1 and out["total"] >= 1
        assert out["codes"]["BACKEND_CRASHED"]["label"] and out["codes"]["BACKEND_CRASHED"]["severity"] == "error"
        with pytest.raises(HTTPException) as e:
            failure_api.failures(tok, days="七天")
        assert e.value.status_code == 400
        with pytest.raises(HTTPException):
            failure_api.failures("不存在的token", days=7)
    finally:
        purge_account(aid)


# ══════════════════════════════════════════════════════════════════════════
# ★ 2026-10-09 真机观感测试抓到的缺陷：`--wait` 生效值是 3，日志却写「等待 180s」
#   —— 报数用了模块常量 `READY_TIMEOUT`，不是形参 `timeout`。用户看日志会以为等了 180 秒。
#   守卫：`start_backend` 里凡是报「等待/超时多少秒」的调用，必须引用生效值 `timeout`。
# ══════════════════════════════════════════════════════════════════════════
def test_timeout_messages_use_effective_value_not_module_constant():
    src = open(APP_PY, encoding="utf-8").read()
    tree = ast.parse(src)
    fn = next((n for n in ast.walk(tree)
               if isinstance(n, ast.FunctionDef) and n.name == "start_backend"), None)
    assert fn is not None, "外壳里找不到 start_backend（改名了？）"

    hits = []
    for node in ast.walk(fn):
        if not isinstance(node, ast.Call):
            continue
        fname = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
        if fname not in ("log", "_log_failure"):
            continue
        seg = ast.get_source_segment(src, node) or ""
        if ("ready_timeout" in seg) or ("等待超时" in seg):
            hits.append(seg)

    assert len(hits) >= 2, "预期两处报数（失败分类 + 人话日志），实际找到 %d 处：%s" % (len(hits), hits)
    for seg in hits:
        assert "READY_TIMEOUT" not in seg, "报数必须用生效值 timeout，不能用模块常量 READY_TIMEOUT：\n%s" % seg
        assert "timeout" in seg, "报数里没见到生效值 timeout：\n%s" % seg
