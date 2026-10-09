# -*- coding: utf-8 -*-
"""素材体检：判断一条能力条目的「面向路由的文本」是否齐备（产品模块，2026-10-06 立）。

为什么放在 `tools/` 而不是 tests：这套规则必须**在接入流程里自动生效**——
用户不会去跑测试脚本。目前由 ①`api/tools_sources.py` 的来源列表/刷新接口回传体检结果、
②`tests/live/m6b_material_lint.py`（薄包装）与 ③接入规范文档共同引用，三处同一份规则。

规则严格对齐 `capability_pool` 的真实语义：
  · 词法路只看 `name + keywords`（短语命中权重最高）
  · 向量文本 = `name + keywords + abilities`，其中（api/mcp）
      abilities = (用户手写工具级描述 or 自动摘要) + "；" + 来源级描述
    即：**写了 `abilities_user` 之后，自动摘要不再进入向量文本** ——
    「自动摘要过长 / 是英文」只在**没写**工具级描述时才算问题。
"""
import re

from tools.capability_pool import (              # 复用池子自己的口径，避免"两套标准"
    _KEYWORDS_PREFIX,      # 「关键词：」前缀（AI 草稿/用户手写都这么产）
    derive_keywords,       # 最后一道兜底：从描述派生
    strip_generic,
)

SPLIT = re.compile(r"[、/，,。；;|｜\s]+")
ASCII_ID = re.compile(r"^[A-Za-z0-9_.#:/-]+$")
NEGATION = re.compile(r"与[^，。；]{0,12}(无关|不同|无涉)")
CURATED = ("关键词：", "关键词:")
NEAR_DUP_JACCARD = 0.6


def _phrases(kw: str) -> list:
    return [p for p in SPLIT.split(kw or "") if p]


def _keyword_line(text: str) -> str:
    """取描述里以「关键词：」开头的那一行（池子就是这么取关键词的）。"""
    m = _KEYWORDS_PREFIX.match(text or "")
    if not m:
        return ""
    # 关键词行取到句末（。/；/换行/竖线 之前）——与 capability_pool 同一口径
    return re.split(r"[。；;\n|]", (text or "")[m.end():], maxsplit=1)[0].strip()


def _effective_keywords(row: dict) -> tuple:
    """**复刻** `capability_pool._entry_from_row` 的关键词取用优先级。

    返回 (池子实际会用的关键词, 来源) —— 来源取值：column / tool_desc / source_desc / derive。
    体检必须看"池子实际会拿到什么"，否则会对着一个能正常取词的条目喊"无关键词"。
    """
    kw = (row.get("keywords") or "").strip()
    if kw:
        return kw, "column"
    src_ab = (row.get("src_abilities") or "").strip()
    for text, tag in ((row.get("abilities_user") or "", "tool_desc"),
                      (src_ab, "source_desc"),
                      ((row.get("abilities") or "") if not src_ab else "", "auto_desc")):
        line = _keyword_line(text)
        if not line and tag == "source_desc":       # 来源级：池子在没写前缀时整段都当候选
            line = (text or "").strip()
        if line:
            return line, tag
    return derive_keywords(row.get("abilities_user") or row.get("abilities") or ""), "derive"


def usable_keywords(kw: str) -> list:
    """去掉泛化词后还剩什么（与 router 的 strip_generic 同口径）。"""
    return [p for p in _phrases(kw) if strip_generic(p)]


def check_entry(row: dict) -> list:
    """单条体检。row 需含 ref / name / keywords / abilities / abilities_user。"""
    ref = row.get("ref") or ""
    name = (row.get("name") or "").strip()
    kw = row.get("keywords") or ""
    abl = (row.get("abilities") or "").strip()
    au = (row.get("abilities_user") or "").strip()
    probs = []

    # ---- 关键词：**按池子的真实取用优先级**判断（keywords 列 → 工具级描述的「关键词：」行
    #      → 来源级描述的「关键词：」行 → 从描述派生兜底），否则会报出假警报
    #      （2026-10-06 实测：get-station-by-telecode 明明能拿到 6 个短语，却被报"无关键词"，
    #       用户于是怎么补都消不掉徽标）。
    kw_used, kw_from = _effective_keywords(row)
    if not kw_used:
        probs.append({"code": "no_keywords", "severity": "hard",
                      "msg": "取不到任何关键词（列、描述里的关键词行、派生都为空）—— 词法路对这条完全没有信号"})
    elif not usable_keywords(kw_used):
        probs.append({"code": "generic_keywords", "severity": "hard",
                      "msg": "关键词几乎全是泛化词 %s —— 去掉泛化词后没有可用短语" % _phrases(kw_used)[:4]})
    elif kw_from == "derive":
        probs.append({"code": "derived_keywords_only", "severity": "soft",
                      "msg": "关键词只有自动派生的 %s（偏弱）—— 在工具描述里加一行「关键词：用户会说的中文词/…」"
                             "或点「✨ AI 预写」即可显著改善路由" % usable_keywords(kw_used)[:5]})

    curated = abl.lstrip().startswith(CURATED)      # 人工整理过的中文摘要（CLI 那类）：长不等于差
    if not au:
        if not abl:
            probs.append({"code": "no_desc", "severity": "hard", "msg": "既无人工工具级描述、也无自动摘要"})
        elif len(abl) > 400 and not curated:
            probs.append({"code": "auto_desc_too_long", "severity": "hard",
                          "msg": "只有自动摘要且过长（%d 字）—— 会稀释向量文本，建议用「AI 预写」补工具级中文描述" % len(abl)})
        elif len(re.findall(r"[A-Za-z]", abl)) > len(abl) * 0.6 and not curated:
            probs.append({"code": "auto_desc_english", "severity": "hard",
                          "msg": "只有自动摘要且以英文为主 —— 对中文问句没有信号，建议用「AI 预写」补工具级中文描述"})

    if ASCII_ID.match(name or ""):
        probs.append({"code": "ascii_name", "severity": "hard",
                      "msg": "名字是英文标识符（%s）—— 会与中文问句在向量空间撞车，建议改成人话" % name})

    if NEGATION.search(au or abl):
        probs.append({"code": "negation", "severity": "hard",
                      "msg": "描述含否定式措辞（「与 X 无关」）—— 会把被否定的词拉进向量空间，"
                             "实测既可压噪也可能反噬，建议改成领域正面措辞"})
    return probs


def check_rows(rows: list) -> dict:
    """批量体检。返回 {ref: [...问题]} 只保留有问题的条目。"""
    out = {}
    for r in rows:
        probs = check_entry(r)
        if probs:
            out[r.get("ref") or ""] = probs

    # 同源近名工具：关键词高度重叠 → 各自需要区分性短语
    by_src = {}
    for r in rows:
        by_src.setdefault((r.get("ref") or "").split("/")[0].replace("mcp:", ""), []).append(
            (r.get("ref") or "", set(usable_keywords(r.get("keywords") or ""))))
    for _src, items in by_src.items():
        for i in range(len(items)):
            for j in range(i + 1, len(items)):
                a, sa = items[i]
                b, sb = items[j]
                if not sa or not sb:
                    continue
                jac = len(sa & sb) / len(sa | sb)
                if jac >= NEAR_DUP_JACCARD:
                    for x, y in ((a, b), (b, a)):
                        out.setdefault(x, []).append({
                            "code": "near_dup", "severity": "soft",
                            "msg": "与同源 %s 的关键词高度重叠（%.0f%%）—— 近名工具需要各自的区分性短语"
                                   % (y.split("/")[-1], jac * 100)})
    return out


def load_rows(account_id: int, conn=None) -> list:
    """读某账号的启用条目（只读）。"""
    from tools.schema_personal import get_personal_conn
    own = conn is None
    conn = conn or get_personal_conn()
    try:
        src_map = {r["slug"]: (r["abilities"] or "") for r in conn.execute(
            "SELECT slug, COALESCE(abilities,'') AS abilities FROM tool_sources WHERE account_id=?",
            (int(account_id),))}
        out = []
        for r in conn.execute(
                """SELECT ref, name, COALESCE(keywords,'') AS keywords, COALESCE(abilities,'') AS abilities,
                          COALESCE(abilities_user,'') AS abilities_user
                   FROM tool_capabilities WHERE account_id=? AND enabled=1 ORDER BY ref""", (int(account_id),)):
            row = dict(r)
            slug = (row["ref"] or "").split(":", 1)[-1].split("/")[0].split("#")[0]
            row["src_abilities"] = src_map.get(slug, "")      # 池子取词的第三顺位
            out.append(row)
        return out
    finally:
        if own:
            conn.close()


def health(account_id: int, conn=None) -> dict:
    """给接口/前端用的体检摘要：{total, problem_count, items:[{ref,name,problems}]}。"""
    rows = load_rows(account_id, conn=conn)
    issues = check_rows(rows)
    items = [{"ref": r["ref"], "name": r["name"], "problems": issues[r["ref"]]}
             for r in rows if r["ref"] in issues]
    hard = sum(1 for it in items for p in it["problems"] if p.get("severity", "hard") == "hard")
    soft = sum(1 for it in items for p in it["problems"] if p.get("severity") == "soft")
    return {"total": len(rows), "problem_count": len([it for it in items
                                                      if any(p.get("severity", "hard") == "hard" for p in it["problems"])]),
            "info_count": len([it for it in items
                               if it["problems"] and all(p.get("severity") == "soft" for p in it["problems"])]),
            "hard_findings": hard, "soft_findings": soft, "items": items,
            "codes": sorted({p["code"] for it in items for p in it["problems"]})}
