# M4 Digest 定期推送 · 实现记录（a/b/c 三阶段）

> 承接 `docs/M4_定期推送与订阅设计.md`（设计书）。前置（第〇章 用户数据自主管理）此前已完成；
> 本文记录 2026-08 实施的 M4-a 订阅存储 / M4-b 引擎 / M4-c 调度推送三阶段代码与实测。
> 决策落地：②检索通道=Tavily 主 + HN Algolia 可选补充；③调度器=APScheduler；④推送=站内 WS+报告列表页。

---

## 一、改动清单

| 文件 | 阶段 | 说明 |
| --- | --- | --- |
| `prompt/prompts.yml` | b-1 | 新增 `digest:` 段：`filter`（三关筛选：实用P1/方法论P2/思维P3 + 硬性排除软文/跑分/旧闻）与 `writer`（周报模板：扫描说明诚实口径 + 本期要点/详细内容/建议行动 + 写作纪律四条）。与代码解耦，占位符由引擎注入。 |
| `agent/digest_engine.py` | b-2 | **多智能体编排引擎（重构版）**：`build_batches`（关键词→查询批次，硬上限 ≤8）；`run_digest(sub_id, owner_id)` 用 `create_deep_agent` 编排主 Agent——主 Agent 按任务指令调度【网络搜索助手】检索（internet_search 已支持 days=7，topic=news）→ 调度【定期情报周报助手】筛选撰写 → 提取 Markdown 落盘 `output/{user}/竞品周报_时间戳.md` + 写 digest_reports；统计口径从周报头部正则解析（容忍加粗，兜底数 🔍）。 |
| `tools/schema_personal.py` | a-1 | ensure_tables 新增两表：`digest_subs(owner_id, scope, name, keywords JSON, schedule daily\|weekly, enabled, last_run_at, UNIQUE(owner_id,scope))`、`digest_reports(owner_id, title, md_path, pdf_path, item_count, status, coverage_note, created_at)`。 |
| `api/server.py` | a-2 / c | 新增 4 路由：`GET /api/digest/subs`（无订阅则按角色自动生成默认：公司←company_competitors 竞品名、个人←interests 标签）；`PUT /api/digest/subs`（改关键词≤8/周期/开关）；`POST /api/digest/run`（手动触发，to_thread offload）；`GET /api/reports`（列表+下载链接，复用 /api/download 同目录校验）。startup 启动 APScheduler，shutdown 停止。 |
| `api/digest_scheduler.py` | c-1 | BackgroundScheduler(Asia/Shanghai)：daily@9:00 跑全部启用订阅、周一 9:00 额外跑 weekly；每份跑完经 monitor 通道 WS 推 `report_ready` 事件给对应 username。 |
| `static/index.html` | c-2 | 顶栏新增「📰 报告」tab（有新报告时红点 NEW）；新增报告视图：订阅卡片（显示 scope/schedule/启停 + 关键词编辑保存）、报告表格（标题/条数/时间/MD·PDF 下载）；WS 收到 `report_ready` → toast 提醒 + 列表自动刷新 + 红点亮起。 |
| `tests/m4_engine_test.py` / `tests/m4_rest_test.py` | 验证 | 引擎独立测试（不依赖服务）；REST 全链路冒烟。 |

依赖增量：`apscheduler==3.11.3`（uv add）。

---

## 二、纪律落地情况（继承 ai-weekly-digest）

| 纪律 | 实现位置 |
| --- | --- |
| 检索批次硬上限 ≤8 | `build_batches()[:MAX_BATCHES]` + PUT subs 服务端再截断 |
| 近7天时间窗防旧闻 | Tavily days=7 参数 + `dedupe_and_filter` 双保险；HN 用 numericFilters 时间过滤 |
| 统计口径诚实 | 报告头「扫描说明」如实写扫描数/批次数/精选数；候选 <15 条强制注明「本期覆盖有限」 |
| 绝不编造来源 URL | writer 提示词纪律 + `_parse_filter_resp` 按 URL 映射回真实候选，映射不到即丢弃 |

## 三、踩坑

1. **prompts.yml 追加 digest 段首次失败**：块内 `{context}` 占位符行缩进不足被 YAML 当成新 key。回滚后统一 4 空格缩进重写并通过 safe_load 校验。
2. **引擎首跑 NameError: today**：`run_digest` 内报告命名用了未定义变量（`llm_write` 里的局部变量误以为可见），补 `today = datetime.now()`。
3. （沿用）Tavily 走系统代理，代理断开即 ProxyError——引擎两条通道都做了降级直连。

## 四、实测验证（2026-08-26）

**引擎独立测试**（tests/m4_engine_test.py，不经服务）：
- 订阅 [至本洗面奶, 溪木源] → 批次 2 个 → Tavily 候选 10 条 → LLM 精选 → md 落盘 `output/m2tester/竞品周报_*.md`
- 报告头正确标注：「共扫描 10 条候选（检索批次 2 个），精选出 N 条。⚠️ 本期覆盖有限（候选不足15条）」

**REST 全链路**（tests/m4_rest_test.py，经服务）：
- 登录 200 → GET subs 自动生成默认订阅（scope=company，kws=[至本洗面奶,溪木源]，weekly）→ PUT 改订阅 200 → POST run 真实执行 200（batches=2, scanned=10）→ GET reports 返回 2 条历史报告且含下载链接。

**调度器**：启动日志 `[digest-sched] APScheduler 已启动（daily@9:00 / weekly@Mon9:00，Asia/Shanghai）`。

浏览器端人工验证路径：登录公司账号 → 顶栏「📰 报告」→ 查看/编辑订阅卡 → 点「立即生成一期」→ toast 提醒 + 表格出现新报告 → MD/PDF 下载。

## 五、遗留与后续

- RAGFlow 知识库接入（M4 设计书中另一模块，未动）；
- 用户画像（等用户新方案）；
- 可选增强：PDF 版 digest（当前仅落 md，pdf_path 字段已预留）、邮件送达通道、报告在线预览页。


## 五、架构演进：digest 回归主智能体编排（2026-08-27 重构）

**背景**：首版 digest 引擎自写 Tavily 请求 + LLM 三关筛选（独立 `services/` 目录 + prompts.yml 顶层 `digest:` 段），
违背本项目「主智能体主导的多智能体协作」定位，且实测频繁产出空周报。

**重构内容**：
1. prompts.yml：`digest` 从顶层独立段并入 `sub_agents`（成为第 5 个子智能体「定期情报周报助手」，不挂工具）；
2. 新增 `agent/subagents/digest_agent.py` 装配；新建 `agent/digest_engine.py` 用 `create_deep_agent` 编排：
   主 Agent 调度【网络搜索助手】检索 → 调度【定期情报周报助手】筛选撰写 → 输出周报 Markdown；
3. 删除 `services/` 目录（调度器移至 `api/digest_scheduler.py`）；
4. `internet_search` 工具新增 `days` 参数（topic=news 时生效），降级直连条件扩宽至 ProxyError/SSLError/ConnectionError；
5. 空周报修复：① 候选<15 时 digest 子智能体只排除明显无关（至少保 1 条，禁止空周报）；
   ② 统计解析容忍 markdown 加粗并兜底数 🔍 来源标记（此前真实内容被误记 0 条）；
   ③ 检索指令强制 topic=news + days=7，digest prompt 硬性剔除超窗旧闻。

**实测**：公司侧订阅扫描 12/精选 4、个人侧 15/8、公司侧 8/8 均正常落盘，无空周报、无旧闻混入。

**2026-08-27 补充（周报质量对标 skill 级）**：应实测反馈，放宽 digest 子智能体筛选与文字量标准——
① 数量：候选 ≥10 保留 8-12 条，5-9 全收，<5 至少 3 条（原 5-10 条）；② 排除项收紧为四类（无关/纯广告/纯跑分/超窗旧闻），其余相关即收；
③ 每条详细内容 80-120 字，采用「为什么值得关注 + 核心信息 + 来源与日期」三段式（对标 ai-weekly-digest skill 的「为什么有用」写法）；
④ 整份周报 1500-2500 字（原 ≤800）；⑤ 日期区间强制带完整年份。
实测：18 条候选 → 精选 11 条，周报 3024 字符（约 2400 汉字），REST 200 落盘正常。
