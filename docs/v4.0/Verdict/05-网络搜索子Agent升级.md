# v4.0 · 05 网络搜索子Agent升级 · 裁定记录

> 纪律同 `04-文档架构与用户数据归口.md`：**当场问、当场记**；同一类问题先翻本文件历史条目；确需推翻就新追加一条并写明理由。

---

## 裁定 1（2026-10-14）：05 方案定稿 —— browser-use 接入与两段式检索的三个设计分叉

| 项 | 内容 |
|---|---|
| **日期** | 2026-10-14 |
| **分叉点** | 草案（browser-use tool 化 + "有网址时先 Tavily 定位筛选 → 再用浏览器打开关键页交互/深抓"）落实时的三个设计分叉：① 库里已存的竞品官网要不要**每轮注入**上下文；② 浏览器工具总开关 `BROWSER_USE_ENABLED` 默认值；③ 浏览器侧用哪个模型 |
| **可选方案** | ①A 每轮硬编码注入 `competitor_sites` 源 ／ **①B 不注入**，靠 Agent 在用户提到后自觉从库里取、再交给网络搜索子 Agent ／ ②A 默认开 ／ ②B 默认关 ／ ③A 沿用「定制助手·模型选型」当前生效配置 ／ ③B 另配一套浏览器侧模型 |
| **裁定结果** | **①B** ＋ **②A**（默认开，失败自动降级回 Tavily 结果）＋ **③A**（沿用现有生效配置，不新增一套） |
| **依据** | 用户 2026-10-14 原话：「绝对不要硬编码每轮注入!设计思想应该是能够用户在对话提到以后Agent自觉从数据库里抓取并喂给网搜子Agent,保持directly_open_url为True,但是我们需要知道一点,用户的消息最先看到的是主Agent,它可没有browser_tool,它会不会自觉把网址传给网搜子Agent?browser_tool实现并接入后进行两个对话实验:…」；另两问的选择原文见同轮回答 |
| **影响面** | `../v4.0规划文档.md` §05 ③ 段：D.2 去掉注入源、改写为"三处描述面 + 自觉路由 + 风险 + 预案"；H 新增第 7/8 条（两轮对话实验）；新建本条。方案其余部分（B/C/E/F/G/I/J）不变 |

---

## 裁定 2（2026-10-14）：**两轮对话实验 = 本大点的核心验收**

| 项 | 内容 |
|---|---|
| **日期** | 2026-10-14 |
| **分叉点** | 链路能否成立的关键未知：**主 Agent 手里没有 browser_tool**，它会不会自觉把网址（来自用户消息或数据库子 Agent）转交给网络搜索子 Agent —— 若不会，方案的实际效果为零 |
| **可选方案** | A 实现后直接按两轮对话实验验收，通过即成立 ／ B 先补主 Agent 提示词路由图再验 ／ C 改成确定性注入（**已被裁定 1 否决** ✗） |
| **裁定结果** | **A**：先按现状实现并接入，然后跑**两轮对话实验**作为验收（实验 1：竞品官网 → 最新价格；实验 2：给定网址 → 看页面内容）；**不通过**则按预案在主 Agent 提示词加路由图（届时追加裁定） |
| **依据** | 用户原话（同上）；实验口径由用户给出 |
| **影响面** | `../v4.0规划文档.md` §05 ③ 段 H.7 / H.8；`Done/05` 实现记录必须留**原始实验痕迹**（工具调用序列、是否触发"任务串带 URL 直接打开"、截图），实验结论**如实写**（含不通过时的形态） |

---

## 裁定 3（2026-10-14）：browser-use **不能装进主 venv** → 改走「独立 venv + 子进程」

| 项 | 内容 |
|---|---|
| **日期** | 2026-10-14 |
| **分叉点** | 方案 §B 原定「`uv pip install browser-use` 装进主 venv，冲突就回退」。实现期第一步实测：uv 解析出 **106 个包**，要**替换 `pydantic_core`** 并**卸掉 14 个既有包**（`pydantic`/`openai`/`requests`/`anyio`/`rich`/`typing-extensions`/`mcp`/`mcp-types`/`pypdf`/`protobuf`/`click`/`google-auth`/`google-genai`/`pydantic-settings`），安装过程还撞上用户正在运行的后端占用 `.pyd` → 中止时项目**已起不来** |
| **可选方案** | A **独立 venv（`browser_venv/`）+ 子进程**调 `scripts/browser_runner.py`，JSON 进出 ／ B 再试 pin 一个能兼容现有 pydantic 的旧版 browser-use（成功率未知，失败会再次弄脏主 venv） ／ C 暂缓本大点 |
| **裁定结果** | **A**：主 venv 永不被碰；顺带把方案里那个难处理的 asyncio 边界换成干净的子进程（超时/中断更好控） |
| **依据** | 用户 2026-10-14 选择 A（原话：「A 独立 venv + 子进程：在项目内新建 `browser_venv/`…好处：主 venv 永不被碰；顺带把方案里那个难处理的 asyncio 边界换成干净的子进程（超时/中断也更好控）」） |
| **影响面** | 方案 §C 的「异步边界」一节由「asyncio.run + 守卫」改为**子进程**；§B 的装包粒度改为独立 venv；新增 `scripts/browser_runner.py`（子进程跑手）与 `.gitignore` 条目。**事故复原**：按装前冻结清单 `uv pip install --no-deps` 精确补回 14 个包、只还原被掏空的 `pydantic_core/__init__.py` 与 dist-info（不碰用户后端占用的 `.pyd`），验证「179 包零差异 + `uv pip check` 全兼容 + 666 项单测全绿」 |

---

## 裁定 4（2026-10-14）：按**实际装到的版本**适配，不用仓库 main 的文档口径

| 项 | 内容 |
|---|---|
| **日期** | 2026-10-14 |
| **分叉点** | 方案 §C 的参数（`directly_open_url`、`max_steps` 归构造参数、`calculate_cost`、顶层 `ProxySettings` 等）来自仓库 `main` 的 `AGENTS.md`；实际 `uv pip install` 装到的是 **PyPI 发布版 `browser-use 0.13.11`**，两者 API 不一致（顶层不导出 `ProxySettings`、`max_steps` 归 `agent.run()`、构造走 `BrowserProfile`、无 `calculate_cost`） |
| **可选方案** | A 按**实际装到的 0.13.11** 适配（发布版、稳定） ／ B 改装仓库 main（未发布，风险高） |
| **裁定结果** | **A**：一律以实际装到的版本为准；文档口径与实现不一致时，实现侧以实测为准并在文件头注明差异 |
| **依据** | 实现期实测证据：`from browser_use import ProxySettings` 报 `ImportError`（顶层不导出，实际在 `browser_use.browser.profile`）；`Agent(max_steps=…, calculate_cost=…)` 不在其构造参数表内（`max_steps` 归 `agent.run()`）；`Browser(headless=…, channel=…, user_data_dir=…)` 的字段实为 `BrowserProfile` 所有。用户 2026-10-14 在裁定 3 中选定路线 A（独立 venv + 子进程），本裁定是该路线下的实现口径 |
| **影响面** | `scripts/browser_runner.py` 的构造段与文件头「适配的版本」说明；另外记录三处**本机环境必需**的适配（均写成代码注释）：① Chrome 必须 `--no-sandbox`（本进程跑在受限令牌下，否则 0.2 秒崩 `0x80000003`、无任何输出；实测加该参数后 CDP 就绪）；② CDP/localhost 必须绕系统代理（`NO_PROXY`，否则 `did not receive a valid HTTP response`）；③ `LOCALAPPDATA`/`TEMP`/profile 一律落**项目内 D 盘**（该进程只能写项目目录，且不占 C 盘）。另：模型调用超时放宽 120 秒 + `flash_mode`/`use_thinking=False` 提速（默认 75 秒会被慢模型打穿，白白吃掉步数） |
