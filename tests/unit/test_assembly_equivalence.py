# -*- coding: utf-8 -*-
"""M4-7 用例：装配收口的**逐字等价性**（老路径 vs 注册表路径）+ 回退开关两态。

判据来源：M4 方案 §六 M4-7（≥8 组输入**逐字相同** + 开关两态用例）+ C7（**先写等价性、后切**）。

做法：同一组内容，一条走老路径（soul_text/memory_text 显式传入），一条走新路径（sources=…），
断言两者产出的**注入文本逐字相同**。这是"切换不许改变行为"的唯一证明方式。

运行：.venv/Scripts/python.exe -m pytest tests/test_assembly_equivalence.py -q
"""
import os
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

from agent.build_context import build_request_context   # noqa: E402

SOUL = "你是女仆，语气轻快。"
MEM = "# 用户记忆画像（MEMORY）\n\n<!-- memory_meta: init=2026-09-13 refreshed=2026-09-27 sources=3 -->\n\n## USER PROFILE（用户画像）\n\n- 身份与领域：大四学生（人工）\n- 预算范围：≤100 元/瓶\n\n## MEMORY（助手笔记）\n\n- 笔记 1：要记住\n"
MEM_BIG = MEM.replace("- 预算范围：≤100 元/瓶", "- 预算范围：" + "很久很久以前的故事啊" * 40)
MEM_ODD = "喜欢猫，晚上喝美式\n不喜欢开会"          # 非标准排版（分块兜底那条路径）
CLI = "【用户自配的工具能力（CLI 只读 / API / MCP）】\n- `weread`：微信读书"
SKILLS = "【本轮启用技能】\n- 简报：把要点压成三行"


def text_of(ctx):
    """取"注入进模型的那段文本"：按已知字段名依次尝试（拿不到就退化为 repr 比对）。"""
    for attr in ("system_text", "dynamic_text", "system_prompt"):
        v = getattr(ctx, attr, None)
        if isinstance(v, str) and v:
            return v
    msgs = getattr(ctx, "messages", None)
    if msgs:
        try:
            return str(getattr(msgs[0], "content", msgs[0]))
        except Exception:
            pass
    return repr(ctx)


def run_both(soul="", memory="", **kw):
    legacy = build_request_context("t-eq", 7, "问句", soul_text=soul, memory_text=memory, **kw)
    new = build_request_context("t-eq", 7, "问句",
                                sources={"soul": soul, "memory": memory}, **kw)
    return text_of(legacy), text_of(new)


# ---------------------------------------------------------------- ① 逐字等价（≥8 组）
CASES = {
    "只有人格": dict(soul=SOUL),
    "只有记忆": dict(memory=MEM),
    "人格+记忆": dict(soul=SOUL, memory=MEM),
    "都为空": dict(),
    "超预算记忆": dict(soul=SOUL, memory=MEM_BIG),
    "非标准排版记忆": dict(memory=MEM_ODD),
    "记忆+技能": dict(soul=SOUL, memory=MEM, skills_text=SKILLS),
    "记忆+CLI简报": dict(soul=SOUL, memory=MEM, cli_brief=CLI),
    "记忆+待更新信号": dict(soul=SOUL, memory=MEM, profile_signal="【画像待更新】新增 1 条兴趣。"),
    "全都有": dict(soul=SOUL, memory=MEM, skills_text=SKILLS, cli_brief=CLI,
                   profile_signal="【画像待更新】新增 1 条兴趣。"),
}


@pytest.mark.parametrize("name", list(CASES))
def test_legacy_and_registry_paths_are_byte_identical(name):
    legacy, new = run_both(username="xieky", **CASES[name])
    assert legacy == new, "★ 切换不许改变行为：%s 两条路径必须逐字相同" % name


def test_equivalence_body_is_actually_nonempty():
    """防止"两边都空所以相等"的假绿：至少有一组要真的注入了人格与记忆。"""
    legacy, new = run_both(username="xieky", **CASES["人格+记忆"])
    assert legacy == new and "女仆" in legacy and "预算范围" in legacy


def test_sources_none_keeps_old_behaviour():
    """sources 不传 = 老路径（兼容旧调用点，M4-7 的向后兼容面）。"""
    a = build_request_context("t", 7, "q", soul_text=SOUL, memory_text=MEM, username="u")
    b = build_request_context("t", 7, "q", soul_text=SOUL, memory_text=MEM, username="u", sources=None)
    assert text_of(a) == text_of(b)


# ---------------------------------------------------------------- ② 回退开关两态
def test_switch_default_is_registry_path():
    s = open(os.path.join(ROOT, "api", "server.py"), encoding="utf-8").read().replace("\r\n", "\n")
    assert "ZX_ASSEMBLY_SOURCES" in s, "回退开关必须存在（C7）"
    assert "context_sources.collect(account_id, question)" in s, "默认路径要走注册表收集"
    assert "soul_text=soul_text, memory_text=memory_text" in s, "legacy 分支要保留老的显式传参"


def test_switch_env_var_flips_the_path():
    """真起一个子进程读开关（不在本进程 import，避免污染其它用例）。"""
    code = ("import api.server as s; print('USE_SOURCES=%s' % s._USE_SOURCES)")
    env2 = {**os.environ, 'PYTHONPATH': '', 'PYTHONIOENCODING': ''}
    a = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                       cwd=ROOT, env=env2)
    b = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, cwd=ROOT,
                       env={**env2, "ZX_ASSEMBLY_SOURCES": "legacy"})
    assert "USE_SOURCES=True" in a.stdout, a.stdout[-300:] + a.stderr[-300:]
    assert "USE_SOURCES=False" in b.stdout, b.stdout[-300:] + b.stderr[-300:]


def test_providers_registered_at_startup():
    s = open(os.path.join(ROOT, "api", "context_providers.py"), encoding="utf-8").read()
    assert "register(\"soul\"" in s and "register(\"memory\"" in s
    assert "read_memory_for_agent" in s, "记忆源必须与周报共用同一个读取函数（C6）"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
