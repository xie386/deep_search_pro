# -*- coding: utf-8 -*-
"""屏幕控制工具（v4.1-03）：鼠标点击与文字输入。

口径见 `docs/v4.1/Verdict/03-悬浮球-屏幕控制.md`：

* **只在「看 + 动」两档都开时可用**：`computer_use`（看）、`allow_control`（动手）。
  关闭时这两个工具会被 `ComputerUseGateMiddleware` 从工具表里摘掉 —— 与 02 的
  「关闭即不存在」口径一致，模型不会看到它们，也调不到。
* **坐标是图上的像素**：换算成物理坐标再注入（`物理 = 图上 ÷ scale + screen_rect 原点`）。
* **必须带 capture_id**：与最近一帧不一致直接拒绝 —— 防「看着旧帧点新界面」。
* **白名单**：前台窗口与落点窗口都必须在设置页勾选的进程名单里，名单为空全部拒绝。
* 每轮动作数受 `CU_MAX_ACTIONS_PER_TURN`（默认 20）约束。

动作面刻意很小：本步只做点击（含双击）与打字；右键/滚轮/拖选/组合键留后续 4.x。
"""
from __future__ import annotations

from langchain_core.tools import tool

from tools._runtime import cu_input
from utils import cu_mode, settings as _settings


def _limit() -> int:
    try:
        n = _settings.get_int("CU_MAX_ACTIONS_PER_TURN", 20)
    except Exception:                                    # noqa: BLE001
        n = 20
    return max(1, min(50, n))


def _guard(capture_id: int) -> tuple[bool, str]:
    """闸门 1~3：本轮是否允许动手、动作次数是否用完、帧号是否最新。"""
    turn = cu_mode.current()
    if not turn.on:
        return False, "本轮没有开启「computer use」（屏幕共享），无法操作屏幕。"
    if not turn.control:
        return False, "本轮只开了「屏幕共享」没有开「屏幕控制」。需要动手时请让用户在聊天页打开该开关后重发。"
    if turn.actions >= _limit():
        return False, "本轮动作次数已用完（上限 %d 次）。需要更多请让用户分多轮提问。" % _limit()
    if not turn.last_cid:
        return False, "还没有看过屏幕。动手前请先调用 screen_capture 截一帧，按那一帧的坐标操作。"
    try:
        cid = int(capture_id)
    except (TypeError, ValueError):
        return False, "capture_id 必须是整数（取 screen_capture 返回里的 capture #N）。"
    if cid != turn.last_cid:
        return False, ("屏幕已变化（当前最新帧是 capture #%d，你引用的是 #%d）。"
                       "请先重新调用 screen_capture，再按新帧操作。" % (turn.last_cid, cid))
    return True, ""


def _to_physical(x: int, y: int) -> tuple[int, int]:
    """图上坐标 → 物理像素坐标。"""
    turn = cu_mode.current()
    scale = float(turn.last_scale or 1.0)
    rect = tuple(turn.last_rect or ())
    ox, oy = (int(rect[0]), int(rect[1])) if len(rect) >= 2 else (0, 0)
    return int(round(float(x) / scale)) + ox, int(round(float(y) / scale)) + oy


@tool
def mouse_click(x: int, y: int, capture_id: int, reason: str = "", double: bool = False) -> str:
    """在屏幕上点击一下鼠标（左键）。只在开启「屏幕控制」的本轮可用。

    什么时候该用：
      · 需要在某个应用里点按钮、点链接、点输入框、点列表项时；
      · 先调用 screen_capture 看一眼，从图上读出要点的像素坐标，再把坐标传进来。
    什么时候不该用：
      · 还没截图（必须先看再点，工具会拒绝）；
      · 目标窗口不在用户设置的白名单里（会被告知拒绝原因）。
    参数：
      · x, y：**截图上**的像素坐标（左上角为原点），不是屏幕物理坐标，工具会自动换算；
      · capture_id：那一帧的编号（screen_capture 返回里的 capture #N），用于确认你看的是最新画面；
      · reason：一句话说明为什么要点这里（会写进对话与审计，用户能看懂）；
      · double：True 表示双击（默认单击）。
    返回：一行结果说明（点到哪、之后前台是什么窗口），或一句人话的拒绝原因。
    """
    ok, msg = _guard(capture_id)
    if not ok:
        return msg
    px, py = _to_physical(x, y)
    passed, who = cu_input.check_target(px, py)
    if not passed:
        return who
    try:
        cu_input.click(px, py, double=bool(double))
    except Exception as exc:                             # noqa: BLE001
        return "点击失败：%s（本轮其它能力不受影响）" % exc
    n = cu_mode.bump_action()
    cu_input.audit(_thread_id(), capture_id, "double_click" if double else "click",
                   x=px, y=py, reason=str(reason or "")[:200])
    fg = cu_input.foreground_process()
    where = cu_input.window_text_at(px, py)
    what = "双击" if double else "单击"
    return ("已在图上 (%d, %d)（屏幕物理 %d, %d）%s%s。当前前台进程：%s；落点所在窗口：%s。"
            "本轮已用 %d/%d 次动作。"
            % (x, y, px, py, what, ("；原因：" + reason.strip()) if reason and reason.strip() else "",
               fg or "未知", where or "（未知）", n, _limit()))


@tool
def type_text(text: str, capture_id: int, reason: str = "") -> str:
    """把一段文字输入到当前光标处（中文、Emoji 都可以）。只在开启「屏幕控制」的本轮可用。

    什么时候该用：
      · 已经点进某个输入框（先 mouse_click 点它），要往里面写字时；
      · 搜索框、聊天输入框、记事本、表单字段都适用。
    什么时候不该用：
      · 还没截图、或还没把焦点点进输入框（工具无法判断焦点在哪，请先点）；
      · 目标窗口不在白名单里（会被拒绝）。
    参数：
      · text：要输入的文字（原样输入，不要带引号）；
      · capture_id：那一帧的编号（必须是最新帧，用于确认界面没变）；
      · reason：一句话说明为什么要输入（会写进对话与审计）。
    返回：一行结果说明（输入了多少字、当前前台是什么窗口），或一句人话的拒绝原因。
    """
    ok, msg = _guard(capture_id)
    if not ok:
        return msg
    body = str(text or "")
    if not body:
        return "没有要输入的文字（text 为空）。"
    passed, who = cu_input.check_foreground()            # 打字没有落点，判定目标就是前台窗口
    if not passed:
        return who
    try:
        cu_input.type_text(body)
    except Exception as exc:                             # noqa: BLE001
        return "输入失败：%s（本轮其它能力不受影响）" % exc
    n = cu_mode.bump_action()
    cu_input.audit(_thread_id(), capture_id, "type", text_len=len(body),
                   reason=str(reason or "")[:200])
    fg = cu_input.foreground_process()
    return ("已输入 %d 个字符%s。当前前台进程：%s。本轮已用 %d/%d 次动作。"
            % (len(body), ("；原因：" + reason.strip()) if reason and reason.strip() else "",
               fg or "未知", n, _limit()))


@tool
def press_key(key: str, capture_id: int, reason: str = "", times: int = 1) -> str:
    """按一次键盘按键（回车 / 退格 / Tab / Esc / 方向键）。只在开启「屏幕控制」的本轮可用。

    什么时候该用：
      · **在搜索框里打完字之后，按回车提交搜索** —— 这是绝大多数人的习惯动作，
        比去找那个小小的放大镜按钮可靠得多（真机踩过：按钮太小，点偏了却没搜出来）；
      · 表单里按 Tab 跳到下一个输入框、对话框里按 Esc 关闭、列表里用方向键选择；
      · 输入错了按退格删字。
    不该用：
      · 想输入一段文字时用 `type_text`（本工具只按单个键，不输入字符）；
      · 发送消息、删除文件这类不可逆动作，先复述再动，且只按用户明确要求的那一下。
    参数：
      · key：按键名，可用 enter / esc / tab / space / backspace / delete / up / down / left / right / home / end / pageup / pagedown；
      · capture_id：那一帧的编号（必须是最新帧）；
      · reason：一句话说明为什么要按（写进对话与审计）；
      · times：连按几次（默认 1，最多 10）。
    返回：一行结果说明，或一句人话的拒绝原因。
    """
    ok, msg = _guard(capture_id)
    if not ok:
        return msg
    name = str(key or "").strip().lower()
    if name not in cu_input.VK:
        return ("不支持的按键：%s（可用：%s）"
                % (key, " / ".join(sorted(cu_input.VK))))
    passed, who = cu_input.check_foreground()            # 按键打进前台窗口，判前台即可
    if not passed:
        return who
    try:
        cu_input.press(name, times=times)
    except Exception as exc:                             # noqa: BLE001
        return "按键失败：%s（本轮其它能力不受影响）" % exc
    n = cu_mode.bump_action()
    cu_input.audit(_thread_id(), capture_id, "key", key=name, times=int(times or 1),
                   reason=str(reason or "")[:200])
    fg = cu_input.foreground_process()
    return ("已按下 %s%s。当前前台进程：%s。本轮已用 %d/%d 次动作。"
            % (name, ("（连按 %d 次）" % times) if int(times or 1) > 1 else "",
               fg or "未知", n, _limit()))


@tool
def mouse_scroll(x: int, y: int, notches: int, capture_id: int, reason: str = "") -> str:
    """在屏幕某个位置上滚动滚轮（翻列表、翻网页、翻节点）。只在开启「屏幕控制」的本轮可用。

    什么时候该用：
      · 目标在列表/长页面里没露出来（比如节点列表、聊天记录、网页正文），需要往下翻；
      · 翻完要**重新截图**再看，不要拿翻之前的坐标继续点（列表位置已经变了）。
    什么时候不该用：
      · 内容已经完整显示在画面里 —— 直接点即可，别多此一举；
      · 想点某个具体项而它已经在视野内 —— 用 `mouse_click`。
    参数：
      · x, y：**截图上**的像素坐标（滚动发生在光标所在位置，所以要先指到列表/页面区域上）；
      · notches：滚几格。正数=向上翻（回到前面），负数=向下翻（往后看）；单次最多 ±20 格，
        一格约等于鼠标滚轮"咔"一下；
      · capture_id：那一帧的编号（必须是最新帧，确认你滚的是当前这个界面）；
      · reason：一句话说明为什么要滚（写进对话与审计）。
    返回：一行结果说明（滚了几格、滚完前台是什么窗口），或一句人话的拒绝原因。
    """
    ok, msg = _guard(capture_id)
    if not ok:
        return msg
    try:
        n = int(notches)
    except (TypeError, ValueError):
        return "notches 必须是整数（正数向上翻，负数向下翻）。"
    if n == 0:
        return "notches 为 0，没有滚动。"
    px, py = _to_physical(x, y)
    passed, who = cu_input.check_target(px, py)
    if not passed:
        return who
    try:
        cu_input.scroll(px, py, n)
    except Exception as exc:                             # noqa: BLE001
        return "滚动失败：%s（本轮其它能力不受影响）" % exc
    cnt = cu_mode.bump_action()
    cu_input.audit(_thread_id(), capture_id, "scroll", x=px, y=py, notches=n,
                   reason=str(reason or "")[:200])
    fg = cu_input.foreground_process()
    where = cu_input.window_text_at(px, py)
    return ("已在图上 (%d, %d)（屏幕物理 %d, %d）向%s滚动 %d 格%s。当前前台进程：%s；"
            "滚动所在窗口：%s。本轮已用 %d/%d 次动作。建议重新截图确认翻到了哪里。"
            % (x, y, px, py, "上" if n > 0 else "下", abs(n),
               ("；原因：" + reason.strip()) if reason and reason.strip() else "",
               fg or "未知", where or "（未知）", cnt, _limit()))


def _thread_id() -> str:
    """审计用的会话号（轮内状态里带着，见 cu_mode.begin_turn）。"""
    return str(cu_mode.current().thread_id or "")
