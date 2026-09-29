"""M5b · 统一能力目录（能力池）—— 路由用的「工具目录」单一事实源。

设计口径（见 `docs/v2.0/M5b工具检索路由.md` §二）：

  ① **统一契约**：路由与注入只认 `CapabilityEntry`（source/ref/name/keywords/abilities/invoke_hint），
     不感知来源是 CLI、API 还是 MCP——以后接第四种来源只需再写一个适配器，路由器一行不改。
  ② **派生而非迁移**：CLI 的能力描述已经存在 `user_clis.abilities`，**不搬家**；
     写入 `user_clis` 的同时 upsert 一条 `tool_capabilities(source='cli')`；
     `rebuild_pool()` 可从来源表整体重建（改完适配器逻辑跑一次即对齐）。
  ③ **单一事实源**：文本在 SQLite（`tool_capabilities`），向量在 Chroma 独立 collection
     `tool_route_{account_id}`（与 M3 的 `kb_user_*` 同款账号隔离），**向量只存引用不存文本**。
  ④ **版本失效**：任何写入口 → `bump_pool_version()`；路由器按 `(account_id, version)` 缓存，不用 TTL。

本模块**只做目录**，不做路由/不做注入（那是 `tools/tool_router.py` 的事）。
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Iterable

from tools.schema_personal import get_personal_conn

# ---------------------------------------------------------------- 常量
SOURCE_CLI = "cli"
SOURCE_API = "api"
SOURCE_MCP = "mcp"
SOURCES = (SOURCE_CLI, SOURCE_API, SOURCE_MCP)

VECTOR_COLLECTION = "tool_route_{account_id}"   # 路由专用 collection（与知识库隔离）
COMPACT_KEYWORD_LIMIT = 160                      # 紧凑行里关键词截断长度

_KEYWORDS_PREFIX = re.compile(r"^\s*关键词\s*[:：]\s*")

# ---------------------------------------------------------------- M5c：泛化词（词法路的毒源）
# 实测（2026-09-27，基准 28 条池 / 30 题）：CLI 的 keywords 里写着「**今天**B站在火什么」这类
# **问句模板**，于是「今天几号」这种完全无关的问句也能让 `cli:bili` 拿到 1.0 词法分；而短语命中
# 权重是 3.0、`LEX_MIN=2`（`tools/tool_router.py`）→ **一个泛化词命中就足以判"自信"**，
# 把向量路正确的 top1 在 RRF 里顶掉（噪声假阳性 50%）。
# 这批词**跨领域毫无区分度**，出现在关键词/问句里只制造假命中 → 词法路先剔除它们再算重叠。
GENERIC_TOKENS = frozenset("""
今天 昨天 明天 现在 最近 目前 当前 刚才 马上 以前 以后 时间 时候 地方 东西 情况
我的 我在 我们 你们 他们 自己 这个 那个 这些 那些 一个 一些 一下 什么 怎么 怎样 如何
为什么 哪个 哪些 哪里 是否 有没有 是不是 能不能 可以 可否 能否 需要 想要 希望
帮我 请问 麻烦 谢谢 一个 我 你 他 她 它 吗 呢 吧 啊
查询 查找 搜索 获取 查看 看看 得到 返回 提供 支持 用于 使用 用法 功能 方法
相关 信息 内容 数据 结果 参数 说明 描述 示例 例子 注意 提示 默认 自动 文档 接口
服务 工具 列表 详情 详情页 全部 所有 常见 一般 通常 主要 简单 快速
the a an for with that this from by in on of to and or is are be do does did
how what when where which who whom whose you your yours me my mine it its we our
help please query search get find list info information data result results support
use used using example examples note notes default doc docs api tool tools
""".split())

_TOKEN_SPLIT = re.compile(r"[\s/、，,。；;：:!！?？()（）\[\]【】\"'“”‘’|]+")
_TOKEN_TRIM = "*-=~·•\t`"

# 派生关键词的**最小片段长度**。为什么不是 2：机械切出来的 2 字片段太容易是通用词
# （实测踩过：`pdd/search_goods` 派生词里有「排序」→「用 python 写个快速排序」命中短语得 3.0 分
# 被误路由）。3 字以上才允许进关键词表；被丢掉的 2 字片段仍会通过 **2-gram 路**（权重 1）计入，
# 不足以单独越过 `LEX_MIN`，安全。
DERIVED_MIN_CHARS = 3

# 切分点：泛化词 + 虚词/连接词。**只用于派生关键词时切句子**（不当停用词删单词，
# 避免把「到站」「书架」这类正常词切碎），例如：
#   「查询指定城市的所有火车站名」→ 指定城市 / 火车站名      （而不是整句当一个关键词）
_SPLITTERS = frozenset("""
的 了 是 在 和 与 或 及 等 把 被 而 就 也 都 并 且 因 由 使 让 给 对 从 到 为 以 按 用 该 其 此
以及 或者 一个 一些 用于 用来 尚且 目前 例如 比如 包括 适用 场景 可选 只能 主要 需要 支持
根据 基于 关于 基本 其中 以下 以上 等等 然后 如果 因为 所以 但是 不过 只是 就是 这样 那样
""".split()) | GENERIC_TOKENS


def _split_on_generic(seg: str) -> list[str]:
    """按泛化词/虚词把长片段切成小块（保序，保留非空块）。"""
    parts = [seg]
    for g in sorted(_SPLITTERS, key=len, reverse=True):        # 长词优先，避免「的所有」被「的」先切开
        if len(g) < 2 and g not in _SPLITTERS:
            continue
        nxt: list[str] = []
        for p in parts:
            if g.isascii():
                nxt.extend(re.split(r"\b%s\b" % re.escape(g), p))
            else:
                nxt.extend(p.split(g))
        parts = [x for x in nxt if x]
    return [p.strip(_TOKEN_TRIM).strip() for p in parts if p.strip(_TOKEN_TRIM).strip()]


def derive_keywords(text: str, limit: int = 8) -> str:
    """从工具级描述里**派生**关键词 —— 只在该工具/来源都没写关键词时兜底。

    为什么需要：实测 28 条池里**只有 3 条 CLI 写了 keywords，25 条 api/mcp 全空**，
    而 `lexical_scores` 只看 `name + keywords` → 词法路（本项目最可靠的信号）
    对 25/28 的条目**完全无信号**，路由只能靠被长描述稀释的向量分。

    规则刻意保守（宁可少、不要泛）：
      · 只取描述**前两句**；
      · 按标点/空白切片段，**再按泛化词/虚词把长片段切碎**（`_SPLITTERS`）；
      · 只留 2~8 字的块，丢掉纯数字、纯泛化词、纯符号；
      · 保序去重，上限 `limit` 条。
    人工写的关键词永远优先（本函数只在为空时触发）。
    """
    txt = re.sub(r"\s+", " ", str(text or "")).strip()
    if not txt:
        return ""
    head = " ".join(re.split(r"[。.;；\n]", txt)[:2])
    out: list[str] = []
    seen: set[str] = set()
    for seg in _TOKEN_SPLIT.split(head):
        pieces = _split_on_generic(seg)
        # 切开了就**只取切开的块**（否则「人口等」这种带尾缀的半句会先被收进去）。
        # ⚠️ 只在"切成了多块"时才用切开的块。曾经改成"只要切过就用"（哪怕只剩一块，
        #    也就是把「查询12306余票信息」剥成「12306余票信息」）—— **基准实测是负收益**：
        #    Z_STRONG 2.0 下 噪声假阳性 0.167 → 0.333、灰区 0.300 → 0.333（precision/recall/hit 不变）,
        #    因为多出来的关键词会改变向量文本、把个别条目顶成离群高分。以数据为准，保持现状。
        for piece in (pieces if len(pieces) > 1 else [seg]):
            piece = (piece or "").strip(_TOKEN_TRIM)
            if not (DERIVED_MIN_CHARS <= len(piece) <= 8) or piece.isdigit():
                continue
            if not strip_generic(piece):          # 整块都是泛化词
                continue
            if piece in seen:
                continue
            seen.add(piece)
            out.append(piece)
        if len(out) >= limit:
            break
    return "/".join(out[:limit])


def strip_generic(text: str) -> str:
    """把泛化词从文本里抹掉（中文按子串、英文按整词）—— 词法路与关键词派生共用。

    ⚠️ 不要用它处理"要展示给模型/用户"的文本，只用于**路由打分**。
    """
    t = str(text or "").lower()
    if not t:
        return ""
    for g in GENERIC_TOKENS:
        if len(g) < 2:
            continue
        if g.isascii():
            t = re.sub(r"\b%s\b" % re.escape(g), " ", t)
        else:
            t = t.replace(g, " ")
    return re.sub(r"\s+", " ", t).strip()


# ---------------------------------------------------------------- 统一契约
@dataclass(frozen=True)
class CapabilityEntry:
    """一条「可用于完成用户请求」的能力（不管它来自 CLI / API / MCP）。"""

    source: str
    ref: str
    name: str
    keywords: str = ""
    abilities: str = ""
    invoke_hint: str = ""
    enabled: bool = True
    # 来源侧的细节（渲染卡片时用；不进契约语义，其它来源可为空）
    rules: tuple[tuple[str, ...], ...] = ()      # CLI：只读命令路径
    invokable: bool = True                       # 是否真的可被 Agent 调用（清单为空等 → False）
    # ===== v3.0 M1：api / mcp 来源需要的四个字段（默认值保证 CLI 侧行为与文本零改动）=====
    input_schema: dict = field(default_factory=dict)   # JSON Schema（api/mcp 有；CLI 为空）
    invoke_spec: dict = field(default_factory=dict)    # 执行侧元数据（模型不可见）
    read_only: bool = True                             # False = 会改外部状态
    confirmed: bool = True                             # 人工确认过（CLI 恒 True：它走只读清单放行）
    # ===== M5c：**工具级**原文（用户手写优先，其次自动摘要）=====
    # 为什么单独留一份：`abilities` 是「工具级 + 来源级」合并后的**卡片**文本；
    # 需要单独看/单独渲染工具级那一层时用它（例如将来按层展示、排查路由）。
    # ⚠️ 曾经在 M5c-2 尝试让它单独当向量文本 → 基准实测负收益（噪声假阳性 0.333→0.500）已回退，
    #    向量文本继续用合并后的 `abilities`。别再改回去，理由见 `_entry_text` 的 docstring。
    # ★ 默认空串 → CLI 条目不受影响（向量文本与 M5b 时代逐字一致，有回归断言钉着 content hash）。
    abilities_tool: str = ""

    # ---------------- 渲染器：三种形态（路由器按相似度分层使用） ----------------
    @property
    def command_count(self) -> int:
        return len(self.rules)

    @property
    def first_keyword_line(self) -> str:
        """取能力描述里的第一行有效文本 —— 紧凑行的路由信号。

        注意去掉「关键词：」这类前缀（用户写法不统一：有的写 `关键词：A/B/C`，有的直接写 `A、B、C`）。
        """
        for ln in (self.abilities or "").splitlines():
            ln = " ".join(ln.split())
            if ln:
                return _KEYWORDS_PREFIX.sub("", ln).strip()
        return ""

    @property
    def is_cli(self) -> bool:
        return self.source == SOURCE_CLI

    @property
    def param_summary(self) -> str:
        """参数摘要（api/mcp 用；CLI 恒为空）：`参数: city(必填,path), lang(可选,query)`。

        既是路由信号（"按城市查天气" ↔ `city` 参数），也是模型调用时的用法提示。
        MCP 的来源没有 `bindings`（那是 OpenAPI 导入器的产物）→ 回落到 `input_schema.properties`
        （MCP 的 `inputSchema` 本身就是 JSON Schema），否则 MCP 卡片的参数行会是空的。
        """
        binds = (self.invoke_spec or {}).get("bindings") or []
        parts: list[str] = []
        for b in binds:
            if not isinstance(b, dict) or not b.get("name"):
                continue
            parts.append("%s(%s,%s)" % (b["name"], "必填" if b.get("required") else "可选",
                                        b.get("location") or "query"))
        if not parts:
            schema = self.input_schema or {}
            props = schema.get("properties") or {}
            req = set(schema.get("required") or [])
            for name in list(props)[:12]:
                if str(name).startswith("_"):        # 内部字段（如固定 query 的登记项）不上卡片
                    continue
                t = str((props.get(name) or {}).get("type") or "any")
                parts.append("%s(%s,%s)" % (name, "必填" if name in req else "可选", t))
        return ("参数: " + ", ".join(parts[:12])) if parts else ""

    def _head(self) -> str:
        if not self.is_cli:
            kind = "API" if self.source == SOURCE_API else "MCP"
            return f"- {self.name}（{kind} 能力 `{self.ref}`）"
        return f"- {self.name}（可执行名 `{self.ref}`）"

    def _call_example(self) -> str:
        """给模型一行**可直接照抄**的调用示例。

        CLI 卡片一直带示例（`例如 search 关键词`），API/MCP 卡片最初没有 → 模型要自己推"ref 怎么摆、
        参数放哪、要不要引号"，这部分推理会体现在"选择工具"的耗时里。**这是怀疑点、不是实测结论**，
        由用户用简单接口（`api/ip`）做对照实验验证。参数值只给占位，避免模型照抄假数据。
        """
        schema = self.input_schema or {}
        props = schema.get("properties") or {}
        req = list(schema.get("required") or [])
        # 必填参数优先；★ 没有必填、但有参数时，给**第一个参数**占位 —— 否则示例会退化成
        # `params={}`，模型不知道要传什么（实测：小虫API 的 name/appid 都是"二选一"，示例成了空对象）。
        picks = [n for n in props if n in req] or list(props)[:1]
        pairs = []
        for name in picks:
            spec = props.get(name) or {}
            t = str(spec.get("type") or "string")
            if spec.get("enum"):
                val = json.dumps(spec["enum"][0], ensure_ascii=False)
            else:
                val = json.dumps({"integer": 0, "number": 0, "boolean": True}.get(t, "…"), ensure_ascii=False)
            pairs.append('"%s": %s' % (name, val))
        return '  → 调用示例：invoke_tool(ref="%s", params={%s})' % (self.ref, ", ".join(pairs))

    def _remote_card(self) -> str:
        """api/mcp 的完整卡（与 CLI 的措辞分开，否则会说成"N 条只读命令"）。"""
        kind = "API" if self.source == SOURCE_API else "MCP"
        lines = [f"{self._head()}：用户自己配好的 {kind} 能力；问到相关需求时"
                 f"**用 `invoke_tool` 取真实数据**，不要凭自己的知识作答。"]
        flat = " ".join((self.abilities or "").split())
        if flat:
            lines.append("  " + flat)
        ps = self.param_summary
        if ps:
            lines.append("  " + ps)
        lines.append(self._call_example())
        if not self.read_only:
            lines.append("  ⚠️ 该能力会改外部状态（非只读）：只有在用户明确要求时才调用。")
        return "\n".join(lines)

    @property
    def card_text(self) -> str:
        """完整能力卡 —— 相似度高时注入。"""
        if not self.is_cli:
            return self._remote_card()
        sample = [t for t in self.rules[:6]]
        sample_txt = " / ".join(" ".join(t) for t in sample) + (" / …" if len(self.rules) > 6 else "")
        if not (self.abilities or "").strip():
            return (f"{self._head()}：可代跑 {self.command_count} 条只读命令"
                    + (f"，例如 {sample_txt}" if sample_txt else "（清单为空，暂不可代跑）"))
        flat = " ".join(self.abilities.split())
        return (f"{self._head()}：用户问到下列任一场景时，**先用它取真实数据**"
                f"（这是用户自己账号里的私有数据，网搜/知识库都拿不到）；"
                f"不要改用别的工具顶替，也不要凭你自己的知识作答——\n"
                f"  {flat}\n"
                f"  共 {self.command_count} 条只读命令，例如 {sample_txt}")

    @property
    def compact_text(self) -> str:
        """紧凑行 —— 相似度中等时注入（保住关键词，细节让位）。"""
        if not self.is_cli:
            kw = self.first_keyword_line
            ps = self.param_summary
            tail = ("；关键词：" + kw[:COMPACT_KEYWORD_LIMIT]) if kw else ""
            return f"{self._head()}{('；' + ps) if ps else ''}{tail}"
        kw = self.first_keyword_line
        tail = ("；关键词：" + kw[:COMPACT_KEYWORD_LIMIT]) if kw else ""
        return f"{self._head()}：共 {self.command_count} 条只读命令{tail}"

    @property
    def nameonly_text(self) -> str:
        """仅名称行 —— 相似度低但**仍要可见**（工具从提示词里隐身 = 用户配了也白配）。"""
        if not self.is_cli:
            return self._head()
        return f"{self._head()}：共 {self.command_count} 条只读命令"


# ---------------------------------------------------------------- 适配器：CLI
def cli_entry(c: dict) -> CapabilityEntry:
    """把 `cli_registry.list_clis()` 的一条记录适配成能力条目（纯函数，便于单测）。"""
    rules = tuple(tuple(t) for t in (c.get("rules") or []))
    abilities = (c.get("abilities") or "").strip()
    keywords = ""
    m = _KEYWORDS_PREFIX.match(abilities)
    if m:
        # 关键词行取到句末（。/；/换行 之前）
        rest = abilities[m.end():]
        keywords = re.split(r"[。；;\n|]", rest, maxsplit=1)[0].strip()
    return CapabilityEntry(
        source=SOURCE_CLI,
        ref=str(c.get("bin") or ""),
        name=str(c.get("name") or c.get("bin") or ""),
        keywords=keywords,
        abilities=abilities,
        invoke_hint=f"{c.get('bin')} <子命令> [参数]",
        enabled=bool(c.get("live")),          # 未接入 / 清单为空 → 不进池（fail closed 口径）
        rules=rules,
        invokable=bool(rules),
    )


def _entries_from_clis(account_id: int) -> list[CapabilityEntry]:
    from tools import cli_registry as cli_reg
    return [cli_entry(c) for c in cli_reg.list_clis(account_id)]


def _json_or_empty(txt) -> dict:
    try:
        d = json.loads(txt or "{}")
        return d if isinstance(d, dict) else {}
    except Exception:  # noqa: BLE001
        return {}


def _entry_from_row(row: dict, source_row: dict | None, source: str) -> CapabilityEntry:
    """把 `tool_capabilities` 的一条记录适配成能力条目（纯函数，便于单测）。api / mcp 共用。

    - `enabled` 口径（方案 §4.6）：**未人工确认 → 不进池**（同时要求来源本身 enabled）；
    - `abilities` 为空时回落到来源级的整体说明（用户在来源上写的那段人话）。
    """
    spec = _json_or_empty(row.get("invoke_spec"))
    schema = _json_or_empty(row.get("input_schema"))
    src = source_row or {}
    src_enabled = bool(src.get("enabled", 1)) if src else True
    confirmed = bool(row.get("confirmed_at"))
    cap_auto = (row.get("abilities") or "").strip()         # 工具级**自动**摘要：OpenAPI summary / MCP 服务自述
    cap_user = (row.get("abilities_user") or "").strip()    # ★ M5c-2'：用户手写的工具级描述（有就顶掉自动摘要）
    cap_ab = cap_user or cap_auto
    src_ab = (src.get("abilities") or "").strip()          # 来源级：**用户按自己的话写的**
    # ★ 两句都要（M1 真机验证发现）：原来只在工具级为空时才回退到来源级 → 用户在来源上精心写的
    #   「能力描述」（他"会怎么问"的那句话）**完全不起作用**，卡片上只有文档里的技术名（如
    #   "查询Steam游戏资料"）→ 路由命中率受影响。这里合并：技术名在前，用户的话在后。
    if src_ab and src_ab not in cap_ab:
        abilities = ("%s；%s" % (cap_ab, src_ab)) if cap_ab else src_ab
    else:
        abilities = cap_ab or src_ab
    keywords = (row.get("keywords") or "").strip()
    if not keywords:
        # ★ 关键词来源优先级：工具级（用户/AI 写的，可能以「关键词：」开头）→ 来源级 → 从描述派生
        for cand in (cap_user, src_ab or abilities):
            m = _KEYWORDS_PREFIX.match(cand or "")
            if m:
                keywords = re.split(r"[。；;\n|]", (cand or "")[m.end():], maxsplit=1)[0].strip()
                if keywords:
                    break
    if not keywords:
        # ★ M5c：来源与工具都没写关键词 → 从**工具级描述**派生一份兜底。
        #   实测（基准 28 条池）：25 条 api/mcp 的 keywords 全空 → `lexical_scores` 只看
        #   `name + keywords` → 词法路（本项目最可靠的信号）对它们**完全无信号**。
        #   只在为空时触发，人工写的永远优先；CLI 侧不动（那边关键词是用户手写的，且有逐字冻结断言）。
        keywords = derive_keywords(cap_ab or abilities)
    return CapabilityEntry(
        source=source,
        ref=str(row.get("ref") or ""),
        name=str(row.get("name") or row.get("ref") or ""),
        keywords=keywords,
        abilities=abilities,
        abilities_tool=cap_ab,        # ★ M5c：向量文本用工具级原文，不用上面合并后的卡片文本
        invoke_hint=str(row.get("invoke_hint") or ""),
        enabled=bool(row.get("enabled", 1)) and confirmed and src_enabled,
        rules=(),
        invokable=confirmed,
        input_schema=schema,
        invoke_spec=spec,
        read_only=bool(row.get("read_only", 1)),
        confirmed=confirmed,
    )


def api_entry(row: dict, source_row: dict | None = None) -> CapabilityEntry:
    """api 来源的能力条目（M1）。"""
    return _entry_from_row(row, source_row, SOURCE_API)


def mcp_entry(row: dict, source_row: dict | None = None) -> CapabilityEntry:
    """mcp 来源的能力条目（M2）。**与 api 同一套判定**，只差 kind 文案与 ref 形状。"""
    return _entry_from_row(row, source_row, SOURCE_MCP)


def _entries_from_remote(account_id: int, source: str) -> list[CapabilityEntry]:
    """从 `tool_sources` + `tool_capabilities` 派生（api / mcp 共用一套逻辑）。

    两表在 Python 里做联接（不用 `json_extract` 的 SQL 联接）——`invoke_spec` 是 JSON 文本，
    用 SQL 取会因脏数据整条查询失败；分两次读、逐行解析更耐操。
    """
    aid = int(account_id)
    conn = get_personal_conn()
    try:
        srows: dict[int, dict] = {}
        for r in conn.execute("SELECT * FROM tool_sources WHERE account_id=? AND source=?", (aid, source)):
            d = dict(r)
            try:
                srows[int(d["id"])] = d
            except Exception:  # noqa: BLE001
                continue
        crows = [dict(r) for r in conn.execute(
            "SELECT * FROM tool_capabilities WHERE account_id=? AND source=?", (aid, source))]
    finally:
        conn.close()
    out: list[CapabilityEntry] = []
    for row in crows:
        spec = _json_or_empty(row.get("invoke_spec"))
        sid = spec.get("source_id")
        src = srows.get(int(sid)) if str(sid or "").isdigit() else None
        out.append(_entry_from_row(row, src, source))
    return out


def _entries_from_apis(account_id: int) -> list[CapabilityEntry]:
    return _entries_from_remote(account_id, SOURCE_API)


def _entries_from_mcps(account_id: int) -> list[CapabilityEntry]:
    """MCP 来源的条目（v3.0 M2）。"""
    return _entries_from_remote(account_id, SOURCE_MCP)


# ---------------------------------------------------------------- 适配器登记表（接新来源只加一行）
_ADAPTERS = {
    SOURCE_CLI: _entries_from_clis,
    SOURCE_API: _entries_from_apis,   # v3.0 M1 接入
    SOURCE_MCP: _entries_from_mcps,   # v3.0 M2 接入
}


# ---------------------------------------------------------------- 读：池条目（派生视图，不读 tool_capabilities）
def pool_entries(account_id: int | None, *, enabled_only: bool = True) -> list[CapabilityEntry]:
    """当前账号的能力池条目。

    ⚠️ 直接**从来源适配器派生**（而非读 `tool_capabilities` 表）——保证「来源表是唯一事实源」：
    用户在 CLI 页改了能力描述，池立刻反映，不存在两张表不同步的窗口。
    `tool_capabilities` 表用于：① 向量文本变更检测（content hash）；② 未来的持久化/审计；
    api/mcp 实装后若来源表读取昂贵，可切到表读 + 版本号失效（接口不变）。
    """
    if not account_id:
        return []
    out: list[CapabilityEntry] = []
    for source, loader in _ADAPTERS.items():
        try:
            out.extend(loader(int(account_id)))
        except Exception as e:  # noqa: BLE001 - 单个来源失败不该让整池不可用
            print(f"[M5b] 能力来源 {source} 读取失败（跳过）: {type(e).__name__}: {e}")
    if enabled_only:
        out = [e for e in out if e.enabled]
    # 去重（同 source+ref 只留一条；来源适配器正常不会重复，防御性处理）
    seen: set[tuple[str, str]] = set()
    uniq: list[CapabilityEntry] = []
    for e in out:
        k = (e.source, e.ref)
        if k in seen:
            continue
        seen.add(k)
        uniq.append(e)
    return uniq


# ---------------------------------------------------------------- 版本（缓存失效）
def current_pool_version(account_id: int) -> int:
    try:
        conn = get_personal_conn()
        try:
            row = conn.execute("SELECT pool_version FROM tool_pool_meta WHERE account_id=?",
                               (int(account_id),)).fetchone()
            return int(row["pool_version"]) if row else 0
        finally:
            conn.close()
    except Exception:  # noqa: BLE001 - 老库没这张表等情况
        return 0


def bump_pool_version(account_id: int | None) -> int:
    """池内容可能变化时调用（工具增删改、状态变更、能力描述编辑）。返回新版本号。"""
    if not account_id:
        return 0
    try:
        conn = get_personal_conn()
        try:
            conn.execute(
                "INSERT INTO tool_pool_meta (account_id, pool_version, updated_at) "
                "VALUES (?, 1, datetime('now','localtime')) "
                "ON CONFLICT(account_id) DO UPDATE SET "
                "  pool_version = pool_version + 1, updated_at = datetime('now','localtime')",
                (int(account_id),))
            conn.commit()
            row = conn.execute("SELECT pool_version FROM tool_pool_meta WHERE account_id=?",
                               (int(account_id),)).fetchone()
            return int(row["pool_version"]) if row else 0
        finally:
            conn.close()
    except Exception as e:  # noqa: BLE001
        print(f"[M5b] 池版本递增失败（忽略）: {type(e).__name__}: {e}")
        return 0


# ---------------------------------------------------------------- 向量：独立 collection
def _account_id_of(username: str | None) -> int:
    if not username:
        return 0
    try:
        conn = get_personal_conn()
        try:
            row = conn.execute("SELECT id FROM accounts WHERE username=?", (username,)).fetchone()
            return int(row["id"]) if row else 0
        finally:
            conn.close()
    except Exception:  # noqa: BLE001
        return 0


def _username_of(account_id: int) -> str:
    """按 account_id 反查 username（路径用 username，与 M3 的 `DB_ROOT/{username}/` 约定一致）。"""
    try:
        conn = get_personal_conn()
        try:
            row = conn.execute("SELECT username FROM accounts WHERE id=?", (int(account_id),)).fetchone()
            return str(row["username"]) if row and row["username"] else f"account_{int(account_id)}"
        finally:
            conn.close()
    except Exception:  # noqa: BLE001
        return f"account_{int(account_id)}"


def get_route_collection(account_id: int):
    """路由专用 Chroma collection（`tool_route_{account_id}`，与知识库隔离）。

    - 目录：`DB_ROOT/{username}/chroma_route`（与 M3 同用户目录、独立子目录，互不干扰）；
    - collection 名只用 account_id（chroma 只允许 `[a-zA-Z0-9._-]`，中文 username 会报 InvalidArgumentError）。
    """
    import chromadb  # lazy

    from rag_knowledge.kb_service import DB_ROOT

    d = DB_ROOT / _username_of(int(account_id)) / "chroma_route"
    d.mkdir(parents=True, exist_ok=True)
    client = chromadb.PersistentClient(path=str(d))
    return client.get_or_create_collection(
        name=VECTOR_COLLECTION.format(account_id=int(account_id)),
        metadata={"hnsw:space": "cosine"})


def _entry_text(e: CapabilityEntry) -> str:
    """用于 embed 的文本：关键词 + 能力描述（**不含命令样例**，避免命令名干扰语义匹配）。

    ★ **M5c-2 试过"只取工具级描述"（`abilities_tool`），实测已回退**（2026-09-27，有基准数字）：
      同池 28 条 / 30 题下，把来源级描述从向量文本里拿掉后 —— precision@3 持平（0.25→0.25），
      但**噪声假阳性 0.333 → 0.500、灰区 0.300 → 0.367 双双变差**。原因：25 条 api/mcp 的工具级描述
      里有一大半是**英文**（如 BNF 8 个工具）或极简（如「查询12306余票信息。」），
      用户写的中文**来源级描述**恰恰是这些条目唯一的中文信号 —— 砍掉它等于把它们变成哑巴。
      **结论：向量文本的收益应来自"给工具补关键词/描述"，不是"砍掉来源级描述"**（见 M5c 文档 §8.2）。
      `abilities_tool` 字段保留（将来若要按来源/工具分层渲染还有用），但**不参与向量文本**。
    """
    parts = [e.name]
    if e.keywords:
        parts.append(e.keywords)
    if e.abilities:
        parts.append(e.abilities)
    if not e.is_cli:
        # api/mcp：补一句参数摘要提升路由信号（"按城市查天气" ↔ city 参数）。
        # CLI 不加，否则会改变既有条目的 content hash → 触发全量向量重算（且有回归断言钉着）。
        ps = e.param_summary
        if ps:
            parts.append(ps)
    return "\n".join(parts).strip()


def _content_hash(text: str) -> str:
    import hashlib
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def sync_vectors(account_id: int, entries: list[CapabilityEntry] | None = None) -> dict:
    """把池条目的向量同步进 collection（只对文本变过的条目重算）。返回统计信息。

    失败不抛异常（向量是**加速层**，缺了它路由器会退回关键词匹配）——返回 `{'ok': False, 'error': …}`。
    """
    if not account_id:
        return {"ok": False, "error": "no account_id"}
    entries = entries if entries is not None else pool_entries(account_id)
    stat = {"ok": True, "added": 0, "updated": 0, "removed": 0, "skipped": 0, "total": len(entries)}
    try:
        col = get_route_collection(account_id)
        existing = {}
        try:
            got = col.get(include=["metadatas"])
            for i, m in zip(got.get("ids") or [], got.get("metadatas") or []):
                existing[i] = (m or {}).get("content_hash", "")
        except Exception:  # noqa: BLE001 - 空 collection
            existing = {}

        from rag_knowledge.kb_service import embedding_model
        model = embedding_model()

        want: dict[str, tuple[CapabilityEntry, str, str]] = {}
        for e in entries:
            txt = _entry_text(e)
            if not txt:
                stat["skipped"] += 1
                continue
            vid = f"{e.source}:{e.ref}"
            want[vid] = (e, txt, _content_hash(txt))

        to_add_ids, to_add_txt, to_add_meta = [], [], []
        for vid, (e, txt, h) in want.items():
            if existing.get(vid) == h:
                stat["skipped"] += 1
                continue
            to_add_ids.append(vid)
            to_add_txt.append(txt)
            to_add_meta.append({"source": e.source, "ref": e.ref, "name": e.name, "content_hash": h})

        if to_add_ids:
            vecs = model.embed_documents(to_add_txt)
            col.upsert(ids=to_add_ids, documents=to_add_txt, embeddings=vecs, metadatas=to_add_meta)
            for vid in to_add_ids:
                if vid in existing:
                    stat["updated"] += 1
                else:
                    stat["added"] += 1

        stale = [i for i in existing if i not in want]
        if stale:
            col.delete(ids=stale)
            stat["removed"] = len(stale)
    except Exception as e:  # noqa: BLE001
        stat["ok"] = False
        stat["error"] = f"{type(e).__name__}: {e}"
    return stat


def _vec_state_path(account_id: int):
    """记录「collection 已同步到哪个池版本」的小状态文件（放在 collection 同目录）。"""
    import chromadb  # noqa: F401 - 仅为保持依赖顺序

    from rag_knowledge.kb_service import DB_ROOT

    d = DB_ROOT / _username_of(int(account_id)) / "chroma_route"
    d.mkdir(parents=True, exist_ok=True)
    return d / "_pool_version.json"


def ensure_vectors_fresh(account_id: int, entries: list[CapabilityEntry] | None = None) -> dict:
    """**惰性**向量同步：collection 的版本落后于池版本时才重建。

    为什么要惰性：向量同步会加载 bge（首次 1~3 分钟）。配置写入路径（保存 CLI）若连带做这件事，
    面板会卡住——所以写入口只落库 + 递增版本，真正的向量重建推迟到路由时刻按版本触发。
    """
    if not account_id:
        return {"ok": False, "error": "no account_id"}
    aid = int(account_id)
    ver = current_pool_version(aid)
    path = _vec_state_path(aid)
    stored = None
    try:
        if path.exists():
            import json as _json
            stored = (_json.loads(path.read_text(encoding="utf-8")) or {}).get("version")
    except Exception:  # noqa: BLE001
        stored = None
    if stored == ver and ver > 0:
        return {"ok": True, "fresh": True, "version": ver}
    stat = sync_vectors(aid, entries)
    if stat.get("ok"):
        try:
            import json as _json
            path.write_text(_json.dumps({"version": ver}), encoding="utf-8")
        except Exception:  # noqa: BLE001
            pass
    stat["version"] = ver
    return stat


def route_scores(account_id: int, question: str) -> dict[str, float]:
    """稠密相似度：{ 'source:ref': similarity }。失败返回 `{}`（调用方退回关键词匹配）。"""
    if not account_id or not (question or "").strip():
        return {}
    try:
        ensure_vectors_fresh(account_id)     # 池变了才重建（惰性）
        col = get_route_collection(account_id)
        if col.count() == 0:
            return {}
        from rag_knowledge.kb_service import embedding_model
        vec = embedding_model().embed_query(question)
        n = min(max(col.count(), 1), 50)
        res = col.query(query_embeddings=[vec], n_results=n)
        out: dict[str, float] = {}
        for i, dist in zip((res.get("ids") or [[]])[0], (res.get("distances") or [[]])[0]):
            # collection 是 cosine 空间 → 距离转相似度
            out[str(i)] = max(0.0, 1.0 - float(dist))
        return out
    except Exception as e:  # noqa: BLE001
        print(f"[M5b] 向量检索失败（退回关键词匹配）: {type(e).__name__}: {e}")
        return {}


# ---------------------------------------------------------------- 重建（幂等）
def rebuild_pool(account_id: int | None, *, with_vectors: bool = True) -> dict:
    """从各来源适配器重建池（幂等）：写 `tool_capabilities` 快照 + 递增版本（可选同步向量）。

    用途：改了适配器逻辑/排查不一致时跑一次即对齐；正常写入路径是 `sync_cli_entry()` 增量同步。
    `with_vectors=False` 时只落库（配置写入路径用这个口径，避免连带加载 bge 卡住界面）。
    """
    if not account_id:
        return {"ok": False, "error": "no account_id"}
    aid = int(account_id)
    entries = pool_entries(aid)
    written = 0
    try:
        conn = get_personal_conn()
        try:
            for e in entries:
                conn.execute(
                    "INSERT INTO tool_capabilities "
                    "(account_id, source, ref, name, keywords, abilities, invoke_hint, enabled, updated_at) "
                    "VALUES (?,?,?,?,?,?,?,?,datetime('now','localtime')) "
                    "ON CONFLICT(account_id, source, ref) DO UPDATE SET "
                    "  name=excluded.name, keywords=excluded.keywords, abilities=excluded.abilities, "
                    "  invoke_hint=excluded.invoke_hint, enabled=excluded.enabled, "
                    "  updated_at=datetime('now','localtime')",
                    (aid, e.source, e.ref, e.name, e.keywords, e.abilities, e.invoke_hint, 1 if e.enabled else 0))
                written += 1
            # 清掉来源已消失的条目
            keep = {(e.source, e.ref) for e in entries}
            rows = conn.execute("SELECT source, ref FROM tool_capabilities WHERE account_id=?",
                                (aid,)).fetchall()
            gone = [(r["source"], r["ref"]) for r in rows if (r["source"], r["ref"]) not in keep]
            for s, r in gone:
                conn.execute("DELETE FROM tool_capabilities WHERE account_id=? AND source=? AND ref=?",
                             (aid, s, r))
            conn.commit()
        finally:
            conn.close()
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}

    vec = sync_vectors(aid, entries) if with_vectors else {"ok": True, "skipped": "with_vectors=False"}
    ver = bump_pool_version(aid)
    return {"ok": True, "entries": len(entries), "written": written, "vectors": vec, "version": ver}


def sync_cli_entry(account_id: int | None) -> None:
    """CLI 写入口的增量同步钩子（save/delete/state 后调用）——UI 改了描述立即生效。

    **只落库 + 递增版本，不同步向量**（向量会加载 bge，界面会被冻住）；
    向量由 `ensure_vectors_fresh()` 在路由时刻按版本惰性重建。
    完全容错：任何异常都只打印，**绝不影响 CLI 配置本身的写入**。
    """
    if not account_id:
        return
    try:
        aid = int(account_id)
        entries = [e for e in _entries_from_clis(aid)]
        conn = get_personal_conn()
        try:
            keep = set()
            for e in entries:
                keep.add((e.source, e.ref))
                conn.execute(
                    "INSERT INTO tool_capabilities "
                    "(account_id, source, ref, name, keywords, abilities, invoke_hint, enabled, updated_at) "
                    "VALUES (?,?,?,?,?,?,?,?,datetime('now','localtime')) "
                    "ON CONFLICT(account_id, source, ref) DO UPDATE SET "
                    "  name=excluded.name, keywords=excluded.keywords, abilities=excluded.abilities, "
                    "  invoke_hint=excluded.invoke_hint, enabled=excluded.enabled, "
                    "  updated_at=datetime('now','localtime')",
                    (aid, e.source, e.ref, e.name, e.keywords, e.abilities, e.invoke_hint, 1 if e.enabled else 0))
            rows = conn.execute("SELECT source, ref FROM tool_capabilities WHERE account_id=? AND source=?",
                                (aid, SOURCE_CLI)).fetchall()
            for r in rows:
                if (r["source"], r["ref"]) not in keep:
                    conn.execute("DELETE FROM tool_capabilities WHERE account_id=? AND source=? AND ref=?",
                                 (aid, r["source"], r["ref"]))
            conn.commit()
        finally:
            conn.close()
        # 不在这里同步向量（会加载 bge 卡住面板）——见 ensure_vectors_fresh()
        bump_pool_version(aid)
    except Exception as e:  # noqa: BLE001
        print(f"[M5b] CLI 能力池同步失败（忽略，不影响配置）: {type(e).__name__}: {e}")


def pool_summary(account_id: int | None) -> dict:
    """池概览（前端/调试用）：条目数、各来源分布、版本号、向量同步状态。"""
    entries = pool_entries(account_id)
    by_source: dict[str, int] = {}
    for e in entries:
        by_source[e.source] = by_source.get(e.source, 0) + 1
    return {
        "count": len(entries),
        "by_source": by_source,
        "version": current_pool_version(int(account_id)) if account_id else 0,
        "refs": [f"{e.source}:{e.ref}" for e in entries],
    }
