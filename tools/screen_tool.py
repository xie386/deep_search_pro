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
from pathlib import Path

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from langchain_core.tools import tool                       # noqa: E402
from PIL import Image                                      # noqa: E402

from utils import cu_mode                                   # noqa: E402
from utils import settings as _settings                     # noqa: E402


_BRIDGE = Path(__file__).resolve().parents[1] / "data" / "_cu_bridge.json"


def _pid_alive(pid: int) -> bool:
    """进程还在不在（Windows 上没有安全的 os.kill(pid, 0)，改用 OpenProcess 探活）。"""
    if not pid:
        return False
    try:
        import ctypes
        k32 = ctypes.windll.kernel32
        h = k32.OpenProcess(0x1000, False, int(pid))      # PROCESS_QUERY_LIMITED_INFORMATION
        if h:
            k32.CloseHandle(h)
            return True
        return False
    except Exception:                                       # noqa: BLE001
        return False


def _endpoint() -> tuple[str, str]:
    """抓图端点：优先环境变量（外壳自己 spawn 的后端），否则读桥接文件。

    ★ 为什么要有桥接文件：用户常常手动 `python -m uvicorn ...` 起后端，桌面壳启动时
      会「复用」它 —— 那个进程不是外壳的子进程，拿不到 ZX_CU_ENDPOINT，只能本进程抓，
      结果是主窗、面板、球全被拍进画面（2026-11-09 真机反馈「截图屏蔽没生效」）。
      外壳启动时会把端点写进 data/_cu_bridge.json，后端按文件回调即可。
    """
    ep = (os.environ.get("ZX_CU_ENDPOINT") or "").strip()
    if ep:
        return ep, os.environ.get("ZX_CU_TOKEN", "")
    try:
        info = json.loads(_BRIDGE.read_text(encoding="utf-8"))
        ep = str(info.get("endpoint") or "").strip()
        token = str(info.get("token") or "")
        if ep and token and _pid_alive(int(info.get("pid") or 0)):
            return ep, token
    except Exception:                                       # noqa: BLE001
        pass
    return "", ""


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
        return max(1, min(50, int(_settings.get("SCREEN_MAX_SHOTS_PER_TURN", 6))))
    except Exception:                                       # noqa: BLE001
        return 6


def _max_zooms() -> int:
    """局部放大帧**不占**截图额度（用户 2026-11-10 口径），但也不能无限刷：按截图额度的 2 倍封顶。"""
    return min(60, max(12, 2 * _max_shots()))


def _grab(monitor: int = 0, region=None) -> tuple[bytes, dict]:
    """抓一帧并压到 JPEG。返回 (jpeg 字节, 元信息)。

    ★ 桌面版走外壳的抓图服务（`ZX_CU_ENDPOINT`）：只有外壳进程能给自己窗口设截图排除、
      并把球临时藏起来；后端自己抓会把主窗/面板/球一起拍进去。
      没有该环境变量（纯 web 版 / 后端独立跑）时退回本进程直接抓。
    """
    endpoint, token = _endpoint()
    if endpoint:
        print("[CU] 抓图走外壳：%s%s" % (endpoint, "（局部放大）" if region else ""), flush=True)
        return _grab_via_shell(endpoint, token, monitor, region)
    print("[CU] 抓图走本进程（没有外壳端点，主窗/面板不会被排除）", flush=True)
    return _grab_local(monitor, region)


def _grab_via_shell(endpoint: str, token: str, monitor: int, region=None) -> tuple[bytes, dict]:
    """让外壳抓一帧（它会在抓的瞬间排除自身窗口、藏球，抓完还原 —— 用户自己的截图不受影响）。

    `region` = 物理像素 (x, y, w, h)：外壳按原生像素裁出这一块（小就放大），用于小图标精定位。
    """
    import urllib.request

    url = "%s/capture?token=%s&monitor=%d" % (endpoint.rstrip("/"), token, int(monitor))
    if region:
        url += "&region=" + ",".join(str(int(v)) for v in region)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(url, timeout=25) as resp:
        data = resp.read()
        meta = json.loads(resp.headers.get("X-CU-Meta") or "{}")
    if not data:
        raise RuntimeError("外壳返回了空图")
    meta.setdefault("bytes", len(data))
    return data, meta


def _grab_local(monitor: int = 0, region=None) -> tuple[bytes, dict]:
    """本进程直接抓（没有外壳时用；此时没有窗口排除，也没有球可藏）。

    `region` 语义与外壳一致：按物理像素裁剪、小则放大，不按 SCREEN_SHOT_EDGE 缩。
    """
    import mss                                              # 延迟导入：未开启时不付这份开销

    factory = getattr(mss, "MSS", None) or mss.mss      # mss>=10.2 推荐 MSS()，老版本回落
    with factory() as sct:
        mons = sct.monitors
        idx = int(monitor) if 0 <= int(monitor) < len(mons) else 0
        mon = mons[idx]
        raw = sct.grab(mon)

    img = Image.frombytes("RGB", raw.size, raw.bgra, "raw", "BGRX")
    if region:
        rx, ry, rw, rh = [int(v) for v in region]
        left, top = max(0, rx - mon["left"]), max(0, ry - mon["top"])
        box = (left, top, min(left + rw, img.width), min(top + rh, img.height))
        if box[2] - box[0] < 8 or box[3] - box[1] < 8:
            raise ValueError("局部区域太小（%dx%d）" % (box[2] - box[0], box[3] - box[1]))
        img = img.crop(box)
        long_edge = max(img.width, img.height)
        zoom = 3.0 if long_edge < 300 else (2.0 if long_edge < 900 else 1.0)
        if zoom > 1.0:
            img = img.resize((int(round(img.width * zoom)), int(round(img.height * zoom))), Image.LANCZOS)
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=95, optimize=True)
        data = buf.getvalue()
        return data, {
            "monitor": idx,
            "screen_rect": [rx, ry, rw, rh],
            "image_size": [int(img.width), int(img.height)],
            "scale": round(float(img.width) / float(rw), 4),
            "zoom": round(float(zoom), 2),
            "bytes": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
        }
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
def screen_capture(reason: str = "", monitor: int = 0, region: list | None = None) -> list | str:
    """看一眼用户当前的屏幕，返回一张截图。只在「computer use」开关打开的本轮可用。

    什么时候该用：
      · 用户问「屏幕上这个报错什么意思」「这个表怎么填」「现在停在哪个页面」这类必须看画面的问题；
      · 需要多页时（翻页、滚动后的新画面）可以连续调用，一页一次；同一画面重复调用会被去重。
    什么时候该**先放大再看**（region 参数，本轮不算截图额度）：
      · 要点的目标在画面里很小 —— **外接框长边不到 40 个图像像素**（在 1456 宽的图里约等于
        一个指甲盖的 1/4）时，直接点很容易偏。典型：标题栏的最小化/关闭按钮、工具栏图标、
        浏览器地址栏的小图标、下拉小三角、桌面图标、卡片右上角的「×」。
      · 做法：先看整屏，估出目标在**当前这张图**上的大概位置，再调
        `screen_capture(region=[x, y, w, h])` 把那一小块放大看清楚，然后按放大图里的坐标点。
      · 大按钮、整行卡片、输入框、菜单项（在图上明显看得出边界）**不要**走这一步，直接点即可。
    什么时候不该用：
      · 用户只是文字提问、和屏幕无关；或者开关没开（会直接被告知不可用）。
    参数：
      · reason：一句话说明为什么要看（会显示在对话里，便于用户理解你在看什么）；
      · monitor：显示器序号，缺省 0 = 所有显示器合成一帧；多屏时可用 1、2… 单独取一屏；
      · region：可选，`[x, y, 宽, 高]`，坐标是**当前最新那一帧图上的像素**（不是屏幕物理像素），
        工具会自己换算。返回的是这一块的**原生分辨率放大图**（必要时放大 2~3 倍），
        不占本轮截图额度；它同样会给出新的 capture_id / 屏幕矩形 / 缩放比，后续点击以它为准。
    返回：
      · 正常：一条说明文字 + 一张图（模型可以直接看）；文字里带 capture_id、图像尺寸、屏幕矩形与缩放比；
      · 同一画面重复截：只回一行「与上一帧相同」，不重复送图；
      · 未开启 / 次数用完 / 抓屏失败：只回一句人话原因，不抛异常。
    """
    turn = cu_mode.current()
    if not turn.on:
        return ("本轮没有开启「computer use」（屏幕共享）模式，看不到屏幕。"
                "需要看屏时请让用户在聊天页打开该开关后重发。")

    zooming = region is not None and len(list(region or [])) == 4
    if zooming:
        if not turn.last_cid or not turn.last_rect:
            return ("放大前需要先有一张整屏图：请先调用 `screen_capture`（不带 region）看全屏，"
                    "再用那张图上的坐标指定 region。")
        if turn.zooms >= _max_zooms():
            return "本轮局部放大次数已用完（上限 %d 次）。请直接用当前画面上的坐标操作。" % _max_zooms()
    elif turn.shots >= _max_shots():
        return "本轮截图次数已用完（上限 %d 张）。需要更多请让用户分多轮提问。" % _max_shots()

    if zooming:
        try:
            zx, zy, zw, zh = [int(float(v)) for v in region]
        except Exception:                                   # noqa: BLE001
            return "region 必须是 [x, y, 宽, 高] 四个数字（当前那张图上的像素坐标）。"
        if zw < 16 or zh < 16:
            return "region 的宽高太小（至少 16×16 图上像素），请把要看的控件连同周围留点边一起框进来。"
        scale, rect = float(turn.last_scale or 1.0), tuple(turn.last_rect or ())
        px = int(round(rect[0] + zx / scale))
        py = int(round(rect[1] + zy / scale))
        pw = max(8, int(round(zw / scale)))
        ph = max(8, int(round(zh / scale)))
        try:
            jpeg, meta = _grab(monitor, (px, py, pw, ph))
        except ImportError:
            return "缺少 mss 依赖，暂时无法截屏：请先在项目环境执行 `uv pip install mss`。"
        except Exception as exc:                            # noqa: BLE001
            return "局部抓取失败：%s（可以退一步直接按整屏坐标点）" % exc
        cid = max(turn.next_cid, turn.last_cid + 1)      # 帧号必须单调：绝不和上一帧撞号
        turn.next_cid = cid + 1
        turn.zooms += 1
        turn.last_cid = cid
        turn.last_meta = meta
        cu_mode.set_frame(cid, meta["scale"], meta["screen_rect"])
        reason_txt = ("原因：%s。" % reason.strip()) if reason and reason.strip() else ""
        head = ("已放大第 %d 块（capture #%d，放大 %.1f 倍，局部图 %dx%d，屏幕区域 %dx%d）。%s\n"
                % (turn.zooms, cid, float(meta.get("zoom") or 1.0),
                   meta["image_size"][0], meta["image_size"][1],
                   meta["screen_rect"][2], meta["screen_rect"][3], reason_txt))
        meta_line = "帧信息：" + json.dumps(
            {"capture_id": cid, "screen_rect": meta["screen_rect"], "scale": meta["scale"],
             "zoom_frame": True}, ensure_ascii=False)
        return [{"type": "text", "text": head + meta_line},
                {"type": "image_url", "image_url": {"url": _data_url(jpeg)}}]

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
        cu_mode.set_frame(cid, meta["scale"], meta["screen_rect"])
        return head + "画面与上一帧完全相同，已复用上一帧（capture #%d），不再重复送图。" % cid

    turn.last_hash = meta["sha256"]
    turn.last_cid = cid
    turn.last_meta = meta
    cu_mode.set_frame(cid, meta["scale"], meta["screen_rect"])
    meta_line = "帧信息：" + json.dumps(
        {"capture_id": cid, "screen_rect": meta["screen_rect"], "scale": meta["scale"]},
        ensure_ascii=False)
    return [
        {"type": "text", "text": head + "\n" + meta_line},
        {"type": "image_url", "image_url": {"url": _data_url(jpeg)}},
    ]
