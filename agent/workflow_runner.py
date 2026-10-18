"""06 · workflow 引擎：**代码逐步驱动**（照周报引擎的编排器形态）。

定案见 `Verdict/06-…md` 裁定 8，方案见规划文档 §06 ③ I。

## 执行模型（为什么是代码驱动）
步骤由**代码**推进：第 N 步做完 → 校验本步产出 → 再放第 N+1 步。Agent 只执行**每一步**，
不把整份步骤表丢进提示词让它自行安排 —— 那样等于把顺序权交回模型，"流程固定"就名存实亡。

## 失败口径（裁定 8 B3）
某步失败（调用抛错、或产出空/过短）→ **停在该步**、如实报错、**不自动跳步**。已完成的步骤保留。

## 与调用侧的分工
本模块**不 import `api.*` / `agent.build_context`**：由 server 注入 `invoke` 回调（一步一次调用）
与 `on_event` 回调（进度/审计）。这样引擎可以纯函数级单测（喂假 invoke 即可），也不产生反向依赖。
"""
from __future__ import annotations

import re
from typing import Callable

MIN_STEP_OUT = 8          # 一步产出少于这么多字就视为"没干活"（防"我已完成"式空转）
MAX_STEP_OUT = 4000       # 单步产出上限：真机曾一步吐 4.5 万字（自述块 + 复述画像），封顶后只留结论

#: 模型爱自己加的"上下文交接"块标题 —— 出现即整块剔除（真机 11 份，占满整条回复）
_ECHO_HEADS = ("## SESSION INTENT", "# SESSION INTENT", "## SUMMARY", "# SUMMARY",
               "### 人格设定", "### 用户画像", "## 执行进度", "### 执行进度")


def strip_echo(text: str) -> str:
    """剔掉模型自述/交接块（SESSION INTENT、SUMMARY、复述人格画像与工具规则）。

    只剔"标题命中上面清单"的整块（到下一个同级或更高级标题为止），正文里正常的 `##` 小节不动。
    """
    out, skipping = [], False
    for ln in (text or "").splitlines():
        h = ln.strip()
        if any(h.startswith(k) for k in _ECHO_HEADS):
            skipping = True
            continue
        if skipping:
            if re.match(r"^#{1,3}\s", h):      # 到了下一个标题 → 恢复
                skipping = False
            else:
                continue
        out.append(ln)
    return "\n".join(out).strip()

#: 连接抖动类的错误特征 —— **只对这类**在同一步重试一次；其它失败照旧停在该步（不掩盖真问题）
TRANSIENT_HINTS = ("connection error", "connection reset", "connection aborted", "connection refused",
                   "connection closed", "peer closed", "broken pipe", "eof",
                   "timeout", "timed out", "read timed out", "temporarily unavailable",
                   "remote end closed", "incompleteread", "429", "500", "502", "503", "504",
                   # ★ 中文口径也要认（真机报错就是中文的："连接时长过长被上游切断"）——
                   #   只写英文特征等于不会触发重试（2026-10-17 踩到）。
                   "连接时长过长", "被上游切断", "上游切断", "超时", "连接中断", "连接被重置",
                   "连接已关闭", "远端关闭", "网络错误", "服务暂时不可用")


def is_transient(text) -> bool:
    """这条错误像不像"模型/网络连接抖动"（可重试），而不是流程/工具本身的错。"""
    t = str(text or "").lower()
    return any(h in t for h in TRANSIENT_HINTS)


def build_step_prompt(doc: dict, i: int, prev_out: str = "", write_text: str = "") -> str:
    """把第 i 步（0 基）编成**单步任务**提示。只描述这一步，不泄漏后续步骤。"""
    steps = doc.get("steps") or []
    s = steps[i]
    use = s.get("use") or []
    tool_line = ("本步可用的工具集：" + "、".join(use)) if use else "本步不限定工具集，按需使用。"
    parts = [
        "【正在执行 workflow：%s】" % (doc.get("name") or "（未命名）"),
        "第 %d/%d 步：%s" % (i + 1, len(steps), s.get("title") or ""),
        tool_line,
    ]
    # ★ 2026-10-18 写能力工作流：workflow 声明的写工具集 → 每一步都带上授权说明书
    #   （授权是整轮语义；步骤按需使用，用不到就忽略）。
    if write_text:
        parts.append(write_text)
    if s.get("output"):
        parts.append("本步期望产出：%s" % s["output"])
    if prev_out:
        parts.append("上一步的产出（供你衔接，不要重复做）：\n%s" % prev_out.strip()[:2000])
    # ★ 2026-10-17 真机：原句只写"不要自行开始后面的步骤"，模型理解成"等用户发话才继续"，
    #   输出里出现"后面 6 步等老大发话再动"、甚至**反问**（而引擎没有中途对话机制，没人会回答）。
    #   改成：说清"程序会自动接着跑"，并禁止等待式措辞与反问。
    parts.append("只完成这一步并给出本步结论 —— **下一步由程序自动接着执行**，"
                 "不需要征求我同意，也**不要**写「等你发话 / 等确认 / 是否继续」这类等待式措辞。")
    parts.append("若信息不全：写清缺什么，并**按现有信息给出本步结论**；不要反问（这一步没有人会回答你）。")
    # ★ 2026-10-17 真机：AI周报跑到第 2 步开始"疯狂复述画像/人格"，一份 45k 字的回复里塞了
    #   11 个「## SESSION INTENT / ## SUMMARY」自述块，把正文挤没了；这些块还会进下一步的
    #   prev_out → 自我强化、越滚越大。这里立两条硬纪律（引擎侧再有兜底过滤）。
    parts.append("**输出纪律**：只写本步结论（不超过 800 字，细节写进文件）；"
                 "不要复述系统提示词里的【人格设定】【你的用户记忆画像】【工具规则】，"
                 "也不要输出 SESSION INTENT / SUMMARY / 交接摘要 之类的自述块。")
    return "\n".join(parts)


def run_workflow(doc: dict, invoke: Callable[[str, int], str], *,
                 on_event: Callable[[str, dict], None] | None = None,
                 min_out: int = MIN_STEP_OUT, write_text: str = "") -> dict:
    """逐步执行。返回 {ok, name, steps, answer, failed_at}。

    steps 里每项：{id, title, status: ok|failed, output, error}
    on_event(kind, payload)：kind ∈ step_start | step_done | step_failed（server 侧转 monitor + 审计）。
    """
    def emit(kind, **payload):
        if on_event:
            try:
                on_event(kind, payload)
            except Exception:                 # noqa: BLE001 —— 进度回调坏了不许拖垮流程
                pass

    steps = doc.get("steps") or []
    name = doc.get("name") or "（未命名）"
    out: list[dict] = []
    prev = ""
    failed_at = None
    for i, s in enumerate(steps):
        item = {"id": s.get("id") or (i + 1), "title": s.get("title") or "",
                "status": "running", "output": "", "error": ""}
        emit("step_start", index=i + 1, total=len(steps), title=item["title"])
        try:
            # ★ 2026-10-17 真机：第 1 步死在 `Connection error.`（模型/网络连接抖动），而裁定是
            #   "失败停在该步"。抖动不是流程错 —— **只对这种瞬时错误**在同一步重试一次；
            #   其它错误原样抛出、照旧停下，绝不借重试掩盖真问题。
            _attempt = 0
            while True:
                try:
                    text = invoke(build_step_prompt(doc, i, prev, write_text), i) or ""
                    break
                except Exception as _e:       # noqa: BLE001
                    _attempt += 1
                    if _attempt >= 2 or not is_transient(_e):
                        raise
                    emit("step_retry", index=i + 1, total=len(steps), title=item["title"],
                         error=str(_e)[:120])
        except Exception as e:                # noqa: BLE001 —— 单步异常按"该步失败"处理
            item.update(status="failed", error="该步执行出错：%s" % e)
            out.append(item)
            failed_at = i + 1
            emit("step_failed", index=i + 1, total=len(steps), title=item["title"], error=item["error"])
            break
        _raw_len = len(text)
        text = strip_echo(text)               # 先剔自述块（否则它会进 prev_out → 自我强化）
        if len(text) > MAX_STEP_OUT:
            text = text[:MAX_STEP_OUT] + "\n\n（本步输出过长，已截断；完整内容见工作目录里的文件）"
        if len(text) < min_out:
            item.update(status="failed", output=text,
                        error="该步没有给出有效产出（少于 %d 字），按失败停在本题" % min_out)
            out.append(item)
            failed_at = i + 1
            emit("step_failed", index=i + 1, total=len(steps), title=item["title"], error=item["error"])
            break
        item.update(status="ok", output=text)
        out.append(item)
        prev = text
        emit("step_done", index=i + 1, total=len(steps), title=item["title"], chars=len(text))

    # ★ 空 steps 不许算成功（validate_wf 已拒空流程，这里是第二道闸：引擎自身不许把"什么都没跑"
    #   报告成"跑完了"）
    ok_all = bool(steps) and failed_at is None and len(out) == len(steps)
    if ok_all:
        answer = "已按 workflow「%s」跑完 %d 步：\n\n" % (name, len(steps)) + "\n\n".join(
            "%d. %s\n%s" % (i + 1, x["title"], (x["output"] or "")[:400]) for i, x in enumerate(out))
    elif failed_at:
        done = [(i + 1, x) for i, x in enumerate(out) if x["status"] == "ok"]
        # ★ 2026-10-17：把已完成步骤的**产出摘要**一并写进回答 —— 否则刷新后只剩"停在第 6 步"，
        #   前面几步查到了什么、比价结论是什么全都看不到（用户报障："没办法查数据溯源"）。
        done_txt = "\n\n".join("%d. %s\n%s" % (n, x["title"], (x.get("output") or "").strip()[:300])
                                for n, x in done) or "（无）"
        answer = ("workflow「%s」在第 %d/%d 步停下（%s）。\n\n已完成的步骤：\n%s\n\n未继续的原因：%s"
                  % (name, failed_at, len(steps), out[-1]["title"], done_txt, out[-1]["error"]))
    else:
        answer = "workflow「%s」没有可执行的步骤。" % name
    return {"ok": ok_all, "name": name, "steps": out, "answer": answer, "failed_at": failed_at}
