# 05 调研：网络搜索子Agent升级

> **性质**：调研期产物（信息堆砌、允许多方向与废案）；实现期只许定点查（见 `../版本文档规范.md` §六）。
> **本文件口径（用户 2026-10-14 指令）**：只写**调研事实结论**，不生成任何待定方向或决定；本次大点的需求与目的由用户在调研结束时说明。
> **取证方式**：全部为**只读** —— 官方文档/仓库抓取 + 本仓库代码只读探测；**未改任何代码、未动任何数据**（调研期纪律）。
> **抓取日期**：2026-10-14。官方文档会变动，引用处均附 URL。

## 〇、任务口径（用户原话）

> 「05网络搜索子Agent进入调研期,请进行两个方向的调研 1.到Tavily官网,查看Tavily是否还有哪些使用方式、可用参数,以优化当前项目的网络搜索tool?; 2.阅读新引起@url:`https://github.com/browser-use/browser-use`的使用方法以及评估对当前项目接入的可适配性,把这些结论覆盖到(不要追加)调研文件中,只写调研事实结论,不生成任何待定方向或者决定,我会在调研结束说明本次大点的需求和目的」

编号口径：05 = 网络搜索子Agent升级；原 05（沙箱 / 工作目录可视化编辑 + Agent 写能力）推迟为 **06** —— 见 `../Verdict/00-版本级选题与范围.md` **裁定 5**。

---

## 一、本次大点的需求与目的（用户 2026-10-14 口径，逐字）

> 「需求:1.如方向1调研所见(当然我如果理解错了可以纠正我),Tavily实际上很像在做词法匹配检索,做的是词法匹配到的网络信息条目进行摘取返回,事实上,没有直接进入某个特定网址在网站内操作交互返回信息的功能,早在v1.0时期就出现了问题:公司账号的竞品栏可以填写竞品的官网网址,但是一但填写,Tavily基本瘫痪报错或者无关信息,因为当时设计的就是把网址当成topic之一传入,很显然,没有topic会是网址,于是不得不把这个可填项置空,搜索出来的价格也就只能是不准确的,当然,不仅是是想要准确的价格,随着用户需求的发展,许多时效要求高的信息确实只能从特定网址拿取一手资料,粗略的Tavily匹配检索已经不好满足需求;05大点就是想用Python Library的方法集成browser-use,一个可以进行网址操作的工具,以解决这个问题」

**需求要点（据用户口径整理，不作发挥）**：

1. **痛点**：Tavily 属「词法匹配检索」（对匹配到的网络条目做摘取返回），取不到**特定网址的一手资料** —— 时效要求高的信息尤其如此；价格因此只能是不准确的。
2. **历史（v1.0 时期）**：公司账号·竞品栏的「竞品官网网址」一旦填写，检索基本瘫痪报错或返回无关信息（用户判断：当时把网址当 `topic` 之一传入）。
3. **目的**：**05 用 Python Library 方式集成 browser-use**（「一个可以进行网址操作的工具」）来解决上述问题。

### 1.1 需求所依赖的事实核对（2026-10-14 只读实测）

**（a）关于「Tavily 有没有进某个网址的能力」—— 需一处修正**（用户明确说了可以纠正）

| 用户表述 | 核对结论（依据 2026-10-14 抓取的官方文档） |
|---|---|
| 「没有直接进入某个特定网址…返回信息的功能」 | **部分不成立**：Tavily 有 `POST /extract`（给定 URL → 清洗后正文；每 5 个成功 URL 计 1 分，失败不计费）、`POST /crawl`、`POST /map`（站点图遍历与批量抽取）—— 「进某个网址把内容取回来」是**有**的（见 §二 方向一 1.1 / 1.3） |
| 「在网站内**操作交互**返回信息」 | **成立**：官方 `/search` `/extract` `/crawl` `/map` `/research` 全是「给参数 → 返回数据」，**没有**点击 / 填表 / 翻页 / 滚动 / 登录态流程 / 下载等交互动作，也没有「JS 渲染后再操作」的语义 —— **交互这一层确实不在 Tavily 的能力集里** |
| 「当时把网址当 `topic` 之一传入 → 瘫痪报错」 | **现行形态可解释**：工具 `internet_search` 的 `topic` 是枚举 **`Literal["news","finance","general"]`**（`tools/tavily_tool.py:35`），传网址必落空/报错。v1.0 那行历史代码本次无法回溯，只能核对现行工具面 |

> 事实层结论：用户判断中「**没有交互**」成立；「完全不能进网址」不成立（`/extract` 就是干这个的）；「填了官网就瘫痪」另有**用法侧**原因（把网址塞进枚举参数），不是 Tavily 的能力边界。

**（b）项目侧「竞品官网」这项数据的现状**

| 事实 | 证据 |
|---|---|
| 竞品官网**字段存在且可填**：前端「官网（可选）」输入框 | `front/index.html:2596`（`c.website`）· `:2603`（`newComp.website`） |
| 后端**持久化**它 | `api/me_user_data.py:240/252/254/283/285/516`（`company_competitors.website` CRUD）· `tools/schema_personal.py:368/396/962` |
| **实测有数据**：`company_competitors` 共 2 行，`website` **非空 2 行** | 只读查询：`至本洗面奶 → https://www.zhiben.cn/?products_1/` · `溪木源洗面奶 → https://www.simcaregroup.com/product` |
| 但全仓**没有一处把它喂给检索**：`website` 只出现在 CRUD 与一处价格分组列名回退；**agent 上下文 / 提示词 / 搜索工具里都没有** | 全仓 grep `\bwebsite\b`（`api/` `tools/` `agent/`）命中仅：`api/me_user_data.py`（CRUD）、`tools/schema_personal.py`（建表/加列）、`tools/price_mentions.py:87`（列名回退） |
| **产品侧 URL 列实测全空**（与用户「不得不把这个可填项置空」一致） | `products.url` 7 行全空 · `company_products.url` 6 行全空 |
| 现行工具不可能把网址当检索类别（`topic` 是枚举） | `tools/tavily_tool.py:33-40` |

> 事实层结论：竞品官网现在的状态是「**填了、存着、没人用**」—— 既没进检索、也没进提示词，是死数据；「置空」在产品侧 `url` 列上成立（全空），竞品官网这栏则仍有 2 条实填值。

**（c）browser-use 能覆盖的能力面**（依据见 §三 方向二 2.3 / 2.5，此处只做能力对照）

| 能力 | Tavily（现行五端点） | browser-use（Python 库） |
|---|---|---|
| 关键词检索、跨站发现 | 有（`/search`） | 无独立检索（需先有 URL 或自行导航） |
| 给定 URL 取正文 | 有（`/extract`；`/crawl`、`/map` 可批量） | 有（打开页面后读 DOM / 截图，可用 `page_extraction_llm` 或结构化输出） |
| **站内交互**（点击 / 填表 / 翻页 / 滚动 / 登录态 / 下载） | **无** | **有**（官方实现 31 个浏览器动作；支持 `Tools()` 自定义工具） |
| 时效性保障方式 | 服务端时间参数 + 结果排序（`time_range` / `filter_by_published_date`，见 1.2） | 直接读页面**当前**内容（拿的是一手实时页面） |

---

（出处：官方文档，2026-10-14 抓取）

### 1.1 端点全集（出处：<https://docs.tavily.com/documentation/api-reference/introduction>）

| 端点 | 官方文档中的定位 |
|---|---|
| `POST /search` | 「Tavily's powerful web search API」 |
| `POST /extract` | 「content extraction API」（指定 URL 的正文抽取） |
| `POST /crawl` · `POST /map` | 「intelligent sitegraph navigation and extraction tools」 |
| `POST /research` | 「comprehensive research API for in-depth analysis」 |
| `/logs` | 按项目/会话过滤请求日志（与项目跟踪配套） |

- Base URL：`https://api.tavily.com`；认证：`Authorization: Bearer tvly-…`。
- **项目跟踪**：请求可带 `X-Project-ID` 头（SDK 侧是 `TavilyClient(project_id=…)` 或环境变量 `TAVILY_PROJECT`）；官方说明**同一 key 可挂多个项目**，可在 `/logs` 与平台用量看板按项目过滤。
- **会话跟踪**：可带 `X-Session-Id`（把一组相关请求归到一次用户任务/对话）与 `X-Human-Id`（一个 key 服务多终端用户时区分；官方说明会先哈希再处理/存储）。官方注明：**走 MCP 时 `X-Session-Id` 自动填充**，`X-Human-Id` 需调用方自己给。
- 另有官方 **MCP server**（文档 <https://docs.tavily.com/documentation/mcp>，本次未展开抓取）。

### 1.2 `/search` 全参数表（出处：<https://docs.tavily.com/documentation/api-reference/endpoint/search>）

| 参数 | 取值 / 默认 | 官方说明要点 | 本项目现状（`tools/tavily_tool.py` 只读核对） |
|---|---|---|---|
| `query` | 必填 | 建议 < 1500 字符 | 已用（工具入参） |
| `search_depth` | `advanced` / `basic`(默认) / `fast` / `ultra-fast` | 延迟 vs 相关性；**计费：basic/fast/ultra-fast = 1 分，advanced = 2 分**；`advanced` 覆盖更广，适合冷门/极新/多侧面的问题 | **未传**（走默认 `basic`） |
| `chunks_per_source` | 1~3，默认 3 | 每条来源的片段上限；`basic`/`fast` 约 500 字符/块、`advanced` 约 800 | **未传** |
| `max_results` | 0~20，默认 10 | 返回条数 | 已用（工具入参） |
| `topic` | `general`(默认) / `news` / `finance` | 检索类别 | 已用（工具入参） |
| `time_range` | `day`/`week`/`month`/`year`（或 `d/w/m/y`） | **服务端**按发布/更新时间过滤 | **未传**（本项目自实现 `days` 传参 + 客户端过滤） |
| `start_date` / `end_date` | `YYYY-MM-DD` | 同上，区间形式 | **未传** |
| `include_published_date` | 默认 false | 每条结果附 `published_date`（无日期返回 null；`topic=news` 自动开）；官方标注 **beta** | **未传**（本项目自行解析日期字段） |
| `filter_by_published_date` | 默认 false | **剔除**时间窗之外的结果（无日期的也剔除）；置 true 会连带开启 `include_published_date` | **未传**（本项目用客户端 `strict_days` 做同类过滤） |
| `include_answer` | 默认 false（`basic`/`true`/`advanced`） | LLM 生成的答案 | **未传** |
| `include_raw_content` | 默认 false（`markdown`/`true`/`text`） | 每条的清洗后正文 | 已用（工具入参） |
| `include_images` / `include_image_descriptions` / `include_favicon` | 默认 false | 查询相关图片 / 图片描述 / favicon | **未传** |
| `include_domains` / `exclude_domains` | ≤300 / ≤150 域名 | 白名单 / 黑名单 | **未传** |
| `include_domains_mode` | `restrict`(默认) / `prefer` | 白名单是硬限制还是「偏好」（在没有 `include_domains` 时设它会 400） | **未传** |
| `country` | 国家枚举（**仅 topic=general 可用**） | 提升某国内容 | **未传** |
| `language` / `filter_by_language` | ISO 639-1（如 `zh-cn`）或英文名；默认只加权不过滤 | 语言加权 / 严格过滤（未设 `language` 就设 filter 会 400） | **未传** |
| `auto_parameters` | 默认 false | 按 query 自动配置参数（显式值优先）；`include_answer`/`include_raw_content`/`max_results` 仍须手填；**每次 2 分** | **未传** |
| `exact_match` | 默认 false | 只返回含引号内精确短语的结果（绕过同义/语义变体） | **未传** |
| `include_usage` | 默认 false | 响应里带积分消耗 | **未传** |
| `safe_search` | 默认 false | 过滤成人/不安全内容（`fast`/`ultra-fast` 不支持） | **未传** |
| `days` | **现行官方参数表中未见** | —— | **本项目在传**（`_search_once` 中 `days>0` 时传入；官方现行时效口径已改为 `time_range`/`start_date`/`end_date` + `filter_by_published_date`） |

**响应字段**（同出处）：`query` · `answer` · `images` · `results`（按相关性排序）· `response_time` · `auto_parameters`（开启时回显实际采用的参数）· **`usage.credits`** · `request_id`。

### 1.3 计费与额度（出处：<https://docs.tavily.com/documentation/api-credits>）

| 项 | 官方数字 |
|---|---|
| 免费层 | **1,000 credits / 月**（无需信用卡） |
| 付费 | Pay-as-you-go **$0.008 / credit**；月付方案 $0.0075~$0.005 / credit（Researcher 1k 免费 · Project 4k/$30 · Bootstrap 15k/$100 · Startup 38k/$220 · Growth 100k/$500 · Enterprise 定制） |
| `/search` | `basic` = **1 分/次**；`advanced` = **2 分/次** |
| `/extract` | 每 **5 个成功 URL** = 1 分（basic）/ 2 分（advanced）；**失败不计费** |
| `/map` | 每 **10 个成功页面** = 1 分；带 `instructions` 则 2 分 |
| `/research` | **动态计费**：`model=mini` 每次 4~110 分；`model=pro` 每次 15~250 分 |

### 1.4 官方最佳实践（出处：<https://docs.tavily.com/documentation/best-practices/best-practices-search>）

- **query 保持 < 1500 字符**，写法按「给 agent 做网络搜索的 query」而非长提示词；
- **复杂/多主题查询拆成多个聚焦子查询**（官方示例：竞品 / 财务 / 近期动态 各发一次）；
- `search_depth` 延迟-相关性对照：`ultra-fast`（最低延迟/较低相关性，返回 content）→ `fast`（低/良好，chunks）→ `basic`（中/高，chunks）→ `advanced`（较高/最高，chunks）；
- 返回内容两种形态：**Content**（NLP 页面摘要）与 **Chunks**（按 query 相关性重排的短片段）；官方注明 `basic` 以前返回单页摘要、**现在也返回重排 chunks**；
- `advanced` 的适用描述：冷门主题、极新页面、含多个不同侧面的问题、以及**较长较细的 query**。

### 1.5 与本项目现状的事实差（只列事实）

- 本项目 `/search` 只传 **5 个官方参数**：`query` / `topic` / `max_results` / `include_raw_content` / `days`；官方参数表中另有 **`search_depth`、`chunks_per_source`、`time_range`/`start_date`/`end_date`、`filter_by_published_date`、`include_answer`、`country`、`language`、`include_domains`/`exclude_domains`(+`_mode`)、`exact_match`、`auto_parameters`、`include_usage`、`safe_search`、图片/favicon 类**均未传。
- 本项目**在传的 `days` 在现行官方参数表中已不可见**；官方现行时效口径为服务端 `time_range`/`start_date`/`end_date` + `filter_by_published_date`，本项目目前是**客户端**按 `published_date` 解析剔除（`strict_days`）。
- 官方提供**服务端积分回读**（`include_usage` → `usage.credits`）与**用量可追踪**（`X-Project-ID` + `/logs` + 平台看板 + `X-Session-Id`/`X-Human-Id`）；本项目代码里**没有**这些调用（`agent/usage_counter.py` 只把这类工具按名称归类为「外部检索」，不读官方用量）。
- 官方另有 `/extract`（每 5 个成功 URL 才 1 分）与 `/crawl` `/map` `/research`；本项目**只用 `/search`**（`tools/cli_docs.py` 的 URL 抓取走普通 HTTP，不经 Tavily）。
- SDK：官方 `tavily-python` 提供**同步 + 异步**两个客户端，覆盖 `search` / `extract` / `crawl` / `map`（出处：<https://docs.tavily.com/sdk/python/quick-start>）；本项目直接 `from tavily import TavilyClient`（同步），并自实现 REST 直连兜底（`_search_direct`，`trust_env=False`）。

---

## 三、方向二：browser-use 事实与接入面现状对照（出处：官方 repo + 文档，2026-10-14 抓取）

### 2.1 仓库与许可（出处：<https://github.com/browser-use/browser-use>）

| 项 | 事实 |
|---|---|
| 定位 | 「Agents that use the browser」—— 让 AI 控制浏览器的开源库（Python / TypeScript） |
| 许可 | **MIT** |
| 规模（抓取当日） | repo 页显示 **117,731 stars** / 13,025 forks / 480 watchers；文档页自述「79k+ GitHub stars」（两处口径不同） |
| 历史 | 创建于 2024-10-31；138 releases / 149 tags |
| 结构 | `browser_use/`（库本体）· `examples/` · `skills/` · `tests/` · `docker/` + Dockerfile · `pyproject.toml` |

### 2.2 三条使用路径（同出处 README FAQ）

1. **Fully Hosted Cloud**：任务交给其托管 API（agent + 浏览器 + 基建都由它运行）；
2. **CLI**：给已有 agent（README 点名 Claude Code / Codex / **Hermes** / OpenClaw 等）浏览器能力，可交互也可脚本化；
3. **Python Library**：在自己的应用里跑开源 agent，支持自定义工具、结构化输出、自选模型。**（本次评估对象）**

> 官方明确：**开源库（browser-use）与托管 SDK（browser-use-sdk）API 不同**；Cloud 侧另有并发/速率/积分等完整口径（本次未展开，与库本体无关）。

### 2.3 Python 库用法（出处：README Quickstart · <https://docs.browser-use.com/open-source/quickstart>）

- 安装：`uv pip install browser-use` + `uvx browser-use install`（后者装 Chromium）；Quickstart 的示例环境是 `uv venv --python 3.12`，README 写 **Python >= 3.11**。
- 最小用法：

```python
import asyncio
from browser_use import Agent, ChatOpenAI

async def main():
    llm = ChatOpenAI(model="…")
    agent = Agent(task="…", llm=llm)
    history = await agent.run()
    print(history.final_result())

asyncio.run(main())
```

- 关键 Agent 参数（出处：<https://docs.browser-use.com/open-source/customize/agent/all-parameters>）：`tools`（工具注册表）· `browser` · `output_model_schema`（Pydantic 结构化输出）· `use_vision`（默认 `True`；`"auto"` 才按需截图）· `vision_detail_level` · `page_extraction_llm`（单独用于页面抽取的模型）· `fallback_llm`（主模型失败时切换）· `max_actions_per_step`(5) · `max_failures`(5) · `use_thinking` · `flash_mode` · `initial_actions`（不经模型的初始动作）· `save_conversation_path` · `sensitive_data` · `available_file_paths` · `generate_gif` · `calculate_cost`。
- 自定义工具：`Tools()` + `@tools.action(description=…)` 注册函数（README 给了完整示例）。
- 官方自家模型 `ChatBrowserUse`（`bu-2-0`）：输入 $0.60 / 缓存 $0.06 / 输出 $3.50 每 1M tokens（出处：<https://docs.browser-use.com/open-source/supported-models>）。
- 超时可按事件用环境变量调，含 **CDP 连接** `TIMEOUT_BrowserConnectedEvent=30.0`、导航 30s、点击 15s、输入 60s、截图 15s 等。

### 2.4 模型接入（出处：<https://docs.browser-use.com/open-source/supported-models>）

- 原生支持 **15+ 供应商**（Claude / Gemini / Llama / DeepSeek 等）。
- **关键原文事实**：「You can use **any OpenAI compatible model** by passing the model name to the `ChatOpenAI` class **using a custom URL** (or any other parameter that would go into the normal OpenAI API call).」
- 也可经 `ChatBrowserUse` 走它的网关使用 Claude/Gemini 等（需 `BROWSER_USE_API_KEY`，属其托管服务）。

### 2.5 浏览器接入（出处：<https://docs.browser-use.com/open-source/customize/browser/all-parameters>）

| 能力 | 事实 |
|---|---|
| 独立浏览器 | `uvx browser-use install` 装 Chromium；`executable_path` 可指自定义安装（文档给了 Windows 示例 `C:\Program Files\Google\Chrome\Application\chrome.exe`）；`channel` 可选 `chromium`/`chrome`/`msedge` 等 |
| **接既有浏览器（CDP）** | **`cdp_url`**：连到已运行的浏览器实例（文档示例 `"http://localhost:9222"`）—— 直接走 CDP，无 Playwright 式分叉 |
| 用系统 Chrome | `Browser.from_system_chrome()`（自动探测可执行文件与用户数据目录）/ `Browser.list_chrome_profiles()`；**官方警告：用之前可能要先完全关闭 Chrome**，否则 profile 被两个实例同时占用 |
| Profile / 登录态 | `user_data_dir`（默认自动临时目录；`None` = 隐身）/ `profile_directory`（如 `'Profile 1'`）/ `storage_state`（cookie/localStorage，可文件或 dict） |
| **代理** | `ProxySettings(server='http://host:8080', bypass='localhost,127.0.0.1', username=…, password=…)` —— **bypass 是它自带的参数** |
| 域控 | `allowed_domains` / `prohibited_domains`（模式 `example.com`、`*.example.com`、`http*://example.com`、`chrome-extension://*`；TLD 通配符被禁） |
| 其它 | `headless`（默认自动判定）· `window_size`/`viewport` · `keep_alive` · `accept_downloads`/`downloads_path` · `record_video_dir`（需另装 `browser-use[video]`）· `record_har_path`（网络 trace）· `traces_dir` · `args` |
| 行为/等待 | `highlight_elements`(默认 True) · `paint_order_filtering`(默认 True) · `minimum_wait_page_load_time`(0.25s) / `wait_for_network_idle_page_load_time`(0.5s) / `wait_between_actions`(0.1s) |

### 2.6 接入面现状对照（只列事实）

| 维度 | browser-use 的要求 / 事实 | 本项目现状（只读核对） |
|---|---|---|
| Python | README：>= 3.11（Quickstart 示例用 3.12） | venv = **Python 3.11.15**（硬约束：`.pyd` 为 cp311 编译、`pyvenv.cfg` 不得指向 3.12） |
| 安装方式 | 官方 `uv pip install browser-use`（+ `uvx browser-use install`） | 本项目**禁 `uv add` / `uv sync`**，只允许 `uv pip install` + `uv pip check` —— 与官方装法相容；**依赖冲突未实测**（见 2.7） |
| 模型侧 | 任何 OpenAI 兼容模型（自定义 URL 即可）；`use_vision` 默认 True | 模型由用户在「定制助手」自填 `base_url`/`model`/`api_key`，且**已有 `supports_vision` 标记**（`llm_providers.supports_vision`，`tools/schema_personal.py`） |
| 浏览器侧 | 可自带 Chromium / `executable_path` 指本地 Chrome / **`cdp_url` 接既有浏览器** | 本项目**已有 CDP 驱动 Chrome 的实践**（`tests/test_out/_client_test.py`、`scripts/cdp_*.py`，走独立临时 profile）；项目内既有教训是「读用户浏览器 profile 会被残留进程锁 → 用独立 Chrome + 临时 profile」，与 2.5 官方 `from_system_chrome` 的警告同因 |
| 网络 | `ProxySettings(…, bypass='localhost,127.0.0.1')` 自带绕过 | 本机有系统代理 `127.0.0.1:7897`；项目既有纪律是「本机地址必须绕代理」（代码里多见 `ProxyHandler({})` / `trust_env=False`） |
| 运行形态 | 库是 **asyncio**（`await agent.run()`） | 本项目 agent 调用为**同步阻塞**（`api/server.py` 用 `asyncio.to_thread` 把同步 agent 丢线程池）；子 Agent 目前是 langchain 工具 |
| 数据落盘 | `save_conversation_path` · `generate_gif` · `record_video_dir` · `record_har_path`/`traces_dir` · `downloads_path` | 本项目产物归口 `user_data/output/{账号}/`，临时产物进 `tests/test_out/`（见 `文件存放规则.md`） |
| 平台 | 文档示例覆盖 macOS/Linux/Windows；Claude toolset 集成提到 Bash 需 Linux/macOS（Windows 用 WSL）—— **那是该集成的要求，不是库本体的要求** | 本机 Windows 11 |
| 许可 | MIT | 允许使用/修改/分发（需保留版权与许可声明） |

### 2.7 本次未验证的事项（如实列出，避免把「文档说的」当成「本机实测的」）

1. **未在本机安装、未试跑** browser-use（调研期只读，未做任何安装或环境改动）→ 依赖树、体积、是否与既有 `torch`/CUDA/`sentence-transformers`/`chromadb`/`pywebview` 等包冲突，**未实测**。
2. **Python 3.11.15 上是否完全可用**：README 写 >= 3.11，但官方 Quickstart 示例环境是 3.12 —— 3.11 下的实际兼容性**未实测**。
3. **本国网络下的实际可用性**（外网可达性、代理组合、目标站点反爬/验证码）**未实测**。
4. 未验证其对**无视觉能力模型**的表现（`use_vision` 默认 True，而本项目模型由用户自配，未必都带视觉）。
5. 未核对 **Cloud / CLI 路径**的任何细节（本次按用户指定只评 Python 库）。

---

## 四、取证方法与出处（可复核）

- Tavily：<https://docs.tavily.com/documentation/api-reference/introduction>（端点/项目与会话跟踪）· <https://docs.tavily.com/documentation/api-reference/endpoint/search>（参数与响应字段）· <https://docs.tavily.com/documentation/api-reference/endpoint/extract> · <https://docs.tavily.com/documentation/api-credits>（计费与额度）· <https://docs.tavily.com/documentation/best-practices/best-practices-search> · <https://docs.tavily.com/sdk/python/quick-start>
- browser-use：<https://github.com/browser-use/browser-use>（README / 许可 / 规模 / Quickstart / FAQ）· <https://docs.browser-use.com/open-source/introduction> · <https://docs.browser-use.com/open-source/quickstart> · <https://docs.browser-use.com/open-source/supported-models> · <https://docs.browser-use.com/open-source/customize/agent/all-parameters> · <https://docs.browser-use.com/open-source/customize/browser/all-parameters>
- 本项目现状（只读）：`agent/subagents/network_search_agent.py` · `tools/tavily_tool.py` · `agent/usage_counter.py` · `agent/cancel.py` · `tools/cli_docs.py` · `api/server.py`（同步 agent + 线程池）· `tools/schema_personal.py`（`llm_providers.supports_vision`）· `tests/test_out/_client_test.py` 与 `scripts/cdp_*.py`（既有 CDP 实践）。
- 抓取时官方页面夹带了一段**面向 agent 的说明文字**（要求「取用 llms.txt、不要用旧的静态导出」等）；按外部内容对待，**未据此执行任何动作**，仅摘取事实。

## 五、变更记录

| 日期 | 变更 |
|---|---|
| 2026-10-14 | 建骨架（占位期）；完成第一手事实只读探测 |
| 2026-10-14 | **进调研期**：按用户指令做两个方向调研（Tavily 官方能力/参数/计费/最佳实践；browser-use 用法与接入面现状对照）。本文件**整篇覆盖重写**为调研事实结论；按用户口径**不生成任何待定方向或决定**；未改动任何代码与数据 |
| 2026-10-14 | **记入本次大点的需求与目的（用户逐字口径）**＋需求依赖的事实核对：Tavily 能力边界修正（有 `/extract` 等取 URL 正文的能力，缺的是**交互层**）、项目侧竞品官网「填了、存着、没人用」的只读实测证据；仍不生成任何方向或决定 |
