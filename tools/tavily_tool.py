import sys
import os
 
# 将项目根目录加入 sys.path，确保 api 模块可导入
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

# 定义一个网络搜索的工具！
# ======================== 导入核心依赖 ========================
# 类型注解：增强代码提示和静态检查能力
from typing import  Literal
# LangChain 工具装饰器：将普通函数转为 Agent 可调用的工具
from langchain_core.tools import tool
# Tavily 官方客户端：实现网络搜索核心功能
from tavily import TavilyClient

# 系统/第三方依赖
from dotenv import load_dotenv  # 加载 .env 文件中的环境变量

# 自定义模块：工具调用埋点监控（需确保 api 模块可导入）
from api.monitor import monitor

# ======================== 初始化配置 ========================
# 加载项目根目录的 .env 文件，读取环境变量（如 TAVILY_API_KEY）
load_dotenv()


# 步骤1： 定义一个TavilyClient对象
tavily_client = TavilyClient(api_key=os.getenv("TAVILY_API_KEY"))


# 步骤2： 定义一个网络搜索工具
@tool
def internet_search(
        query: str,
        topic: Literal[ "news",  "finance",  "general"] = "general",
        max_results: int = 5,
        include_raw_content: bool = False,
        days: int = 0,
        strict_days: bool = False,
        bilingual: bool = False
):
    """
    根据用户问题，进行网络信息搜索!
    注意：主要搜索公开的网络信息！如果指定查询数据库或者rag不能使用此工具！
    :param query: 用户的查询信息（建议使用简短实体词/关键词，不要堆叠修饰词，相关性更好）
    :param topic: 查询的类型（news=新闻，时效性强；finance=金融；general=综合）
    :param max_results: 返回的最大条数
    :param include_raw_content: 是否返回原内容 False 精简 True 详细
    :param days: 仅返回最近 N 天的结果（0=不限；topic=news 时生效，如 7 = 近7天）
    :param strict_days: 严格模式（默认 False）。True 时在代码层解析每条结果的
        published_date，剔除超过 days 天的旧闻（不靠模型自觉判断时效）。
        仅当 days>0 时生效；普通问答不传此参数，保持不过滤。
    :param bilingual: 中英双语检索（默认 False）。True 时先用 LLM 把中文 query
        翻译成英文，分别检索中/英两个 query 并合并去重——Tavily 对中文实体词
        （如「米诺地尔」）相关性差，英文常能召回更精准的结果；对泛领域词
        （如「传统香烟」）中文反而更准，双语合并让模型从更全候选池里选。
        仅当 query 含中文字符时生效；纯英文 query 直接单次检索。
    :return:
    """
    # 每次调用工具，都都会向前端推进调用进度！
    # 参数1： 工具的名字  参数2： 就是调用工具的参数信息
    monitor.report_tool(tool_name="网络搜索工具",
                        args={"query": query, "topic": topic, "max_results": max_results,
                              "include_raw_content": include_raw_content, "days": days,
                              "bilingual": bilingual})

    # 中英双语检索（方案 B 增强）：中文 query 先用 LLM 翻译成英文，
    # 中英各查一次合并去重——Tavily 对中文实体词相关性差，双语互补。
    if bilingual and _contains_cjk(query):
        try:
            en_query = _translate_query(query)
            if en_query and en_query.lower() != query.lower():
                r_zh = _search_once(query, topic, max_results, include_raw_content, days, strict_days)
                r_en = _search_once(en_query, topic, max_results, include_raw_content, days, strict_days)
                return _merge_results(r_zh, r_en, en_query)
        except Exception as e:
            print(f"[tavily] 双语检索失败（降级单语）: {e}")
    return _search_once(query, topic, max_results, include_raw_content, days, strict_days)


def _search_once(query, topic, max_results, include_raw_content, days, strict_days):
    """单次 Tavily 检索 + strict_days 时效过滤（双语模式中/英各调一次）"""
    # 优先走系统代理（本机代理可访问外网时速度更稳）；若代理断开/不可用，
    # 自动降级为直连重试（Session.trust_env=False），避免整个问答因 ProxyError 失败。
    kwargs = {}
    if days and days > 0:
        kwargs["days"] = days
    try:
        result = tavily_client.search(query=query, topic=topic,
                                      max_results=max_results,
                                      include_raw_content=include_raw_content,
                                      **kwargs)
    except Exception as e:
        # 代理断开/代理 TLS 干扰/连接被重置等均降级直连重试（本机代理不稳定时常见）
        ename = type(e).__name__
        if ename in ("ProxyError", "SSLError", "ConnectionError", "ConnectTimeout") \
                or "proxy" in str(e).lower() or "ssl" in str(e).lower():
            print(f"[tavily] {ename}，降级直连重试…")
            result = _search_direct(query, topic, max_results, include_raw_content, kwargs)
        else:
            raise

    # 严格时效过滤（方案 A）：代码层解析 published_date，剔除超过 days 天的旧闻。
    # 不靠模型自觉——Tavily 的 days 只是硬上限，不保证排序时效，旧闻常排前面。
    if strict_days and days and days > 0:
        result = _filter_by_date(result, days)
    return result


def _contains_cjk(text: str) -> bool:
    """判断是否含中文字符"""
    import re
    return bool(re.search(r"[\u4e00-\u9fff]", text))


def _translate_query(query: str) -> str:
    """用项目默认 LLM 把中文 query 翻译成英文（简短新闻检索词）。

    失败时返回空串（调用方降级为单语）。
    """
    try:
        from langchain_core.messages import HumanMessage
        from agent.llm import model as default_model
        sys_prompt = (
            "你是新闻检索翻译器。把用户给出的中文检索词翻译成英文新闻检索词，"
            "要求：1) 简短（3-6 个词），2) 保留核心实体与领域词，3) 适合搜索引擎，"
            "4) 只输出英文翻译本身，不要解释、不要引号、不要加句号。"
        )
        resp = default_model.invoke(
            [{"role": "system", "content": sys_prompt},
             {"role": "user", "content": query}]
        )
        out = (resp.content or "").strip().strip('"').strip("'")
        return out[:80]
    except Exception as e:
        print(f"[tavily] 翻译失败: {e}")
        return ""


def _merge_results(r_zh: dict, r_en: dict, en_query: str) -> dict:
    """合并中英结果：按 url 去重（中文优先），保留各自排序，标注双语。"""
    seen, merged = set(), []
    for src in (r_zh, r_en):
        for it in (src.get("results") or []):
            url = it.get("url", "")
            key = url if url else it.get("title", "")
            if key in seen:
                continue
            seen.add(key)
            merged.append(it)
    r_zh = dict(r_zh)
    r_zh["results"] = merged
    r_zh["bilingual"] = True
    r_zh["bilingual_note"] = f"已做中英双语检索（中文原文 + 英文翻译 {en_query!r}）合并去重"
    return r_zh


def _filter_by_date(result: dict, days: int) -> dict:
    """解析每条结果的 published_date（RFC822 如 'Fri, 21 Aug 2026 11:24:46 GMT'），
    剔除超过 days 天的条目；无日期/解析失败的条目保留但排到末尾（宁可保留不误杀）。
    """
    from datetime import datetime, timedelta, timezone
    import email.utils
    items = result.get("results") or []
    if not items:
        return result
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    kept, nodate = [], []
    for it in items:
        pd = it.get("published_date")
        dt = None
        if pd:
            try:
                dt = email.utils.parsedate_to_datetime(pd)
            except Exception:
                dt = None
        if dt is None:
            nodate.append(it)
        elif dt >= cutoff:
            kept.append(it)
        # 超窗条目直接丢弃（不打印，静默剔除）
    result["results"] = kept + nodate
    # 提示调用方做了严格过滤（模型可见，理解为什么结果少）
    result["strict_filtered"] = True
    result["strict_note"] = f"已按 published_date 剔除超过 {days} 天的旧闻（代码层强制）"
    return result


def _search_direct(query, topic, max_results, include_raw_content, extra=None):
    """绕过系统代理直连 Tavily REST API（Session trust_env=False）。"""
    import requests
    sess = requests.Session()
    sess.trust_env = False  # 忽略 HTTP_PROXY/HTTPS_PROXY 环境变量
    payload = {
        "api_key": os.getenv("TAVILY_API_KEY"),
        "query": query,
        "topic": topic,
        "max_results": max_results,
        "include_raw_content": include_raw_content,
    }
    if extra:
        payload.update(extra)
    resp = sess.post("https://api.tavily.com/search", json=payload, timeout=60)
    resp.raise_for_status()
    return resp.json()


# ======================== 测试 ========================
if __name__ == "__main__":
    try:
        result = internet_search.invoke("查询米诺地尔相关的新闻", topic="news", max_results=3, include_raw_content=True)
        print(result)
    except Exception as e:
        print(f"调用工具出错：{e}")














