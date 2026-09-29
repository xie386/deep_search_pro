# -*- coding: utf-8 -*-
"""生成「时钟 + 天气卡」的 Vue 沙盒页面，用于**视觉验收**（本机 CDP/浏览器自动化不可用时的替代方案）。

为什么需要它：
  - 卡片是 Vue 内联模板（in-DOM template）渲染的，静态 HTML 预览（build_weather_preview.js）复现不了
    「浏览器解析 → Vue 编译 → 渲染」这条真实链路（实测过：`v-else` 因为被插入了别的元素而失效，
    静态预览完全看不出来，只有真实 Vue 渲染才暴露）。
  - 本机 `agent-browser`/CDP 不可用，改用 **无头 Chrome 截图 + 看图** 做视觉验收。

用法：
    .venv/Scripts/python.exe scripts/build_wx_harness.py            # 生成 front/tutorial/previews/_wx_harness.html（展开态）
    .venv/Scripts/python.exe scripts/build_wx_harness.py pill city   # 追加收起态/换城市态两个页面
    # 截图（Windows，Chrome 路径按需改）：
    chrome --headless=new --disable-gpu --hide-scrollbars --window-size=1300,620 \
           --virtual-time-budget=3000 --screenshot=tests/test_out/_wx_shot.png \
           file:///<项目绝对路径，中文需 URL 编码>/front/tutorial/previews/_wx_harness.html

要点：CSS 与卡片标记都**从 front/index.html 现场抽取**，所以只要真页面变了，沙盒必然跟着变（不会漂移）。
"""
import os
import re
import sys
from urllib.parse import quote

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HTML_PATH = os.path.join(ROOT, "front", "index.html")
OUT_DIR = os.path.join(ROOT, "front", "tutorial", "previews")

WX_JSON = """{ city:'成都·四川', city_input:'', now:{temp:20.4,feels:21.8,humidity:76,wind:4.8,text:'晴间多云',icon:'🌤️'},
  daily:[{date:'2026-09-11',icon:'☁️',tmax:23.5,tmin:18.0,rain:2},{date:'2026-09-12',icon:'🌦️',tmax:23.4,tmin:16.9,rain:47},{date:'2026-09-13',icon:'🌦️',tmax:24.9,tmin:19.0,rain:55}],
  source:'open-meteo.com（免 Key）', updated:'2026-09-11 19:28:41' }"""

STATS = """    <div class="stat-grid"><div class="stat-card c1"><div class="num">2</div><div class="lbl">关注竞品</div></div>
    <div class="stat-card c2"><div class="num">6</div><div class="lbl">我司产品</div></div>
    <div class="stat-card c3"><div class="num">9</div><div class="lbl">已填档案字段</div></div>
    <div class="stat-card c4"><div class="num">0</div><div class="lbl">本次会话问答</div></div></div>"""


def build():
    html = open(HTML_PATH, encoding="utf-8", newline="").read()
    css = re.search(r"<style>([\s\S]*?)</style>", html).group(1)
    i = html.index("<!-- 时钟 + 天气（012 号")
    j = html.index("\n          </div>", i)          # hero 的收尾
    dock = html[i:j]
    if 'wxSkin' not in dock or 'wx-card-load' not in dock:
        raise SystemExit("抽到的卡片标记不完整，锚点可能已变")

    def page(open_state, city_edit, title, skin="green", has_wx=True):
        return """<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8"><title>%s</title>
<style>
%s
    html, body { margin: 0; background: #eef2f7; font-family: "Microsoft YaHei", system-ui, sans-serif; }
</style></head><body>
<div id="app">
  <div class="home" style="background:#fff;border-radius:18px;">
    <div class="hero">
      <span class="hero-clip"><i class="hero-glow"></i></span>
      <span class="emoji">🏢</span>
      <div>
        <h2>晚上好，上海猫良灵康生物科技有限公司</h2>
        <p>这里是你的竞品情报工作台。维护好公司资料与关注竞品后，AI 助手可产出「我们 vs 竞品」的对比简报。</p>
      </div>
%s
    </div>
%s
  </div>
</div>
<script src="../front/vendor/vue.global.prod.js"></script>
<script>
Vue.createApp({ setup() {
  const wx = %s;
  return { wx, clockTime: '19:28:41', clockDate: '2026 年 09 月 11 日 · 星期五',
           role: 'company', displayName: '上海猫良灵康生物科技有限公司', greeting: '晚上好',
           wxOpen: %s, wxLoading: false, wxErr: '', wxCityEdit: %s, wxCityInput: '杭州', wxSkin: '%s',
           WX_SKINS: [{k:'green',t:'松绿',c1:'#4fae6b',c2:'#1f5c37'},{k:'amber',t:'暖阳',c1:'#e2ab34',c2:'#8a5a08'},
                      {k:'indigo',t:'靛蓝',c1:'#4f6bd8',c2:'#23306f'},{k:'violet',t:'紫罗兰',c1:'#5936b4',c2:'#362a84'}],
           wxPromptCity() {}, wxSetCity() {}, wxCancelCity() {}, wxSetSkin() {} };
} }).mount('#app');
</script></body></html>""" % (title, css, dock, STATS, WX_JSON if has_wx else "null",
                              "true" if open_state else "false", "true" if city_edit else "false", skin)

    os.makedirs(OUT_DIR, exist_ok=True)
    jobs = [("_wx_harness.html", True, False, "weather: 展开态")]
    if "pill" in sys.argv:
        jobs.append(("_wx_harness_pill.html", False, False, "weather: 收起态"))
    if "city" in sys.argv:
        jobs.append(("_wx_harness_city.html", True, True, "weather: 换城市"))
    if "fallback" in sys.argv:
        jobs.append(("_wx_harness_fallback.html", True, False, "weather: 无数据兜底卡", "green", False))
    for job in jobs:
        name, o, c, t = job[:4]
        skin = job[4] if len(job) > 4 else "green"
        has_wx = job[5] if len(job) > 5 else True
        p = os.path.join(OUT_DIR, name)
        open(p, "w", encoding="utf-8", newline="").write(page(o, c, t, skin, has_wx))
        print("写好了 %s" % p)
    url = "file:///" + quote(os.path.join(OUT_DIR, "_wx_harness.html").replace("\\", "/"))
    print("\n截图命令示例：\n  chrome --headless=new --disable-gpu --hide-scrollbars "
          "--window-size=1300,620 --virtual-time-budget=3000 --screenshot=tests/test_out/_wx_shot.png\n      %s" % url)


if __name__ == "__main__":
    build()
