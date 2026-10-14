# -*- coding: utf-8 -*-
"""05 · 网络取法三分支的提示词断言（2026-10-14 定稿）。

**为什么要测提示词**：三支取法（全网检索 / 只进指定网址 / 先检索定位再进站）是**用提示词实现**的
（用户 2026-10-14 选定 C 案：维持一个子 Agent、模式写在任务描述里）。提示词是本项目里唯一没有类型约束的
"配置"，很容易在后续改动里被顺手删掉或改坏 —— 所以钉三条断言：
  1. YAML 必须仍可解析（缩进写错会**启动即崩**，这是本项目踩过的那类事故）；
  2. 三种模式字样与"声明之外不启用"的口径必须都在；
  3. 情况②的"不许先检索"、情况③的"1~2 个站点上限"这两条具体口径不能被抹掉。
"""
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent.parent
PROMPTS = ROOT / "prompt" / "prompts.yml"


def test_prompts_yaml_still_parses():
    y = yaml.safe_load(PROMPTS.read_text(encoding="utf-8"))
    assert "main_agent" in y and "sub_agents" in y
    assert "tavily" in y["sub_agents"], "网搜子 Agent 段不见了"


def test_main_agent_has_three_way_routing_section():
    y = PROMPTS.read_text(encoding="utf-8")
    assert "网络信息的三种取法" in y, "主 Agent 缺三分支路由节"
    for mode in ("只做全网检索", "只进这个网址", "先检索定位、再进站深抓"):
        assert mode in y, "缺模式字样：%s" % mode


def test_prompts_point_at_three_workflow_tools():
    """取法改由**工作流工具**承担（2026-10-14 用户定稿）：提示词必须点名三个工作流，
    并且子 Agent 只许调"点名的那一个"。"""
    y = PROMPTS.read_text(encoding="utf-8")
    seg = y.split("sub_agents:", 1)[1]
    for wf in ("web_search_only", "browse_only", "search_then_browse"):
        assert wf in y, "提示词未点名工作流：%s" % wf
    assert "只调主 Agent 点名的那一个工作流" in seg, "子 Agent 缺「只调点名工作流」的纪律"
    assert "1~2 个" in seg, "情况③的站点数上限丢了"


def test_plan_document_records_the_policy():
    """口径也要落在规划文档里（否则下次读文档的人不知道有三分支）。"""
    doc = (ROOT / "docs" / "v4.0" / "v4.0规划文档.md").read_text(encoding="utf-8")
    assert "网络搜索子Agent升级" in doc
