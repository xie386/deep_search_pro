# v4.0 · 06 Agent 工作目录前端可编辑可视化与写能力开发 · 实现记录

> **性质**：本大点的**实现记录**（问题 → 方案 → 结果）。裁定见 `../Verdict/06-Agent工作目录前端可编辑可视化与写能力开发.md`；调研推导见 `../research/06-Agent工作目录前端可编辑可视化与写能力开发.md`（实现期不通读）；逐文件带读见 `06-Agent工作目录前端可编辑可视化与写能力开发·逐文件带读.md`。
> **一句话**：把「工作目录」从 Agent 内部的一个沙箱路径，做成**用户能在前端看见、能逐级点开、能直接编辑**的真目录；并把「写能力」拆成**内置写工具（纯文字可用）**与**自配来源写工具集（靠「/」显式授权）**两套口径，最后让 workflow 也能声明自己的写工具集。
> **口径**：内置写工具不需要「/」授权（裁定 11）；自配来源的写命令、写能力**不进路由池、不进提示词简报**，只在本轮被「/」选中时放行。

---

## 一、改了哪些文件

| 文件 | 改动 |
|---|---|
| `tools/workdir_guard.py`（新增） | 路径闸：归一化后强制前缀校验、保护名单 fail-closed、凭据红线（`*key*`/`token`/`secret`/`credential`/`password` 与点开头目录连读都拒）、体积闸 |
| `tools/workdir_tools.py`（新增） | 内置写工具 `workdir_create` / `workdir_edit` / `workdir_delete`（删除只进回收站，可还原）、`workdir_read` / `workdir_list` |
| `tools/workdir_store.py`（新增） | 多目录登记（显示名 + 真实路径 + 启用标记）、「主页」自愈、`active()` 只读兜底、回收站索引 `_index.json` 与 TTL |
| `api/workdir_api.py` + `api/server.py` | `/api/workdir/*` 全套：列表 / 逐层树 / 读（可编辑·只读预览·仅下载三档）/ 下载 / 写 / 重命名 / 移动 / 删除 / 回收站（list/restore/purge/empty）/ 加目录（先风险说明再登记） |
| `front/index.html` | 左栏「📁 工作目录」面板 + 三用居中弹窗（文件 / 回收站 / 加目录）+ 一键收起左右栏 + 「/」三组扁平键盘索引（技能 → 写工具集 → workflow）+ 用户气泡右下角 `类别:名字` 标签 |
| `utils/write_auth.py`（新增） | 本轮「/」授权的写工具集中立上下文（api 写入、agent 与 tools 读取） |
| `api/write_pool.py`（新增） | 写工具集清单、`compose_write_sets_text()` 授权说明书（CLI 给 `run_shell_command`，api/mcp 给 `invoke_tool` 并列出可用 ref） |
| `tools/capability_pool.py` | 路由池 / 写池的派生与统计；`set_pool` 级联到能力行（cli 按 bin、api 按 `api:<slug>#`、mcp 按 `mcp:<slug>/`） |
| `tools/capability_invoke.py` | 执行网关收尾：写操作显式标注、图片字节落盘、正文里的图片地址自动下载、结果裁剪与审计 |
| `tools/_runtime/api_driver.py` | 图片响应不再按文本解码；只读能力的「图片类跨主机 302」窄放行（剥鉴权头） |
| `tools/_runtime/shell_runtime.py` | `execute(..., allow_write_pool=False)`：写池 CLI 只在本轮「/」选中时带 `write_rules`，CLI 面板手动试跑是唯一例外 |
| `tools/cli_registry.py` | CLI「写命令清单」`write_cmds`（填了自动进写池，只升不降）、`authorized_entry_for()`、`readonly_entry_for()`、`agent_brief()` 与 `ability_examples()` 排除写池 |
| `api/tools_sources.py` | 发现/确认按写语义给池初值；`set_pool` 级联；写工具确认时自动进写池 |
| `api/workflows.py` + `agent/workflow_runner.py` + `api/server.py` | workflow 顶层 `write:` 声明写工具集 → 本轮按「/」授权、说明书进每一步提示、跑完还原 |
| `agent/conversation_store.py` | `save_turn(..., picks=)`：用户消息也存 `additional_kwargs`，历史回传 `类别:名字` 标签 |
| **新增测试** | `test_workdir_guard.py` · `test_workdir_tools.py` · `test_workdir_api.py` · `test_pool_readonly_redline.py` · `test_write_pool.py` · `test_cli_write_cmds.py` · `test_picks_persistence.py` · `test_static_scope_gate.py` · `test_image_returns.py` 等 |
| **未动的** | 路由/精排算法本身、素材层、`data/personal.db` 的既有数据、`.env` 里既有密钥的值 |

## 二、关键落点

1. **工作目录只做「真实目录」**：登记就是登记，不复制、不接管磁盘内容；默认那条叫「主页」，指向该账号自己的沙箱，且**不可移出**。
2. **三重闸都在代码里**：路径逃逸（归一化后前缀校验）、保护名单 fail-closed（名单自身硬保护，删了会拦死所有写入）、凭据红线（只标记不提供内容）。这三条与「授权」无关，任何来源都过。
3. **写能力两种口径**（裁定 11）：内置 `workdir_*` 纯文字直接可用（此前被运行期闸拦、而「/」第二组又只列自配来源 → 两条路都不通）；自配来源的写命令与写能力必须「/」显式选中。
4. **写池与路由池彻底分开**：来源归写池后，它的**只读**命令也不再进路由池、不进 CLI 简报、不进示例块，只能靠「/」唤起；CLI 面板的手动试跑是唯一例外。
5. **授权说明书要教对调用方式**：CLI 走 `run_shell_command(command=…, argv=[…])`，api/mcp 走 `invoke_tool(ref=…, params={…})` 并列出该来源下已确认的工具 ref —— 否则模型会把来源名当可执行文件。
6. **写能力工作流**：workflow 顶层 `write:` 声明 → 本轮合并进「/」授权 → 说明书进每一步提示 → 跑完还原；声明了账号里没有的名字只提示、不中断。
7. **工具返回图片**：字节响应与「正文里的图片地址」两条路都在执行网关收口，统一落 `<工作目录>/_tool_images/` 并附 `[[img:…]]`，前端渲染成图；下载失败静默降级，绝不让调用失败。
8. **进度与留痕**：workflow 步骤事件推右栏步骤卡；每轮落库（含 `/` 选择）；工具调用统一进 `data/audit/commands.jsonl`。

## 三、实测数字（收官读数）

| 项 | 读数 |
|---|---|
| 全量单测 | **900 passed / 1 skipped**（本点起点 850；新增 50 条：工作目录三件套 + 写池红线 + 写命令清单 + picks 落库 + 静态作用域闸 + 图片返回 + workflow 写集 + 302 放行） |
| 前端 harness | **15 个全部通过**（含「技能最后一项再按 ↓ 进入 workflow 组」跨组键盘用例，61 项） |
| 前端导出不变量 | **26 项通过 / 0 失败** |
| 真机（桌面壳，用户逐项点） | B1~B13 全段通过（登录、三列与收起、工作目录、弹窗、回收站、「/」三组、写工具池、workflows、CLI 联动、其它页面、账号隔离、对话主链、桌面壳专属） |
| 写能力真机 | GitHub Issues：`listIssues` 200、`createIssue` 201、`commentIssue` 201（issue #6） |
| 图片返回真机 | 小小API：JSON 模式的地址经自动下载落盘（148,834 字节 JPEG）；`?return=302` 经窄放行拿到 173,064 字节 JPEG |
| CLI 写命令 | `officecli` 写子命令只在「/」选中时放行；未选中时同样的命令被拒 |

## 四、本轮修掉的 bug / 事故（14 个）

| # | 现象 | 根因 | 归属 |
|---|---|---|---|
| 1 | 登录页白屏 | `wd*` 脚本块在备份/还原时整段丢失（`$TMPDIR` 为空导致还原成了空路径），`setup` 里引用未定义函数 | 本轮引入，恢复函数块 + 补「return 里每个名字都在 setup 内定义」守卫 |
| 2 | 三列布局塌陷（中栏全空在左边） | `.col-left` 从未闭合（早前提交引入） | 历史遗留，补闭合 + 「视图不被困在左栏」用例 |
| 3 | 回收站面板永远空 | 前端用 GET 调 `/api/workdir/trash`（实为 POST）→ 405 | 本轮引入，改 POST；顺带修 `/api/logout` 同样的 GET/POST 误用 |
| 4 | 内置写工具「怎么问都用不了」 | 运行期闸一律拒，而「/」第二组只列自配来源 —— 两条路都不通 | 本轮设计缺口，按裁定 11 改为内置写工具不需要授权 |
| 5 | workflow 轮对话消失（刷新即无） | workflow 分支提前 `return`，`save_turn` 在其后；且 `cs` 作用域错被 `except` 吞成一行日志 | 本轮引入，落库前移 + `_save_turn_safe()` + ruff F821 静态闸 |
| 6 | 45,284 字回复反复复述人格/画像 | 每步流式输出灌进同一气泡，自述块（SESSION INTENT/SUMMARY）进下一步 `prev_out` 自我强化 | 本轮引入，步骤改非流式 + `strip_echo()` + 单步封顶 + 输出纪律 |
| 7 | workflow 一小时停在第 3/10 步 | 每步重灌 SQLite 历史、思考区只增不清、10 步 + 6 次进站 | 本轮引入，`include_history=False`、每步清思考区、流程改 5 步、检索改 curl |
| 8 | CLI 面板报「officecli 不在白名单」 | 写池 CLI 被 `allowed_for_agent()` 整条排除，连只读命令都跑不了 | 本轮引入，新增 `readonly_entry_for()` + 三段取条目顺序 |
| 9 | 写池只读命令泄漏给 Agent | `agent_brief()` 只看 `live`；`allow_write_pool` 未区分调用方 | 本轮引入，简报排除写池 + 执行入口默认拒绝 |
| 10 | MCP 写工具落只读池 / 归写池后路由条数不变 | 发现/确认未按写语义设池；`set_pool` 只改来源表、不级联能力行 | 本轮引入，写语义初值 + 只升不降 + 级联 |
| 11 | 「AI 预写全部描述」报 `Unexpected token 'I'` | `tool_listing()` 把 JSON 字符串当 dict → 未捕获异常 → FastAPI 返回纯文本 500 | 本轮引入，`_as_dict()` 容错 + 前端 `r.json()` 容错 |
| 12 | MinerU 写集「/」选中后仍被拦 | 授权说明书没写 MCP 的调用形态，模型把来源名交给 `run_shell_command` | 本轮引入，说明书按类型分派并列出可用 ref |
| 13 | 小小API 图片接口报「重定向到另一个主机」 | 解析样例把 `return=302` 写成唯一枚举值，模型照抄 → 302 跳 CDN → 跨主机重定向策略拒 | 本轮引入，样例与能力行去掉该参数；另加只读能力的图片窄放行 |
| 14 | workflow 列表有内容、点开编辑框空白 | 手工放入的文件名带空格，`wf_safe_name()` 规范化成下划线后按名读取找不到；前端读失败静默 `return` | 本轮引入，加原始文件名兜底（get/save/delete）+ 失败提示 |

## 五、遗留与边界（如实记录，不粉饰）

1. **api/mcp 写能力的硬闸只有「人工确认」一条**：写池保证它不进路由池与提示词，但模型若凭记忆写出 ref，`invoke_tool` 仍会放行。要收紧成「写能力必须本轮被「/」选中」，需要改网关判定链（会同时影响 MCP 与已确认的写 API）。
2. **跨主机重定向只对「图片」放行**：签发型接口（SigV4）、OAuth2 授权码类应用仍配不了 —— 这两类需要额外的鉴权实现，不在本点范围。
3. **图片上限沿用来源的 `max_bytes`（默认 1MB）**，只落前 3 个地址；超大图会被跳过（正文仍保留原链接）。
4. **workflow 的写授权是整轮语义**：一条流程里任何一步都能用到声明的写工具集，没有"按步收敛"。若要更细，可在步骤上加 `write:` 覆盖。
5. **recap 之外的既有待办仍在**：`sessions` 累积、`data/audit/` 里 4 条单测写脏的 `auto_off`、残留 `user_data/sandbox/u/`、真机 e2e 是否固化成 `tests/testclient/` —— 都不属于本大点，未动。
