# M6c · 成本表与失败分类 实现记录

> **对应方案**：`M6c-成本表与失败分类方案.md`（✅ 定稿，零开放项）｜**依赖**：M4（`usage_events` 底座 + `wrap_model_call` 挂钩点）
> **开工决策（2026-09-29 用户拍板）**：① **M6a（桌面版体验与打包）整块不做**（A8 窗口记忆 / A9 拖文件 / A10 热键 / A11 打包 全部砍掉）；② **M6 验收判据改写**为「陌生人按 README 快速开始走**源码路径**，30 分钟内看到第一份周报」；③ **直接开 M6c-1 → M6c-8**，M6b（探针基线/路由基准）等 2026-10-01 Tavily 额度重置后另起。

---

## 一、九步状态

| 步 | 内容 | 状态 | 验证 |
|---|---|---|---|
| **M6c-1** | 迁移：`llm_providers` +3 单价列 · `usage_events` +10 token/成本列 · `failure_events` 新表 +2 索引 | ✅ 完成 | `tests/unit/test_cost_schema.py` **11 passed** |
| **M6c-2** | `agent/token_usage.py`：`extract_usage` 四形态 + `compute_cost` | ✅ 完成 | `tests/unit/test_token_usage_extract.py` **21 passed**（要求 ≥12） |
| **M6c-3** | `wrap_model_call` / `awrap_model_call` 接入（每次调用一条）+ provider 维度落库 | ✅ 完成 | `tests/unit/test_model_usage.py` **15 passed**（要求 ≥10，含反证「7 次调用 = 7 行」） |
| **M6c-4** | 折算落库 + 显式「按当前单价重算」（G6 反证：改价不改历史） | ✅ 完成 | `tests/unit/test_cost_calc.py` **12 passed**（要求 ≥10，含 G6 反证「改价后旧 10 条一动不动」） |
| **M6c-5** | 接口：`/api/usage` 扩展 + `/api/cost/summary` + `/api/cost/recalc` + provider 单价读写 | ✅ 完成 | `tests/unit/test_cost_api.py` **14 passed**（要求 ≥8，含越权 403/404、未配价文案、单价两条分支） |
| **M6c-6** | Part B 失败分类：`exit_codes.py` 枚举 + `classify()` + 外壳口径 + `failure_events` 落库 + 静态不变量 + `GET /api/failures` | ✅ 完成 | `tests/unit/test_failure_kinds.py` **12 passed**（要求 ≥10，含静态不变量「枚举名不在外壳里当字面量」）；桌面静态契约 harness **24/24** |
| **M6c-7** | 前端：模型配置三处单价 + 工作台成本卡 + 本轮 token 小字 + 估算文案（单处） | ✅ 完成 | `tests/harness/m6c_cost_frontend.js` **24 passed**（含真跑折算函数：留空≠0、未配价≠¥0.00）；全量 harness 10/10 |
| **M6c-8** | 真机抽样：一轮带工具调用的真实会话 → 核对账目与成本出口 | ✅ 完成 | **一轮 10 次模型调用 = 10 条 `kind='model'`**（旧实现只会记 1 条）；进 137209 / 出 7190 tokens；命中 103936 / 未命中 33273；金额=未配置单价（G5 ✓） |

## 二、M6c-1 关键落点

- **迁移代码**：`tools/schema_personal.py`
  - 新列**同时**加在 CREATE TABLE（新库路径）与顶层 `try` 的幂等 ALTER（老库路径）——本项目 M4/M5 各踩过一次"只改一处"✗，所以 `test_cost_schema.py` 里有一条**全新库**用例专门盯它（用 `monkeypatch` 把 `DB_PATH` 指到临时库跑 `ensure_tables()`）；
  - 迁移块放在**函数体顶层**（M5-1 的教训：嵌进别人的 `if/except` 就永不执行、还不报错）。
- **数据安全**：迁移前用 `sqlite3` 的**在线备份 API** 存了 `docs/v3.0/backups/personal_before_M6c_20260929.db`（2.3MB；普通文件复制在 WAL 下可能拷到半截）；迁移后实测**行数 69 = 69**、**幂等三连**无副作用。
- **隔离**：`failure_events` 带 `account_id REFERENCES accounts(id)` → `purge_account()` **自省 sqlite_master**，新表自动被连带清理（G9 无需改函数，但有用例钉住 ✓）。
- **语义细节**：单价 `NULL` = 未配置（不折算）／`0` = **明确免费**（折算成 ¥0.00）—— 两者不同，用例 `test_zero_price_is_distinct_from_null` 钉住。

## 三、M6c-2 关键落点

- **模块**：`agent/token_usage.py`（**零依赖，只 import json** —— 有用例钉住"agent 层不 import api"）。
- **raw 优先于归一化（C2）**：`extract_usage(raw, normalized)` 先读 raw `response_metadata["token_usage"]`，
  只有 raw 缺席才用归一化 `usage_metadata` 兜底 —— 因为 **DeepSeek 的 `prompt_cache_hit_tokens` /
  `prompt_cache_miss_tokens` 只在 raw 里**，LangChain 只映射 OpenAI 系的 `prompt_tokens_details.cached_tokens`。
- **四形态**：① DeepSeek 顶层 hit/miss（只给 hit 时用「总量 − 命中」补 miss）② OpenAI `details.cached_tokens`
  （超量时钳到总量）③ 只有总量 → 全记未命中 + `no_cache_detail`（**D3：宁高不低**）④ 无 usage → `unavailable`，
  **四个数都不写（不是 0）**；形态不认识 → `unknown_shape` + 留原文前 200 字。
- **折算**：三处单价**任一为 NULL/缺失** → `no_price` 不折算（**留空 ≠ 免费**）；三处填 **0** → 明确免费，折算 **0.0**；
  `no_cache_detail` → 按未命中价 + note 标注；`unavailable`/`unknown_shape` → 不折算（note 即 token_source）；
  成功折算写 `price_snapshot`（单价 + 用量 JSON，可追溯）；金额 `round(…, 6)`，纯函数不掺时间/环境。

## 四、M6c-3 关键落点

- **注册点零改动**：`UsageCounterMiddleware` 在 **6 处**注册（主图 server ×2 · 4 个子 Agent · 周报 digest）——
  用的是**同一个类**，所以「加两个 `wrap_model_call` / `awrap_model_call` 方法」即**全覆盖**，一处都不用再接线。
- **接口签名不靠猜**：动手前先 `inspect.signature` 核了框架真实契约 ——
  `wrap_model_call(request: ModelRequest, handler) -> ModelResponse`，而 `ModelResponse.result: list[BaseMessage]`。
  `_ai_message()` 对三种形态宽容（ModelResponse / 单条 AIMessage / 裸 list），挑「带 usage 元数据的最后一条」。
- **provider 维度（C3）**：依赖倒置 `set_provider_resolver(fn)`（与 M4 的 `set_context_reader` 同手法，agent 层仍不 import api）；
  api 层 `_usage_provider()` 取 `is_active DESC, id DESC` 第一条 = 用户当前选中的模型配置；
  解析器自身抛错只丢 provider 维度，**不丢记账**。
- **不编造**：调用失败 → `ok=0` + `token_source='unavailable'`、四个数留 NULL（**不写 0**）；
  `response_metadata` 缺席同理；同时**异常照常向上抛**（模型错误绝不能被计数器吞掉）。
- **本步只落 token**，`cost` 列留 NULL —— 折算与快照按方案放进 M6c-4，保持「每步独立可验证」。

## 五、M6c-4 关键落点

- **采集时落库**：`record_model` 写行时就调 `compute_cost` 落 `cost` / `cost_note` / `price_snapshot` ——
  金额**存下来**而不是查询时现算，这就是 G6（换模型/改价不篡改历史账）的实现方式。
- **显式重算** `recalc_costs(account_id, days)`：只动「窗口内 + `kind='model'` + 当前取得到单价」的行，
  写 `recalculated_at` 留痕，返回 `{scanned, updated, skipped_no_price, skipped_no_tokens}`；
  窗口外、工具行、无用量行**一律不碰**（连快照都不动）。
- **取价规则**：按**行上记的 `provider_id`** 找当前单价；该配置已被删除则退回「账号当前生效」那套（不静默跳过）。
- **同一套折算口径**：重算复用 `compute_cost`，只把库里的行还原成 `extract_usage` 的产物形状（`_usage_of_row`）——
  避免「采集一套算法、重算另一套」这种最隐蔽的错账。
- **同 cursor 陷阱**：重算先 `list()` 物化外层结果集（循环内还要用同一个 cursor 查 provider）——
  本项目第三次遇到，已固化成习惯。

## 六、M6c-5 关键落点

- **`/api/usage` 扩展**：`this_turn` / `this_session` 各自带上 `model_calls` · `tokens{input,output,cached,uncached}` ·
  `cost` · `cost_note` · `unavailable_calls`（方案 §5.3 的四个新字段），顶层给 `estimated_cost` 与 `cost_note_text` 文案。
  工具行**不进**模型账（它们不花 token）。
- **`api/cost_api.py`（新）**：
  · `cost_summary(token, days)` —— 按 `provider · model` 汇总 + **按天趋势** + `total`，带「估算值/不是账单」声明；
  · `recalc(token, days)` —— 直接复用 M6c-4 的 `recalc_costs`（同一套口径），非法 `days` → 400。
- **provider 三处单价读写**：请求模型加三字段；`_norm_prices` 负数 → 400、**0 合法（明确免费）**；
  INSERT 与 `llm_provider_update` 的**两条 SQL 分支**都要带单价；
  `_normalize_provider` 的**出参**也要带上 —— 只改表不改出参 = 前端永远拿不到 ✗。
- **隔离**：全部走 `_require_account_id`；会话越权 403、别人的 provider id 404（用例钉住）。

## 七、M6c-6 关键落点

- **`front/desktop/exit_codes.py`（新）**：9 个码 + `UNKNOWN` 兜底，每个码带 `(severity, 中文, 建议动作)`；
  `classify(raw)` 认**现有字符串**（`child_exit_port_taken:…` → `BACKEND_PORT_TAKEN` 等）与**码名本身**；
  `describe(raw, detail)` 输出一行人话 `CODE | 中文 | 建议：…`（日志与界面共用一句，避免两处口径）。
  **零依赖**（只有 `re`）—— 外壳在后端还没起来时就要用它。
- **落库放 `tools/failure_log.py`（新）**：外壳 `app.py` **一 import 就把整个桌面应用拉起来**（还会装崩溃日志器），
  根本没法单测 ✗ → 所以"记一条 / 读汇总"放这里，外壳只调用。`account_id` **可空**（登录前失败无账号可归属）。
- **外壳只加口径、不动行为**（C5）：新增 `_log_failure(raw, detail)`（打分类行 + 尽力记库），
  在「后端启动后立即退出」与「等待超时」两条路径各调用一次；**原有那几行人话日志、看护逻辑、退出判据一字未改**。
- **出口** `GET /api/failures?days=7`：按码计数 + 最近原文 + **码表文案**（前端不必内置一份）；
  可见范围 = 自己的 + 系统级（`account_id IS NULL`），跨账号互相不可见。
- **静态不变量**（G7）：AST 扫外壳源码，**枚举名不许当字面量写死**；同时检查失败路径确实经过 `_log_failure`、
  且看护关键字（`_OWN_BACKEND` / `stop_backend` / 「只关自己起的服务」）原样保留。

## 八、M6c-7 / M6c-8 关键落点与真机数字

### 前端（M6c-7）
- **模型配置**：每套配置下一行三个**可选填**单价（单位写在界面上：元/百万 tokens），
  文案明确「留空 = 只统计 token，不折算金额；填 0 = 明确免费」；新增/保存都把三处单价带进 payload，
  且**空串转 null**（后端是 `float | None`，收不了 `''` ✗）。
- **工作台成本卡**：`c7` 本月 tokens（进/出）· `c8` 本月估算金额（未配价时显示**「未配置单价」**而不是 ¥0.00）；
  明细面板给 调用数 / 命中-未命中 / 无用量调用 / 外部检索（本会话），并挂 **「按当前单价重算」** 按钮（接 M6c-5）。
- **会话用量条**：补「模型 N 次 · 进 x/出 y · 命中 a · 金额（或未配置单价）」，无用量调用显式标注。
- **文案单处**：`COST_NOTE` 常量一份（harness 用"全文件只出现一次"钉住，防两处口径漂移）。
- harness 不只静态断言：`numOrNull` / `costTokens` / `costAmountText` 是**抽真代码真跑** ——
  「空串 → null（不折算）」「0 → ¥0.0000（免费）」「null → 未配置单价」三条语义一旦写错就是错账。

### 真机数字（M6c-8，账号「尼古喵喵」，8124）
| 指标 | 实测 |
|---|---|
| **一轮的模型调用** | **10 次** → `usage_events` 里 **10 条 `kind='model'`** ✓（M4 旧实现只会在轮末记 **1** 条 ✗） |
| tokens | 进 **137209** / 出 **7190**；缓存命中 **103936** / 未命中 **33273** |
| 金额 | **未配置单价**（cost_note=`no_price`）—— 当前生效配置「Deepseek」没填单价 → **G5 在真机上成立**：token 照数、金额不编 ✗ |
| 形态覆盖 | 既有 `reported`（命中/未命中都拿到）也有 `no_cache_detail`（上游没给拆分）→ G2/G3 **在野外被覆盖** |
| 工具行 | `internet_search` 4 · `query_kb` 6 · `run_shell_command` 2 · `list_agent_docs` 1 · `task` 1 · `write_file` 1 |

- **为什么是 10 而不是"会话里 7 条 assistant 消息"**：本轮触发过 `task` 子 Agent —— 子 Agent 的模型调用
  **不进主会话的消息表**，但**必须计入账目**。差的 3 条正是 M4 时代会**整批漏掉**的那部分，
  所以本步的价值不只是"数字变大"，而是把子 Agent 的消耗第一次算进了账 ✓。

## 九、踩坑（M6c-1）

| 坑 | 现象 | 处置 |
|---|---|---|
| M4 老用例写死了列清单 | `test_usage_schema.py::test_create_is_idempotent_and_complete` 变红（`COLS` 精确相等） | 按方案 §七「加列后必须仍绿」的**本意**同步更新 `COLS`（该用例的价值就是钉死列清单），全量回到 **438 passed** |
| 中文串里嵌 ASCII 双引号 | 写脚本当场 `SyntaxError` | 统一改用「」；写完必跑 `py_compile`/pytest |

## 十、踩坑（M6c-2）

| 坑 | 现象 | 处置 |
|---|---|---|
| **脏数据别当"只有总量"** | `prompt_tokens: -5`（脏）经 `_i` 变 None 后，会掉进形态③ → 未命中按 0 算 → **成本被算漏** ✗ | 加"字段在但值不可用 → `unknown_shape`（不猜、不折算）"；用例 `test_dirty_inputs_do_not_raise` 钉住 |
| **不变量用例被 docstring 骗了** | 用 `"import api" not in src` 检查"agent 层不 import api" ✗ —— 模块 docstring 里恰好写着那句"不 import api"，于是断言失败 | 改用 **AST 解析**取真实 import（注释/文档串骗不了 AST），同时断言"只 import json" |
| 测试里写废话断言 | 手滑写出 `assert u["input"] is None and u["cost"] if False else True`（恒真、还引用了不存在的键 ✗） | 改成真断言（四个数都不写）；**写完必跑**，这次是通读时自查出来的 |

## 十一、踩坑（M6c-3）

| 坑 | 现象 | 处置 |
|---|---|---|
| **夹具照着「我以为」的形状写** ✗ | 把 `model_name` 塞进 `token_usage` 里、把工具结果当成裸字符串 → 两条用例失败，看着像产品 bug，其实是夹具不真实 | 去**框架源码**核对真实形状（`langchain_openai/chat_models/base.py:1399` 与 `:1871` 都是 `{token_usage: …, model_name: …}` **同级兄弟**）；夹具改成带 `.content` 的消息对象 |
| 手算期望值错 | `「结果」 * 10` 断言成 30 字符（实际 20） | 改成 20 并写明算式；这类「数字对不上」先怀疑自己算错 |
| 中文串里混 ASCII 双引号（惯犯） | 写落档脚本时 `SyntaxError` 连中两次 | 表格正文统一「」；多行文本用三引号块，不把换行与引号转义混在一行里 |

## 十二、踩坑（M6c-4）

| 坑 | 现象 | 处置 |
|---|---|---|
| **又写出「恒真」的废话断言** ✗ | 两处写成 `assert … if False else True`（恒真、还引用了不存在的键）—— 恒真断言比没有断言更坏：它给的是「测过了」的错觉 | 改成真断言；定下规矩：**断言里不许出现条件表达式**，写完扫一遍 `if False else` / `or True` |
| 落档脚本锚点脆断 | 用「逐字原文」当锚点，但第一版写的行与文件里已有的行不一致 → `AssertionError: 0` | 长文档改行统一用 `re.subn(r'^\| M6c-4 \|.*$', …, flags=re.M)` 按行模式定位；节标题重编号**降序**做，避免连锁撞名 |

## 十三、踩坑（M6c-5）

| 坑 | 现象 | 处置 |
|---|---|---|
| **聚合查询带前导列，取数忘了偏移** ✗（真 bug） | `by_provider` = `(provider_id, model_name, <聚合列…>)`、`daily` = `(date, <聚合列…>)`，我却按"聚合列从 0 开始"取 → `int('deepseek-chat')` 当场炸 | `_row_to_block(row, off)` 加偏移参数；by_provider 传 2、daily 传 1。**是用例当场抓出来的** ✓ |
| 测试 helper 里 token 硬编码前缀 ✗ | `mk_provider()` 写死 `tkca%d`，换个账号 tag 就 401 —— 看着像"越权检查通过"，其实是我自己拼错 token | helper 改成**按调用方传 token**；这类"环境/名字拼错"的失败要先怀疑自己 |
| **`str.replace` 锚点不匹配时静默不生效** ✗（惯犯） | 改测试时一条替换没匹配上、也不报错 → 后一条改动用到了未定义变量，`NameError` 才暴露 | 定规矩：**每次 `replace` 都 `assert count == 1`**（改文档时我已经这么做，改代码/测试时又忘了） |

## 十四、踩坑（M6c-6）

| 坑 | 现象 | 处置 |
|---|---|---|
| **静态不变量当场抓到我自己** ✓✗ | 我在外壳里写了 `code = code or "UNKNOWN"` —— 枚举名字面量，正好踩中本步要立的 G7 不变量 | 外壳不留枚举字面量，兜底交给 `failure_log.record`；这正说明"不变量比自觉可靠" |
| 未知串**丢了原文** ✗（真缺口） | `describe` 只拼 `detail`，没带 `raw` → `UNKNOWN` 成了"什么都没说"，无法排查漂移 | 方案要求"降级 UNKNOWN **并留原文**" → `describe` 在 UNKNOWN 时追加 `| 原文：…`（截 200 字） |
| 用例之间**互相污染** ✗ | 系统级（`account_id IS NULL`）的行按设计对谁都可见，导致"窗口内应为 0"的断言被别的用例的数据打破 | 断言只针对**本用例自己造的码**（`{g['code']: count}`）而不是 `total`；这也是在提醒：`total` 天然含系统级 |
| 又写 `if False else` ✗（惯犯第 2 次） | `failure_log.summary` 里写了 `where = "…" if False else "…"` | 定规矩已写进上一节；这次是**写完扫一遍**抓出来的 |

## 十五、遗留（本版不做，沿用方案 §八）

真实账单对账 · Tavily 额度折算成钱 · 按工具的成本归因 · 跨账号汇总 · 模型单价自动同步。

## 十六、待跑清单（本会话未跑，需真机执行）

| # | 项 | 原因 |
|---|---|---|
| 1 | `tests/live/desktop_shell_e2e.py`（107 项）· `tests/live/desktop_window_e2e.py`（46 项） | 它们会**真的拉起桌面应用**（pywebview + WebView2）—— 本会话只跑了桌面**静态契约** harness（`desktop_export_logic.js` 24/24 ✓）。方案 §七 要求外壳改动后全跑，请在真机双击 `desktop.cmd` 侧一并执行 |
| 2 | M6c-6 的日志观感 | `[失败分类] …` 那行要在**真发生失败**时才看得到（如占用 8123 后再启动一次） |

## 十七、踩坑（M6c-7 / M6c-8）

| 坑 | 现象 | 处置 |
|---|---|---|
| **接口形状靠猜** ✗ | 抽样脚本按 JSON body 打 `/api/chat` → **422**；实际它是 `question/token/thread_id` **query 参数** | 读端点签名再写请求（这条和 M6c-3 的"夹具形状"是同一类毛病：**不确认就下手**） |
| 交叉核对写得太天真 ✗ | 我用"assistant 消息数 ≈ 模型调用次数"当判据 → 真机给出 10 vs 7 被判"不一致" | 子 Agent 的模型调用**不在主会话消息里**，但**必须入账** → 改成"**≥ 主会话轮数**"并写清差值的来源 |
| **Tavily 被触发了 4 次** ✗ | 我的提问只问知识库，Agent 自己补了网搜（额度纪律：能不用就不用） | 如实记下：本轮消耗 4 次外部检索。后续抽样可加"禁止联网"的措辞或直接用 `STUB_WEBSEARCH` |
| 观察到 `write_file` 工具行 | 本轮出现 `write_file`（kind=internal）。但 M4 记录里写过"去除 deepagents 内置 filesystem 工具，只保留项目自定义的" | **待确认**：这是项目自定义工具，还是内置 filesystem 又被挂回来了？（不影响 M6c 记账正确性） |

## 十八、方案偏差

| # | 位置 | 偏差 | 原因与处置 |
|---|---|---|---|
| 1 | §5.1 迁移 DDL | 漏了 `usage_events.recalculated_at` | §3.2 ⑤ 明确要求「写 `recalculated_at`」，但 §5.1 的建表语句里没有这一列 → **补列**（CREATE 与幂等 ALTER 两路都加）。纯 additive、无破坏性，故未打断流程，记此备查 |
| 2 | §六 M6c-4 描述 | 只写「折算落库 + 重算」，未写重算的取价规则 | 落地定为：按行的 `provider_id` 取价、该配置已删则退回「账号当前生效」那套（见上方关键落点） |
| 3 | `failure_events.account_id` 约束 | M6c-1 建表时写成了 `NOT NULL`，方案 §5.1 原文是**可空** | 外壳失败（端口被占/后端崩溃）发生在**登录之前**，没有账号可归属 → 必须放开为可空。SQLite 改不了列的 NOT NULL，故用**守卫式重建**（仅当表为空才 DROP+CREATE，有数据宁可留旧约束）；已实测可写 NULL ✓，属于"实现偏离方案、由后续步骤暴露"的偏差 |

## 十九、实机测试期发现并修复的 bug（2026-09-30）

### 19.1 自定义模型配置的密钥被掩码串污染 → 上游 401

| 项 | 内容 |
|---|---|
| **现象** | 用「自定义配置」的模型对话 → `AuthenticationError: 401 … Your api key: ****f98f is invalid`；而**同一条** model+key 放 `.env` 能用 |
| **根因** | `api/customize.py: get_active_provider()` 对所有调用方都套 `_normalize_provider()`，而后者内部 `api_key=_mask_key(...)` ✗ —— 造模型时把**掩码串**当密钥发给了上游 |
| **为什么难查** | `_mask_key` **保留尾部 4 位** → 报错里 `****f98f` 看着就像真 key；掩码串长度与真 key 相同、字符集干净 → 先被误判成"输入框截断"（已排除：无 `maxlength`、后端无切片） |
| **定位方式** | **用户 A/B 实测**：同 key 放 `.env` 能用、放自定义配置 401 → 断定问题在本模块（我的第一版怀疑"模型名不对"是**错的**，`deepseek-flash` 确为官方模型名） |
| **修复** | `_normalize_provider(p, mask=True)` 加开关（默认掩码 = 回显行为不变）；`get_active_provider` 传 `mask=False` 取**原文**。全仓该函数**只有 1 个调用方**（造模型处）→ 影响面最小 |
| **回归** | 新建 `tests/unit/test_provider_key_masking.py` **4 passed**：造模型取原文 ✓ / 列表仍掩码 ✓ / 出参掩码但库里原文 ✓ / **掩码串回存不许毁掉真 key** ✓ |

### 19.2 前端两处体验修正（用户实测反馈）

| 项 | 变化 |
|---|---|
| 价格台账卡 | 原来**一开会话就整卡铺开**（挡视线）→ 现在默认只留标题行（像个可点的小按钮 + 「⤢ 悬浮展开」提示），**鼠标悬浮 / 键盘聚焦**才展开；纯 CSS 实现（不包 wrapper：模板里字符串含 `<`，div 配平不可靠） |
| 模型选型 | 原来只有"切到另一套"，**没法回到默认模型** ✗ → 后端 `llm_provider_set_active` 改成**开关**：已是「使用中」再点一次即**停用**，回到 `.env` 默认模型；按钮文案变「✓ 使用中（再点停用）」并带 title 说明 |

### 19.3 收尾：错误人话映射 + 模板碰撞不变量（2026-09-30 第二轮）

| 项 | 变化 |
|---|---|
| **上游错误人话映射** | `api/server.py: _llm_error_hint(err)` —— 401/402/404/429/连不上 各自给"**用的是哪套配置** + 照着做的动作"；错误行变成 `Agent 执行出错：<人话>\n\n<原始异常>`。**这正是 19.1 事故要防的**：当时只丢一句 `401 … ****f98f is invalid`，谁也想不到是本地把掩码串发了出去 |
| **价格卡收起态（第二轮）** | 第一版用 `.price-card > *:not(.pc-head) { display:none }` 失败 ✗（只有 `.pc-picker` 被藏住）；第二版照登录页 `:hover/:focus-within` 也失败 ✗（点过输入框后 `focus-within` 一直保持展开）。**第三版改为 JS 显式状态**：`priceCardOpen` + `@mouseenter/@mouseleave` + 4 处 `v-show` + 点标题可切换；收起态 = **只有「📉 价格台账」小标签** |
| **模板碰撞不变量** | 插 `v-show` 时插入点落进了 `v-if="… >= 1"` 的 `>` 里 ✗（`node --check` 查不出模板 ✗）→ 已修复并加**静态不变量**：扫 `v-xxx="…v-yyy="` 嵌套、扫 `v-if/v-show/v-for` 语法、以及"价格卡必须用 v-show 显式控制" |
| **模型启停** | `llm_provider_set_active` 改成**开关**：再点「✓ 使用中」= 停用 → 回到 `.env` 默认模型（原来只能切到另一套 ✗） |
