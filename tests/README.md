# tests/ — 测试与调试脚本

> 约定：**测试 / 调试脚本统一放本目录**，不放项目根目录。项目根只保留 `main.py` 入口。
> 统一跑法：项目根下 `unset PYTHONPATH && .venv/Scripts/python.exe tests/<脚本>`（Hermes 会注入自己的 venv，不清会撞 pydantic/numpy 版本）。

## 一、这个文件夹是干什么的

本目录是项目的**验证层**（不是产品代码）。放三类东西：

| 类别 | 是什么 | 谁会跑 |
| --- | --- | --- |
| **回归测试** | 守住「已实现且已验证过」的行为，防止后续改动悄悄弄坏它 | 改完代码默认跑 |
| **端到端验证脚本** | 把一条链路真跑一遍（真路由 / 真模型 / 真开窗口） | 里程碑收尾、改动核心链路时 |
| **调试与探针** | 排查故障、标定阈值、量化唤醒率（`_` 前缀是临时脚本，可删） | 出事时、调参时 |

**分层（由快到慢，改代码后按需往下一层跑）**：

| 层 | 特点 | 代表 |
| --- | --- | --- |
| ① pytest 单元 / 静态契约 | **秒级**、零外部依赖、不调模型不联网 | `test_context_budget.py` · `test_assembly.py` · `test_prompt_contracts.py` · `test_answer_text.py` |
| ② 前端抽取式 harness | **Node 里跑真实前端代码**（从 `front/index.html` 抽取 + 极简 shim），不需要浏览器 | `m4a_frontend_logic.js` · `desktop_export_logic.js` · `m4_*_frontend.js` |
| ③ TestClient 端到端 | **进程内直调真实路由**（不占端口、不碰用户开着的服务） | `m1_context_e2e_test.py` · `m5_*_e2e.py` · `auth_split_e2e.py` · `m4c_cli_e2e.py` |
| ④ 真机端到端 | 真调大模型 / 真跑 CLI / 真开窗口 → **慢**，真机问句可能触发**真实网络搜索（耗额度）** | `m5_probe_e2e.py` · `m5b_pressure_probe.py` · `desktop_window_e2e.py` |

**两条判据（写测试时先问自己）**：

1. **断言要有真值来源**：优先「审计日志 / 数据库 `messages.tool_calls` / 磁盘文件逐字节比对」，**不采信模型自述**（它会说「已调用」而其实没调）。
2. **测试不许动用户的数据**：流程类断言用既有账号（并断言**账号总数跑前跑后不变**）；测试自建的临时账号 / 会话 / 文件**跑完自己回收**（`purge_account` + 目录）。

---

## 二、索引（脚本 → 对应功能 → 代码段）

> 全量清单以本节为准；第四节起是部分脚本的**详细备注**（踩坑与判定方法）。
> 跑法统一前缀：`unset PYTHONPATH && .venv/Scripts/python.exe tests/<脚本>`（Node 脚本用 `node tests/<脚本>`）。

### ① pytest 单元 / 静态契约（秒级）

| 脚本 | 测什么（功能） | 对应代码段 | 怎么跑 / 规模 |
| --- | --- | --- | --- |
| `test_context_budget.py` | **M1** 上下文预算四组件（token 预算、裁剪策略） | `agent/context_budget.py` | `pytest`（秒级） |
| `test_assembly.py` | **M2** 请求装配（人格/记忆/技能如何拼进 SystemMessage） | `agent/build_context.py` · `agent/context_budget.py` · `agent/conversation_store.py` | `pytest`（5 项） |
| `test_knowledge_base.py` | **M3** 知识库服务层（分块 / 检索 / 清洗） | `rag_knowledge/kb_service.py` · `rag_knowledge/cleaner.py` | `pytest`（无需 bge） |
| `test_voice_asr.py` | **v3.1 语音输入**（客户端层）：开关语义（默认开 `0` / `false` / `off` / `no` 均视为关）· 状态形状 · 引擎 400 翻人话 · 字段透传 · 引擎不可达 · ★ **源码级「音频不落盘」断言**（唯一允许的落盘是引擎日志） | `tools/voice_asr.py` | `pytest`（**14 项**，0.1s） |
| `test_prompt_contracts.py` | **提示词 ↔ 代码的隐式契约**（周报头部格式、能力描述三种写法、记忆/技能路径写法、防腐） | `prompt/prompts.yml` · `agent/digest_engine.py` · `tools/cli_registry.py` · `tools/capability_pool.py` · `agent/build_context.py` · `front/index.html` | `pytest`（**11 项**，0.2s） |
| `test_ability_draft.py` | **CLI 能力描述的证据链**（文档 / `--help` 真值 / 审计） | `tools/cli_docs.py` · `tools/cli_registry.py`(classify_usage/collect_help/audit_ability_draft) · `api/customize.py`(cli_ability_generate) · `api/server.py`(`/api/cli/ability_draft`) · `front/index.html` | `pytest`（**24 项**，0.13s） |
| `test_cli_docs_mode.py` | **CLI 依据三选一（2026-10-07）**：网址 / **本机文件** / **跑一遍 `--help`** —— 解决自研（无 README）与闭源公测（官网无介绍）两类工具「AI 预写没依据」；只读闸对 `--help`/`-h`/`--version`/`-v`/`--usage` **窄放行**（`--help --output x` 仍拒、空命令行仍拒）；`docs_mode` 列老库启动自动迁移；npm/pipx 的 **`.cmd`/`.bat` 垫片**经 `cmd.exe /c` 启动（原报 `[WinError 193]`）；`collect_help_digest` 端到端真跑 | `api/customize.py`(cli_ability_generate) · `tools/cli_registry.py` · `tools/_runtime/shell_runtime.py` · `tools/schema_personal.py` | `pytest`（**18 项**） |
| `test_cli_audit.py` | **CLI 能力描述审计（flag 形态）**：新增形态 `needs_flag`——**只按 help 原文**判「必需且要取值」的开关（`--id string (required…)`、cobra 的 `Required Flags:` 小节），工具自己写明的链不反过来质疑用户；修两处误报（**真机报障**：agently-cli 参数全是 flag，旧逻辑只读 Usage 行位置参数 → 判成「无参数直调」→ 误报「凭空造链」）与补两处漏报（直调 `needs_flag` 要从 help 反查给出真正的第 1 步）；**含「正确草稿必须 0 警告」回归守卫** | `tools/cli_registry.py`(classify_usage / audit_ability_draft) · `api/customize.py` · `prompt/prompts.yml`(ability_writer) | `pytest`（**12 项**） |
| `test_answer_text.py` | **答案提取 / 落库清洗**（reasoning 回填、不许跨轮回溯、空白卡过滤） | `agent/answer_text.py` · `api/server.py` · `agent/digest_engine.py` · `front/index.html` | `pytest`（**15 项**，0.15s） |
| `test_spa_cache_headers.py` | **HTML 入口绝不缓存**（桌面端"改了看不到"事故固化：WebView2 持久化缓存喂旧 HTML） | `api/server.py` 的 `_no_store_html_entry` 中间件 | `pytest`（**5 项**，7s） |
| `test_api_driver.py` | **M1 API 驱动**：参数四位置分发（path/query/header/body）· URL 模板占位与编码 · 鉴权注入 · 超时/体积上限 · SSRF 三项口径（内网 base_url 默认允许；模型可控 URL 参数与跨主机重定向一律拒） | `tools/_runtime/api_driver.py` | `pytest`（**28 项**，全离线，`httpx.MockTransport`；含"固定 query 与模板内 query 不能被丢"） |
| `test_openapi_import.py` | **M1 OpenAPI 导入**：`$ref` 内联 · `operationId` 兜底命名 · path 级参数合并 · 写方法标记 · json/form 请求体 · cookie 降级 · 重名后缀 · JSON/YAML 容错 | `tools/openapi_import.py` | `pytest`（**20 项**；含路径内 `?cc=cn` 与 `x-static-query` → 固定 query 参数） |
| `test_pool_mixed_sources.py` | **M1 混合来源能力池**：CLI 渲染文本**逐字冻结** · api 条目措辞与参数摘要 · **未确认能力不进池** · `_entry_text` 对 api 补参数摘要（CLI 不加，否则触发全量向量重算）** + **路由器快路径**（混合池必须注入非 CLI 能力；纯 CLI 逐字一致） | `tools/capability_pool.py` · `tools/tool_router.py` | `pytest`（**15 项**，含"来源级能力描述必须合并进卡片""可选参数时示例不能是 `{}`"） |
| `test_capability_invoke.py` | **M1 执行网关**：ref 解析 · 四类拒绝（未知 ref / 未确认 / 未启用 / 缺必填）· 类型与枚举校验 · 写能力放行判定 · 成功路径 · **审计行字段** · `invoke_tool` 入参契约（必须是 `{ref, params}`，防 langchain 的 `v__args` 回归） | `tools/capability_invoke.py` | `pytest`（**29 项**，全离线 + 临时账号；含"params 宽容归一"：JSON 字符串 / null / 省略 / 非法类型可读报错） |
| `test_tools_sources_api.py` | **M1 工具来源配置面**（配置导向 CRUD · 密钥掩码 · OpenAPI 候选落库但**未确认不进池** · 确认闸（只读直接过 / **写方法必须显式勾选**）· **重复粘贴保留人工决定** · 删除连带清理 · 体检（不联网的配置校验）· 账号隔离） | `api/tools_sources.py` · `api/server.py`(`/api/tools/*`) | `pytest`（**21 项**，全离线 + 临时账号；含"两入口形状一致""重贴文档刷新描述"回归） |
| `frontend_setup_exports_check.py` | **前端静态不变量**：① 模板引用的标识符 ⊆ `setup()` 的 return（漏导出 → 交互静默失效）；② **`go('x')` 的目标必须是真实视图**（2026-09-25 报障：阅读器返回写成 `go('report')` 而视图名是复数 `reports` → 整页空白，点任意导航才恢复）；③ **模型选型卡片必须有「上下文要求」提示**（`class="cust-tip ctx-tip"`，文案含 32K/128K/512K 且位于该卡片内——2026-09-25 议题 X1 固化：用户自配小上下文模型不会被莫名截断）；④ **API 教程完整性**（正文存在 / 章节齐全 / 含最小 OpenAPI 骨架 / 两条安全口径 / **图片引用逐一存在**——2026-09-26，防止裂图） | `front/index.html` · `front/tutorial/` | `python`（**25 项**；`--fix` 可自动补漏导出） |

### ② 前端抽取式 harness（Node，无需服务）

| 脚本 | 测什么（功能） | 对应代码段 | 怎么跑 |
| --- | --- | --- | --- |
| `m4a_frontend_logic.js` | **M4a `/` 技能菜单 + M4c 自定义 CLI 页 + M5a 草稿按钮** | `front/index.html`（技能菜单 / `send()` 的 `skills` 参数 / CLI 表单 / `ability_draft` 调用） | `node`（**56 项**） |
| `asr_voice_frontend.js` | **v3.1 语音输入（长按说话）前端状态机**：长按开录 松手上传 **追加不替换** 移出取消不上传 太短不上传 后端 400 detail 空结果不动输入框 WAV 头是 16k/mono/16bit 关麦+关上下文 | `front/index.html` 的「v3.1：语音转文字」标记块 | `node`（**23 项**） |
| `m4_tutorial_frontend.js` | 教程页 **md 渲染器**（转义 / 代码块 / 折叠块 / 注入防护）+ **API 教程真渲染**（图片张数/代码块数/表格/尖括号坑） | `front/index.html` 的 `tutRender` · `front/tutorial/api_tools_tutorial.md` | `node`（**40 项**） |
| `m4_weather_frontend.js` | 工作台**时钟 + 天气卡**前端（补零 / 星期 / URL 编码 / 换城市） | `front/index.html` 的 `tickClock`·`loadWeather`·`wxSetCity`·`wxStart` | `node`（**51 项**） |
| `desktop_export_logic.js` | **导出/下载统一入口** `saveFromUrl`（网页分支 vs 桌面原生另存为、base64 分块一致） | `front/index.html` 的 `saveFromUrl` | `node`（**24 项**） |
| `m5c_report_reader_frontend.js` | **M5c 周报应用内阅读**（打开→取正文→渲染→返回；缓存键、异常路径、模板契约） | `front/index.html` 的 `openReport`·`readerBack`（真实代码） + **复用的** `tutRender` | `node`（**42 项**） |
| `m1c_tools_sources_frontend.js` | **M1 工具来源前端逻辑**（表单→请求体校验与后端同口径 · **勾选→确认请求体：写操作必须二次确认** · 徽标/参数摘要 · 添加/体检/编辑/删除/解析候选/确认 的真实调用序列 · 模板结构） | `front/index.html` 的工具来源代码块（真实抽取） | `node`（**11 组**，含弹窗范式：常态无表单 / 打开导入即列已落库工具；**第 9 组 = MCP tab**（分来源过滤 · 发现七步 · 确认带 `source` · 表单只在弹窗）；**第 10 组 = M5c-1 只读声明**（勾选框只在弹窗 · 确认框决定是否刷新已确认行 · 边界文案在位）；**第 11 组 = M5c-2' 工具描述**（弹窗共用 · AI 预写填框 · 与自动摘要相同的按不覆盖 · **两个信息源**：都没填时不发请求并给同一句话、payload/回填带官方文案） |

### ③ TestClient 端到端（进程内，不占端口）

| 脚本 | 测什么（功能） | 对应代码段 | 怎么跑 / 规模 |
| --- | --- | --- | --- |
| `m1_context_e2e_test.py` | **M1** 多会话隔离 + 会话持久化 + 越权校验 | `agent/context_budget.py` · `agent/conversation_store.py` · `api/server.py`(`/api/chat*`) · `tools/schema_personal.py` | `python` |
| `m2_assembly_e2e_test.py` | **M2** 装配管线（人格/记忆 invoke 期注入、改人格零重建） | `agent/build_context.py` · `agent/prompts.py` · `api/server.py`(`_get_agent_for`) | `python` |
| `m3_kb_e2e_test.py` | **M3** 入库 → 检索全链路（评估 / 清洗 / 去重） | `rag_knowledge/kb_service.py` · `rag_knowledge/cleaner.py` · `api/server.py`(`/api/kb/*`) | `python`（需 `D:/LLM/model/bge-*`） |
| `m4a_skills_e2e.py` | **M4a** 技能（CRUD → 前缀装配 → 用后清空） | `api/customize.py`(skills) · `agent/build_context.py`(`compose_user_prompt`) · `api/server.py` | `python`（**30 项**，含 2 次真机） |
| `m4b_cli_e2e.py` | **M4b** CLI 沙箱面板（白名单 / 安全矩阵 12 例 / 审计日志） | `tools/shell_executor.py` · `api/server.py`(`/api/shell/*`) · `data/audit/commands.jsonl` | `python`（含 1 次真机） |
| `m4c_cli_e2e.py` | **M4c** 自定义 CLI 接入（校验矩阵 15 例 / 只读闸 / 账号隔离） | `tools/cli_registry.py` · `agent/build_context.py` · `api/customize.py` · `api/server.py` | `python`（**109 项**，含 1 次真机；`SKIP_LLM=1` 跳过） |
| `m4_tutorial_e2e.py` | **M4 收尾**教程只读阅读页（含项目外贴图内联 / 安全） | `api/server.py`(`/api/tutorial/doc` + 资源位) | `python` |
| `m4_weather_e2e.py` | **M4 收尾**天气卡后端（WMO 映射 / 缓存 / 降级 / 404） | `/api/weather` 路由（`api/` 内） | `python`（**需外网**） |
| `m5_abilities_e2e.py` | **M5a** 能力语义注入（`agent_brief` 双形态、few-shot 过只读闸） | `tools/cli_registry.py` · `agent/build_context.py` · `api/customize.py`(`cli_ability_generate`) · `api/server.py` | `python`（**43 项**，不耗模型） |
| `m5b_pool_e2e.py` | **M5b-1** 统一能力目录 / 能力池 / 向量惰性同步 | `tools/capability_pool.py` · `tools/schema_personal.py`(`tool_capabilities`·`tool_pool_meta`) | `python`（**62 项**；`SKIP_VEC=1` 跳过 bge） |
| `m5b_router_e2e.py` | **M5b-2** 工具检索路由（快路径 / RRF / 相对置信 / 三形态注入 / 兜底） | `tools/tool_router.py` · `tools/capability_pool.py` · `api/server.py`(`_run_agent` 注入) | `python`（**34 项**） |
| `auth_split_e2e.py` | **登录/注册两卡分离** + **会话跨后端重启仍有效** | `api/account.py` · `tools/schema_personal.py`(`sessions`) · `api/server.py` · `front/index.html` | `python`（**43 项**，约 8s） |
| `cli_dspro_e2e.py` | **CLI 版 `dspro`**（子进程真跑命令、登录态文件、`list/digest/chat`） | `cli/*.py` · `tools/schema_personal.py` | `python`（**45 项**；`SKIP_LLM=1`） |
| `m4_engine_test.py` | **Digest 引擎**（造默认订阅 → 真跑一轮 → 查产物） | `agent/digest_engine.py` · `agent/subagents/digest_agent.py` | `python`（真机 1~3 分钟，**会触发网搜**） |
| `m4_rest_test.py` | **Digest REST 冒烟**（订阅 CRUD → run → 报告列表） | `api/server.py`(`/api/digest/*`·`/api/reports`) | 需服务已启动 |
| `m3_smoketest.py` | 接口层冒烟（登录 → 竞品清单 → 导出 MD/PDF → 下载） | `api/server.py`(`/api/competitors`·`/api/export`·`/api/download`) | 需服务已启动 |
| `pre_work_test.py` | **数据归属权 + `/api/me/*` CRUD** | `api/me_user_data.py` · `tools/schema_personal.py` | 需服务已启动 |
| `m35_attrs_test.py` | 产品/收藏 **attributes 扩展字段**链路 | `api/me_user_data.py` | 需服务已启动 |
| `tool_agent_e2e_test.py` | **文档读写工具挂到 Agent 后真能用** | `tools/readtofile.py` · `tools/writetofile.py` · `api/server.py` | `python`（真机） |
| `tool_file_io_test.py` | 读写工具的轻量验证（路径穿越防护 / 扩展名白名单） | `tools/readtofile.py` · `tools/writetofile.py` | `python` |

### ④ 真机 / 外壳 / 真窗口（慢，注意额度）

| 脚本 | 测什么（功能） | 对应代码段 | 怎么跑 / 规模 |
| --- | --- | --- | --- |
| `m5_probe_e2e.py` | **M5a 唤醒率量化**（10 正例 + 3 反例，只看审计真值） | `tools/cli_registry.py` · `tools/shell_executor.py` · `data/audit/commands.jsonl` | `python`（真机 10~20 分钟；`SKIP_LLM=1` 只自检 **8 项**；**`STUB_WEBSEARCH=1` 零额度**） |

| `m5b_pressure_probe.py` | **加压探针（v3 记录器）**：**43 条真实工具题集**（覆盖账号 28 条能力 单工具 / 多工具 / 反例三类）—— ★ **脚本不判对错 只记录「调了哪些工具、什么顺序」** 对错由人工打分（链式工具如 12306「取时间 → 站码 → 查票」使「只看首调」必然误判） | `tools/tool_router.py` · `tools/capability_pool.py` · `tests/fixtures/m5b_probe_baseline.json` | `python`（真机 **core 16 条约 8~16 分钟 / full 43 条约 34 分钟**；`SKIP_LLM=1` 只自检；`M5B_SET=core\|full`、`M5B_ONLY=<关键字>`、`M5B_KEEP=1` 保留会话） |
| `asr_endpoint_probe.py` | **语音转文字真机探针**：临时账号 → `GET /api/asr/status` → 真音频（16k/mono/16bit WAV）POST `/api/asr` → ★ 文字非空 + 耗时字段 + ★ **转写前后无任何新增音频文件**（不落盘）+ 太短→400 人话 + 坏 token→401（会**自动拉起常驻引擎** 首次 ~9s 之后 ≈0.3s） | `tools/voice_asr.py` · `api/server.py`(`/api/asr*`) · 外部 `asr_cli`(`serve`) | `python`（约 30s） |
| `stream_delta_verify.py` | **流式语义（真机）**：★ 同一次运行内「流式聚合 == 最终正文（逐字）」 + TTFT < 总耗时 + `usage_metadata` 仍在 + `ZX_STREAM=0` 回退（2 次模型调用、无工具、不花 Tavily） | `agent/stream_sink.py` · `api/server.py`(`_invoke_or_stream`) | `python`（约 20s） |
| `stream_ws_e2e.py` | **流式传输 e2e（真机）**：自起 uvicorn 8124 → 真 WS 客户端 → ★ 增量比 `/api/chat` 返回**早**到达 + 流式聚合 == 接口 answer 逐字 + `seq` 单调 + 返回体契约不变 + **思考增量也经 WS 到达**（默认问「17*23 两步推理」 可用 `STREAM_E2E_Q` 换）；临时账号跑完回收 预期 **7/7 · `exit=0` · 无残留**（⚠️ 别用 TestClient 改本脚本 会卡在 `__exit__` 等挂着的 WS 会话上 见文件头三条踩坑） | `api/monitor.py`(`report_delta`) · `api/server.py` · `front/index.html`(`applyStreamDelta`) | `python`（约 40s） |
| `m6b_probe_drift.py` | **探针漂移对比器**（v3）：只看**调用序列差异清单** 不再自动告警对错 | `tests/fixtures/m5b_probe_baseline.json` · `data/m5b_probe_drift.json` | `python`（`--selftest` 自检 / `--diff` 比基线；**离线零额度**） |
| `m6b_score_import.py` | **人工评分表 → 基线回填器**：解析评分表的 `评分：` + `实际调用序列` → 写 `tests/fixtures/m5b_probe_baseline.json`（符号归一 跨盘符路径已容错 被拆断的续行会自动并回） | 评分表 md（桌面/`tests/test_out/`） | `python`（**离线**；`<评分表路径>`） |
| `m6b_delta_rerun.py` | **M6b 定向复跑器**：全量里 **√ 类不复跑**，只跑 ① live：上一轮判 △ 的题（默认 #25 #27 #30 #35 #36，经探针新增的 `M5B_IDS` 定向复跑）② offline：v1 遗留的**离线路由 5 例**（`router_bench_regressions.json`，复用基准的冻结池与 top3 口径，纯本地零额度）。★ 两段都**只记录、不判对错**：live 出人工评分表、离线出「期望 ref 落位」对照；`--live-pool` 可改用真实账号池（能看到刷新后的卡片，**不可与冻结基线直接比**） | `tests/live/m5b_pressure_probe.py`（`M5B_IDS`）· `tests/live/m5c_router_bench.py` · `tests/fixtures/router_bench_regressions.json` | `python`（`--selftest`；`--offline-only` 约 1 分钟零额度；live 段约 10~20 分钟 + 模型额度） |
| `m6b_material_ab.py` | **素材层 A/B 校验器**（只读）：把 `tests/fixtures/material_layer_draft.json`（工具级 keywords / abilities_user / 人话名字的**草稿**）**纯内存替换**进冻结池 → 跑①5 例回归的**期望 ref 落位** ②基准 30 题的**五指标**（与基线逐项对比）③**噪声题逐题体检**（confident 与 top1，用来定位被误路由的肇事条目）。用途：改素材前**先离线证明有效、且不碰硬判据（噪声假阳性）**；★ 不写数据库、不写夹具、不改冻结池 | `tests/fixtures/material_layer_draft.json` · `tests/live/m5c_router_bench.py` · `tests/fixtures/router_bench_regressions.json` | `python`（**离线零额度**，约 20 秒） |
| `m6b_material_lint.py` | **素材层静态闸门**（只读）：逐条检查素材齐不齐——关键词是否可用（去掉泛化词后还剩什么）、工具级描述是否到位、名字是否英文标识符、是否含否定式措辞、同源条目关键词是否高度重叠。★ 规则**对齐 `capability_pool` 的真实语义**（写了 `abilities_user` 后自动摘要不再进向量文本，故「自动摘要过长/是英文」只在没写工具级描述时才算问题） | `tools/capability_pool.py` · `data/personal.db` | `python [账号名或 id，默认 3]`（**只读**，秒级） |
| `front_template_bindings.js` | **前端不变量：模板绑定必须在 setup 的 return 里**（★ 2026-10-06 立，因用户报障「点开工具描述整页变白」）：这版前端是普通 `<script>`+`setup()`，模板只能看到 `return` 出去的名字 —— 新增模板里要用的函数却忘了 return，就会一渲染抛错、页面只剩背景，而 `m1c` 那种字符串断言抓不到。口径：花括号配对扫描**所有** return 块取并集 + 只认 `{{ }}` 首名 / 绑定里的**函数调用**（跳过 `a.b()` 方法调用）与条件指令；已自证（故意漏 return 时会失败并点名） | `front/index.html` | `node`（毫秒级，**只读**） |
| `m6b_check_leftovers.py` | **残留复查**：真实账号跑前跑后计数一致（conversations 48 / messages 1096 / sessions 83 / usage_events 111 基准写死） | `data/personal.db` | `python`（**只读**） |
| `m6b_purge_probe_run.py` | **中断残留清除器**（★ 默认 **dry-run** 加 `--purge` 才真删 按会话 id 定点 只碰探针会话） | `data/personal.db` | `python`（`--last-conv N [--purge]`；**动手前先看管理员给的手动分步指南**） |
| `m6b_restore_convs.py` | **会话还原**（误删/中断后的回滚，配 `docs/v3.1/backups/` 快照） | `data/personal.db` · `docs/v3.1/backups/` | `python` |

| `_m5b_route_calib.py` | **阈值标定**（z 分数 / 词法命中 / 注入量敏感性） | `tools/tool_router.py`（改阈值必重跑） | `python` |
| `test_mcp_driver.py` | **M2 MCP 执行驱动**：三种连接（进程内 / stdio 真子进程 / http 参数校验）· list/call/结构化结果 · 写工具无 `readOnlyHint` · 结果上限 · 命令白名单拒绝 · 超时口径 · ★ **惰性导入不变量**（`import mcp_driver` 与 `import api.server` 都不得拉起 `mcp` —— 实测 `import mcp` 要 5s） | `tools/_runtime/mcp_driver.py` | `pytest`（**13 项**，全离线；含真起 stdio 子进程的一条，约 12s） |
| `test_mcp_probe.py` | **M2 MCP 七步体检**：正常（七步全绿）· **命令不存在** · **协议不兼容**（满口胡话的假 server，必须快速失败）· **超时**（装死 300s + 5s 超时）· http 连不上 · 配置不合法即收摊 | `tools/mcp_probe.py` | `pytest`（**11 项**，全离线，约 30s；含 2 条"子进程崩溃/一声不吭时要把 stderr 带出来"的取证用例） |
| `test_mcp_sources_api.py` | **M2 MCP 来源配置面**（与 M1 同一套候选/确认/入池/审计/放行链路）：配置校验拒绝 · 列表投影（**env 只回键名**）· 体检七步 · 发现落候选**但不进池** · 两入口形状一致 · 写工具需显式勾选 · 入混合池 · **真调用** · 未确认被拒 · 重复发现保留人工决定 · 删除连带 · 越权 404 | `api/tools_sources.py` · `tools/capability_pool.py` · `tools/capability_invoke.py` | `pytest`（**12 项**，全离线 + 本地 stdio server，约 8s） |
| `test_memory_format.py` | **M3-1 画像格式契约**：行格式（`- 维度：事实（来源：X · 日期）`）· 维度白名单与**别名归一**（只并真同义，绝不并可能存不同事实的名字）· 占位行判定 · `memory_meta` 元数据 · **就地更新去重** · 保人工行 | `tools/memory_profile.py` | `pytest`（**18 项**，全离线） |
| `test_memory_snapshots.py` | **M3-9 画像快照与还原**：写 5 次只剩 3 份 · 还原可来回 · 账号隔离 · `purge_account` 连带清理 · `_memory_initialized` 兼容两种标记 | `tools/memory_snapshots.py` · `api/customize.py` | `pytest`（**10 项**，全离线） |
| `test_profile_evidence.py` | **M3-2 证据收集**：7 张结构化表**带 `表名#行id` 引用**（供溯源）· 最近 3 篇周报要点（相对路径 + 反斜杠）· 会话纯文本 · 空账号不炸 | `tools/profile_evidence.py` | `pytest`（**11 项**，全离线） |
| `test_memory_suggest.py` | **M3-3 建议器**：★ **只出建议绝不落盘**（文件逐字节不变）· add/update 差分 · 白名单外维度标 `⚠️新维度` · 老占位行以 `−` 呈现且默认不勾 · 证据不足时不调模型 · 两段提示词共有硬约束契约 | `api/customize.py` · `tools/memory_profile.py` · `prompt/prompts.yml` | `pytest`（**12 项**，全离线，模型用假的） |
| `test_memory_apply.py` | **M3-4 应用建议**：★ **G2 = 连续 3 次更新同一维度 → 恰好 1 行** · 保人工行 · 来源与日期落盘 · 一条都没落地则不写文件不留快照 | `api/customize.py` | `pytest`（**11 项**，全离线） |
| `test_memory_import.py` | **M3-10 一键导入**：必须显式 `confirm` · 整份应用 + **人工行生存**（`protect_manual`）· 导入前必快照 · 幂等 · 与 `/apply` **共用内核**（源码断言防分叉） | `api/customize.py` · `api/server.py` | `pytest`（**10 项**，全离线） |
| `test_profile_update.py` | **M3-5 结构化写入**：`update_user_profile` 的 upsert 语义 · **人工行改不动** · 无账号上下文不炸 · 改前快照（`agent_update`）· **计数水位**刷新 · 主提示词契约（旧"读全文再重写"口径已废）· 两个主 Agent 装配点都注册 | `tools/profile_update.py` · `api/server.py` · `prompt/prompts.yml` | `pytest`（**19 项**，全离线） |
| `test_memory_budget.py` | **M3-6 注入预算**：画像 ≤500 字 / 笔记 ≤300 字（装配期裁剪）· 情报相关维度优先 · **最近 3 条笔记无条件保留** · 相关度争名额（v1 词法）· 预算常量只有一份来源 | `agent/build_context.py` · `agent/context_budget.py` | `pytest`（**10 项**，纯文本无 DB） |
| `m3_memory_frontend.js` | **M3-7 前端建议面板**：默认勾选口径（**人工行 / 白名单外新维度默认不勾**）· 只发勾中项 · **一键导入取消则一个请求都不发** · 还原可来回 · 失败不谎报成功 · 关闭保护时确认文案要说清"会被覆盖" | `front/index.html` | `node`（**20 项**） |
| `m2_mcp_e2e.py` | **M2 真机：接真实 MCP 服务跑通全链路**（A = **真实第三方 server** `uvx mcp-server-time`（官方参考实现）；B = 本地 stdio server 兜底零网络；**C = 真实 Streamable HTTP 服务**（魔搭 ModelScope 部署的拼多多选品，免 token，连不上自动跳过））——保存 → 七步体检 → 发现工具 → 未确认被拒 → 写操作闸 → 确认 → 入池（卡带调用示例/来源级描述/人工改名保留）→ **`invoke_tool` 真调用拿到真实数据**（时间/天气）→ 审计 jsonl `source=mcp` → 重复发现幂等 → 清理 | `api/tools_sources.py` · `tools/_runtime/mcp_driver.py` · `tools/mcp_probe.py` · `tools/capability_pool.py` · `tools/capability_invoke.py` | `python`（**64 项**，约 40s；A 段首次 `uvx` 要下载建环境 ~15s，C 段连不上时自动跳过 → 离线只跑 B） |
| `test_m5c_readonly_declare.py` | **M5c-1 来源级「该服务全是只读」声明**：未声明→可被声明覆盖 · ★ **服务端明确 `readOnlyHint=false` 一律不被覆盖** · `sync_readonly` 才刷新已确认工具（默认受"M1 已确认行保留人工决定"保护）· 取消声明回到保守 · 候选视图与确认闸同口径 | `api/tools_sources.py` | `pytest`（**6 项**，全离线 + 本地 stdio 3 工具 server） |
| `test_pool_route_text.py` | **M5c-2 向量文本卫生**：送去 embed 的文本优先用**工具级**描述（不再整段带来源级，否则同源工具向量分不开）· 工具级为空才回退 · ★ **CLI 条目向量文本逐字不变**（M5b 的 content_hash 断言）· 卡片文本不受影响 | `tools/capability_pool.py` | `pytest`（**4 项**，纯函数、不读库） |
| `test_capability_draft.py` | **M5c-2\' 工具级描述**（用户手写 + AI 预写）：草稿解析容错（标识写成名字/尾段、漏写、多写、折行）· README 依据两种形态（网址 / 本地文件，含大小·二进制·不存在边界）· 保存与清除覆盖 · **能力池读取优先级**（手写 > 自动摘要，来源级描述仍保留）· 接线不变量（★ 提示词里不得出现端点 URL/命令行/env/密钥） | `tools/capability_draft.py` · `tools/source_docs.py` · `api/tools_sources.py` · `api/customize.py` | `pytest`（**32 项**，全离线；含**两个信息源**：官方介绍文案优先 / README 只作上下文 / 都不填则不生成 · 老库 `intro` 幂等补列 · 保存与清除覆盖 · 能力池读取优先级 · ★ 提示词里不得出现端点 URL/命令行/env） |
| `m5c_router_bench.py` | **M5c-3 离线路由基准**（五个数：precision@3 / recall@3 / hit@3 / 灰区比例 / 噪声假阳性率）· **冻结池**（`tests/fixtures/router_pool_snapshot.json`）+ **题库**（`tests/fixtures/router_bench.json`，30 道含噪声与跨工具）· 基线存 `tests/fixtures/router_bench_baseline.json`（`--write-baseline` 更新）· `--legacy`（M5c-2 之前的向量文本）、`--no-derive-keywords` 用于逐项归因对比⚠️ 三份 fixture 必须随仓库提交（别放 `tests/data/`，那里被 `.gitignore` 的 `data/` 吞掉） | `tools/tool_router.py` · `tools/capability_pool.py` | `python`（**全离线**：本地 embedding，不调 LLM、不联网、**不烧 Tavily**；约 14 秒/次） |
| `m1_weather_e2e.py` | **M1 真机：接真实公开 API（Open-Meteo，免密钥）跑通全链路**（自起临时后端 8124+ → 建来源 → 体检 → 粘贴 OpenAPI → 解析候选（未确认不进池）→ 确认入池 → 路由器注入 → `invoke_tool` 真调用拿到气温 → 审计行 → 清理并杀进程） | `api/tools_sources.py` · `tools/openapi_import.py` · `tools/_runtime/api_driver.py` · `tools/capability_invoke.py` · `tools/tool_router.py` | `python`（**23 项**，约 40s；用库里既有会话 token，不猜密码） |
| `_m5_real_weread_check.py` | **真实账号 + 真实 CLI** 三层证据（注入 / 路由 / 执行） | `tools/cli_registry.py` · `tools/shell_executor.py` | `python`（真机） |
| `test_ability_draft.py` | **CLI 能力描述的证据链（pytest，纯静态）**：对应 2026-09-24 报障「CLI 能力描述撰写 AI 写的子命令描述经常是错的」——根因是它原先只拿到**命令名清单**、没有任何依据，只能望文生义（实测 13 条映射 3 条错，且**全错在需要判断力的地方**：哪些命令要 ID、哪条是「书名→ID」解析器）。夹具用**本机实测的 weread `--help` 真值**，锁住：① URL 规范化（GitHub blob→raw、仓库根→`HEAD/README.md`、补协议头）；② **SSRF 守卫**（localhost / 127. / 10. / 172.16-31. / 192.168. / 169.254. / `*.local` / `::1` 全部拒绝，公网 IP 放行）；③ 证据抽取（留命令与用法、丢安装/贡献/许可段；抽太狠时整篇回退）与覆盖统计（能报出文档未覆盖的命令）；④ 形态分类三值（`needs_id` / `free_text` / `standalone`，含全大写形参与 `[OPTIONS]` 的边界）；⑤ **审计四条规则**（直调需要 ID 的命令 / 链头就需要 ID / 第 2 步是 standalone 的凭空造链 / 第 1 步不产出 ID），并断言建议里给的是**真解析器**（`book resolve`）而不是 `search`；⑥ 防回退：`api/server.py` 必须透传 docs/help_text/归属上下文、`cli_ability_generate` 必须走文档+help+审计、`prompts.yml` 的「按证据判断、不要凭命令名猜」硬规则不得被冲掉、前端必须回显证据与警告 | `.venv/Scripts/python.exe -m pytest tests/unit/test_ability_draft.py -q`（约 0.15s） | **24 项** |
| `desktop_shell_e2e.py` | **桌面外壳**（进程归属 / 端口 / 单实例 / 托盘 / 快速失败 / GBK / 图标守卫 / **HTML 入口不缓存 + 加载 URL nonce** / **测试隔离：日志与 WebView2 存储都用临时目录，且绝不碰用户实例**） | `front/desktop/app.py` · `desktop.cmd` · `desktop.exe` · `assets/icon.ico` · `launcher/desktop.cs` | `python`（**114 项**，6~7 分钟；**检测到用户桌面实例在跑时自动跳过 [16] 真机重启段（107 项）**） |
| `desktop_window_e2e.py` | **真开窗口两轮**（记住登录两半 / 下载真落盘 / 失效 token） | `front/desktop/app.py`(`--selftest`) · `front/index.html` | `python`（**46 项**，约 4 分钟） |
| `model_thinking_probe.py` | 当前接入的模型**是否输出思考内容** | `agent/llm.py` | `python`（真机） |
| `thinking_capture_probe.py` | `reasoning_content` 在非流式/流式/extra_body 下的抓取 | `agent/reasoning_model.py` · `agent/thinking_capture.py` | `python`（真机） |

### ⑤ 调试 / 临时（`_` 前缀可删）

| 脚本 | 用途 | 对应代码段 |
| --- | --- | --- |
| `m2_debug_run.py` | 进程内直接跑 `_run_agent`，打印**真实异常堆栈**（绕过 FastAPI 包装） | `api/server.py`(`_run_agent`) |
| `m2_ws_probe.py` | **WS 链路隔离**：连 `/ws/dbg` → 调 `/api/_test_emit` → 看是否收到事件 | `api/monitor.py` · `api/server.py`(`/ws/*`) |
| `_check_login.py` | 登录路由快速核对 | `api/account.py` |
| `_verify_kb_e2e.py` / `_verify_kb_fixes.py` / `_verify_kb_testclient.py` | **M3 修复项复核**（三条入库链路都返回 evaluation + 清洗预览） | `api/server.py`(`/api/kb/*`) · `front/index.html` |
| `_fix_e2e_unicode.py` | 批量修正测试脚本的 UTF-8 输出（Windows GBK 环境下打印 emoji 会崩） | `tests/*.py` |

---

## 三、阅读指南（先读这几个）

测试代码是「这里曾经出过什么事」的档案 —— 按下面的顺序读，能最快看清这个项目的**要害**在哪：

| 顺序 | 读什么 | 读它的理由 | 思考题 |
| --- | --- | --- | --- |
| 1 | `test_context_budget.py` → `test_assembly.py` | 最小最快的两个：看 M1/M2 的**装配契约**怎么被钉住（谁在 invoke 期注入什么、预算怎么裁） | 为什么「上下文预算」值得单独写测试，而不是靠线上观察？ |
| 2 | `test_prompt_contracts.py` | 看**「提示词 ↔ 代码」的隐式契约**如何用测试防「静默失效」（周报格式、能力描述写法、记忆路径） | 这类 bug 为什么单侧改动看不出来？为什么必须把契约写成常量 + 断言？ |
| 3 | `m5b_router_e2e.py` → `m5_abilities_e2e.py` | 本项目最核心的机制：**工具怎么被想起来**（能力语义 → 检索路由 → 三形态注入 → 兜底） | 为什么「不确定就整段回退全量」比「猜一个最像的」更安全？为什么未命中的工具也要保留一行名称？ |
| 4 | `m4a_frontend_logic.js` | 前端逻辑**不进浏览器**也能验证：从 `front/index.html` 抽取真实代码 + 极简 shim 在 Node 里跑 | 为什么不能「把逻辑重抄一份做单测」？抽取式测试的锚点怎么会失效？ |
| 4.5 | `test_memory_format.py` → `test_memory_apply.py` → `m3_memory_frontend.js` | **「记忆」这一类功能最容易悄悄烂掉**：格式契约 → 就地更新（连续改三次仍是一行）→ 建议不落盘 → 前端默认勾选 | 为什么「同一维度只有一行」要靠**代码**保证而不是靠提示词叮嘱模型？为什么"建议"与"应用"必须是两个动作？ |
| 5 | `desktop_shell_e2e.py` → `desktop_window_e2e.py` | 最好测的部分：**进程/端口/窗口**（谁起的进程、谁持有端口、记住登录的两半、下载真落盘） | 为什么「进程还活着」不等于「服务还在」？为什么单轮测试永远发现不了「下次启动失效」？ |

| 6 | `m5_probe_e2e.py` → `m5b_pressure_probe.py`（**v3**）→ `m6b_probe_drift.py` → `m6b_score_import.py` | 真机行为怎么**量化判定**：真值来源（审计日志 / 库 `tool_calls`）+ 反例 + 零额度手段；★ v3 起**脚本只记录序列、对错人工打分** 序列差异只看清单 基线 = `tests/fixtures/m5b_probe_baseline.json`（full 43 条） | 为什么「单次探针不达标」不能当作回归结论？为什么**链式调用**（12306 取时间→站码→查票）让「只看首调」必然误判？为什么基线只能记「一次路径」，序列变了 ≠ 变差？ |

## 四、跑法与环境约定

| 事项 | 约定 |
| --- | --- |
| 解释器 | 一律 `.venv/Scripts/python.exe`（不要用系统 python） |
| PYTHONPATH | 先 `unset PYTHONPATH` —— Hermes 会注入自己的 venv，不清会撞 pydantic / numpy 版本 |
| 编码 | 跑**项目自带套件**时带 `PYTHONUTF8=1 PYTHONIOENCODING=utf-8`（套件自己会打印 emoji）；要复现「用户双击」的 GBK 故障则反过来用 `env -u PYTHONUTF8 -u PYTHONIOENCODING` |
| 代理 | 本机系统代理 `127.0.0.1:7897` 会拦 localhost：脚本用 `Session().trust_env=False` / `build_opener(ProxyHandler({}))` / websockets `proxy=None`，curl 加 `--noproxy "*"` |
| 服务 | **默认直接打用户已开着的服务**；要自己起必须先告诉用户。判据：`netstat` 里 LISTENING 的 PID ≠ 你启动的 PID = 那是用户的服务（可能是旧代码）→ **换端口验证**，绝不 kill |
| 测试账号 | **用既有账号**做流程 / 只读 / UI 断言，并断言**账号总数跑前跑后不变**；只有「必须全新起步」的断言才建临时账号，且跑完自己回收（库 + 目录） |
| 不留残留 | 测试自建的账号 / 会话 / 文件跑完回收；`m5_probe_e2e.py` 还会在**启动时兜底回收**被 kill 的那轮留下的 `m5p_*` 账号与孤儿目录 |

### 外部 API 配额纪律（2026-09-24 用户提出）

> ⚠️ **外部 API 配额纪律（2026-09-24 用户提出）**：真机用例（`m5_probe_e2e.py` / `m5b_pressure_probe.py` / `m4_engine_test.py` 等）里，模型**可能委派【网络搜索助手】**去查资料——那是**真实 Tavily 调用**（实测一轮探针 ≥7 次），会吃掉有限的月度额度。用户额度紧张时（如 2026-09 仅剩 ~20%）：
> - 只跑**不联网**的用例：`SKIP_LLM=1` 自检、桩夹具用例、纯 TestClient 用例（`m5_abilities_e2e` / `m5b_pool_e2e` / `m5b_router_e2e` / `m4c_cli_e2e` 的非真机段）；
> - 需要真机行为验证时，**先问用户**当前额度是否允许，或把问题设计成**不需要公开资料**的形式（只用账号内私有数据 / 桩 CLI）；
> - 跑完若发现模型委派过网搜，在交付说明里如实报告消耗。
> - **现成开关**：`tests/live/m5_probe_e2e.py` 支持 `STUB_WEBSEARCH=1` —— 在 `import api.server` 之前把 `tools.tavily_tool._search_once` 换成离线桩，模型照样会委派，但**不出网**（零额度）。桩只改"检索返回什么内容"、不改"模型是否决定用工具"，所以 A/B 与行为对比仍然有效；每次被委派的 query 会打 `[webstub]` 行，便于统计。

## 五、端到端回归详细备注（按里程碑顺序）

| 脚本 | 用途 / 备注 | 前置条件 / 怎么跑 | 规模 / 项数 |
| --- | --- | --- | --- |
| `m1_context_e2e_test.py` | **M1**：上下文预算四组件 + 会话持久化 + 多会话越权校验 | 无需服务（TestClient 直调） |
| `m2_assembly_e2e_test.py` | **M2**：装配管线——人格/记忆 invoke 期注入、改人格零重建 | 无需服务 |
| `m3_kb_e2e_test.py` | **M3**：知识库入库→检索全链路（含评估/清洗/去重）。⚠️ 7b 段（中文 username 入库回归）**必须用全新注册的中文账号**（`中文测试公司_<时间戳>`，跑完连账号一起 `purge_account`）——曾复用固定老账号「灵康科技有限公司」，结果它 09-06 遗留了一个空 collection（文档数 0 但 `exists=True`），「默认无库」断言假失败（2026-09-16 修） | 需要 `D:/LLM/model/bge-*` 本地模型 |
| `m4a_skills_e2e.py` | **M4a**：技能 CRUD → `/api/chat` 带 skills 真机 invoke → 前缀装配校验 → 新会话反证（不带 skills 则无技能痕迹）→ 用后清空 | 无需服务；**会真实调用大模型 2 次（约 1-3 分钟）** |
| `m4a_frontend_logic.js` | **M4a + M4c + M5a 前端**：从 `front/index.html` **抽取真实代码块** + 极简 Vue shim，在 Node 里验证 `/` 技能目录交互（筛选/回绕/上限 5/不重复/Esc）、`send()` 是否带 `skills` 参数、用后即清、自定义 CLI 页目录/模板/状态都来自服务端（`/api/cli/list` `/api/cli/save` `/api/cli/status` `/api/cli/delete`）、预填示例只填表单不发请求（**56 项**） | `node tests/harness/m4a_frontend_logic.js`；**M5a**：CLI 表单「✨ 按只读清单生成草稿」——清单为空时不发请求并提示、有清单时 POST `/api/cli/ability_draft`、草稿只进表单**不自动保存**、生成中状态复位 | `node tests/harness/m4a_frontend_logic.js`（无需服务） |
| `frontend_setup_exports_check.py` | **前端静态不变量：模板引用 ⊆ setup 导出**（2026-09-16 真实事故固化）。`front/index.html` 是「in-DOM 模板 + 单个 setup() 返回对象」：模板里引用、却没写进 `return {...}` 的名字会绑成 `undefined`——**不报错、不崩、渲染正常，只有交互静默失效**。本次就是 `skOnInput`/`skOnKey`/`skillPick`/`skillUnpick` 漏导出 → 用户报「聊天框敲 / 没反应」。用例抽取模板全部 `{{ }}`/`v-*`/`@事件`/`:绑定`/插槽 props 与 setup 返回表做集合包含校验（含解析器自检：引用数 >0、导出数 >100，防解析失效导致假通过） | `.venv/Scripts/python.exe tests/unit/frontend_setup_exports_check.py`；`--fix` 可自动补进 return；另有不变量 ②（死路由）· ③（模型选型卡片上下文提示，class=`cust-tip ctx-tip`）· ④（API 教程完整性 + 图片引用存在） | （25 项） |
| `test_prompt_contracts.py` | **提示词 ↔ 代码的隐式契约（pytest，纯静态）**：这类 bug 的共同点是**静默失效**，单侧改动看不出来（M4 期「周报内容齐全、统计却记 0 条」= 生成侧措辞漂移、解析正则没命中；2026-09 记忆被写进 `agents_docs/agents_docs/{user}/` = 提示词里的路径写法与 `write_agent_doc(filename)` 的基准目录不一致）。用例：① 周报「扫描说明」契约三处一致（`prompts.yml` digest 段 ↔ `agent/digest_engine.py` 的 `SCAN_LINE_CONTRACT` / `SCAN_LINE_RE`）；② 能力描述三种写法（单命令箭头 / 两步链只取第一步 / 关键词行）都能被 `capability_pool.cli_entry` 与 `cli_registry._parse_ability_pairs` 吃下；③ 记忆与技能路径必须是「相对 agents_docs」（注入块与静态提示词都不许出现带前缀的路径形态）；④ 防腐断言（`our_products` / `MySQL` / `ragflow` 等陈旧术语不得回流；`--output` / `argv=[` 等 CLI 机械细节不得回流进常驻提示词；优先级声明 / 先后顺序 / 三类工具 /「只列名称不等于不可用」不得被后续里程碑追加冲掉） | `.venv/Scripts/python.exe -m pytest tests/unit/test_prompt_contracts.py -q`（约 0.2s） | **11 项** |
| `test_answer_text.py` | **「答案提取 / 落库清洗」回归（pytest，纯静态）**：对应 2026-09-24 用户报障**「约 1/5 概率正文跑到思考里、气泡没正文，刷新后一屏空白卡片」**。链一 = 推理模型偶尔把可见正文写进 `reasoning_content` 而 `content` 为空 → 原实现 `result["messages"][-1].content` 拿到空串；链二 = 工具轮会产生 `content="\n"` 的 assistant，前端对每条 assistant 都渲染一张报告卡。用例：① 提取优先级（content → reasoning **回填** → 本轮更早的非工具回答 → 显式失败，**工具轮那句「好嘞，先查一下～」不算答案**）；② **不能跨轮回溯**（否则把上一轮旧答案当这一轮答案）；③ 回填必须**写回消息对象**（只改返回值 → 落库仍空 → 刷新又变空卡），且不许碰 `tool_calls`；④ 落库过滤只丢「空内容且无 tool_calls」，带 `tool_calls` 的一条都不能少（孤儿 tool 消息会被上游 400）；⑤ 静态守卫：`api/server.py` 不得回退到无脑取末条、`digest_engine.py` 共用同一提取、前端空 assistant 必须跳过 | `.venv/Scripts/python.exe -m pytest tests/unit/test_answer_text.py -q`（约 0.15s） | **15 项** |
| `desktop_shell_e2e.py` | **桌面版外壳（pywebview，`front/desktop/`）**：启停后端 / **进程归属（只关自己起的、绝不误杀用户的）** / 复用别人服务后退出不关它 / 单实例锁 / 图标与托盘降级 / JS API 安全面（**不许有代发请求的通道**）/ 启动路径静态不变量（**`.cmd` 必须纯 ASCII**、ROOT 层级、加载页占位符数、`%` 双写）/ **后端起不来必须快速失败**（子进程秒退 → **约 18s** 返回〔B6 宽限 15s + 轮询，2026-10-09 实测 **17.7s**〕、端口被占 → **约 11s** 返回且报出死因+换端口建议）/ **GBK 崩溃回归**（干净环境下 import 不崩；并断言 `agent/prompts.py` 的调试 print 仍处于注释态）/ **P0 新能力**（base64→bytes 还原与落盘、关闭收托盘的 4 种分支——尤其**托盘不可用时必须能真关闭**、托盘菜单项齐全、页面 URL 带 `?desktop=1`、托盘动作走页面 hook 而不自己发请求）/ **第二实例唤起已存窗口**（真起 listener，发 `SHOW` 断言第一实例收到并会唤出窗口）/ **端口自动换**（空端口→new、被占→换下一个、只剩一个且被占→none、有 HTTP 服务→reuse）/ **托盘状态与忙碌图标** / **后端崩溃自动重启**（判定逻辑 6 例 + **真机：认出端口持有者→杀掉→断言服务被自动拉回**）/ **端口探测的 backlog 陷阱**（bind 探测与 connect 探测互补）/ **HTML 入口不缓存 + 桌面加载 URL nonce**（2026-09-25 报障"桌面端看不到前端改动"的防回退：URL 必须带 `&_=时间戳`、外壳自检与前端 `IS_DESKTOP` 正则都必须容忍额外参数）。**⚠️ 已知低频偶发**：`[16] 后端崩溃自动重启` 里有 3 项（自动拉回 / 日志含自动重启 / 外壳仍存活）在 2026-09-25 的一次运行中失败过 —— 机制是 **venv launcher 在真服务 bind 端口前先退出（退出码 1）**，外壳于是判成"子进程已退 + 端口还没人听"→ 按"秒级报死因"立刻失败（**后端本身是健康的**，日志里能看到 `Application startup complete` + 探针 `GET / 200 OK`）。**与后端 `Cache-Control` 中间件无关**：同参数 A/B（含/不含中间件）各 4 次直接 spawn，**8 次全部未复现**，属低频竞态；**已修（B6）**：给"子进程已退但端口未监听"加了 `CHILD_GONE_UNLISTENED_GRACE = 15s` 宽限（仍不通再报死因）—— **2026-10-09 真机复跑 114 通过 / 0 失败，[16] 真机重启段通过（自动拉回耗时 24s）** | `.venv/Scripts/python.exe tests/live/desktop_shell_e2e.py`（约 6~7 分钟，会临时起 3~4 个后端 + 开一次窗口；**用户实例在跑时会跳过开窗口段并少 7 项**） | **114 项**（用户实例在跑 → 107） |
| `desktop_window_e2e.py` | **桌面版真开窗口 + 真实启动路径（两轮）**：调 `app.py --selftest N`（与用户双击**同一段代码**），解析它打印的 `SELFTEST_JSON` 断言。**第 1 轮**：清空存储（**自动刷新页面**后才登录，否则 SPA 内存里还留着旧 token、登录表单根本不存在）→ 页面上**真登录**（用**既有账号**〔2026-09-22 起不再建临时号；跑完断言账号总数不变〕，并断言"表单真填上了 / 按钮真点到了"，避免假前提）→ 断言进了工作台、token 落在 localStorage、`?desktop=1` 生效、hook 与 `save_binary_b64` 可用；**下载落盘整条链路**：把一份周报放进临时账号的 `output/`，调页面真实的 `saveFromUrl()` → 断言返回 `ok:true, native:true`、**文件真的出现在保存目录**、内容与源文件逐字节一致；**第 2 轮**：不带任何登录参数 → 断言**未登录也直接进工作台**、且读到第 1 轮写入的 localStorage 探针值（= **「记住登录」的存储端硬证据**，也是它逮住了 pywebview `private_mode` 默认开着那个坑）；**★★ 两轮都用页面里存着的 token 真拉一次 `/api/me/overview`**，断言第 1 轮 `200` 且数据非空、**第 2 轮（此时是全新后端进程）仍是 `200` 且数据与第 1 轮逐项一致** —— 这是 2026-09-23 用户报障（"记住登录后数据全是 0"）的回归：**"进了工作台"不算记住登录，"能真读到数据"才算**；**第 2 轮末尾还带一个「失效 token」探针**：把 localStorage 里的 token 换成假值 → 页面自己 `location.reload()` → 断言**回到登录页（出现密码框）、假 token 已被清掉、页面上给出「登录已失效」提示**（老代码在这里是一堆静默的 0）。另：同源地址（非 file://）/ 加载页已切走 / 后端由外壳自己拉起 / 退出后自起的服务已清理 / **测试登录产生的会话行由测试自己回收、账号总数跑前跑后不变（既没建号也没删号）** / **测试自带 `--storage-path` 临时目录、绝不碰用户真实桌面版的登录态** | `.venv/Scripts/python.exe tests/live/desktop_window_e2e.py`（约 4 分钟，两轮真开窗口） | **46 项** |
| `desktop_export_logic.js` | **桌面版导出/下载统一入口 `saveFromUrl`**（用户实测报障：周报「MD↓」点了没反应）。从 `front/index.html` **抽取真实源码** + 最小桩（fetch/document/desktopApi/showToast）在 Node 里跑真实分支：① **网页版分支仍是浏览器下载**（`<a>` + `download` + 真点击，不能因桌面改造把网页版弄坏）；② 桌面版走原生 `save_binary_b64`，**base64 解回来与原字节逐字节一致**（含 **200KB 分块拼接**这个易踩栈溢出的点）；③ 三条异常路径（用户取消 → 安静退出不报错 / 保存失败 → 返回 error 原文 + 红字提示 / 取文件 404 → **不弹另存为**让用户白选路径）；④ `nameFromUrl` 从 `?name=` 解析中文文件名与兜底 | `node tests/harness/desktop_export_logic.js`（无需服务与浏览器） | （24 项） |
| `auth_split_e2e.py` | **登录/注册两卡分离（2026-09-16 收尾）**：背景是旧的「登录注册混合卡」——`doLogin()` 遇 401 就调 `/api/register` **自动建号**，打错用户名会静默建出错误账号（用户实际踩到「尼姑喵喵」）。用例断言：① 后端新语义（**账号不存在 → 404「账号不存在」/ 密码错误 → 401「密码错误」**）；② **用打错的用户名登录不会建号**（查库计数不变，这是本次的核心回归）；③ 注册 409 重名 / 400 空用户名 / 正常建号后能用新密码登录；④ 前端不再有「登录即注册」的调用路径（静态断言）+ 关闭按钮旁的注册入口；⑤ 注册卡字段与校验规则、注册成功后回登录卡不自动登录；⑥ `/api/login` 与 `/api/register` 的路由契约；⑦ **★ 会话跨后端重启仍然有效**（2026-09-23 用户报障修）：登录后 token 必须落库、**在真·新进程**里 `get_session(token)` 仍然有效、用旧 token 取总览仍是 200 且数据非空、退出后立刻失效。前端校验规则**行为**验证在 `scripts/cdp_auth_probe.py`（真机点出来） | `.venv/Scripts/python.exe tests/testclient/auth_split_e2e.py`（纯 TestClient，不耗模型，约 8s） | （43 项） |
| `m4b_cli_e2e.py` | **M4b**：CLI 沙箱面板全链路——白名单查询 → `POST /api/shell/exec` 正常执行（pwd/echo/mkdir/ls/cat/grep，落盘真实目录）→ **安全矩阵 12 例**（路径逃逸/`..`/绝对路径/反斜杠/`python`/`rm -rf /`/管道/`curl -o`/`curl -X POST`/`git -c`/`git --git-dir=`）→ 外部程序真跑（ffmpeg/git）→ 审计日志（含 rejected 与 cwd）→ **真机 Agent 调 `run_shell_command`** → 首页 CLI 面板结构 | 无需服务；**会真实调用大模型 1 次（约 1-3 分钟）** |
| `m4c_cli_e2e.py` | **M4c**：自定义 CLI 接入（配置导向）——列表/预填示例/状态枚举 → **校验矩阵 15 例**（路径式可执行名 / shell 元字符 / 选项开头 / 重复 bin / 超长 / 重复行绕过条数上限 / 非法状态）零脏数据落库 → 新增后落库直查归属与规范化 → 未登记拒绝 / 登记后放行 → 只读闸矩阵（未列出子命令 / 落盘参数）→ 编辑换名立刻回收、清空清单立刻不放行 → **局部更新不清掉 state/note/readonly** → 删除即回收 → 旧版内置登记幂等迁移 → 账号隔离 → **真机 Agent 调 `run_shell_command` 跑自定义 CLI** → 首页结构；`SKIP_LLM=1` 跳过真机步 | 无需服务；**会真实调用大模型 1 次（约 1-3 分钟）** |
| `m4_tutorial_e2e.py` | **M4 收尾**：教程只读阅读页——`GET /api/tutorial/doc` 只读返回（标题取自 md / 不泄露绝对路径）→ **7 张图片逐个 200 + image\* 且非空**（含项目外的 Typora 贴图）→ 安全（无 token / 错 token / `../` 穿越 / `.md` 不可经资源位读取）→ md 结构保真 → 首页结构 | 无需服务（TestClient 直调） |
| `m4_tutorial_frontend.js` | **M4 收尾前端**：抽 `index.html` 里**真实的 `tutRender`** 在 Node 跑——标题/表格/图片 URL 不被转义/代码块内容转义/折叠块/列表闭合/**注入转义**（`<script>`、`<img onerror>`、`<iframe>`）/真实文档整体渲染结构断言 | `node tests/harness/m4_tutorial_frontend.js`（无需服务；含 `front/tutorial/api_tools_tutorial.md` 的真渲染） | **40 项** |
| `m5c_report_reader_frontend.js` | **M5c 周报应用内阅读**（新增：周报不再只能"下载到本地看"，可在应用内直接读 `output/` 里那份 md）。要点：① 打开即 `go('reader')`，取数 URL **必须**是后端给的 `r.download_md`（与「下载」同一个 `/api/download`——**不另造接口**，也就不会出现"能下载却读不到"的两套逻辑）；② 正文交给**教程页那套真实 `tutRender`** 渲染（本用例把两段真实代码抽进同一个作用域一起跑，专门防"另写一个渲染器"）；③ 缓存键 = md URL 而非标题（**同名周报可能有多份**，用例单独覆盖这一点）；④ 四条异常路径（无 md → 不发请求 + 明确提示 / HTTP 4xx → 用后端 `detail` / 网络异常 → `error:'exception'` / 失败时不留上一份正文）；⑤ `readerBack()` 回报告页并清空 `readerItem`；⑥ 模板契约静态断言（阅读按钮的 `v-if="r.download_md"` 与 `@click="openReport(r)"`、MD↓/PDF↓ **未被替换掉**、PDF 按钮闭合标签已从误写的 `</a>` 修正为 `</button>`、`#/reader` 直达守卫）；⑦ 真实周报整体渲染（挑 `output/` 下**最大**那份——目录里也有几十字节的测试存根，拿存根断言"有表格/列表"会误判） | `node tests/harness/m5c_report_reader_frontend.js`（无需服务与浏览器） | **42 项** |
| `m4_weather_e2e.py` | **M4 收尾**：工作台天气——WMO 码映射与兜底 / **真实访问 Open-Meteo** 的城市级字段（城市·省·国家·经纬·温度·体感·湿度·风速）/ 4 天预报 / 内存缓存 0.005s / `force` 刷新 / **磁盘缓存落盘** / 换城市与英文城市名 / 404 / 鉴权 / 前端接线 | 无需服务（TestClient 直调），**需外网**（无外网会明确 FAIL） |
| `m4_weather_frontend.js` | **M4 收尾前端**：抽 `index.html` 真实函数（`tickClock`/`loadWeather`/`wxSetCity`/`wxStart`）在 Node 跑——假 Date 验秒级补零与星期、URL 拼装与编码、错误分支、换城市内联输入（确定/取消/清空回落默认）、定时器周期与幂等 | `node tests/harness/m4_weather_frontend.js`（无需服务） | **51 项** |
| `m5_abilities_e2e.py` | **M5a**：能力语义注入——`abilities` 列与**幂等迁移**（重跑不重复）/ 存读与「局部更新不清空」/ 超长拒绝 → `agent_brief()` **双形态**（有描述=能力路由卡、无描述=回退命令名简报）/ 多行描述注入时压平 → `ability_examples()` **few-shot 必须逐条过只读闸**（指向未放行命令的映射被丢弃、同段放行的照常生成）→ 装配链路（简报+示例进同一条动态 SystemMessage）→ API（save 回读 abilities / 草稿接口缺清单 400 不调模型）→ 首页接线 → 清理隔离账号。**用全新临时账号隔离**（避免真实配置干扰断言），跑完自动删账号与数据 | 无需服务（TestClient 直调）；不耗模型 |
| `m5b_pool_e2e.py` | **M5b-1 统一能力目录**：两表结构 + 幂等 `ensure_tables` → `cli_entry()` 适配器（关键词前缀剥离 / `enabled` 跟随 `live` / 无描述回退命令名形态 / 三种渲染形态长度递减）→ **写入口联动**（`save_cli`/`set_state`/`delete_cli` 后池立即反映）→ 池版本递增 → `rebuild_pool` 幂等 → **向量**（首次 added、二次全 skipped、删除工具后清理、惰性版本对齐）→ 账号隔离 → **不污染 M5a 简报** → 清理。含「`save_cli` 不因池同步变慢（<3s，即未加载 bge）」的回归断言 | `.venv/Scripts/python.exe tests/testclient/m5b_pool_e2e.py`；`SKIP_VEC=1` 跳过 bge（约 1~3 分钟） | （62 项） |
| `m5b_router_e2e.py` | **M5b-2 工具检索路由**：快路径（≤4 工具 → 与 `agent_brief` **逐字一致**）→ 路由路径（8 工具规模下命中集必须含期望工具，**不允许「路由走了但命中错的工具」**）→ 反例回退 → 护栏（完整卡数 ≤ MAX_FULL_CARDS、注入量 ≤ 全量、**未命中工具仍有一行名称**、示例只留命中工具）→ 兜底（向量不可用但有词法命中仍路由 / 两路都无信号回退 / 池异常回退 / 空问题回退）→ RRF 单元（无信号排序器必须剔除）→ 来源无关（混合 cli+api+mcp 条目）→ 接线（server 走 tool_router） | `.venv/Scripts/python.exe tests/testclient/m5b_router_e2e.py`；`SKIP_VEC=1` 跳过需 bge 的用例 | （34 项） |

| `m5b_pressure_probe.py` | **加压探针（真机，慢 · v3 制度）**：⚠️ 本节旧描述（8 个自造桩工具 + 10 问 + **自动判首调**）**已作废**—— 2026-10-01 用户裁定**制度 v3**：题集换成**账号真实的 28 条能力**（43 条题 含单工具 / 多工具 / 反例）， ★ **脚本只记录调用序列、对错由人工打分**（原因：12306 那类是链式调用「取时间 → 城市换 station_code → 查票」， 「只看首调」必然误判；且每多一个来源就要补一条前置规则，打补丁没完没了）。 真值仍走**会话直读**（`messages.tool_calls`，不用审计窗口计数）+ 不采信模型自述。 **配套四件套**：记录器（本脚本）· 对比器 `m6b_probe_drift.py` · 回填器 `m6b_score_import.py` · 残留清除 `m6b_purge_probe_run.py`； 主基线落 `tests/fixtures/m5b_probe_baseline.json`（**full 43 条**：序列 + 评分 + 备注）。 制度与题集全文见 `docs/v3.1/M6b-探针问题集v2.md`（§八 制度 · §十 全量审计）；跑法与纪律见同目录手动指南（历史）与本文件 §四。结果落 `data/m5b_pressure_result.json`；支持**备选首调**（`alts`：需前置 ID 的命令先 `search` 也算命中）与**单条重跑** `M5B_ONLY=m5bp-vid`（落 `..._only.json`，不覆盖整轮） | `.venv/Scripts/python.exe tests/live/m5b_pressure_probe.py`（真机 **8 工具 × 10 问，约 20~40 分钟**）；`SKIP_LLM=1` 只验夹具与判定逻辑（几秒） | （★ 现行口径：**不做自动判定** 跑完看「序列变化清单」+ 人工打分；历史结论「8/8 正例 + 2/2 反例」属 v1 自造桩时代，见 `docs/v2.0/M5b工具检索路由.md` §11.5） |
| `_m5b_route_calib.py` | **M5b 阈值标定脚本**（不算测试用例）：8 个异构工具 → 逐问句打印「向量 top3 / z 分数 / 词法命中 / 是否路由 / 命中集」，并给出 `MAX_FULL_CARDS` 1/2/3 对应的注入量敏感性（51% / 59% / 66%）。**改 `tools/tool_router.py` 里的阈值必须重跑它** | `.venv/Scripts/python.exe tests/live/_m5b_route_calib.py`（`--keep` 保留临时账号） | — |
| `m5_probe_e2e.py` | **M5a 探针组（唤醒率量化）**：隔离账号 + 假可执行名探针 CLI（本机未装 → 执行失败但**调用尝试照样进审计日志**）→ 自检（路由卡/示例注入 + 审计链路）→ **10 条自然语言正例**（问「看看我最近在读什么书」等，断言审计里出现该 CLI 且 argv 命中期望命令，主命中/备选命中都算过）→ **3 条反例**（天气/写诗/概念解释，断言没误调）→ 结论（正例 ≥9/10、反例 3/3）+ 结果落 `data/m5_probe_result.json` → 清理。**判定只看 `data/audit/commands.jsonl` 的调用事实，不采信模型自述** | `.venv/Scripts/python.exe tests/live/m5_probe_e2e.py`；`SKIP_LLM=1` 只跑自检；全量**真机 13 轮对话（约 10-20 分钟）** ⚠️ **不要在探针全量跑的同时另跑本脚本的 `SKIP_LLM=1` 自检**——自检会往审计日志写一条同 bin 的调用，而探针的窗口判定依赖审计计数，会污染「第一条探针」的判定（真值以库里 `messages.tool_calls` 为准）。 ⚙️ 夹具：探针可执行名用**能跑通的桩**（`tests/fixtures/m5_probe_bin/` 下的同名 `.cmd`，输出固定 JSON），否则「未安装」会诱发模型重试风暴、单探针拖到 5+ 分钟；判定**只看第一次调用**（路由决策），后续自纠仅记录不计入。 |
| `cli_dspro_e2e.py` | **CLI 版 dspro**：子进程真跑 `python -m cli.main`——帮助/落地页、未登录与错密码的退出码与提示、登录态文件（含「不含明文密码」与密码指纹）、`whoami`、`list` 四种形态（`--json` 可解析）、`digest` 参数解析与报错文案、**同名订阅歧义必须报错**、`chat --sessions/--skills`、`logout`；全量模式追加真跑 `digest`（断言落库 +1）与 `chat` 单轮 | `.venv/Scripts/python.exe tests/testclient/cli_dspro_e2e.py`（`SKIP_LLM=1` 只跑不耗模型的用例） |

## 六、接口层 / 冒烟（详细备注）

| 脚本 | 用途 | 前置条件 |
| --- | --- | --- |
| `m3_smoketest.py` | 接口层回归：登录 → 竞品清单 → 导出下载 | 服务已启动：`uvicorn api.server:app --port 8123` |
| `pre_work_test.py` | 环境自检（依赖 / 模型 / 库表） | 依赖已装 |
| `tool_agent_e2e_test.py` | 文档读写工具挂到全局 AGENT 后由 Agent 真实完成任务 | 无需服务；会调大模型 |

## 七、调试 / 探针（详细备注）

| 脚本 | 用途 |
| --- | --- |
| `_m5_real_weread_check.py` | **M5a 真实场景复核**（真实账号 + 真实 CLI）：三层证据一次到位——① 注入证据（打印该账号实际收到的能力路由卡 + few-shot 示例）② 路由证据（问一句自然语言，从库读 `messages.tool_calls` 真值配对）③ 执行证据（调用必命中只读白名单 + 回答含只可能来自 CLI 的真数据）；跑完删除自己创建的会话。`--question` 可换提问 |
| `m2_debug_run.py` | 进程内直接调 `_run_agent`，打印真实异常堆栈（绕过 FastAPI 包装） |
| `m2_ws_probe.py` | WS 链路隔离验证：连 `/ws/dbg` → 调 `/api/_test_emit` → 验证收到 monitor 事件 |
| `_verify_kb_testclient.py` / `_verify_kb_fixes.py` | M3 修复项复核（TestClient 直调） |
| `_check_login.py` / `_verify_kb_e2e.py` / `m35_attrs_test.py` | 登录、知识库、M3.5 属性字段的辅助核查 |
| `model_thinking_probe.py` / `thinking_capture_probe.py` | 思考内容（`reasoning_content`）抓取验证 |

## 八、注意事项

- 本机 `HTTP_PROXY=127.0.0.1:7897`：访问 localhost 的脚本须先清代理（`requests.Session().trust_env=False`；curl 加 `--noproxy "*"`）。
- 若本机存在其他名为 `agent` 的顶层包（如全局 venv 同名包）：脚本需把项目根 `insert(0)` 到 `sys.path` 并剔除冲突路径（脚本均已内置）。
- `/api/_test_emit` 是 `api/server.py` 中的临时调试端点（M2 验证 WS 用），正式使用阶段可移除。
- **本机浏览器自动化：原生 CDP 可用**（2026-09-16 实测更正）——`agent-browser` CLI 确实没装，但
 `chrome --headless=new --remote-debugging-port=9333 --no-proxy-server` + Python `websockets` 直连
 DevTools 协议完全可行（`scripts/cdp_skill_menu_probe.py` 就是这条链路：真实登录、派发真实键鼠事件、
 读计算样式与 `elementFromPoint` 命中测试）。⚠️ 两个坑：① 必须 `--no-proxy-server`（系统代理
 127.0.0.1:7897 会让 localhost 变 `ERR_CONNECTION_REFUSED`）；② 派发按键要用
 `rawKeyDown → char → keyUp`，`keyDown`+`char` 都带 text 会**插入两次**字符。
 纯视觉效果仍建议人工肉眼确认；静态逻辑仍用 `m4a_frontend_logic.js` 的「抽真实代码 + shim」。
- **真机交互探针**：`scripts/cdp_auth_probe.py`（登录/注册两卡分离，21 项）与 `scripts/cdp_skill_menu_probe.py`
 （`/` 技能菜单）是同一条链路的完整范例——**先起真服务、再起无头 Chrome，用真实键鼠事件点出交互，
 并断言副作用**（如「注册成功后账号是否真的建出来 / 打错用户名是否真的没建出来」直接查库）。
 ⚠️ 踩过的三个坑：① `StaticFiles` 有缓存，改完前端要么换端口要么带 query 强刷，否则**探针测的是旧页面**；
 ② CDP 探针里的选择器别用「按文案找 button」——登录页的 tab 与提交按钮文案都含「登录」，会点错元素
 （用 `button.bubbles` 这类结构性选择器）；③ **助手起的服务要和用户的服务分清**：若 `8123` 上已有一台
 旧代码的服务在跑，新起的会以 `[Errno 10048]` 静默退出，而健康检查照样通过（旧服务在应答）——换个端口
 （`APP_URL` / `--port 8124`）验证即可，别去 kill 用户的进程。

---

## 目录约定（2026-09-29 用户确认）

```text
tests/
├── unit/ # ① pytest 单元 / 静态契约（秒级，无需服务）
├── harness/ # ② 前端抽取式 harness（Node，无需服务）
├── testclient/ # ③ TestClient 端到端（进程内，不占端口）
├── live/ # ④ 真机 / 外壳 / 真窗口（慢，注意额度）
├── debug/ # ⑤ 调试 / 临时（`_` 前缀，可删）
├── fixtures/ # 基准与夹具（**不能放 data/**，data/ 被 gitignore）
└── test_out/ # 系统级测试产物（已 gitignore）
```

> ⚠️ 新增子目录需**用户同意**；文件归位规则见 `docs/文件存放规则.md`。

| 目录 | 放什么 |
|---|---|
| `tests/` 根 | **不放**散落的产物；离线单测与真机脚本见下（按类分子目录后以此为界） |

| `tests/fixtures/` | **基准与夹具**：路由池快照 / bench 基线（`router_bench_baseline.json`）· **未命中回归清单**（`router_bench_regressions.json` 供「路由质量修复」验收）· **探针主基线**（`m5b_probe_baseline.json` full 43 条）。⚠️ 不能放 `data/`——`data/` 已被 `.gitignore` 忽略，放那儿会丢 |

| `tests/test_out/` | **针对整个系统测试运行的产物**：探针日志/结果 JSON、静态预览截图等（已 gitignore，不入库） |
| `data/` | **运行时数据**（个人库 `personal.db`、沙箱、审计、缓存等），不是测试产物 |
| `output/{账号}/` | **agent（deep_search_pro 本体）的产物**：周报 md/pdf、导出、`tts/` 语音 |
| `pic/{账号}/` | 用户上传的背景图 |

**判据（来自用户口径）**：
- **测试账号产出的数据**按**正常账号**的存放规则对待（如 `output/m2tester/` 里的周报就是 normal 产物，不必特殊处理）；
- 只有**针对整个系统测试运行**的产物（探针、基准跑批、截图）才进 `tests/test_out/`；
- `output/` 的归属是"**deep_search_pro 这个 agent 的产物**"，所以不要拿它当测试垃圾场。
