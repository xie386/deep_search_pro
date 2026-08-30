from dotenv import load_dotenv, find_dotenv
import os

from agent.reasoning_model import ReasoningChatOpenAI

# 加载配置文件
# find_dotenv() 确保找到 .env文件 递归查询当前项目文件夹
load_dotenv(find_dotenv())

"""os.getenv() 从环境变量中获取变量值"""

# 用 ReasoningChatOpenAI（继承 ChatOpenAI）替代 init_chat_model("openai")：
# 唯一区别是它会保留第三方模型的 reasoning_content（思考过程）到
# additional_kwargs，供前端「思考过程可视化」展示——单请求、不弃 langchain。
model = ReasoningChatOpenAI(
    model=os.getenv("LLM_MODEL_MAX"),
    api_key=os.getenv("OPENAI_API_KEY"),
    base_url=os.getenv("OPENAI_BASE_URL"),
)
