from agent.usage_counter import UsageCounterMiddleware   # M4-2：框架级用量计数（C1）
from agent.prompts import sub_agents_content
from tools.company_read_tools import (
    list_company_tables,
    get_company_data,
    query_company_db,
)

# 数据库查询子 Agent 的装配
# 结构说明（与 network_search_agent.py 保持一致）：
#   name          -> 子 Agent 的唯一标识，主 Agent 通过 task() 工具用这个名字调度它
#   description   -> 告诉主 Agent「这个专家能干什么」，用于决定何时委派任务
#   system_prompt -> 给这个子 Agent 的系统提示词（来自 prompt/prompts.yml）
#   tools         -> 这个子 Agent 能使用的工具（从 tools/db_tools.py 导入的三个 @tool）
db_agent = {
    'name': sub_agents_content['db']['name'],
    'description': sub_agents_content['db']['description'],
    'system_prompt': sub_agents_content['db']['system_prompt'],
    'tools': [list_company_tables, get_company_data, query_company_db],
    "middleware": [UsageCounterMiddleware()],     # M4-3：子 Agent **不吃**主图的 middleware，必须各自注册（C2）
}
