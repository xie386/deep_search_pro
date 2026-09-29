"""端到端验证：把 read/write 文档工具挂到全局主智能体后，让 Agent 实例
实际调用工具完成一个需要读写 agents_docs 的简单任务。

用法（项目根目录）：
    .venv/Scripts/python.exe tests/tool_agent_e2e_test.py

验证链路：
  1. 导入项目的全局智能体实例 AGENT（api/server.py 中的单例）
  2. 给一个简单任务：在 agents_docs 下创建 _e2e_hello.md 写入固定内容，再读回确认
  3. 检查：Agent 真正调用了工具（文件落地 / 能读回一致内容）
  4. 清理生成的测试文件

不跑全量冒烟，只验证「工具已挂上 + Agent 能驱动工具」这一条链路。
"""
import json
import os
import sys

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, _ROOT)
os.chdir(_ROOT)
sys.path = [p for p in sys.path if "hermes-agent" not in p and "hermes_agent" not in p]

from langchain_core.messages import HumanMessage

# 导入项目全局主智能体实例（已挂载 read_agent_doc / write_agent_doc / list_agent_docs）
from api.server import AGENT

DOCS_DIR = os.path.join(_ROOT, "agents_docs")
TEST_FILE = "_e2e_hello.md"
TEST_CONTENT = "智选情报官端到端验证：工具读写链路正常。"
TEST_PATH = os.path.join(DOCS_DIR, TEST_FILE)

PASS = 0
FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✅ {name}")
    else:
        FAIL += 1
        print(f"  ❌ {name}  -> {detail}")


def main():
    print("== 导入全局 AGENT 实例 ==")
    check("AGENT 实例存在", AGENT is not None, "import 失败")
    # 确认工具模块可导入（说明已挂到全局 AGENT 的 tools 列表）
    try:
        from tools.readtofile import read_agent_doc, list_agent_docs
        from tools.writetofile import write_agent_doc
        # @tool 装饰后是 StructuredTool 实例，有 .name 属性即证明已定义
        check("read_agent_doc 已定义", hasattr(read_agent_doc, "name"), "未导出")
        check("write_agent_doc 已定义", hasattr(write_agent_doc, "name"), "未导出")
    except Exception as e:
        check("工具模块可导入", False, repr(e))

    # 先清掉可能残留的测试文件
    if os.path.exists(TEST_PATH):
        os.remove(TEST_PATH)

    print("\n== 任务：让 Agent 写文件并报回内容 ==")
    task = (
        f"请使用你的文档工具，在 agents_docs 目录下创建文件 {TEST_FILE}，"
        f"写入以下内容（一字不差）：{TEST_CONTENT}\n"
        f"写完后，再用读取工具把它读回来，确认内容一致，并简短回复『已完成：<你读到的内容>』。"
    )
    cfg = {"configurable": {"thread_id": "e2e_tool_test"}}
    try:
        result = AGENT.invoke({"messages": [HumanMessage(content=task)]}, cfg)
        answer = result["messages"][-1].content or ""
    except Exception as e:
        answer = ""
        check("Agent 调用未抛异常", False, repr(e))

    print("    Agent 回复（节选）:", answer[:200].replace("\n", " "))
    check("Agent 调用未抛异常", bool(answer), "无回复")

    print("\n== 校验工具真实生效（文件落地）==")
    check("文件已生成", os.path.exists(TEST_PATH), f"{TEST_PATH} 不存在")
    if os.path.exists(TEST_PATH):
        with open(TEST_PATH, "r", encoding="utf-8") as f:
            written = f.read()
        check("写入内容一致", written.strip() == TEST_CONTENT, repr(written))
        check("Agent 回复含读回内容", TEST_CONTENT in answer, "回复未回显内容")

    # 清理
    if os.path.exists(TEST_PATH):
        os.remove(TEST_PATH)
        print("\n已清理测试文件", TEST_FILE)

    print(f"\n结果：通过 {PASS} / 失败 {FAIL}")
    if FAIL:
        sys.exit(1)


if __name__ == "__main__":
    main()
