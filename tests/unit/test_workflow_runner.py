# -*- coding: utf-8 -*-
"""06 · workflow 引擎单测（纯函数级，喂假 invoke）。

**为什么要有它**：引擎是"流程固定"这件事的唯一保障 —— 钉住三条：
  ① **代码逐步驱动**：一步一次 invoke，且**单步提示里不泄漏后续步骤**（泄漏了模型就会抢跑，
     "流程固定"名存实亡）；
  ② **失败停在该步**（抛错 / 空产出都算失败），不跳步，已完成步骤保留；
  ③ 进度事件按 step_start → step_done/step_failed 顺序发出（前端步骤卡靠它渲染）。
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from agent import workflow_runner as wr  # noqa: E402

DOC = {"name": "取价流程", "desc": "", "steps": [
    {"id": 1, "title": "定位官网", "use": ["db_agent"], "output": "URL 列表"},
    {"id": 2, "title": "进站取价", "use": ["search_then_browse"], "output": "价格与来源"},
]}


def test_step_prompt_has_this_step_only():
    p = wr.build_step_prompt(DOC, 0)
    assert "第 1/2 步" in p and "定位官网" in p and "db_agent" in p and "URL 列表" in p
    assert "进站取价" not in p, "★ 单步提示不许泄漏后面的步骤（否则模型会抢跑）"
    p2 = wr.build_step_prompt(DOC, 1, prev_out="上一步给了：pdd.com")
    assert "第 2/2 步" in p2 and "pdd.com" in p2 and "不要重复做" in p2
    assert "定位官网" not in p2


def test_all_steps_ok():
    calls = []
    def inv(prompt, i):
        calls.append(i)
        return "第%d步的产出内容" % (i + 1)
    ev = []
    r = wr.run_workflow(DOC, inv, on_event=lambda k, d: ev.append((k, d.get("index"))))
    assert r["ok"] and r["failed_at"] is None
    assert calls == [0, 1], "一步一次、按顺序"
    assert [s["status"] for s in r["steps"]] == ["ok", "ok"]
    assert "跑完 2 步" in r["answer"] and "定位官网" in r["answer"]
    assert [e[0] for e in ev] == ["step_start", "step_done", "step_start", "step_done"]


def test_step_error_stops_at_that_step():
    def inv(prompt, i):
        if i == 1:
            raise RuntimeError("boom")
        return "第一步产出足够长"
    ev = []
    r = wr.run_workflow(DOC, inv, on_event=lambda k, d: ev.append(k))
    assert r["ok"] is False and r["failed_at"] == 2
    assert [s["status"] for s in r["steps"]] == ["ok", "failed"]
    assert "在第 2/2 步停下" in r["answer"] and "已完成的步骤" in r["answer"] and "boom" in r["answer"]
    assert ev[-1] == "step_failed"


def test_short_output_counts_as_failure():
    def inv(prompt, i):
        return "好的" if i == 0 else "详细产出写在这里" * 3
    r = wr.run_workflow(DOC, inv, min_out=8)
    assert r["ok"] is False and r["failed_at"] == 1
    assert "没有给出有效产出" in r["steps"][0]["error"]
    assert len(r["steps"]) == 1, "失败即停，后面的步骤不跑"


def test_first_step_failure_keeps_no_done_steps():
    r = wr.run_workflow(DOC, lambda p, i: "", min_out=8)
    assert r["ok"] is False and r["failed_at"] == 1
    assert "（无）" in r["answer"]


def test_event_callback_errors_do_not_break_run():
    def bad(k, d):
        raise RuntimeError("进度回调坏了")
    r = wr.run_workflow(DOC, lambda p, i: "足够长的产出内容", on_event=bad)
    assert r["ok"] is True, "进度回调坏了不许拖垮流程"


def test_empty_steps_document():
    r = wr.run_workflow({"name": "空流程", "steps": []}, lambda p, i: "x")
    assert r["ok"] is False and "没有可执行的步骤" in r["answer"]


# ---------------------------------------------- 连接抖动：只重试瞬时错误（2026-10-17 真机）
def test_transient_error_retries_once_and_succeeds():
    """真机死因：第 1 步 `Connection error.`。抖动不是流程错 → 同一步重试一次。"""
    calls = {"n": 0}
    def invoke(prompt, i):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("Connection error.")
        return "第一步的有效产出，足够长。"
    doc = {"name": "T", "steps": [{"id": "a", "title": "第一步", "use": [], "output": "x"}]}
    events = []
    r = wr.run_workflow(doc, invoke, on_event=lambda k, p: events.append(k))
    assert r["ok"] is True and calls["n"] == 2
    assert "step_retry" in events and "step_failed" not in events


def test_transient_error_still_fails_after_one_retry():
    calls = {"n": 0}
    def invoke(prompt, i):
        calls["n"] += 1
        raise RuntimeError("Connection error.")
    doc = {"name": "T", "steps": [{"id": "a", "title": "第一步", "use": [], "output": "x"}]}
    r = wr.run_workflow(doc, invoke)
    assert r["ok"] is False and calls["n"] == 2 and r["failed_at"] == 1


def test_real_error_is_not_retried():
    """非瞬时错误（工具/参数/逻辑）绝不重试 —— 重试会掩盖真问题，也违背"停在该步"。"""
    calls = {"n": 0}
    def invoke(prompt, i):
        calls["n"] += 1
        raise ValueError("参数不对：缺 id")
    doc = {"name": "T", "steps": [{"id": "a", "title": "第一步", "use": [], "output": "x"}]}
    r = wr.run_workflow(doc, invoke)
    assert r["ok"] is False and calls["n"] == 1 and r["failed_at"] == 1


def test_is_transient_classifier():
    assert wr.is_transient("Connection error.")
    assert wr.is_transient("Read timed out")
    assert wr.is_transient("HTTP 503 Service Unavailable")
    assert not wr.is_transient("参数不对：缺 id")
    assert not wr.is_transient("")
    # ★ 真机报错是中文的：只认英文特征等于不重试（2026-10-17 踩到）
    assert wr.is_transient("连接时长过长被上游切断")
    assert wr.is_transient("请求超时")


def test_step_prompt_says_next_step_is_automatic():
    """★ 2026-10-17 真机：原措辞让模型以为"等用户发话"，还反问 —— 引擎没有中途对话机制。

    单步提示要明说"下一步由程序自动继续"，并禁止等待式措辞/反问。
    """
    p = wr.build_step_prompt(DOC, 0)
    assert "自动接着执行" in p and "不要" in p and "等你发话" in p
    assert "不要反问" in p
    assert "不要自行开始后面的步骤" not in p, "旧措辞会让模型停下来等用户（已废弃）"


# ---------------------------------------------- 自述块 / 超长输出（2026-10-17 真机 45k 字回复）
def test_strip_echo_removes_self_summary_blocks():
    """真机：一份回复里 11 个 `## SESSION INTENT` / `## SUMMARY`，里面整段复述人格与画像。"""
    t = ("第 2 步结论：抓到 15 条。\n\n## SESSION INTENT\n老大正在执行 workflow…\n\n"
         "## SUMMARY\n### 人格设定（必须保持）\n- 女仆\n\n### 用户画像\n- 实习生\n\n"
         "## 真正的结论\n保留这行")
    o = wr.strip_echo(t)
    for bad in ("SESSION INTENT", "SUMMARY", "人格设定", "用户画像"):
        assert bad not in o, bad
    assert "抓到 15 条" in o and "保留这行" in o


def test_step_output_is_capped_and_echo_stripped():
    long = "正文" * 5000 + "\n\n## SESSION INTENT\n自述块"
    doc = {"name": "T", "steps": [{"id": "a", "title": "一步", "use": [], "output": "x"}]}
    r = wr.run_workflow(doc, lambda p, i: long)
    assert r["ok"] is True
    out = r["steps"][0]["output"]
    assert "SESSION INTENT" not in out
    assert len(out) <= wr.MAX_STEP_OUT + 60 and "已截断" in out


def test_success_answer_is_summarized_per_step():
    """成功回答原来把每步全文拼进去（10 步 × 4k = 糊满气泡），现在每步只留 400 字。"""
    doc = {"name": "T", "steps": [{"id": i, "title": "步%d" % i, "use": [], "output": "x"}
                                  for i in range(1, 4)]}
    r = wr.run_workflow(doc, lambda p, i: "内容" * 500)
    assert r["ok"] and len(r["answer"]) < 3 * 450


def test_step_prompt_forbids_echo_and_caps_length():
    p = wr.build_step_prompt(DOC, 0)
    assert "输出纪律" in p and "SESSION INTENT" in p and "800 字" in p and "人格设定" in p


# ---------------------------------------------- 写能力工作流（2026-10-18 约定在 bug 维护与优化期做）
WRITE_TXT = "【本轮经「/」授权的写工具集】\n- GitHub Issues（api 来源 `github`）：用 invoke_tool 调用"


def test_step_prompt_carries_write_sets_text():
    """workflow 声明的写工具集 → 每一步提示里都要带授权说明书（否则模型不知道 ref 与调用方式）。"""
    p = wr.build_step_prompt(DOC, 0, write_text=WRITE_TXT)
    assert "授权的写工具集" in p and "GitHub Issues" in p
    assert "第 1/2 步" in p, "原有限制（只描述本步）不能被破坏"


def test_step_prompt_without_write_text_unchanged():
    assert "授权的写工具集" not in wr.build_step_prompt(DOC, 0)


def test_run_workflow_passes_write_text_to_every_step():
    seen = []

    def inv(prompt, i):
        seen.append(prompt)
        return "第%d步的产出内容很充实，够长了" % (i + 1)

    r = wr.run_workflow(DOC, inv, write_text=WRITE_TXT)
    assert r["ok"] is True and len(seen) == 2
    assert all("授权的写工具集" in p for p in seen)
