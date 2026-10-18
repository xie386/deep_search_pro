# 智选情报官（deep-search-pro）

一个本地运行的个人 AI 情报助手：你用自然语言提问，它**自己决定调用哪些工具**（联网检索、进站抓取、读你自己的资料库、跑你配的 CLI / API / MCP），把过程实时显示在右栏，最后给你带来源的回答；它还会按你订阅的主题定期生成周报。

- 给谁用：想在自己电脑上跑一个「能查、能写、能定时汇报」的个人情报助手的用户。
- 形态：Web 版（浏览器）+ 桌面版（双击即用的窗口壳）+ CLI（`dspro`）。
- 平台：**Windows**（依赖 `pywin32`；语音朗读、桌面壳为 Windows 特有）。

> 想读代码、看设计取舍与逐文件讲解 → [`开发者阅读指南.md`](开发者阅读指南.md)｜想了解 v4.0 新增了什么 → [`docs/v4.0/README.md`](docs/v4.0/README.md)

---

## 功能特性

- **会自己选工具的多智能体问答**：主 Agent 调度 3 个专职子 Agent（网络搜索 / 个人情报 / 周报）；工具分三族——内置工具、子 Agent 自带工具、你自配的 CLI·API·MCP 来源，按提问相关性挑选。
- **三种联网取法**：「只检索」「只进指定网址」「先检索定位再进站深抓」，由工具签名约束，每轮检索次数有上限。
- **工作目录可见可编辑**：左栏「📁 工作目录」能逐层浏览、直接改文本、预览 docx/xlsx、删除进回收站可还原。
- **写能力有边界**：内置写工具（建/改/删文件）用人话直接可用；你自配来源的写操作必须在输入框用 `/` 选中对应「写工具集」才放行，且只对那一轮生效。
- **固定流程（workflows）**：把多步任务写成流程，逐步执行、失败停步；流程可声明自己要用的写工具集。
- **能真的下载文件**：让它「打开这个网页，把附件/图片下下来」，文件会落进你的工作目录 `_downloads/`，回答里列出文件名与体积，图片直接显示。
- **知识库与周报**：本地自建向量库（导出报告一键入库，检索前自动清洗）；周报按订阅定时生成并推送，支持中英双语检索。

---

## 快速开始

### 1. 前置依赖

| 依赖 | 最低版本 | 说明 |
| --- | --- | --- |
| Windows | 10 / 11 | 桌面壳、语音、`pywin32` 相关功能仅 Windows |
| Python | **3.11.x** | `pyproject.toml` 要求 `>=3.11`；请用 3.11（不要用 3.12+，部分依赖无 cp312 轮子） |
| [uv](https://docs.astral.sh/uv/) | 0.4+ | 包与虚拟环境管理（项目用它生成 `uv.lock`） |
| Git | 2.x | 拉代码 |
| Chrome | 任意较新版本 | 进站抓取（browser-use）复用本机 Chrome，不下 Chromium |

```powershell
# 安装 uv（Windows PowerShell）
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

### 2. 克隆

```bash
git clone https://github.com/xie386/deep_search_pro.git
cd deep_search_pro
```

### 3. 安装依赖

```bash
# 用 Python 3.11 建虚拟环境
uv venv --python 3.11

# 安装项目依赖（依据 pyproject.toml，含 dspro 命令行入口）
uv pip install -e .
```

`pyproject.toml` **之外**还需要按需补装（不在 lock 里，缺失只会让对应功能不可用）：

| 功能 | 安装命令 | 缺失表现 |
| --- | --- | --- |
| 知识库（RAG） | `uv pip install chromadb sentence-transformers` | 知识库页/检索报 `ModuleNotFoundError` |
| 语音朗读 | 见 `voice_test` 项目（Qwen3-TTS），需与本机 GPU 匹配 | 「🔊 朗读」不可用 |
| 桌面版外壳 | `uv pip install pywebview pystray pillow` | 双击 `desktop.exe` 无反应 |
| 语音输入（ASR） | `uv pip install sherpa-onnx`（推理引擎，需独立 venv） | 长按说话无反应 |

> 需要 GPU 加速时，请先按本机 CUDA 版本安装对应的 `torch`（例如 CUDA 12.1：`uv pip install torch --index-url https://download.pytorch.org/whl/cu121`），再装 `sentence-transformers`。

### 4. 配置

```bash
cp .env.example .env
```

然后编辑 `.env`。**最少要填 3 个变量**（见下一节表格）；其余留空即可启动。

### 5. 启动

**A. Web 版（推荐）**

```bash
uv run python -m uvicorn api.server:app --host 127.0.0.1 --port 8123
```

**B. 桌面版（双击窗口）**

```bash
front/desktop/desktop.cmd
```

**C. 命令行版**

```bash
python main.py      # 交互式问答，输入 exit / quit / q 退出
dspro --help        # 打包好的 CLI 子命令（登录、列数据、生成周报、单轮问答）
```

### 6. 确认启动成功

- 浏览器打开 **http://127.0.0.1:8123**，能看到**登录页两张卡**（「登录」与「注册」）；
- 没有账号就切到「注册」卡：用户名 + 密码 + 确认密码 + 账号类型（个人 / 公司）→ 注册后用该密码登录；
- 登录后进入工作台，左侧可见「工作台 / AI 助手 / 报告 / 定制助手 / 技能与工具 / 📁 工作目录 / 设置」等入口；
- 在「AI 助手」里问一句「今天有什么科技新闻」，右栏出现工具调用进度，说明模型与工具链都通了。

---

## 配置说明

`.env` 的完整键名见 `.env.example`（22 项）。常用项：

| 变量名 | 必填 | 默认值 | 说明 | 示例 |
| --- | --- | --- | --- | --- |
| `LLM_MODEL_MAX` | 是 | 无 | 主模型名（OpenAI 兼容接口）。**必须选支持视觉/多模态的模型** —— 进站抓取在 JS 浮层上要靠看截图取字段，纯文本模型会反复超时 | `gpt-4o` / `qwen-vl-max` |
| `OPENAI_API_KEY` | 是 | 无 | 模型 API Key | `sk-xxxx`（只放 `.env`，不入库） |
| `OPENAI_BASE_URL` | 是 | 无 | 模型接入地址（兼容 OpenAI 协议的任意服务） | `https://api.openai.com/v1` |
| `TAVILY_API_KEY` | 否 | 空 | 网络搜索密钥；不填则「联网检索」不可用 | `tvly-xxxx` |
| `EMBEDDING_MODEL_PATH` | 用知识库时必填 | 空 | 本地向量模型目录（知识库与工具路由用）；不填时工具路由退回关键词匹配，知识库不可用 | `D:/LLM/bge-large-zh` |
| `RERANKER_MODEL_PATH` | 用知识库时必填 | 空 | 重排序模型目录（周报全模式检索用） | `D:/LLM/bge-reranker-base` |
| `EMBEDDING_DEVICE` | 否 | `cpu` | 向量模型运行设备 | `cuda` |
| `WEATHER_CITY` | 否 | 成都 | 工作台天气卡默认城市（Open-Meteo，免 Key） | `上海` |
| `ASR_ENABLED` / `ASR_LANG` / `ASR_MODEL_DIR` | 否 | `1` / `zh` / 空 | 语音输入开关、语言、模型目录（`ASR_MODEL_DIR` 指向本地 ASR 模型） | `1` / `zh` / `D:/LLM/asr` |
| `QQMAIL` / `QQMSTP` | 否 | 空 | 周报邮件通道（发件邮箱 / 授权码）；只在 `.env`，界面不回显 | — |
| `ANONYMIZED_TELEMETRY` | 否 | `false` | browser-use 匿名遥测，默认关闭 | `false` |

> 上表 22 个键的完整注释见 `.env.example`（每段都写了「什么情况下必填」）。
>
> **密钥规矩**：真实值只放 `.env`（已 git 忽略）；界面上配置的第三方密钥会写回 `.env`，数据库里只留键名。

---

## 常用命令

| 用途 | 命令 |
| --- | --- |
| 启动 Web 服务 | `uv run python -m uvicorn api.server:app --host 127.0.0.1 --port 8123` |
| 桌面版 | `front/desktop/desktop.cmd` |
| CLI 交互问答 | `python main.py` |
| CLI 子命令 | `dspro login` / `dspro list` / `dspro digest` / `dspro chat` |
| 单元测试与静态契约 | `uv run python -m pytest tests/unit -q` |
| 前端 harness（Node） | `node tests/harness/m4a_frontend_logic.js` |
| 全部 harness（经 pytest 总闸） | `uv run python -m pytest tests/unit/test_harness_gate.py -q` |
| 端到端（进程内 TestClient） | `uv run python -m pytest tests/testclient -q` |
| 真机 / 真窗口（慢，注意额度） | `uv run python -m pytest tests/live -q` |

> 跑测试前建议先 `unset PYTHONPATH`（避免外部同名包干扰）；真机测试会调真实模型，注意额度。

---

## 目录结构

```text
deep_search_pro/
├── main.py                  # CLI 交互入口（主 Agent + 子 Agent 装配）
├── dspro / dspro.cmd        # 打包好的命令行入口（login / list / digest / chat）
├── pyproject.toml / uv.lock # 依赖清单与锁文件（uv 管理）
├── .env.example             # 环境变量示例（复制成 .env 后填）
├── 开发者阅读指南.md         # 代码阅读顺序、思考题与逐文件讲解
│
├── agent/                   # 智能体核心：模型接入、请求装配、会话、思考捕获、周报引擎
│   └── subagents/           # 4 个子 Agent 的装配
├── api/                     # FastAPI 服务层：REST + WebSocket + 各业务 API
├── cli/                     # CLI 版实现（dspro）
├── prompt/prompts.yml       # 全部提示词（主 Agent / 子 Agent / 各撰写 AI）
├── tools/                   # 内置工具 + 能力池/路由 + 执行网关
│   └── _runtime/            # 内部实现：shell / api / mcp 驱动（外部不直接 import）
├── rag_knowledge/           # 知识库：切分、清洗、评估、检索
├── front/                   # 前端一切：index.html（免构建 Vue3 SPA）、教程素材、桌面壳
├── utils/                   # 通用小工具（路径、密钥、设置、审计）
├── scripts/                 # 运维与探针脚本（预览生成、CDP 探针、数据迁移）
├── tests/                   # unit / harness / testclient / live / debug / fixtures
├── docs/                    # 版本文档：v1.0 ~ v4.0（规划 / 调研 / 裁定 / 实现记录）
├── user_data/               # ★ 账号级用户数据（不入库）：画像、技能、工作目录、背景图、向量库
└── data/                    # 运行时数据（不入库）：personal.db、审计日志、缓存
```

---

## 常见问题（FAQ）

**1. 启动报端口被占用（8123）**
`uvicorn` 会直接失败。换端口即可：`uv run python -m uvicorn api.server:app --port 8124`；桌面壳会自己从 8123 往后找可用端口。

**2. `uv sync` 之后某些功能坏了（知识库/语音/桌面版）**
`uv sync` 会按 `uv.lock` **收掉不在锁里的包**（`chromadb`、`sentence-transformers`、`torch`、`pywebview` 等是手动安装的）。日常请用 `uv pip install -e .` 或 `uv pip install <包>`，不要用 `uv sync` 清理环境。

**3. Python 版本不对（3.12+）导致装不上/跑不起来**
请用 `uv venv --python 3.11` 重建环境。部分依赖（如 Chroma 的 onnxruntime、`pywin32`）对 3.12+ 支持不全。

**4. `opentelemetry` 相关导入报错**
`pyproject.toml` 里锁了 `opentelemetry-api<1.45`（`chromadb` 依赖它内部一个已被移除的类型）。升级依赖时别放宽这一条。

**5. 联网检索/进站一直失败**
- 检索失败先查 `TAVILY_API_KEY` 是否有效、额度是否用完；
- 进站依赖本机 Chrome：确认已安装 Chrome，且**本机 127.0.0.1 不要走系统代理**（项目内部已对 localhost 设 `NO_PROXY`，但系统级代理软件可能仍拦截）；
- 企业网络下需要放行 `api.tavily.com`、目标站点与 `127.0.0.1`。

**6. 桌面版双击没反应 / 白屏**
先跑 `front/desktop/desktop.cmd`（带控制台，能看到原因）；常见原因是 WebView2 运行时缺失，或 `pywebview`/`pystray`/`pillow` 没装。日志在桌面外壳自己的 `desktop.log` / `backend.log`。

---

## 贡献与许可

- 这是一个**个人自用项目**，没有对外贡献流程；如果你 fork 后要改，建议先读 [`开发者阅读指南.md`](开发者阅读指南.md) 与 `docs/` 下对应版本的规划文档（每个版本的「明确不做」清单比路线图更有用）。
- **许可证：TODO（仓库内暂无 LICENSE 文件）** —— 在补充许可证之前，请按「保留所有权利」理解，不要直接商用或再分发。

---
