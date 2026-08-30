import sys
import os

# 将项目根目录加入 sys.path，确保 api 模块可导入
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

# LangChain 工具装饰器：将普通函数转为 Agent 可调用的工具
from langchain_core.tools import tool

# 自定义模块：工具调用埋点监控（需确保 api 模块可导入）
from api.monitor import monitor

# ======================== 路径约束 ========================
# 与 readtofile 保持一致：只能编辑 agents_docs 目录下的 .md / .txt 文件。
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
    candidate = os.path.normpath(os.path.join(_AGENTS_DOCS_DIR, filename))
    if candidate != _AGENTS_DOCS_DIR and not candidate.startswith(_AGENTS_DOCS_DIR + os.sep):
        raise ValueError(f"越权访问被拒绝：只允许操作 agents_docs 目录下的文件（收到 {filename}）")
    ext = os.path.splitext(candidate)[1].lower()
    if ext not in _ALLOWED_EXT:
        raise ValueError(f"仅允许编辑 .md 或 .txt 文件（收到扩展名 {ext or '空'}）")
    return candidate


@tool
def write_agent_doc(filename: str, content: str, mode: str = "w") -> str:
    """写入或追加内容到 agents_docs 目录下的一个 .md 或 .txt 文档。

    用途：主智能体需要把生成的知识/笔记/指令保存到 agents_docs 时调用。
    约束：filename 只能是 agents_docs 目录内的文件名（含子目录相对路径，
    禁止 ../ 穿越）；仅支持 .md 和 .txt；mode='w' 覆盖、mode='a' 追加。
    :param filename: 目标文件名，如 '周报规范.md'
    :param content: 要写入的文本内容
    :param mode: 'w' 覆盖写入（默认）或 'a' 追加到末尾
    :return: 成功/失败说明
    """
    monitor.report_tool(tool_name="写入文档工具",
                        args={"filename": filename, "mode": mode, "len": len(content)})
    if mode not in ("w", "a"):
        return f"写入失败：mode 仅支持 'w' 或 'a'（收到 {mode}）"
    try:
        path = _resolve_safe_path(filename)
    except ValueError as e:
        return f"写入失败：{e}"
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, mode, encoding='utf-8') as f:
            f.write(content)
        return f"写入成功：{filename}（{'覆盖' if mode == 'w' else '追加'}，{len(content)} 字符）"
    except Exception as e:
        return f"写入失败：{e}"


if __name__ == "__main__":
    # 简单自测（不依赖 Agent 运行时）
    print(write_agent_doc.invoke({"filename": "_selftest.md", "content": "# 自测\nhello", "mode": "w"}))
    print(write_agent_doc.invoke({"filename": "_selftest.md", "content": "\nappend", "mode": "a"}))
    from tools.readtofile import read_agent_doc, list_agent_docs
    print("--- list ---")
    print(list_agent_docs.invoke({}))
    print("--- read ---")
    print(read_agent_doc.invoke({"filename": "_selftest.md"}))
    # 清理自测文件
    import os as _os
    _p = _resolve_safe_path("_selftest.md")
    _os.remove(_p)
    print("自测文件已清理")
