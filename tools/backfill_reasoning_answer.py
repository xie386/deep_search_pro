# -*- coding: utf-8 -*-
"""历史数据回填：把「正文被写进 reasoning_content、content 为空」的 assistant 行补回正文。

背景（2026-09-24 用户报障）：推理模型偶尔把可见正文写进 `reasoning_content`、`content` 留空。
当时服务端落库时**整条消息都存了**（`additional_kwargs` 含 reasoning），所以历史行是可救的：
    content='' + additional_kwargs.reasoning_content='没有找到相关视频。让我看看…（正文）'

只处理满足全部条件的行（宁可漏、不可错）：
  ① role='assistant'；② TRIM(content)=''；③ tool_calls 为空（工具轮的空 content 是协议要求，绝不碰）；
  ④ additional_kwargs 里的 reasoning_content 非空且够长（默认 ≥30 字，避免把「子目录创建成功。」这类
     中间推理片段当成正文回填）。

默认 **dry-run（只打印不写库）**；确认无误后加 `--apply` 才落库，且落库前自动备份受影响行到
`%TEMP%/backfill_reasoning_backup.json`。

用法：
    .venv/Scripts/python.exe tools/backfill_reasoning_answer.py            # 预览（不写）
    .venv/Scripts/python.exe tools/backfill_reasoning_answer.py --apply    # 真正回填
"""
from __future__ import annotations

import argparse
import io
import json
import os
import sys
import tempfile
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools.schema_personal import get_personal_conn  # noqa: E402

MIN_LEN = 30  # reasoning 太短的多半是中间推理片段，不当正文


def find_rows(con):
    rows = con.execute(
        "SELECT id, conversation_id, additional_kwargs FROM messages "
        "WHERE role='assistant' AND TRIM(COALESCE(content,''))='' "
        "AND COALESCE(tool_calls,'')='' ORDER BY id"
    ).fetchall()
    out = []
    for r in rows:
        try:
            rc = str((json.loads(r["additional_kwargs"] or "{}") or {}).get("reasoning_content") or "")
        except Exception:  # noqa: BLE001
            rc = ""
        if len(rc.strip()) >= MIN_LEN:
            out.append({"id": r["id"], "conversation_id": r["conversation_id"], "text": rc.strip()})
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="真正写库（默认只预览）")
    args = ap.parse_args()

    con = get_personal_conn()
    rows = find_rows(con)
    print(f"扫描完成：可回填行 {len(rows)} 条（判据：content 空 + 无 tool_calls + reasoning ≥{MIN_LEN} 字）\n")
    for r in rows:
        print(f"  #{r['id']}（会话 {r['conversation_id']}）→ 回填 {len(r['text'])} 字： {r['text'][:80]}…")

    if not rows:
        con.close()
        print("\n无需回填。")
        return 0
    if not args.apply:
        con.close()
        print("\n[dry-run] 未写库。确认上面内容确实是正文后，加 --apply 执行：")
        print("    .venv/Scripts/python.exe tools/backfill_reasoning_answer.py --apply")
        return 0

    bak = os.path.join(tempfile.gettempdir(),
                       f"backfill_reasoning_backup_{datetime.now():%Y%m%d_%H%M%S}.json")
    io.open(bak, "w", encoding="utf-8").write(json.dumps(rows, ensure_ascii=False, indent=1))
    for r in rows:
        con.execute("UPDATE messages SET content=? WHERE id=? AND TRIM(COALESCE(content,''))=''", (r["text"], r["id"]))
    con.commit()
    con.close()
    print(f"\n✅ 已回填 {len(rows)} 条；原值备份：{bak}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
