# -*- coding: utf-8 -*-
"""审计事件落盘（v4.0 大点 04 · 议题二）：账号类敏感动作留痕。

为什么单独一个流：`data/audit/` 下已有 commands / tool_calls / agent_errors / decision 四个流，
各自服务一类事件；**账号安全动作**（改设置、绑邮箱、注销）归 `account.jsonl`，
排查时一眼能看出「谁在什么时候动了账号」。
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
AUDIT_DIR = PROJECT_ROOT / "data" / "audit"


def write_event(stream: str, event: str, **fields) -> str:
    """往 `data/audit/<stream>.jsonl` 追加一条。**永不抛错**（审计失败不该打断业务）。"""
    try:
        AUDIT_DIR.mkdir(parents=True, exist_ok=True)
        rec = {"ts": time.strftime("%Y-%m-%d %H:%M:%S"), "event": event}
        rec.update({k: v for k, v in fields.items() if v is not None})
        p = AUDIT_DIR / ("%s.jsonl" % stream)
        with open(p, "a", encoding="utf-8", newline="\n") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        return str(p)
    except Exception as e:                                            # noqa: BLE001
        print("[audit] 写入失败（忽略）: %s" % e)
        return ""
