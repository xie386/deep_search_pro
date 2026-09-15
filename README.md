# deep-search-pro

> 一个面向初学者的 **多智能体（Multi-Agent）深度搜索** 框架项目，基于 `deepagents` + `LangChain` + `FastAPI` 构建，已演进为本地纯个人使用的 AI 情报助手「**智选情报官**」。
> 当前状态：**v1.0 已定版 · v2.0 已收官**（M1~M5b 全部完成，仅 **MCP / API 适配器**按拍板推迟到 v3.0）——**M1** 上下文工程化（多会话管理 + token 预算防爆窗）；**M2** 请求装配管线化（agent 静态化、人格/记忆 invoke 期注入零重建）；**M3** RAG 知识库（自建 Chroma、评估清洗入库、快/全双模式检索）；**M4a** 技能（SKILL.md + `/` 选单 + 前缀注入）；**M4b** CLI 沙箱面板（白名单命令 + 六道闸 + 只读口径）；**M4c** 自定义 CLI 接入（配置导向：自己填命令与只读清单 + 本机检测 + 只读子命令代跑）；**M4 收尾** CLI 版 `dspro` / 工作台时钟+天气卡 / 教程只读阅读页；**M5a** 工具能力语义路由（能力描述 + 能力路由卡 + few-shot 示例）；**M5b** 工具检索路由（统一能力目录 + 按提问相关性选工具）；**收尾** 登录卡 / 注册卡分离。v1.0 功能（多智能体、思考可视化、定制助手、记忆画像、语音朗读、周报时效修复）全部实测通过。**v2.0 全部更新一览（每个里程碑改了什么、关键设计、实测数字）见 [八、项目现状 & 改造方向](#八项目现状--改造方向) 开头的总览表**；设计文档见 `docs/v2.0/`，v1.0 里程碑回顾见 `docs/v1.0/`。
>
> **本轮新增（2026-09-16）**：v2.0 **M5b 工具检索路由（CLI 部分）**已实现并验证——**M5b-1 统一能力目录**（`CapabilityEntry` 契约 + `tool_capabilities`/`tool_pool_meta` 两表 + CLI/API/MCP 三来源适配器 + 独立向量 collection）+ **M5b-2 路由器**（`tools/tool_router.py`：快路径 / 混合检索 / 相对置信判定 / 三形态分层注入 / 全链兜底）。单测 62/62 + 34/34，回归 M4c 109/109、M5a 43/43；8 工具标定下命中判定 10/10、注入量 1644 字（全量 2499 字）。详见 `docs/v2.0/M5b工具检索路由.md`。
>
> **v2.0 收尾（2026-09-16）**：登录界面 **登录卡 / 注册卡分离**——旧版是「登录注册混合卡」（输入的用户名不存在即自动注册），**打错一个字就会静默建出错误账号**（实际误建过「尼姑喵喵」）。现在：注册独立成卡（用户名 + 密码 + **确认密码** + 账号类型 + 「即将创建：xxx（个人账号）」实时回显），**登录失败绝不建号**，账号不存在时给出「去注册 →」引导；后端登录语义随之拆细（**账号不存在 → 404「账号不存在」/ 密码错误 → 401「密码错误」**，CLI 侧同步指路 `--register`）。顺带修掉一个同批发现的层级 bug：全局提示条原挂在 `<template v-else>`（登录后才渲染的分支）里，**导致注册成功的提示在登录页根本看不见**。验证：`tests/auth_split_e2e.py` 36/36、真机 CDP `scripts/cdp_auth_probe.py` 21/21、CLI 回归 `cli_dspro_e2e.py` 38/38、前端不变量 3/3 + `m4a_frontend_logic.js` 56/56。

---

## 一、项目简介

`deep-search-pro` 的目标是让你用最少的概念，理解一个「能联网搜索、能查数据库、能查知识库」的智能体系统是怎么组织起来的。

它的核心理念是 **编排（Orchestration）**：

- 有一个 **主智能体（Main Agent）** 充当「团队负责人」，负责理解用户问题、拆解任务、并把子任务分派出去；
- 有若干个 **子智能体（Sub Agents）** 各司其职，例如：
  - 🔍 **网络搜索助手**（已装配）：调用 Tavily 搜索公开网络信息；
  - 🗄️ **数据库查询助手**（已装配）：读取当前登录公司自己的资料（SQLite，按账号隔离：公司信息 / 我司产品 / 关注竞品）；
  - 👤 **个人情报助手**（已装配，M1.5）：读取本地 SQLite 个人库，做消费选购的性价比分析与兴趣快讯；
  - 📚 **RAG 知识库**：自建 Chroma 向量库（每用户一库，导出报告弹窗确认入库 / 上传 .md + 评估清洗 + 快/全双模式检索 + 消息级防重复入库）。
- 每个子智能体都挂载自己的 **工具（Tools）**，由大模型按需调用；
- 整个过程通过 **监控埋点 + WebSocket** 把「当前在做什么」实时推送给前端，方便观察 Agent 的思考与执行链路；
- **思考过程可视化**：模型推理内容（reasoning_content）经 ReasoningChatOpenAI 透传后推给前端右栏「🤔 模型思考过程」折叠区，肉眼可见 Agent 在想什么；
- **可定制**：模型选型（自带免费模型或自填 OpenAI 兼容接口）+ 助手人格（SOUL.md 注入系统提示词，可 AI 生成人格一键导入）。

> 一句话总结：把「一个会自己调用工具的 AI」升级成「一个会指挥多个专家 AI 协同工作的 AI」。

---

## 二、功能特性

| 特性 | 说明 |
| --- | --- |
| 多智能体协作 | 主 Agent 调度多个专职子 Agent，分工完成复杂任务 |
| 可插拔工具 | 用 `@tool` 装饰器即可把任意函数变成 Agent 可调用的工具 |
| 网络搜索 | 通过 Tavily API 检索公开网络信息（已装配） |
| 数据库查询 | 通过 MySQL 工具直接读取企业内部表数据（已装配） |
| 个人库查询 | 通过本地 SQLite 工具读取个人收藏 / 兴趣 / 关注清单，支撑选购决策（已装配，M1.5） |
| 知识库检索 | 自建 Chroma 向量库（弃 RAGFlow）：每用户一库 + 导出报告弹窗确认入库 / 上传 .md + 评估清洗 + 快/全双模式检索 + 消息级防重复入库 |
| 工具能力路由（M5a/M5b） | 让 Agent 在**你说人话时想起对的工具**：`user_clis.abilities` 用用户语言写能力描述（关键词 + 「说法→命令路径」映射，需 ID 的命令写两步链）→ 注入「能力路由卡 + few-shot 映射示例」（M5a）；工具变多后按**提问相关性**选工具（bge 稠密 + 关键词词法 → RRF 融合 → 相对置信判定 → 完整卡/紧凑行/仅名称分层注入，**不确定就回退全量、工具永不隐身**）（M5b） |
| 可观测性 | `ToolMonitor` 单例在每次工具调用时上报进度，支持控制台 / WebSocket 双通道 |
| 思考过程可视化 | 自定义 `ReasoningChatOpenAI`（继承 ChatOpenAI）透传模型 `reasoning_content`，回答后推前端右栏折叠区（langchain 丢弃非标准字段，官方建议 provider 子类） |
| 背景图持久化 | `pic/{username}/` 目录存储 + 上传/列表/删除 API + 前端图库网格选择 + 透明度调节（按账号隔离，刷新不丢） |
| 定制助手·模型选型 | `llm_providers` 表（按账号）存模型名/base_url/api_key，前端可选启用；启用后该账号对话走自定义模型，默认 .env 免费模型 |
| 定制助手·助手人格 | `agents_docs/{username}/SOUL.md` 按账号存储人格，注入 system_prompt（对话+周报措辞均体现）；`soul_writer` 独立段 AI 生成人格（如「活泼女仆」「稳重管家」）一键导入 |
| 周报时效性保障 | 代码层按 `published_date` 严格剔除超窗旧闻（strict_days）+ 查询词泛用化（去修饰词）+ 中英双语检索合并（bilingual）+ 订阅级检索语言可选（zh/en/both） |
| 语音朗读与自定义音色 | 联动 `voice_test` 小项目（Qwen3-TTS 音色克隆）：定制页上传参考音频+台词克隆音色包（pkl，按账号隔离），AI 回复框「🔊 朗读」合成语音播放；模型仅在用户使用时临时启动（subprocess 跑脚本），用完即释放显存（RTX 3050 4GB 约束） |
| Web 服务与实时监控 | FastAPI + WebSocket：登录鉴权（**登录卡 / 注册卡分离**，登录失败绝不自动建号）、账号角色分流（公司/个人双工作台），右栏实时滚动工具调用进度（M2 已装配） |
| 用户数据自主管理 | `/api/me/*` 全套 CRUD：公司资料/我司产品/关注竞品、个人兴趣/关注/收藏，按账号隔离；Agent 工具只读消费（M4 前置已装配） |
| Digest 定期推送 | 订阅管理 + Tavily/HN 检索 + LLM 三关筛选生成周报 + APScheduler 定时调度 + WS 实时提醒 + 报告列表页下载（M4 已装配） |
| 异步隔离 | 基于 `ContextVar` 的请求级上下文隔离，避免多用户「串台」 |
| 配置外置 | 所有提示词（Prompt）集中在 `prompt/prompts.yml`，改提示词无需动代码 |

---

## 三、项目结构

```text
deep_search_pro/
├── dspro / dspro.cmd           # CLI 包装脚本（免激活 venv；PATH 加了项目根即可直接 dspro）
├── main.py                      # 程序入口（CLI 交互版：已装配主 Agent + 3 个子 Agent，含多轮记忆）
├── pyproject.toml              # 依赖清单（uv 管理，已配置清华镜像源）
├── .python-version             # 锁定 Python 3.11
├── .gitignore                  # 忽略 .env / __pycache__ / data 等
├── IDEA.md                     # 项目定位：初学框架，后续改造为成熟应用
│
├── cli/                        # ★ CLI 版（dspro）：命令行聊天 + 命令行工具，与 web 共用同一套引擎
│   ├── main.py                 #    argparse 入口（login/logout/whoami/list/digest/chat）
│   ├── session.py              #    登录态 data/dspro_session.json（密码指纹校验，不存明文）
│   ├── ui.py                   #    终端输出：ANSI 颜色 + 东亚宽度表格 + markdown 轻渲染（零依赖）
│   └── cmd_*.py                #    各子命令实现（auth / list / digest / chat）
├── agent/                      # ★ 智能体核心
│   ├── llm.py                  #   初始化大模型（从 .env 读取 LLM_MODEL_MAX）
│   ├── reasoning_model.py     #   ReasoningChatOpenAI：继承 ChatOpenAI，透传第三方 reasoning_content（A+ 方案）
│   ├── thinking_capture.py     #   思考捕获：invoke 后遍历消息提取 reasoning_content 推前端
│   ├── context_budget.py      #   M1 上下文预算：TokenCounter/Truncator/Compressor/Manager（AstrBot 借鉴）
│   ├── conversation_store.py  #   M1 会话持久化：SQLite 读写 conversations/messages（多会话管理）
│   ├── build_context.py       #   M1/M2 请求装配管线：build_request_context（人格/记忆 SystemMessage 注入 + 历史 + 预算）
│   ├── prompts.py              #   加载 prompt/prompts.yml 为字典，供主/子 Agent 使用
│   └── subagents/              #   子 Agent 装配（提示词 + 工具 → 字典）
│       ├── network_search_agent.py   # 网络搜索子 Agent 装配（范本）
│       ├── db_agent.py                # 数据库查询子 Agent 装配（挂载 MySQL 三件套）
│       ├── personal_agent.py          # 个人情报子 Agent 装配（挂载 SQLite 个人库工具）
│       └── digest_agent.py            # 定期情报周报子 Agent 装配（M4，筛选撰写周报）
├── rag_knowledge/              # ★ M3 RAG 知识库（与 agent/ 平级）
│   ├── kb_service.py           #   每用户 Chroma 向量库：embedding(GPU)/分块/入库/检索（快/全双模式）
│   ├── kb_store.py             #   入库记录（md5 去重，用户目录隔离）
│   ├── cleaner.py              #   入库前清洗（AI 报告 emoji/表格/口吻）
│   ├── evaluator.py            #   入库评估独立小 agent（脏/质量建议）
│   └── db/                     #   每用户向量库落盘（.gitignore）
│
├── prompt/
│   └── prompts.yml             # ★ 所有提示词配置：主 Agent + 各子 Agent 的 name/description/system_prompt
│
├── tools/                      # ★ 工具层（Agent 的「手脚」）
│   ├── tavily_tool.py          #   网络搜索工具（internet_search，支持 days/strict_days/bilingual）
│   ├── db_tools.py             #   MySQL 三件套：list_sql_tables / get_table_data / execute_sql_query
│   ├── personal_tools.py       #   个人库三件套：list_personal_tables / get_personal_data / query_personal_db
│   ├── readtofile.py           #   读 agents_docs 文档工具（read_agent_doc / list_agent_docs，限 .md/.txt）
│   ├── writetofile.py          #   写 agents_docs 文档工具（write_agent_doc，路径穿越/扩展名双重防护）
│   ├── kb_tools.py             #   知识库检索工具（query_kb：对话快模式 / 周报全模式，M3）
│   ├── shell_executor.py       #   沙箱命令工具（run_shell_command，白名单，M4b）
│   ├── cli_registry.py         #   自定义 CLI 配置（字段校验/本机检测/CRUD/只读闸/旧数据迁移，M4c）
│   ├── capability_pool.py      #   M5b 统一能力目录：CapabilityEntry 契约（CLI/API/MCP 三来源适配器 + 版本化向量惰性重建）
│   ├── tool_router.py          #   M5b 工具检索路由：快路径 / 混合检索 RRF / 相对置信判定 / 三形态分层注入
│   ├── _runtime/               #   能力层（CLI 面板与 Agent 共用同一执行器）
│   │   └── shell_runtime.py    #     白名单命令执行器：六道闸（白名单/路径校验/沙箱 cwd/超时/输出上限/审计）
│   └── schema_personal.py      #   个人库 SQLite Schema + 样例数据（幂等建库，含 llm_providers 表）
│
├── api/                        # 服务与可观测性
│   ├── monitor.py              #   ToolMonitor 单例 + ConnectionManager（WebSocket 进度推送）
│   ├── context.py              #   基于 ContextVar 的请求级上下文隔离（session_dir / thread_id）
│   ├── server.py               #   ★ FastAPI 应用入口：REST 登录/问答 + WebSocket + Digest 路由 + SPA 托管
│   ├── account.py              #   账号服务：accounts 表登录鉴权与角色分流
│   ├── me_user_data.py         #   用户数据自主管理 API（/api/me/*：资料/产品/竞品/兴趣/关注/收藏）
│   ├── customize.py            #   定制助手 API：背景图库 /api/bg/*、模型选型 /api/llm/*、人格 SOUL /api/soul/*、记忆 /api/memory/*
│   └── voice_tts.py            #   语音朗读 API：/api/voice/packs（克隆/列表/选中/删除）+ /api/tts/speak（合成），subprocess 联动 voice_test
│
├── agent/digest_engine.py      #   ★ Digest 引擎（M4）：create_deep_agent 编排主 Agent 调度子智能体生成周报
├── api/digest_scheduler.py     #   APScheduler 调度（daily@9:00 / weekly@Mon9:00）+ WS report_ready 推送（M4）
│
├── pic/                        # 用户上传的背景图片（按 {username}/ 子目录隔离，/pic 静态挂载）
├── agents_docs/                # 助手人格 SOUL.md（按 {username}/SOUL.md 隔离，注入 system_prompt）
│
├── static/                     # 前端 SPA（免构建 Vue 3）
│   ├── index.html              #   登录页 + 多页签工作台(工作台/信息/AI助手/报告/定制助手) + WebSocket 实时监控 + 思考过程折叠区 + 背景图库
│   └── vendor/vue.global.prod.js     # 本地 Vue 3 全局构建
│
├── tests/                      # 测试与调试脚本（不放项目根）
│   ├── m2_debug_run.py 等      #   各里程碑冒烟/回归脚本（详见 tests/README.md）
│
├── scripts/                    # 一次性运维脚本
│   └── import_mysql_to_sqlite.py     # 旧 MySQL 数据迁移到 SQLite
│   └── build_tutorial_preview.js  # 生成教程页静态预览（真实 CSS + 真实渲染结果，肉眼验收用）
│   └── build_weather_preview.js   # 生成时钟/天气卡静态预览（真实 CSS 三态，肉眼验收用）
│   └── build_wx_harness.py       # 生成 Vue 沙盒页（真 CSS + 真卡片标记），配无头 Chrome 做视觉验收
│
├── data/                       # 运行时生成的本地数据
│   └── personal.db             #   个人库 SQLite 文件（首次调用自动建库）
│
├── output/                     # 导出产物（按用户目录隔离）
│   └── {username}/             #   简报 PDF 与 Digest 周报 md
│
├── docs/                       # 设计文档与里程碑（按版本分目录）
│   ├── v1.0/                    #   v1.0 定版文档
│   │   ├── 里程碑计划.md             #     开发顺序准绳（M1✅ M1.5✅ M2✅ M3✅ M4✅）
│   │   ├── 项目预期功能和预期UI设计.md #     产品定位 / 功能清单 / UI 设计 / 里程碑
│   │   ├── M1_公司侧实现设计.md ~ M4_Digest实现记录.md  # 各里程碑实现与实测记录
│   │   └── READMEv1.0.md            #     v1.0 定版说明
│   ├── v2.0/                    #   v2.0 设计与验证（进行中）
│   │   ├── M1上下文工程化.md / M2请求装配管线化.md / M3RAG知识库搭建与使用.md
│   │   ├── M4技能与工具.md           #     技能 / CLI 面板 / 第三方 CLI（M4a·M4b·M4c 已完成）
│   │   ├── 演示文档/演示文档.md      #     CLI 工具接入教程（教程指导按钮打开的只读文档，含图片）
│   │   └── v2.0方案.md · AstrBot_v2.0对比借鉴.md · Agent命令行能力调研.md
│   └── sql/company_schema.sql   #   公司侧 MySQL 三表建库 + 样例数据（legacy，数据已迁 SQLite）
│
└── utils/                      # 通用工具
    ├── path_utils.py           #   统一的文件路径解析（含虚拟路径清洗 / 会话隔离 / 防嵌套）
    └── word_converter.py       #   Markdown → PDF 转换（依赖 Word COM，仅 Windows）
```

---

## 四、阅读指南（Web）（建议顺序）

> 智选情报官 **Web 版**（FastAPI + Vue3 免构建前端）的代码阅读路径。按「配置 → 模型 → 工具 → 装配 → 可观测性」的难度递进组织，每读一个文件都先问自己一个问题。

> **只关心命令行版？** 跳到 [五、阅读指南（CLI）（建议顺序）](#五阅读指南cli建议顺序)。

| 顺序 | 文件 | 看什么 / 思考题 |
| --- | --- | --- |
| 1️⃣ | `prompt/prompts.yml` | **先读「大脑」**。主 Agent 和子 Agent 的 `system_prompt` 写在这里。理解：一个 Agent 的「人设」和「能力边界」是怎么用 YAML 描述出来的？为什么要把提示词放在配置里而不是写死在代码里？ |
| 2️⃣ | `agent/prompts.py` | 配置是怎么被代码读进来的？注意 `yaml.safe_load` 比 `load` 安全（防执行注入）。输出 `main_agent_content` / `sub_agents_content` 两个字典。 |
| 3️⃣ | `agent/llm.py` + `agent/reasoning_model.py` | 大模型是怎么初始化的？现在用 `ReasoningChatOpenAI`（继承 ChatOpenAI，见 1️⃣9️⃣）实例化，不再用 `init_chat_model`——唯一区别是保留第三方模型 `reasoning_content`。`LLM_MODEL_MAX`/`OPENAI_BASE_URL` 在 `.env` 里配置。 |
| 4️⃣ | `tools/tavily_tool.py` | **工具的本质**：一个普通函数 + `@tool` 装饰器 + 清晰的 docstring（docstring 就是给模型看的「说明书」）。注意开头的 `monitor.report_tool(...)` 埋点。 |
| 5️⃣ | `tools/db_tools.py` | 三个数据库工具：`list_sql_tables`（看有哪些表）→ `get_table_data`（预览表结构）→ `execute_sql_query`（自定义 SQL）。体会「工具之间有序协作」的设计。 |
| 6️⃣ | `agent/subagents/network_search_agent.py` | **子 Agent 的装配**：把「提示词 + 工具」打包成一个字典。这就是第 5️⃣ 步工具和第 2️⃣ 步配置的结合点。 |
| 7️⃣ | `api/monitor.py` | **可观测性**：`ToolMonitor` 单例如何把「工具开始/进行中/结束」推给前端？理解 `WebSocketManager` 与「脚本运行时 stream_writer」两种输出通道。 |
| 8️⃣ | `api/context.py` | **异步难点**：为什么不能用全局变量 / `threading.local`？`ContextVar` 如何在同一个线程的多个协程之间隔离数据（避免 A 用户看到 B 用户的结果）？ |
| 9️⃣ | `utils/` | `path_utils.py` 处理多用户文件隔离；`word_converter.py` 是结果导出的辅助能力。 |
| 🔟 | `main.py` | 入口。当前为 **CLI 交互版**（`while True + input` + `MemorySaver` 多轮记忆），已把主 Agent 与 3 个子 Agent 串起来跑通；Web 版入口见 `api/server.py`。 |
| 1️⃣1️⃣ | `agent/subagents/db_agent.py` | **数据库子 Agent 的装配**：与 `network_search_agent.py` 同构，把「提示词 + MySQL 三件套工具」打包成字典。对照阅读，体会「一套装配模式复用到不同专家」。 |
| 1️⃣2️⃣ | `tools/personal_tools.py` + `tools/schema_personal.py` | **个人侧工具与本地库**（M1.5）：操作 SQLite 个人库，`attributes` 用 JSON 列承载异质规格（耳机 vs 显卡字段完全不同却共用一表）。这是「泛用化 + JSON 扩展」设计的落点。 |
| 1️⃣3️⃣ | `api/server.py` + `api/account.py` | **Web 服务层**（M2/M3）：① 同步的 `agent.invoke` 为什么必须用 `asyncio.to_thread` 放到线程池？（否则卡死事件循环，WebSocket 推不出去）② startup 时 `manager.set_loop()` 绑定事件循环后，monitor 埋点如何经 `run_coroutine_threadsafe` 从工作线程送回主循环、再按 thread_id 定向推到 WebSocket？③ 账号如何用个人库 `accounts` 表做角色分流？④（M3）`/api/competitors` 为什么不走 agent 直查 MySQL？`/api/export`→`/api/download` 链路如何做「用户目录隔离 + 防目录穿越」校验？ |
| 1️⃣4️⃣ | `static/index.html` | **前端 SPA**（M2/M3，免构建 Vue 3）：登录页按 role 分流到公司/个人双工作台；WebSocket 收到 `monitor_event` 渲染右栏进度；来源标记 🔍/🗄️/📚 高亮。登录区是**登录 / 注册两张卡**（2026-09-16 收尾拆分）——旧版是混合卡「输入的用户名不存在即自动注册」，打错一个字就会静默建出错误账号，现在改为注册卡独立（含确认密码 + 「即将创建：xxx」实时回显），且**登录失败绝不建号**、账号不存在时提示「去注册 →」引导。思考：为什么「登录失败自动注册」看着贴心却危险？（用户打错用户名时得到的是一个**能登进去的空账号**，等到发现数据都不见了才察觉，错误被延迟放大）；为什么「即将创建」这行回显比弹窗二次确认更合适？（不打断流程，但把不可逆操作的后果提前摆到眼前）。组件美化来自 uiverse 库（003 气泡按钮 / 004 硬阴影输入框 / 001 3D 方块加载器），体会「snippet 重配色适配主题」的方法。后续增强：浅绿/白/黄三色系重配色、AI 回复框深底亮字对比、右栏「🤔 模型思考过程」折叠区、顶栏「🎨 背景」图库面板。 |
| 1️⃣5️⃣ | `tests/` | **测试与调试脚本**：与项目代码分离存放。`m3_smoketest.py` 是接口层回归（登录→竞品清单→导出下载）；`auth_split_e2e.py` 是登录/注册拆分的回归（**核心断言：用打错的用户名登录不会建号**——查库计数不变）；前端交互的真机验证在 `scripts/cdp_auth_probe.py`（无头 Chrome + CDP，真实点击并断言「账号是否真的建出来」）。注意脚本须基于 `__file__` 解析项目根注入 sys.path、并豁免本机 HTTP_PROXY 才能访问 localhost。 |
| 1️⃣6️⃣ | `api/me_user_data.py` | **用户数据自主管理**（M4 前置）：`/api/me/*` 全套 CRUD，全部按 session 的 account_id 隔离。思考：为什么「增删改走 REST、Agent 工具只读」的读写分离能同时保住数据安全与 Agent 可靠性？ |
| 1️⃣7️⃣ | `agent/digest_engine.py` + `agent/subagents/digest_agent.py` + `api/digest_scheduler.py` | **Digest 定期推送**（M4，回归主智能体编排）：① 为什么 digest 不自己写检索代码、而是让主 Agent 调度【网络搜索助手】？② 查询批次为何硬上限 ≤8？③ 候选不足 15 条时筛选门槛为何放宽（禁止空周报）？④ APScheduler 为何内嵌 FastAPI 进程而非外部 cron？⑤（时效性修复）`build_batches` 为何弃用「关键词+修饰词」拼接、改用关键词原文？——实测 Tavily 对带修饰词的中文长查询相关性崩坏（返回无关金融/加密货币新闻），且修饰词无法跨领域通用。 |
| 1️⃣8️⃣ | `docs/v1.0/` + `docs/v2.0/` | **设计文档与里程碑**：`docs/v1.0/` 是定版档案（`里程碑计划.md` 为开发顺序准绳 + `M1_*~M4_*` 实现与实测记录）；`docs/v2.0/` 是当前迭代（`M1上下文工程化.md`/`M2请求装配管线化.md`/`M3RAG知识库搭建与使用.md`/`M4技能与工具.md`/`M5工具智能路由.md`/`M5b工具检索路由.md`/`CLI命令行工具dspro.md`）。开新功能前先对齐里程碑，做完一个里程碑就补对应文档。 |
| 1️⃣9️⃣ | `agent/reasoning_model.py` + `agent/thinking_capture.py` + `api/monitor.py` | **思考过程可视化（A+ 方案）**：langchain-openai 明确丢弃第三方 base_url 的非标准字段 `reasoning_content`（源码 `_convert_delta_to_message_chunk` 只读 content/function_call/tool_calls，文档明言「Use a provider-specific subclass」）。方案：自定义 `ReasoningChatOpenAI(BaseChatOpenAI)` 重写 `_create_chat_result`（非流式）把 `reasoning_content` 塞进 `additional_kwargs`；`thinking_capture.py` 在 `agent.invoke` 完成后遍历 `result["messages"]` 提取各轮 AIMessage 的思考推前端。思考：为什么不用 `on_chat_model_end` 回调做实时？（deepagents 底层 `langchain.agents.create_agent` 的模型节点 `_execute_model_sync` 只 `model_.invoke(messages)` 不传 config，回调不触发；`model.with_config(callbacks=...)` 返回 RunnableBinding 会被 resolve_model 当字符串报错）。为什么这样比旧「双请求旁路」好？（单请求省 token + 覆盖所有轮次，旁路只覆盖首轮）。 |
| 2️⃣0️⃣ | `api/customize.py` + `tools/schema_personal.py`(llm_providers 表) | **定制助手后端**：五组 API 按账号隔离——① 背景图 `/api/bg/*`（multipart 上传存 `pic/{username}/`、列表、删除，uuid 前缀防重名）；② 模型选型 `/api/llm/providers`（CRUD + activate，**api_key 列表时掩码 `****后4位`，编辑留空=不改**）；③ 人格 `/api/soul`（默认 `SOUL.md` + 多人格集 `SOUL/{name}.md`，`ACTIVE_SOUL` 文件记录当前激活，list/create/delete/set_active/named 路由）；④ 记忆画像 `/api/memory`（读写 `MEMORY.md` + 一次性初始化 `/api/memory/init`）；⑤ 批量导入 `/api/me/*/batch`（竞品/收藏剪贴板粘贴，一行一个，去空去重 ≤50 条）。思考：`_get_agent_for()` 为什么「有人格/自定义模型/记忆画像就按账号缓存独立 agent」而不是复用全局 AGENT？（全局 AGENT 的 system_prompt 在构建时固定，无法注入动态内容；缓存 key 含 soul+memory 哈希，人格/记忆变化自动重建） |
| 2️⃣1️⃣ | `agent/digest_engine.py`(任务指令) + `tools/tavily_tool.py`(strict_days/bilingual) | **周报时效性修复**：① `strict_days=True` 在代码层解析每条 `published_date`（RFC822 格式）剔除超窗旧闻——为什么不能只靠模型自觉？（Tavily 的 days 只是硬上限不保证排序时效，旧闻常排前面）② `bilingual=True` 用 LLM 把中文关键词翻译成英文做中英双语检索合并——为什么单中文查询会漏？（实测 Tavily 对中文实体词相关性差，英文召回更精准）③ 订阅级 `lang` 字段（zh/en/both）控制是否双语。思考：无日期的结果为什么「宁可保留不误杀」？ |
| 2️⃣2️⃣ | `tools/readtofile.py` + `tools/writetofile.py` | **agents_docs 文档读写工具**：主智能体可读/写 `agents_docs/` 下的 .md/.txt（路径穿越防护：归一化后强制前缀校验；扩展名白名单）。端到端验证：`tests/tool_agent_e2e_test.py` 把工具挂到全局 AGENT 后由 Agent 实际完成写文件+读回任务。思考：为什么这类「越权敏感」工具要同时在路径与扩展名两层设防？ |
| 2️⃣3️⃣ | `prompt/prompts.yml`(soul_writer + memory_initializer 段) | **人格/画像撰写 AI**：顶层独立段（不进 main_agent/sub_agents，不挂工具）——`soul_writer` 按用户一句话需求（如「活泼女仆」「稳重管家」）生成 200-500 字人格提示词（称呼/说话风格/情绪/周报风格/行为准则五维）；`memory_initializer` 按用户系统已有资料（兴趣/收藏/竞品/公司信息）提炼 150-400 字初始画像（缺的维度如实标注「暂无」）。思考：为什么这两个 AI 必须独立于主 Agent 体系？ |
| 2️⃣4️⃣ | `agents_docs/{user}/MEMORY.md` + `agent/digest_engine.py`(画像注入) | **记忆白盒化（用户画像/公司形象）**：① 账号注册即生成空 MEMORY.md（`<!-- memory_init: pending -->` 标记）；② 主 Agent 的 system_prompt 注入【你的用户记忆画像】区块 + 强制记忆维护指令——用户表达稳定偏好时必须调用【写入文档工具】更新 MEMORY.md（禁止只口头确认）；③ `_get_agent_for` 缓存 key 含 memory 哈希，画像变化自动重建 agent；④ chat 路由对比调用前后 MEMORY.md 内容，变化则回复末尾附「📝 已更新你的记忆画像」提示；⑤ digest 引擎读 USER PROFILE 段注入「情报侧重」（价格敏感→降价/优惠类优先）；⑥ 前端定制页可视化编辑 + 一次性初始化按钮。**踩坑**：deepagents 内置 filesystem 工具（read_file/write_file）与自定义文档工具重名竞争，模型优先选内置导致绕过 agents_docs 约束——用 `_ToolExclusionMiddleware(excluded=...)` 排除内置工具。思考：为什么「排除内置工具」比「改名自定义工具」更优？（保留 agents_docs 安全边界） |
| 2️⃣5️⃣ | `api/voice_tts.py` + `voice_test`(外部小项目) | **语音朗读与自定义音色**：① 为什么不能把 voice_test 的代码复制进主项目？（环境冲突：voice_test=py3.12+torch+qwen_tts，主项目=py3.11+langchain，numpy .pyd 不兼容）→ 用 **subprocess 执行 voice_test 的 venv python 跑脚本**，环境天然隔离；② pkl 按账号隔离：`--name {username}_{包名}` 前缀，voice_test 脚本零改动；③ 显存约束（RTX 3050 4GB）：模型**只在用户使用时临时启动**（subprocess 进程退出即释放显存），不做常驻服务；④ 每次合成要重新加载模型（30-60s），常驻化方案见 `docs/v2.0/v2.0方案.md`；⑤ **踩坑**：subprocess 必须清 PYTHONPATH（Hermes 注入的 hermes-agent venv 会让 voice_test 的 numpy 加载崩）；克隆用后即删参考音频（只留 pkl 资产）。思考：为什么「名字前缀隔离」比「子目录隔离」更省事？（voice_test 脚本读固定 PACK_DIR，不支持子目录） |
| 2️⃣6️⃣ | `agent/context_budget.py` + `agent/conversation_store.py` + `agent/build_context.py` + `api/server.py`(会话路由) | **M1 上下文工程化（v2.0）**：① 为什么 v1.0 的 MemorySaver 承载消息历史是错的？（进程内内存、重启即丢、无界增长必然爆免费模型窗口）→ 对话历史改存 SQLite（`conversations`/`messages` 表，按账号隔离），每次 invoke 显式传入完整上下文（SQLite 恢复 + 新提问），agent 无状态执行；② 上下文预算四组件（AstrBot `core/agent/context/` 借鉴）：TokenCounter（启发式估算，中文 1.5 字/token）/ Truncator（按"轮"丢弃，system 永不丢）/ Compressor（LLM 摘要可选，失败自动降级截断）/ ContextManager 编排——token 超限或轮数超限时触发，静默保证不爆窗；③ 多会话管理（类主流 AI 聊天应用）：UUID thread_id、标题自动取首问、前端左栏会话列表（新建/切换/重命名/删除）、会话归属校验（防越权 404）。思考：为什么「截断按轮不按条」？（工具消息必须跟随所属 user 轮次，留孤立 tool 块模型会崩）；为什么删掉 checkpointer 也安全？（deepagents 只用 interrupt_on 等可选功能才需要 checkpointer，本项目只用了工具排除 middleware）。 |
| 2️⃣7️⃣ | `agent/build_context.py` + `api/server.py`(agent 构建/缓存) | **M2 请求装配管线化（v2.0）**：① 为什么 v1.0「改人格/记忆就重建整个 agent」不好？（每次画像变化重建开销大、缓存膨胀、动态来源无法叠加、难以调试这次请求带了什么）→ agent 静态化（只按自定义模型配置缓存），SOUL/MEMORY 每次 invoke 前由 `build_request_context` 拼成 **SystemMessage 放 messages 首条**注入（Spike 实测：deepagents invoke 首条 system 动态覆盖构建时 system_prompt）；② 人格切换零重建（e2e：P1海盗→P2管家不重启服务即生效）；③ 单一来源：旧拼接副本删除，动态内容统一走 `compose_dynamic_prompt`（人格在前、记忆在后）。思考：为什么动态 SystemMessage 不怕历史截断？（ContextTruncator 保护所有 SystemMessage，persona 永不丢）；为什么「改模型才重建」？（模型配置影响 agent 绑定的模型实例，人格/记忆只是请求期输入）。 |
| 2️⃣8️⃣ | `rag_knowledge/` + `tools/kb_tools.py` | **M3 RAG 知识库（v2.0）**：① 为什么弃 RAGFlow 用自建 Chroma？（蜀道已验证同栈 chromadb+bge 混合检索 ALL@10=0.83；单机本地零运维；模型方法可复用）② 存什么？（用户私有沉淀：导出报告经弹窗确认入库 + 上传 .md——**不做**网络缓存/SOUL 向量化）；③ 检索管线（BM25 稀疏 + bge 稠密 → RRF 融合 → 可选 bge-reranker 重排 → 父块注入）；④ 快/全双模式（对话 query_kb 快模式跳过 rerank ~0.1s；周报代码层必查 full 模式 <1s——个人库 ≤100 文档规模小）；⑤ **入库决策链**（evaluator 独立小 agent 评估脏/质量 → 前端弹窗显示评估建议 + 清洗预览 → 用户确认 → cleaner 清洗 AI 报告脏内容 → 分块入库）；⑥ **消息级防重复入库**（messages 表 `kb_ingested` 标志：同一条 assistant 消息的报告已入过库 → 弹窗顶部 ⚠️ 警告 + 后端 400 拒绝；force=true 可强入）；⑦ **前端弹窗**：用 `<teleport to="body">` 挂载到 body 根（任何 view 都能弹，不再局限知识库页）；亮色文字适配深底弹窗。思考：为什么入库前要清洗 AI 导出报告？（人格口吻/emoji/表格符是检索脏内容，蜀道 272 篇污染清理教训）；为什么 query_kb 是工具不是子 Agent？（主 Agent 掌控何时查，免费模型不会主动查——周报场景代码层必查兜底）；为什么用消息级去重而不是 md5 去重？（同内容报告每次导出文本可能微调措辞，md5 会漏；消息级精确到"这条回复是否已入过"）。 |
| 2️⃣9️⃣ | `api/customize.py`(技能能力层) + `agent/build_context.py`(`compose_user_prompt`) + `api/server.py`(`/api/chat` 的 skills) + `static/index.html`(技能与工具视图) | **M4a 技能（v2.0）**：① 技能 = **用户自己写的 SKILL.md 任务说明书**，存 `agents_docs/{user}/skills/`（复用 SOUL 的目录与文档读写工具）——为什么用「user message 前缀注入」而不是塞进 system_prompt？（技能是**本轮一次性**语义：用户只在这次提问里要它，塞 system 会与人格/记忆的常驻语义混淆且无法随轮次消失）② 注入链路：输入框 `/` 唤出目录（筛选、↑↓ 回绕、Enter/Tab 选中、Esc 关闭，最多叠加 5 个）→ 气泡里留技能 pill → `POST /api/chat?skills=a,b` → **server 层**读技能正文传入 `build_request_context`（不能放 `build_context.py` 里读——该模块铁律「不 import api.*」）→ `compose_user_prompt()` 拼成 `【本轮启用技能】…———【用户提问】…` 前缀；③ **落库的仍是原始提问**（前缀只进本轮 invoke）→ 下一轮自然不再带技能，这就是「一次性」的实现方式；④ 边界识别修正：本轮起点判定改按 `ctx.final_user_msg`（装配后文本）比对，否则加了前缀后 `m.content == question` 恒不成立、工具调用消息会被漏掉；⑤ 技能名即文件名 → sanitize 白名单（防 `..` 与 Windows 保留名 `nul/CON/AUX`）+ 单技能 4000 字上限（前后端同值硬截断）。思考：为什么技能要用「说明书文档」而不是「硬编码工具」？（工具是能力、技能是方法：换口径不用改代码）；为什么「用完即清」而不是会话内常驻？（常驻会让多个技能互相污染，也解释不清「这次为什么这样答」）；为什么句中斜杠（如「价格/性能」）不该弹菜单？（只在输入框开头为 `/` 时触发，否则正常提问会被打断）。 |
| 3️⃣0️⃣ | `tools/_runtime/shell_runtime.py` + `tools/shell_executor.py` + `api/server.py`(`/api/shell/*`) + `static/index.html`(CLI 面板) | **M4b CLI 沙箱面板（v2.0）**：① 为什么给 Agent 开命令行？（CLI 是 AI 的母语：`--help` 自描述、参数组合表达力强、输出可解析；对比 MCP 每接一个服务都要写一层适配）② **工具层 / 能力层分离**：`shell_runtime.py` 只干执行，`shell_executor.py` 用 `@tool run_shell_command` 包给 Agent——**CLI 面板与 Agent 共用同一执行器**，所以「面板能敲什么」＝「Agent 能跑什么」，安全规则不会两套漂移；③ **六道闸**：命令白名单 → 参数级路径校验 → 沙箱 cwd → 超时上限 → 输出 1MB 截断 → 审计日志 `data/audit/commands.jsonl`（含被拒条目）；④ 为什么剔除 `python`？（它本身就是沙箱后门：一条 `python -c` 就能读任意文件、起子进程，其余五道闸形同虚设）；⑤ 为什么 `ls/cat/grep/echo/pwd/mkdir` 要 Python 内建实现？（实测 Windows 原生 PATH 下这些 exe 一个都找不到——Git 只把 `PortableGit\bin` 加进 PATH，`usr\bin` 没加）；⑥ 为什么只挡 cwd 不够？（`cat ../../.env`、`git --git-dir=C:/Users`、`curl file:///C:/Users/ZQK/.ssh/id_rsa` 都能绕过沙箱 → 必须参数级校验，且「等号写法」要一起拦）。思考：为什么 `curl` 只允许 GET 且禁 `-o`？（否则会变成任意文件下载器直写沙箱）；为什么这个沙箱刻意保持**只读**——只做「看文件 + 抓网页 + 建目录」，不提供写文件能力？ |
| 3️⃣1️⃣ | `tools/cli_registry.py` + `tools/_runtime/shell_runtime.py`(第七道闸) + `api/server.py`(`/api/cli/*`) + `static/index.html`(🧩 自定义 CLI 页) | **M4c 自定义 CLI 接入（v2.0，配置导向）**：① 为什么不预设厂商？（推出 CLI 的厂家远不止飞书/钉钉/Google，写死就得改代码；改成「用户填字段」后，与模型配置填 base_url/model/key 一样通用——系统只按三条与厂商无关的规则办事：白名单＝用户配的条目、只读闸＝用户填的清单、本机检测＝`which 可执行名`；内置几家退化为 `templates` 预填示例，点一下只填表单、不进白名单）；② **只读清单为什么必须人给**？（哪些子命令只读无法自动推断——`doc search` 是读、`cache clear` 是写、`im +messages-send` 是写，同厂内部都没有统一命名规律；系统不猜，清单留空＝不放行 AI 代跑）；③ 与 Agent 的关系＝折中方案 C：登记后只放行只读/查询子命令，写操作与落盘参数（`-o/--output…`）一律拒绝；④ 安全边界（自定义≠放松）：**可执行名只允许纯名字**（含 `/ \ :` 即拒，否则等于把「执行任意程序」交给表单）、清单含 shell 元字符（分号/管道/重定向/美元符等）即拒、每行 ≤6 段 ≤40 行、fail closed；⑤ 配置落 SQLite `user_clis`（`UNIQUE(account_id, bin)`，按账号隔离），旧版内置厂商登记幂等迁移进来；⑥ **局部更新「缺省即保留」**：只传 `id/name/bin` 时不能把 `state` 清成 none、`readonly` 清空（否则「只改个名字」就把接入登记和代跑权限一起弄没了）。⑦ **CLI 简报注入**：`tools/cli_registry.agent_brief()` 为每个已接入的 CLI 生成一行（名称+可执行名+放行条数+示例子命令），由 server 层经 `build_request_context(cli_brief=…)` 拼进 invoke 期的动态 SystemMessage（与人格/记忆同一机制），`prompts.yml` 主 Agent 同步加「命令行使用纪律」——不注入的话，Agent 会答「该命令不在白名单」（实测它连工具都没调，把工具说明书的通用描述当成了查过白名单的事实）；没配 CLI 时注入空串、零开销。思考：为什么「可执行名不允许写路径」这条比「白名单里少几个命令」重要得多？（前者一旦放开，等于给表单开了任意程序执行的后门，只读清单再严也没用）；为什么只读清单要去重却仍按**原始行数**限流？（否则 45 行相同内容去重成 1 条，绕过条数上限） |
| 3️⃣2️⃣ | `api/server.py`(`/api/tutorial/doc` + `/tutorial-assets` 挂载) + `static/index.html`(`tutRender` 只读阅读页) + `scripts/build_tutorial_preview.js` | **M4 收尾·教程指导（只读 md 阅读页）**：CLI 配置页的「📘 教程指导」按钮（视觉取自 uiverse **003 号 buttons/bubbles**，hover 气泡扩散填充；原片段是 SCSS 嵌套写法，移植时必须展平成 `.bubbles:hover > .text`）→ 跳到 `#/tutorial` 只读阅读页。要点：① **后端只读接口 + 静态资源分离**：md 由 `GET /api/tutorial/doc` 返回（不改文件），图片走 `/tutorial-assets` 静态挂载（`<img>` 不必带 token）；② **图片内联是难点**：教程 md 里的图片是本机**绝对路径**（含一张项目外的 Typora 贴图）——做法是「只取文件名 → 在白名单目录（文档目录 + Typora 贴图目录）里查找 → 拷进 `data/tutorial_assets/` → src 改写成 `/tutorial-assets/<名>`」，找不到的整段剔除并回报 `missing`（避免裂图）；安全上只按文件名解析（挡 `../` 与绝对路径穿越）、只处理图片扩展名（实测 `/tutorial-assets/../.env` → 404）；③ **前端手写 `tutRender`**（零依赖离线可用，照蜀道「政策文件阅读器」做法）：支持标题/表格/列表/引用/代码块/图片/加粗/行内码/`<details>` 折叠块，**先整体转义再只白名单放行 `<details>`/`<summary>`**（`<script>`、`<img onerror>`、`<iframe>` 实测全被转义）。思考：为什么图片不能直接按 md 里的绝对路径返回？（那等于把「读任意本地文件」做成了接口——只能认白名单目录里的**文件名**）；为什么渲染器要「先转义再白名单还原」而不是「选择性转义」？（黑名单永远漏，白名单只放四个字面量最稳） |
| 3️⃣3️⃣ | `api/server.py`(`/api/weather` + 三层缓存 + 启动预热) + `static/index.html`(`.wx-dock` 时钟/天气卡) + `scripts/build_weather_preview.js` + `scripts/build_wx_harness.py` | **M4 收尾·工作台时钟 + 天气卡**：欢迎语右侧的药丸收起 / 悬浮展开（视觉取自 uiverse **012 号 cards/weather-gradient**）。三个要点：① **数据源选 Open-Meteo**——免 Key 且自带**地理编码**接口，所以能做到**城市级**（成都 30.667/104.067，可中英文城市名）；② **三层缓存是必需的**：首次外网请求实测 2.7~17s（DNS/代理抖动），只做内存缓存的话每次重启首屏都得转圈——所以「城市坐标永久 + 天气 10 分钟 + `data/weather_cache.json` 磁盘缓存 + 启动后台预热」，并按项目惯例写了「代理失败降级直连」与「刷新失败返回 stale 旧数据而不是 502」；③ **收起/展开用绝对定位浮层**（`opacity+transform`，不占文档流），否则会把欢迎语挤走；秒级时钟靠 `setInterval(tickClock,1000)` + `_pad2` 补零，`wxStart()` 做成幂等避免重复起定时器；④ **配色默认松绿以对齐项目色系**（012 原片段是紫罗兰）：色值全抽成 `.wx-dock` 上的 CSS 变量，主题用 `data-wx` 切换、SVG 渐变靠 `stop-color: var(--wx-c1)` 覆盖表现属性，右下角圆点可切 4 套并记 localStorage；⑤ **用户实测后修掉 6 个问题**（v-else 被插入元素打断→生产版无条件渲染致「纯色无内容」、卡片被 hero 的 overflow:hidden 裁切、换城市面板与卡片重叠、`.hero p` 样式污染卡片文字、内容超高致页脚压字、页脚折行）——详见文档附录 D.7。思考：为什么天气不该由前端直连第三方 API？（外网可达性/代理问题会被甩给浏览器，且前端无法缓存、无法统一降级）；为什么展开态不能用 `display:none/block` 切换？（过渡动画会整段失效） |
| 3️⃣4️⃣ | `cli/main.py` → `cli/cmd_auth.py` `cmd_list.py` `cmd_digest.py` `cmd_chat.py` → `cli/session.py` `ui.py`；对照 `api/server.py`(`_run_agent`) 与 `api/monitor.py`(`set_console_sink`) | **CLI 版 dspro（命令行聊天 + 命令行工具）**：`dspro login/list/digest/chat`。核心设计是「**不做第二套业务逻辑**」——CLI 独立进程直接用 `data/personal.db`、`agent/digest_engine.run_digest()`、`api/server._run_agent()`，与 web 同库同引擎，所以 CLI 生成的周报在 web「情报周报」里也能看到。要点：① **登录态** `data/dspro_session.json` 存身份 + **账号密码哈希的指纹**（不存明文），密码一改旧会话自动失效；② **`list -db/-digest`** 按账号角色取模块（公司看档案/我司产品/竞品，个人看兴趣/关注清单/收藏商品），表格按**东亚显示宽度**对齐（中文/emoji 记 2 列，零依赖）；③ **`digest [领域]`** 的领域解析：`#id` → 名称精确 → 名称包含/关键词命中，**多义时报错列出候选**（实测同一账号 3 条订阅同名「选购快讯」，静默取第一条会误导）；④ **`chat` 复用 `_run_agent`**，SOUL 人格/记忆画像/技能/知识库/自配 CLI 全部生效，会话落 `conversations` 表（web 也能看到）；⑤ **monitor 进度**：给 `ToolMonitor` 加了 `set_console_sink()`，CLI 注册后把工具调用/子智能体/思考渲染成终端进度行，而 web 不注册、行为不变（否则 `_emit` 的保底 `print` 会把 `[Monitor:xxx]` 混进对话）。思考：为什么 CLI 不该自己再写一遍 digest 逻辑？（同一份引擎才能保证 CLI 与 web 结果一致、且生成物同库落盘）；同名订阅为什么不能「取第一条」？（用户以为生成的是 A，实际是 B——静默选择比重名更危险） |
| 3️⃣5️⃣ | `tools/cli_registry.py`(`agent_brief` / `_parse_ability_pairs` / `ability_examples`) → `prompt/prompts.yml`(`ability_writer` + 3 条纪律) → `api/customize.py`(`cli_ability_generate`) → `api/server.py`(`POST /api/cli/ability_draft` + `_run_agent` 注入) → `static/index.html`(CLI 表单「能力描述」)；验证 `tests/m5_abilities_e2e.py`(43 项) `tests/m5_probe_e2e.py`(探针组) `tests/_m5_real_weread_check.py`(真实账号)；对照/迭代记录见 `docs/v2.0/M5工具智能路由.md` §3 与 §9 | **工具智能路由 M5a：让 Agent 在你说人话时想起对的工具**。M4c 解决了「Agent 知不知道有这些 CLI」，但注入的是 `shelf recent` 这类**命令名**——用户问「我最近在读什么书」时意图与命令名不在同一语义空间（LLM Agent 的经典「工具路由」问题，业界公认解法就是把描述**改写成用户语言**）。M5a 三步：① `user_clis.abilities` 存一段用户语言（关键词 + 「用户说法→命令路径」映射），`agent_brief()` **有描述出「能力路由卡」、没有就回退命令名清单**（存量零影响）；② 从描述生成 **few-shot 示例**与路由卡一起注入（**每条 argv 逐条过只读闸**，未放行的映射直接丢弃——示范一条会被拒的命令等于反向教学）；③ 「✨ 按只读清单生成草稿」让 AI 起草、**只填表单不落库**，人确认后保存。**★ 最关键的坑**：第三方 CLI 里需要 ID 的命令（`book info` / `reviews list` / `book progress` / `discover similar`，`--help` 实测）不能直调，映射要写成**两步链** `书评怎么样→book resolve 再 reviews list`（示例取链上第一步）；否则模型要么调报错命令、要么绕去网搜答你的私有数据。**实测**：探针组 10 正全中（复跑 9/10）+ 3 反例全过；**同题对照组（M4c 命令名形态）仅 2/10**——8 条连工具都没调；真实账号 + 真实 weread 端到端 15/15。思考：为什么能力描述不必像只读清单那样「必须人给」？（一个决定**能不能跑**、一个决定**想不想得起跑**，错判代价量级不同——安全边界与路由信号分层治理）；为什么判定要看库里 `messages.tool_calls`（会话真值）而不是审计日志的窗口计数？（窗口会被并发进程写进同名调用污染，实测踩过）；为什么要「只看第一次调用」？（装桩后每条命令都能成功，模型会把清单挨个试，按「出现过即命中」判会虚高） |
> 💡 学习心法：Agent 系统 = **模型（llm.py）** + **提示词（prompts.yml）** + **工具（tools/）** + **编排（subagents/ + main.py）** + **可观测性（api/）**。把这条主线记牢，再看任何 Agent 框架都不慌。

---

| 3️⃣6️⃣ | `tools/capability_pool.py` → `tools/tool_router.py` → `api/server.py`(`_run_agent` 注入) → `tools/schema_personal.py`(`tool_capabilities`/`tool_pool_meta`/`purge_account`)；标定与验证 `tests/_m5b_route_calib.py` `tests/m5b_pool_e2e.py`(62) `tests/m5b_router_e2e.py`(34) `tests/m5b_pressure_probe.py`；设计与实测见 `docs/v2.0/M5b工具检索路由.md` | **工具检索路由 M5b：工具变多之后，怎么还能想起对的那个**。① **能力目录**把 CLI / API / MCP 三种来源统一成 `CapabilityEntry`（路由只认这个形状，接新来源只写适配器）；CLI 侧**派生而不迁移**（`user_clis.abilities` 仍是唯一事实源）；池版本 + 向量惰性重建，配置写入不再冻界面。② **路由器**四级决策：池 ≤4 → 快路径（与 M5a 逐字一致）；否则 bge 稠密 + 关键词词法 → RRF 融合；**置信判定用相对信号**（词法命中 ≥2 或向量 z ≥2.0，绝对阈值已被实测证伪）；置信才分层注入（完整卡 / 紧凑行 / **仅名称**——未命中工具也保留一行，藏掉工具等于该类问题永远无解），不置信就**整段回退全量**。**实测**：8 工具异构池下 10 条问句命中判定 **10/10**、**0 条路由到错的工具**、2 条无关问句全部回退；注入量 1644 字 vs 全量 2499 字；两个真 bug 被测试逮住（RRF 无信号排序器、示例过滤条件）。思考：为什么阈值必须用 **z 分数/词法命中**而不是「相似度 > 0.5」？（中文短问句的余弦区分度只有 0.02~0.06、绝对值全挤在 0.53~0.70——绝对阈值会「全部命中」，分层形同虚设；相对信号还能随工具数增长自动放大）；为什么「不确定就回退全量」比「猜一个最像的」更好？（回退等于 M5a 行为、只是没省 token，而猜错会让模型拿着错工具的说明书去答用户的问题）；为什么未命中的工具也要留一行名称？（路由是概率判断，藏掉工具会让「那次没命中」的问题变成永久无解）；为什么要**惰性**同步向量？（向量同步要加载 bge，放进配置写入路径会让面板冻 1~3 分钟——写入只落库+打版本，重建推迟到真正需要路由的时刻） |

## 五、阅读指南（CLI）（建议顺序）

> 智选情报官 **CLI 版**（`dspro` 命令行）的代码阅读路径。CLI 版**不做第二套业务逻辑**——它直接复用 web 版的本地库、Agent 装配层与周报引擎，所以这份指南**只聚焦 CLI 自身的 6 个文件**；凡是「业务逻辑」一律指向 web 版对应模块，对照 [四、阅读指南（Web）](#四阅读指南web建议顺序) 阅读。

> **只关心 Web 版？** 跳到 [四、阅读指南（Web）（建议顺序）](#四阅读指南web建议顺序)。

| 顺序 | 文件 | 看什么 / 思考题 |
| --- | --- | --- |
| 1️⃣ | `cli/main.py` | **CLI 总入口**：`argparse` 装配 4 个子命令（login/logout/whoami/list/digest/chat）。注意：① 子命令用 `set_defaults(func=lambda x: ...)` 挂载，主函数 `args.func(args)` 统一调度；② 错误分层：业务错误用 `CliError`（`main` 统一捕获 → 红字 + 退出码，**不打印堆栈**），真正的 bug 仍走原生 traceback；③ 无参数不报错 → 打印落地页（ASCII 标识 + 用法 + 当前登录态）；④ 全局开关 `--no-color` 挂在父 parser 上，子命令 `add_help=False` 避免 `-h` 冲突。 |
| 2️⃣ | `cli/session.py` | **登录态与账号访问**：① 登录态文件 `data/dspro_session.json` 存**身份 + 账号密码哈希的指纹**（不存明文），密码被改后指纹不匹配 → 旧会话自动失效；② 校验**复用** `api/account.py` 的 `login()`（同一份盐 + 同一张 `accounts` 表），不复制第二套哈希逻辑；③ `require()` 是「取当前登录态 + 校验失效」的统一入口，所有子命令先调它；④ `db()` 返回个人库连接（Row 工厂 + 外键），调用方负责 close。 |
| 3️⃣ | `cli/ui.py` | **终端输出（零依赖）**：① 颜色开关：`--no-color` / `NO_COLOR` 环境变量 / 非 tty → 自动降级纯文本，Windows 老 conhost 手动开 VT；② **东亚宽度**：`unicodedata.east_asian_width(ch) in ("W","F")` 记 2、emoji（`ord(ch) >= 0x1F300`）记 2、ANSI 转义码不计入 → 表格不会因中文/emoji 错位；③ `md_light()` 做 markdown 轻渲染（标题加色加粗、`**x**` 加粗、`` `x` `` 上色、引用加竖线）；④ `progress()` 是 monitor 事件的终端渲染器（`⚙ 调用工具` / `🤝 调度助手` / 思考片段 / `✓ 子任务完成`）。 |
| 4️⃣ | `cli/cmd_auth.py` | **login / logout / whoami**：① `login` 支持「密码写 `-`」走 `getpass` 隐藏输入（避免进 shell 历史）；② `--register` 直接注册新账号（角色 company/personal）；③ `logout` 删除登录态文件；④ `whoami` 显示账号/角色/订阅数/周报数/默认城市（直接查库，不走 web API）。 |
| 5️⃣ | `cli/cmd_list.py` | **list（默认 / -db / -digest / --json）**：① 按**账号角色**取模块——公司账号看「公司档案 + 我司产品 + 关注竞品」，个人账号看「兴趣领域 + 关注清单 + 收藏商品」；另一侧若有数据会额外提示一行，避免用户以为「数据丢了」；② 表格按东亚宽度对齐；③ `--json` 给脚本用（`{username, role, counts, db, digest}`）。 |
| 6️⃣ | `cli/cmd_digest.py` | **digest [领域名\|#id]**：① 领域解析优先级：`#id` 或纯数字 → 名称精确匹配 → 名称包含/关键词命中；**多义时报错并列出候选（带 #id 与关键词），绝不静默取第一条**（实测同一账号 3 条订阅同名「选购快讯」，静默选择会误导）；② 引擎直接调 `agent.digest_engine.run_digest()`（与 web `/api/digest/run` 同函数），生成物落 `digest_reports` 表（web 端「情报周报」也能看到）；③ 进度行由 `monitor.set_console_sink(ui.progress)` 渲染（工具调用/子智能体/思考片段），用完 `set_console_sink(None)` 恢复。 |
| 7️⃣ | `cli/cmd_chat.py` | **chat（交互 + 单轮）**：① 直接调 `api/server._run_agent()`（与 web `/api/chat` 同装配管线：SOUL 人格 + MEMORY 画像 + 技能 + 自配 CLI 简报 + 同一份 SQLite 历史与 token 预算）；② 会话落 `conversations`/`messages` 表（web 也能看到 CLI 的对话）；③ 交互命令：`:new` 新会话、`:sessions` 列表、`:skills` 技能列表、`:q` 退出；`Ctrl+C` 中断本轮但保留历史；④ `--skill` 可重复，仅本轮生效（技能文件在 `agents_docs/{用户名}/skills/*.md`）。 |
| 8️⃣ | `api/monitor.py`（对照） | **CLI 与 Web 共享 monitor 事件源**：① `ToolMonitor._emit` 有「控制台保底输出」`print("[Monitor:xxx] …")`，CLI 直接调服务端模块会被调试行污染；② 给 `ToolMonitor` 加了 `set_console_sink(fn)` 钩子：CLI 注册后渲染成进度行，**web 端不注册、行为不变**；③ 这是「对 Web 行为零风险的加法」的典型做法。 |
| 9️⃣ | `docs/v2.0/CLI命令行工具dspro.md` | **CLI 版设计文档**：命令清单、架构对照表（CLI 与 web 一一对应的模块）、登录态设计、同名歧义处理、monitor sink 接线、踩坑记录、决策记录（用户拍板：独立运行 / 项目内登录态 / 一并提供 chat / 纯文本 ANSI 零依赖）。 |

> 💡 CLI 心法：**CLI ＝ 同一份业务逻辑的另一个调用方**。6 个 CLI 文件（main/session/ui/cmd_auth/cmd_list/cmd_digest/cmd_chat）本身不含业务规则，全部指向 web 版对应模块。读 CLI 时始终问：「这个能力在 web 版走哪里？」——答案在 [四、阅读指南（Web）](#四阅读指南web建议顺序)。

---

## 六、环境要求

- **Python 3.11**（项目通过 `.python-version` 锁定）
- 包管理器：**uv**（推荐，已配置清华镜像源，安装更快）
- 操作系统：Windows / macOS / Linux 均可；`utils/word_converter.py` 依赖 Windows 上的 Word COM，仅转换 PDF 功能受限

---

## 七、快速开始

### 1. 安装 uv（如未安装）

```bash
# Windows (PowerShell)
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps4 | iex"

# macOS / Linux
curl -LsSf https://astral.sh/uv/install.sh | sh
```

### 2. 克隆并进入项目

```bash
cd deep_search_pro
```

### 3. 创建虚拟环境并安装依赖

```bash
# 用项目指定的 Python 3.11 创建环境并安装依赖
uv sync
# 或：uv venv && uv pip install -e .
```

> 依赖较多（langchain、langgraph、deepagents、fastapi、mysql-connector、tavily、ragflow-sdk 等），首次安装可能需要几分钟。

### 4. 配置环境变量

复制示例文件并填入你自己的密钥：

```bash
cp .env.example .env
```

然后编辑 `.env`，至少填写以下变量（详见 `.env.example` 内注释）：

| 变量 | 作用 | 是否必须 |
| --- | --- | --- |
| `LLM_MODEL_MAX` | 大模型名称（OpenAI 兼容接口） | ✅ 必填 |
| `OPENAI_API_KEY` / `OPENAI_BASE_URL` | 模型 API Key 与接入地址 | ✅ 必填 |
| `TAVILY_API_KEY` | Tavily 网络搜索密钥（启用网络搜索助手） | ⚠️ 用到才填 |
| `MYSQL_HOST` / `MYSQL_USER` / `MYSQL_PASSWORD` / `MYSQL_DATABASE` | 数据库连接信息（启用数据库助手） | ⚠️ 用到才填 |
| ~~`RAGFLOW_*`~~ | 已废弃：M3 起知识库为**自建 Chroma**（`rag_knowledge/`），无需外部 RAG 服务 | ❌ 不用填 |
| `WEATHER_CITY` | 工作台天气卡默认城市（Open-Meteo 免 Key 数据源；前端可随时「📍 换城市」覆盖） | ⚠️ 想要非成都默认才填 |

### 5. 跑通最小验证

`main.py` 已是可运行的 CLI 交互版。可先单独验证「配置 + 模型 + 工具」这条链路是否打通：

```bash
# 验证提示词配置能正常加载（应看到 main_agent + 4 个子 Agent：tavily / db / personal / ragflow 占位）
python -c "from agent.prompts import main_agent_content, sub_agents_content; print('主Agent:', main_agent_content); print('子Agent:', list(sub_agents_content.keys()))"

# 验证模型初始化（需先配好 .env 中的模型变量）
python -c "from agent.llm import model; print(model)"
```

看到配置字典和模型对象正常打印，说明环境已就绪。

### 6. 跑起来（两种方式任选）

**方式 A：Web 版（M2，推荐）**

```bash
# 启动 FastAPI 服务（默认 127.0.0.1:8000，可自行改端口）
uv run python -m uvicorn api.server:app --host 127.0.0.1 --port 8123
```

浏览器打开 `http://127.0.0.1:8123`：
- 登录页输入用户名/密码 + 选「公司账号 / 个人账号」→ 未注册会自动注册；
- 登录后按角色进入多页签工作台：**工作台**（总览，含快捷入口）/ **公司信息·个人信息**（维护资料/产品/竞品/兴趣，供 Agent 分析用）/ **AI 助手**（问答 + 右栏 WebSocket 实时进度 + 「🤔 模型思考过程」折叠区 + 回答导出 MD/PDF）/ **报告**（Digest 订阅管理与周报下载）/ **定制助手**（模型选型 + 助手人格设计）；顶栏「🎨 背景」可上传/选择/删除背景图并调透明度；
- **Digest 报告页**：首次进入自动生成默认订阅（公司←竞品清单、个人←兴趣标签），可改关键词、周期（daily/weekly）与**检索语言**（中文/英文/中英双语，`lang` 字段）；点「批量生成（选中 N 个）」手动触发，或等 APScheduler 每天 9 点 / 每周一 9 点自动跑；新报告生成时顶栏红点 + WS 弹提醒。
- **定制助手页**：模型选型区填「名称/模型名/base_url/API Key」四点并「启用」，该账号后续对话即走自定义模型（默认 .env 免费模型）；人格区可手动编辑 SOUL.md，或输入风格描述（如「活泼女仆」）点「✨ AI 生成人格」→「一键导入」→「保存人格」，对话与周报措辞立即体现该人格。

**方式 B：CLI 版（保留可用）**

```bash
# 进入交互式问答（输入 exit / quit / q / 退出 结束）
python main.py
```

> 注意：若本机存在其他名为 `agent` 的 Python 包（如全局 venv 中的同名包），`api/server.py` 已在顶部做了 sys.path 防御（项目根优先 + 剔除冲突路径）；`main.py` 直接在项目根运行不受影响。开发顺序以 `docs/v1.0/里程碑计划.md` 为准绳。

---

## 八、项目现状 & 改造方向

根据 `IDEA.md`，本项目定位是**初学框架**，已演进为本地个人使用的 AI 情报助手「智选情报官」。开发以 `docs/v1.0/里程碑计划.md` 为准绳，**v1.0 的 M1 / M1.5 / M2 / M3 / M4 已全部完成并实测通过；v2.0 的 M1~M4（含 a/b/c 与三项收尾）、M5a、M5b（CLI 部分）已全部完成并端到端实测通过，v2.0 正式收官**——唯一按拍板推迟到 v3.0 的是 **MCP / API 适配器**（M5b 的目录层与路由器已按来源无关实现，届时只写适配器）。

### v2.0 全部更新总览

> 一张表看完整轮迭代：每行 = 「做了什么 / 关键设计与踩过的坑 / 独立实测数字」。数字取自各里程碑的端到端脚本（`tests/`），**全部为本机真跑结果**；设计细节见 `docs/v2.0/` 对应文档。

| 里程碑 | 做了什么 | 关键设计与踩过的坑 | 实测 |
| --- | --- | --- | --- |
| **M1** 上下文工程化 | 对话历史落 SQLite（`conversations`/`messages`）+ 多会话管理（新建/切换/重命名/删除、越权 404）+ 上下文预算四组件（TokenCounter / Truncator / Compressor / Manager） | 弃掉 v1.0 的进程内 `MemorySaver`（重启即丢、无界增长必爆免费模型窗口）；**截断按「轮」不按「条」**——工具消息必须跟随所属 user 轮次，留孤立 tool 块模型会崩 | 11/11 |
| **M2** 请求装配管线化 | agent 静态化（缓存只按自定义模型配置）+ 人格/记忆每次 invoke 前拼成 SystemMessage 注入 | 把「改人格就重建整个 agent」换成 invoke 期注入（Spike 验证 deepagents 支持首条 system 动态覆盖）；动态内容**单一来源** `compose_dynamic_prompt`；SystemMessage 永不被截断 → 人格不丢 | 8/8 |
| **M3** RAG 知识库 | 自建 Chroma 每用户一库 + 入库决策链（evaluator 评估 → 前端弹窗确认 → cleaner 清洗）+ BM25 稀疏 ⊕ bge 稠密 RRF ⊕ reranker + 快/全双模式 + 消息级防重复入库 | 弃 RAGFlow（单机零运维）；**入库前必须清洗**（人格口吻/emoji/表格符都是检索脏内容）；弹窗 `teleport` 到 body（否则只在知识库页可见、其它页触发了也看不见）；去重按**消息级**而非 md5（同一报告的措辞会微调，md5 会漏） | 11/11 |
| **M4a** 技能 | 用户自写 SKILL.md 任务说明书 → 输入框 `/` 唤出目录（筛选/回绕/→多选 ≤5）→ `POST /api/chat?skills=a,b` → **user message 前缀**注入 | 技能是**本轮一次性**语义（塞 system 会与人格/记忆的常驻语义混淆）；**落库仍是原始提问** → 下一轮自然不带技能；句中斜杠（「价格/性能」）不弹菜单 | 30/30 + 前端 56/56 |
| **M4b** CLI 沙箱面板 | 白名单命令执行器（能力层 / 工具层分离）+ **六道闸**（白名单 / 参数级路径校验 / 沙箱 cwd / 超时 / 输出 1MB / 审计日志）+ 面板与 Agent 共用同一执行器 | 剔除 `python`（一行 `-c` 就能读任意文件、起子进程，其余五道闸形同虚设）；`ls/cat/grep` 必须 Python 内建实现（Windows 的 PATH 里这些 exe 一个都找不到）；**只挡 cwd 不够**——`cat ../../.env`、`curl file:///…/.ssh/id_rsa` 都能绕过 → 必须参数级校验；口径定为**只读** | 51/51 |
| **M4c** 自定义 CLI 接入 | **不预设厂商**：用户自填名称/可执行名/安装与认证命令/文档/只读命令清单 + 本机检测 + 只读子命令代跑 + CLI 简报注入 Agent 上下文 | **可执行名只允许纯名字**（含路径＝把「执行任意程序」交给表单，只读清单再严也没用）；只读清单必须人给（同厂内 `doc search` 是读、`cache clear` 是写，无统一规律，系统不猜）；局部更新「**缺省即保留**」（否则只改个名字就把代跑权限清空）；简报不注入 → Agent 会答「该命令不在白名单」 | 109/109 |
| **M4 收尾** CLI 版 `dspro` | `login/logout/whoami/list/digest/chat` 六命令，独立进程运行（不需 uvicorn） | 「**不做第二套业务逻辑**」：直接复用 `run_digest()` / `_run_agent()`，与 web 同库同引擎；同名订阅**多义必须报错列候选**（静默取第一条会让用户以为生成的是 A）；`ToolMonitor` 加 `set_console_sink`（web 端不注册、行为不变） | 38/38（SKIP_LLM）+ 真跑 digest/chat |
| **M4 收尾** 工作台时钟 + 天气卡 | 药丸收起 / 悬浮展开成渐变天气卡（大号读数 + 体感·湿度·风速·降水 + 3 天预报 + 数据源） | 数据源选 **Open-Meteo**（免 Key + 自带地理编码 → 能做到城市级）；**三层缓存 + 启动预热**（首次外网实测 2.7~17s，只做内存缓存则每次重启首屏都转圈）；展开体用绝对定位浮层（`display` 切换会让过渡动画整段失效） | 40/40 + 前端 31/31 |
| **M4 收尾** 教程只读阅读页 | `#/tutorial` 只读 md 阅读页 + md 里本机绝对路径图片自动内联 | 图片**只按文件名**在白名单目录里找（按原路径返回＝把「读任意本地文件」做成接口）；渲染器**先整体转义再白名单放行** `<details>`（黑名单永远漏）；占位式代码块必须在「整行就是占位符」的分支里显式还原（否则整块消失） | 35/35 + 渲染器 31/31 |
| **M5a** 工具能力语义路由 | `user_clis.abilities` 用户语言能力描述（关键词 + 「说法→命令路径」映射）→ **能力路由卡** + few-shot 映射示例 + AI 草稿按钮 | M4c 只解决「Agent 知不知道有这工具」，M5a 解决「**你说人话时它想不想得起**」；**需要 ID 的命令要写成两步链**（`书评怎么样→book resolve 再 reviews list`）；示例 argv **逐条过只读闸**（示范一条会被拒的命令＝反向教学） | 43/43；探针 10/10；**同题对照组（命令名形态）仅 2/10** |
| **M5b** 工具检索路由 | 统一**能力目录**（`CapabilityEntry` 契约，CLI/API/MCP 同形状 + 版本化向量惰性重建）+ **路由器**（快路径 / 混合检索 RRF / 相对置信判定 / 完整卡·紧凑行·仅名称分层注入 / 全链兜底） | 注入决策依据从「工具数量」改为「**提问相关性**」；**绝对余弦阈值被实测证伪**（中文短问句 top1~top3 只差 0.02~0.06、绝对值挤在 0.53~0.70 → 改词法命中 ≥2 或向量 z ≥2.0）；**未命中的工具也留一行名称**（藏掉＝那类问题永远无解）；向量**惰性**重建（写进配置保存路径会冻界面 1~3 分钟） | 62/62 + 34/34；真机加压探针 8/8 正例 + 2/2 反例；注入 1644 字 vs 全量 2499 字 |
| **v2.0 收尾** 登录 / 注册两卡分离 | 卡片顶部「登录 / 注册」两个 tab、两张卡彻底独立；注册卡含确认密码 + 「即将创建：xxx」实时回显；后端登录语义拆细（**账号不存在 → 404 / 密码错误 → 401**） | 旧版「登录失败即自动注册」＝ **打错一个用户名就静默建出一个能登进去的空账号**（真踩过：想登「尼古喵喵」建成了「尼姑喵喵」）；顺带发现全局提示条挂在「登录后才渲染」的分支里 → **注册成功的提示在登录页根本看不见**；同批修复 `/` 技能菜单静默失效（`setup()` 漏导出 4 个事件处理函数：不报错、不白屏、只有交互失效） | 36/36 + 真机 CDP 21/21 + CLI 38/38 + 前端不变量 3/3 |

**v2.0 改动规模**：新增/重构 8 个模块（`agent/context_budget.py`、`agent/conversation_store.py`、`agent/build_context.py`、`rag_knowledge/`、`tools/cli_registry.py`、`tools/capability_pool.py`、`tools/tool_router.py`、`cli/`），1 个 SPA 前端（`static/index.html`，单文件 ~8 万字内联 JS）与 1 套 SQLite 个人库扩展（会话/技能/CLI/能力目录/池版本，全部按 `account_id` 隔离），配套 20+ 个端到端脚本。**遗留到 v3.0**：MCP / API 适配器、用户画像沉淀、检索会话级计数器、`build_request_context` 完整落地（SOUL/MEMORY 注入）、价格 MCP 自动化。

当前状态：

- ✅ 已完成（已实测通过）：
  - 提示词配置机制、LLM 初始化、监控埋点、异步上下文隔离；
  - 网络搜索子 Agent（Tavily，含代理断开自动降级直连）、数据库子 Agent、个人情报子 Agent 三者装配；
  - **Web 版（M2）**：FastAPI + 登录鉴权/角色分流 + Vue3 免构建前端 + WebSocket 实时进度；
  - **前端体验（M3）**：简报卡片分块渲染、左栏竞品清单、回答导出 MD/PDF；
  - **用户数据自主管理（M4 前置）**：`/api/me/*` 全套 CRUD（公司资料/我司产品/关注竞品/兴趣/关注/收藏），按账号隔离、空置起步；公司侧数据已迁 SQLite（MySQL 转 legacy 演示源）；产品支持描述/卖点/目标客户等 attributes 扩展字段供 Agent 评估；
  - **Digest 定期推送（M4）**：订阅管理（默认订阅按角色自动生成，关键词≤8）+ Tavily/HN 双通道检索（近7天时间窗）+ LLM 三关筛选（编造 URL 直接丢弃）+ 周报模板生成 + APScheduler 定时调度（daily@9:00/weekly@周一）+ WS 实时提醒 + 报告列表页与下载；
  - **思考过程可视化**：`agent/reasoning_model.py` 的 ReasoningChatOpenAI 透传 reasoning_content + `agent/thinking_capture.py` 在 invoke 后遍历消息提取，前端右栏折叠展示（langchain 丢弃非标准字段 → 官方 provider 子类方案，单请求不旁路）；
  - **定制助手**：模型选型（`llm_providers` 表按账号存 base_url/api_key，**列表掩码显示**，动态构建 agent）+ 多人格集（`SOUL/` 子目录 + 下拉切换）+ 助手人格（SOUL.md 注入 system_prompt，`soul_writer` 独立段 AI 生成人格一键导入）；
  - **记忆白盒化**：账号级 `MEMORY.md`（注册自动生成 + Agent 对话中自行读写维护 + 前端可视化编辑 + 一次性 AI 初始化画像 + digest 画像驱动情报权重 + 回复末尾画像变更提示）；
  - **批量导入**：竞品/收藏剪贴板粘贴（一行一个，去空去重 ≤50 条）；
  - **语音朗读与自定义音色**：联动 voice_test（Qwen3-TTS）——定制页上传参考音频+台词克隆音色 pkl（按账号前缀隔离），AI 回复框「🔊 朗读」合成播放；模型用时启、用完关（显存约束），subprocess 环境隔离；
  - **背景图持久化**：`pic/{username}/` 目录 + 上传/列表/删除 API + 前端图库选择与透明度调节；
  - **周报时效性修复**：查询词泛用化（弃用修饰词拼接）+ `strict_days` 代码层按 published_date 剔旧闻 + `bilingual` 中英双语检索合并 + 订阅级检索语言可选。
- ✅ **v2.0 M1 上下文工程化（已落地）**：对话历史 SQLite 持久化（`conversations`/`messages` 表）+ 多会话管理（UUID 会话、前端左栏新建/切换/重命名/删除、越权 404）+ 上下文预算四组件（TokenCounter/Truncator/Compressor/Manager，AstrBot 借鉴）防 token 爆窗——规划与验证记录见 `docs/v2.0/M1上下文工程化.md`；
- ✅ **v2.0 M2 请求装配管线化（已落地）**：agent 静态化（缓存只按自定义模型配置，改人格/记忆零重建）+ SOUL/MEMORY 每次 invoke 前经 `build_request_context` 拼成 SystemMessage 注入（Spike 验证 deepagents 支持动态覆盖）+ 消除「定制助手 × 全局 AGENT」双逻辑——规划与验证见 `docs/v2.0/M2请求装配管线化.md`；
- ✅ **v2.0 M3 RAG 知识库（已落地）**：自建 Chroma（弃 RAGFlow）每用户一库（`rag_knowledge/db/{user}/`，默认无库首建）+ 导出报告弹窗确认入库/上传 .md + **入库前评估（evaluator 独立小 agent）+ 清洗（cleaner 去 emoji/口吻/表格）** + BM25+向量 RRF 检索（对话 query_kb 快模式 ~0.1s / 周报全模式 rerank）+ **消息级防重复入库**（messages 表 `kb_ingested` 标志 + 强制再入）+ 📚 来源激活——规划与验证见 `docs/v2.0/M3RAG知识库搭建与使用.md`；2026-09-08 修复：弹窗亮色文字 + teleport 到 body（任何 view 可弹）+ 防重复入库落地 |
- ✅ **v2.0 M4a 技能（已落地）**：用户自定义 SKILL.md 任务说明书（`agents_docs/{user}/skills/`）——输入框 `/` 唤出技能目录（筛选 / ↑↓ 回绕 / Enter 选中 / Esc 关闭，单轮最多叠加 5 个）→ `POST /api/chat?skills=a,b` → server 层读正文经 `build_request_context` 拼成 **user message 前缀**注入（本轮一次性；**落库仍是原始提问**，故下一轮自然不带技能）+ 技能与工具视图（技能 / CLI 面板 / 第三方 CLI / MCP / API 多 Tab，CLI 视觉取自 uiverse 011 终端窗口卡）——规划与验证见 `docs/v2.0/M4技能与工具.md`；
- ✅ **v2.0 M4b CLI 沙箱面板（已落地）**：白名单命令执行器（`tools/_runtime/shell_runtime.py` 能力层 + `tools/shell_executor.py` 工具层）——**六道闸**（白名单 / 参数级路径校验 / 沙箱 cwd / 超时 / 输出 1MB 上限 / 审计日志）+ **CLI 面板与 Agent 共用同一执行器**；面板是「技能与工具」页的「🖥️ CLI 面板」tab（`#/shell` 直达，视觉取自 uiverse 011 终端窗口卡），沙箱按账号隔离 `data/sandbox/{username}/`；口径为**只读**（看文件 / 抓网页回显 / 建目录，不写文件）；端到端 51/51（含真机 Agent 调 `run_shell_command` 建目录）——见 `docs/v2.0/M4技能与工具.md` 附录 A.9；
- ✅ **v2.0 M4c 自定义 CLI 接入（已落地，配置导向）**：`tools/cli_registry.py` —— **不预设厂商**，字段由用户填：名称 / 可执行名 / 安装命令 / 认证命令 / 文档链接 / **只读命令清单**（与「模型配置填 base_url+model+key」同构）；内置 4 家（飞书/企微/钉钉/Google）降级为 `templates` 预填示例（点一下只填表单，不进白名单）；配置落 SQLite `user_clis`（按 account_id 隔离，UNIQUE(account_id,bin)）；API：`GET /api/cli/list` + `POST /api/cli/save|status|delete`；**与 Agent 的关系走折中方案 C**：登记后只把用户填的只读命令放进 `run_shell_command` 动态白名单，写操作与落盘参数一律拒绝，清单留空＝不放行；安全边界：可执行名只允许纯名字（含路径即拒）、清单含 shell 元字符即拒、fail closed；前端「🧩 自定义 CLI」页（我的 CLI 列表 + 空白新建表单 + 模板预填 + 编辑/删除 + 逐条试跑）；**CLI 简报注入 Agent 上下文**（`agent_brief` → 动态 SystemMessage，消除「配好了却说没有」的幻觉）+ 主提示词命令行纪律；端到端 105+/105+（含真机 Agent：先问它有哪些 CLI、再让它真跑一条）+ 前端逻辑 50/50 —— 见 `docs/v2.0/M4技能与工具.md` 附录 B.11；
- ✅ **v2.0 CLI 版 dspro（已落地）**：`dspro login / logout / whoami / list [-db|-digest] [--json] / digest [领域名|#id] [--list] [-o] [--json] / chat [问题] [--new|--session|--sessions] [--skill]`；**独立进程运行**（不需 uvicorn），与 web **同库同引擎**（`run_digest` / `_run_agent` / `conversations` 表）；登录态 `data/dspro_session.json`（密码指纹，不存明文）；终端表格按东亚宽度对齐、零新依赖；端到端 31 项（`SKIP_LLM=1`）+ 真跑 digest/chat —— 见 `docs/v2.0/CLI命令行工具dspro.md`；
- ✅ **v2.0 M4 收尾·时钟 + 天气卡（已落地）**：工作台欢迎语右侧「收起=药丸（图标+温度+城市+**秒级时钟**）/ 悬浮或点击=展开成 012 号渐变天气卡（渐变+探出云+大号读数+体感·湿度·风速·降水概率+**3 天预报**+数据源）」；后端 `GET /api/weather` 走 **Open-Meteo（免 Key）**，城市级地理编码 + **三层缓存（坐标永久 / 天气 10 分钟 / 磁盘 + 启动预热）** + 代理降级 + stale 兜底；换城市走卡片内联输入并记 `localStorage`；**配色默认松绿对齐项目主色系**（012 原片段是紫罗兰，色值全抽成 CSS 变量、4 套可选、右下角圆点切换）；端到端 40/40 + 前端 31/31 —— 见 `docs/v2.0/M4技能与工具.md` 附录 D；
- ✅ **v2.0 M4 收尾·教程指导（已落地）**：CLI 配置页「📘 教程指导」按钮（uiverse 003 号气泡按钮）→ `#/tutorial` **只读 md 阅读页**；后端 `GET /api/tutorial/doc`（只读、不改文件）+ `/tutorial-assets` 静态挂载；**md 里的本机绝对路径图片（含项目外的 Typora 贴图）按文件名同步进 `data/tutorial_assets/` 后内联显示**，缺图整段剔除并提示；前端手写 `tutRender`（表格/代码块/折叠块，先转义再白名单放行 `<details>`）；演示文档已润色为完整教程（字段对照表 / 可粘贴只读清单 / 判定证据 / FAQ）；端到端 35/35 + 渲染器 31/31 —— 见 `docs/v2.0/M4技能与工具.md` 附录 C；
- ✅ **v2.0 M5a 工具智能路由（已落地并验证）**：把「命令名清单」升级为「能力语义路由」——① `user_clis.abilities` **能力描述**（用户语言：关键词 + 「用户的说法→命令路径」映射，允许多行、≤400 字，局部更新不清空）；② `agent_brief()` 双形态（**有描述 → 能力路由卡 / 无描述 → 回退 M4c 命令名简报**，存量零影响）+ `ability_examples()` 生成 **few-shot 对照示例**（每条 argv **逐条过只读闸**，指向未放行命令的映射直接丢弃）；③ AI 草稿：`prompts.yml` 顶层 `ability_writer` 独立段 + `POST /api/cli/ability_draft`（**只返回草稿不落库**，人确认后保存）+ CLI 表单「✨ 按只读清单生成草稿」；④ 卡片新增能力描述区块（未填时明确提示「AI 只能看到命令名清单，自然语言容易唤不醒」）；⑤ 提示词纪律补「示例只是格式示范，实际命令以区块为准」+「口语需求先对关键词、别让用户猜命令」。**验收（修正口径后，2026-09-13）**：自然语言探针组 **10 正全中（首调命中 10/10、首调可执行 10/10；同口径复跑 9/10）+ 3 反例全过**；**同题对照组（探针 CLI 不填能力描述 = M4c 命令名形态）仅 2/10**——8 条探针连工具都没调，证明收益来自能力描述 + few-shot 示例。单测 43/43、前端逻辑 56/56。**踩坑与修正**：`weread book info` / `reviews list` / `book progress` / `discover similar` **必须带数字 bookId**（`--help` 实测），而口语提问只有书名 → 能力描述须写成**两步链**（`书评怎么样→book resolve 再 reviews list`），示例生成器取链上第一步，否则模型要么调报错命令、要么绕去网搜（用户账号的能力描述已按此改写）。规划书与实施记录见 `docs/v2.0/M5工具智能路由.md`；
- ✅ **v2.0 M5b 工具检索路由（CLI 部分已完成）**：M5a 的注入是「**预算分配器**」（所有工具平铺、装不下就降级，工具一多每份额度变小 → 精度随规模下降），M5b 换成「**相关性选择器**」——① **统一能力目录** `tools/capability_pool.py`：`CapabilityEntry` 是唯一契约（source/ref/keywords/abilities/invoke_hint），CLI 从 `user_clis` **派生（零迁移）**、API/MCP 各写适配器，路由与注入**不感知来源**；向量存独立 collection `tool_route_{account_id}`（与 M3 知识库隔离），阈值用 **pool_version + 惰性重建** 失效（配置写入 0.18s，不因加载 bge 冻界面）；② **路由器** `tools/tool_router.py`：工具 ≤4 个走**快路径**（与 M5a 逐字一致）；超过则「bge 稠密 + 关键词词法 → RRF 融合」选工具，命中给**完整能力卡**、次要给**紧凑行**、其余只留**一行名称**（工具永不从提示词里隐身），few-shot 示例只给命中工具；**不确定就整段回退全量**（不猜）；③ **阈值是实测标定的相对信号**：绝对余弦阈值被证伪（中文短问句 top1~top3 只差 0.02~0.06、绝对值全在 0.53~0.70，「>0.35 算命中」会全部命中），改用**词法命中 ≥2 或向量 z ≥2.0**；④ **两个实测修掉的真 bug**：RRF 会把「无信号的排序器」当有效输入（词法全 0 时按插入序给分，顶掉向量正确的 top1）、示例过滤条件写错（示例行不以 `- ` 开头 → 过滤没生效）；⑤ 连带根因修复：新增表的外键让「删账号」撞约束 → 加 `purge_account()`（自省表结构，以后加表不用改）。**API / MCP 适配器（M5b-3/4）本轮未做**——目录层与路由器已按来源无关实现并有混合来源单测。
- 🔧 **修复（2026-09-16）：聊天框敲 `/` 唤不出技能菜单**。根因是 `static/index.html` 的 `setup()` 返回表**漏了 4 个事件处理函数**（`skOnInput`/`skOnKey`/`skillPick`/`skillUnpick`）→ 模板把它们绑成 `undefined`：不报错、不崩、界面渲染正常（`v-if`/`v-for` 用的状态变量都在），**只有交互静默失效**。修法：补进 return + 新增**静态不变量用例** `tests/frontend_setup_exports_check.py`（模板引用 ⊆ setup 导出，一次覆盖整类问题）。验证：真机 CDP（无头 Chrome + `websockets` 直连 DevTools）走完 **敲 `/` → 菜单出现 2 个技能 → 真鼠标点击 → 技能 pill 出现**全链路。
- ✅ **v2.0 收尾（2026-09-16）：登录卡 / 注册卡分离**。旧版是**混合卡**——前端 `doLogin()` 遇到 401（账号不存在）就直接调 `/api/register` 建号再登录，于是**打错用户名 = 静默建出一个能登进去的空账号**（用户实际误建过「尼姑喵喵」，与正确账号「尼古喵喵」一字之差）。改法：① 卡片顶部「登录 / 注册」两个 tab，**两张卡独立**；② 登录卡只留用户名 + 密码（拿掉了原来混在里面、其实是给注册用的「账号类型」字段），注册卡是用户名 + 密码 + **确认密码** + 账号类型 + **「即将创建：xxx（个人账号）」实时回显**（把不可逆操作的后果提前摆到眼前，不打断流程）；③ 移除"登录失败即注册"的整条路径，**登录永不建号**；④ 注册成功**不自动登录**，回登录卡并提示「账号已创建，请用刚设置的密码登录」（拍板口径：更稳）；⑤ 后端语义拆细以支撑引导：**账号不存在 → 404「账号不存在」/ 密码错误 → 401「密码错误」**（原统一 401「用户名或密码错误」会把「用户名打错」误导成"密码打错"），CLI `dspro login` 同步指路 `--register`。**顺带修掉一个同批发现的层级 bug**：全局提示条 `.toast` 原挂在 `<template v-else>`（登录后才渲染的分支）里 → **注册成功的提示在登录页根本看不见**（真机探针查 `.toast` 文本时暴露），已挪到 `#app` 根层且必须排在 `v-if="!token"` 之前（夹在 `v-if` 与 `v-else` 之间会编译报错）。验证：`tests/auth_split_e2e.py` **36/36**（核心断言 = 用打错的用户名登录后**查库确认没有新账号**）+ 真机 CDP `scripts/cdp_auth_probe.py` **21/21**（真实键鼠点出两张卡、断言账号真的建/真的没建）+ CLI 回归 **38/38** + 前端不变量 3/3 + `m4a_frontend_logic.js` 56/56。**代价**：账号不存在与密码错误可区分，理论上可枚举账号（本地单机应用，无此风险，用户拍板选择区分）。
- 📋 **v2.0 后续规划**：用户画像沉淀、检索会话级计数器、`build_request_context` 完整落地（SOUL/MEMORY 注入）、价格 MCP 自动化等——见 `docs/v2.0/v2.0方案.md` + `docs/v2.0/AstrBot_v2.0对比借鉴.md`。**M5b 的 MCP / API 适配器（原 M5b-3/4）已拍板推迟到 v3.0。**

> 改造路线回顾：M1/M1.5 用「提示词 + 工具 → 字典」装配三个子 Agent；M2 Web 化 + 鉴权；M3 前端卡片与导出；M4 先做用户数据归属权改造（读写分离），再落地 Digest 订阅/引擎/调度三阶段。

---

## 九、技术栈

`deepagents` · `langchain` / `langchain-core` / `langchain-openai` · `langgraph` · `fastapi` / `uvicorn`（Web 服务）· `apscheduler`（M4 定时调度）· `mysql-connector-python`(legacy) · `tavily-python` · `ragflow-sdk`(v2.0) · `pydantic` · `pyyaml` · `markdown` + `pywin32`（Word 转 PDF）· 内置 `sqlite3`（个人库/账号/Digest 存储）· 前端 **Vue 3**（免构建全球构建版）

---

## 十、里程碑与开发顺序

以 `docs/v1.0/里程碑计划.md` 为权威准绳，概览如下：

| 里程碑 | 范围 | 状态 |
| --- | --- | --- |
| **M1** | 公司侧：提示词重做 + MySQL 三表建库 + 样例 + CLI 竞品简报实测 | ✅ 已完成 |
| **M1.5** | 个人侧：SQLite 个人库 + 个人子 Agent + 路由 + 性价比分析实测 | ✅ 已完成 |
| **M2** | Web 化：FastAPI 服务 + 登录鉴权/角色分流 + Vue3 双工作台 + WebSocket 实时进度 | ✅ 已完成 |
| **M3** | 简报卡片渲染 / 左栏真实竞品清单 / MD·PDF 导出 | ✅ 已完成 |
| **M4** | Digest 定期推送：订阅管理 + Tavily/HN 检索引擎 + LLM 三关筛选 + APScheduler 调度 + 报告列表页 | ✅ 已完成（RAGFlow/用户画像仍待后续） |
| **v2.0-M1** | 上下文工程化：会话 SQLite 持久化 + 多会话管理 + token 预算防爆窗 | ✅ 已完成（见 `docs/v2.0/M1上下文工程化.md`） |
| **v2.0-M2** | 请求装配管线化：agent 静态化 + 人格/记忆 invoke 注入（零重建）| ✅ 已完成（见 `docs/v2.0/M2请求装配管线化.md`） |
| **v2.0-M3** | RAG 知识库：自建 Chroma 每用户一库 + 清洗评估入库 + 快/全双模式检索 | ✅ 已完成（见 `docs/v2.0/M3RAG知识库搭建与使用.md`） |
| **v2.0-M4** | 技能与工具：**M4a 技能**（SKILL.md + `/` 选单 + 前缀注入）✅ / **M4b CLI 面板**（白名单沙箱命令 + 六道闸 + 只读口径）✅ / **M4c 自定义 CLI 接入**（配置导向：自己填命令与只读清单 + 本机检测 + 只读子命令代跑）✅ | ✅ 三段全部完成并端到端实测（M4a 30/30+50/50、M4b 51/51、M4c 105/105），见 `docs/v2.0/M4技能与工具.md` |
| **v2.0-M5b** | 工具检索路由：**统一能力目录**（`CapabilityEntry` 契约 + `tool_capabilities`/`tool_pool_meta` + CLI/API/MCP 三适配器 + 独立向量 collection + pool_version 惰性失效）+ **路由器**（快路径 / 混合检索 RRF / 词法与 z 分数两级置信 / 完整卡·紧凑行·仅名称三形态 / 未命中不隐身 / 全链兜底回退全量）| ✅ **CLI 部分已完成并验证**（单测 62/62 + 34/34；回归 M4c 109/109、M5a 43/43；8 工具标定 10/10 命中、0 猜错、注入量 1644/2499 字；真机加压探针 8/8 正例 + 2/2 反例）；**API / MCP 适配器（原 M5b-3/4）按拍板推迟到 v3.0**，见 `docs/v2.0/M5b工具检索路由.md` |
| **v2.0 收尾** | 登录界面：**登录卡 / 注册卡分离**（后端登录语义拆细：账号不存在 → 404 / 密码错误 → 401）+ 全局提示条挂载层级修复（注册成功提示在登录页看不见）+ `/` 技能菜单静默失效修复 | ✅ 已完成（`tests/auth_split_e2e.py` 36/36、真机 CDP 21/21、CLI 回归 38/38、前端不变量 3/3、`m4a_frontend_logic.js` 56/56） |
| **v2.0-M5a** | 工具智能路由：`user_clis.abilities` 能力描述（用户语言关键词 + 「说法→命令路径」映射，**需要 ID 的命令写两步链**）+ `agent_brief` 双形态能力路由卡（私有数据优先指令）+ few-shot 映射示例（逐条过只读闸）+ AI 草稿按钮 | ✅ 已完成并端到端验证（探针组 10/10、复跑 9/10、反例 3/3；**同题对照组 M4c 命令名形态仅 2/10**；真实账号 + 真实 weread 15/15），见 `docs/v2.0/M5工具智能路由.md`（检索式路由已由 M5b 落地） |

> 每完成一个里程碑，对应在 `docs/` 下增补 `M{n}_*实现设计.md` 记录改动与实测，保持文档与代码同步。
