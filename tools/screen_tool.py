# -*- coding: utf-8 -*-
"""屏幕捕获工具（v4.1-02 · 屏幕共享）。

口径见 `docs/v4.1/Verdict/02-悬浮球-屏幕共享.md`：

1. **按会话开**：只有本轮 `computer_use=1` 时才放行；关闭时直接拒绝，不截屏。
2. **整屏一帧**：用 `mss` 抓物理像素，按长边 1456 + JPEG q85 压缩（本机实测约 200 KB / 130 ms）。
3. **张数由 agent 决定、上限兜底**：`SCREEN_MAX_SHOTS_PER_TURN`（默认 6，可设 1~10），超了拒绝。
4. **同屏去重**：压缩后字节的 sha256 与上一帧相同 → 只回一行说明，不再送图（省视觉 token）。
5. **纯内存**：图片以 data URL 随工具消息返回，不落盘；轮末 `cu_mode.end_turn()` 清场，什么都不留。
6. **自身窗口**：主窗与面板靠 `WDA_EXCLUDEFROMCAPTURE` 常驻排除；悬浮球由外壳在共享期间隐藏
   （分层窗对该属性是假成功，实测过，见裁定第 3 条）。

坐标口径：返回值里给出 `screen_rect`（物理像素的原点与宽高）与 `scale`（送进去的图 ÷ 物理像素）。
03 的点击必须按同一帧的这两个值换算，不要另算一套。
"""
from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from langchain_core.tools import tool                       # noqa: E402
from PIL import Image                                      # noqa: E402

from utils import cu_mode                                   # noqa: E402
from utils import settings as _settings                     # noqa: E402


def _edge() -> int:
    try:
        return max(320, min(2048, int(_settings.get("SCREEN_SHOT_EDGE", 1456))))
    except Exception:                                       # noqa: BLE001
        return 1456


def _quality() -> int:
    try:
        return max(50, min(95, int(_settings.get("SCREEN_SHOT_QUALITY", 85))))
    except Exception:                                       # noqa: BLE001
        return 85


def _max_shots() -> int:
    try:
        return max(1, min(10, int(_settings.get("SCREEN_MAX_SHOTS_PER_TURN", 6))))
    except Exception:                                       # noqa: BLE001
        return 6


def _grab(monitor: int = 0) -> tuple[bytes, dict]:
    """抓一帧并压到 JPEG。返回 (jpeg 字节, 元信息)。"""
    import mss                                              # 延迟导入：未开启时不付这份开销

    with mss.mss() as sct:
        mons = sct.monitors
        idx = int(monitor) if 0 <= int(monitor) < len(mons) else 0
        mon = mons[idx]
        raw = sct.grab(mon)

    img = Image.frombytes("RGB", raw.size, raw.bgra, "raw", "BGRX")
    w, h = img.size
    edge = _edge()
    scale = min(1.0, edge / float(max(w, h)))
    if scale < 1.0:
        img = img.resize((max(1, int(round(w * scale))), max(1, int(round(h * scale)))), Image.BILINEAR)

    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=_quality(), optimize=True)
    data = buf.getvalue()
    meta = {
        "monitor": idx,
        "screen_rect": [int(mon["left"]), int(mon["top"]), int(mon["width"]), int(mon["height"])],
        "image_size": [int(img.width), int(img.height)],
        "scale": round(float(img.width) / float(w), 4),
        "bytes": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
    }
    return data, meta


def _data_url(jpeg: bytes) -> str:
    return "data:image/jpeg;base64," + base64.b64encode(jpeg).decode("ascii")


@tool
def screen_capture(reason: str = "", monitor: int = 0) -> list | str:
    """看一眼用户当前的屏幕，返回一张截图。只在「computer use」开关打开的本轮可用。

    什么时候该用：
      · 用户问「屏幕上这个报错什么意思」「这个表怎么填」「现在停在哪个页面」这类必须看画面的问题；
      · 需要多页时（翻页、滚动后的新画面）可以连续调用，一页一次；同一画面重复调用会被去重。
    什么时候不该用：
      · 用户只是文字提问、和屏幕无关；或者开关没开（会直接被告知不可用）。
    参数：
      · reason：一句话说明为什么要看（会显示在对话里，便于用户理解你在看什么）；
      · monitor：显示器序号，缺省 0 = 所有显示器合成一帧；多屏时可用 1、2… 单独取一屏。
    返回：
      · 正常：一条说明文字 + 一张图（模型可以直接看）；文字里带 capture_id、图像尺寸、屏幕矩形与缩放比；
      · 同一画面重复截：只回一行「与上一帧相同」，不重复送图；
      · 未开启 / 次数用完 / 抓屏失败：只回一句人话原因，不抛异常。
    """
    turn = cu_mode.current()
    if not turn.on:
        return ("本轮没有开启「computer use」（屏幕共享）模式，看不到屏幕。"
                "需要看屏时请让用户在聊天页打开该开关后重发。")
    if turn.shots >= _max_shots():
        return "本轮截图次数已用完（上限 %d 张）。需要更多请让用户分多轮提问。" % _max_shots()

    try:
        jpeg, meta = _grab(monitor)
    except ImportError:
        return "缺少 mss 依赖，暂时无法截屏：请先在项目环境执行 `uv pip install mss`。"
    except Exception as exc:                                # noqa: BLE001
        return "抓屏失败：%s（本轮其它能力不受影响）" % exc

    cid = turn.next_cid
    turn.next_cid += 1
    turn.shots += 1
    reason_txt = ("原因：%s。" % reason.strip()) if reason and reason.strip() else ""
    head = ("已截取第 %d 帧（capture #%d，%dx%d，屏幕 %dx%d，scale %.3f）。%s"
            % (turn.shots, cid, meta["image_size"][0], meta["image_size"][1],
               meta["screen_rect"][2], meta["screen_rect"][3], meta["scale"], reason_txt))

    if meta["sha256"] == turn.last_hash:
        turn.last_cid = cid
        turn.last_meta = meta
        return head + "画面与上一帧完全相同，已复用上一帧（capture #%d），不再重复送图。" % cid

    turn.last_hash = meta["sha256"]
    turn.last_cid = cid
    turn.last_meta = meta
    meta_line = "帧信息：" + json.dumps(
        {"capture_id": cid, "screen_rect": meta["screen_rect"], "scale": meta["scale"]},
        ensure_ascii=False)
    return [
        {"type": "text", "text": head + "\n" + meta_line},
        {"type": "image_url", "image_url": {"url": _data_url(jpeg)}},
    ]
