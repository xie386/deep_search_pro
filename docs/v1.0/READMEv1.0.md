# deep-search-pro

> 一个面向初学者的 **多智能体（Multi-Agent）深度搜索** 框架项目，基于 `deepagents` + `LangChain` + `FastAPI` 构建，已演进为本地纯个人使用的 AI 情报助手「**智选情报官**」。
> 当前状态：**v1.0 已定版**——M1 / M1.5 / M2 / M3 / M4(Digest 定期推送) 全部完成并实测通过；增强功能（思考过程可视化、定制助手、记忆画像、语音朗读、背景图库、周报时效修复）均已落地。**二期规划见 `docs/v2.0方案.md`**；里程碑回顾见 `docs/里程碑计划.md`。

---

## 一、项目简介

`deep-search-pro` 的目标是让你用最少的概念，理解一个「能联网搜索、能查数据库、能查知识库」的智能体系统是怎么组织起来的。

它的核心理念是 **编排（Orchestration）**：

- 有一个 **主智能体（Main Agent）** 充当「团队负责人」，负责理解用户问题、拆解任务、并把子任务分派出去；
- 有若干个 **子智能体（Sub Agents）** 各司其职，例如：
  - 🔍 **网络搜索助手**（已装配）：调用 Tavily 搜索公开网络信息；
  - 🗄️ **数据库查询助手**（已装配）：读取当前登录公司自己的资料（SQLite，按账号隔离：公司信息 / 我司产品 / 关注竞品）；
  - 👤 **个人情报助手**（已装配，M1.5）：读取本地 SQLite 个人库，做消费选购的性价比分析与兴趣快讯；
  - 📚 **RAGFlow 助手**：对接 RAGFlow 知识库做私有文档检索（**v2.0 接入，当前未装配**）。
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
| 知识库检索 | 预留 RAGFlow 知识库接入（v2.0 接入，当前未装配） |
| 可观测性 | `ToolMonitor` 单例在每次工具调用时上报进度，支持控制台 / WebSocket 双通道 |
| 思考过程可视化 | 自定义 `ReasoningChatOpenAI`（继承 ChatOpenAI）透传模型 `reasoning_content`，回答后推前端右栏折叠区（langchain 丢弃非标准字段，官方建议 provider 子类） |
| 背景图持久化 | `pic/{username}/` 目录存储 + 上传/列表/删除 API + 前端图库网格选择 + 透明度调节（按账号隔离，刷新不丢） |
| 定制助手·模型选型 | `llm_providers` 表（按账号）存模型名/base_url/api_key，前端可选启用；启用后该账号对话走自定义模型，默认 .env 免费模型 |
| 定制助手·助手人格 | `agents_docs/{username}/SOUL.md` 按账号存储人格，注入 system_prompt（对话+周报措辞均体现）；`soul_writer` 独立段 AI 生成人格（如「活泼女仆」「稳重管家」）一键导入 |
| 周报时效性保障 | 代码层按 `published_date` 严格剔除超窗旧闻（strict_days）+ 查询词泛用化（去修饰词）+ 中英双语检索合并（bilingual）+ 订阅级检索语言可选（zh/en/both） |
| 语音朗读与自定义音色 | 联动 `voice_test` 小项目（Qwen3-TTS 音色克隆）：定制页上传参考音频+台词克隆音色包（pkl，按账号隔离），AI 回复框「🔊 朗读」合成语音播放；模型仅在用户使用时临时启动（subprocess 跑脚本），用完即释放显存（RTX 3050 4GB 约束） |
| Web 服务与实时监控 | FastAPI + WebSocket：登录鉴权、账号角色分流（公司/个人双工作台），右栏实时滚动工具调用进度（M2 已装配） |
| 用户数据自主管理 | `/api/me/*` 全套 CRUD：公司资料/我司产品/关注竞品、个人兴趣/关注/收藏，按账号隔离；Agent 工具只读消费（M4 前置已装配） |
| Digest 定期推送 | 订阅管理 + Tavily/HN 检索 + LLM 三关筛选生成周报 + APScheduler 定时调度 + WS 实时提醒 + 报告列表页下载（M4 已装配） |
| 异步隔离 | 基于 `ContextVar` 的请求级上下文隔离，避免多用户「串台」 |
| 配置外置 | 所有提示词（Prompt）集中在 `prompt/prompts.yml`，改提示词无需动代码 |

---

## 三、项目结构

```text
deep_search_pro/
├── main.py                      # 程序入口（CLI 交互版：已装配主 Agent + 3 个子 Agent，含多轮记忆）
├── pyproject.toml              # 依赖清单（uv 管理，已配置清华镜像源）
├── .python-version             # 锁定 Python 3.11
├── .gitignore                  # 忽略 .env / __pycache__ / data 等
├── IDEA.md                     # 项目定位：初学框架，后续改造为成熟应用
│
├── agent/                      # ★ 智能体核心
│   ├── llm.py                  #   初始化大模型（从 .env 读取 LLM_MODEL_MAX）
│   ├── reasoning_model.py     #   ReasoningChatOpenAI：继承 ChatOpenAI，透传第三方 reasoning_content（A+ 方案）
│   ├── thinking_capture.py     #   思考捕获：invoke 后遍历消息提取 reasoning_content 推前端
│   ├── prompts.py              #   加载 prompt/prompts.yml 为字典，供主/子 Agent 使用
│   └── subagents/
│       ├── network_search_agent.py   # 网络搜索子 Agent 装配（范本）
│       ├── db_agent.py                # 数据库查询子 Agent 装配（挂载 MySQL 三件套）
│       ├── personal_agent.py          # 个人情报子 Agent 装配（挂载 SQLite 个人库工具）
│       └── digest_agent.py            # 定期情报周报子 Agent 装配（M4，筛选撰写周报）
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
├── front/                     # 前端 SPA（免构建 Vue 3）
│   ├── index.html              #   登录页 + 多页签工作台(工作台/信息/AI助手/报告/定制助手) + WebSocket 实时监控 + 思考过程折叠区 + 背景图库
│   └── vendor/vue.global.prod.js     # 本地 Vue 3 全局构建
│
├── tests/                      # 测试与调试脚本（不放项目根）
│   ├── m2_debug_run.py 等      #   各里程碑冒烟/回归脚本（详见 tests/README.md）
│
├── scripts/                    # 一次性运维脚本
│   └── import_mysql_to_sqlite.py     # 旧 MySQL 数据迁移到 SQLite
│
├── data/                       # 运行时生成的本地数据
│   └── personal.db             #   个人库 SQLite 文件（首次调用自动建库）
│
├── output/                     # 导出产物（按用户目录隔离）
│   └── {username}/             #   简报 PDF 与 Digest 周报 md
│
├── docs/                       # 设计文档与里程碑
│   ├── 项目预期功能和预期UI设计.md   # 产品定位 / 功能清单 / UI 设计 / 里程碑
│   ├── 里程碑计划.md                # 开发顺序准绳（M1✅ M1.5✅ M2✅ M3✅ M4✅）
│   ├── M1_公司侧实现设计.md          # M1 实现与验证记录
│   ├── M1.5_个人侧实现设计.md        # M1.5 实现与验证记录
│   ├── M2_Web服务与鉴权实现设计.md   # M2 实现与验证记录（含架构图与踩坑）
│   ├── M3_前端体验实现设计.md        # M3 实现与验证记录
│   ├── M4_定期推送与订阅设计.md      # M4 设计书（含前置改造第〇章）
│   ├── M4_Digest实现记录.md          # M4 a/b/c 三阶段实现与实测记录
│   └── sql/company_schema.sql       # 公司侧 MySQL 三表建库 + 样例数据（legacy，数据已迁 SQLite）
│
└── utils/                      # 通用工具
    ├── path_utils.py           #   统一的文件路径解析（含虚拟路径清洗 / 会话隔离 / 防嵌套）
    └── word_converter.py       #   Markdown → PDF 转换（依赖 Word COM，仅 Windows）
```

---

## 四、阅读指南（建议顺序）

这个项目是**按「配置 → 模型 → 工具 → 装配 → 可观测性」的难度递进**组织的。建议按下面的顺序读，每读一个文件都先问自己一个问题：

| 顺序 | 文件 | 看什么 / 思考题 |
| --- | --- | --- |
| 1️⃣ | `prompt/prompts.yml` | **先读「大脑」**。主 Agent 和子 Agent 的 `system_prompt` 写在这里。理解：一个 Agent 的「人设」和「能力边界」是怎么用 YAML 描述出来的？为什么要把提示词放在配置里而不是写死在代码里？ |
| 2️⃣ | `agent/prompts.py` | 配置是怎么被代码读进来的？注意 `yaml.safe_load` 比 `load` 安全（防执行注入）。输出 `main_agent_content` / `sub_agents_content` 两个字典。 |
| 3️⃣ | `agent/llm.py` | 大模型是怎么初始化的？`init_chat_model` + `model_provider="openai"` 说明：本项目通过 OpenAI 兼容接口接入模型，`LLM_MODEL_MAX` 在 `.env` 里配置。 |
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
| 1️⃣4️⃣ | `front/index.html` | **前端 SPA**（M2/M3，免构建 Vue 3）：登录页按 role 分流到公司/个人双工作台；WebSocket 收到 `monitor_event` 渲染右栏进度；来源标记 🔍/🗄️/📚 高亮。思考：为什么选全球构建版而不是工程化打包？（M3）回答如何被 `splitSections()` 按 ①~⑤ 序号切成简报卡片分块？组件美化来自 uiverse 库（003 气泡按钮 / 004 硬阴影输入框 / 001 3D 方块加载器），体会「snippet 重配色适配主题」的方法。后续增强：浅绿/白/黄三色系重配色、AI 回复框深底亮字对比、右栏「🤔 模型思考过程」折叠区、顶栏「🎨 背景」图库面板。 |
| 1️⃣5️⃣ | `tests/` | **测试与调试脚本**：与项目代码分离存放。`m3_smoketest.py` 是接口层回归（登录→竞品清单→导出下载）；注意脚本须基于 `__file__` 解析项目根注入 sys.path、并豁免本机 HTTP_PROXY 才能访问 localhost。 |
| 1️⃣6️⃣ | `api/me_user_data.py` | **用户数据自主管理**（M4 前置）：`/api/me/*` 全套 CRUD，全部按 session 的 account_id 隔离。思考：为什么「增删改走 REST、Agent 工具只读」的读写分离能同时保住数据安全与 Agent 可靠性？ |
| 1️⃣7️⃣ | `agent/digest_engine.py` + `agent/subagents/digest_agent.py` + `api/digest_scheduler.py` | **Digest 定期推送**（M4，回归主智能体编排）：① 为什么 digest 不自己写检索代码、而是让主 Agent 调度【网络搜索助手】？② 查询批次为何硬上限 ≤8？③ 候选不足 15 条时筛选门槛为何放宽（禁止空周报）？④ APScheduler 为何内嵌 FastAPI 进程而非外部 cron？⑤（时效性修复）`build_batches` 为何弃用「关键词+修饰词」拼接、改用关键词原文？——实测 Tavily 对带修饰词的中文长查询相关性崩坏（返回无关金融/加密货币新闻），且修饰词无法跨领域通用。 |
| 1️⃣8️⃣ | `docs/` | **设计文档与里程碑**：`M1_*~M4_*` 是各里程碑的实现与实测记录；`里程碑计划.md` 是后续开发顺序的准绳，开新功能前先对齐里程碑。 |
| 1️⃣9️⃣ | `agent/reasoning_model.py` + `agent/thinking_capture.py` + `api/monitor.py` | **思考过程可视化（A+ 方案）**：langchain-openai 明确丢弃第三方 base_url 的非标准字段 `reasoning_content`（源码 `_convert_delta_to_message_chunk` 只读 content/function_call/tool_calls，文档明言「Use a provider-specific subclass」）。方案：自定义 `ReasoningChatOpenAI(BaseChatOpenAI)` 重写 `_create_chat_result`（非流式）把 `reasoning_content` 塞进 `additional_kwargs`；`thinking_capture.py` 在 `agent.invoke` 完成后遍历 `result["messages"]` 提取各轮 AIMessage 的思考推前端。思考：为什么不用 `on_chat_model_end` 回调做实时？（deepagents 底层 `langchain.agents.create_agent` 的模型节点 `_execute_model_sync` 只 `model_.invoke(messages)` 不传 config，回调不触发；`model.with_config(callbacks=...)` 返回 RunnableBinding 会被 resolve_model 当字符串报错）。为什么这样比旧「双请求旁路」好？（单请求省 token + 覆盖所有轮次，旁路只覆盖首轮）。 |
| 2️⃣0️⃣ | `api/customize.py` + `tools/schema_personal.py`(llm_providers 表) | **定制助手后端**：五组 API 按账号隔离——① 背景图 `/api/bg/*`（multipart 上传存 `pic/{username}/`、列表、删除，uuid 前缀防重名）；② 模型选型 `/api/llm/providers`（CRUD + activate，**api_key 列表时掩码 `****后4位`，编辑留空=不改**）；③ 人格 `/api/soul`（默认 `SOUL.md` + 多人格集 `SOUL/{name}.md`，`ACTIVE_SOUL` 文件记录当前激活，list/create/delete/set_active/named 路由）；④ 记忆画像 `/api/memory`（读写 `MEMORY.md` + 一次性初始化 `/api/memory/init`）；⑤ 批量导入 `/api/me/*/batch`（竞品/收藏剪贴板粘贴，一行一个，去空去重 ≤50 条）。思考：`_get_agent_for()` 为什么「有人格/自定义模型/记忆画像就按账号缓存独立 agent」而不是复用全局 AGENT？（全局 AGENT 的 system_prompt 在构建时固定，无法注入动态内容；缓存 key 含 soul+memory 哈希，人格/记忆变化自动重建） |
| 2️⃣1️⃣ | `agent/digest_engine.py`(任务指令) + `tools/tavily_tool.py`(strict_days/bilingual) | **周报时效性修复**：① `strict_days=True` 在代码层解析每条 `published_date`（RFC822 格式）剔除超窗旧闻——为什么不能只靠模型自觉？（Tavily 的 days 只是硬上限不保证排序时效，旧闻常排前面）② `bilingual=True` 用 LLM 把中文关键词翻译成英文做中英双语检索合并——为什么单中文查询会漏？（实测 Tavily 对中文实体词相关性差，英文召回更精准）③ 订阅级 `lang` 字段（zh/en/both）控制是否双语。思考：无日期的结果为什么「宁可保留不误杀」？ |
| 2️⃣2️⃣ | `tools/readtofile.py` + `tools/writetofile.py` | **agents_docs 文档读写工具**：主智能体可读/写 `agents_docs/` 下的 .md/.txt（路径穿越防护：归一化后强制前缀校验；扩展名白名单）。端到端验证：`tests/tool_agent_e2e_test.py` 把工具挂到全局 AGENT 后由 Agent 实际完成写文件+读回任务。思考：为什么这类「越权敏感」工具要同时在路径与扩展名两层设防？ |
| 2️⃣3️⃣ | `prompt/prompts.yml`(soul_writer + memory_initializer 段) | **人格/画像撰写 AI**：顶层独立段（不进 main_agent/sub_agents，不挂工具）——`soul_writer` 按用户一句话需求（如「活泼女仆」「稳重管家」）生成 200-500 字人格提示词（称呼/说话风格/情绪/周报风格/行为准则五维）；`memory_initializer` 按用户系统已有资料（兴趣/收藏/竞品/公司信息）提炼 150-400 字初始画像（缺的维度如实标注「暂无」）。思考：为什么这两个 AI 必须独立于主 Agent 体系？ |
| 2️⃣4️⃣ | `agents_docs/{user}/MEMORY.md` + `agent/digest_engine.py`(画像注入) | **记忆白盒化（用户画像/公司形象）**：① 账号注册即生成空 MEMORY.md（`<!-- memory_init: pending -->` 标记）；② 主 Agent 的 system_prompt 注入【你的用户记忆画像】区块 + 强制记忆维护指令——用户表达稳定偏好时必须调用【写入文档工具】更新 MEMORY.md（禁止只口头确认）；③ `_get_agent_for` 缓存 key 含 memory 哈希，画像变化自动重建 agent；④ chat 路由对比调用前后 MEMORY.md 内容，变化则回复末尾附「📝 已更新你的记忆画像」提示；⑤ digest 引擎读 USER PROFILE 段注入「情报侧重」（价格敏感→降价/优惠类优先）；⑥ 前端定制页可视化编辑 + 一次性初始化按钮。**踩坑**：deepagents 内置 filesystem 工具（read_file/write_file）与自定义文档工具重名竞争，模型优先选内置导致绕过 agents_docs 约束——用 `_ToolExclusionMiddleware(excluded=...)` 排除内置工具。思考：为什么「排除内置工具」比「改名自定义工具」更优？（保留 agents_docs 安全边界） |
| 2️⃣5️⃣ | `api/voice_tts.py` + `voice_test`(外部小项目) | **语音朗读与自定义音色**：① 为什么不能把 voice_test 的代码复制进主项目？（环境冲突：voice_test=py3.12+torch+qwen_tts，主项目=py3.11+langchain，numpy .pyd 不兼容）→ 用 **subprocess 执行 voice_test 的 venv python 跑脚本**，环境天然隔离；② pkl 按账号隔离：`--name {username}_{包名}` 前缀，voice_test 脚本零改动；③ 显存约束（RTX 3050 4GB）：模型**只在用户使用时临时启动**（subprocess 进程退出即释放显存），不做常驻服务；④ 每次合成要重新加载模型（30-60s），常驻化方案见 `docs/v2.0方案.md`；⑤ **踩坑**：subprocess 必须清 PYTHONPATH（Hermes 注入的 hermes-agent venv 会让 voice_test 的 numpy 加载崩）；克隆用后即删参考音频（只留 pkl 资产）。思考：为什么「名字前缀隔离」比「子目录隔离」更省事？（voice_test 脚本读固定 PACK_DIR，不支持子目录） |

> 💡 学习心法：Agent 系统 = **模型（llm.py）** + **提示词（prompts.yml）** + **工具（tools/）** + **编排（subagents/ + main.py）** + **可观测性（api/）**。把这条主线记牢，再看任何 Agent 框架都不慌。

---

## 五、环境要求

- **Python 3.11**（项目通过 `.python-version` 锁定）
- 包管理器：**uv**（推荐，已配置清华镜像源，安装更快）
- 操作系统：Windows / macOS / Linux 均可；`utils/word_converter.py` 依赖 Windows 上的 Word COM，仅转换 PDF 功能受限

---

## 六、快速开始

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
| `RAGFLOW_*` | RAGFlow 知识库接入配置（启用 RAG 助手） | ⚠️ 用到才填 |

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

> 注意：若本机存在其他名为 `agent` 的 Python 包（如全局 venv 中的同名包），`api/server.py` 已在顶部做了 sys.path 防御（项目根优先 + 剔除冲突路径）；`main.py` 直接在项目根运行不受影响。开发顺序以 `docs/里程碑计划.md` 为准绳。

---

## 七、项目现状 & 改造方向

根据 `IDEA.md`，本项目定位是**初学框架**，已演进为本地个人使用的 AI 情报助手「智选情报官」。开发以 `docs/里程碑计划.md` 为准绳，**M1 / M1.5 / M2 / M3 / M4 已全部完成并实测通过**。当前状态：

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
- 📋 **v2.0 规划**：RAGFlow 知识库接入、用户画像沉淀、价格 MCP 自动化、语音合成常驻化等——见 `docs/v2.0方案.md`。

> 改造路线回顾：M1/M1.5 用「提示词 + 工具 → 字典」装配三个子 Agent；M2 Web 化 + 鉴权；M3 前端卡片与导出；M4 先做用户数据归属权改造（读写分离），再落地 Digest 订阅/引擎/调度三阶段。

---

## 八、技术栈

`deepagents` · `langchain` / `langchain-core` / `langchain-openai` · `langgraph` · `fastapi` / `uvicorn`（Web 服务）· `apscheduler`（M4 定时调度）· `mysql-connector-python`(legacy) · `tavily-python` · `ragflow-sdk`(v2.0) · `pydantic` · `pyyaml` · `markdown` + `pywin32`（Word 转 PDF）· 内置 `sqlite3`（个人库/账号/Digest 存储）· 前端 **Vue 3**（免构建全球构建版）

---

## 九、里程碑与开发顺序

以 `docs/里程碑计划.md` 为权威准绳，概览如下：

| 里程碑 | 范围 | 状态 |
| --- | --- | --- |
| **M1** | 公司侧：提示词重做 + MySQL 三表建库 + 样例 + CLI 竞品简报实测 | ✅ 已完成 |
| **M1.5** | 个人侧：SQLite 个人库 + 个人子 Agent + 路由 + 性价比分析实测 | ✅ 已完成 |
| **M2** | Web 化：FastAPI 服务 + 登录鉴权/角色分流 + Vue3 双工作台 + WebSocket 实时进度 | ✅ 已完成 |
| **M3** | 简报卡片渲染 / 左栏真实竞品清单 / MD·PDF 导出 | ✅ 已完成 |
| **M4** | Digest 定期推送：订阅管理 + Tavily/HN 检索引擎 + LLM 三关筛选 + APScheduler 调度 + 报告列表页 | ✅ 已完成（RAGFlow/用户画像仍待后续） |

> 每完成一个里程碑，对应在 `docs/` 下增补 `M{n}_*实现设计.md` 记录改动与实测，保持文档与代码同步。
