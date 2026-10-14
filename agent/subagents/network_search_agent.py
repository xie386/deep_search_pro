from agent.usage_counter import UsageCounterMiddleware   # M4-2：框架级用量计数（C1）
from agent.prompts import sub_agents_content
from tools.web_workflows import browse_only, search_then_browse, web_search_only   # 05：三个取法工作流
from utils import settings                                # 05：总开关在**装配期**生效（SPECS 里标了 restart=True）

# 05 口径（2026-10-14 用户定稿，裁定 6/7）：**取法由工作流工具决定** —— 底层 Tavily / 浏览器两个工具
#   已降级为 util（不再挂载），只被工作流调用，于是"情况②不许检索"这类约束是**结构性**的。
#   · `web_search_only`（只全网检索）总是挂载；
#   · `browse_only` / `search_then_browse` 需要浏览器，随 `BROWSER_USE_ENABLED` 开关；
#   · 关掉开关时子 Agent 只剩「只全网检索」→ 与升级前行为等价，可安全回滚。
#   为什么在 import 期判定：子 Agent 的工具列表是装配期固定的（框架约定）；工具内部另有一道兜底。
_TOOLS = [web_search_only]
if settings.get_bool("BROWSER_USE_ENABLED", True):
    _TOOLS += [browse_only, search_then_browse]

network_search_agent = {
    'name': sub_agents_content['tavily']['name'],
    'description': sub_agents_content['tavily']['description'],
    'system_prompt': sub_agents_content['tavily']['system_prompt'],
    "tools": _TOOLS,
    "middleware": [UsageCounterMiddleware()],     # M4-3：子 Agent **不吃**主图的 middleware，必须各自注册（C2）

}
