

# M3 RAG 知识库搭建与使用 · 实现设计 & 验证规划（智选情报官 v2.0）

> 里程碑：M3（承接 `docs/v2.0/RAG库内容设计讨论.md` + `docs/v2.0/v2.0方案.md` §一）
> **技术选型拍板（2026-09-04）**：**自建 Chroma 向量库，弃 RAGFlow**——理由见 §〇。
> **批注修订（2026-09-04 用户 13 处批注已全部落入）**：目录结构、工具挂载、入库确认、清洗评估、GPU 策略、单用户单库等见各节标注。
> 状态：✅ 已完成并实测通过（2026-09-04）；bug 修复 + 防重复入库 + 评估可视化 ✅（2026-09-08）
> 前置依赖：M1（会话持久化/多会话）+ M2（请求装配管线化）已完成

---

## 〇、一句话目标 + 技术选型拍板

**把「用户私有的自然语言沉淀物」（导出的报告、上传的 md）做成可按语义检索的本地知识库，主 Agent 对话 / 周报生成时可引用（📚 来源），替代 v2.0 原方案的 RAGFlow。**

### 技术选型：Chroma 自建 vs RAGFlow（拍板记录）

| 维度 | **Chroma 自建（拍板）** | RAGFlow（原 v2.0 方案）|
| --- | --- | --- |
| 成熟度证据 | **蜀道项目已完整跑通**：996 份治理、ALL@10=0.83、混合检索 8 轮迭代、污染清理方法论 | 未在本机验证过 |
| 部署形态 | 嵌入式库（进程内），零运维 | Docker 服务 + SDK + **常驻进程**（与本项目"用完即关"的本地单机定位冲突）|
| 模型 | bge-large-zh / bge-reranker **已在本机**（D:\LLM\model，蜀道共用），**一律 GPU**（批注 9）| 自带解析/embedding 栈，模型路线不同 |
| 账号隔离 | collection 级隔离（每用户一个库，见 §二）| 需租户/知识库空间映射 |
| 检索定制 | 蜀道已验证的父子分块 + BM25+向量 RRF + reranker 全链路可移植 | 黑盒，调优路径不透明 |
| 对 Agent 透明 | ✅（工具背后是谁无所谓）| ✅ |
| 成本 | 主 venv 加 torch(CUDA) + chromadb + sentence-transformers | ragflow 镜像 + 服务依赖 |

**决策**：单机本地应用、已有同栈资产与完整方法论 → Chroma 自建；RAGFlow 留作未来多端/多设备同步场景再评估。

### 与「RAG库内容设计讨论.md」的关系

- **采纳**：P0 个人知识库为主库、P2 后置、明确不做网络缓存/SOUL 向量化/多账号共享、QA 库慎做、四判据方法论
- **修正**：P1「任务模板库」**降级推迟**（与 deepagents 子 Agent 静态路由重叠）；P0 补充入库确认链路 + 清洗评估；检索触发补充代码层必查场景（周报）与快/全双模式（见 §五）

---

## 一、本期改动清单（按落地顺序）※ 批注 1：新增文件目录规划

> **目录纪律（批注 1）**：本里程碑所有新增文件，除**确实必须放 agent/ 目录的**（挂载给 agent 的工具封装）与**测试脚本**外，一律放**新建的 `rag_knowledge/` 目录（与 agent/ 平级）**。
> **向量库落盘（批注 2/5）**：`rag_knowledge/db/{username}/` 子目录按用户存放（每用户一个向量库；单用户中长期顶天 ~100 份文档入库，规模小、索引开销可忽略）。
> **前端（批注 1）**：RAG 知识库做**专门模块页面**（增删改查 + 检索测试），工作台（home）设快捷入口。

```
项目根/
├── rag_knowledge/                    # ★ 新增（M3），与 agent/ 平级
│   ├── kb_service.py                 #   知识库服务：embedding(bge-large-zh GPU)/分块/入库/检索
│   ├── kb_store.py                   #   入库记录管理（source_id 去重，见批注 11）
│   ├── cleaner.py                    #   入库前内容清洗（emoji/表格符等，见批注 8）
│   ├── evaluator.py                  #   入库评估（独立小 agent，见批注 7）
│   └── db/{username}/                #   每用户向量库目录（chroma persist + 入库记录）
├── tools/kb_tools.py                 #   query_kb / upload_to_kb 工具封装（M3 工具，放 tools/）
└── static/…                          #   前端新增「知识库」模块页 + 工作台入口
```

| 阶段 | 文件 | 改动 | 说明 |
| --- | --- | --- | --- |
| ① | `pyproject.toml` / venv | 依赖：`chromadb`、`sentence-transformers`、`rank_bm25`、`langchain-chroma`、`langchain-huggingface` | torch 用 CUDA 版（批注 9：一律 GPU）；版本对齐蜀道 requirements（chromadb 1.5.9 / sentence-transformers 5.7.0）|
| ② | `rag_knowledge/kb_service.py`（新）| embedding（bge-large-zh，GPU）、collection 管理（按用户）、父子分块入库、检索管线（快/全双模式，见 §五）| 移植蜀道 `rag.py`/`embeddings.py`/`knowledge.py` |
| ③ | `rag_knowledge/kb_store.py`（新）| 入库记录表（md5 去重 + 用户目录管理）| 与 `digest_reports` 联动见批注 11 |
| ④ | `rag_knowledge/cleaner.py`（新）| 入库前清洗：去 emoji/表格符/人格口吻残留（批注 8）| 参考 `api/voice_tts.py` 的 `_clean_tts_text` 案例 |
| ⑤ | `rag_knowledge/evaluator.py`（新）| 入库评估：**独立小 agent**（不纳入 main_agent 体系，直接调 LLM）评估 ①脏→清洗 ②质量建议（批注 7）| 只建议，最终决定权用户 |
| ⑥ | `tools/kb_tools.py`（新）| `query_kb`（对话快模式）/ `upload_to_kb` 工具封装 | M3 工具放 tools/ |
| ⑦ | `api/server.py` | ① tools 挂载：`query_kb` **双挂**（主 Agent/定制 agent 工具 + 周报引擎代码层必查，批注 3）；② `/api/kb/*` 路由（上传/列表/删除/检索测试/创建库）| 见批注 3 方案 |
| ⑧ | `api/server.py` 导出路径 | 导出 MD 后**弹窗"是否入库"**（批注 4），用户同意才走评估→入库 | 见批注 11 去重 |
| ⑨ | `static/index.html` | 新增「知识库」模块页（**用户手动维护增删改查 + 首次建库**，批注 1/13）+ 工作台快捷入口 + 导出后入库确认弹窗 | 上传仅 .md（批注 12）|
| ⑩ | `agent/digest_engine.py` | 周报生成前**代码层必查**（周报全模式），候选区注入 | 讨论文档候选 1 落地 |
| ⑪ | `prompt/prompts.yml` | main_agent 📚 来源激活 + query_kb 纪律段 + 周报知识库段 | ragflow 占位停用 |
| ⑫ | `tests/test_knowledge_base.py`（单元）| embedding/分块/去重/隔离/清洗/评估 | 规模按单用户 ~100 份（批注 5）|
| ⑬ | `tests/m3_kb_e2e_test.py`（端到端）| 导出确认入库→问答引用→周报引用→隔离 | 真跑 |

> 不动：context_budget/conversation_store（M1）、build_context（M2）——知识库通过**工具 + 代码层注入**接入。

---

## 二、数据与集合设计

### 2.1 每用户一个向量库（批注 5/13），目录与 collection 命名

- **库的创建（批注 13）**：账号**默认无库**；用户在「知识库」模块页首次操作（上传/建库）时创建——`rag_knowledge/db/{username}/` 目录 + 对应 collection 首次初始化（UI 上体现"创建你的知识库"引导态）
- **collection 命名**：`kb_{username}`（每用户一库，规模 ≤100 文档 → 无需 account_id 前缀区分多次库；username 已唯一）
- **持久化路径**：`rag_knowledge/db/{username}/chroma/`（入库记录 JSON/SQLite 同目录）
- ~~`kb_meta` 系统级 collection~~（批注 6：不需要）

### 2.2 入库文档元数据 schema（父子分块 + 去重键）

每条子块（chunk）metadata：
```json
{
  "source_id": "md5(content)",        // ★ 去重键（批注 11：同内容多次导出/上传只入库一次）
  "source_kind": "export|upload",     // 来源类型（导出报告 / 上传 md）
  "title": "报告标题 or 文件名",
  "parent_text": "...",               // 父块完整文本（命中子块、注入父块）
  "created_at": "2026-09-04 10:00",
  "username": "alice"
}
```

### 2.3 去重策略（批注 11：可重复导出 vs 避免重复入库）

- 入库键 = **内容 md5**（导出的是同一回复 → 内容相同 → 同一 source_id）
- 导出弹窗（批注 4）：用户点「导出 MD」→ 导出文件照常下载 → **弹窗「是否将本报告加入知识库？」** → 同意 → 服务端算 md5 → 若已入库，返回「该内容已在知识库中（不重复添加）」并给出库内条目链接；未入库 → 走评估流程（§四）
- 用户可无限次重复导出下载，知识库只收一份

### 2.4 与现有存储的关系（不重复造轮子）

| 现有物 | 与 KB 关系 |
| --- | --- |
| SQLite `messages`（M1）| 知识库不存对话（QA 慎做结论）|
| `digest_reports` + `output/{user}/` md | 导出报告是入库内容来源之一（经弹窗确认）|
| MEMORY.md 画像 | 不进 KB（维度不同）|

---

## 三、核心设计：入库流程（评估 → 清洗 → 确认 → 入库）

### 3.1 入库决策链（批注 4/7/8）

```
用户触发入库（导出弹窗同意 / 知识库页上传 .md）
  → ① evaluator（独立小 agent，不纳入 main_agent 体系，直接调 LLM）
       评估两件事：
       a. 内容是否含脏（AI 导出报告带的人格口吻/emoji/表格符等——批注 8）
          → 脏 → 建议清洗，展示清洗前后预览
       b. 内容质量是否值得入库（如纯闲聊导出 / 重复信息 → 建议不入）
       → 只输出建议，不自动执行
  → ② 用户确认（预览清洗结果 / 采纳或忽略质量建议）
  → ③ cleaner 执行清洗（确认采纳时）→ kb_service 分块入库（GPU embed）
```

### 3.2 cleaner 清洗范围（批注 8：重点防 AI 导出报告的检索脏内容）

| 脏类型 | 例子 | 处理 |
| --- | --- | --- |
| 表情符号 | 😂✨(≧∇≦)ﾉ | 删除（保留必要语义的 emoji 如 📚 来源标记 → 替换为纯文本"知识库"）|
| 表格/分隔符残留 | `| --- |`、`---`、`>` 引用块符 | Markdown 表格转文本描述；分隔符删除 |
| 人格口吻装饰 | "奴婢为您…" 开场白/结尾客套 | 删除首尾寒暄段（保留正文情报）|
| 来源标记符 | 🔍🌐🗄️📚 | 转纯文本（"网络/数据库/个人库/知识库"）|

参考案例：`api/voice_tts.py` 的 `_clean_tts_text`（语音合成前清洗 md/emoji/表格）——同款思路，目标对象不同（检索友好 vs 朗读友好）。

### 3.3 上传格式（批注 12）

- **本次 M3 只支持上传 `.md` 文件**（数据文档同一性；txt/pdf/其他格式后置）
- 知识库页「粘贴文本」入口：保留，但提示"粘贴内容将按 Markdown 处理"（粘贴物等同 md 源，走同一清洗/评估链）

---

## 四、核心设计：知识库服务与检索（移植蜀道 + 快/全双模式）

### 4.1 移植蓝图（蜀道已验证）

```
入库侧：md 文本 → 清洗(cleaner) → 按标题/段落切父块(500-1500字) → 子块(200-500字, 重叠10%)
        → bge-large-zh(GPU) embed 子块 → chromadb upsert（metadata 带 parent_text）
检索侧：question → [稠密] embed(GPU) → chroma top30
              → [稀疏] BM25Okapi(top30) → RRF 融合 top30
              → 快模式：直接取 top-k 父块  /  全模式：bge-reranker 重排 → 父块去重 → top-k
```

参数参考（蜀道 B2 调优）：`FUSE_TOPK=30`、全模式最终 `k=6` 父块、快模式 `k=4`；中文 tokenize 用 jieba。

### 4.2 快 / 全双模式（批注 3 方案，用户已确认）

| 模式 | 链路 | 延迟 | 使用方 |
| --- | --- | --- | --- |
| 🚀 对话快模式 | BM25+向量 RRF → top4 父块（**跳过 reranker**）| ~0.3-0.5s（GPU embed <200ms + BM25 秒级）| AI 助手对话 `query_kb` 工具 |
| 📚 周报全模式 | 全链路 + bge-reranker 重排 → top6 父块 | ~2-3s | 周报生成代码层必查 |

依据：embedding 单条 query GPU <200ms；reranker 才是慢环节（30 对 1.5-3s）；0.5s 检索相对"模型思考 20s+"无感。个人库 ≤100 文档，BM25 语料与向量库开销可忽略。

### 4.3 工具契约（批注 3：双挂）

```python
# tools/kb_tools.py —— 挂载到：① 主 Agent/定制 agent 的 tools（AI 助手对话可用）
#                        ② digest_engine 周报链路（代码层必查，不经 LLM 决策）
def query_kb(question: str, mode: str = "fast") -> str:
    """检索当前账号个人知识库（快模式 top4 / 全模式 top6），返回带 📚 来源片段。
    无库/空库 → 明确引导话术；检索不到 → 明说，不编造。"""

def upload_to_kb(title: str, content: str) -> dict:
    """文本入库（账号隔离，md5 去重）。通常由导出弹窗/知识库页调用，Agent 一般不直接调。"""
```

> **澄清（批注 3 原疑）**：不是"周报 agent 专用"。`query_kb` 双挂——AI 助手对话里主 Agent 可自主调用（快模式，不拖慢回复）；周报引擎在生成前代码层必查（全模式，质量优先）。两条链路都拿到 RAG 能力。

---

## 五、检索触发设计（代码层 vs 模型自觉）

| 场景 | 触发方式 | 模式 |
| --- | --- | --- |
| 周报生成（digest）| **代码层必查**：每个订阅生成前 query_kb(订阅关键词相关) | 全模式 |
| AI 助手对话 | query_kb 工具（Agent 自主）| 快模式 |
| 无库账号 | 工具返回引导话术（前端「知识库」页创建）| — |

> 教训引用：周报时效性（strict_days 代码层剔旧闻）证明**代码层比提示词层可靠**——周报必查同理。

---

## 六、踩坑预判

| # | 风险 | 防御 |
| --- | --- | --- |
| 1 | **torch/依赖进主 venv 冲突**（py3.11 + langchain vs sentence-transformers 需 torch）| 版本对齐蜀道 requirements（已证同栈共存：蜀道 langchain 1.3.4 + sentence-transformers 5.7.0）；torch CUDA 版（批注 9）；若冲突则 embedding 子进程隔离（参照 voice_test，不首选）|
| 2 | **bge-large-zh 首次加载慢**（~1.3GB，GPU 加载 5-15s）| lazy 单例；首次操作提示"首次加载模型约 15s"；加载后常驻（GPU 显存 ~1.5GB，与语音不同——bge 小，常驻可接受且检索要快必须常驻）|
| 3 | **导出报告 emoji/表格脏内容污染检索**（批注 8）| cleaner 入库前强制清洗 + evaluator 预览 |
| 4 | **重复入库**（同回复多次导出，批注 11）| **消息级**：messages 表 `kb_ingested` 标志该消息的报告是否已入过库（每条 assistant 消息一个 flag）。导出弹窗触发时：若 `kb_ingested=1` 且非 force → 后端 400 拒绝，前端弹窗顶部显示 ⚠️ 警告"该报告已入过知识库"；用户可点强制再入（`force=true`）。入库成功后自动回写 `kb_ingested=1`。 |
| 5 | **账号隔离泄漏** | 每用户独立目录 + collection（`rag_knowledge/db/{username}/`），查询强制按当前登录用户 |
| 6 | **无库/空库工具行为**（模型幻觉假装查到）| query_kb 空库返回固定话术；prompt 纪律"📚 检索不到必须明说" |
| 7 | **对话检索慢拖慢回复**（批注 3）| 快模式跳过 reranker（~0.5s）；embedding 常驻 GPU |
| 8 | **评估 agent 本身引入延迟/成本**（每次入库一次 LLM 调用）| 评估是小 agent 单次调用（无工具链），~2-5s 可接受（入库低频操作）；提供"跳过评估直接入库"选项 |
| 9 | **chroma 目录体积/位置** | `rag_knowledge/db/` 加入 .gitignore（新增规则）；单用户文本 KB ≈ 几十 MB |
| 10 | **chroma collection 命名只允 `[a-zA-Z0-9._-]`**（2026-09 真实复现）| 中文 username（如「灵康科技」）拼到 `kb_用户名` 触发 `InvalidArgumentError` → 后端 500 → 前端收到 HTML 错误页触发 `Unexpected token "l" not valid JSON`。**修复**：collection 名改用 `kb_user_{account_id}`（account_id 是整数一定合法），目录路径仍用 username（路径允许任意字符）。已在 e2e 加中文 username 回归用例。|

---

## 七、实测验证

### 7.1 单元测试（`tests/test_knowledge_base.py`）

| 用例 | 预期 |
| --- | --- |
| md 分块：标题/段落切分、父块含子块 | 边界正确、无重叠丢失 |
| embed + upsert → query 命中 | 同义表述（"显卡性价比"vs"GPU 值不值"）召回 top5 含原文 |
| 用户隔离：A 账号查不到 B 账号文档 | 空结果 |
| 同 md5 二次入库 | 去重更新不膨胀 |
| cleaner：emoji/表格/人格口吻 | 清洗后无残留 |
| evaluator：脏文档 / 低质量文档建议 | 给出建议（不自动入库）|
| 空库 query_kb | 引导话术不抛错 |

### 7.2 端到端测试（`tests/m3_kb_e2e_test.py`）

```python
"""M3 端到端：导出确认入库→问答引用→周报引用→隔离。
前置：服务已起 8123。
"""

def test_export_confirm_ingest_and_query():
    """发问→导出 MD→弹窗"是否入库"→同意→入库→新会话问该报告内容 → 回答引用 📚"""

def test_repeat_export_no_dup():
    """同一回复导出两次并同意入库两次 → 第二次提示已入库，库中仅一份"""

def test_upload_md_only_and_query():
    """上传 .md 独家资料（.txt 被拒）→ 问答引用 📚"""

def test_weekly_digest_cites_kb():
    """知识库存 2 条相关观点 → 手动跑周报 → 周报含 📚（代码层必查生效）"""

def test_account_isolation():
    """A 上传 → B query_kb → 检索不到"""
```

### 7.3 验证记录（2026-09-04 实测回填）

**单元测试**（`tests/test_knowledge_base.py`）：10 项全过——分块（标题切分/超长滑窗含重叠）、md5 稳定、cleaner 7 项（emoji/表格转文本/人格口吻/来源 emoji 转文字/md 结构保留/纯文本零改动）、store 去重。
**端到端测试**（`tests/m3_kb_e2e_test.py`）：8/8 全过（真 bge GPU + 全局 Agent）：

| 测试项 | 验证结果 |
| --- | --- |
| 新账号默认无库（批注 13）| ✅ exists=False |
| 入库 + 同内容 md5 去重（批注 11）| ✅ dup=True（重复导出不重复入库）|
| 语义检索命中（fast，GPU embed ~30ms）| ✅ "先进封装产能"命中报告 |
| 上传仅 .md（批注 12）| ✅ .txt 被 400 拒绝 |
| 脏内容评估（批注 7/8）| ✅ needs_clean=True（emoji/表格检测）|
| **AI 对话 query_kb**（核心）| ✅ 问"昇腾 910C 进展"→ 模型自主调 query_kb 正确引用（📚）|
| 环境性能 | bge-large-zh GPU 稳态 22-50ms/条；reranker 13-15ms/对；full 模式含首次模型加载 ~40s，稳态 <1s |

**实施踩坑修复**：① prompts.yml 插入内容破坏 YAML 块缩进 → 修复并 yaml 校验；② cleaner 口吻行判定误删正文短行 → 收紧为"需含口吻特征词"；③ _split_parents 短标题节被并入前块 → 标题块独立成块；④ _dense_search 用 chroma id 字符串当索引 → zip 三列表；⑤ **chroma collection 中文名 IllegalArgumentError**（2026-09 复现：灵康科技入库 500）→ collection 名改 `kb_user_{account_id}`（account_id 必为合法字符）；⑥ `_load_corpus`/`_get_bm25`/`_sparse_search` 等辅助函数漏传 account_id 致 NameError → 全部加 `account_id` 形参贯穿。

**性能结论**：对话快模式实测 ~0.1-0.5s（GPU embed 常驻），周报全模式稳态 <1s——快/全双模式延迟预算全部达标。

### 7.4 Bug 修复 + 功能完善（2026-09-08）

入库流程真实使用后暴露的三个问题，已全部修复并端到端验证（TestClient 直调 ingest 路由 5/5 通过）。

#### 7.4.1 问题 1：评估弹窗文字太暗看不清

- **根因**：弹窗 CSS 使用 `var(--muted)` 等深色变量（与深色主题一致），但弹窗本身是深色底 → 文字几乎黑底黑字
- **修复**：`.kb-modal-box * { color: #e5e7eb }` 强制亮色文字；标题 `#f3f4f6` 加粗；预览区加深背景 `#ffffff08` 加边框
- **验证**：弹窗所有文字（来源/标题/评估建议/清洗预览）在深底上清晰可读

#### 7.4.2 问题 2：弹窗出现在错误的 view

- **根因**：弹窗 DOM 位于 `<div v-show="view === 'kb'">`（知识库 view）内。用户在 AI 助手页点导出触发 `kbIngestShow=true` → 弹窗在知识库 view 上，但 view 隐藏所以**看不见**；用户必须切换到知识库页才能看到
- **修复**：用 Vue3 `<teleport to="body">` 把弹窗 DOM 提出 view 容器，挂载到 `<body>` 根（z-index:9999）。任何 view（AI 助手/知识库/工作台）都能直接弹出
- **验证**：在 AI 助手页导出报告 → 弹窗立即在 AI 助手页顶部弹出，无需切 view

#### 7.4.3 问题 3：重复入库拦截未生效

- **根因**：原设计用 `md5(content)` 去重，**但未实现**（计划书只写了"用户确认才入库"，没落代码）。后端 ingest 路由直接把内容写 chroma，没有查 messages 表的 `kb_ingested` 标志
- **修复**：
  - `schema_personal.py` 给 messages 表加 `kb_ingested INTEGER DEFAULT 0`（CREATE + ALTER 幂等迁移）
  - `conversation_store.save_turn()` 返回 `last_assistant_msg_id`（最后一条 assistant 消息 id）
  - `api_chat` 把 `last_msg_id` 放到响应里；前端 `send()` 把 `msg_id` 写到 messages 列表
  - `kb_ingest` 路由加 `message_id`/`force` 参数：有 `message_id` 且 `kb_ingested=1` 且非 force → 400 拒绝 + 提示"已入过知识库"
  - 入库成功后自动回写对应消息 `kb_ingested=1`
  - 前端弹窗顶部加 ⚠️ 警告条（"该报告已入过知识库，确认继续将以强制再入模式写入"）
- **验证**：
  - 同一消息第二次入库 → 400 "该报告已入过知识库（force=true 强制再入）" ✅
  - 强制再入（force=true）→ 通过（dup=True，chroma 端已 md5 去重）✅
  - `messages.kb_ingested` 正确回写 1 ✅
  - 中英 username 入库 200 + 检索 200 命中 ✅

#### 7.4.4 修复涉及的代码

| 文件 | 改动 |
|---|---|
| `static/index.html`（CSS） | `.kb-modal-box` 强制亮色文字；加 `z-index:9999` |
| `static/index.html`（DOM） | `<teleport to="body">` 包裹 kbIngestShow 弹窗 |
| `static/index.html`（JS） | `kbIngestOpenWith` 加 `message_id`/`already_ingested` 字段；`kbIngestConfirm` 透传 `force`；入库成功回写 `m.kb_ingested` |
| `static/index.html`（模板） | 弹窗顶部加 ⚠️ 警告条（`v-if="kbIngestCtx.already_ingested"`） |
| `api/server.py`（kb_ingest） | 加 `message_id`/`force` 参数；查 `kb_ingested` 标志决定是否拒绝；入库成功后回写 1 |
| `api/server.py`（api_chat） | 返回 `last_msg_id`（从 `cs.get_last_msg_id(thread_id)` 取） |
| `agent/conversation_store.py` | `save_turn` 返回 `{"ok":True, "last_assistant_msg_id": ...}`；模块级 `_LAST_MSG_ID` 字典 |
| `tools/schema_personal.py` | messages 表加 `kb_ingested INTEGER DEFAULT 0`（CREATE + ALTER 幂等） |

---

## 八、步骤计划（看板）

```
[x] ① 依赖安装（torch 2.5.1+cu121 cuda=True；chromadb 1.5.9 等对齐蜀道；bge 模型走蜀道本地目录 safetensors 绕过 torch<2.6 安全检查）
[x] ② rag_knowledge/kb_service.py：embedding(GPU 常驻)/collection/分块/入库/检索（快全双模式）
[x] ③ rag_knowledge/kb_store.py：md5 去重 + store.json 用户目录管理
[x] ④ rag_knowledge/cleaner.py：入库前清洗（emoji 来源转文字/表格转文本/口吻行）
[x] ⑤ rag_knowledge/evaluator.py：入库评估独立小 agent（只建议）
[x] ⑥ tools/kb_tools.py：query_kb/upload_to_kb（挂载封装，owner→username 隔离）
[x] ⑦ server.py：query_kb 挂 AGENT/定制 agent + /api/kb/* 6 路由 + 默认无库
[x] ⑧ 导出弹窗"是否入库"（askIngestExport）+ md5 去重提示
[x] ⑨ 前端知识库模块页（上传/粘贴+评估/列表/检索/建库引导）+ 工作台入口 + 📚 导航 tab
[x] ⑩ digest_engine 周报代码层必查（full 模式，订阅词 top2×3）注入 task_prompt
[x] ⑪ prompts.yml：📚 激活 + query_kb 纪律段（YAML 缩进修复）
[x] ⑫ tests/test_knowledge_base.py（单元 10 项）
[x] ⑬ tests/m3_kb_e2e_test.py（端到端 8 项全过，含 AI 对话 query_kb 引用）
[x] ⑭ README 2️⃣8️⃣ + 项目现状/里程碑 v2.0-M3 + .gitignore rag_knowledge/db/
```

---

## 九、与蜀道 / 讨论文档的差异小结

| 维度 | 蜀道 | 本项目 M3 |
| --- | --- | --- |
| 库形态 | 单 collection | **每用户一个库**（`rag_knowledge/db/{username}/`，默认无库首建）|
| 数据来源 | 政务站自动采集 | 导出报告（弹窗确认入库）+ 上传 .md |
| 入库前处理 | 人工治理全流程 | **evaluator 建议 + cleaner 清洗 + 用户确认** |
| 触发 | 每问必检 | 对话快模式（工具自主）+ 周报全模式（代码层必查）|
| 显存 | GPU | 一律 GPU（embedding 常驻）|
| 去重 | 采集时间戳 | md5(content)（可重复导出不重复入库）|

---

## 十、参考文档

- `docs/v2.0/RAG库内容设计讨论.md`（内容选型依据；本文修正其 P1 降级 + P0 采集链路空缺）
- 蜀道项目：`backend/src/ai/rag.py`（检索管线）、`embeddings.py`、`knowledge.py`、`backend/scripts/collect_kb.py`
- `docs/v2.0/AstrBot_v2.0对比借鉴.md` B-4（知识库工具化路线）
- `api/voice_tts.py` `_clean_tts_text`（清洗案例参考）

---

*由智选情报官 v2.0 实施组维护 · M3 RAG 知识库搭建与使用规划（批注修订版）· 2026-09*
