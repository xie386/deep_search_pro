# CLI 能力描述撰写：从「望文生义」到「按证据写」

> **版本** v2.5（十进制：现有功能优化 + bug 修补）　**日期** 2026-09-24　**体例** 问题 → 方案 → 结果
> **报障** 用户：「我发现它写的子命令描述很多时候都是错误的，和你写出来的差距很大」→ 追问「它到底是实际执行过，还是望文生义」
> **触及文件** `tools/cli_docs.py`(新) · `tools/cli_registry.py` · `tools/_runtime/shell_runtime.py` · `api/customize.py` · `api/server.py` · `prompt/prompts.yml` · `static/index.html`
> **守住它的用例** `tests/test_ability_draft.py`（**24 项**，离线零模型）

---

## 一、问题：AI 只拿到「命令名」，没有任何依据

| 环节 | 事实（代码级） |
|---|---|
| 前端 | 草稿请求只带三个字段：`{name, bin, readonly}` |
| `api/customize.py::cli_ability_generate` | 拼给模型的 user_msg = 名称 + 可执行名 + 只读清单原文；**`default_model.invoke(...)` 无 tools、无沙箱、零执行** |
| `prompt/prompts.yml` 的 `ability_writer` | 规则全部建立在「清单里的命令名」上，且**自己承认在猜**：*"这类**通常**需要 ID 或参数"*、*"不要列出你没把握的能力"* |
| `tools/cli_registry.py::parse_rules` | 只保留**子命令路径 token**（`#` 行跳过、参数与注解全部裁掉）——用户写的说明也到不了模型 |
| 表单里现成却没传的字段 | `docs`（文档链接）、`auth_cmd` |

**结论：判断依据 = 命令名的字面联想（望文生义），既不是执行、也不是文档。**

### 实测复现（用库里真实的 weread 条目，同输入重生成）

| AI 写的 | `--help` 真值 | 判定 |
|---|---|---|
| 这本书讲什么→`search 再 book info` | `book resolve <title>` 的描述原文是 *"Resolve a book title to likely bookId matches"* | 没用解析器（勉强可行但绕） |
| 最近读的同类书→`discover similar` | `Usage: … similar [options] <bookId>` | ❌ **直调需要 ID 的命令** |
| 把我笔记导出来→`notes export` | `Usage: … export [options] <bookId>` | ❌ 同上 |
| 详细阅读数据→`readdata summary 再 readdata detail` | `readdata detail` **无位置参数**（靠 `--mode`） | ❌ **凭空造链** |
| 其余 9 条（shelf / notes / recommend…） | 名字即可推断 | ✅ |

**13 条映射，3 条错 —— 错的全是需要判断力的地方**，而 `book resolve` 在草稿里出现 **0 次**。

**代价**：错的映射会被 `ability_examples()` 抽成 few-shot **教模型去调一条必然报参数错的命令**，模型撞错后往往绕去联网检索 —— 直接吃掉有限的 Tavily 额度。

> 同一时期的人工版也有 3 条错（`book progress` / `discover similar` / `notes export` 直调）。
> 说明问题不是「AI 不如人」，而是**「有没有真据、核得全不全」**——靠人工抽查 `--help` 同样会漏。

---

## 二、方案：证据分层（用户提的「先读 README」+ 真值兜底）

| 层 | 来源 | 作用 | 实现要点 |
|---|---|---|---|
| **① 语义层** | 用户填的 `docs`（一般是对应仓库的 README） | **唯一事实来源**，让 AI 有据可依 | `tools/cli_docs.py`：URL 规范化（GitHub blob→`raw.githubusercontent.com`、仓库根→`HEAD/README.md`、无协议头补 `https://`）；直连→代理双路径；**SSRF 守卫**（拒环回/私有/链路本地/`*.local`）；响应体上限 40KB；按 URL 哈希**缓存 7 天**；抽取时**丢安装/贡献/许可段**、留命令与用法，上限 20K 字 |
| **② 校验层** | 逐条 `<bin> <cmd> --help`（**走沙箱运行时 + 现有只读闸**） | 把每条命令分类成 `needs_id` / `free_text` / `standalone`，**当事实表喂给模型**并用于**生成后审计** | `tools/cli_registry.py::collect_help / classify_usage / audit_ability_draft`；实测 `weread book resolve --help` **能过闸**（`--help` 不在写标志里、按子命令路径匹配），裸 `<bin> --help` 被拒 —— **口径一点没放宽** |
| **③ 兜底** | 文档没覆盖那条命令 → 补 `--help` **原文**；再不行 → 用户粘贴的「帮助文本」；都没有 → 保守化（宁可少写一条） | 应对「README 不全 / 抓不到 / npm 包 README 只是占位」 | 表单新增**临时字段**「帮助文本（可选）」；实测 `@googleworkspace/cli` 的 registry `readme` 只有 **28 字节占位**，npm 路线不可指望 |

**为什么要①和②一起**：README 有**语义与示例**（`book resolve "三体"` 引号书名 vs `book info 3300045871` 裸数字 ID —— 一眼看出参数性质），但会**过时/不全**（本例真实 README **完全没写 `reviews list`**）；而 `--help` 是**最新、可执行**的真值，却只有一行 `Get /book/info` 这种没信息量的描述。**一个负责「写什么」，一个负责「写得对不对」。**

### 提示词同步加硬规则（`prompts.yml` 的 `ability_writer`）

1. 依据按可信度排序（官方文档 ＞ 本机真值表 ＞ 粘贴的帮助文本 ＞ **没依据就不写**）；
2. 「需要 ID」的命令**不能直调**，必须做成两步链，**链头要用专门做「名称→ID」解析的那条**；
3. **不要编造两步链**（真值表标「无参数直调」的命令不能当第 2 步）。

---

## 三、结果

### 生成质量（同一提示词，只换证据）

| 证据 | 映射数 | 危险警告 | 两步链 | `book resolve` 使用 |
|---|---|---|---|---|
| 修复前：只给命令名 | 13 | **3** | 4（链头全错） | **0 次** |
| 修复后：读 README | **13** | **0** ✅ | 5 | **5 次** |
| 修复后：不给文档、靠 `--help` 兜底 | 12 | **0** ✅ | 4 | 4 次 |

两个分支都端到端跑通（真实接口 `/api/cli/ability_draft`，账号 `尼古喵喵`）：
- 有文档：证据回显「已抓取；覆盖 **15/16** 条命令，未覆盖 `reviews list`；本机 `--help` 实测 **16/16**」→ 草稿里 5 条需要 ID 的命令**全部**写成 `book resolve 再 …`，**连 README 没写的 `reviews list` 也写对了**（真值表补的位）；
- 无文档：证据回显「未读到官方文档；改用本机 `--help` 实测 16/16 作依据」→ 仍然 0 危险警告。
- 唯一的警告是**覆盖提示**：`config set-key` 没被描述到（它其实是**写操作**，建议从只读清单里删掉——见「遗留」）。

### 自动化回归

| 用例 | 结果 |
|---|---|
| `tests/test_ability_draft.py`（新增，离线零模型） | **24/24** |
| 覆盖点 | URL 规范化 / **SSRF 守卫**（8 类内网地址全拒）/ 证据抽取与覆盖统计 / 形态分类三值（含全大写形参、`[OPTIONS]` 边界）/ **审计四条规则**（直调需要 ID、链头就需要 ID、凭空造链、第 1 步不产出 ID）/ 审计建议必须给**真解析器**`book resolve` 而非 `search` / 防回退（server 透传、generate 走三层证据、提示词硬规则、前端回显） |

### 顺带修掉的真 bug

审计原型第 3 条规则写错过一次：「命令不在只读清单里」原先用「首词是否出现在某条命令里」判定，于是 `shelf books`（与 `shelf list` 共享首词）**被漏判**。改成整段精确匹配后，`shelf books` 能正确报「不在只读清单里（示例会被丢弃）」。

---

## 四、遗留（诚实说明）

1. **不保证选出「最优」链头**：新的 README 草稿里仍有 `帮我搜一搜这本书→search`（功能可行，但不如 `book resolve` 精准）。要更严可以再加软规则，但**不做绝对保证**。
2. **README 覆盖不全时依赖 `--help`**：本例 `reviews list` 就是靠真值表补的；若某个 CLI 的 `--help` 也整体报错（用户提到的情形），只剩「用户粘贴帮助文本」这一条路 —— 所以那个粘贴框不是装饰。
3. **n=1**：以上是单次生成的结果。要下「稳定 0 警告」的结论得像上次提示词 A/B 那样跑多轮；目前的确定性保障来自**审计器**（纯代码、可回归）而不是模型。
4. **安全问题**：`config set-key` 仍在用户的只读清单里（**写操作**，会改 API key）。AI 不会引用它，但清单 = 放行范围，建议从清单移除。
5. **`adhoc` 通道的口径**：`shell_runtime.execute(..., adhoc=...)` 仅供服务端草稿接口，用「用户此刻表单里的清单」当规则、**照旧走只读闸**；Agent 侧工具入口不传该参数（已写进 docstring）。

---

## 五、复盘思考题

1. 为什么「README 当证据」比「直接跑 `--help`」更适合当**语义**来源？两者各缺什么？（提示：看 `Get /book/info` 这行 help 到底告诉了你什么）
2. 「需要 ID 的命令必须做两步链」这条规则，为什么**只能靠证据**得出、靠命令名猜不出来？（提示：`book resolve` 这个名字对不懂业务的人意味着什么）
3. 为什么审计要用**纯代码**实现、而不是「再叫一次模型帮我看写得对不对」？（提示：确定性、可回归、零额度）
4. 为什么表单里那个「帮助文本」粘贴框不能省？在什么情况下它是唯一的证据来源？

---

## 六、涉及文件

```
tools/cli_docs.py                     # 新增：URL 规范化 / SSRF 守卫 / 直连+代理抓取 / 缓存 / 证据抽取与覆盖统计
tools/cli_registry.py                 # 新增：classify_usage / collect_help / suggest_resolver / audit_ability_draft / coverage_note
tools/_runtime/shell_runtime.py       # execute(..., adhoc=)：供草稿接口用「表单里还没保存的清单」跑 --help（不放宽口径）
api/customize.py                      # cli_ability_generate 重写：文档 → help → 粘贴文本 → 保守化；返回 evidence + warnings
api/server.py                         # /api/cli/ability_draft 透传 docs / help_text / 归属上下文，返回证据与审计
prompt/prompts.yml                    # ability_writer：依据排序 + 两步链硬规则 + 禁止编造链
static/index.html                     # 传 docs/help_text；回显证据（读了哪份文档、覆盖几条）与审计警告；新增「帮助文本」框
tests/test_ability_draft.py           # 新增 24 项离线回归
tests/README.md                       # 用例登记
```
