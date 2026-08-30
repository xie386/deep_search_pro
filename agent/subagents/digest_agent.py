from agent.prompts import sub_agents_content

# 定期情报周报助手（M4）
# 职责：把主 Agent 调度的候选情报筛选 + 撰写周报。
# 本助手不直接联网（检索由主 Agent 调度【网络搜索助手】完成），故不挂工具。
digest_agent = {
    'name': sub_agents_content['digest']['name'],
    'description': sub_agents_content['digest']['description'],
    'system_prompt': sub_agents_content['digest']['system_prompt'],
    "tools": []
}
