# -*- coding: utf-8 -*-
"""只读探针：悬浮球窗口的透明观感 / DPI 坐标 / 跨窗口登录态 / 展开几何。

只开自建本地页面，不碰项目后端、不碰用户数据；两个窗口 5 秒后自关，
截图落 tests/test_out/（已 gitignore）。
跑法：.venv/Scripts/python.exe tests/debug/_spike_ball_window.py
"""
import ctypes
import http.server
import json
import socketserver
import threading
import time
from ctypes import wintypes
from pathlib import Path

import webview

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "tests" / "test_out"
OUT.mkdir(parents=True, exist_ok=True)

BALL_HTML = """<!doctype html><html><head><meta charset="utf-8"><style>
html,body{margin:0;background:transparent;overflow:hidden}
#ball{width:100%;height:100%;border-radius:50%;background:radial-gradient(circle at 32% 30%,#ffd479,#e2892b);
color:#3a2408;font:600 26px/1 sans-serif;user-select:none;display:flex;align-items:center;justify-content:center}
</style></head><body><div id="ball" class="pywebview-drag-region">●</div>
<script>window.mark=v=>{localStorage.setItem('spike',v);return localStorage.getItem('spike')};
window.read=()=>localStorage.getItem('spike');</script></body></html>"""


class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        body = BALL_HTML.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


srv = socketserver.TCPServer(("127.0.0.1", 0), Handler)
srv.daemon_threads = True
threading.Thread(target=srv.serve_forever, daemon=True).start()
URL = "http://127.0.0.1:%d/" % srv.server_address[1]


def hwnd_of(win):
    h = win.native.Handle
    return h.ToInt64() if hasattr(h, "ToInt64") else int(h)


def rect(win):
    r = wintypes.RECT()
    ctypes.windll.user32.GetWindowRect(hwnd_of(win), ctypes.byref(r))
    return (r.left, r.top, r.right - r.left, r.bottom - r.top)


def colorkey(win, rgb=(255, 0, 255)):
    """色键：整窗刷成键色 → 系统把该色抠成透明且点击穿透。"""
    hwnd = hwnd_of(win)
    ctypes.windll.user32.SetWindowRgn(hwnd, None, True)          # 撤掉上一轮的裁形
    GWL_EXSTYLE, WS_EX_LAYERED = -20, 0x00080000
    ex = ctypes.windll.user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
    ctypes.windll.user32.SetWindowLongW(hwnd, GWL_EXSTYLE, ex | WS_EX_LAYERED)
    key = rgb[0] | (rgb[1] << 8) | (rgb[2] << 16)
    return bool(ctypes.windll.user32.SetLayeredWindowAttributes(hwnd, key, 0, 0x00000001))


def shape_circle(win):
    """把窗口裁成圆形（物理像素）：圆外区域既不可见、也不接收点击。"""
    _, _, w, h = rect(win)
    rgn = ctypes.windll.gdi32.CreateRoundRectRgn(0, 0, w + 1, h + 1, w, h)
    hwnd = hwnd_of(win)
    ok = bool(ctypes.windll.user32.SetWindowRgn(hwnd, rgn, True))
    # WebView2 若以子窗口形态存在，父窗体裁形不一定管得到它 —— 子窗口也裁一遍
    CB = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
    hits = []

    def _child(chwnd, _):
        r2 = ctypes.windll.gdi32.CreateRoundRectRgn(0, 0, w + 1, h + 1, w, h)
        hits.append(bool(ctypes.windll.user32.SetWindowRgn(chwnd, r2, True)))
        return True

    ctypes.windll.user32.EnumChildWindows(hwnd, CB(_child), 0)
    RDW = 0x0001 | 0x0004 | 0x0020 | 0x0100 | 0x0080
    ctypes.windll.user32.RedrawWindow(hwnd, None, None, RDW)
    print(json.dumps({"children_shaped": hits}, ensure_ascii=False))
    return ok


def report(win, tag):
    print(json.dumps({"tag": tag, "win32_rect_xywh": rect(win),
                      "api_xywh": [win.x, win.y, win.width, win.height]}, ensure_ascii=False))


def shot(name, box=None):
    from PIL import ImageGrab
    img = ImageGrab.grab(all_screens=True)
    if box:
        img = img.crop(box)
    p = OUT / name
    img.save(p)
    return str(p), img


def corners(img, w, h):
    pts = ((1, 1), (w - 2, 1), (1, h - 2), (w // 2, 1))
    return [img.getpixel(pt) for pt in pts]


def worker(ball, panel):
    time.sleep(2.5)
    aware = ctypes.c_int()
    ctypes.windll.shcore.GetProcessDpiAwareness(None, ctypes.byref(aware))
    print(json.dumps({"process_dpi_awareness": aware.value,
                      "screens": [{"x": s.x, "y": s.y, "w": s.width, "h": s.height}
                                  for s in webview.screens]}, ensure_ascii=False))
    report(ball, "collapsed")
    report(panel, "second_window")
    f = ball.native
    print(json.dumps({"native": {"size": str(f.Size), "client": str(f.ClientSize),
                                 "min": str(f.MinimumSize), "autosize": str(f.AutoSize),
                                 "border": str(f.FormBorderStyle)}}, ensure_ascii=False))
    print(json.dumps({"localStorage": {
        "set_in_ball": ball.evaluate_js("window.mark('shared-across-windows')"),
        "read_in_panel": panel.evaluate_js("window.read()")}}, ensure_ascii=False))

    x, y, w, h = rect(ball)
    M = 24  # 裁剪时留出窗口外的边距，好对比「窗内角」与「窗外桌面」

    def probes(img):
        return {"outside_tl": img.getpixel((2, 2)),
                "inside_tl": img.getpixel((M + 2, M + 2)),
                "inside_tr": img.getpixel((M + w - 3, M + 2)),
                "inside_bl": img.getpixel((M + 2, M + h - 3)),
                "inside_top_mid": img.getpixel((M + w // 2, M + 2))}

    path_a, img_a = shot("_spike_ball_before_shape.png",
                         (x - M, y - M, x + w + M, y + h + M))
    print(json.dumps({"before_shape_png": path_a, "probes": probes(img_a)}, ensure_ascii=False))

    ok = shape_circle(ball)
    time.sleep(0.6)
    path_b, img_b = shot("_spike_ball_after_shape.png",
                         (x - M, y - M, x + w + M, y + h + M))
    print(json.dumps({"shape_ok": ok, "after_shape_png": path_b, "probes": probes(img_b)},
                     ensure_ascii=False))

    # 方案 B：色键透明（窗体背景刷成品红，系统抠掉）
    try:
        import clr
        clr.AddReference("System.Drawing")
        from System.Drawing import Color
        ball.native.BackColor = Color.Fuchsia
        ball.native.TransparencyKey = Color.Fuchsia
        form_ok = True
    except Exception as e:
        form_ok = "ERR:%s" % e
    ck = colorkey(ball)
    time.sleep(0.8)
    path_k, img_k = shot("_spike_ball_colorkey.png",
                         (x - M, y - M, x + w + M, y + h + M))
    print(json.dumps({"form_transparency_key": form_ok, "colorkey_ok": ck,
                      "colorkey_png": path_k, "probes": probes(img_k)}, ensure_ascii=False))

    # 方案 C：直接把 WebView2 控制器的背景色置透明（看 pywebview 当时设成了什么）
    try:
        from System.Drawing import Color as _Color
        wv = ball.native.webview
        before = str(wv.DefaultBackgroundColor)
        wv.DefaultBackgroundColor = _Color.Transparent
        time.sleep(0.8)
        path_t, img_t = shot("_spike_ball_wv_transparent.png",
                             (x - M, y - M, x + w + M, y + h + M))
        print(json.dumps({"wv_bg_before": before, "wv_bg_after": str(wv.DefaultBackgroundColor),
                          "wv_transparent_png": path_t, "probes": probes(img_t)},
                         ensure_ascii=False))
    except Exception as e:
        print(json.dumps({"wv_bg": "ERR:%s" % e}, ensure_ascii=False))

    # 创建期被钳制的尺寸能否改回来：强制 68×68（物理 136×136）
    ctypes.windll.user32.SetWindowPos(hwnd_of(ball), None, x, y, 136, 136, 0)
    time.sleep(0.5)
    report(ball, "forced_68")
    path_d, _ = shot("_spike_ball_forced68.png", (x - M, y - M, x + 136 + M, y + 136 + M))
    print(json.dumps({"forced68_png": path_d}, ensure_ascii=False))

    ball.resize(300, 260)
    ball.move(ball.x - 232, ball.y)
    time.sleep(0.8)
    report(ball, "expanded_anchored")
    path_c, _ = shot("_spike_ball_expanded.png")
    print(json.dumps({"expanded_png": path_c}, ensure_ascii=False))

    ball.destroy()
    panel.destroy()
    srv.shutdown()


if __name__ == "__main__":
    x0, y0 = 40, 40
    ball = webview.create_window("spike-ball", URL, x=x0, y=y0, width=68, height=68,
                                 min_size=(24, 24), frameless=True, on_top=True,
                                 transparent=True, shadow=False, easy_drag=True,
                                 focus=False, background_color="#000000")
    panel = webview.create_window("spike-panel", URL, x=x0 + 420, y=y0 + 260, width=200, height=120)
    webview.start(worker, (ball, panel), private_mode=False,
                  storage_path=str(OUT / "_spike_profile"))
