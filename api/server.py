# ============================================================
# 智选情报官 · FastAPI 服务（M2）
# 能力：
#   1) REST 登录/注册（账号角色分流，company / personal）
#   2) REST 问答（POST /api/chat，后台线程跑 agent，多轮按 username 隔离）
#   3) WebSocket 实时进度（/ws/{thread_id}，把 api/monitor 埋点推到前端右栏）
#   4) 静态 SPA 托管（front/index.html）
#
# 设计要点：
#   - deepagents 的 agent.invoke 是同步阻塞的，必须用 asyncio.to_thread
#     放到线程池执行，否则会卡住整个事件循环、WebSocket 也推不出去。
#   - monitor 的 _emit 通过 asyncio.run_coroutine_threadsafe 把进度事件
#     从工作线程送回主事件循环，再经 ConnectionManager.send_to_thread 推给
#     对应 thread_id 的 WebSocket —— 这条链路 依赖本文件在 startup 时
#     manager.set_loop(get_running_loop()) 完成 loop 绑定。
#   - thread_id 用 username（REST 会话）或前端传入的任意 id（独立 WS 模式）。
# ============================================================

import asyncio
import json
import os
import re
import shutil
import httpx
import sys
import time
import threading
from pathlib import Path
from typing import List, Optional

# ---- 项目根路径防御（务必放在任何「项目内包」导入之前）----
# 本机 Hermes 全局 venv 里存在一个同名为 `agent` 的顶层包，会遮蔽本项目的
# agent/ 目录；db_tools.py / tavily_tool.py 也靠 sys.path.insert 规避。
# 这里：① 把项目根 insert(0) 保证最高优先级；② 剔除 Hermes venv 的
# site-packages，彻底避免同名包污染。
_PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_PROJECT_ROOT))
sys.path = [p for p in sys.path if "hermes-agent" not in p and "hermes_agent" not in p]

from dotenv import load_dotenv
from fastapi import UploadFile, File, Form, Body, Depends, FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect, status
from pydantic import BaseModel
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from langchain_core.messages import AIMessage, HumanMessage

from agent.llm import model
from agent.prompts import main_agent_content
from agent.subagents.db_agent import db_agent
from agent.subagents.network_search_agent import network_search_agent
from agent.subagents.personal_agent import personal_agent
# 文档读写工具：主智能体可把知识/笔记保存到 agents_docs（仅限该目录 .md/.txt）
from tools.readtofile import read_agent_doc, list_agent_docs
from tools.writetofile import write_agent_doc
from tools.profile_update import update_user_profile   # M3-5：结构化画像写入（按维度就地更新）
from tools.price_tool import record_price   # M5-3：记价工具（模糊匹配，多候选让模型问用户）
from tools.kb_tools import query_kb  # M3：个人知识库检索工具（fast 模式）
from tools.shell_executor import run_shell_command  # M4b：CLI 沙箱命令工具（与 CLI 面板共用执行器）
from tools._runtime import shell_runtime as shell_runtime  # M4b：CLI 面板 /api/shell/* 路由用
from tools import cli_registry as cli_reg  # M4c：第三方 CLI 接入登记（注册表 + 本机检测 + 状态）
from api.account import LoginReq, RegisterReq, login, logout, register, get_session
from api.context import set_owner_context, set_thread_context
import api.me_user_data as me
import api.tools_sources as ts        # v3.0 M1：工具来源（API 适配器配置面）
from tools.capability_invoke import invoke_tool  # v3.0 M1：API/MCP 能力的唯一调用入口
from api.monitor import manager, monitor
from agent import cancel as agent_cancel
from agent import answer_text
import api.context as ctx        # M4-3：给计数器提供账号/会话上下文
from agent import usage_counter   # M4-2/3：用量计数器（计数不依赖各工具自觉上报）
from agent import context_sources   # M4-7：注入源注册表（收集）
import api.customize as cust
import api.voice_tts as vtts
from deepagents import create_deep_agent
from langchain_core.messages import AIMessage, HumanMessage
from agent.reasoning_model import ReasoningChatOpenAI
from deepagents.middleware._tool_exclusion import _ToolExclusionMiddleware

load_dotenv()

# M4-3：把"当前账号/会话"注入计数器 —— 反过来由 api 层提供读取函数（agent 层不 import api.*）。
def _usage_context():
    """给计数器提供 `(account_id, thread_id, turn_index)`。

    ★ 2026-09-28 真机抽样发现的缺口：M4-3 时这里只返回了 2 元组 → `turn_index` 恒为 0，
      于是 `/api/usage` 的「本轮」等于"所有 turn=0 的行"，**这一维在真机上等于废的**。
      轮号取会话的 `message_count // 2`（一轮 ≈ user+assistant 两条消息），单调递增、够用；
      取不到就给 0（不抛，计数器坏了不能拖垮对话）。
    """
    aid, tid = ctx.get_owner_context(), ctx.get_thread_context()
    turn = 0
    if aid and tid:
        try:
            from tools.schema_personal import get_personal_conn
            conn = get_personal_conn()
            try:
                row = conn.execute("SELECT message_count FROM conversations WHERE thread_id=?",
                                   (tid,)).fetchone()
            finally:
                conn.close()
            if row:
                n = row[0] if not hasattr(row, "keys") else row["message_count"]
                turn = max(0, int(n or 0)) // 2
        except Exception:
            turn = 0
    return (aid, tid, turn)


usage_counter.set_context_reader(_usage_context)


def _usage_provider():
    """给计数器提供**当前账号生效的模型配置行**（M6c-3：账目按 provider 维度记）。

    取 `is_active DESC, id DESC` 第一条：即"用户当前选中的那套模型配置"。
    拿不到就返回 None → 该条用量只记 token、provider_id 留空（绝不编造）。
    """
    aid = ctx.get_owner_context()
    if not aid:
        return None
    try:
        from tools.schema_personal import get_personal_conn
        conn = get_personal_conn()
        try:
            row = conn.execute("SELECT * FROM llm_providers WHERE owner_id=?"
                               " ORDER BY is_active DESC, id DESC LIMIT 1", (int(aid),)).fetchone()
            return dict(row) if row is not None else None
        finally:
            conn.close()
    except Exception:
        return None


usage_counter.set_provider_resolver(_usage_provider)


def _llm_error_hint(err, provider=None) -> str:
    """把上游的模型调用错误翻成**能照着做**的一句话（2026-09-30 用户实测事故的教训）。

    为什么需要它：当时上游只回 `401 … Your api key: ****f98f is invalid` ✗ ——
    "掩码保留了尾部 4 位"看着就像真 key，用户根本没想到是**本地把掩码串当密钥发出去了**，
    也没有任何地方告诉他"这次用的是哪一套自定义配置"。所以这里必须**点出配置**并给出动作。
    """
    t = str(err or "")
    low = t.lower()
    who = ""
    try:
        p = provider or _usage_provider()
        if p:
            who = "（用的是自定义配置 #%s「%s / %s」）" % (p.get("id"), p.get("provider_name"),
                                                          p.get("model_name"))
        else:
            who = "（用的是项目默认模型，来自 .env）"
    except Exception:
        who = ""
    if "401" in t or "authenticationerror" in low or "invalid_api_key" in low:
        return ("密钥无效、或与接口地址不配对%s → 去「定制助手 → 模型选型」用 👁 核对该配置的 key 是否完整；"
                "或点「✓ 使用中（再点停用）」停用它，先回到项目默认模型" % who)
    if "402" in t or "insufficient" in low:
        return "上游账户余额不足%s → 充值或换一套配置" % who
    if "404" in t or "model_not_found" in low or "does not exist" in low:
        return "模型名不存在或接口地址不对%s → 核对模型名与 base_url（以厂商文档为准）" % who
    if "429" in t or "rate limit" in low or "too many requests" in low:
        return "触发上游限流%s → 等几十秒重试；免费模型限流更频繁" % who
    if "timeout" in low or "connect" in low or "ssl" in low:
        return "连不上接口%s → 检查网络与 base_url；本机若开了代理，本地地址要绕开代理" % who
    return "模型调用失败%s" % who

# M4-7：注册注入源（依赖倒置的另一半）—— import 一次即完成注册。
# 回退开关 ZX_ASSEMBLY_SOURCES=legacy → 继续走"server 取好再传参"的老路径（逐字不变）。
import api.context_providers as ctx_providers          # noqa: E402
import api.price_api as price_ack                      # noqa: E402  (M5-4)
import api.cost_api as cost_api                        # noqa: E402  (M6c-5)
ctx_providers.register_all()
_USE_SOURCES = os.getenv("ZX_ASSEMBLY_SOURCES", "").strip().lower() != "legacy"

# deepagents 内置 filesystem 工具（ls/read_file/glob/grep/write_file/edit_file/delete）
# 与项目自定义文档工具（read_agent_doc/write_agent_doc，仅限 agents_docs 目录）功能重叠，
# 且名字更通用导致模型优先选内置工具、绕过 agents_docs 安全约束——一律排除。
_EXCLUDED_FS_TOOLS = frozenset({"ls", "read_file", "glob", "grep", "write_file", "edit_file", "delete"})

# ---------------------------------------------------------------------------
# 全局单例：主 Agent（只 build 一次，共享同一个 checkpointer = 多轮记忆）
# ---------------------------------------------------------------------------
AGENT = create_deep_agent(
    model=model,
    system_prompt=main_agent_content["system_prompt"],
    subagents=[network_search_agent, db_agent, personal_agent],
    tools=[read_agent_doc, write_agent_doc, list_agent_docs, query_kb, run_shell_command, invoke_tool,
           update_user_profile, record_price],
    middleware=[_ToolExclusionMiddleware(excluded=_EXCLUDED_FS_TOOLS),
                    usage_counter.UsageCounterMiddleware()]   # M4-3：用量计数（框架级，C1）,#去除deepagents内置的filesystem工具，只保留项目自定义的
    # M1：不再用 MemorySaver 承载消息历史（进程重启即丢、无界增长）。
    # 对话历史由 SQLite 持久化（agent/conversation_store.py），每次 invoke 显式传入完整上下文。
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]


# ---------------------------------------------------------------------------
# 异步安全地跑同步 agent
# ---------------------------------------------------------------------------
# 按账号缓存的自定义 agent（用户配置了自定义模型时构建；无则用全局 AGENT）
_custom_agents: dict = {}


def _build_custom_agent(provider: dict):
    """按账号构建独立主 Agent（M2：agent 静态化）。

    只注入「账号自定义模型 + main 基础提示词」——SOUL 人格 / MEMORY 记忆画像等
    动态内容不再固化进构建（每次 invoke 前由 build_request_context 装配注入）。
    """
    if provider:
        custom_model = ReasoningChatOpenAI(
            model=provider["model_name"],
            api_key=provider["api_key"],
            base_url=provider["base_url"],
        )
    else:
        custom_model = model  # 无自定义模型时用默认 .env 模型
    sys_prompt = main_agent_content["system_prompt"]
    return create_deep_agent(
        model=custom_model,
        system_prompt=sys_prompt,
        subagents=[network_search_agent, db_agent, personal_agent],
        tools=[read_agent_doc, write_agent_doc, list_agent_docs, query_kb, run_shell_command, invoke_tool,
           update_user_profile, record_price],
        middleware=[_ToolExclusionMiddleware(excluded=_EXCLUDED_FS_TOOLS),
                    usage_counter.UsageCounterMiddleware()]   # M4-3：用量计数（框架级，C1）,
    )


def _get_memory_text(account_id: int):
    """读取该账号 MEMORY.md 内容 + 用户名（供注入 system_prompt），失败静默返回 ("", "")"""
    try:
        import api.customize as _cust
        from tools.schema_personal import get_personal_conn
        conn = get_personal_conn()
        try:
            row = conn.execute("SELECT username FROM accounts WHERE id=?", (account_id,)).fetchone()
        finally:
            conn.close()
        if not row:
            return "", ""
        username = row["username"]
        # M4-8：正文读取也走唯一事实源（与周报侧同一个函数 → 不可能读到不同的文件）
        from tools import user_doc_paths as _paths
        return _paths.read_memory_for_agent(username), username
    except Exception as e:
        print(f"[memory] 读取 MEMORY.md 失败（忽略）: {e}")
        return "", ""


def _get_agent_for(account_id: int | None):
    """按账号选择 agent（M2：agent 静态化——只按自定义模型配置缓存）。

    SOUL 人格 / MEMORY 记忆画像不再进入构建（走每次 invoke 的请求装配），
    因此只有「自定义模型」变化才需要重建 agent；改人格/记忆零重建。
    无自定义模型 → 全局 AGENT（静态单例）。
    """
    if account_id is None:
        return AGENT
    try:
        provider = cust.get_active_provider(account_id)
    except Exception:
        provider = None
    if provider:
        # 缓存 key：账号 + 模型配置（base_url/model_name/api_key 任一变化 → 重建）
        key = (account_id, provider.get("base_url"), provider.get("model_name"),
               provider.get("api_key"))
        if key not in _custom_agents:
            _custom_agents[key] = _build_custom_agent(provider)
        return _custom_agents[key]
    return AGENT


def _run_agent(question: str, thread_id: str, account_id: int | None = None,
               skill_names: list[str] | None = None) -> str:
    """在工作线程里执行同步的 agent.invoke（M1 上下文工程化改造）。

    上下文装配：
      1. 历史从 SQLite 读取（agent/conversation_store.get_history）
      2. agent/context_budget 做 token 预算（截断/压缩）
      3. invoke 显式传入完整消息（无 MemorySaver 累积，多轮靠 SQLite 历史）
      4. 本轮新增消息（user 提问 + 工具消息 + 回答）持久化回 SQLite
    monitor 埋点会经 run_coroutine_threadsafe 推到主 loop 的 WebSocket；
    account_id 注入数据归属上下文，工具查询按 owner_id 隔离。
    """
    set_thread_context(thread_id)  # 让 monitor 知道本轮进度推给哪个 WS
    if account_id is not None:
        set_owner_context(account_id)  # 数据归属权：Agent 只能读到该用户的数据

    agent = _get_agent_for(account_id)

    # M2：请求装配管线——动态内容（SOUL 人格 + MEMORY 记忆画像）每次 invoke 前
    # 拼成 SystemMessage 注入 messages 首条（agent 已静态化，不再因画像变化重建）。
    from agent.build_context import build_request_context
    if account_id is not None:
        try:
            soul_text = cust.get_soul_content(account_id)
        except Exception:
            soul_text = ""
        memory_text, username = _get_memory_text(account_id)
    else:
        soul_text, memory_text, username = "", "", ""
    # M4a：技能说明书由 server 层读取（技能文件在 api 层的用户文档目录下，
    # 装配管线不 import api.* 以免反向依赖），再经 skills_text 传入装配。
    skills_text = ""
    if account_id is not None and skill_names:
        try:
            skills_text = cust.get_skills_text(account_id, skill_names)
        except Exception as e:
            print(f"[M4a] 技能读取失败（忽略）: {type(e).__name__}: {e}")
    # M4c：用户自配 CLI 的可用清单——由 server 层取好传入（装配管线不 import api.*）。
    # 目的是消除「配好了却自称没有」的幻觉；没配 CLI 时是空串，零开销。
    # M5b：改由 `tool_router` 决定注入内容——工具少（≤4）时与 M5a 逐字一致（快路径）；
    # 工具多时只把**相关**工具完整注入，其余留一行名称（不隐身），不确定则回退全量。
    cli_brief = ""
    route_mode = "full"
    if account_id is not None:
        try:
            from tools import tool_router
            _r = tool_router.route_tools(account_id, question)
            cli_brief = (_r.brief + "\n" + _r.examples) if _r.examples else _r.brief
            route_mode = _r.mode
            if _r.mode == "routed":
                monitor.report_thinking(
                    "（工具路由：%d 个工具中命中 %d 个完整能力卡）"
                    % (_r.detail.get("pool", 0), len(_r.hits)))
        except Exception as e:
            print(f"[M4c/M5] 工具注入生成失败（忽略）: {type(e).__name__}: {e}")
    # M3-5：画像待更新信号（服务端算数字，模型只判断相关性）。没有新数据时是空串，零开销。
    profile_signal = ""
    if account_id is not None and memory_text:
        try:
            from tools import memory_profile as _mprof
            from tools import profile_evidence as _pev
            profile_signal = _pev.staleness(account_id, _mprof.parse_meta(memory_text))
        except Exception as e:
            print(f"[M3] 画像待更新信号生成失败（忽略）: {type(e).__name__}: {e}")
    if _USE_SOURCES:
        # 新路径：soul/memory 让 build_context 自己从注册表取（server 不再取这两个）
        ctx = build_request_context(
            thread_id, account_id, question,
            username=username, skills_text=skills_text, cli_brief=cli_brief,
            profile_signal=profile_signal, sources=context_sources.collect(account_id, question),
        )
    else:
        # 老路径（ZX_ASSEMBLY_SOURCES=legacy）：server 取好再传 —— 与改造前逐字一致
        ctx = build_request_context(
            thread_id, account_id, question,
            soul_text=soul_text, memory_text=memory_text, username=username,
            skills_text=skills_text, cli_brief=cli_brief, profile_signal=profile_signal,
        )
    if ctx.cli_injected:
        monitor.report_thinking(
            "（已注入可用 CLI 清单 %d 条）"
            % len([ln for ln in cli_brief.splitlines() if ln.startswith("- ")]))
    if ctx.skills_injected:
        monitor.report_thinking(f"（已注入 {len(skill_names or [])} 个技能说明书）")
    invoke_messages = ctx.messages
    if ctx.dropped_rounds:
        monitor.report_thinking(f"（历史过长，已自动截断 {ctx.dropped_rounds} 轮旧对话）")
    if ctx.compressed:
        monitor.report_thinking("（历史过长，已自动压缩旧对话摘要）")

    cfg = {"configurable": {"thread_id": thread_id}}
    # 用户强制中断（v2.0 聊天界面优化）：登记本轮为可中断运行；中断标志由
    # 前端 POST /api/chat/cancel 置位，模型调用前的检查点负责抛 AgentCancelled。
    # finally 里清标记，避免残留标志把下一轮 invoke 立刻掐掉。
    agent_cancel.begin(thread_id)
    try:
        result = agent.invoke({"messages": invoke_messages}, cfg)
    finally:
        agent_cancel.end(thread_id)
    # 思考捕获（A+ 方案）：ReasoningChatOpenAI 保留 reasoning_content 到每轮
    # AIMessage 的 additional_kwargs，invoke 完成后遍历提取并推给前端右栏——
    # 单请求（省 token）、不弃 langchain、覆盖所有轮次（含工具调用后的思考）。
    from agent.thinking_capture import report_thinking_from_messages
    report_thinking_from_messages(result["messages"])

    # ★ 最终答案提取（2026-09-24 用户报障修复）：推理模型偶尔把**可见正文**写进
    #   reasoning_content 而 content 为空（实测约 1/5）——原来直接取 messages[-1].content
    #   会拿到空串，气泡没正文、正文只出现在右侧思考面板。统一走 agent.answer_text：
    #   content → reasoning 回填 → 本轮更早的非工具回答；回填/回溯时会**把文本写回该条消息**，
    #   否则落库仍是空内容，刷新后又变成空白卡片。
    #   注：`_boundary` 必须限定在本轮内找，跨轮回溯会把上一轮的旧答案当成这一轮的答案。
    _boundary = {ctx.final_user_msg or question, question}
    answer, _ans_src = answer_text.restore_answer(result["messages"], _boundary)
    if _ans_src == answer_text.SOURCE_REASONING:
        monitor.report_thinking("（模型未返回正文，已用其思考内容回填答案）")
    elif _ans_src == answer_text.SOURCE_EARLIER:
        monitor.report_thinking("（模型未返回正文，已回填本轮更早的一段回答）")
    elif _ans_src == answer_text.SOURCE_NONE:
        answer = ("⚠️ 本轮模型没有返回正文（上游偶发把输出放进了思考字段）。可以再问一次；"
                  "若反复出现，把右侧「模型思考过程」的内容反馈给我，便于继续定位。")
        monitor.report_thinking("（本轮没有正文可回填，已给出显式提示而非空白回复）")
        # 补一条带内容的 assistant：既让实时气泡有正文，也保证刷新后不是空卡、且「问-答成对」
        result["messages"].append(AIMessage(content=answer))

    if account_id is not None:
        # M1：本轮持久化（user 提问 + 新增的 assistant/tool 消息）
        try:
            from agent import conversation_store as cs
            # 结果消息里 question 之后的部分 = 本轮新增（含工具调用链）
            new_msgs: list = []
            seen_q = False
            # M4a：技能前缀会改写实际用户文本（ctx.final_user_msg），
            # 故按「装配后的最终文本」定位本轮起点；无技能时二者相等。
            # 若都不匹配（异常），退化为只存最终回答。
            for m in result["messages"]:
                if isinstance(m, HumanMessage) and m.content in _boundary:
                    seen_q = True
                    continue
                if seen_q:
                    new_msgs.append(m)
            if not seen_q:  # 兜底（异常情况）：至少存最终回答
                new_msgs = [result["messages"][-1]]
            # ★ 落库前过滤（2026-09-24 报障修复的另一半）：工具轮会产生 content="\n" 的 assistant
            #   消息，它们本身没有信息量，却会在前端各渲染成一张“只有标题、内容为空”的报告卡
            #   （实测一轮 7 个工具轮 → 刷新后 8 张卡）。带 tool_calls 的**不能丢**：
            #   OpenAI 格式要求 tool 消息紧跟带 tool_calls 的 assistant，否则重放历史会被上游 400。
            new_msgs = answer_text.drop_empty_assistant(new_msgs)
            # token 用量：从最后一条 AIMessage 的 usage_metadata 取（尽力而为）
            tok_usage = 0
            for m in reversed(result["messages"]):
                um = getattr(m, "usage_metadata", None)
                if um and um.get("total_tokens"):
                    tok_usage = um["total_tokens"]
                    break
            _sr = cs.save_turn(account_id, thread_id, question, new_msgs, token_usage=tok_usage)
        except Exception as e:
            print(f"[M1] 会话持久化失败（忽略）: {type(e).__name__}: {e}")

    return answer


# ---------------------------------------------------------------------------
# FastAPI 应用
# ---------------------------------------------------------------------------
app = FastAPI(title="智选情报官", version="M2")


@app.on_event("startup")
async def _startup():
    # 关键：把主事件循环绑定给 monitor.ConnectionManager，
    # 这样 monitor._emit 里的 run_coroutine_threadsafe 才有合法的 loop 可投送。
    manager.set_loop(asyncio.get_event_loop())
    print("[M2] FastAPI 启动，monitor loop 已绑定。")
    _wx_load_disk()   # 工作台天气：先吃磁盘缓存，首屏秒出
    threading.Thread(target=_wx_warm, daemon=True).start()   # 后台预热默认城市（外网首次实测 ~16s）
    from api.digest_scheduler import start as start_digest_sched
    start_digest_sched()


@app.on_event("shutdown")
async def _shutdown():
    from api.digest_scheduler import stop as stop_digest_sched
    stop_digest_sched()


# ----------------------------- 账号 -----------------------------
@app.post("/api/register", summary="注册账号")
async def api_register(req: RegisterReq):
    resp = register(req)
    try:
        cust.ensure_user_memory(req.username.strip())  # 创建账号即生成空记忆画像 MEMORY.md
    except Exception as e:
        print(f"[memory] 初始化 MEMORY.md 失败（忽略）: {e}")
    return resp


@app.post("/api/login", summary="登录（返回 token）")
async def api_login(req: LoginReq):
    return login(req)


@app.post("/api/logout", summary="登出")
async def api_logout(token: str = Query(...)):
    logout(token)
    return {"ok": True}


# ----------------------------- 问答 -----------------------------
@app.post("/api/chat", summary="发送一条消息，返回最终回答（多轮按会话隔离，M1）")
async def api_chat(
    question: str = Query(..., description="用户问题"),
    token: str = Query(..., description="登录 token"),
    thread_id: str = Query(None, description="会话 ID（前端「+ 新对话」建的 UUID）；缺省=用户名单会话（兼容 v1.0）"),
    skills: str = Query("", description="M4a：本轮启用的技能名，逗号分隔（如 '竞品对比,来源标注'）；空=不用技能"),
):
    sess = get_session(token)  # 校验登录，无效抛 401
    # 会话存储在本函数多处要用（含 v1.0 兼容路径下的 last_msg_id），提到函数顶部
    # 统一 import：原来只在 thread_id 分支里 import，不带 thread_id 时会 UnboundLocalError
    from agent import conversation_store as cs
    # M4a：技能名列表（一次性，仅本轮生效；后端按账号读文件，越权名会被静默跳过）
    skill_names = [s.strip() for s in (skills or "").split(",") if s.strip()]
    if not thread_id:
        thread_id = sess["username"]  # v1.0 兼容：以用户名为 thread
    else:
        # M1 多会话：校验归属（防越权用他人 thread_id），不存在则 404
        if cs.get_session(sess["account_id"], thread_id) is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "会话不存在或不属于当前账号")
    monitor.set_current_thread(thread_id)  # 让工作线程里的 monitor 埋点能定向推 WS
    cancelled = False
    try:
        # 画像变更通知：记录调用前的 MEMORY.md 内容，回复末尾对比提示
        before_memory = cust.memory_get(token).get("content", "")
        answer = await asyncio.to_thread(_run_agent, question, thread_id,
                                        sess.get("account_id"), skill_names)
        try:
            after_memory = cust.memory_get(token).get("content", "")
            if after_memory != before_memory:
                answer = f"{answer}\n\n---\n📝 已更新你的记忆画像（智能体在本次对话中做了记忆维护），可在「定制助手 → 记忆画像」查看或编辑。"
        except Exception:
            pass
    except agent_cancel.AgentCancelled:
        # 用户点了「中断」（v2.0 聊天界面优化）：不是错误，转成正常响应。
        # 落一条占位回答，保持「问-答成对」——否则历史里悬着一个没有回答的提问，
        # 下一轮装配时模型会把它当成待答问题；前端也用这条回复挂「删除」按钮。
        cancelled = True
        answer = "⏹ 本轮回答已被中断（未完成）。"
        try:
            cs.save_turn(sess.get("account_id"), thread_id, question,
                         [AIMessage(content=answer)])
        except Exception as e:
            print(f"[中断] 占位回答落库失败（忽略）: {type(e).__name__}: {e}")
    except Exception as e:  # 不吞掉错误，给出可读信息
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR,
                            f"Agent 执行出错：{_llm_error_hint(e)}\n\n{type(e).__name__}: {e}")
    finally:
        monitor.clear_current_thread()
    return {
        "answer": answer,
        "role": sess["role"],
        "thread_id": thread_id,
        "cancelled": cancelled,  # 前端据此把这条气泡标成「已中断」
        "last_msg_id": cs.get_last_msg_id(thread_id),  # M3：用于 kb 重复入库检测
    }


@app.post("/api/chat/cancel", summary="强制中断本轮回答（前端「中断」按钮）")
async def api_chat_cancel(token: str = Query(...),
                          thread_id: str = Query(None, description="缺省=用户名单会话（兼容 v1.0）")):
    """给正在跑的那轮 invoke 置中断标记。

    生效点：`agent/reasoning_model.py` 里每次模型调用前的检查点
    （`agent/cancel.py` 说明为什么选在这儿）——抛出的 AgentCancelled 会穿透
    langgraph 冒回 `/api/chat`，那里以 `cancelled=true` 正常收尾。
    局限：工具内部正在等 HTTP 响应（如一次网搜）打断不了，会在它返回后的
    下一个模型调用点生效，秒级延迟。
    """
    sess = get_session(token)
    from agent import conversation_store as cs
    tid = thread_id or sess["username"]
    if thread_id and cs.get_session(sess["account_id"], thread_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "会话不存在或不属于当前账号")
    hit = agent_cancel.request(tid)
    return {
        "ok": True, "thread_id": tid, "hit": hit,
        "detail": "已请求中断" if hit else "该会话当前没有正在跑的回答（可能刚好结束）",
    }


@app.post("/api/chat/message/delete", summary="删除一轮问答（问题 + 回答 + 工具链，不可恢复）")
async def api_message_delete(token: str = Query(...),
                             msg_id: int = Query(..., description="AI 回复气泡的消息 id（last_msg_id）"),
                             thread_id: str = Query(None, description="缺省=用户名单会话（兼容 v1.0）")):
    """前端 AI 回复气泡右上角的「删除」按钮：只传回答的 msg_id，成对删除规则
    （连带该轮提问与工具链）在 `conversation_store.delete_turn` 里。"""
    sess = get_session(token)
    from agent import conversation_store as cs
    tid = thread_id or sess["username"]
    if thread_id and cs.get_session(sess["account_id"], thread_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "会话不存在或不属于当前账号")
    res = cs.delete_turn(sess["account_id"], tid, msg_id)
    if not res.get("ok"):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "消息不存在或已被删除（刷新会话后重试）")
    return res


# ----------------------------- 会话管理（M1 上下文工程化） -----------------------------
@app.get("/api/chat/sessions", summary="当前账号会话列表（按活跃倒序）")
async def api_sessions_list(token: str = Query(...)):
    sess = get_session(token)  # 401 if invalid
    from agent import conversation_store as cs
    items = cs.list_sessions(sess["account_id"])
    return {"items": items}


@app.post("/api/chat/sessions", summary="新建空会话（UUID thread_id，前端随后带它发问）")
async def api_sessions_create(token: str = Query(...)):
    sess = get_session(token)
    from agent import conversation_store as cs
    s = cs.create_session(sess["account_id"], "新对话")
    return s


@app.get("/api/chat/sessions/{thread_id}", summary="读某会话全部消息")
async def api_sessions_read(thread_id: str, token: str = Query(...)):
    sess = get_session(token)
    from agent import conversation_store as cs
    if cs.get_session(sess["account_id"], thread_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "会话不存在或不属于当前账号")
    msgs = cs.get_messages_plain(sess["account_id"], thread_id)
    return {"messages": msgs}


@app.patch("/api/chat/sessions/{thread_id}", summary="重命名会话标题")
async def api_sessions_rename(thread_id: str, token: str = Query(...), title: str = Query(...)):
    sess = get_session(token)
    from agent import conversation_store as cs
    ok = cs.rename_session(sess["account_id"], thread_id, title.strip()[:30] or "新对话")
    if not ok:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "会话不存在或不属于当前账号")
    return {"ok": True}


@app.delete("/api/chat/sessions/{thread_id}", summary="删除会话（级联删消息，不可恢复）")
async def api_sessions_delete(thread_id: str, token: str = Query(...)):
    sess = get_session(token)
    from agent import conversation_store as cs
    ok = cs.delete_session(sess["account_id"], thread_id)
    if not ok:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "会话不存在或不属于当前账号")
    return {"ok": True}


# ----------------------------- 知识库（M3 RAG，每用户 Chroma 库） -----------------------------
def _kb_username(token: str) -> str:
    """token → 当前账号 username（KB 按 username 目录隔离）。"""
    sess = get_session(token)
    return sess["username"]


@app.get("/api/kb/status", summary="知识库状态（是否已创建/文档数）")
async def kb_status(token: str = Query(...)):
    sess = get_session(token)
    username, aid = sess["username"], sess["account_id"]
    from rag_knowledge.kb_service import kb_exists
    from rag_knowledge import kb_store
    recs = kb_store.list_records(username) if kb_exists(username, aid) else []
    return {"exists": kb_exists(username, aid), "doc_count": len(recs),
            "docs": recs}  # 顺带返回列表，前端一次拿全


@app.post("/api/kb/ingest", summary="文本入库（md5 去重；首次自动建库）")
async def kb_ingest(token: str = Query(...), req: dict = Body(...)):
    """M3 评估+清洗：始终跑 evaluator(质量建议) + cleaner(脏内容清洗)。

    防重复：req.message_id + req.force：
      - message_id 非空且对应消息 kb_ingested=1 时 → 除非 force=True，否则拒入。
      - 入库成功后自动把对应消息的 kb_ingested 置 1（仅 source_kind=export 的「导出报告」路径）。
    """
    sess = get_session(token)
    username, aid = sess["username"], sess["account_id"]
    title = (req.get("title") or "").strip()[:60]
    content = (req.get("content") or "").strip()
    source_kind = req.get("source_kind") or "paste"
    message_id = req.get("message_id")
    force = bool(req.get("force", False))
    if not content:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "内容为空")
    # -- 重复入库拦截：消息级 --
    if message_id and not force:
        try:
            from tools.schema_personal import get_personal_conn
            conn = get_personal_conn()
            row = conn.execute(
                "SELECT kb_ingested FROM messages m "
                "JOIN conversations c ON c.id = m.conversation_id "
                "WHERE m.id = ? AND c.account_id = ?",
                (message_id, aid),
            ).fetchone()
            conn.close()
            if row and (row["kb_ingested"] if isinstance(row, dict) else row[0]):
                raise HTTPException(
                    status.HTTP_400_BAD_REQUEST,
                    "该报告已入过知识库（force=true 强制再入）",
                )
        except HTTPException:
            raise
        except Exception:
            pass  # 查询失败不阻塞入库
    try:
        from rag_knowledge import cleaner
        from rag_knowledge.evaluator import evaluate_doc
        cleaned = cleaner.clean_for_kb(content)
        needs_clean = cleaned != content
        try:
            verdict = evaluate_doc(title or "未命名", content)
        except Exception as e:
            verdict = {"quality_ok": True, "suggestion": "评估器异常，按可入库处理", "error": str(e)}
    except Exception as e:
        cleaned = content
        needs_clean = False
        verdict = {"quality_ok": True, "suggestion": "评估+清洗模块异常，原文入库", "error": str(e)}
    from rag_knowledge.kb_service import ingest_text
    r = ingest_text(username, title or "未命名文档", content, source_kind, account_id=aid)
    if not r.get("ok"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, r.get("error", "入库失败"))
    r["evaluation"] = verdict
    r["cleaned_preview"] = cleaned[:800] if needs_clean else ""
    r["needs_clean"] = needs_clean
    # -- 入库成功：写回 messages.kb_ingested（仅导出报告路径，且消息归属当前账号）--
    if message_id and source_kind == "export":
        try:
            from tools.schema_personal import get_personal_conn
            conn = get_personal_conn()
            conn.execute(
                "UPDATE messages SET kb_ingested = 1 "
                "WHERE id = ? AND conversation_id IN "
                "(SELECT id FROM conversations WHERE account_id = ?)",
                (message_id, aid),
            )
            conn.commit()
            conn.close()
        except Exception:
            pass
    return r


@app.post("/api/kb/evaluate", summary="入库评估（脏/质量建议 + 清洗预览）")
async def kb_evaluate(token: str = Query(...), req: dict = Body(...)):
    username = _kb_username(token)
    title = (req.get("title") or "").strip()[:60]
    content = (req.get("content") or "").strip()
    from rag_knowledge import cleaner
    from rag_knowledge.evaluator import evaluate_doc
    cleaned = cleaner.clean_for_kb(content)
    try:
        verdict = evaluate_doc(title or "未命名", content)
    except Exception as e:
        verdict = {"error": str(e)}
    return {"evaluation": verdict, "cleaned_preview": cleaned,
            "needs_clean": cleaned != content}


@app.post("/api/kb/upload", summary="上传 .md 文件入库（仅 md，批注 12）")
async def kb_upload(token: str = Query(...), file: UploadFile = File(...)):
    """M3 上传 .md 也跑评估+清洗（AI 报告脏内容重灾区）。"""
    sess = get_session(token)
    username, aid = sess["username"], sess["account_id"]
    fname = file.filename or ""
    if not fname.lower().endswith(".md"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "仅支持 .md 文件（文档同一性）")
    raw = (await file.read()).decode("utf-8", errors="ignore")
    title = fname[:-3] if fname.endswith(".md") else fname
    try:
        from rag_knowledge import cleaner
        from rag_knowledge.evaluator import evaluate_doc
        cleaned = cleaner.clean_for_kb(raw)
        needs_clean = cleaned != raw
        try:
            verdict = evaluate_doc(title, raw)
        except Exception as e:
            verdict = {"quality_ok": True, "suggestion": "评估器异常，按可入库处理", "error": str(e)}
    except Exception as e:
        cleaned = raw
        needs_clean = False
        verdict = {"quality_ok": True, "suggestion": "评估+清洗模块异常，原文入库", "error": str(e)}
    from rag_knowledge.kb_service import ingest_text
    r = ingest_text(username, title, raw, "upload", account_id=aid)
    if not r.get("ok"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, r.get("error", "入库失败"))
    r["evaluation"] = verdict
    r["cleaned_preview"] = cleaned[:800] if needs_clean else ""
    r["needs_clean"] = needs_clean
    return r


@app.delete("/api/kb/docs/{source_id}", summary="删除库内文档")
async def kb_delete_doc(source_id: str, token: str = Query(...)):
    sess = get_session(token)
    username, aid = sess["username"], sess["account_id"]
    from rag_knowledge.kb_service import delete_doc
    if not delete_doc(username, source_id, account_id=aid):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "文档不存在")
    return {"ok": True}


@app.post("/api/kb/query", summary="知识库检索测试（fast/full）")
async def kb_query(token: str = Query(...), req: dict = Body(...)):
    sess = get_session(token)
    username, aid = sess["username"], sess["account_id"]
    question = (req.get("question") or "").strip()
    mode = req.get("mode") or "fast"
    if not question:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "问题为空")
    from rag_knowledge.kb_service import query, kb_exists
    if not kb_exists(username, aid):
        return {"hits": [], "empty": True}
    hits = query(username, question, mode=mode, account_id=aid)
    return {"hits": hits, "empty": False}


# ----------------------------- WebSocket 实时进度 -----------------------------
@app.websocket("/ws/{thread_id}")
async def ws_progress(websocket: WebSocket, thread_id: str):
    await manager.connect(websocket, thread_id)
    try:
        # 前端连上后第一条消息通常是 ping / 或本轮问题的占位；
        # 这里仅保持连接并把 thread_id 注入 context（供 monitor 定向推送）。
        # 真正的问答通过 /api/chat 触发，进度会按 thread_id 自动推到本连接。
        set_thread_context(thread_id)  # 主协程侧也置一下，确保 get_thread_context 有值
        while True:
            # 前端可发送 {"type":"ping"} 维持；服务端主动推 monitor 事件。
            data = await websocket.receive_text()
            # 收到文本就原样回个 ack（可选）。不做业务处理。
            await websocket.send_json({"type": "ack", "echo": data})
    except WebSocketDisconnect:
        manager.disconnect(websocket, thread_id)
    except Exception:
        try:
            manager.disconnect(websocket, thread_id)
        except Exception:
            pass


# ----------------------------- 公司侧数据（M3） -----------------------------
@app.get("/api/competitors", summary="关注竞品清单（当前账号，公司工作台左栏）")
async def api_competitors(token: str = Query(...)):
    items = me.competitors_list(token)
    # mapped_product_ids 是 JSON 文本，转成数组方便前端使用
    for it in items:
        try:
            it["mapped_product_ids"] = json.loads(it["mapped_product_ids"]) if it["mapped_product_ids"] else []
        except Exception:
            it["mapped_product_ids"] = []
        it["is_competitor"] = bool(it["is_competitor"])
    return {"items": items}


# ----------------------------- 导出（M3） -----------------------------
OUTPUT_DIR = PROJECT_ROOT / "output"


@app.post("/api/export", summary="把回答导出为 Markdown/PDF 文件，返回下载信息")
async def api_export(
    token: str = Query(...),
    title: str = Query("情报简报", description="报告标题"),
    fmt: str = Query("both", pattern="^(md|pdf|both)$", description="导出格式"),
    content: str = Body(..., embed=True, description="要导出的回答正文(Markdown)"),
):
    sess = get_session(token)
    # 用户级输出目录：output/{username}/，按用户隔离
    user_dir = OUTPUT_DIR / sess["username"]
    user_dir.mkdir(parents=True, exist_ok=True)
    ts = time.strftime("%Y%m%d_%H%M%S")
    safe_title = re.sub(r'[\\/:*?"<>|]', "_", title)[:40] or "report"
    md_path = user_dir / f"{safe_title}_{ts}.md"

    md_path.write_text(content, encoding="utf-8")

    pdf_name = None
    if fmt in ("pdf", "both"):
        from utils.word_converter import convert_md_to_pdf_via_word
        pdf_path = md_path.with_suffix(".pdf")
        result = convert_md_to_pdf_via_word(md_path.resolve(), pdf_path.resolve())
        if not pdf_path.exists():
            raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR,
                                f"PDF 转换失败：{result}")
        pdf_name = pdf_path.name

    return {
        "md_file": md_path.name,
        "pdf_file": pdf_name,
        "download_md": f"/api/download?token={token}&name={md_path.name}",
        **({"download_pdf": f"/api/download?token={token}&name={pdf_name}"} if pdf_name else {}),
    }


@app.get("/api/download", summary="下载 output/ 下已生成的报告文件（仅限本用户目录）")
async def api_download(token: str = Query(...), name: str = Query(...)):
    sess = get_session(token)
    # 安全校验：只允许纯文件名（防目录穿越），且必须在当前用户 output 目录内
    if "/" in name or chr(92) in name or ".." in name:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "非法文件名")
    user_root = (OUTPUT_DIR / sess["username"]).resolve()
    f = (user_root / name).resolve()
    if user_root not in f.parents:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "非法路径")
    if not f.exists():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "文件不存在或已被清理")
    media = "application/pdf" if f.suffix.lower() == ".pdf" else "text/markdown; charset=utf-8"
    return FileResponse(f, filename=name, media_type=media)


# ----------------------------- 用户数据自主管理（前置 /api/me/*） -----------------------------
@app.get("/api/me/overview", summary="左栏总览：一次拉全本账号数据 + 完成度")
async def me_overview(token: str = Query(...)):
    return me.overview(token)

@app.get("/api/me/profile", summary="公司信息")
async def me_profile_get(token: str = Query(...)):
    return me.profile_get(token)

@app.post("/api/me/profile", summary="填写/更新公司信息")
async def me_profile_upsert(req: me.ProfileReq, token: str = Query(...)):
    return me.profile_upsert(req, token)

@app.get("/api/me/products", summary="我司产品列表（公司）")
async def me_cproducts_list(token: str = Query(...)):
    return {"items": me.cproducts_list(token)}

@app.post("/api/me/products", summary="新增我司产品")
async def me_cproducts_add(req: me.CompanyProductReq, token: str = Query(...)):
    return me.cproducts_add(req, token)

@app.put("/api/me/products/{pid}", summary="修改我司产品")
async def me_cproducts_update(pid: int, req: me.CompanyProductReq, token: str = Query(...)):
    return me.cproducts_update(pid, req, token)

@app.delete("/api/me/products/{pid}", summary="删除我司产品")
async def me_cproducts_delete(pid: int, token: str = Query(...)):
    return me.cproducts_delete(pid, token)

@app.get("/api/me/competitors", summary="我的竞品列表（含 CRUD 元数据）")
async def me_competitors_list(token: str = Query(...)):
    return {"items": me.competitors_list(token)}

@app.post("/api/me/competitors/batch", summary="批量添加竞品（每行一个名）")
async def me_competitors_batch(req: me.BatchReq, token: str = Query(...)):
    return me.competitors_batch_add(req, token)

# ----------------------------- v3.0 M1：工具来源（API 适配器的配置面） -----------------------------
@app.get("/api/tools/sources", summary="工具来源列表（API；密钥掩码返回）")
async def tools_sources(token: str = Query(...)):
    return ts.sources_list(token)


@app.post("/api/tools/source/save", summary="保存/更新一个 API 来源（配置导向，无厂商名）")
async def tools_source_save(req: ts.ApiSourceReq, token: str = Query(...)):
    return ts.source_save(req, token)


@app.delete("/api/tools/source/{sid}", summary="删除来源（连带其能力）")
async def tools_source_delete(sid: int, token: str = Query(...)):
    return ts.source_delete(sid, token)


@app.post("/api/tools/source/{sid}/test", summary="来源体检（校验 + 可选可达性探测）")
async def tools_source_test(sid: int, token: str = Query(...), probe: bool = Query(False)):
    return ts.source_test(sid, token, probe=probe)


@app.get("/api/tools/source/{sid}/candidates", summary="该来源的能力（含未确认候选）")
async def tools_source_candidates(sid: int, token: str = Query(...)):
    return ts.candidates_list(sid, token)


@app.post("/api/tools/discover", summary="粘贴 OpenAPI → 解析候选（未确认不入池）")
async def tools_discover(req: ts.DiscoverReq, token: str = Query(...)):
    return ts.discover(req, token)


@app.post("/api/tools/confirm", summary="人工确认候选 → 入池可被 invoke_tool 调用")
async def tools_confirm(req: ts.ConfirmReq, token: str = Query(...)):
    return ts.confirm(req, token)


@app.post("/api/tools/sources/cap_abilities",
          summary="M5c-2'：保存工具级描述（用户手写 / 一键导入 AI 草稿）")
async def tools_cap_abilities(req: ts.CapAbilitiesReq, token: str = Query(...)):
    """`items` 里 `text` 传空串 = 清除手写、回到自动摘要。写的是 `abilities_user` 列，
    重新「发现/导入工具」不会冲掉用户写的内容。"""
    return ts.cap_abilities_save(req, token)


@app.post("/api/tools/sources/ability_draft",
          summary="M5c-2'：按 README/文档 + 工具清单，为每个工具预写中文描述草稿（不落库）")
async def tools_ability_draft(req: dict = Body(...), token: str = Query(...)):
    """证据：README/文档（http 网址或**本地文件路径**）+ 工具清单与参数 schema + 来源级描述。

    README 里常有的能力差异（按书名 vs 按作者）只有它写得出来 —— 单靠工具名模型只能猜。
    返回 `items`（每个工具一段，前端可一键导入并自由编辑）+ `evidence` + `warnings`。
    """
    sess = get_session(token)
    return cust.capability_ability_generate(
        (req.get("source") or "api").strip().lower() or "api",
        (req.get("slug") or "").strip(),
        account_id=sess.get("account_id"),
        refresh_docs=bool(req.get("refresh_docs")))


@app.post("/api/me/collection/batch", summary="批量添加收藏（每行 商品名,品牌,参考价）")
async def me_collection_batch(req: me.BatchReq, token: str = Query(...)):
    return me.collection_batch_add(req, token)

@app.post("/api/me/competitors", summary="添加竞品")
async def me_competitors_add(req: me.CompetitorReq, token: str = Query(...)):
    return me.competitors_add(req, token)

@app.put("/api/me/competitors-meta/{cid}", summary="编辑竞品详情")
async def me_competitors_update(cid: int, req: me.CompetitorReq, token: str = Query(...)):
    return me.competitors_update(cid, req, token)

@app.delete("/api/me/competitors/{cid}", summary="删除竞品")
async def me_competitors_delete(cid: int, token: str = Query(...)):
    return me.competitors_delete(cid, token)

@app.get("/api/me/interests", summary="兴趣标签列表（个人）")
async def me_interests_list(token: str = Query(...)):
    return {"items": me.interests_list(token)}

@app.post("/api/me/interests", summary="添加兴趣标签")
async def me_interests_add(req: me.InterestReq, token: str = Query(...)):
    return me.interests_add(req, token)

@app.delete("/api/me/interests/{iid}", summary="删除兴趣标签")
async def me_interests_delete(iid: int, token: str = Query(...)):
    return me.interests_delete(iid, token)

@app.put("/api/me/interests/{iid}", summary="编辑兴趣（含详细描述/关键词）")
async def me_interests_update(iid: int, req: me.InterestReq, token: str = Query(...)):
    return me.interests_update(iid, req, token)

@app.put("/api/me/watchlist/{wid}", summary="编辑关注项")
async def me_watchlist_update(wid: int, req: me.WatchlistReq, token: str = Query(...)):
    return me.watchlist_update(wid, req, token)

@app.put("/api/me/collection/{pid}", summary="编辑收藏商品")
async def me_pproducts_update(pid: int, req: me.PersonalProductReq, token: str = Query(...)):
    return me.pproducts_update(pid, req, token)

@app.get("/api/me/watchlist", summary="关注品牌商品列表（个人）")
async def me_watchlist_list(token: str = Query(...)):
    return {"items": me.watchlist_list(token)}

@app.post("/api/me/watchlist", summary="添加关注")
async def me_watchlist_add(req: me.WatchlistReq, token: str = Query(...)):
    return me.watchlist_add(req, token)

@app.delete("/api/me/watchlist/{wid}", summary="删除关注")
async def me_watchlist_delete(wid: int, token: str = Query(...)):
    return me.watchlist_delete(wid, token)

@app.get("/api/me/collection", summary="个人收藏商品列表")
async def me_pproducts_list(token: str = Query(...)):
    return {"items": me.pproducts_list(token)}

@app.post("/api/me/collection", summary="添加个人收藏商品")
async def me_pproducts_add(req: me.PersonalProductReq, token: str = Query(...)):
    return me.pproducts_add(req, token)

@app.delete("/api/me/collection/{pid}", summary="删除个人收藏商品")
async def me_pproducts_delete(pid: int, token: str = Query(...)):
    return me.pproducts_delete(pid, token)


# ----------------------------- Digest 定期推送（M4） -----------------------------
@app.get("/api/digest/subs", summary="我的订阅列表（无则按角色自动建一份默认订阅）")
async def digest_subs_get(token: str = Query(...)):
    sess = get_session(token)
    aid = sess["account_id"]
    from tools.schema_personal import get_personal_conn, ensure_tables
    ensure_tables()
    conn = get_personal_conn()
    try:
        rows = conn.execute("SELECT * FROM digest_subs WHERE owner_id=?", (aid,)).fetchall()
        if not rows:
            # 首次：按角色建一份默认订阅（公司←竞品清单；个人←兴趣标签）
            if sess["role"] == "company":
                kws = [r[0] for r in conn.execute(
                    "SELECT comp_name FROM company_competitors WHERE owner_id=?", (aid,)).fetchall()] or ['行业动态']
                scope, name = 'company', '竞品周报'
            else:
                kws = [r[0] for r in conn.execute(
                    "SELECT interest_tag FROM interests WHERE owner_id=?", (aid,)).fetchall()] or ['数码新品']
                scope, name = 'personal', '选购快讯'
            conn.execute(
                "INSERT INTO digest_subs (owner_id, scope, name, keywords, schedule) VALUES (?,?,?,?,?)",
                (aid, scope, name, json.dumps(kws, ensure_ascii=False), 'weekly'))
            conn.commit()
            rows = conn.execute("SELECT * FROM digest_subs WHERE owner_id=?", (aid,)).fetchall()
        subs = [{k: r[k] for k in r.keys()} for r in rows]
        return {"subs": subs}
    finally:
        conn.close()


@app.post("/api/digest/subs", summary="新增一条订阅")
async def digest_subs_post(token: str = Query(...),
                           name: str = Body(...),
                           scope: str = Body("personal", pattern='^(company|personal)$'),
                           keywords: str = Body("[]", description="JSON数组文本"),
                           schedule: str = Body("weekly", pattern='^(daily|weekly)$'),
                           lang: str = Body("zh", pattern='^(zh|en|both)$'),
                           enabled: bool = Body(True)):
    sess = get_session(token)
    aid = sess["account_id"]
    try:
        arr = json.loads(keywords)
        assert isinstance(arr, list) and all(isinstance(x, str) for x in arr)
    except Exception:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "keywords 必须是字符串数组的 JSON 文本")
    from tools.schema_personal import get_personal_conn, ensure_tables
    ensure_tables()
    conn = get_personal_conn()
    try:
        cur = conn.execute(
            "INSERT INTO digest_subs (owner_id, scope, name, keywords, schedule, lang, enabled) "
            "VALUES (?,?,?,?,?,?,?)",
            (aid, scope, name, json.dumps(arr[:8], ensure_ascii=False), schedule, lang, int(enabled)))
        conn.commit()
        row = conn.execute("SELECT * FROM digest_subs WHERE id=?", (cur.lastrowid,)).fetchone()
        return {"sub": {k: row[k] for k in row.keys()}}
    finally:
        conn.close()


@app.put("/api/digest/subs/{sub_id}", summary="更新某条订阅（关键词/周期/开关/名称）")
async def digest_subs_put(sub_id: int, token: str = Query(...),
                          name: Optional[str] = Body(None),
                          keywords: Optional[str] = Body(None, description="JSON数组文本，如 [\"飞书\",\"钉钉\"]"),
                          schedule: Optional[str] = Body(None, pattern='^(daily|weekly)$'),
                          lang: Optional[str] = Body(None, pattern='^(zh|en|both)$'),
                          enabled: Optional[bool] = Body(None)):
    sess = get_session(token)
    aid = sess["account_id"]
    from tools.schema_personal import get_personal_conn, ensure_tables
    ensure_tables()
    conn = get_personal_conn()
    try:
        # 校验归属
        own = conn.execute("SELECT id FROM digest_subs WHERE id=? AND owner_id=?", (sub_id, aid)).fetchone()
        if not own:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "订阅不存在")
        sets, vals = [], []
        if name is not None: sets.append("name=?"); vals.append(name)
        if keywords is not None:
            try:
                arr = json.loads(keywords)
                assert isinstance(arr, list) and all(isinstance(x, str) for x in arr)
            except Exception:
                raise HTTPException(status.HTTP_400_BAD_REQUEST, "keywords 必须是字符串数组的 JSON 文本")
            sets.append("keywords=?"); vals.append(json.dumps(arr[:8], ensure_ascii=False))  # 纪律：≤8
        if schedule is not None: sets.append("schedule=?"); vals.append(schedule)
        if lang is not None: sets.append("lang=?"); vals.append(lang)
        if enabled is not None: sets.append("enabled=?"); vals.append(int(enabled))
        if sets:
            vals.append(sub_id); vals.append(aid)
            conn.execute(f"UPDATE digest_subs SET {', '.join(sets)} WHERE id=? AND owner_id=?", vals)
            conn.commit()
        row = conn.execute("SELECT * FROM digest_subs WHERE id=?", (sub_id,)).fetchone()
        return {"sub": {k: row[k] for k in row.keys()} if row else None}
    finally:
        conn.close()


@app.delete("/api/digest/subs/{sub_id}", summary="删除一条订阅")
async def digest_subs_delete(sub_id: int, token: str = Query(...)):
    sess = get_session(token)
    aid = sess["account_id"]
    from tools.schema_personal import get_personal_conn, ensure_tables
    ensure_tables()
    conn = get_personal_conn()
    try:
        conn.execute("DELETE FROM digest_subs WHERE id=? AND owner_id=?", (sub_id, aid))
        conn.commit()
        return {"ok": True}
    finally:
        conn.close()



class DigestRunReq(BaseModel):
    sub_ids: list[int] = []  # 空=所有启用

@app.post("/api/digest/run", summary="手动触发（批量）：传 sub_ids 列表，逐个生成")
async def digest_run(token: str = Query(...),
                     req: DigestRunReq = Body(...)):
    sess = get_session(token)
    aid = sess["account_id"]
    from tools.schema_personal import get_personal_conn, ensure_tables
    ensure_tables()
    conn = get_personal_conn()
    try:
        if req.sub_ids:                       # 校验归属
            qmarks = ",".join("?" * len(req.sub_ids))
            rows = conn.execute(
                f"SELECT id FROM digest_subs WHERE id IN ({qmarks}) AND owner_id=? AND enabled=1",
                list(req.sub_ids) + [aid]).fetchall()
        else:
            rows = conn.execute(
                "SELECT id FROM digest_subs WHERE owner_id=? AND enabled=1", (aid,)).fetchall()
        ids = [r[0] for r in rows]
        if not ids:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "没有可生成的启用订阅")
    finally:
        conn.close()
    from agent.digest_engine import run_digest
    results = []
    for sid in ids:
        res = await asyncio.to_thread(run_digest, sid, aid)  # 逐条串行（避免并发压垮 LLM）
        results.append({"sub_id": sid, **res})
    ok = sum(1 for r in results if r.get("ok"))
    return {"ok": ok == len(results), "generated": ok, "total": len(results), "results": results}


@app.get("/api/reports", summary="我的报告列表（新→旧）")
async def reports_list(token: str = Query(...)):
    sess = get_session(token)
    from tools.schema_personal import get_personal_conn, ensure_tables
    ensure_tables()
    conn = get_personal_conn()
    try:
        rows = conn.execute(
            "SELECT id, title, md_path, pdf_path, item_count, status, coverage_note, created_at "
            "FROM digest_reports WHERE owner_id=? ORDER BY id DESC LIMIT 50",
            (sess["account_id"],)).fetchall()
        items = []
        for r in rows:
            it = {k: r[i] for i, k in enumerate(
                ["id","title","md_path","pdf_path","item_count","status","coverage_note","created_at"])}
            # 提供下载链接（复用 /api/download，传文件名即可——同目录校验）
            if it["md_path"]:
                it["download_md"] = "/api/download?token=" + token + "&name=" + os.path.basename(it["md_path"])
            if it["pdf_path"]:
                it["download_pdf"] = "/api/download?token=" + token + "&name=" + os.path.basename(it["pdf_path"])
            items.append(it)
        return {"items": items}
    finally:
        conn.close()

# ----------------------------- 定制助手 API（背景图/模型/人格）-----------------------------
from fastapi import UploadFile as _UpFile, File as _File, Body as _Body

@app.post("/api/bg/upload", summary="上传背景图（存 pic/{username}/）")
async def bg_upload(token: str = Query(...), file: _UpFile = _File(...)):
    return cust.bg_upload(token, file)

@app.get("/api/bg/list", summary="我的背景图列表")
async def bg_list(token: str = Query(...)):
    return cust.bg_list(token)

@app.delete("/api/bg/{name}", summary="删除一张背景图")
async def bg_delete(name: str, token: str = Query(...)):
    return cust.bg_delete(token, name)

@app.get("/api/llm/providers", summary="我的模型配置列表")
async def llm_providers_list(token: str = Query(...)):
    return cust.llm_providers_list(token)

@app.post("/api/llm/providers", summary="新增模型配置")
async def llm_provider_add(req: cust.LlmProviderReq, token: str = Query(...)):
    return cust.llm_provider_add(req, token)

@app.put("/api/llm/providers/{pid}", summary="更新模型配置")
async def llm_provider_update(pid: int, req: cust.LlmProviderReq, token: str = Query(...)):
    return cust.llm_provider_update(pid, req, token)

@app.delete("/api/llm/providers/{pid}", summary="删除模型配置")
async def llm_provider_delete(pid: int, token: str = Query(...)):
    return cust.llm_provider_delete(pid, token)

@app.post("/api/llm/providers/{pid}/activate", summary="启用某模型配置")
async def llm_provider_activate(pid: int, token: str = Query(...)):
    return cust.llm_provider_set_active(pid, token)

@app.get("/api/soul", summary="读取我的助手人格 SOUL.md")
async def soul_get(token: str = Query(...)):
    return cust.soul_get(token)

@app.post("/api/soul", summary="保存我的助手人格 SOUL.md")
async def soul_save(req: cust.SoulReq, token: str = Query(...)):
    return cust.soul_save(token, req)

@app.post("/api/soul/generate", summary="人格撰写 AI：按需求生成人格提示词")
async def soul_generate(req: cust.SoulReq, token: str = Query(...)):
    # SoulReq.content 复用为「需求描述」（如 活泼女仆）；token 仅校验登录
    return cust.soul_generate(req.content)

# ----------------------------- 多人格集（SOUL/ 子目录） -----------------------------
@app.get("/api/soul/list", summary="列出该账号所有人格（默认+多人格），标记激活")
async def soul_list(token: str = Query(...)):
    return cust.soul_list(token)

@app.post("/api/soul/create", summary="新建人格文件 SOUL/{name}.md")
async def soul_create(req: cust.SoulNameReq, token: str = Query(...)):
    return cust.soul_create(token, req)

@app.post("/api/soul/delete", summary="删除人格文件（默认人格除外）")
async def soul_delete(req: cust.SoulNameReq, token: str = Query(...)):
    return cust.soul_delete(token, req.name)

@app.post("/api/soul/active", summary="激活某个人格文件")
async def soul_set_active(req: cust.SoulActiveReq, token: str = Query(...)):
    return cust.soul_set_active(token, req.file)

@app.get("/api/soul/named", summary="读取某个人格内容（name=default 读 SOUL.md）")
async def soul_get_named(name: str = Query(...), token: str = Query(...)):
    return cust.soul_get_named(token, name)

@app.post("/api/soul/named", summary="保存某个人格内容（name=default 写 SOUL.md）")
async def soul_save_named(req: cust.SoulNameReq, token: str = Query(...)):
    return cust.soul_save_named(token, req)

# ----------------------------- 记忆画像 MEMORY.md -----------------------------
@app.get("/api/memory", summary="读取我的记忆画像 MEMORY.md")
async def memory_get(token: str = Query(...)):
    return cust.memory_get(token)

@app.post("/api/memory", summary="保存我的记忆画像（用户手动编辑）")
async def memory_save(req: cust.MemoryReq, token: str = Query(...)):
    return cust.memory_save(token, req)

@app.post("/api/memory/init", summary="一次性初始化用户画像（AI 根据系统已有信息生成）")
async def memory_init(token: str = Query(...)):
    return cust.memory_initialize(token)


@app.post("/api/memory/suggest", summary="M3-3：让 AI 给画像提修订建议（★ 只出建议，不落盘）")
async def memory_suggest(token: str = Query(...)):
    return cust.memory_suggest(token)


@app.post("/api/memory/apply", summary="M3-4：应用勾选的画像建议（就地更新 · 保人工行 · 改前快照）")
async def memory_apply(req: cust.MemoryApplyReq, token: str = Query(...)):
    return cust.memory_apply(token, req)


@app.post("/api/memory/import", summary="M3-10：一键导入整份 AI 建议（需 confirm · 默认保人工行）")
async def memory_import(req: cust.MemoryImportReq, token: str = Query(...)):
    return cust.memory_import(token, req)


@app.get("/api/price/history", summary="Q6④：某个对象的全部台账历史价（前端可展开列表）")
async def price_history(item_type: str = Query(...), item_id: int = Query(...),
                        token: str = Query(...), limit: int = Query(50)):
    from api.price_api import history as _h
    return _h(token, item_type, item_id, limit)


@app.post("/api/price/register", summary="Q6④：设为登记价（只改配置，不写台账、不触发提醒）")
async def price_register(req: price_ack.RegisterReq, token: str = Query(...)):
    from api.price_api import set_registered as _s
    return _s(token, req)


@app.post("/api/price/history/delete", summary="删除一条台账记录（★ 只能删自己账号的；删完重算最近价）")
async def price_history_delete(req: price_ack.DelHistReq, token: str = Query(...)):
    from api.price_api import delete_history as _d
    return _d(token, req)


@app.get("/api/price/source", summary="M5-8：价格源配置列表（阶段 2 预留，配置导向）")
async def price_sources(token: str = Query(...)):
    from api.price_api import sources as _s
    return _s(token)


@app.post("/api/price/source", summary="M5-8：新增/更新一个自配价格源")
async def price_source_save(req: price_ack.SourceReq, token: str = Query(...)):
    from api.price_api import save_source as _s
    return _s(token, req)


@app.post("/api/price/check", summary="M5-8：立即检查一个商品的价（走自配源；本版为桩）")
async def price_check(req: price_ack.CheckReq, token: str = Query(...)):
    from api.price_api import run_check as _r
    return _r(token, req)


@app.get("/api/price/summary", summary="M5-6：价格汇总（监控商品 / 竞品价差 / 资讯价）")
async def price_summary(token: str = Query(...)):
    from api.price_api import summary as _s
    return _s(token)


@app.post("/api/price/record", summary="M5-6：前端「更新价格」（与对话记价同一事实源）")
async def price_record(req: price_ack.RecordReq, token: str = Query(...)):
    from api.price_api import record as _r
    return _r(token, req)


@app.get("/api/price/alerts", summary="M5-4：待通知的价格提醒（外壳/页面取走 → 提示 → 回执）")
async def price_alerts(token: str = Query(...)):
    from api.price_api import alerts as _a
    return _a(token)


@app.post("/api/price/alerts/ack", summary="M5-4：提醒回执（避免重复弹）")
async def price_alerts_ack(req: price_ack.AckReq, token: str = Query(...)):
    from api.price_api import ack as _ack
    return _ack(token, req)


@app.get("/api/failures", summary="M6c-6：失败分类汇总（按码计数 + 最近原文）")
async def failures(token: str = Query(...), days: int = Query(7)):
    from api.failure_api import failures as _f
    return _f(token, days)


@app.get("/api/cost/summary", summary="M6c-5：成本汇总（按 provider·model 与按天；金额为估算值）")
async def cost_summary(token: str = Query(...), days: int = Query(30)):
    from api.cost_api import cost_summary as _s
    return _s(token, days)


@app.post("/api/cost/recalc", summary="M6c-5：按当前单价重算（只影响指定时间窗，写 recalculated_at）")
async def cost_recalc(req: cost_api.RecalcReq, token: str = Query(...)):
    from api.cost_api import recalc as _r
    return _r(token, req.days)


@app.get("/api/usage", summary="M4-4：本会话/本轮的用量事件（外部检索次数 + 分类）")
async def usage(token: str = Query(...), thread_id: str = Query("")):
    from api.usage_api import usage_report
    return usage_report(token, thread_id)


@app.get("/api/memory/snapshots", summary="M3-9：最近 3 份历史画像（新的在前）+ 当前元信息")
async def memory_snapshots(token: str = Query(...)):
    return cust.memory_snapshots(token)


@app.post("/api/memory/restore", summary="M3-9：一键还原到某份历史画像（还原前先快照当前版本）")
async def memory_restore(req: cust.MemoryRestoreReq, token: str = Query(...)):
    return cust.memory_restore(token, req)

# ----------------------------- 技能 SKILL.md（M4a） -----------------------------
@app.get("/api/skills", summary="列出我的技能（含简介/字数）")
async def skills_list(token: str = Query(...)):
    return cust.skills_list(token)


@app.get("/api/skills/get", summary="读取单个技能正文")
async def skill_get(name: str = Query(...), token: str = Query(...)):
    return cust.skill_get(token, name)


@app.post("/api/skills/create", summary="新建技能（重名 409）")
async def skill_create(req: cust.SkillReq, token: str = Query(...)):
    return cust.skill_create(token, req)


@app.post("/api/skills/save", summary="保存技能正文（不存在则创建）")
async def skill_save(req: cust.SkillReq, token: str = Query(...)):
    return cust.skill_save(token, req)


@app.post("/api/skills/delete", summary="删除技能")
async def skill_delete(req: cust.SkillReq, token: str = Query(...)):
    return cust.skill_delete(token, req.name)

# ----------------------------- M4b：CLI 面板（白名单沙箱执行） -----------------------------
@app.get("/api/shell/allowed", summary="CLI 面板：白名单命令 + 沙箱目录")
async def shell_allowed(token: str = Query(...)):
    """返回白名单命令清单（面板底部命令栏 + Agent 提示共用同一份定义）。"""
    sess = get_session(token)
    return shell_runtime.allowed_commands(sess["username"], sess.get("account_id"))


@app.post("/api/shell/exec", summary="CLI 面板：执行一条白名单命令（沙箱内）")
async def shell_exec(req: dict = Body(...), token: str = Query(...)):
    """在 `data/sandbox/{username}/` 里执行白名单命令。

    安全拒绝（路径逃逸 / 非白名单命令 / curl 写了文件…）也返回 HTTP 200，
    靠 `ok=false` + `error` 表达——面板按行渲染，不当成接口异常。
    实际执行走 `asyncio.to_thread`，避免 ffmpeg 这类长命令阻塞 uvicorn 事件循环。
    """
    sess = get_session(token)
    line = (req.get("command") or "").strip()
    args = req.get("args")
    timeout = req.get("timeout")
    if not line and not args:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "命令不能为空")
    if len(line) > 2000:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "命令过长（>2000 字符）")
    if args is not None and not isinstance(args, list):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "args 必须是字符串数组")
    acc = sess.get("account_id")
    if args:
        return await asyncio.to_thread(shell_runtime.execute, line, [str(a) for a in args],
                                       timeout, sess["username"], acc)
    return await asyncio.to_thread(shell_runtime.run_line, line, sess["username"], timeout, acc)


# ----------------------------- M4c 自定义 CLI 接入配置 -----------------------------
@app.get("/api/cli/list", summary="我的 CLI 配置 + 预填示例 + 本机检测")
async def cli_list(token: str = Query(...)):
    """**配置导向**：系统不预设厂商。返回该账号自己配置的 CLI（含只读清单与本机检测）+ 预填示例。

    预填示例（飞书/企微/钉钉/Google）只是表单模板，不进白名单、不参与任何判定。
    本机检测只看可执行文件与 npm 全局目录，**不执行任何第三方程序**。
    """
    sess = get_session(token)
    acc = sess.get("account_id")
    cli_reg.migrate_legacy(acc)          # 旧版内置厂商登记 → 搬进 user_clis（幂等，一次性）
    items = cli_reg.list_clis(acc)
    return {
        "ok": True,
        "states": cli_reg.STATES,
        "items": items,
        "templates": cli_reg.templates(),
        "summary": {
            "total": len(items),
            "authed": sum(1 for i in items if i["state"] == "authed"),
            "installed": sum(1 for i in items if i["state"] == "installed"),
            "detected_local": sum(1 for i in items if i["detected"]["found"]),
            "agent_live": sum(1 for i in items if i["live"]),
            "with_rules": sum(1 for i in items if i["rules"]),
        },
    }


@app.post("/api/cli/ability_draft", summary="M5：按证据（官方文档 + --help 真值）生成「能力描述」草稿")
async def cli_ability_draft(req: dict = Body(...), token: str = Query(...)):
    """生成能力描述草稿（用户语言：触发关键词 + 用户说法→命令映射）。

    **只返回草稿，不写库**——用户在表单里确认/修改后，走 /api/cli/save 保存。
    证据分层（2026-09-24）：① 文档地址 `docs`（README）当**语义依据**；② 逐条 `<bin> <cmd> --help`
    当**校验依据**（形态分类 + 生成后审计）；③ 都没有就用 `help_text`（用户粘贴）或保守化。
    描述里的命令必须来自该 CLI 的只读清单（清单为空则拒绝生成，先填清单）。
    返回值额外带 `evidence`（读了哪份文档、覆盖几条命令）与 `warnings`（审计发现的可疑映射）。
    """
    sess = get_session(token)
    return cust.cli_ability_generate(req.get("name") or "", req.get("bin") or "",
                                     req.get("readonly") or "",
                                     docs=req.get("docs") or "",
                                     help_text=req.get("help_text") or "",
                                     username=sess.get("username"),
                                     account_id=sess.get("account_id"),
                                     refresh_docs=bool(req.get("refresh_docs")))


@app.post("/api/cli/save", summary="自定义 CLI：新增 / 修改配置")
async def cli_save(req: dict = Body(...), token: str = Query(...)):
    """新增（`id` 留空）或修改一条 CLI 配置。

    必填：名称、可执行名（纯名字，不含路径）；只读命令清单可留空——留空 = 不放行 Agent 代跑。
    安装 / 认证命令只做展示，仍由你在自己的终端执行（本模块不代跑 npm / OAuth）。
    """
    sess = get_session(token)
    try:
        item = cli_reg.save_cli(sess.get("account_id"), req or {})
    except cli_reg.CliConfigError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e))
    return {"ok": True, "item": item}


@app.post("/api/cli/status", summary="自定义 CLI：登记 / 撤销接入状态（含备注）")
async def cli_status(req: dict = Body(...), token: str = Query(...)):
    """状态：none（未接入）/ installed（已装未认证）/ authed（已接入已授权）。"""
    sess = get_session(token)
    try:
        item = cli_reg.set_state(sess.get("account_id"), req.get("id"),
                                 (req.get("state") or "none").strip() or "none", req.get("note"))
    except cli_reg.CliConfigError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e))
    return {"ok": True, "item": item}


@app.post("/api/cli/delete", summary="自定义 CLI：删除配置（同时回收 Agent 代跑权限）")
async def cli_delete(req: dict = Body(...), token: str = Query(...)):
    sess = get_session(token)
    if req.get("id") in (None, ""):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "缺少 id")
    ok = cli_reg.delete_cli(sess.get("account_id"), req.get("id"))
    if not ok:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "该 CLI 不存在（或不属于当前账号）")
    return {"ok": True, "deleted": True}

WX_CACHE_FILE = PROJECT_ROOT / "data" / "weather_cache.json"


def _wx_load_disk():
    """启动时把上次成功的天气灌进内存缓存 → 重启后首屏也是秒出（外网首次请求实测 ~16s）。"""
    try:
        if WX_CACHE_FILE.is_file():
            data = json.loads(WX_CACHE_FILE.read_text(encoding="utf-8"))
            for city, item in (data or {}).items():
                if isinstance(item, dict) and item.get("payload"):
                    _wx_cache[city] = (float(item.get("ts") or 0), item["payload"])
    except Exception as e:  # noqa: BLE001 - 缓存坏了不影响功能
        print("[天气] 磁盘缓存读取失败（忽略）: %s" % e)


def _wx_save_disk():
    try:
        WX_CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        WX_CACHE_FILE.write_text(
            json.dumps({c: {"ts": ts, "payload": pl} for c, (ts, pl) in _wx_cache.items()},
                       ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass


def _wx_warm():
    """启动后台预热默认城市（不阻塞启动；失败只打印一行）。"""
    try:
        city = DEFAULT_CITY
        hit = _wx_cache.get(city)
        if hit and time.time() - hit[0] < WEATHER_TTL:
            return
        data = _wx_fetch(city)
        _wx_cache[city] = (time.time(), data)
        _wx_save_disk()
        print("[天气] 预热完成：%s %s°C %s" % (data.get("city"), data["now"].get("temp"), data["now"].get("text")))
    except Exception as e:  # noqa: BLE001
        print("[天气] 预热失败（不影响启动）: %s" % type(e).__name__)



# ----------------------------- M4 收尾：工作台天气（Open-Meteo，免 Key，城市级） -----------------------------
# 数据源：Open-Meteo（geocoding + forecast），完全免费、无需 API Key，支持城市级地理编码。
# 策略：进程内缓存（城市坐标永久、天气 10 分钟），外网失败时降级直连重试（本机系统代理可能挡外部域名）。
WMO_TEXT = {
    0: ("晴", "☀️"), 1: ("晴间多云", "🌤️"), 2: ("多云", "⛅"), 3: ("阴", "☁️"),
    45: ("雾", "🌫️"), 48: ("雾凇", "🌫️"),
    51: ("毛毛雨", "🌦️"), 53: ("小雨", "🌦️"), 55: ("中雨", "🌧️"), 56: ("冻毛毛雨", "🌧️"), 57: ("冻雨", "🌧️"),
    61: ("小雨", "🌧️"), 63: ("中雨", "🌧️"), 65: ("大雨", "🌧️"), 66: ("冻雨", "🌧️"), 67: ("强冻雨", "🌧️"),
    71: ("小雪", "🌨️"), 73: ("中雪", "🌨️"), 75: ("大雪", "❄️"), 77: ("雪粒", "🌨️"),
    80: ("阵雨", "🌦️"), 81: ("强阵雨", "🌧️"), 82: ("暴雨", "⛈️"), 85: ("阵雪", "🌨️"), 86: ("强阵雪", "❄️"),
    95: ("雷阵雨", "⛈️"), 96: ("雷阵雨伴冰雹", "⛈️"), 99: ("强雷暴伴冰雹", "⛈️"),
}
WEATHER_TTL = 600          # 天气缓存 10 分钟（Open-Meteo 自身也是 15 分钟粒度）
DEFAULT_CITY = os.getenv("WEATHER_CITY", "成都")
_wx_cache: dict = {}       # {city: (ts, payload)}
_wx_geo: dict = {}         # {city: geo dict}（城市坐标不变，永久缓存）


def _wx_code(code):
    try:
        return WMO_TEXT.get(int(code), ("未知", "🌡️"))
    except (TypeError, ValueError):
        return ("未知", "🌡️")


def _wx_json(url: str, params: dict) -> dict:
    """取 JSON：先走环境（含系统代理），失败再降级直连重试一次。"""
    try:
        r = httpx.get(url, params=params, timeout=12.0)
        r.raise_for_status()
        return r.json()
    except Exception:
        with httpx.Client(trust_env=False, timeout=12.0) as c:
            r = c.get(url, params=params)
            r.raise_for_status()
            return r.json()


def _wx_geocode(city: str) -> dict:
    key = (city or "").strip() or DEFAULT_CITY
    if key in _wx_geo:
        return _wx_geo[key]
    d = _wx_json("https://geocoding-api.open-meteo.com/v1/search",
                 {"name": key, "count": 1, "language": "zh", "format": "json"})
    res = (d or {}).get("results") or []
    if not res:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "没找到城市「%s」，换个写法试试（如：成都 / Chengdu）" % key)
    g = res[0]
    _wx_geo[key] = g
    return g


def _wx_fetch(city: str) -> dict:
    g = _wx_geocode(city)
    fc = _wx_json("https://api.open-meteo.com/v1/forecast", {
        "latitude": g["latitude"], "longitude": g["longitude"],
        "current": "temperature_2m,relative_humidity_2m,apparent_temperature,weather_code,wind_speed_10m,precipitation",
        "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max",
        "timezone": "Asia/Shanghai", "forecast_days": 4,
    })
    cur = (fc or {}).get("current") or {}
    daily = (fc or {}).get("daily") or {}
    text, icon = _wx_code(cur.get("weather_code"))
    days = []
    for i, day in enumerate(daily.get("time") or []):
        t, ic = _wx_code((daily.get("weather_code") or [None])[i])
        days.append({"date": day, "text": t, "icon": ic,
                     "tmax": (daily.get("temperature_2m_max") or [None])[i],
                     "tmin": (daily.get("temperature_2m_min") or [None])[i],
                     "rain": (daily.get("precipitation_probability_max") or [None])[i]})
    label = g.get("name") or city
    if g.get("admin1") and g.get("admin1") != g.get("name"):
        label = "%s·%s" % (g.get("name"), g.get("admin1"))
    return {
        "ok": True, "city": label, "city_input": city,
        "admin1": g.get("admin1"), "country": g.get("country"),
        "lat": g.get("latitude"), "lon": g.get("longitude"),
        "now": {"temp": cur.get("temperature_2m"), "feels": cur.get("apparent_temperature"),
                "humidity": cur.get("relative_humidity_2m"), "wind": cur.get("wind_speed_10m"),
                "rain_now": cur.get("precipitation"), "code": cur.get("weather_code"),
                "text": text, "icon": icon, "time": cur.get("time")},
        "daily": days,
        "source": "open-meteo.com（免 Key）",
        "updated": time.strftime("%Y-%m-%d %H:%M:%S"),
    }


@app.get("/api/weather", summary="工作台天气（Open-Meteo，城市级，含 3 天预报）")
async def weather(token: str = Query(...), city: str = Query(""), force: int = Query(0)):
    """城市级实时天气 + 预报。city 留空取 .env 的 WEATHER_CITY（默认成都）。"""
    get_session(token)
    key = (city or "").strip() or DEFAULT_CITY
    now = time.time()
    hit = _wx_cache.get(key)
    if hit and not force and now - hit[0] < WEATHER_TTL:
        return hit[1]
    try:
        data = await asyncio.to_thread(_wx_fetch, key)
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001 - 外网异常统一转 502，别 500 抛栈
        if hit:      # 有旧数据就先用旧的，比报错强
            return {**hit[1], "stale": True, "warning": "天气刷新失败（%s），显示上次结果" % type(e).__name__}
        raise HTTPException(status.HTTP_502_BAD_GATEWAY,
                            "天气服务不可用：%s（数据源 open-meteo.com，需要外网）" % type(e).__name__)
    _wx_cache[key] = (now, data)
    _wx_save_disk()
    return data



# ----------------------------- M4 收尾：教程指导（只读 md 阅读器 + 内联图片） -----------------------------
# 对齐蜀道「政策文件只读阅读器」的做法：后端只读接口（目录/扩展名/白名单校验 + 防 ../ 穿越），
# 前端手写 md 渲染器（零外部依赖、离线可用）。
TUTORIAL_DIR = PROJECT_ROOT / "docs" / "v2.0" / "演示文档"
TUTORIAL_MD = TUTORIAL_DIR / "演示文档.md"
TUTORIAL_ASSETS = PROJECT_ROOT / "data" / "tutorial_assets"   # 运行时缓存（data/ 已 gitignore）
TUTORIAL_IMG_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".svg"}


def _tutorial_roots() -> list[Path]:
    """图片允许的来源目录（白名单）——文档同目录 + Typora 贴图目录（作者常把图贴到项目外）。"""
    # ★ 统一放 front/tutorial（2026-09-29 用户要求）：教程素材（演示文档引用的截图/svg）现在
    #   也放这里；`data/tutorial_assets/` 只是**运行时缓存**（data/ 已 gitignore），不再当素材源。
    roots = [TUTORIAL_DIR, PROJECT_ROOT / "front" / "tutorial"]
    appdata = os.environ.get("APPDATA")
    if appdata:
        roots.append(Path(appdata) / "Typora" / "typora-user-images")
    return roots


def _tutorial_sync_assets(md_text: str):
    """把文档引用的图片按**文件名**同步到 data/tutorial_assets/，src 改写为 /tutorial-assets/<文件名>。

    安全：只取 `Path(name).name`（挡绝对路径与 `../` 穿越）→ 只在白名单目录里查找 → 只处理图片扩展名；
    对外只暴露**拷贝后的缓存目录**（静态挂载），不直接暴露用户目录。
    找不到的图片整段剔除（避免裂图），并把原始路径回报给前端。
    """
    TUTORIAL_ASSETS.mkdir(parents=True, exist_ok=True)
    images, missing = [], []

    def _sync(name):
        safe = Path(str(name).strip().strip('"').replace("\\", "/")).name
        if not safe or Path(safe).suffix.lower() not in TUTORIAL_IMG_EXTS:
            return None
        dst = TUTORIAL_ASSETS / safe
        for root in _tutorial_roots():
            src = root / safe
            try:
                if src.is_file():
                    if (not dst.is_file()) or src.stat().st_mtime > dst.stat().st_mtime:
                        shutil.copy2(src, dst)
                    return "/tutorial-assets/" + safe
            except OSError:
                continue
        return None

    def _sub(m):
        alt, src = m.group(1), m.group(2)
        url = _sync(src)
        if url is None:
            missing.append(src)
            return ""
        images.append({"alt": alt, "url": url})
        return "![%s](%s)" % (alt, url)

    return re.sub(r"!\[([^\]]*)\]\(([^)]+)\)", _sub, md_text), images, missing


@app.get("/api/tutorial/doc", summary="教程指导：只读读取演示文档（图片已内联到 /tutorial-assets）")
async def tutorial_doc(token: str = Query(...)):
    """只读返回教程 md（**不改文件**）：图片引用改写成静态资源 URL，前端直接渲染。"""
    get_session(token)
    if not TUTORIAL_MD.is_file():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "教程文档不存在：%s" % TUTORIAL_MD.name)
    try:
        text = TUTORIAL_MD.read_text(encoding="utf-8")
        md, images, missing = _tutorial_sync_assets(text)
        st = TUTORIAL_MD.stat()
    except OSError as e:
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "教程文档读取失败：%s" % e)
    title = TUTORIAL_MD.stem
    for ln in md.splitlines():
        if ln.strip().startswith("# "):
            title = ln.strip().lstrip("#").strip() or title
            break
    return {
        "ok": True, "name": TUTORIAL_MD.name,
        "path": str(TUTORIAL_MD.relative_to(PROJECT_ROOT)).replace("\\", "/"),
        "title": title, "content": md, "images": images, "missing": missing,
        "updated": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(st.st_mtime)),
        "size": st.st_size,
    }


# ----------------------------- 语音朗读（voice_test 联动） -----------------------------
@app.get("/api/voice/packs", summary="我的语音包列表（含当前选中）")
async def voice_packs(token: str = Query(...)):
    return vtts.voice_packs_list(token)

@app.post("/api/voice/packs", summary="上传参考音频+台词，克隆生成语音包 pkl")
async def voice_pack_build(token: str = Query(...),
                           file: UploadFile = File(...),
                           ref_text: str = Form(...),
                           pack_name: str = Form(...)):
    return vtts.voice_pack_build(token, file, ref_text, pack_name)

@app.post("/api/voice/packs/select", summary="选中某语音包（记录 ACTIVE_VOICE）")
async def voice_pack_select(req: vtts.SpeakReq, token: str = Query(...)):
    return vtts.voice_pack_select(token, req.pack_name)

@app.delete("/api/voice/packs/{pack_name}", summary="删除语音包")
async def voice_pack_delete(pack_name: str, token: str = Query(...)):
    return vtts.voice_pack_delete(token, pack_name)

@app.post("/api/tts/speak", summary="朗读文本（异步）：立即返回 task_id，后台线程合成")
async def tts_speak(req: vtts.SpeakReq, token: str = Query(...)):
    return vtts.voice_speak(token, req)

@app.get("/api/tts/status/{task_id}", summary="查询朗读合成任务状态（pending/running/done/error）")
async def tts_status(task_id: str, token: str = Query(...)):
    return vtts.voice_speak_status(task_id)

@app.get("/api/tts/audio", summary="获取该账号合成的 wav 音频")
async def tts_audio(name: str = Query(...), token: str = Query(...)):
    return vtts.voice_audio(token, name)

# ----------------------------- 静态 SPA -----------------------------
STATIC_DIR = PROJECT_ROOT / "front"

# ★ HTML 入口**绝不缓存**（2026-09-25 桌面端报障）：桌面壳是 pywebview + `private_mode=False` +
#   固定 storage_path（为了"记住登录"必须这么设），WebView2 于是会**持久化 HTTP 缓存**；而这里原先
#   只发 Last-Modified / ETag、没有 Cache-Control，Chromium 就按启发式把它当成"还新鲜"，直接把旧
#   `index.html` 交给窗口 —— 表现是「同一次前端改动，网页端刷新能看到、桌面端怎么重启都看不到」，
#   实测缓存里那份 HTML 停在改动之间的某个版本（有 015 样式、没有当时的 阅读 按钮）。
#   单文件 SPA 的 HTML 是入口，必须每次校验；带版本的静态资源与 API 不受影响（见下面的白名单判断）。
@app.middleware("http")
async def _no_store_html_entry(request, call_next):
    resp = await call_next(request)
    if request.method in ("GET", "HEAD"):
        p = request.url.path
        if p == "/" or p.endswith(".html"):
            resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
            resp.headers["Pragma"] = "no-cache"
    return resp


@app.get("/", summary="前端 SPA 入口")
async def index():
    return FileResponse(STATIC_DIR / "index.html")


# 挂载静态资源（vendor/vue 等）。注意：放在 / 路由之后，避免覆盖。
app.mount("/front", StaticFiles(directory=str(STATIC_DIR), html=False), name="front")
# 背景图目录：pic/{username}/ 按账号隔离
app.mount("/pic", StaticFiles(directory=str(cust.PIC_DIR), html=False), name="pic")
TUTORIAL_ASSETS.mkdir(parents=True, exist_ok=True)   # 教程图片缓存目录（静态挂载要求目录存在）
app.mount("/tutorial-assets", StaticFiles(directory=str(TUTORIAL_ASSETS), html=False), name="tutorial-assets")
