# -*- coding: utf-8 -*-
"""账号切换必须清空「按账号取数」的前端缓存（2026-10-15 用户报障）。

现象：以「药喵」登录后，「技能与工具」里仍是「尼古喵喵」的数据（CLI 4 项 / 题集 43 题 /
MCP 来源 5 条）；「灵康科技有限公司」同样如此。

排查结论（逐层核对过）：
  - 后端**没有问题**：`/api/cli/list`、`/api/tools/sources`、`/api/probe/cases` 拿到 token 后
    都按 `account_id` 过滤（存储层 `list_clis` / `sources_list` / `load_cases(<用户名>.json)` 全部带账号）；
    用真实会话直连接口复现，药喵返回的是药喵自己的 14 题、尼古喵喵返回 43 题，各归各家。
  - 问题在前端：这三个页签各有「加载过就不再取」的门闩（`cliLoaded` / `mcpAll.length` / `shAllowed.length`），
    题集还有「非空即保留」（`probeOpen` 里 `if (!probeCases.value.length)`）；
    而 `doLogin` / `doLogout` / `forceLogout` 都只清了消息与会话 → 切账号后它们**不重取**。

本文件把修补钉住：三处入口都必须调用 `resetAccountState()`，且该函数必须复位已知门闩与题集缓存。
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
HTML = ROOT / "front" / "index.html"


def _src() -> str:
    return HTML.read_text(encoding="utf-8")


def _fn_body(src: str, name: str) -> str:
    """抠出 `function <name>(...)` 的函数体（按缩进 ``    }`` 收尾，够本文件用）。"""
    i = src.index("function %s(" % name)
    lines = src[i:].splitlines()
    out = [lines[0]]
    for line in lines[1:]:
        out.append(line)
        if line == "    }":
            break
    return "\n".join(out)


def test_three_switch_points_all_reset():
    src = _src()
    for fn in ("doLogin", "doLogout", "forceLogout"):
        assert "resetAccountState()" in _fn_body(src, fn), (
            "%s 少了 resetAccountState()：切账号会留下上一个账号的缓存" % fn)


def test_reset_covers_known_latches_and_hold_caches():
    body = _fn_body(_src(), "resetAccountState")
    for key in ("cliLoaded", "mcpAll", "shAllowed",        # 三个「加载过就不再取」的门闩
                "probeCases", "probeScores", "probeAgent",  # 题集 + 人工终判/Agent 预填
                "skills", "myData", "sessions", "providers"):
        assert key in body, "%s 没被复位" % key


def test_reset_still_clears_prices():
    """价格缓存原来单独清（resetPriceState），新函数不能把它漏掉。"""
    assert "resetPriceState()" in _fn_body(_src(), "resetAccountState")


def test_module_entry_loads_the_current_subtab():
    """第二轮修复（2026-10-15 真机复测暴露）：只清缓存不触发重取 → 换账号后本模块一片空白。

    `go('skills')` 原来只调用 `loadSkills()`（技能子页签），其余五个子页签靠「子页签变化」
    才加载；登录后进本模块时子页签值没变 → 什么都不会取。现在必须走 `loadSkillsTab(true)`。
    """
    src = _src()
    go_line = next(l for l in src.splitlines() if "location.hash = '#/' + v;" in l)
    assert "loadSkillsTab(true)" in go_line, "进入「技能与工具」必须按当前子页签加载"


def test_subtab_watcher_has_no_once_latches():
    """子页签切换必须无条件重取（去掉 cliLoaded / mcpAll / shAllowed 的「加载过就不再取」）。"""
    src = _src()
    w = next(l for l in src.splitlines() if l.strip().startswith("watch(skTab"))
    assert "loadSkillsTab(true)" in w
    for latch in ("cliLoaded", "mcpAll", "shAllowed"):
        assert latch not in w, "子页签监听里还留着门闩：%s" % latch


def test_dispatcher_covers_all_six_subtabs():
    body = _fn_body(_src(), "loadSkillsTab")
    for t in ("'skill'", "'shell'", "'cli'", "'mcp'", "'api'", "'probe'"):
        assert t in body, "loadSkillsTab 没有覆盖子页签 %s" % t


def test_reset_also_clears_probe_board():
    """工具池大屏也要清：不清的话首帧会闪现上一个账号的指标（真机复测看到的就是这个）。"""
    body = _fn_body(_src(), "resetAccountState")
    assert "probe.value =" in body


def test_no_double_count_of_manual_clears():
    """注销路径用手写的零散清理换成统一复位后，别把 myData/sessions 的清空又写回去两遍。"""
    body = _fn_body(_src(), "doLogout")
    assert body.count("myData.value = emptyMy()") == 0, "doLogout 里应交给 resetAccountState 统一清"
