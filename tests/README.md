# tests/ — 测试与调试脚本

> 约定：**测试 / 调试脚本统一放本目录**，不放项目根目录。项目根只保留 `main.py` 入口。
> 统一跑法：项目根下 `unset PYTHONPATH && .venv/Scripts/python.exe tests/<脚本>`（Hermes 会注入自己的 venv，不清会撞 pydantic/numpy 版本）。

## 里程碑端到端回归（按里程碑顺序）

| 脚本 | 用途 | 前置条件 |
| --- | --- | --- |
| `m1_context_e2e_test.py` | **M1**：上下文预算四组件 + 会话持久化 + 多会话越权校验 | 无需服务（TestClient 直调） |
| `m2_assembly_e2e_test.py` | **M2**：装配管线——人格/记忆 invoke 期注入、改人格零重建 | 无需服务 |
| `m3_kb_e2e_test.py` | **M3**：知识库入库→检索全链路（含评估/清洗/去重）。⚠️ 7b 段（中文 username 入库回归）**必须用全新注册的中文账号**（`中文测试公司_<时间戳>`，跑完连账号一起 `purge_account`）——曾复用固定老账号「灵康科技有限公司」，结果它 09-06 遗留了一个空 collection（文档数 0 但 `exists=True`），「默认无库」断言假失败（2026-09-16 修） | 需要 `D:/LLM/model/bge-*` 本地模型 |
| `m4a_skills_e2e.py` | **M4a**：技能 CRUD → `/api/chat` 带 skills 真机 invoke → 前缀装配校验 → 新会话反证（不带 skills 则无技能痕迹）→ 用后清空 | 无需服务；**会真实调用大模型 2 次（约 1-3 分钟）** |
| `m4a_frontend_logic.js` | **M4a + M4c + M5a 前端**：从 `static/index.html` **抽取真实代码块** + 极简 Vue shim，在 Node 里验证 `/` 技能目录交互（筛选/回绕/上限 5/不重复/Esc）、`send()` 是否带 `skills` 参数、用后即清、自定义 CLI 页目录/模板/状态都来自服务端（`/api/cli/list` `/api/cli/save` `/api/cli/status` `/api/cli/delete`）、预填示例只填表单不发请求（50 项） | `node tests/m4a_frontend_logic.js`；**M5a**：CLI 表单「✨ 按只读清单生成草稿」——清单为空时不发请求并提示、有清单时 POST `/api/cli/ability_draft`、草稿只进表单**不自动保存**、生成中状态复位 | `node tests/m4a_frontend_logic.js`（无需服务） |
| `frontend_setup_exports_check.py` | **前端静态不变量：模板引用 ⊆ setup 导出**（2026-09-16 真实事故固化）。`static/index.html` 是「in-DOM 模板 + 单个 setup() 返回对象」：模板里引用、却没写进 `return {...}` 的名字会绑成 `undefined`——**不报错、不崩、渲染正常，只有交互静默失效**。本次就是 `skOnInput`/`skOnKey`/`skillPick`/`skillUnpick` 漏导出 → 用户报「聊天框敲 / 没反应」。用例抽取模板全部 `{{ }}`/`v-*`/`@事件`/`:绑定`/插槽 props 与 setup 返回表做集合包含校验（含解析器自检：引用数 >0、导出数 >100，防解析失效导致假通过）| `.venv/Scripts/python.exe tests/frontend_setup_exports_check.py`；`--fix` 可自动补进 return | （3 项） |
| `auth_split_e2e.py` | **登录/注册两卡分离（2026-09-16 收尾）**：背景是旧的「登录注册混合卡」——`doLogin()` 遇 401 就调 `/api/register` **自动建号**，打错用户名会静默建出错误账号（用户实际踩到「尼姑喵喵」）。用例断言：① 后端新语义（**账号不存在 → 404「账号不存在」/ 密码错误 → 401「密码错误」**）；② **用打错的用户名登录不会建号**（查库计数不变，这是本次的核心回归）；③ 注册 409 重名 / 400 空用户名 / 正常建号后能用新密码登录；④ 前端不再有「登录即注册」的调用路径（静态断言）+ 关闭按钮旁的注册入口；⑤ 注册卡字段与校验规则、注册成功后回登录卡不自动登录；⑥ `/api/login` 与 `/api/register` 的路由契约。前端校验规则**行为**验证在 `scripts/cdp_auth_probe.py`（真机点出来） | `.venv/Scripts/python.exe tests/auth_split_e2e.py`（纯 TestClient，不耗模型，约 5s） | （36 项） |
| `m4b_cli_e2e.py` | **M4b**：CLI 沙箱面板全链路——白名单查询 → `POST /api/shell/exec` 正常执行（pwd/echo/mkdir/ls/cat/grep，落盘真实目录）→ **安全矩阵 12 例**（路径逃逸/`..`/绝对路径/反斜杠/`python`/`rm -rf /`/管道/`curl -o`/`curl -X POST`/`git -c`/`git --git-dir=`）→ 外部程序真跑（ffmpeg/git）→ 审计日志（含 rejected 与 cwd）→ **真机 Agent 调 `run_shell_command`** → 首页 CLI 面板结构 | 无需服务；**会真实调用大模型 1 次（约 1-3 分钟）** |
| `m4c_cli_e2e.py` | **M4c**：自定义 CLI 接入（配置导向）——列表/预填示例/状态枚举 → **校验矩阵 15 例**（路径式可执行名 / shell 元字符 / 选项开头 / 重复 bin / 超长 / 重复行绕过条数上限 / 非法状态）零脏数据落库 → 新增后落库直查归属与规范化 → 未登记拒绝 / 登记后放行 → 只读闸矩阵（未列出子命令 / 落盘参数）→ 编辑换名立刻回收、清空清单立刻不放行 → **局部更新不清掉 state/note/readonly** → 删除即回收 → 旧版内置登记幂等迁移 → 账号隔离 → **真机 Agent 调 `run_shell_command` 跑自定义 CLI** → 首页结构；`SKIP_LLM=1` 跳过真机步 | 无需服务；**会真实调用大模型 1 次（约 1-3 分钟）** |
| `m4_tutorial_e2e.py` | **M4 收尾**：教程只读阅读页——`GET /api/tutorial/doc` 只读返回（标题取自 md / 不泄露绝对路径）→ **7 张图片逐个 200 + image\* 且非空**（含项目外的 Typora 贴图）→ 安全（无 token / 错 token / `../` 穿越 / `.md` 不可经资源位读取）→ md 结构保真 → 首页结构 | 无需服务（TestClient 直调） |
| `m4_tutorial_frontend.js` | **M4 收尾前端**：抽 `index.html` 里**真实的 `tutRender`** 在 Node 跑——标题/表格/图片 URL 不被转义/代码块内容转义/折叠块/列表闭合/**注入转义**（`<script>`、`<img onerror>`、`<iframe>`）/真实文档整体渲染结构断言 | `node tests/m4_tutorial_frontend.js`（无需服务） |
| `m4_weather_e2e.py` | **M4 收尾**：工作台天气——WMO 码映射与兜底 / **真实访问 Open-Meteo** 的城市级字段（城市·省·国家·经纬·温度·体感·湿度·风速）/ 4 天预报 / 内存缓存 0.005s / `force` 刷新 / **磁盘缓存落盘** / 换城市与英文城市名 / 404 / 鉴权 / 前端接线 | 无需服务（TestClient 直调），**需外网**（无外网会明确 FAIL） |
| `m4_weather_frontend.js` | **M4 收尾前端**：抽 `index.html` 真实函数（`tickClock`/`loadWeather`/`wxSetCity`/`wxStart`）在 Node 跑——假 Date 验秒级补零与星期、URL 拼装与编码、错误分支、换城市内联输入（确定/取消/清空回落默认）、定时器周期与幂等 | `node tests/m4_weather_frontend.js`（无需服务） |
| `m5_abilities_e2e.py` | **M5a**：能力语义注入——`abilities` 列与**幂等迁移**（重跑不重复）/ 存读与「局部更新不清空」/ 超长拒绝 → `agent_brief()` **双形态**（有描述=能力路由卡、无描述=回退命令名简报）/ 多行描述注入时压平 → `ability_examples()` **few-shot 必须逐条过只读闸**（指向未放行命令的映射被丢弃、同段放行的照常生成）→ 装配链路（简报+示例进同一条动态 SystemMessage）→ API（save 回读 abilities / 草稿接口缺清单 400 不调模型）→ 首页接线 → 清理隔离账号。**用全新临时账号隔离**（避免真实配置干扰断言），跑完自动删账号与数据 | 无需服务（TestClient 直调）；不耗模型 |
| `m5b_pool_e2e.py` | **M5b-1 统一能力目录**：两表结构 + 幂等 `ensure_tables` → `cli_entry()` 适配器（关键词前缀剥离 / `enabled` 跟随 `live` / 无描述回退命令名形态 / 三种渲染形态长度递减）→ **写入口联动**（`save_cli`/`set_state`/`delete_cli` 后池立即反映）→ 池版本递增 → `rebuild_pool` 幂等 → **向量**（首次 added、二次全 skipped、删除工具后清理、惰性版本对齐）→ 账号隔离 → **不污染 M5a 简报** → 清理。含「`save_cli` 不因池同步变慢（<3s，即未加载 bge）」的回归断言 | `.venv/Scripts/python.exe tests/m5b_pool_e2e.py`；`SKIP_VEC=1` 跳过 bge（约 1~3 分钟） | （62 项） |
| `m5b_router_e2e.py` | **M5b-2 工具检索路由**：快路径（≤4 工具 → 与 `agent_brief` **逐字一致**）→ 路由路径（8 工具规模下命中集必须含期望工具，**不允许「路由走了但命中错的工具」**）→ 反例回退 → 护栏（完整卡数 ≤ MAX_FULL_CARDS、注入量 ≤ 全量、**未命中工具仍有一行名称**、示例只留命中工具）→ 兜底（向量不可用但有词法命中仍路由 / 两路都无信号回退 / 池异常回退 / 空问题回退）→ RRF 单元（无信号排序器必须剔除）→ 来源无关（混合 cli+api+mcp 条目）→ 接线（server 走 tool_router）| `.venv/Scripts/python.exe tests/m5b_router_e2e.py`；`SKIP_VEC=1` 跳过需 bge 的用例 | （34 项） |
| `m5b_pressure_probe.py` | **M5b-2 加压探针（真机，慢）**：8 个异构真桩工具（`data/m5b_probe_bin/` 下同名 `.cmd`）+ 8 条正例（每条应首调命中对应工具）+ 2 条反例（不该调任何工具）。判定沿用 M5a 方法论：**只看第一次调用** + **会话真值配对**（读 `messages.tool_calls`，不用审计窗口计数）+ 真桩防重试风暴。结果落 `data/m5b_pressure_result.json`；支持**备选首调**（`alts`：需前置 ID 的命令先 `search` 也算命中）与**单条重跑** `M5B_ONLY=m5bp-vid`（落 `..._only.json`，不覆盖整轮） | `.venv/Scripts/python.exe tests/m5b_pressure_probe.py`（真机 **8 工具 × 10 问，约 20~40 分钟**）；`SKIP_LLM=1` 只验夹具与判定逻辑（几秒） | （自检 6 项；真机实测 **8/8 正例 + 2/2 反例**，见 `docs/v2.0/M5b工具检索路由.md` §11.5） |
| `_m5b_route_calib.py` | **M5b 阈值标定脚本**（不算测试用例）：8 个异构工具 → 逐问句打印「向量 top3 / z 分数 / 词法命中 / 是否路由 / 命中集」，并给出 `MAX_FULL_CARDS` 1/2/3 对应的注入量敏感性（51% / 59% / 66%）。**改 `tools/tool_router.py` 里的阈值必须重跑它** | `.venv/Scripts/python.exe tests/_m5b_route_calib.py`（`--keep` 保留临时账号） | — |
| `m5_probe_e2e.py` | **M5a 探针组（唤醒率量化）**：隔离账号 + 假可执行名探针 CLI（本机未装 → 执行失败但**调用尝试照样进审计日志**）→ 自检（路由卡/示例注入 + 审计链路）→ **10 条自然语言正例**（问「看看我最近在读什么书」等，断言审计里出现该 CLI 且 argv 命中期望命令，主命中/备选命中都算过）→ **3 条反例**（天气/写诗/概念解释，断言没误调）→ 结论（正例 ≥9/10、反例 3/3）+ 结果落 `data/m5_probe_result.json` → 清理。**判定只看 `data/audit/commands.jsonl` 的调用事实，不采信模型自述** | `.venv/Scripts/python.exe tests/m5_probe_e2e.py`；`SKIP_LLM=1` 只跑自检；全量**真机 13 轮对话（约 10-20 分钟）**   ⚠️ **不要在探针全量跑的同时另跑本脚本的 `SKIP_LLM=1` 自检**——自检会往审计日志写一条同 bin 的调用，而探针的窗口判定依赖审计计数，会污染「第一条探针」的判定（真值以库里 `messages.tool_calls` 为准）。   ⚙️ 夹具：探针可执行名用**能跑通的桩**（`data/m5_probe_bin/` 下的同名 `.cmd`，输出固定 JSON），否则「未安装」会诱发模型重试风暴、单探针拖到 5+ 分钟；判定**只看第一次调用**（路由决策），后续自纠仅记录不计入。 |
| `cli_dspro_e2e.py` | **CLI 版 dspro**：子进程真跑 `python -m cli.main`——帮助/落地页、未登录与错密码的退出码与提示、登录态文件（含「不含明文密码」与密码指纹）、`whoami`、`list` 四种形态（`--json` 可解析）、`digest` 参数解析与报错文案、**同名订阅歧义必须报错**、`chat --sessions/--skills`、`logout`；全量模式追加真跑 `digest`（断言落库 +1）与 `chat` 单轮 | `.venv/Scripts/python.exe tests/cli_dspro_e2e.py`（`SKIP_LLM=1` 只跑不耗模型的用例） |

## 接口层 / 冒烟

| 脚本 | 用途 | 前置条件 |
| --- | --- | --- |
| `m3_smoketest.py` | 接口层回归：登录 → 竞品清单 → 导出下载 | 服务已启动：`uvicorn api.server:app --port 8123` |
| `pre_work_test.py` | 环境自检（依赖 / 模型 / 库表） | 依赖已装 |
| `tool_agent_e2e_test.py` | 文档读写工具挂到全局 AGENT 后由 Agent 真实完成任务 | 无需服务；会调大模型 |

## 调试 / 探针（`_` 前缀为临时脚本，可删）

| 脚本 | 用途 |
| --- | --- |
| `_m5_real_weread_check.py` | **M5a 真实场景复核**（真实账号 + 真实 CLI）：三层证据一次到位——① 注入证据（打印该账号实际收到的能力路由卡 + few-shot 示例）② 路由证据（问一句自然语言，从库读 `messages.tool_calls` 真值配对）③ 执行证据（调用必命中只读白名单 + 回答含只可能来自 CLI 的真数据）；跑完删除自己创建的会话。`--question` 可换提问 |
| `m2_debug_run.py` | 进程内直接调 `_run_agent`，打印真实异常堆栈（绕过 FastAPI 包装） |
| `m2_ws_probe.py` | WS 链路隔离验证：连 `/ws/dbg` → 调 `/api/_test_emit` → 验证收到 monitor 事件 |
| `_verify_kb_testclient.py` / `_verify_kb_fixes.py` | M3 修复项复核（TestClient 直调） |
| `_check_login.py` / `_verify_kb_e2e.py` / `m35_attrs_test.py` | 登录、知识库、M3.5 属性字段的辅助核查 |
| `model_thinking_probe.py` / `thinking_capture_probe.py` | 思考内容（`reasoning_content`）抓取验证 |

## 注意事项

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
