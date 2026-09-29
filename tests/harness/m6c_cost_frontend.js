/**
 * M6c-7 前端 harness：模型配置三处单价 / 工作台成本卡 / 本轮 token 小字。
 *
 * 判据来源：M6c 方案 §5.4 + §六 M6c-7（≥10 项）+ G5/G8。
 * 写法与 m4/m5 同源：**从 front/index.html 抽真实代码**再断言，不复制粘贴一份假实现。
 * 特别地：`numOrNull` / `costTokens` / `costAmountText` 是**真跑**的（抽出来执行），
 *   因为"留空 ≠ 0"这条语义一旦写错，用户看到的就是错账。
 */
const fs = require('fs');
const path = require('path');
const ROOT = path.join(__dirname, '..', '..');
const html = fs.readFileSync(path.join(ROOT, 'front', 'index.html'), 'utf8');
const scripts = [...html.matchAll(/<script[^>]*>([\s\S]*?)<\/script>/g)].map(m => m[1]);
const js = scripts[1] || '';
const tpl = html;

let ok = 0, bad = 0;
function t(name, cond, note) {
  if (cond) { ok++; console.log('  ✅ ' + name); }
  else { bad++; console.log('  ❌ ' + name + (note ? '  → ' + note : '')); }
}
function grab(name) {
  const i = js.indexOf('function ' + name + '(');
  if (i < 0) return null;
  let d = 0, started = false;
  for (let k = i; k < js.length; k++) {
    if (js[k] === '{') { d++; started = true; }
    else if (js[k] === '}') { d--; if (started && d === 0) return js.slice(i, k + 1); }
  }
  return null;
}

console.log('\n[M6c-7 成本前端]');

// ---------- ① 模型配置：三处可选填单价 ----------
t('模型配置有"三处单价"输入（缓存命中/未命中/输出）',
  /price_in_cached/.test(tpl) && /price_in_uncached/.test(tpl) && /price_out/.test(tpl));
t('单价单位写死在界面上（元/百万 tokens）', /元\/百万 tokens/.test(tpl));
t('★ 明确提示"留空 = 只统计 token，不折算金额"', /留空\s*=\s*只统计 token/.test(tpl));
t('★ 明确提示"填 0 = 明确免费"', /填 0\s*=\s*明确免费/.test(tpl));
t('保存/新增都把三处单价带进 payload', (js.match(/price_in_cached: numOrNull\(/g) || []).length >= 2);
t('★ 空串转 null（后端是 float|None，收不了 \'\' ✗）', /function numOrNull\(/.test(js) && /return null;/.test(grab('numOrNull') || ''));

// ---------- ② 工作台成本卡 ----------
t('工作台有成本卡（本月 tokens + 本月估算金额）',
  /stat-card c7/.test(tpl) && /stat-card c8/.test(tpl) && /本月 tokens/.test(tpl) && /本月估算金额/.test(tpl));
t('成本明细面板含调用数/命中-未命中/无用量调用/外部检索',
  /本月模型调用/.test(tpl) && /缓存命中\/未命中/.test(tpl) && /无用量调用/.test(tpl) && /外部检索/.test(tpl));
t('面板有"按当前单价重算"按钮', /recalcCost\(\)/.test(tpl) && /按当前单价重算/.test(tpl));
t('★ 估算文案只写死一处（COST_NOTE 常量），界面引用它',
  (js.match(/金额为估算值/g) || []).length === 1 && /COST_NOTE/.test(tpl));
t('loadCost 打 /api/cost/summary 且 days=30 带 token', /\/api\/cost\/summary\?days=30/.test(js));
t('recalcCost 打 /api/cost/recalc（POST + JSON body）', /\/api\/cost\/recalc/.test(js) && /method: 'POST'/.test(js));
t('loadUsage 里顺带刷成本（复用既有节奏，不新增定时器）', /loadCost\(\);\s*\n\s*usageInfo\.value/.test(js));

// ---------- ③ 本轮小字 ----------
t('会话用量条里有"本轮模型 N 次 + 进/出 + 命中 + 金额"小字',
  /u-tokens/.test(tpl) && /模型 \{\{ usageInfo\.model_calls \}\} 次/.test(tpl));
t('无用量调用要显式说明（不能装作没发生）', /次无用量数据/.test(tpl));

// ---------- ④ 真跑：折算语义（"留空 ≠ 0"就在这里） ----------
let fns = null;
try {
  const src = [grab('numOrNull'), grab('costTokens'), grab('costAmountText')].join('\n');
  fns = new Function(src + '\nreturn { numOrNull, costTokens, costAmountText };')();
} catch (e) { fns = null; }
t('三个折算函数都抽到了真代码', !!fns);
if (fns) {
  t('★ numOrNull：空串 → null（留空 = 不折算）', fns.numOrNull('') === null);
  t('★ numOrNull：0 → 0（明确免费，不能变 null）', fns.numOrNull('0') === 0);
  t('numOrNull：正常数字与非法输入', fns.numOrNull('2.5') === 2.5 && fns.numOrNull('abc') === null);
  t('★ 金额为 null → 显示「未配置单价」（不是 ¥0.00）',
    fns.costAmountText({ total: { cost: null } }) === '未配置单价');
  t('★ 金额为 0 → 显示 ¥0.0000（免费与未配置必须能区分）',
    fns.costAmountText({ total: { cost: 0 } }) === '¥0.0000');
  t('金额正常显示四位小数', fns.costAmountText({ total: { cost: 0.0123 } }) === '¥0.0123');
  t('tokens 合计带千分位', fns.costTokens({ tokens: { input: 1000, output: 200 } }) === '1,200');
  t('没有数据时给「—」而不是 0', fns.costAmountText(null) === '—' && fns.costTokens(null) === '—');
}

// ---------- ⑤ 模板健康：属性值里不许再嵌一个 v- 属性（2026-09-30 真踩过 ✗）----------
// 症状：往 `<div class="pc-picker" v-if="a >= 1">` 里插 v-show 时，插入点落进了表达式 →
//       变成 `v-if="a  v-show="x"">= 1"`；而 `node --check` **只查 JS、查不出模板结构** ✗。
const collide = [...html.matchAll(/v-[\w-]+="[^"]*v-[\w-]+="/g)].map(m => m[0].slice(0, 70));
t('★ 模板属性里没有嵌套的 v- 属性', collide.length === 0, collide.join(' | '));
t('★ v-if / v-show / v-for 语法没被属性插入写坏',
  !/v-(if|show|for)="[^"]*\s+v-(if|show|for)=/.test(html));
t('★ 价格卡收起态 = 只有「价格台账」小标签（v-show 显式控制，不靠 CSS 层叠）',
  /class="pc-tag"/.test(tpl) && /@mouseenter="priceCardOpen = true"/.test(tpl)
  && (tpl.match(/v-show="priceCardOpen/g) || []).length >= 4);

console.log('\n' + (bad ? '❌ 有失败：' : '✅ 全部通过：') + ok + ' 通过 / ' + bad + ' 失败');
process.exit(bad ? 1 : 0);
