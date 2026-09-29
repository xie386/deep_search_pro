# -*- coding: utf-8 -*-
"""M4c 自定义 CLI 接入配置 —— 端到端验证（TestClient 直调真实路由 + 真机 Agent 调用）

拍板口径（docs/v2.0/M4技能与工具.md【决策记录】2 + 6 + 7）：
  · **配置导向，不预设厂商**：名称 / 可执行名 / 安装认证命令 / 只读命令清单都由用户填，系统只按
    「白名单＝用户配的条目、只读闸＝用户填的清单、本机检测＝which 可执行名」三条与厂商无关的规则办事。
  · 内置 4 家（飞书/企微/钉钉/Google）只是 `templates` 里的**预填示例**，不进白名单、不参与判定。
  · 与 Agent 的关系＝折中方案 C：登记为已接入后**只放行只读/查询类子命令**；清单留空＝不放行。

覆盖链路（用探针配置 `m4cprobe` 跑，不动用户已有配置）：
  1) 登录拿 token（另备一个账号验隔离）
  2) GET  /api/cli/list       我的配置 + 预填示例 + 状态枚举 + 汇总
  3) 预填示例不进白名单（模板 ≠ 配置）
  4) POST /api/cli/save       校验矩阵（路径式 bin / shell 元字符 / 选项开头 / 重复 / 超长 / 非法状态）
  5) 正常新增 → 落库直查（account_id 归属、readonly 规范化）
  6) 未登记 → shell 拒绝；登记 authed → 只读命令过闸
  7) 只读闸安全矩阵（写操作 / 未列出命令 / 落盘参数）
  8) 编辑：换可执行名 → 老名字立刻回收；清空清单 → 立刻不放行
  9) GET  /api/shell/allowed  动态白名单出现探针 bin
 10) POST /api/cli/delete     删除即回收
 11) 旧版内置厂商登记 → user_clis 迁移（幂等）
 12) 账号隔离
 13) 真机端到端：Agent 调 run_shell_command 跑探针 CLI（链路通）
 14) 首页结构（自定义 CLI 卡 / 表单 / 试跑）

运行：
  cd <项目根> && unset PYTHONPATH && .venv/Scripts/python.exe tests/m4c_cli_e2e.py
  （加 SKIP_LLM=1 跳过第 13 步真机调用）
"""
import json
import os
import shutil
import sys
from pathlib import Path

# 本机 127.0.0.1:7897 代理会让本地自测串味，测试脚本一律清掉
for _k in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "ALL_PROXY", "all_proxy"):
    os.environ.pop(_k, None)

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient  # noqa: E402

from api.server import app  # noqa: E402
from tools.schema_personal import get_personal_conn, ensure_tables  # noqa: E402

USER = "尼古喵喵"
PWD = "123456"
USER2 = "灵康科技有限公司"       # 另一个账号（隔离验证）
PWD2 = "123456"
PROBE = "m4cprobe"              # 探针可执行名（本机不存在 → 只能验到「已放行 → 未安装」）

ok_cnt = [0]
fail = []


def check(label, cond, extra=""):
    if cond:
        ok_cnt[0] += 1
        print("  [OK]   " + label)
    else:
        fail.append(label)
        print("  [FAIL] " + label + ("  |  " + str(extra)[:400] if extra else ""))


def account_id_of(username):
    conn = get_personal_conn()
    try:
        row = conn.execute("SELECT id FROM accounts WHERE username=?", (username,)).fetchone()
        return row["id"] if row else None
    finally:
        conn.close()


def query(sql, args=()):
    conn = get_personal_conn()
    try:
        return [dict(r) for r in conn.execute(sql, args).fetchall()]
    finally:
        conn.close()


def main():
    ensure_tables()
    c = TestClient(app)

    print("\n== 1. 登录 ==")
    r = c.post("/api/login", json={"username": USER, "password": PWD})
    check("POST /api/login 200", r.status_code == 200, r.text[:300])
    if r.status_code != 200:
        return
    token = r.json()["token"]
    acc = account_id_of(USER)
    print("  token 前缀：%s…  account_id=%s" % (token[:12], acc))

    def save(payload):
        return c.post("/api/cli/save", params={"token": token}, json=payload)

    def exe(line, t=None):
        return c.post("/api/shell/exec", params={"token": t or token}, json={"command": line}, timeout=120)

    def why(resp):
        j = resp.json()
        return (j.get("error") or "") + (j.get("stderr") or "")

    def listing(t=None):
        return c.get("/api/cli/list", params={"token": t or token}).json()

    # 清理上次跑动残留（探针一律以 m4cprobe 开头；旧版登记遗留也一并清）
    for other, pwd in ((USER, PWD), (USER2, PWD2)):
        rr = c.post("/api/login", json={"username": other, "password": pwd})
        if rr.status_code != 200:
            continue
        t_o = rr.json()["token"]
        for it in listing(t_o).get("items", []):
            if it["bin"].startswith(PROBE) or it["note"] == "旧版登记":
                c.post("/api/cli/delete", params={"token": t_o}, json={"id": it["id"]})
    conn = get_personal_conn()
    try:
        conn.execute("DELETE FROM third_party_clis WHERE cli_id IN ('lark','wecom','dingtalk','google')")
        conn.commit()
    finally:
        conn.close()

    print("\n== 2. GET /api/cli/list（我的配置 + 预填示例） ==")
    r = c.get("/api/cli/list", params={"token": token})
    check("GET /api/cli/list 200", r.status_code == 200, r.text[:300])
    d = r.json()
    check("三条状态枚举（none/installed/authed）", set(d.get("states", {})) == {"none", "installed", "authed"},
          d.get("states"))
    check("预填示例 4 条（飞书/企微/钉钉/Google）", len(d.get("templates", [])) == 4,
          [t.get("key") for t in d.get("templates", [])])
    tpl = d["templates"][0]
    check("示例含表单所需字段", all(k in tpl for k in ("key", "icon", "name", "bin", "install_cmd",
                                                  "auth_cmd", "docs", "readonly", "hint")), sorted(tpl))
    check("汇总含 total/authed/detected_local/agent_live/with_rules",
          all(k in d.get("summary", {}) for k in ("total", "authed", "installed", "detected_local",
                                                  "agent_live", "with_rules")), d.get("summary"))

    print("\n== 3. 预填示例不进白名单（模板 ≠ 配置） ==")
    bins = [i["bin"] for i in d["items"]]
    tpl_bins = [t["bin"] for t in d["templates"]]
    db_cnt = len(query("SELECT id FROM user_clis WHERE account_id=?", (acc,)))
    check("列表 = 用户自己配置的行数（模板不混进列表）", len(bins) == db_cnt, (bins, db_cnt))
    if "lark-cli" not in bins:
        r = exe("lark-cli auth status")
        check("示例（飞书）未配置时不放行 Agent", "不在白名单" in why(r), why(r)[:200])
    else:
        print("  （跳过：该账号已自行配置 lark-cli，示例断言不适用）")

    print("\n== 4. POST /api/cli/save 校验矩阵 ==")
    BADBIN = PROBE + "bad"        # 矩阵专用：合法但不会被后续正常用例复用
    many = "\n".join("cmd%d sub" % i for i in range(45))
    bad_cases = [
        ({"name": "", "bin": BADBIN}, "空名称"),
        ({"name": "X", "bin": ""}, "空可执行名"),
        ({"name": "X", "bin": "C:/windows/system32/cmd.exe"}, "可执行名含路径（疑似任意执行）"),
        ({"name": "X", "bin": "../evil"}, "可执行名含 .."),
        ({"name": "X", "bin": "rm -rf"}, "可执行名含空格"),
        ({"name": "X", "bin": "x" * 70}, "可执行名超长"),
        ({"name": "Y" * 50, "bin": BADBIN}, "名称超长"),
        ({"name": "X", "bin": BADBIN, "docs": "ftp://x"}, "文档链接非 http(s)"),
        ({"name": "X", "bin": BADBIN, "readonly": "auth status; rm -rf /"}, "只读清单含 shell 元字符"),
        ({"name": "X", "bin": BADBIN, "readonly": "| whoami"}, "只读清单含管道"),
        ({"name": "X", "bin": BADBIN, "readonly": "-x foo"}, "只读清单以选项开头"),
        ({"name": "X", "bin": BADBIN, "readonly": many}, "只读清单条数超限"),
        ({"name": "X", "bin": BADBIN, "readonly": "\n".join(["a b"] * 45)}, "重复行去重后仍按原始行数限流"),
        ({"name": "X", "bin": BADBIN, "state": "hacked"}, "非法状态"),
        ({"name": "X", "bin": BADBIN, "id": 999999}, "改一个不存在的 id"),
    ]
    for payload, label in bad_cases:
        r = save(payload)
        check("拒绝：%s" % label, r.status_code == 400, "%s %s" % (r.status_code, r.text[:160]))
    leftover = [i for i in listing()["items"] if i["bin"].startswith(PROBE)]
    check("校验失败一个都没落库（脏数据零残留）", not leftover, [i["bin"] for i in leftover])

    print("\n== 5. 正常新增（探针配置） ==")
    r = save({"name": "探针 CLI", "bin": PROBE, "install_cmd": "npm i -g m4cprobe",
              "auth_cmd": "m4cprobe auth login", "docs": "https://example.com/probe",
              "readonly": "auth status\n# 注释行忽略\ncalendar +agenda", "note": "e2e 探针"})
    check("POST /api/cli/save 200", r.status_code == 200, r.text[:300])
    item = r.json().get("item", {})
    pid = item.get("id")
    check("返回解析后的规则（注释行被忽略）", item.get("rules") == [["auth", "status"], ["calendar", "+agenda"]],
          item.get("rules"))
    check("返回 AI 可代跑命令", item.get("agent_commands") == [PROBE + " auth status", PROBE + " calendar +agenda"],
          item.get("agent_commands"))
    check("新配置默认未接入（live=false）", item.get("state") == "none" and item.get("live") is False, item)
    rows = query("SELECT account_id, name, bin, readonly, state, note FROM user_clis WHERE bin=?", (PROBE,))
    check("落库到 user_clis", len(rows) == 1, rows)
    check("落库归属当前账号", rows and rows[0]["account_id"] == acc, rows)
    check("落库的 readonly 已规范化（每行一条、去注释、不含可执行名）",
          rows and rows[0]["readonly"] == "auth status\ncalendar +agenda",
          rows[0]["readonly"] if rows else None)
    r2 = save({"name": "另一个名字", "bin": PROBE})
    check("拒绝：同账号可执行名重复", r2.status_code == 400, r2.text[:160])

    print("\n== 5.5 整条粘贴自动裁成子命令路径（用户实际场景：从官方文档整行粘） ==")
    PB = PROBE + "p"          # 本次用的可执行名（粘贴行用真实命令名，模拟从官方文档整行粘）
    pasted = ("%s notes top --limit 20\n"
              "%s notes export 3300045871 --format markdown --output notes.md\n"
              "%s search 三体\n"
              "%s readdata detail --mode annually" % (PB, PB, PB, PB))
    r = save({"name": "粘贴测试", "bin": PROBE + "p", "readonly": pasted})
    check("整条粘贴保存成功（不再报「单条规则最多 N 段」）", r.status_code == 200, r.text[:220])
    it = r.json().get("item", {})
    check("自动裁掉可执行名 / 选项 / 取值，只留子命令路径",
          it.get("rules") == [["notes", "top"], ["notes", "export"], ["search"], ["readdata", "detail"]],
          it.get("rules"))
    check("返回裁剪说明供前端提示", len(it.get("parse_notes") or []) == 4, it.get("parse_notes"))
    check("AI 可代跑命令带可执行名", it.get("agent_commands", [""])[0] == PB + " notes top",
          it.get("agent_commands"))
    # 另一类真实情况：可执行名填成了 npm 包名，粘贴行却是真实命令名 → 自动识别为命令名并提示核对
    r = save({"name": "包名误填", "bin": "node-cli", "readonly": "node status"})
    check("包名当可执行名：粘贴行首段是真实命令名 → 自动去掉",
          r.status_code == 200 and r.json()["item"]["rules"] == [["status"]],
          r.json().get("item", {}).get("rules"))
    notes2 = r.json()["item"].get("parse_notes") or []
    check("并提示核对「可执行名」是否写成了包名", any("可执行名" in n for n in notes2), notes2)
    r = save({"name": "正常子命令不被误裁", "bin": PROBE + "s", "readonly": "search\ninfo\nlist"})
    check("常见子命令（search/info/list）不会被误判成命令名",
          r.status_code == 200 and r.json()["item"]["rules"] == [["search"], ["info"], ["list"]],
          r.json().get("item", {}).get("rules"))
    for it3 in listing()["items"]:
        if it3["bin"] in ("node-cli", PROBE + "s"):
            c.post("/api/cli/delete", params={"token": token}, json={"id": it3["id"]})
    r = c.post("/api/cli/status", params={"token": token}, json={"id": it["id"], "state": "authed"})
    check("登记后放行", r.status_code == 200 and r.json()["item"]["live"] is True, r.text[:200])
    r = exe("%sp notes export 999 --format markdown" % PROBE)
    check("带参数的调用被放行（参数不参与白名单判定）", "不在白名单" not in why(r), why(r)[:200])
    r = exe("%sp notes export 999 --output out.md" % PROBE)
    check("落盘参数 --output 仍被拒（沙箱只读）", (not r.json().get("ok")) and "不在白名单" not in why(r), why(r)[:200])
    r = exe("%sp search 其他词" % PROBE)
    check("裁剪后的 search 规则能吃不同关键词", "不在白名单" not in why(r), why(r)[:200])
    r = save({"name": "粘贴测试2", "bin": PROBE + "q", "readonly": "a b c d e f g h i"})
    check("拒绝：子命令路径超过 8 段", r.status_code == 400, r.text[:160])
    for it2 in listing()["items"]:
        if it2["bin"] in (PROBE + "p", PROBE + "q"):
            c.post("/api/cli/delete", params={"token": token}, json={"id": it2["id"]})

    print("\n== 6. 未登记 → 登记后过闸 ==")
    r = exe("%s auth status" % PROBE)
    check("未登记 → 拒绝且说明不在白名单", (not r.json().get("ok")) and "不在白名单" in why(r), why(r)[:200])
    r = c.post("/api/cli/status", params={"token": token}, json={"id": pid, "state": "authed", "note": "已装好"})
    check("POST /api/cli/status 200", r.status_code == 200, r.text[:300])
    it = r.json().get("item", {})
    check("状态写回 + live=true", it.get("state") == "authed" and it.get("live") is True and "已接入" in it.get("state_label", ""), it)
    check("备注落库", query("SELECT note FROM user_clis WHERE id=?", (pid,))[0]["note"] == "已装好")
    installed = bool(shutil.which(PROBE))
    r = exe("%s auth status" % PROBE)
    e = why(r)
    check("登记后不再报「不在白名单」（放行到执行阶段）", "不在白名单" not in e, e[:240])
    check("本机未装 → 报「未安装或不在 PATH」", installed or "未安装" in e, e[:240])

    print("\n== 7. 只读闸安全矩阵 ==")
    for line, label in [
        ("%s auth logout" % PROBE, "未列出的 auth 子命令（登出，写操作）"),
        ("%s send message hello" % PROBE, "未列出的子命令（发消息）"),
        ("%s auth status -o out.txt" % PROBE, "落盘参数 -o"),
        ("%s auth status --output-dir dump" % PROBE, "落盘参数 --output-dir"),
        ("%s" % PROBE, "只给可执行名、没给子命令"),
    ]:
        r = exe(line)
        e = why(r)
        check("拒绝：%s" % label, (not r.json().get("ok")) and ("不在白名单" not in e), e[:200])
    r = exe("%s calendar calendars list" % PROBE)
    check("拒绝：清单里只有 calendar +agenda，其它 calendar 子命令不因前缀沾光",
          (not r.json().get("ok")) and ("不在白名单" not in why(r)), why(r)[:200])

    print("\n== 8. 编辑配置：换名字 / 清空清单都立刻生效 ==")
    r = save({"id": pid, "name": "探针 CLI v2", "bin": PROBE + "2", "readonly": "auth status"})
    check("改可执行名成功", r.status_code == 200, r.text[:200])
    r = exe("%s auth status" % PROBE)
    check("老可执行名立刻回收（不再放行）", "不在白名单" in why(r), why(r)[:200])
    r = exe("%s2 auth status" % PROBE)
    check("新可执行名立刻生效（放行到执行阶段）", "不在白名单" not in why(r), why(r)[:200])
    r = save({"id": pid, "name": "探针 CLI v3", "bin": PROBE + "2", "readonly": ""})
    check("清空只读清单成功", r.status_code == 200 and r.json()["item"]["live"] is False, r.json().get("item"))
    r = exe("%s2 auth status" % PROBE)
    check("清单清空 → 不放行（提示清单为空）", (not r.json().get("ok")) and "不在白名单" in why(r), why(r)[:240])
    r = save({"id": pid, "name": "探针 CLI", "bin": PROBE, "readonly": "auth status\ncalendar +agenda",
              "install_cmd": "npm i -g m4cprobe", "auth_cmd": "m4cprobe auth login",
              "docs": "https://example.com/probe", "note": "e2e 探针"})
    check("改回探针配置", r.status_code == 200 and r.json()["item"]["bin"] == PROBE, r.text[:160])

    print("\n== 8.5 局部更新的边界：只改名字不该清掉登记状态 ==")
    r = save({"id": pid, "name": "探针 CLI 改名不改状态", "bin": PROBE})
    it = r.json().get("item", {})
    check("缺省 state 时保留原登记（仍为已接入）", it.get("state") == "authed", it.get("state"))
    check("缺省 note 时保留原备注", it.get("note") == "e2e 探针", it.get("note"))
    check("仍然放行（live 未被清掉）", it.get("live") is True, it.get("live"))
    save({"id": pid, "name": "探针 CLI", "bin": PROBE, "state": "authed", "note": "e2e 探针",
          "readonly": "auth status\ncalendar +agenda"})
    r = listing()
    probe_now = next((i for i in r["items"] if i["bin"] == PROBE), {})
    check("显式传 readonly 后清单恢复、live 回来", probe_now.get("live") is True, probe_now)

    print("\n== 9. GET /api/shell/allowed：动态白名单 ==")
    d = c.get("/api/shell/allowed", params={"token": token}).json()
    names3 = [x["name"] for x in d.get("third_party", [])]
    print("  动态项：%s" % (" / ".join(names3) or "（空）"))
    check("静态白名单仍是 9 条", len(d.get("commands", [])) == 9, len(d.get("commands", [])))
    check("动态项含探针（已接入 + 有清单）", PROBE in names3, names3)
    probe_item = next((x for x in d["third_party"] if x["name"] == PROBE), {})
    check("动态项带只读命令清单与检测结果", bool(probe_item.get("commands")) and "detected" in probe_item, probe_item)

    print("\n== 10. POST /api/cli/delete：删除即回收 ==")
    r = c.post("/api/cli/delete", params={"token": token}, json={"id": pid})
    check("删除返回 ok", r.status_code == 200 and r.json().get("ok"), r.text[:200])
    r = c.post("/api/cli/delete", params={"token": token}, json={"id": pid})
    check("重复删除 404", r.status_code == 404, r.text[:200])
    r = exe("%s auth status" % PROBE)
    check("删除后不放行", "不在白名单" in why(r), why(r)[:200])
    d = c.get("/api/shell/allowed", params={"token": token}).json()
    check("动态白名单同步移除", PROBE not in [x["name"] for x in d.get("third_party", [])],
          [x["name"] for x in d.get("third_party", [])])
    check("库里也已删除", not query("SELECT id FROM user_clis WHERE bin=?", (PROBE,)))

    print("\n== 11. 旧版内置厂商登记 → user_clis 迁移（幂等，在第二账号干净环境验） ==")
    l2 = c.post("/api/login", json={"username": USER2, "password": PWD2})
    if l2.status_code == 200:
        t2 = l2.json()["token"]
        acc2 = account_id_of(USER2)
        conn = get_personal_conn()
        try:
            conn.execute("INSERT INTO third_party_clis (account_id, cli_id, state, note) VALUES (?,?,?,?) "
                         "ON CONFLICT(account_id, cli_id) DO UPDATE SET state=excluded.state, note=excluded.note",
                         (acc2, "lark", "authed", "旧版登记"))
            conn.commit()
        finally:
            conn.close()
        d = listing(t2)
        lark_items = [i for i in d["items"] if i["bin"] == "lark-cli"]
        check("旧登记被搬进 user_clis（lark-cli）", len(lark_items) == 1, [i["bin"] for i in d["items"]])
        if lark_items:
            li = lark_items[0]
            check("迁移保留状态与备注", li["state"] == "authed" and li["note"] == "旧版登记",
                  (li["state"], li["note"]))
            check("迁移带上示例里的只读清单（有清单才可能 live）", bool(li["rules"]) and li["live"] is True,
                  (li["rules"], li["live"]))
            again = [i for i in listing(t2)["items"] if i["bin"] == "lark-cli"]
            check("重复调用不重复迁移（幂等）", len(again) == 1, len(again))
            c.post("/api/cli/delete", params={"token": t2}, json={"id": li["id"]})
            print("  已清理迁移产生的 lark-cli 配置")
        check("迁移不污染第一账号", "lark-cli" not in [i["bin"] for i in listing()["items"]],
              [i["bin"] for i in listing()["items"]])
        conn = get_personal_conn()
        try:
            conn.execute("DELETE FROM third_party_clis WHERE account_id=?", (acc2,))
            conn.commit()
        finally:
            conn.close()
    else:
        check("第二账号可登录（迁移验证前置）", False, l2.text[:200])

    print("\n== 12. 账号隔离 ==")
    r2 = c.post("/api/login", json={"username": USER2, "password": PWD2})
    if r2.status_code == 200:
        t2 = r2.json()["token"]
        d3 = listing(t2)
        check("另一账号看不到本账号的配置", PROBE not in [i["bin"] for i in d3["items"]],
              [i["bin"] for i in d3["items"]])
        r3 = c.post("/api/cli/save", params={"token": t2}, json={"name": "探针 CLI", "bin": PROBE,
                                                                 "readonly": "auth status", "state": "authed"})
        check("另一账号可以配同名 bin（各自独立）", r3.status_code == 200, r3.text[:200])
        r3 = exe("%s auth status" % PROBE, t=t2)
        check("另一账号登记后自己放行（互不影响本账号是否登记）", "不在白名单" not in why(r3), why(r3)[:200])
        # 清掉第二账号的探针
        for it in listing(t2)["items"]:
            if it["bin"] == PROBE:
                c.post("/api/cli/delete", params={"token": t2}, json={"id": it["id"]})
    else:
        check("第二个测试账号可登录（隔离验证前置）", False, r2.text[:200])

    print("\n== 12.5 CLI 简报注入（Agent 上下文里能看到自己有哪些 CLI） ==")
    from tools import cli_registry as registry
    from agent.build_context import build_request_context, compose_dynamic_prompt
    # 零开销路径：没账号 / 不存在的账号 / 该账号一条 CLI 都没配 → 空串（不注入）
    check("没有可用 CLI 时简报为空串（零 token 开销）",
          registry.agent_brief(None) == "" and registry.agent_brief(999999) == "",
          (registry.agent_brief(None), registry.agent_brief(999999)))
    r = save({"name": "简报探针", "bin": PROBE + "b", "readonly": "auth status", "state": "authed"})
    bid = r.json()["item"]["id"]
    brief = registry.agent_brief(acc)
    check("登记后简报含该 CLI 与可执行名", (PROBE + "b") in brief and "简报探针" in brief, brief)
    check("简报里给出放行条数", "1 条只读命令" in brief, brief)
    dyn = compose_dynamic_prompt(soul_text="人格", memory_text="记忆", username=USER, cli_brief=brief)
    check("动态 SystemMessage 含【可用命令行工具】区块", "【可用命令行工具（用户自配的 CLI，只读）】" in dyn, dyn[-400:])
    check("区块里给出调用方式与拒绝纪律", "run_shell_command" in dyn and "错误原文" in dyn, "")
    ctx = build_request_context("t-brief-e2e", acc, "你能用哪些命令行工具？",
                                soul_text="", memory_text="", username=USER, cli_brief=brief)
    check("装配结果标记 cli_injected=True", ctx.cli_injected is True)
    check("首条 SystemMessage 里真的带上了 CLI", (PROBE + "b") in ctx.messages[0].content, "")
    ctx2 = build_request_context("t-brief-e2e2", acc, "你好", cli_brief="")
    check("不传简报时不注入（cli_injected=False）", ctx2.cli_injected is False)
    r = save({"name": "简报探针2", "bin": PROBE + "c", "readonly": "auth status", "state": "authed"})
    cid2 = r.json()["item"]["id"]
    capped = registry.agent_brief(acc, max_clis=1)
    # 断言「上限真的截住了」——注意不能拿「结果是单行」当判据：M5 起能力路由卡合法地是多行，
    # 且同账号可能还登记着别的 CLI（如真实 weread），单行判据会随账号配置漂移。
    check("CLI 数量超过上限时截断（第二个 CLI 不再出现 / 最多一条 CLI 条目）",
          (PROBE + "c") not in capped and capped.count("\n- ") <= 1, capped)
    for x in (bid, cid2):
        c.post("/api/cli/delete", params={"token": token}, json={"id": x})

    print("\n== 13. 真机端到端：Agent 调 run_shell_command 跑自定义 CLI ==")
    if os.getenv("SKIP_LLM"):
        print("  已按 SKIP_LLM=1 跳过（真机调用约 1-3 分钟）")
    else:
        r = save({"name": "探针 CLI", "bin": PROBE, "readonly": "auth status", "state": "authed"})
        pid2 = r.json()["item"]["id"]
        tprobe = c.post("/api/chat/sessions", params={"token": token}).json()["thread_id"]
        # 先验「注入是否进了模型视野」：问它有哪些可用命令行工具，不要求调用
        qa = ("只回答一个问题：根据你的系统提示，你现在可以通过 run_shell_command 调用哪些命令行工具？"
              "只要列出名字，不要调用工具。")
        ra = c.post("/api/chat", params={"token": token, "thread_id": tprobe, "question": qa}, timeout=900)
        if ra.status_code == 200:
            ans_a = ra.json().get("answer", "")
            print("  简报问答前 120 字：" + ans_a[:120].replace("\n", " / "))
            check("Agent 能说出自己可用的自定义 CLI（简报注入生效）", PROBE in ans_a, ans_a[:200])
        else:
            check("简报问答 200", False, ra.text[:200])
        tprobe = c.post("/api/chat/sessions", params={"token": token}).json()["thread_id"]
        q = ("请调用 run_shell_command 工具，命令名 %s，参数就是 [\"auth\",\"status\"]，"
             "把它返回的内容如实告诉我；如果失败就把失败原因原文告诉我。" % PROBE)
        r = c.post("/api/chat", params={"token": token, "thread_id": tprobe, "question": q}, timeout=900)
        check("POST /api/chat 200（真机）", r.status_code == 200, r.text[:400])
        if r.status_code == 200:
            ans = r.json().get("answer", "")
            print("  回答前 160 字：" + ans[:160].replace("\n", " / "))
            check("Agent 的回答能对上「未安装 / 白名单以外的真实报错」",
                  (PROBE in ans) and ("未安装" in ans or "不在白名单" not in ans), ans[:200])
            try:
                mon = c.get("/api/chat/sessions/%s" % tprobe, params={"token": token}).json()
                blob = json.dumps(mon, ensure_ascii=False)
            except Exception as e:  # noqa: BLE001
                blob = str(e)
            check("会话历史里能看到探针 CLI 的调用痕迹", PROBE in blob or "run_shell_command" in blob, blob[:200])
        c.post("/api/cli/delete", params={"token": token}, json={"id": pid2})

    print("\n== 14. 首页结构（自定义 CLI 卡） ==")
    html = c.get("/").text
    for frag in ("自定义 CLI", "cliItems", "cliTemplates", "/api/cli/list", "/api/cli/save",
                 "/api/cli/status", "/api/cli/delete", "cliSave", "cliDelete", "cliRunAll",
                 "cli-form", "cli-cmd", "cli-run", "只读命令清单"):
        check("首页含 %s" % frag, frag in html, "")

    # 收尾清理
    for t_o, u in ((token, USER),):
        for it in listing(t_o).get("items", []):
            if it["bin"].startswith(PROBE) or it["note"] == "旧版登记":
                c.post("/api/cli/delete", params={"token": t_o}, json={"id": it["id"]})
    conn = get_personal_conn()
    try:
        conn.execute("DELETE FROM third_party_clis WHERE account_id=?", (acc,))
        conn.commit()
    finally:
        conn.close()
    print("\n  已清理探针配置与旧版登记残留")

    print("\n================ M4c 自定义 CLI 接入 e2e 结果 ================")
    print("通过 %d 项，失败 %d 项" % (ok_cnt[0], len(fail)))
    if fail:
        print("失败清单：")
        for f in fail:
            print("  - " + f)
    print("=============================================================")
    sys.exit(1 if fail else 0)


if __name__ == "__main__":
    main()
