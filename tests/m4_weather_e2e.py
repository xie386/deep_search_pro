# -*- coding: utf-8 -*-
"""M4 收尾 · 工作台时钟 + 天气卡 —— 端到端验证（后端侧）

覆盖：
  1) 纯函数：WMO 天气码映射（覆盖全部已登记码 + 未知码兜底）
  2) GET /api/weather  城市级：city/经纬度/省份/国家 + 实时字段 + 3 天以上预报
  3) 缓存：同城二次调用走内存（<50ms），force=1 强制刷新
  4) 换城市：city 参数生效；不存在的城市 → 404 且给人话提示
  5) 鉴权：无 token 被拒
  6) 磁盘缓存：取数成功后会落到 data/weather_cache.json（重启首屏秒出的前提）
  7) 前端接线：index.html 里时钟秒级 / 天气 ref / 换城市 / 012 卡片结构

运行：.venv/Scripts/python.exe tests/m4_weather_e2e.py
说明：本脚本会真的访问 open-meteo.com（首次约 10~20s，之后走缓存）。无外网时明确报 FAIL，不静默跳过。
"""
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("PYTHONIOENCODING", "utf-8")

from fastapi.testclient import TestClient  # noqa: E402

from api import server  # noqa: E402

PASS, FAIL = [], []


def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name)
    print("%s %s%s" % ("  ✅" if cond else "  ❌", name, (" | " + str(extra)) if extra else ""))


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HTML = open(os.path.join(ROOT, "static", "index.html"), encoding="utf-8").read()


def main():
    print("\n=== M4 收尾 · 时钟 + 天气卡（后端） ===\n")

    # 1) WMO 映射
    print("[1] WMO 天气码映射")
    for code, (text, icon) in server.WMO_TEXT.items():
        t, i = server._wx_code(code)
        if t != text or i != icon:
            check("码 %s 映射为 %s/%s" % (code, text, icon), False, "实际 %s/%s" % (t, i))
            break
    else:
        check("全部 %d 个已登记天气码映射正确" % len(server.WMO_TEXT), True)
    check("未知码兜底（999 -> 未知）", server._wx_code(999) == ("未知", "🌡️"))
    check("非法码兜底（None/abc）", server._wx_code(None) == ("未知", "🌡️") and server._wx_code("abc") == ("未知", "🌡️"))
    check("默认城市来自 .env（WEATHER_CITY）", server.DEFAULT_CITY == os.getenv("WEATHER_CITY", "成都"), server.DEFAULT_CITY)

    c = TestClient(server.app)
    r = c.post("/api/login", json={"username": "尼古喵喵", "password": "123456"})
    if r.status_code != 200:
        check("测试账号登录", False, r.text[:120])
        return summary()
    tok = r.json()["token"]
    check("测试账号登录", True)

    # 2) 城市级实时天气
    print("\n[2] GET /api/weather（真实外网）")
    t0 = time.time()
    r = c.get("/api/weather", params={"token": tok}, timeout=90)
    live = time.time() - t0
    if r.status_code != 200:
        check("天气接口 200", False, "HTTP %s %s" % (r.status_code, r.text[:200]))
        return summary()
    check("天气接口 200（耗时 %.1fs）" % live, True)
    d = r.json()
    check("城市级：city 含城市名", bool(d.get("city")), d.get("city"))
    check("城市级：返回省份/国家", bool(d.get("admin1")) and bool(d.get("country")), "%s / %s" % (d.get("admin1"), d.get("country")))
    check("城市级：返回城市经纬度（非全国中心点）", abs(float(d["lat"])) > 0.01 and abs(float(d["lon"])) > 0.01, "%s, %s" % (d["lat"], d["lon"]))
    now = d.get("now") or {}
    check("实时：温度/体感/湿度/风速齐全",
          all(now.get(k) is not None for k in ("temp", "feels", "humidity", "wind")),
          "temp=%s feels=%s hum=%s wind=%s" % (now.get("temp"), now.get("feels"), now.get("humidity"), now.get("wind")))
    check("实时：天气码已翻译成中文 + 图标", bool(now.get("text")) and bool(now.get("icon")), "%s %s" % (now.get("icon"), now.get("text")))
    check("实时：温度在合理区间（-60~60）", -60 < float(now["temp"]) < 60, now.get("temp"))
    dl = d.get("daily") or []
    check("预报：≥3 天且每天有 高/低温 + 天气码", len(dl) >= 3 and all(x.get("tmax") is not None and x.get("tmin") is not None and x.get("text") for x in dl),
          "%d 天 %s" % (len(dl), [(x["date"][5:], x["icon"], x["tmax"], x["tmin"]) for x in dl[:3]]))
    check("预报：每日高温 ≥ 低温", all(float(x["tmax"]) >= float(x["tmin"]) for x in dl))
    check("注明数据源与更新时间", "open-meteo" in (d.get("source") or "") and bool(d.get("updated")), "%s · %s" % (d.get("source"), d.get("updated")))

    # 3) 缓存
    print("\n[3] 缓存")
    t0 = time.time()
    r2 = c.get("/api/weather", params={"token": tok}, timeout=30)
    cached = time.time() - t0
    check("同城二次调用走内存缓存（<0.2s）", r2.status_code == 200 and cached < 0.2, "%.3fs" % cached)
    check("缓存命中内容一致", r2.json().get("updated") == d.get("updated"))
    t0 = time.time()
    r3 = c.get("/api/weather", params={"token": tok, "force": 1}, timeout=90)
    check("force=1 强制刷新成功", r3.status_code == 200, "%.1fs" % (time.time() - t0))
    check("磁盘缓存已落盘 data/weather_cache.json", server.WX_CACHE_FILE.is_file(),
          "%.1f KB" % (server.WX_CACHE_FILE.stat().st_size / 1024) if server.WX_CACHE_FILE.is_file() else "缺失")

    # 4) 换城市
    print("\n[4] 换城市")
    r4 = c.get("/api/weather", params={"token": tok, "city": "杭州"}, timeout=90)
    check("换城市 city=杭州 生效", r4.status_code == 200 and "杭州" in (r4.json().get("city") or ""),
          r4.json().get("city") if r4.status_code == 200 else r4.text[:120])
    check("不同城市坐标不同", r4.status_code == 200 and (r4.json().get("lat"), r4.json().get("lon")) != (d.get("lat"), d.get("lon")))
    r5 = c.get("/api/weather", params={"token": tok, "city": "Chengdu"}, timeout=90)
    check("英文城市名可用（Chengdu）", r5.status_code == 200 and "成都" in (r5.json().get("city") or ""),
          r5.json().get("city") if r5.status_code == 200 else r5.text[:120])
    r6 = c.get("/api/weather", params={"token": tok, "city": "zzz不存在的城市zzz"}, timeout=60)
    check("不存在的城市 → 404 + 人话提示", r6.status_code == 404 and "没找到城市" in r6.text, r6.text[:120])

    # 5) 鉴权
    print("\n[5] 鉴权")
    check("无 token 被拒", c.get("/api/weather").status_code in (401, 422))
    check("错 token 被拒", c.get("/api/weather", params={"token": "bad"}).status_code in (401, 403, 404))

    # 6) 前端接线（真代码文本）
    print("\n[6] 前端接线（static/index.html）")
    check("秒级时钟：setInterval(tickClock, 1000)", "setInterval(tickClock, 1000)" in HTML)
    check("时钟补零 + 时分秒", "clockTime.value = _pad2(d.getHours()) + ':' + _pad2(d.getMinutes()) + ':' + _pad2(d.getSeconds())" in HTML)
    check("中文日期 + 星期", "'星期日', '星期一'" in HTML and " 日 · ' + wk" in HTML)
    check("天气 10 分钟自动刷新", "10 * 60 * 1000" in HTML)
    check("onMounted 启动时钟/天气", "wxStart(); });" in HTML)
    check("收起态药丸（图标+温度+城市+秒）", 'class="wx-mini-clock"' in HTML and 'class="wx-mini-city"' in HTML)
    check("悬浮/点击展开（hover + open 双通道）",
          ".wx-dock:hover .wx-card, .wx-dock.open .wx-card" in HTML and '@mouseenter="wxOpen = true"' in HTML and 'wxToggleDock($event)' in HTML)
    check("012 卡片结构：渐变背景 + 云 + 大号读数 + H/L",
          'id="wxgrad"' in HTML and 'class="cloud"' in HTML and 'class="main-text"' in HTML and "H:{{ wx.daily[0]" in HTML)
    check("3 天预报 + 体感/湿度/风", 'wx.daily.slice(0, 3)' in HTML and "体感 {{ wx.now.feels }}°" in HTML and "💨 {{ wx.now.wind }}" in HTML)
    check("换城市入口：卡片内联输入（无系统 prompt）+ localStorage 记忆",
          "wxPromptCity()" in HTML and "wxSetCity()" in HTML and "wxCityInput" in HTML
          and "dw_wx_city_" in HTML and "window.prompt" not in HTML)
    check("清空城市 = 回落 .env 默认（不带 city 参数）", 'if (!name) loadWeather(true);' in HTML)
    check("加载中/失败态有兜底卡", "wx-card-load" in HTML and "正在获取天气…" in HTML)
    check("窄屏自动隐藏，不挤坏欢迎语", "@media (max-width: 1080px) { .wx-dock { display: none; } }" in HTML)
    check("setup 已导出 clock/wx 变量（含内联换城市）",
          "clockTime, clockDate, wx, wxLoading, wxErr, wxOpen, wxCityEdit, wxCityInput," in HTML
          and "loadWeather, wxPromptCity, wxCancelCity, wxSetCity, wxStart," in HTML)
    check("卡片从药丸下方展开（不再居中越出 hero）", "top: calc(100% - 2px)" in HTML and "translateY(-50%)" not in HTML.split("/* ==== 012")[1].split("</style>")[0])
    check("hero 放开裁切 + 装饰光晕改自带裁切的装饰层",
          ".hero-clip { position: absolute; inset: 0; overflow: hidden" in HTML and '<span class="hero-clip"><i class="hero-glow"></i></span>' in HTML
          and "position: relative; overflow: visible;   /* 放开裁切" in HTML)
    check("兜底卡用独立 v-if（不再依赖相邻 v-if/v-else，避免被插在中间的元素打断）",
          '<div class="wx-card wx-card-load" v-if="!wx && !wxCityEdit">' in HTML and 'wx-card-load" v-else' not in HTML)
    check("展开卡与换城市面板互斥（不会叠在一起）",
          'v-if="wx && !wxCityEdit"' in HTML and 'v-if="!wx && !wxCityEdit"' in HTML)
    check("配色：4 套主题变量（默认松绿=项目主色 #4fae6b）",
          '.wx-dock { position: relative' in HTML and "--wx-c1: #4fae6b; --wx-c2: #1f5c37" in HTML
          and HTML.count('.wx-dock[data-wx="') == 3)
    check("配色：硬编码紫色已变量化（仅配色表与 SVG 兜底值保留）",
          HTML.count("linear-gradient(120deg, var(--wx-c1)") >= 2 and "rgba(var(--wx-sh)" in HTML)
    check("配色：SVG 渐变由 CSS 变量喂入（换主题无需改标记）",
          ".wx-card .background stop:first-of-type { stop-color: var(--wx-c1); }" in HTML)
    check("配色：右下角圆点切换 + 按账号记忆",
          'class="wx-skin"' in HTML and "wxSetSkin(s.k)" in HTML and ":data-wx=\"wxSkin\"" in HTML and "dw_wx_skin_" in HTML)
    check("悬浮悖论修复：卡片贴合药丸（无间隙断点）", "top: calc(100% - 2px)" in HTML and "top: calc(100% + 10px)" not in HTML)
    check("悬浮悖论修复：卡片/面板内部点击不收起", "wxToggleDock($event)" in HTML and "closest('.wx-card, .wx-city-form')" in HTML)
    check("登录后补取天气（挂载早于登录的场景）+ 登出清理",
          "watch(token, (v) => {" in HTML and "if (_wxStarted) loadWeather(); else wxStart();" in HTML
          and "wx.value = null; wxErr.value = '';" in HTML)
    check("兜底卡单独紧凑高度（不留半张空卡）", ".wx-card-load { height: 208px; }" in HTML)
    check("兜底卡有「↻ 重新获取」按钮", 'class="wx-retry"' in HTML and "loadWeather(true)" in HTML and "登录后会自动获取" in HTML)
    check("登录卡悬浮热区改成「容器 + 渐长热区」（展开动画期间不掉 hover）",
          ".login-container:hover .login-card {" in HTML and ".login-container::after {" in HTML
          and ".login-container:hover::after { height: 380px; }" in HTML)
    check("登录卡热区不挡表单点击（z-index 在卡片之下）",
          "top: 0; height: 80px; z-index: -1;" in HTML)
    check("天气卡只在首页（home view 内）", HTML.find('class="wx-dock"') > HTML.find('view === \'home\''))

    return summary()


def summary():
    print("\n" + "=" * 56)
    print("M4 天气/时钟 e2e：通过 %d，失败 %d" % (len(PASS), len(FAIL)))
    if FAIL:
        print("失败项：")
        for f in FAIL:
            print("  -", f)
        return 1
    print("全部通过 ✅")
    return 0


if __name__ == "__main__":
    sys.exit(main())
