# -*- coding: utf-8 -*-
"""网络取法的三个**工作流工具**（v4.0 大点 05 · 2026-10-14 用户定稿，裁定 6/7）。

**为什么把"取法"做成工作流工具，而不是写在提示词里**（对比见 `Verdict/05` 裁定 6）：
  · 流程逻辑（先搜后进的顺序、进站站点数与挑站点规则）放进**代码** → 确定性、可单测、模型少走一步
    （一次调用走完流程 → 更快）；
  · 底层两个工具（`tools/tavily_tool.internet_search` / `tools/browser_tool.browse_pages`）**不再直接挂载**，
    降级为 util，只被本文件的三个工作流调用 —— 于是"情况②不许检索"这类约束变成**结构性的**：
    `browse_only` 的签名里**根本没有查询词**，模型想违规也无从下手。

**三个工作流**（主 Agent 在任务里点名其中一个）：
  ① `web_search_only`    —— 只做全网检索（Tavily），**不进站**；
  ② `browse_only`        —— 只进给定网址做交互/深度抓取（browser-use），**不检索**；
  ③ `search_then_browse` —— 先检索定位（去重到不同域名、跳过聚合站、取前 N 个），再进站深抓。

**记账与进度**（M4 口径：要能回答"这次为什么烧了额度"）：
  · 本文件的工具都是框架可见的工具 → middleware 会给它们各记一条，分类表里映射成**内部工具**
    （`agent/usage_counter.py`），所以不会重复计费；
  · 底层真正花外部检索额度的调用（Tavily / 浏览器）由本文件**显式** `record(...)`，额度账不被这层遮蔽；
  · 进度条：每个工作流 + 底层调用都各上报一次（`monitor.report_tool`），前端看得到细分。
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.parse

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from langchain_core.tools import tool                       # noqa: E402

from api.monitor import monitor                             # noqa: E402
from tools.browser_tool import browse_pages                 # noqa: E402  ← util（已不挂载）
from tools.tavily_tool import internet_search               # noqa: E402  ← util（已不挂载）

#: 纯聚合/问答/百科站：进站往往拿不到一手资料，情况③挑站点时跳过。
_AGGREGATOR_HINTS = (
    "baike.baidu.com", "zhidao.baidu.com", "wikipedia.org", "wikiwand.com",
    "zhihu.com", "jianshu.com", "csdn.net", "cnblogs.com", "toutiao.com",
    "sohu.com/a/", "baijiahao.baidu.com", "bilibili.com/read", "douban.com",
    "xiaohongshu.com", "smzdm.com", "jd.com", "tmall.com", "taobao.com",
)


def _sizes(obj) -> int:
    try:
        return len(json.dumps(obj, ensure_ascii=False, default=str))
    except Exception:                                       # noqa: BLE001
        return 0


def _emit(tool_name: str, ok: bool, t0: float, result=None, source: str = "") -> None:
    """显式记一条**外部检索**账（工作流这层不遮蔽底层调用）。任何异常都吞掉。"""
    try:
        from agent import usage_counter as uc
        aid, tid, turn = uc.context()
        uc.record(aid, tid, uc.KIND_RETRIEVAL_EXTERNAL, tool_name, source or tool_name,
                  ok=bool(ok), latency_ms=int((time.time() - t0) * 1000), turn_index=turn,
                  result_chars=_sizes(result))
    except Exception:                                       # noqa: BLE001
        pass


def pick_sites(search_result, max_sites: int = 2) -> list:
    """从检索结果里挑进站目标：**按相关性顺序**、**去重到不同域名**、**跳过纯聚合/问答站**。

    纯函数（可离线单测）—— 情况③的"挑最相关的 1~2 个站点"就靠它。
    """
    picked, seen = [], set()
    results = (search_result or {}).get("results") if isinstance(search_result, dict) else None
    for r in results or []:
        url = str((r or {}).get("url") or "").strip()
        if not url.lower().startswith("http"):
            continue
        low = url.lower()
        if any(h in low for h in _AGGREGATOR_HINTS):
            continue
        host = (urllib.parse.urlparse(url).hostname or "").lower()
        if not host or host in seen:
            continue
        seen.add(host)
        picked.append(url)
        if len(picked) >= max(1, int(max_sites or 1)):
            break
    return picked


@tool
def web_search_only(query: str, topic: str = "general", max_results: int = 8,
                    include_raw_content: bool = False, days: int = 0,
                    strict_days: bool = False, bilingual: bool = False) -> dict:
    """【只做全网检索】跨站关键词检索公开信息，**不进任何网站**。

    ★ 什么时候用：主 Agent 点名「只做全网检索」时（问题要公开信息、但没有明确来源导向）。
      需要进某个网址看内容，请用 `browse_only` 或 `search_then_browse`。

    :param query: 查询词（建议简短实体词/关键词，别堆叠「最新/价格」这类修饰词）。
    :param topic: general 综合 / news 新闻 / finance 金融。
    :param max_results: 返回条数上限（默认 8）。
    :param include_raw_content: 是否连正文一起返回（默认 False 只要摘要）。
    :param days: 仅近 N 天（0=不限；news 类按发布日期在代码层剔除旧闻）。
    :param strict_days: 严格时效（配合 days 使用，代码层按 published_date 剔除超窗旧闻）。
    :param bilingual: 中英双语检索（中文实体词召回更全；仅当 query 含中文时生效）。
    :return: Tavily 原始结构（`{"results": [...]}`），便于逐条标注来源。
    """
    monitor.report_tool(tool_name="全网检索", args={"query": query, "topic": topic, "days": days, "bilingual": bilingual})
    t0 = time.time()
    try:
        got = internet_search(query=query, topic=topic, max_results=max_results,
                              include_raw_content=include_raw_content, days=days,
                              strict_days=strict_days, bilingual=bilingual)
        _emit("internet_search", True, t0, got)
        return got
    except Exception as e:                                  # noqa: BLE001
        _emit("internet_search", False, t0)
        return {"ok": False, "error": "全网检索失败（%s：%s）" % (type(e).__name__, str(e)[:200]), "results": []}


@tool
def browse_only(urls: list, task: str, max_steps: int | None = None) -> dict:
    """【只进指定网址】打开给定网址做交互或深度抓取，取回一手资料（价格/规格/公告/日期等）。

    ★ 什么时候用：主 Agent 点名「只进这个网址」时（已有明确地址：用户给的、或从数据库取的）。
      **本工具不检索**（签名里没有查询词）——进站打不开/抓不到就如实回报，不要自己改道去检索。

    :param urls: 要打开的页面地址（照原样传，别改写成查询词）；建议 1~3 个。
    :param task: 在这一页要拿什么，用自然语言写清（例："把详情里的字段原样照抄出来"）。
    :param max_steps: 本次最多多少步；不传用设置里的默认值。
    :return: {"ok":…, "results":[{"url","title","content"}], "urls_visited":[…], "steps":…, "seconds":…}
    """
    monitor.report_tool(tool_name="进站抓取", args={"urls": urls, "task": task})
    t0 = time.time()
    got = browse_pages(task=task, urls=urls, max_steps=max_steps)
    _emit("browse_pages", bool((got or {}).get("ok")), t0, got, source=";".join([str(u) for u in (urls or [])])[:200])
    return got


@tool
def search_then_browse(query: str, task: str, max_sites: int = 2, topic: str = "general") -> dict:
    """【先检索定位、再进站深抓】先用关键词检索找出最相关的站点，再逐个进站按 `task` 取一手资料。

    ★ 什么时候用：主 Agent 点名「先检索定位、再进站深抓」时（需要有来源导向，但用户与库里都没有地址，
      例如"帮我找几个 xx 网站并给出详细信息"）。
      · 挑站点规则（代码内固定）：按相关性顺序、**去重到不同域名**、**跳过纯聚合/问答/百科站**、最多 `max_sites` 个；
      · 返回**检索结果与进站结果并列**，便于逐条标注来源。

    :param query: 定位用的查询词（例："至本 官网"）。
    :param task: 进站后要拿什么（要窄：一次只取一类信息）。
    :param max_sites: 最多进几个站点（默认 2 —— 进站单次可能跑满几分钟，多了会把这一轮拖很久）。
    :param topic: general / news / finance。
    :return: {"ok":…, "search": <检索原始结构>, "browsed": <进站结果>, "sites": [<实际进的网址>]}
    """
    monitor.report_tool(tool_name="先检索定位再进站",
                        args={"query": query, "task": task, "max_sites": max_sites})
    t0 = time.time()
    try:
        found = internet_search(query=query, topic=topic)
    except Exception as e:                                  # noqa: BLE001
        _emit("internet_search", False, t0)
        return {"ok": False, "error": "检索定位失败（%s：%s）" % (type(e).__name__, str(e)[:200]), "results": []}
    _emit("internet_search", True, t0, found)

    sites = pick_sites(found, max_sites)
    if not sites:
        return {"ok": False, "search": found,
                "error": "检索到结果，但挑不出可进站的站点（结果多为聚合/问答/电商站）。"
                         "可改用「只做全网检索」补信息，或由主 Agent 换更精确的查询词再来一次。"}

    t1 = time.time()
    browsed = browse_pages(task=task, urls=sites)
    _emit("browse_pages", bool((browsed or {}).get("ok")), t1, browsed, source=";".join(sites)[:200])
    return {"ok": bool((browsed or {}).get("ok")), "search": found, "browsed": browsed, "sites": sites,
            "error": (browsed or {}).get("error")}
