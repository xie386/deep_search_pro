# v4.0 · 06 Agent 工作目录前端可编辑可视化与写能力开发 · 逐文件带读

> 配套 `06-Agent工作目录前端可编辑可视化与写能力开发·实现记录.md`。这里只讲**怎么读**：先看哪一层、每层带着什么问题读。
> 一句话主线：**一个"能写"的系统，安全边界不是提示词，而是三道代码闸 + 一套显式授权。**

## 一、阅读顺序总览

```
① 闸：tools/workdir_guard.py            ← 先看"什么不许写"，再看"怎么写"
② 档：tools/workdir_store.py            ← 多目录怎么登记、"主页"怎么自愈
③ 手：tools/workdir_tools.py            ← 内置写工具（纯文字可用）
④ 面：api/workdir_api.py / server.py    ← 前端要的能力怎么暴露
⑤ 授权：utils/write_auth.py + api/write_pool.py  ← 自配来源的写，为什么必须「/」
⑥ 池：tools/capability_pool.py          ← 路由池 / 写池怎么派生，`set_pool` 为什么必须级联
⑦ 网：tools/capability_invoke.py        ← 执行网关（判定链 + 图片收口 + 审计）
⑧ 驱：tools/_runtime/api_driver.py      ← HTTP 驱动（重定向、SSRF、图片、窄放行）
⑨ 流程：api/workflows.py → agent/workflow_runner.py → api/server.py(workflow 分支)
⑩ 前端：front/index.html 的「工作目录 / 「/」三组 / 气泡标签 / 图片渲染」
```

## 二、逐文件

### 1. `tools/workdir_guard.py` —— 三道闸

- 读它先问：**如果一个请求要写 `.env`，在哪里被拦？** 答案在 `resolve()` 里：归一化 → 前缀校验 → 保护名单 → 凭据红线 → 体积闸。任何一步不过就抛，调用方没有"绕过"的入口。
- 再看**保护名单自身**为什么硬保护：名单可编辑（用户可以自己维护），但**不许删** —— 删了以后所有写入都会被"名单缺失"拦死。

### 2. `tools/workdir_store.py` —— 多目录与「主页」

- 关键约定：登记**只是登记**，不接管磁盘。默认条目叫「主页」，指向该账号自己的沙箱，`is_default=1`，不可移出。
- `ensure_default()` 是**自愈**：历史脏行（主页指到别的路径）会就地修回；`active()` 只读兜底，保证 Agent 永远拿到一个可用目录。

### 3. `tools/workdir_tools.py` —— 内置写工具

- 三个写工具 + 两个读工具。**删除只进回收站**（`.回收站/{时间戳}_{原名}` + `_index.json` 记原相对路径），TTL 由 `WORKDIR_TRASH_TTL_DAYS` 控制。
- 思考题：为什么这三个工具**不需要**「/」授权？（裁定 11：它们过的是三道闸，与"外部副作用"不是一类风险；而「/」第二组只列自配来源，曾导致它们两条路都不通。）

### 4. `api/workdir_api.py` + `api/server.py` —— 前端能力面

- 三档读取：**可编辑**（文本）/ **只读预览**（docx、xlsx，服务端转文本）/ **仅下载**（pdf 等）。三档由文件类型决定，不是前端猜。
- 回收站是**独立资源**（list / restore / purge / empty），树里不出现 `.回收站`；注意它全走 POST（历史 bug：前端用 GET 调 → 405，面板永远空）。

### 5. `utils/write_auth.py` + `api/write_pool.py` —— 「/」授权的两条腿

- `write_auth.selected` 是 `ContextVar`：api 层每轮写入，agent 与 tools 只读。为什么放 `utils/`？因为 `tools/` 不能反向依赖 `agent/`。
- `compose_write_sets_text()` 是**给模型看的说明书**：CLI 给 `run_shell_command(command=…, argv=[…])`；api/mcp 给 `invoke_tool(ref=…, params={…})` 并列出可用 ref。真机教训：说明书写不清，模型就把 MCP 来源名当可执行文件。

### 6. `tools/capability_pool.py` —— 两个池

- 路由池按 `pool != write` 派生；写池另算。`set_pool` 必须**同时改来源行与能力行**（cli 按 bin、api 按 `api:<slug>#`、mcp 按 `mcp:<slug>/`），否则前端数字纹丝不动（历史 bug 10）。
- 写语义的初值：api 按 HTTP 方法、mcp 按 `read_only_hint`；确认写工具时自动进写池，且**只升不降**。

### 7. `tools/capability_invoke.py` —— 执行网关

- 判定链：解析 ref → 查能力（归属 / 已确认 / 启用）→ 参数校验 → 放行判定 → 驱动执行 → 结果裁剪 → 审计。
- 收尾三件事：写操作在正文开头标注；`blob`（图片字节）落 `<工作目录>/_tool_images/`；正文里的图片**地址**自动下载再落盘，附 `[[img:…]]`。
- 注意 `_is_private_host()`：图片地址来自外部接口，仍要挡回环/内网。

### 8. `tools/_runtime/api_driver.py` —— HTTP 驱动

- 三条口径：`base_url` 由用户填（默认允许内网）；**模型可控的参数值若是 URL** 走 `_host_is_safe`；重定向默认不跟，只跟同主机一次。
- 本点新增：`image/*` 不当文本解码；只读能力的**图片类跨主机 302** 窄放行 —— 条件是"只读 + 安全主机 + 目标确为图片"，且探跳时**剥掉鉴权头**。

### 9. `api/workflows.py` → `agent/workflow_runner.py` → `server.py`

- 校验：`write:` 接受列表或逗号串，≤7 个；缺省为空。命名要经 `wf_safe_name()` 规范化 —— **文件名必须与规范化结果一致**，否则"列表有、点开空"（历史 bug 14，已有兜底）。
- 引擎：`build_step_prompt(..., write_text=)` 把授权说明书带进每一步；`run_workflow(..., write_text=)` 透传。步骤仍**只描述本步**，不许泄漏后续。
- 服务层：解析 `doc.write` → 核对账号里确有这些写工具集 → 合并进本轮选择 → 跑完**还原**。

### 10. `front/index.html`

- 左栏「📁 工作目录」：条目（主页不可移出）、逐层树、保护/凭据标记、新建/编辑/重命名/移动/删除（进回收站）。
- 三用弹窗：居中覆盖整页（`backdrop-filter` 曾把它困在左栏里）；点遮罩关闭。
- 「/」三组**扁平索引**：技能 → 写工具集 → workflow，↑↓ 贯穿三组，已选项跳过，Enter/Tab 按组分派。
- 气泡：用户消息右下角 `类别:名字` 标签（技能 / workflow / 自配写工具），历史加载同样带。
- 图片：`renderRich` 把 `[[img:…]]` 渲染成 `<img>`，走 `/api/workdir/download` 的鉴权通道（不新开接口）。

## 三、故障史（读代码时的背景）

| 读到这里时 | 想想当年的故障 |
|---|---|
| `workdir_guard.py` | 保护名单被删 → 所有写入被拦死（所以名单自身硬保护） |
| `workdir_api.py` 回收站 | 前端 GET 调 POST 接口 → 405 → 面板永远空 |
| `write_auth.py` | 「/」选了写集，模型仍去 `run_shell_command("mineru")` → 说明书缺调用形态 |
| `capability_pool.py` | 归写池后路由条数不变 → `set_pool` 没级联能力行 |
| `api_driver.py` 重定向 | 302 跳 CDN 被拒 → 图片接口全线不可用 → 加窄放行 |
| `api/workflows.py` | 文件名带空格 → 列表有、点开空 |
| `api/server.py` workflow 分支 | 提前 `return` → 这一轮没落库，刷新即消失 |
