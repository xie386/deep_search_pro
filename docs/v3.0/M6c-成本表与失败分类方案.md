# M6c · 成本表与失败分类 方案

> **状态**：✅ **定稿**（2026-09-25 用户裁决 Q1~Q4，见 §零；无开放项）
> **对应规划书**：§4 S2 / §3 B5·B6 / §5 M6-3｜**依赖**：M4（`usage_events` 底座 + `wrap_model_call` 挂钩点，M4-D2 明确把模型级 token 留给本议题）
> **一句话**：把"花了多少"分成**两件不同的事** —— ① **token 账目（确定能拿到）**，② **金额（只有用户填了单价才折算，且明确是估算）**；顺带把散落在代码里的**外壳退出码**收敛成失败分类。
> **用户口径（逐字要点）**：**"项目只做算了多少 token，在定制助手模型配置那里加三个可选填处：输入价格(缓存命中)、输入价格(缓存未命中)、输出价格，如果用户填了，就可以输出用户的消费。"**

---

## 零、决策记录

### 0.1 用户裁决（2026-09-25）

| # | 议题 | **裁决** |
|---|---|---|
| **D1** | "成本"的定义 | **项目只统计 token**；`llm_providers` 加**三个可选填单价**（缓存命中输入 / 缓存未命中输入 / 输出）→ **用户填了才折算金额**，不填就只显示 token。**不内置任何厂商价格表**（同模型不同中转站价格不同，内置必然出错） |
| **D2** | 计价单位 | **固定「元 / 百万 tokens」**（少一个字段、厂商多按 1M 报价）→ 写进 UI 与列注释，**不加单位列** |
| **D3** | 上游未返回缓存拆分时的折算口径 | **按"未命中"折算** + UI 标注"该次调用未返回缓存明细，金额可能偏高"（**不按命中折算** —— 那会低估成本） |
| **D4** | 上游完全不返回 usage（部分中转站） | 记 `token_source='unavailable'`，**不折算、不猜**；UI 显示"—"并注明"N 次调用无用量数据" |
| **D5** | 失败分类是否并入 M6c | **并入**：把散落的退出码字符串**收敛成分类枚举 + 用户可见文案 + 可上报** |

### 0.2 助手已定（依据取证，非分叉）

| # | 结论 |
|---|---|
| **C1** | **token 采集必须"每次模型调用一条"**：现状只取最后一条 AIMessage 的 `total_tokens`（`api/server.py:298-305`）→ 一轮 7 次工具调用**只记最后一次**。改用 M4 定好的 `wrap_model_call` 中间件按调用记 |
| **C2** | **必须读 raw `response_metadata["token_usage"]`**，不能只读归一化 `usage_metadata`：langchain 只把 `prompt_tokens_details.cached_tokens` 映射成 `input_token_details.cache_read`（`langchain_openai/chat_models/base.py:4144-4171`），而 **DeepSeek 用的是顶层 `prompt_cache_hit_tokens` / `prompt_cache_miss_tokens`** → 归一化字段拿不到。raw usage 被原样保留（`base.py:1868-1873`），且项目的 `_create_chat_result` 只在 `super()` 后追加 `reasoning_content`、**不丢 response_metadata**（`agent/reasoning_model.py:53-70`）。**先例**：`reasoning_content` 就是这么兼容"原生字段 + `model_extra.reasoning`"两形态的 |
| **C3** | **账目按 provider 维度记**（`provider_id` / `model_name`），且**折算结果落库**：`llm_providers` 支持多配置与 `is_active` 切换，若只记"账号 + token"再按当前单价现算 → 用户换模型后**历史账目被重算（错账）** |
| **C4** | 金额一律标注为**估算**（"按你填的单价 × 我们数到的 token"），**不是账单**；工具/检索不算钱（那是额度，M4 只记次数） |
| **C5** | 失败分类**只收敛口径、不改行为**：看护逻辑（只对自起后端生效、绝不动用户服务）等既有铁律原样保留 |
| **C6** | 复用 M4 的 `usage_events` **一张底座表**加列（不为模型再开一张表），并在表上补 token 相关列 |

---

## 一、目标与验收判据

| # | 目标 | 验收判据（可测） |
|---|---|---|
| G1 | **token 不漏计** | 跑一轮"含 7 次工具调用"的对话 → `usage_events` 里 `kind='model'` 的行数 = **该轮真实模型调用次数**（不再只记最后一次） |
| G2 | **多形态 usage 都能解析** | 三组夹具（DeepSeek 形态 / OpenAI 形态 / 只有 prompt+completion）→ 缓存命中与未命中数解析正确（≥3 形态用例） |
| G3 | **缺缓存明细的口径** | 无缓存字段 → 全部计入"未命中"折算，记录 `cost_note='no_cache_detail'`，UI 显示该标注 |
| G4 | **无 usage 不编造** | 夹具"无 usage" → `token_source='unavailable'`、**不折算**、UI 显示"—"并统计"N 次调用无用量数据" |
| G5 | **未配置单价不编造金额** | 三个单价留空 → 只显示 token，金额显示"未配置单价"；**填 0 才显示 ¥0.00（免费）** —— 留空 ≠ 免费 |
| G6 | **换模型不篡改历史账** | 记 10 条（provider A）→ 改 A 的单价 → 旧 10 条金额**不变**；点"按当前单价重算"才变（并有"已重算"标记） |
| G7 | **失败分类收敛** | 现有全部退出码字符串都有对应枚举项；**静态断言：代码里不再有裸字符串退出码**（白名单机制，与"死路由不变量"同手法） |
| G8 | **可看** | 工作台成本卡：本月 tokens / 金额（或"未配置单价"）/ 外部检索次数（M4）；`GET /api/cost/summary?days=30` 按 provider·model 汇总 |
| G9 | **隔离** | 全部按 `account_id`；`purge_account()` 连带清理（自省表结构） |

---

## 二、现状与证据

| 事实 | 证据 |
|---|---|
| `llm_providers` 只有 `provider_name / model_name / base_url / api_key / is_active / created_at`，**无任何价格字段** | `tools/schema_personal.py:215` |
| token **只记轮级最后一条**（多工具轮漏计） | `api/server.py:298-305`：`for m in reversed(result["messages"]): um = getattr(m,"usage_metadata",None) ... break` |
| 轮级累计存在 | `conversations.token_usage`（`schema_personal.py:228`） |
| 归一化 `usage_metadata` 只认 `prompt_tokens_details.cached_tokens` | `langchain_openai/chat_models/base.py:4144-4171`（`cache_read` ← `cached_tokens`；`cache_creation` ← `cache_write_tokens`） |
| **raw usage 被原样保留** | `base.py:1868-1873`：`llm_output = {"token_usage": token_usage, ...}` → `AIMessage.response_metadata["token_usage"]` |
| 项目包装**不丢** response_metadata | `agent/reasoning_model.py:53-70`：`rtn = super()._create_chat_result(...)` 之后只**追加** `additional_kwargs["reasoning_content"]` |
| 历史调用**没存** raw usage（所以无法回溯核对） | 实测：`messages.additional_kwargs` 只有 `refusal` + `reasoning_content`（查了最近 3 条 assistant 记录） |
| `additional_kwargs`/`tool_calls` 已落库（结构可扩展） | `messages.additional_kwargs`（`schema_personal.py:242+`） |
| M4 已定：`usage_events` 一张底座表 + `kind='model'` 槽位 + `wrap_model_call` 挂钩点留给 M6c | `docs/v3.0/M4-计数器与装配收口方案.md` §5.1 / §4.3（D2） |
| 外壳失败语义**已有细粒度字符串但无枚举** | `front/desktop/app.py:355`（launcher 先退是正常现象）、`:383` `child_exit_port_taken:%s`、`:389` `child_exit:%s`、`:391` `child_exit_gone:%s`、`:415`（10048 bind）、`:456`（"启动后立即退出（退出码 %s）——不是等得不够久"） |

---

## 三、设计

### 3.1 Part A：token 账目（确定能拿到的那部分）

```
[采集] UsageCounterMiddleware.wrap_model_call（M4 已建中间件框架，本议题加 model 分支）
        每次模型调用 → 读 AIMessage.response_metadata["token_usage"]（raw）
                       + usage_metadata（归一化，作兜底）
        → 解析成 4 个数：input_tokens / output_tokens / cached_tokens / uncached_tokens
        → 写 usage_events（kind='model'，带 provider_id / model_name / token_source）
        ↑ 一次调用一条 → 多工具轮不再漏计（G1）

[兼容表 extract_usage(raw, normalized)]   —— 与 reasoning_content 的"两形态兼容"同手法
  形态① DeepSeek  ：prompt_tokens / completion_tokens / prompt_cache_hit_tokens / prompt_cache_miss_tokens
  形态② OpenAI   ：prompt_tokens / completion_tokens / prompt_tokens_details.cached_tokens
  形态③ 只有总量  ：有 prompt/completion、无缓存拆分 → cached=0，uncached=prompt，token_source='no_cache_detail'
  形态④ 无 usage ：token_source='unavailable'，四个数都不写（**不猜**）
  兜底：形态无法识别 → token_source='unknown_shape'（同样不折算，日志留原文前 200 字便于排查漂移）
```

### 3.2 Part A：金额折算（只有用户填了单价才算）

```
llm_providers 加三列（单位固定 元/百万 tokens；NULL = 未配置）
    price_in_cached    输入价（缓存命中）
    price_in_uncached  输入价（缓存未命中）
    price_out          输出价

cost = (cached_tokens/1e6) * price_in_cached
     + (uncached_tokens/1e6) * price_in_uncached
     + (output_tokens/1e6) * price_out

规则：
 ① 三个单价任一为 NULL → **不折算**（金额列留空 + UI"未配置单价"），token 照常显示（G5）
 ①-b **填 0 ≠ 留空**：填 0 表示"**明确免费**"（默认模型 `agnes-2.5-flash` 就是这种情况：标价 $0.03/M 输入、$0.15/M 输出，**实收 $0**）→ 折算结果为 **0.00 元**，UI 显示"¥0.00（免费）"；留空则显示"未配置单价"。两者语义不同，**不能互相替代**
 ② token_source='no_cache_detail' → 按 price_in_uncached 折算 + cost_note='no_cache_detail'（D3）
 ③ token_source='unavailable'/'unknown_shape' → **不折算**（D4/G4）
 ④ 折算结果**落库**（usage_events.cost + cost_note + price_snapshot），不每次现算（C3/G6）
 ⑤ 提供**显式**"按当前单价重算"（只对指定时间窗生效 + 写 recalculated_at），避免误改历史
```

### 3.3 Part A：出口

| 出口 | 内容 |
|---|---|
| `GET /api/usage?thread_id=`（M4 已定，本次加字段） | 本轮/本会话：调用次数 + tokens（输入/输出/命中/未命中）+ 金额（可折算时）+ **无用量调用次数** |
| `GET /api/cost/summary?days=30`（新增） | 按 `provider · model` 汇总 tokens 与金额、无用量次数、按天的趋势点（给工作台卡/折线） |
| 前端·定制助手·模型配置 | 三个**可选填**输入框（缓存命中 / 缓存未命中 / 输出，单位"元/百万 tokens"）+ 说明文案"**留空=只统计 token，不折算金额**" |
| 前端·工作台 `.stat-card` | 成本卡：本月 tokens ／ 金额（或"未配置单价"）／ 外部检索次数（M4）／ 无用量调用数（有小字提示） |
| 文案（写死在 UI 与文档各一处） | "金额为**估算值**（你填的单价 × 数到的 token），不含工具与检索成本，**不是账单**" |

### 3.4 Part B：失败分类（外壳退出码语义）

现状：字符串散落（§二 表最后一行），**无枚举**。本议题只做**收敛与呈现**，不动看护逻辑（C5）。

```
front/desktop/exit_codes.py（新；纯数据 + 一个 classify()，零依赖便于测试）
  CODE                      severity  zh_label（用户可见）            建议动作
  USER_QUIT                 info      你主动退出了应用                  —
  LAUNCHER_EXIT             info      （正常现象：venv 启动器先退出）    不作为失败（evidence: app.py:355）
  BACKEND_PORT_TAKEN        error     后端端口被占用（10048）           关掉占用端口的程序，或让它自动换端口重试
  BACKEND_START_FAILED      error     后端启动后立即退出               看 backend.log 末尾（依赖/配置问题）
  BACKEND_READY_TIMEOUT     warn      后端就绪超时（180s）              检查 .env 与网络，重试
  BACKEND_CRASHED           error     后端运行中崩溃                    已自动重启 N 次；连续失败则停止并提示
  BACKEND_RESTART_EXHAUSTED error     自动重启次数用尽                  需人工介入（附日志路径）
  WINDOW_CLOSE_UNEXPECTED   warn      窗口异常关闭                      已收进托盘 / 已退出（按托盘可用性）
  WEBVIEW_INIT_FAILED       error     内嵌浏览器初始化失败              检查 WebView2 运行时

classify(raw: str) -> ExitCode   # 把现有字符串映射到枚举：child_exit_port_taken:… → BACKEND_PORT_TAKEN 等
```

落地：
1. 现有调用点改为 `classify(...)` 后再打日志 → 日志形如 `[失败分类] BACKEND_PORT_TAKEN | 后端端口被占用（10048）| 建议：…`（**行为不变，只改口径**）；
2. **静态不变量**：代码里不再出现裸退出码字符串（白名单 + 断言，与 `frontend_setup_exports_check` 的"死路由"不变量同手法）；
3. **可上报**：`usage_events` 之外新增 `failure_events(account_id, code, detail, occurred_at)`（或复用 usage_events 加 `kind='failure'`？→ **选独立表**：失败不是用量，混表会让"成本卡"多出噪声）；
4. 与 M6b 衔接：崩溃/重启类失败正是那条**历史竞态**（venv launcher 双 pid）的观察面，枚举化后基线更好写。

---

## 四、设计选型与取舍

| 议题 | 备选 | 结论 |
|---|---|---|
| token 采集点 | A 保留"取最后一条 AIMessage" / **B `wrap_model_call` 中间件** / C 旁路低层 client 自己数 | ✅ **B**（C1）：一次调用一条、不依赖响应形态、与 M4 同框架 |
| usage 来源 | A 只读归一化 `usage_metadata` / **B raw `response_metadata["token_usage"]` + 归一化兜底** | ✅ **B**（C2）：DeepSeek 的缓存字段**只在 raw 里** |
| 金额折算时机 | A 每次查询现算 / **B 落库 + 显式重算** | ✅ **B**（C3/G6）：换模型不篡改历史账 |
| 表设计 | A 模型用量另开一张表 / **B 复用 M4 的 `usage_events` 加列** | ✅ **B**（C6）：一张底座表 = 一个 `/api/usage` 就能回答所有用量问题 |
| 失败记录 | A 混进 `usage_events` / **B 独立 `failure_events`** | ✅ **B**：成本卡不该被失败噪声污染 |
| 失败分类 | A 只加注释不改代码 / **B 枚举 + 文案 + 上报 + 静态不变量** | ✅ **B**（D5/G7）：否则明年还是散字符串 |

---

## 五、契约与数据模型（照抄可开工）

### 5.1 迁移（`tools/schema_personal.py`，沿用既有 `_ensure_*` 幂等风格）

```sql
-- llm_providers：三个可选填单价（单位固定：元 / 百万 tokens；NULL = 未配置）
ALTER TABLE llm_providers ADD COLUMN price_in_cached   REAL;
ALTER TABLE llm_providers ADD COLUMN price_in_uncached REAL;
ALTER TABLE llm_providers ADD COLUMN price_out         REAL;

-- usage_events（M4 已建）：补 token 与成本列（工具行留 NULL）
ALTER TABLE usage_events ADD COLUMN provider_id    INTEGER;
ALTER TABLE usage_events ADD COLUMN model_name     TEXT;
ALTER TABLE usage_events ADD COLUMN input_tokens   INTEGER;
ALTER TABLE usage_events ADD COLUMN output_tokens  INTEGER;
ALTER TABLE usage_events ADD COLUMN cached_tokens  INTEGER;
ALTER TABLE usage_events ADD COLUMN uncached_tokens INTEGER;
ALTER TABLE usage_events ADD COLUMN token_source   TEXT;   -- reported | no_cache_detail | unavailable | unknown_shape
ALTER TABLE usage_events ADD COLUMN cost           REAL;   -- NULL = 未折算（未配单价 / 无用量）
ALTER TABLE usage_events ADD COLUMN cost_note      TEXT;   -- no_cache_detail | no_price | unavailable | unknown_shape
ALTER TABLE usage_events ADD COLUMN price_snapshot TEXT;   -- 折算时用的 JSON 单价快照（可追溯）

-- 失败事件（独立表，不与用量混）
CREATE TABLE IF NOT EXISTS failure_events (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  account_id  INTEGER,
  code        TEXT NOT NULL,     -- 枚举名（exit_codes.py 中的 CODE）
  detail      TEXT,              -- 原始字符串/退出码/日志摘要
  occurred_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_failure_time ON failure_events(occurred_at DESC);
```

### 5.2 采集契约（`agent/usage_counter.py` 扩展 + `agent/token_usage.py` 新增）

```python
# agent/token_usage.py（纯函数，便于单测；不 import api.*）
def extract_usage(raw: dict | None, normalized: dict | None) -> dict:
    """→ {"input":int,"output":int,"cached":int,"uncached":int,"token_source":str}"""

def compute_cost(tok: dict, prices: dict | None) -> tuple[float | None, str | None, str | None]:
    """→ (cost, cost_note, price_snapshot_json)；prices 为 None/含 NULL → (None,'no_price',...)"""
```

`wrap_model_call(self, request, handler)`：调用 `handler(request)` → 从返回的 `AIMessage` 取 `response_metadata` / `usage_metadata` → `extract_usage` → 查该账号当前 provider 的单价 → `compute_cost` → 一条 `usage_events(kind='model')`；**异常路径不写 token 行但写 `ok=0`**（与 M4 工具行同口径）。

### 5.3 接口

| 端点 | 用途 |
|---|---|
| `GET /api/usage?thread_id=`（扩展） | 加 `tokens:{input,output,cached,uncached}`、`cost`、`cost_note`、`unavailable_calls` |
| `GET /api/cost/summary?days=30`（新增） | 按 provider·model 汇总：调用数 / tokens / 金额 / 无用量次数 / 日趋势 |
| `POST /api/cost/recalc`（新增） | 显式"按当前单价重算"：入参 `{days}`，写 `recalculated_at`（不改 token） |
| `GET/POST /api/llm/providers`（扩展） | 三个单价字段的读写（**留空 = 不折算**，前端要有明确说明） |
| `GET /api/failures?days=7`（新增） | 失败分类汇总（给 M6b 基线与排障） |

### 5.4 前端

| 位置 | 变化 |
|---|---|
| 定制助手·模型配置 | 三个可选填输入框（"输入价(缓存命中)/输入价(缓存未命中)/输出价"，单位**元/百万 tokens**）+ 说明"留空=只统计 token" |
| 工作台 `.stat-card` | 成本卡（本月 tokens / 金额或"未配置单价" / 外部检索次数 / 无用量调用数） |
| 会话侧栏/聊天页 | 本轮 tokens（命中/未命中/输出）与金额（若可折算）小字显示 |
| 文案 | "金额为估算值，不是账单；不含工具与检索成本"（**写死一处**，别在多处重复） |

---

## 六、实施步骤（每步独立可验证）

| 步 | 做什么 | 验证 |
|---|---|---|
| M6c-1 | 迁移：`llm_providers` 三列 + `usage_events` 十列 + `failure_events` 表 | `tests/test_cost_schema.py`：幂等、列齐、默认 NULL、清理连带（≥8 项） |
| M6c-2 | `agent/token_usage.py`：`extract_usage`（四形态）+ `compute_cost`（含 no_price/no_cache_detail 分支） | `tests/test_token_usage_extract.py`（≥12 项）：DeepSeek/OpenAI/仅总量/无 usage/形态未知/异常输入 |
| M6c-3 | `UsageCounterMiddleware.wrap_model_call` 接入（每次调用一条）+ provider 维度落库 | `tests/test_model_usage.py`（≥10 项）：多工具轮调用数正确（反证：≠1 条）、provider_id 正确、异常行 ok=0 |
| M6c-4 | 折算落库 + "按当前单价重算"（G6 反证：改价不改历史） | `tests/test_cost_calc.py`（≥10 项）：未配价不折算、缺缓存按未命中、快照可追溯、重算只影响指定窗口 |
| M6c-5 | 接口：`/api/usage` 扩展 + `/api/cost/summary` + `/api/cost/recalc` + provider 单价读写 | `tests/test_cost_api.py`（≥8 项）：汇总正确、越权 403、空数据、未配价文案字段 |
| M6c-6 | **Part B 失败分类**：`exit_codes.py`（枚举 + classify + 文案）+ 调用点改造 + 静态不变量 | `tests/test_failure_kinds.py`（≥10 项）：现有全部字符串可分类、未知串降级为 `UNKNOWN` 并留原文、**静态断言无裸退出码**、`failure_events` 落库 |
| M6c-7 | 前端：三个单价输入 + 成本卡 + 本轮小字 + 文案 | 前端 harness（抽取式，≥10 项）+ `frontend_setup_exports_check` |
| M6c-8 | 真机抽样：跑一轮带工具调用的真实会话 → 核对 `usage_events` 与实际调用数一致，成本卡出数 | 数字（调用数/tokens/金额或"未配置"）记进 v3.0 记录 |

---

## 七、测试与回归清单

**新增**：`test_cost_schema.py`(≥8) · `test_token_usage_extract.py`(≥12) · `test_model_usage.py`(≥10) · `test_cost_calc.py`(≥10) · `test_cost_api.py`(≥8) · `test_failure_kinds.py`(≥10) · 前端 harness(≥10)。

**必须重跑**（动 `usage_events` 表结构 + 模型层中间件，属"改核心链路必查同类依赖"）：

| 类别 | 清单 |
|---|---|
| M4 全部用例 | `test_usage_counter.py`(≥12) · `test_usage_api.py`(≥8) · `test_usage_schema.py`(≥6)（**加列后必须仍绿**） |
| 模型层 | 会话相关 pytest（`test_answer_text.py`(15) · `test_assembly.py`(5) 等）· `m4a_skills_e2e`(30) |
| 外壳 | `desktop_shell_e2e`(107/114) · `desktop_window_e2e`(46)（Part B 改了外壳日志口径） |
| 入口 | `cli_dspro_e2e`(45)（CLI 复用同一中间件）· 桌面导出 `desktop_export_logic.js`(24) |
| 前端 | m4a(56)/m5c(42)/tutorial(31)/weather(51) + `frontend_setup_exports_check`(10) |

**纪律**：全部离线（**用夹具 JSON 模拟三种 usage 形态，不发真实请求**）；不碰 Tavily；真机抽样用测试账号、跑完出快照（避免动用户数据）；**真机抽样会产生少量真实 token 消耗**（一次会话），提前说明。

---

## 八、风险、回退与遗留

| 风险 | 缓解 |
|---|---|
| **金额被误当账单** | C4：UI 与小字文案统一写"估算值，不是账单"；接口字段名用 `cost_estimated` 系语义；文档单处定义 |
| raw usage 形态漂移（厂商改字段） | `extract_usage` 四形态 + `unknown_shape` 兜底（**不折算**）+ 日志留原文前 200 字；夹具覆盖已知两形态 |
| 换模型/改价后历史账被改写 | C3：折算落库 + 单价快照 + 只有显式重算才变（G6 反证用例） |
| 中间件加 `wrap_model_call` 影响调用链 | 与 M4 同一中间件、同一注册点；失败时**绝不影响模型调用本身**（try/except 包裹，只丢记录不丢回答） |
| 失败分类改造外壳日志 | C5：只改日志口径与呈现，**看护/收进托盘/退出判据一律不动**；外壳用例全跑 |
| 多账号/多 provider 汇总口径混乱 | 汇总一律按 `(provider_id, model_name, 日)` 分组；跨账号不做（个人应用，隔离优先） |

**回退**：全部是加列/加表/加端点；`ZX_MODEL_USAGE=0` 关掉模型用量采集；`ZX_FAILURE_CODES=0` 回到裸字符串日志；三处单价可清空（回到"只统计 token"）。**不涉及数据迁移与破坏性改动。**

**遗留（本版不做）**：真实账单对账（不接厂商账单 API）；把 Tavily 额度折算成钱（额度 ≠ 钱，M4 只记次数）；按工具的成本归因（工具不花 token，不产生费用）；跨账号汇总；模型单价自动同步（正是 D1 否掉的方向）。

---

## 九、开放项

**无 —— 本文件为完全定稿**（Q1~Q4 已裁决：D2 固定元/百万 tokens · D3 按未命中折算并标注 · D4 记 unavailable 不折算 · D5 失败分类并入）。

> 落地过程中若出现新的分叉或不确定点，按项目纪律**暂停询问用户**，不擅自决定。
