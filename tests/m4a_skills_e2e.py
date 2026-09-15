# -*- coding: utf-8 -*-
"""M4a 技能模块 —— 端到端验证（TestClient 直调，绕开代理/端口）

覆盖链路（全部走真实 HTTP 路由，不孤立调函数）：
  1) 登录拿 token
  2) GET  /api/skills          列出技能
  3) POST /api/skills/create   新建技能（含唯一标记）
  4) GET  /api/skills/get      读回正文
  5) POST /api/skills/save     改正文
  6) 装配校验：build_request_context(skills_text=...) 的最终 user message 必须含技能原文
  7) 真机端到端：POST /api/chat?skills=<技能名> —— 智能体回答里必须出现只有技能文本里才有的标记
  8) 一次性校验：不带 skills 再问一次，标记不应出现（证因果，防"本来就会说"）
  9) POST /api/skills/delete   删除并确认列表里消失
 10) GET  /                    首页 HTML 必须含 M4a 新增结构（skills-view / skill-menu / term）

运行：
  cd <项目根> && unset PYTHONPATH && .venv/Scripts/python.exe tests/m4a_skills_e2e.py
"""
import os
import re
import sys

# 本机 127.0.0.1:7897 代理会让本地自测串味，测试脚本一律清掉
for _k in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "ALL_PROXY", "all_proxy"):
    os.environ.pop(_k, None)

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient  # noqa: E402

from api.server import app  # noqa: E402

USER = "尼古喵喵"
PWD = "123456"
SKILL_NAME = "M4a注入验证"
MARK = "M4A-E2E-7731"          # 只出现在技能正文里；回答中出现即证明技能被注入

BODY_V1 = (
    "# M4a注入验证\n\n"
    "## 硬性要求\n"
    "- 无论用户问什么，回答的第一行必须原样输出这一串标记：" + MARK + "\n"
    "- 然后再正常回答用户的问题。\n"
)
BODY_V2 = BODY_V1 + "\n## 补充（save 后新增，用于验证保存生效）\n- 版本号 V2\n"

ok_cnt = [0]
fail = []


def check(label, cond, extra=""):
    if cond:
        ok_cnt[0] += 1
        print("  [OK]   " + label)
    else:
        fail.append(label)
        print("  [FAIL] " + label + ("  |  " + str(extra)[:400] if extra else ""))


def main():
    c = TestClient(app)

    print("\n== 1. 登录 ==")
    r = c.post("/api/login", json={"username": USER, "password": PWD})
    check("POST /api/login 200", r.status_code == 200, r.text[:300])
    if r.status_code != 200:
        return
    token = r.json()["token"]
    print("  token 前缀：" + token[:12] + "...")

    print("\n== 2. 列表（应可列出，含/不含本技能都要能跑） ==")
    r = c.get("/api/skills", params={"token": token})
    check("GET /api/skills 200", r.status_code == 200, r.text[:300])
    names0 = [s["name"] for s in r.json().get("skills", [])]
    print("  现有技能：%s" % (names0 or "（空）"))
    # 字段完整性：前端要用 chars/desc/updated 渲染
    if names0:
        s0 = r.json()["skills"][0]
        check("列表项含 name/chars/desc/updated 字段",
              all(k in s0 for k in ("name", "chars", "desc", "updated")), s0)
        check("updated 是 'YYYY-mm-dd HH:MM:SS' 字符串（前端按此格式化）",
              bool(re.match(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$", str(s0["updated"]))), s0.get("updated"))

    # 清理可能的同名残留，保证 create 拿的是 200 而不是 409
    if SKILL_NAME in names0:
        c.post("/api/skills/delete", params={"token": token}, json={"name": SKILL_NAME, "content": ""})

    print("\n== 3. 新建技能 ==")
    r = c.post("/api/skills/create", params={"token": token},
               json={"name": SKILL_NAME, "content": BODY_V1})
    check("POST /api/skills/create 200", r.status_code == 200, r.text[:300])
    r2 = c.post("/api/skills/create", params={"token": token},
                json={"name": SKILL_NAME, "content": BODY_V1})
    check("重名再建返回 409（不覆盖既有）", r2.status_code == 409, r2.status_code)

    print("\n== 4. 读回正文 ==")
    r = c.get("/api/skills/get", params={"name": SKILL_NAME, "token": token})
    check("GET /api/skills/get 200", r.status_code == 200, r.text[:300])
    check("正文含唯一标记", MARK in r.json().get("content", ""), r.text[:200])

    print("\n== 5. 保存（V1 -> V2） ==")
    r = c.post("/api/skills/save", params={"token": token},
               json={"name": SKILL_NAME, "content": BODY_V2})
    check("POST /api/skills/save 200", r.status_code == 200, r.text[:300])
    r = c.get("/api/skills/get", params={"name": SKILL_NAME, "token": token})
    check("改动已落盘（含 V2 段）", "版本号 V2" in r.json().get("content", ""), r.text[:200])

    print("\n== 6. 装配校验：技能文本进入最终 user message ==")
    from agent.build_context import build_request_context
    ctx = build_request_context(
        "_m4a_unit", None, "装配校验用问题",
        soul_text="", memory_text="", username=USER,
        skills_text="【本轮启用技能】\n" + BODY_V2,
    )
    check("final_user_msg 含技能正文", MARK in (ctx.final_user_msg or ""), (ctx.final_user_msg or "")[:200])
    check("技能被标记为已注入（skills_injected）", bool(getattr(ctx, "skills_injected", False)),
          getattr(ctx, "skills_injected", None))
    ctx_none = build_request_context(
        "_m4a_unit", None, "不带技能的问题",
        soul_text="", memory_text="", username=USER, skills_text="",
    )
    check("skills_text 为空时 final_user_msg 不含技能区块", "【本轮启用技能】" not in (ctx_none.final_user_msg or ""),
          (ctx_none.final_user_msg or "")[:200])

    print("\n== 7. 真机端到端：/api/chat 带 skills（会真实调用大模型，稍慢） ==")
    # 用全新会话做 A/B：避免同 thread 历史里模型上一轮回答照抄标记造成假阳性
    tA = c.post("/api/chat/sessions", params={"token": token}).json()["thread_id"]
    tB = c.post("/api/chat/sessions", params={"token": token}).json()["thread_id"]
    print("  会话 A=%s（用技能）  B=%s（不用技能）" % (tA[:8], tB[:8]))
    ans_with = None
    r = c.post("/api/chat", params={"token": token, "thread_id": tA,
                                    "question": "请用一句话说明今天适合做什么。",
                                    "skills": SKILL_NAME}, timeout=600)
    check("POST /api/chat?skills=... 200", r.status_code == 200, r.text[:400])
    if r.status_code == 200:
        ans_with = r.json().get("answer", "")
        check("返回的 thread_id 就是传入的会话", r.json().get("thread_id") == tA, r.json().get("thread_id"))
        print("  回答前 120 字：" + ans_with[:120].replace("\n", " / "))
        check("回答含技能里的唯一标记 → 技能链路端到端打通", MARK in ans_with, ans_with[:300])
        # 技能文本不得落进会话历史（一次性语义）：库里存的应是原始问题
        hist = c.get("/api/chat/sessions/%s" % tA, params={"token": token}).json()["messages"]
        user_msgs = [m for m in hist if m.get("role") == "user"]
        check("会话历史里的用户消息不含技能正文（技能不入库）",
              all(MARK not in str(m.get("content", "")) for m in user_msgs),
              [str(m.get("content"))[:80] for m in user_msgs])

    print("\n== 8. 一次性校验：换一个新会话、不带 skills 再问一次 ==")
    r = c.post("/api/chat", params={"token": token, "thread_id": tB,
                                    "question": "请用一句话说明今天适合做什么。"}, timeout=600)
    check("POST /api/chat 不带 skills 200", r.status_code == 200, r.text[:300])
    if r.status_code == 200:
        ans_wo = r.json().get("answer", "")
        print("  回答前 120 字：" + ans_wo[:120].replace("\n", " / "))
        check("回答不含标记 → 技能确实只在选中那轮生效", MARK not in ans_wo, ans_wo[:300])
    for t in (tA, tB):
        c.delete("/api/chat/sessions/%s" % t, params={"token": token})

    print("\n== 9. 删除技能 ==")
    r = c.post("/api/skills/delete", params={"token": token}, json={"name": SKILL_NAME, "content": ""})
    check("POST /api/skills/delete 200", r.status_code == 200, r.text[:300])
    r = c.get("/api/skills", params={"token": token})
    check("列表里已消失", SKILL_NAME not in [s["name"] for s in r.json().get("skills", [])], r.text[:200])

    print("\n== 10. 前端静态页（M4a 结构是否随服务下发） ==")
    r = c.get("/")
    check("GET / 200", r.status_code == 200, r.status_code)
    page = r.text
    for frag, desc in [('skills-view', '技能与工具视图容器'),
                       ('class="skill-menu"', '「/」技能目录弹层'),
                       ('class="input-area"', '输入区包裹层'),
                       ('loadSkills', '技能列表加载函数'),
                       ('/api/cli/list', '自定义 CLI 配置（M4c：服务端单一来源）'),
                       ('cliItems', 'CLI 配置卡片数据源'),
                       ('class="term"', '011 终端窗口卡（命令行 UI）'),
                       ('@keydown="skOnKey"', '键盘上下键选择绑定')]:
        check("首页含 %s（%s）" % (frag, desc), frag in page)

    print("\n---------------- 结果 ----------------")
    print("通过 %d 项，失败 %d 项" % (ok_cnt[0], len(fail)))
    if fail:
        for f in fail:
            print("  ✗ " + f)
        sys.exit(1)
    print("M4a 技能模块端到端全部通过 ✅")


if __name__ == "__main__":
    main()
