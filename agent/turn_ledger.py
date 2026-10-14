# -*- coding: utf-8 -*-
"""运行中轮次的台账 —— 用于「后端被强杀 / 重启后补落中断占位」。

背景（2026-10-15 真机实测踩到）：端口冲突导致后端在**一轮正在跑**的时候重启，那一轮
没有任何回执就凭空消失 —— 用户看到「准备开始查」，然后对话直接结束，历史里悬着一个
没有回答的提问（下一轮装配时还会被当成待答问题）。

已有的 `⏹ 本轮回答已被中断（未完成）。` 占位只覆盖「用户点中断」这条路径
（`agent/cancel.py` → `AgentCancelled` → `/api/chat` 落库）；**进程被 kill 时根本走不到
那儿**（关服钩子也不保证执行），所以必须做**启动侧补偿**：

  - 一轮开始 → 台账写一条（会话 / 账号 / 问题原文 / 起始时间）
  - 一轮结束（正常、异常、中断）→ 删掉
  - 后端启动 → 扫台账，残留的每条补一句中断占位并清空

单机串行（与 `agent/cancel.py` 同一假设：同一时刻只跑一轮），故台账就是一个小 JSON 文件。
文件落在审计目录（运行态产物、已 gitignore）。**任何异常都不许影响问答主流程**（全程 try）。
"""
import json
import os
import threading
import time
from pathlib import Path

__all__ = ["begin", "end", "recover", "ledger_path"]

_LOCK = threading.Lock()
_TURNS: dict = {}          # thread_id -> {account_id, question, started_at}

PLACEHOLDER = ("⏹ 本轮回答已被重启 / 停止打断（未完成）。\n\n"
               "> 进程在回答过程中被停掉时，结果无法写回，所以这里留一条占位，"
               "避免历史里悬着一个没有回答的提问。重新提问即可。")


def ledger_path() -> Path:
    """台账文件路径（测试可用环境变量 `TURN_LEDGER_PATH` 覆盖到临时目录）。"""
    env = os.getenv("TURN_LEDGER_PATH")
    if env:
        return Path(env)
    base = Path("data/audit")
    try:
        from utils import paths                              # 路径唯一事实源
        fn = getattr(paths, "audit_dir", None)
        if callable(fn):
            base = Path(fn())
    except Exception:                                        # noqa: BLE001
        pass
    return base / "turn_inflight.json"


def _flush() -> None:
    p = ledger_path()
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(_TURNS, ensure_ascii=False), encoding="utf-8")
        tmp.replace(p)
    except Exception:                                        # noqa: BLE001
        pass                                                 # 台账写不了也不能影响问答


def begin(account_id, thread_id: str, question: str = "") -> None:
    """一轮开始：登记在跑状态。"""
    if not thread_id:
        return
    with _LOCK:
        _TURNS[str(thread_id)] = {"account_id": int(account_id or 0),
                                  "question": (question or "")[:500],
                                  "started_at": time.strftime("%Y-%m-%d %H:%M:%S")}
        try:
            _flush()
        except Exception:                                    # noqa: BLE001
            pass                                             # 台账绝不拖垮问答


def end(thread_id: str) -> None:
    """一轮结束（正常 / 异常 / 中断都走这里）：注销。"""
    if not thread_id:
        return
    with _LOCK:
        if _TURNS.pop(str(thread_id), None) is not None:
            try:
                _flush()
            except Exception:                                # noqa: BLE001
                pass


def recover() -> list:
    """后端启动时调用：把上次残留的在跑轮次补一句中断占位。返回处理过的清单。

    幂等：处理完即删台账文件，再调返回空列表。
    """
    p = ledger_path()
    if not p.exists():
        return []
    try:
        raw = json.loads(p.read_text(encoding="utf-8") or "{}")
    except Exception:                                        # noqa: BLE001
        raw = {}
    if not isinstance(raw, dict) or not raw:
        try:
            p.unlink()
        except Exception:                                    # noqa: BLE001
            pass
        return []
    try:
        from langchain_core.messages import AIMessage
        from agent import conversation_store as cs
    except Exception:                                        # noqa: BLE001
        return []
    done = []
    for tid, info in raw.items():
        info = info or {}
        aid = int(info.get("account_id") or 0)
        q = str(info.get("question") or "").strip()
        if not aid or not q:
            continue
        when = str(info.get("started_at") or "")
        text = PLACEHOLDER if not when else PLACEHOLDER.replace(
            "（未完成）。", "（未完成；该轮开始于 %s）。" % when)
        try:
            cs.save_turn(aid, tid, q, [AIMessage(content=text)])
            done.append({"thread_id": tid, "account_id": aid})
        except Exception as e:                               # noqa: BLE001
            print("[turn_ledger] 补占位失败（忽略）: %s: %s" % (type(e).__name__, e))
    try:
        p.unlink()
    except Exception:                                        # noqa: BLE001
        pass
    return done


def pending() -> dict:
    """只读：当前台账内容（测试 / 排查用）。"""
    return dict(_TURNS)
