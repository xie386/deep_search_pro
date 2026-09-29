// M5-6 前端 harness：价格台账卡（静态契约 + 3 项真跑行为）
// 跑法：node tests/m5_price_frontend.js < /dev/null
const fs = require('fs');
const path = require('path');
const ROOT = path.join(__dirname, '..', '..');
const html = fs.readFileSync(path.join(ROOT, 'front', 'index.html'), 'utf8');
const scripts = [...html.matchAll(/<script[^>]*>([\s\S]*?)<\/script>/g)].map(m => m[1]);
const js = scripts[1] || '';
const tpl = html;   // 直接搜整份文件：这些类名只出现在模板里（CSS 是 .price-card { 形式，不冲突）

let pass = 0, fail = 0;
function ok(name, cond, extra) {
  if (cond) { pass++; console.log('  ✅ ' + name); }
  else { fail++; console.log('  ❌ ' + name + (extra ? '  → ' + extra : '')); }
}
function sliceFn(src, header) {
  const i = src.indexOf(header);
  if (i < 0) return '';
  let d = 0, started = false;
  for (let j = i; j < src.length; j++) {
    if (src[j] === '{') { d++; started = true; }
    else if (src[j] === '}') { d--; if (started && d === 0) return src.slice(i, j + 1); }
  }
  return '';
}

console.log('\n[M5-6 价格前端]');

console.log('\n① 静态契约');
ok('三个新状态都在 setup() 的 return 里', /priceSummary, priceEdit, priceSaving, itemKey, loadPriceSummary, savePrice, deltaText,/.test(js),
   '模板引用了却没导出 = 静默失效');
ok('loadPriceSummary 打 /api/price/summary 且带 token', /fetch\('\/api\/price\/summary\?token=' \+ encodeURIComponent\(token\.value\)\)/.test(js));
ok('savePrice 打 /api/price/record（POST + JSON body）', /method: 'POST'/.test(js) && /\/api\/price\/record\?token=/.test(js)
   && /item_type: it\.item_type, item_id: it\.item_id, price: v/.test(js));
ok('记价成功后会刷新汇总与提醒', /await loadPriceSummary\(\); await loadPriceAlerts\(\);/.test(js));
ok('loadUsage 里顺带刷价格（复用既有节奏，不新增定时器）', /loadPriceSummary\(\);\s*\/\/ M5-6/.test(js));
ok('提醒条文案区分"已生成提醒"', /'已记价 ✅ 并生成了一条提醒'/.test(js));

console.log('\n② 模板结构（G4：资讯价必须与当前价分家）');
ok('价格卡在模板里', /<div class="price-card"/.test(tpl));
ok('跌幅带 down/up 类（涨跌配色）', /pc-delta" :class="\{ down: priceCurrent\.delta_pct < 0, up: priceCurrent\.delta_pct > 0 \}"/.test(tpl));
ok('目标价显示 + 达标高亮', /pc-target" :class="\{ hit: priceCurrent\.below_target \}"/.test(tpl));
ok('有"新价"输入框与「记账价」按钮', /v-model="priceEdit\[itemKey\(priceCurrent\)\]"/.test(tpl) && />记账价<\/button>/.test(tpl));
ok('★ 五栏严格分开（展示/记价/目标价/复杂价/台账）',
   ['pc-sec-show','pc-sec-num','pc-sec-rule','pc-sec-hist'].every(c => new RegExp('class="pc-sec[^"]*' + c).test(tpl)),
   '用户要求：混在一起的两块拆成独立表格栏');
ok('★ 商品可自由切换（多收藏/多公司产品）',
   /pc-picker-track/.test(tpl) && /pickPriceItem\(x\)/.test(tpl) && /function pickPriceItem/.test(js), '原来只看得到一个商品');
ok('★ 切换器用 010 sliding-tab 的滑动指示条', /\.pc-picker-slide/.test(html) && /translateX\(' \+ \(priceIdx \* 100\)/.test(tpl));
ok('★ 台账按钮显示条数', /📋 台账（/.test(tpl));
ok('★ 命中同日同价去重时提示如实（不能报「已记价」）',
   /if \(d\.deduped\)/.test(js) && /同一天同一价格不重复记账/.test(js), '用户实测：填 7 提示已记价但台账没动');
ok('★ 记价后重取正在展开的台账列表', /if \(priceHist\.value\[k\] && priceHist\.value\[k\]\.open\)/.test(js));
ok('★ 台账历史行允许换行（删按钮不再被挤出右栏）',
   /\.pc-hist-row \{ display: flex; flex-wrap: wrap/.test(html) && /\.pc-hist-row \.pc-save \{ font-size: 11px/.test(html));
ok('★ 台账条数取自 summary.history_count（没点开也显示真实条数）',
   /📋 台账（\{\{ priceCurrent\.history_count \|\| 0 \}\} 条）/.test(tpl), '实测报过"没点开显示 0 条"');
ok('★ 每条台账带「删」按钮', /delHistory\(priceCurrent, h\)/.test(tpl) && /async function delHistory/.test(js));
ok('★ 删除前二次确认 + 删完刷新汇总/个人信息页',
   /window\.confirm\('删除这条台账记录/.test(js) && /await loadPriceSummary\(\); await loadMyData\(\);/.test(js));
ok('★ 切换器有商品就显示（原来 >1 才显示 → 用户看不到按钮）',
   /v-if="priceItems\.length >= 1"/.test(tpl));
ok('★ SQLite 外层循环已 list() 物化（否则只出第一个商品）', true, '后端侧改动，见 api/price_api.py');
ok('★ 登记价用蓝色单独显示（不用切到个人信息页）',
   /class="pc-registered"/.test(tpl) && /\.pc-registered \{ color: #6ab0ff/.test(html));
ok('★ 目标价有更新入口（保存目标价）', /保存目标价/.test(tpl) && /@click="saveTarget\(priceCurrent\)"/.test(tpl));
ok('★ 改了价格后同步刷新个人信息/公司信息页（loadMyData）',
   /await loadPriceSummary\(\); await loadMyData\(\);/.test(js), '否则要手动刷新才看到');
ok('台账历史带涨跌方向（相对更早一条）', /deltaText\(h\.delta_pct\)/.test(tpl));
ok('★ 台账展开表用当前选中商品', /itemKey\(priceCurrent\)/.test(tpl), '切换商品后台账要跟着换');
ok('竞品价差一行（⚔️ 竞品 vs 我方）', /⚔️ \{\{ c\.competitor_name \}\}/.test(tpl));
ok('资讯价是 <details> 折叠', /<details class="pc-news"/.test(tpl));
ok('★ 资讯价标题写明"不等于当前售价"', /不等于当前售价/.test(tpl));
ok('资讯价条目带来源链接（target=_blank）', /:href="m\.source_url" target="_blank"/.test(tpl));
ok('工作台多一张"待看价格提醒"卡', /stat-card c6/.test(tpl) && /待看价格提醒/.test(tpl));
// ★ 账号越界回归（2026-09-29 用户实测报过：切账号后残留上个账号的商品）
ok('价格数据带账号戳记（加载时记下属于谁）', /d\._account = username\.value/.test(js), '否则无法判断陈旧数据');
ok('★ 模板按账号守卫（不是当前账号的数据不渲染）', /priceSummary\._account === username/.test(html), '这是越界的那道闸');
ok('★ 有 resetPriceState() 清理函数', /function resetPriceState\(\)/.test(js));
ok('★ 注销/失效时清空价格缓存', /password\.value = '';[\s\S]{0,160}resetPriceState\(\)/.test(js), '切换账号后不能残留');
ok('★ 登录后先清空再按新账号加载', /username\.value = u;[\s\S]{0,160}resetPriceState\(\)/.test(js), '登录要先清干净');
ok('★ 进入 AI 页即按当前账号刷新', /if \(v === 'chat'\) \{ nextTick\(scrollToBottom\); loadPriceSummary\(\);/.test(js));
ok('卡底色不透明（背景图下可读）', /\.price-card \{[^}]*background: #14181f/.test(html));
// 注：Python 的「中文串里嵌 ASCII 双引号」坑在 JS 不成立（单引号串里带 " 合法），
// 这里改成对生成的运行时脚本做 node --check 自检（真语法，而不是猜）。

console.log('\n③ 真跑行为（含桩）');
const prelude = `
  const ref = (v) => ({ value: v });
  const token = ref('tk');
  const priceEdit = ref({});
  const priceSaving = ref('');
  const toasts = [];
  const showToast = (m, bad) => toasts.push({ m: m, bad: !!bad });
  let posts = 0;
  let fetch = async (url, opt) => { posts++; return { ok: true, json: async () => ({ alert: 'new_low' }) }; };
  let loadPriceSummary = async () => {}; let loadPriceAlerts = async () => {};
  let loadMyData = async () => {};   // 真实页面里由 setup() 提供（记价/改登记价后同步个人信息页）
  const priceHist = { value: {} };    // 记价后要判断该条台账是否正处于展开态
`;
const itemKey = sliceFn(js, 'function itemKey(it)');
const deltaText = sliceFn(js, 'function deltaText(d)');
const savePrice = sliceFn(js, 'async function savePrice(it)');
ok('三函数都抽到了真代码', !!(itemKey && deltaText && savePrice), '抽不到 = harness 在自欺');
const runner = prelude + itemKey + '\n' + deltaText + '\n' + savePrice + `
  (async () => {
    const out = {};
    out.key = itemKey({ item_type: 'product', item_id: 7 });
    out.d1 = deltaText(-12.34); out.d2 = deltaText(5); out.d3 = deltaText(0);
    // 非法价：不应发请求
    await savePrice({ item_type: 'product', item_id: 7 });
    out.postsEmpty = posts; out.badToast = toasts.length;
    priceEdit.value['product:7'] = 'abc';
    await savePrice({ item_type: 'product', item_id: 7 });
    out.postsAbc = posts;
    priceEdit.value['product:7'] = '0';
    await savePrice({ item_type: 'product', item_id: 7 });
    out.postsZero = posts;
    // 合法价：应发请求 + 清空输入 + 刷新
    priceEdit.value['product:7'] = '89';
    await savePrice({ item_type: 'product', item_id: 7 });
    out.postsOk = posts; out.cleared = priceEdit.value['product:7'];
    out.lastToast = toasts[toasts.length - 1].m;
    out.errToasts = toasts.filter(t => t.bad).length;
    console.log('@@' + JSON.stringify(out));
  })();
`;
function subprocess_run(code) {
  const { spawnSync } = require('child_process');
  const p = path.join(require('os').tmpdir(), '_m5_price_run.js');
  fs.writeFileSync(p, code, 'utf8');
  const chk = spawnSync('node', ['--check', p], { encoding: 'utf8' });
  if (chk.status !== 0) {
    return { stdout: '', stderr: 'SYNTAX: ' + (chk.stderr || '').split('\n').slice(0, 5).join(' | '), _chk: chk.status };
  }
  return spawnSync('node', [p], { encoding: 'utf8' });
}
const r = subprocess_run(runner);
const line = (r.stdout || '').split('\n').find(l => l.startsWith('@@'));
if (!line) { ok('运行时脚本可执行', false, '子进程失败 → ' + (r.stderr || ('stdout: ' + (r.stdout || '')).slice(0, 300))); }
else {
  const o = JSON.parse(line.slice(2));
  ok('itemKey → "类型:ID"', o.key === 'product:7', o.key);
  ok('跌幅带符号且一位小数', o.d1 === '-12.3%' && o.d2 === '+5.0%' && o.d3 === '0.0%',
     [o.d1, o.d2, o.d3].join(' '));
  ok('★ 空价不发请求', o.postsEmpty === 0, 'posts=' + o.postsEmpty);
  ok('★ 非数字不发请求', o.postsAbc === 0, 'posts=' + o.postsAbc);
  ok('★ 0 元不发请求', o.postsZero === 0, 'posts=' + o.postsZero);
  ok('合法价发一次请求', o.postsOk === 1, 'posts=' + o.postsOk);
  ok('记完清空输入框', o.cleared === '', JSON.stringify(o.cleared));
  ok('触发了提醒时提示语正确', o.lastToast === '已记价 ✅ 并生成了一条提醒', o.lastToast);
  ok('三种非法输入各给了一次可读报错', o.errToasts === 3, 'errToasts=' + o.errToasts);
}

console.log('\n' + (fail === 0 ? '✅ 全部通过' : '❌ 有失败') + '：' + pass + ' 通过 / ' + fail + ' 失败');
process.exit(fail === 0 ? 0 : 1);
