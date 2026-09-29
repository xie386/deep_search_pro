from agent.usage_counter import UsageCounterMiddleware   # M4-2：框架级用量计数（C1）
from agent.prompts import sub_agents_content
from tools.personal_tools import (
    list_personal_tables,
    get_personal_data,
    query_personal_db,
)

# 个人侧子 Agent 的装配（与公司侧 db_agent 平行）
# 结构：name/description/system_prompt 来自 prompts.yml 的 sub_agents.personal
#       tools 来自 tools/personal_tools.py（操作本地 SQLite 个人库）
personal_agent = {
    'name': sub_agents_content['personal']['name'],
    'description': sub_agents_content['personal']['description'],
    'system_prompt': sub_agents_content['personal']['system_prompt'],
    'tools': [list_personal_tables, get_personal_data, query_personal_db],
    "middleware": [UsageCounterMiddleware()],     # M4-3：子 Agent **不吃**主图的 middleware，必须各自注册（C2）
}
