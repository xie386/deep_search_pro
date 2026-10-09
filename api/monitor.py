import datetime
import asyncio
from typing import Any, Dict, Optional
from fastapi import WebSocket
from api.context import get_thread_context

# 尝试导入全局运行时（用于脚本模式下的流式输出）
try:
    import builtins
except ImportError:
    builtins = None


class ToolMonitor:
    """
    工具监控类，用于在工具执行过程中上报进度和状态。
    设计为单例模式，可在任何工具中直接导入使用。
    兼容 FastAPI WebSocket 和 脚本运行时的 stream_writer。

    使用示例:
    from api.monitor import monitor

    def my_tool(arg1):
        monitor.report_start("my_tool", {"arg1": arg1})
        ...
        monitor.report_running("my_tool", "正在处理数据...", progress=0.5)
        ...
        monitor.report_end("my_tool", result)
    """
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(ToolMonitor, cls).__new__(cls)
            cls._instance.websocket_manager = None  # 预留给 FastAPI WebSocketManager
            cls._instance._current_thread_id = None  # 跨线程推送用的当前 thread_id
            cls._instance._console_sink = None       # 非 Web 前端（如 dspro CLI）可接管控制台输出
        return cls._instance

    def set_websocket_manager(self, manager):
        """设置 FastAPI 的 WebSocket 管理器"""
        self.websocket_manager = manager

    def set_console_sink(self, sink):
        """接管「控制台保底输出」。

        Web 端不设置（保持内置 print 便于排查）；dspro CLI 注册自己的 sink，
        把工具调用/思考过程渲染成终端进度行，避免 [Monitor:xxx] 原始输出混进对话。
        签名：sink(event_type: str, message: str, data: dict) -> None
        """
        self._console_sink = sink

    # ------------------------------------------------------------------
    # 当前请求 thread_id 的跨线程载体
    # 说明：ContextVar 在线程间不共享。agent.invoke 通过 asyncio.to_thread
    # 在工作线程执行，工作线程里设置的 ContextVar 主事件循环线程读不到，
    # 导致 monitor 推送时 get_thread_context() 为 None、推不出 WS。
    # 因此用一个普通实例变量承载「当前请求 thread_id」，由主协程在把任务
    # 丢进 to_thread 之前设置、结束后清除；工作线程里的 report_tool 读同一
    # 单例属性即可定向推送。本项目定位「本地纯个人使用」，单用户/单任务
    # 串行，此简化安全；若日后多用户并发需改为 task-local 载体。
    # ------------------------------------------------------------------
    def set_current_thread(self, thread_id: Optional[str]):
        self._current_thread_id = thread_id

    def clear_current_thread(self):
        self._current_thread_id = None

    def _current_tid(self) -> Optional[str]:
        return self._current_thread_id or get_thread_context()

    def _emit(self, event_type: str, message: str, data: Optional[Dict[str, Any]] = None):
        """内部发送方法"""
        payload = {
            "type": "monitor_event",
            "event": event_type,
            "message": message,
            "data": data or {},
            "timestamp": datetime.datetime.now().isoformat()
        }

        # 1. 优先尝试通过 FastAPI WebSocket 发送 (定向推送)
        if self.websocket_manager:
            try:
                # 获取当前线程 ID（跨线程场景下由主协程通过 set_current_thread 注入）
                thread_id = self._current_tid()

                # 确保 loop 已加载
                manager_loop = self.websocket_manager.loop

                if manager_loop:
                    if thread_id:
                        # 检查当前是否在同一个事件循环中
                        try:
                            current_loop = asyncio.get_running_loop()
                        except RuntimeError:
                            current_loop = None

                        if current_loop and current_loop == manager_loop:
                            # 如果在同一个循环中（例如在 create_task 中运行），直接创建任务
                            current_loop.create_task(
                                self.websocket_manager.send_to_thread(payload, thread_id)
                            )
                        else:
                            #  FastAPI 的 WebSocket 依赖异步事件循环，且协程必须在创建它的循环中运行：
                            #  如果当前线程和 WebSocket 管理器在同一个循环（比如在 FastAPI 的接口 / 任务中运行）：直接 create_task 效率最高；
                            #  如果在不同循环 / 不同线程（比如同步线程调用）：必须用 asyncio.run_coroutine_threadsafe（线程安全的方式），否则会报错 “协程在错误的循环中运行”。
                            # 如果在不同线程，使用 threadsafe 方法
                            asyncio.run_coroutine_threadsafe(
                                self.websocket_manager.send_to_thread(payload, thread_id),
                                manager_loop
                            )
                    else:
                        # 如果没有 thread_id，说明可能是系统级消息，或者未上下文环境
                        pass
            except Exception as e:
                print(f"[Monitor] WebSocket send failed: {e}")

        # 2. 尝试通过全局 runtime 输出 (DeepAgents 脚本模式)
        # 这使得 simple_agents.py 中的 MockRuntime 能接收到数据
        if builtins and hasattr(builtins, 'runtime') and hasattr(builtins.runtime, 'stream_writer'):
            try:
                builtins.runtime.stream_writer(payload)
            except Exception:
                pass

        # 3. 控制台保底输出 (方便调试)
        # 加上特殊前缀，方便肉眼识别；CLI 等前端注册 sink 后由它接管
        if event_type == "delta":
            # 聊天增量块很密、总量大 ✗ 控制台/CLI 不逐块打（正文由最终回答给出 ✓）
            return
        if self._console_sink is not None:
            try:
                self._console_sink(event_type, message, payload.get("data") or {})
            except Exception:
                pass
            return
        print(f"\n[Monitor:{event_type}] {message}")

    def report_tool(self, tool_name: str, args: Dict[str, Any] = None):
        """报告工具开始执行"""
        self._emit("tool_start", f"开始执行工具: {tool_name}", {"tool_name": tool_name, "args": args})

    def report_thinking(self, text: str):
        """报告模型思考过程片段（reasoning_content 旁路捕获，逐段推送）"""
        self._emit("thinking", "模型思考中…", {"text": text})

    def report_delta(self, kind: str, text: str, seq: int = 0):
        """聊天增量（v3.1 流式输出）：正文/思考的逐段推送。

        只在**前台问答**的流式路径上出现（`api/server.py::_invoke_or_stream` + `agent/stream_sink.py`）；
        `kind` ∈ `answer`（正文）/ `reasoning`（思考）✓ 前端按 kind 分别追加到气泡与思考区 ✓
        `seq` 是同一轮内的单调序号，前端可用它发现丢块/乱序 ✓
        """
        self._emit("delta", "", {"kind": kind, "text": text, "seq": seq})

    def report_assistant(self, assistant_name: str, args: Dict[str, Any] = None):
        """报告正在调用的子智能体进度"""
        self._emit("assistant_call", f"正在调用助手: {assistant_name}",
                   {"assistant_name": assistant_name, "args": args})

    def report_task_result(self, result: str):
        """报告任务最终结果"""
        self._emit("task_result", "任务执行完成", {"result": result})

    def report_session_dir(self, path: str):
        """报告任务工作目录"""
        self._emit("session_created", f"工作目录已创建: {path}", {"path": path})


# 全局单例实例
monitor = ToolMonitor()


class ConnectionManager:
    def __init__(self):
        self.active_connections: Dict[str, WebSocket] = {}
        # 延迟绑定 loop，防止初始化时 loop 不一致
        self.loop = None

    def set_loop(self, loop):
        """显式设置事件循环"""
        self.loop = loop
        monitor.set_websocket_manager(self)
        print(f"[Monitor] ConnectionManager manually bound to loop: {id(self.loop)}")

    async def connect(self, websocket: WebSocket, thread_id: str):
        await websocket.accept()
        self.active_connections[thread_id] = websocket
        print(f"Client connected: {thread_id}")

    def disconnect(self, websocket: WebSocket, thread_id: str):
        if thread_id in self.active_connections:
            del self.active_connections[thread_id]
        print(f"Client disconnected: {thread_id}")

    async def send_personal_message(self, message: str, websocket: WebSocket):
        await websocket.send_text(message)

    async def send_to_thread(self, message: dict, thread_id: str):
        if thread_id in self.active_connections:
            websocket = self.active_connections[thread_id]
            await websocket.send_json(message)


manager = ConnectionManager()