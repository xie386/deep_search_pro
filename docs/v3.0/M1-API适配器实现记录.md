# M1 · API 适配器 实现记录（v3.0）

> **状态**：✅ **已实现并实测通过**（2026-09-25）
> **对应方案**： `M1M2-API与MCP适配框架方案.md`（§五 契约 / §六 M1 六步）｜ **下一步**：M2（MCP 适配器，复用本记录的全部链路）
> **一句话**：现在可以 **零代码**把任意 OpenAI 风格之外的真实 HTTP API 接进来 —— 填地址与鉴权 → 粘贴 OpenAPI → 勾选操作 → Agent 用 `invoke_tool` 取真实数据； **没勾选确认的操作，Agent 看不到也调不到**。

---

## 一、交付物（点得出来的文件）

| 文件 | 作用 |
| --- | --- |
| `tools/schema_personal.py` | 新表 `tool_sources`（api/mcp 共用来源表， `config_json` 类型扩展位）+ `tool_capabilities` 补 5 个槽位（`invoke_spec`/`input_schema`/`read_only`/`confirmed_at`/`cost_hint`）+ 老库幂等迁移 |
| `tools/openapi_import.py`（新） | OpenAPI(JSON/YAML) → 候选能力： `$ref` 递归内联、 `operationId` 兜底命名、path 级参数合并、写方法标记、json/form 请求体、cookie 降级 |
| `tools/_runtime/api_driver.py`（新） | 执行驱动：bindings 四位置分发（path/query/header/body）+ 鉴权注入 + 超时/1MB 上限 + SSRF 三项口径 + 直连→代理两臂兜底 |
| `tools/capability_invoke.py`（新） | **统一执行网关** + Agent 唯一新增工具 `invoke_tool(ref, params)`：归属/确认/启用校验 → 参数校验 → 放行判定 → 驱动执行 → 结果裁剪 → 审计 |
| `tools/capability_pool.py` | `api_entry()` + `_entries_from_apis()` + `_ADAPTERS` 打开 `SOURCE_API`；契约补 4 字段；api 条目的措辞/参数摘要（**CLI 文本逐字不变**） |
| `tools/tool_router.py` | 含非 CLI 来源时， **快路径也从统一池渲染**（修掉"池小时 API 能力对模型隐身"，见 §三-4） |
| `agent/build_context.py` | 注入块：出现 API/MCP 卡片时换标题并补 `invoke_tool` 用法（**只有 CLI 时逐字不变**） |
| `api/tools_sources.py`（新） | 配置面业务逻辑：来源 CRUD / 体检 / 发现候选 / 人工确认 |
| `api/server.py` | 7 个端点（`/api/tools/sources`、 `/source/save`、 `/source/{id}`、 `/source/{id}/test`、 `/source/{id}/candidates`、 `/discover`、 `/confirm`）+ `invoke_tool` 注册进两处工具列表 |
| `front/tutorial/api_tools_tutorial.md` + 4 张示意图 SVG（新） | **模块自带教程**：进入「🔗 API 工具」页右上角「📘 教程指导」；内容 = 前端操作步骤 + **怎么自己写最小 OpenAPI 文档**（骨架 / 逐字段 / 三种参数位置 / 三份可抄文档 / 六个坑）+ 验证与安全边界 + FAQ。取静态资源（`/front/tutorial/...`）， **不需要改后端** —— 复用教程阅读页， `loadTutorial(force, kind)` 新增 `kind='api'` 分支、 `tutBack()` 回到来的 tab |
| `front/index.html` | 定制助手新增「🔌 工具来源（API）」卡片：来源列表 + 表单（含鉴权/超时/说明）+ 粘贴 OpenAPI + 候选勾选（写操作需二次确认）+ 样式 `.ts-*` |

---

## 二、实测数字（全部真跑）

| 用例 | 规模 | 说明 |
| --- | --- | --- |
| `tests/test_api_driver.py` | **26 项** | 全离线（`httpx.MockTransport`）：参数四位置、模板占位与编码、鉴权、超时、截断、SSRF 三项 |
| `tests/test_openapi_import.py` | **18 项** | 三份真实形态 spec 片段（含 `$ref` / 无 `operationId` / cookie 参数 / form 体 / 重名） |
| `tests/test_pool_mixed_sources.py` | **13 项** | CLI 文本逐字冻结 · api 措辞与参数摘要 · 未确认不进池 · `_entry_text` 差异 · **路由器快路径两条回归** |
| `tests/test_capability_invoke.py` | **24 项** | ref 解析 · 四类拒绝 · 类型/枚举 · 写能力放行 · 成功路径 · **审计行字段** · `invoke_tool` 入参契约 |
| `tests/test_tools_sources_api.py` | **20 项** | 来源 CRUD（幂等/掩码/校验）· 候选落库但不进池 · 确认闸（只读直过 / **写方法必须显式勾选**）· **重复粘贴保留人工决定** · 删除连带 · 体检 · 隔离 |
| `tests/m1c_tools_sources_frontend.js` | **8 组**（约 80 断言） | 抽取真实 JS 块：表单→请求体（8 类非法输入）· 勾选→确认体（写操作二次确认）· 保存/体检/删除/解析/确认 的真实调用序列 · **弹窗范式**（常态无表单／打开导入即列已落库工具／关闭不留表单）· 模板结构（含"来源卡区域内不得出现任何表单字段"） |
| `tests/m1_weather_e2e.py` | **23 项** | ★ **真机**：自起临时后端 → 建来源 → 体检 → 粘贴 OpenAPI → 候选（未确认不进池）→ 确认入池 → 路由器注入 → **`invoke_tool` 真调用 Open-Meteo 拿到气温** → 审计行 → 清理并杀进程 |
| 回归 | `m5b_pool_e2e` **62/62** · `m5b_router_e2e`**34/34** · 离线 pytest**182 passed** · 前端 harness 6 套（56/42/31/51/24 + m1c）· 静态不变量**10/10** | 全绿 |

**数据纪律**：用库里 **既有**会话 token（不猜密码、不新建测试账号）· 临时账号在用例结束时 `purge_account` · 真机 e2e 建的来源 **用完即删**（池回到 3 条 CLI）· 临时后端 **结束即杀**（不动用户在跑的 8123）。

---

## 三、落地时发现并修掉的 4 个问题（都是"跑起来才露出来"的）

1. **`confirmed_at` 语义陷阱（会锁死现有功能）**
 老库升级后 **CLI 行的 `confirmed_at` 恒为 NULL**。若按方案字面"NULL = 不可调用"一刀切，**现有 3 个 CLI 工具会全部失效 **。
 → 定死： **确认闸只对 `source in ('api','mcp')` 生效**；CLI 照旧走 `user_clis` 只读清单。已写进 schema 注释与方案 §5.2。

2. **api_driver 截断 bug**
 响应 **单个 chunk** 就超过 1MB 时，只 `break` 不裁剪 → `bytes`/`text` 仍是全量（用例逮到）。
 → 改成真截断（`raw = raw[:max_bytes]`）。

3. **langchain `@tool` 劫持 `args`**
 参数名写成 `args` 会被换成模型看不懂的 **`v__args`**（实测 schema 变成 `ref` + `v__args`）。
 → 改名 `params`，并在用例里断言入参 **恰为** `{ref, params}`（防回归）。

4. **路由器快路径让 API/MCP 对模型隐身**（最隐蔽的一个，真机 e2e 逮到）
 `route_tools()` 在"池 ≤ FAST_PATH_MAX"时直接用 `cli_reg.agent_brief()`（**CLI 专属**）→ 3 CLI + 1 API = 4 条走快路径，注入文本里 **没有那条 API 能力**（配好了却调不到）。
 → 含非 CLI 来源时从 **统一池**渲染（`_full_pool`），纯 CLI 时保持 `agent_brief` **逐字一致**；两条回归用例钉住。

> 另： **SSRF 口径**按用户裁决落地为分层（用户填的 base_url 默认允许内网/环回， `allow_private=false` 可收紧； **模型可控的 URL 参数**与 **跨主机重定向**一律拒），与方案初稿的"一律拒绝"不同，已在 §五 上方标注。

---

## 四、怎么用（3 步，全在界面上）

1. **定制助手 → 🔌 工具来源（API）**：填「短名（如 `openmeteo`）/ base_url / 鉴权」，保存 → 点 **体检**（只校验配置，不联网）。
2. **粘贴 OpenAPI** 文档（JSON 或 YAML，浏览器里解析、不外发）→**解析候选 ** → 列表里勾选要放行的操作（**写操作默认不勾**，要勾必须先点「我确认这是写操作」）→ **确认勾选**。
3. **问 Agent**：「成都今天天气多少度」→ 路由器把这条能力注入给模型 → 模型调 `invoke_tool(ref="api:openmeteo#getForecastCurrent", params={...})` → 真实数据回来（审计落在 `data/audit/commands.jsonl`）。

**边界**：未确认的能力 **不入池**（既不被路由，也无法调用）· 写能力即使确认了，卡片上也会带「⚠️ 会改外部状态」提示模型先确认用户意图 · 结果与 `run_shell_command` 共用同一套审计与体积上限。

---

## 五、实测后补修（2026-09-26，用户真机验收提出）

用户在真机上接自己的 API（`xxapi` 抖音热榜 / IP 查询）实测后提的问题，逐条对应到这里。

| # | 现象 | 根因 | 修法 |
| --- | --- | --- | --- |
| 1 | 源那行显示「待确认 N」，但 **点不进这个列表** —— 想确认候选只能把 OpenAPI 再贴一次 | `/api/tools/source/{id}/candidates` 端点实现了， **前端没有任何入口** | 源行加「查看能力/待确认」按钮 → 读回 **已落库**的能力与候选，复用勾选 UI（★ **默认一律不勾**，避免"打开列表 = 顺手确认"） |
| 2 | 刚贴完文档那一步，POST 类候选显示成「只读」、写操作的勾选框也不被禁用（服务端仍会拒，但 **界面在骗人**） | `discover` 直接把解析器条目吐出去（只有 `read_only`，没有 `is_write` / `param_summary`），与 `candidates_list` 的形状 **不一致** | 抽 `_cand_view()` 统一两个入口的返回形状（状态以库为准）； `pytest` + 真机 e2e 各加一条"形状一致"回归 |
| 3 | 结果裁剪写着 6000 上限，实测 6085 | 只裁 payload，前缀与尾注没算进去 | 上限改为对 **模型看到的整段文本**生效（含极端兜底） |
| 4 | 模型"选择工具"这一步偏慢（3 分钟） | **怀疑**：API 卡片没有可直接照抄的调用示例（CLI 卡片一直有），模型得自己推 ref/参数怎么摆 | 卡片加一行 `→ 调用示例：invoke_tool(ref="…", params={…})`。 **这是怀疑点、不是已证结论**，由用户用简单接口复测 |
| 7 | 缺少 **模块自带教程**（CLI 有「📘 教程指导」，API 没有） | M1 只做了界面，没做"怎么用"的文档；而这块恰恰是最需要教的（OpenAPI 文档要自己写） | 写 `front/tutorial/api_tools_tutorial.md`（8 节 + FAQ + 图片清单）+ 4 张手绘示意图 SVG，挂到指导栏的「📘 教程指导」；教程阅读页支持两篇（`kind`），返回回到来的 tab。 **图片用示意图（SVG，随项目版本管理）**，想换真实截图按文档附录清单替换 |
| 6 | 配置页"乱"：表单 **常态存在**、跟列表粘在一起，源标题与工具列表被表单隔开 | 我把「源表单 + 粘贴框 + 候选」三块都平铺在页面上，没学「自定义 CLI」那种"常态只读列表 + 点按钮才出表单"的范式 | 按 CLI 范式重做： **常态** = 指导栏（说明 / ⚠️ 安全口径 / 统计 chips / ＋新增）+ 每来源一张只读卡（卡内直接列出它下面的工具：✅已确认 · ○待确认 · 只读／⚠️写操作 · ref · 参数摘要）； **表单与导入都进弹窗**（复用 `.mask` + `.modal`，来源一个、导入一个），点遮罩即关。 **MCP 自配沿同一范式** |
| 5 | 抖音接口返回 52KB → 模型只看到前 6000 字符（约 6 条） | JSON 缩进白占字符 | 改紧凑 JSON。实测同 6000 字符条目 **5 → 6 条**， **提升有限**：体积主要在 `word_cover.url_list` 这类长 URL |

**"慢"的定位（有据，非猜测）**：驱动层 231~335ms（审计 jsonl）· 路由 `route_tools` **191ms**（池 ≤4 走快路径，不调模型/不查向量）· `invoke_tool` schema**771 字 **（比 `run_shell_command` 的 1225 字还小）→**三段都不慢 **；慢在模型侧消化**低密度 JSON**（52KB 截到 6000 字符、开头全是封面 URL）+ 会话历史长度（那次会话已 76 轮）。另外 `messages.created_at` 同一轮内时间差为 0 秒 →**每轮分段耗时目前不落库 **，精确归因需要 M4 的计时埋点。

| 8 | 第 7 条之后又验证了第二个真实源（小虫API · Steam，鉴权放 query 的 `token`），发现两处 **只在真机才看得见**的质量问题 | ① **来源级「能力描述」被埋没**： `api_entry` 原来只在工具级 `abilities` 为空时才回退到来源级 → 用户在来源上按"自己会怎么问"写的那句话 **完全不起作用**，卡片上只剩文档里的技术名（"查询Steam游戏资料"），直接影响路由命中；② **参数全是可选时调用示例退化成 `params={}`**，模型不知道要传什么 | ① 两级**合并 **（技术名在前、用户的话在后），关键词优先从来源级取；② 示例改为"必填优先，无必填时用第一个参数占位"（枚举取首值）。各补 1 条回归用例（`test_api_abilities_merges_source_level_text` / `test_call_example_uses_first_param_when_none_required`） |

> **第二个真源的实测结论**：鉴权放 query（`?token=`）→ 配置里选「Query 参数里带」+ 参数名 `token`， **token 由来源注入、模型永远看不到**；该站错误 **不走 HTTP 状态码**（一律 200，业务码在正文 `code`：504 缺 token / 505 token 无效）→ 提示词/能力描述里要写明这点，否则"调用成功"会被误读。

| 9 | 再验一个第三方源（小虫API · Steam， `https://api.ovo1.cc/api/Steam`）时 **站方接口坏着**：token 有效（不再 504/505），但任何参数组合都返回 `code:500 获取Steam数据失败 `（UA/Referer/等待 30s 重试均无效） | 供应商侧上游抓取失败， **与本项目无关** | 判定为"站方不可用"→ **没有把它存进用户库**；改用 **Steam 官方公开接口**（`store.steampowered.com/api/*`，免 key）做等价能力，真机 13/13 通过（搜索返回 ¥298.00、详情用 `filters=price_overview` 把几万字符压到 250 字符）并留在用户库中可用 |

> **胖接口的正解**（本轮实战经验，已写进教程 §5.4）：先看供应商有没有 **自带的字段裁剪参数**（Steam 的 `filters=`），比"换个问法"有效得多 —— 也说明 M1 不做自动字段投影是合理的（供应商给了就不必我们猜）。

| 10 | 用户报障： **带参数的工具用不了**，报错 `params: Input should be a valid dictionary`（不带参数的 `douyinHot` 正常，所以"工具看着在列表里却调不动"） | 模型把 `params` 传成了 **JSON 字符串**（而不是对象）。更深一层： `@tool` 的 **入参校验发生在我们的函数体之前**，而 langchain 的 `tool_call_schema` 是它 **自己重新生成**的模型 —— **不保留**我们写的 `field_validator` → 只加校验器没用，必须在 **字段类型**上放宽 | 三层兜底：① `InvokeArgs.params` 类型放宽成 `dict \| str \| None`（`null`/省略也要放行——无参数工具模型常发 `null`）；② `mode="before"` 校验器把字符串 `json.loads` 成对象；③ 网关 `coerce_params()` 再兜一次，非法类型给 **可读中文原因**（不再吐 pydantic 黑话）。新增 4 条用例，真机复现用户姿势（字符串）已能取到 Steam 真实价格 |

> **教训（可复用）**： `@tool(args_schema=…)` 的字段 **类型注解**才是"模型能写什么"的真正闸门 —— 自定义校验器在 langchain 重新生成的 `tool_call_schema` 里会 **丢**。要兼容模型的写法，先放宽类型，再在函数体里归一。

| 11 | 接好 Steam 源后真机又暴露两处：① `searchGame` **返回空结果** `{"total":0,"items":[]}`；② `gameDetail` 查"Blasphemous"返回了别的游戏 | ① **承重参数被当成可选**：Steam `storesearch` 不带 `cc/l` 就是空结果（实测），我把它们声明成了 optional → 模型不传 → 空；② **不是我们的问题**：appid `942300` 确实是 `✌ Johnny Rocket`（工具如实返回），是 **模型自己猜错了 Blasphemous 的 AppID**（真值 774361）—— 它自己也承认"需要正确 App ID"。另一层隐患： `url_template` 自带 query 时，httpx 的 `params=` 会**替换 **掉原有 query → cc/l 被丢 | ① 支持**固定 query 参数 **：路径里写 `?cc=cn&l=schinese`（导入器拆成纯路径 + `static_query`）或操作级 `x-static-query`； `gameDetail` 同样固化（否则价格货币跟着请求地区变成 MYR——实测踩到）；② `gameDetail` 的描述里写明**"appid 必须先用 searchGame 查，不要凭记忆猜"**；③ 驱动兜底：模板里的 query 一律并进 `params`（模型显式参数优先）；④ 顺带把源超时 30→60s（Steam 偶发 20~32s）。各补回归用例（导入器 2 条 / 驱动 2 条 / 配置面 1 条） |

> **两条可复用教训**： **(a)** 供应商文档里标"可选"的参数**不一定是可省的 ** —— 固定值（地区/语言/货币/版本）应当固化进能力定义，而不是指望模型每次都填对；**(b)** 只要你的 HTTP 客户端在传 `params=`，就**必须 **把 URL 模板里已有的 query 并进 `params`，否则它会被替换掉（这类丢参数在日志里完全看不出来，只表现为"接口返回空"）。

## 六、遗留与下一步

- **M2（MCP 适配器）**： `mcp_driver` + `probe()` 七步体检 + `list_tools` 发现 + 前端 MCP 表单（`mcpServers` 整段粘贴）。 **本记录里的候选/确认/入池/审计/放行五条链路 M2 全部复用**，只需再加一个 driver 与一个发现器。
- **方案里的"填 URL 由后端抓取"**：按用户裁决**M1 只做粘贴 **（`O2`），不做 URL 抓取。
- **`cost_hint` 列**：已建好但还没写入（等 M6c 成本表用）。
- **候选的字段级裁剪/投影**（如只取 `word`/`hot_value`）：对"响应体很胖"的 API 有价值，但它属于 M5/M6c 之外的 **新功能**，不塞进 M1 —— 已登记待议。
- **真机 e2e 只接了 1 个公开 API**：多 API / 写方法 / 失败限流三条真机路径未覆盖（写方法在离线用例里覆盖，限流属阶段 2 范畴）。
