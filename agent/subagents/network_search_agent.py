from agent.usage_counter import UsageCounterMiddleware   # M4-2：框架级用量计数（C1）
from agent.prompts import sub_agents_content
from tools.tavily_tool import internet_search

network_search_agent = {
    'name': sub_agents_content['tavily']['name'],
    'description': sub_agents_content['tavily']['description'],
    'system_prompt': sub_agents_content['tavily']['system_prompt'],
    "tools": [internet_search],
    "middleware": [UsageCounterMiddleware()],     # M4-3：子 Agent **不吃**主图的 middleware，必须各自注册（C2）

}
