"""本轮 agent 运行的「用户强制中断」控制。

背景：前端在 AI 回答期间把「发送」按钮切成「中断」，点一下要能真的把正在跑的
`agent.invoke`（同步阻塞 30–200s、含多轮网搜）停下来，而不是只让前端不等了
（只丢前端会继续烧 token、继续写库、继续推 WS）。

设计：
  - **单机串行假设**：项目定位「本地纯个人使用」，同一时刻只跑一轮 invoke，故用
    模块级「当前会话」承载中断标志（与 `api/monitor.py` 的 `_current_thread_id`
    同款简化；若日后多用户并发，这里要换成 per-task 载体）。
    不用 threading.local：langchain 的异步路径会把 `_generate`/`_stream` 丢进
    线程池执行，thread-local 到那儿就丢了标志。
  - **检查点放在模型调用前**（`agent/reasoning_model.py` 的 `_generate`/`_stream`）：
    每个 agent 步骤都要过一次模型调用，在这里 raise 即「下一步立刻停」；模型调用
    的异常会一路穿透 langgraph 冒到 `invoke` 调用方，不会被工具节点的错误吞噬逻辑
    吃掉（那套只作用于工具调用）。
  - 缺点是**工具内部的长时间 HTTP 调用无法打断**（如一次 tavily 检索正在等响应），
    只能等它返回后在下一个模型调用点生效——秒级延迟，可接受。
"""
import threading

__all__ = ["AgentCancelled", "begin", "end", "request", "is_cancelled", "check", "running_thread"]


class AgentCancelled(Exception):
    """用户主动中断本轮运行。

    语义上不是错误，调用方（api/server.py 的 api_chat）应把它转成
    「已中断」的正常响应，而不是 500。
    """


_lock = threading.Lock()
_cancelled: set[str] = set()   # 已被请求中断的 thread_id
_current: str | None = None    # 当前正在跑的会话（单机串行，只可能有一个）


def begin(thread_id: str) -> None:
    """一轮 invoke 开始：清掉可能残留的中断标记，并登记为当前会话。"""
    global _current
    with _lock:
        _cancelled.discard(thread_id)
        _current = thread_id


def end(thread_id: str) -> None:
    """一轮 invoke 结束（正常返回或异常）：清标记，避免影响下一轮。"""
    global _current
    with _lock:
        if _current == thread_id:
            _current = None
        _cancelled.discard(thread_id)


def request(thread_id: str) -> bool:
    """前端请求中断。返回是否命中「此刻正在跑的会话」。"""
    with _lock:
        hit = _current == thread_id
        _cancelled.add(thread_id)
        return hit


def is_cancelled(thread_id: str | None = None) -> bool:
    """是否已请求中断（不传则看当前会话）。"""
    with _lock:
        tid = thread_id or _current
        return bool(tid) and tid in _cancelled


def check(thread_id: str | None = None) -> None:
    """检查点：已请求中断则抛 AgentCancelled。"""
    if is_cancelled(thread_id):
        raise AgentCancelled("用户已中断本轮回答")


def running_thread() -> str | None:
    """当前正在跑的会话 id（没有则为 None）。"""
    with _lock:
        return _current
