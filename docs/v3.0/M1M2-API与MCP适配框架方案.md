# M1+M2 · API 与 MCP 适配框架（「最小并集」设计）方案

> **状态**：✅ **定稿（2026-09-25）** —— 6 个分叉 + 3 个开放项 **全部裁决完毕**（见 §零 决策记录）；照 §五（契约）/§六（步骤）即可开工
> **依赖**：无（可独立开工）｜ **对应规划书**： `v3.0规划书.md` §2 P1（README 已登记项）、§5 M1/M2
> **取证日期**：2026-09-25（调研对象： `D:\code\aicode\sichuan-agricultural-training-master` + MCP 现行规范 + OpenAPI→tool 生态）
> **一句话结论**： **不要把"API 工具 / MCP 工具"做成两种新工具，而要把它们做成"同一个能力契约的两种来源驱动"**；
> 模型可见层只认 **JSON Schema**（MCP 原生 = OpenAI function 原生，天然是最小并集），执行细节各自挂在**扩展位 **上。

---

## 零、决策记录（用户裁决，2026-09-25）

用户在 §四 各小点标题后标注了选择，本文件据此定稿。 **留痕在此，便于日后复盘"当初为什么这么定"**。

| 分叉 | 议题 | **裁决** | 与方案建议的关系 |
| --- | --- | --- | --- |
| F5 | MCP 客户端实现（§4.1） | **A：官方 SDK v2**（钉 `mcp>=2,<3`） | 与建议一致 |
| F2 | Agent 工具面（§4.2） | **C：混合**——保留 `run_shell_command`，新增单一 `invoke_tool(ref,args)` 覆盖 api+mcp | 与建议一致 |
| F6 | API 能力录入主路径（§4.3） | **采纳**：贴 OpenAPI 为主 + 手填兜底 + 渐进披露 | 与建议一致 |
| F4' | 只读判定口径（§4.4） | **采纳**：CLI 只读清单 / API 默认仅 GET（写方法显式勾选）/ MCP **人工确认后**才可调用 | 与建议一致 |
| F4 | MCP stdio 安全边界（§4.5） | **B：命令白名单 + 显式确认 + 审计**（不用"禁止 stdio"，也不用"任意命令"） | 与建议一致 |
| — | 发现→确认流程（§4.6） | **采纳**：体检 → 发现 → 候选 → **人工确认** → 入池（bump pool_version） | 与建议一致 |
| F3 | 新能力默认状态 | 由 §4.6 裁决 **连带确定**：未被人工确认前 **不入池、不可调用**（`enabled=0`） | 与建议一致 |
| **F1**/**O1** | API 的 `ref` 形态（§3.3） | **`api:<服务名>#<操作名>`，并在 `invoke_spec` 里存 `source_id` 指向来源行 ** | 比建议多存一个 id：**既可读又能溯源 **（重建来源后仍能找回原行） |
| **O2** | OpenAPI「填 URL 由后端抓取」 | **不进 M1**：先只做粘贴；「URL 抓取」与档 ③「从调用示例反推」一起后置 | 与建议一致 |
| **O3** | 内置 `_describe` 能力（让模型按需查 API 文档） | **后置 **：先靠「路由器选相关操作 + 完整卡带参数摘要」覆盖；真遇到"操作过多"的实例再补 | 与建议一致 |

> **注**：裁决与建议一致不代表"没讨论" —— 记录在这里的意义是： **下次有人问"为什么 stdio 不直接禁止"，
> 答案就在第 5 行 **（白名单 + 确认 + 审计，而不是一刀切）。

## 一、目标与验收判据

**目标**（用户原话的展开）：建立一套 **可套用于大部分 API 与 MCP 的框架**，新增一个 API/MCP 工具 **不写代码、只填配置**；
本项目已有能力目录（`CapabilityEntry`）与路由器不改架构，只 **填槽位**。

| # | 目标 | 验收判据（可测） |
| --- | --- | --- |
| G1 | **零代码接入**：新增一个 REST 接口 / 一个 MCP server，全程只填表单 | 录屏或脚本证明：从"填写"到"Agent 能正确调用"不改任何 `.py`；单测断言"新增来源后 `_ADAPTERS` 无需改动" |
| G2 | **混合池路由不退化** | 混合池（CLI 3 + API 3 + MCP 3）上跑 `tests/_m5b_route_calib.py`： **命中判定 ≥9/10、0 条路由到错的工具、无关问句 100% 回退**（M1 结束时给基线与实测） |
| G3 | **安全口径不放松** | API 未勾选的写方法 **100% 被拒**；MCP 未确认的工具 **不可调用**；每次调用（含被拒）都有审计行；SSRF 守卫拒环回/内网 |
| G4 | **发现即候选，不手写能力描述**（"非硬编码"的落点） | MCP `tools/list` 拿到的 N 个工具 → N 条候选（参数直接来自 `inputSchema`）、人工确认后入池；API 贴 OpenAPI 文档 → 候选能力（带 `operationId`） |
| G5 | **注入预算不破** | 命中形态注入 ≤ **1500 字**（与 v2.0 同口径），API/MCP 卡片含一行参数摘要时也不超 |

---

## 二、现状与证据

### 2.1 本项目已有的地基（不是从零开始）

| 已有件 | 位置（证据） | 对本次设计的意义 |
| --- | --- | --- |
| 来源常量 | `tools/capability_pool.py:25-28`： `SOURCE_CLI/API/MCP` + `SOURCES` | 三种来源的命名已定 |
| **适配器注册表** | `tools/capability_pool.py:130-134`： `_ADAPTERS = {SOURCE_CLI: _entries_from_clis, # SOURCE_API: …, # SOURCE_MCP: …}` | **只差两行**——填槽位就是本次任务 |
| 统一能力契约 | `tools/capability_pool.py:38-50`： `CapabilityEntry(source, ref, name, keywords, abilities, invoke_hint, enabled, rules, invokable)` | 模型可见语义已具备； **缺参数 schema 与调用绑定** |
| **派生表 + 版本号** | `tools/schema_personal.py:292-312`： `tool_capabilities(account_id, source, ref, name, keywords, abilities, invoke_hint, enabled, UNIQUE(account_id,source,ref))` + `tool_pool_meta(pool_version)`；注释明写「**api/mcp 实装后各写各的来源表再派生进这里**」 | **表已存在**，本方案不是重造，是补两张来源表 + 派生逻辑 |
| 路由器 | `tools/tool_router.py`： `FAST_PATH_MAX=4`、 `Z_STRONG=2.0`、 `LEX_MIN=2`、 `TOTAL_BUDGET_CHARS=1500`、 `decide()`/`route_tools()`/三形态渲染 | 混合池直接复用；只需给 `_entry_text()` 补参数摘要 |
| 向量池 + 惰性同步 | `capability_pool.py:267/337/368`： `sync_vectors` / `ensure_vectors_fresh` / `route_scores`，collection `tool_route_{account_id}` | API/MCP 条目自动进同一向量空间，无需新索引 |
| 执行侧样板 | `tools/shell_executor.py:39-40`： `@tool def run_shell_command(command, argv) -> str` + `tools/_runtime/shell_runtime.py`（六道闸 / 只读闸 / 30s 超时 / 1MB 上限 / 审计 `data/audit/commands.jsonl:43`） | **新执行器照抄这套口径**（含审计格式） |
| 配置驱动的前例 | `user_clis` 表（`schema_personal.py:271-286`）： `bin/readonly/abilities/docs/auth_cmd` —— M4c 已把"CLI 接入"做成配置 | API/MCP 的"配置导向"有现成先例与 UI 范式 |
| 证据链先例 | v2.5 的 CLI 能力描述改造（`docs/v2.5/CLI能力描述撰写改造.md`）： **README（语义层）→ `--help`（校验层）→ 人工确认** | API/MCP 的"发现→草稿→审计→确认"直接复刻这套 |
| 架构铁律 | M5b 决策记录 #1： **「统一目录 + 通用调用工具」**（`docs/v2.0/M5b工具检索路由.md`），因为 M2 把「agent 静态化 + invoke 期注入」定成契约 | 决定了 **不能用** `langchain-mcp-adapters` 的动态工具集（会逼出"每轮重建 agent"） |

### 2.2 参考项目（`sichuan-agricultural-training-master`）的做法 —— 值得抄什么、不能抄什么

那个项目的「自配 MCP/API」板块实现如下（**逐条读过源码，非推测**）：

**它的数据结构（单表两型 + JSON 扩展）** —— `db_init.py:209-225`

```sql
CREATE TABLE tool_config (
 id, name, tool_type, -- 'api' | 'mcp' ← 两型共用一张表
 description, -- 给模型看的说明
 api_url, request_method, -- REST 用
 request_example, -- 一个完整的可跑示例（含 query 参数）
 headers_json, params_json, -- headers / 参数
 config_json, -- ★ 类型特有字段：MCP 放 mcpServers / server_url
 status, create_time, update_time );
-- 另有 employee_tool_rel(employee_id, tool_id)：工具与"数字员工"的授权关系（≈ 本项目的账号隔离）
```

**它的 MCP 配置直接复用生态格式** —— `service/MCPVerifier.py:57-77`

```jsonc
// stdio 形态（与 Claude Desktop / Cursor 的 mcpServers 完全一致，用户可整段搬运）
{ "mcpServers": { "fs": { "command": "npx", "args": ["-y", "@modelcontextprotocol/server-filesystem", "/tmp"] } } }
// HTTP 形态（两个键都容忍）
{ "server_url": "https://host/mcp" } 或 { "url": "https://host/mcp" }
```

**它的执行侧是通用 driver（不是一户一套代码）** —— `service/ToolService.py:103-138`

```python
# API：GET → urlencode 拼 query；其他 → JSON body；headers 来自 headers_json（通用，无厂商分支）
# MCP：POST JSON-RPC tools/call，params = {name: config['default_tool_name'], arguments: params}
```

**它还有一件好东西：配置分步体检** —— `MCPVerifier.verify()` 七步报告
（配置解析 → 网络连通性/命令是否存在 → 参数校验 → **JSON-RPC initialize** →**tools/list** → 错误语义（故意调不存在的工具/方法）→ 性能）

#### ✅ 值得借鉴（我采纳）
1. **单表 + 类型扩展字段**：新增来源不动表结构（本方案用「来源表 + `config_json` 扩展位」实现同一目标）。
2. **复用 `mcpServers` 生态格式**：用户能从现有 MCP 客户端配置里 **整段粘贴**，学习成本几乎为 0。
3. **配置分步体检**：把"配了但连不上"变成一份 **可读报告**，而不是一句报错 —— 本方案做成"MCP 连接体检"面板。
4. **通用 driver + 授权关系表**：与本项目"路由器不感知来源 + 账号隔离"同构。

#### ❌ 不能抄（这些正是本方案要解决的）

| 问题 | 证据 | 后果 |
| --- | --- | --- |
| **参数模型不是 JSON Schema** | `db_init.py:246-250` 种子数据： `params_json = [{"name":"city","label":"城市名称","type":"string","required":true,"placeholder":"..."}]` —— 自造的、面向表单的**扁平列表 ** | 与 MCP（原生 JSON Schema）/ OpenAI function（JSON Schema）**不同构 **，每次接新来源都要写转换代码 = 硬编码 |
| **参数没有"位置"概念** | `_testApiTool` 只做「GET→query、其他→body」 | **REST 的 path 参数（`/users/{id}`）、header 参数、cookie 参数完全无法表达**；一个操作里 query+body 混合也无法表达 |
| **MCP 手写 JSON-RPC，无 SDK** | `MCPVerifier` 用 `urllib`/`Popen` 自己拼 `initialize`/`tools/list`/`tools/call` | 缺 `notifications/initialized`、 `Mcp-Session-Id`、 `MCP-Protocol-Version`、SSE 流、分页 cursor、版本协商 →**能过握手但连不上真实 server** 的概率高；规范一改就得重写 |
| **MCP 只有 HTTP 能调用** | `ToolService._testMCPTool:69-81` 只走 `server_url`（stdio 只用于"验证"，没有执行路径） | 本机 stdio MCP（生态里最多的一类） **不可用** |
| **"发现"不落条目** | 验证报告里有 `tools/list` 结果，但调用时 `default_tool_name` 要 **手工填**（`ToolService.py:77`） | 仍是"人工硬编码"：一个 server 有 15 个工具就得手工建 15 次 |
| **两套并行 + 三处参数描述** | `tool_config`（工具）与 `function_config`（函数， `func_type=api_call/code_exec/sql_exec/data_analysis`， `FunctionService.py:49-59`）两套；参数在三处各写一遍 | 配置漂移；新增能力要决定"写哪一套" |
| **没有只读判定/审计/超时口径** | 全表无 `readonly` 概念（对比本项目 `user_clis.readonly`） | 任意 API/MCP 调用都能被模型触发；演示项目可接受， **本项目不行** |
| **全量喂给模型** | `LLMClient._buildFunctionDefinitions()` 把员工所有工具塞进 `functions=[...]`（旧版协议） | 工具一多就爆上下文 —— 这正是本项目 M5b 要解决的问题 |

### 2.3 外部规范与生态（决定了技术选型）

| 事实 | 出处 | 对方案的影响 |
| --- | --- | --- |
| MCP 当前协议版本 **2025-11-25**；已有**2026-07-28** 修订 | `modelcontextprotocol.io/specification`（本页直接跳 2025-11-25，首页写明"current protocol version is 2025-11-25"）；Python SDK 文档："speaks the 2026-07-28 revision" | **协议演进很快 **（2024-11-05 → 2025-03-26 → 2025-06-18 → 2025-11-25 → 2026-07-28）→ 手写客户端会持续腐烂 |
| **2026-07-28 修订移除了握手、会话与所有服务端发起的请求** | Python SDK「What's new in v2」原文 | 客户端实现复杂度会 **下降**；但这恰恰说明"跟着 SDK 走"比"自己实现某个快照"更稳 |
| 官方 **Python SDK 已是 v2**（`pip install mcp` 装 2.x；v1 在 `v1.x` 分支，需要的话 `mcp>=1.28,<2`） | `github.com/modelcontextprotocol/python-sdk` | 引入依赖时要**钉大版本 **： `mcp>=2,<3` |
| SDK v2 客户端就一个对象： `Client(url)`=Streamable HTTP、 `Client(StdioServerParameters)`=stdio、 `Client(MCPServer实例)`=**进程内传输**； `async with` 即连接+协商； `server_info/capabilities/protocol_version/instructions` 在进入时就有 | `py.sdk.modelcontextprotocol.io/client/` | ★ **进程内传输** = 离线单测 MCP 的完美手段（不用起进程、不用端口） |
| 工具定义 = `name/title/description/inputSchema(JSON Schema)/outputSchema/annotations`； `annotations` 含 `readOnlyHint/destructiveHint/idempotentHint/openWorldHint` | `specification/2025-06-18/server/tools`、Swift/TS SDK 结构体 | ⭐ **`inputSchema` 就是 JSON Schema** → "最小并集"的参数语言不用发明 |
| 规范明确： **annotations 是 hint，不是安全保证**（"should never make tool use decisions based on ToolAnnotations received from untrusted servers"） | 同上 + 多篇实现指导 | ⭐ **不能拿 `readOnlyHint: true` 当放行依据** → 必须人工确认（与本项目 M5b 决策 #5 一致） |
| Streamable HTTP 细节： `Mcp-Session-Id`（服务端可能下发，后续请求必须带）、 `MCP-Protocol-Version` 头、收到 404 要 **重新 initialize**、 `DELETE` 终止会话、 `Accept: application/json, text/event-stream`、SSE 可恢复（`Last-Event-ID`） | `specification/2025-06-18/basic/transports` | 这些"细节"就是**手写客户端容易漏 **的部分；也给出超时/重连要求 |
| OpenAPI 参数位置： `in: path | query | header | cookie`（3.2 还有 `querystring`）+ `requestBody`；path 参数 `required` 必须 true | `learn.openapis.org/specification/parameters.html` | ⭐ API 能力的参数 **必须带位置**，否则表达不了真实 REST |
| 社区转换器的映射约定： `operationId`（缺省用 `METHOD /path` 生成如 `get_pets_by_pet_id`）→ 工具名；参数带 `position: path | query | header | body`； **写操作需确认**；响应 **裁剪到 LLM 上下文** | Higress `openapi-to-mcpserver`、 `openapi-mcp`（Go）、 `@karlangas12/openapi-to-mcp`（npm） | 与我的设计交叉验证； `operationId` 缺失要兜底命名；响应裁剪要有明确上限 |
| 生态里成熟做法还有：暴露一个 `describe` 工具让模型 **按需**查询 API 文档，而不是把 N 个操作全塞进 tools | `openapi-mcp` README | ⭐ 与本项目"路由 + 三形态注入"是同一思路（渐进披露），可互相印证 |

---

## 三、「最小并集」怎么划（本方案的核心）

### 3.1 三层切分：取交集的只取一层，其余各自扩展

```
┌─ ① 模型可见契约（三种来源的 **交集**，也是 MCP/OpenAI function 的原生形状）──────────┐
│ ref 稳定标识（唯一键，见 §3.3） │
│ title / name 展示名 │
│ description 人话说明（含"什么时候用它"） │
│ input_schema ★ JSON Schema（object）—— 唯一的参数语言，两边原生就是它 │
│ read_only ★ 语义标记（只读 / 会改东西）—— 只作"提示 + 默认勾选"，不作放行依据 │
│ enabled 是否进池 │
└──────────────────────────────────────────────────────────────────────────────────┘
 ▲ 三种来源 **都**要能给出这 6 项 → 这就是"最小并集"
┌─ ② 执行侧元数据（各来源自己的扩展位，模型 **看不到**）───────────────────────────────┐
│ CLI： argv 形状（bin + 子命令路径）+ 只读命令清单（rules） │
│ API： method + url_template（含 {path} 占位）+ ★bindings（每个参数的位置/序列化） │
│ + auth（header/query/服务端注入）+ 超时/体积上限 │
│ MCP： server_ref（连接哪个 server）+ tool_name + 原始 annotations（留档） │
└──────────────────────────────────────────────────────────────────────────────────┘
┌─ ③ 来源配置（"一个服务"一层，与能力 1:N）──────────────────────────────────────────┐
│ CLI： user_clis（**已存在**，不搬家） │
│ API： 新表 tool_sources(source='api', config_json={base_url, auth, headers, 默认超时})│
│ MCP： 新表 tool_sources(source='mcp', config_json={mcpServers:{…}} 或 {server_url}}) │
└──────────────────────────────────────────────────────────────────────────────────┘
```

**为什么这样划**：并集一旦往上取（比如要求所有来源都有 argv、都有 method），就会退化成"为 CLI 造 method"或"为 API 造 argv"的硬编码；而交集只留"模型真正需要知道的 6 项"， **执行怎么发生**完全由驱动决定 —— 这正是"新增来源只加驱动、不动契约"的前提。

### 3.2 为什么参数语言只能是 JSON Schema

| 来源 | 原生参数表达 | 转成 JSON Schema 的成本 |
| --- | --- | --- |
| MCP | `inputSchema` **本身就是 JSON Schema** | **0**（直接存） |
| OpenAI 兼容 function calling | `parameters` **就是 JSON Schema** | **0**（直接给模型） |
| API（OpenAPI 3.x） | `parameters[]`(in/name/schema/style) + `requestBody.content[schema]` | 一条 **确定性**映射（位置信息留在 bindings，见 §4.3），无需厂商分支 |
| CLI | 用户语言的能力描述 + 只读清单（无 schema） | 可以不提供 schema（README 里"两步链"那种写法继续用）——**JSON Schema 是"可选项"，不是所有来源的强制项** |

→ 结论： **JSON Schema 做"参数语言"，位置/序列化做"执行元数据"**。这样 API 与 MCP 的差别被压缩成"多一张 bindings 表"，
模型侧完全一致（也就自动获得了 M5b 的三形态注入与路由能力）。

### 3.3 统一 ref 命名空间

| 来源 | ref 形态 | 例 | 说明 |
| --- | --- | --- | --- |
| CLI | `cli:<bin>` | `cli:weread` | 与现状一致（`user_clis.bin` 唯一） |
| API | `api:<service>#<operation>` | `api:weather#getCurrent` | `service` = 用户填的短名（slug 化）； `operation` = `operationId`，缺失时按生态约定兜底 `METHOD-path`（如 `get-users-id`） |
| MCP | `mcp:<server>#<tool>` | `mcp:fs#read_file` | `server` = 来源表里的短名 |

> **对既有注释的一处 refine**： `schema_personal.py:295` 写的是 `api: tool_id │ mcp: server/tool`。
> 那是 2026-09 写目录层时的 **占位**（当时还没有来源表）。本方案把 `api` 的 ref 从"来源行 id"改成
> **"服务名#操作名"**，理由：① 可读（审计/日志/前端回显能直接看懂）；② 稳定（重新导入 OpenAPI 不会因行 id 变化而失效）；
> ③ 与 CLI 的 `bin` 语义对齐（都是"用户起的名字"）。 **已裁决（§零 F1/O1）**：采用"可读 ref + `invoke_spec.source_id` 溯源"的形态； `schema_personal.py:295` 那句占位注释在 M1-1 建表时一并改成新口径。

### 3.4 三来源九宫格对照（实现时的"一张查表"）

| 层 | CLI | API | MCP |
| --- | --- | --- | --- |
| 来源配置存哪 | `user_clis`（已有） | `tool_sources`（新） | `tool_sources`（新） |
| 能力从哪来 | 用户写的能力描述 + 只读清单 | **贴 OpenAPI** → 自动候选（或手填） | **`tools/list`** → 自动候选 |
| 参数语言 | 无（可选：手写少量字段） | OpenAPI schema → JSON Schema | 原样 `inputSchema` |
| 调用绑定 | bin + 子命令路径 + 位置参数 | method + url_template + bindings + auth | server_ref + tool_name |
| 只读判定 | 只读清单逐条命中（`readonly_verdict`） | **默认只放行 GET**；写方法需显式勾选 | `readOnlyHint`**预勾选建议 ** +**人工确认 ** |
| 执行器 | `shell_runtime.execute()`（沙箱 6 闸） | `api_driver.call()`（httpx + 白名单 + 超时/体积） | `mcp_driver.call()`（官方 SDK Client） |
| 审计 | `data/audit/commands.jsonl`（已有） | **同一条文件、同一格式**（加 `source`/`ref` 字段） | 同左 |
| 路由/注入 | `CapabilityEntry` → router（不变） | 同左（`_entry_text` 补参数摘要） | 同左 |

---

## 四、设计选型与取舍（6 个分叉，每个都给备选）

### 4.1 MCP 客户端：官方 SDK vs 手写 JSON-RPC　→ **裁决 A：官方 SDK v2（`mcp>=2,<3`）**
| 备选 | 优点 | 缺点 | 结论 |
| --- | --- | --- | --- |
| **A. 官方 SDK v2（`mcp>=2,<3`）** | 版本协商/stdio/Streamable HTTP/SSE 全交给 SDK； **进程内传输可做离线单测**；协议改动由上游跟进 | 新增一个依赖；SDK v2 较新（有 v1 迁移面） | ✅ **推荐** |
| B. 手写 JSON-RPC（参考项目的做法） | 零依赖；实现透明 | 规范已演进 5 版（2024-11-05→2026-07-28）； `Mcp-Session-Id`/协议头/SSE/分页/重连都要自己维护； **stdio 执行路径还得自己写** | ❌ 不选（参考项目正是栽在这里：能验证不能调用） |
| C. 用 `langchain-mcp-adapters` 直接拿工具集 | 代码最少 | **违背 M2 契约**（agent 静态化 + invoke 期注入）：动态工具集逼迫"每轮重建 agent / 按工具集指纹缓存多份 agent" | ❌ 不选（已有决策记录否决） |

> 补充：SDK 只用在 **连接与协议**上，"把工具变成本项目的能力条目"仍然是我们自己的代码（这是本框架的价值所在）。

### 4.2 Agent 工具面：一源一工具 / 单一通用工具 / 混合　→ **裁决 C：混合**（保留 `run_shell_command` + 新增单一 `invoke_tool` 覆盖 api+mcp）
| 备选 | 模型可见工具数 | 改动风险 | 结论 |
| --- | --- | --- | --- |
| A. 一源一工具（`run_shell_command` + `call_api_tool` + `call_mcp_tool`） | 3 | 低 | 可行，但每加来源就多一个常驻工具（提示词面与路由面变宽） |
| B. 单一 `invoke_tool(ref, args)`（CLI 也迁进来） | 1 | **高**（`run_shell_command` 的沙箱/只读闸/109 项测试与提示词契约都要动） | 待 M1 之后视情况演进 |
| **C. 混合（推荐）** | 2 | 中低 | ✅ **本次采用**： **保留** `run_shell_command`（CLI 端口径与测试不动）， **新增一个** `invoke_tool(ref, args)` 覆盖 **api + mcp**；docstring 里声明 ref 命名空间 |

`invoke_tool` 的模型可见契约（照抄 CLI 那个工具的写法，机械细节只写 docstring 一处）：

```python
@tool
def invoke_tool(ref: str, args: dict) -> str:
 """按能力 ref 调用一个外部工具（API / MCP）。

 ref 形态：api:<服务名>#<操作名>，mcp:<服务名>#<工具名>。
 只读原则：api 默认仅放行 GET；写方法（POST/PUT/PATCH/DELETE）必须在配置里显式勾选，
 否则拒绝执行并返回原因。mcp 工具必须在配置里被人工确认过才可调用（annotations 仅是提示）。
 args 为该能力 input_schema 描述的参数对象；缺必填参数会被拒绝并指出缺哪个。
 调用会被审计（data/audit/commands.jsonl），超时 30s，输出超过 1MB 会被截断。
 """
```

### 4.3 API 能力的录入：三档 + 渐进披露　→ **裁决：采纳本方案**（① 贴 OpenAPI 为主路径 + ② 手填兜底 + 渐进披露）
| 档 | 输入 | 产出 | 适用 |
| --- | --- | --- | --- |
| **① 贴 OpenAPI（推荐主路径）** | 粘贴 OpenAPI 3.x JSON/YAML（**M1 只做粘贴**；"填 URL 由后端抓"已裁决后置，见 §零 O2） | **自动候选**：每个 `operationId` 一条能力；参数 = OpenAPI → JSON Schema； `bindings` = 参数位置 | 有文档的 API（绝大多数） |
| ② 手填单操作 | base_url + method + path 模板 + 参数表 | 一条能力 | 内部小接口、文档缺失 |
| ③ 从调用示例反推（可选，后置） | 一条真实可跑的 curl/URL | 候选能力（参数靠示例推断， **必须人工确认**） | 兜底（参考项目的 `request_example` 思路） |

**OpenAPI → 能力的确定性映射规则**（写进方案就是契约）：

| OpenAPI | 我们的字段 |
| --- | --- |
| `operationId`（缺省 → `METHOD-path` 归一化） | `operation` → `ref` 的 `#` 后半 |
| `summary` + `description` | `abilities`（人话能力描述，供路由/注入） |
| `parameters[].in ∈ {path,query,header,cookie}` + `requestBody` | `input_schema.properties` + **`bindings[].location`** |
| `parameters[].required`、path 参数恒为必填 | `input_schema.required` |
| `parameters[].schema`、 `requestBody.content['application/json'].schema` | 原样进 `input_schema`（`$ref` 就地解析，深度上限） |
| `servers[0].url` | `tool_sources.base_url`（可被用户覆盖） |
| `security` / `securitySchemes` | 只生成"**需要鉴权**"提示；实际凭据由用户在来源配置里填（不自动搬密钥） |
| 方法 ∈ {POST,PUT,PATCH,DELETE} | `read_only=false` + **默认不勾选** |

**渐进披露**：候选可能有几十条（一个真实 OpenAPI 常有 30~200 个操作）。 **不为每条都注入卡片**——靠既有 router 选相关的 1~3 条；
另外还有一种生态做法：内置一个"**查文档**"能力（`api:<service>#_describe`）回答"这个 API 能做什么" ——
**本版后置**（§零 O3），先靠路由器 + 完整卡覆盖。

### 4.4 只读判定：三来源统一口径　→ **裁决：采纳本方案**（CLI 只读清单 / API 默认 GET / MCP 人工确认后生效）
| 来源 | 判定 | 与人确认的关系 |
| --- | --- | --- |
| CLI | 只读清单 **逐条命中**（现状 `readonly_verdict`，口径不动） | 清单由人写 = 人已确认 |
| API | 方法白名单： **GET 默认放行**；其余 **默认拒绝**，需在表单里逐个勾选 | 勾选 = 人已确认（一次确认、长期有效，可撤销） |
| MCP | 从 `annotations.readOnlyHint` **预勾选建议**； **必须人工确认后**才进池可调用 | 规范明说 annotations 是 hint → **不能自动放行**（本方案与 M5b 决策 #5 一致） |

**统一"拒因可读"**：三类拒绝都必须返回"为什么被拒 + 怎么才能放行"（CLI 已有此风格，REST/MCP 照抄）。

### 4.5 MCP stdio 的安全边界　→ **裁决 B：命令白名单 + 显式确认 + 审计**
stdio MCP = **客户端 spawn 一个本地进程**，风险等级等同"执行任意命令"。三档：

| 档 | 策略 | 评价 |
| --- | --- | --- |
| A. 完全禁止 stdio，只允许 Streamable HTTP | 最安全 | 但生态里大量 server 是 stdio（本地文件、git、数据库），会砍掉一半价值 |
| B. **命令白名单 + 显式确认**（推荐） | 沿用 M4 口径： `command` 必须在白名单（如 `npx`/`uvx`/`python`/`node` 等 **解释器或已登记可执行**）， `args` 进审计；首次启用要人点确认，展示完整命令行 | 与项目既有安全哲学一致；代价是"任意二进制"不可用（可接受） |
| C. 任意命令 + 沙箱目录限制 | 灵活 | 沙箱不能阻止"进程读用户目录/联网"，保护有限； **不推荐** |

**联网/环回**：MCP server 与本项目自身都是本机进程 → **HTTP 型 server 的 SSRF 守卫要单独例外**（`127.0.0.1` 是合法目标），
但 **用户手填的远程 MCP URL** 仍要禁内网/链路本地（复用 `tools/cli_docs.py` 的守卫，只放行显式 localhost 白名单）。

### 4.6 「发现 → 确认」流程（复刻 v2.5 的证据链套路）　→ **裁决：采纳本方案**
```
① 用户填来源配置（表单）
 ↓
② 点【连接体检】→ 逐步报告（抄参考项目的七步，但用 SDK 实现）
 · 配置解析 / 传输识别 / 连通或命令存在 / 协议协商（版本）/ 列工具 / 错误语义 / 延迟
 ↓
③ 点【发现能力】→ 候选列表（每条: ref / 名称 / 描述 / 参数摘要 / 只读建议 / 来源行号）
 ↓
④ 人工逐条确认（可批量）→ 写入 tool_capabilities（派生）→ bump_pool_version → 向量惰性重建
 ↓
⑤ Agent 端：路由器按提问选相关能力 → 注入卡片（完整/紧凑/仅名称）→ 模型调 invoke_tool(ref, args)
```

**"非硬编码"在这里落地**：②③④ 全是数据流，没有一行"为某个 API/某个 MCP 写的代码"。

---

> **M1-2 的 SSRF 口径（2026-09-25 用户裁决，覆盖本节初稿的"环回/内网一律拒绝"）**：
> ① `base_url` 是 **用户自己填的** → **默认允许内网/环回**（自托管、公司内网、M5 阶段 2 的局域网价格源
> 都是真实场景），要收紧可设 `config_json.allow_private=false`；
> ② **模型可控的参数值**若本身是 URL（`http(s)://…`）→ 一律走 `cli_docs._host_is_safe` 守卫；
> ③ **跨主机重定向一律不跟随**（只跟同主机一次，挡住"公网 URL 被 302 到内网"的经典绕过）。

## 五、契约与数据模型（照抄即可开工）

### 5.1 新表： `tool_sources`（一张表容纳 api / mcp 两种来源；CLI 继续用 `user_clis`）

```sql
CREATE TABLE IF NOT EXISTS tool_sources (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 account_id INTEGER NOT NULL REFERENCES accounts(id),
 source TEXT NOT NULL, -- 'api' | 'mcp'
 slug TEXT NOT NULL, -- 短名（ref 里用它）：weather / fs
 name TEXT NOT NULL DEFAULT '', -- 展示名（「和风天气」「本地文件系统」）
 config_json TEXT NOT NULL DEFAULT '{}', -- ★ 类型扩展位：
 -- api: {"base_url":"https://…","auth":{"type":"header","name":"Authorization","prefix":"Bearer ","secret":"…"},
 -- "headers":{…},"timeout":30,"max_bytes":1048576}
 -- mcp: {"transport":"stdio","mcpServers":{"fs":{"command":"npx","args":[…],"env":{…}}}}
 -- 或 {"transport":"http","server_url":"http://127.0.0.1:8765/mcp","auth":{…}}
 abilities TEXT NOT NULL DEFAULT '', -- 可选：这条来源整体的人话说明（进来源级卡片）
 docs TEXT NOT NULL DEFAULT '', -- 文档链接（复用 M4c 的 docs 字段语义）
 state TEXT NOT NULL DEFAULT 'none', -- none | configured | verified | failed（体检结果）
 enabled INTEGER NOT NULL DEFAULT 1,
 note TEXT DEFAULT '',
 created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
 updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
 UNIQUE(account_id, source, slug)
);
```

### 5.2 扩展既有能力表（**不新建能力表**）

`tool_capabilities` 已存在（`schema_personal.py:292`），按项目既有迁移风格 **幂等补列**：

```python
# 新增列（老库用 PRAGMA table_info 判断后 ALTER TABLE ADD COLUMN）
invoke_spec TEXT NOT NULL DEFAULT '{}' -- ★ 执行侧元数据（模型不可见）：
 -- api: {"source_id":3,"method":"GET","url_template":"/v1/current/{city}","bindings":[
 -- {"name":"city","location":"path","required":true},
 -- {"name":"lang","location":"query","required":false}],
 -- "body_mode":"json","auth":"inherit"} # auth=inherit → 用来源的 auth
 -- mcp: {"server_id":3,"tool":"read_file","annotations":{"readOnlyHint":true,…}}
input_schema TEXT NOT NULL DEFAULT '{}' -- ★ JSON Schema（模型可见）；CLI 可为空
read_only INTEGER NOT NULL DEFAULT 1 -- 0 = 会改外部状态；1 = 只读（**人工确认前一律 enabled=0，不入池**，见 §4.6）
confirmed_at DATETIME -- 人工确认时间（NULL = 未确认， **不可调用**）
cost_hint TEXT NOT NULL DEFAULT '' -- 可选：计费/配额提示（给"成本表"用）
```

> ⚠️ **`confirmed_at` 的语义只对 `source in ('api','mcp')` 生效**（实现时定死）：
> 老库升级后 **CLI 行的 `confirmed_at` 恒为 NULL**，而 CLI 的放行靠 `user_clis` 的只读清单
>（既有口径，不变）。 **任何"NULL 即不可调用"的一刀切判定都会锁死现有 CLI 工具**（实测在用例里被逮到）。
> ```sql
```

**唯一约束不变**（`UNIQUE(account_id, source, ref)`）→ 重复发现同一工具是幂等 update，不会产生重复能力。

### 5.3 执行网关（一个 dispatcher + 三个 driver）

```python
# tools/capability_invoke.py（新）
@tool
def invoke_tool(ref: str, params: dict) -> str: # Agent 唯一新增入口（见 §4.2）
 # 实现注记：参数名必须是 `params` 不能叫 `args` —— langchain 的 `@tool` 把 `args`
 # 当保留名（会变成模型看不懂的 `v__args`），实测踩过；用例已断言入参恰为 {ref, params}
 ...

def invoke(ref: str, args: dict, *, account_id: int, via: str = "agent") -> dict:
 """统一执行网关。步骤（每一步都可单独测试）：
 1) 解析 ref → (source, service, name) → 查 tool_capabilities（校验归属/启用/已确认）
 2) 参数校验：input_schema(required/type/enum) → 失败则返回"缺哪个参数"的可读错误
 3) ★ 放行判定（三来源各自 driver 内的同一函数签名 readonly_verdict(...)）
 4) 执行：cli→shell_runtime / api→api_driver / mcp→mcp_driver
 5) 结果裁剪：摘要 + 上限（api/mcp 同 1MB）+ 结构化字段优先
 6) 审计：data/audit/commands.jsonl（追加 source/ref/args_digest/耗时/结果摘要/是否被拒）
 7) 返回给模型：文本（含"下一步可做什么"的提示，与 CLI 工具风格一致）
 """

# tools/_runtime/api_driver.py（新）
def call(source_cfg: dict, invoke_spec: dict, args: dict) -> dict:
 """httpx（已在环境里，0.28.1）+ 路径参数替换 + query/header/body 按 bindings 分发 + 超时/体积上限。
 注：GET 以外的请求 **只有在能力被显式勾选 read_only=0 时**才会走到这里。"""

# tools/_runtime/mcp_driver.py（新）
def call(source_cfg: dict, invoke_spec: dict, args: dict) -> dict:
 """官方 SDK：Client(url|StdioServerParameters) → async with → call_tool(name, arguments)
 → 取 structuredContent（有）否则拼 content 文本；会话/重连交给 SDK。"""

# tools/mcp_discovery.py（新）
async def probe(source_cfg: dict) -> dict: # 体检：七步报告（抄参考项目的分步体检，但用 SDK）
async def list_tools(source_cfg: dict) -> list[dict]: # → 候选能力（inputSchema 原样带走）
```

### 5.4 与既有代码的对接点（改哪里，一目了然）

| # | 位置 | 改动 |
| --- | --- | --- |
> **实现注记（2026-09-25，M1 落地后回填）**：第 3 条"`_entry_text` 补参数摘要"实现为 **仅对非 CLI 生效**
>（CLI 加参数摘要会改变既有条目的 content hash → 触发全量向量重算，且有 `m5b_pool_e2e` 钉着）；
> 另外发现并修掉一个 **快路径缺口**： `route_tools()` 在"池 ≤ FAST_PATH_MAX"时原来直接用
> `cli_reg.agent_brief()`（**CLI 专属**）→ 池小的时候 API/MCP 能力对模型完全隐身
>（真机 e2e 逮到：3 CLI + 1 API = 4 条走快路径，注入文本里没有那条 API 能力）。
> 修法：含非 CLI 来源时从 **统一池**渲染全量卡（`_full_pool`），纯 CLI 时保持 `agent_brief` **逐字一致**
>（回归用例： `test_pool_mixed_sources.py` 的 `test_fast_path_*` 两条）。

| 1 | `tools/capability_pool.py:130` `_ADAPTERS` | 打开两行： `SOURCE_API: _entries_from_apis`、 `SOURCE_MCP: _entries_from_mcps`（各自读 `tool_sources`+`tool_capabilities` 派生） |
| 2 | `capability_pool.CapabilityEntry` | 加 `input_schema: dict`、 `invoke_spec: dict`、 `read_only: bool`、 `confirmed: bool` 四个字段（**默认值保证 CLI 侧零改动**） |
| 3 | `capability_pool._entry_text()` | 拼接文本时补一句 **参数摘要**（如 `参数: city(必填,path), lang(可选,query)`）→ 提升路由信号质量 |
| 4 | `tool_router` 三形态渲染 | 完整卡增加"参数"一行（算进 1500 字预算；超预算先降级紧凑行） |
| 5 | `agent/build_context.py` 注入块 | 模型可见的工具说明从"只讲 `run_shell_command`"扩为"两个工具"（CLI + `invoke_tool`）， **机械细节仍只写在 `@tool` docstring 一处**（提示词维护纪律） |
| 6 | `tools/schema_personal.py` | 加 `tool_sources` 表 + `tool_capabilities` 补列 + `purge_account()` 自省带上新表（既有机制自动生效） |
| 7 | `_runtime/shell_runtime.py` 审计 | 审计写入抽成公共函数（`_audit` 已在 `shell_runtime.py:354`）供 api/mcp 复用， **同一条 jsonl、同一字段名**（新增 `source/ref`） |
| 8 | `api/server.py` + `front/index.html` | 新增"工具来源"配置页（API/MCP 两套表单 + 体检报告 + 候选确认列表）； `/api/tools/source/*`、 `/api/tools/discover`、 `/api/tools/confirm` |

### 5.5 前端表单字段（配置导向，不用写代码）

**API 来源**：短名 / 展示名 / base_url / 鉴权（类型下拉： `header`|`query`|`none` + 名称 + 前缀 + 密钥）/ 默认头 / 超时 / 文档链接 /【粘贴 OpenAPI】或【从 URL 拉取】→ 候选列表（勾选 + 每条可改显示名与只读）/ 写方法逐个勾选开关

**MCP 来源**：短名 / 展示名 / 传输（`stdio`|`http`）/ stdio→命令 + 参数 + 环境变量（**直接从 Claude/Cursor 配置整段粘贴**）/ http→URL + 鉴权 /【连接体检】→ 七步报告 /【发现工具】→ 候选列表（默认按 `readOnlyHint` 预勾选，逐条确认）

---

## 六、实施步骤（每步能独立验证 → 每步一个提交点）

> **进度**： **M1 六步 / M2 五步全部 ✅ 已实现并实测通过**（M1 2026-09-25、M2 2026-09-27）。
> 实现与实测记录： `M1-API适配器实现记录.md`、 `M2-MCP适配器实现记录.md`。

### M1（API 适配器）

| 步 | 做什么 | 验证（当场可跑） |
| --- | --- | --- |
| M1-1 | 建 `tool_sources` + `tool_capabilities` 补列 + 迁移幂等 | `pytest tests/test_assembly.py`；重复 `ensure_tables()` 两次无异常；老库升级后 CLI 池条目数不变 |
| M1-2 | `api_driver.call()`（httpx + bindings 分发 + 上限/超时 + SSRF 守卫） | 新增 `tests/test_api_driver.py`：路径参数替换、query/header/body 分发、超时、超大响应截断、SSRF 三项口径（**已按用户裁决改为分层**，见下）（**26 项**， **全离线**，用 `httpx.MockTransport`） |
| M1-3 | OpenAPI → 候选能力（**录入方式只有"粘贴"**； `operationId` 兜底命名、 `$ref` 内联、required、写方法标记） | `tests/test_openapi_import.py`：用 3 份真实 spec 片段（含无 operationId、含 path 参数、含 requestBody）断言候选条数与字段（≥15 项） |
| M1-4 | `_entries_from_apis` + `_ADAPTERS` 打开 + `_entry_text` 补参数摘要 + **路由器快路径改从统一池渲染**（见 §5.4 实现注记） | `tests/m5b_pool_e2e.py`（62 项）· `tests/m5b_router_e2e.py`（34 项）全绿 + `tests/test_pool_mixed_sources.py`（13 项，含两条快路径回归） |
| M1-5 | `invoke_tool` 网关 + 放行判定 + 审计 + 提示词 docstring | `tests/test_capability_invoke.py`：未确认/未勾选写方法/缺必填/未知 ref 四类拒绝 + 成功路径（**用假 API**）；审计行断言 |
| M1-6 | 前端配置页 + 候选确认流 | `tests/m4a_frontend_logic.js` 风格的新 harness（≥20 项）；真机：接一个真实公开 API（如天气）跑通 |

**M1 收尾验收**：G2 的混合池标定（`_m5b_route_calib.py`）+ `m5b_pool_e2e` + `m5c` 前端 harness + 全量前端回归。

### M2（MCP 适配器）

| 步 | 做什么 | 验证 |
| --- | --- | --- |
| M2-1 | 引入 `mcp>=2,<3`（`uv add`）+ 锁文件更新 | 干净环境 import OK； **不影响**既有启动耗时（<0.2s 增量） | ✅ **已实现**（2026-09-27，见 `M2-MCP适配器实现记录.md`） |
| M2-2 | `mcp_driver`（stdio + Streamable HTTP + 进程内三种连接） | 新增 `tests/test_mcp_driver.py`： **用 SDK 进程内传输**起一个 `MCPServer`（含 1 只读 + 1 写工具）→ 断言 list/call/结构化结果；stdio 路径用一个小脚本 server 验证 | ✅ **已实现**（2026-09-27，见 `M2-MCP适配器实现记录.md`） |
| M2-3 | `probe()` 七步体检（抄参考项目，但用 SDK 实现） | 新增 `tests/test_mcp_probe.py`：正常 / 命令不存在 / 协议不兼容（假 server）/ 超时 四条路径 | ✅ **已实现**（2026-09-27，见 `M2-MCP适配器实现记录.md`） |
| M2-4 | 发现→候选→人工确认→入池（`ast.literal_eval` 不用；JSON 解析 + schema 校验） | `tests/m5_mcp_e2e.py`（新）：未确认不可调用 → 确认后进池 → 路由器能命中 → `invoke_tool` 真调用成功 | ✅ **已实现**（2026-09-27，见 `M2-MCP适配器实现记录.md`） |
| M2-5 | 前端 MCP 配置页（`mcpServers` 粘贴 + 体检报告渲染 + 确认列表）—— ★ **照 M1 的"自定义 CLI 范式"**：常态 = 指导栏 + 已接入服务卡（卡内直接列工具与状态）；表单与粘贴只在**弹窗 **里出现，不许平铺在页面上 | 前端 harness（≥25 项） | ✅**已实现 **（2026-09-27，见 `M2-MCP适配器实现记录.md`） |

**M2 收尾验收**：G1（零代码接入演示）+ G4（候选条数 = server 工具数）+ 混合三来源标定。

> **顺序建议**：仍然 **M1 → M2**（API 简单、能立刻吃到天气/价格等现成接口，且 M2 能复用 M1 的候选/确认/审计三条链路）。

---

## 七、测试与回归清单

**新增**（全部 **离线优先**，不依赖外网、不耗 Tavily）：

| 用例 | 覆盖 | 规模 |
| --- | --- | --- |
| `tests/test_api_driver.py` | 参数位置分发 / 序列化 / 超时 / 体积上限 / SSRF 守卫 / 鉴权头注入 | ~12 |
| `tests/test_openapi_import.py` | operationId 兜底 / `$ref` / required / 写方法标记 / 异常 spec 容错 | ~15 |
| `tests/test_capability_invoke.py` | 四类拒绝 + 成功路径 + 审计行 + ref 解析 | ~10 |
| `tests/test_mcp_driver.py` | 进程内 MCP server（只读+写）/ stdio 脚本 server / 结构化解包 | ~10 |
| `tests/test_mcp_probe.py` | 体检七步的四条异常路径 | ~8 |
| `tests/m5_mcp_e2e.py` | 未确认→确认→入池→路由命中→真调用（TestClient + 本地 stdio server） | ~20 |
| `tests/m5c_tool_source_frontend.js` | 来源表单 / 候选确认 / 体检报告渲染 / setup 导出不变量 | ~25 |

**必须重跑**（防"改 A 坏 B"）： `m5b_pool_e2e`(62) · `m5b_router_e2e`(34) · `m5_abilities_e2e`(43) · `m4c_cli_e2e`(109) · `m4b_cli_e2e`(51) · pytest 全套(60+) · 前端 harness 全套 · `frontend_setup_exports_check`。

**纪律**：
- **不烧 Tavily**：本议题全程不需要外网 → 真机用例只用本地 MCP server 与公开无鉴权 API（或 mock）；**跑前确认额度 **。
- **账号隔离**：新增表全部带 `account_id`；用例用既有账号做断言（**断言账号数不变**）； `purge_account()` 自省机制自动带上新表。
- **审计与只读**：新执行器的拒绝路径必须有用例（这是安全回归，不是可选）。

---

## 八、风险、回退与遗留

| 风险 | 说明 | 缓解 |
| --- | --- | --- |
| 混合池路由退化 | 条目文本特征变化（API/MCP 描述更长、含参数摘要） | M1 验收强制"混合池重新标定"；若 z 分数分布变化，重标 `Z_STRONG`/`LEX_MIN` 并记录 |
| 注入预算被参数挤爆 | 完整卡带参数行后可能超 1500 字 | 渲染器按预算降级（完整卡 → 紧凑行 → 仅名称），用例断言总量 |
| SDK v2 较新 | v1→v2 有破坏性变更（`FastMCP`→`MCPServer` 等） | 钉 `mcp>=2,<3`；只用 `Client` + (`MCPServer` 仅测试用)，不用边缘 API |
| stdio MCP = 任意命令执行 | 最重的安全面 | §4.5 的 B 档（命令白名单 + 显式确认 + 审计）； **写进 README 的安全声明** |
| 远程 MCP OAuth | 企业级 server 要 OAuth 2.1 动态注册 | **本版不做**，只支持"静态 token/无鉴权"；列为遗留 |
| OpenAPI 噪声 | 200 个操作里大部分与用户无关 | 候选 **按 tag 分组 + 默认不勾选**；靠 router 选相关；可选 `describe` 工具 |
| 凭据落库 | API key / MCP env 明文进 SQLite | 库在 `data/`（已 gitignore）；前端只回显掩码；日志与审计 **脱敏**；不写入审计正文 |

**回退**：两张新表与新增文件 **不改变既有 CLI 路径**（`CapabilityEntry` 新字段都有默认值）→ 出问题只需关掉 `_ADAPTERS` 两行，回到"只有 CLI"的现状。

**遗留（本版不做，记进规划书 §3）**：MCP 的 `resources`/`prompts` 两个原语（只做 `tools`）；OAuth 远程 MCP；OpenAPI 的 `oneOf/anyOf` 复杂体（先降级为"只保留基础类型 + 人工补充"）；"从调用示例反推"的档 ③； **OpenAPI 的"填 URL 抓取"（§零 O2）**； **内置 `_describe` 能力（§零 O3）**。

---

## 九、开放项（3 个 —— **已全部确认**，2026-09-25）

§四 的六个分叉在 §零 裁决完毕；定稿扫尾时又发现 3 处"方案没写死、但会影响实现"的点，已逐条确认：

| # | 开放项 | **裁决** | 落地点（开工时看这三处就够） |
| --- | --- | --- | --- |
| **O1**<br>（原 F1） | API 的 `ref` 形态 | **`api:<服务名>#<操作名>`，且 `invoke_spec` 里存 `source_id`** 指向来源行 | §3.3 命名规则 · §5.2 `invoke_spec` 示例 ·**M1-1**（建表时把 `schema_personal.py:295` 那句占位注释一并改成新口径） |
| **O2** | OpenAPI「填 URL 由后端抓取」 | **不进 M1**：先只做"粘贴" | §4.3 ①档 ·**M1-3**；后置项已记入 §八 |
| **O3** | 内置 `api:<service>#_describe` | **后置 ** | §4.3 渐进披露段；后置项已记入 §八 |

> 三个都 **不阻塞开工**：O1 是 M1-1 建表时半小时的事；O2/O3 是 **范围**决定（不是技术风险）。

---

## 完结说明

- 本文件是 **M1 / M2 两个里程碑的实现图纸**； `v3.0规划书.md` 里只保留"目标 / 顺序 / 验收判据"，细节指向这里。
- **所有议题方案定稿后**，再把各方案的目标与判据抽进定版规划书（按 `docs/v3.0/README.md` §五 的汇编规则）。
- 研讨期全程 **只读**：未改代码、未动数据、未起服务、零 Tavily 消耗。
