# -*- coding: utf-8 -*-
"""「Agent 执行出错」这条链路的**文案与留证**（用户 2026-10-11 真机实测事故）。

事故：真机测评里某一题卡死 4 分钟后返回 500，前端显示
  `Agent 执行出错：模型调用失败（用的是项目默认模型，来自 .env）`
  `RuntimeError: cannot schedule new futures after shutdown`
而用户当时用的**明明是定制助手的自定义模型（DeepSeek）** —— 文案在说谎，且只有一行 message、
服务端日志也翻不到 traceback，根因无从考据。这个文件钉死两条：
  ① 文案必须说**真实生效的模型配置**（自定义配置要报 id/厂商/模型名；默认要报模型名）；
  ② `cannot schedule …` 这类**进程级故障**不许再说「模型调用失败」误导用户去查 key；
  ③ 出错必须**落完整 traceback**（`AGENT_ERROR_LOG` 可覆盖路径，便于测试 ✓）。

跑法：`pytest tests/unit/test_agent_error_report.py -q`
"""
import json
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
os.environ.pop("PYTHONPATH", None)

from api import server  # noqa: E402

ok, bad = [], []


def check(name, cond, extra=""):
    (ok if cond else bad).append(name)
    print("%s %s%s" % ("  ✅" if cond else "  ❌", name, (" | " + str(extra)) if extra else ""))


def test_agent_error_report():
    # ---- ① 文案说真话：自定义配置 ----
    prov = {"id": 7, "provider_name": "DeepSeek 官方", "model_name": "deepseek-chat"}
    t = server._llm_error_hint(RuntimeError("401 invalid api key"), provider=prov)
    check("自定义配置：报出 id/厂商/模型名",
          "自定义配置" in t and "#7" in t and "DeepSeek 官方" in t and "deepseek-chat" in t, t[:110])

    # ---- ② 项目默认：也报出**模型名**（原来只说「项目默认」）----
    _sv = os.environ.get("LLM_MODEL_MAX")
    os.environ["LLM_MODEL_MAX"] = "agnes-默认模型-x"
    try:
        t = server._llm_error_hint(RuntimeError("402 insufficient balance"), provider=None)
    finally:
        if _sv is None:
            os.environ.pop("LLM_MODEL_MAX", None)
        else:
            os.environ["LLM_MODEL_MAX"] = _sv
    check("项目默认：报出 .env 里的模型名（不再含糊）",
          "项目默认模型" in t and "agnes-默认模型-x" in t, t[:110])

    # ---- ③ 进程级故障不许甩锅给模型（用户实测的那条）----
    msg = "cannot schedule new futures after shutdown"
    t = server._llm_error_hint(RuntimeError(msg), provider=prov)
    check("cannot schedule：不再说「模型调用失败」，而是指出服务关闭/重启",
          "cannot schedule" not in t and "模型调用失败" not in t and "服务关闭" in t, t[:130])
    check("cannot schedule：文案里带上当时的模型配置（便于判断是不是模型侧）",
          "deepseek-chat" in t, t[:130])
    t2 = server._llm_error_hint(RuntimeError("Event loop is closed"), provider=None)
    check("Event loop is closed：同样归到「服务关闭」分支", "服务关闭" in t2, t2[:110])

    # ---- ④ 出错必须落完整 traceback ----
    tmp = os.path.join(tempfile.gettempdir(), "agent_errors_test.jsonl")
    if os.path.exists(tmp):
        os.remove(tmp)
    _sv2 = os.environ.get("AGENT_ERROR_LOG")
    os.environ["AGENT_ERROR_LOG"] = tmp
    try:
        try:
            raise RuntimeError("cannot schedule new futures after shutdown")
        except RuntimeError as e:
            server._log_agent_error(e, thread_id="tid-1", account_id=3, question="看看B站视频")
    finally:
        if _sv2 is None:
            os.environ.pop("AGENT_ERROR_LOG", None)
        else:
            os.environ["AGENT_ERROR_LOG"] = _sv2
    rec = {}
    if os.path.isfile(tmp):
        with open(tmp, encoding="utf-8") as f:
            rec = json.loads(f.readline())
        os.remove(tmp)
    check("出错留证：落 jsonl（含 type/message/traceback/题目/线程）",
          rec.get("type") == "RuntimeError" and "cannot schedule" in rec.get("message", "")
          and rec.get("thread_id") == "tid-1" and rec.get("account_id") == 3
          and "Traceback" in rec.get("traceback", ""), list(rec))
    check("出错留证：traceback 指到真正的抛出点（含 raise 语句所在文件）",
          "test_agent_error_report" in rec.get("traceback", ""), rec.get("traceback", "")[-120:])

    print("\n" + "=" * 62)
    print("通过 %d ／ 失败 %d" % (len(ok), len(bad)))
    for n in bad:
        print("  -", n)
    assert not bad, "报错文案/留证自测失败 %d 项：%s" % (len(bad), "；".join(bad))
