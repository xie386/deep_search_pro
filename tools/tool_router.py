"""M5b · 工具检索路由 —— 把「全量注入」换成「相关性注入」。

设计文档：`docs/v2.0/M5b工具检索路由.md` §三。

**它解决什么**：M5a 的注入是「预算分配器」——所有工具一律平铺，装不下就降级；
工具一多，每份额度就小，路由精度随规模下降。这里改成「相关性选择器」：
只把与当前问题相关的工具**完整**注入，其余只留一行名称（不隐身），不确定就整段回退全量。

**实测校准（重要，别改回绝对阈值）**：bge 对中文短问句的余弦相似度**区分度极低**——
3 个 CLI 上四条不同领域问句的 top1/top3 只差 0.02~0.06，且绝对值全落在 0.53~0.70。
所以「相似度 > 0.5 算命中」这种绝对阈值**不可用**（会全部命中，等于没路由）。
改用相对信号：
  ① **z 分数**（top1 相对全体均值的标准差倍数）——随工具数增加而拉大，天然适配规模增长；
  ② **词法命中**（问句与「关键词」行的短语/2-gram 重叠）——中文短问句的强信号，向量弱时兜住；
两者都拿不到信号 → **回退全量简报**（不确定就别猜，这是 fail-safe 而不是 fail-closed 意义上的保守）。
"""
from __future__ import annotations

import math
import os
import re
from dataclasses import dataclass, field

from tools import capability_pool as pool_mod


def _route_provider(account_id: int | None = None) -> str:
    """工具路由**仲裁层**的取值：`local`（默认，现状）/ `decision`（决策模型精排，已实现）。

    ★ 2026-10-13 用户裁定：开关来源改为**账号级 DB 字段 + 前端开关**（`accounts.decision_router`），
    `.env` 不再存"开不开"。取值统一由 `tools/decision_router` 决定，这里只做转发（模块不可用即按 local）。
    """
    try:
        from tools import decision_router as _dr
        return _dr.provider(account_id)
    except Exception:  # noqa: BLE001
        return "local"

# ---------------------------------------------------------------- 可调参数（改动须重跑加压探针）
FAST_PATH_MAX = 4          # 池内条目 ≤ 此数 → 直接走 M5a 全量简报（路由没有收益，省一次 embedding）
MAX_FULL_CARDS = 3         # 最多给几张完整能力卡
COMPACT_TOP_N = 2          # 完整卡之后，再给几条紧凑行
TOTAL_BUDGET_CHARS = 1500  # 注入总量上限（≈750 token）
Z_STRONG = 3.8             # 向量置信阈值：top1 的 z 分数达到此值才算「有明确目标」
# ★ 2026-10-09 **两轮**重标定（用户拍板；依据与实测表见 `docs/v4.0/Done/01-度量口径收口工程·实现记录.md`）：
#   ① 2.4 → 3.3（度量口径收口后）：噪声假阳性 0.143 → 0.000，四个质量指标不变，
#      代价是非噪声灰区 0.087 → 0.217（3 道真命中题改为回退全量）。
#   ② 3.3 → 3.8（与「素材层第三批」同批落地）：素材改动（把站码工具里的城市名示例拿掉）会把
#      `city-climate` 的 z 顶上去（改素材前 3.22 → 冻结池 3.59／真机池 3.62；池内余弦标准差 ≈0.01，
#      这一项本就易跳）。**两个口径分别扫描**后发现平台区不同：
#        · 冻结池（28 条）：3.4 起就全绿，3.4 ~ 4.0 五个值完全一致；
#        · 真机池（29 条）：**3.62 起**才全绿，3.62 ~ 4.2 六个值完全一致。
#      取 **3.8** = 两池都在平台区内、离真机边界（3.62）留 ~0.18 余量，防下次素材微调再次漏过。
#      落地后（两个口径同值）：precision@1 0.739 → **0.870**、hit@3 **1.000**、非噪声灰区
#      0.217 → **0.130**、噪声假阳性 **0.000**。
#   ⚠️ 改这个值必须**两个口径都重跑**（`tests/live/m5c_router_bench.py` + `tests/live/m6b_material_ab.py`，
#      后者要 `--live-pool` 也跑一遍——**冻结池够不代表真机够**，本次就踩过），
#      并同步三处：本注释 / `router_bench_baseline.json` / `router_bench_regressions.json` 的 `baseline_metrics`。
# 历史（2026-09-27）：2.0 → 2.4 —— precision@3 / recall@3 / hit@3 完全不变（0.292 / 0.674 / 0.792），
#   噪声假阳性 0.333 → 0.000，代价是灰区 0.300 → 0.467。（2.0 是 8 工具时期的标定值：无关问句
#   「你是谁」z=1.57、真命中最低 1.65~1.99；池子涨到 28 条后那套数已失效，故按基准扫描重标定。）
LEX_MIN = 2                # 或：top1 的词法命中 ≥ 此值（更可靠的那一路）
RRF_K = 60                 # RRF 融合常数（与 M3 检索栈同口径）

_SPLIT = re.compile(r"[、/，,。；;|｜\s]+")
_HAN = re.compile(r"[^\u4e00-\u9fffA-Za-z0-9]+")


@dataclass
class RouteResult:
    brief: str                       # 注入的动态 SystemMessage 内容（能力区块）
    examples: str                    # few-shot 示例区块（只对命中工具生成）
    mode: str                        # 'full'（快路径/兜底） | 'routed'
    hits: list[str] = field(default_factory=list)    # 命中的 ref（审计/度量用）
    tokens_est: int = 0              # 估算注入量（中文≈字符数×0.5 token）
    detail: dict = field(default_factory=dict)       # 决策细节（调试/度量：分数、z、词法）


# ---------------------------------------------------------------- 词法信号
def _phrase_tokens(text: str) -> list[str]:
    """从「关键词」行里切出短语（用户写的关键词本身就是短语，如 `我在读`、`书评`）。"""
    out = []
    for part in _SPLIT.split(text or ""):
        part = part.strip()
        if len(part) >= 2:
            out.append(part)
    return out


def _char_bigrams(text: str) -> set[str]:
    """中文 2-gram（问句没分词，用它做粗粒度重叠）；英文按词切。"""
    out: set[str] = set()
    for w in _HAN.split((text or "").lower()):
        if not w:
            continue
        if w.isascii():
            if len(w) >= 2:
                out.add(w)
        else:
            out.update(w[i:i + 2] for i in range(len(w) - 1))
    return out


def lexical_scores(question: str, entries: list[pool_mod.CapabilityEntry]) -> dict[str, float]:
    """词法命中分：短语命中权重 3、2-gram 重叠权重 1（只统计 ≥2 字的中文/英文片段）。

    ★ M5c：打分前先剔除**泛化词**（`capability_pool.GENERIC_TOKENS`：今天/我/有没有/查询/信息…）。
    实测踩过：CLI 关键词里写着「**今天**B站在火什么」这类**问句模板** →「今天几号」这个完全无关的
    问句也能让 `cli:bili` 拿到 1.0 分，而短语命中权重 3.0 ≥ `LEX_MIN=2` → **一个泛化词命中就足以
    判"自信"**，把向量路正确的 top1 在 RRF 里顶掉（基准实测噪声假阳性 50%）。
    泛化词跨领域零区分度，剔除后：「今天几号」→「几号」；「今天B站在火什么」→「b站在火」→ 不再假命中。
    只影响**打分**，卡片/提示词里的原文一个字不动。
    """
    q_big = _char_bigrams(pool_mod.strip_generic(question))
    out: dict[str, float] = {}
    for e in entries:
        hay = f"{e.name} {e.keywords}"
        score = 0.0
        for ph in _phrase_tokens(e.keywords):
            if not ph or not ph in (question or ""):
                continue
            if not pool_mod.strip_generic(ph):     # 整个短语都是泛化词（如「今天」「有没有」）→ 丢弃
                continue
            score += 3.0
        score += float(len(q_big & _char_bigrams(pool_mod.strip_generic(hay))))
        out[f"{e.source}:{e.ref}"] = score
    return out


# ---------------------------------------------------------------- 融合与置信
def _rrf(ranks: list[list[str]]) -> dict[str, float]:
    """RRF 融合多路排序（沿用 M3 检索栈口径）。

    ⚠️ **无信号的排序器必须剔除**：词法全为 0 时，若仍把「全是 0 的排序」当一路输入，
    RRF 会给排序里靠前（实为插入顺序/字母序）的工具加分，把向量路正确的 top1 顶掉——
    实测踩过：`我这个月读了多久` 的向量 top1 是 booky，融合后却变成 sched。
    """
    acc: dict[str, float] = {}
    for order in ranks:
        if not order:
            continue
        for i, key in enumerate(order):
            acc[key] = acc.get(key, 0.0) + 1.0 / (RRF_K + i + 1)
    return acc


def _zscore(values: list[float]) -> list[float]:
    if len(values) < 2:
        return [0.0 for _ in values]
    m = sum(values) / len(values)
    var = sum((v - m) ** 2 for v in values) / len(values)
    sd = math.sqrt(var)
    if sd < 1e-9:
        return [0.0 for _ in values]
    return [(v - m) / sd for v in values]


def decide(question: str, entries: list[pool_mod.CapabilityEntry],
           dense: dict[str, float]) -> dict:
    """算分数 + 判定是否「有明确目标」。纯函数（便于单测，不碰 DB/模型）。

    融合只用**有信号**的排序器（见 `_rrf` 注释）；置信判定用的是**两级保守口径**：
    - 词法命中 ≥ `LEX_MIN`（问句里出现了关键词行的短语）→ 算明确；
    - 否则要求向量有**很清楚的赢家**（z ≥ `Z_STRONG`）才算明确；
    - 都不满足 → 交给调用方回退全量简报（保守：宁可多注入，不可猜错）。
    为什么 z 的门槛比早期设想的 0.8 高：8 工具实测中「你是谁 / 帮我写一首诗」这类
    无关问句的 z 也能到 0.89~1.03，与部分真命中（0.95）重叠 → 低门槛会把噪声放进来。
    """
    keys = [f"{e.source}:{e.ref}" for e in entries]
    dense_vals = [float(dense.get(k, 0.0)) for k in keys]
    lex = lexical_scores(question, entries)
    lex_vals = [lex[k] for k in keys]
    ranks = []
    if any(v > 0 for v in dense_vals):
        ranks.append([k for k, _ in sorted(zip(keys, dense_vals), key=lambda kv: -kv[1])])
    if any(v > 0 for v in lex_vals):
        ranks.append([k for k, _ in sorted(zip(keys, lex_vals), key=lambda kv: -kv[1])])
    fused = _rrf(ranks) or {k: 0.0 for k in keys}
    order = sorted(keys, key=lambda k: (-fused.get(k, 0.0), k))
    z_by_key = dict(zip(keys, _zscore(dense_vals)))
    top = order[0] if order else None
    z_top = z_by_key.get(top, 0.0) if top else 0.0
    lex_top = lex.get(top, 0.0) if top else 0.0
    confident = bool(entries) and (lex_top >= LEX_MIN or z_top >= Z_STRONG)
    return {
        "order": order,
        "fused": fused,
        "dense": dict(zip(keys, dense_vals)),
        "z": z_by_key,
        "lexical": lex,
        "top": top,
        "z_top": round(z_top, 3),
        "lex_top": lex_top,
        "confident": confident,
    }


# ---------------------------------------------------------------- 渲染
def _render_routed(entries: list[pool_mod.CapabilityEntry], order: list[str],
                   budget: int = TOTAL_BUDGET_CHARS) -> tuple[str, list[str], list[str]]:
    """按顺序铺三种形态，超预算时依次降级。返回 (文本, 完整卡 refs, 命中顺序)。"""
    by_key = {f"{e.source}:{e.ref}": e for e in entries}
    full_refs: list[str] = []
    blocks: list[str] = []
    used = 0
    for i, key in enumerate(order):
        e = by_key.get(key)
        if e is None:
            continue
        if i < MAX_FULL_CARDS:
            txt = e.card_text
            if used + len(txt) > budget:        # 超预算 → 降级成紧凑行（不丢工具）
                txt = e.compact_text
            else:
                full_refs.append(e.ref)
        elif i < MAX_FULL_CARDS + COMPACT_TOP_N:
            txt = e.compact_text
            if used + len(txt) > budget:
                txt = e.nameonly_text
        else:
            txt = e.nameonly_text
        blocks.append(txt)
        used += len(txt)
    return "\n".join(blocks), full_refs, list(order)


# ---------------------------------------------------------------- 主入口
def route_tools(account_id: int | None, question: str, *,
                force_full: bool = False, entries: list[pool_mod.CapabilityEntry] | None = None,
                allow_decision: bool = True
                ) -> RouteResult:
    """按问题选工具，产出注入文本。

    - 快路径（池 ≤ FAST_PATH_MAX / force_full / 无问题）→ M5a 全量简报，行为与 M5a 逐字一致；
    - 路由路径 → 命中给完整卡、次要给紧凑行、其余只留一行名称 + 只对命中工具给 few-shot；
    - 任何异常 → 回退全量简报（绝不让注入变空）。
    - `allow_decision=False` = **测评请求**（03 路由质量测评走 `/api/chat?probe=1`）：
      **强制关掉决策模型精排**（2026-10-13 用户裁定）——测评要量的是路由器 + 素材层，
      带上决策层就换了仪器、与历史基线不可比。
    """
    from tools import cli_registry as cli_reg

    def _full(reason: str) -> RouteResult:
        brief = cli_reg.agent_brief(account_id)
        ex = cli_reg.ability_examples_block(account_id)
        return RouteResult(brief=brief, examples=ex, mode="full",
                           tokens_est=int((len(brief) + len(ex)) * 0.5), detail={"reason": reason})

    def _full_pool(ents: list, reason: str) -> RouteResult:
        """池里**含非 CLI 来源**时的全量简报：必须从统一池渲染。

        为什么：快路径原来直接用 `cli_reg.agent_brief()`（**CLI 专属**），于是"池小"时
        API/MCP 能力对模型完全隐身——配好了却调不到（真机 e2e 逮到：3 CLI + 1 API = 4 条
        走快路径，注入文本里没有那条 API 能力）。只有全是 CLI 时仍走 `agent_brief`，
        保证与 M5a 逐字一致（回归用例钉着）。
        """
        brief = "\n".join(e.card_text for e in ents)
        ex = cli_reg.ability_examples_block(account_id)
        return RouteResult(brief=brief, examples=ex, mode="full",
                           tokens_est=int((len(brief) + len(ex)) * 0.5),
                           detail={"reason": reason, "mixed_sources": True})

    def _full_auto(ents: list, reason: str) -> RouteResult:
        """有非 CLI 来源 → 统一池渲染；纯 CLI → 保持老行为（逐字一致）。"""
        if ents and any(e.source != pool_mod.SOURCE_CLI for e in ents):
            return _full_pool(ents, reason)
        return _full(reason)

    if not account_id:
        return _full("no_account")
    try:
        ents = entries if entries is not None else pool_mod.pool_entries(account_id)
    except Exception as e:  # noqa: BLE001
        return _full("pool_error:%s" % type(e).__name__)

    if not ents:
        return _full("empty_pool")
    if force_full or len(ents) <= FAST_PATH_MAX or not (question or "").strip():
        return _full_auto(ents, "fast_path" if ents else "empty_pool")

    try:
        dense = pool_mod.route_scores(account_id, question)
        dec = decide(question, ents, dense)
        # ★ 大点 02【决策模型精排】—— 候选互相咬住时请决策模型复核一次，只改写顺序。
        #   方案见 docs/v4.0/v4.0规划文档.md §02 ③ 段；裁定见 docs/v4.0/Verdict/02-决策模型仲裁层.md。
        #   未启用（tool_route_provider=local，默认）/ 未触发（灰区题与相距不近的题都不触发）/
        #   不可用（超时 无额度 形状错 限流 预算用尽）→ 返回 None，下面一切行为逐字等于现状。
        try:
            from tools import decision_router as dec_router
            _refined = dec_router.maybe_refine(account_id, question, ents, dec,
                                               allow=allow_decision)
            if _refined:
                dec = _refined
        except Exception:  # noqa: BLE001
            pass
        if not dec["confident"]:
            # M5c-4【占位已由大点 02 接管】：灰色区间仍然**回退全量**（噪声与「要不要用工具」
            #   的判定归路由器负责，见裁定 3；决策模型只做「从候选里选」，而灰区题的候选本就全给了模型）。
            prov = _route_provider(account_id)
            r = _full_auto(ents, "low_confidence")
            r.detail.update({"z_top": dec["z_top"], "lex_top": dec["lex_top"], "arbiter": prov,
                             "decision": dec.get("decision")})
            return r
        brief, full_refs, order = _render_routed(ents, dec["order"])
        ex_all = cli_reg.ability_examples_block(account_id)
        ex = _filter_examples(ex_all, full_refs)
        return RouteResult(
            brief=brief, examples=ex, mode="routed", hits=full_refs,
            tokens_est=int((len(brief) + len(ex)) * 0.5),
            detail={"z_top": dec["z_top"], "lex_top": dec["lex_top"],
                    "order": order, "full": full_refs,
                    "scores": {k: round(v, 4) for k, v in dec["fused"].items()},
                    "z": {k: round(v, 3) for k, v in dec["z"].items()},
                    "lexical": dec["lexical"],
                    "pool": len(ents),
                    "decision": dec.get("decision")},
        )
    except Exception as e:  # noqa: BLE001
        r = _full("route_error:%s" % type(e).__name__)
        r.detail["error"] = str(e)
        return r


def _filter_examples(examples_block: str, refs: list[str]) -> str:
    """示例区块里只保留命中工具的示例（其余工具的示例白占预算、还会诱使模型乱调）。

    注意示例行的真实格式是 `用户问到「…」→ run_shell_command(command="bin", argv=[…])`——
    **不以 `- ` 开头**（早先按 `- ` 判断导致过滤没生效，实测踩过）。
    """
    if not examples_block or not refs:
        return ""
    keep_lines = []
    for ln in examples_block.splitlines():
        if not ln.strip():
            continue
        if "run_shell_command(" in ln or "command=" in ln:
            if not any(('command="%s"' % r) in ln for r in refs):
                continue
        keep_lines.append(ln)
    # 若过滤后只剩引导语、没有一条示例 → 整段丢掉（空标题没意义）
    if not any(("command=" in ln or "run_shell_command" in ln) for ln in keep_lines):
        return ""
    return "\n".join(keep_lines)
