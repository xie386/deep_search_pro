# -*- coding: utf-8 -*-
"""前端调用方法 ↔ 后端路由方法 的一致性守卫。

★ 为什么要有它（2026-10-17 两次真机报障）：
  1. 回收站列表用 `fetch('/api/workdir/trash')`（GET）调 **POST** 路由 → 405，
     手写 fetch 又把失败当空列表 → 用户看到"删了文件，回收站却是空的"；
  2. `doLogout` 同样少写 `method` → 405，前端清了本地状态、**服务端 token 从没被注销**。
  两处都因为"失败被吞掉"，脚本测试与其它守卫全绿。所以这里做**静态对表**：
  前端每一处 `/api/...` 调用所声明的方法，必须在该路径的后端路由方法集合里。
"""
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SERVER = ROOT / "api" / "server.py"
INDEX = ROOT / "front" / "index.html"


def _routes() -> dict:
    src = SERVER.read_text(encoding="utf-8")
    out: dict[str, set] = {}
    for m in re.finditer(r'@app\.(get|post|put|delete)\("(/api/[^"]+)"', src):
        out.setdefault(m.group(2), set()).add(m.group(1).upper())
    return out


def _balanced(text: str, open_idx: int) -> str:
    """从 `(` 的位置取到配对的 `)`（含），忽略字符串里的括号。"""
    depth, i, quote = 0, open_idx, None
    while i < len(text):
        ch = text[i]
        if quote:
            if ch == "\\":
                i += 2
                continue
            if ch == quote:
                quote = None
        elif ch in "\"'`":
            quote = ch
        elif ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                return text[open_idx:i + 1]
        i += 1
    return text[open_idx:]


def _calls() -> list[tuple[str, str, int]]:
    """返回 [(方法, 路径, 行号)]，只收字面量路径（模板拼接的跳过）。"""
    src = INDEX.read_text(encoding="utf-8")
    out = []
    # api('POST', ...) 形式
    for m in re.finditer(r"\bapi\(\s*['\"](GET|POST|PUT|DELETE)['\"]", src):
        region = _balanced(src, src.index("(", m.start()))
        pm = re.search(r"['\"](/api/[^'\"?]+)", region)
        if pm:
            out.append((m.group(1), pm.group(1), src[:m.start()].count("\n") + 1))
    # fetch(...) 形式（方法写在选项里；没写 = GET）
    for m in re.finditer(r"\bfetch\(", src):
        region = _balanced(src, src.index("(", m.start()))
        pm = re.search(r"['\"](/api/[^'\"?]+)", region)
        if not pm:
            continue                      # 变量拼接（如 url 变量）→ 跳过
        mm = re.search(r"method:\s*['\"](GET|POST|PUT|DELETE)['\"]", region)
        out.append((mm.group(1) if mm else "GET", pm.group(1), src[:m.start()].count("\n") + 1))
    return out


def test_frontend_call_methods_match_backend_routes():
    if not (SERVER.exists() and INDEX.exists()):
        pytest.skip("找不到 api/server.py 或 front/index.html")
    routes = _routes()
    assert len(routes) > 100, "路由表解析异常（只解析出 %d 条）" % len(routes)
    bad = []
    for method, path, line in _calls():
        allow = routes.get(path)
        if allow and method not in allow:
            bad.append("行 %d：前端 %s %s，后端只收 %s" % (line, method, path, sorted(allow)))
    assert not bad, ("前端调用方法与后端路由不一致（会 405，而失败多半被静默吞掉）：\n  "
                     + "\n  ".join(sorted(set(bad))))


def test_no_silent_failure_on_trash_listing():
    """回收站列表必须有**失败可见**的分支（2026-10-17 用户的"回收站为空"就是失败被静默）。"""
    src = INDEX.read_text(encoding="utf-8")
    i = src.find("async function wdLoadTrash(")
    assert i > 0, "找不到 wdLoadTrash"
    body = src[i:i + 900]
    assert "action: 'list'" in body, "回收站列表要用 POST {action:'list'}（后端该路由只收 POST）"
    assert "回收站读取失败" in body, "取不到时要有可见错误，不能静默当成空列表"
