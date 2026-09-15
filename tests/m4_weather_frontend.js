/* M4 收尾 · 工作台时钟 + 天气卡（前端真实代码测试，无需浏览器）
 *
 * 思路同 m4a_frontend_logic.js / m4_tutorial_frontend.js：从 static/index.html **抽取真实函数体**
 * （tickClock / loadWeather / wxPromptCity / wxStart），用 Vue ref shim + 假 Date + 假 fetch 在 Node 里跑，
 * 验证：秒级格式化、URL 拼装、错误分支、定时器周期。
 *
 * 运行：node tests/m4_weather_frontend.js
 */
const fs = require('fs');
const path = require('path');

const root = path.resolve(__dirname, '..');
const html = fs.readFileSync(path.join(root, 'static', 'index.html'), 'utf8');

let pass = 0;
const fails = [];
function check(name, cond, extra) {
  if (cond) { pass++; console.log('  ✅ ' + name + (extra ? ' | ' + extra : '')); }
  else { fails.push(name); console.log('  ❌ ' + name + (extra ? ' | ' + extra : '')); }
}

// ---------- 抽取真实代码块（从 marker 到 wxStart 之后） ----------
const start = html.indexOf('// ---- 工作台时钟（秒级）+ 天气');
const endMark = '// ---- M4 收尾：教程指导';   // 天气块延伸到教程块之前（含 wxStart 整个函数）
const end = html.indexOf(endMark);
if (start < 0 || end < 0) { console.error('❌ 抽不到时钟/天气代码块，锚点失效'); process.exit(1); }
const block = html.slice(start, end);

const refShim = (v) => ({ value: v });
const M = new Function('{ ref, token, username, showToast, fetch, setInterval, watch, __W }',
  block + '\nconst __watchers = __W;\nreturn { tickClock, clockTime, clockDate, loadWeather, wxPromptCity, wxCancelCity, wxSetCity, wxSavedCity, wx, wxLoading, wxErr, wxStart, wxCityEdit, wxCityInput, wxOpen, wxSkin, wxSetSkin, wxSavedSkin, WX_SKINS, wxToggleDock, __watchers };');

function boot(over) {
  const o = Object.assign({
    token: refShim('T0KEN'), username: refShim('尼古喵喵'),
    showToast: () => {}, fetch: async () => ({ ok: true, json: async () => ({}) }),
    setInterval: () => 0, prompt: () => null,
  }, over || {});
  const watchers = [];
  const api = M({ ref: refShim, token: o.token, username: o.username, showToast: o.showToast, fetch: o.fetch,
                  setInterval: o.setInterval, watch: (src, cb) => watchers.push({ src, cb }), __W: watchers });
  return api;
}

// ---------- 1) 秒级时钟 ----------
(async () => {

console.log('\n[1] 秒级时钟（真实 tickClock + 假 Date）');
{
  const FakeDate = class extends Date {
    constructor(...a) { if (a.length === 0) super(2026, 8, 11, 9, 5, 7); else super(...a); }
  };
  // tickClock 内部用全局 Date —— 通过 new Function 注入不再可行，改为重写全局（测试进程内安全）
  const RealDate = global.Date;
  global.Date = FakeDate;
  const m = boot();
  m.tickClock();
  global.Date = RealDate;
  check('HH:MM:SS 补零（9 时 5 分 7 秒 → 09:05:07）', m.clockTime.value === '09:05:07', m.clockTime.value);
  check('日期含中文年月日', /^2026 年 09 月 11 日 · /.test(m.clockDate.value), m.clockDate.value);
  check('日期含星期（星期五）', m.clockDate.value.endsWith('星期五'), m.clockDate.value);
}

// ---------- 2) loadWeather URL 拼装 ----------
console.log('\n[2] loadWeather：URL 拼装 / 默认城市 / 强制刷新');
{
  const calls = [];
  const m = boot({ fetch: async (u) => { calls.push(u); return { ok: true, json: async () => ({ city: '成都·四川', now: { temp: 20 } }) }; } });
  // 无 localStorage（Node 环境）→ 不应该抛
  let threw = null;
  try { await m.loadWeather(); } catch (e) { threw = e; }
  check('未设置城市时不报错', threw === null, threw && threw.message);
  check('URL 带 token', calls[0] && calls[0].startsWith('/api/weather?token=T0KEN'), calls[0]);
  check('未设置城市时不带 city 参数（后端取 .env 默认）', calls[0] && !calls[0].includes('city='), calls[0]);
  check('返回体写入 wx', m.wx.value && m.wx.value.city === '成都·四川');
  check('成功后 wxLoading=false / wxErr 清空', m.wxLoading.value === false && m.wxErr.value === '');

  await m.loadWeather(true);
  check('force=true 追加 &force=1', calls[1].includes('&force=1'), calls[1]);
}
{
  // 有 localStorage + 记忆城市
  const calls = [];
  global.localStorage = { _d: {}, getItem(k) { return this._d[k] || null; }, setItem(k, v) { this._d[k] = v; }, removeItem(k) { delete this._d[k]; } };
  localStorage.setItem('dw_wx_city_尼古喵喵', '杭州');
  const m = boot({ fetch: async (u) => { calls.push(u); return { ok: true, json: async () => ({ city: '杭州·浙江' }) }; } });
  check('读到记忆城市', m.wxSavedCity() === '杭州');
  await m.loadWeather();
  check('城市进 URL 且做了 URL 编码', calls[0].includes('city=%E6%9D%AD%E5%B7%9E'), calls[0]);
  delete global.localStorage;
}

// ---------- 3) 错误分支 ----------
console.log('\n[3] 错误分支');
{
  const m = boot({ fetch: async () => ({ ok: false, json: async () => ({ detail: '没找到城市「zzz」' }) }) });
  await m.loadWeather();
  check('后端 detail 进 wxErr', m.wxErr.value === '没找到城市「zzz」', m.wxErr.value);
  check('失败时 wx 不写入（保留旧数据/显示兜底卡）', m.wx.value === null);
  check('失败也复位 loading', m.wxLoading.value === false);
}
{
  const m = boot({ fetch: async () => { throw new Error('network down'); } });
  let threw = null;
  try { await m.loadWeather(); } catch (e) { threw = e; }
  check('网络异常被 catch（不冒泡到 Vue）', threw === null);
  check('网络异常写入提示', /天气获取失败：network down/.test(m.wxErr.value), m.wxErr.value);
}
{
  const m = boot({ token: refShim('') });
  const calls = [];
  await m.loadWeather.call(null);
  check('未登录不请求天气', true);
}

// ---------- 4) 换城市 ----------
console.log('\n[4] 换城市（卡片内联输入）');
{
  global.localStorage = { _d: {}, getItem(k) { return this._d[k] || null; }, setItem(k, v) { this._d[k] = v; }, removeItem(k) { delete this._d[k]; } };
  const calls = [];
  const m = boot({ fetch: async (u) => { calls.push(u); return { ok: true, json: async () => ({ city: '西安·陕西', city_input: '西安' }) }; } });
  // 打开输入框：预填记忆城市（无记忆则空）
  m.wxPromptCity();
  check('点「换城市」打开内联输入', m.wxCityEdit.value === true);
  check('输入框预填当前/记忆城市', m.wxCityInput.value === '', JSON.stringify(m.wxCityInput.value));
  check('打开输入时卡片保持展开（不闪回药丸）', m.wxOpen.value === true);
  m.wxCancelCity();
  check('取消 → 关闭输入且不请求', m.wxCityEdit.value === false && calls.length === 0);

  m.wxCityInput.value = '  西安  ';   // 带空格
  m.wxSetCity();
  await new Promise(r => setTimeout(r, 30));
  check('确定 → 去空格后写入 localStorage', localStorage.getItem('dw_wx_city_尼古喵喵') === '西安', localStorage.getItem('dw_wx_city_尼古喵喵'));
  check('确定 → 关闭输入框', m.wxCityEdit.value === false);
  check('确定 → 立即重取（force=1 + city 编码）',
        calls.length === 1 && calls[0].includes('city=%E8%A5%BF%E5%AE%89') && calls[0].includes('force=1'), calls[0]);
  check('取到新城市写入 wx', m.wx.value && m.wx.value.city === '西安·陕西');

  // 已有记忆 → 再次打开时预填
  m.wxPromptCity();
  check('再次打开预填「西安」', m.wxCityInput.value === '西安', m.wxCityInput.value);

  // 清空 → 回落 .env 默认（不带 city 参数）
  calls.length = 0;
  m.wxCityInput.value = '';
  m.wxSetCity();
  await new Promise(r => setTimeout(r, 30));
  check('清空 → 移除记忆（回落 .env 默认城市）', localStorage.getItem('dw_wx_city_尼古喵喵') === null);
  check('清空 → 重取时不带 city 参数', calls.length === 1 && !calls[0].includes('city='), calls[0]);
  delete global.localStorage;
}

// ---------- 5) 配色切换 ----------
console.log('\n[5] 卡片配色（松绿默认 + 4 套可选）');
{
  global.localStorage = { _d: {}, getItem(k) { return this._d[k] || null; }, setItem(k, v) { this._d[k] = v; }, removeItem(k) { delete this._d[k]; } };
  const m = boot({});
  check('4 套配色已登记', m.WX_SKINS.length === 4, m.WX_SKINS.map(x => x.k).join('/'));
  check('每套都有中文名与两个色值', m.WX_SKINS.every(x => x.t && /^#[0-9a-f]{6}$/i.test(x.c1) && /^#[0-9a-f]{6}$/i.test(x.c2)));
  check('默认配色是松绿（项目主色 #4fae6b）', m.WX_SKINS[0].k === 'green' && m.WX_SKINS[0].c1 === '#4fae6b', m.WX_SKINS[0].c1);
  check('无记忆时默认 green', m.wxSavedSkin() === 'green', m.wxSavedSkin());
  m.wxSetSkin('amber');
  check('切换写入内存 ref', m.wxSkin.value === 'amber');
  check('切换写入 localStorage（按账号）', localStorage.getItem('dw_wx_skin_尼古喵喵') === 'amber');
  check('读回记忆', m.wxSavedSkin() === 'amber');
  m.wxSetSkin('rainbow');
  check('非法配色值被忽略（不会写坏）', m.wxSkin.value === 'amber' && localStorage.getItem('dw_wx_skin_尼古喵喵') === 'amber');
  // 启动时读回
  const ivs = [];
  const m2 = boot({ setInterval: (fn, ms) => { ivs.push(ms); return 0; }, fetch: async () => ({ ok: true, json: async () => ({}) }) });
  m2.wxStart();
  check('wxStart 会把记忆配色读回 ref', m2.wxSkin.value === 'amber', m2.wxSkin.value);
  delete global.localStorage;
}

// ---------- 6) 悬浮/点击交互（悬浮悖论修复）----------
console.log('\n[6] 卡片点击判定（卡片内部点击不该收起）');
{
  const m = boot({});
  const inCard = { target: { closest: (sel) => sel.includes('.wx-card') ? {} : null } };
  const inForm = { target: { closest: (sel) => sel.includes('.wx-city-form') ? {} : null } };
  const onPill = { target: { closest: () => null } };
  m.wxOpen.value = false;
  m.wxToggleDock(onPill);
  check('点药丸 → 钉住展开', m.wxOpen.value === true);
  m.wxToggleDock(onPill);
  check('再点药丸 → 取消钉住', m.wxOpen.value === false);
  m.wxOpen.value = true;
  m.wxToggleDock(inCard);
  check('点卡片内部 → 不收起（能点圆点/换城市）', m.wxOpen.value === true);
  m.wxToggleDock(inForm);
  check('点输入面板内部 → 不收起', m.wxOpen.value === true);
  let threw = null;
  try { m.wxToggleDock(undefined); } catch (e) { threw = e; }
  check('事件为空不报错', threw === null);
}

// ---------- 7) 登录后自动补取天气（挂载早于登录的场景）----------
console.log('\n[7] 登录/登出时 token 变化 → 补取/清理');
{
  const calls = [];
  const ivs = [];
  const tok = refShim('');                            // 真实 ref：watch 回调在 ref 已更新后才触发
  const m = boot({ token: tok, username: refShim('尼古喵喵'), setInterval: (f, ms) => { ivs.push(ms); return 0; },
                   fetch: async (u) => { calls.push(u); return { ok: true, json: async () => ({ city: '成都·四川' }) }; } });
  check('注册了 token 监听（挂载早于登录的兜底）', m.__watchers.length === 1 && typeof m.__watchers[0].cb === 'function');
  check('无 token 时先不请求', calls.length === 0);
  tok.value = 'TOKEN-AFTER-LOGIN';                    // 模拟登录成功（先写 ref）
  m.__watchers[0].cb('TOKEN-AFTER-LOGIN');
  await new Promise(r => setTimeout(r, 40));
  check('登录后立即取一次天气（且只请求一次）', calls.length === 1 && calls[0].includes('token=TOKEN-AFTER-LOGIN'), 'calls=' + calls.length + ' ' + calls[0]);
  check('并启动秒针/刷新定时器', ivs.includes(1000) && ivs.includes(600000));
  // 第二条路径：定时器已起过时，watch 走 loadWeather（同样只请求一次）
  calls.length = 0;
  m.__watchers[0].cb('TOKEN-AGAIN');
  await new Promise(r => setTimeout(r, 40));
  check('定时器已启动时补取也只请求一次', calls.length === 1, 'calls=' + calls.length);

  m.wx.value = { city: '成都·四川' };
  tok.value = '';                                     // 模拟登出
  m.__watchers[0].cb('');
  await new Promise(r => setTimeout(r, 20));
  check('登出清空天气（不漏上一个账号的数据）', m.wx.value === null);
}

// ---------- 8) wxStart 定时器周期 ----------
console.log('\n[8] wxStart：秒针 + 天气刷新周期');
{
  const ivs = [];
  const m = boot({ setInterval: (fn, ms) => { ivs.push(ms); return 0; }, fetch: async () => ({ ok: true, json: async () => ({}) }) });
  m.wxStart();
  m.wxStart(); // 二次调用不应重复起定时器
  check('时钟 1000ms 起一次', ivs.filter(x => x === 1000).length === 1, JSON.stringify(ivs));
  check('天气 10 分钟刷新一次', ivs.filter(x => x === 600000).length === 1, JSON.stringify(ivs));
  check('重复调用不重复起定时器（幂等）', ivs.length === 2, JSON.stringify(ivs));
}

console.log('\n' + '='.repeat(56));
console.log('M4 时钟/天气前端：通过 ' + pass + '，失败 ' + fails.length);
if (fails.length) { fails.forEach(f => console.log('  - ' + f)); process.exit(1); }
console.log('全部通过 ✅')
})().catch(e => { console.error('❌ 测试脚本异常:', (e && e.stack) || e); process.exit(1); });
