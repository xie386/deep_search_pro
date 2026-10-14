# 05 调研：网络搜索子Agent升级

> **状态**：占位骨架（2026-10-14 建立），等用户口令进入调研期。
> 本文件是**调研期产物**（信息堆砌、允许多方向与废案、不追求可读）；实现期只许定点查（见 `../版本文档规范.md` §六）。
> 规划文档里的方案权威是 `../v4.0规划文档.md` §05；本文件只负责「信息不丢」。

## 〇、任务口径（用户原话）

> 「之前说的 05-沙箱或工作目录可视化编辑与Agent写能力开发推迟到06,现进行占位,现在的05大点是05-网络搜索子Agent升级」

- 原 05「沙箱 / 工作目录可视化编辑 + Agent 写能力开发」→ 改记为 **06**（未占位，等用户口令）。
- 编号调整的裁定：`../Verdict/00-版本级选题与范围.md` 裁定 5。
- 原先指向「05 大点」的承接处（`04-….md` §3.1(b) 疑点 9：用户交付物落用户资料目录、被沙箱复制一份）**问题本体不变，承接编号变为 06**。

## 一、第一手事实（2026-10-14 只读探测，路径级证据）

| 事实 | 位置 |
|---|---|
| 子 Agent 只挂一个工具 `internet_search`；name/description/system_prompt 全来自提示词配置 | `agent/subagents/network_search_agent.py:9` · `prompt/prompts.yml` 的 `tavily:` 段 |
| 全网检索只有 Tavily 一个通道（228 行工具本体全围绕它；未接入第二引擎） | `tools/tavily_tool.py` |
| 能力参数：`topic`(news/finance/general) · `max_results` · `include_raw_content` · `days` · `strict_days`（代码层解析 `published_date` 剔除旧闻，不靠模型自觉）· `bilingual`（先 LLM 翻英，中英各搜一次后合并去重） | `tools/tavily_tool.py:33-54`、`:103-107`、`:140-155` |
| 健壮性：优先走系统代理；`ProxyError/SSLError/ConnectionError/ConnectTimeout/含 proxy/ssl 字样` → **降级直连重试**（`Session.trust_env=False`）；双语失败降级单语 | `tools/tavily_tool.py:83-99`、`:76-77` |
| 用量侧只有**归类**没有**计量/闸门**：`usage_counter` 按名称子串把 `tavily*/web_search*/news*` 归为「外部检索」 | `agent/usage_counter.py` |
| **工具内部长时间 HTTP 不可打断**（中断要等本次检索返回） | `agent/cancel.py` 自述局限 |
| 非搜索的相邻通道：`cli_docs.py` 普通 HTTP 抓 URL（自述「不消耗 Tavily 额度」）；`shell_executor` 提供 `curl` GET | `tools/cli_docs.py` · `tools/shell_executor.py` |
| 零消耗跑法：`STUB_WEBSEARCH=1` 用离线桩替换真实检索 —— **只在测试侧**（`tests/live/m5_probe_e2e.py`）实现，运行时不认这个键 | `tests/live/m5_probe_e2e.py:33` · `.env.example:74` |
| 周报（Digest）侧不再自写 Tavily/LLM 调用，同样用 `create_deep_agent` 编排（即共用同一个搜索工具面） | `agent/digest_engine.py` 注释 |

## 二、候选方向（调研期填，先记框架；本节允许废案）

- **A. 第二检索通道**（换/加引擎）：代价＝新依赖 + 新额度池 + 结果合并口径。
- **B. 额度计量与闸门**：本地计数（按账号/按日）+ 阈值触发降级到零消耗通道；与既有 `usage_counter`、`DECISION_BUDGET_*` 的口径要保持一致。
- **C. 可中断 / 超时预算**：工具内部 HTTP 的可取消设计（与 `agent/cancel.py` 的既有机制配合）。
- **D. 检索策略化**：按场景（问答 / 周报 / 专项）固定一套参数，而不是靠模型临场传参。
- **E. 来源可信度与去重**：域名权重 / 同源合并 / 时效排序（现有仅做 URL 去重）。
- **F. 不新增依赖的最低成本路线**：先把既有 Tavily 的**结果后处理**（排序、去重、正文截断、引用格式）做扎实，通道不动。

## 三、进调研期要取证的点

1. 现有 Tavily 调用的**真实消耗分布**：哪些链路在调、一天多少次（只读查 `data/audit/`，若已记录）。
2. Tavily 免费额度与本机历史用量（外部额度页 + 本地审计对照）。
3. 备选引擎的接入代价：是否需要 key、有没有免费层、结果字段是否够用（须与「配置导向、不写死厂商」的偏好相容）。
4. `strict_days` / `bilingual` 在真实问答里的**触发率**（模型到底传没传这些参数、传得对不对）——这可决定方向 D 的必要性。

## 四、变更记录

| 日期 | 变更 |
|---|---|
| 2026-10-14 | 建骨架（占位期）；完成第一手事实只读探测（§一）；记下候选方向框架与待取证点 |
