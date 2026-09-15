# -*- coding: utf-8 -*-
"""M5a 工具智能路由 · 能力语义注入 —— 端到端验证（不耗模型）

覆盖（与 M5 规划书 §3.5 对应）：
  1) 数据模型：user_clis.abilities 列存在、ensure_tables 幂等、老数据不受影响
  2) 读写：save_cli 存/读 abilities；局部更新（只传 id/name/bin）不清空它；显式空串才清空；
     超长（>MAX_ABILITIES）被拒
  3) agent_brief：① 有 abilities → 能力路由卡形态（含关键词、能力→命令映射、命令计数）；
     ② 无 abilities → 回退 M4c 命令名简报形态
  4) ability_examples（few-shot）：**只挑真实放行的命令**（用只读闸校验每条示例的 argv）；
     指向未放行命令的映射被丢弃；区块格式正确；无 abilities 时返回空
  5) 装配链路：cli_brief（简报 + 示例）经 build_request_context 进入动态 SystemMessage
  6) API：/api/cli/save 接受 abilities 并回读；/api/cli/ability_draft 缺只读清单 → 400（不调模型）
  7) 前端接线：表单字段、草稿按钮、卡片展示、CSS 类

运行：
    .venv/Scripts/python.exe tests/m5_abilities_e2e.py
"""

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient  # noqa: E402

from api import server  # noqa: E402
from tools import cli_registry as reg  # noqa: E402
from tools.schema_personal import ensure_tables, get_personal_conn  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HTML = open(os.path.join(ROOT, "static", "index.html"), encoding="utf-8").read()

PROBE = "m5probe-routing"          # 临时探针 CLI（用完删除，别污染用户配置）
# 用**全新临时账号**做隔离验证：该账号下只有探针 CLI，断言不会被真实配置（如 weread）干扰。
# 这也是项目既有的测试惯例（m2tester / pre_*_test 等隔离账号）。用完删除账号与其数据。
USER = "m5_%s" % time.strftime("%m%d%H%M%S")
PWD = "m5probe123"

# 探针 CLI 的能力描述：左=用户说法，右=只读清单里逐字存在的命令路径
PROBE_ABILITIES = (
    "关键词：读书/阅读/书架/我在读/笔记/划线/书评/找书。\n"
    "我正在读什么书→shelf recent；读到哪了→book progress；帮我找书→search；"
    "这本书讲什么→book info；书评怎么样→reviews list；我的笔记与划线→notes notebooks；"
    "找相似的书→discover similar；读书数据统计→readdata summary；我的书架→shelf list"
)
PROBE_READONLY = ("shelf recent\nbook progress\nsearch\nbook info\nreviews list\n"
                  "notes notebooks\ndiscover similar\nreaddata summary\nshelf list\ndoctor")

PASS, FAIL = [], []


def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name)
    print("%s %s%s" % ("  ✅" if cond else "  ❌", name, (" | " + str(extra)) if extra else ""))


def _account_id(username: str):
    conn = get_personal_conn()
    try:
        row = conn.execute("SELECT id FROM accounts WHERE username=?", (username,)).fetchone()
        return row["id"] if row else None
    finally:
        conn.close()


def _drop_account(username: str) -> bool:
    """删除临时账号及其数据（CLI 配置 / 会话 / 记忆文件），不留测试残留。

    实际清理走 `purge_account()`（自省表结构，新增账号域表不用再改这里）。
    """
    import shutil
    from tools.schema_personal import purge_account
    conn = get_personal_conn()
    try:
        row = conn.execute("SELECT id FROM accounts WHERE username=?", (username,)).fetchone()
        if not row:
            return False
        aid = row["id"]
    finally:
        conn.close()
    purge_account(aid)
    shutil.rmtree(os.path.join(ROOT, "agents_docs", username), ignore_errors=True)
    return True


def main():
    print("\n=== M5a 能力语义注入 e2e（不耗模型） ===\n")
    ensure_tables()

    # ------------------------------------------------------------------
    print("[1] 数据模型：abilities 列 + 迁移幂等")
    conn = get_personal_conn()
    try:
        cols = [r[1] for r in conn.execute("PRAGMA table_info(user_clis)").fetchall()]
    finally:
        conn.close()
    check("user_clis 有 abilities 列", "abilities" in cols, ",".join(cols))
    ensure_tables()
    ensure_tables()
    conn = get_personal_conn()
    try:
        cols2 = [r[1] for r in conn.execute("PRAGMA table_info(user_clis)").fetchall()]
    finally:
        conn.close()
    check("重复 ensure_tables 幂等（列不重复、不报错）", cols2 == cols)

    c = TestClient(server.app)
    rr = c.post("/api/register", json={"username": USER, "password": PWD, "role": "personal"})
    if rr.status_code != 200:
        check("创建临时测试账号", False, rr.text[:120])
        return summary()
    r = c.post("/api/login", json={"username": USER, "password": PWD})
    if r.status_code != 200:
        check("临时账号登录", False, r.text[:120])
        return summary()
    token = r.json()["token"]
    acc = _account_id(USER)
    check("创建并登录临时测试账号（隔离环境）", bool(acc), USER)

    # ------------------------------------------------------------------
    print("\n[2] 读写：存/读/局部更新/超长拒绝")
    it = reg.save_cli(acc, {"name": "M5 探针 CLI", "bin": PROBE, "readonly": PROBE_READONLY,
                          "abilities": PROBE_ABILITIES, "state": "authed"})
    pid = it["id"]
    check("新建时写入 abilities", (it.get("abilities") or "").startswith("关键词："), (it.get("abilities") or "")[:24])
    check("list_clis 能读回 abilities", any(x["id"] == pid and x["abilities"] for x in reg.list_clis(acc)))

    it2 = reg.save_cli(acc, {"id": pid, "name": "M5 探针 CLI", "bin": PROBE})   # 只传必填
    check("局部更新（只传 id/name/bin）不清空 abilities",
          (it2.get("abilities") or "").startswith("关键词："), (it2.get("abilities") or "")[:20])
    check("局部更新也不动 state / 只读清单",
          it2["state"] == "authed" and len(it2["rules"]) == 10, "%s / %s 条" % (it2["state"], len(it2["rules"])))

    it3 = reg.save_cli(acc, {"id": pid, "name": "M5 探针 CLI", "bin": PROBE, "abilities": ""})
    check("显式空串 = 清空 abilities", (it3.get("abilities") or "") == "")
    try:
        reg.save_cli(acc, {"id": pid, "name": "M5 探针 CLI", "bin": PROBE, "abilities": "啊" * (reg.MAX_ABILITIES_HARD + 5)})
        check("超长能力描述被拒（>%d 字，硬上限）" % reg.MAX_ABILITIES_HARD, False, "居然存进去了")
    except reg.CliConfigError as e:
        check("超长能力描述被拒（>%d 字，硬上限）" % reg.MAX_ABILITIES_HARD, True, str(e)[:40])

    # ------------------------------------------------------------------
    print("\n[3] agent_brief：两种形态")
    fallback = reg.agent_brief(acc)
    check("无 abilities → 回退命令名简报形态（M4c）", "可代跑" in fallback and "关键词：" not in fallback)

    reg.save_cli(acc, {"id": pid, "name": "M5 探针 CLI", "bin": PROBE, "abilities": PROBE_ABILITIES})
    brief = reg.agent_brief(acc)
    check("有 abilities → 能力路由卡形态", "关键词：" in brief and "→" in brief)
    check("路由卡含可执行名（模型据此组 argv）", "`%s`" % PROBE in brief)
    check("路由卡仍带命令计数（保留 M4c 信息）", "条只读命令" in brief)
    _kw_line = [ln for ln in brief.splitlines() if "关键词：" in ln]
    check("路由卡把多行能力描述压平成一行（关键词与映射同段）",
          bool(_kw_line) and "→" in _kw_line[0], (_kw_line[0][:60] if _kw_line else ""))
    check("路由卡整体长度可控（单 CLI < 900 字）", len(brief) < 900, "%d 字" % len(brief))

    # ------------------------------------------------------------------
    print("\n[4] ability_examples：few-shot 只从真实放行清单里挑")
    exs = reg.ability_examples(acc, max_n=5, max_per_cli=5)
    check("能为探针 CLI 生成示例", len(exs) >= 3, "%d 条" % len(exs))
    check("示例格式正确（run_shell_command + command + argv）",
          all(('run_shell_command(command="%s"' % PROBE) in e and "argv=[" in e for e in exs), exs[0] if exs else "")
    # 逐条用只读闸复核：示例里出现的 argv 必须真的被放行
    bad = []
    for e in exs:
        argv_txt = e.split("argv=", 1)[1].rstrip(")")
        import json as _json
        argv = _json.loads(argv_txt)
        ok, why = reg.readonly_verdict(argv, reg.get_cli(acc, pid)["rules"])
        if not ok:
            bad.append((argv, why))
    check("每条示例的 argv 都真的命中只读白名单（否则等于教模型撞拒绝）", not bad, bad[:2])

    # 指向「未放行命令」的映射必须被丢弃
    reg.save_cli(acc, {"id": pid, "name": "M5 探针 CLI", "bin": PROBE,
                     "abilities": "关键词：笔记。我的划线→notes underlines；我的笔记→notes notebooks"})
    exs2 = reg.ability_examples(acc, max_n=5, max_per_cli=5)
    check("指向未放行命令的映射被丢弃（notes underlines 不在清单）",
          all("underlines" not in e for e in exs2), exs2)
    check("同一描述里放行的那条仍能生成（notes notebooks）",
          any("notebooks" in e for e in exs2), exs2)

    reg.save_cli(acc, {"id": pid, "name": "M5 探针 CLI", "bin": PROBE, "abilities": PROBE_ABILITIES})
    blk = reg.ability_examples_block(acc)
    check("示例区块含引导语与「以区块为准」的约束",
          "怎么把用户的话对应到工具调用" in blk and "以本区块列出的为准" in blk)
    reg.save_cli(acc, {"id": pid, "name": "M5 探针 CLI", "bin": PROBE, "abilities": ""})
    check("无 abilities → 示例区块为空（零开销）", reg.ability_examples_block(acc) == "")
    reg.save_cli(acc, {"id": pid, "name": "M5 探针 CLI", "bin": PROBE, "abilities": PROBE_ABILITIES})

    # ------------------------------------------------------------------
    print("\n[5] 装配链路：简报 + 示例进动态 SystemMessage")
    from agent.build_context import build_request_context
    full = reg.agent_brief(acc) + "\n" + reg.ability_examples_block(acc)
    ctx = build_request_context("m5-thread-probe", acc, "看看我最近在读什么书",
                                soul_text="", memory_text="", username=USER, cli_brief=full)
    msgs = ctx.messages or []
    first = msgs[0].content if msgs else ""
    check("装配后第一条 SystemMessage 含【可用命令行工具】区块", "可用命令行工具" in first)
    check("同一个 SystemMessage 里含能力路由卡（关键词）", "关键词：" in first)
    check("同一个 SystemMessage 里含 few-shot 示例区块", "怎么把用户的话对应到工具调用" in first)
    check("示例里出现了 run_shell_command + 探针可执行名",
          'run_shell_command(command="%s"' % PROBE in first)
    check("ctx.cli_injected 标记为真（server 层据此提示思考）", bool(getattr(ctx, "cli_injected", False)))

    # 跨端一致性（用户关注点）：CLI 版 `dspro chat` 复用 api/server.py 的 _run_agent，
    # 所以 M5 的能力路由卡 + few-shot 示例对命令行端同样生效，不需要第二套实现。
    _chat_src = open(os.path.join(ROOT, "cli", "cmd_chat.py"), encoding="utf-8").read()
    check("CLI 版 dspro chat 复用 _run_agent（M5 注入自动覆盖命令行端）",
          "from api.server import _run_agent" in _chat_src)
    _srv = open(os.path.join(ROOT, "api", "server.py"), encoding="utf-8").read()
    # M5b 起，工具卡与示例由 tool_router 统一产出（内部即 agent_brief + ability_examples_block），
    # 仍然拼成同一段 cli_brief 注入——意图不变，接线换人。
    check("_run_agent 里同时注入工具卡与示例（拼成同一段 cli_brief）",
          "route_tools(account_id" in _srv and "_r.brief" in _srv and "_r.examples" in _srv)

    # ------------------------------------------------------------------
    print("\n[6] API：save 持久化 abilities + 草稿接口的参数校验")
    rr = c.post("/api/cli/save", params={"token": token}, json={
        "id": pid, "name": "M5 探针 CLI", "bin": PROBE, "readonly": PROBE_READONLY,
        "abilities": PROBE_ABILITIES, "state": "authed"})
    check("POST /api/cli/save 接受 abilities", rr.status_code == 200 and rr.json().get("ok"), rr.text[:120])
    listing = c.get("/api/cli/list", params={"token": token}).json()
    got = [x for x in listing.get("items", []) if x["id"] == pid]
    check("GET /api/cli/list 回读 abilities", bool(got) and (got[0].get("abilities") or "").startswith("关键词："))
    rbad = c.post("/api/cli/ability_draft", params={"token": token},
                  json={"name": "X", "bin": PROBE, "readonly": ""})
    check("草稿接口：缺只读清单 → 400（且不调模型）",
          rbad.status_code == 400 and "只读命令清单" in rbad.text, rbad.text[:90])

    # ------------------------------------------------------------------
    print("\n[7] 前端接线")
    for frag, label in (("cliForm.abilities", "表单绑定 abilities"),
                        ("/api/cli/ability_draft", "调用草稿接口"),
                        ("cliAbilityDraft", "草稿函数"),
                        ("cliDrafting", "生成中状态"),
                        ("能力描述", "字段文案"),
                        ("按只读清单生成草稿", "按钮文案"),
                        ("cli-ability", "卡片展示样式"),
                        ("必须来自上面的只读清单", "写法提示")):
        check("首页含 %s" % label, frag in HTML)

    # ------------------------------------------------------------------
    print("\n[8] 清理：探针配置 + 临时账号")
    ok = reg.delete_cli(acc, pid)
    check("删除探针 CLI", ok)
    check("删除后 brief 不再包含它", PROBE not in reg.agent_brief(acc))
    removed = _drop_account(USER)
    check("删除临时账号及其数据（不留测试残留）", removed, USER)

    return summary()


def summary():
    print("\n" + "=" * 60)
    print("M5a 能力语义注入 e2e：通过 %d，失败 %d" % (len(PASS), len(FAIL)))
    if FAIL:
        print("失败项：")
        for f in FAIL:
            print("  -", f)
        return 1
    print("全部通过 ✅")
    return 0


if __name__ == "__main__":
    sys.exit(main())
