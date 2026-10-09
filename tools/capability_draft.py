# -*- coding: utf-8 -*-
"""M5c-2'：API/MCP「工具级描述」草稿的**证据打包**与**结果解析**（纯函数，离线可测）。

为什么要拆出来：和 CLI 那套（`cli_docs` + `cli_registry.audit_*`）同理 —— 草稿质量取决于
"给了模型什么依据"以及"它的输出能不能被可靠地回收"：

  · **依据** = README/文档（语义事实） + 工具清单与参数 schema（形态真值） + 用户写的来源级描述（语气）；
  · **解析**必须容错：模型可能把标识写成名字、多写一段、漏几个工具、甚至写清单外的工具 ——
    这些都要识别出来并提示，而不是静默丢进库里。

本模块不碰网络、不碰数据库，因此可以单独回归（见 `tests/test_capability_draft.py`）。
"""
from __future__ import annotations

import re

_TOOL_LINE = re.compile(r"^\s*(?:工具|tool)\s*[:：]\s*(.+?)\s*$", re.I)
_DESC_LINE = re.compile(r"^\s*(?:描述|description)\s*[:：]\s*(.*)$", re.I)
_NAME_LINE = re.compile(r"^\s*(?:名称|名字|显示名|name)\s*[:：]\s*(.*)$", re.I)
MAX_NAME_TEXT = 24            # 显示名上限（「人话名字」，2026-10-06 增）
MAX_TOOL_TEXT = 400          # 单条描述的落库上限（模型偶尔会写长，砍掉尾巴而不是整条丢）


def tool_listing(caps: list[dict]) -> str:
    """把该来源的工具清单渲染成**给模型看的事实表**（不是给用户看的卡片）。

    每条给：标识（ref，模型必须逐字照抄）、名称、参数（必填/可选 + 位置）、只读判定、现有描述。
    现有描述要带上 —— 用户可能已经手写过几条，模型应当在此基础上改，而不是推倒重来。
    """
    lines = ["| 工具标识（逐字照抄） | 名称 | 参数（必填★） | 只读 | 现有描述 |", "| --- | --- | --- | --- | --- |"]
    for c in caps:
        params = []
        schema = c.get("input_schema") or {}
        props = schema.get("properties") or {}
        required = set(schema.get("required") or [])
        binds = (c.get("invoke_spec") or {}).get("bindings") or []
        for b in binds:
            if isinstance(b, dict) and b.get("name"):
                params.append("%s%s(%s)" % ("★" if b.get("required") else "", b["name"],
                                            b.get("location") or "query"))
        if not params:
            for name in list(props)[:12]:
                params.append("%s%s" % ("★" if name in required else "", name))
        cur = (c.get("abilities_user") or "") or (c.get("abilities") or "")
        lines.append("| `%s` | %s | %s | %s | %s |" % (
            c.get("ref") or "", (c.get("name") or "")[:40],
            "、".join(params)[:160] or "（无参数）",
            "是" if c.get("read_only", True) else "**否（写操作）**",
            (cur or "").replace("|", "/")[:80]))
    return "\n".join(lines)


def _index(caps: list[dict]) -> dict[str, dict]:
    """建立"模型可能怎么写标识" → 工具行 的索引（ref / name / ref 的尾段）。"""
    idx: dict[str, dict] = {}
    for c in caps:
        ref = str(c.get("ref") or "")
        keys = {ref, str(c.get("name") or "")}
        for sep in ("/", "#", ":"):
            if sep in ref:
                keys.add(ref.rsplit(sep, 1)[-1])
        for k in keys:
            k = k.strip().lower()
            if k:
                idx.setdefault(k, c)
    return idx


def parse_draft(text: str, caps: list[dict]) -> dict:
    """解析撰写 AI 的输出 → `{items, warnings}`。

    约定格式（三段一段，段间空行；「名称：」可选，用于给英文标识符一条人话名字）：
        工具：<标识>
        名称：<中文显示名>
        描述：关键词：a/b/c。<正文>
    容错：描述折行会并回同一段；标识写成名称/尾段也能对上；对不上的标识只告警不落库。
    """
    idx = _index(caps)
    items: list[dict] = []
    warnings: list[str] = []
    unknown: list[str] = []
    seen_refs: set[str] = set()
    cur: list | None = None      # [标识原文, [描述行], 显示名建议]

    def _flush() -> None:
        nonlocal cur
        if not cur:
            return
        key = cur[0]
        body = " ".join(x for x in cur[1] if x).strip()
        suggest_name = (cur[2] if len(cur) > 2 else "").strip()[:MAX_NAME_TEXT]
        cur = None
        row = idx.get(key.strip().lower())
        if not row:
            unknown.append(key)
            return
        ref = str(row.get("ref") or "")
        if not body:
            warnings.append("`%s` 只给了标识、没给描述，已跳过" % ref)
            return
        if ref in seen_refs:
            warnings.append("`%s` 出现了两次，只保留第一次" % ref)
            return
        seen_refs.add(ref)
        item = {"ref": ref, "name": row.get("name") or ref, "text": body[:MAX_TOOL_TEXT]}
        if suggest_name and suggest_name != (row.get("name") or ""):
            item["suggest_name"] = suggest_name      # ★ 只作建议；用户在前端确认后才落库
        items.append(item)

    for raw in (text or "").splitlines():
        line = raw.strip()
        m = _TOOL_LINE.match(line)
        if m:
            _flush()
            cur = [m.group(1).strip(), [], ""]
            continue
        m = _NAME_LINE.match(line)
        if m and cur is not None:
            cur[2] = m.group(1).strip()          # 名称行（可选）
            continue
        m = _DESC_LINE.match(line)
        if m and cur is not None:
            cur[1].append(m.group(1).strip())
            continue
        if cur is not None and line:
            cur[1].append(line)          # 模型把描述折了行
    _flush()

    if unknown:
        warnings.append("模型写了清单里没有的工具，已忽略：%s" % "、".join(unknown[:6]))
    missing = [str(c.get("ref") or "") for c in caps
               if str(c.get("ref") or "") not in seen_refs and str(c.get("ref") or "")]
    if missing:
        warnings.append("%d 个工具没被写到描述（可手动补，或把该工具的帮助文本/README 补全后再生成一次）：%s"
                        % (len(missing), "、".join("`%s`" % m for m in missing[:6])))
    if not items:
        warnings.append("没解析出任何可用描述 —— 请检查模型输出格式（工具：/描述：两行一段）")
    return {"items": items, "warnings": warnings}

MAX_README_CHARS = 12000     # README/文档进提示词的上限（这类文档常只讲怎么接入，够用即可）
MAX_INTRO_CHARS = 20000      # 官方介绍文案的上限：**比 README 更宽**，因为文案才是能力说明的正主
#   （用户 2026-09-27：「字数限制可以放宽一点」→ 从社区页整段复制通常几千字，给足空间）


def build_user_msg(src: dict, caps: list[dict], doc: dict, intro: str,
                   listing: str | None = None, transport: str = "") -> tuple[str, list[str]]:
    """拼「给撰写 AI 的用户消息」+ 预检警告。**纯函数**（不碰网络/DB），便于离线断言。

    为什么要分成两份依据（2026-09-27 用户反馈，很实在）：第三方 MCP / API 的 README 往往**只讲怎么接入**
    （安装、鉴权、技术栈、部署），能力文案在社区/市场页上，有些 API 干脆没有 README。
    所以：
      · `intro`（官方介绍文案）= **能力说明的正主**，写描述时优先看它；
      · `docs`（README/文档）= 帮助理解"这个服务是干什么的"与形态，但**接入细节不许写进能力描述**；
      · 两者都没填 → 仍可生成，但只能按工具名与参数保守写，并**明确告警**（提示补一个再生成）。

    返回 `(user_msg, pre_warnings)`。
    """
    kind = (src.get("source") or "api").lower()
    label = "MCP 服务" if kind == "mcp" else "API 来源"
    intro = (intro or "").strip()
    if len(intro) > MAX_INTRO_CHARS:
        intro = intro[:MAX_INTRO_CHARS] + "\n…（文案较长，已截断）"
    doc = doc or {}
    doc_text = (doc.get("text") or "").strip()
    if len(doc_text) > MAX_README_CHARS:
        doc_text = doc_text[:MAX_README_CHARS] + "\n…（文档较长，已截断）"
    listing = listing if listing is not None else tool_listing(caps)

    pre: list[str] = []
    if not intro and not doc_text:
        pre.append("这条来源既没填「README / 文档地址」，也没贴「官方介绍文案」—— "
                   "描述只能按工具名与参数保守地写（命名不直观的工具会写不准）。"
                   "建议补一个：第三方服务的 README 常只讲怎么接入，这时贴**官方介绍文案**更管用。")

    parts = ["【来源】%s · 短名 `%s` · 展示名 %s" % (label, src.get("slug") or "", src.get("name") or "（未填）"),
             "【怎么连上的】%s" % (transport or ("MCP 服务" if kind == "mcp" else "HTTP 接口"))]
    if intro:
        parts.append("【官方介绍文案（**能力说明优先看这份**）】\n%s" % intro)
    else:
        parts.append("【官方介绍文案】没填 —— 若下面那份文档只讲「怎么接入」，能力就只能靠工具名与参数推。")
    if doc_text:
        parts.append("【README / 文档（**可能偏「怎么接入」与技术栈**，接入细节不要写进能力描述）——来自 %s】\n%s"
                     % (doc.get("url") or "", doc_text))
    else:
        parts.append("【README / 文档】未提供或读取失败（%s）" % (doc.get("note") or "无"))
    parts.append("【用户写的来源级描述（用词与语气向它靠拢）】%s" % ((src.get("abilities") or "").strip() or "（没写）"))
    parts.append("【工具清单与参数（服务自己暴露的 schema，形态以此为准）】\n%s" % listing)
    parts.append(
        "# 本次的硬规则\n"
        "1. **清单里的每个工具都要有一段**，一段两行：`工具：<标识>` / `描述：关键词：…。<正文>`；\n"
        "2. **能力以「官方介绍文案」+ 工具清单为准**；README/文档只用来理解这个服务是干什么的，"
        "**安装 / 配置 / 鉴权 / 部署 / 技术栈这些接入细节一个字都不要写进能力描述**；\n"
        "3. 标识**逐字照抄**清单第一列，一个字都不要改；清单外的工具一律不写；\n"
        "4. 同一来源里的工具**必须能区分开**（写清它跟隔壁那个的区别），不要几个工具套同一句；\n"
        "5. 参数上的关键区别（如「要先拿到 ID 再查详情」这类两步链）要写进描述；\n"
        "6. 关键词用领域词，不要写「今天/有没有/XX」这类通用词或占位符；\n"
        "7. 三种依据都没提到的能力：**不要写**（宁可少写一条，也不要编造）；\n"
        "8. 只输出规定格式的文本（不要代码围栏、不要解释、不要开场白）。")
    return "\n\n".join(parts), pre 
