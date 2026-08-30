from dotenv import load_dotenv,find_dotenv
import os
from langchain.chat_models import init_chat_model
 
# 加载配置文件
# find_dotenv() 确保找到 .env文件 递归查询当前项目文件夹
load_dotenv(find_dotenv())

"""
os.getenv() 从环境变量中获取变量值
"""

model = init_chat_model(
    model=os.getenv("LLM_MODEL_MAX"),
    model_provider="openai"
)

