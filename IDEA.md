初学者框架型项目,后续改造成成熟应用项目
from langgraph.checkpoint.memory import MemorySaver

def build_agent():
    return create_deep_agent(
        model=model,
        system_prompt=main_agent_content["system_prompt"],
        subagents=[network_search_agent, db_agent],
        checkpointer=MemorySaver(),   # 加上这一行
    )

# 同一个 thread_id 串起多轮
def ask(agent, question, thread_id="u1"):
    cfg = {"configurable": {"thread_id": thread_id}}
    result = agent.invoke(
        {"messages": [HumanMessage(content=question)]},
        cfg,
    )
    return result["messages"][-1].content
