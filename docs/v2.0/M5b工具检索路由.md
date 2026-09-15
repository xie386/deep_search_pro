# M5b · 工具检索路由（统一能力目录 + 相关性注入）

> **里程碑**：v2.0 M5b（第三层：路由层）
> **前置**：M5a 已完成（能力语义注入：`abilities` 能力描述 → 能力路由卡 → few-shot 映射示例）
> **本文档取代** `M5工具智能路由.md` §四「M5b：检索式工具路由（第三层，最小闭环）」那版偏简的设计；
> M5a 的实施记录与实测数据仍在原文档 §三 / §九，本文不重复。
> **状态**：设计待评审（开工前须逐条确认 §【决策记录】）

---

## 【决策记录】待拍板项

| # | 决策点 | 待定项 | 建议 |
|---|---|---|---|
| 1 | **架构分叉** | MCP 走「原生工具（langchain-mcp-adapters）」还是「统一目录 + 通用调用工具」？ | **统一目录 + 通用调用工具**：M2 已把「agent 静态化 + invoke 期注入」定成契约，动态工具集会逼出「每轮重建 agent / 按工具集指纹缓存多份 agent」；而且三种来源共用一套路由，路由器不必 `if source == ...` |
| 2 | **触发阈值** | 什么时候从 M5a 切到 M5b？ | **两条任一满足即触发**：① 已接入工具数 ≥ 5；② 任意工具的简报块发生降级（紧凑行）且其探针失败 |
| 3 | **未命中工具的可见性** | 未命中工具完全不注入，还是保留一行名称？ | **保留「名称 + 可执行名」一行**：路由是概率性的，藏掉工具 = 该类问题永远无解；一行只 ~40 字，代价极低 |
| 4 | **API 写操作默认策略** | API 工具默认放行哪些方法？ | **默认只放行 GET**；`POST/PUT/PATCH/DELETE` 必须在配置里显式勾选（沿用 M4c「清单必须人给」的安全口径） |
| 5 | **MCP 工具默认策略** | MCP server 声明的工具默认放行哪些？ | **默认只放行只读工具**（按 MCP schema 的 `readOnlyHint`/命名启发式预勾选，**由人确认**后生效）；写操作工具默认不勾 |
| 6 | **缓存与失效** | 池向量如何缓存与失效？ | `pool_version` 计数器：任何工具增删改/状态变更 → version+1；路由器按 `(account_id, version)` 常驻内存缓存 |
| 7 | **实施顺序** | 目录层先做还是三来源并行？ | **串行四段**：M5b-1 目录层 → M5b-2 路由器 → M5b-3 API 适配器 → M5b-4 MCP 适配器；每段端到端验证通过再进下一段（沿用 M4/M5a 节奏纪律） |
| 8 | **token 预算** | 命中形态注入多少？ | 完整卡 ≤ 3 个、注入总量 ≤ 1500 字（≈750 token）；未命中保留名称行 |

---

## 〇、为什么要做 M5b

### 0.1 M5a 的注入是「单点聚合 + 预算切分」

M5a 的注入链路（无缓存、每轮现装配）：

```
topic/question
   │
   ▼
cli_registry.agent_brief(account_id)      ← 把「全部已接入 CLI」拼成一段文本
   │  max_chars = 2600（M5b 前的临时缓解值）
   │  share = max(300, 2600 / CLI 数)      ← 每个 CLI 的能力描述展示额度
   │  装不下 → 降级成紧凑行（名称 + 可执行名 + 命令数 + 关键词首行）
   ▼
agent/build_context.py::compose_dynamic_prompt(cli_brief=…)
   ▼
动态 SystemMessage 的【可用命令行工具】区块（模型唯一的工具信息来源）
```

### 0.2 实测：3 个 CLI 就把预算打爆了

| CLI | 能力描述 | 该块实际字数 | 累计写入 |
|---|---|---|---|
| `weread` | 371 字 | 569 | 569 ✅ |
| `wechat-cli` | 251 字 | 433 | 1002 ✅ |
| **`bili`** | **787 字** | **959** | 需要 959，**只剩 398** → 被整块丢弃 ❌ |

旧版（`max_chars=1400`）会输出「其余 1 个 CLI 因篇幅未列出」，**bili 从未进入系统提示词**——
面板显示「已接入」、本机检测正常、**没有任何报错**，只是模型永远不去调它。
（该缺陷已由「公平分配 + 降级不消失」临时缓解，见 §0.4。）

### 0.3 三条结构性缺陷（会随规模放大）

| # | 缺陷 | 后果 |
|---|---|---|
| 1 | **线性预算切分** | 工具越多、每份额度越小 → 必然降到紧凑行 → 命令级映射丢失，路由精度随规模**单调下降** |
| 2 | **均等 ≠ 相关** | 用户问「我在读什么书」，却把 B 站、日程、企业通讯的完整卡一并塞进上下文；**该给的信息被稀释，不该给的占用预算** |
| 3 | **每轮全量注入** | token 成本与工具数线性增长；长上下文还会稀释注意力（M5a 实测：模型会把示例里的头两条命令当「万能命令」） |

> 结论：现有机制是**预算分配器**，不是**相关性选择器**。加 API / MCP 之后工具数会翻倍，缺陷 1/2/3 同时放大。

### 0.4 当前临时缓解（M5a 已落地，不是 M5b 的替代）

- 预算 1400 → **2600**；`share = max(300, 预算 / 工具数)` 公平分配；
- 装不下 → **紧凑行**（名称 + 可执行名 + 命令数 + 能力描述首行=关键词），**删除了「其余 N 个未列出」话术**；
- 效果：3 个 CLI 全部进提示词（1981 字）。

它保证「不丢工具」，但**不解决「给谁的卡更详细」**——这正是 M5b 要解决的。

---

## 一、设计约束（不可违背）

| # | 约束 | 来源 | 对 M5b 的要求 |
|---|---|---|---|
| 1 | **agent 静态化，上下文 invoke 期装配** | M2 请求装配管线化 | 不动态增删原生 tool；M5b 只改**注入文本** |
| 2 | `agent/build_context.py` **不 import `api.*`** | 既有铁律 | 能力卡文本由 server 层读好传入（与 M5a 同款） |
| 3 | **M5b 只动「怎么被想起」，不动「能不能跑」** | M4c/M5a 安全口径 | 六道闸 / 第七道闸 / 只读闸 / 白名单**零改动** |
| 4 | **复用 M3 检索栈** | 已落地 | bge-large-zh + BM25 + RRF；Chroma 独立 collection，不与知识库混 |
| 5 | **调用事实可审计** | M4c 诊断铁律 | 新来源的调用同样写 `data/audit/commands.jsonl`（加 `source` 字段） |
| 6 | **fail closed** | M4c | 任何环节异常（embedding 挂、池读失败）→ 回退 M5a 全量简报，绝不静默少注入 |

---

## 二、统一能力目录（M5b 地基）

### 2.1 统一契约：`CapabilityEntry`

路由与注入**只认这一个形状**，不感知来源：

```python
# tools/capability_pool.py
@dataclass(frozen=True)
class CapabilityEntry:
    source: str        # 'cli' | 'api' | 'mcp'
    ref: str           # cli: bin（weread）│ api: tool_id（weather）│ mcp: "server/tool"（github/issue_list）
    name: str          # 展示名（微信读书 / 天气 API / GitHub MCP）
    keywords: str      # 关键词行 —— 路由主信号（字面匹配 + 向量都用它）
    abilities: str     # 用户语言映射（说法 → 命令 / 参数 / 用途）
    invoke_hint: str   # 调用形态（模型可见的精简版：命令清单 / 方法+路径 / server+tool）
    enabled: bool
    # 渲染器（由适配器实现）
    @property
    def card_text(self) -> str: ...       # 完整能力卡（命中时注入）
    @property
    def compact_text(self) -> str: ...    # 紧凑行（次要命中）
    @property
    def nameonly_text(self) -> str: ...   # 仅名称 + 可执行名（未命中保底可见性）
```

### 2.2 落库：SQLite 单一事实源 + 向量另存

```sql
-- tools/schema_personal.py（沿用 idempotent CREATE + ALTER 迁移模式）
CREATE TABLE IF NOT EXISTS tool_capabilities (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  account_id  INTEGER NOT NULL,
  source      TEXT    NOT NULL,          -- cli | api | mcp
  ref         TEXT    NOT NULL,
  name        TEXT    NOT NULL DEFAULT '',
  keywords    TEXT    NOT NULL DEFAULT '',   -- 关键词行（路由主信号）
  abilities   TEXT    NOT NULL DEFAULT '',   -- 用户语言映射
  invoke_hint TEXT    NOT NULL DEFAULT '',
  enabled     INTEGER NOT NULL DEFAULT 1,
  updated_at  TEXT    NOT NULL DEFAULT '',
  UNIQUE(account_id, source, ref)
);

-- 池版本（缓存失效用）
CREATE TABLE IF NOT EXISTS tool_pool_meta (
  account_id   INTEGER PRIMARY KEY,
  pool_version INTEGER NOT NULL DEFAULT 0,
  updated_at   TEXT    NOT NULL DEFAULT ''
);
```

**向量**：Chroma 独立 collection `tool_route_{account_id}`（与 M3 的 `kb_user_{account_id}` 同款账号隔离），
`metadata = {source, ref}`，**只存向量**；文本仍以 SQLite 为单一事实源（改文本 → 重算该条向量）。

### 2.3 派生而非迁移（CLI 零迁移）

CLI 的能力描述已经存在 `user_clis.abilities`，**不搬家**：

```
user_clis  ──cli_entry()──┐
tool_api   ──api_entry()──┼──▶ tool_capabilities（物化视图/派生表，可随时重建）
tool_mcp   ──mcp_entry()──┘
```

- 写入 `user_clis` 的同时 upsert 一条 `tool_capabilities(source='cli')`（同一事务）；
- 提供 `rebuild_pool(account_id)` 用于**重建**（改完适配器逻辑跑一次即对齐）；
- 好处：CLI 侧现有数据/测试/前端**零改动**，M5b 是叠加层。

### 2.4 版本与失效

```python
def bump_pool_version(account_id: int) -> None:  # 任何写入口调用（save/delete/state/enable）
    ...
def pool_entries(account_id: int) -> tuple[int, list[CapabilityEntry]]:  # (version, entries)
```

路由器缓存 `(version → entries + 向量矩阵)`；version 变了就重算，不用 TTL。

---

## 三、路由算法（相关性选择）

### 3.1 主流程

```
route_tools(account_id, question) -> RouteResult
│
├─ ① 快路径：池内 enabled 条目 ≤ 4  →  直接返回 M5a 全量简报（跳过 embedding，零额外成本）
│
├─ ② 混合检索（复用 M3 口径）
│     稠密：bge-large-zh  embed_query(question)  ↔  池向量（≤ 50 条，暴力余弦即可，不建 ANN）
│     稀疏：question ↔ entry.keywords 的字面命中（BM25 口径，中文按字/词 2-gram）
│     融合：RRF（与 M3 同一套；bge 查询前缀由 kb_service 统一处理，不重复造）
│
├─ ③ 分层注入（**相对信号**，不是绝对相似度——绝对阈值实测已被证伪，见 §3.1.1）
│     置信判定：词法命中 ≥ LEX_MIN(2)  或  向量 top1 的 z 分数 ≥ Z_STRONG(2.0)
│       不置信 → **回退 M5a 全量简报**（不猜；回退是安全行为，不是失败）
│     置信后按融合排序铺开：
│       第 1~3 名   → 完整能力卡 + 该工具的 few-shot 映射示例
│       第 4~5 名   → 紧凑行（名称 + 可执行名 + 命令数 + 关键词）
│       其余条目    → 仅「名称 + 可执行名」一行（保底可见性，决策 3）
│
└─ ④ 预算护栏：完整卡 ≤ 3 个；注入总量 ≤ 1500 字；超出时按序降级（先降级再截断）
```

### 3.1.1 阈值标定（实测，别再改回绝对阈值）

**开工时的假设被实测推翻**：原设计按绝对余弦相似度分层（τ_high 0.50 / τ_low 0.35）。
实测发现 bge-large-zh 对中文短问句的余弦**区分度极低**——3 个 CLI 上四条不同领域问句的
top1 与 top3 只差 **0.02~0.06**，绝对值全部落在 **0.53~0.70**。也就是说「相似度 > 0.35」会
**全部命中**，分层形同虚设；而「相似度 > 0.5」依然全部命中。绝对阈值在这类文本上不可用。

改用**相对信号**（8 工具异构池实测，`tests/_m5b_route_calib.py`）：

| 问句 | 向量 top3 | z_top | 词法命中 | 结果 |
|---|---|---|---|---|
| 我本周还有什么安排 | sched .64 / ghcli .60 / vidz .60 | 2.36 | 8 | ✅ 路由→sched |
| 帮我看看昨天接口有没有报错 | logq .72 / sched .69 / ghcli .67 | 2.26 | 8 | ✅ 路由→logq |
| 我有哪些还没合并的 PR | ghcli .64 / imq .63 / sched .62 | 1.97 | 5 | ✅ 路由→ghcli |
| 现在外面天气怎么样 | wxapi .72 / vidz .68 / sched .67 | 2.45 | 4 | ✅ 路由→wxapi |
| 宁德时代今天涨了多少 | finq .63 / logq .61 / vidz .60 | 1.65 | 0 | ～ 回退全量（保守） |
| 我正在读什么书 | booky .62 / sched .57 / vidz .57 | 2.46 | 4 | ✅ 路由→booky |
| 那个视频讲了什么 | vidz .69 / sched .62 / logq .62 | 2.36 | 6 | ✅ 路由→vidz |
| 和妹妹的聊天记录 | imq .70 / logq .68 / vidz .67 | 1.99 | 6 | ✅ 路由→imq |
| 我这个月读了多久 | booky .57 / imq .55 / vidz .55 | 1.93 | 0 | ～ 回退全量（保守） |
| 今天大盘怎么样 | finq .72 / vidz .70 / logq .69 | 1.88 | 4 | ✅ 路由→finq |
| **你是谁（反例）** | vidz .65 / sched .64 / logq .63 | 1.57 | 0 | ✅ 回退全量 |
| **帮我写一首诗（反例）** | finq .65 / logq .65 / sched .64 | 1.35 | 0 | ✅ 回退全量 |

结论（定阈值的依据）：

1. **命中判定正确率 10/10**（路由走的 8 条命中集都含正确工具；2 条保守回退）；
2. **反例 z 上限 1.57，真命中 z 下限 1.65** —— 只差 0.08，所以 Z_STRONG 取 **2.0**
   （宁可让弱信号回退全量 = M5a 行为，也不要把噪声放进来）；
3. **词法是最可靠的那一路**：所有 lex ≥ 2 的用例都命中，故 `LEX_MIN = 2` 单独即可置信；
4. z 随工具数增加而拉大 → 天然适配规模增长，这是相对信号相对绝对阈值的本质优势。

**注入量**（8 工具，全量形态 2499 字）：

| MAX_FULL_CARDS | 命中时注入量 | 占全量 |
|---|---|---|
| 1 | 1273 字 | 51% |
| 2 | 1465 字 | 59% |
| **3（默认，精度优先）** | **1644 字** | **66%** |

> 默认取 3 张完整卡：多一张卡意味着「真工具排在第 2/3 名」时仍拿到完整映射。
> 若更在意 token，把 `MAX_FULL_CARDS` 调成 2 即降到 59%（文档 §7.1 的 60% 目标按此口径达成）。

### 3.2 返回结构（server 层消费）

```python
@dataclass
class RouteResult:
    brief: str            # 注入用的完整文本（含【可用命令行工具】区块内容）
    examples: str         # few-shot 示例区块（只对命中工具生成）
    mode: str             # 'full'（快路径/兜底） | 'routed'
    hits: list[str]       # 命中的 ref（便于写审计与度量）
    tokens_est: int       # 估算注入量（度量用）
```

### 3.3 为什么必须混合检索，而不是纯向量

- 中文短问句（「我在读什么书」「上周看过的视频」）向量区分度有限，尤其能力描述里大量是**同一个领域的不同动作**；
- 能力描述的**关键词行**是人工/AI 精心写的、与用户问句同分布的字面信号，BM25 口径命中极强；
- 这正是 M3 里选「BM25 + 向量 + RRF」的同一个理由——**直接复用，不新造轮子**。

### 3.4 server 层接入点（改动极小）

```python
# api/server.py::_run_agent（M5a 已有 cli_brief / 示例两段）
r = tool_router.route_tools(account_id, question)     # 新增一行
cli_brief, examples = r.brief, r.examples             # 取代原来的 agent_brief + ability_examples_block
```

> `build_context` 与 `prompts.yml` 的纪律段**不改**（区块名与语义保持 M5a 口径，降低回归面）。

---

## 四、三种来源的接入规范

### 4.1 CLI（已有，只改注入）

- 能力描述仍在 `user_clis.abilities`；派生进池；
- 调用仍走 `run_shell_command(bin, argv)`，只读闸 / 沙箱 / 审计零改动；
- M5b 对它的唯一影响：**注入哪几条卡由路由器决定**。

### 4.2 API（新）

```sql
CREATE TABLE IF NOT EXISTS tool_api (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  account_id   INTEGER NOT NULL,
  name         TEXT NOT NULL,            -- 展示名（天气 API）
  tool_id      TEXT NOT NULL,            -- 稳定标识（weather）
  base_url     TEXT NOT NULL,            -- https://api.example.com
  auth_env_key TEXT NOT NULL DEFAULT '', -- **只存 .env 里的变量名**，不存明文
  allow_methods TEXT NOT NULL DEFAULT 'GET',   -- 逗号分隔；默认只 GET（决策 4）
  allow_paths  TEXT NOT NULL DEFAULT '',       -- 每行一条：GET /v1/weather?city={city}
  abilities    TEXT NOT NULL DEFAULT '',       -- 用户语言映射（说法 → 参数模板）
  enabled      INTEGER NOT NULL DEFAULT 1,
  UNIQUE(account_id, tool_id)
);
```

**安全边界（必须全部实现）**：

| # | 规则 | 说明 |
|---|---|---|
| 1 | 密钥只在服务端注入 | `auth_env_key` 只存**变量名**；密钥永不进模型上下文（也就不会被诱导复述） |
| 2 | 目标 URL 不可由模型指定 | 只允许 `base_url` + `allow_paths` 里声明的路径模板；模型只能填**参数占位符** |
| 3 | 方法白名单 | 默认仅 `GET`；写方法需显式勾选（决策 4） |
| 4 | 禁跨域重定向 | 重定向目标 host 必须等于 base_url 的 host，否则拒 |
| 5 | 参数校验 | 参数值同样过「无 `..` / 无 `://` / 无绝对路径」检查（与沙箱参数级校验同口径） |
| 6 | 超时 / 响应上限 / 审计 | 30s、1MB、`data/audit/commands.jsonl`（`source:"api"`） |

### 4.3 MCP（新）

```sql
CREATE TABLE IF NOT EXISTS tool_mcp (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  account_id    INTEGER NOT NULL,
  name          TEXT NOT NULL,           -- 展示名（GitHub MCP）
  server_id     TEXT NOT NULL,           -- 稳定标识（github）
  transport     TEXT NOT NULL DEFAULT 'stdio',  -- stdio | http
  command_or_url TEXT NOT NULL DEFAULT '',      -- stdio: 启动命令；http: 端点
  enabled_tools TEXT NOT NULL DEFAULT '',       -- 每行一条：只放行的工具名（决策 5）
  abilities     TEXT NOT NULL DEFAULT '',       -- 用户语言映射（可留空 → 用 MCP 自带 description）
  enabled       INTEGER NOT NULL DEFAULT 1,
  UNIQUE(account_id, server_id)
);
```

**接入流程**：`tools/list` 拉取工具清单 → 按 `readOnlyHint` / 命名启发式**预勾选只读工具** →
用户在「自定义 CLI」同款面板确认（可改）→ 每个勾选的工具生成一条 `CapabilityEntry`
（`keywords`/`abilities` 种子取自 MCP 的 description，再走 M5a 的 `ability_writer` AI 草稿润色）。

**安全边界**：`enabled_tools` 白名单**服务端强制**（不依赖模型自觉）；写操作工具默认不勾；超时/上限/审计同上。

### 4.4 第四种来源怎么办

再写一个 `xxx_entry()` 适配器 + 一张来源表即可——**路由器与注入层一行不改**。
这是选「统一目录」而不是「MCP 原生工具」的核心收益。

---

## 五、调用侧：通用工具与闸门

M5b 需要新增两个**通用**工具（与 `run_shell_command` 平级，都不是「一个工具一个 schema」）：

```python
run_api_tool(tool_id: str, path_id: str, params: dict) -> dict
run_mcp_tool(server_id: str, tool: str, args: dict) -> dict
```

| 闸门 | CLI（已有） | API | MCP |
|---|---|---|---|
| 来源白名单 | 用户登记只读清单 | `allow_methods` + `allow_paths` | `enabled_tools` |
| 参数级路径校验 | ✅ `_resolve_in_sandbox` | ✅ 参数值同口径 | ✅ 参数值同口径 |
| 超时 / 输出上限 | 30s / 1MB | 30s / 1MB | 30s / 1MB |
| 落盘参数拦截 | `WRITE_FLAGS` | 不适用（无落盘） | 不适用 |
| 审计 | `source:"third_party"` | `source:"api"` | `source:"mcp"` |

**审计条目扩展**（向后兼容，老条目无 `source` 字段按 CLI 处理）：

```json
{"ts":"…","user":"…","source":"api","tool":"weather","call":"GET /v1/weather","args":{"city":"成都"},
 "ok":true,"elapsed":0.31,"truncated":false}
```

---

## 六、改动清单（文件级）

| # | 文件 | 改动 | 量级 |
|---|---|---|---|
| 1 | `tools/schema_personal.py` | `tool_capabilities` / `tool_pool_meta` / `tool_api` / `tool_mcp` 四表 + 幂等迁移 | 小 |
| 2 | `tools/capability_pool.py`（新建） | `CapabilityEntry`、三种 `*_entry()` 适配器、`pool_entries()`、`bump_pool_version()`、`rebuild_pool()`、向量 upsert | 中 |
| 3 | `tools/tool_router.py`（新建） | `route_tools()`：快路径 / 混合检索 / 分层注入 / 阈值 / 缓存 / 兜底；`RouteResult` | 中 |
| 4 | `api/server.py` | `_run_agent` 接入 `route_tools`；新增 `/api/api-tool/*`、`/api/mcp/*` 配置路由 | 小 |
| 5 | `tools/_runtime/api_runtime.py`（新建，M5b-3） | `run_api_tool` 执行器（白名单 + 密钥注入 + 闸门 + 审计） | 中 |
| 6 | `tools/_runtime/mcp_runtime.py`（新建，M5b-4） | `run_mcp_tool` 执行器（连接 + 白名单 + 闸门 + 审计） | 中 |
| 7 | `tools/api_tool_registry.py` / `tools/mcp_registry.py`（新建） | 配置读写（与 `cli_registry` 同款「配置导向」口径） | 中 |
| 8 | `static/index.html` | 面板：API / MCP 配置卡 + 统一「能力描述」编辑（复用 M5a 组件） | 中 |
| 9 | `prompt/prompts.yml` | 纪律段补「API/MCP 工具的调用形态 + 不得自造 URL/工具名」 | 小 |
| 10 | `tests/m5b_pool_e2e.py` / `tests/m5b_router_e2e.py` / `tests/m5b_pressure_probe.py` / `tests/m5b_api_e2e.py` / `tests/m5b_mcp_e2e.py` | 见 §七 | 大 |

---

## 七、验收与度量

### 7.1 加压探针矩阵（核心验收）

沿用 M5a 探针方法论（`tests/m5_probe_e2e.py`：真桩夹具、**只看首调**、会话真值配对、`M5_CONTROL` 对照组），
把工具数从 3 加压到 **8~10 个（混合来源）**：

| 组 | 工具构成 | 期望 |
|---|---|---|
| 基线 | 3 真 CLI（weread / wechat-cli / bili） | ≥ 10/10（复现 M5a） |
| 加压 | 3 真 + 假 CLI 2 个 + 假 API 2 个 + 假 MCP 3 个 | **≥ M5a 基线**（≥10/10） |
| 反例 | 同加压工具集 | 3/3 不误调 |
| token | 加压工具集 | 命中形态注入量 ≤ 全量形态的 **60%** |

### 7.2 兜底分支（必须有断言）

| 场景 | 期望行为 |
|---|---|
| 池空 / ≤ 4 条 | 走 M5a 全量简报（快路径），结果与 M5a **逐字一致** |
| 置信判定不通过（词法 0 且 z < Z_STRONG） | 回退全量简报（不猜） |
| 向量不可用（bge 挂 / collection 空） | **有词法命中则仍路由**（词法单路可用，实测可靠）；无词法命中才回退全量 |
| 池读取异常 / 路由内部异常 | try/except → 回退全量简报，**绝不静默少注入**，也绝不抛出 |

> 「向量不可用仍可路由」是实测后的口径修正：原本设想 embedding 一挂就整段回退，
> 但词法那一路（关键词行短语命中）本身就是**精确信号**——8 工具标定中所有 lex ≥ 2 的用例
> 全部命中正确工具。为了一次模型加载失败就放弃整个路由能力，收益太小、损失太大。

### 7.3 回归清单（每段收尾都跑）

`M4b 51` / `M4c 105` / `M5a 43` / `M4a 前端 56` / 教程 `35 + 31` / 天气 `55 + 51` / `dspro 36`
（`_run_agent` 是共享入口，任何路由改动都要复跑）

### 7.4 度量沉淀

- 每次加压探针落 `data/m5b_pressure_result.json`（逐条 `expect/got/tag/ok/exec_ok/mode/tokens_est`）；
- 在 `M5b` 文档记录「工具数 → 通过率 / 注入量」曲线，形成**路由回归基线**；
- 阈值每次调整必须重跑探针（禁止凭感觉调参）。

---

## 八、触发条件与实施节奏

### 8.1 触发条件（两条任一满足）

1. 已接入工具数（CLI + API + MCP）**≥ 5**；
2. **任意工具的简报块发生降级**（紧凑行）**且其探针失败**——说明预算切分已经在吃掉精度。

> 当前状态（2026-09-15）：3 个 CLI 已出现过一次降级（bili 787 字描述），
> 靠「公平分配 + 降级不消失」兜住；**再加两个工具即达阈值**。

### 8.2 分段实施

| 段 | 内容 | 交付判据 |
|---|---|---|
| **M5b-1 目录层** | 四张表 + `capability_pool.py` + 三种适配器（API/MCP 先留桩不算真接入）+ `rebuild_pool` | 单测：派生一致性（`user_clis` ↔ 池）、幂等 rebuild、版本号变更、向量 upsert；**CLI 侧行为与 M5a 完全一致** |
| **M5b-2 路由器** | `tool_router.py` + `_run_agent` 接入 + 阈值标定 + 缓存失效 | 加压探针（8~10 工具）≥ M5a 基线；token ≤ 60%；兜底三分支全绿；回归全绿 |
| **M5b-3 API 适配器** | `tool_api` + `api_tool_registry` + `run_api_tool` + 面板 + 纪律 | 安全矩阵（未声明路径 / 未声明方法 / 跨域重定向 / 参数逃逸 / 密钥不出现在上下文）全拒；只读调用真机成功 |
| **M5b-4 MCP 适配器** | `tool_mcp` + `mcp_registry` + `run_mcp_tool` + 面板 + `tools/list` 预勾选 | 白名单外工具调用被拒；只读工具真机成功；加压探针复跑不回退 |

> 节奏沿用 M4/M5a 纪律：**每段端到端验证通过再进下一段**；用户手动验证后才在 README 记「✅ 已落地」。

---

## 九、风险与预案

| # | 风险 | 预案 |
|---|---|---|
| 1 | **过度收窄**把对的工具藏掉（比全量更糟） | 未命中仍保留「名称+可执行名」一行；全都没过阈值 → 全量兜底；探针里加「只应命中第 4/5 个工具」的难例 |
| 2 | 阈值 τ 敏感、换模型就漂 | 阈值写死在配置 + 探针标定；改阈值必须重跑加压探针；把「通过率-阈值」曲线存档 |
| 3 | 中文短问句向量区分度差 | 混合检索（关键词字面 + RRF），不单靠向量 |
| 4 | bge 与 M3 抢显存 / 加载慢 | lazy 单例（M3 已有）、GPU 串行；加载失败 → 回退全量并降级为「只做关键词匹配」 |
| 5 | 池与来源表不一致（改了 CLI 忘了同步池） | 写入口同事务 upsert + `rebuild_pool()` 兜底 + 单测比对两份数据 |
| 6 | 每轮 embedding 增加延迟 | 快路径（≤4 工具跳过）；向量矩阵常驻内存；实测 P95 延迟写进 §7.4 |
| 7 | API 侧成为新的越权面（自造 URL / 密钥泄露 / 写操作） | §4.2 六条安全边界 + 安全矩阵测试；密钥只存变量名 |
| 8 | MCP server 声明恶意/危险工具 | `enabled_tools` 人工确认 + 服务端强制白名单 + 默认只勾只读 |
| 9 | 注入内容每轮变化影响提示词缓存 | 本项目为「动态 SystemMessage」既有形态（M2 已如此），无新增风险；保持区块名稳定以降低回归面 |
| 10 | 过早优化（工具就 3~4 个） | 触发条件（§8.1）不满足就不开工；M5a 的临时缓解足以支撑到 5 个工具 |

---

## 十、范围外（明确不做）

- ❌ **embedding 微调 / 在线学习**（Online-Optimized RAG 的进阶玩法，当前规模不需要）；
- ❌ **多工具并发编排 / 调用图规划**（主 Agent 单步调度已够用，属另一里程碑）；
- ❌ **自动推断 API/MCP 工具的「只读性」**（仍走「预勾选 + 人确认」，与 M4c 口径一致）；
- ❌ **安全闸门改动**（六道闸 / 第七道闸 / 只读闸 / 沙箱零改动）；
- ❌ **把 MCP 工具 schema 直接灌进模型 tool 列表**（决策 1 已否，避免破坏静态 agent 与缓存）；
- ❌ **跨账号共享向量集合**（每账号独立 `tool_route_{account_id}`，与 M3 隔离口径一致）。

---

## 十一、实施记录（M5b-1 / M5b-2 已落地）

### 11.1 M5b-1 · 统一能力目录

| 交付物 | 说明 |
|---|---|
| `tools/schema_personal.py` | `tool_capabilities`（池快照）+ `tool_pool_meta`（池版本）两表；`purge_account()` 账号清理助手（自省表结构，未来加表不用改） |
| `tools/capability_pool.py` | `CapabilityEntry` 契约 + `cli_entry()` 适配器 + `pool_entries()` 派生 + `sync_cli_entry()` 增量同步 + `rebuild_pool()` 幂等重建 + `sync_vectors()/ensure_vectors_fresh()/route_scores()` 向量层 |
| `tools/cli_registry.py` | `save_cli` / `delete_cli` / `set_state` 三处写入口挂钩（完全容错，池坏了不影响配置写入） |
| `tests/m5b_pool_e2e.py` | **62/62**（表结构 12 / 适配器 16 / 写入口联动 8 / 版本 2 / rebuild 幂等 4 / 向量 9 / 隔离 2 / 不污染 M5a 3 / 清理 2） |

**两条实施中确定的取舍**：

1. **派生而非迁移**：`pool_entries()` 直接调来源适配器，不读 `tool_capabilities`；
   后者只作快照/审计。好处是「改完 CLI 能力描述立刻生效」，不存在两表不同步的窗口。
2. **向量惰性同步**：`save_cli` 只落库 + 递增版本，**不同步向量**——因为向量同步会加载 bge。
   实测：改前保存一次配置要冻 **1~3 分钟**（首次加载模型），改后 **0.18s**；
   向量由 `ensure_vectors_fresh()` 在路由时刻按版本重建（版本对齐标记写在 `chroma_route/_pool_version.json`）。

**连带修复（根因级）**：新表带 `REFERENCES accounts(id)` + `PRAGMA foreign_keys = ON`，
让「删账号」这条路撞外键约束（M5a 测试清理直接报 `FOREIGN KEY constraint failed`）。
修法是加 `purge_account(account_id)`：自省 `sqlite_master`，凡带 `account_id` / `owner_id` 的表都清，
M5b-3/M5b-4 再加 `tool_api` / `tool_mcp` 时**无需再改**，三个测试的清理逻辑已改为调它。

### 11.2 M5b-2 · 工具检索路由

| 交付物 | 说明 |
|---|---|
| `tools/tool_router.py` | 快路径 / 混合检索 / 相对置信判定 / 三形态分层渲染 / 预算护栏 / 示例过滤 / 全链兜底；`decide()` 为纯函数（可单测） |
| `api/server.py` | `_run_agent` 改调 `route_tools(account_id, question)`，仍拼成同一段 `cli_brief` 注入（契约不变） |
| `tests/_m5b_route_calib.py` | 阈值标定脚本（8 工具异构池，输出 §3.1.1 的表） |
| `tests/m5b_router_e2e.py` | **34/34**（快路径 4 / 路由命中不猜错 / 反例回退 2 / 护栏 6 / 兜底 5 / RRF 单元 4 / 来源无关 3 / 接线 4 / 清理 2） |
| `tests/m5b_pressure_probe.py` | 加压真机探针（8 个真桩工具 + 8 正 2 反，判定「只看首调 + 会话真值配对」）；`SKIP_LLM=1` 自检 **6/6** |

**8 工具实测结果**：10 条问句中 **8 条走路由**（命中集都含正确工具，0 条猜错）、
**2 条无关问句全部回退全量**；注入量 1644 字 vs 全量 2499 字。

### 11.3 实施中发现并修掉的两个真 bug

| # | 现象 | 根因 | 修法 |
|---|---|---|---|
| 1 | `我这个月读了多久` 的向量 top1 是 booky，融合后却变成 sched | 词法全为 0 时，仍把「全是 0 的排序」当一路输入 RRF → 给排序里靠前（实为插入序）的工具加分，顶掉向量路正确的 top1 | `_rrf()` 剔除**无信号**的排序器；单测固化该场景 |
| 2 | 路由后示例区仍出现非命中工具的命令 | `_filter_examples` 按 `ln.startswith("- ")` 判断「这是示例行」，而示例行真实格式是 `用户问到「…」→ run_shell_command(command="…")`，**不以 `- ` 开头** → 过滤形同虚设 | 改为按 `run_shell_command(` / `command=` 判断；测试补「非命中示例已被滤掉」断言 |

### 11.4 回归（M5b-2 收尾，2026-09-16）

| 套件 | 结果 |
|---|---|
| `tests/m5b_pool_e2e.py` | 62/62 ✅ |
| `tests/m5b_router_e2e.py` | 34/34 ✅ |
| `tests/m4c_cli_e2e.py`（含真机问答） | **109/109** ✅ |
| `tests/m5_abilities_e2e.py` | **43/43** ✅（其中 1 条断言随接线更新：`_run_agent` 的注入改由 `route_tools` 产出，意图不变） |
| `tests/m5b_pressure_probe.py`（SKIP_LLM 自检） | 6/6 ✅ |

### 11.5 加压探针真机实测（2026-09-16，用户手动跑 + 单条复验）

`tests/m5b_pressure_probe.py`（8 工具 × 8 正例 + 2 反例，纯真机、只看首调）：

| 轮次 | 正例 | 反例 | 备注 |
|---|---|---|---|
| 首轮（原题面） | **7/8** | **2/2** | 失败项「那个视频讲了什么」= 题面悬空指代（下详） |
| 单条复验（自足题面 + 备选首调） | **1/1** ✅ | — | `M5B_ONLY=m5bp-vid` |

综合口径：**修正后 8/8 正例命中**（7 条首选首调 + 1 条备选首调）、反例 2/2；
8 条正例全部 `mode=routed`（说明该规模下路由器确实在工作，不是退化成全量），
命中的首调全部落在该工具的只读清单内。

**首轮唯一失败项的定性：「那个视频讲了什么」不是路由失败，是题面缺陷。**

明细（`data/m5b_pressure_result.json`）显示：该条 `mode=routed`、`hits` 第一个就是 `m5bp-vid`
（完整能力卡已正确给到目标工具），但模型**没调工具**，而是回了一句
「请问您指的是哪个视频？请提供视频链接 / BV号 / 标题」——
全新会话里「那个视频」**没有先行词**，模型选择澄清而不是瞎猜（这本身是合理行为）。

同类问题在 M5a 第二轮已出现过（4/5/7 三条探针写「这本书…」无指代对象），当时的结论同样是
「属题面歧义，不是纯路由失败」。修正方式一致：**题面写自足**。

**复验结果**：题面改为 `B站那个讲降噪耳机的视频讲了什么` 后，模型首调
`m5bp-vid search 降噪耳机` —— 正是正确第一步（`info` 需要视频 ID，所以先搜到视频再问详情，
沿用 M5a 的两步链口径），判定为**备选命中** ✅。

**本轮对探针夹具的三处改进**：

1. 题面自足化（点名到具体视频）；
2. 新增**备选首调**判定（`alts`）：需前置 ID 的命令，先 `search` 解析也是正确第一步。
   实现时踩了一个类型坑：argv 是 `list`、备选写成 `tuple`，
   `["search","x"][:2] in [("search",)]` **恒为 False**，会把备选命中误判成失败 → 比较时统一 `list(a)`；
3. 新增 `M5B_ONLY=<bin 或问句子串>` 单条重跑开关：迭代一条探针不必再等 20~40 分钟，
   结果单独落 `data/m5b_pressure_result_only.json`（不覆盖整轮结果）。

> **夹具局限（诚实标注）**：桩只返回固定 JSON（无真实内容），所以它只能验证
> 「**有没有调对的工具**」，验证不了「回答是否忠实于工具返回」——
> 复验时模型就基于空壳 JSON 编出了一段像模像样的视频综述（桩的产物，不是产品缺陷）。

### 11.6 尚未做的（M5b 剩余）

- **M5b-3（API 适配器）/ M5b-4（MCP 适配器）**：本次只做 CLI 部分，
  目录层与路由器已按「来源无关」实现并用混合来源条目单测（§7 用例 9），接新来源只需写适配器 + 网关壳。

---

## 附录 A · 默认参数

| 参数 | 默认 | 说明 |
|---|---|---|
| 快路径阈值 | 池内 ≤ 4 条 | 直接走 M5a 全量简报 |
| `Z_STRONG` | 2.0 | 向量 top1 的 z 分数达到此值才算「有明确目标」（8 工具实测标定） |
| `LEX_MIN` | 2 | top1 的词法命中 ≥ 此值即置信（更可靠的那一路） |
| `MAX_FULL_CARDS` | 3 | 完整卡张数（2 → 注入量降到 59%，见 §3.1.1） |
| `COMPACT_TOP_N` | 2 | 完整卡之后给几条紧凑行 |
| `RRF_K` | 60 | RRF 融合常数（与 M3 检索栈同口径） |
| 注入总预算 | 1500 字 | ≈750 token |
| 池内条目上限 | 50 条 | 暴力余弦即可，不建 ANN |
| 超时 / 响应上限 | 30s / 1MB | 与 CLI 闸门一致 |
| 向量集合 | `tool_route_{account_id}` | Chroma，账号隔离 |

## 附录 B · 接口签名草案

```python
# tools/capability_pool.py
def cli_entry(c: dict) -> CapabilityEntry: ...
def api_entry(a: dict) -> CapabilityEntry: ...
def mcp_entry(m: dict) -> CapabilityEntry: ...
def pool_entries(account_id: int) -> tuple[int, list[CapabilityEntry]]: ...
def bump_pool_version(account_id: int) -> None: ...
def rebuild_pool(account_id: int) -> int: ...            # 返回重建条数
def upsert_vectors(account_id: int, entries: list[CapabilityEntry]) -> None: ...

# tools/tool_router.py
def route_tools(account_id: int | None, question: str) -> RouteResult: ...
def _hybrid_scores(question: str, entries: list[CapabilityEntry]) -> list[tuple[str, float]]: ...
def _render(result_scores, ...) -> RouteResult: ...
```

## 附录 C · 与既有文档的关系

| 文档 | 关系 |
|---|---|
| `M5工具智能路由.md` | M5a 设计/实施/实测的权威记录；本文档**取代其 §四**（M5b 简版设计） |
| `M4技能与工具.md` 附录 A/B | CLI 沙箱与自定义 CLI 的安全口径来源，M5b 不改其任何闸门 |
| `M3RAG知识库搭建与使用.md` | 混合检索（BM25 + 向量 + RRF）与 collection 账号隔离的实现来源，M5b 直接复用 |
| `docs/v2.0/演示文档/演示文档.md` | 用户侧教程；M5b 落地后需补「多工具时的注入策略」一节（§8.5 现有降级说明将被检索路由取代） |

---

*本文档为 v2.0 M5b 工程设计书；开工前请逐条确认 §【决策记录】。*
