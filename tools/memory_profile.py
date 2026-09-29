# -*- coding: utf-8 -*-
"""M3-1 · 记忆画像的**格式契约**（`MEMORY.md` 的行格式 / 维度白名单 / 元数据）。

为什么单独成模块：画像文件是**唯一事实源**，而它同时被四个人读写 ——
人工编辑（前端 textarea）、AI 建议器、`update_user_profile` 工具、以及周报引擎的只读消费。
格式规则散在四处必然漂移，所以全部收进这里，由**纯函数**承担：

  · `split_sections()`  head（含元数据）/ profile（`## USER PROFILE` 段）/ notes（`## MEMORY` 段）
  · `parse_rows()`      画像段 → 结构化行 `{field, fact, source, date, manual}`
  · `render_rows()`     结构化行 → 画像段正文（每行 `- 维度：事实（来源：… · 日期）`）
  · `upsert()`          **按维度就地更新**（同一维度只有一行）· 人工行默认不动 → 修 R2
  · `parse_meta()/render_meta()`  可解析元数据（与老的 `memory_init` 标记**并存**，向后兼容）
  · `has_placeholder()` 「暂无 / 待补充」式占位判定 → 修 R3（缺失就不写，而不是写占位）

★ 设计口径（M3 方案 §3.4 / §4.4 / §5.1，用户 2026-09-25 裁决）：
  1. 每行 `- 维度：事实（来源：<表> #<id> · <日期>）`；维度名从白名单里选，避免维度漂移；
  2. **禁止占位行**：缺失就不写（占位由下游字符串过滤救不回来，是 R3 的根因）；
  3. **人工行**（来源标 `人工`）永不被自动流程改动或删除；
  4. 元数据新旧**并存**：`<!-- memory_init: … -->`（老，`_memory_initialized()` 还在读）
     + `<!-- memory_meta: init=… refreshed=… sources=… -->`（新，建议器与前端用）。

本模块不碰文件系统、不碰数据库、不调模型 —— 因此可以整段离线回归。
"""
from __future__ import annotations

import re
from datetime import date as _date

# ---------------------------------------------------------------- 维度白名单（方案 §5.1）
FIELD_WHITELIST: tuple[str, ...] = (
    "身份与领域", "消费偏好", "预算范围", "价格敏感度", "情报关注点",
    "竞品关注", "关注渠道", "品类偏好", "身份变化", "其他稳定事实",
)
# 老画像里出现过的写法 → 白名单（避免"同一维度写成两个"导致就地更新失效）
FIELD_ALIASES: dict[str, str] = {
    "身份": "身份与领域", "职业": "身份与领域", "领域": "身份与领域",
    "身份与职业": "身份与领域", "身份职业": "身份与领域",     # 实测：真实账号文件里写的就是这个
    "消费": "消费偏好", "偏好": "消费偏好", "消费习惯": "消费偏好",
    "预算": "预算范围", "价格敏感": "价格敏感度", "敏感度": "价格敏感度",
    "关注点": "情报关注点", "情报关注": "情报关注点", "兴趣": "情报关注点", "兴趣点": "情报关注点",
    "关注领域": "情报关注点",                                 # 实测：真实账号文件里的写法
    "竞品": "竞品关注", "竞争对手": "竞品关注", "关注渠道": "关注渠道", "渠道": "关注渠道",
    "品类": "品类偏好", "品类倾向": "品类偏好", "身份变化": "身份变化",
    "其他": "其他稳定事实", "其他事实": "其他稳定事实",
}
# ★ 合并原则（别手滑）：**只并"同一个概念的语法变体"**。
#   像 `生日` / `幸运数字` / `订阅动态` / `酒类偏好` 这类**各自可能存不同事实**的名字一律**不并** ——
#   它们保持各自独立维度（标 `new`、由人工确认），这样 `upsert` 就是安全的；
#   一旦把两个不同概念的维度并成一个，就地更新会**吃掉**其中一条（数据丢失）。

PROFILE_HEAD = "## USER PROFILE（用户画像）"
NOTES_HEAD = "## MEMORY（助手笔记）"
PROFILE_HEADS = (PROFILE_HEAD, "## USER PROFILE", "## 用户画像")
NOTES_HEADS = (NOTES_HEAD, "## MEMORY", "## 助手笔记")

MANUAL_SOURCE = "人工"          # 人工行的来源标记：唯一能"免于自动写入"的行
PLACEHOLDER_RE = re.compile(r"暂无|待补充|待完善|待填写|尚未填写|初始为空")
_META_RE = re.compile(r"<!--\s*memory_meta\s*:\s*(?P<body>.*?)\s*-->", re.S)
_LEGACY_RE = re.compile(r"<!--\s*memory_init\s*:\s*(?P<val>[A-Za-z]+)\s*-->")
_LINE_RE = re.compile(r"^\s*(?:[-*+]|\d+[.、])\s*(?P<field>[^：:（(]{1,24}?)\s*[：:]\s*(?P<rest>.*)$")
_SRC_RE = re.compile(r"[（(]\s*来源\s*[：:]\s*(?P<src>[^）)]*?)\s*[）)]\s*$")
_HEADING_RE = re.compile(r"^\s*#{1,6}\s")
# 文档级 H1（`# 用户记忆画像（MEMORY）`）：用它切"块"。注意 `##` 不算（第二个字符是 #）。
_BLOCK_H1_RE = re.compile(r"^#(?!#)\s*\S")


def _norm_field(name: str) -> str:
    """维度名归一：去空白/标点 → 别名映射。未命中的原样返回（白名单外**允许**，由人工确认）。"""
    s = re.sub(r"[\s、，,。.：:;；|]+", "", (name or "").strip())
    s = s.strip("（）()[]【】")
    return FIELD_ALIASES.get(s, s)


def field_status(field: str) -> str:
    """`known`（白名单内或已知别名）/ `new`（白名单外，建议器要标 ⚠️ 默认不勾）。"""
    return "known" if _norm_field(field) in FIELD_WHITELIST else "new"


def today() -> str:
    return _date.today().isoformat()


# ---------------------------------------------------------------- 段落切分
def _find_head_line(lines: list[str], heads: tuple[str, ...]) -> int:
    for i, ln in enumerate(lines):
        t = ln.strip()
        if any(t.startswith(h) or t == h for h in heads):
            return i
    return -1


def split_sections(content: str) -> dict:
    """把 MEMORY.md 切成 `{head, profile, notes, ok}`。

    · `head`    = 画像标题之前（含元数据标记）；
    · `profile` = `## USER PROFILE` 段正文（不含小标题行）；
    · `notes`   = `## MEMORY` 段正文（**原样保留**，它不参与版本化与结构化）。
    找不到画像段时 `ok=False`（调用方据此回退到"整份重写"的保守路径）。
    """
    text = (content or "").replace("\r\n", "\n")
    lines = text.split("\n")
    # ★ 先按 H1 切块（`# 用户记忆画像（MEMORY）`）：块内才是"一份画像"。
    #   历史 bug（2026-09-27 用户实测）：notes 段原来一路 `lines[i_notes+1:]` 取到**文件尾**，
    #   于是把"agent 早先用 write_agent_doc 追加进来的第二份整块"也吞进 notes —— 写回时就变成
    #   新画像在上、旧整块垫在笔记段里，**一份文件两份画像**（用户报障原话："老的被挤到了后面"）。
    starts = [i for i, l in enumerate(lines) if _BLOCK_H1_RE.match(l.strip())]
    bounds = list(zip(starts or [0], (starts[1:] if starts else [len(lines)]) + [len(lines)])) or [(0, len(lines))]
    first = bounds[0]
    i_prof = _find_head_line(lines[first[0]:first[1]], PROFILE_HEADS)
    if i_prof < 0:
        return {"head": text.strip("\n"), "profile": "", "notes": "", "ok": False}
    i_prof += first[0]
    head = "\n".join(lines[:i_prof]).strip("\n")

    def _block_profile(lo: int, hi: int) -> tuple[str, str]:
        k = _find_head_line(lines[lo:hi], PROFILE_HEADS)
        if k < 0:
            return "", ""
        k += lo
        n = -1
        for j in range(k + 1, hi):
            if any(lines[j].strip().startswith(h) or lines[j].strip() == h for h in NOTES_HEADS):
                n = j
                break
        p_txt = "\n".join(lines[k + 1:(n if n > 0 else hi)]).strip("\n")
        n_txt = "\n".join(lines[n + 1:hi]).strip("\n") if n > 0 else ""
        return p_txt, n_txt

    prof_blocks, note_blocks = [_block_profile(*first)[0]], [_block_profile(*first)[1]]
    for lo, hi in bounds[1:]:                      # 后面的块：合并（不再丢、也不再垫在笔记里）
        bp, bn = _block_profile(lo, hi)
        if bp:
            prof_blocks.append(bp)
        if bn:
            note_blocks.append(bn)
    profile = "\n".join(x for x in prof_blocks if x).strip("\n")
    # 笔记去重（同一行不重复出现），保持原顺序
    seen, merged = set(), []
    for blk in note_blocks:
        for ln in blk.split("\n"):
            key = ln.strip()
            if key and key not in seen:
                seen.add(key)
                merged.append(ln)
    notes = "\n".join(merged).strip("\n")
    return {"head": head, "profile": profile, "notes": notes, "ok": True}


# ---------------------------------------------------------------- 元数据
def parse_meta(content: str) -> dict:
    """读元数据。新标记缺失时用老 `memory_init` 兜底（`init` 用文件里能找到的信息）。"""
    text = content or ""
    m = _META_RE.search(text)
    out = {"init": "", "refreshed": "", "sources": 0, "counts": [], "has_meta": bool(m)}
    if m:
        for part in m.group("body").split():
            if "=" not in part:
                continue
            k, _, v = part.partition("=")
            k = k.strip()
            if k in ("init", "refreshed"):
                out[k] = v.strip()
            elif k == "sources":
                try:
                    out["sources"] = int(v.strip() or 0)
                except ValueError:
                    out["sources"] = 0
            elif k == "counts":
                # 各来源条数的水位（M3-5：用户域那几张表**没有时间戳**，只能按条数差算"自上次刷新以来新增了什么"）
                try:
                    out["counts"] = [int(x) for x in v.strip().split(",") if x.strip() != ""]
                except ValueError:
                    out["counts"] = []
    lm = _LEGACY_RE.search(text)
    out["legacy"] = (lm.group("val").strip().lower() if lm else "")
    out["initialized"] = out["legacy"] == "done" or bool(out["has_meta"])
    return out


def render_meta(meta: dict) -> str:
    """渲染新元数据行（老 `memory_init` 行由 `render_file` 单独维护，两行并存）。"""
    meta = meta or {}
    line = "<!-- memory_meta: init=%s refreshed=%s sources=%d" % (
        (meta.get("init") or "").strip() or "-",
        (meta.get("refreshed") or "").strip() or "-",
        int(meta.get("sources") or 0))
    counts = meta.get("counts")
    if counts:
        line += " counts=%s" % ",".join(str(int(x)) for x in counts)
    return line + " -->"


# ---------------------------------------------------------------- 行解析 / 渲染
def parse_rows(profile_text: str, dedupe: bool = False) -> list[dict]:
    """画像段 → 结构化行。宽容：半角冒号、无来源、多个来源片段都能收。

    `dedupe=True` 时按维度**只留先出现的那个** —— 给**写路径**用（文件被写花时同一维度会重复，
    写回必须是"一维度一行"；我们的写路径把当前状态放最上面，所以先出现的就是最新值）。
    默认 False：解析要忠实，别在读取环节悄悄并掉用户的变体写法。
    """
    rows: list[dict] = []
    for raw in (profile_text or "").replace("\r\n", "\n").split("\n"):
        ln = raw.strip()
        if not ln or _HEADING_RE.match(ln):
            continue
        m = _LINE_RE.match(ln)
        if not m:
            continue                      # 段落说明文字（如模板里那句"（初始为空…）"）不算行
        field_raw = m.group("field").strip()
        # 建议器对白名单外的维度会在维度名前加 ⚠️（方案 §5.1）→ 剥掉标记但记下来
        flagged = bool(re.match(r"^\s*(?:⚠️|⚠|\[新维度\]|【新维度】)", field_raw))
        field_raw = re.sub(r"^\s*(?:⚠️|⚠|\[新维度\]|【新维度】)\s*", "", field_raw).strip()
        rest = m.group("rest").strip()
        source = ""
        sm = _SRC_RE.search(rest)
        if sm:
            source = sm.group("src").strip()
            rest = rest[:sm.start()].strip()
        elif re.search(r"[（(]\s*人工\s*[）)]\s*$", rest):
            # 短标记 `（人工）`：render_row 只对人工行写这个（文件里不再写来源 id）
            source = "人工"
            rest = re.sub(r"[（(]\s*人工\s*[）)]\s*$", "", rest).strip()
        rest = re.sub(r"\*\*?", "", rest).strip()      # 建议器偶尔带 markdown 星号，剥掉（用户实测）
        fact, when = rest, ""
        if "·" in rest:                   # 兼容"事实 · 2026-09-24"的写法
            head, _, tail = rest.rpartition("·")
            if re.fullmatch(r"\s*\d{4}-\d{2}-\d{2}\s*", tail):
                fact, when = head.strip(), tail.strip()
        s_when = ""
        if source and "·" in source:
            source, _, s_when = source.rpartition("·")
            source, s_when = source.strip(), s_when.strip()
        rows.append({
            "field": re.sub(r"\*\*?", "", field_raw).strip(), "norm": _norm_field(field_raw),
            "fact": fact, "flagged": flagged,
            "source": source, "date": s_when or when,
            "manual": source == MANUAL_SOURCE, "raw": ln,
            "field_status": field_status(field_raw),
        })
    # ★ 按维度去重（**先出现者为准**）：文件被写花时（历史 bug：一份文件两份画像）同一维度会重复出现；
    #   我们的写路径把"当前状态"放在最上面，所以先出现的就是最新值。
    if not dedupe:
        return rows
    seen, uniq = set(), []
    for r in rows:
        if r["norm"] in seen:
            continue
        seen.add(r["norm"])
        uniq.append(r)
    return uniq


def render_row(row: dict) -> str:
    """一行 → `- 维度：事实`（**人工行**额外带一个短标记 `（人工）`）。

    ★ 为什么不把 `来源`/`日期` 写进文件（2026-09-27 用户实测后的修正，**覆盖方案 §5.2/G5 的写法**）：
      来源里的 `关注#4 / 收藏#12` 是**给人在建议面板里溯源用的**，写进画像文件后会被原样注入模型上下文——
      而 agent 没有跨会话的表引用知识，那串 id 对它就是纯噪声（用户原话："会造成污染"）。
      来源与日期仍然**保留在结构化层**（API 响应 / 建议面板 / 快照），溯源不丢。
      唯一必须留在文件里的是**人工标记**：`upsert(protect_manual=True)` 靠它识别"用户手写的行"，去掉就等于
      丢掉 D8 的人工行保护（这是它唯一的、不可替代的作用）。
    """
    src = (row.get("source") or "").strip()
    tail = "（人工）" if (row.get("manual") or src == MANUAL_SOURCE) else ""
    return "- %s：%s%s" % ((row.get("field") or "").strip(), (row.get("fact") or "").strip(), tail)


def render_rows(rows: list[dict]) -> str:
    return "\n".join(render_row(r) for r in rows if (r.get("fact") or "").strip())


def has_placeholder(text: str) -> bool:
    """占位/空话判定（R3）。注意"暂无"是**被禁止的写法**，不是合法内容。"""
    return bool(PLACEHOLDER_RE.search(text or ""))


# ---------------------------------------------------------------- 就地更新（修 R2 的核心）
def upsert(rows: list[dict], field: str, fact: str, *, source: str = "", date: str = "",
           protect_manual: bool = True) -> tuple[list[dict], str]:
    """按维度**就地更新**：同一维度只有一行，新维度才追加。返回 `(rows, action)`。

    action ∈ `add` / `update` / `same`（值没变）/ `skip_manual`（人工行，受保护）。
    ★ 这是"不追加成瘾"（G2）与"人工行永不动"（D8）两条判据的实现处。
    """
    fact = (fact or "").strip()
    if not fact:
        return rows, "empty"
    norm = _norm_field(field)
    out = [dict(r) for r in rows]
    for r in out:
        if r.get("norm") != norm:
            continue
        if r.get("manual") and protect_manual:
            return out, "skip_manual"
        if (r.get("fact") or "").strip() == fact and (r.get("source") or "").strip() == (source or "").strip():
            return out, "same"
        r["fact"] = fact
        if source:
            r["source"] = source
        if date:
            r["date"] = date
        r["manual"] = (r.get("source") or "").strip() == "人工"
        return out, "update"
    out.append({"field": (field or "").strip(), "norm": norm, "fact": fact,
                "source": source, "date": date or today(),
                "manual": (source or "").strip() == "人工",
                "field_status": field_status(field)})
    return out, "add"


def drop_placeholders(rows: list[dict]) -> tuple[list[dict], list[dict]]:
    """把占位行摘出来（老文件迁移用：以 `−` 呈现给人工确认，**不静默删**）。"""
    kept, dropped = [], []
    for r in rows:
        (dropped if has_placeholder(r.get("fact") or "") else kept).append(r)
    return kept, dropped


# ---------------------------------------------------------------- 整文件写回
def render_file(content: str, rows_or_text, meta: dict | None = None) -> str:
    """把画像段替换成本次的行/文本，**原样保留** head 与笔记段；同时维护两行元数据。

    · `rows_or_text` 传 list[dict] → 走 render_rows；传 str → 直接用（AI 的整段输出）。
    · 老的 `<!-- memory_init: … -->` 会被改成 `done`（`_memory_initialized()` 保持不变）；
      新的 `<!-- memory_meta: … -->` 插在它后面（两行并存）。
    """
    parts = split_sections(content)
    body = render_rows(rows_or_text) if isinstance(rows_or_text, list) else (rows_or_text or "").strip()
    head = parts["head"]
    meta_line = render_meta(meta or {})
    if _META_RE.search(head + "\n" + body):
        head = _META_RE.sub(lambda _m: meta_line, head)
    else:
        legacy_m = _LEGACY_RE.search(head)
        if legacy_m:
            head = head[:legacy_m.end()] + "\n" + meta_line + head[legacy_m.end():]
        else:
            head = head.rstrip() + "\n\n" + meta_line
    if "memory_init" in head:
        head = _LEGACY_RE.sub("<!-- memory_init: done -->", head)
    head = head.strip("\n")
    notes = parts["notes"] or "（Agent 在对话中自行维护：你的长期偏好、未决问题、重要事实等。）"
    return "%s\n\n%s\n\n%s\n\n%s\n" % (head, PROFILE_HEAD, body or "（暂无画像内容）", NOTES_HEAD + "\n\n" + notes)
