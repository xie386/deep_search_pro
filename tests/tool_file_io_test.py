"""writetofile / readtofile 工具验证脚本（轻量，不跑全量冒烟）。

运行：从项目根目录执行
    .venv/Scripts/python.exe test/tool_file_io_test.py

覆盖场景：
  1. 写入 .md 文件（覆盖 / 追加）
  2. 读取该文件，内容与写入一致
  3. 列出 agents_docs 能枚举到该文件
  4. 越权路径（../ 穿越、绝对路径）被拒绝
  5. 非法扩展名（.py / 空）被拒绝
  6. 读取不存在文件返回友好提示
"""
import os
import sys

# 项目根目录入 path，保证 tools.* 可导入
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT)
# 复刻 server.py 的防御：剔除 Hermes venv 同名顶层包污染（agent/api 等）
sys.path = [p for p in sys.path if "hermes-agent" not in p and "hermes_agent" not in p]

from tools.writetofile import write_agent_doc
from tools.readtofile import read_agent_doc, list_agent_docs

PASS = 0
FAIL = 0


def check(name: str, cond: bool, detail: str = ""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✅ {name}")
    else:
        FAIL += 1
        print(f"  ❌ {name}  -> {detail}")


print("== 1. 写入与读取 ==")
w = write_agent_doc.invoke({"filename": "_verify_tmp.md", "content": "# 标题\n正文内容", "mode": "w"})
check("写入 .md 覆盖成功", w.startswith("写入成功"), w)
r = read_agent_doc.invoke({"filename": "_verify_tmp.md"})
check("读取内容一致", r == "# 标题\n正文内容", repr(r))

print("== 2. 追加 ==")
w2 = write_agent_doc.invoke({"filename": "_verify_tmp.md", "content": "\n追加行", "mode": "a"})
check("追加成功", w2.startswith("写入成功"), w2)
r2 = read_agent_doc.invoke({"filename": "_verify_tmp.md"})
check("追加后内容正确", r2 == "# 标题\n正文内容\n追加行", repr(r2))

print("== 3. 写入 .txt ==")
wt = write_agent_doc.invoke({"filename": "sub/note.txt", "content": "plain text", "mode": "w"})
check("写入子目录 .txt 成功", wt.startswith("写入成功"), wt)
rt = read_agent_doc.invoke({"filename": "sub/note.txt"})
check("读取 .txt 内容一致", rt == "plain text", repr(rt))

print("== 4. 列出目录 ==")
ls = list_agent_docs.invoke({})
check("枚举到两份文档", "_verify_tmp.md" in ls and os.path.join("sub", "note.txt") in ls, ls)

print("== 5. 越权路径拒绝 ==")
bad1 = write_agent_doc.invoke({"filename": "../secret.md", "content": "x"})
check("拒绝 ../ 穿越写入", "越权" in bad1, bad1)
bad2 = read_agent_doc.invoke({"filename": "../../etc/passwd"})
check("拒绝 ../ 穿越读取", "越权" in bad2, bad2)
bad3 = write_agent_doc.invoke({"filename": "C:/windows/win.ini", "content": "x"})
check("拒绝绝对路径写入", "越权" in bad3, bad3)

print("== 6. 非法扩展名拒绝 ==")
bad4 = write_agent_doc.invoke({"filename": "evil.py", "content": "x"})
check("拒绝 .py 写入", "仅允许" in bad4, bad4)
bad5 = read_agent_doc.invoke({"filename": "data.json"})
check("拒绝 .json 读取", "仅允许" in bad5, bad5)

print("== 7. 读取不存在 ==")
miss = read_agent_doc.invoke({"filename": "nope.md"})
check("不存在文件返回提示", "不存在" in miss, miss)

print(f"\n结果：通过 {PASS} / 失败 {FAIL}")
# 清理自测产物
for f in ("_verify_tmp.md", os.path.join("sub", "note.txt")):
    p = os.path.join(ROOT, "agents_docs", f)
    if os.path.exists(p):
        os.remove(p)
sub = os.path.join(ROOT, "agents_docs", "sub")
if os.path.isdir(sub) and not os.listdir(sub):
    os.rmdir(sub)
if FAIL:
    sys.exit(1)
