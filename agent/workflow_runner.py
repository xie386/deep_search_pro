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

from typing import Callable

MIN_STEP_OUT = 8          # 一步产出少于这么多字就视为"没干活"（防"我已完成"式空转）


def build_step_prompt(doc: dict, i: int, prev_out: str = "") -> str:
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
    if s.get("output"):
        parts.append("本步期望产出：%s" % s["output"])
    if prev_out:
        parts.append("上一步的产出（供你衔接，不要重复做）：\n%s" % prev_out.strip()[:2000])
    parts.append("只完成这一步，给出本步结论即可；**不要**自行开始后面的步骤。")
    return "\n".join(parts)


def run_workflow(doc: dict, invoke: Callable[[str, int], str], *,
                 on_event: Callable[[str, dict], None] | None = None,
                 min_out: int = MIN_STEP_OUT) -> dict:
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
            text = invoke(build_step_prompt(doc, i, prev), i) or ""
        except Exception as e:                # noqa: BLE001 —— 单步异常按"该步失败"处理
            item.update(status="failed", error="该步执行出错：%s" % e)
            out.append(item)
            failed_at = i + 1
            emit("step_failed", index=i + 1, total=len(steps), title=item["title"], error=item["error"])
            break
        text = text.strip()
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
            "%d. %s\n%s" % (i + 1, x["title"], x["output"]) for i, x in enumerate(out))
    elif failed_at:
        done = [x for x in out if x["status"] == "ok"]
        answer = ("workflow「%s」在第 %d/%d 步停下（%s）。\n\n已完成的步骤：\n%s\n\n未继续的原因：%s"
                  % (name, failed_at, len(steps), out[-1]["title"],
                     "\n".join("%d. %s" % (i + 1, x["title"]) for i, x in enumerate(done)) or "（无）",
                     out[-1]["error"]))
    else:
        answer = "workflow「%s」没有可执行的步骤。" % name
    return {"ok": ok_all, "name": name, "steps": out, "answer": answer, "failed_at": failed_at}
