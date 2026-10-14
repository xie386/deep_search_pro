# v4.0 · 05 网络搜索子Agent升级（Tavily + browser-use 三分支取法）· 实现记录

> **性质**：本大点的**实现记录**（问题 → 方案 → 结果）。裁定见 `../Verdict/05-网络搜索子Agent升级.md`；调研推导见 `../research/05-网络搜索子Agent升级.md`（实现期不通读）；逐文件带读见 `05-网络搜索子Agent升级·逐文件带读.md`。
> **一句话**：让「网络搜索助手」从**只会全网关键词检索**，变成**按信息需求选三种取法**——①只检索 ②只进站 ③先检索定位再进站；取法由**工具签名**约束、流程走**代码**，不靠模型自觉。
> **额度**：Tavily 按用户口径「每轮 ≤15 次」，且这道闸**在代码里**（不是提示词）；真机只读、写操作一律临时账号 + 自清。

---

## 一、改了哪些文件

| 文件 | 改动 |
|---|---|
| `tools/web_workflows.py`（新增，236 行） | **三个取法工作流**：`web_search_only` / `browse_only` / `search_then_browse`；纯函数 `pick_sites`（去重域名、跳过聚合/问答/电商站、按序取前 N）；**两道代码闸** `budget_gate`（同轮查询词去重 + 每轮上限）|
| `tools/browser_tool.py`（新增，199 行） | `browse_pages` 从 `@tool` **降级为 util**（只被工作流调用）；模型解析走聊天侧同一处判定（`get_active_provider` / `get_provider_by_ref`），全机同名口径 |
| `tools/tavily_tool.py` | `internet_search` 同样降级为 util；行为逐字不变 |
| `scripts/browser_runner.py`（新增，283 行） | **浏览器侧子进程跑手**：stdin 收 JSON、stdout 末行回 JSON（密钥不进命令行）；`--no-sandbox`、`NO_PROXY`、`flash_mode`、模型调用超时 120s、`compat_json` 可切换 |
| `browser_venv/`（新增，107 包） | 独立 venv（Python 3.11.15）装 **browser-use 0.13.11** —— 主 venv **装不进**（见 §四 事故 1） |
| `agent/subagents/network_search_agent.py`（23 行） | 工具面改为**这三个工作流**（`web_search_only` 常挂；另两个随 `BROWSER_USE_ENABLED`） |
| `prompt/prompts.yml` | 主 Agent：三种取法的**路由节** + 按「用户要什么」判别的**判别口径** + 「涉及具体网址/网站现状/价格时效必须经助手取证，禁止凭记忆作答」；子 Agent：一条铁律「只调点名的那一个工作流」 |
| `utils/settings.py`（384 行） | 新增 🌐 浏览器抓取组 5 项 + `BROWSER_MODEL_REF` + `WEB_SEARCH_MAX_PER_TURN`（每轮检索上限，默认 15） |
| `agent/usage_counter.py` | 三工作流名映射为**内部工具**（不双记）；工作流内部对底层调用**显式** `record(...)`（额度账不被这层遮蔽） |
| `agent/turn_ledger.py`（新增，135 行） | **在跑轮次台账** + 启动补偿（进程被强杀也能补落中断占位） |
| `api/server.py` | 三类改动：① `/api/chat` 不再直接挂工具（装配期挂工作流）；② `_run_agent` 记台账；③ 启动钩子 `recover()`；④ 开关端点落审计 |
| `tools/decision_router.py` | 新增 `_maybe_auto_off`：模型侧**致命错误**（402 无额度 / 鉴权 / 模型不存在）自动关掉该账号开关并留痕 |
| `api/customize.py` | 新增 `get_provider_by_ref`（按 provider_name/id 取，防越权） |
| **新增测试** | `tests/unit/test_web_workflows.py`（9）· `test_web_budget_gate.py`（7）· `test_browser_tool.py`（20）· `test_web_prompt_modes.py`（4）· `test_turn_ledger.py`（6）· `test_decision_auto_off.py`（7） |
| **未动的** | 路由/精排算法本身、素材内容、`data/personal.db` 位置、`.env` 里既有密钥的值 |

## 二、关键落点

1. **三种取法 = 三个工作流工具，越界在结构上不可能**。底层两个工具降为 util 后**不再挂载**（全仓已无 `.invoke`）；`web_search_only` 的签名里**没有 urls**、`browse_only` 的签名里**没有查询词**——「情况②不许检索」不是提示词纪律，是**签名约束**。
2. **浏览器侧必须独立进程**：browser-use 装不进主 venv（要替换 `pydantic_core` 且文件被占用）。故 `browser_venv/` + `scripts/browser_runner.py` 子进程；密钥走 stdin，不进命令行、不进日志。
3. **模型选型由用户定**：新增「浏览器侧模型引用」（留空 = 跟随聊天侧生效配置，无自配则退 `.env` 默认模型）。实测三个模型只有 **GLM-5.3-Flash 干净可用**（agnes 兼容模式丢步、deepseek 用自家 DSML 语法完全解析不了）。
4. **两道闸进代码**（提示词的次数纪律实测被无视：审计见一轮 2 分钟约 30 次、含字面重复）：同轮查询词归一后去重、每轮上限 15（设置页可调、0 = 不限）；被挡回时返回中文说明且**不发起任何外部调用**。
5. **中断占位补强**：新增在跑轮次台账，`_run_agent` 登记 / 注销，启动时扫残留补落「本轮回答已被重启 / 停止打断（未完成）」。原占位只覆盖「用户点中断」，进程被 kill 时走不到（真机故障史见 §四）。
6. **两处排查暴露的缺口**（用户 2026-10-15 报「开关关着却报余额不足」后补）：开关变更落审计；模型侧致命错误自动降级（避免开关开着而模型没额度时**每轮白试**、用户无从察觉）。

## 三、实测数字（收官读数）

| 项 | 读数 |
|---|---|
| 全量单测 | **719 passed / 1 skipped**（05 起点 666；本点新增 53：工作流 9 + 闸门 7 + 浏览器工具 20 + 提示词 4 + 台账 6 + 降级 7） |
| 启动冒烟（进程内 TestClient） | **2 项通过 / 0 失败** |
| 真机链路（用户会话「网搜测试」） | **三分支全部命中过**：① 漫画网站清单（Tavily 结果自带网址，判为正确）；③ sakuracat（`search_then_browse` → 内层 Tavily 5 条 → 内层进站 2 个站，含如实报告候选站返回 40x） |
| 单次进站耗时 | 265s → **100s**（`flash_mode` + 模型调用超时 75s→120s） |
| 浏览器侧模型对照 | GLM（真点击、0 结构化输出报错）> agnes（真点进、2 步丢、兼容模式可用）> deepseek（零点击、3 条 DSML 报错，判为不可用） |
| 检索闸门效果 | 上限 15 生效；被挡回时零外部调用（单测钉住） |

## 四、本轮修掉的 bug / 事故（11 个）

| # | 现象 | 根因 | 归属 |
|---|---|---|---|
| 1 | `uv pip install browser-use` **弄坏主 venv**（卸 14 包、`WinError 5` 拒绝访问） | 该包要替换 `pydantic_core`，而文件被用户正在运行的后端进程占用 | 环境，按装前冻结清单精确回滚（179 包零差异）；改独立 venv + 子进程 |
| 2 | Chrome **静默崩溃**（0.2s，退出码 0x80000003） | ① browser-use 用旧式 `--headless`（Chrome 155 已移除）；② 受限令牌下建不了子进程 | 本轮引入，改 `--headless=new` + `--no-sandbox` |
| 3 | CDP 连不上（`did not receive a valid HTTP response`） | CDP/localhost 被系统代理劫持 | 本轮引入，子进程加 `NO_PROXY=127.0.0.1,localhost,::1` |
| 4 | 一次浏览要 265s | 慢模型撞 browser-use 默认模型调用超时后重试 | 本轮引入，`flash_mode` + 超时 120s → 100s |
| 5 | deepseek 在浏览器侧完全不可用 | 它用自家 `<｜｜DSML｜｜ calls>` 方言 + `response_format` 不支持 | 模型侧事实，已写进选型说明 |
| 6 | agnes 兼容模式偶发丢步 | 输出成大白话，`AgentOutput` 解析失败 | 本轮引入，`compat_json` 原生优先 / 明确拒时降级 |
| 7 | 视觉模式（`EXP_VISION=1`）打坏输出解析 | 多模态路径下一堆 `model_type` `PydanticUndefined` | 已知限制，本点不启用视觉模式 |
| 8 | 调度节制写错位置（整轮最多 2 次写在子 Agent 提示词，而重试由**主 Agent**发起） | 写在被调度方，管不住调度方 | 本轮引入，挪到子 Agent 的 `description`（主 Agent 读它决定是否再调度） |
| 9 | `tools/browser_tool.py` docstring 未闭合 | 漏结尾三引号 | 本轮引入，已修 |
| 10 | **Agent 说「准备开始查」后对话直接结束**（用户报，起初疑为 Agent 故障） | 后端在那一轮运行中被**重启**（`failure_events` 两次 `BACKEND_PORT_TAKEN`），在跑的轮次没有任何回执就消失 | 改动前就有，本轮补：台账 + 启动补偿 |
| 11 | 单测把**真实审计文件**写脏（4 条 `auto_off`） | 自动降级另调 `utils.audit`，绕过了单测的重定向 | 本轮引入，改为只走本模块 `audit`；另：`test_probe_runner_offline` 的顺序依赖被新用例暴露（两块共用 scratch 目录），已隔离 |

## 五、遗留与边界（如实记录，不粉饰）

1. **「选哪个工作流」仍是主 Agent 传话 → 子 Agent 挑工具**，仍属模型判断。彻底钉死要把三个工作流**拆成三个严格子 Agent**（工具集强制），约十几行改动——工作流这层已把逻辑准备好，随时可上。
2. **浏览器侧只有结构化输出能力达标的模型可用**（本机实测 GLM 干净）。用 deepseek 那类「自家方言」的模型会直接不可用。
3. **视觉模式暂不可用**（见 §四 事故 7）。
4. **每轮 15 次是**「防失控」**而非**「省额度」：真实的检索质量仍取决于模型第一次问得准不准；15 次只是兜住上限。
5. 用户会话「网搜测试」里有两轮被后端重启打断，痕迹已随会话删除，**只有审计留存**——本点的台账补偿正是针对这类场景。
