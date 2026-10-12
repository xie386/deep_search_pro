"""v4.0 大点 03：probe_agent 的**离线自测**（不调模型 ✓ `invoke` 用桩替换）。

跑法：`pytest tests/unit/test_probe_agent.py -q`
覆盖：提示词段可读、JSON 抽取（含代码围栏/前后噪声）、ref 归一、出题硬规则（覆盖全部工具）、
      出题/判分/建议三段**流程**与**入参结构**（判分段拿不到出题推理 ✓）、题集落盘读回。
"""
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
os.environ.pop("PYTHONPATH", None)

from tools import probe_agent as pa  # noqa: E402
from tools import probe_runner as pr  # noqa: E402


class _Ent:
    def __init__(self, ref, name=""):
        self.ref = ref
        self.name = name
        self.card_text = "【卡片】%s %s" % (ref, name)


def test_probe_agent_offline():
    ok, bad = [], []

    def check(name, cond, extra=""):
        (ok if cond else bad).append(name)
        print("%s %s%s" % ("  ✅" if cond else "  ❌", name, (" | " + str(extra)) if extra else ""))

    # ---- 1. 提示词段 ----
    for sec in ("probe_writer", "probe_judge", "probe_advisor"):
        txt = pa.prompt_of(sec)
        check("提示词段可读：%s" % sec, len(txt) > 300, len(txt))
    check("非测评段被拒", _raises(lambda: pa.prompt_of("main_agent")))
    check("判分段声明「只预填」", "预填" in pa.prompt_of("probe_judge"))
    check("建议段禁止改代码/阈值", "不要**建议改代码" in pa.prompt_of("probe_advisor")
          or "不要*" in pa.prompt_of("probe_advisor"))
    check("出题段要求逐字照抄 ref", "逐字" in pa.prompt_of("probe_writer"))

    # ---- 2. JSON 抽取 ----
    check("裸 JSON", pa.json_of('{"a": 1}') == {"a": 1})
    check("代码围栏 JSON", pa.json_of('```json\n{"a": 1}\n```') == {"a": 1})
    check("前后噪声 JSON", pa.json_of('好的，结果如下：\n{"cases": []}\n以上。') == {"cases": []})
    check("无 JSON 抛错", _raises(lambda: pa.json_of("没有花括号")))

    # ---- 3. ref 归一（CLI 池条目的 ref 是裸可执行名 ✓）----
    check("cli 裸名归一", pa.canon_ref("weread") == "cli:weread")
    check("api 保持原样", pa.canon_ref("api:steam#searchGame") == "api:steam#searchGame")
    check("mcp 保持原样", pa.canon_ref("mcp:pdd/search_goods") == "mcp:pdd/search_goods")

    # ---- 4. 出题硬规则：每个 ref 至少 1 道单工具题 ----
    ents = [_Ent("weread"), _Ent("mcp:pdd/search_goods")]
    full = {"cases": [
        {"id": 1, "kind": "single", "q": "我最近在读什么", "expect": {"kind": "cli", "bin": "weread"}},
        {"id": 2, "kind": "single", "q": "拼多多搜一下手机壳", "expect": {"kind": "tool", "ref": "mcp:pdd/search_goods"}},
    ]}
    check("覆盖齐 → 无错", pa.coverage_errors(full, ents) == [])
    short = {"cases": [full["cases"][0]]}
    errs = pa.coverage_errors(short, ents)
    check("漏覆盖 → 报错并点名", bool(errs) and "mcp:pdd/search_goods" in errs[0], errs)

    # ---- 5. 出题流程（invoke 打桩，不调模型；**分批**后每批一次调用）----
    seen = {}
    all_msgs = {"probe_writer": []}
    calls = {"stages": []}

    def fake_invoke(section, user_msg, model=None, stage=""):
        calls["stages"].append(stage)
        seen[section] = user_msg
        all_msgs.setdefault(section, []).append(user_msg)
        if section == "probe_writer":
            return '```json\n%s\n```' % json.dumps({"cases": [
                {"id": 1, "kind": "single", "q": "我最近在读什么",
                 "expect": {"kind": "cli", "bin": "weread"}, "chain": "weread shelf recent"},
                {"id": 2, "kind": "single", "q": "拼多多搜一下手机壳",
                 "expect": {"kind": "tool", "ref": "mcp:pdd/search_goods"}, "chain": "search_goods"},
            ]}, ensure_ascii=False)
        if section == "probe_judge":
            return json.dumps({"scores": [{"id": 1, "verdict": "✓", "why": "链对", "conf": "高"}]})
        return "- 工具：weread\n- 字段：keywords\n- 现状：（空）\n- 建议：补上「我在读」等"

    real_invoke = pa.invoke
    real_card_block = pa.card_block
    pa.invoke = fake_invoke
    pa.card_block = lambda acc: ("【卡片】weread\n【卡片】mcp:pdd/search_goods", ents)
    progress = []
    try:
        g = pa.gen_cases(1, "单元测试账号", prev_questions=["往期题面甲"],
                         on_progress=lambda *a: progress.append(a))
        check("出题：无校验错", g["errors"] == [], g["errors"])
        check("出题：条数带出", g["count"] == 2 and g["multi"] == 2, (g["count"], g["multi"]))
        check("出题：**分批**后阶段出现顺序 = 单工具批 → 多工具 → 噪声 → 个人数据核查",
              list(dict.fromkeys(p[0] for p in progress)) == ["single", "multi", "noise", "privacy"],
              [p[0] for p in progress])
        check("出题：进度回调带批次与工具名（前端进度条的数据源）",
              "第 1/1 批" in str(progress[0][4]) and "weread" in str(progress[0][4]),
              progress[0][4])
        check("出题：一轮调用次数 = 批数 + 3（多工具 / 噪声 / 个人数据核查各一次）",
              len(calls["stages"]) == 4, calls["stages"])
        check("出题：每次调用都带阶段名（日志/进度靠它）",
              all(calls["stages"]) and calls["stages"][0].startswith("单工具题"), calls["stages"])
        check("出题：题面重复会被合并（桩每次返回同两题 → 仍是 2 题）", g["count"] == 2, g["count"])
        check("出题：编号连续 1..N", [c["id"] for c in g["doc"]["cases"]] == [1, 2])
        check("出题：多工具题量写进提示（覆盖系统段的公式）",
              any("只出多工具题 2 条" in m for m in all_msgs["probe_writer"]),
              [m[-80:] for m in all_msgs["probe_writer"]])
        check("出题：卡片文本进提示", any("【卡片】weread" in m for m in all_msgs["probe_writer"]))
        check("出题：往期题面进提示（防重复）", any("往期题面甲" in m for m in all_msgs["probe_writer"]))
        check("出题：单工具批的提示只带本批卡片（分批真的缩小了输入）",
              any("本批共 2 个工具" in m for m in all_msgs["probe_writer"]),
              sum(1 for m in all_msgs["probe_writer"] if "本批共" in m))

        # ---- 5b. 分批真的按批切（8 个工具 → 2 批 → 调用数 = 2+2）----
        ents8 = [_Ent("mcp:t%d" % i, "工具%d" % i) for i in range(1, 9)]
        pa.card_block = lambda acc: ("\n".join("【卡片】mcp:t%d" % i for i in range(1, 9)), ents8)
        b1 = {"cases": [{"id": i, "kind": "single", "q": "问题 t%d" % i,
                         "expect": {"kind": "tool", "ref": "mcp:t%d" % i}} for i in range(1, 9)]}
        pa.invoke = lambda section, msg, model=None, stage="": (
            calls["stages"].append(stage) or json.dumps(b1, ensure_ascii=False))
        prog2 = []
        g8 = pa.gen_cases(1, "批测试", on_progress=lambda *a: prog2.append(a))
        _labs = set()
        for p in prog2:
            for m in re.findall(r"第 \d+/\d+ 批", str(p[4])):
                _labs.add(m)
        check("分批：8 个工具 → 2 批（每批 %d）" % pa.SINGLE_BATCH, len(_labs) == 2, sorted(_labs))
        check("分批：进度 total = 批数 + 3 = 5（多工具 / 噪声 / 个人数据核查 ✓）",
              all(p[3] == 5 for p in prog2), [p[3] for p in prog2])
        check("并发：同一快照里出现两批同时在跑（detail 同时含两批标签）",
              any("第 1/2 批" in str(p[4]) and "第 2/2 批" in str(p[4]) for p in prog2),
              [str(p[4])[:60] for p in prog2])
        check("并发：默认并发数 = min(3, 批数) = 2", g8["concurrency"] == 2, g8["concurrency"])
        _dones = [p[2] for p in prog2]
        check("进度：done 单调不减且最后达到 total",
              _dones == sorted(_dones) and max(_dones) == 5, _dones)
        check("分批：8 题全部覆盖 → 无校验错", g8["errors"] == [], g8["errors"])
        calls["stages"] = []
        pa.invoke = fake_invoke

        # ---- 5c. **部分成功**：一批失败不该扔掉其它批（2026-10-10 追修）----
        def flaky_batch(section, msg, model=None, stage=""):
            if "本批共" in msg and "mcp:t8" in msg:   # 只有**第 2 批的单工具请求**失败（t7/t8）
                raise pa.ProbeGenError("%s：模型返回**空内容**" % stage)
            if "本批共" in msg:
                return json.dumps({"cases": [c for c in b1["cases"]
                                             if c["expect"]["ref"] in msg]}, ensure_ascii=False)
            return json.dumps({"cases": []})          # 多工具/噪声这次没产出 → 触发对应告警
        pa.invoke = flaky_batch
        prog3 = []
        g_p = pa.gen_cases(1, "部分成功", on_progress=lambda *a: prog3.append(a))
        check("部分成功：不抛错、照常返回题目", g_p["count"] >= 6, g_p["count"])
        check("部分成功：失败批次进 warnings（点名哪一批）",
              any("第 2/2 批" in w for w in g_p["warnings"]), g_p["warnings"])
        check("部分成功：缺噪声题 / 多工具题不足也各自告警",
              any("噪声题 0 条" in w for w in g_p["warnings"])
              and any("多工具题只有 0 条" in w for w in g_p["warnings"]), g_p["warnings"])
        check("部分成功：硬规则点名未覆盖的工具（t7/t8）",
              any("t7" in e for e in g_p["errors"]), g_p["errors"])
        pa.invoke = lambda section, msg, model=None, stage="": (
            calls["stages"].append(stage) or (_ for _ in ()).throw(
                pa.ProbeGenError("%s：模型返回**空内容**" % stage)))
        try:
            _raised = False
            try:
                pa.gen_cases(1, "全失败")
            except pa.ProbeGenError:
                _raised = True
            check("全部批失败 → 才抛错（不能让用户拿到空题集）", _raised)
        finally:
            pa.invoke = fake_invoke

        # ---- 5d. 空内容 → 「不要思考、直接给 JSON」自修复一次 ----
        _seq2 = []

        def empty_then_ok(section, msg, model=None, stage=""):
            _seq2.append(stage)
            if len(_seq2) == 1:
                raise pa.ProbeGenError("%s：模型返回**空内容**（373.7s；思考字段 13154 字）" % stage)
            return '{"ok": true}'

        pa.invoke = empty_then_ok
        try:
            out2 = pa.invoke_json("probe_writer", "x", stage="空内容用例")
            check("空内容自修复：重试一次后成功", out2 == {"ok": True} and len(_seq2) == 2, _seq2)
            check("空内容自修复：重试带「空内容重试」阶段名",
                  len(_seq2) == 2 and _seq2[1].endswith("·空内容重试"), _seq2)
        finally:
            pa.invoke = fake_invoke

        run = {"rows": [
            {"id": 1, "kind": "single", "q": "我最近在读什么",
             "expect": {"kind": "cli", "bin": "weread"},
             "calls": [{"brief": "weread shelf recent"}], "unmeasured": False},
            {"id": 3, "kind": "noise", "q": "你是谁", "expect": None, "calls": [], "unmeasured": False},
        ]}
        j = pa.judge_run(run)
        check("判分：解析出预填", j["scores"].get(1, {}).get("verdict") == "✓", j["scores"])
        check("判分：入参含题面/期望/实际调用",
              all(x in seen["probe_judge"] for x in ("题面", "期望工具", "实际调用序列")))
        check("判分：噪声题被标出", "类型：噪声题" in seen["probe_judge"])
        check("判分：**不含**出题推理", "往期题面甲" not in seen["probe_judge"])

        a = pa.advise(1, run, {1: {"verdict": "✓", "note": "好"}})
        check("建议：有返回", "建议" in a["text"])
        check("建议：入参含素材与判定", "【工具池素材】" in seen["probe_advisor"]
              and "判定：✓" in seen["probe_advisor"])

        # ---- 6. 题集落盘 / 读回 / 清除（data/probe_sets，产品数据）----
        who = "_probe_agent_selftest"
        p = pa.save_cases(who, g["doc"])
        try:
            check("题集落 data/probe_sets/", "data%sprobe_sets" % os.sep in p, p)
            back = pa.load_cases(who)
            check("读回一致", [c["q"] for c in back["cases"]] == [c["q"] for c in g["doc"]["cases"]])
            check("runner 也能按名读到它（③ 查找顺序）",
                  len(pr.load_case_set(who)["cases"]) == 2)
            check("清除成功", pa.clear_cases(who) and not pa.load_cases(who))
        finally:
            pa.clear_cases(who)

        # ---- 7. JSON 加固与自修复重试（★ 2026-10-10 用户实测两次出题失败后新增）----
        check("加固：尾逗号", pa.json_of('{"a": 1,}') == {"a": 1})
        check("加固：中文引号", pa.json_of('{"q": “标题”}') == {"q": "标题"})
        check("加固：空内容报错带「空内容」字样",
              _raises_msg(lambda: pa.json_of(""), "空内容"))
        # 截断：按括号配平抢救出**完整**的对象（最后一个不完整的自动丢掉）
        salv = pa.json_of('{"cases": [{"id": 1, "kind": "single", "q": "甲", "expect": null},'
                          ' {"id": 2, "kind": "sin')
        check("加固：截断时按括号配平抢救完整对象",
              salv.get("_salvaged") and len(salv["cases"]) == 1
              and salv["cases"][0]["q"] == "甲", salv)
        seq = []

        def flaky(section, user_msg, model=None, stage=""):
            seq.append(stage)
            if len(seq) == 1:
                return '{"cases": [{"id": 1, "q": "题目一'          # 完全没有配平对象 → 必须重试
            return '{"ok": true}'

        pa.invoke = flaky
        try:
            out = pa.invoke_json("probe_writer", "x", stage="重试用例")
            check("自修复：坏 JSON → 自动重试并成功", out == {"ok": True} and len(seq) == 2, seq)
            check("自修复：重试调用带「修复重试」阶段名",
                  len(seq) == 2 and seq[1].endswith("·修复重试"), seq)
        finally:
            pa.invoke = fake_invoke

        # 空内容：走**真实** invoke（只把 model 换成桩）→ 应点明「空内容」并提示换模型
        class _EmptyModel:
            def invoke(self, msgs):
                class _R:
                    content = ""
                    additional_kwargs = {"reasoning_content": "想了很多但没写答案"}
                return _R()

        check("空内容：报错点明「空内容」并提示换模型",
              _raises_msg(lambda: real_invoke("probe_writer", "x", model=_EmptyModel(),
                                              stage="空内容用例"), "空内容"))
    finally:
        pa.invoke, pa.card_block = real_invoke, real_card_block

    # ---- 8. expect 归一（快模型的两种写法错）+ 可验证补漏 ----
    pool3 = [_Ent("weread", "微信读书"), _Ent("bili", "哔哩哔哩CLI"),
             _Ent("mcp:pdd/search_goods", "拼多多搜商品")]
    cs = [
        {"id": 1, "kind": "single", "q": "a", "expect": {"kind": "tool", "ref": "weread"}},
        {"id": 2, "kind": "single", "q": "b", "expect": {"kind": "cli", "bin": "微信读书"}},
        {"id": 3, "kind": "single", "q": "c",
         "expect": {"kind": "tool", "ref": "mcp:pdd/search_goods"}},
        {"id": 4, "kind": "single", "q": "d", "expect": {"kind": "tool", "ref": "不存在的东西"}},
        {"id": 5, "kind": "noise", "q": "e", "expect": None},
    ]
    nfix = pa.normalize_expects(cs, pool3)
    check("归一：CLI 的 tool→cli（补 argv）",
          cs[0]["expect"] == {"kind": "cli", "bin": "weread", "argv": []}, cs[0]["expect"])
    check("归一：中文显示名 → 池内 ref",
          cs[1]["expect"] == {"kind": "cli", "bin": "weread", "argv": []}, cs[1]["expect"])
    check("归一：本来就对的 api/mcp ref 不动",
          cs[2]["expect"] == {"kind": "tool", "ref": "mcp:pdd/search_goods"})
    check("归一：认不出的**不猜**（留给硬规则报错）",
          cs[3]["expect"]["ref"] == "不存在的东西" and nfix == 2, nfix)
    check("归一：噪声题（expect=None）不碰", cs[4]["expect"] is None)
    _err = pa.coverage_errors({"cases": cs[:3]}, pool3)
    check("归一后覆盖判定能对上（weread 已认作已覆盖，只剩真没题的 cli:bili）",
          len(_err) == 1 and "cli:bili" in _err[0] and "cli:weread" not in _err[0], _err)

    # 补漏：桩少给一个工具 → 应触发**一次**补漏调用，补完不留缺口
    pa.card_block = lambda acc: ("\n".join("【卡片】mcp:t%d" % i for i in range(1, 9)), ents8)
    seq3 = []

    def partial_stub(section, msg, model=None, stage=""):
        seq3.append(stage)
        refs = sorted(set(re.findall(r"mcp:t\d+", msg)))
        if "补漏" in stage:
            return json.dumps({"cases": [{"id": i, "kind": "single", "q": "补 %s" % r,
                                          "expect": {"kind": "tool", "ref": r}}
                                         for i, r in enumerate(refs, 1)]}, ensure_ascii=False)
        if "本批共" in msg:
            return json.dumps({"cases": [{"id": i, "kind": "single", "q": "Q %s" % r,
                                          "expect": {"kind": "tool", "ref": r}}
                                         for i, r in enumerate(refs[:5], 1)]},
                              ensure_ascii=False)      # 故意少给最后一个
        return json.dumps({"cases": []})

    pa.invoke = partial_stub
    try:
        g_r = pa.gen_cases(1, "补漏测试")
        check("补漏：缺口触发一次补漏调用", any("补漏" in str(s) for s in seq3), seq3)
        check("补漏后不再有「没被覆盖」的硬错误",
              not any("没有被单工具题覆盖" in e for e in g_r["errors"]), g_r["errors"])
    finally:
        pa.invoke = fake_invoke

    # ---- 9. 瞬时错误自动重试（免费模型会 429）----
    class _Boom:
        def invoke(self, msgs):
            raise RuntimeError("Error code: 429 - temporarily rate-limited upstream")

    try:
        real_invoke("probe_writer", "x", model=_Boom(), stage="限流用例", retries=0)
        _ok429 = False
    except pa.ProbeGenError as e:
        _ok429 = ("429" in str(e)) and ("PROBE_LLM_MODEL" in str(e))
    check("瞬时错误：报错带原始原因 + 可操作旋钮（429 不吞）", _ok429)

    # ---- 10. 套装判据（多工具题必须跨套装）+ 备选答案归一 + 噪声避搜索 ----
    check("套装：CLI 裸名 = 自己一个套装", pa.suit_of("weread") == "cli:weread")
    check("套装：MCP 服务器 / API 服务各算一个",
          pa.suit_of("mcp:pdd/search_goods") == "mcp:pdd"
          and pa.suit_of("api:steam#searchGame") == "api:steam")
    _bad = {"cases": [
        {"id": 1, "kind": "multi", "q": "a", "expect": {"kind": "cli", "bin": "bili"},
         "refs": ["bili", "bili"]},
        {"id": 2, "kind": "multi", "q": "b", "expect": {"kind": "cli", "bin": "weread"},
         "refs": ["weread", "mcp:pdd/search_goods"]},
        {"id": 3, "kind": "multi", "q": "c", "expect": {"kind": "cli", "bin": "bili"}, "refs": ["bili"]},
    ]}
    _v = pa.multi_violations(_bad)
    check("多工具判据：同套装两条 → 报违规", any("第 1 条" in v for v in _v), _v)
    check("多工具判据：跨套装 → 通过", not any("第 2 条" in v for v in _v), _v)
    check("多工具判据：refs 不足 2 个 → 「无法验证」", any("第 3 条" in v and "无法验证" in v for v in _v), _v)
    # 对称判据：**单工具题只能用一个套装**（同套装内可含多条子命令 ✓）
    _b2 = {"cases": [
        {"id": 1, "kind": "single", "q": "a", "expect": {"kind": "cli", "bin": "weread"},
         "refs": ["weread"]},
        {"id": 2, "kind": "single", "q": "b", "expect": {"kind": "cli", "bin": "weread"},
         "refs": ["weread", "mcp:pdd/search_goods"]},
    ]}
    _sv = pa.suit_violations(_b2)
    check("单工具判据：一个套装（可多条子命令）→ 通过", len(_sv["single"]) == 1, _sv)
    check("单工具判据：牵涉两个套装 → 点名违规",
          any("第 2 条" in v and "只能用一个套装" in v for v in _sv["single"]), _sv)
    _ad = {"cases": [{"id": 1, "kind": "single", "q": "x", "expect": {"kind": "cli", "bin": "weread"},
                      "alts": [{"kind": "cli", "bin": "微信读书"}, "bili", 42]}]}
    _rd = pa.normalize_refs_and_alts(_ad["cases"], pool3 + [_Ent("bili", "哔哩哔哩CLI")])
    check("备选答案：字典形态被转成 ref、非字符串被丢弃",
          _ad["cases"][0]["alts"] == ["weread", "bili"]
          and any("非字符串" in d for d in _rd["dropped"]),
          (_ad["cases"][0]["alts"], _rd))
    _sm = {"cases": [
        {"id": 1, "kind": "multi", "q": "a", "expect": {"kind": "cli", "bin": "bili"},
         "refs": ["cli:bili", "mcp:12306-mcp"]},
        {"id": 2, "kind": "multi", "q": "b", "expect": {"kind": "cli", "bin": "bili"},
         "refs": ["cli:bili", "bili"]},
    ]}
    _rs = pa.normalize_refs_and_alts(_sm["cases"], pool3)
    check("refs 写 `cli:<名>` → 解析到池内 CLI（不是丢掉）", _sm["cases"][0]["refs"][0] == "bili",
          _sm["cases"][0]["refs"])
    check("refs 只给**套装名**（`mcp:12306-mcp`）→ 保留作套装证据并记账",
          "mcp:12306-mcp" in _sm["cases"][0]["refs"] and bool(_rs["suit_only"]), _rs)
    _v2 = pa.suit_violations(_sm)
    check("套装名当证据：跨套装仍通过 ✓ / 同一套装仍报违规 ✓",
          not any("第 1 条" in v for v in _v2["multi"])
          and any("第 2 条" in v for v in _v2["multi"]), _v2)
    _nw = pa.noise_warnings({"cases": [{"id": 1, "kind": "noise", "q": "给我推荐几道美食"},
                                       {"id": 2, "kind": "noise", "q": "帮我写一首诗"}]})
    check("噪声题：像「需要查资料」的被点名（避免诱发网络搜索）",
          len(_nw) == 1 and "第 1 条" in _nw[0], _nw)
    _na = {"cases": [
        {"id": 1, "kind": "single", "q": "x", "expect": {"kind": "cli", "bin": "weread"},
         "alts": ["微信读书", "mcp:pdd/search_goods", "根本不存在的工具"]},
        {"id": 2, "kind": "multi", "q": "y", "expect": {"kind": "cli", "bin": "bili"},
         "refs": ["哔哩哔哩CLI", "weread"], "alts": []},
    ]}
    _r = pa.normalize_refs_and_alts(_na["cases"], pool3)
    check("备选答案归一：中文名 → 池内 ref、认不出的丢弃并记账",
          _na["cases"][0]["alts"] == ["weread", "mcp:pdd/search_goods"]
          and len(_r["dropped"]) == 1, _r)
    check("refs 归一：中文名 → 池内 ref", _na["cases"][1]["refs"] == ["bili", "weread"],
          _na["cases"][1]["refs"])
    check("题集校验：multi 的 refs 少于 2 个 / alts 非数组 会被拦",
          any("refs 少于 2 个" in e for e in pr.validate_case_set(
              {"cases": [{"id": 1, "kind": "multi", "q": "a",
                          "expect": {"kind": "tool", "ref": "mcp:pdd/search_goods"},
                          "refs": ["mcp:pdd/search_goods"]}]}))
          and any("alts 必须是数组" in e for e in pr.validate_case_set(
              {"cases": [{"id": 1, "kind": "single", "q": "b",
                          "expect": {"kind": "tool", "ref": "mcp:pdd/search_goods"},
                          "alts": "weread"}]})))

    # 多工具题违规 → **自动重出一次**（同套装两条 → 重出为跨套装）
    ents_m = [_Ent("mcp:srv/a"), _Ent("mcp:srv/b"), _Ent("cli:x")]
    pa.card_block = lambda acc: ("卡片", ents_m)
    seq5 = []

    def multi_stub(section, msg, model=None, stage=""):
        seq5.append(stage)
        if "本批共" in msg:
            return json.dumps({"cases": [
                {"id": i, "kind": "single", "q": "Q %s" % e.ref,
                 "expect": ({"kind": "tool", "ref": e.ref} if e.ref.startswith("mcp:")
                            else {"kind": "cli", "bin": e.ref})}
                for i, e in enumerate(ents_m, 1)]}, ensure_ascii=False)
        if "重出" in str(stage):
            return json.dumps({"cases": [{"id": 1, "kind": "multi", "q": "跨套装题",
                                          "expect": {"kind": "tool", "ref": "mcp:srv/a"},
                                          "refs": ["mcp:srv/a", "cli:x"]}]}, ensure_ascii=False)
        if str(stage) == "多工具题":
            return json.dumps({"cases": [{"id": 1, "kind": "multi", "q": "假多工具题",
                                          "expect": {"kind": "tool", "ref": "mcp:srv/a"},
                                          "refs": ["mcp:srv/a", "mcp:srv/b"]}]}, ensure_ascii=False)
        return json.dumps({"cases": [{"id": 1, "kind": "noise", "q": "陪我聊聊天", "expect": None}]},
                          ensure_ascii=False)

    pa.invoke = multi_stub
    try:
        g_m = pa.gen_cases(1, "多工具测试")
        check("多工具题：同套装 → 自动重出并修好",
              any("重出" in str(s) for s in seq5) and g_m["multi_ok"], (seq5, g_m.get("multi_ok")))
        _mq = [c for c in g_m["doc"]["cases"] if str(c.get("kind")) == "multi"]
        check("多工具题：重出后 refs 来自不同套装",
              bool(_mq) and len({pa.suit_of(r) for r in _mq[0]["refs"]}) >= 2, _mq)
    finally:
        pa.invoke = fake_invoke

    # ---- 11. 判分模型可单独配（用户 2026-10-10：出题要快、判分要判断力）----
    # ★ 用例必须**对 `.env` 免疫**：`probe_model()` 内部会 `load_dotenv()`，会把 `.env` 里
    #   真的 `PROBE_LLM_JUDGE_MODEL` 灌回来（这正说明改 `.env` 要重启服务才生效），
    #   所以这里把 `dotenv.find_dotenv` 指向一个空文件 → 隔离 `.env` ✓
    import tempfile
    import dotenv
    _empty = os.path.join(tempfile.gettempdir(), "hermes_probe_noenv_for_test")
    with open(_empty, "w", encoding="utf-8"):
        pass
    _orig_find = dotenv.find_dotenv
    dotenv.find_dotenv = lambda *a, **kw: _empty
    _sv = dict(os.environ)
    try:
        os.environ["PROBE_LLM_MODEL"] = "gen-model-x"
        os.environ.pop("PROBE_LLM_JUDGE_MODEL", None)
        pa._MODEL_CACHE.clear()
        check("判分模型：不配则**沿用出题模型**",
              getattr(pa.probe_model("judge"), "model_name", "") == "gen-model-x")
        os.environ["PROBE_LLM_JUDGE_MODEL"] = "judge-model-y"
        pa._MODEL_CACHE.clear()
        check("判分模型：配了 `PROBE_LLM_JUDGE_MODEL` 则单独生效，出题模型不受影响",
              getattr(pa.probe_model("judge"), "model_name", "") == "judge-model-y"
              and getattr(pa.probe_model(), "model_name", "") == "gen-model-x")
    finally:
        dotenv.find_dotenv = _orig_find
        os.environ.clear()
        os.environ.update(_sv)
        pa._MODEL_CACHE.clear()

    # ---- 12. 个人数据核查（用户 2026-10-11：题单最上方要提醒「依赖你个人数据」的题）----
    _ents12 = [_Ent("wechat-cli", "微信聊天"), _Ent("mcp:12306-mcp/get-tickets", "火车票")]
    _cases12 = [
        {"id": 1, "kind": "single", "q": "最近和家人聊了什么",
         "expect": {"kind": "cli", "bin": "wechat-cli"}},
        {"id": 2, "kind": "single", "q": "明天从北京到上海的火车票",
         "expect": {"kind": "tool", "ref": "mcp:12306-mcp/get-tickets"}},
        {"id": 3, "kind": "single", "q": "我B站收藏里关于python的视频",
         "expect": {"kind": "cli", "bin": "bili"}},
    ]

    def _priv_stub(section, msg, model=None, stage=""):
        # 模型该只挑「用户私有数据」题（#1 聊天记录 / #3 自己的收藏）；公开信息题（#2 火车票）不该出现
        # 另故意塞一个不存在的题号（99）→ 必须被忽略 ✓
        return json.dumps({"cases": [{"id": 1, "why": "他自己的聊天记录"},
                                     {"id": 3, "why": "他自己的 B 站收藏"},
                                     {"id": 99, "why": "不存在的题号"}]}, ensure_ascii=False)

    pa.invoke = _priv_stub
    try:
        _pv = pa.privacy_check(_cases12, _ents12)
        check("个人数据核查：只认本轮题号（模型幻觉的 #99 被忽略）", _pv["ids"] == [1, 3], _pv)
        check("个人数据核查：带理由（供题单提示逐题说明）",
              _pv["why"].get(3) == "他自己的 B 站收藏", _pv["why"])
        check("个人数据核查：公开信息题（火车票 #2）不在名单里", 2 not in _pv["ids"], _pv["ids"])
    finally:
        pa.invoke = fake_invoke

    def _boom(*a, **k):
        raise RuntimeError("核查模型挂了")

    pa.invoke = _boom
    try:
        _pv2 = pa.privacy_check(_cases12, _ents12)
        check("个人数据核查：失败只记 error、不抛（绝不影响出题 ✓）",
              _pv2["ids"] == [] and "核查模型挂了" in (_pv2.get("error") or ""), _pv2)
    finally:
        pa.invoke = fake_invoke

    # 接进出题链：命中就把提醒插到 **warnings 第一条**（题单最上方）+ 落进 doc.privacy
    def _priv_gen(section, msg, model=None, stage=""):
        if "本批共" in msg:
            return json.dumps({"cases": [
                {"id": i, "kind": "single", "q": "Q%d" % i,
                 "expect": ({"kind": "cli", "bin": e.ref.split(":")[-1]} if e.ref.startswith("cli:")
                            else {"kind": "tool", "ref": e.ref})}
                for i, e in enumerate(ents_m, 1)]}, ensure_ascii=False)
        if "逐题判断" in msg:                       # ← 个人数据核查那一步
            return json.dumps({"cases": [{"id": 1, "why": "他自己的数据"}]}, ensure_ascii=False)
        return json.dumps({"cases": []}, ensure_ascii=False)

    pa.invoke = _priv_gen
    try:
        _g12 = pa.gen_cases(1, "隐私测试")
    finally:
        pa.invoke = fake_invoke
    check("出题链：核查命中的提醒被插到 warnings **第一条**（显示在题单最上方 ✓）",
          bool(_g12["warnings"]) and "依赖" in _g12["warnings"][0] and "第 1" in _g12["warnings"][0],
          _g12["warnings"][:1])
    check("出题链：核查结果落进 doc.privacy（存题集后仍可显示 ✓）",
          (_g12["doc"].get("privacy") or {}).get("ids") == [1], _g12["doc"].get("privacy"))

    # ---- 13. 「CLI 名 + 子命令」写法必须能被归一（用户 2026-10-11 保存被拒的真因）----
    #   `expect={"kind":"tool","ref":"bili search"}` → 校验器按「ref 必须 api:/mcp: 开头」拦死 → 题集存不进去 ✗
    _ents13 = [_Ent("bili", "哔哩哔哩"), _Ent("mcp:pdd/search_goods", "拼多多查商品"),
               _Ent("weread", "微信读书")]
    _cases13 = [
        {"id": 1, "kind": "single", "q": "a",
         "expect": {"kind": "tool", "ref": "bili search"}},                    # 名 + 子命令（空格）
        {"id": 2, "kind": "single", "q": "b",
         "expect": {"kind": "tool", "ref": "cli:bili favorites"}},             # 带 cli: 前缀 + 子命令
        {"id": 3, "kind": "single", "q": "c",
         "expect": {"kind": "tool", "ref": "bili"}},                           # 只是 CLI 名
        {"id": 4, "kind": "single", "q": "d",
         "expect": {"kind": "cli", "bin": "bili", "argv": ["feed"]}},          # 已经是规范 CLI 形态
        {"id": 5, "kind": "single", "q": "e",
         "expect": {"kind": "cli", "bin": "bili", "argv": ["hot"]}},           # 中文名 + 子命令
        {"id": 6, "kind": "single", "q": "f",
         "expect": {"kind": "tool", "ref": "mcp:pdd/search_goods 耳机"}},      # ★ api/mcp 不许被拆
    ]
    _cases13[4]["expect"] = {"kind": "tool", "ref": "微信读书 hot"}            # 中文显示名 + 子命令
    _n13 = pa.normalize_expects(_cases13, _ents13)
    check("归一：`bili search` → cli/bili + argv=[search] ✓",
          _cases13[0]["expect"] == {"kind": "cli", "bin": "bili", "argv": ["search"]}, _cases13[0]["expect"])
    check("归一：`cli:bili favorites` → cli/bili + argv=[favorites] ✓",
          _cases13[1]["expect"] == {"kind": "cli", "bin": "bili", "argv": ["favorites"]}, _cases13[1]["expect"])
    check("归一：裸 CLI 名 `bili` → cli/bili（argv 空）✓",
          _cases13[2]["expect"] == {"kind": "cli", "bin": "bili", "argv": []}, _cases13[2]["expect"])
    check("归一：已规范形态不被改（argv 保留原样）✓",
          _cases13[3]["expect"] == {"kind": "cli", "bin": "bili", "argv": ["feed"]}, _cases13[3]["expect"])
    check("归一：**中文显示名** + 子命令也能认（→ 池里规范名 + argv）✓",
          _cases13[4]["expect"] == {"kind": "cli", "bin": "weread", "argv": ["hot"]}, _cases13[4]["expect"])
    check("归一：`mcp:` 形态带空格**不许被拆**（它不是 CLI ✗）",
          _cases13[5]["expect"] == {"kind": "tool", "ref": "mcp:pdd/search_goods 耳机"}, _cases13[5]["expect"])
    check("归一：该组修好 4 条（已规范的那条不动、mcp 那条不拆）", _n13 == 4, _n13)
    check("归一：`_split_cli_ref` 三种写法都能拆",
          pa._split_cli_ref("bili search") == ("bili", ["search"])
          and pa._split_cli_ref("cli:bili/favorites") == ("bili", ["favorites"])
          and pa._split_cli_ref("bili") == ("bili", []))

    # ---- 14. 词法建议必须认得 CLI 的「能力描述」（用户 2026-10-11 指出：CLI 的建议要针对那个框）----
    #   病灶：`advise()` 原来只读 `abilities_user` → CLI 条目（文本在 user_clis.abilities 的镜像里）
    #   一律显示「描述：（空）」→ 根本没法针对 CLI 能力描述提建议 ✗
    _pool14 = {"pool_version": 1, "items": [
        {"ref": "weread", "name": "微信读书", "source": "cli:weread",
         "keywords": "我的书架/最近看的", "desc_text": "我的书架有什么→shelf list；我最近看了什么→shelf recent",
         "abilities_user": ""},
        {"ref": "api:xxapi#ipQuery", "name": "查询IP归属地", "source": "api:xxapi",
         "keywords": "查IP", "desc_text": "查该 IP 的归属地", "abilities_user": "查该 IP 的归属地"},
    ]}
    import tools.probe_runner as _pr14
    _orig_pool14 = _pr14.pool_overview
    _pr14.pool_overview = lambda aid: _pool14
    _cap14 = {}

    def _adv_stub(section, msg, model=None, stage=""):
        _cap14["msg"] = msg
        return "工具：weread\n字段：能力描述\n现状：…\n建议：…"

    _orig_invoke14 = pa.invoke
    pa.invoke = _adv_stub
    try:
        _adv14 = pa.advise(1, {"rows": [{"id": 1, "kind": "single", "expect": {"kind": "cli", "bin": "weread"},
                                        "calls": [{"brief": "weread shelf list"}]}]},
                           scores={1: {"verdict": "△", "note": "绕路"}})
    finally:
        pa.invoke = _orig_invoke14
        _pr14.pool_overview = _orig_pool14
    _m14 = _cap14.get("msg") or ""
    check("词法建议：CLI 条目的**能力描述**真的喂进去了（不再是「（空）」✗）",
          "我的书架有什么→shelf list" in _m14, _m14[:120])
    check("词法建议：写明 CLI 该改哪个框（能力描述 / 接入 CLI）",
          "能力描述" in _m14 and "接入 CLI" in _m14)
    check("词法建议：API/MCP 条目标为「工具描述」（可改字段不同 ✓）",
          "工具描述" in _m14 and "api:xxapi#ipQuery" in _m14)
    check("词法建议：工具池素材里**逐条标了是 CLI 还是 API/MCP**",
          "CLI 工具" in _m14 and "API/MCP 工具" in _m14)
    check("建议提示词：已写明「改在哪」（cli→能力描述 / api·mcp→工具描述）",
          "改在哪" in pa.prompt_of("probe_advisor")
          and "能力描述" in pa.prompt_of("probe_advisor"), None)

    # ---- ⑯ 无对象自检（2026-10-12 用户追加：出题 Agent 爱出「想做一件事但没有明确对象」的题）----
    _VAGUE_POS = [
        ("我正要开始吃一种新药，但担心它和我正在吃的降压药有冲突，先查查这药的说明书，再找找它有没有被报告过和别的药一起用导致问题的案例。",
         "一种新药"),
        ("我看的那个 bili 视频讲了什么", "那个 bili 视频"),          # 尼古喵喵那轮的同款
        ("我收藏的那个商品现在多少钱", "我收藏的那个商品"),
        ("帮我看看我最近在读的那本书值不值得读", "我最近在读的那本书"),
        ("它讲了什么", "它"),
    ]
    for q, expect_hit in _VAGUE_POS:
        v = pa.vague_check([{"id": 1, "q": q}])
        check("无对象自检：应报「%s」（少写具体对象 ✗）" % expect_hit[:16],
              v["ids"] == [1], (q, v))
    _VAGUE_NEG = [
        "帮我查一下二甲双胍的说明书和不良反应上报记录",            # 点名了药 ✓
        "帮我查下 BV1xx411c7mD 这个视频讲了什么",                  # 有 BV 号锚点 ✓
        "《三体》这本书讲了什么",                                  # 书名号锚点 ✓
        "我收藏的『奢莉 水果啤酒』现在多少钱",                      # 引号锚点 ✓
        "帮我搜一下机械键盘的价格",                                # 具体检索词 ✓（不是我说的「一个键盘」）
        "最近有没有关于 GLP-1 受体激动剂减重的论文",                # 论文 + 具体药物 ✓
        "你是谁呀？",                                              # 噪声题 ✓
        "帮我写一首诗",                                            # 噪声题（「一首」+ 诗 不在类别词表）✓
        "最近有人举报说吃那个叫阿司匹林的药后有严重皮疹，想知道这药还有没有别的不良反应被报过？",  # 具名短语 ✓ 误报已修
    ]
    for q in _VAGUE_NEG:
        v = pa.vague_check([{"id": 1, "q": q}])
        check("无对象自检：不该报「%s」" % q[:16], v["ids"] == [], (q, v))
    _vg = pa.vague_check([{"id": 3, "q": "帮我看看我在吃的那个药"}, {"id": 4, "q": "帮我查阿司匹林"}])
    check("无对象自检：只报无对象的那道（#3 ✓ #4 不报）", _vg["ids"] == [3] and 3 in _vg["hits"], _vg)

    # ---- ⑰ 词法建议的输入拼装（2026-10-12 真机 bug：体检 problems 是**字典**，join 直接炸 ✗）----
    _h_items = [
        {"ref": "mcp:arxiv/get_abstract", "name": "get_abstract",
         "problems": [{"code": "ascii_name", "severity": "hard", "msg": "名字是英文标识符（get_abstract）"},
                      {"code": "no_kw", "severity": "soft", "msg": "没有关键词"}]},
        {"ref": "cli:x", "name": "某CLI", "problems": ["老形状：字符串"]},        # 兼容老形状 ✓
        {"ref": "cli:y", "name": "没事的", "problems": []},
    ]
    _hl = pa._health_lines(_h_items)
    check("词法建议：体检 problems（字典列表）渲染成文本行、不再 join 字典 ✗（真机 TypeError 回归）",
          isinstance(_hl, list) and all(isinstance(x, str) for x in _hl)
          and "[硬]" in _hl[0] and "[软]" in _hl[0] and "名字是英文标识符" in _hl[0]
          and "老形状：字符串" in _hl[1] and _hl[2].endswith("无"), _hl)

    # 端到端（离线）：advise 的**输入拼装**整段跑通（stub 掉池子与模型，不碰真库/不烧额度 ✓）
    from tools import probe_runner as _pr
    _saved_pool, _saved_invoke = _pr.pool_overview, pa.invoke
    _seen = {}
    try:
        _pr.pool_overview = lambda aid: {
            "items": [{"ref": "mcp:arxiv/search_papers", "name": "search_papers", "source": "mcp",
                       "keywords": "", "desc_text": "查 arXiv 论文"},
                      {"ref": "officecli", "name": "Office文档CLI", "source": "cli",
                       "keywords": "文档", "desc_text": "用户会说的话：看文档→view"}],
            "health": {"items": _h_items}}
        def _fake_invoke(section, prompt, *a, **k):
            _seen["section"] = section
            _seen["prompt"] = prompt
            return "工具：mcp:arxiv/search_papers\\n字段：工具描述\\n现状：（空）\\n建议：补关键词"
        pa.invoke = _fake_invoke
        _run = {"rows": [{"id": 1, "kind": "single", "q": "查论文", "expect": {"kind": "tool", "ref": "mcp:arxiv/search_papers"},
                          "calls": [{"brief": "invoke_tool(mcp:arxiv/search_papers)"}]}], "pool_version": 5}
        _out = pa.advise(1, _run, {"1": {"verdict": "✓", "note": "好"}})
        check("词法建议：advise 输入拼装整段跑通（含体检段落 + 逐题段落）",
              _seen.get("section") == "probe_advisor" and "[硬]" in _seen.get("prompt", "")
              and "第 1 条" in _seen.get("prompt", "")
              and "能力描述（改在「技能与工具 → 接入 CLI」的「能力描述」框）" in _seen.get("prompt", "")
              and _out.get("text", "").startswith("工具："), (_seen.get("section"), _seen.get("prompt", "")[:160]))
    finally:
        _pr.pool_overview, pa.invoke = _saved_pool, _saved_invoke

    print("\n" + "=" * 62)
    print("通过 %d ／ 失败 %d" % (len(ok), len(bad)))
    for n in bad:
        print("  -", n)
    assert not bad, "probe_agent 离线自测失败 %d 项：%s" % (len(bad), "；".join(bad))


def _raises(fn) -> bool:
    try:
        fn()
        return False
    except Exception:  # noqa: BLE001
        return True


def _raises_msg(fn, needle: str) -> bool:
    """异常**消息里**必须含 `needle`（用来钉住「报错要说人话」这类要求）。"""
    try:
        fn()
        return False
    except Exception as e:  # noqa: BLE001
        return needle in str(e)
