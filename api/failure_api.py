# -*- coding: utf-8 -*-
"""M6c-6 · 失败分类出口 `GET /api/failures?days=7`（M6c 方案 §5.3）。

给两件事用：① 排障（"昨晚它为什么没起来"）；② M6b 的探针/崩溃基线（枚举化之后才好写阈值）。
范围：自己的失败 + **系统级的（account_id IS NULL）**，跨账号互相不可见。
"""
from fastapi import HTTPException, status


def failures(token: str, days: int = 7) -> dict:
    from api.customize import _require_account_id
    from tools.failure_log import summary
    aid = _require_account_id(token)
    try:
        days = max(0, int(days))
    except (TypeError, ValueError):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "days 要是整数天数")
    out = summary(aid, days)
    # 顺带把码表文案带上（前端不用再内置一份，避免两处口径漂移）
    try:
        import os as _os
        import sys as _sys
        _root = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
        if _root not in _sys.path:
            _sys.path.insert(0, _root)
        from front.desktop.exit_codes import CODES
        out["codes"] = {k: {"severity": v[0], "label": v[1], "advice": v[2]} for k, v in CODES.items()}
    except Exception:
        out["codes"] = {}
    return out
