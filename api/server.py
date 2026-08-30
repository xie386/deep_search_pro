# ============================================================
# 智选情报官 · FastAPI 服务（M2）
# 能力：
#   1) REST 登录/注册（账号角色分流，company / personal）
#   2) REST 问答（POST /api/chat，后台线程跑 agent，多轮按 username 隔离）
#   3) WebSocket 实时进度（/ws/{thread_id}，把 api/monitor 埋点推到前端右栏）
#   4) 静态 SPA 托管（static/index.html）
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
import sys
import time
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
from langchain_core.messages import HumanMessage

from agent.llm import model
from agent.prompts import main_agent_content
from agent.subagents.db_agent import db_agent
from agent.subagents.network_search_agent import network_search_agent
from agent.subagents.personal_agent import personal_agent
# 文档读写工具：主智能体可把知识/笔记保存到 agents_docs（仅限该目录 .md/.txt）
from tools.readtofile import read_agent_doc, list_agent_docs
from tools.writetofile import write_agent_doc
from api.account import LoginReq, RegisterReq, login, logout, register, get_session
from api.context import set_owner_context, set_thread_context
import api.me_user_data as me
from api.monitor import manager, monitor
import api.customize as cust
import api.voice_tts as vtts
from deepagents import create_deep_agent
from langgraph.checkpoint.memory import MemorySaver
from langchain_core.messages import HumanMessage
from langchain_openai import ChatOpenAI
from deepagents.middleware._tool_exclusion import _ToolExclusionMiddleware

load_dotenv()

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
    tools=[read_agent_doc, write_agent_doc, list_agent_docs],
    middleware=[_ToolExclusionMiddleware(excluded=_EXCLUDED_FS_TOOLS)],
    checkpointer=MemorySaver(),
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]


# ---------------------------------------------------------------------------
# 异步安全地跑同步 agent
# ---------------------------------------------------------------------------
# 按账号缓存的自定义 agent（用户配置了自定义模型时构建；无则用全局 AGENT）
_custom_agents: dict = {}


def _build_custom_agent(provider: dict, soul_text: str, memory_text: str = "", username: str = ""):
    """用用户自定义模型（若有）+ SOUL.md 人格 + 记忆画像构建一个独立主 Agent"""
    if provider:
        custom_model = ChatOpenAI(
            model=provider["model_name"],
            api_key=provider["api_key"],
            base_url=provider["base_url"],
        )
    else:
        custom_model = model  # 无自定义模型时用默认 .env 模型
    sys_prompt = main_agent_content["system_prompt"]
    if soul_text:
        sys_prompt = f"{soul_text}\n\n——\n\n{sys_prompt}"
    if memory_text:
        mem_hint = f"（记忆文件：agents_docs/{username}/MEMORY.md，可用读取/写入文档工具维护）" if username else ""
        sys_prompt = f"{sys_prompt}\n\n——\n\n【你的用户记忆画像】{mem_hint}\n{memory_text}"
    return create_deep_agent(
        model=custom_model,
        system_prompt=sys_prompt,
        subagents=[network_search_agent, db_agent, personal_agent],
        tools=[read_agent_doc, write_agent_doc, list_agent_docs],
        middleware=[_ToolExclusionMiddleware(excluded=_EXCLUDED_FS_TOOLS)],
        checkpointer=MemorySaver(),
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
        p = _cust._user_memory_path(username)
        return (p.read_text(encoding="utf-8").strip() if p.exists() else ""), username
    except Exception as e:
        print(f"[memory] 读取 MEMORY.md 失败（忽略）: {e}")
        return "", ""


def _get_agent_for(account_id: int | None):
    """按账号选择 agent：有自定义模型/人格/记忆画像则用（缓存），否则全局 AGENT。
    同时返回注入后的 system_prompt（供旁路思考捕获使用）。
    缓存 key 含 soul+memory 哈希：人格或记忆变化时自动重建 agent。"""
    if account_id is None:
        return AGENT, main_agent_content["system_prompt"]
    try:
        provider = cust.get_active_provider(account_id)
    except Exception:
        provider = None
    soul_text = cust.get_soul_content(account_id)
    memory_text, username = _get_memory_text(account_id)
    # 有自定义模型/人格/记忆画像时按账号建独立 agent：
    # 全局 AGENT 的 system_prompt 在构建时固定，无法注入这些动态内容。
    if provider or soul_text or memory_text:
        key = (account_id, hash((soul_text or "", memory_text or "")))
        if key not in _custom_agents:
            _custom_agents[key] = _build_custom_agent(provider, soul_text, memory_text, username)
        sys_prompt = main_agent_content["system_prompt"]
        if soul_text:
            sys_prompt = f"{soul_text}\n\n——\n\n{sys_prompt}"
        if memory_text:
            mem_hint = f"（记忆文件：agents_docs/{username}/MEMORY.md，可用读取/写入文档工具维护）" if username else ""
            sys_prompt = f"{sys_prompt}\n\n——\n\n【你的用户记忆画像】{mem_hint}\n{memory_text}"
        return _custom_agents[key], sys_prompt
    return AGENT, main_agent_content["system_prompt"]


def _run_agent(question: str, thread_id: str, account_id: int | None = None) -> str:
    """在工作线程里执行同步的 agent.invoke。
    monitor 埋点会经 run_coroutine_threadsafe 推到主 loop 的 WebSocket；
    account_id 注入数据归属上下文，工具查询按 owner_id 隔离。"""
    set_thread_context(thread_id)  # 让 monitor 知道本轮进度推给哪个 WS
    if account_id is not None:
        set_owner_context(account_id)  # 数据归属权：Agent 只能读到该用户的数据

    agent, sys_prompt = _get_agent_for(account_id)

    # 旁路思考捕获（方案 B）：langchain 丢弃 reasoning_content，用底层 client
    # 对同一组消息（system_prompt + 用户问题）发流式请求，逐 token 抓 thinking
    # 推给前端右栏；后台线程运行，不阻塞主 agent。
    try:
        from agent.thinking_capture import start_thinking_thread
        start_thinking_thread(
            [
                {"role": "system", "content": sys_prompt},
                {"role": "user", "content": question},
            ],
            thread_id,
        )
    except Exception as e:
        print(f"[thinking] 旁路启动失败（忽略）: {e}")

    cfg = {"configurable": {"thread_id": thread_id}}
    result = agent.invoke(
        {"messages": [HumanMessage(content=question)]}, cfg
    )
    return result["messages"][-1].content


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
@app.post("/api/chat", summary="发送一条消息，返回最终回答（多轮按 token 隔离）")
async def api_chat(
    question: str = Query(..., description="用户问题"),
    token: str = Query(..., description="登录 token"),
):
    sess = get_session(token)  # 校验登录，无效抛 401
    thread_id = sess["username"]  # 多轮记忆以用户为单位隔离
    monitor.set_current_thread(thread_id)  # 让工作线程里的 monitor 埋点能定向推 WS
    try:
        # 画像变更通知：记录调用前的 MEMORY.md 内容，回复末尾对比提示
        before_memory = cust.memory_get(token).get("content", "")
        answer = await asyncio.to_thread(_run_agent, question, thread_id,
                                        sess.get("account_id"))
        try:
            after_memory = cust.memory_get(token).get("content", "")
            if after_memory != before_memory:
                answer = f"{answer}\n\n---\n📝 已更新你的记忆画像（智能体在本次对话中做了记忆维护），可在「定制助手 → 记忆画像」查看或编辑。"
        except Exception:
            pass
    except Exception as e:  # 不吞掉错误，给出可读信息
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR,
                            f"Agent 执行出错：{type(e).__name__}: {e}")
    finally:
        monitor.clear_current_thread()
    return {
        "answer": answer,
        "role": sess["role"],
        "thread_id": thread_id,
    }


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
STATIC_DIR = PROJECT_ROOT / "static"


@app.get("/", summary="前端 SPA 入口")
async def index():
    return FileResponse(STATIC_DIR / "index.html")


# 挂载静态资源（vendor/vue 等）。注意：放在 / 路由之后，避免覆盖。
app.mount("/static", StaticFiles(directory=str(STATIC_DIR), html=False), name="static")
# 背景图目录：pic/{username}/ 按账号隔离
app.mount("/pic", StaticFiles(directory=str(cust.PIC_DIR), html=False), name="pic")
