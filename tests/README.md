# tests/ — 测试与调试脚本

> 约定：**测试 / 调试脚本统一放本目录**，不放项目根目录。项目根只保留 `main.py` 入口。

| 脚本 | 用途 | 前置条件 |
| --- | --- | --- |
| `m2_debug_run.py` | 进程内直接调 `_run_agent`，打印真实异常堆栈（绕过 FastAPI 包装），用于排查 Agent 执行错误 | 无需服务运行；依赖已装即可。`python tests/m2_debug_run.py` |
| `m2_ws_probe.py` | WS 链路隔离验证：连 `/ws/dbg` → 调 `/api/_test_emit?thread_id=dbg` → 验证 WS 收到 monitor 事件 | 服务已启动：`uvicorn api.server:app --port 8123` |

## 注意事项

- 本机 `HTTP_PROXY=127.0.0.1:7897`：访问 localhost 的脚本须先清代理环境变量（两个脚本均已内置豁免）。
- 若本机存在其他名为 `agent` 的顶层包（如全局 venv 同名包）：脚本需把项目根 `insert(0)` 到 `sys.path` 并剔除冲突路径（`m2_debug_run.py` 已内置）。
- `/api/_test_emit` 是 `api/server.py` 中的临时调试端点（M2 验证 WS 用），正式使用阶段可移除。
