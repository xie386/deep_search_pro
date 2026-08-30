import sys
import os

# 将项目根目录加入 sys.path，确保 api 模块可导入
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

# 类型注解
from typing import List
# LangChain 工具装饰器：将普通函数转为 Agent 可调用的工具
from langchain_core.tools import tool

# 自定义模块：工具调用埋点监控（需确保 api 模块可导入）
from api.monitor import monitor

# ======================== 路径约束 ========================
# 本工具只能读写 agents_docs 目录下的 .md / .txt 文件，避免越权访问项目其他位置。
_AGENTS_DOCS_DIR = os.path.abspath(
    os.path.join(os.path.dirname(__file__), '..', 'agents_docs')
)

_ALLOWED_EXT = ('.md', '.txt')


def _resolve_safe_path(filename: str) -> str:
    """把文件名解析为 agents_docs 下的绝对路径，并做越权校验。

    返回绝对路径；若越权或扩展名非法，抛出 ValueError。
    """
    if not filename:
        raise ValueError("filename 不能为空")
    # 归一化并禁止任何形式的路径穿越（../ 或绝对路径）
    candidate = os.path.normpath(os.path.join(_AGENTS_DOCS_DIR, filename))
    if candidate != _AGENTS_DOCS_DIR and not candidate.startswith(_AGENTS_DOCS_DIR + os.sep):
        raise ValueError(f"越权访问被拒绝：只允许操作 agents_docs 目录下的文件（收到 {filename}）")
    ext = os.path.splitext(candidate)[1].lower()
    if ext not in _ALLOWED_EXT:
        raise ValueError(f"仅允许读取 .md 或 .txt 文件（收到扩展名 {ext or '空'}）")
    return candidate


@tool
def read_agent_doc(filename: str) -> str:
    """读取 agents_docs 目录下的一个 .md 或 .txt 文档的全部内容。

    用途：主智能体需要参考 agents_docs 下维护的知识/指令文档时调用。
    约束：filename 只能是 agents_docs 目录内的文件名（含子目录相对路径也可，
    但禁止 ../ 路径穿越）；仅支持 .md 和 .txt 扩展名，其余一律拒绝。
    :param filename: 目标文件名，如 '周报规范.md' 或 'sub/notes.txt'
    :return: 文件完整文本；文件不存在或越权时返回错误说明
    """
    monitor.report_tool(tool_name="读取文档工具",
                        args={"filename": filename})
    try:
        path = _resolve_safe_path(filename)
    except ValueError as e:
        return f"读取失败：{e}"
    if not os.path.isfile(path):
        return f"读取失败：文件不存在 —— {filename}（agents_docs 目录下）"
    try:
        with open(path, 'r', encoding='utf-8') as f:
            content = f.read()
        return content
    except Exception as e:
        return f"读取失败：{e}"


@tool
def list_agent_docs() -> str:
    """列出 agents_docs 目录下所有允许读取的 .md / .txt 文件（含子目录）。

    用途：主智能体不确定有哪些文档可用时，先调用本工具枚举文件名。
    :return: 文件清单（每行一个相对路径）；目录为空时返回提示
    """
    monitor.report_tool(tool_name="列出文档工具", args={})
    results: List[str] = []
    if not os.path.isdir(_AGENTS_DOCS_DIR):
        return "agents_docs 目录不存在"
    for root, _dirs, files in os.walk(_AGENTS_DOCS_DIR):
        for fn in files:
            if fn.lower().endswith(_ALLOWED_EXT):
                rel = os.path.relpath(os.path.join(root, fn), _AGENTS_DOCS_DIR)
                results.append(rel)
    if not results:
        return "agents_docs 目录下暂无 .md / .txt 文件"
    return "agents_docs 下的文档：\n" + "\n".join("- " + r for r in sorted(results))


if __name__ == "__main__":
    # 简单自测（不依赖 Agent 运行时）
    print(list_agent_docs.invoke({}))
