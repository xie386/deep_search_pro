"""思考内容旁路捕获（方案 B：底层 openai client 直读 reasoning_content）。

背景：
  langchain-openai 1.4.1/1.6.0 明确丢弃第三方 base_url 的非标准字段
  （reasoning_content 不提取不保留），所以拿不到模型的思考过程。
  本模块绕开 langchain 解析层：用底层 openai client 对同一组消息发流式请求，
  逐 token 读取 choices[0].delta.reasoning_content，通过 monitor.report_thinking
  实时推给前端（右栏「实时过程监控」）。

用法（在 agent.invoke 前调用）：
  from agent.thinking_capture import stream_thinking
  stream_thinking(messages, thread_id)   # 阻塞直到 reasoning 流结束或超时

注意：
  - 依赖 .env 的 OPENAI_API_KEY / OPENAI_BASE_URL / LLM_MODEL_MAX（与主模型同配置）。
  - 旁路请求与主 agent 请求消息序列一致，保证思考内容贴合实际任务。
  - 免费模型无 token 顾虑；本模块专为本项目「智选情报官」设计。
"""
import os
import sys
import threading
import time

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
sys.path = [p for p in sys.path if "hermes-agent" not in p and "hermes_agent" not in p]

from dotenv import load_dotenv, find_dotenv
from openai import OpenAI

from api.monitor import monitor

load_dotenv(find_dotenv())


def _raw_client() -> OpenAI:
    return OpenAI(
        api_key=os.getenv("OPENAI_API_KEY"),
        base_url=os.getenv("OPENAI_BASE_URL"),
    )


def stream_thinking(
    messages: list,
    thread_id: str,
    timeout: float = 60.0,
    max_think_chars: int = 4000,
) -> str:
    """对 messages 发流式请求，抓取并推送 reasoning_content 思考过程。

    与主 agent 使用同一模型（LLM_MODEL_MAX），消息序列一致；
    思考内容逐段经 monitor.report_thinking 推送到 thread_id 的 WS。
    返回捕获到的完整 thinking 文本（供调用方记录/调试）。

    :param messages: OpenAI 格式消息列表（含 system + user，与主 agent 一致）
    :param thread_id: WS 目标线程（monitor 定向推送用）
    :param timeout: 最长等待秒数（旁路不应阻塞主流程过久）
    :param max_think_chars: 思考内容累计上限，防止无限输出
    """
    monitor.set_current_thread(thread_id)
    thinking_parts: list[str] = []
    start = time.time()
    try:
        client = _raw_client()
        stream = client.chat.completions.create(
            model=os.getenv("LLM_MODEL_MAX"),
            messages=messages,
            stream=True,
            temperature=0.7,
        )
        for chunk in stream:
            if time.time() - start > timeout:
                break
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta
            rc = getattr(delta, "reasoning_content", None)
            if rc:
                thinking_parts.append(rc)
                monitor.report_thinking(rc)  # 逐段推送
                if sum(len(p) for p in thinking_parts) >= max_think_chars:
                    break
    except Exception as e:
        # 旁路失败不影响主流程：只打印，不抛
        print(f"[thinking] 旁路捕获异常（忽略）: {type(e).__name__}: {e}")
    finally:
        monitor.clear_current_thread()
    return "".join(thinking_parts)


def start_thinking_thread(
    messages: list,
    thread_id: str,
    timeout: float = 60.0,
) -> threading.Thread:
    """在后台线程启动旁路捕获（不阻塞主 agent 执行）。

    agent.invoke 前调用；返回线程句柄（无需 join，旁路自行超时结束）。
    """
    t = threading.Thread(
        target=stream_thinking,
        args=(messages, thread_id, timeout),
        daemon=True,
    )
    t.start()
    return t
