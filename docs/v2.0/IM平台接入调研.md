# 智选情报官 · IM 平台接入调研（QQ / 微信 / Telegram）

> 调研背景：对比 AstrBot v4.28.0-beta.1 的 20+ 平台适配器实现，评估 deep_search_pro
> 在 v2.0+ 是否需要、如何接入 IM 平台（QQ/微信/Telegram 等）。
>
> **本文是调研文档，不构成 v2.0 实施承诺**。所有结论需结合 v2.0 实际需求
> （多用户隔离 / 推送渠道扩展 / 移动端可达性）再决定。
>
> 对照项目：`D:\LLM\AstrBot-master\AstrBot`
> 关联文档：`docs/v2.0/v2.0方案.md`、`docs/v2.0/AstrBot_v2.0对比借鉴.md`

---

## 〇、TL;DR

- **AstrBot 接入 IM 的本质**：把"消息收发"做成一组可插拔的适配器，**主 Agent 只见统一
  `MessageChain`，不见平台原始 payload**——经典的 Ports & Adapters 架构。
- **平台不同，协议模型根本不同**（反向 WS / HTTP 回调 / 主动长轮询 三种），不存在
  "一个 SDK 接所有 IM"的银弹。
- **deep_search_pro 当前不需要接 IM**——v2.0 的上下文工程化、RAGFlow、检索治理
  才是 10 倍 ROI 的事。但 **"将现有 webchat 改造成平台适配器"可以顺手做**——它
  是 v2.0 多 session 隔离改造的副产品，成本几乎为 0。
- **如果一定要做**：先接 **Telegram Bot**（30 分钟验证完整链路，库成熟、无企业资质要求），
  再考虑 **QQ 官方机器人**（1-2 天，需开放平台账号），最后才碰企业微信/公众号。
- **强烈不建议接微信个人号第三方协议**（iPad/Web）：封号率高、商业用途违反 ToS。

---

## 一、AstrBot 原理剖析

### 1. 抽象层：统一接口 `Platform(abc.ABC)`

`astrbot/core/platform/platform.py:38` 定义所有平台适配器必须实现的方法：

| 方法 | 职责 |
|---|---|
| `run()` | 启动长连接 / HTTP 服务 / 轮询循环（**适配器入口**） |
| `terminate()` | 关闭 |
| `convert_message(event)` | "平台原始事件" → 统一 `AstrBotMessage` |
| `send_by_session(session, chain)` | 统一 `MessageChain` → 本平台协议发出去 |
| `commit_event()` / `handle_msg()` | 把消息**投到 event_queue**，主循环消费 |

主程序 `core_lifecycle.py` 只 `await platform.run()`，**完全不知道是哪种 IM**。
这就是"核心域是 Agent，平台是外围 IO"的 Hexagonal 架构。

### 2. 三种根本不同的协议模型

| 协议模型 | 例子 | 通信方向 | 典型实现 |
|---|---|---|---|
| **① 反向 WebSocket** | QQ（OneBot v11）、Lagrange、官方 QQ 机器人 SDK | Bot 进程起 **WS Server**，OneBot 客户端反连 | `aiocqhttp_platform_adapter.py:55` `CQHttp(use_ws_reverse=True)` → `:435` `bot.run_task(host, port)` |
| **② HTTP 回调（被动回复）** | 微信公众号、企业微信、Satori 协议 | 平台**主动 POST** 到你暴露的 HTTPS 端点 | `wecom_adapter.py:71-83` 起 aiohttp server，挂 `/callback/command` 路由 |
| **③ 主动长轮询** | Telegram Bot API | 你**定时拉取** `getUpdates` 拿新消息 | `tg_adapter.py`（实现 `getUpdates` 循环） |

### 3. QQ 的两条路线

- **第三方协议（go-cqhttp / Lagrange / napcat）→ OneBot v11 反向 WS**
  - 用 `aiocqhttp` 库封装了 OneBot v11。Bot 进程是 WS **服务端**，客户端**主动连你**。
  - **为什么用"反向 WS"而不是主动调官方 API**：
    - 个人 QQ 没法用官方机器人 API（腾讯 2018 年后基本关停了个人号的 bot 申请）
    - 基于 `Mirai / go-cqhttp` 等开源框架，可以**模拟登录协议**，拿到"事实上的"消息收发能力
    - 反连避免被防火墙/NAT 拦住
- **官方协议（腾讯开放平台）→ `qqofficial`**
  - `astrbot/core/platform/sources/qqofficial/` 走 QQ 官方机器人 SDK（基于 WebSocket 长连接）
  - 需 `appid` + `secret` 走 `bot.send_msg` 这类方法
  - **适合场景**：有企业资质 / 已申请 QQ 机器人频道号的开发者

### 4. 微信的三条路线

| 路线 | 模式 | 难度 | AstrBot 适配器 |
|---|---|---|---|
| **个人微信（网页版协议）** | `weixin_oc` 走**第三方协议**（类似 Mirai） | ★★★★★ 高（腾讯频繁封号） | `astrbot/core/platform/sources/weixin_oc/` |
| **公众号（订阅号/服务号）** | HTTP 回调 + 被动 XML 回复（**5 秒内必须回**） | ★★★ 中（需企业认证 / 测试号即可） | `astrbot/core/platform/sources/weixin_official_account/` |
| **企业微信（自建应用）** | HTTP 回调 + 主动 API（应用支持收发） | ★★★★ 中高（需企业认证） | `astrbot/core/platform/sources/wecom/` |

**关键差异：微信公众号必须"被动"**——拿到消息后必须在 **5 秒内**返回 XML，否则
微信认为你没响应会重试。AstrBot 的公众号适配器通常用"先回空消息 + 异步推客服消息"
模式绕过。

### 5. 消息体归一化：`MessageChain` 抽象

QQ 发分段（文字+@+图片+表情+回复），微信发 XML，Telegram 发 JSON——AstrBot 全部
**先在 `convert_message()` 里拆成统一 `MessageChain` 段列表**，每段是
`Plain` / `Image` / `At` / `File` / `Reply` / `Poke` / `Forward` 等。

Agent 只看 `MessageChain`，**绝不看平台原始 payload**。
这就是"平台无关 Agent"的核心——所有多样性都被压在适配器这一层。

---

## 二、deep_search_pro 现状盘点

| 维度 | 现状 | 接 IM 后影响 |
|---|---|---|
| 入口 | 单入口 Web 前端（SSE 流式 + WebSocket 心跳已有） | 已有"接收 + 推送"通路 |
| 后端 | FastAPI + 主 Agent 单例（`MemorySaver`） | 加 IM 适配器是**新加一个适配器，不动主流程** |
| 用户模型 | 单账号级别 | 变成"按 IM 用户隔离"——QQ 群、人号、群成员都需要 **session 维度状态** |
| 消息/出 | 前端 → 后端 → 推回前端 | IM → 后端 → 推回 IM（**没有流式 UI 进度条**！） |
| 鉴权 | 浏览器登录态 | IM 是**全开放端点**，需要 app secret/access token 校验 |

---

## 三、三条可行路径对比

### 路径 A：官方协议（稳健、合规、门槛高）

| 平台 | 协议 | 库 | 工作量 | 适配难度 |
|---|---|---|---|---|
| **QQ 官方机器人** | Bot SDK 长连接 | `botpy` / `qq-botpy` | 1-2 天 | 低，接口规整 |
| **企业微信** | HTTP 回调 + 主动 API | `wechat-work` SDK 或裸 aiohttp | 2-3 天 | 中，要管加密签名/加解密 |
| **微信公众号** | HTTP 回调（被动 5 秒回） | `wechatpy` / `werobot` | 2-3 天 | **中**，5 秒限制麻烦（用"先 ack 再异步推客服消息"模式） |
| **Telegram Bot** | 长轮询 + sendMessage | `python-telegram-bot` | **半天** | **最低**，有现成 lib |

- **优点**：合规、长期稳定、企业级 SLA
- **缺点**：QQ 官方机器人**不支持个人号**；企业微信/公众号需要企业资质

### 路径 B：第三方开源框架（快，但有封号风险）

- **QQ**：`go-cqhttp` / `Lagrange.Core` / `napcat` / `Shamrock` → 暴露 OneBot v11 标准
  - AstrBot 的 `aiocqhttp` 适配器就是为这个写的，直接抄思路
  - FastAPI 端起 WebSocket Server，OneBot 客户端反连
  - **风险**：腾讯对模拟协议查得严，2024 年后多次大面积封号，需要风控（小号、限频、夜间休眠）
- **微信**：`wechaty`（基于 iPad/Mac/网页协议）→ 暴露 gRPC/RPC
  - **不推荐**：风控比 QQ 还严，商业用途直接违法（腾讯 ToS），第三方协议 2 周~2 月必封

**结论**：QQ 第三方协议**仅适合个人玩**；微信第三方协议**强烈不建议**。

### 路径 C：跨平台协议（一次接入多平台）

- **Satori Protocol**（Satori-bot 开源协议）→ AstrBot 的 `satori` 适配器就是接它
- 一个 Satori Server 同时支持：QQ、Discord、Telegram、飞书、Kook、Slack 等
- **好处**：只实现一次"接 Satori 协议"，**自动多平台**
- **代价**：得自己部署一个 Satori 协议服务（Java/Node，不是 Python），又回到"搭中间层"

**对比**：适合"想支持 5+ 平台但不愿逐个写适配器"的人；deep_search_pro 短期**用不到**，
但若 v3 走 SaaS 多租户，就是必经之路。

---

## 四、推荐落地路线（阶段 0~4）

> **先做 Telegram**。理由：半小时跑通完整链路，验证"适配器抽象 + 消息归一化"
> 在你项目里的可行性，再决定是否上 QQ/微信。

### 阶段 0：抽 Platform 抽象（地基，1 天）

```
deep_search_pro/
├── platforms/                      # 新增
│   ├── __init__.py
│   ├── base.py                     # Platform 抽象（对照 AstrBot 简化）
│   │                               # 核心: run / stop / convert_message / send
│   ├── webchat.py                  # 现有 SSE/WebSocket 入口"升级"为适配器(★)
│   ├── telegram.py                 # 第一个真实 IM 适配器
│   └── qq_official.py              # 后期
```

`base.py` 定义：
- `IncomingMessage`（消息归一化）：`{platform, session_id, user_id, text, raw, images?, reply_to?, mentions?}`
- `OutgoingMessage`（出）：`{text, images?, file?, reply_markup?}`
- `Platform(ABC)`：抽象方法 `run / stop / handle_message(IncomingMessage)`

**关键**：现有 webchat 入口**改造成第一个适配器**——立刻验证抽象能否覆盖现有逻辑，
避免"为未来设计，现在跑不起来"。这也是 v2.0 上下文工程化的**多 session 隔离**副产品，
分摊成本几乎为 0。

### 阶段 1：接 Telegram Bot（最快验证，半天）

- 用 `python-telegram-bot`（v20+ 异步）
- `TelegramPlatform(Platform)` 内部 `application.run_polling()`
- `context.user_data["session_id"] = f"tg:{chat.id}"`，主 Agent 走"按 session 隔离的对话"
- 出消息：`application.bot.send_message(chat_id, text, parse_mode=Markdown)`
- **流式输出问题**：Telegram 只能"整段发或编辑消息"，**没有原生流式**
  - 解决方案：收到消息 → 立刻回"🤔 思考中..." 占位 → Agent 流式产出 → 每段
    `editMessageText` 更新占位 → 完成 → 最终稿
  - 这个"AstrBot 也没有流式"的问题，`support_streaming_message=False` 标记就是证据——
    它也是攒完了再发

### 阶段 2：接 QQ 官方机器人（中等成本，1-2 天）

- 走 `qqofficial`（`astrbot/core/platform/sources/qqofficial/` 思路）
- 用 `botpy`（腾讯官方 SDK）
- `QQOfficialPlatform(Platform)` 内部：起 botpy Client，订阅 `on_at_message_create` 事件
- **关键差异**：QQ 官方机器人**只在 @机器人时**才推送消息，群消息不 @ 不响应
- 配置在开放平台后台：`appid` / `secret` / `token`

### 阶段 3：接企业微信 / 公众号（可选，2-3 天/平台）

- 起 FastAPI 额外路由 `/wecom/callback`、`/wechatmp/callback`
- 验签 → 解密 → 走 `convert_message` → 投到主循环
- **公众号 5 秒限制**：用"先空响应 + 异步客服消息"绕过
  - 注意：客服消息接口**有调用配额**（免费版每天有限条）

### 阶段 4（可选）：走 Satori 跨平台

- 仅当你想"一次接 5+ 平台"时才考虑；否则收益不抵成本

---

## 五、必须解决的"非协议"问题

| 问题 | 解决方案 |
|---|---|
| **多用户隔离** | 现在 `AGENT` 单例，`MemorySaver` 共享。改成"按 `session_id`（`tg:123` / `qq:group:456` / `wc:user_789`）分 `thread_id`"，LangGraph 里就是 `config={"configurable": {"thread_id": ...}}` |
| **图片/附件支持** | webchat 是图→后端直传；IM 是图在平台服务器上，要先 `bot.get_file(file_id)` 拉下来。**偷懒方案**：v2.0 先只支持文字，图让用户"截图发到 web 端" |
| **模型限流** | 用的免费 API，**每个 IM 用户都触发一次请求 = 极易 429**。在平台适配器层加"每用户每分钟 N 条"节流 |
| **敏感信息** | IM 消息里可能含 token、密码——**记录前脱敏**（尤其写到 `MEMORY.md` 之前） |
| **错误/超时** | Agent 卡住时，IM 端会一直"等待中"。适配器设 30s 超时 → 主动回"思考超时，请简化问题" |
| **用户画像归属** | MEMORY 是"账号级"，接 IM 后一人可能有 QQ 号、TG 号两个身份——**MEMORY 按真实用户（手机号/邮箱）归一化**，IM 标识只是联系方式，不是身份 |

---

## 六、最小代码骨架（半小时尝鲜）

如果想**立刻**验证可行性，阶段 0+1 的最简版本：

```python
# platforms/telegram.py
import asyncio
from telegram import Update
from telegram.ext import Application, MessageHandler, filters
from .base import Platform, IncomingMessage, OutgoingMessage

class TelegramPlatform(Platform):
    def __init__(self, token: str, agent_caller):
        self.token = token
        self.agent = agent_caller  # 主 Agent 入口
        self.app = Application.builder().token(token).build()
        self.app.add_handler(MessageHandler(filters.TEXT, self._on_message))

    async def _on_message(self, update: Update, ctx):
        # 1. 归一化
        msg = IncomingMessage(
            platform="telegram",
            session_id=f"tg:{update.effective_chat.id}",
            user_id=str(update.effective_user.id),
            text=update.message.text,
            raw=update,
        )
        # 2. 投到主 Agent（与 webchat 共用）
        reply: OutgoingMessage = await self.agent.handle(msg)
        # 3. 占位 + 最终稿（简单版：整段发；流式需 editMessageText 循环）
        placeholder = await update.message.reply_text("🤔 思考中...")
        await placeholder.edit_text(reply.text)

    def run(self):
        self.app.run_polling()  # 长轮询入口
```

`agent.handle(msg)` 内部**与 webchat 共用**主 Agent 逻辑，只是入口多了一种。
这就是平台抽象的价值：**主程序一行不改，多一个 IM 入口**。

---

## 七、我的坦率判断

1. **接 IM 对这个项目不是优先级**——v2.0 的上下文工程化、RAGFlow、检索治理
   都是 10 倍 ROI 的事。
2. **除非有具体场景**（比如想把周报推送到 Telegram、想在某个 QQ 群当机器人助手），
   否则别提前做。
3. **但阶段 0 可以顺手做**——把现有 webchat 重构成"平台适配器"，它是 v2.0
   上下文工程化（多 session 隔离）的副产品，分摊下来成本几乎为 0。
4. **微信个人号第三方协议强烈不建议**——封号率 + ToS 双重风险。
5. **调研文档值得留**——本文件，半年后你可能忘了为什么"先做 Telegram"。

---

## 八、参考资料

- AstrBot 平台适配器源码：`astrbot/core/platform/platform.py`、`astrbot/core/platform/sources/`
- AstrBot 三种协议代表：
  - 反向 WS：`astrbot/core/platform/sources/aiocqhttp/aiocqhttp_platform_adapter.py`
  - HTTP 回调：`astrbot/core/platform/sources/wecom/wecom_adapter.py`
  - 长轮询：`astrbot/core/platform/sources/telegram/tg_adapter.py`
- OneBot v11 规范：<https://github.com/botuniverse/onebot-11>
- Satori Protocol：<https://satori.chat/>
- python-telegram-bot：<https://python-telegram-bot.org/>

---

*由智选情报官 v2.0 规划组维护 · 调研日期 2026-09*
