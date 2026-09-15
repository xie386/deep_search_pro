# dspro · 命令行版（CLI）

> 状态：✅ 已落地（2026-09-12）｜入口 `cli/`，命令名 `dspro`
> 一句话：**同一个智选情报官，多一个命令行入口**——不做第二套业务逻辑，直接复用 web 版的本地库、适配层与 Agent。

---

## 1. 命令清单（用户视角）

| 命令 | 作用 | 示例 |
|---|---|---|
| `dspro login <用户名> <密码>` | 登录鉴权（校验后把登录态写本地） | `dspro login 尼古喵喵 123456` |
| `dspro login <用户名> -` | 同上，但密码走隐藏输入（不进 shell 历史） | — |
| `dspro login <用户名> <密码> --register [--role company]` | 账号不存在时直接注册 | `dspro login 新用户 123456 --register --role company` |
| `dspro logout` | 退出登录（删除本地登录态文件） | — |
| `dspro whoami` | 当前登录态 + 数据概况 | — |
| `dspro list` | **默认**：个人信息/公司信息 + 周报生成模块的所有关注信息 | — |
| `dspro list -db` | 只打印个人信息 / 公司信息 | — |
| `dspro list -digest` | 只打印周报生成模块的所有关注信息 | — |
| `dspro list --json` | 机器可读（脚本/管道用） | `dspro list --json \| jq .counts` |
| `dspro digest` | **默认**：所有启用中的关注领域各生成一份周报 | — |
| `dspro digest <领域名>` | 只生成该领域一份；领域名不存在 → 报错并列出可用值 | `dspro digest "米诺地尔生发剂"` |
| `dspro digest #<id>` | 订阅同名时用 id 精确指定（见 §5） | `dspro digest '#8'` |
| `dspro digest --list` | 只列出可用领域名（含 #id 与关键词） | — |
| `dspro digest <领域名> -o out.md` | 同时另存一份 markdown | — |
| `dspro digest --json` | 机器可读（含 `md_path`/`item_count`/耗时） | — |
| `dspro chat` | 进入对话（默认续用上次的 CLI 会话） | — |
| `dspro chat "<问题>"` | 单轮问答后退出（适合脚本） | `dspro chat "1000 元档降噪耳机怎么选"` |
| `dspro chat --new` / `--session <thread_id>` / `--sessions` | 新会话 / 续指定会话 / 列出会话 | — |
| `dspro chat --skill "<技能名>"` / `--skills` | 本轮注入技能说明书 / 列出可用技能 | — |

通用：`-h/--help`、`--version`、`--no-color`（或环境变量 `NO_COLOR`）。

> 小提示：`#8` 在 bash 里 `#` 会被当注释，所以要写成 `'#8'`（引号包住）。

---

## 2. 架构：CLI 与 web 共用同一套「数据 + 引擎」

**决策：CLI 独立运行，不做 web 服务的客户端。**（用户拍板）

| 能力 | web 版走哪里 | CLI 走哪里 | 是否同一份实现 |
|---|---|---|---|
| 账号/鉴权 | `api/account.py` `login()` | 同一个 `login()` | ✅ |
| 业务数据 | `data/personal.db`（按账号隔离） | 同一个库、同一张表 | ✅ |
| 周报生成 | `/api/digest/run` → `agent/digest_engine.run_digest()` | 直接 `run_digest()` | ✅ |
| 对话 | `/api/chat` → `api/server._run_agent()` | 直接 `_run_agent()` | ✅ |
| 会话历史 | `conversations`/`messages` 表 | 同一张表（web 端能看到 CLI 的对话） | ✅ |
| 进度上报 | WebSocket 推右栏 | `monitor.set_console_sink()` 打终端 | 同事件源 ✅ |
| 人格/记忆/技能/知识库/自配 CLI | M2 装配管线 | 同一个装配管线 | ✅ |

好处：CLI 不需要 uvicorn 在跑；不存在「两套逻辑漂移」；CLI 生成的周报**同样落 `digest_reports`**，
所以 web 端「情报周报」列表里能看到它（反之亦然）。

---

## 3. 登录鉴权设计

登录态文件：`data/dspro_session.json`（在 `.gitignore` 内，随项目走）

| 字段 | 说明 |
|---|---|
| `username` / `account_id` / `role` / `display_name` | 身份四件套 |
| `pwd_fp` | **账号密码哈希的指纹**（不是密码）——密码被改动后指纹不匹配，旧会话自动失效 |
| `login_at` | 登录时间 |
| `default_city` | 默认城市（与 `.env` 的 `WEATHER_CITY` 保持一致） |
| `last_thread_id` | `dspro chat` 上次的会话，用于「续聊」 |

- **不存明文密码**（测试用例断言：会话文件里不含 `123456`）；
- 校验复用 `api/account.py` 的同一份盐与哈希，**不复制第二套**（否则两边口径会漂）；
- 失败路径全部给人话 + 退出码：未登录 → 「尚未登录。请先执行：dspro login <用户名> <密码>」；
  密码错 → 「用户名或密码错误」；密码改过 → 「登录已失效（该账号密码已变更）」；
- `dspro login u p` 会把密码留在 shell 历史/进程列表（本地单机可接受），所以额外提供
  `dspro login u -` 的隐藏输入写法。

---

## 4. `list` 输出设计

- **总览**：关注领域/历史周报/兴趣/关注清单/收藏商品/我司产品/关注竞品/公司档案 一览（两列对齐）；
- **`-db`**：按**账号角色**取模块——公司账号看「公司档案 + 我司产品 + 关注竞品」，
  个人账号看「兴趣领域 + 关注清单 + 收藏商品」；另一侧若有数据会额外提示一行，
  避免用户以为「数据丢了」；
- **`-digest`**：订阅（= 关注领域）逐条列出 **#id / 领域名 / 范围 / 周期 / 检索语言 / 启用状态 / 上次生成 / 关键词**，
  再附最近 5 份周报（标题/条数/状态/时间）；
- 表格按**东亚显示宽度**对齐（中文、emoji 记 2 列），零外部依赖（未引入 rich）；
- `--json` 给脚本用：`{username, role, counts, db, digest}`。

---

## 5. `digest`：领域匹配与「同名歧义」

传给 `dspro digest` 的领域参数按以下优先级解析（**匹配不到一律报错并列出可用值**，不猜）：

1. `#<id>` 或纯数字 → 按订阅 id；
2. **名称精确匹配**（忽略空白/大小写）；
3. 名称包含 或 **关键词命中**（如 `dspro digest "米诺地尔"` 命中关键词含「米诺地尔生发剂」的那条）。

⚠️ 实测发现：同一账号可能有多条**同名**订阅（测试账号 3 条都叫「选购快讯」）。此时
**不静默挑第一条**，而是报错并列出候选（带 #id 与关键词），提示改用 `#id`：

```
✘ 「选购快讯」匹配到 3 条订阅，请用 #id 指定要生成哪一条：
    #2 选购快讯（关键词：AI新资讯）
    #7 选购快讯（关键词：香烟）
    #8 选购快讯（关键词：米诺地尔生发剂）
```

生成过程与 web 端 `POST /api/digest/run` **同一个 `run_digest()`**：
串行生成（避免并发压垮模型）、每个领域约 1-3 分钟、正文打印到终端、文件路径在 `output/{用户名}/digest/` 下、
`-o` 可另存、`--json` 给出 `md_path/item_count/elapsed_s/error`。
进度行（调用工具 / 调度助手 / 思考片段）来自 monitor 事件（见 §7 的 sink 机制）。

---

## 6. `chat`：命令行对话

- **复用 `api/server.py` 的 `_run_agent`**：同一套装配管线（SOUL 人格 + MEMORY 画像 + 技能 + 自配 CLI 简报）、
  同一份 SQLite 历史与 token 预算、同一批工具与子智能体、同样的 RAG 知识库检索 → **CLI 与 web 回答口径一致**；
- 会话落 `conversations`/`messages`（按账号隔离）：`dspro chat --sessions` 能看到 web 建的会话，
  web 端也能看到 CLI 的会话（同一系统两个前端）；
- 交互命令：`:new`（新会话）、`:sessions`、`:skills`、`:q`（退出）；`Ctrl+C` 中断本轮但保留历史；
- `--skill` 可重复，仅本轮生效（技能文件在 `agents_docs/{用户名}/skills/*.md`）；
- `--quiet` 关掉进度行；`dspro chat "问题"` 单轮模式便于脚本调用。

---

> **与 web 侧能力同步（重要）**：`dspro chat` 复用 `api/server.py::_run_agent`，因此 web 端的能力它**自动继承**，
> 不需要第二套实现。例如 M5 工具智能路由（能力语义描述 + few-shot 映射示例）改在 `_run_agent` 的装配层，
> **命令行端的对话同样会把「用户的话」翻译成对的 CLI 调用**——这也是「CLI 不做第二套业务逻辑」这条设计原则的收益。
> 同理 M4a 技能（`--skill`）、SOUL 人格、记忆画像、知识库检索在命令行端全部生效。

## 7. 实现要点与踩坑

| # | 事项 | 说明 |
|---|---|---|
| 1 | **monitor 的 print 会污染 CLI 输出** | `api/monitor.py::_emit` 有「控制台保底输出」`print("[Monitor:xxx] …")`，直接跑 CLI 会把原始事件混进对话。给 `ToolMonitor` 增加 `set_console_sink(fn)`：注册后由 sink 接管（CLI 渲染成进度行），**web 端不注册、行为不变** |
| 2 | **进度行的复用** | CLI 拿到的是同一批 monitor 事件（`tool_start`/`assistant_call`/`thinking`/`task_result`），所以终端进度与 web 右栏同源 |
| 3 | **argparse 的多字符短选项** | 用户指定 `-db` / `-digest` 这种写法；argparse 允许（`add_argument("-db", "--db")`），但要注意别同时定义 `-d`（会歧义） |
| 4 | **入口三种形态** | `python -m cli.main`（零配置）/ 项目根包装脚本 `./dspro`、`dspro.cmd`（免激活 venv）/ `[project.scripts] dspro`（项目被当包安装时生效） |
| 5 | **代理环境** | CLI 全程访问本机 SQLite 与外网 LLM/搜索，不需要 localhost HTTP；外网请求沿用项目既有的代理降级逻辑 |
| 6 | **`ui.SESSION_FILE` 误用** | `SESSION_FILE` 在 `cli/session.py`，写 `ui.SESSION_FILE` 会 `AttributeError` → 加了一个「cli/ 内所有 `ui.X` 是否为 ui 真实属性」的自查脚本，一次性扫出 |
| 7 | **表格宽度** | 中文/emoji 按东亚宽度（2 列）计算，否则表格错位；ANSI 颜色码不计入宽度 |

---

## 8. 安装与运行

```bash
# ① 最简单：不依赖安装，模块方式
.venv/Scripts/python.exe -m cli.main login 尼古喵喵 123456

# ② git-bash / WSL 用项目根包装脚本（免激活 venv）
./dspro list -digest

# ③ cmd / PowerShell 用 dspro.cmd
.\dspro.cmd digest '#8'

# ④ 把项目根加入 PATH 后，任意目录都能直接 dspro
export PATH="/d/code/aicode/ai智能体开发实战5期/weekend/deep_search_pro:$PATH"
dspro whoami
```

`pyproject.toml` 已注册 `[project.scripts] dspro = "cli.main:main"`：项目被当包安装
（`uv sync` / `pip install -e .`，需配 build-system）时会自动生成 `dspro` 命令。

---

## 9. 验证

| 脚本 | 覆盖 | 结果 |
|---|---|---|
| `tests/cli_dspro_e2e.py`（`SKIP_LLM=1`） | 帮助/落地页、未登录与错密码的退出码与提示、登录态文件内容（含「不含明文密码」与指纹）、`whoami`、`list` 四种形态（含 `--json` 可解析、**公司账号分支**走公司模块且不泄露 `password_hash`）、`digest` 参数解析与报错文案、**同名订阅歧义必须报错**、`chat --sessions/--skills` 与非法技能报错、`logout` | **36/36**（5s） |
| 测试开关 | `SKIP_LLM=1` 只跑不耗模型的用例；`SKIP_DIGEST=1` 保留真跑 chat、跳过重复烧一次周报 | — |
| **真跑 `dspro digest '#8'`（手动记录）** | 终端打印完整周报（要点/详细内容/来源清单/风险与机会）→ 落库 `digest_reports` **21 → 22**（#38《快讯周报 2026-09-12》9 条情报）→ 文件 `output/尼古喵喵/快讯周报_20260912_141330.md`（6362 字节）→ 订阅 `last_run_at` 更新；免费模型下整轮约 **9 分钟** | ✅ |
| **monitor sink 接线（手动记录）** | 未注册时保持 `[Monitor:tool_start] …` 原始 print（web 行为不变）；注册后输出「🤝 调度助手 / ⚙ 调用工具 / 思考片段 / ✓ 子任务完成」；注销后恢复默认 | ✅ |
| **真跑 `dspro chat`（`SKIP_DIGEST=1` 全量模式）** | 单轮返回答案 + 退出码 0（用时 5.6s）+ 会话/消息真的落 `conversations`/`messages`（会话 14 → 15） | ✅ **39/39**（76s） |
| 回归 | `api/monitor.py` 的改动对 web 无影响：M1 上下文 11/11、M2 装配管线 8/8 复跑通过 | ✅ |

测试纪律沿用本项目：**子进程真跑命令**（不看内部函数返回值）、断言落在用户可见输出与真实副作用上、
破坏性操作（登录态文件）用完还原。

---

## 10. 决策记录（用户拍板 2026-09-12）

| # | 决策点 | 结论 |
|---|---|---|
| 1 | CLI 运行形态 | **独立运行**：直接用本地库 + 同一套 digest 引擎/Agent，不需要启动 web 服务 |
| 2 | 登录态存放 | **项目内** `data/dspro_session.json`（已 gitignore，随项目走） |
| 3 | 命令行聊天 | **一并提供** `dspro chat`（复用 web 同一套对话能力） |
| 4 | 输出风格 | **纯文本 + 轻量 ANSI**（东亚宽度表格），**零新依赖**（不引 rich） |
