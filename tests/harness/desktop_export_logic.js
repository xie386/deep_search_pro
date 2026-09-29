/* 桌面版「导出/下载」统一入口 saveFromUrl 的逻辑测试（无需浏览器、无需后端）
 *
 * 背景（用户实测报障）：桌面版点周报的「MD↓」没任何反应——WebView2 不支持浏览器式
 * 下载，`<a :href>` 点了请求发出去、文件却静默丢弃。修法是把全站取文件的出口统一
 * 收口到 setup() 里的 `saveFromUrl(url, filename)`（桌面→原生另存为 / 网页→浏览器下载）。
 *
 * 这个文件**从 front/index.html 抽取真实的 saveFromUrl/nameFromUrl 源码**，补一层
 * 最小桩（fetch / desktopApi / document / showToast）后在 Node 里跑真实分支，重点验证：
 *   ① 网页版分支仍是「浏览器下载」（不能因为改桌面版把网页版弄坏）
 *   ② 桌面版分支走原生保存、字节经 base64 往返**完全一致**（含 >64KB 的分块拼接）
 *   ③ 取消 / 保存失败 / 取文件失败 三种异常路径的返回值与提示
 *
 * 运行：node tests/desktop_export_logic.js
 */
const fs = require('fs');
const path = require('path');

const HTML = path.join(__dirname, '..', '..', 'front', 'index.html');
const src = fs.readFileSync(HTML, 'utf8');

// ── 从 index.html 抽取真实源码（不重写逻辑，只搬运）──────────────────────────
const START = '// ★ 统一的「把某个 URL 保存成文件」入口（全站导出/下载都走它）';
const END = "    const token = ref(storeGet('zx_token'));";
const i = src.indexOf(START);
const j = src.indexOf(END, i);
if (i < 0 || j < 0) {
  console.error('未能在 index.html 中定位 saveFromUrl / nameFromUrl 代码块（标记被改动？）');
  process.exit(1);
}
// 去掉缩进后当普通函数体使用
const block = src.slice(i, j).replace(/^ {4}/gm, '');

// ── 最小桩 ────────────────────────────────────────────────────────────────
const toasts = [];
let IS_DESKTOP = false;                       // 每个用例前重设
let API = null;                               // 模拟 window.pywebview.api
let FETCHES = [];                             // 记录 fetch 调用
let fetchImpl = null;

const document = {
  createElement(tag) { return { tag, style: {}, click() { this._clicked = true; }, remove() { this._removed = true; } }; },
  body: { appendChild() {}, removeChild() {} },
};
const window = {};                            // 下面会挂 pywebview
function showToast(msg, isErr) { toasts.push({ msg, isErr: !!isErr }); }
// saveFromUrl 里调的是 setup() 上文的 desktopApi()，抽取块之外，所以按真实语义注入桩
function desktopApi() { return (IS_DESKTOP && window.pywebview && window.pywebview.api) ? window.pywebview.api : null; }
async function fetchStub(url) { FETCHES.push(url); return fetchImpl(url); }

// 把抽取到的代码包成工厂：显式注入桩，避免与真实作用域里的重名混淆
const factory = new Function(
  'IS_DESKTOP', 'window', 'document', 'showToast', 'fetch', 'desktopApi',
  block + '\n  return { saveFromUrl, nameFromUrl };'
);

function build(desktop) {
  IS_DESKTOP = desktop;
  window.pywebview = desktop ? { api: API } : undefined;
  fetchImpl = null; FETCHES = []; toasts.length = 0;
  return factory(desktop, window, document, showToast, fetchStub, desktopApi);
}

// ── 断言工具 ──────────────────────────────────────────────────────────────
let pass = 0, fail = 0;
function check(name, cond, extra) {
  if (cond) { pass++; console.log('  ✅ ' + name); }
  else { fail++; console.log('  ❌ ' + name + (extra !== undefined ? ' | ' + extra : '')); }
}
function bytesOf(n) { const b = new Uint8Array(n); for (let k = 0; k < n; k++) b[k] = k % 256; return b; }
async function flush() { for (let k = 0; k < 6; k++) await Promise.resolve(); }

(async function main() {
  console.log('桌面版导出/下载统一入口测试（从 index.html 抽取真实源码）\n');

  // ───────────────────────────── ① 网页版分支 ─────────────────────────────
  console.log('[1] 网页版：必须仍是浏览器下载（不能因桌面改造把网页版弄坏）');
  {
    const fns = build(false);
    let made = null;
    document.createElement = (tag) => { made = { tag, style: {}, click() { this._clicked = true; }, remove() { this._removed = true; } }; return made; };
    const r = await fns.saveFromUrl('/api/download?token=t&name=a.md', '报告.md');
    check('返回 ok 且标记 native=false（走的不是原生保存）', r.ok === true && r.native === false, JSON.stringify(r));
    check('创建的是 <a> 元素', made && made.tag === 'a', made && made.tag);
    check('href 指向下载地址', made && made.href === '/api/download?token=t&name=a.md', made && made.href);
    check('带上 download 属性（用文件名保存，不整页跳转）', made && made.download === '报告.md', made && made.download);
    check('真的触发点击', made && made._clicked === true);
    check('没有调用原生保存接口', !API, 'pywebview.api 不该被用到');
  }
  {
    // 无文件名时不该硬塞 download（避免把 URL 当文件名）
    const fns = build(false);
    let made = null;
    document.createElement = (tag) => { made = { tag, style: {}, click() {}, remove() {} }; return made; };
    const r = await fns.saveFromUrl('/x.md', '');
    check('网页版：文件名为空时仍能下载且不设 download', r.ok === true && (made.download === undefined || made.download === ''), made.download);
  }

  // ───────────────────────────── ② 桌面版正常路径 ─────────────────────────────
  console.log('\n[2] 桌面版：走原生「另存为」，字节必须一字不差');
  {
    const payload = bytesOf(2048);
    const saved = [];
    API = { save_binary_b64: async (name, b64) => { saved.push({ name, b64 }); return { ok: true, path: 'C:\\下载\\' + name }; } };
    const fns = build(true);
    fetchImpl = async () => ({ ok: true, status: 200, arrayBuffer: async () => payload.buffer.slice(0) });
    const r = await fns.saveFromUrl('/api/download?token=t&name=周报.md', '周报.md');
    check('返回 ok + native=true + path', r.ok === true && r.native === true && !!r.path, JSON.stringify(r));
    check('文件名原样传给外壳', saved[0] && saved[0].name === '周报.md', saved[0] && saved[0].name);
    const back = Buffer.from(saved[0].b64, 'base64');
    check('★ base64 解回来与原字节完全一致（2048 字节）',
      back.length === payload.length && Buffer.compare(back, Buffer.from(payload)) === 0,
      'len ' + back.length + ' vs ' + payload.length);
    check('fetch 用的就是传入的下载 URL', FETCHES[0] === '/api/download?token=t&name=周报.md', FETCHES[0]);
  }
  {
    // ★ 大文件分块：saveFromUrl 里按 0x8000 分块拼 String.fromCharCode，
    //   这块写错（如一次性 apply）会栈溢出或丢字节——用 200KB 压一遍
    const payload = bytesOf(200000);
    const saved = [];
    API = { save_binary_b64: async (name, b64) => { saved.push(b64); return { ok: true, path: name }; } };
    const fns = build(true);
    fetchImpl = async () => ({ ok: true, status: 200, arrayBuffer: async () => payload.buffer.slice(0) });
    const r = await fns.saveFromUrl('/big.pdf', 'big.pdf');
    const back = Buffer.from(saved[0] || '', 'base64');
    check('★ 200KB 分块拼接无栈溢出且字节一致',
      r.ok === true && back.length === payload.length && Buffer.compare(back, Buffer.from(payload)) === 0,
      'len ' + back.length + ' vs ' + payload.length);
  }

  // ───────────────────────────── ③ 异常路径 ─────────────────────────────
  console.log('\n[3] 异常路径：取消 / 保存失败 / 取文件失败');
  {
    const payload = bytesOf(16);
    let called = 0;
    API = { save_binary_b64: async () => { called++; return { ok: false, cancelled: true }; } };
    const fns = build(true);
    fetchImpl = async () => ({ ok: true, status: 200, arrayBuffer: async () => payload.buffer.slice(0) });
    const r = await fns.saveFromUrl('/a.md', 'a.md');
    check('用户取消：返回 cancelled=true 且不报错', r.ok === false && r.cancelled === true, JSON.stringify(r));
    check('用户取消：不弹错误提示（安静退出）', toasts.length === 0, JSON.stringify(toasts));
    check('确实调用过保存接口', called === 1);
  }
  {
    const payload = bytesOf(16);
    API = { save_binary_b64: async () => ({ ok: false, error: '磁盘满了' }) };
    const fns = build(true);
    fetchImpl = async () => ({ ok: true, status: 200, arrayBuffer: async () => payload.buffer.slice(0) });
    const r = await fns.saveFromUrl('/a.md', 'a.md');
    check('保存失败：返回 error 原文', r.ok === false && r.error === '磁盘满了', JSON.stringify(r));
    check('保存失败：有错误提示（用户能看见）', toasts.length === 1 && toasts[0].isErr === true, JSON.stringify(toasts));
  }
  {
    const payload = bytesOf(16);
    let called = 0;
    API = { save_binary_b64: async () => { called++; return { ok: true }; } };
    const fns = build(true);
    fetchImpl = async () => ({ ok: false, status: 404, arrayBuffer: async () => payload.buffer.slice(0) });
    const r = await fns.saveFromUrl('/gone.md', 'gone.md');
    check('取文件 404：返回失败并带上状态码', r.ok === false && /404/.test(r.error || ''), JSON.stringify(r));
    check('取文件失败时不弹「另存为」对话框（别让用户白选一次路径）', called === 0);
    check('取文件失败有错误提示', toasts.length === 1 && toasts[0].isErr === true, JSON.stringify(toasts));
  }

  // ───────────────────────── ④ 文件名解析 nameFromUrl ─────────────────────────
  console.log('\n[4] 文件名解析：从下载 URL 里取 ?name=（后端给的就是这样）');
  {
    const fns = build(false);
    check('解析中文文件名（URL 编码）',
      fns.nameFromUrl('/api/download?token=abc&name=%E5%BF%AB%E8%AE%AF%E5%91%A8%E6%8A%A5_2026.md', 'x.md')
        === '快讯周报_2026.md',
      fns.nameFromUrl('/api/download?token=abc&name=%E5%BF%AB%E8%AE%AF%E5%91%A8%E6%8A%A5_2026.md', 'x.md'));
    check('没有 name 参数时回退到兜底名',
      fns.nameFromUrl('/api/download?token=abc', '兜底.md') === '兜底.md');
    check('URL 为空也不炸（回退兜底）', fns.nameFromUrl('', '兜底.md') === '兜底.md');
    check('name 在中间位置也能取到',
      fns.nameFromUrl('/api/download?x=1&name=a.pdf&y=2', 'z.md') === 'a.pdf',
      fns.nameFromUrl('/api/download?x=1&name=a.pdf&y=2', 'z.md'));
  }

  await flush();
  console.log('\n' + '-'.repeat(40));
  console.log('桌面版导出逻辑测试：通过 ' + pass + '，失败 ' + fail);
  process.exit(fail === 0 ? 0 : 1);
})();
