# M2 Web 服务与鉴权实现设计 & 验证记录（智选情报官）

> 里程碑：M2（基于 `docs/里程碑计划.md` 与 `项目预期功能和预期UI设计.md`）
> 目标：把 CLI 版能力搬上 Web —— FastAPI + WebSocket 实时进度推送 + 登录鉴权 + 账号角色分流（公司/个人双工作台）。
> 状态：✅ 已完成并实测通过（2026-08）

---

## 一、本期改动清单

| 文件 | 改动 | 说明 |
| --- | --- | --- |
| `api/server.py` | **新增** | FastAPI 应用：REST 登录/注册/登出、REST 问答（`/api/chat`）、WebSocket 进度（`/ws/{thread_id}`）、静态 SPA 托管（`/`）。startup 绑定 `monitor` 的 loop。 |
| `api/account.py` | **新增** | 账号服务：复用个人库 SQLite 的 `accounts` 表（`CREATE TABLE IF NOT EXISTS`，不破坏样例）；注册/登录/会话校验；token 用 `secrets.token_hex` 存进程内字典。 |
| `front/index.html` | **新增** | 前端 SPA（Vue 3 全球构建版，免打包）：登录页 + 公司/个人双工作台，右栏 WebSocket 实时滚动工具调用进度，来源标注 🔍/🗄️/📚 高亮，轻量 Markdown/表格渲染。 |
| `front/vendor/vue.global.prod.js` | **新增** | 本地 Vue 3.5.13 全局构建（157KB），由 FastAPI `/static` 托管，前端免 CDN 直连。 |
| `pyproject.toml` | 依赖已在列 | fastapi / uvicorn 已声明；通过 `uv sync` 正式装进项目 `.venv`。 |
| `README.md` | 同步更新 | 修正「项目现状」为「M2 进行中/已完成」；结构图补 `api/server.py` / `api/account.py` / `front/`；保留原阅读指南。 |
| `docs/里程碑计划.md` | 同步更新 | M2 标记完成。 |

> 未动：公司侧/个人侧 Agent 与工具（`main.py` 的 CLI 交互版仍保留可用）；MCP、用户画像按拍板暂缓。

---

## 二、架构与关键设计

### 2.1 服务结构
```
浏览器(Vue SPA)
   │  REST: /api/login /api/register /api/chat
   │  WS:   /ws/{thread_id}
   ▼
api/server.py (FastAPI)
   ├─ 登录校验 → api/account.py（accounts 表, SQLite 个人库）
   ├─ /api/chat → asyncio.to_thread(_run_agent)  ← 同步 agent.invoke 必须 offload
   │                └─ _run_agent 内 set_thread_context(thread_id)
   │                     └─ 工具调用 → monitor.report_tool(...)
   │                          └─ monitor._emit → run_coroutine_threadsafe(loop)
   │                               └─ ConnectionManager.send_to_thread(payload, thread_id)
   │                                    └─ ws.send_json  ──► 浏览器右栏实时进度
   └─ /ws/{thread_id} → manager.connect → 按 thread_id 定向推送
```

### 2.2 三个必须注意的点
1. **同步 Agent 必须 `asyncio.to_thread`**：`deepagents` 的 `agent.invoke` 是阻塞同步调用，若直接在 async 路由里跑会卡死整个事件循环，WebSocket 也推不出去。故 `_run_agent` 用 `asyncio.to_thread` 放到线程池。
2. **monitor loop 绑定**：`api/monitor.py` 的 `_emit` 在工作线程里用 `asyncio.run_coroutine_threadsafe(payload, manager_loop)` 把事件送回主循环。这就要求 `server.py` 在 `@app.on_event("startup")` 里 `manager.set_loop(asyncio.get_event_loop())` —— 否则 `manager_loop` 为 None，事件只走控制台保底、不进 WS。已验证绑定成功（启动日志 `[Monitor] ConnectionManager manually bound to loop: ...`）。
3. **thread_id 隔离**：`/api/chat` 用 `username` 作 thread_id（多轮记忆按用户隔离）；WebSocket 用同一 `username` 连接，monitor 按 thread_id 精确推到该连接。

### 2.3 鉴权说明（本地个人应用定位）
- 账号存个人库 SQLite 的 `accounts` 表（与 `schema_personal.py` 同库，`IF NOT EXISTS` 不破坏 M1.5 样例）。
- 密码用 `sha256(SALT + pwd)` 哈希（本地单文件应用，非生产级安全，够用）。
- token = `secrets.token_hex(16)`，存**进程内字典**；重启即失效，符合「纯个人本地」定位（如需持久会话后续可换 JWT/SQLite 持久化）。

---

## 三、踩坑记录（关键）

### Bug 1：导入 `agent.llm` 报 ModuleNotFoundError 或命中错误包
- **现象**：`uvicorn api.server:app` 启动报 `No module named 'agent.llm'`；或更隐蔽地 `import agent` 成功但 `import agent.llm` 失败。
- **定位**：本机 **Hermes 全局 venv**（`C:\Users\ZQK\AppData\Local\hermes\hermes-agent\`）里存在一个**同名为 `agent` 的顶层包**，项目 `.venv` 的 `sys.path` 把它排在前面，导致 `import agent` 命中 Hermes 的 `agent` 包而非项目目录的 `agent/`。这正是 `db_tools.py` / `tavily_tool.py` 一开头都要 `sys.path.insert(0, 项目根)` 的原因，`server.py` 漏了。
- **修复**：`server.py` 顶部（任何项目内包导入前）做两件事：① `sys.path.insert(0, 项目根)`；② `sys.path = [p for p in sys.path if "hermes-agent" not in p]` 剔除 Hermes venv 的 site-packages。修复后 `agent -> None`（命名空间包，指向项目根）、`agent.llm` 正常加载。
- **影响面**：所有新增的 `api/*.py` 顶层入口若用 `uvicorn` 启动，都需这层防御；或统一在项目 `.venv` 内运行 uvicorn（`.venv` 已 `uv sync` 装好 fastapi/uvicorn）。

### Bug 2：本地 127.0.0.1 被 HTTP_PROXY 拦截
- **现象**：测试脚本 `urllib` / `websockets` 连 `127.0.0.1:8123` 失败（URLError），但 `curl --noproxy '*'` 能连。
- **原因**：本机 `HTTP_PROXY=http://127.0.0.1:7897`，urllib/websockets 默认代理 localhost。
- **修复**：测试脚本开头清掉 `HTTP_PROXY/HTTPS_PROXY` 并设 `NO_PROXY=*`。**仅测试脚本需要**；浏览器/服务端本身不受影响（服务端监听本地，前端同源请求不经系统代理）。

### 现象（非 bug，记录）：子 Agent 重复工具调用
- 实测 WS 收到 **2 次 `list_sql_tables` + 3 次 `get_table_data`** 才完成「列出有哪些表」这类简单问题。deepagents 框架可能在同一轮内重复触发子 Agent 工具，或因无幂等缓存而重查。不影响正确性，但浪费（尤其 Tavily 网搜时）。**留作 M2.x 优化项**（如给工具加结果缓存 / 限制单轮调用），不阻塞本期。

---

## 四、实测验证

### 4.1 环境
- 模型：`agnes-2.5-flash`（OpenAI 兼容，`LLM_MODEL_MAX`）；Tavily 已配置；本地 MySQL `deep_search_pro` 可用；项目 `.venv` 已 `uv sync`（fastapi/uvicorn/deepagents/langchain 齐全）。
- 启动：`uvicorn api.server:app --host 127.0.0.1 --port 8123`，日志 `Application startup complete` + `monitor loop 已绑定`。

### 4.2 验证 1：WebSocket 实时推送（轻量问题）
- 前端连 `ws://127.0.0.1:8123/ws/m2tester` → 服务端 `Client connected: m2tester`。
- 发「列出数据库有哪些表」→ `/api/chat` 返回 200。
- **WS 客户端实时收到 5 条 `monitor_event`（tool_start）**：`list_sql_tables`×2 + `get_table_data`×3，与工具真实调用一一对应。
- ✅ 证明 `monitor.report_tool → run_coroutine_threadsafe → ConnectionManager.send_to_thread → ws.send_json` 链路真实工作。

### 4.3 验证 2：真实竞品问答（重问题）
- 发「飞书主要对标我们公司的哪些产品？」→ `/api/chat` 返回 200。
- 回答含完整对标矩阵（云协IM/文档/会议/OA 对标飞书对应产品）、定价对比、来源标注 🔍网络。
- 服务端日志大量 `[Monitor:tool_start]`（DB 查询 + 网络搜索），证明多源融合与埋点均生效。
- ✅ 主 Agent 调度 + 子 Agent + 模型 + Tavily + MySQL 在 Web 服务里全链路跑通。

### 4.4 验证 3：登录鉴权
- 未注册账号 `POST /api/login` 返回 401 → `POST /api/register` 注册（role=company）→ 再次登录返回 200 + token + `{role: company}`。
- ✅ 账号角色分流基础可用。

---

## 五、下一步（M3 建议）

- **M3**：前端体验增强 —— 公司中栏简报卡片（结构化渲染①摘要②动态③对比表④风险机会⑤来源）、左栏竞品清单、底栏导出 MD/PDF（`utils/word_converter.py` 已在）；个人快讯流 + 性价比分析卡片。
- **M2.x（可选优化）**：① 工具结果缓存 / 单轮调用上限，抑制子 Agent 重复调用（见第三节现象）；② token 持久化（SQLite）避免重启失效；③ 前端 Markdown 渲染增强（代码块/列表）。
- **M4（二期）**：RAGFlow 接入 + 定时推送 + 用户画像（用户新方案确定后排期）。

> 注：当前 `main.py` CLI 交互版仍保留可用；Web 版入口为 `api/server.py`，启动见第四节。git 提交由用户自行规划（不纳入本 Agent 任务）。
