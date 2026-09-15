# ============================================================
# 智选情报官 · Digest 定期推送引擎（M4-b，重构版）
# 回归「主智能体主导的多智能体协作」架构：
#   引擎不再自写 Tavily/LLM 调用，而是用 create_deep_agent 编排：
#   主 Agent 调度【网络搜索助手】完成联网检索（复用 internet_search 工具，
#   含代理降级直连）→ 再把候选条目交给【定期情报周报助手】筛选与撰写。
#
# 纪律（继承 ai-weekly-digest skill）：
#   - 检索关键词批次硬上限 MAX_BATCHES=8（build_batches 截断）
#   - 近 7 天时间窗（任务指令 + digest 子智能体 prompt 双重约束）
#   - 统计口径诚实：覆盖不足在报告头 coverage_note 注明
#   - 绝不编造来源 URL（digest 子智能体 prompt 硬约束）
# ============================================================

import json
import os
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path

# 项目根路径防御（同 api/server.py，防同名 agent 包遮蔽）
_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))
sys.path = [p for p in sys.path if "hermes-agent" not in p and "hermes_agent" not in p]

from dotenv import load_dotenv

load_dotenv(_PROJECT_ROOT / ".env")

MAX_BATCHES = 8          # 单次 digest 检索批次硬上限
DAYS_WINDOW = 7          # 时间窗：近 N 天
MIN_CANDIDATES = 15      # 候选少于该值时报告头注明覆盖有限

OUTPUT_DIR = _PROJECT_ROOT / "output"


# ---------------------------------------------------------------------------
# 订阅数据访问（M4-a 表；引擎只读）
# ---------------------------------------------------------------------------
def get_sub(conn, sub_id: int) -> dict | None:
    cur = conn.execute("SELECT * FROM digest_subs WHERE id=?", (sub_id,))
    row = cur.fetchone()
    if not row:
        return None
    cols = [d[0] for d in cur.description]
    return dict(zip(cols, row))


def mark_sub_ran(conn, sub_id: int):
    conn.execute("UPDATE digest_subs SET last_run_at=CURRENT_TIMESTAMP WHERE id=?", (sub_id,))
    conn.commit()


# ---------------------------------------------------------------------------
# 第一步：订阅 → 查询词批次（硬上限 MAX_BATCHES）
# ---------------------------------------------------------------------------
def build_batches(sub: dict) -> list[str]:
    """把订阅的 keywords JSON 数组切成查询批次，硬上限 MAX_BATCHES。

    泛用性设计（方案 B）：直接用关键词原文作为查询词，不拼接领域化修饰词
    （如「新品 评测 价格」「动态 新闻」）——实测 Tavily 对带修饰词的中文长
    查询相关性崩坏（返回无关金融/加密货币新闻），且修饰词对不同领域（AI、
    香烟、米诺地尔、竞品）无法通用。简短实体词对任何领域都更精准。
    多词兴趣（如「AI大模型资讯」）保留原样，靠任务指令让检索助手拆分补查。
    """
    try:
        kws = json.loads(sub["keywords"] or "[]")
    except Exception:
        kws = []
    kws = [k.strip() for k in kws if isinstance(k, str) and k.strip()]
    return kws[:MAX_BATCHES]


# ---------------------------------------------------------------------------
# 主入口：run_digest(sub_id, owner_id) —— 同步函数，可独立调试
# 编排：主 Agent（main_agent 提示词 + 任务指令）调度 network_search_agent 检索、
#       digest_agent 筛选撰写，最终返回 markdown 周报。
# ---------------------------------------------------------------------------
def run_digest(sub_id: int, owner_id: int, conn=None) -> dict:
    """执行一次 digest。conn 由调用方提供（调度器/REST 复用其连接）；
    独立调试时传 None 则自建连接。返回 {ok, md_path, item_count, error?}"""
    from langchain_core.messages import HumanMessage
    from deepagents import create_deep_agent

    from agent.llm import model
    from agent.prompts import main_agent_content
    from agent.subagents.network_search_agent import network_search_agent
    from agent.subagents.digest_agent import digest_agent
    from tools.schema_personal import get_personal_conn

    own = conn is None
    conn = conn or get_personal_conn()
    try:
        sub = get_sub(conn, sub_id)
        if not sub:
            return {"ok": False, "error": f"订阅 {sub_id} 不存在"}
        today = datetime.now()

        username = conn.execute(
            "SELECT username FROM accounts WHERE id=?", (owner_id,)
        ).fetchone()
        username = username[0] if username else f"user_{owner_id}"

        # 1. 批次
        batches = build_batches(sub)
        if not batches:
            return {"ok": False, "error": "订阅无有效关键词"}

        scope = sub.get("scope") or "personal"
        scope_label = "竞品周报" if scope == "company" else "快讯周报"
        week_ago = today - timedelta(days=DAYS_WINDOW)
        week_range = f"{week_ago:%m-%d} ~ {today:%m-%d}"

        # 检索语言（用户可选 zh/en/both，控制 bilingual 参数）
        sub_lang = sub.get("lang") or "zh"
        if sub_lang == "en":
            lang_desc = "英文优先：把中文关键词翻译成英文检索（bilingual=False，仅英文）"
            bilingual_flag = "bilingual 不传（保持 False）"
        elif sub_lang == "both":
            lang_desc = "中英双语：中文原文 + 英文翻译双检索合并（bilingual=True）"
            bilingual_flag = "bilingual 必须传 True"
        else:
            lang_desc = "中文检索（默认，bilingual 不传）"
            bilingual_flag = "bilingual 不传（保持 False）"

        # 画像驱动情报权重（用户批注 ⑪）：读取该账号 MEMORY.md 的 USER PROFILE 段，
        # 注入为情报侧重提示（如价格敏感→降价/优惠类优先），让周报更贴合用户画像。
        profile_hint = ""
        try:
            _mem_path = Path(__file__).resolve().parents[1] / "agents_docs" / username / "MEMORY.md"
            if _mem_path.exists():
                _mem = _mem_path.read_text(encoding="utf-8")
                if "## USER PROFILE" in _mem:
                    _seg = _mem.split("## USER PROFILE", 1)[1]
                    _seg = _seg.split("## ", 1)[0].strip()
                    if _seg and "（初始为空" not in _seg and "暂无资料" not in _seg:
                        profile_hint = _seg[:400]
        except Exception:
            profile_hint = ""

        # 2. 构造主 Agent（一次性任务，不挂 checkpointer）
        profile_line = ""
        if profile_hint:
            profile_line = (f"- 用户画像（情报侧重参考）：{profile_hint}\n"
                            "- 情报侧重：优先收录贴合上述画像的信息（如价格敏感用户侧重降价/优惠/性价比类动态），但不得因侧重而忽略订阅关键词范围内的重大新闻")

        # M3：知识库代码层必查（full 模式）——订阅关键词相关私有资料注入候选参考
        kb_line = ""
        try:
            from rag_knowledge.kb_service import kb_exists, query as _kb_query
            if kb_exists(username):
                kb_parts = []
                for _q in batches[:3]:  # 最多查 3 个关键词（控制耗时，full rerank <1s/次）
                    for _h in _kb_query(username, _q, mode="full", top_k=2, account_id=owner_id):
                        _snip = (_h["text"] or "")[:200].replace("\n", " ")
                        kb_parts.append(f"《{_h['title']}》{_snip}")
                if kb_parts:
                    kb_line = ("- 知识库相关观点（用户私有沉淀，来自个人知识库；周报如需可引用，来源标 📚 知识库）：\n"
                               + "\n".join(f"  · {p}" for p in kb_parts[:6]))
        except Exception as _e:
            print(f"[digest] 知识库必查失败（忽略）: {_e}")

        task_prompt = f"""请为以下订阅生成一期定期情报周报（Digest）。

【订阅信息】
- 订阅名称：{sub.get('name') or scope_label}
- 订阅范围：{'公司竞品情报' if scope == 'company' else '个人选购决策'}
- 关注关键词（共 {len(batches)} 个，逐个作为独立检索批次，最多 8 批）：
{chr(10).join(f'  {i+1}. {q}' for i, q in enumerate(batches))}
- 时间窗：近 {DAYS_WINDOW} 天内（{week_range}），只保留时间窗内的信息
- 检索语言：{lang_desc}
- 输出语言：中文
{profile_line}
{kb_line}
【执行步骤】
1. 调用【网络搜索助手】，为每个关键词各检索一轮：query 用订阅关键词原文（简短实体词，不要自行堆叠「最新/新闻/价格」等修饰词——修饰词会显著降低相关性）；topic 必须用 news；days 必须传 7；strict_days 必须传 True（工具会在代码层剔除超过 7 天的旧闻）；{bilingual_flag}（工具会在 strict_days=True 时于代码层剔除超7天旧闻）；max_results 用 8。若某关键词首轮结果不足或全部被剔除，可换一个更聚焦的表述（如多词兴趣拆成核心实体词）再补一轮；全程检索轮次控制在 {len(batches)}~{len(batches)*2} 次内。
2. 汇总所有检索结果（工具已做严格时效过滤，只保留近 7 天），去重后作为「候选条目」。
3. 调用【定期情报周报助手】，把候选条目与订阅范围/关键词交给它筛选并撰写周报。
4. 把周报 Markdown 全文作为最终回答输出（不要输出 JSON、不要输出检索过程日志）。

【输出要求】
- 只输出周报 Markdown 正文，从 "## 📰" 开始到 "*由智选情报官自动生成*" 结束。
- 周报头部扫描说明格式：本次共扫描 N 条候选，精选出 M 条（N/M 为真实数字）。
"""

        digest_main_prompt = main_agent_content["system_prompt"] + f"""

# 当前任务：生成定期情报周报（Digest）
你正在执行一次定时推送任务，而非普通问答。严格按用户消息中的【执行步骤】调度子智能体，
最终只输出周报 Markdown 全文。"""
        print(f"[digest] 编排主 Agent（{len(batches)} 批次）…")
        agent = create_deep_agent(
            model=model,
            system_prompt=digest_main_prompt,
            subagents=[network_search_agent, digest_agent],
        )
        result = agent.invoke({"messages": [HumanMessage(content=task_prompt)]})
        answer = result["messages"][-1].content or ""
        answer = answer.strip()

        # 3. 清洗：去掉可能的代码围栏，取 markdown 主体
        md = _extract_markdown(answer)
        if len(md) < 30:
            return {"ok": False, "error": "主 Agent 未产出有效周报内容", "raw": answer[:200]}

        # 4. 解析统计口径（真实数字来自周报头部；容忍 markdown 加粗 **N** 包裹）
        m = re.search(r"共扫描\s*\*{0,2}\s*(\d+)\s*\*{0,2}\s*条候选[，,]\s*精选出\s*\*{0,2}\s*(\d+)\s*\*{0,2}\s*条", md)
        scanned = int(m.group(1)) if m else 0
        item_count = int(m.group(2)) if m else 0
        # 正则未命中时回退：数"🔍"来源标记作为最低可信精选数（避免真实内容被记 0）
        if item_count == 0 and md.count("🔍") > 0:
            item_count = md.count("🔍")
        coverage = "覆盖有限" if scanned < MIN_CANDIDATES else ""

        # 5. 落盘
        user_dir = OUTPUT_DIR / username
        user_dir.mkdir(parents=True, exist_ok=True)
        ts = today.strftime("%Y%m%d_%H%M%S")
        md_path = user_dir / f"{scope_label}_{ts}.md"
        md_path.write_text(md, encoding="utf-8")

        # 6. 写 digest_reports + 更新 last_run_at
        conn.execute(
            "INSERT INTO digest_reports (owner_id, title, md_path, item_count, status, coverage_note, created_at) "
            "VALUES (?,?,?,?,?,?,CURRENT_TIMESTAMP)",
            (owner_id, f"{scope_label} {today:%Y-%m-%d}", str(md_path.relative_to(_PROJECT_ROOT)),
             item_count, "done", coverage),
        )
        mark_sub_ran(conn, sub_id)
        conn.commit()

        print(f"[digest] ✓ 周报落盘 {md_path}（扫描 {scanned} / 精选 {item_count}）")
        return {"ok": True, "md_path": str(md_path), "item_count": item_count,
                "scanned": scanned, "batches": len(batches)}
    except Exception as e:
        import traceback
        traceback.print_exc()
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}
    finally:
        if own:
            conn.close()


def _extract_markdown(text: str) -> str:
    """从 Agent 回答中提取周报 markdown 主体：去代码围栏，取 ## 📰 到结尾。"""
    t = text.strip()
    # 去 ```markdown / ``` 围栏
    t = re.sub(r"^```[a-zA-Z]*\s*", "", t)
    t = re.sub(r"\s*```\s*$", "", t)
    # 若包含多个块，取第一个 ## 📰 开始
    i = t.find("## 📰")
    if i >= 0:
        t = t[i:]
    return t.strip()
