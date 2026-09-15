"""入库记录管理（M3）——每用户入库记录 JSON：md5 去重 / 列表 / 删除。

记录文件：rag_knowledge/db/{username}/store.json
- 去重键 source_id = md5(内容)（批注 11：同内容重复导出/上传只入库一份）
- chroma 删除文档时同步清记录（kb_service.delete_doc 调用）
"""
from __future__ import annotations
 
import json
import threading
from pathlib import Path

from rag_knowledge.kb_service import user_db_dir

_lock = threading.Lock()


def _store_path(username: str) -> Path:
    return user_db_dir(username) / "store.json"


def _read(username: str) -> list[dict]:
    p = _store_path(username)
    if not p.exists():
        return []
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return []


def _write(username: str, records: list[dict]):
    p = _store_path(username)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")


def record_exists(username: str, source_id: str) -> bool:
    with _lock:
        return any(r["source_id"] == source_id for r in _read(username))


def add_record(username: str, rec: dict):
    with _lock:
        records = _read(username)
        if any(r["source_id"] == rec["source_id"] for r in records):
            return False
        records.append(rec)
        _write(username, records)
        return True


def remove_record(username: str, source_id: str) -> bool:
    with _lock:
        records = _read(username)
        left = [r for r in records if r["source_id"] != source_id]
        if len(left) == len(records):
            return False
        _write(username, left)
        return True


def list_records(username: str) -> list[dict]:
    """按入库时间倒序（新在前）。"""
    with _lock:
        return sorted(_read(username), key=lambda r: r.get("created_at", ""), reverse=True)
