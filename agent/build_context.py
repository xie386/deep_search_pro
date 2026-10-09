"""请求装配管线（M1/M2 上下文工程化）——每次 invoke 前拼装完整上下文。

承接 AstrBot 借鉴文档「请求装配管线化」：把动态内容（SOUL 人格 / MEMORY 记忆画像 /
未来 KB 片段）在每次 invoke 前拼装，替代 v1.0「改人格/记忆就重建整个 agent」。

装配管线（M2 版）：
    _apply_persona(SOUL.md) → _apply_memory(MEMORY.md) → _apply_kb(预留)
    → 历史(SQLite) → 上下文预算(截断/压缩) → ContextBundle

注入载体（Spike 验证 2026-09）：deepagents invoke 时 messages[0] 放 SystemMessage
可动态生效并覆盖构建时 system_prompt——因此动态内容拼成一条 SystemMessage 放
messages 首条；main 基础提示词仍由 agent 构建时承载（静态，不进动态消息）。
ContextTruncator 保护所有 SystemMessage → persona 消息永不因截断丢失。

本模块不 import api.* 层（避免反向依赖）：SOUL/MEMORY 文本由调用方（server 层）
读取后经 dynamic 参数传入。
"""
from __future__ import annotations

from dataclasses import dataclass, field

from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage

from agent import conversation_store
from agent.context_budget import (
    ContextConfig,
    ContextManager,
    ProcessedContext,
    default_config,
    default_manager,
)


@dataclass
class ContextBundle:
    """一次请求的完整上下文（invoke 直接可用）。"""

    messages: list[BaseMessage] = field(default_factory=list)  # [SystemMessage(动态), *历史, 提问]
    history_len: int = 0          # 处理前历史消息数
    dropped_rounds: int = 0       # 截断丢弃轮数
    compressed: bool = False      # 是否触发压缩
    new_user_msg: str = ""        # 本次用户提问（**原始文本**，用于落库/展示）
    final_user_msg: str = ""      # 装配后的实际用户文本（含技能前缀，用于定位本轮边界）
    skills_injected: bool = False # 本次是否注入了技能说明书
    cli_injected: bool = False    # 本次是否注入了「可用命令行工具」简报（M4c）
    persona_injected: bool = False  # 本次是否注入了动态 SystemMessage（调试/观测用）


# ---------------------------------------------------------------- M3-6：记忆注入的两段预算
# 方案 §5.4：「画像 ≤500 字、笔记 ≤300 字；超限在**装配期**裁剪，而不是靠下游句子过滤」。
# 为什么要在装配期裁：下游（周报/摘要）按行做字符串过滤会**按内容猜**该丢什么，既不可控又难测；
# 装配期按"预算 + 优先级"裁，口径唯一、可断言。
PROFILE_MAX_CHARS = 500      # 画像段硬上限（超出按情报相关维度优先保留）
NOTES_MAX_CHARS = 300        # 笔记段硬上限
NOTES_MAX_ITEMS = 5          # 笔记最多取几条（§4.3）
NOTES_KEEP_RECENT = 3        # ★ 时效兜底：最近 3 条无论相关性都保留（防"刚记的事看不到"）
# 裁剪优先保留的维度（越靠前越先留）。方案 §5.4 给的是「消费偏好/情报关注点/竞品 > 其他」；
# 这里把**身份与领域 / 身份变化**也提到前面：身份是最稳定、最有用的上下文，丢它比丢"关注渠道"更糟。
_PRIORITY_DIMS = ("身份与领域", "身份变化", "消费偏好", "情报关注点", "竞品关注",
                  "预算范围", "价格敏感度")


def _split_memory(memory_text: str) -> tuple[str, str]:
    """把注入用的 MEMORY.md 文本切成（画像段正文、笔记段正文）。

    解析交给 `tools.memory_profile`（唯一事实源），这里只做取用；失败则整段当画像（保守）。
    """
    try:
        from tools import memory_profile as _mp
        sec = _mp.split_sections(memory_text)
        prof, notes = sec.get("profile", "").strip(), sec.get("notes", "").strip()
        if not prof and not notes and (memory_text or "").strip():
            # ★ 兜底：文本不是标准格式（老文件 / 用户自己写的花式排版）→ 整段当画像原样注入。
            #   绝不能返回空 —— 那等于"悄悄把用户的记忆删了"（实测被 test_assembly 的老契约挡下来过）。
            return (memory_text or "").strip(), ""
        return prof, notes
    except Exception:
        return (memory_text or "").strip(), ""


def _bullet_lines(block: str) -> list[str]:
    return [l.strip() for l in (block or "").split("\n") if l.strip().startswith("-")]


def _trim_profile(block: str, limit: int = PROFILE_MAX_CHARS) -> str:
    """画像段按"情报相关维度优先"裁剪到 limit 字；仍放不下的整行丢弃（不切半行）。

    ★ **放得下就原样返回**（含 `## USER PROFILE（用户画像）` 标题、原顺序、原空行）——
      不要无缘无故重排：老契约 `test_assembly.py::test_compose_dynamic_prompt_combined`
      断言的正是"没超预算时逐字一致"，把它排成另一种样子会白白破坏既有契约。
    """
    if len(block.strip()) <= limit:
        return block.strip()
    lines = _bullet_lines(block)
    if not lines:
        return block.strip()
    order = sorted(range(len(lines)),
                   key=lambda i: (next((k for k, d in enumerate(_PRIORITY_DIMS) if d in lines[i]),
                                       len(_PRIORITY_DIMS)), i))
    kept, used = [], 0
    for i in order:
        cost = len(lines[i]) + (1 if kept else 0)
        if used + cost > limit:
            continue
        kept.append((i, lines[i]))
        used += cost
    kept.sort()
    return "\n".join(l for _, l in kept)


def _lexical_overlap(text: str, question: str) -> int:
    """极简词法相关度（v1 只用词法，不引向量 —— 方案 §4.3 明说"若复用成本高，v1 只用词法"）。

    中文按**二元字组**取交集，避免为一个排序引入分词/向量依赖（embedding 冷启动 ~33s）。
    """
    q = "".join(ch for ch in (question or "") if ch.strip())
    if len(q) < 2:
        return 0
    grams = {q[i:i + 2] for i in range(len(q) - 1)}
    t = text or ""
    return sum(1 for g in grams if g in t)


def _trim_notes(block: str, limit: int = NOTES_MAX_CHARS, question: str = "") -> str:
    """笔记段：按相关度取前 N 条 + 总长 ≤ limit；**最近 3 条无条件保留**（时效兜底）。

    同样**放得下就原样返回**（理由见 `_trim_profile`）。
    """
    if len(block.strip()) <= limit and len(_bullet_lines(block)) <= NOTES_MAX_ITEMS:
        return block.strip()
    lines = _bullet_lines(block)
    if not lines:
        return block.strip()
    recent = set(range(max(0, len(lines) - NOTES_KEEP_RECENT), len(lines)))
    scored = sorted(range(len(lines)),
                    key=lambda i: (0 if i in recent else 1,
                                   -_lexical_overlap(lines[i], question), i))
    kept, used = [], 0
    for i in scored:
        if len(kept) >= NOTES_MAX_ITEMS:
            break
        cost = len(lines[i]) + (1 if kept else 0)
        if used + cost > limit:
            continue
        kept.append((i, lines[i]))
        used += cost
    kept.sort()
    return "\n".join(l for _, l in kept)


def compose_dynamic_prompt(
    soul_text: str = "",
    memory_text: str = "",
    username: str = "",
    cli_brief: str = "",
    profile_signal: str = "",        # M3-5：服务端算好的【画像待更新】信号（没有新数据时是空串）
    question: str = "",              # M3-6：笔记段按与问句的相关度取前 N（v1 只用词法，不引向量）
    image_ids: "list[str] | None" = None,   # ★ v3.1 视觉：本轮贴的图（有图才注入能力声明 ✓）
    vision_capable: bool = False,           # ★ v3.1：当前生效模型是否支持图片输入（接口层判定 ✓）
) -> str:
    """拼装动态注入内容：人格(SOUL) + 记忆画像(MEMORY) + 可用命令行工具(M4c)。

    只含动态部分（人格在前、记忆画像居中、CLI 简报在后）；main 基础提示词由 agent 构建时
    承载，不在此重复（避免双 system 语义冲突）。
    对应 AstrBot 的 _apply_persona + _apply_memory 段。

    `cli_brief` 由 server 层从 `tools/cli_registry.agent_brief()` 取好传入（本模块不 import api.*）。
    注入目的是消除「明明配好了 CLI 却自称没有/凭知识编造」的幻觉——清单只在用户登记为
    已接入且填了只读命令时才有内容，不配 CLI 的用户零开销。
    """
    parts: list[str] = []
    if soul_text:
        parts.append(f"【人格设定】\n{soul_text}")
    if memory_text:
        # ★ 路径写法必须是「相对 agents_docs 的路径」：read/write_agent_doc 的 filename 参数
        #   就是按 agents_docs 解析的（tools/writetofile.py::_resolve_safe_path）。早先这里写成
        #   `agents_docs/{username}/MEMORY.md`，模型照抄传参 → 落到了 agents_docs/agents_docs/{user}/
        #   （能通过越权校验、返回"写入成功"，但加载器读的是 agents_docs/{user}/MEMORY.md → 永远读不到）。
        #   实测踩过：agents_docs/agents_docs/尼古喵喵/MEMORY.md 就是这么来的。
        mem_hint = (
            f"（记忆文件：{username}/MEMORY.md —— 相对 agents_docs 目录的路径，"
            f"读写文档工具的 filename 直接用这个相对路径，不要带 agents_docs/ 前缀）"
            if username
            else ""
        )
        _prof, _notes = _split_memory(memory_text)
        _prof = _trim_profile(_prof, PROFILE_MAX_CHARS)
        _notes = _trim_notes(_notes, NOTES_MAX_CHARS, question=question)
        # ★ 笔记段必须带自己的标题：否则模型分不清"用户画像事实"与"助手流水笔记"（旧契约测试
        #   test_assembly.py::test_compose_dynamic_prompt_combined 就是被这一条挡下来的）。
        try:
            from tools.memory_profile import NOTES_HEAD as _NOTES_HEAD
        except Exception:
            _NOTES_HEAD = "## MEMORY（助手笔记）"
        _mem_body = "\n".join(x for x in (_prof, ("%s\n%s" % (_NOTES_HEAD, _notes)) if _notes else "") if x)
        parts.append(f"【你的用户记忆画像】{mem_hint}\n{_mem_body}")
        if profile_signal:
            # M3-5（方案 §3.3）：把"看不见的判断"变成看得见的信号 —— 数字由服务端算好，模型只判断相关性
            parts.append(profile_signal)
    if cli_brief:
        # ★ 这三句是「信念锚」，别删：真机探针实测过——把它们拿掉后，10 条自然语言探针里
        #   有 5 条模型**一个工具都不调**（只是把工具清单当背景资料看），命中率从 9~10/10 掉到 5/10。
        #   它们只在本区块存在时才有意义（条件性强度要求），所以归属注入块，不进静态提示词。
        # v3.0 M1：这份简报现在可能同时含 CLI / API / MCP 三种来源的卡片（路由器统一渲染）。
        # 出现 API/MCP 卡片时换标题并补一句 invoke_tool 用法；**只有 CLI 时文本逐字不变**
        # （契约测试与"信念锚"的 A/B 结论都建立在那段原文上）。
        _has_remote = ("API 能力 `" in cli_brief) or ("MCP 能力 `" in cli_brief)
        _head = ("【用户自配的工具能力（CLI 只读 / API / MCP）】\n"
                 if _has_remote else
                 "【可用命令行工具（用户自配的 CLI，只读）】\n")
        _remote_hint = (
            "\n需要用到上面形如 `api:<服务名>#<操作名>` 的 **API/MCP 能力**时，调用 "
            "`invoke_tool(ref=\"…\", params={…})`：ref 与参数**照抄能力卡**，不要自己编；"
            "卡片上带「⚠️ 会改外部状态」的写能力，先确认用户明确要求再调用；"
            "被拒绝时如实转述错误原文，不要凭知识推断。" if _has_remote else ""
        )
        parts.append(
            _head +
            "（本区块出现 = 用户本机**确实装了**下列 CLI，并已放行其中的只读命令——它们是真实可用的工具）\n"
            f"{cli_brief}\n"
            "用户的问题落在某个能力描述覆盖的领域时，**优先调用 run_shell_command 取真实数据**："
            "这些是用户自己账号里的私有数据，网络搜索与知识库都拿不到，不要改用它们顶替，也不要凭你自己的知识作答；"
            "也不要在没有任何工具返回的情况下声称「该命令不可用」或「未登记」。\n"
            "调用方式：`run_shell_command(command=\"<可执行名>\", argv=[...])`。"
            "只读清单内的命令才能执行，写操作与落盘参数会被拒绝；"
            "多词子命令路径要**整体作为一个 argv 传入**（如 `argv=[\"log\",\"query\"]`），"
            "不要拆成两次调用（拆开后每一半都不在只读清单里，只会换来两次拒绝）；"
            "被拒绝时如实转述工具返回的错误原文，不要凭知识推断白名单内容。"
            + _remote_hint
        )
    if vision_capable:
        # ★ v3.1 视觉：**能力事实**（与下面"本轮有图"的强指令分开写 ✓）
        #   用户 2026-09-30 实测：哪怕纯文字轮，它也会自称「我目前没有视觉识别的能力」✗ ——
        #   因为注入块长期只有工具视角，它据此推理出"我没有视觉"✗，而它其实是多模态的 ✓。
        #   所以这里给**事实**、下面给**本轮真有图**的强指令 ✓；两段都只在条件成立时出现 ✓。
        parts.append(
            "【你自己的能力：支持图片输入】\n"
            "你当前使用的模型**本身支持图片输入**（多模态）—— 用户可以直接把图片贴进对话框，"
            "贴了你就**看得到**，直接看图作答即可，**不需要也不会用到任何图像工具**。\n"
            "★ 不要再说自己「没有视觉识别能力」「看不到图片」「只能处理文字」✗ —— 那是错的。\n"
            "反向也要守住诚实：**没贴图时不要声称自己看到了什么** ✗（图片只在用户贴了的那一轮送达 ✓）。"
        )
    if image_ids:
        # ★ v3.1 视觉能力声明（用户 2026-09-30 实测反馈）：
        #   真机现象：模型**准确描述出了图片内容**（它真的看见了），却坚持说
        #     「我目前没有视觉识别的能力」「图片是通过您描述的方式呈现给我的」✗
        #   根因：这个注入块一直是**工具视角**（人格 + 记忆 + CLI/API/MCP 能力卡）✗，
        #     模型据此推理出「我的工具都是文字处理类的 → 我没有视觉」✗ —— 而它其实是多模态的 ✓。
        #   纪律与上面的 CLI 块一致：**只在"本轮真有图"时出现**才有力（条件性强度 ✓），
        #     因此归属注入块、**不进静态提示词** ✓（也不在没图时提视觉 —— 免得它凭空虚报"我看得见" ✗）。
        parts.append(
            "【本轮：用户直接贴了图片】\n"
            f"用户本轮贴了 {len(image_ids)} 张图片，图片**已经随本轮消息一起送达给你** —— "
            "你**本身就能看图**（当前对话用的模型支持图片输入），直接看着图回答即可。\n"
            "★ 不要说自己「没有视觉识别能力」「看不到图片」「图片是通过用户描述得知的」✗ —— "
            "这些都是错的；也不要为了看图去调用任何工具（既没有、也不需要图像工具）。\n"
            "照常结合用户的文字提问作答；图片内容以你**实际所见**为准，看不清的细节如实说看不清。"
        )
    return "\n\n——\n\n".join(parts)


def compose_user_prompt(new_user_msg: str, skills_text: str = "") -> str:
    """把本轮技能说明书拼到用户提问之前（M4a：技能 = user message 前缀）。

    选择 user message 前缀而非 system 注入的原因：技能是「本轮一次性」语义，
    混进 system 会与人格/记忆的常驻语义混淆，且无法随轮次自然消失
    （历史里存的是原始提问，故下一轮自然不再带技能）。

    注入的是**最终文本**；落库/展示仍用原始提问（调用方各自取用）。
    """
    if not skills_text:
        return new_user_msg
    return f"{skills_text}\n\n———\n\n【用户提问】\n{new_user_msg}"


def build_request_context(
    thread_id: str,
    account_id: int,
    new_user_msg: str,
    *,
    image_ids: "list[str] | None" = None,      # ★ v3.1 视觉：**本轮**贴的图（只喂这一轮 ✓ 不进历史 ✓）
    vision_capable: bool = False,             # ★ v3.1：生效模型是否支持图片（接口层算好传入 ✓）
    soul_text: str = "",
    memory_text: str = "",
    username: str = "",
    skills_text: str = "",
    cli_brief: str = "",
    profile_signal: str = "",        # M3-5：画像待更新信号（server 层算好传入）
    sources: "dict[str, str] | None" = None,   # M4-7：注册表收集到的源（None=老路径，逐字等价）
    config: ContextConfig | None = None,
    manager: ContextManager | None = None,
) -> ContextBundle:
    """装配一次对话请求的完整上下文（M2 请求装配管线）。

    :param thread_id: 会话 ID（历史按此读取/落库）
    :param account_id: 账号 ID（数据隔离）
    :param new_user_msg: 本次用户提问（原始文本）
    :param soul_text: 激活人格 SOUL.md 内容（可空）
    :param memory_text: 记忆画像 MEMORY.md 内容（可空）
    :param username: 用户名（记忆提示里标注文件路径）
    :param skills_text: 本轮启用技能的说明书前缀文本（M4a，可空；由 server 层读取传入）
    :param cli_brief: 「可用命令行工具」简报（M4c，可空；由 server 层取 tools/cli_registry 传入）
    :return: ContextBundle.messages 可直接传给 agent.invoke
    """
    mgr = manager or default_manager
    cfg = config or default_config

    # 1. _apply_persona + _apply_memory：动态内容 → SystemMessage（messages 首条）
    if sources is not None:
        # M4-7：soul / memory 改由注册表提供（api/context_providers 注册）。
        # 只接管这两个"纯账号级"的源；skills / cli_brief / profile_signal 需要本轮特有入参，
        # 仍由 server 显式传入（见 api/context_providers 的说明）。
        soul_text = sources.get("soul", "") or ""
        memory_text = sources.get("memory", "") or ""
    dynamic_text = compose_dynamic_prompt(soul_text, memory_text, username, cli_brief or "",
                                          profile_signal=profile_signal, question=new_user_msg,
                                          image_ids=image_ids, vision_capable=vision_capable)
    persona_msg = SystemMessage(content=dynamic_text) if dynamic_text else None

    # 2. 历史（SQLite 持久化）
    history = conversation_store.get_history(account_id, thread_id)

    # 3. _apply_skills（M4a）：技能前缀 + 提问 → 实际用户文本
    final_user_msg = compose_user_prompt(new_user_msg, skills_text)

    # 4. 装配 [persona?] + 历史 + 提问 → 上下文预算（截断/压缩，system 永不丢）
    # ★ v3.1 视觉能力：有图 → 本轮提问变成多模态 blocks（先文字后图 ✓ 顺序更稳 ✓）
    #   · 无图时**不 import、不构造**，final_user_msg 原样进 ✓（老链路零变化 ✓）
    #   · 只喂本轮 ✓ 历史仍只存文字（用户 2026-09-30 拍板「只跟本轮」✓）
    _user_content = final_user_msg
    if image_ids:
        from tools import chat_images as _ci
        _user_content = _ci.build_content(final_user_msg, list(image_ids), account_id)
    new_msg = HumanMessage(content=_user_content)
    raw = ([persona_msg] if persona_msg else []) + [*history, new_msg]
    processed: ProcessedContext = mgr.process(raw, cfg)

    return ContextBundle(
        messages=processed.messages,
        history_len=len(history),
        dropped_rounds=processed.dropped_rounds,
        compressed=processed.compressed,
        new_user_msg=new_user_msg,
        final_user_msg=final_user_msg,
        skills_injected=bool(skills_text),
        cli_injected=bool(cli_brief),
        persona_injected=persona_msg is not None,
    )
