// 前端不变量：**模板里用到的名字必须能在 setup() 的 return 名单里找到**。
//
// 起因（2026-10-06，用户报障"点开「工具描述」整页变白"，并说这是"老问题"）：
//   这版前端是普通 <script> + setup()，模板只能看见 return 出去的名字。
//   新增一个模板里要用的函数（capIssuesOf）却忘了 return → 一渲染就抛错、页面只剩背景。
//   既有的 m1c harness 只做字符串断言，抓不到这一类。
//
// 跑法：node tests/harness/front_template_bindings.js
const fs = require('fs');
const path = require('path');
const assert = require('assert');

const ROOT = path.resolve(__dirname, '..', '..');
const html = fs.readFileSync(path.join(ROOT, 'front', 'index.html'), 'utf8');

// ---- ① setup 的 return 名单：花括号配对扫描**所有** return 块取并集
//      （非贪婪正则会截在列表中间而大量误报 —— 已踩过）
const returned = new Set();
let blocks = 0;
for (let i = html.indexOf('return {'); i >= 0; i = html.indexOf('return {', i + 1)) {
  let depth = 0, j = i + 'return '.length;
  for (; j < html.length; j++) {
    if (html[j] === '{') depth++;
    else if (html[j] === '}' && --depth === 0) break;
  }
  (html.slice(i, j + 1).match(/[A-Za-z_$][\w$]*/g) || []).forEach(n => returned.add(n));
  blocks++;
}
assert.ok(blocks > 0, '找不到任何 setup 的 return 块');

// ---- ② 模板区
const appStart = html.indexOf('id="app"');
const appEnd = html.indexOf('<script', appStart);
const tpl = html.slice(appStart, appEnd > 0 ? appEnd : html.length);

// v-for 局部别名 / v-slot 解构，不算未返回
const locals = new Set();
for (const m of tpl.matchAll(/v-for="\(?\s*([\w$]+)\s*(?:,\s*([\w$]+)\s*)?\)?\s+in\s/g)) {
  locals.add(m[1]); if (m[2]) locals.add(m[2]);
}
for (const m of tpl.matchAll(/v-slot[^>]*\{([^}]*)\}/g)) {
  m[1].split(',').forEach(x => locals.add(x.trim()));
}

const BUILTIN = new Set(['true', 'false', 'null', 'undefined', 'Math', 'JSON', 'String', 'Number',
                         'Array', 'Object', 'Date', 'Boolean', 'parseInt', 'parseFloat', '$event',
                         'in', 'of', 'typeof', 'new', 'await', 'If']);

const used = new Map();
function note(name, snippet) {
  if (!name || BUILTIN.has(name) || locals.has(name) || returned.has(name)) return;
  if (!/^[A-Za-z_$][\w$]*$/.test(name)) return;
  if (!used.has(name)) used.set(name, snippet.replace(/\s+/g, ' ').slice(0, 70));
}
function stripStrings(expr) {
  return expr.replace(/'[^']*'/g, "''").replace(/"[^"]*"/g, '""');
}
function firstId(expr) {
  const ids = stripStrings(expr).match(/[A-Za-z_$][\w$]*/g) || [];
  return ids.length ? ids[0] : '';
}

// ③ {{ ... }}：第一个标识符
for (const m of tpl.matchAll(/\{\{([^}]*)\}\}/g)) note(firstId(m[1]), m[0]);

// ④ Vue 绑定与指令里的函数调用（只认「名字(」，且跳过 a.b() 这类方法调用）
//     ★ 必须覆盖 **所有** v-* 与 :/@ 开头的属性 —— 只认 :/@ 会漏掉 v-for="… foo(x)"，
//       而那正是这次白屏的形态（capIssuesOf 写在 v-for 里，自证时没被抓到）。
for (const m of tpl.matchAll(/(?:^|\s)([:@]|v-)[\w:.-]*="([^"]*)"/g)) {
  const expr = stripStrings(m[2]);
  for (const c of expr.matchAll(/([A-Za-z_$][\w$]*)\s*\(/g)) {
    if (c.index > 0 && expr[c.index - 1] === '.') continue;    // 方法调用（a.b()）不算
    note(c[1], m[0] + m[1] + m[2]);
  }
}

// ⑤ 条件类指令里的标识符（跳过对象/数组字面量与字符串）
for (const m of tpl.matchAll(/v-(?:if|show|model|html|text)="([^"]*)"/g)) {
  const expr = stripStrings(m[1]).trim();
  if (expr.startsWith('{') || expr.startsWith('[')) continue;
  for (const id of expr.match(/[A-Za-z_$][\w$]*/g) || []) {
    const idx = expr.indexOf(id);
    if (idx > 0 && expr[idx - 1] === '.') continue;
    note(id, m[0]);
  }
}

if (used.size) {
  console.log('  ✗ 模板里用到了 setup 没 return 的名字（点开就会白屏）：');
  for (const [name, snip] of used) console.log('      · ' + name + '   （出现在：' + snip + '）');
  process.exit(1);
}
console.log('  ✅ 模板绑定的名字都能在 setup() 的 return 里找到（扫描 ' + blocks + ' 个 return 块）');
