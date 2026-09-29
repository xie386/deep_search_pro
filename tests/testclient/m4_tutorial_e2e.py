# -*- coding: utf-8 -*-
"""M4 收尾 · 教程指导（只读 md 阅读页 + 内联图片）——端到端验证

覆盖链路：
  1) 登录拿 token
  2) GET  /api/tutorial/doc        只读返回教程 md（标题/路径/更新时间/图片清单）
  3) 图片：引用被改写成 /tutorial-assets/<文件名>，且**真的取得到**（200 + image/*）
  4) 安全：无 token 被拒；静态挂载挡住 `../` 穿越；正文不含用户绝对路径
  5) md 结构保真：折叠块 <details>、代码块、表格、图片引用都还在
  6) GET  /                        首页含教程按钮与阅读页结构

运行：
  cd <项目根> && unset PYTHONPATH && .venv/Scripts/python.exe tests/m4_tutorial_e2e.py
"""
import os
import re
import sys
from pathlib import Path

for _k in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "ALL_PROXY", "all_proxy"):
    os.environ.pop(_k, None)

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient  # noqa: E402

from api.server import app  # noqa: E402

USER = "尼古喵喵"
PWD = "123456"
MD = ROOT / "docs" / "v2.0" / "演示文档" / "演示文档.md"

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

    print("\n== 0. 前置：教程文档存在 ==")
    check("演示文档.md 存在", MD.is_file(), str(MD))
    if not MD.is_file():
        return

    print("\n== 1. 登录 ==")
    r = c.post("/api/login", json={"username": USER, "password": PWD})
    check("POST /api/login 200", r.status_code == 200, r.text[:200])
    if r.status_code != 200:
        return
    token = r.json()["token"]

    print("\n== 2. GET /api/tutorial/doc ==")
    r = c.get("/api/tutorial/doc", params={"token": token})
    check("200", r.status_code == 200, r.text[:240])
    d = r.json()
    check("返回标题（取自 md 首个 # 行）", bool(d.get("title")) and "CLI" in d["title"], d.get("title"))
    check("返回相对路径（不泄露绝对路径）", d.get("path", "").startswith("docs/"), d.get("path"))
    check("返回更新时间与大小", bool(d.get("updated")) and d.get("size", 0) > 2000, (d.get("updated"), d.get("size")))
    check("正文长度与文件相当", len(d.get("content", "")) > 2000, len(d.get("content", "")))
    # 只要求「图片引用」不再是本机绝对路径（正文代码块里出现 C:\... 是教程内容，属正常）
    check("图片引用已改写（不再含本机绝对路径）",
          not re.search(r"!\[[^\]]*\]\([A-Za-z]:", d.get("content", "")),
          (re.findall(r"!\[[^\]]*\]\(([^)]{0,40})", d.get("content", ""))[:2]))

    print("\n== 3. 图片内联 ==")
    imgs = d.get("images", [])
    # 期望张数从 md 现场推导（文档会长新内容，硬编码数字会随内容漂移）
    md_path = os.path.join(ROOT, "docs", "v2.0", "演示文档", "演示文档.md")
    _md = open(md_path, encoding="utf-8").read()
    _want = len(re.findall(r"!\[[^\]]*\]\([^)]+\)", _md))
    check("识别到全部图片（md 里 %d 张）" % _want, len(imgs) == _want, [i.get("url") for i in imgs])
    check("没有缺失图片", d.get("missing") == [], d.get("missing"))
    check("图片 URL 都指向 /tutorial-assets/", all(i["url"].startswith("/tutorial-assets/") for i in imgs),
          [i.get("url") for i in imgs][:3])
    ok_img = 0
    for im in imgs:
        rr = c.get(im["url"])
        if rr.status_code == 200 and rr.headers.get("content-type", "").startswith("image/") and len(rr.content) > 1000:
            ok_img += 1
    check("每一张图片都真的可取到（200 + image/* + 非空）", ok_img == len(imgs), "%s/%s" % (ok_img, len(imgs)))
    check("外部贴图（Typora）也被同步进来", any("image-2026" in i["url"] for i in imgs),
          [i["url"] for i in imgs])
    check("md 正文里改写后的图片语法仍在", "![" in d["content"] and "(/tutorial-assets/" in d["content"])

    print("\n== 4. 安全 ==")
    check("无 token 被拒（4xx）", c.get("/api/tutorial/doc").status_code >= 400)
    check("错误 token 被拒（401）", c.get("/api/tutorial/doc", params={"token": "bad"}).status_code == 401)
    check("静态挂载挡住 ../ 穿越", c.get("/tutorial-assets/../.env").status_code in (400, 403, 404),
          c.get("/tutorial-assets/../.env").status_code)
    check("目录内不存在非图片扩展名被外泄（.md 不可通过资源位读取）",
          c.get("/tutorial-assets/演示文档.md").status_code == 404)

    print("\n== 5. md 结构保真 ==")
    md = d["content"]
    for frag, label in (("<details>", "折叠块（长终端回显可收起）"), ("```powershell", "powershell 代码块"),
                        ("```json", "json 代码块"), ("| 字段 |", "字段对照表"), ("## 5. 在「AI 助手」验证", "章节标题"),
                        ("**只读命令清单必须你给**", "加粗强调")):
        check("含 %s（%s）" % (frag[:18], label), frag in md)

    print("\n== 6. 首页结构 ==")
    html = c.get("/").text
    for frag in ("bubbles tut-entry", "📘 教程指导", "/api/tutorial/doc", "tutorial-view", "tutRender",
                 "openTutorial", "loadTutorial", "md-doc", "md-table", "md-pre", "md-det"):
        check("首页含 %s" % frag, frag in html, "")

    print("\n================ M4 教程指导 e2e 结果 ================")
    print("通过 %d 项，失败 %d 项" % (ok_cnt[0], len(fail)))
    if fail:
        print("失败清单：")
        for f in fail:
            print("  - " + f)
    print("=====================================================")
    sys.exit(1 if fail else 0)


if __name__ == "__main__":
    main()
