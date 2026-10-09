# deep-search-pro

> 一个面向初学者的 **多智能体（Multi-Agent）深度搜索** 框架项目，基于 `deepagents` + `LangChain` + `FastAPI` 构建，已演进为本地纯个人使用的 AI 情报助手「**智选情报官**」。
> 当前状态：**v1.0 已定版 · v2.0 已收官**（M1~M5b 全部完成，仅 **MCP / API 适配器**按拍板推迟到 v3.0）——**M1** 上下文工程化（多会话管理 + token 预算防爆窗）；**M2** 请求装配管线化（agent 静态化、人格/记忆 invoke 期注入零重建）；**M3** RAG 知识库（自建 Chroma、评估清洗入库、快/全双模式检索）；**M4a** 技能（SKILL.md + `/` 选单 + 前缀注入）；**M4b** CLI 沙箱面板（白名单命令 + 六道闸 + 只读口径）；**M4c** 自定义 CLI 接入（配置导向：自己填命令与只读清单 + 本机检测 + 只读子命令代跑）；**M4 收尾** CLI 版 `dspro` / 工作台时钟+天气卡 / 教程只读阅读页；**M5a** 工具能力语义路由（能力描述 + 能力路由卡 + few-shot 示例）；**M5b** 工具检索路由（统一能力目录 + 按提问相关性选工具）；**收尾** 登录卡 / 注册卡分离。v1.0 功能（多智能体、思考可视化、定制助手、记忆画像、语音朗读、周报时效修复）全部实测通过。**v2.0 全部更新一览（每个里程碑改了什么、关键设计、实测数字）见 [八、项目现状 & 改造方向](#八项目现状--改造方向) 开头的总览表**；设计文档见 `docs/v2.0/`，v1.0 里程碑回顾见 `docs/v1.0/`。
>
> **本轮新增（2026-09-16）**：v2.0 **M5b 工具检索路由（CLI 部分）**已实现并验证——**M5b-1 统一能力目录**（`CapabilityEntry` 契约 + `tool_capabilities`/`tool_pool_meta` 两表 + CLI/API/MCP 三来源适配器 + 独立向量 collection）+ **M5b-2 路由器**（`tools/tool_router.py`：快路径 / 混合检索 / 相对置信判定 / 三形态分层注入 / 全链兜底）。单测 62/62 + 34/34，回归 M4c 109/109、M5a 43/43；8 工具标定下命中判定 10/10、注入量 1644 字（全量 2499 字）。详见 `docs/v2.0/M5b工具检索路由.md`。
>
> **v2.0 收尾（2026-09-16）**：登录界面 **登录卡 / 注册卡分离**——旧版是「登录注册混合卡」（输入的用户名不存在即自动注册），**打错一个字就会静默建出错误账号**（实际误建过「尼姑喵喵」）。现在：注册独立成卡（用户名 + 密码 + **确认密码** + 账号类型 + 「即将创建：xxx（个人账号）」实时回显），**登录失败绝不建号**，账号不存在时给出「去注册 →」引导；后端登录语义随之拆细（**账号不存在 → 404「账号不存在」/ 密码错误 → 401「密码错误」**，CLI 侧同步指路 `--register`）。顺带修掉一个同批发现的层级 bug：全局提示条原挂在 `<template v-else>`（登录后才渲染的分支）里，**导致注册成功的提示在登录页根本看不见**。验证：`tests/testclient/auth_split_e2e.py` 36/36、真机 CDP `scripts/cdp_auth_probe.py` 21/21、CLI 回归 `cli_dspro_e2e.py` 38/38、前端不变量 3/3 + `m4a_frontend_logic.js` 56/56。
> 
> **v2.5（2026-09-24）修复与优化**：① **CLI 能力描述撰写改造**——原先只把「命令名清单」喂给 AI，它只能**望文生义**（实测 13 条映射 3 条错，且**全错在「哪些命令需要 ID」这类判断上**，错的映射会被抽成 few-shot 教 Agent 去调必然报参数错的命令）；现改为**证据分层**：先读用户填的官方文档（README）当语义依据，再逐条跑 `<bin> <cmd> --help`（**走沙箱 + 只读闸，口径不放宽**）拿真值表做校验与**生成后审计**，文档没覆盖的命令用 help 原文 / 用户粘贴文本兜底 —— 实测危险警告 **3 → 0**，配套 **24 项离线回归**（含 SSRF 守卫与审计四条规则）。② 聊天「正文跑到思考里、刷新后一屏空白卡片」修复（reasoning 回填 + 落库过滤 + 前端跳过空气泡）。③ 桌面版修复（记住登录的两半、`.ico` 必须 BMP 帧、下载收口、进程/端口归属）。④ **前端表单视觉体系**：借 uiverse **014** 号的「色块跟踪」理念（色块滑过来吞掉你正在操作的那一段 + 反相），把**个人信息 / 报告订阅 / 定制助手**三处纯色表单统一成 **基色 `#FFF81F` + 跟踪色块 `#88F529`** 的纸片卡，输入框组件换成 **004**（硬偏移投影 + 聚焦时投影收掉、底部色带展开，**去掉原组件的 span 包裹层**，一次替换全站 **122 处** `.m-input`）；配套 `scripts/build_form_preview.js` 出静态预览（class 自检保证预览用的是真实 CSS）。⑤ **周报应用内阅读**：报告列表新增「阅读」入口——**前端 fetch 与「下载」完全相同的 `/api/download` URL**，把正文交给教程页那套真实 `tutRender` 渲染，进 `.tutorial-view` 同构的阅读页、左上角「← 返回」回应用（**后端零改动**，阅读与下载同一条鉴权/越权闸；越权 `../` 路径 400、假 token 401 均实测）。**紧跟的空白页 bug**：返回写成 `go('report')` 而报告页视图名是**复数** `reports` → 跳到不存在的视图 → 整页空白、点任意导航才恢复；已修正并把"`go()` 目标必须是真实视图"写成**静态不变量**（前端静态检查 3 → 5 项）。⑥ **桌面端「改了看不到」= WebView2 持久化缓存喂旧 HTML**（报障：同一次前端改动网页端可见、桌面端连重启都看不到；证据 = 缓存文件里有 015 样式却没有新按钮，且窗口加载时刻晚于文件写入 9 分钟）→ 两道保险：**后端给 HTML 入口打 `Cache-Control: no-store`**（`/` 与 `*.html`；图片/API 不受影响，配 `tests/unit/test_spa_cache_headers.py` 5 项含"不过度应用"反向断言）+ **桌面壳加载 URL 加一次性 nonce**（`&_=<时间戳>`；前端 `/[?&]desktop=1/` 与外壳自检都容忍额外参数，已写断言防回退）。**并补齐测试隔离**（事故复盘：自检起的临时外壳实例与用户正在跑的实例共用 WebView2 用户目录与同一份 `desktop.log`，导致用户实例收到异常窗口事件后沿"托盘不可用则放行关闭"兜底连后端一起退出）——现在自检的**日志目录与 WebView2 存储都指到临时目录**，且**检测到用户实例在跑就跳过会开窗口的段落**；实测自检全程用户实例 PID 不变（107/0，跳过 7 项）。以上逐项的问题-方案-结果见 `docs/v2.5/`；版本与文档规则见 `docs/README.md`。

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
| 桌面应用版（pywebview 外壳） | `front/desktop/`：双击 `desktop.exe`（带图标、无黑窗）或 `desktop.cmd`（后备，能看到完整输出）即用——自带/复用后端、**同源加载同一份 SPA**（后端与前端零改动）、**记住登录**（关掉再打开不用重新登录）、托盘常驻（立即生成周报 / 打开导出目录 / 当前账号）、**周报完成弹系统通知**、导出走**原生「另存为」**、点窗口 X **收进托盘** |
| 用户数据自主管理 | `/api/me/*` 全套 CRUD：公司资料/我司产品/关注竞品、个人兴趣/关注/收藏，按账号隔离；Agent 工具只读消费（M4 前置已装配） |
| Digest 定期推送 | 订阅管理 + Tavily/HN 检索 + LLM 三关筛选生成周报 + APScheduler 定时调度 + WS 实时提醒 + 报告列表页下载（M4 已装配） |
| 异步隔离 | 基于 `ContextVar` 的请求级上下文隔离，避免多用户「串台」 |
| 配置外置 | 所有提示词（Prompt）集中在 `prompt/prompts.yml`，改提示词无需动代码 |

---

## 三、项目结构

> ⚠️ **唯一权威是 [`docs/文件存放规则.md`](docs/文件存放规则.md)**（含每个目录的职责、新文件放哪的判据、不入库清单）。
> 本章只是速览；新增文件/目录前请先读那份规则，**新建子文件夹需先与用户确认**。

```text
deep_search_pro/
├── main.py # 程序入口（CLI 交互版：主 Agent + 子 Agent 装配）
├── dspro / dspro.cmd # CLI 包装脚本（免激活 venv；PATH 加项目根即可直接 dspro）
├── pyproject.toml / uv.lock # 依赖清单与锁文件（uv 管理）
├── .env / .env.example # 环境变量（.env* 不入库）
├── README.md / IDEA.md # 主文档（含阅读指南）/ 项目定位
│
├── agent/ # ★ 智能体核心（模型接入 / 装配 / 预算 / 会话 / 思考捕获 / 周报引擎）
│ └── subagents/ # 子 Agent 装配（网络搜索 / 数据库 / 个人情报 / 周报）
├── api/ # ★ FastAPI 服务层（REST + WebSocket + 各业务 API 模块）
├── cli/ # ★ CLI 版（dspro）：login / list / digest / chat
├── prompt/prompts.yml # ★ 全部提示词（主 Agent + 各子 Agent + 各撰写 AI 段）
│
├── tools/ # ★ agent 的**硬编码内置工具**（产出用于挂载 agent）
│ └── _runtime/ # ⚠️ 内部实现（能力执行层），外部不许直接 import
├── utils/ # 便利开发的小工具（如路径辅助：封装一次、别处一句引用）
├── rag_knowledge/ # ★ RAG 知识库（kb_service / kb_store / cleaner / evaluator）
│ └── db/ # 每用户 Chroma 落盘（不入库；含路由基准临时库，保留）
│
├── front/ # ★ 前端一切（唯一家）：SPA + 教程素材 + 桌面壳
│ ├── index.html # 免构建 Vue3 单文件 SPA（登录 / 工作台 / AI 助手 / 报告 / 定制）
│ ├── vendor/vue.global.prod.js
│ ├── tutorial/ # 教程素材（演示文档引用的截图/svg + api 教程正文）
│ │ └── previews/ # 机器生成的静态预览/harness（不入库）
│ └── desktop/ # 桌面版外壳（pywebview：app.py / desktop.cmd / 图标）
│
├── scripts/ # 开发运维脚本（预览生成 / CDP 探针 / 数据迁移）
├── tests/ # 测试与调试（按类分子目录）
│ ├── unit/ # ① pytest 单元 / 静态契约（秒级，无需服务）
│ ├── harness/ # ② 前端抽取式 harness（Node）
│ ├── testclient/ # ③ TestClient 端到端（进程内）
│ ├── live/ # ④ 真机 / 外壳 / 真窗口（慢，注意额度）
│ ├── debug/ # ⑤ 调试 / 临时
│ ├── fixtures/ # 基准与夹具（不能放 data/，会被 gitignore 丢掉）
│ └── test_out/ # 系统级测试产物（不入库）
├── docs/ # 里程碑文档：v1.0 / v2.0 / v2.5 / v3.0 + sql/ + 本规则文件
│ └── <版本>/backups/ # 按版本存放的备份
│
├── data/ # 运行时数据（不入库）：personal.db / sandbox / audit / 各类缓存
├── output/{账号}/ # ★ agent 的产物：周报 md/pdf、导出、tts/ 语音（不入库）
├── pic/{账号}/ # 用户上传的背景图（不入库）
└── agents_docs/{账号}/ # 用户级智能体资料：SOUL.md / MEMORY.md / 人格库 / 技能（不入库）
```

**三条最容易被忽略的规矩**：
1. **前端一切文件都在 `front/`**，不放别处；
2. **`tools/` 是"挂给 agent 的内置工具"，`utils/` 是"给开发者用的小工具"**——别混；
3. **测试账号产出的数据按正常账号规则对待**（在 `output/{账号}/`）；只有**针对整个系统测试运行**的产物才进 `tests/test_out/`。

### v3.0 / v3.1 新增模块的阅读路径（M1 ~ M6c · M6b）

> 上面那张表是 v1.0/v2.0 的骨架；v3.0 是在同一套骨架上**填槽位**，v3.1 补的是**度量**（怎么证明「没改坏」），
>
> 🗺 **先看一张图（读代码前建立全局坐标）**：[`docs/v3.1/agent工作与工具调用体系设计图.html`](docs/v3.1/agent工作与工具调用体系设计图.html)（交互查看器：明暗切换 / 缩放 / 搜索 / 关系追踪 / **点击节点看技术栈** / 4 章导览 / 演示 / 导出）—— 主 Agent 的**调用依据**（能力池与路由 → 系统提示词装配 → `messages[0]` 注入）、三族工具（内置 @tool / 4 个子 Agent / 自配 CLI·API·MCP）如何**纳入**与**联动**、以及执行侧护栏，全在一张图里。
> 所以按下面这八步接着读即可。每步都留了思考题——它们就是面试里最容易被追问的点。

| 顺序 | 文件 | 看什么 / 思考题 |
|---|---|---|
| 1️⃣ | `docs/v3.0/v3.0规划书.md` | 先看 §2 优先级与 §7「明确不做」。**思考题**：为什么"不做清单"对个人项目比路线图更有用？ |
| 2️⃣ | `tools/api_driver.py` · `tools/mcp_driver.py` · `tools/capability_pool.py` | 两类外部能力怎么被抹平成同一个 `CapabilityEntry`。**思考题**：如果当初把工具池写成"CLI 专用"，加 API/MCP 要多付哪三笔代价？ |
| 3️⃣ | `agent/usage_counter.py` · `agent/token_usage.py` · `docs/v3.0/M6c-成本表与失败分类实现记录.md` | 一次工具调用、一次模型调用分别在哪一刻落库。**思考题**：为什么金额必须**落库**而不是查询时现算？（提示：换模型后历史账） |
| 4️⃣ | `tools/memory_profile.py` · `tools/profile_evidence.py` | 画像为什么坚持"人可读、可编辑、可追溯"。**思考题**：只做向量化黑盒画像，三个月后哪类问题会集中爆发？ |
| 5️⃣ | `tools/price_ledger.py` · `tools/price_display.py` · `api/price_api.py` | 台账、登记价、目标价、复杂价格规则四者的边界。**思考题**：为什么「资讯价」必须与「当前价」分表并强制标注来源？ |
| 6️⃣ | `tests/live/m5c_router_bench.py` · `docs/v3.1/M6b-测试基线与路由基准方案.md` | **路由质量怎么变成可复跑的基准**：冻结池（28 条快照）+ 题库（30 题）+ 五个数（precision@3 / recall@3 / hit@3 / 灰区 / 噪声假阳性）——**纯本地 embedding、零网络、零额度、一次约 2 分 42 秒**。**思考题**：为什么「池子 8 → 28 后路由全对」这个结论**不可复现**？为什么灰区 0.5（一半问句回退全量）是 **fail-safe 而不是错误**？为什么判据要分**软**（能力目标，只记录）与**硬**（相对基线不得下降，才告警）两层？ |
| 7️⃣ | `tests/live/m5b_pressure_probe.py`(v3) · `m6b_probe_drift.py` · `m6b_score_import.py` · `docs/v3.1/M6b-探针问题集v2.md`(§八/§十) | **真机探针制度 v3**：43 条真实工具题集（覆盖账号 28 条能力）+ ★ **脚本只记录「调了哪些工具、什么顺序」，对错人工打分**；基线 = 序列 + 评分 + 备注（`tests/fixtures/m5b_probe_baseline.json` full 43 条）。**思考题**：为什么链式调用（12306「取时间 → 城市换站码 → 查票」）让「只看首调」**必然误判**、且每加一个来源就得打一块补丁？为什么基线只能记**一次路径**，序列变了 ≠ 变差？为什么**未测（429/5xx）**必须单列、不能记成能力问题？ |
| 9️⃣ | **`docs/v3.1/工具路由设计-逐文件带读.md`**（★ 想**逐行检查路由设计**就从这份走） | 路由这条线的**全量文件带读**：按「非 Python 契约/配置 → 零第三方依赖的纯逻辑 → 少量依赖 → 装配层」八层排序，每个文件都给**关键定义行号 + 在链路里的位置 + 逐行检查要点 + 思考题**；配套命令速查与已知缺陷清单 |
| 8️⃣ | `docs/v3.1/路由质量修复任务书.md`（**已批注 · 主体落地**） | **拿实测证据立项**：7 个「选错工具」用例（离线基准 5 + 真机探针 2）分两种形态（期望工具不入 top3 / 命中了来源却取错函数），候选修法按「**先补配置 → 再动策略 → 阈值放最后**」排序，验收判据六条。**思考题**：为什么噪声假阳性**不得上升**必须和 precision 一起写进硬判据？为什么旧根因假设（来源级描述污染向量文本）**必须先复验**再开工？为什么「选对工具」与「正确使用对的工具」要**分成两件事**？ |
| 9️⃣ | `tools/voice_asr.py` · `api/server.py`(`/api/asr*`) · 前端 `index.html` 的语音块 · 外部小项目 `asr_cli` · `docs/v3.1/语音转文字-逐文件带读.md` | **语音输入（长按说话 → 松手转文字）**：像微信那样**长按输入框里的话筒**说话，松手把话转成文字**追加**进输入框—— 这是**产品自己的输入方式** 不是 agent 的工具（不进工具路由）。★ 两个实测逼出来的设计：① 模型冷加载 **8.5s** → 引擎**常驻**（之后 ≈0.3s）；② 主项目 venv 里 chromadb 的 `onnxruntime` 与 sherpa 自带的**撞名 → 段错误** → 引擎**独立进程 + 独立 venv**。**音频全程不落盘**。**思考题**：为什么「每次起一个 CLI 进程」在这个功能上**必然不可行**？为什么两条边界要用**两种请求格式**（前端 multipart 后端到引擎裸字节）？为什么把「音频不落盘」写成**源码级断言**而不是只测行为？ |

> 真机口径与额度纪律见 `tests/README.md`（例如 `STUB_WEBSEARCH=1` 零消耗跑周报、Tavily 额度紧张期不碰网搜）。

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
| 1️⃣4️⃣ | `front/index.html` | **前端 SPA**（M2/M3，免构建 Vue 3）：登录页按 role 分流到公司/个人双工作台；WebSocket 收到 `monitor_event` 渲染右栏进度；来源标记 🔍/🗄️/📚 高亮。登录区是**登录 / 注册两张卡**（2026-09-16 收尾拆分）——旧版是混合卡「输入的用户名不存在即自动注册」，打错一个字就会静默建出错误账号，现在改为注册卡独立（含确认密码 + 「即将创建：xxx」实时回显），且**登录失败绝不建号**、账号不存在时提示「去注册 →」引导。思考：为什么「登录失败自动注册」看着贴心却危险？（用户打错用户名时得到的是一个**能登进去的空账号**，等到发现数据都不见了才察觉，错误被延迟放大）；为什么「即将创建」这行回显比弹窗二次确认更合适？（不打断流程，但把不可逆操作的后果提前摆到眼前）。组件美化来自 uiverse 库（003 气泡按钮 / 004 硬阴影输入框 / 001 3D 方块加载器），体会「snippet 重配色适配主题」的方法。后续增强：浅绿/白/黄三色系重配色、AI 回复框深底亮字对比、右栏「🤔 模型思考过程」折叠区、顶栏「🎨 背景」图库面板。 |
| 1️⃣5️⃣ | `tests/` | **测试与调试脚本**：与项目代码分离存放。`m3_smoketest.py` 是接口层回归（登录→竞品清单→导出下载）；`auth_split_e2e.py` 是登录/注册拆分的回归（**核心断言：用打错的用户名登录不会建号**——查库计数不变）；前端交互的真机验证在 `scripts/cdp_auth_probe.py`（无头 Chrome + CDP，真实点击并断言「账号是否真的建出来」）。注意脚本须基于 `__file__` 解析项目根注入 sys.path、并豁免本机 HTTP_PROXY 才能访问 localhost。 |
| 1️⃣6️⃣ | `api/me_user_data.py` | **用户数据自主管理**（M4 前置）：`/api/me/*` 全套 CRUD，全部按 session 的 account_id 隔离。思考：为什么「增删改走 REST、Agent 工具只读」的读写分离能同时保住数据安全与 Agent 可靠性？ |
| 1️⃣7️⃣ | `agent/digest_engine.py` + `agent/subagents/digest_agent.py` + `api/digest_scheduler.py` | **Digest 定期推送**（M4，回归主智能体编排）：① 为什么 digest 不自己写检索代码、而是让主 Agent 调度【网络搜索助手】？② 查询批次为何硬上限 ≤8？③ 候选不足 15 条时筛选门槛为何放宽（禁止空周报）？④ APScheduler 为何内嵌 FastAPI 进程而非外部 cron？⑤（时效性修复）`build_batches` 为何弃用「关键词+修饰词」拼接、改用关键词原文？——实测 Tavily 对带修饰词的中文长查询相关性崩坏（返回无关金融/加密货币新闻），且修饰词无法跨领域通用。 |
| 1️⃣8️⃣ | `front/index.html` 的「v3.1：语音转文字」标记块 · `tools/voice_asr.py` · `api/server.py`(`/api/asr*`) · `docs/v3.1/语音转文字-逐文件带读.md` | **长按说话 → 松手转文字**（输入侧）：为什么它**不是** agent 的工具、不进工具路由？为什么必须**两个进程**（`onnxruntime` 撞名 → 段错误 + 冷加载 8.5s → 常驻引擎）？「音频不落盘」为什么要在**源码层**钉住？「移出话筒 = 取消」为什么**单向不可逆**？**思考题**：harness 从 `index.html` **抽取代码块**时，会怎样被「插在中间的新代码」悄悄弄坏（本次踩过 区间锚点该指向什么才稳）？ |
| 2️⃣7️⃣ | `agent/build_context.py` + `api/server.py`(agent 构建/缓存) | **M2 请求装配管线化（v2.0）**：① 为什么 v1.0「改人格/记忆就重建整个 agent」不好？（每次画像变化重建开销大、缓存膨胀、动态来源无法叠加、难以调试这次请求带了什么）→ agent 静态化（只按自定义模型配置缓存），SOUL/MEMORY 每次 invoke 前由 `build_request_context` 拼成 **SystemMessage 放 messages 首条**注入（Spike 实测：deepagents invoke 首条 system 动态覆盖构建时 system_prompt）；② 人格切换零重建（e2e：P1海盗→P2管家不重启服务即生效）；③ 单一来源：旧拼接副本删除，动态内容统一走 `compose_dynamic_prompt`（人格在前、记忆在后）。（**v3.0 M3 追加**：记忆注入不再是"整份塞进去"——画像段 ≤500 字、笔记段 ≤300 字，超限在**装配期**按"情报相关维度优先 / 笔记相关度取前 N + 最近 3 条兜底"裁剪（下下游按句子做字符串过滤是不可控的，看 `_trim_profile`/`_trim_notes`）；同时注入一行服务端算好的**【画像待更新】信号**，把"该不该更新画像"从模型的自由发挥变成看得见的事实。）思考：为什么动态 SystemMessage 不怕历史截断？（ContextTruncator 保护所有 SystemMessage，persona 永不丢）；为什么「改模型才重建」？（模型配置影响 agent 绑定的模型实例，人格/记忆只是请求期输入）。 |

| 1️⃣8️⃣ | `docs/README.md` + `docs/v1.0/` + `docs/v2.0/` + `docs/v2.5/` + **`docs/v3.0/`** + **`docs/v3.1/`** | **设计文档与里程碑**（★ **v3.1 先看这三篇**：`M6b-测试基线与路由基准方案.md`（离线路由基准实测 + 探针制度 v3 + 三项裁决）· `M6b-探针问题集v2.md`（43 条真实题集 + §十 全量审计：36 / △5 / 2 + 两个悬案结案）· `路由质量修复任务书.md`（7 个「选错工具」用例 + 候选修法 + 六条验收判据；**已批注 · 主体落地，剩一轮 live △ 复跑**）■ ★ v3.0 里还有一篇**路由专题**：`遗留问题v2.0M5c-路由基准与Jev仲裁预研.md` —— 池子从 8 涨到 28 后实测出的三类真问题（权限元数据把 19/28 个纯查询工具标成写操作 / 来源级描述污染同源每个工具的向量文本 / 缺带标签基准），以及三步走方案与 Jev 仲裁层的立项条件；想理解"路由为什么会自信选错"，读它）：`docs/v1.0/` 是定版档案（`里程碑计划.md` 为开发顺序准绳 + `M1_*~M4_*` 实现与实测记录）；`docs/v2.0/` 是当前迭代（`M1上下文工程化.md`/`M2请求装配管线化.md`/`M3RAG知识库搭建与使用.md`/`M4技能与工具.md`/`M5工具智能路由.md`/`M5b工具检索路由.md`/**`工具路由设计.md`（跨里程碑的工具路由总设计：链路全景 + 逐文件带读 + 阅读指南）**/`CLI命令行工具dspro.md`）。**★ 版本与文档规则（2026-09-24 定，详见 `docs/README.md`）**：**整数版**（v1.0/v2.0/v3.0）= 大规模变化（新功能开发、现有功能颠覆性优化、架构改进），**技术调研 / 大型方案 / 工程计划书一律进整数目录**——**即使该版本收官之后才写**也算它的（判断依据是**文档性质**而非写作时间）；**小数版**（v2.5/v2.6）= 现有功能的用户观感优化、bug 修补、小功能添加，且**小数目录只放「问题-方案-结果」性质的文件**，不允许出现长文档。放错目录比不写更麻烦（找不回、也对不上版本号）。思考：为什么「按文档性质、而非按写作时间」归档，才能让一个版本的目录自解释？（提示：读者来找的是「这个版本改了什么」，不是「你哪天写的」） |

| 1️⃣9️⃣ | `agent/reasoning_model.py` + `agent/thinking_capture.py` + `api/monitor.py` | **思考过程可视化（A+ 方案）**：langchain-openai 明确丢弃第三方 base_url 的非标准字段 `reasoning_content`（源码 `_convert_delta_to_message_chunk` 只读 content/function_call/tool_calls，文档明言「Use a provider-specific subclass」）。方案：自定义 `ReasoningChatOpenAI(BaseChatOpenAI)` 重写 `_create_chat_result`（非流式）把 `reasoning_content` 塞进 `additional_kwargs`；`thinking_capture.py` 在 `agent.invoke` 完成后遍历 `result["messages"]` 提取各轮 AIMessage 的思考推前端。思考：为什么不用 `on_chat_model_end` 回调做实时？（deepagents 底层 `langchain.agents.create_agent` 的模型节点 `_execute_model_sync` 只 `model_.invoke(messages)` 不传 config，回调不触发；`model.with_config(callbacks=...)` 返回 RunnableBinding 会被 resolve_model 当字符串报错）。为什么这样比旧「双请求旁路」好？（单请求省 token + 覆盖所有轮次，旁路只覆盖首轮）。 |
| 2️⃣0️⃣ | `api/customize.py` + `tools/schema_personal.py`(llm_providers 表) | **定制助手后端**：五组 API 按账号隔离——① 背景图 `/api/bg/*`（multipart 上传存 `pic/{username}/`、列表、删除，uuid 前缀防重名）；② 模型选型 `/api/llm/providers`（CRUD + activate，**api_key 列表时掩码 `****后4位`，编辑留空=不改**）；③ 人格 `/api/soul`（默认 `SOUL.md` + 多人格集 `SOUL/{name}.md`，`ACTIVE_SOUL` 文件记录当前激活，list/create/delete/set_active/named 路由）；④ 记忆画像 `/api/memory`（读写 `MEMORY.md` + 一次性初始化 `/api/memory/init`）；⑤ 批量导入 `/api/me/*/batch`（竞品/收藏剪贴板粘贴，一行一个，去空去重 ≤50 条）。思考：`_get_agent_for()` 为什么「有人格/自定义模型/记忆画像就按账号缓存独立 agent」而不是复用全局 AGENT？（全局 AGENT 的 system_prompt 在构建时固定，无法注入动态内容；缓存 key 含 soul+memory 哈希，人格/记忆变化自动重建） |
| 2️⃣1️⃣ | `agent/digest_engine.py`(任务指令) + `tools/tavily_tool.py`(strict_days/bilingual) | **周报时效性修复**：① `strict_days=True` 在代码层解析每条 `published_date`（RFC822 格式）剔除超窗旧闻——为什么不能只靠模型自觉？（Tavily 的 days 只是硬上限不保证排序时效，旧闻常排前面）② `bilingual=True` 用 LLM 把中文关键词翻译成英文做中英双语检索合并——为什么单中文查询会漏？（实测 Tavily 对中文实体词相关性差，英文召回更精准）③ 订阅级 `lang` 字段（zh/en/both）控制是否双语。思考：无日期的结果为什么「宁可保留不误杀」？ |
| 2️⃣2️⃣ | `tools/readtofile.py` + `tools/writetofile.py` | **agents_docs 文档读写工具**：主智能体可读/写 `agents_docs/` 下的 .md/.txt（路径穿越防护：归一化后强制前缀校验；扩展名白名单）。端到端验证：`tests/testclient/tool_agent_e2e_test.py` 把工具挂到全局 AGENT 后由 Agent 实际完成写文件+读回任务。思考：为什么这类「越权敏感」工具要同时在路径与扩展名两层设防？ |
| 2️⃣3️⃣ | `prompt/prompts.yml`(soul_writer + memory_initializer 段) | **人格/画像撰写 AI**：顶层独立段（不进 main_agent/sub_agents，不挂工具）——`soul_writer` 按用户一句话需求（如「活泼女仆」「稳重管家」）生成 200-500 字人格提示词（称呼/说话风格/情绪/周报风格/行为准则五维）；`memory_initializer` 按用户系统已有资料（兴趣/收藏/竞品/公司信息）提炼 150-400 字初始画像（缺的维度如实标注「暂无」）。思考：为什么这两个 AI 必须独立于主 Agent 体系？ |
| 2️⃣4️⃣ | `agents_docs/{user}/MEMORY.md` + `agent/digest_engine.py`(画像注入) | **记忆白盒化（用户画像/公司形象）**：① 账号注册即生成空 MEMORY.md（`<!-- memory_init: pending -->` 标记）；② 主 Agent 的 system_prompt 注入【你的用户记忆画像】区块 + 强制记忆维护指令——用户表达稳定偏好时必须调用【写入文档工具】更新 MEMORY.md（禁止只口头确认）；③ `_get_agent_for` 缓存 key 含 memory 哈希，画像变化自动重建 agent；④ chat 路由对比调用前后 MEMORY.md 内容，变化则回复末尾附「📝 已更新你的记忆画像」提示；⑤ digest 引擎读 USER PROFILE 段注入「情报侧重」（价格敏感→降价/优惠类优先）；⑥ 前端定制页可视化编辑 + 一次性初始化按钮。**踩坑**：deepagents 内置 filesystem 工具（read_file/write_file）与自定义文档工具重名竞争，模型优先选内置导致绕过 agents_docs 约束——用 `_ToolExclusionMiddleware(excluded=...)` 排除内置工具。思考：为什么「排除内置工具」比「改名自定义工具」更优？（保留 agents_docs 安全边界） |
| 2️⃣5️⃣ | `api/voice_tts.py` + `voice_test`(外部小项目) | **语音朗读与自定义音色**：① 为什么不能把 voice_test 的代码复制进主项目？（环境冲突：voice_test=py3.12+torch+qwen_tts，主项目=py3.11+langchain，numpy .pyd 不兼容）→ 用 **subprocess 执行 voice_test 的 venv python 跑脚本**，环境天然隔离；② pkl 按账号隔离：`--name {username}_{包名}` 前缀，voice_test 脚本零改动；③ 显存约束（RTX 3050 4GB）：模型**只在用户使用时临时启动**（subprocess 进程退出即释放显存），不做常驻服务；④ 每次合成要重新加载模型（30-60s），常驻化方案见 `docs/v2.0/v2.0方案.md`；⑤ **踩坑**：subprocess 必须清 PYTHONPATH（Hermes 注入的 hermes-agent venv 会让 voice_test 的 numpy 加载崩）；克隆用后即删参考音频（只留 pkl 资产）。思考：为什么「名字前缀隔离」比「子目录隔离」更省事？（voice_test 脚本读固定 PACK_DIR，不支持子目录） |
| 2️⃣6️⃣ | `agent/context_budget.py` + `agent/conversation_store.py` + `agent/build_context.py` + `api/server.py`(会话路由) | **M1 上下文工程化（v2.0）**：① 为什么 v1.0 的 MemorySaver 承载消息历史是错的？（进程内内存、重启即丢、无界增长必然爆免费模型窗口）→ 对话历史改存 SQLite（`conversations`/`messages` 表，按账号隔离），每次 invoke 显式传入完整上下文（SQLite 恢复 + 新提问），agent 无状态执行；② 上下文预算四组件（AstrBot `core/agent/context/` 借鉴）：TokenCounter（启发式估算，中文 1.5 字/token）/ Truncator（按"轮"丢弃，system 永不丢）/ Compressor（LLM 摘要可选，失败自动降级截断）/ ContextManager 编排——token 超限或轮数超限时触发，静默保证不爆窗；③ 多会话管理（类主流 AI 聊天应用）：UUID thread_id、标题自动取首问、前端左栏会话列表（新建/切换/重命名/删除）、会话归属校验（防越权 404）。思考：为什么「截断按轮不按条」？（工具消息必须跟随所属 user 轮次，留孤立 tool 块模型会崩）；为什么删掉 checkpointer 也安全？（deepagents 只用 interrupt_on 等可选功能才需要 checkpointer，本项目只用了工具排除 middleware）。 |
| 2️⃣8️⃣ | `rag_knowledge/` + `tools/kb_tools.py` | **M3 RAG 知识库（v2.0）**：① 为什么弃 RAGFlow 用自建 Chroma？（蜀道已验证同栈 chromadb+bge 混合检索 ALL@10=0.83；单机本地零运维；模型方法可复用）② 存什么？（用户私有沉淀：导出报告经弹窗确认入库 + 上传 .md——**不做**网络缓存/SOUL 向量化）；③ 检索管线（BM25 稀疏 + bge 稠密 → RRF 融合 → 可选 bge-reranker 重排 → 父块注入）；④ 快/全双模式（对话 query_kb 快模式跳过 rerank ~0.1s；周报代码层必查 full 模式 <1s——个人库 ≤100 文档规模小）；⑤ **入库决策链**（evaluator 独立小 agent 评估脏/质量 → 前端弹窗显示评估建议 + 清洗预览 → 用户确认 → cleaner 清洗 AI 报告脏内容 → 分块入库）；⑥ **消息级防重复入库**（messages 表 `kb_ingested` 标志：同一条 assistant 消息的报告已入过库 → 弹窗顶部 ⚠️ 警告 + 后端 400 拒绝；force=true 可强入）；⑦ **前端弹窗**：用 `<teleport to="body">` 挂载到 body 根（任何 view 都能弹，不再局限知识库页）；亮色文字适配深底弹窗。思考：为什么入库前要清洗 AI 导出报告？（人格口吻/emoji/表格符是检索脏内容，蜀道 272 篇污染清理教训）；为什么 query_kb 是工具不是子 Agent？（主 Agent 掌控何时查，免费模型不会主动查——周报场景代码层必查兜底）；为什么用消息级去重而不是 md5 去重？（同内容报告每次导出文本可能微调措辞，md5 会漏；消息级精确到"这条回复是否已入过"）。 |
| 2️⃣9️⃣ | `api/customize.py`(技能能力层) + `agent/build_context.py`(`compose_user_prompt`) + `api/server.py`(`/api/chat` 的 skills) + `front/index.html`(技能与工具视图) | **M4a 技能（v2.0）**：① 技能 = **用户自己写的 SKILL.md 任务说明书**，存 `agents_docs/{user}/skills/`（复用 SOUL 的目录与文档读写工具）——为什么用「user message 前缀注入」而不是塞进 system_prompt？（技能是**本轮一次性**语义：用户只在这次提问里要它，塞 system 会与人格/记忆的常驻语义混淆且无法随轮次消失）② 注入链路：输入框 `/` 唤出目录（筛选、↑↓ 回绕、Enter/Tab 选中、Esc 关闭，最多叠加 5 个）→ 气泡里留技能 pill → `POST /api/chat?skills=a,b` → **server 层**读技能正文传入 `build_request_context`（不能放 `build_context.py` 里读——该模块铁律「不 import api.*」）→ `compose_user_prompt()` 拼成 `【本轮启用技能】…———【用户提问】…` 前缀；③ **落库的仍是原始提问**（前缀只进本轮 invoke）→ 下一轮自然不再带技能，这就是「一次性」的实现方式；④ 边界识别修正：本轮起点判定改按 `ctx.final_user_msg`（装配后文本）比对，否则加了前缀后 `m.content == question` 恒不成立、工具调用消息会被漏掉；⑤ 技能名即文件名 → sanitize 白名单（防 `..` 与 Windows 保留名 `nul/CON/AUX`）+ 单技能 4000 字上限（前后端同值硬截断）。思考：为什么技能要用「说明书文档」而不是「硬编码工具」？（工具是能力、技能是方法：换口径不用改代码）；为什么「用完即清」而不是会话内常驻？（常驻会让多个技能互相污染，也解释不清「这次为什么这样答」）；为什么句中斜杠（如「价格/性能」）不该弹菜单？（只在输入框开头为 `/` 时触发，否则正常提问会被打断）。 |
| 3️⃣0️⃣ | `tools/_runtime/shell_runtime.py` + `tools/shell_executor.py` + `api/server.py`(`/api/shell/*`) + `front/index.html`(CLI 面板) | **M4b CLI 沙箱面板（v2.0）**：① 为什么给 Agent 开命令行？（CLI 是 AI 的母语：`--help` 自描述、参数组合表达力强、输出可解析；对比 MCP 每接一个服务都要写一层适配）② **工具层 / 能力层分离**：`shell_runtime.py` 只干执行，`shell_executor.py` 用 `@tool run_shell_command` 包给 Agent——**CLI 面板与 Agent 共用同一执行器**，所以「面板能敲什么」＝「Agent 能跑什么」，安全规则不会两套漂移；③ **六道闸**：命令白名单 → 参数级路径校验 → 沙箱 cwd → 超时上限 → 输出 1MB 截断 → 审计日志 `data/audit/commands.jsonl`（含被拒条目）；④ 为什么剔除 `python`？（它本身就是沙箱后门：一条 `python -c` 就能读任意文件、起子进程，其余五道闸形同虚设）；⑤ 为什么 `ls/cat/grep/echo/pwd/mkdir` 要 Python 内建实现？（实测 Windows 原生 PATH 下这些 exe 一个都找不到——Git 只把 `PortableGit\bin` 加进 PATH，`usr\bin` 没加）；⑥ 为什么只挡 cwd 不够？（`cat ../../.env`、`git --git-dir=C:/Users`、`curl file:///C:/Users/ZQK/.ssh/id_rsa` 都能绕过沙箱 → 必须参数级校验，且「等号写法」要一起拦）。思考：为什么 `curl` 只允许 GET 且禁 `-o`？（否则会变成任意文件下载器直写沙箱）；为什么这个沙箱刻意保持**只读**——只做「看文件 + 抓网页 + 建目录」，不提供写文件能力？ |
| 3️⃣1️⃣ | `tools/cli_registry.py` + `tools/_runtime/shell_runtime.py`(第七道闸) + `api/server.py`(`/api/cli/*`) + `front/index.html`(🧩 自定义 CLI 页) | **M4c 自定义 CLI 接入（v2.0，配置导向）**：① 为什么不预设厂商？（推出 CLI 的厂家远不止飞书/钉钉/Google，写死就得改代码；改成「用户填字段」后，与模型配置填 base_url/model/key 一样通用——系统只按三条与厂商无关的规则办事：白名单＝用户配的条目、只读闸＝用户填的清单、本机检测＝`which 可执行名`；内置几家退化为 `templates` 预填示例，点一下只填表单、不进白名单）；② **只读清单为什么必须人给**？（哪些子命令只读无法自动推断——`doc search` 是读、`cache clear` 是写、`im +messages-send` 是写，同厂内部都没有统一命名规律；系统不猜，清单留空＝不放行 AI 代跑）；③ 与 Agent 的关系＝折中方案 C：登记后只放行只读/查询子命令，写操作与落盘参数（`-o/--output…`）一律拒绝；④ 安全边界（自定义≠放松）：**可执行名只允许纯名字**（含 `/ \ :` 即拒，否则等于把「执行任意程序」交给表单）、清单含 shell 元字符（分号/管道/重定向/美元符等）即拒、每行 ≤6 段 ≤40 行、fail closed；⑤ 配置落 SQLite `user_clis`（`UNIQUE(account_id, bin)`，按账号隔离），旧版内置厂商登记幂等迁移进来；⑥ **局部更新「缺省即保留」**：只传 `id/name/bin` 时不能把 `state` 清成 none、`readonly` 清空（否则「只改个名字」就把接入登记和代跑权限一起弄没了）。⑦ **CLI 简报注入**：`tools/cli_registry.agent_brief()` 为每个已接入的 CLI 生成一行（名称+可执行名+放行条数+示例子命令），由 server 层经 `build_request_context(cli_brief=…)` 拼进 invoke 期的动态 SystemMessage（与人格/记忆同一机制），`prompts.yml` 主 Agent 同步加「命令行使用纪律」——不注入的话，Agent 会答「该命令不在白名单」（实测它连工具都没调，把工具说明书的通用描述当成了查过白名单的事实）；没配 CLI 时注入空串、零开销。思考：为什么「可执行名不允许写路径」这条比「白名单里少几个命令」重要得多？（前者一旦放开，等于给表单开了任意程序执行的后门，只读清单再严也没用）；为什么只读清单要去重却仍按**原始行数**限流？（否则 45 行相同内容去重成 1 条，绕过条数上限） |
| 3️⃣2️⃣ | `api/server.py`(`/api/tutorial/doc` + `/tutorial-assets` 挂载) + `front/index.html`(`tutRender` 只读阅读页) + `scripts/build_tutorial_preview.js` | **M4 收尾·教程指导（只读 md 阅读页）**：CLI 配置页的「📘 教程指导」按钮（视觉取自 uiverse **003 号 buttons/bubbles**，hover 气泡扩散填充；原片段是 SCSS 嵌套写法，移植时必须展平成 `.bubbles:hover > .text`）→ 跳到 `#/tutorial` 只读阅读页。要点：① **后端只读接口 + 静态资源分离**：md 由 `GET /api/tutorial/doc` 返回（不改文件），图片走 `/tutorial-assets` 静态挂载（`<img>` 不必带 token）；② **图片内联是难点**：教程 md 里的图片是本机**绝对路径**（含一张项目外的 Typora 贴图）——做法是「只取文件名 → 在白名单目录（文档目录 + Typora 贴图目录）里查找 → 拷进 `data/tutorial_assets/` → src 改写成 `/tutorial-assets/<名>`」，找不到的整段剔除并回报 `missing`（避免裂图）；安全上只按文件名解析（挡 `../` 与绝对路径穿越）、只处理图片扩展名（实测 `/tutorial-assets/../.env` → 404）；③ **前端手写 `tutRender`**（零依赖离线可用，照蜀道「政策文件阅读器」做法）：支持标题/表格/列表/引用/代码块/图片/加粗/行内码/`<details>` 折叠块，**先整体转义再只白名单放行 `<details>`/`<summary>`**（`<script>`、`<img onerror>`、`<iframe>` 实测全被转义）。思考：为什么图片不能直接按 md 里的绝对路径返回？（那等于把「读任意本地文件」做成了接口——只能认白名单目录里的**文件名**）；为什么渲染器要「先转义再白名单还原」而不是「选择性转义」？（黑名单永远漏，白名单只放四个字面量最稳） |
| 3️⃣3️⃣ | `api/server.py`(`/api/weather` + 三层缓存 + 启动预热) + `front/index.html`(`.wx-dock` 时钟/天气卡) + `scripts/build_weather_preview.js` + `scripts/build_wx_harness.py` | **M4 收尾·工作台时钟 + 天气卡**：欢迎语右侧的药丸收起 / 悬浮展开（视觉取自 uiverse **012 号 cards/weather-gradient**）。三个要点：① **数据源选 Open-Meteo**——免 Key 且自带**地理编码**接口，所以能做到**城市级**（成都 30.667/104.067，可中英文城市名）；② **三层缓存是必需的**：首次外网请求实测 2.7~17s（DNS/代理抖动），只做内存缓存的话每次重启首屏都得转圈——所以「城市坐标永久 + 天气 10 分钟 + `data/weather_cache.json` 磁盘缓存 + 启动后台预热」，并按项目惯例写了「代理失败降级直连」与「刷新失败返回 stale 旧数据而不是 502」；③ **收起/展开用绝对定位浮层**（`opacity+transform`，不占文档流），否则会把欢迎语挤走；秒级时钟靠 `setInterval(tickClock,1000)` + `_pad2` 补零，`wxStart()` 做成幂等避免重复起定时器；④ **配色默认松绿以对齐项目色系**（012 原片段是紫罗兰）：色值全抽成 `.wx-dock` 上的 CSS 变量，主题用 `data-wx` 切换、SVG 渐变靠 `stop-color: var(--wx-c1)` 覆盖表现属性，右下角圆点可切 4 套并记 localStorage；⑤ **用户实测后修掉 6 个问题**（v-else 被插入元素打断→生产版无条件渲染致「纯色无内容」、卡片被 hero 的 overflow:hidden 裁切、换城市面板与卡片重叠、`.hero p` 样式污染卡片文字、内容超高致页脚压字、页脚折行）——详见文档附录 D.7。思考：为什么天气不该由前端直连第三方 API？（外网可达性/代理问题会被甩给浏览器，且前端无法缓存、无法统一降级）；为什么展开态不能用 `display:none/block` 切换？（过渡动画会整段失效） |
| 3️⃣4️⃣ | `cli/main.py` → `cli/cmd_auth.py` `cmd_list.py` `cmd_digest.py` `cmd_chat.py` → `cli/session.py` `ui.py`；对照 `api/server.py`(`_run_agent`) 与 `api/monitor.py`(`set_console_sink`) | **CLI 版 dspro（命令行聊天 + 命令行工具）**：`dspro login/list/digest/chat`。核心设计是「**不做第二套业务逻辑**」——CLI 独立进程直接用 `data/personal.db`、`agent/digest_engine.run_digest()`、`api/server._run_agent()`，与 web 同库同引擎，所以 CLI 生成的周报在 web「情报周报」里也能看到。要点：① **登录态** `data/dspro_session.json` 存身份 + **账号密码哈希的指纹**（不存明文），密码一改旧会话自动失效；② **`list -db/-digest`** 按账号角色取模块（公司看档案/我司产品/竞品，个人看兴趣/关注清单/收藏商品），表格按**东亚显示宽度**对齐（中文/emoji 记 2 列，零依赖）；③ **`digest [领域]`** 的领域解析：`#id` → 名称精确 → 名称包含/关键词命中，**多义时报错列出候选**（实测同一账号 3 条订阅同名「选购快讯」，静默取第一条会误导）；④ **`chat` 复用 `_run_agent`**，SOUL 人格/记忆画像/技能/知识库/自配 CLI 全部生效，会话落 `conversations` 表（web 也能看到）；⑤ **monitor 进度**：给 `ToolMonitor` 加了 `set_console_sink()`，CLI 注册后把工具调用/子智能体/思考渲染成终端进度行，而 web 不注册、行为不变（否则 `_emit` 的保底 `print` 会把 `[Monitor:xxx]` 混进对话）。思考：为什么 CLI 不该自己再写一遍 digest 逻辑？（同一份引擎才能保证 CLI 与 web 结果一致、且生成物同库落盘）；同名订阅为什么不能「取第一条」？（用户以为生成的是 A，实际是 B——静默选择比重名更危险） |
| 3️⃣5️⃣ | `tools/cli_registry.py`(`agent_brief` / `_parse_ability_pairs` / `ability_examples`) → `prompt/prompts.yml`(`ability_writer` + 主 Agent「你手上的三类工具」段——2026-09-23 重整后 3 条纪律并入此段) → `api/customize.py`(`cli_ability_generate`) → `api/server.py`(`POST /api/cli/ability_draft` + `_run_agent` 注入) → `front/index.html`(CLI 表单「能力描述」)；验证 `tests/testclient/m5_abilities_e2e.py`(43 项) `tests/live/m5_probe_e2e.py`(探针组) `tests/live/_m5_real_weread_check.py`(真实账号)；对照/迭代记录见 `docs/v2.0/M5工具智能路由.md` §3 与 §9 | **工具智能路由 M5a：让 Agent 在你说人话时想起对的工具**。M4c 解决了「Agent 知不知道有这些 CLI」，但注入的是 `shelf recent` 这类**命令名**——用户问「我最近在读什么书」时意图与命令名不在同一语义空间（LLM Agent 的经典「工具路由」问题，业界公认解法就是把描述**改写成用户语言**）。M5a 三步：① `user_clis.abilities` 存一段用户语言（关键词 + 「用户说法→命令路径」映射），`agent_brief()` **有描述出「能力路由卡」、没有就回退命令名清单**（存量零影响）；② 从描述生成 **few-shot 示例**与路由卡一起注入（**每条 argv 逐条过只读闸**，未放行的映射直接丢弃——示范一条会被拒的命令等于反向教学）；③ 「✨ 按只读清单生成草稿」让 AI 起草、**只填表单不落库**，人确认后保存。**★ 最关键的坑**：第三方 CLI 里需要 ID 的命令（`book info` / `reviews list` / `book progress` / `discover similar`，`--help` 实测）不能直调，映射要写成**两步链** `书评怎么样→book resolve 再 reviews list`（示例取链上第一步）；否则模型要么调报错命令、要么绕去网搜答你的私有数据。**实测**：探针组 10 正全中（复跑 9/10）+ 3 反例全过；**同题对照组（M4c 命令名形态）仅 2/10**——8 条连工具都没调；真实账号 + 真实 weread 端到端 15/15。思考：为什么能力描述不必像只读清单那样「必须人给」？（一个决定**能不能跑**、一个决定**想不想得起跑**，错判代价量级不同——安全边界与路由信号分层治理）；为什么判定要看库里 `messages.tool_calls`（会话真值）而不是审计日志的窗口计数？（窗口会被并发进程写进同名调用污染，实测踩过）；为什么要「只看第一次调用」？（装桩后每条命令都能成功，模型会把清单挨个试，按「出现过即命中」判会虚高） |
> 💡 学习心法：Agent 系统 = **模型（llm.py）** + **提示词（prompts.yml）** + **工具（tools/）** + **编排（subagents/ + main.py）** + **可观测性（api/）**。把这条主线记牢，再看任何 Agent 框架都不慌。

---

| 3️⃣6️⃣ | `tools/capability_pool.py` → `tools/tool_router.py` → `api/server.py`(`_run_agent` 注入) → `tools/schema_personal.py`(`tool_capabilities`/`tool_pool_meta`/`purge_account`)；标定与验证 `tests/live/_m5b_route_calib.py` `tests/testclient/m5b_pool_e2e.py`(62) `tests/testclient/m5b_router_e2e.py`(34) `tests/live/m5b_pressure_probe.py`；设计与实测见 `docs/v2.0/M5b工具检索路由.md` | **工具检索路由 M5b：工具变多之后，怎么还能想起对的那个**。① **能力目录**把 CLI / API / MCP 三种来源统一成 `CapabilityEntry`（路由只认这个形状，接新来源只写适配器）；CLI 侧**派生而不迁移**（`user_clis.abilities` 仍是唯一事实源）；池版本 + 向量惰性重建，配置写入不再冻界面。② **路由器**四级决策：池 ≤4 → 快路径（与 M5a 逐字一致）；否则 bge 稠密 + 关键词词法 → RRF 融合；**置信判定用相对信号**（词法命中 ≥2 或向量 z ≥2.0，绝对阈值已被实测证伪）；置信才分层注入（完整卡 / 紧凑行 / **仅名称**——未命中工具也保留一行，藏掉工具等于该类问题永远无解），不置信就**整段回退全量**。**实测**：8 工具异构池下 10 条问句命中判定 **10/10**、**0 条路由到错的工具**、2 条无关问句全部回退；注入量 1644 字 vs 全量 2499 字；两个真 bug 被测试逮住（RRF 无信号排序器、示例过滤条件）。思考：为什么阈值必须用 **z 分数/词法命中**而不是「相似度 > 0.5」？（中文短问句的余弦区分度只有 0.02~0.06、绝对值全挤在 0.53~0.70——绝对阈值会「全部命中」，分层形同虚设；相对信号还能随工具数增长自动放大）；为什么「不确定就回退全量」比「猜一个最像的」更好？（回退等于 M5a 行为、只是没省 token，而猜错会让模型拿着错工具的说明书去答用户的问题）；为什么未命中的工具也要留一行名称？（路由是概率判断，藏掉工具会让「那次没命中」的问题变成永久无解）；为什么要**惰性**同步向量？（向量同步要加载 bge，放进配置写入路径会让面板冻 1~3 分钟——写入只落库+打版本，重建推迟到真正需要路由的时刻） |
| 3️⃣7️⃣ | `front/desktop/`（`app.py` + `desktop.cmd`） | **桌面版外壳（pywebview）：同一份 SPA，换个壳**。① 为什么窗口必须加载 `http://127.0.0.1:PORT/?desktop=1` 而**不能**用 `file://` 打开本地 `index.html`？（页面与 API 必须**同源**：登录态与 WebSocket 都按 origin 隔离，而后端**没有 CORS 中间件**——跨源等于登录与聊天全废；同源加载让后端一行不用改）② 为什么前端用 **URL 的 `?desktop=1`** 判断「我在桌面壳里」而不是 `window.pywebview`？（后者是页面加载后**异步注入**的，`setup()` 阶段读不到——时序坑）③ 为什么「记住登录」只把 `sessionStorage` 换成 `localStorage` 还不够？（pywebview 默认 **`private_mode=True`**，文档原话 *cookies and local storage are not preserved*——必须同时 `webview.start(private_mode=False, storage_path=…)`，否则第 1 轮写进去、第 2 轮读不到）④ 为什么托盘的「立即生成周报」不走外壳自己发请求、而是调页面的 `window.__zxDesktop.runDigest()`？（前端已有「取订阅 → 生成 → 刷新 → 提示」的完整逻辑，外壳重写等于复制业务、还会把 token 搬出页面）⑤ 为什么「点 X 收进托盘」要先判**托盘是否可用**？（托盘没起来时若仍拦截关闭，用户就没有退出手段，只能杀进程）⑥ 为什么后端要**当子进程监控**、而不是只看 HTTP 轮询？（子进程可能启动即崩——端口被占 / 依赖缺失 / 编码问题；只看轮询会让用户傻等 180 秒而拿不到任何线索）。思考：**同源加载 / 持久化开关 / 进程归属** 这三件事，哪一件做错会让「桌面版」看起来能跑、实际不可用？⑦ 为什么「再次双击」不能只是"第二个实例直接退出"？（用户看到的是**双击没反应**；正确做法是让它把已有窗口唤出来——复用现成的锁 socket 当 IPC 即可）⑧ 判断"端口空着"为什么不能只用 `connect_ex`？（**只 listen 不 accept 的端口会被探测连接的 backlog 塞满**，之后 connect 被拒 → 误判为空闲 → 在占用端口上硬起；要配 `bind` 探测，且自己那把锁绝不能设 `SO_REUSEADDR`）⑨ 后端崩了要自动重启，判定基准该是"子进程还在吗"还是"服务还能访问吗"？（**必须是后者**：venv 的 `python.exe` 是 launcher，会再起一个真解释器跑 uvicorn，端口归"孙子进程"——只盯 `Popen.poll()` 会把正常启动误判成崩溃、退出时还会把真服务留成孤儿）⑩ 「记住登录」为什么是**两半**、缺一半会怎样？（存储端＝`private_mode=False`；**服务端＝token 本身要能活过后端重启**。会话原本是进程内内存字典，而桌面壳每次重开都新起后端 → 旧 token 全失效；前端又只看"token 非空"就渲染工作台，各接口 401 被 `if (!r.ok) return;` 静默吞掉 → 用户看到的是「已登录、数据全是 0、天气卡报未登录或登录已失效」。修法＝会话落 `sessions` 表 + 启动先 `verifySession()`，401 就回登录页。**要守住它，测试就不能只断言"进了工作台"**——必须用页面里存着的 token 真拉一次数据，且**第 2 轮（新后端进程）拿到与第 1 轮相同的数据**才算过） |
| 3️⃣8️⃣ | `tools/openapi_import.py` + `tools/_runtime/api_driver.py` + `tools/capability_invoke.py` + `api/tools_sources.py` + `front/tutorial/api_tools_tutorial.md` | **M1 · API 工具接入**（两个「配置导向」来源适配器之一，v3.0）：建议按「怎么接进来 → 怎么发出去 → 怎么管住」的顺序读 —— ① `openapi_import.py`：把**用户粘贴的 OpenAPI 文档**变成候选能力（`$ref` 递归内联、`operationId` 兜底命名、路径固定 query 拆成 `static_query`、写方法标记）；② `_runtime/api_driver.py`：真发请求（bindings 四位置分发 path/query/header/body + 鉴权注入 + 超时/1MB 真截断 + SSRF 三项口径 + 直连→代理两臂兜底）；③ `capability_invoke.py`：**Agent 侧唯一的执行网关**（工具名就叫 `invoke_tool`），五道闸依次是「能力存在 → 已人工确认 → 已启用 → 参数合 schema → 只读放行」；④ `api/tools_sources.py`：配置面（来源 CRUD / 体检 / 发现候选 / 人工确认）；⑤ 前端 `front/index.html` 的「🔗 API 工具」页 + 自带教程 `front/tutorial/api_tools_tutorial.md`（含 4 张示意图）。思考：① 为什么「未确认」必须挡在**入池**这一层，而不是只在界面上不显示该工具？② 为什么「是不是写操作」必须由**服务端**从 `invoke_spec` 推导，而不能信前端传来的 `read_only`？③ 卡片上那句「调用示例」看着只是省事，它实际省掉的是模型哪一步推理？④ 为什么供应商文档里标「可选」的参数**不一定能省**（Steam `cc`/`l` 的教训），以及为什么修法是「把它固化进能力定义」而不是「在提示词里提醒模型别忘了填」？ |
| 3️⃣9️⃣ | `tools/_runtime/mcp_driver.py` + `tools/mcp_probe.py` + `tools/capability_pool.py`(mcp 分支) + `api/tools_sources.py`(mcp 分支) | **M2 · MCP 工具接入**（另一个来源适配器，v3.0；**与 M1 共用同一套候选/确认/入池/审计/放行链路**）：① `mcp_driver.py`：三种连接（`stdio` 起子进程 / `http` 端点 / `inproc` 仅测试）+ **惰性导入 SDK** + `async→sync` 桥接 + 命令白名单 + 超时与结果上限；② `mcp_probe.py`：**七步体检**（配置解析 → 环境检测 → 协议握手 → `tools/list` → **异常容错** → 性能 → 汇总），一次连接分阶段计时，顺带把工具表交给「发现」复用；③ `capability_pool.py`：`mcp_entry` 与 `api_entry` 抽自同一个 `_entry_from_row`（同池共存、同样进路由注入）；④ 前端「🔌 MCP 工具」页（与 CLI/API 同一范式：常态只读卡 + 弹窗表单/发现结果）。思考：① 为什么 `import mcp` 绝不能写在模块顶层？（实测 5s，且 `tests/live/test_mcp_driver.py` 有两条不变量用例专门钉住它）② MCP 的 `annotations.readOnlyHint` 规范原文说它是什么？为什么它只能用来「预勾选」，不能当安全保证（那"未声明"该按只读还是按写处理）？③ 体检第 ⑤ 步为什么要**故意调一个不存在的工具**？④ 为什么 `stdio` 来源需要命令白名单，而 `http` 来源不需要（两者的风险面差在哪）？⑤ 两种来源的 ref 形状为什么不一样（`api:<短名>#<操作>` vs `mcp:<短名>/<工具>`），这对 SQL 里的 `LIKE` 查询意味着什么？ |
| 4️⃣0️⃣ | `tools/memory_profile.py` + `tools/profile_update.py` + `tools/profile_evidence.py` + `tools/memory_snapshots.py` + `api/customize.py`(画像段) + `agent/build_context.py`(记忆注入) + `front/index.html`(🧠 记忆画像) | **M3 · 用户画像沉淀**（v3.0，**改造既有 `MEMORY.md`，不新建文件**）：读这条线建议按「契约 → 写 → 读证据 → 建议 → 落地」的顺序 —— ① `memory_profile.py`：**格式契约**（`- 维度：事实（来源：X · 日期）`、维度白名单、`FIELD_ALIASES`、`memory_meta` 元数据）——解析/渲染/去重/占位判定**只有这一份**，四条写路径都调它；② `profile_update.py`：给 Agent 的结构化写入工具 `update_user_profile`（模型只说「哪个维度、新值、来源」，**读改写/去重/加来源全交给框架**）；③ `profile_evidence.py`：证据收集（7 张表**带 `表名#行id` 引用**、最近 3 篇周报要点、最近 N 条对话）+ **计数水位**与【画像待更新】信号；④ `api/customize.py`：建议器（`/api/memory/suggest`，**只出建议绝不落盘**）→ 勾选应用（`/apply`）/ 一键导入（`/import`）→ `memory_snapshots.py` 改前必留 3 份快照与一键还原；⑤ `agent/build_context.py`：注入**两段预算**（画像 ≤500 字优先保留情报相关维度 / 笔记 ≤300 字按相关度取前 N、最近 3 条无条件保留）；⑥ 前端「🧠 记忆画像」页：建议差异行（＋/～/−）、逐条勾选（**人工行与白名单外新维度默认不勾**）、一键导入确认、历史版本与还原。思考：① 为什么「同一维度只有一行」必须靠**代码的 upsert** 保证，而不是在提示词里叮嘱模型「别重复写」？（R2 根因：原来只能"读全文→整份重写"，成本高又怕覆盖丢内容，模型于是理性地选择追加）② 为什么「建议」与「应用」必须是两个动作，中间还要人工勾选？③ 待更新信号为什么必须是**服务端算好数字**（"新增了 2 条兴趣"）而不是让模型自己判断该不该更新？④ 为什么人工手写的那一行（来源=人工）任何自动路径都不能覆盖？⑤ 老文件里那些「暂无」占位行为什么要清掉 —— 如果留着，注入给模型之后会发生什么？ |
| 4️⃣1️⃣ | **`docs/v3.1/agent工作与工具调用体系设计图.html`**（单文件交互版，鼠标悬停/点击节点即看该节点的技术栈落点） | **一张图收口整个 Agent 工具体系**（读完全部模块后用它把线索串起来）：左侧「能力池与路由 → 系统提示词装配 → `messages[0]` 注入」＝主 Agent 的**调用依据**；中间虚线框＝**账号内工具面（按账号隔离）**，装着三族工具（内置 `@tool` 开箱即用 / 4 个子 Agent 由主 Agent 用 **task** 调度 / 自配 CLI 与 API·MCP 走**统一执行网关**按 `cli:`·`api:`·`mcp:` ref 分派）＋网关本身（ref 解析 / 参数类型宽容 / 超限载荷整条裁剪并附分页提示）；底部「真实数据源」「审计与度量」与三张浓缩卡（调用依据 / 纳入路径 / 执行护栏）。图由 archify 生成，过 **9 项几何校验（0 交叉 · 0 走廊合并 · 微段 0）**，浏览器实测 4 种视口 × 明暗两套不溢出。**思考题**：① 为什么「内置工具」**不经过**执行网关，而自配工具必须经过？（提示：权限面来源不同——框架内注册 vs 用户配置，后者要面对未声明的写操作与不存在的命令）② 要把一个新 MCP 服务接进来，图里会依次亮起哪几个节点？③ 为什么「能不能跑」和「想不想得起跑」要分两条线治理？ |

## 五、阅读指南（CLI）（建议顺序）

> 智选情报官 **CLI 版**（`dspro` 命令行）的代码阅读路径。CLI 版**不做第二套业务逻辑**——它直接复用 web 版的本地库、Agent 装配层与周报引擎，所以这份指南**只聚焦 CLI 自身的 6 个文件**；凡是「业务逻辑」一律指向 web 版对应模块，对照 [四、阅读指南（Web）](#四阅读指南web建议顺序) 阅读。

> **只关心 Web 版？** 跳到 [四、阅读指南（Web）（建议顺序）](#四阅读指南web建议顺序)。

| 顺序 | 文件 | 看什么 / 思考题 |
| --- | --- | --- |
| 1️⃣ | `cli/main.py` | **CLI 总入口**：`argparse` 装配 4 个子命令（login/logout/whoami/list/digest/chat）。注意：① 子命令用 `set_defaults(func=lambda x: ...)` 挂载，主函数 `args.func(args)` 统一调度；② 错误分层：业务错误用 `CliError`（`main` 统一捕获 → 红字 + 退出码，**不打印堆栈**），真正的 bug 仍走原生 traceback；③ 无参数不报错 → 打印落地页（ASCII 标识 + 用法 + 当前登录态）；④ 全局开关 `--no-color` 挂在父 parser 上，子命令 `add_help=False` 避免 `-h` 冲突。 |
| 2️⃣ | `cli/session.py` | **登录态与账号访问**：① 登录态文件 `data/dspro_session.json` 存**身份 + 账号密码哈希的指纹**（不存明文），密码被改后指纹不匹配 → 旧会话自动失效；② 校验**复用** `api/account.py` 的 `login()`（同一份盐 + 同一张 `accounts` 表），不复制第二套哈希逻辑；③ `require()` 是「取当前登录态 + 校验失效」的统一入口，所有子命令先调它；④ `db()` 返回个人库连接（Row 工厂 + 外键），调用方负责 close。 |
| 3️⃣ | `cli/ui.py` | **终端输出（零依赖）**：① 颜色开关：`--no-color` / `NO_COLOR` 环境变量 / 非 tty → 自动降级纯文本，Windows 老 conhost 手动开 VT；② **东亚宽度**：`unicodedata.east_asian_width(ch) in ("W","F")` 记 2、emoji（`ord(ch) >= 0x1F300`）记 2、ANSI 转义码不计入 → 表格不会因中文/emoji 错位；③ `md_light()` 做 markdown 轻渲染（标题加色加粗、`**x**` 加粗、`` `x` `` 上色、引用加竖线）；④ `progress()` 是 monitor 事件的终端渲染器（`⚙ 调用工具` / `🤝 调度助手` / 思考片段 / ` 子任务完成`）。 |
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

### 6. 跑起来（三种方式任选）

**方式 A：Web 版（M2，推荐）**

```bash
# 启动 FastAPI 服务（默认 127.0.0.1:8123，可自行改端口）
uv run python -m uvicorn api.server:app --host 127.0.0.1 --port 8123
```

浏览器打开 `http://127.0.0.1:8123`：
- **登录页是两张卡**：登录卡只输用户名 + 密码；**没有账号就切到「注册」卡**创建（用户名 + 密码 + 确认密码 + 选「个人账号 / 公司账号」）。**登录绝不自动建号**——打错用户名只会提示「账号不存在」，不会像旧版那样静默建出一个错字账号；
- 登录后按角色进入多页签工作台：**工作台**（总览，含快捷入口）/ **公司信息·个人信息**（维护资料/产品/竞品/兴趣，供 Agent 分析用）/ **AI 助手**（问答 + 右栏 WebSocket 实时进度 + 「🤔 模型思考过程」折叠区 + 回答导出 MD/PDF）/ **报告**（Digest 订阅管理与周报下载）/ **定制助手**（模型选型 + 助手人格设计）；顶栏「🎨 背景」可上传/选择/删除背景图并调透明度；
- **Digest 报告页**：首次进入自动生成默认订阅（公司←竞品清单、个人←兴趣标签），可改关键词、周期（daily/weekly）与**检索语言**（中文/英文/中英双语，`lang` 字段）；点「批量生成（选中 N 个）」手动触发，或等 APScheduler 每天 9 点 / 每周一 9 点自动跑；新报告生成时顶栏红点 + WS 弹提醒。
- **定制助手页**：模型选型区填「名称/模型名/base_url/API Key」四点并「启用」，该账号后续对话即走自定义模型（默认 .env 免费模型）；人格区可手动编辑 SOUL.md，或输入风格描述（如「活泼女仆」）点「✨ AI 生成人格」→「一键导入」→「保存人格」，对话与周报措辞立即体现该人格。

**方式 B：CLI 版（保留可用）**

```bash
# 进入交互式问答（输入 exit / quit / q / 退出 结束）
python main.py

dspro 子命令 #作为CLI工具使用
```

**方式 C：桌面版（pywebview 外壳，双击即用）**

```bash
# 双击这个文件（推荐）——Windows 资源管理器里直接双击
front/desktop/desktop.exe # 有自己的图标、且不弹控制台黑窗

# 后备：front/desktop/desktop.cmd（排查启动问题时能看到完整输出）
# 或者命令行起（等价）
.venv/Scripts/python.exe front/desktop/app.py
# 首次需先装外壳依赖：uv pip install pywebview pystray pillow；
# 若 desktop.exe 不存在，用系统自带编译器生成：python front/desktop/build_launcher.py
```

窗口里跑的就是**方式 A 的那份前端**（同一个后端、同一套 API、同一个库，外壳零改动），只是多了原生外壳：

- **不用先开服务**：外壳自己探测/启动后端（约 7~9 秒就绪，首次装依赖后更快）；**你已经开着服务时它直接复用，退出时也不会关掉你的服务**；
- **记住登录**：关掉窗口再打开，不用重新登录；
- **托盘常驻**：右键图标可「显示主窗口 / 立即生成周报 / 打开导出目录 / 当前账号 / 打开日志 / 退出」；长任务（生成周报、回答中）时托盘提示会显示进度并亮起琥珀点；
- **点窗口 X 收进托盘**（不会退出）；**再次双击 `desktop.cmd` 会把已有窗口唤出来**（不会开出两个）；
- **端口自动换**：8123 被别的程序占着就自动往后试（8123→8127），不用手动指定；
- **后端崩了自动重启**（退避 2s/5s/15s，最多 3 次），恢复后弹通知并刷新页面；
- **导出走原生「另存为」**（可自己选目录与文件名），周报生成完若窗口不在前台会弹系统通知。

> 细节（架构、`.cmd` 编码铁律、排查表、可选项）见 `front/desktop/README.md`。

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

**v2.0 改动规模**：新增/重构 8 个模块（`agent/context_budget.py`、`agent/conversation_store.py`、`agent/build_context.py`、`rag_knowledge/`、`tools/cli_registry.py`、`tools/capability_pool.py`、`tools/tool_router.py`、`cli/`），1 个 SPA 前端（`front/index.html`，单文件 ~8 万字内联 JS）与 1 套 SQLite 个人库扩展（会话/技能/CLI/能力目录/池版本，全部按 `account_id` 隔离），配套 20+ 个端到端脚本。**遗留到 v3.0**：MCP / API 适配器、用户画像沉淀、检索会话级计数器、`build_request_context` 完整落地（SOUL/MEMORY 注入）、价格 MCP 自动化。

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

 - ✅ **周报时效性修复**：查询词泛用化（弃用修饰词拼接）+ `strict_days` 代码层按 published_date 剔旧闻 + `bilingual` 中英双语检索合并 + 订阅级检索语言可选。
 - ✅ **v3.0（M1~M6c）→ v3.1（M6b 度量收尾）**：API/MCP 适配器与混合来源能力池（`CapabilityEntry` 契约 + 配置导向的来源管理）· 用户画像沉淀（白盒 `MEMORY.md` + 证据溯源 + 快照还原）· 用量计数与成本表（只统计 token + 可选单价）· 价格监控 · **测试基线体系**（离线路由基准一键复跑、零额度 + 探针制度 v3：43 条真实工具题集、只记录序列、人工打分 + 主基线 `tests/fixtures/m5b_probe_baseline.json`）均已落地并实测。**v3.0 未完的 M6b 已在 v3.1 做完**；进度表与用户裁决见 `docs/v3.0/README.md` 与 `docs/v3.1/`。**下一件事已立项并已落地主体**：`docs/v3.1/路由质量修复任务书.md`（7 个「选错工具」用例；已批注，工具侧 6 项 + 素材层入库完成，剩一轮 live △ 复跑）。

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
- 🔧 **修复（2026-09-16）：聊天框敲 `/` 唤不出技能菜单**。根因是 `front/index.html` 的 `setup()` 返回表**漏了 4 个事件处理函数**（`skOnInput`/`skOnKey`/`skillPick`/`skillUnpick`）→ 模板把它们绑成 `undefined`：不报错、不崩、界面渲染正常（`v-if`/`v-for` 用的状态变量都在），**只有交互静默失效**。修法：补进 return + 新增**静态不变量用例** `tests/unit/frontend_setup_exports_check.py`（模板引用 ⊆ setup 导出，一次覆盖整类问题）。验证：真机 CDP（无头 Chrome + `websockets` 直连 DevTools）走完 **敲 `/` → 菜单出现 2 个技能 → 真鼠标点击 → 技能 pill 出现**全链路。
- 🔧 **修复（2026-09-22）：`agent/prompts.py` 的调试 print 会让"输出被重定向"的启动直接崩**。该文件 import 时把整份提示词打印到 stdout，而提示词含 emoji（🔍 等）——**当 stdout 不是控制台而是文件/管道时**，Python 按系统 locale 选编码（中文机 = GBK）→ `UnicodeEncodeError` → **进程在 import 阶段就死**。影响面：`uvicorn api.server:app > server.log`、`dspro list > out.txt`、计划任务/CI，以及**桌面版双击**（外壳把后端 stdout 落盘到 `backend.log`，正是这条路径）。已在两行 print 上加注释说明成因（需要时可按注释临时打开）；桌面外壳另在子进程环境强制 `PYTHONUTF8=1` + `PYTHONIOENCODING=utf-8` 兜底。验证：`PYTHONIOENCODING=gbk python -c "import agent.prompts, api.server"` 现在正常退出（修复前必崩），`tests/live/desktop_shell_e2e.py` 108/108 内含该回归与控制断言。
- ✅ **v2.0 收尾（2026-09-16）：登录卡 / 注册卡分离**。旧版是**混合卡**——前端 `doLogin()` 遇到 401（账号不存在）就直接调 `/api/register` 建号再登录，于是**打错用户名 = 静默建出一个能登进去的空账号**（用户实际误建过「尼姑喵喵」，与正确账号「尼古喵喵」一字之差）。改法：① 卡片顶部「登录 / 注册」两个 tab，**两张卡独立**；② 登录卡只留用户名 + 密码（拿掉了原来混在里面、其实是给注册用的「账号类型」字段），注册卡是用户名 + 密码 + **确认密码** + 账号类型 + **「即将创建：xxx（个人账号）」实时回显**（把不可逆操作的后果提前摆到眼前，不打断流程）；③ 移除"登录失败即注册"的整条路径，**登录永不建号**；④ 注册成功**不自动登录**，回登录卡并提示「账号已创建，请用刚设置的密码登录」（拍板口径：更稳）；⑤ 后端语义拆细以支撑引导：**账号不存在 → 404「账号不存在」/ 密码错误 → 401「密码错误」**（原统一 401「用户名或密码错误」会把「用户名打错」误导成"密码打错"），CLI `dspro login` 同步指路 `--register`。**顺带修掉一个同批发现的层级 bug**：全局提示条 `.toast` 原挂在 `<template v-else>`（登录后才渲染的分支）里 → **注册成功的提示在登录页根本看不见**（真机探针查 `.toast` 文本时暴露），已挪到 `#app` 根层且必须排在 `v-if="!token"` 之前（夹在 `v-if` 与 `v-else` 之间会编译报错）。验证：`tests/testclient/auth_split_e2e.py` **43/43**（核心断言 = 用打错的用户名登录后**查库确认没有新账号**；[7] 段追加「会话跨后端重启仍有效」）+ 真机 CDP `scripts/cdp_auth_probe.py` **21/21**（真实键鼠点出两张卡、断言账号真的建/真的没建）+ CLI 回归 **45/45** + 前端不变量 3/3 + `m4a_frontend_logic.js` 56/56。**代价**：账号不存在与密码错误可区分，理论上可枚举账号（本地单机应用，无此风险，用户拍板选择区分）。
- ✅ **提示词系统性重整（2026-09-23，跨里程碑维护）**：`prompt/prompts.yml` 原来是「每个里程碑往后追加一段」的堆叠（M3 知识库纪律 → M4a 技能纪律 → M4c 命令行纪律 → M5a 能力描述纪律），累积出三个问题：① **同一件事散在多处**——命令行纪律同时写在 `prompts.yml`、`agent/build_context.py` 的注入块、`tools/shell_executor.py` 的 `@tool` 文档串、每轮能力卡里（4 处，改一处漏三处）；② **陈旧与自相矛盾的文本仍在模型上下文里**——"公司数据在 MySQL（our_products 等）"（公司侧早已迁 SQLite，`schema_personal.py` 原话「替代 MySQL 全局表」），而且那 4 行带着 `#` 前缀，**YAML 块标量里的 `#` 不是注释、是正文**，模型分不清是规则还是废案；③ **与 M5b 路由设计脱节**——静态提示词还假设「工具清单总是完整的」，没有「只列名称的工具也可调用」的落点。改法：主 Agent 段按「角色 + **指令优先级** → 职责 → 来源可追溯 → **工具使用总则** → **你手上的三类工具** → 记忆 → 技能 → 输出结构」重排，机械细节（argv 形状、只读闸语义、两步链）移到工具说明与注入块，删掉失效段落与 RAGFlow 占位段。**实测主 Agent 2752 → 2218 字（-19.4%）**、**子 Agent 合计 2886 → 2711 字（-6.1%）**，规则一条没删。随后一轮（同日）又补了三件事：① 把「**优先**调用账号内工具」落成**顺序约束**（"问题指向用户自己账号里的对象时，先用账号内工具取真实数据，不足再联网"——实测模型当天倾向 web-first，这条既省外部检索额度也保证首轮答案来自私有数据）；② 三个子 Agent 删掉与 `@tool` 文档串重复的机械细节（表名 / 列名 / 行数上限 / JSON 列 / 检索参数语义）；③ 新增 `tests/unit/test_prompt_contracts.py`（**11 条**）把「提示词 ↔ 代码」的隐式契约钉死：周报头部格式（prompts.yml ↔ `digest_engine.SCAN_LINE_*` ↔ 解析正则）、能力描述三种写法 ↔ `_parse_ability_pairs`、记忆/技能路径必须相对 agents_docs、以及防腐断言（陈旧术语不得回流、CLI 机械细节不得回流进主提示词）。**顺带修掉一个真 bug**：提示词与注入块把记忆文件写成 `agents_docs/{username}/MEMORY.md`，而 `write_agent_doc(filename)` 的路径是**相对 agents_docs** 的 → 模型照抄传参，记忆被写进 `agents_docs/agents_docs/{user}/`（越权校验通过、返回"写入成功"，但加载器读的是 `agents_docs/{user}/MEMORY.md`，**永远读不到**；实测残留 `agents_docs/agents_docs/尼古喵喵/{MEMORY.md,SKILLS.md}`，后者还是模型编造的技能清单）；已改成相对路径，并把**编码了这个 bug 的老断言** `tests/unit/test_assembly.py` 反向钉死（同时断言不再出现带前缀的写法）。验证：`m4a_skills_e2e` 30/30、`m4c_cli_e2e` 109/109、`m5_abilities_e2e` 43/43、`m5b_pool_e2e` 62/62、`m5b_router_e2e` 34/34、pytest 26/26。设计细节与「静态提示词结构」表见 `docs/v2.0/工具路由设计.md` §7.2-b。
- ✅ **桌面版外壳（2026-09-22，v2.0 收官后的支线）**：`front/desktop/`——双击 `desktop.cmd` 得到独立窗口 + 托盘，**后端与现有前端零改动**（同一份 SPA、同一套 API、同一个库）。① **同源加载**是前提：窗口加载后端自己托管的 `http://127.0.0.1:8123/?desktop=1`，页面与 API 同源 → `localStorage`/WebSocket 全部原样可用（后端**没有 CORS 中间件**，用 `file://` 或另起静态端口会立刻跨源、登录与聊天全废）；② **记住登录**：前端在桌面模式（靠 URL 的 `?desktop=1` 判断，不用异步注入的 `window.pywebview`）改用 `localStorage`，**并且**外壳必须 `webview.start(private_mode=False, storage_path=...)`——pywebview 默认 `private_mode=True`，localStorage 每次进程结束都被清空（实测第 1 轮写入、第 2 轮读不到，只有两轮对比测试才抓得到）；③ **再次双击 = 唤出已有窗口**：单实例锁（`socket.bind(端口+1000)`）顺便当 IPC 用——第二实例 `connect` 后发一行 `SHOW`，第一实例的监听线程收到就 `show()+restore()`（原来只是"直接退出"，用户看到的是**双击没反应**）；选端口时**先探锁再判"服务可复用"**，否则会把"已有实例"误判成"可复用的服务"而起第二个窗口；④ **端口自动换**：起始端口被占就自动往后试（8123→8127），不再要求你手动 `--port`（注意"空着"的判定 = `can_bind() and not port_in_use()`：单用 connect 探测会被**只 listen 不 accept 的端口把 backlog 塞满**而误判为空闲）；⑤ **后端崩溃自动重启**：判定基准是**服务通不通**（连续 3 次 HTTP 探测失败）而不是子进程句柄——venv 的 `python.exe` 是 launcher、会再起一个真解释器跑 uvicorn（端口归"孙子进程"），只盯 Popen 句柄既会误判崩溃、退出时也杀不掉真服务；重启带退避（2s/5s/15s，最多 3 次）并自动刷新页面，**复用别人的服务时绝不插手**；⑥ **托盘状态/进度**：长任务时托盘提示变「智选情报官 · 正在生成周报…」/「· 正在回答…」且图标加**琥珀点**（收在托盘也能看到在干活）；⑦ **托盘菜单**：显示主窗口 / 立即生成周报 / 打开导出目录 / 当前账号 / 日志 / 退出；**托盘动作走页面的 `window.__zxDesktop` hook**（外壳不自己拼 API 请求，否则等于把 token 搬出页面、还得重写一遍业务）；⑧ **点窗口 X 收进托盘**（托盘不可用时自动放行关闭，避免"关不掉"）；⑨ 导出走**原生「另存为」**（前端取回字节 → base64 → `save_binary_b64` 落盘）——**原因是被用户逮到的真 bug**：WebView2 **不支持浏览器式下载**，周报列表那两个 `<a :href>` 普通链接点了等于什么都没发生（后端日志里 `GET /api/download 200 OK` 请求确实发出去了，但文件在「下载」文件夹、桌面、临时目录里全找不到），因此**全站 4 个取文件出口统一收口到 `saveFromUrl()`**（AI 回答导出 MD/PDF + 周报列表 MD↓/PDF↓）；周报完成时窗口不在前台则弹**系统通知**；⑩ **进程归属**：只关自己起的 uvicorn（连 launcher 退出后仍占着端口的"孙子进程"一起清），用户自己开的服务绝不碰；子进程输出落 `backend.log` 且监控其生死（起不来时 2s 内报死因，而不是傻等 180 秒）；⑪ **应用图标**：正式设计稿（圆角松绿底 + 白色猫头）已接入——托盘读 `assets/icon.png`（512px；`icon-small.png` 小尺寸补偿版**默认不生成**：裁边会把圆角切出缺口，外壳存在才用、否则回退），任务栏/窗口走 `webview.start(icon=assets/icon.ico)`（**pywebview 文档说 Windows 不支持 `icon=`，实际 WinForms 后端实现了**；不传就抠 `sys.executable` 的图标 = 任务栏显示 Python 图标的原因）+ `SetCurrentProcessExplicitAppUserModelID` 纠正任务栏归组；`front/desktop/make_icon.py` 负责**去白底 + 生成多尺寸 .ico**，`build_launcher.py` 把图标编进 `desktop.exe`（换设计稿时这两个各跑一次）；⑫ **「记住登录」的后半截＝服务端会话落库**（2026-09-23 用户报障修）：会话原本是 `api/account.py` 里的**进程内内存字典**，而桌面壳每次重开都**新起一个后端进程** → localStorage 里的旧 token 全失效；前端又只看"token 非空"就渲染工作台，各接口 401 被 `if (!r.ok) return;` 静默吞掉 —— 用户看到的是「已登录、兴趣/关注/收藏全是 0、天气卡报未登录或登录已失效」，手动退出重登即恢复（这就是报障现象）。修法两侧都补：**服务端**新增 `sessions(token PK, account_id, login_at)` 表（90 天有效期、登录时清过期行，用户名/角色/昵称运行时从 `accounts` 联查），**前端**启动先 `verifySession()`（拿 token 拉一次 `/api/me/overview`），401 就 `forceLogout()` 回登录页并提示（只删 `zx_token/zx_user/zx_role`，不能 `storeClear()` 把背景图偏好一起清掉）。验证：`tests/live/desktop_shell_e2e.py` **108/108**（含**真机**杀掉后端→断言自动拉回）、`tests/live/desktop_window_e2e.py` **45/45**（两轮对比验证「记住登录」——**两轮都用页面存着的 token 真拉一次 `/api/me/overview`**，第 2 轮是**全新后端进程**、必须拿到与第 1 轮一致的**非空**数据；**末尾再用假 token 刷新一次，断言回到登录页并提示失效**；外加 **真跑一遍「点下载→原生另存为→文件落盘」整条链路**）、`tests/harness/desktop_export_logic.js` 24/24、前端不变量 3/3 + `m4a_frontend_logic.js` 56/56。详见 `front/desktop/README.md`。

- 📋 **v2.0 后续规划**：用户画像沉淀、检索会话级计数器、`build_request_context` 完整落地（SOUL/MEMORY 注入）、价格 MCP 自动化等——见 `docs/v2.0/v2.0方案.md` + `docs/v2.0/AstrBot_v2.0对比借鉴.md`。**M5b 的 MCP / API 适配器（原 M5b-3/4）已拍板推迟到 v3.0。** ★ **v3.0 的完整规划（含上面这些计划项、v2.5 收工的遗留总盘、里程碑草案与验收判据、明确不做清单）见 `docs/v3.0/v3.0规划书.md`。**

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
| **v2.0-M2** | 请求装配管线化：agent 静态化 + 人格/记忆 invoke 注入（零重建） | ✅ 已完成（见 `docs/v2.0/M2请求装配管线化.md`） |
| **v2.0-M3** | RAG 知识库：自建 Chroma 每用户一库 + 清洗评估入库 + 快/全双模式检索 | ✅ 已完成（见 `docs/v2.0/M3RAG知识库搭建与使用.md`） |
| **v2.0-M4** | 技能与工具：**M4a 技能**（SKILL.md + `/` 选单 + 前缀注入）✅ / **M4b CLI 面板**（白名单沙箱命令 + 六道闸 + 只读口径）✅ / **M4c 自定义 CLI 接入**（配置导向：自己填命令与只读清单 + 本机检测 + 只读子命令代跑）✅ | ✅ 三段全部完成并端到端实测（M4a 30/30+50/50、M4b 51/51、M4c 105/105），见 `docs/v2.0/M4技能与工具.md` |
| **v2.0-M5b** | 工具检索路由：**统一能力目录**（`CapabilityEntry` 契约 + `tool_capabilities`/`tool_pool_meta` + CLI/API/MCP 三适配器 + 独立向量 collection + pool_version 惰性失效）+ **路由器**（快路径 / 混合检索 RRF / 词法与 z 分数两级置信 / 完整卡·紧凑行·仅名称三形态 / 未命中不隐身 / 全链兜底回退全量） | ✅ **CLI 部分已完成并验证**（单测 62/62 + 34/34；回归 M4c 109/109、M5a 43/43；8 工具标定 10/10 命中、0 猜错、注入量 1644/2499 字；真机加压探针 8/8 正例 + 2/2 反例）；**API / MCP 适配器（原 M5b-3/4）按拍板推迟到 v3.0**，见 `docs/v2.0/M5b工具检索路由.md` |
| **v2.0 收尾** | 登录界面：**登录卡 / 注册卡分离**（后端登录语义拆细：账号不存在 → 404 / 密码错误 → 401）+ 全局提示条挂载层级修复（注册成功提示在登录页看不见）+ `/` 技能菜单静默失效修复 | ✅ 已完成（`tests/testclient/auth_split_e2e.py` 36/36、真机 CDP 21/21、CLI 回归 38/38、前端不变量 3/3、`m4a_frontend_logic.js` 56/56） |
| **v2.0-M5a** | 工具智能路由：`user_clis.abilities` 能力描述（用户语言关键词 + 「说法→命令路径」映射，**需要 ID 的命令写两步链**）+ `agent_brief` 双形态能力路由卡（私有数据优先指令）+ few-shot 映射示例（逐条过只读闸）+ AI 草稿按钮 | ✅ 已完成并端到端验证（探针组 10/10、复跑 9/10、反例 3/3；**同题对照组 M4c 命令名形态仅 2/10**；真实账号 + 真实 weread 15/15），见 `docs/v2.0/M5工具智能路由.md`（检索式路由已由 M5b 落地） |

| **v3.0-M1~M6c** | API 适配器 · MCP 适配器 · 用户画像沉淀 · 计数器与装配收口 · 价格监控 · 成本表与失败分类 | ✅ 全部完成并实测（**M6a 桌面版体验与打包按拍板不做**；进度表与逐项裁决见 `docs/v3.0/README.md`） |
| **v3.1-M6b** | 测试基线（探针基线快照 + 漂移对比）与**路由质量基准**（可一键复跑、完全离线） | ✅ 已完成（`docs/v3.1/`：任务书 · 方案 · 探针问题集v2 + 主基线；**「路由质量修复」已批注并主体落地**——工具侧 6 项 + 素材层入库已完成，验收五指标见任务书 §六；**live 段不跑**（△ 类已基本修复），以任务书 §十 收口） |

> 每完成一个里程碑，对应在 `docs/` 下增补 `M{n}_*实现设计.md` 记录改动与实测，保持文档与代码同步。
