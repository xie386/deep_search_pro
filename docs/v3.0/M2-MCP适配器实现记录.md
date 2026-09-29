# M2 · MCP 适配器 实现记录（v3.0）

> **状态**：✅ **已实现并实测通过**（2026-09-27）
> **对应方案**：`M1M2-API与MCP适配框架方案.md`（§五 契约 / §七 M2 五步）｜ **上一步**：M1（API 适配器）
> **一句话**：现在可以把任意 **MCP 服务**接成 Agent 的能力 —— 本地命令（stdio）或远程端点（http）→ 七步体检 → 发现工具 → 勾选确认 → Agent 用 `invoke_tool` 取真实数据；**没确认的工具，Agent 看不到也调不到**。

---

## 一、交付物（点得出来的文件）

| 文件 | 作用 |
|---|---|
| `pyproject.toml` | 引入 **MCP 官方 SDK v2**（`mcp>=2,<3`，实测装的 2.2.0）+ **固定 `opentelemetry-api<1.45`**（原因见 §三-1，别当垃圾清理掉） |
| `tools/_runtime/mcp_driver.py`（新） | 执行驱动：**三种连接**（`stdio` 命令 / `http` 端点 / `inproc` 进程内，后者仅测试用）+ **惰性导入 SDK** + **async→sync 桥接** + 命令白名单 + 超时/6000 上限 + 工具视图（`name/description/input_schema/read_only_hint`） |
| `tools/mcp_probe.py`（新） | **七步体检**：① 配置解析 ② 环境检测 ③ 协议握手 ④ tools/list ⑤ 异常容错 ⑥ 性能指标 ⑦ 汇总；顺带把工具表返回给"发现"复用（省第二次连接）。★ stdio 握手失败时会**抓子进程 stderr 的尾巴**拼进详情（否则只看到没用的 `ExceptionGroup`，见 §五-3） |
| `api/tools_sources.py` | 来源面打通两种来源：`source` 字段贯穿请求模型；`save` 分来源校验（mcp 复用 `validate_cfg`）；`test` 对 mcp 走七步体检；`discover` 对 mcp = 连上去列工具（**服务端自己就是文档**）；`candidates`/`confirm`/`delete` 按来源取 ref 前缀与写操作口径 |
| `tools/capability_pool.py` | `api_entry` 抽成通用构造器 `_entry_from_row`，新增 `mcp_entry` + `_entries_from_mcps`，`_ADAPTERS` 打开 `SOURCE_MCP`；`param_summary` 回落到 `input_schema.properties`（MCP 没有 `bindings`） |
| `tools/capability_invoke.py` | mcp 分派进驱动；**与 api 共用同一套收尾**（失败审计 / 组装 / 整段裁剪 / 成功审计）—— 这就是"复用 M1 五条链路"的落点；mcp 只读放行判定与 api 同口径 |
| `front/index.html` | **MCP tab**（照 CLI/API 范式）：指导栏（说明 + ⚠️ 安全口径 + 统计 chips + ＋新增）+ 每来源一张常态只读卡（卡内列出工具：✅/○ · 只读／⚠️写操作 · ref · 参数摘要）+ **两个弹窗**（来源配置 / 体检·发现结果）。**一处必须改的地方**：后端现在一次返回 api+mcp 两类来源 → 前端按 `source` 分流给两个 tab，否则 MCP 来源会串进 API 列表 |
| `tests/test_mcp_driver.py`·`test_mcp_probe.py`·`test_mcp_sources_api.py`·`m2_mcp_e2e.py`（新） | 见 §二 |

---

## 二、实测数字（全部真跑）

| 用例 | 规模 | 说明 |
|---|---|---|
| `tests/test_mcp_driver.py` | **13 项** | 进程内 list/call/结构化/写工具无只读提示/超大裁剪/未知工具 · **真起 stdio 子进程**的 list+call · 白名单拒绝 · 校验与超时纯函数 · ★ **2 条惰性导入不变量**（`import mcp_driver` 与 `import api.server` 都不得把 `mcp` 拉进 `sys.modules`） |
| `tests/test_mcp_probe.py` | **11 项** | 四条路径：**正常**（七步全绿）/ **命令不存在** / **协议不兼容**（满口胡话的假 server，必须快速失败）/ **超时**（装死 300s 的假 server + 5s 超时，必须在超时附近返回）+ http 连不上 / 配置不合法即收摊 + **子进程崩溃时的 stderr 取证（2 条）** |
| `tests/test_mcp_sources_api.py` | **12 项** | MCP 全链路离线：配置校验拒绝 · 列表投影（**env 只回键名，不回值**）· 体检七步 · 发现落候选**但不进池** · 两入口形状一致 · 写工具需显式勾选 · 入混合池（卡带 `invoke_tool` 示例 / 来源级能力描述 / 人工改名保留）· **真调用** · 未确认被拒 · 重复发现保留人工决定 · 删除连带 · 越权 404 |
| `tests/m2_mcp_e2e.py` | **64 项** | ★ **真机三路**：**A = 真实第三方 stdio server**（`uvx mcp-server-time`，PyPI 官方参考实现）· **B = 本地 stdio server**（零网络兜底）· **C = 真实 Streamable HTTP 服务**（魔搭 ModelScope 部署的拼多多选品，免 token；离线自动跳过），各跑一遍完整链路（保存→七步体检→发现→未确认被拒→写操作闸→确认→入池→**真调用拿到真实数据**→审计→重复发现幂等） |
| `tests/m1c_tools_sources_frontend.js` | **9 组**（新增第 9 组） | MCP tab：**分来源过滤**（MCP 不串进 API 列表）· 表单校验 5 类非法输入 · 发现请求带 `source='mcp'` · 只读默认勾/写操作不勾 · 确认体带 `source` · 体检不出候选 · 编辑回填/新建清空 · 模板结构（表单只在弹窗、卡片区不得出现表单字段） |
| 回归 | 离线 pytest **228 passed** · 静态不变量 **25/25** · 6 套前端 harness · `m5b_pool_e2e` **62/62** · `m5b_router_e2e` **34/34** · `m1_weather_e2e` **23/23** | 全绿 |

**真机采样（`mcp-server-time`）**：`list_tools` 15.6s（uvx 冷启动，含下载建环境）→ 2 个工具 `get_current_time` / `convert_time`，`read_only_hint` 均为 true；`call_tool(get_current_time, {"timezone":"Asia/Shanghai"})` → **1.4s** 返回 `{"datetime":"2026-09-27T01:55:07+08:00","day_of_week":"Sunday","is_dst":false}`。

**数据纪律**：临时账号用例结束即 `purge_account` · 真机 e2e 建的来源随账号一起清掉 · **不新建长期测试账号**（沿用既有 8 个）· 不烧 Tavily 额度（本里程碑零外部搜索）。

---

## 三、落地时发现并修掉的问题（9 条，都是"跑起来才露出来"的）

1. **装 `mcp` 会把 `opentelemetry-api` 顺带升到 1.45 → 知识库向量库（chromadb）直接崩**
   现象：`m5b_pool_e2e` 报 `ImportError: cannot import name '_ExtendedAttributes' from 'opentelemetry.util.types'`（1.45 移除了它）。
   → pyproject 固定 `opentelemetry-api<1.45`，并把原因写在依赖旁边（这行看着像"多余的锁"，删掉就复发）。

2. **`import mcp` 要 5.0 秒**（它拖 `httpx2` / `opentelemetry` / `jsonschema` 一堆重依赖）
   → 驱动**一律惰性导入**（只在真要连 MCP 时才 import），并加 **2 条不变量用例**钉住：`import tools._runtime.mcp_driver` 与 `import api.server` 都不得把 `mcp` 拉进 `sys.modules` —— 否则每次启动白付 5 秒。

3. **SDK v2 的真实形状与方案假设不同（三处）**
   - **纯 async**：`async with Client(...) as c: await c.call_tool(...)`，没有同步门面 → 做了 async→sync 桥接，并特别处理"**已在事件循环里不能直接 `asyncio.run`**"（换线程跑私有 loop）；
   - **`send_ping` 已被移除**（SDK 警告：as of 2026-07-28 仅在 legacy 模式可用，实测 `MCPError Method not found`）→ 体检改用 `list_tools()` 当健康检查（既证明握手成功，又顺带拿到工具表）；
   - 字段是 **snake_case**：`input_schema` / `structured_content` / `is_error` / `annotations.read_only_hint`。

4. **stdio 首次连接要给足超时**：子进程首先要 `import mcp`（≈5s，冷启动更久）——实测 **15s 超时会失败、20s 起才稳** → 来源默认 30s；用例里给 60s。

5. **命令白名单两侧要做同样的归一**（用例逮到）：只归一命令那一侧，用户填 `python.exe` 而白名单写 `python`（或反过来）就会被误拒 → 现在统一 `basename + 去 .exe + 小写`。

6. **来源表共享带来的三处"只改 API 会漏"的地方**（都在代码注释里标了）
   - `sources_list` 原来写死 `source='api'` → MCP tab 会永远是空的；
   - ref 前缀两种来源不同（api 是 `api:<短名>#<操作>`，mcp 是 `mcp:<短名>/<工具>`）→ 所有 `LIKE` 查询必须走 `_ref_like()`；
   - "是不是写操作"的口径不同（api 看 `method`，mcp 看 `read_only_hint`，且**只有明确 true 才算只读**）→ 抽 `_is_write_row(source, spec)`。

7. **前端必须按来源分流**：后端一次返回 api+mcp 两类来源，`tsLoad` 不在前端过滤就会把 MCP 来源显示在 API tab 里（M2 最容易漏的一处）→ 一份数据分给两个 tab，用例断言两边互不串台。

8. **两条过期断言（行为变化后必须同步口径，不是回归）**：① 提示文案从"写方法"改成"写操作"（MCP 没有 method）；② 参数摘要行现在会按 JSON Schema 列出全部参数（新增的回落逻辑，调用示例仍只列必填）。

9. **`timeout=0` 被 falsy 吞掉**（`0 or 30` → 30）→ 明确写成"**<=0 或非数字 = 未配置 → 用默认值**"，不留"0 等于立即超时"这种隐含语义。

---

## 四、怎么用（3 步，全在界面上）

1. **定制助手 → 🔌 MCP 工具接入 → ＋ 新增 MCP 来源**：选连接方式 ——
   - **stdio**：命令（如 `npx`）+ 参数（JSON 数组，如 `["-y","@modelcontextprotocol/server-everything"]`）+（可选）工作目录/额外环境变量/命令白名单；
   - **http**：端点 URL（如 `http://127.0.0.1:8000/mcp`）。
   保存后**自动进入发现**。
2. **🩺 体检**（真连一次：握手 → 列工具 → 用不存在的工具试一次容错）→ **🔍 发现工具** → 勾选要放行的工具（**只读默认已勾**；**没声明只读的按写操作处理**，要额外勾「我确认这是写操作」）→ **确认勾选**。
3. **问 Agent**：「现在几点（上海）」→ 路由器把这条能力注入给模型 → 模型调 `invoke_tool(ref="mcp:realtime/get_current_time", params={"timezone":"Asia/Shanghai"})` → 真实数据回来（审计落在 `data/audit/commands.jsonl`，与 CLI 同一份 jsonl）。

**边界**：白名单外的命令**不启动** · 未确认的工具**不入池、不可调用** · 未声明只读一律按写处理（规范明说 annotations 只是提示，客户端不得据此做安全决策）· 结果与 `run_shell_command` 共用同一套审计与体积上限（6000 字符整段上限）。

---

## 五、真实服务实测带来的四条观察（2026-09-27，用户接的两只真服务 + 一次填表踩坑）

> 用户接了两只真服务实测：**魔搭 ModelScope 的 Streamable HTTP 服务**（拼多多选品，免 token）+ **BnF/Gallica 的 stdio 服务**（2025-03 的老代码）。冒出三条**只有真机才会遇到**的问题。

2. **真实服务经常一个 `readOnlyHint` 都不声明**（魔搭那只：3 个工具全是 `None`）。
   按我们的保守口径 → 三个**查询类**工具全部按「写操作」处理，用户每个都要勾一次「我确认这是写操作」。
   这不是 bug（规范本来就说 annotations 只是提示、客户端不得据此做安全决策），但**引导上要讲清楚**：
   勾这个框的含义是「我批准放行它」，**不是**「它一定会改数据」。
   若将来觉得每个都勾太啰嗦，可选的口子是**来源级批量确认**，或**来源级"该服务全是只读"的显式声明**——
   这属于设计分叉，留给你拍板，我没有擅自加。
3. **stdio 服务起不来时，我们原本什么都看不到**（已修）：BnF 那只因为**它自己的 `requirements.txt` 钉得太松**
   （`fastmcp==0.1.0` 只要求 `mcp>=1.2.0`）→ 装成了 **mcp 2.x** → `from mcp.server.fastmcp import FastMCP` 直接崩，
   而 SDK 客户端只抛 `ExceptionGroup: unhandled errors in a TaskGroup` —— **真正的原因全在子进程的 stderr 里**，查了半天。
   → 体检的 ③ 步现在会在**握手失败且 transport=stdio** 时**把子进程原样跑一次、抓 stderr 尾巴**拼进失败详情
   （只在失败路径执行，happy path 零成本）；新增 2 条用例钉住（有输出的崩溃 / 一声不吭的退出）。
   同时这也是一条**依赖钉版本**的教训：给 MCP 服务建**独立环境**，别装进项目 venv（它钉死了 `requests==2.31.0`）。
   **这只服务已作为 stdio 的常备实验对象留在 `D:\LLM\mcp_servers\bnf`**（仓库 + 独立环境 `env/`：装了 `mcp<2`，
   另加一个只属于它的 UA 垫片 `env/Lib/site-packages/sitecustomize.py` —— Gallica 对 `python-requests` 默认 UA 返回 403、
   对浏览器 UA 返回 200，实测；★ 注意 `requests.sessions` 是按名导入 `default_headers` 的，两处都要 patch）。
   **另外两只自建常备实验对象（2026-09-27，由用户 `zuoye/day17` 的两个 MCP tool 服务化而来）**：
   - `D:\LLM\mcp_servers\route_planner` —— 路线规划（`plan_route`）：GeoNames 解析地名 + openrouteservice 出路线；
     实测 成都→上海 1922.6 km / 19.4 小时；默认只回摘要（约 500 字符），`include_raw=True` 才回 ~40KB 原始轨迹。
   - `D:\LLM\mcp_servers\city_lookup` —— 城市详情（`search_city`）：GeoNames 查询 + 中文字段映射；**不需要任何 key**。
   两只都：用官方 SDK v2 写、**声明了 `readOnlyHint=true`**（接进来不必勾"写操作"）、只做 stdio、各自带 `env/` 与 `selfcheck.py`。
   逐个字段的接入填法见各自 README。★ 它们还顺带暴露了两个可复用事实：① ORS 的 directions REST 接口**不认** `optimize_waypoints`
   （值给 false 也 400），原项目用 python 客户端时被静默丢掉才没踩到；② **返回类型标注会影响结构**：标注 `dict` 时 SDK 不生成
   output_schema、结果走文本块（裸字典），标注 `Optional[dict]` 时生成 schema 并**多包一层 `{"result": …}`**。

   用法：命令 = `D:\LLM\mcp_servers\bnf\env\Scripts\python.exe`，参数 = `["D:/LLM/mcp_servers/bnf/bnf_server.py"]`
   （**注意正斜杠**，见下条），工作目录 = `D:\LLM\mcp_servers\bnf`（这个字段是纯文本，反斜杠随便写），超时 120s。

4. **填「参数」时贴 Windows 路径 → 撞 JSON 转义**（用户实测第二个撞上的坑）。
   `args`/`env` 是 **JSON 文本**字段，而 `["D:\LLM\mcp_servers\bnf\bnf_server.py"]` 里 `\L` `\m` `\b` **都不是合法的 JSON 转义**
   → `JSON.parse` 直接失败。旧提示只说「参数必须是JSON数组」，**一个字没提到反斜杠**，用户只能猜。
   → 前后端两处都改成给出**可读原因 + 正确写法示例**（`参数（args）不是合法 JSON：Bad escaped character in JSON at position 4。
   Windows 路径请改用正斜杠，例如 ["D:/LLM/mcp_servers/bnf/bnf_server.py"]（或用两个反斜杠转义）`），
   并各加一条用例（前端 harness 断言**报错文案里必须有"正斜杠"** + 正斜杠写法必须能过；后端断言 `detail` 里含"正斜杠"）。
   **口径**：只有 `args` / `env` 是 JSON 字段要转义；`命令` / `工作目录` / `URL` 都是纯文本，反斜杠原生可用 —— 所以最省事的写法就是
   路径一律用**正斜杠**（Windows 上一样能用）。

5. **业务失败被包在成功响应里**（已在两只服务上各见一次）：`get_goods_detail` 传错 `goods_sign` → 返回 `{"result":"查询失败：… goodsSign解析错误(10002)"}`，
   而 MCP 层是**成功**（`is_error=false`）。这与 M1 踩过的「小虫API 错误不走 HTTP 状态码」是同一类问题 →
   能力描述里值得写一句「返回文本里出现『查询失败』即参数有误，需先用搜索工具拿到正确的 `goods_sign`」，
   否则模型会把失败当成数据。该服务还是一条**两步链**（`search_goods`/`explore_deals` 拿 `goods_sign` → `get_goods_detail`），
   和「weread 必须先 `book info` 拿 bookId」同款，值得写进能力描述。

---

## 六、遗留与下一步

- **MCP 教程**（像 API 那样带示意图）**没做**——用户本轮没要求；要做只需照 `front/tutorial/api_tools_tutorial.md` 的体例补一篇 + 挂一个「📘 教程指导」入口。
- ~~Streamable HTTP 没有真机~~ → **已补上（2026-09-27）**：用户接了一只真实的魔搭 ModelScope Streamable HTTP MCP 服务（拼多多选品，免 token），`m2_mcp_e2e.py` 的 **C 段**固化这条路径。实测：握手 2.2s / `tools/list` 0.6s / 调用 `search_goods` 3.9s，返回 4.4KB **未触上限**。
- **每次调用一条新连接**：stdio 会**重启子进程**（冷启动可能几秒）。会话缓存（长活连接 + 看护）是明确的后续优化项，本轮按"无状态、不用看护进程"的取舍实现。
- **只做 tools**：MCP 的 `resources` / `prompts` / `tasks` 未接入。
- **`sse` transport 未支持**（SDK 仍留，规范已转 Streamable HTTP）；`inproc` 仅供测试。
- **`uvx` 首次冷启动慢**（15.6s，含下载建环境）——体检/发现会把这段时间算进超时，来源超时因此默认给到 30s 起。
- **一个坑值得记**：跑 MCP 相关脚本时若 `PYTHONPATH` 被污染到别的 venv，会出现 `No module named 'anyio._core._streams'` 这种"看着像没装 SDK"的假故障 —— 用 `env -u PYTHONPATH` 跑即可（与 M1 记录里的跨 venv 坑同源）。
