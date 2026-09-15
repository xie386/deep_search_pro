"""入库评估（M3，批注 7）——独立小 agent（不纳入 main_agent 体系，直接调 LLM）。

用户在决定入库前调用，评估两件事并**只给建议、不自动执行**：
  1. 内容是否含"检索脏"（AI 导出报告带的人格口吻/emoji/表格装饰）→ 是否需要清洗
  2. 内容质量是否值得入库（信息密度 / 重复度 / 是否纯寒暄导出）

清洗执行由 cleaner 完成（evaluator 判断"需要"时用户可在前端预览清洗后文本再确认）。
"""
from __future__ import annotations
 
import json
import re

EVAL_PROMPT = """你是知识库内容质检员。用户要把一段文本存入"个人知识库"（供 AI 情报助手语义检索引用）。
请评估这段文本，输出 JSON：
{"needs_clean": true/false, "clean_reason": "若需清洗，说明哪里脏（emoji/口吻/表格噪音等）",
 "quality_ok": true/false, "quality_reason": "质量判断（信息密度低/重复/纯寒暄等）",
 "suggestion": "一句话建议（入库/清洗后入库/不建议入库）"}

注意：
- AI 导出的情报报告若带大量 emoji、人格口吻（奴婢/主人/喵~等）、表格装饰——算"需清洗"（脏内容会污染向量检索）
- 判断"值得入库"的标准：是否有可复用的情报价值（数据、观点、结论、清单）；纯寒暄/空泛内容不建议
- 只做建议，最终由用户决定"""


def _extract_json(text: str) -> dict:
    """从 LLM 回复中提取 JSON（容忍 ```json 围栏与前后废话）。"""
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        return {}
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        return {}


def evaluate_doc(title: str, content: str) -> dict:
    """评估一段待入库文本（标题 + 前 3000 字截断给 LLM）。

    :return: {needs_clean, clean_reason, quality_ok, quality_reason, suggestion,
              raw_response}——LLM 不可用时返回全空并标注 error。
    """
    snippet = (content or "")[:3000]
    try:
        from langchain_core.messages import HumanMessage
        from agent.llm import model  # 默认模型（独立小调用，无工具链）
        resp = model.invoke([HumanMessage(content=(
            f"{EVAL_PROMPT}\n\n标题：{title}\n\n--- 待评估文本 ---\n{snippet}"))])
        text = (resp.content or "") if resp else ""
    except Exception as e:
        return {"error": f"评估调用失败: {type(e).__name__}: {e}"}

    d = _extract_json(str(text))
    if not d:
        return {"needs_clean": None, "quality_ok": None,
                "suggestion": "（评估模型未返回结构化结果）",
                "raw_response": str(text)[:500]}
    d["raw_response"] = str(text)[:200]
    return d
