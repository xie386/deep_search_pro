from agent.usage_counter import UsageCounterMiddleware   # M4-2：框架级用量计数（C1）
from agent.prompts import sub_agents_content
from tools.tavily_tool import internet_search
from tools.browser_tool import browse_pages               # 05：浏览器工具（有网址时进站交互/深抓）
from utils import settings                                # 05：总开关在**装配期**生效（SPECS 里标了 restart=True）

# 05 口径：`BROWSER_USE_ENABLED=0` 时**该工具不挂进子 Agent** —— 与升级前逐字等价，可安全回滚。
#   为什么在 import 期判定：子 Agent 的工具列表是装配期固定的（框架约定）；工具内部另有一道兜底。
_TOOLS = [internet_search]
if settings.get_bool("BROWSER_USE_ENABLED", True):
    _TOOLS.append(browse_pages)

network_search_agent = {
    'name': sub_agents_content['tavily']['name'],
    'description': sub_agents_content['tavily']['description'],
    'system_prompt': sub_agents_content['tavily']['system_prompt'],
    "tools": _TOOLS,
    "middleware": [UsageCounterMiddleware()],     # M4-3：子 Agent **不吃**主图的 middleware，必须各自注册（C2）

}
