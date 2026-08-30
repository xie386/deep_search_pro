"""
deep_search_pro 入口：把「主 Agent + 子 Agent」真正组装并跑起来。

架构回顾（与 README 主线一致）：
    模型(agent/llm.py) + 提示词(prompt/prompts.yml)
        + 工具(tools/) + 子 Agent 装配(agent/subagents/)
        -> create_deep_agent 编排出主 Agent
        -> invoke 一个问题，主 Agent 会按需调度子 Agent 并调用工具

运行方式：
    uv run python main.py            # 用内置的示例问题跑一次
    uv run python main.py "你的问题"  # 指定自己的问题

注意：
    - 网络搜索子 Agent 需要 .env 中配置 TAVILY_API_KEY；
    - 数据库子 Agent 需要 .env 中配置 MYSQL_* 并能连通数据库；
    - RAGFlow 子 Agent 暂未装配（提示词已在 prompts.yml 预留），后续学完再补。
"""

import sys

from dotenv import load_dotenv
from langchain_core.messages import HumanMessage
from deepagents import create_deep_agent

from agent.llm import model
from agent.prompts import main_agent_content
from agent.subagents.network_search_agent import network_search_agent
from agent.subagents.db_agent import db_agent
from agent.subagents.personal_agent import personal_agent
from langgraph.checkpoint.memory import MemorySaver

# 加载 .env（确保模型 Key 等环境变量生效）
load_dotenv()


def build_agent():
    """装配主 Agent：挂载主 Agent 提示词 + 两个已实现的子 Agent。"""
    # 仅接入已经实现并配好工具的子 Agent；ragflow 暂未装配，先留空
    subagents = [network_search_agent, db_agent, personal_agent]

    agent = create_deep_agent(
        model=model,
        system_prompt=main_agent_content["system_prompt"],
        subagents=subagents,
        checkpointer=MemorySaver(),
    )
    return agent


def ask(agent,question: str, thread_id="u1") -> str:
    cfg = {"configurable": {"thread_id": thread_id}}
    result = agent.invoke(
        {"messages": [HumanMessage(content=question)]}, cfg
        )
    # result 是 DeepAgentState，最终回答在最后一条消息里
    answer = result["messages"][-1].content
    return answer


def main():
    # 关键：agent 只 build 一次，循环内复用同一个实例 + 同一个 checkpointer
    # 这样每轮对话的历史才会被 MemorySaver 累积（实现多轮上下文记忆）
    agent = build_agent()
    thread_id = "u1"
    while True:
        # 优先使用命令行参数作为问题，否则用内置示例
        print("用户👤：")
        question = input()
        if question.lower() == "exit" or question.lower() == "quit" or question.lower() == "q" or question.lower() == "退出":
            break
        answer = ask(agent, question, thread_id)
        print(f"\n👩‍⚕️Agent 回答:\n{'-' * 60}\n{answer}")


if __name__ == "__main__":
    main()
