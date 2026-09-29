# -*- coding: utf-8 -*-
"""提示词契约测试 —— 锁住「提示词 ↔ 代码」之间那些**隐式契约**（离线，不调模型、不联网）。

为什么单独一个文件：这几类 bug 的共同点是**静默失效**，单侧改动看不出来，只有真的跑起来
（甚至只有用户用起来）才暴露。历史实证：
  · M4 期「周报内容齐全、统计却记 0 条」——生成侧措辞漂移，解析正则没命中；
  · 2026-09 记忆被写进 `agents_docs/agents_docs/{user}/`——提示词里的路径写法与
    `write_agent_doc(filename)` 的基准目录不一致（能写成功、但加载器永远读不到）。

覆盖：
  1) 周报「扫描说明」格式契约（prompts.yml digest 段 ↔ digest_engine.SCAN_LINE_* ↔ 解析正则）
  2) 能力描述契约（ability_writer 要求用户写的三种形态 ↔ cli_registry._parse_ability_pairs 能解析）
  3) 记忆/技能文件的路径写法（注入块与静态提示词都必须给「相对 agents_docs」的路径）
  4) 提示词防腐（陈旧术语不再出现；CLI 机械细节不再回流进主提示词）

运行：.venv/Scripts/python.exe -m pytest tests/test_prompt_contracts.py -q
"""

import io
import os
import sys

import pytest
import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from agent import build_context  # noqa: E402
from agent import digest_engine as de  # noqa: E402
from tools import capability_pool as pool  # noqa: E402
from tools import cli_registry as reg  # noqa: E402

PROMPTS = yaml.safe_load(io.open(os.path.join(ROOT, "prompt", "prompts.yml"), encoding="utf-8"))
MAIN = PROMPTS["main_agent"]["system_prompt"]
DIGEST = PROMPTS["sub_agents"]["digest"]["system_prompt"]


# ---------------------------------------------------------------- 1) 周报格式契约
def test_digest_scan_line_contract_matches_parser():
    """生成侧措辞必须能被解析侧认出（改一侧忘了另一侧时立刻红）。"""
    assert de._SCAN_CONTRACT_OK, "契约常量与 SCAN_LINE_RE 不一致"
    m = de.SCAN_LINE_RE.search(de.SCAN_LINE_SAMPLE)
    assert m and m.groups() == ("12", "8")


def test_digest_prompt_states_same_scan_line_contract():
    """prompts.yml 的 digest 段必须与 digest_engine 的契约同口径。

    容忍 markdown 加粗 / 全角逗号差异（解析正则本来就容忍），所以这里断言的是
    「关键片段」而不是整句逐字相等。
    """
    for frag in ("共扫描", "条候选", "精选出", "条"):
        assert frag in DIGEST, "digest 段缺少扫描说明的关键片段：%s" % frag
    # 契约常量里的占位词也必须能在 digest 段里找到（说明两处讲的是同一件事）
    assert "扫描" in DIGEST and "候选" in DIGEST and "精选" in DIGEST


# ---------------------------------------------------------------- 2) 能力描述契约
def test_ability_writer_contract_is_parseable():
    """ability_writer 要求用户写的三种形态，解析器必须都能吃下。

    形态（见 prompts.yml ability_writer 段）：
      · 单命令箭头：`我正在读什么书→shelf recent`
      · 两步链：   `书评怎么样→book resolve 再 reviews list`（示例只取链上第一步）
      · 关键词行： `关键词：读书/阅读/书架`
    """
    text = ("我正在读什么书→shelf recent；读到哪了→shelf recent 再 book progress；"
            "帮我找书→search")
    pairs = reg._parse_ability_pairs(text)
    assert ("我正在读什么书", "shelf recent") in pairs
    # 两步链只取第一步（第二步需要 id，不能直接调）
    two = dict(reg._parse_ability_pairs("书评怎么样→book resolve 再 reviews list"))
    assert two.get("书评怎么样") == "book resolve", two
    # 关键词行必须能被 cli_entry 解析出来（路由信号），且不残留「关键词：」前缀
    entry = pool.cli_entry({"bin": "probe", "name": "探针", "live": True,
                           "abilities": "关键词：读书/阅读/书架\n我在读什么→shelf recent",
                           "rules": [["shelf", "recent"]]})
    assert entry.keywords.startswith("读书"), entry.keywords
    assert "关键词" not in entry.keywords


# ---------------------------------------------------------------- 3) 路径写法契约
def test_memory_path_must_be_relative_to_agents_docs():
    """注入块给模型的记忆文件必须是「相对 agents_docs」的路径，绝不能带前缀。"""
    s = build_context.compose_dynamic_prompt(memory_text="喜欢猫", username="alice")
    assert "alice/MEMORY.md" in s
    assert "agents_docs/alice/MEMORY.md" not in s, "带前缀的写法会让模型写进 agents_docs/agents_docs/"


def test_static_prompt_uses_relative_paths_too():
    """静态提示词里的记忆/技能路径同样不得写成 `agents_docs/<用户名>/...`。

    注意：提示词**合法地**提到「不要带 `agents_docs/` 前缀」这句禁令，所以这里断言的是
    **路径形态**（前缀直接接占位符/用户名），而不是"文本里不出现 agents_docs/"。
    """
    assert "<用户名>/MEMORY.md" in MAIN
    assert "<用户名>/skills/" in MAIN
    for bad in ("agents_docs/{username}", "agents_docs/<用户名>", "agents_docs/{用户名}",
                "agents_docs/alice"):
        assert bad not in MAIN, "主提示词里出现了带前缀的路径写法：%s" % bad


# ---------------------------------------------------------------- 4) 防腐断言
@pytest.mark.parametrize("stale", ["our_products", "MySQL", "ragflow", "RAGFlow"])
def test_prompt_has_no_stale_terms(stale):
    """v2.0 期间被替换掉的旧事实不得回流（公司侧已迁 SQLite、RAGFlow 方案已弃）。"""
    assert stale not in MAIN, "main_agent 段仍含陈旧术语：%s" % stale
    for k, v in PROMPTS["sub_agents"].items():
        assert stale not in (v.get("system_prompt") or ""), "%s.system_prompt 仍含陈旧术语" % k
        assert stale not in (v.get("description") or ""), "%s.description 仍含陈旧术语" % k


def test_cli_mechanics_not_duplicated_into_static_prompt():
    """CLI 调用的机械细节只应存在于工具说明与注入块，不该回流进常驻提示词。"""
    for frag in ("--output", "argv=[", "-o ", "落盘参数"):
        assert frag not in MAIN, "主提示词里又出现了 CLI 机械细节：%s" % frag


def test_main_prompt_declares_precedence_and_tool_layers():
    """结构化成果别被后续里程碑追加冲掉：优先级声明 + 三类工具 + 只列名称仍可调用。"""
    assert "指令优先级" in MAIN
    assert "先后顺序" in MAIN, "「先用账号内工具、不足再联网」的顺序约束被删了"
    assert "只列名称不等于不可用" in MAIN
    assert "你手上的三类工具" in MAIN


# ============================ M3：画像提示词契约（防两段口径漂移）============================
def _m3_prompts():
    with io.open(os.path.join(ROOT, "prompt", "prompts.yml"), encoding="utf-8") as f:
        return yaml.safe_load(f)


def _seg(doc, key):
    body = doc[key]["system_prompt"] if isinstance(doc[key], dict) else doc[key]
    return body


def test_memory_prompts_share_hard_constraints():
    """初始化器与建议器是两段，但**共有硬约束必须逐字一致** —— 单侧漂移会让"同一份画像"两种口径。"""
    doc = _m3_prompts()
    for key in ("memory_initializer", "memory_suggester"):
        seg = _seg(doc, key)
        assert "150~400" in seg, "%s 缺字数约束" % key
        assert "`##`" in seg or "## 标题" in seg, "%s 缺「不得输出 ## 标题」" % key
        assert "绝不编造" in seg, "%s 缺「只依据证据」" % key
        assert "占位" in seg, "%s 缺「不写占位」" % key


def test_initializer_no_longer_asks_for_placeholders():
    """R3：初始化器曾被要求"缺失维度写「暂无」"→ 已删（否则下游把占位当真实内容）。"""
    seg = _seg(_m3_prompts(), "memory_initializer")
    assert "如实说明「暂无」" not in seg
    assert "预算范围：暂无" not in seg
    assert "不要写" in seg and "占位" in seg


def test_main_prompt_points_to_structured_profile_tool():
    """主提示词的「记忆维护」段必须指向 update_user_profile、写明就地更新、给待更新信号一个去处。"""
    doc = _m3_prompts()
    main = json_dumps = None
    seg = ""
    for v in doc.values():
        if isinstance(v, dict) and "system_prompt" in v and "记忆维护" in str(v["system_prompt"]):
            seg = str(v["system_prompt"])
    assert seg, "找不到带「记忆维护」段的主提示词"
    assert "update_user_profile" in seg and "就地更新" in seg
    assert "【画像待更新】" in seg
    assert "写前先用【读取文档工具】看当前内容" not in seg, "旧的「读全文再重写」口径已废（R2 根因）"
