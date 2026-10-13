# 04 调研：文档架构优化与外接配置 env 条目优化

> **调研期产物**（编排规则见 `../版本文档规范.md` §三）。**2026-10-13 已吸收用户批注**：批注结论一律以"既定口径"记入本文，正文相应处已改写；**尚未定性**的点集中列在 **§五**（等你继续批注）。
> **逐目录职责表**（每个目录"放什么 / 不放什么 / 谁在用"）在附件 `04-附录-全量目录树与逐目录职责.md` §一 —— 本文件只留**问题与结论**，不重复目录职责。
> **调研期硬约束**：不动代码、不改数据、取证只做只读探测。**例外**：2026-10-13 经用户明确授权，已执行 §3.6 的全部存量清理（测试账号 + 根目录违规文件）。

---

## 〇、任务口径（用户原话要点 + 本轮批注）

**大点名**：文档架构优化与外接配置 env 条目优化。两方面调研：

1. **文档架构优化** —— 是 v3.0 时期《文件存放规则.md》工作的**延伸**。v3.0 整治"项目文件放在哪个文件夹下没有明确约束导致乱放"；本次整治**项目文档架构设计上**的问题。要求：**全面调研当前项目下所有文件夹及其子文件的作用**、存放哪方面的文件、以及每个"非代码的其他记录性质文档"的作用；**大量参考** `docs/文件存放规则.md` 并验证查缺补漏。
2. **外接配置 env 条目优化** —— 顺带勘察**有哪些需要外部信息填入 `.env`、但当前没进 env 条目的外部配置**。验证方法（原话）："一台之前完全没有下载该项目、没有任何 `.env` 配置的电脑，在保证 `.venv` 正确的情况下，会在哪些地方因为没有外部 key 或认证而报错。"

**追加的两问**：排除自配 CLI/MCP/API 后有没有外部键连 `.env` 都没进（→ §3.3(c)）；目录遍历要完整递归（→ §3.5 与附件）。

**追加的一轮前置工作（2026-10-13，用户指令，均已完成）**：① 根目录存量违规文件清除；② 全部无用测试账号及其 RAG 数据删除完毕；③ 文件内批注全部落成口径、待批事项已定性。**前置清理已收官，04 可进入「定方案」。**

---

## 一、调研方法与取证手段

| 手段 | 具体做法 | 覆盖 |
|---|---|---|
| 只读清点 | `ls` / `find` / `du` + 抽样读文件头部注释 | 顶层 16 个目录、800+ 文件 |
| 三路并行侦察（子代理） | ① 文档与记录类 ② 代码与资产类 ③ `.env` 外接配置；各自只读、禁止改文件、禁止打印密钥 | 全仓库 |
| 交叉验证 | 关键结论由助手独立复核（键名 diff、目录实况、规则对照） | 见 §二 / §三 |
| 规则对照 | 逐条比对《文件存放规则.md》《版本文档规范.md》与实际 | 两份宪法 |
| **全量递归普查** | 项目根完整递归遍历，逐目录产出「文件数 / 子目录数 / 体积 / 代表文件 / 宪法覆盖」，并补写逐目录职责 | **378 个目录**（附件） |
| 静态清点（env） | 匹配 `os.getenv` / `os.environ.*` / `dotenv_values` + 传参型 `_env(名字, 默认)`；与两份 env 文件对账 | 内置部分全部读取点 |
| **全量账号清点（本轮新增）** | 跨 6 个存放点 + SQLite **25 张表**逐账号统计，产出 §3.6 清理清单 | 301 个账号 / 20 张有账号列的表 |

**取证红线**：不修改 `.env` / `.env.example`；密钥一律脱敏；不打网络、不触发额度型调用。

---

## 二、第一手事实（助手自查，只读）

### 2.1 顶层实况

| 目录 | 文件数 | 体量 | 规则里的职责 | 实况 |
|---|---|---|---|---|
| 目录 | 文件数 | 子目录 | 体量 | 规则里的职责 | 实况 |
|---|---|---|---|---|---|
| `agent/` | 20 | 1 | 0.13 MB | 智能体核心 | 相符 |
| `agents_docs/` | 23 | 10 | 0.05 MB | 用户级资料（不入库） | 清理后**只剩 3 个真实账号**（尼古喵喵 / 灵康科技有限公司 / 药喵） |
| `api/` | 16 | 0 | 0.34 MB | FastAPI 服务层 | 相符 |
| `cli/` | 10 | 0 | 0.06 MB | CLI 版（dspro） | 相符 |
| `data/` | 449 | 107 | **153.65 MB** | 运行时数据（不入库） | 相符；体量集中在 `desktop_webview/`（143.5 MB） |
| `docs/` | 193 | 18 | **56.38 MB** | 里程碑文档 + `sql/` + 索引 | 体量异常偏大（§3.5） |
| `front/` | 42 | 6 | 5.77 MB | 前端一切 | 相符 |
| `output/` | 73 | 4 | 17.42 MB | agent 产物（不入库） | 清理后只剩 3 个真实账号 |
| `pic/` | 16 | 4 | 12.73 MB | 用户上传背景图（不入库） | 清理后只剩 3 个真实账号（含 `.gitkeep`） |
| `prompt/` | 1 | 0 | 0.05 MB | `prompts.yml` | 相符 |
| `rag_knowledge/` | 14 | 14 | 3.18 MB | M3 RAG（`db/` 不入库） | `db/` 清理后只剩 4 个目录（3 账号 + `capability_pool_3`） |
| `scripts/` | 8 | 0 | 0.06 MB | 开发/运维脚本 | 相符 |
| `tests/` | 217 | 9 | 3.72 MB | 测试与调试（七子目录） | 相符；有混放（§3.2） |
| `tools/` | 39 | 1 | 0.60 MB | 内置工具开发（含 `_runtime/`） | 相符 |
| `utils/` | 2 | 0 | 0.01 MB | 通用小工具 | 相符 |

根目录 11 个文件（`main.py` · `dspro` · `dspro.cmd` · `pyproject.toml` · `uv.lock` · `.python-version` · `.gitignore` · `.env` · `.env.example` · `README.md` · `IDEA.md`）+ 16 个子目录 —— **合规**（原有 4 个存量违规文件已于 2026-10-13 清除，见 §六）。

### 2.2 `.env` 与 `.env.example` 的键位差异（只读键名，不含任何值）

**`.env.example` 系统性落后**：`.env` **36 键** / 示例 **19 键** / 「只在示例」**0 键** —— 示例是 `.env` 的真子集，**17 个键没进示例**（清单见 §3.3(b)）。

| 分组 | 只在 `.env` 的键 | 来源 |
|---|---|---|
| 决策模型层（大点 02） | `DECISION_MODEL_API_KEY` `DECISION_MODEL_BASE_URL` `DECISION_MODEL_NAME` `DECISION_TRIGGER` `DECISION_TOP_K` `DECISION_TIE_THRESHOLD` | 02 实现期新增，未回写示例 |
| 测评 LLM（大点 03） | `PROBE_LLM_API_KEY` `PROBE_LLM_BASE_URL` `PROBE_LLM_MODEL` `PROBE_LLM_JUDGE_MODEL` `PROBE_LLM_TIMEOUT` `PROBE_LLM_RETRIES` `PROBE_LLM_MAXTOKENS` `PROBE_ASK_TIMEOUT` `PROBE_GEN_CONCURRENCY` | 03 实现期新增，未回写示例 |
| 其它 | `PERSONAL_DB_PATH` `MYSQL_USE_PURE` | 更早批次新增，同样未回写 |

> 这本身就是"文档架构病"的实例：**同一份配置事实分散在两个文件、靠人记得同步 → 必然漂移**。

### 2.3 状态与计数漂移（文档架构问题实证）

- `v4.0规划文档.md` 目录表写 02=「方案未敲定（调研中）」、03=「实现中」，而**同一文件** §02 正文写「方案已定（2026-10-12）」、§03 写「已完成」——同一文件内两处打架。
- `Verdict/02` 实际 **9 条**；`Done/02·实现记录.md` 写「6 条」、规划文档 §02 头部写「5 条」、`README` 写 9 条——**同一事实三处三个数字**。

**根因（架构层）**：同一事实要维护在 **4 处** —— ① 规划文档目录表 ② 大点标题状态行 ③ 版本 `README` 进度表 ④ `交接文档` 状态块；无同步机制 → 漂移是必然而非偶然。
**本轮批注已给出方向**：第三处（版本 README）**退出**版本内状态同步（见 §3.1(a)），漂移面从 4 处缩到 3 处。

---

## 三、调研发现

### 3.1 文档与记录类

**一句话结论**：v4.0 四件套（规划 / `research/` / `Verdict/` / `Done/`）内部编排干净、引用链完整；出问题的全是**层级边界与状态同步** —— 规则落后于目录事实、同一裁定的条数被抄成三个数字、状态在"权威文档"内部自相矛盾，以及 4 类"记录性质文档"（设计图产物 / 文档快照 / 学习笔记 / 用户交付物）规则未覆盖。

#### （a）记录性质文档的角色表 —— **已按本轮批注重定义**

批注原文（独立段落）：
> 保持严格口径，决策相关字眼永远只在 Verdict 里面写；规划文件的实现过程不再记录裁决记录，只记录详细方案规划；实现记录只严格记录实现过程；README.md 其实不应该是作为索引表，而是留在整个版本做完以后，写用于面向用户式的（不从事开发的用户视角）新版本功能介绍。

| 记录类型 | **新定位（本轮批注后）** | 变化 |
|---|---|---|
| `Verdict/NN-*.md` | **决策字眼的唯一落点**：所有"选型 / 取舍 / 为何这么定"只写这里 | 强化（原来是"裁决唯一落点"，现在是**决策类字眼的唯一出口**） |
| `vn.0规划文档.md` | **方案唯一权威**：只写**详细方案规划**；**不再**记录裁决结论、不记实现过程 | 变更（原表述含"用户裁定后定稿"） |
| `Done/NN-*·实现记录.md` | **严格只记录实现过程**：问题 → 做法 → 结果 + 可复跑数字 + 偏差/事故 | 收窄（不承载决策口径） |
| `Done/NN-*·逐文件带读.md` | 逐行检查：文件清单 + 关键行号 + 链路位置 + 思考题 | 不变 |
| `docs/<版本>/README.md` | **不再是版本内索引 / 进度表**；改为**整个版本做完之后**写的、**面向非开发用户**的「新版本功能介绍」 | **重定义**；另按批注：**进度真值唯一落点 = `v4.0规划文档.md` 的目录表**，以后不再在其它文档写进度 |
| `交接文档.md` | 版本**冷启动入口**：当下状态 + 下一步 + 纪律 | **收紧（批注）**：不再承载进度，进度只写在规划文档目录表；本文件只留冷启动信息与指向 |（原文另注：新版 README 之后，"版本进行中的状态"就更依赖它与规划文档，见 §五 第 5 点） |
| `research/NN-*.md` | 调研期信息（多方向、含废案），实现期只许定点查 | 不变 |
| `research/*-demo/` | 破例目录：脚本 + 数据 + 日志同处复核，整体不入库 | 不变 |
| `backups/` | 改用户数据前只读快照 | 留存期变更：**该大版本 git 提交完成后即删除**（批注，见 §3.4） |

#### （b）疑点清单（15 条，含"本轮批注后状态"）

| # | 疑点 | 复核 | **本轮批注后状态** |
|---|---|---|---|
| 1 | **规则落后于目录事实** —— `文件存放规则.md:37` 与 `README.md:103` 的 `docs/` 行仍写「v1.0/ v2.0/ v2.5/ v3.0/ + sql/」，**缺 v3.1、v4.0，也未列《版本文档规范.md》** | 助手复核 | **待改**（D1 修宪，含批注新增的老版本统一口径） |
| 2 | **设计图类产物无归属** —— 同一张图两个"现行"落点（`docs/*.html` 新 / `docs/v3.1/*.html` 旧，同名且都被跟踪）；规格 json 归位与入库口径空白 | 助手复核 | **批注已定**：设计图**以后放项目根目录**，随版本更新、路由变化**不断维护更新**（常青文档）→ 进宪法 §一 与判据速查；旧的那份按"单一现行版"收口 |
| 3 | **同一裁定条数被抄成三个数字** | 助手复核 | 部分缓解：README 退出索引后少一处；仍需 D3 定"引用计数同步清单" |
| 4 | **权威文档内部状态自相矛盾** | 助手复核 | 同上；另按批注，规划文档不再落裁决 → 规划文档与 Verdict 的边界要写进宪法二 |
| 5 | **破例目录混入编译产物** —— `research/02-*-demo/__pycache__/` 10 个 `.pyc` | 助手复核 | 待办：破例条款补一句"不含 pyc"；pyc 随清理删除 |
| 6 | **裁决记录两套口径并存** —— `docs/v3.0/README.md` 的"用户裁决记录"与宪法二「裁决只有一处」同树 | 侦察结论 | 按批注（老版本不维护）**降级为历史**，只在新宪法里加一句边界 |
| 7 | **backups 口径外延**（文档快照 vs 数据快照；"哪些可删"无判据） | 助手复核 | **批注已定**：**大版本 git 提交完毕 → backup 即删除** |
| 8 | **不入库清单分散两处、口径不全**（v3.0/backups 未忽略已入库） | 助手复核 | 同上（随 backups 新口径一起收口，D1） |
| 9 | **用户交付物落用户资料目录、且被沙箱复制一份**（药喵 3 份 + `data/sandbox/药喵/profile_backup_*/` 副本） | 助手复核 | **批注已定**：本大点**不处理**，交由 **05 大点**（前端用户自由编辑沙箱文件操作 + 工作路径自由指定 + Agent 写能力）解决 |
| 10 | **测试账号残留无清理口径**（20 个 `bk_*` + 4 个未登记测试账号与真实账号平级） | 助手复核 | **批注已定** → 本轮**已彻底清理完毕**（执行记录见 §六，根因见 §3.6）；宪法补"测试账号"口径（D6） |
| 11 | **SOUL 双形态无判据** | 侦察结论 | **批注已定 + 已按证据处理**：生效源 = `ACTIVE_SOUL`，且**它可指向两种形态**（`SOUL/*.md` 或顶层 `SOUL.md`）。查证：**只有 `尼古喵喵` 的顶层 `SOUL.md` 是脱离残留（已删）**；**`药喵` 的 `ACTIVE_SOUL` 正指向顶层 `SOUL.md`，是生效人格（保留）** → 宪法写清生效源与两种形态 |
| 12 | **`ACTIVE_VOICE` 未登记**（`api/voice_tts.py:113` 在用） | 侦察结论 | **批注已定**：补上，**漏记的都要补上**（→ D1 修宪范围扩大：以代码实际读写为准逐项补登） |
| 13 | **教程截图归属空白**（`docs/v2.0/演示文档/` 6 张 png） | 助手复核 | **批注已定（关闭）**：**docs 是纯文档信息、不应被项目依赖**；被前端链接的是复制到 `front/` 下的那份，老教程文档**就留在原地不动** |
| 14 | **学习笔记 / 待定清单无角色**（`docs/v1.0/note.md`、拼写错的 `someting_to_be_deter.md`） | 助手复核 | **批注已定**：老版本文档较乱、**暂不维护**；对老版本 docs 的**统一口径 = "存放各种文档"**，详细文档存放规则**从 v4.0 起**才讲 |
| 15 | **文档线索三处并存、互不强制同步**（根 README 两套阅读指南 / docs/README 15 步 / 交接文档；根 README 独漏 `Done/02`） | 助手复核 | 待批注（§五 第 6 点）：README 重定义后，根 README 阅读指南与 `docs/README.md` 的角色需一并定 |

### 3.2 代码与资产类

**一句话结论**：主干职责与《规则》§二高度吻合；问题集中在 ① 根目录存量散落 ② 少量真实越界 ③ 规则覆盖缺口。

| 路径 | 实际内容 | 越界/多余 | 规则 |
|---|---|---|---|
| 根目录 | 入口/包装/配置/声明 | **已合规**：`c7.js`、`index_prompts.tmp`、`.env.bak_*`×2 已清除（2026-10-13）；`__pycache__/`、`.pytest_cache/` 属工具生成且已被忽略 | 相符 |
| `agent/` | 20 文件：llm / build_context / context_* / conversation_store / digest_engine / thinking_capture / usage_counter…；`subagents/` 4 | **两处反向依赖**：`agent/stream_sink.py:83`、`agent/thinking_capture.py:28` 均 `from api.monitor import monitor` | 违反（批注：调研能否修复 → §3.2(b)） |
| `api/` | 16 文件：server / monitor / account / customize / cost_api / usage_api / price_api / probe / tools_sources / voice_tts / digest_scheduler | **直接 import `tools/_runtime`**：`api/server.py:59`、`api/tools_sources.py:335` | 违反（批注：调研能否修复 → §3.2(b)） |
| `cli/` `prompt/` `utils/` `scripts/` `rag_knowledge/` | CLI 10 / prompts.yml 1 / 工具 2 / 脚本 8 / RAG 5 py + `db/` 131 账号 | 无 | 相符 |
| `tools/` | 39 文件：capability_{pool,invoke,lint,draft}、price_*(7)、memory_*/profile_*、probe_{agent,runner}、tool_router、`_runtime/{api,mcp,shell}` | `backfill_reasoning_answer.py`（批注：查清是不是一次性脚本 → §3.2(b)） | 待办 |
| `front/` | 46 文件：index.html、vendor/、tutorial/（md+svg+png + `previews/` 8）、desktop/ | `desktop.exe` 367KB 二进制入库；日志已忽略 | 待修 |
| `tests/` | 七子目录 | `test_out/` 混入 **8 个 `.py` 脚本**；`fixtures/` 的 2 个 `.bak` 已移出（来源见 §3.6）；`test_out/` 的 8 个 `.py` 待归位 | 待办 |
| `data/` | 155MB：audit / cli_docs_cache / desktop_webview(399,145M) / probe_sets / sandbox / tutorial_assets / tts_ref(空) | 根级 **3 个 `m5b_pressure_result*.json`** 错放；`probe_sets/` 规则未列 | 待办（批注：按规则归位 + 明确 probe_sets 规则） |
| `output/{账号}/` | 4 账号：m2tester(21) / 灵康(21) / 尼古喵喵(51) / 药喵(1) | 打分表命名像测试产物 | 相符（m2tester 待定性，§五 第 2 点） |
| `pic/{账号}/` | 6 目录：`3`（数字 ID，6 图）/ m2tester(空) / 灵康(1) / 尼姑喵喵(空) / 尼古喵喵(8) / 药喵(空) | 数字账号 ID、错别字重复、3 个空目录 | 待清理（批注：见前文，做无用测试账号彻底清理 → §3.6） |

**`tests/` 七子目录实测**：`unit/` 48 · `harness/` 15（纯 .js）· `testclient/` 18 · `live/` 42（命名混）· `debug/` 10（8 个 `_` 前缀可删）· `fixtures/` 11 · `test_out/` 72（含 8 个 `.py` = 越界）。

**共性缺口**：规则只约束"新增"，**没有"定期巡检 + 存量纠正"** —— 故本大点新增 D5。

#### （b）【本轮批注复查】三处结论（已实测，非推测）

**① `agent/` → `api.monitor`（2 处）—— 结论：可修复，成本低**

- `agent/stream_sink.py:78-86`：**已经是"注入式"设计** —— 构造参数 `emit` 可传入，只有 `emit is None` 时才在函数内 `from api.monitor import monitor` 兜底。
- `agent/thinking_capture.py:28`：模块级 `from api.monitor import monitor`，属硬依赖。
- 修法：让**调用方（本来就是 api 层）必传 `emit`**，`thinking_capture` 改为接收上报函数参数（或由调用方传 monitor 实例）。`api/monitor.py` 是纯状态 + WS 推送对象，只依赖 `api.context`，不需要反向依赖 agent —— 因此**单向化可行，不改变运行链路**。

**② `api/` → `tools/_runtime`（2 处）—— 结论：更推荐"修规则一行"，而不是改代码**

- 用途：`shell_runtime`（`/api/shell/*` CLI 面板路由用：`allowed_commands` / `execute` / `run_line`）、`mcp_driver`（保存 MCP 来源时复用 `validate_cfg` 做配置语义校验）。
- 关键事实：宪法 §二 自己写着 `tools/_runtime/` = "**内部实现（能力执行层）：面板与 Agent 共用的执行器**" —— **"面板"就是 `api`**。也就是说，**规则正文与规则禁令自相矛盾**：定位说给面板用，禁令说不许外部 import。
- 两条路：**(a) 改代码**：在 `tools/` 加薄门面（facade）导出这两个符号，api 只 import 门面（多一个文件，但保持禁令）；**(b) 修规则**：把禁令改成"只许 `api`（面板层）与 `tools/` 内部 import，其它模块（agent/cli/utils）禁止"（一行代价）。
- 助手倾向 **(b)**：因为它承认实际架构（api 就是面板层），而 (a) 只是为了绕开一句写窄了的规则、凭空多一层。

**③ `tools/backfill_reasoning_answer.py` 是不是一次性脚本 —— 结论：是，但性质是"历史数据回填的备用脚本"，不是迁移脚本**

- 文件自述：把"正文被写进 `reasoning_content`、`content` 为空"的历史 assistant 行补回正文；**默认 dry-run**，`--apply` 前自动备份受影响行。
- 历史裁定：`docs/v3.0/README.md` **X2 已定：不回填**（不影响功能；dry-run 命中的行当前仍只有 1 条 `#2539`）；脚本**留作备用**。
- 引用：被 `docs/v2.5/…`、`docs/v3.0/v3.0规划书.md`、`docs/v3.1/backups/…` 提及（均为历史文档）。
- 判定：符合宪法 §二 `scripts/` 的定位（"开发/运维脚本（非测试）：预览生成、CDP 探针、**数据迁移**"）→ **应移到 `scripts/backfill_reasoning_answer.py`**；历史文档里的路径引用按"老版本不维护"口径**不回改**，只在 v4.0 的迁移记录里留一行。

### 3.3 `.env` 外接配置（核心产出）

#### （a）已在 `.env.example` 的键 + 实际读取点（节选关键行）

| 变量 | 读取点 | 缺失后果 |
|---|---|---|
| `LLM_MODEL_MAX` | `agent/llm.py:16`、`api/server.py:187` | **import 期即崩**（空串 → `OpenAIError: Missing credentials`；未设 → pydantic `ValidationError`） |
| `OPENAI_API_KEY` | `agent/llm.py:17` | **import 期即崩**；`api.server`/`main`/CLI/桌面**四种入口全灭** |
| `OPENAI_BASE_URL` | `agent/llm.py:18` | 不报错，**静默**打到 api.openai.com → 调用时 401/超时 |
| `TAVILY_API_KEY` | `tools/tavily_tool.py:28,195` | **不报错**（SDK 回落 keyless 仍能搜）；填错值才 `InvalidAPIKeyError` |
| `MYSQL_{HOST,PORT,USER,PASSWORD,DATABASE,CHARSET,COLLATION,SQL_MODE}` | `tools/db_tools.py:20-28` | 调用期 `ValueError: 缺失数据库核心配置`；`MYSQL_PORT` 空串 → `int('')` 崩 |
| `MYSQL_USE_PURE` | `db_tools.py:32` | 无（默认 True） |
| `EMBEDDING_MODEL_PATH` / `RERANKER_MODEL_PATH` | `rag_knowledge/kb_service.py:36,37` | **默认值硬编码本机路径 `D:\LLM\model\...`** → 全新电脑知识库必失败 |
| `EMBEDDING_DEVICE` | `kb_service.py:38` | 默认 `cuda` → 无 GPU 机器硬崩 |
| `PERSONAL_DB_PATH` | `tools/schema_personal.py:20` | 无（默认 `data/personal.db`） |
| `WEATHER_CITY` | `api/server.py:1944`、`cli/session.py:189` | 无（Open-Meteo 免 Key） |
| `ASR_ENABLED` / `ASR_LANG` / `ASR_PORT` / `ASR_IDLE` / `ASR_SERVE_BIN` | `tools/voice_asr.py:43,141,167,48,96,59` | 无（有默认） |
| **`ASR_MODEL_DIR`** | **代码从不读** | **死条目** |
| **`RAGFLOW_API_KEY` / `RAGFLOW_BASE_URL`** | **代码从不读** | 已废弃（自建 Chroma），仍在示例注释区 |

#### （b）内置代码读、但**不在** `.env.example` 的键

| 分组 | 变量 | 读取点 | 归类 |
|---|---|---|---|
| **测评（含密钥）** | `PROBE_LLM_API_KEY` `PROBE_LLM_BASE_URL` `PROBE_LLM_MODEL` `PROBE_LLM_JUDGE_MODEL` | `tools/probe_agent.py:67,66,65,64` | **必补** |
| 测评（参数） | `PROBE_LLM_TIMEOUT` `PROBE_LLM_MAXTOKENS` `PROBE_LLM_RETRIES` `PROBE_ASK_TIMEOUT` `PROBE_GEN_CONCURRENCY` | `probe_agent.py:68,69,91,636`、`tools/probe_runner.py:69` | 补（可选） |
| **仲裁（含密钥）** | `DECISION_MODEL_NAME` `DECISION_MODEL_BASE_URL` `DECISION_MODEL_API_KEY` | `tools/decision_router.py:96,100,104` | **必补** |
| 仲裁（参数） | `DECISION_TRIGGER` `DECISION_TIE_THRESHOLD` `DECISION_TOP_K` `DECISION_TIMEOUT_S` `DECISION_MAX_RETRIES` `DECISION_CACHE_ENABLED` `DECISION_CACHE_TTL_H` `DECISION_BUDGET_PER_DAY` `DECISION_BUDGET_PER_SESSION` `DECISION_ALLOW_NONE` | `decision_router.py:76,83,90,109,116,122,208,132,139,127` | 补 |
| 仲裁（内部） | `DECISION_AUDIT_PATH` `DECISION_CACHE_PATH` `DECISION_AUDIT_SEEN` | `decision_router.py:32,33,168` | 可选/内部 |
| 运维 | `AGENT_ERROR_LOG` `TOOL_CALL_LOG` `ZX_STREAM` `ZX_ASSEMBLY_SOURCES` `ZQK_DESKTOP_PORT` `ZX_DESKTOP_LOGDIR` | `api/server.py:155,345,233`、`api/monitor.py:16`、`front/desktop/app.py:1328,43` | 补（可选） |
| 向量/路径 | `ASR_SERVE_BIN`（可执行文件路径） | `tools/voice_asr.py:59` | 补（或在说明里写清） |
| 动态名 | `<PRICE_SOURCE_SECRET_ENV>` | `tools/price_source.py:175`（名存库、值走 env） | **已是正确范式** |
| 孤儿 | `ZX_TOOL_ROUTE_PROVIDER` | 仅 `tests/live/m5c_decision_ab.py:79` 写，**生产无读点** | 别名不统一 |
| 标准/POSIX | `NO_COLOR` · `APPDATA` `WINDIR` `COMSPEC` 等 | `cli/ui.py:31` 等 | 不入 |
| 脚本区 | `CDP_PORT` `APP_URL` `APP_PWD` `OPENROUTER_API_KEY` | `scripts/cdp_*.py`、`research/*-demo/jev_demo.py` | 不入主示例（应明确标注） |

#### （c）「内置部分零遗漏」结论（回答"有没有连 `.env` 都没进的外部键"）

**做法**：对内置部分（`agent/ api/ cli/ tools/ utils/ rag_knowledge/ scripts/ front/desktop/ prompt/ main.py`，含 `.js`）全量静态清点，并单独处理**传参型**读取 `_env(名字, 默认)`。

**结果：内置部分共读 49 个环境变量**

| 分类 | 数量 | 说明 |
|---|---|---|
| 两个文件都有 | 18 | `LLM_MODEL_MAX` `OPENAI_API_KEY` `OPENAI_BASE_URL` `TAVILY_API_KEY` `MYSQL_*`(8) `EMBEDDING_*`(2) `RERANKER_MODEL_PATH` `ASR_ENABLED` `ASR_LANG` `WEATHER_CITY` |
| 只在 `.env`（示例缺） | 11 | `PROBE_LLM_*`(7) `PROBE_ASK_TIMEOUT` `PROBE_GEN_CONCURRENCY` `PERSONAL_DB_PATH` `MYSQL_USE_PURE` |
| 只在 `.env.example` | **0** | — |
| **两个文件都没有** | **20** | OS 变量（`APPDATA` `WINDIR` `COMSPEC`）· 内部开关（`DS_DEBUG_PROMPTS` `ZX_STREAM` `ZX_ASSEMBLY_SOURCES` `AGENT_ERROR_LOG` `TOOL_CALL_LOG` `DECISION_AUDIT_SEEN`）· ASR 端口/空闲/可执行文件（`ASR_PORT` `ASR_IDLE` `ASR_SERVE_BIN`）· 决策层文件路径（`DECISION_AUDIT_PATH` `DECISION_CACHE_PATH`）· 桌面壳（`ZQK_DESKTOP_PORT` `ZX_DESKTOP_LOGDIR`）· 调试脚本专用（`CDP_PORT` `APP_URL` `APP_PWD`）—— **没有一个是密钥** |

传参型读取：`decision_router.py` 用 `_env()` 读 13 个 —— 3 个两文件都有、3 个属「只在 `.env`」、另 **7 个两文件都没有但全是阈值/开关**（`DECISION_TIMEOUT_S`(默认20) `DECISION_MAX_RETRIES`(2) `DECISION_CACHE_ENABLED`(1) `DECISION_ALLOW_NONE`(0) `DECISION_BUDGET_PER_DAY`(500) `DECISION_BUDGET_PER_SESSION`(200) `DECISION_CACHE_TTL_H`(24)）。

**两条否定性结论**：

1. **代码里没有硬编码密钥** —— `sk-` 形 / `Bearer` / `URL 带凭据` / `key=长字面量` 四组形态扫全仓库（含 `.js`/`.html`/`.yml`/`.json`），**只命中 1 条**且是预览页 `<input value="***">` 占位（假阳性）。
2. **没有第二处「DB 里的值当环境变量名」** —— 全仓库只有 `tools/price_source.py:175` 一处；`price_sources` 表**当前 0 行**（机制未启用）；11 个 `tool_sources` 行的 `config_json` 里没有任何 `*key`/`secret`/`token` 字段。

**那么真正「连 `.env` 都没进」的外部键是什么？** —— **只有账号级 / 来源级那一类（本次调研的排除范围）**：

| 外部密钥 | 现在的家 | 规模（实查，**本轮已定位到具体账号**） |
|---|---|---|
| 账号级模型凭证（`provider_name` / `model_name` / `base_url` / **`api_key` 明文**） | SQLite `llm_providers`（界面填、列表掩码） | **476 行**，其中 **474 行属于 §3.6 的测试账号**（`v3vd_/v3vm_/v3vk_` 等模型配置测试建号），真实账号仅 2 行（账号 3 `尼古喵喵`） |
| API/MCP 来源密钥（`auth.secret`、MCP 的 `env` 块） | `tool_sources.config_json`（明文） | 11 行，当前无密钥字段 |

> **结论**：排除自配 CLI / MCP / API 后，**内置部分没有任何"外部密钥"连 `.env` 都没进**。内置部分的外部凭据只有 4 类（`OPENAI_API_KEY`、`TAVILY_API_KEY`、`MYSQL_*`、测评与仲裁两组密钥），且**都在 `.env` 里**（两组尚未回写示例，即 E1）。唯一"密钥不进 `.env`"的口径是**账号级模型凭证与来源密钥存 SQLite 明文** —— 属 E5 取舍点。
> **顺带发现（本轮新增）**：那 474 行明文密钥**绝大多数是测试账号的遗留** —— 清掉测试账号后，明文密钥存量会降到 2 行，E5 的风险面大幅缩小。

#### （d）不读 env 但同样需要外部信息

| 外部信息 | 现在从哪来 | 全新电脑会怎样 |
|---|---|---|
| 账号级模型凭证 | SQLite `llm_providers`；界面填、列表掩码 | 空表不影响启动；启用自定义模型才需要 |
| API 来源密钥 | `tool_sources.config_json` | 空表，不影响启动 |
| MCP 来源配置（`env` 含明文 `KEY=VALUE`） | `tool_sources.config_json` | 空表；且需本机有 `npx/uvx/python/node` |
| MCP server 脚本路径 | 本机路径，UI 填写 | 全新电脑无此路径 |
| `asr` 可执行文件 | `~/.local/bin/asr.exe`（`uv tool install`）或 `ASR_SERVE_BIN` | 语音输入失败："没找到 `asr` 可执行文件" |
| 向量 / 重排模型目录 | 硬编码默认 `D:\LLM\model\...` | 知识库加载失败 |
| 天气数据源 | Open-Meteo（免 Key） | 正常 |

#### （e）「全新电脑」复现实验（助手独立复跑）

| 实验（关键命令） | 观察到的报错 | 崩在哪 |
|---|---|---|
| 切到无 `.env` 目录 + 相关键置空 → `import agent.llm` | `openai.OpenAIError: Missing credentials. Please pass an api_key … or set OPENAI_API_KEY` | **import 期**（`agent/llm.py:15` 模块级构造） |
| 同上 + 相关键未设（None） | `pydantic_core.ValidationError: model Input should be a valid string [input_value=None]` | **import 期** |
| 只补 `LLM_MODEL_MAX` + `OPENAI_API_KEY` → `import api.server` | **装配成功** | — |
| 项目根目录正常 `import api.server`（基线对照） | **装配成功** | — |

> **结论**：全新电脑的**唯一硬阻断** = `LLM_MODEL_MAX` + `OPENAI_API_KEY`；`OPENAI_BASE_URL` 缺失不报错但会静默打官方端点；其余多为**调用期**才报（DB 工具 / 知识库 / 语音 / MCP）。

#### （f）env 侧疑点

- `.env` 系统性领先 `.env.example`（36 vs 19）：`cp .env.example .env` 会漏掉整块测评 8 键与仲裁 8 键，其中两组含密钥。
- **死条目**：`ASR_MODEL_DIR`、`RAGFLOW_*`。
- **硬编码本机路径当默认值**：`EMBEDDING_MODEL_PATH` / `RERANKER_MODEL_PATH` / `EMBEDDING_DEVICE`。
- **命名前缀不统一**：`ZX_*` / `ZQK_*` / `DECISION_*` / `PROBE_*`。
- **密钥落库明文**：`llm_providers.api_key`、`tool_sources` 的密钥、MCP `env`（仅列表接口掩码），与 `price_sources.secret_env` 口径不一致。
- **占位值不校验**：示例占位原样拷入时 import 能过、调用才 401。

### 3.4 两份宪法的问题（含本轮批注要改写的条目）

**《文件存放规则.md》** —— 待改条目（批注已明确的部分标"批注已定"）：

| 类型 | 条目 | 问题 / 改法 |
|---|---|---|
| 过时 | §二 `docs/` 行 | 版本列表停在 v3.0，缺 v3.1/v4.0 与《版本文档规范.md》；**批注已定**：老版本目录统一口径 = **"存放各种文档"**（不逐一细究），详细规则**自 v4.0 起** |
| 新增 | §二 顶层 | **批注已定**：设计图（常青文档）**放项目根目录**，随版本/路由变化持续维护 |
| 新增 | 原则 | **批注已定**：**docs 是纯文档信息，不应被项目依赖**（被前端链接的副本放 `front/`） |
| 过时 | §二 `agents_docs/` 行 | 缺 `ACTIVE_VOICE`（代码在用）；**批注已定**：**漏记的都要补上**（以代码实际读写为准逐项补登）；补 SOUL 生效源 = `ACTIVE_SOUL` → `SOUL/*.md`，孤立 `SOUL.md` 判为残留垃圾 |
| 变更 | §二 backups 行 | 只定义"改用户**数据**前"；**批注已定**：留存期 = **该大版本 git 提交完成后即删除**（同时解决"文档快照"口径与入库不一致） |
| 过时 | §五 不入库清单 | 缺 `docs/<版本>/backups/`（v3.0 已入库）；缺 demo 内 `__pycache__`；缺设计图与 `docs/*.html` 口径 |
| 缺失 | §一 / §三 | "不放临时提取文件"无判据（`*.tmp` 一律禁？限目录？）；`.env.bak_*` 归属空白 |
| 缺失 | §三 判据速查 | 答不出：`交接文档.md`、版本级 `Verdict/00-*`、设计图 html/json、`docs/` 内嵌截图、学习笔记、**机器生成目录（浏览器 profile / 向量库 / 沙箱快照）**、`probe_sets`（账号级 vs 基准） |
| 缺失 | §二 / §三 | 无"测试账号"口径（命名前缀 / 留存期 / 清理时机）→ 待 §五 第 1、2 点定性后写 |
| 矛盾 | §二 `_runtime` 行 vs 禁令 | 定位写"**面板与 Agent 共用的执行器**"，禁令写"外部不许直接 import" —— **面板就是 api**，自相矛盾 → §3.2(b)② 建议修规则 |
| 纪律空转 | §六 变更记录 | 破例条款只记在变更记录，未回写 §二/§三 判据 |
| **变更** | §二 `rag_knowledge/db/` 行 | 原文"含路由基准的临时库，**保留**" → 按批注（用户裁定**全删**）改为：路由基准临时库属测试产物，复核后即可清理（126 个已清） |
| **新增** | §二 `pic/` 行 | 批注已定：`pic/<账号>/*.png` = 用户自设**背景图**；`pic/<账号>/chat_images/*.png` = **聊天中发出的图**；**空目录是合法状态，不得因"空"而删**（`pic/.gitkeep` 已保底） |
| **确认** | `docs/README.md` | 批注已定：**继续承担**"文档体系索引 + 全树导航"职责 |

**《版本文档规范.md》** —— 与实际 v4.0 结构吻合；缺"引用计数 / 状态字段同步清单"（§2.4 漂移根因）；**批注已定**：需补写 README 的新角色（版本收尾的**用户向功能介绍**）与"决策字眼只进 Verdict"的边界。

### 3.5 全量目录树普查（完整递归）

**产出**：附件 `04-附录-全量目录树与逐目录职责.md` —— §一 逐目录职责表、§二 完整递归目录树（含折叠）、§三 覆盖率统计、§四 普查发现。

**规模**（普查于前置清理**之前**）：根下 15 文件 / 15 子目录（清理后为 11 文件 / 16 子目录）；遍历覆盖 **378 个目录**（宪法登记 28 · 部分登记 38 · 未登记 312）；其中 **79 个机器生成的深层子树**（439 文件 / 86 子目录）折叠为一行 + 计数。

**相对"侦察"的增量发现（5 条）**：

| # | 发现 | 为什么算问题 |
|---|---|---|
| 1 | `data/desktop_webview/` 是一整份 **WebView2/Edge 用户 profile**（399 文件 / 81 子目 / **143.5MB**） | 机器生成；宪法与判据速查都没提（既没说可删，也没给清理办法） |
| 2 | `rag_knowledge/db/` 下 **131 个账号目录**（清理后剩 **4 个**：3 个真实账号 + `capability_pool_3`），每个一套 Chroma | 宪法只写"路由基准临时库**保留**"，**没有留存期 / 清理判据** —— 已按批注收口：**基准临时库属测试产物，复核后即清理**（126 个已清） |
| 3 | `data/sandbox/` 里混着工具自身配置与怪目录：`.wechat-cli/`、`.weread-cli/`、`~/` | 三者都未登记 |
| 4 | `data/probe_sets/`（账号级题集）与 `tests/fixtures/probe_sets/`（基准）**同名形不同家**；`tests/fixtures/m5_probe_bin/` 未登记 | 易混、规则未覆盖 |
| 5 | `data/` 根级错放 3 个 `m5b_pressure_result*.json`；`data/tts_ref/` 是空目录 | 产物错位与空壳目录 |

### 3.6 已修复：测试脚本曾向真实库 / 真实目录写入（前置清理暴露的根因）

> 前置清理执行后立刻复核，发现**删掉的东西会再长出来** —— 逐一定位到五处**写真实环境的测试代码**。**不修这一层，任何存量清理都会被下一次测试重新污染**（这正是"上次没清干净"的根因）。
> **修复结果（2026-10-13，前置处理阶段完成）**：五处全部改为「自建自清」——
> ① `tests/unit/test_vision_flag.py` 补 `atexit` + `purge_account` 回收自建账号（此前注释写"用例自清"但**实际没有清理代码**）；
> ② `tests/unit/test_probe_runner.py` / `test_probe_isolation.py` 追加 `atexit` 删除临时用户目录（`_probe_selftest_user` / `_probe_selftest_profile`）；
> ③ `tests/live/m6b_score_import.py` 的旧基线备份改落 `tests/test_out/`（不再写进入库目录 `tests/fixtures/`）；
> ④ 两个泄漏型真机探针沿用 `purge_account` 回收；⑤ **八个默认/硬编码账号为 `m2tester` 的真机、调试与 TestClient 脚本**改为「自建临时账号（`t_*` 前缀）+ 退出回收」（`m35_attrs_test` · `m4_rest_test` · `m4_engine_test` · `_verify_kb_e2e` · `m1_context_e2e_test` · `m2_assembly_e2e_test` · `m3_smoketest` · `m3_kb_e2e_test`）；
> ⑥ **四个直接写死真实账号的 TestClient 脚本**（`m4a_skills_e2e` / `m4b_cli_e2e` / `m4c_cli_e2e` / `m4_tutorial_e2e`，原件 `USER = "尼古喵喵"`，`m4c` 连第二个账号也用 `灵康科技有限公司`）改为**自建临时账号 + 退出回收** —— 这直接违反"e2e 冒烟绝不在真实账号上做写操作"（裁定 31）；
> ⑦ **`purge_account` 补上文件侧**（`tools/schema_personal.py`，40 个调用点全在测试里）：此前只清库行、**账号目录会留下**（实测每跑一次就留空目录）→ 新增 `is_test_account()` **前缀白名单安全阀** + `_purge_account_files()`，清 5 个存放点目录与题集 json，**真实账号名一律不碰**。
> **二次验证（19:4x）**：`tests/testclient/m4_tutorial_e2e.py` 跑完 **35 项全过**，临时账号连同其目录一并回收（5 个存放点只剩真实账号）；`tests/unit` **626 passed / 1 skipped**；跑后 `accounts` 6、`llm_providers` 2、**零新增目录**。
> **验证读数**：`tests/unit` 跑完 **626 passed / 1 skipped**；**跑后 `accounts` 仍为 6、`llm_providers` 仍为 2、`agents_docs` 仍只有 3 个真实账号、无 `_probe_selftest_*` 目录** —— 同样一轮在修复前会新增 3 个账号 + 6 行 + 2 个目录。

| 源头 | 写什么 | 证据 |
|---|---|---|
| `tests/unit/test_vision_flag.py:33` | **直接 INSERT 真实库 `data/personal.db` 的 `accounts`**（`('v3v<tag>_<毫秒>','x','user')`），**无 teardown** | 每次跑 `tests/unit` 多 3 个 `v3v*` 账号 + 6 行 `llm_providers`；**已删的 237 个 `v3v*` 与 474 行 = 79 次单测 × (3, 6)**，数字完全吻合 |
| `tests/unit/test_probe_*.py`（探针离线自测） | 真实 `agents_docs/_probe_selftest_user/`、`data/sandbox/_probe_selftest_user/`（含 `probe_run_*.json` + `profile_backup_*`） | 清理后跑一次单测（19:30:09）即重建 |
| `tests/live/m6b_score_import.py:116` | 在**入库目录** `tests/fixtures/` 写 `m5b_probe_baseline.json.bak<时间戳>` | 现有 `.bak1791212387` / `.bak1791212416`（已移出） |
| `tests/live/asr_endpoint_probe.py` / `stream_ws_e2e.py` | 真机探针建 `bk_asr_*` / `bk_stream_*` 账号（有回收逻辑，**失败即泄漏**） | 脚本日志自写"回收失败可手动清理" |
| `scripts/cdp_auth_probe.py` / `tests/testclient/auth_split_e2e.py` | 建 `尼姑喵喵_*` 错别字探针账号 | 已被删的 `尼姑喵喵` 即此来源（故用户不记得存在过该账号） |

**收口方向**（→ D11）：单测与探针一律改用**临时库 / 临时目录**，或自建自清；命名前缀统一（D6）。
**同类待办**：若干真机/调试脚本的默认账号写成已删的 `m2tester` —— `tests/live/m35_attrs_test.py`、`m4_rest_test.py`、`tests/debug/m4_engine_test.py`、`_verify_kb_e2e.py`、`tests/testclient/m1_context_e2e_test.py`、`m2_assembly_e2e_test.py` → 需改默认账号（实现期）。

---

## 四、建议（等用户口令；调研期只出建议、不写方案）

### 4.1 文档架构方向

| # | 方向 | 说明 / 代价 |
|---|---|---|
| **D1** | **修宪（按批注合并改写）**：《文件存放规则.md》§一（加设计图常青文档）· §二 `docs/`（老版本统一口径"存放各种文档"、自 v4.0 起详细）· §二 `agents_docs/`（补漏记项 + SOUL 生效源）· §二 `backups/`（版本提交后即删）· §五 不入库清单；`版本文档规范.md` 补 README 新角色与"决策字眼只进 Verdict" | 最便宜、收益最直接；按批注清单一次改完 |
| **D2** | **文档角色表进宪法二**：把 §3.1(a) 那张**已按批注重定义**的表正式化 | 中等；把"每类记录的角色靠惯例"变成成文 |
| **D3** | **状态同步清单**：README 退出版本内同步后，真值只在 **Verdict（决策）+ 规划文档（方案）+ Done（过程）**；状态供冷启动由 `交接文档` 承担。配"引用计数必须与实际条数一致"的检查项 | 中等；直治 §2.4 根因 |
| **D4** | **设计图归位**（批注已定）：移入项目根目录并作为**常青文档**持续维护；旧的同名图按"单一现行版"处理（删除或标注历史） | 小 |
| **D5** | **存量巡检机制**：宪法增"定期巡检 + 存量纠正"一节（本轮**前置清理已一次性做完**：测试账号与 RAG 数据、根目录违规文件；余项见 D8 / D11） | 小-中 |
| **D6** | **测试账号口径**：命名前缀（建议 `t_` 或 `zz_` 前缀 + 日期）、留存期、清理时机；**并加硬约束：单测/探针不得向真实库建号，用完必须自清**（根因与三处源头见 §3.6） | 小-中；不修这条，清理会被单测反复重新污染 |
| **D7** | **两处依赖收口**（§3.2(b) 已给结论）：`agent→api.monitor` **改代码**（调用方必传 emit，单向化）；`api→tools._runtime` **建议改规则一行**（承认 api 是面板层） | 小；两处都不动运行链路 |
| **D8** | **代码资产归位**（**已完成，2026-10-13 前置处理**）：`tools/backfill_reasoning_answer.py` → `scripts/`（`git mv` 保历史）；`tests/test_out/*.py`(8) → `tests/debug/`；`data/m5b_pressure_result*.json`(3) → `tests/test_out/` | 已完成 |
| **D9** | **机器生成目录的留存口径**：`data/desktop_webview/`（143.5MB 浏览器 profile，可随时删、随用重建）；`data/sandbox/<账号>/profile_backup_*`；`data/tts_ref/` 空目录；`rag_knowledge/db/` 的基准临时库（已按批注清理：属测试产物） | 中；直接关系磁盘占用 |
| **D10** | **老版本 docs 口径**（批注已定，并入 D1）：v1.0/v2.0/v2.5/v3.0/v3.1 统一标"存放各种文档"，不再逐一维护 | 极小 |
| **D11** | **修掉「测试写真库 / 写实时目录」的污染**（§3.6 五处源头）：改用临时库 / 临时目录或自建自清；并改掉默认账号为已删 `m2tester` 的 6 个真机/调试脚本 | **已完成（前置处理阶段，2026-10-13）**，验证读数见 §3.6 |

### 4.2 外接配置方向

| # | 方向 | 说明 |
|---|---|---|
| E1 | **补必补条目**：`PROBE_LLM_*`（含 KEY）与 `DECISION_MODEL_*`（含 KEY）进 `.env.example`；连同 `PERSONAL_DB_PATH`、`MYSQL_USE_PURE` 等**共 11 个"只在 `.env`"的键**回写示例 | 收益最大：`cp .env.example .env` 即可跑测评与仲裁 |
| E2 | **补运维/阈值条目**（或写明"内部开关，可不填"）：`AGENT_ERROR_LOG` / `TOOL_CALL_LOG` / `ZX_STREAM` / 桌面端口 / 7 个 `DECISION_*` 阈值 / `ASR_SERVE_BIN` | 小 |
| E3 | **标注废弃**：`ASR_MODEL_DIR`、`RAGFLOW_*` 移入注释区或删除 | 小 |
| E4 | **默认值去本机化**：`EMBEDDING_MODEL_PATH` / `RERANKER_MODEL_PATH` 改"必填提示"；`EMBEDDING_DEVICE` 明确 CPU 兜底 | 中；会动代码默认值 |
| E5 | **密钥落库口径统一**：是否把 `llm_providers.api_key` / `tool_sources` 密钥改"名入库、值走 env"（照 `price_sources.secret_env`）。**注：清掉测试账号后明文密钥仅剩 2 行，风险面大减** | **需用户取舍** |
| E6 | **占位值校验**：示例用明显非法占位，启动自检区分"未配置"与"配错" | 小-中 |
| E7 | **脚本区变量标注**：`APP_PWD` / `CDP_PORT` / `APP_URL` / `OPENROUTER_API_KEY` 写明"只给 `scripts/cdp_*.py` 与调研 demo 用" | 小 |

---

## 五、仍需你明确的点

### 5.1 查证纠偏（已按证据处理，请确认）

| 项 | 你原批 | 查证结果 | 我的处理 |
|---|---|---|---|
| `data/sandbox/public/` | 删除 | 它是 `tools/_runtime/shell_runtime.py:48` 的**匿名兜底沙箱**（`ANON_USERNAME = "public"`，目录里自带说明 `README.md`），删掉会被代码按需重建 | **保留**（属代码资产，不是测试残留） |
| 孤立 `SOUL.md` | 脱离 `SOUL/*.md` 的是残留垃圾 | **只有 `尼古喵喵` 那份成立**（其 `ACTIVE_SOUL` = `SOUL/活泼女仆.md`）；**`药喵` 的 `ACTIVE_SOUL` 就指向顶层 `SOUL.md`，是生效人格** | 删前者、留后者；宪法写清 `ACTIVE_SOUL` 可指向两种形态 |

### 5.2 仍需你拍板

| # | 取舍点 | 助手倾向 |
|---|---|---|
| 1 | **E4** 向量 / 重排模型默认值去本机化（现默认 `D:\LLM\model\...`） | 改"必填 + 明确报错"，本机用 `.env` 覆盖 |
| 2 | **E5** 密钥落库口径统一（清理后 `llm_providers` 明文**只剩 2 行**，正是动手时机） | 改"名入库、值走 env"（照 `price_sources.secret_env`） |
| 3 | **D7** 两处依赖怎么收（改代码 vs 改规则） | 见 §3.2(b)：`agent→api.monitor` 改代码（注入式）；`api→tools._runtime` 改规则一行 |
| 4 | ~~D8 / D11~~ **已于前置处理阶段完成**（存量归位 + 修掉"测试写真库"），不再需要排期 | — |

## 六、第二轮调研：三项新议题（用户 2026-10-13 指定，正式调研期）

> 用户原话（留档）：「正式进入调研期，不是定方案，之前的调研属于在进行项目当前信息核对并发现的需要前置处理的项，请进行以下正式调研：
> 1. 当前用户数据存放相关的文件夹散落在项目各处，pic、output、data 下都有用户账号数据，能不能把它们归到一个统一的文件夹下管理；
> 2. 结合之前完整清理一整个账号的经验，开发新功能，新增一个「设置」模块，一键账号注销，可以在前端的「设置模块」点击注销账号，附带的，在「设置」处新加账号绑定 QQ（邮箱）功能，如果注销账号，需要账号密码和 QQ 邮箱传的验证码；
> 3. 在「设置处」新增一个表单，对于之前在 .env 调研的结果，所有当前没有在 .env 写入的开关类放在表单，用户可以自由开关。」
>
> 编排说明：本规范要求**一个大点对应一份 research 文档**（`版本文档规范.md` §三），故三项议题并入本篇（编号六）。本轮取证全部为只读探测（读代码 / 读库 / 列目录），未改任何代码或数据。

### 6.1 议题一：账号数据散落 —— 能否统一归口

#### （a）现状：账号级存放点实测共 7 处

| # | 存放点 | 内容 | 规模（实测） | 路径构造点 |
|---|---|---|---|---|
| 1 | `agents_docs/{账号}/` | SOUL.md / MEMORY.md / ACTIVE_SOUL / SOUL/*.md 人格库 / skills/*.md | 7 / 6 / 9 个文件 | `tools/user_doc_paths.py`（已收口为唯一事实源）；另有 `api/customize.py:25`、`api/voice_tts.py:111`、`cli/cmd_chat.py:28` |
| 2 | `output/{账号}/` | 周报、对话导出稿、测评打分表、语音包（tts 子目录） | 尼古喵喵 51 文件 8.4 MB；灵康 21 文件 9.5 MB | `agent/digest_engine.py:62`、`api/server.py:1005/1017/1049`、`tools/probe_runner.py:36/979/1002`、`api/voice_tts.py:39` |
| 3 | `pic/{账号}/`（内含 `chat_images/`） | 自定义背景图、聊天图片 | 尼古喵喵 14 文件 13.0 MB | `api/customize.py:24/58`、`tools/chat_images.py:139` |
| 4 | `rag_knowledge/db/{账号}/` | `chroma/`、`chroma_route/`、`store.json`（知识库向量库） | 尼古喵喵 4 文件 1.7 MB | `rag_knowledge/kb_service.py:35`、`kb_store.py:3` |
| 5 | `data/sandbox/{账号}/` | 该账号的命令执行沙箱（含 `.git`、`.wechat-cli`、`.weread-cli`、probe_run_*.json、profile_backup_*） | 尼古喵喵 32 文件 290 KB | `tools/probe_runner.py:37`、`tools/_runtime/shell_runtime.py:42` |
| 6 | `data/probe_sets/{账号}.json` | 该账号的测评题集（裁定 13：产品数据，不入库） | 2 个文件 22 KB | `tools/probe_agent.py:22` |
| 7 | `data/tts_ref/{账号}/` | 音色克隆参考音频 | 灵康（空目录） | `api/voice_tts.py:35` |

非账号级但同属"用户数据"一摊：`data/personal.db`（27 张账号域表）、`data/audit/*.jsonl`（4 类审计流，含账号归属列）、`data/desktop_webview/`（146 MB，桌面壳 WebView 缓存，无账号维度）、`data/cli_docs_cache/`（全局缓存）。

#### （b）关键实证：账号级目录清单在代码里存在**三份互不一致的副本**

| 位置 | 清单内容 | 项数 |
|---|---|---|
| `tools/probe_runner.py:74` `ARTIFACT_DIRS` | agents_docs / output / pic / data/sandbox | 4 |
| `tests/live/m5_probe_e2e.py:234` `_ACCOUNT_DIR_ROOTS` | 上述 4 项 + data/tts_ref + rag_knowledge/db | 6 |
| `tools/schema_personal.py:55`（2026-10-13 前置处理期新增） | agents_docs / output / pic / rag_knowledge.db / data/sandbox + data/probe_sets/{账号}.json | 5 目录 + 1 文件 |

→ 要清一个账号的数据，得先知道"一共几处"；现在这个答案在代码里有三个版本，且**第三份漏了 `data/tts_ref/`**（本助手昨日所加，属已知残留缺陷，待口令修）。

#### （c）归并的硬约束（逐条带证据，都是"直接搬家会断"的项）

1. **`/pic` 有 HTTP 静态挂载**：`api/server.py:2186` 把 `pic/` 挂在 `/pic` 上，图片 URL 形如 `/pic/{账号}/{文件名}`（前端背景图与聊天图都走这个 URL）。物理搬家会改 URL，需同步前端与任何已保存的 URL。
2. **库里存的是相对路径**：`digest_reports.md_path` 实测值为 `output\尼古喵喵\快讯周报_x.md`（34 行）。搬 `output/` 必须同步改写这 34 行，或保留一条兼容读取路径。
3. **桌面壳引用 `output/`**：`front/desktop/app.py:664`（打开输出目录）、`:78`（下载目录候选）。搬家要同步改桌面壳。
4. **`.gitignore` 按现路径逐条忽略**：37 行 `agents_docs/`、40 行 `output/`、41 行 `pic/`、54 行 `rag_knowledge/db/`、59 行 `data/desktop_webview/`。搬家要同步改忽略规则（并确认新路径被忽略，否则用户数据会进仓库）。
5. **规则文档写死了现路径**：`docs/文件存放规则.md:38`（`agents_docs/{账号}/` 的职责定义）、`:86`（不入库清单含 `data/`、`agents_docs/`、`output/`、`pic/`、`rag_knowledge/db/`、`tests/test_out/`）。
6. **测试按现路径断言**：如 `tests/unit/test_probe_agent.py:216`（断言落在 `data\probe_sets`）、`tests/unit/test_probe_runner.py:595-596`、`tests/testclient/m2_assembly_e2e_test.py:91`。归并要连带改测试。
7. **`output/` 是四类产出的混合目录**：周报（`agent/digest_engine.py:252`）、对话导出稿（`api/server.py:1017/1049`）、测评打分表（`tools/probe_runner.py:979/1002`）、语音包（`api/voice_tts.py:39`，含 `tts/` 子目录）。归并前要先回答"它们是同一种用户数据吗"。
8. **反面先例**：本机绑定绝对路径已存在（`api/voice_tts.py:28` 指向 `D:\code\...\voice_test`）—— 说明"路径散落"在本项目是已被认知的结构风险，而不是个别疏漏。

#### （d）已有成功样板：`agents_docs` 的收口

`tools/user_doc_paths.py` 的文件头注释写明收口理由：同一个文件被三处各拼一遍，只要有一处写法不一致就会出现"写进 A、读的是 B"，并且记录过 `agents_docs/agents_docs/{账号}/` 那次事故（`agent/build_context.py:175-179` 也有记载）。这是本项目里"路径收口"的现成模板与成功案例。

#### （e）候选方向（调研期只列方向，不拍板）

| 方向 | 做法 | 收益 | 代价/风险 |
|---|---|---|---|
| 甲 物理归并 | 新建统一顶层目录（如 `users/{账号}/`），7 处搬入 | 一眼看清一个账号的全部资产；清理/迁移/备份只有一个入口 | 需一次性迁移脚本；库内相对路径改写（34 行）；`/pic` URL 变更；`.gitignore`、规则文档、测试、桌面壳同步；迁移期双写风险 |
| 乙 逻辑归口（门面） | 磁盘路径不动，新增"账号数据路径唯一事实源"模块（照 `agents_docs` 样板），所有模块必须来问它 | 风险最低、可立即消掉 6.1(b) 的三份清单；为将来物理归并铺路 | 不解决"散落"的表象；需要逐个改造现有拼路径处 |
| 丙 局部归并 | 只归并"纯用户产出"（output/pic/sandbox/probe_sets/tts_ref），`agents_docs` 保持现状（已收口）、`rag_knowledge/db` 留在知识库子系统 | 兼顾收益与风险，避开知识库/人格库两处高耦合区 | 顶层仍有两个"账号维度"的根，需明确边界 |

#### （f）议题一待拍板

1. 是否物理归并（还是只做逻辑归口）；
2. 若归并，统一顶层的目录名与层级；
3. 旧路径是否保留兼容（双读 / 软链接 / 一次性迁移后彻底切换）；
4. `rag_knowledge/db`（知识库向量）与 `agents_docs`（人格记忆）是否纳入同一次归并；
5. 迁移是否按本机惯例"先备份再动手"（备份落 `docs/v4.0/backups/`）；
6. 是否顺带统一 6.1(b) 的三份清单，并把 `data/tts_ref` 补进清理函数。

### 6.2 议题二：设置模块（一键注销账号 + 绑定 QQ 邮箱 + 验证码）

#### （a）现状事实（全部实测）

| 面 | 现状 | 证据 |
|---|---|---|
| 前端 | 单文件 SPA（`front/index.html` 8628 行），hash 路由；导航页签 7 个：工作台、个人信息/公司信息、AI 助手、知识库、报告、定制助手、技能与工具 | `front/index.html:2078-2086` |
| 前端（设置类 UI 先例） | 已有"背景设置面板"浮层（图库选择 + 上传 + 删除 + 透明度），不是独立页签 | `front/index.html:2094-2096` |
| 后端命名空间 | `/api/me/*` 已存在，注释即"用户数据自主管理" | `api/server.py:1059`；`/api/me/overview`、`/api/me/profile`、`/api/me/products` 等 |
| 现有账号接口 | 只有 4 个：注册、登录、登出、资料读写。**没有注销、没有邮箱、没有验证码** | `api/server.py:597/607/612/1064/1068` |
| 鉴权 | token 制（登录返回 token，`/api/logout?token=`），会话在 `sessions` 表 | `api/account.py:95` `get_session`、`:114` `logout`、`:182` `login` |
| 口令 | `sha256(_SALT + 明文口令)` | `api/account.py:158-160` |
| 账号表 | 8 列：`id/username/password_hash/role/display_name/created_at/digest_auto/decision_router` —— **无 email 列** | `PRAGMA table_info(accounts)` 实测 |
| 库表总数 | 27 张，**没有任何验证码/邮件相关表** | 实测 |
| 邮件能力 | **全仓没有** smtplib / SMTP / 发信代码；`pyproject.toml` 依赖仅 fastapi、requests 一类 | 全仓 grep 实测 |
| 本机现成通道 | `qqmail.exe`（QQ Agent Mail CLI，`C:\Users\ZQK\.local\bin\qqmail.exe`，实测在 PATH）；另有 `agently-cli` 技能（QQ 邮箱操作） | `shutil.which` 实测 |
| 已有清理能力 | `tools/schema_personal.py:34` 起 `purge_account`（库行 + 文件侧 + 测试账号名前缀安全阀，2026-10-13 扩展）；整账号删除实操记录在 `docs/v4.0/backups/cleanup_*` | 昨日前置处理 |

#### （b）缺口清单（新功能需要、当前都没有）

1. 注销接口（`/api/me/delete` 一类）；
2. 邮箱绑定关系与验证码（生成 / 存储 / 校验 / 过期 / 重发）；
3. 发信通道（项目内零基础）；
4. 前端"设置"页；
5. 注销的数据处置策略（导出？保留？冷却期？）；
6. 注销与 token / `sessions` 表的联动失效；
7. 大资产处置：`output/{账号}/`（周报、导出稿、语音包）与 `rag_knowledge/db/{账号}/`（知识库向量）；
8. 注销动作的审计落盘（`data/audit/`）；
9. 验证码频率限制与风控。

#### （c）硬约束与项目既有偏好（会直接决定方案形态）

1. **真实账号只读**：既有裁定要求 e2e 冒烟绝不在真实账号上做写操作 —— 注销功能必须先用临时账号端到端验证（本项目已有成熟做法：自建 `t_*` 临时账号 + 退出回收）。
2. **凭据口径**：发信凭据要么进 `.env`（并沿用"回传只给模型名、不含 base_url/key"的口径），要么复用 `qqmail` CLI 自己的配置；不得把明文授权码落进库。
3. **配置导向**（用户既有偏好，曾明确异议"CLI 接入写成内置厂商注册表"）：邮箱通道要逐字段可配（SMTP 主机/端口/账号/授权码），不要写死"QQ 邮箱"一家。
4. **不可逆操作的防线**：验证码 + 密码双验证是用户已定的；改用户数据前先备份是本机惯例。
5. **语义确认**：`purge_account` 目前是**测试支持函数**（40 个调用点全在测试里），生产注销若复用它，需要先确认"删号"还是"停用"。

#### （d）议题二待拍板

1. 注销语义：硬删（物理清除）还是软删（停用并保留数据 N 天）；
2. 数据处置：聊天记录、知识库向量、周报/导出稿、聊天图片是否一并删；是否提供"先导出再注销"；
3. 验证码规则：有效期、重发间隔、存储位置（新表）、失败次数上限；
4. 绑定规则：一个邮箱能否绑多个账号；是否允许换绑；注销时是否要求"输入邮箱 == 绑定邮箱"；
5. 发信通道选型：直连 SMTP（新增 `.env` 键）还是 shell 调 `qqmail.exe`；
6. 前端位置："设置"作为第 8 个导航页签，还是并入"定制助手"；"背景设置"浮层是否一并迁入；
7. 是否需要注销冷静期与操作审计。

### 6.3 议题三：把未进 `.env` 的"开关类"做成设置表单

#### （a）现状：三份清单规模（实测）

| 口径 | 数量 |
|---|---|
| 生产代码引用环境变量 | 59 个键名 |
| `.env` 实际键 | 36 |
| `.env.example` 实际键 | 19 |
| 只在 `.env`（示例缺） | 17 |
| 只在示例（`.env` 缺） | 0 |
| **代码引用但 `.env` 里没有** | **24** |

#### （b）24 个"未进 `.env`"按键性分四类

| 分类 | 键 | 默认值 / 取值 | 读取点 |
|---|---|---|---|
| 开关类（布尔，正是议题三的目标） | `DECISION_CACHE_ENABLED` | 1（开） | `tools/decision_router.py:122` |
| | `DECISION_AUDIT_SEEN` | 1（开） | `tools/decision_router.py:168` |
| | `DECISION_ALLOW_NONE` | 0（关） | `tools/decision_router.py:127` |
| | `ZX_STREAM` | 1（开，流式输出） | `api/server.py:345` |
| | `ZX_ASSEMBLY_SOURCES` | 非 `legacy` 即新实现 | `api/server.py:233` |
| | `DS_DEBUG_PROMPTS` | 调试用，现为注释态 | `agent/prompts.py:32` |
| 路径类 | `PERSONAL_DB_PATH` | 默认库路径 | `tools/schema_personal.py:20` |
| | `DECISION_AUDIT_PATH` / `DECISION_CACHE_PATH` | 默认审计/缓存路径 | `tools/decision_router.py:32-33` |
| | `ASR_SERVE_BIN` | ASR 可执行 | `tools/voice_asr.py:59` |
| | `AGENT_ERROR_LOG` | 默认错误日志路径 | `api/server.py:155` |
| | `TOOL_CALL_LOG` | 默认工具调用日志路径 | `api/monitor.py:16` |
| 数量/阈值类 | `DECISION_TRIGGER` / `TIE_THRESHOLD` / `TOP_K` / `TIMEOUT_S` / `MAX_RETRIES` / `BUDGET_PER_DAY` / `BUDGET_PER_SESSION` / `CACHE_TTL_H` | near_tie / 0.0005 / 3 / 20 / 2 / 500 / 200 / 24 | `tools/decision_router.py:76-208` |
| | `ASR_PORT` / `ASR_IDLE` | 默认端口与静默秒数 | `tools/voice_asr.py:48/96` |
| 平台/测试类（不应进表单） | `COMSPEC`、`APPDATA`、`NO_COLOR`、`APP_URL`、`APP_PWD`、`CDP_PORT` | 环境与探针参数 | `tools/_runtime/shell_runtime.py:512`、`cli/ui.py:31`、`scripts/cdp_auth_probe.py:37-38` |

#### （c）已在 `.env` 的开关类（表单可直接覆盖）

| 键 | 当前值 | 读取点 |
|---|---|---|
| `ASR_ENABLED` | 1 | `tools/voice_asr.py:43` |
| `MYSQL_USE_PURE` | True | `tools/db_tools.py:32` |

（另有 `DECISION_MODEL_NAME` / `_BASE_URL` / `_API_KEY` 三个键已在 `.env`，属密钥口径那一摊，见 §3.3。）

#### （d）关键结构事实（决定"表单改了怎么生效"）

1. **部分键在导入期读取**：`agent/llm.py:16-18`（模块级常量）、`tools/decision_router.py:32-33`（模块级路径常量）——这类键改完必须**重启**才生效；而函数体内 `_env(...)` 的键是"每次调用读"（如 `tools/decision_router.py:122-208`、`tools/voice_asr.py:43`）。表单若要"点了就生效"，必须逐键确认它属于哪一类。
2. **已有"按账号存开关"的现成先例**：`accounts.digest_auto`、`accounts.decision_router`（账号表里的两个开关列）、`llm_providers` 表（按账号的模型配置）、`digest_subs` 表（订阅设置）。也就是说本项目**已经**把一部分开关放在库里而不是 `.env` 里。
3. 库里**没有** `settings` 表。
4. `.env` 是全局单文件（`.gitignore` 已忽略）；本机已授权助手读写，但按惯例改前要备份为 `.env.bak_<时间戳>`。
5. 项目"配置导向"偏好：表单应逐字段可填（含新增条目），不要写死枚举值。

#### （e）两条载体路线（调研期只列不拍板）

| 路线 | 做法 | 优点 | 代价 |
|---|---|---|---|
| 甲 `.env` 直写 | 表单直接读写 `.env`，重启生效 | 与现状一致、实现简单、运维口径单一 | 全局而非按账号；需重启；并发写文件要加锁 |
| 乙 写库（新 `settings` 表或按账号列） | 代码改成"库优先、`.env` 兜底" | 可按账号、可即时生效、与 `llm_providers`/`accounts` 既有做法同构 | 要改每个读取点（导入期常量那一批尤其麻烦） |
| 混合 | 开关类进库（按账号），密钥与路径类留在 `.env` | 兼顾 | 两套口径并存，需在文档里写清边界 |

#### （f）议题三待拍板

1. 载体：`.env` / 库 / 混合；
2. 作用域："用户可以自由开关"指的是全局开关还是每个账号各自的开关；
3. 生效方式：接受重启，还是要求热生效（后者要改读取点）；
4. 上表单的范围：只开关类，还是含阈值 / 枚举 / 路径；
5. 密钥类是否也进表单（掩码显示 + 落库，还是继续只由 `.env` 管）；
6. 表单入口：与议题二同一个"设置"页，还是放在"定制助手"里的高级区。

### 6.4 议题一方案稿：`user_data/` 统一归口（用户 2026-10-13 拍板方向）

> 用户拍板（留档）：「我想在根目录下新建 `user_data` 文件夹，存放上面排查到的散落的用户数据……这件事比较急，所以需要妥善的移位（排查哪些代码会因为这些文件移位而报错，需要妥善处理）；`data/personal.db` 也算有用户数据，不过暂时就存在 `data` 下吧；`data/tts_ref/` 也是用户数据，也要归到 `user_data/tts_ref/` 下。」
>
> 本节是**方案稿 + 执行步骤**。**2026-10-13 用户拍板后已执行完毕**（用户选定：知识库保持旧层级 `user_data/rag_knowledge/db/`；
> 用户授权助手执行全程）。执行记录见本节末尾「（h）执行记录」。

#### （a）目标布局（旧 → 新）

| 旧路径 | 新路径 | 说明 |
|---|---|---|
| `agents_docs/{账号}/` | `user_data/agents_docs/{账号}/` | 人格 / 记忆 / 技能 |
| `output/{账号}/` | `user_data/output/{账号}/` | 周报、导出稿、打分表、`tts/` 音频 |
| `pic/{账号}/` | `user_data/pic/{账号}/` | 背景图 + `chat_images/` |
| `rag_knowledge/db/{账号}/` | `user_data/rag_db/{账号}/` | `chroma/`、`chroma_route/`、`store.json` |
| `data/sandbox/`（含 `public/`） | `user_data/sandbox/` | 账号命令沙箱 + 匿名兜底沙箱 |
| `data/probe_sets/` | `user_data/probe_sets/` | 账号级题集 |
| `data/tts_ref/{账号}/` | `user_data/tts_ref/{账号}/` | 音色参考音频（用户 2026-10-13 指定纳入） |
| `data/personal.db` | 不动 | 用户指定暂留 `data/` |
| `rag_knowledge/db/chroma.sqlite3`、`capability_pool_3/` | 不动 | 非账号数据（全局） |
| `data/audit/`、`data/cli_docs_cache/`、`data/desktop_webview/`、`data/tutorial_assets/` | 不动 | 非账号级 |

**URL 不变**：`/pic` 仍挂在同一位置（`api/server.py:2186` 用 `cust.PIC_DIR` 构造），只是目录源改为新根 —— 前端与已保存的图片 URL 零改动。

#### （b）同步改造：新增路径唯一事实源 `utils/paths.py`

现状是 7 个根目录常量散在 7 个模块里（`tools/user_doc_paths.py:13`、`api/customize.py:24-25`、`rag_knowledge/kb_service.py:34-35`、`tools/probe_runner.py:36-37`、`tools/probe_agent.py:22`、`tools/_runtime/shell_runtime.py:42`、`api/voice_tts.py:35`），且**账号目录清单存在三份互不一致的副本**（§6.1(b)）。

顺带实证：`tools/chat_images.py:118` 的注释写着「第一版写成 `utils.paths.pic_dir` → 该模块根本不存在」，第 120-139 行至今仍在"探测 helper 模块，探测不到就自己拼"。代码早就在等一个统一路径模块。

方案：新建 `utils/paths.py` 定义全部 7 个根 + `ACCOUNT_DIRS`（唯一账号目录清单）+ `pic_dir(account)` 等 helper；`tools/user_doc_paths.AGENTS_DOCS` 改为转发它（保持既有 API，调用方不必全改）；`tools/chat_images.images_dir()` 的探测分支自然命中该模块。

#### （c）受影响清单（实测：真路径构造/断言共 123 处，38 个文件）

| 类别 | 文件数 | 要点 |
|---|---|---|
| 生产代码 | 16 | `tools/user_doc_paths.py:13`、`tools/readtofile.py:17`、`tools/writetofile.py:15`（文档工具越权校验根）、`api/customize.py:24-25`、`api/server.py:1005`、`api/voice_tts.py:35/39/111`、`api/probe.py:308/318/374`、`agent/digest_engine.py:62`、`rag_knowledge/kb_service.py:34-35`（`kb_store` 经 `user_db_dir` 自动跟随）、`tools/probe_runner.py:36-37/74/443/457/769/979/1002`、`tools/probe_agent.py:22`、`tools/_runtime/shell_runtime.py:42`、`tools/profile_update.py:28`、`cli/cmd_chat.py:28`、`tools/chat_images.py:139`、`tools/schema_personal.py:55/64`（补 `tts_ref`） |
| 桌面壳 | 1 | `front/desktop/app.py:78/664`（打开输出目录） |
| 脚本 | 1 | `scripts/cdp_skill_menu_probe.py:309/310/352` |
| 测试 | 12 | 路径断言与自清目录：`tests/unit/test_memory_paths.py:40`（断言根目录，改造后必须同步）、`test_probe_runner.py:594-596`、`test_probe_isolation.py:181-183`、`tests/testclient/probe_api_e2e.py:60-61/276`、`m2_assembly_e2e_test.py:91`、`tool_agent_e2e_test.py:29`、`tool_file_io_test.py:83/86`、`tests/live/m5_probe_e2e.py:234`、`test_memory_snapshots.py:53`、`test_profile_update.py:50`、`desktop_window_e2e.py:136/170`、`tests/testclient/m4b_cli_e2e.py:91` |
| 配置 | 1 | `.gitignore`：新增 `user_data/`（旧 5 条可留可删） |
| 文档 | 4 | `docs/文件存放规则.md:38/41/86`、`README.md:109` 等目录树、`docs/v4.0/research/04-附录-全量目录树与逐目录职责.md`、`tests/README.md` |

#### （d）数据侧改动（唯一需要改库的地方）

- `digest_reports.md_path` 存的是**相对项目根**的路径（`agent/digest_engine.py:288` 写 `str(md_path.relative_to(_PROJECT_ROOT))`），实测 **34 行**形如 `output\尼古喵喵\快讯周报_x.md`；读取方是 `tools/profile_evidence.py:113-115`（按相对路径打开）。**移库前必须把这 34 行的前缀 `output\` 改成 `user_data\output\`**。
- 全库扫描未见其他表/列存这些路径（`pdf_path` 全为 NULL；`tool_capabilities.invoke_hint` 里的 `D:\LLM\...` 与本议题无关）。
- 沙箱内文件内容含旧路径字样（如 `data/sandbox/药喵/probe_run_*.json` 的 `"data/sandbox/药喵"`）：属**历史记录正文**，是当时事实，不改写。

#### （e）执行步骤（给用户手动执行；或经用户许可后由助手执行）

1. **停服**：关掉正在运行的服务与桌面壳（Windows 上 Chroma 会持有 `rag_db` 下的 sqlite 句柄，不停服搬不动）。
2. **备份**（只读快照，落 `docs/v4.0/backups/user_data_move_<时间戳>/`，该目录不入库）：7 个旧根共约 34 MB；另存一份 `digest_reports.md_path` 的 34 行原值文本。
3. **改代码**：按（c）清单改造 + 新建 `utils/paths.py`（此时服务仍停着，避免"代码找新路径、文件还在旧路径"的窗口）。
4. **搬文件**：按（a）表 `shutil.move`；`pic/.gitkeep` 属入库文件，搬完需在新目录保留一份（`git mv`）。
5. **改库**：34 行 `md_path` 前缀改写（改前比对行数，改后复核）。
6. **起服 + 验证**：见（f）。
7. **收尾**：`.gitignore`、规则文档、目录树附录更新；删除旧空目录；本文件与 `Verdict/` 落记录。

**回滚**：代码 `git checkout` 还原，文件从 `docs/v4.0/backups/user_data_move_<时间戳>/` 搬回，`md_path` 用备份文本还原。

#### （f）验证清单（缺一不可）

| # | 验证项 | 手段 |
|---|---|---|
| 1 | 单测基线 | `tests/unit` 应仍为 626 passed / 1 skipped |
| 2 | 路径契约 | `tests/unit/test_memory_paths.py` 断言根目录已指向新根 |
| 3 | 账号目录清单唯一 | `utils/paths.ACCOUNT_DIRS` 与 `purge_account`、`ARTIFACT_DIRS`、探针清单四处同源 |
| 4 | 图片链路 | 前端背景图与聊天图仍能显示（URL 仍是 `/pic/{账号}/{文件}`） |
| 5 | 周报/导出 | 生成一次周报并下载（`/api/download`），确认新目录可读、库里 `md_path` 可解析 |
| 6 | 知识库 | 入库一次文档（`rag_db/{账号}/chroma` 生效） |
| 7 | 人格/记忆/技能 | 读写 `MEMORY.md`、技能列表可用（文档工具越权校验根已改） |
| 8 | CLI 沙箱 | 面板执行一条白名单命令，cwd 已在新沙箱 |
| 9 | 测评链路 | 题集读取、原始记录落盘、打分表输出 |
| 10 | 残留检查 | 旧位置零残留；`data/` 下不再有账号目录 |

#### （g）风险与对策

| 风险 | 对策 |
|---|---|
| 服务未停导致文件被占用、搬家半途失败 | 第 1 步强制停服；先跑一次 dry-run 打印搬迁映射 |
| 代码改了、文件没搬（或反之）造成"读不到" | 停服窗口内按 3→4→5 顺序一次做完；不接受"先改代码、明天再搬" |
| 34 行库值漏改 → 周报证据链读不到文件 | 改前导出行数、改后复核计数；`profile_evidence` 单独点检 |
| 测试里硬编码旧路径 → 单测红 | 按（c）清单同步改 12 处测试 |
| 迁移后再次出现"清单不一致" | 本次即把三份清单合一（`ACCOUNT_DIRS`），并保留 `test_memory_paths.py` 这类契约测试 |
| 桌面壳/脚本漏改 | 桌面壳"打开输出目录"与 `scripts/cdp_skill_menu_probe.py` 各点检一次 |


#### （h）执行记录（2026-10-13 20:42 ~ 20:53，实际执行）

| 步骤 | 做了什么 | 读数 |
|---|---|---|
| 0 前置 | 确认端口 8123/8124 未监听（用户服务当时未运行）、全仓无 `--reload`（改代码不会打断运行中的进程） | 通过 |
| 1 备份 | `docs/v4.0/backups/user_data_move_20261013_204252/`（7 处 + `digest_reports.md_path` 原值 34 行 TSV） | 33.72 MB |
| 2 搬迁 | 7 处 → `user_data/`（agents_docs 22 文件 / output 73 / pic 16 / rag_knowledge/db 10 / sandbox 45 / probe_sets 2 / tts_ref 0）；`pic/.gitkeep` 在新位置补回 | 168 个文件全部到位 |
| 3 改库 | `digest_reports.md_path` 34 行前缀 `output\` → `user_data\output\` | rowcount=34，残留 0 |
| 4 代码改造 | 新增 `utils/paths.py`（**唯一事实源**）+ 生产代码 17 个文件改问它；桌面壳 2 处、CDP 脚本 3 处、前端文案 3 处；测试 15 个文件同步 | 见下 |
| 5 文档同步 | `.gitignore` 增 `user_data/`（`user_data/*` + `!user_data/.gitkeep`）；`文件存放规则.md` 目录职责表与不入库清单；`README.md` 目录树与现况行；`04-附录` 顶部加迁移说明 | 4 份 |
| 6 验证 | 见下「验证读数」 | 全通过 |

**新增文件**
- `utils/paths.py` —— 账号级用户数据路径的**唯一事实源**（7 个根 + `ACCOUNT_DIRS` + `PROBE_ARTIFACT_DIRS` + 各取路径函数）。顺带补上历史窟窿：`tools/chat_images.py:118` 的注释写着「第一版写成 `utils.paths.pic_dir` → 该模块根本不存在」，现在它存在了，该处探测直接命中。
- `scripts/migrate_user_data.py` —— 一次性迁移脚本（默认 dry-run；`--apply` 执行「备份→搬迁→改库→自检」；`--restore <备份目录>` 回滚）。

**三份"账号目录清单"合并为一处**（§6.1(b) 的根因）
- `utils/paths.py::ACCOUNT_DIRS` 为唯一来源：`tools/probe_runner.py::ARTIFACT_DIRS` 改为由它生成（用测评专用子集 `PROBE_ARTIFACT_DIRS`，**不含 tts_ref / rag db**，避免水位线清理误删音色素材）；
  `tools/schema_personal.py::_purge_account_files` 改为 `account_dirs_of()`（**补上此前漏掉的 `data/tts_ref`**）；
  `tests/live/m5_probe_e2e.py::_ACCOUNT_DIR_ROOTS` 改为引用同一清单。

**过程中单测抓出的真 bug（已修）**：`tools/probe_runner.py::load_case_set` 的题集查找第三候选仍是写死的 `data/probe_sets/`，
搬迁后按名读题集会 `FileNotFoundError` —— 正是"移位会打断哪里"的实例，已改为 `paths.PROBE_SETS_DIR`。

**验证读数（全部实测）**

| # | 验证项 | 结果 |
|---|---|---|
| 1 | 单测基线 | `tests/unit` **626 passed / 1 skipped**（与迁移前一致） |
| 2 | 路径契约 | `test_memory_paths.py` 断言新根 `项目根/user_data/agents_docs` 通过 |
| 3 | 清理日志 | 单测结束打印 `[cleanup] 已删除临时目录 user_data\agents_docs\_probe_selftest_user` 等（说明测试造的数据也在新根、且自清） |
| 4 | 只读真机脚本 | `tests/testclient/m4_tutorial_e2e.py` **35 项通过 / 0 失败** |
| 5 | 进程内端到端（临时账号） | 注册即在 `user_data/agents_docs/{账号}/MEMORY.md` 生成画像；技能保存落 `.../skills/verify_migr3.md` 且列表接口 200；背景图上传落 `user_data/pic/{账号}/`；`/api/skills`、`/api/memory`、`/api/bg/list`、`/api/me/overview` 全 200 |
| 6 | 四个路径函数 | 打分表目录 / 命令沙箱 / 知识库向量 / 背景图根 全部指向 `user_data/` |
| 7 | 账号回收能力 | `purge_account` 返回库行 + `_files` 4 个目录；回收后零残留（**议题二的注销可直接复用**） |
| 8 | 周报证据链 | 改写后的 `user_data\output\尼古喵喵\快讯周报_20261003_034416.md` 可被 `profile_evidence.report_bullets` 解析出 **19 条要点**，文件存在 |
| 9 | `/pic` URL | `GET /pic/尼古喵喵/<jpg>` → **200 / 403653 字节**（前端与已存 URL 零改动） |
| 10 | 数据终态 | 账号表仍 **6 个真实账号**；7 处旧位置全部为空；`user_data/` 下 7 个子根各就各位 |

**尚未做的（需用户起服务后点检，或需模型额度）**
- 真机面板：背景图在前端渲染、周报实际生成一次、"打开输出目录"（桌面壳）、CLI 沙箱面板跑一条命令、测评链路（题集→原始记录→打分表）。
- `04-附录-全量目录树与逐目录职责.md` 的目录树为迁移前快照（已在顶部加说明），待「实现记录」阶段重生成。
- `front/tutorial/previews/*.html` 是生成物、含旧路径字样，重新生成即可（不入库）。

**遗留候选（不在本次范围，供定方案时议）**
- `utils/path_utils.py` 的 `resolve_path` 全仓**无调用方**（死代码，且文件头有重复定义），可另案清理。
- 旧 `pic/.gitkeep` 随目录移走：git 侧会记为一次删除，由用户提交时一并记账（新位置 `user_data/pic/.gitkeep` 因 `user_data/*` 规则不入库，已另建 `user_data/.gitkeep` 占位）。

### 6.5 本轮待用户明确的点（汇总）

| # | 问题 | 归属 |
|---|---|---|
| 1 | **大点名与范围**：本轮三项（尤其议题二的"设置模块"新功能）是否并入大点 04？现名"文档架构优化与外接配置 env 条目优化"装不下议题二，若并入需同步扩写 `v4.0规划文档.md` 与 `docs/v4.0/README.md` 的大点描述 | 编排 |
| 2 | 议题一：**已完成**（7 处归口 `user_data/`、知识库保持旧层级、助手全程代执行）——见 §6.4(h) 执行记录 | 6.4 |
| 3 | 议题二：注销语义与数据处置、验证码规则、绑定规则、发信通道、前端位置 | 6.2(d) |
| 4 | 议题三：载体、作用域、生效方式、上表单范围、密钥是否进表单 | 6.3(f) |

## 七、变更记录

| 日期 | 变更 |
|---|---|
| 2026-10-13 | 建文件；写入任务口径、调研方法、第一手事实；派出三路并行侦察（文档与记录类 / 代码与资产类 / `.env` 外接配置） |
| 2026-10-13 | 合并侦察结论并逐条独立复核；补"全新电脑"四组复现实验；补 env 全量静态清点（49 个读取点 + 传参型 13 个 + 两条否定性结论）；补全量递归普查（378 目录，附件） |
| 2026-10-13 | **去重整理**：删掉被覆盖的旧结论（原 §2.4 与旧疑点 3/4 合并、原疑点 16/17/18 合并或降级、原 §2.5"规则空白"并入 §3.4、"追加调研 (f)"并入 §3.3）；疑点由 18 条压缩为 15 条；目录职责移入附件 |
| 2026-10-13 | **吸收用户批注（11 处）并按新状态整理**：§3.1(a) 角色表**按批注重定义**（决策字眼只进 Verdict / 规划文档只写方案 / 实现记录只记过程 / README 改为版本收尾的用户向功能介绍 / backups 版本提交后即删）；§3.1(b) 疑点表新增"本轮批注后状态"列（9 条已定性，其中 13 条关闭、9 条移交 05 大点）；**新增 §3.2(b) 三处复查结论**（两处依赖可修复性 + backfill 定性）；**新增 §3.6 存量清理全量清单**（301 账号分类 A/B/C、6 个存放点、25 张表、精确文件数与行数）；**新增 §五 待批注清单 8 点**；建议表由 D1~D9/E1~E7 调整为 D1~D10/E1~E7 |
| 2026-10-13 | **执行前置清理（用户裁定后，已完成）**：A 类 294 个测试账号 + B 类 `m2tester` 及其 RAG 数据全量删除 —— 文件层 **160 目录 / 243 文件**（含 126 个路由基准库）、库侧 **863 行**（295 账号行 + 子表 568 行）+ 3 行孤儿行；`pic/3` 的 6 张图并入 `pic/尼古喵喵/chat_images/` 后删空壳；根目录 4 个违规文件清除。清理后 `accounts` 301 → **6**、`llm_providers` 476 → **2**，真实账号数据 1025 行分毫未动，`messages`/`conversations` 不变，`tests/unit` 626 passed / 1 skipped。备份：`docs/v4.0/backups/cleanup_20261013_192644/`（整库 + 文件层 tar + 删除/保留 id 清单 + `root_violations/` + `misc/`） |
| 2026-10-13 | **整理（本轮）**：删掉已解决的前置清理内容（原 §2.2 根目录违规表、原 §3.6 清理清单与执行记录、原 §五 8 点裁定记录）；§2.1 顶层实况换成清理后数字；§3.6 改写为**未解决的污染根因**（五处测试写真实环境的源头）；§五 收缩为"查证纠偏 + 仍需拍板 5 项"；建议表新增 **D11** |
| 2026-10-13 | **前置处理（二）：存量归位（D8）**：`tools/backfill_reasoning_answer.py` → `scripts/`（`git mv`）；`tests/test_out/*.py` 8 个脚本 → `tests/debug/`；`data/m5b_pressure_result*.json` 3 个 → `tests/test_out/`。三处均先核查引用（无任何代码引用）再动 |
| 2026-10-13 | **前置处理（三）：修掉"测试写真库/真目录"污染（D11）**：五处源头全部改为自建自清（`test_vision_flag.py` 补 `atexit`+`purge_account`；两个探针单测加 `atexit` 删临时目录；`m6b_score_import.py` 备份改落 `test_out/`；六个默认账号 `m2tester` 的真机/调试脚本改为自建 `t_*` 临时账号 + 退出回收）。**验证：`tests/unit` 626 passed / 1 skipped，跑后 accounts 仍为 6、llm_providers 仍为 2、无 `_probe_selftest_*` 目录** |
| 2026-10-13 | **前置处理（三·补）**：全仓复查又揪出两个同型脚本 —— `tests/testclient/m3_smoketest.py`（硬编码 `USER, PWD, ROLE`）与 `m3_kb_e2e_test.py`（`--account` 默认 `m2tester`），一并改为自建临时账号 + 退出回收；至此**测试/脚本层再无硬编码的共享测试号**（仅注释与"临时账号前缀识别"正则里留有历史字样） |
| 2026-10-13 | **前置处理（三·再补）**：全仓复查又发现**四个 TestClient 脚本直接写死真实账号**（`m4a_skills_e2e`/`m4b_cli_e2e`/`m4c_cli_e2e`/`m4_tutorial_e2e` 用 `尼古喵喵`，`m4c` 还用 `灵康科技有限公司`）—— 违反裁定 31，全部改为自建临时账号 + 退出回收；并把 `purge_account`（`tools/schema_personal.py`）**补上文件侧清理**（前缀白名单安全阀，真实账号不碰），至此"账号删了、目录还在"的遗留也一并解决。二次验证：m4_tutorial 35/0 且目录被清、tests/unit 626 passed / 1 skipped、零新增 |
| 2026-10-13 | **第二轮调研（正式调研期）**：按用户指定的三项议题开展只读侦察并全部落档——①账号数据散落（实测 7 处账号级存放点 + 代码里三份互不一致的清单 + 8 条归并硬约束，含 `/pic` HTTP 挂载与 34 行库内相对路径）②设置模块（注销 + 绑定 QQ 邮箱 + 验证码：前端 7 个页签无"设置"页、`/api/me/*` 命名空间已备、账号表无 email 列、库内无验证码表、项目零邮件能力、本机有 `qqmail.exe` 可作发信通道）③开关类表单（59 个引用 / 36 个 `.env` 键 / 24 个未进 `.env`，按键性分四类并标出导入期读取的键）。三项议题的待拍板项汇总在 §6.4。**本轮未改任何代码或数据** |
| 2026-10-13 | 编排：三项议题按《版本文档规范》§三"一个大点一份 research 文档"并入本篇（新增 §六），原"六、变更记录"顺延为"七、变更记录" |
| 2026-10-13 | **议题一方案稿**（§6.5）：用户拍板新建 `user_data/` 归口；本助手完成"移位影响面"全量排查（真路径构造/断言 **123 处 / 38 个文件**，逐类列表），并给出目标布局、唯一事实源设计（`utils/paths.py`，含`chat_images.py:118` 注释早已期待的证据）、数据侧唯一改动（`digest_reports.md_path` 34 行）、七步执行顺序、十项验证清单与六条风险对策。**本轮未执行任何搬迁或代码改动** |

### 6.6 实现记录（议题二 / 议题三，2026-10-13 已实现：脚本侧验证通过，**实际客户端测试待做**）

> 用户 2026-10-13 口令：「数据移位算大点内容，不过你直接做了，那现在就算进入实现阶段吧，把后面两个议题也完成了。」
> 裁决落 `Verdict/04-文档架构与用户数据归口.md`；方案落 `v4.0规划文档.md` §04 ③。

**议题二 · 设置模块（绑定邮箱 + 一键注销）**

| 项 | 落地 |
|---|---|
| 数据 | `accounts` 加 `email` / `email_bound_at`；新表 `verify_codes`（只存哈希、5 分钟时效、60 秒重发限制、5 次试错上限） |
| 接口 | `GET /api/me/security`、`POST /api/me/email/send_code`、`/api/me/email/confirm`、`/api/me/email/unbind/send_code`、`/api/me/email/unbind/confirm`、`/api/me/delete/send_code`、`/api/me/delete/confirm`（共 7 条，落在既有 `/api/me/*` 命名空间） |
| 注销序 | 密码 + 绑定邮箱验证码 + 确认词「注销账号」三重校验 → **先快照** `docs/v4.0/backups/account_delete_<账号>_<时间戳>/` → `purge_account`（库行 + 文件侧 + sessions）→ 旧 token 失效 → 审计 `data/audit/account.jsonl` |
| 发信 | `utils/mailer.py`（标准库 smtplib，逐字段可配）；`MAIL_DEBUG=1` 调试模式写 `tests/test_out/_mail_outbox.log`（本机联调/自动化用，界面有提示） |
| 前端 | 第 8 个页签「⚙️ 设置 → 🔐 账号安全 / 🗑 注销账号（危险区）」 |
| 验证 | `tests/testclient/settings_account_e2e.py` **31 项通过 / 0 失败**：绑定（错码拒 / 正码成）、邮箱唯一性、注销三道闸（错密码、错确认词、错邮箱、错验证码各自被拒）、成功注销后库行与目录全清、旧 token 401、备份目录已生成 |

**议题三 · 开关类配置表单**

| 项 | 落地 |
|---|---|
| 数据 | 新表 `app_settings`（键值 + 更新时间 + 操作人） |
| 模块 | `utils/settings.py`（登记表 `SPECS` + 库→.env→默认 的兜底顺序 + 校验 + 掩码 + `.env` 写入备份 + `apply_runtime_hooks` 热更）；`api/settings_api.py`（读 / 存 / 测试发信） |
| 上表单 | 开关 9 · 决策参数 8 · 邮件 7 · 只读密钥 2（`GET /api/settings` 按分组下发，每项带 label/help/当前值/来源/默认值） |
| 读取点 | `decision_router._env()` 一处覆盖该模块全部开关与参数；`voice_asr._enabled()`；`api/server._use_stream()`；`ZX_ASSEMBLY_SOURCES` 常量（保存时热更）；`TOOL_CALL_LOG_ENABLED` / `AGENT_ERROR_LOG_ENABLED`（新键，默认开）；`DS_DEBUG_PROMPTS` 改为写 `data/audit/prompts_dump.txt` |
| 前端 | 同页第 8 个页签的「🔘 开关 / 🧭 决策模型参数 / ✉️ 邮件发信 / 🔑 密钥（只读）」四组 + 保存按钮 + 测试发信 |
| 验证 | 单测 `tests/unit/test_settings_store.py` 6 项（登记表完整性 / 兜底顺序与来源 / 校验拒绝 / 只读保护 / 开关归一化与掩码 / `.env` 写入保留注释+备份+幂等）；e2e 覆盖读分组、非法参数被拒、开关落库生效、改回生效、测试发信 |

**回归读数**：`tests/unit` **632 passed / 1 skipped**（原 626 + 新增 6，零回归）；跑后 `accounts` 仍 6、孤儿验证码 0、`user_data/` 四个子根只剩 3 个真实账号。

**本轮未做**（见 `Verdict/04` 裁定 3）：文档架构类 D1~D7；`Done/04-*.md` 两篇待大点收官时补写。

