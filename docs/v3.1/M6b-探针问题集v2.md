# M6b · 加压探针问题集 v2（**定稿**）

> 目的：把探针问句从「**8 个虚构桩工具**」换成「**尼古喵喵账号真实配置的 28 条能力**」
> 依据：`tool_capabilities`（account_id=3，2026-10-01 快照）＋ `tool_sources`（7 个来源）
> 状态：**已定稿并落地**（2026-10-01 用户裁决四项全采纳）→ 实现在 `tests/live/m5b_pressure_probe.py`
> 桌面批注副本：`C:\Users\ZQK\Desktop\M6b-探针问题集v2（待批注）.md`（编号与本文件一一对应）

---

## 一、为什么要换（旧题集的问题）

| 旧题集（v1） | 问题 |
|---|---|
| 8 个自造桩工具（`m5bp-sched` / `m5bp-fin` …） | 与用户真实工具池**毫无对应**，测的是「虚构工具的区分度」 |
| 问句集中在「今天大盘怎么样」这类**默认/内置工具领域** | 用户对内置工具（网搜/知识库/文档）基本不问，比重过高 |
| 无**多工具协作**用例 | 真实使用里常有「先查 A 再据结果调 B」，v1 完全没覆盖 |

新集口径：**每个工具至少 1 题**（低频调试类工具也只给 1 题、不重复）＋ **6 条跨源多工具题** ＋ 2 条反例；不再引入任何虚构桩工具。

---

## 二、工具 → 问句映射（35 条单工具）

判定形状：CLI = `run_shell_command(command=<bin>, argv=[…])`；API/MCP = `invoke_tool(ref=<ref>, …)`。
「期望首调」= **仅供人工打分参照** **不参与任何自动判定**（v3 制度见 §八）；题集里的 `exp` 字段就是它。

### A. CLI 来源（3 个工具 / 10 题）

| # | 工具 | 提问 | 期望首调 |
|---|---|---|---|
| 1 | weread | 我最近在微信读书上看的是什么书 | `run_shell_command(weread, [shelf, recent])` |
| 2 | weread | 微信读书上《人类简史》这本书讲什么 | `run_shell_command(weread, [book, resolve, …])` |
| 3 | weread | 我这个月在微信读书上读了多久 | `run_shell_command(weread, [readdata, summary])` |
| 4 | weread | 帮我看看我的读书笔记都在哪 | `run_shell_command(weread, [notes, notebooks])` |
| 5 | wechat-cli | 我最近和谁聊过天 | `run_shell_command(wechat-cli, [sessions])` |
| 6 | wechat-cli | 帮我找找我和袁战的聊天记录 | `run_shell_command(wechat-cli, [history, …])` |
| 7 | wechat-cli | 微信里有没有我还没读的消息 | `run_shell_command(wechat-cli, [new-messages])` |
| 8 | bili | B站我收藏的视频里有没有讲 Rust 的 | `run_shell_command(bili, [favorites])` |
| 9 | bili | B站今天有什么热门视频 | `run_shell_command(bili, [hot])` |
| 10 | bili | BV1xx411c7mD 这个视频讲了什么 | `run_shell_command(bili, [video, …])` |

### B. api 来源（4 个工具 / 4 题）

| # | 工具 | 提问 | 期望首调 |
|---|---|---|---|
| 11 | steam#searchGame | Steam 上《空洞骑士》卖多少钱 | `invoke_tool(api:steam#searchGame)` |
| 12 | steam#gameDetail | Steam 上 appid 是 774361 的游戏什么时候发售 | `invoke_tool(api:steam#gameDetail)` |
| 13 | xxapi#douyinHot | 抖音今天最热门的那条视频是什么内容 | `invoke_tool(api:xxapi#douyinHot)` |
| 14 | xxapi#ipQuery | 114.114.114.114 这个 IP 是哪儿的 | `invoke_tool(api:xxapi#ipQuery)` |

### C. mcp:12306-mcp（8 个工具 / 8 题）

| # | 工具 | 提问 | 期望首调 |
|---|---|---|---|
| 15 | get-tickets | 帮我查一下后天成都到重庆还有没有票 | `invoke_tool(mcp:12306-mcp/get-tickets)` |
| 16 | get-interline-tickets | 从成都坐火车去广州，有哪些需要中转的车次 | `invoke_tool(mcp:12306-mcp/get-interline-tickets)` |
| 17 | get-train-route-stations | G1033 这趟车中间都停哪些站 | `invoke_tool(mcp:12306-mcp/get-train-route-stations)` |
| 18 | get-station-code-by-names | 「上海虹桥」这个火车站的站点代码是多少 | `invoke_tool(mcp:12306-mcp/get-station-code-by-names)` |
| 19 | get-station-code-of-citys | 上海的车站代码是什么 | `invoke_tool(mcp:12306-mcp/get-station-code-of-citys)` |
| 20 | get-stations-code-in-city | 成都有哪几个火车站 | `invoke_tool(mcp:12306-mcp/get-stations-code-in-city)` |
| 21 | get-station-by-telecode | VNP 这个站码对应的是哪个车站 | `invoke_tool(mcp:12306-mcp/get-station-by-telecode)` |
| 22 | get-current-date | 下周三具体是几月几号，我要买那天的票 | `invoke_tool(mcp:12306-mcp/get-current-date)` |

### D. mcp:bnf 法国国家图书馆（8 个工具 / 8 题）

| # | 工具 | 提问 | 期望首调 |
|---|---|---|---|
| 23 | search_by_title | 法国国家图书馆里有没有《悲惨世界》 | `invoke_tool(mcp:bnf/search_by_title)` |
| 24 | search_by_author | Gallica 上有没有雨果写的资料 | `invoke_tool(mcp:bnf/search_by_author)` |
| 25 | search_by_subject | Gallica 里关于巴黎公社的资料有哪些 | `invoke_tool(mcp:bnf/search_by_subject)` |
| 26 | search_by_date | Gallica 里 1914 年的资料有哪些 | `invoke_tool(mcp:bnf/search_by_date)` |
| 27 | search_by_document_type | Gallica 里的手稿类文献有哪些 | `invoke_tool(mcp:bnf/search_by_document_type)` |
| 28 | advanced_search | 用法语 CQL 语法在 Gallica 检索 dc.creator=Victor Hugo | `invoke_tool(mcp:bnf/advanced_search)` |
| 29 | natural_language_search | 用自然语言在 Gallica 上找关于巴黎公社的资料 | `invoke_tool(mcp:bnf/natural_language_search)` |
| 30 | sequential_reporting | 用法语资料帮我做一份关于雨果的研究报告 | `invoke_tool(mcp:bnf/sequential_reporting)` |

### E. mcp:pdd 拼多多（3 个工具 / 3 题）

| # | 工具 | 提问 | 期望首调 |
|---|---|---|---|
| 31 | search_goods | 拼多多上搜一下 iPhone 17 的价格 | `invoke_tool(mcp:pdd/search_goods)` |
| 32 | explore_deals | 拼多多百亿补贴现在有什么好东西 | `invoke_tool(mcp:pdd/explore_deals)` |
| 33 | get_goods_detail | 拼多多这个商品 goods_sign 是 E932cKJQRhht7tuxwfDAxrupzXBiZpWMgg_JQPw6VPiN3，帮我看看详情和优惠券 | `invoke_tool(mcp:pdd/get_goods_detail)` |

### F. mcp:city / mcp:route（2 个工具 / 2 题）

| # | 工具 | 提问 | 期望首调 |
|---|---|---|---|
| 34 | city/search_city | Shanghai 这个城市在哪个省 | `invoke_tool(mcp:city/search_city)` |
| 35 | route/plan_route | 从成都开车到稻城亚丁大概要多久 | `invoke_tool(mcp:route/plan_route)` |

---

## 三、多工具题（6 条 · 跨源协作）

参考链路（**仅供人工打分参照**）：跨源协作题看「是否真的调了多个来源」——**由人判断** 不再由脚本自动要求 ≥2 来源（v3 制度见 §八）。

| # | 提问 | 期望链路 |
|---|---|---|
| 36 | 我想下周去广州，帮我查从成都出发的火车票，再规划一条从广州南站到珠江新城的路线 | `12306/get-tickets` → `route/plan_route` |
| 37 | 从成都坐火车去广州，中途要换乘哪些车？把车次和经停站都列出来 | `12306/get-interline-tickets` → `12306/get-train-route-stations`（同源两工具） |
| 38 | 帮我翻翻我和袁战的聊天记录，里面提到的那本书在微信读书上有没有 | `wechat-cli/history` → `weread/search` |
| 39 | Steam 上《空洞骑士》现在多少钱？顺便看看拼多多有没有卖周边的 | `steam/searchGame` → `pdd/search_goods` |
| 40 | 抖音今天最热门的那条视频讲了什么？顺便找找B站有没有相关视频 | `xxapi/douyinHot` → `bili/search` |
| 41 | 查一下 114.114.114.114 是哪儿的，然后帮我规划从那个城市到成都的路线 | `xxapi/ipQuery` → `route/plan_route` |

### 反例（不该调任何工具）

| # | 提问 | 期望 |
|---|---|---|
| 42 | 你是谁 | 不调工具 |
| 43 | 帮我写一首诗 | 不调工具 |

---

## 四、与旧集的可比性（重要）

- 旧集 10 题一轮 → 新集 **43 题一轮**，单轮耗时约 **20~45 分钟**（v1 实测单轮 3~8 分钟）；B1 三干净轮预计 **约 2~3 小时**。
- **`tests/fixtures/m5b_pressure_baseline.json`（B2 基线，8 桩工具 × 10 问）与新集不可比** → **已删除作废**（用户 2026-10-01 裁决④）；`data/m5b_pressure_result.json` 保留为普通产物（每轮覆盖）。
- 旧集 8 轮原始行（`data/m5b_probe_drift.json`）同样是旧集产物，不能当新集基线（本轮本来也没写基线 无损失）。
- 路由侧的 S3 基准（`tests/live/m5c_router_bench.py`，28 条池 / 30 题）**不受影响** 它用的是真实池快照，与新探针互补。

---

## 五、✅ 裁决（2026-10-01 · 用户全部采纳建议）

| # | 事项 | 裁决 |
|---|---|---|
| 1 | 题量 | **分层**：★核心 16 条做 B1 漂移基线（全量 43 条另跑一次「深度审计」） |
| 2 | 账号 | **跑真实账号 尼古喵喵**（工具与鉴权都是真的）；跑完按水位线清掉本轮新建数据 |
| 3 | 多工具判定 | 首调对 **且** 整轮出现 **≥2 个不同来源**（同源两工具题另立规则，见下「一处实现细节」） |
| 4 | 旧 B2 基线 | `tests/fixtures/m5b_pressure_baseline.json`（8 桩工具 × 10 问）**已删除作废** |

### 据此落地（`tests/live/m5b_pressure_probe.py` 已改写）

1. **题集**：43 条（35 单工具 + 6 多工具 + 2 反例），覆盖全部 **28 个工具**；
2. **分层开关**：`M5B_SET=core`（默认，★16 条）/ `M5B_SET=full`（43 条深度审计）；
3. **不再装桩、不再建账号**：改用真实账号（`M5B_USER`/`M5B_PWD` 可覆盖）；
4. **跑完清理**（`M5B_KEEP=1` 可跳过）：按**水位线**删本轮新建的 `conversations` / `messages` /
 `usage_events` / `memory_snapshots` / `failure_events` / `sessions`，并按**新增文件差分**删
 `agents_docs/<账号>`、`output/<账号>`、`pic/<账号>`、`data/sandbox/<账号>` 下的新文件 
—— 既有数据一律不动（跑前基准：conversations 48 / messages 1096 / sessions 83 / usage_events 111）
5. **软判据只记录不告警**（原「正例通过率 ≥7/8」硬失败项已改为打印记录，与 M6b 裁决① 对齐）。

### 一处实现细节（需你知道，因为它不可能靠"≥2 来源"过关）

第 37 题（`get-interline-tickets → get-train-route-stations`）是**同源两工具**协作：
来源只有 12306 一个 → 该题改为 `need_tools=2`（整轮出现 **≥2 个不同工具**）才算通过，
其余 5 条多工具题仍是 `≥2 个不同来源`。来源统计**不计内置工具**（网搜/知识库/文档）
——「先网搜再调工具」不算多工具协作。

### 跑法

```bash
.venv/Scripts/python.exe tests/live/m5b_pressure_probe.py # core 16 条（默认）
$env:M5B_SET="full"; .venv/Scripts/python.exe tests/live/m5b_pressure_probe.py # 全量 43 条（PS）
$env:M5B_ONLY="get-tickets"; .venv/Scripts/python.exe tests/live/m5b_pressure_probe.py # 单条冒烟
SKIP_LLM=1 .venv/Scripts/python.exe tests/live/m5b_pressure_probe.py # 离线自检（不调模型）
```

---

## 六、批注采纳记录（2026-10-01 · 桌面批注版回批）

| 批注位置 | 你的意见 | 处理 |
|---|---|---|
| #2《人类简史》 | 微信 CLI 与法国图书馆 MCP 都能答 → 要么两个都算对，要么问句里加工具限定 | **取「加限定」**：问句改「**微信读书上**《人类简史》这本书讲什么」。理由：加限定后期望唯一（`weread book resolve`）**不必放宽判定**；若改成"两个都算对"，这条就失去「该选哪个来源」的区分力。题面点名工具在真实使用里也常见 |
| #6 | 「牢妹」改「**袁战**」 | 已改（第 38 题同步改） |
| #13 抖音热搜 | 直接问热搜会让 agent 拉全量视频信息、**时长容易爆炸** → 改问「今天最热门视频的内容是什么」 | 已改「抖音今天最热门的那条视频是什么内容」 |
| #38 | 「牢妹」改「**袁战**」 | 已改 |
| #40 | （沿用 #13 的教训，我主动同步） | 改「抖音今天最热门的那条视频讲了什么？顺便找找B站有没有相关视频」 若你认为该题该保持原样，说一声我回退 |

**未采纳/未改动**：其余 39 条批注位为空；`M6b-测试基线与路由基准方案.md` §四之三 里的「帮我看看和牢妹的聊天记录」
是 **v1 实测的原始记录** 保持原样不改（那是当时真实跑出来的证据）。

**核心集（★，16 条）**：1、6、9、11、13、15、17、23、30、31、34、35、36、38、42、43

---

## 七、★ 实测反馈修正（2026-10-01 · 用户跑测中途发现）｜ ⚠️ **本节已废弃**

> ⚠️ **本节做法已作废（同日稍后）**：给 12306 补「合法前置表」被证明是**打补丁**——
> 链式工具（时间 → 城市/站点 code → 余票）会一直冒出来，补不完。最终改为
> **脚本只记调用序列、对错人工打分** 见 §八。保留本节仅为记录决策过程。

### 1）12306 的「先取日期」是**正确前置**，不能判错（用户实测反馈）

12306 MCP 里**除 `get-current-date` 之外几乎所有命令都要日期参数**（`get-tickets` /
`get-interline-tickets` / `get-train-route-stations` 都要 `date`），而项目**没有任何地方注入当前日期**
→ agent 先调 `get-current-date` 拿日期，是**正确的第一步** 旧判定只看首调，把这类全判成错。

**修正**：新增**合法前置表** `PRE_BY_SOURCE`（`tests/live/m5b_pressure_probe.py`）：

| 项 | 规则 |
|---|---|
| 放行范围 | 期望落在 `mcp:12306-mcp` 的问句（#15/#16/#17/#36/#37）→ 开头允许出现 `mcp:12306-mcp/get-current-date` |
| 判定 | **跳过开头的前置调用后**再看首调（`pre_skipped` 字段留痕，输出里会打印「已跳过前置 …」） |
| 不计协作 | 前置**不计入多工具题的来源数/工具数** → 「先取日期 + 随便一个来源」不算多工具协作 |
| 不越界 | 非 12306 的题不吃这条规则；前置之后的工具**不对**照样判错（自检有对应用例） |
| 将来扩展 | 别的来源发现同类需求，在 `PRE_BY_SOURCE` 加一行；也可在题目上写 `pre=[…]` 单独覆盖 |

### 2）自检当场逮到一个真 bug：MCP 的 ref 来源解析错 

`ref.split("#")[0]` 是**错的**：API 的 ref 是 `api:<服务>#<操作>`（用 `#`），而 MCP 的 ref 是
`mcp:<服务>/<工具>`（用 `/`）→ 按 `#` 切，MCP 每个工具的"来源"都变成整条 ref，
导致 **#37 的「同源两工具」被误算成 2 个来源**、前置表也查不到。
已收敛成 `_source_of(ref)`（`#` 优先、退化到 `/`） 自检覆盖 api/mcp 两种 ref。

> 影响面：本次修正**不改任何问句**，只改判定口径；`docs/v3.1/M6b-测试基线与路由基准方案.md` §四之四
> 同步记了一笔。**还没有基线**，所以不涉及基线作废。

---

## 八、★ 测试制度 v3：脚本只记录调用序列，对错**人工打分**（2026-10-01 用户裁定）

用户原话口径：12306 这类工具是**链式的**（除 `get-current-date` 外都要日期，余票类还要先拿城市 →
`station_code`），「只看首调」必然判错；而且**别的 MCP/API 将来只要有前置或多步，就得再补一条规则**
→ 打补丁没完没了。**∴ 脚本只返回调用了哪些工具、什么顺序，对错由人打分**。

### 8.1 四个文件，各管一件事

| 文件 | 职责 |
|---|---|
| `tests/live/m5b_pressure_probe.py` **v3 记录器** | 跑题 → 只记录：**调用序列（工具 + 参数摘要）**、顺序、来源数、耗时、状态码、答案摘录。**已无任何判定函数**（`judge()` / `PRE_BY_SOURCE` 全部删除） |
| `tests/test_out/m6b_score_sheet_<ts>.md` **人工评分表** | 每题一块：参考链路 / 实际调用序列 / `评分：` / `备注：`；`--desktop` 同时复制到桌面 |
| `tests/live/m6b_score_import.py` **回填器** | 解析填好的评分表 → `tests/fixtures/m5b_probe_baseline.json`（序列 + 人工评分 + 备注） |
| `tests/live/m6b_probe_drift.py` **v3 对比器** | 重跑后**只比调用序列变没变**；未测单列；基线里**没评分的题不比对** |

### 8.2 打分口径（建议，你说了算）

- `✓`：链路完整且步骤必要（例：12306 走「取时间 → 取站点码 → 查票」）
- `△`：结果对但路径冗余/绕路，或漏了可选步骤（备注写清）
- `✗`：用错来源或漏关键步骤导致拿不到结果（备注写清）→ 回填时会自动列进「路由质量修复」观察清单 
- `未测`（429/5xx/超时）：留空或写「未测」，**不计入能力评价** 

### 8.3 作废清单（相对 v2 ✗）

| 作废项 | 原因 |
|---|---|
| 「期望首调 == 实际首调」自动判定 | 链式工具必然误判 |
| 多工具「≥2 个不同来源」自动判据 | 改为人工看「是否跨来源协作」 |
| `PRE_BY_SOURCE` 合法前置表（§七） | 打补丁做法，已从代码里删除 |
| B1「基线中位数 − 2 题才告警」 | 建立在自动判分上 → 改为「**序列变化清单 + 人工复核**」 |
| 「干净轮」作为轮次有效性门槛 | 未测只单列 不再挡住整轮（未测题评分留空即可） |

### 8.4 标准流程

```powershell
# ⓪ ★ 先确认没有残留的过滤变量（2026-10-01 踩过：冒烟用的 M5B_ONLY 留在会话里 → 正式跑只跑了 2 题）
Remove-Item Env:M5B_ONLY -ErrorAction SilentlyContinue; Remove-Item Env:M5B_SET -ErrorAction SilentlyContinue
# ① 记录（默认 core 16 条；全量加 $env:M5B_SET="full"；要桌面副本加 --desktop）
.venv\Scripts\python.exe tests\live\m5b_pressure_probe.py --desktop
# ② 在评分表里填「评分：」与「备注：」（其余不用动）
# ③ 回填成基线
.venv\Scripts\python.exe tests\live\m6b_score_import.py "tests\test_out\m6b_score_sheet_<时间戳>.md"
# ④ 以后重跑后比序列
.venv\Scripts\python.exe tests\live\m6b_probe_drift.py --diff
```

### 8.4.1 冒烟（单条）怎么写才不留隐患 

```powershell
$env:M5B_ONLY="get-tickets"; .venv\Scripts\python.exe tests\live\m5b_pressure_probe.py; Remove-Item Env:M5B_ONLY
```

- 过滤运行的评分表**文件名带 `_only`** 且表头有「这不是完整题集」警示（脚本自证）；
- 控制台开跑时也会打 68 个 `!` 的横幅提醒—— 看到它说明这轮**不是**完整题集。

> ★ 本轮冒烟里暴露的「**正确使用对的工具**」类问题（ref/工具名拼错、参数不合规、重试风暴）
> **不属本制度管辖**—— 已作为**下一优化方向**单独记录：见 `M6b-测试基线与路由基准方案.md` §八。

### 8.5 已验证（离线，未调模型）

记录器自检（序列 / 顺序 / 参数摘要 / 来源解析 / **确认不存在判定函数** / 编号 / 覆盖 28）；
对比器自检（序列一致 / 变化 / 未测单列 / 基线未评分不比对）；

---

## 九、★ 首轮实跑与基线（2026-10-01 夜 ~ 2026-10-02 凌晨）

### 9.1 首轮 core 集记录（16 题）

- 命令：`.venv/Scripts/python.exe tests/live/m5b_pressure_probe.py --desktop`（`M5B_SET=core`）
- 聚合：**16 题 / 133 次工具调用 / 总耗时约 24 分钟 / 未测 0 题** 
（★ 换掉免费档模型后 **429 一次没出现** 印证「挡路的是额度不是能力」）
- 最重的三条：**#6 56 次调用 461s**、#30 11 次 248s、#36 14 次 213s
- 评分表：`tests/test_out/m6b_score_sheet_20261001_235916.md`（用户填）

### 9.2 终判（经用户逐条答复二次评分）

| # | 原判 | 终判 | 依据 |
|---|---|---|---|
| 6 | √ | **△** | 56 次分页拉全量（461s），『无冗余』字面不成立；护栏归后续方向 |
| 30 | √ | **△** | 绕开 `sequential_reporting` 的设计流程（6 次近似重复检索 + 3 次 `write_agent_doc`）；属用法层 → 后续方向 |
| 34 | √ | **（换题重测，见 9.3）** | 原题被模型记忆答对、路由未触发 |
| 38 | √ | **维持 √** | 用户裁定：写画像/存档是 agent 的**设计特性**（长期状态值得记入画像 见 `tools/profile_evidence.py`）→ 该题意外触发了**正确**行为 |
| 其余 12 条 | — | 维持原判 | — |

**终判分布：✓ 11 · △ 5 · ✗ 0 · 未测 0** ✓（无一条判"选错工具"）
裁定版评分表：`tests/test_out/m6b_score_sheet_core_final_20261001.md` 
基线：`tests/fixtures/m5b_probe_baseline.json`（16 题序列 + 评分 对比器实测可消费）

### 9.3 #34 换题（用户提出「知名城市已被训进记忆」假设 已证实）

| 项 | 内容 |
|---|---|
| 原题 | 「Shanghai 这个城市在哪个省」→ **0 次工具调用**、靠记忆答对（路由没被测到） |
| 换题 | 「帮我查一下**石渠县**的地理信息：属于哪个省、经纬度和人口分别是多少」 |
| 选点依据 | 离线核过数据源：`city_lookup` = GeoNames `searchJSON` 取第一条；石渠县查得到（**人口 80834 / 经度 98.10**）且冷门 → 记忆答不出 |
| 重测结果 | **1 次调用 `mcp:city/search_city {"city_name": "石渠县"} `、20.9s、200** → 证实原题是记忆导致的假通过 |

### 9.4 ⚠️ 清理脚本事故（已修 已可还原）

- **事故**：清除脚本 v1 的 `--last-conv N` 语义 = 「账号最新 N 条会话」 → 连带删掉 **2 条真实会话**
 （`1225`「先用知识库查一下「价格监控」相关的实现」18 条消息、`1278`「你在上传的图片中看到了什么」4 条）
- **数字完全对得上**（可自查）：`conversations 48→50→46`、`messages 1096→1107→1074`、
 `usage_events 111→135→84` 差额 = 中断残留(+2/+11/+24) + 误删(-2/-22/-27) 
- **加固（v2 ✓）**：`--last-conv N` **只在"探针候选"里取**（标题必须与题集问句对得上）；命中真实会话
 默认**拒绝执行** 必须显式 `--allow-real`；预演优先不变—— 实测现在 0 候选 真实会话不会再被选中 
- **还原器（新）**：`tests/live/m6b_restore_convs.py`（只读打开备份 只 INSERT 不存在的 id 默认预演）
 预演已确认：`1225`(18 消息) + `1278`(4) + 27 条 `usage_events` 可从
 `docs/v3.1/backups/personal_before_m5b_cleanup_20261001.db` 还原 

### 9.5 已知限制（探针清理的两点，用户自行处理）

1. **不回滚"被修改"的文件**：本轮 `agents_docs/尼古喵喵/MEMORY.md` 被改（3207 → 4451 字节，
 由 #36 的 `update_user_profile` 触发）而清理只删新增文件 → **被修改的旧文件原样留着**；
2. **会删本轮新建的 `memory_snapshots`**：那是「改之前」的回滚点，删了就没了回退依据。

> 对策（用户 2026-10-02：自行处理）：跑探针时若想留证据/留回滚点，用 `M5B_KEEP=1`；
> 或跑前手工备份 `agents_docs/<账号>` 与 `data/personal.db`。

### 9.6 小坑：评分符号 `√` ≠ `✓`

用户习惯写 `√`（U+221A），脚本生成的是 `✓`（U+2713）—— 两者**不是同一个字符**。
回填器已加**符号归一**（`√/v/对 → ✓`、`×/x/✕/错 → ✗`、`o/部分 → △`），且本轮终判表已统一成 `✓`。

---

## 十、★ 全量 43 条深度审计（2026-10-02 02:14 跑 · 用户评分 + 二次评分裁定）

### 10.1 运行与聚合

- 命令：`M5B_SET=full` + `M5B_KEEP=1`（保留会话与产物便于回看）+ `--desktop`；模型 openRouter `space-bunny-alpha`
- 聚合：**43 题 / 209 次工具调用 / 总耗时约 34 分钟 / 未测 0 题** 
- 最重三条：#8 30 次/221.7s（翻收藏夹分页）、#36 31 次/189.1s、#28 4 次/133.7s
- 评分表：`tests/test_out/m6b_score_sheet_full_final_20261002.md`

### 10.2 终判：**✓ 36 · △ 5（#3 #25 #27 #35 #36）· ✗ 2（#30 #37）· 未测 0**

三处改判与一处调研（用户全部采纳）：

| # | 原判 → 终判 | 依据 |
|---|---|---|
| 3 | → **△** | 步骤 2/3 同参数同命令（`readdata detail` ×2），与本轮 #25/#27/#30 的同一口径 |
| 19 | 待调研 → **✓** | **三个站码工具都是真的、语义不同**：`get-station-code-of-citys`=城市→**代表站**码；`get-stations-code-in-city`=城市→**全部车站**列表；`get-station-code-by-names`=**站名**→码。agent 首调即选对 `of-citys`，第二次 `in-city` 是补充信息 → 非错误选择 |
| 30 | △ → **✗** | **usage_events 实证**（会话 id=1529，20 条事件）：未调 `write_agent_doc`、参考链路里的 `sequential_reporting` **一次未调**，只做 kb/docs 读取 + 7 次 bnf 检索（反复参数类型错）→ **报告未产出** |
| 37 | → **✗** | 问的是「换乘/中转 + 经停站」→ 参考链路 `get-interline-tickets` + `get-train-route-stations`，实际 **`get-interline-tickets` 一次未调**，全程只有 `get-tickets`（直达余票）两次 → **同源内选错函数**（同 S3 的 trip-mixed/trip-transfer 形态） |

★ 两条 进「路由质量修复」观察清单（与 S3 的 5 例同属"选对工具"范畴）。

### 10.3 两个悬案结案（均有实测证据）

1. **#30「报告到底落盘没有」→ 没有**：全盘扫描 `agents_docs|output|pic|data/sandbox/尼古喵喵` +
 全项目按文件名搜 `Victor/雨果/Hugo` = **0 命中**；本轮唯一被改的文件是 `MEMORY.md`。
 ★ 上一轮 core 里模型自称"报告已交付，文件名 `尼古喵喵/Victor-Hugo-法语研究报告.md`"属**自述不实** 
 → 再次印证探针铁律「**不采信模型自述**」。
2. **#19 两个站码工具** → 见 10.2 ✓（真工具、语义不同、非错误写法）。

### 10.4 给「下一优化方向（正确使用对的工具）」的新证据

| 题 | 现象 | 分类 |
|---|---|---|
| 36 | **4 处畸形调用**：`invoke_tool="珠江新城</invoke><invoke name="invoke_tool`（XML 标签泄漏进参数）、`mcp:route tool`（凭空的工具名）、`"date": "108"`、`"fromStation": "SOH"` | 调用有效性（比 mpc/toulry 更严重） |
| 12 | 用 `curl` 直接打 Steam 接口，**绕过已配置的 `api:steam` 能力** | 边界/用法 |
| 8 | 30 次分页翻遍 bili 收藏夹（CLI 默认只返第一页） | 工具能力 / 分页护栏 |
| 25/27/30/36 | 「同参数同命令」重复（已计入 △） | 重复调用护栏 |

### 10.5 概率性变化（用户裁定：暂不特殊处理）

**#6 同题同模型两次跑差异巨大**：core 轮 **56 次/461s**（分页拉全量）vs full 轮 **2 次/23.4s**。
→ 基线记录的是**一次路径**，**序列变化 ≠ 变差** 交由人工判断（比对器输出已写明这句）。

### 10.6 主基线与残留

- **主基线**：`tests/fixtures/m5b_probe_baseline.json` = **full 43 条**（序列 + 评分 + 备注 
 用户裁决：full 覆盖 core 它包含 core，core 跑也能比对）
- 核心集基线存档：`tests/test_out/m6b_probe_baseline_core_20261001.json` 
- ⚠️ **KEEP 残留待清**：43 条探针会话 / 482 消息 / 439 用量事件（预演确认命中全为 ★探针 真实会话未碰）
 → `.venv/Scripts/python.exe tests/live/m6b_purge_probe_run.py --last-conv 43 --purge`
- ⚠️ `agents_docs/尼古喵喵/MEMORY.md` **再次被改**（4451 → 4931 字节，02:34）—— §9.5 那个
 "清理不回滚被改文件"的已知限制在 **KEEP 模式下同样成立**（该模式下连清理都没跑）

### 10.7 端到端复验抓到并修掉一个真 bug（换行把序列行拆断）

- **现象**：拿本轮记录比新基线，报「#36 序列变化」；但对逐字对比发现两串**看起来一模一样**。
- **根因**：#36 那次畸形调用（XML 泄漏）的 brief **含真换行** → 写进评分表时把编号行拆成两行 →
 回填器只取到前半截 → 基线少一段 → 下一次比对**误报变化**（典型"数据格式 bug 伪装成行为差异"）
- **三处修掉**（都带注释说明）：
 ① 记录器 `parse_calls`：brief 生成时把 `
\n` **转义成 `\n` 字面量**（源头保证单行）；
 ② 回填器 `parse`：容忍旧表被拆断的行，按"末尾有反引号"判定完整、否则**续行并回**；
 ③ 对比器 `seq_key`：比对前**两边都归一换行**（治本：无论数据哪来的都能比）。
- **复验**：`m6b_probe_drift.py --diff` → **序列一致 43 题 · 变化 0 题** ✓（exit 0 ✓）
**往返自检**：记录器写出的评分表 → 回填器解析 → 对比器消费，契约一致（含「序列变化能被抓出」用例）。
