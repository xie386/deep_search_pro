import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))


def test_tool_listing_tolerates_json_string_schemas():
    """★ 2026-10-18 真机：DB 行里 `input_schema`/`invoke_spec` 是 **JSON 字符串**，
    `tool_listing` 直接 .get() → AttributeError → 接口 500 → 前端只看到 "Unexpected token 'I'"。"""
    import json
    from tools.capability_draft import tool_listing

    caps = [{
        "ref": "mcp:mineru/parse_documents", "name": "parse_documents", "read_only": False,
        "input_schema": json.dumps({"type": "object",
                                    "properties": {"file_sources": {"type": "array"}, "language": {"type": "string"}},
                                    "required": ["file_sources"]}),
        "invoke_spec": json.dumps({"server_id": 1, "tool": "parse_documents"}),
        "abilities": "", "abilities_user": "",
    }]
    out = tool_listing(caps)
    assert "mcp:mineru/parse_documents" in out
    assert "★file_sources" in out and "language" in out
    assert "写操作" in out

    # 脏数据（不是 JSON / 是 list）也不炸
    bad = [{"ref": "x", "input_schema": "not json", "invoke_spec": [], "name": "x"}]
    assert "| `x` |" in tool_listing(bad)
