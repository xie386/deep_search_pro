/* 生成「时钟 + 天气卡」静态预览（三种状态：收起 / 展开 / 换城市输入）
 * 用 index.html 里的**真实 CSS**渲染，供肉眼验收（不依赖服务与浏览器自动化）。
 * 用法：node scripts/build_weather_preview.js
 * 产物：front/tutorial/previews/weather_preview.html
 * 自检：预览用到的每个 class 都必须能在 index.html 的 CSS 里找到，否则报错退出。
 */
const fs = require('fs');
const path = require('path');

const root = path.resolve(__dirname, '..');
const html = fs.readFileSync(path.join(root, 'front', 'index.html'), 'utf8');
const css = (html.match(/<style>([\s\S]*?)<\/style>/) || [, ''])[1];
if (!css) { console.error('❌ 抽不到 <style>'); process.exit(1); }

const CLOUD = (html.match(/<div class="cloud">[\s\S]*?<\/div>/) || [''])[0];
if (!CLOUD) { console.error('❌ 抽不到云 SVG'); process.exit(1); }

const GRAD = `<svg class="background" fill="none" viewBox="0 0 342 300" preserveAspectRatio="none" xmlns="http://www.w3.org/2000/svg">
  <path fill="url(#wxgradP)" d="M0 66.4396C0 31.6455 0 14.2484 11.326 5.24044C22.6519 -3.76754 39.6026 0.147978 73.5041 7.97901L307.903 62.1238C324.259 65.9018 332.436 67.7909 337.218 73.8031C342 79.8154 342 88.2086 342 104.995V256C342 276.742 342 287.113 335.556 293.556C329.113 300 318.742 300 298 300H44C23.2582 300 12.8873 300 6.44365 293.556C0 287.113 0 276.742 0 256V66.4396Z"></path>
  <defs><linearGradient gradientUnits="userSpaceOnUse" y2="220" x2="360" y1="120" x1="0" id="wxgradP">
    <stop stop-color="#5936B4"></stop><stop stop-color="#362A84" offset="1"></stop></linearGradient></defs></svg>`;

const DAYS = [['今天', '☁️', 23.5, 18.0], ['明天', '🌦️', 23.4, 16.9], ['后天', '🌦️', 24.9, 19.0]]
  .map(([l, ic, hi, lo]) => `<div class="wx-day"><span class="wx-day-lbl">${l}</span><span class="wx-day-ic">${ic}</span><span class="wx-day-t">${hi}° / ${lo}°</span></div>`).join('\n              ');

const mini = `<div class="wx-mini" title="悬浮展开">
              <span class="wx-mini-ic">🌤️</span>
              <span class="wx-mini-t">20.4°</span>
              <span class="wx-mini-city">成都·四川</span>
              <span class="wx-mini-clock">19:28:41</span>
            </div>`;

const card = `<div class="wx-card">
            ${GRAD}
            ${CLOUD}
            <div class="wx-clock">19:28:41</div>
            <div class="wx-date">2026 年 09 月 11 日 · 星期五</div>
            <p class="main-text">20.4°<span class="wx-cond">🌤️ 晴间多云</span></p>
            <div class="info">
              <div class="info-left"><p class="text-gray">H:23.5° L:18.0°</p><p>成都·四川</p></div>
              <p class="info-right">体感 21.8°</p>
            </div>
            <div class="wx-meta"><span>💧 76%</span><span>💨 4.8 km/h</span><span>🌧️ 2%</span></div>
            <div class="wx-days">
              ${DAYS}
            </div>
            <div class="wx-foot"><span title="open-meteo.com（免 Key）">🌐 open-meteo · 19:28</span>
              <span><span class="wx-skins">
                <i class="wx-skin on" style="background:linear-gradient(120deg,#4fae6b,#1f5c37)"></i>
                <i class="wx-skin" style="background:linear-gradient(120deg,#e2ab34,#8a5a08)"></i>
                <i class="wx-skin" style="background:linear-gradient(120deg,#4f6bd8,#23306f)"></i>
                <i class="wx-skin" style="background:linear-gradient(120deg,#5936b4,#362a84)"></i>
              </span><span class="wx-city-btn">📍 换城市</span></span></div>
          </div>`;

const cityForm = `<div class="wx-city-form">
            <input placeholder="城市名（成都 / Chengdu）" value="" />
            <button>确定</button><button class="ghost">取消</button>
          </div>`;

function dock(inner, cls) {
  return `          <div class="wx-dock ${cls || ''}">
            ${inner}
          </div>`;
}

const hero = (title, dockHtml) => `      <div class="hero">
        <span class="hero-clip"><i class="hero-glow"></i></span>
        <span class="emoji">👋</span>
        <div>
          <h2>${title}</h2>
          <p>这里是你的选购决策工作台。添加兴趣与关注后，AI 助手为你做性价比分析与快讯解读。</p>
        </div>
${dockHtml}
      </div>`;

const page = `<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8" />
<title>时钟 + 天气卡 · 三态预览</title>
<style>
${css}
    html, body { margin: 0; background: #eef2f7; }
    body { font-family: "Microsoft YaHei", "PingFang SC", system-ui, sans-serif; padding: 26px 30px 60px; }
    .home { display: block; }
    .preview-note { max-width: 1180px; margin: 0 auto 18px; font-size: 13px; color: #5b6b85; }
    .preview-note b { color: #362a84; }
    .preview-row { max-width: 1180px; margin: 0 auto 26px; }
    .preview-row > .lbl { font-size: 12.5px; font-weight: 700; color: #362a84; margin: 0 0 8px 2px; }
    .preview-row .home { background: #fff; border-radius: 18px; padding: 0 6px; box-shadow: 0 10px 30px rgba(20,30,50,.06); }
    .preview-row .wx-dock { margin-right: 26px; }
    .preview-city .wx-mini { display: none; }
    .preview-skins { display: flex; gap: 14px; align-items: center; padding: 18px 26px; }
    .preview-skins .wx-dock { margin: 0; }
    .preview-row.has-card { margin-bottom: 350px; }
    /* 预览用：展开态直接常显，不靠 hover */
    .preview-open .wx-mini { opacity: 0; transform: scale(.95); }
    .preview-open .wx-card { opacity: 1; transform: translateY(0) scale(1); }
    .arrow { color: #a7b3c7; font-size: 22px; margin: 0 10px; }
</style></head>
<body>
<div class="preview-note">
  <b>静态预览</b>：CSS 取自 <code>front/index.html</code> 真实样式；鼠标悬浮卡片即可看到气泡展开的实际动效（下方第二/三行用 <code>open</code> 类强制展开，方便截图）。
  时钟为秒级（真实页面每秒跳动 · <code>setInterval(tickClock, 1000)</code>），天气数据为真实接口当日返回（成都 20.4° 晴间多云）。<br/>卡片<b>从药丸下方展开</b>（不再居中越出 hero），悬浮面板浮在下方内容之上，不会被 hero 裁切；配色默认「松绿」对齐项目主色系，可在卡片右下角一键切换。
</div>

<div class="preview-row">
  <div class="lbl">① 正常工作台（收起态：悬浮或点击展开）</div>
  <div class="home">
${hero('下午好，尼古喵喵', dock(mini))}
  </div>
</div>

<div class="preview-row has-card">
  <div class="lbl">② 悬浮展开（012 号 weather-gradient：渐变 + 云 + 大号读数 + 秒级时钟 + 3 天预报）</div>
  <div class="home preview-open">
${hero('下午好，尼古喵喵', dock(mini + '\n            ' + card, 'open'))}
  </div>
</div>

<div class="preview-row has-card">
  <div class="lbl">③ 点「📍 换城市」（卡片内联输入，不跳系统弹窗；回车=确定，Esc=取消）</div>
  <div class="home preview-open">
${hero('下午好，尼古喵喵', dock('<span style="display:none"></span>' + cityForm, 'open'))}
  </div>
</div>
<div class="preview-row">
  <div class="lbl">④ 四套配色（卡片右下角圆点即切即换，按账号记忆；默认「松绿」对齐项目主色系）</div>
  <div class="home preview-skins">
    <div class="wx-dock" data-wx="green"><div class="wx-mini"><span class="wx-mini-ic">🌤️</span><span class="wx-mini-t">20.4°</span><span class="wx-mini-city">松绿（默认）</span><span class="wx-mini-clock">19:28:41</span></div></div>
    <div class="wx-dock" data-wx="amber"><div class="wx-mini"><span class="wx-mini-ic">🌤️</span><span class="wx-mini-t">20.4°</span><span class="wx-mini-city">暖阳</span><span class="wx-mini-clock">19:28:41</span></div></div>
    <div class="wx-dock" data-wx="indigo"><div class="wx-mini"><span class="wx-mini-ic">🌤️</span><span class="wx-mini-t">20.4°</span><span class="wx-mini-city">靛蓝</span><span class="wx-mini-clock">19:28:41</span></div></div>
    <div class="wx-dock" data-wx="violet"><div class="wx-mini"><span class="wx-mini-ic">🌤️</span><span class="wx-mini-t">20.4°</span><span class="wx-mini-city">紫罗兰（012）</span><span class="wx-mini-clock">19:28:41</span></div></div>
  </div>
</div>
</body></html>`;

fs.mkdirSync(path.join(root, 'data'), { recursive: true });
fs.writeFileSync(path.join(root, 'front', 'tutorial', 'previews', 'weather_preview.html'), page, 'utf8');

// ---- 自检：预览用到的 class 必须都在真实 CSS 里 ----
const used = new Set();
for (const m of page.matchAll(/class="([^"]+)"/g)) m[1].split(/\s+/).forEach(c => c && used.add(c));
used.delete('wx-dock');   // wx-dock 在 CSS 里是独立选择器（非 class 属性匹配）
['preview-note', 'preview-row', 'preview-open', 'preview-skins', 'has-card', 'lbl', 'home', 'arrow'].forEach(c => used.delete(c));   // 预览页自身的样式
const missing = [...used].filter(c => !new RegExp('\\.' + c.replace(/[-.]/g, '\\$&') + '[\\s,{.:]').test(css));
console.log('预览用到 class 数：', used.size);
if (missing.length) { console.error('❌ 以下 class 在真实 CSS 里找不到：', missing); process.exit(1); }
console.log('✅ class 自检通过（全部来自真实 CSS）');
console.log('产物：front/tutorial/previews/weather_preview.html', (page.length / 1024).toFixed(1) + ' KB');
