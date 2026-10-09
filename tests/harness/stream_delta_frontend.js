// 聊天流式增量渲染（v3.1）前端 harness：从 front/index.html **抽取真实代码**在 Node 里跑。
// 覆盖：增量开气泡/追加 · 思考不进气泡 · 收尾覆盖（防重复）· 异常兜底 · 滚动节流
// 跑法：node tests/harness/stream_delta_frontend.js
'use strict';
const fs = require('fs');
const path = require('path');

const HTML = fs.readFileSync(path.join(__dirname, '..', '..', 'front', 'index.html'), 'utf8');
const START = HTML.indexOf('// ★ v3.1 流式输出：增量渲染');
// ⚠️ 结束锚点用**语音转文字的块标记**而不是 `async function send` ✗ ——
//   v3.1 语音块就插在「流式块」与 `send()` 之间 ✓ 用 send 当锚点会把语音块也切进来 ✗
//   （语音块里的 `ref()`/`navigator` 在本套件的 shim 里没有 → 直接 ReferenceError ✓ 已踩 ✓）
const END = HTML.indexOf('// ==================== v3.1：语音转文字（长按说话） ====================');
if (END < 0) { console.error('未能在 index.html 中定位流式块的结束锚点（语音块标记）'); process.exit(1); }
if (START < 0 || END < 0 || END < START) { console.error('✗ 抽取锚点失效（改过锚点注释？）'); process.exit(1); }
const CODE = HTML.slice(START, END);

let pass = 0, fail = 0;
function ok(name, cond, extra) {
  if (cond) { pass++; console.log('  ✓ ' + name); }
  else { fail++; console.log('  ✗ ' + name + (extra !== undefined ? '  → ' + JSON.stringify(extra) : '')); }
}
function mkRef(v) { return { value: v }; }

// ---- shim：setup() 里这几个是闭包引用（messages / thinkingText / scroll / scrollToBottom）----
function makeEnv() {
  const env = { messages: mkRef([]), thinkingText: mkRef(''), scroll: mkRef(null), scrollCalls: 0,
                scrollToBottom: () => { env.scrollCalls++; } };
  // 把抽取到的真实函数装进这个作用域
  const fn = new Function('messages', 'thinkingText', 'scroll', 'scrollToBottom', 'Date',
                          CODE + '\nreturn { applyStreamDelta, finishStreaming, finalizeStreamingOnError, schedScroll };');
  Object.assign(env, fn(env.messages, env.thinkingText, env.scroll,
                        env.scrollToBottom, Object.assign({}, Date, { now: () => env._now || 0 })));
  return env;
}

console.log('【1】增量渲染：正文开气泡 → 后续追加');
{
  const env = makeEnv();
  env.applyStreamDelta({ kind: 'answer', text: '你好' });
  ok('首块建了一条 streaming 气泡', env.messages.value.length === 1 && env.messages.value[0].streaming === true);
  env.applyStreamDelta({ kind: 'answer', text: '，世界' });
  ok('后续块追加到同一气泡（不新增）', env.messages.value.length === 1);
  ok('文本逐字累加', env.messages.value[0].text === '你好，世界', env.messages.value[0].text);
}
console.log('【2】思考增量：进右栏折叠区，不进气泡');
{
  const env = makeEnv();
  env.applyStreamDelta({ kind: 'reasoning', text: '先想想' });
  ok('思考没建气泡', env.messages.value.length === 0);
  ok('思考进了 thinkingText', env.thinkingText.value === '先想想');
  env.applyStreamDelta({ kind: 'answer', text: '答案' });
  ok('思考与正文互不污染', env.messages.value[0].text === '答案' && env.thinkingText.value === '先想想');
  env.applyStreamDelta({ kind: 'delta-only-trap', text: '' });
  ok('空文本不建气泡', env.messages.value.length === 1);
}
console.log('【3】收尾：以后端完整文本覆盖（防重复渲染）');
{
  const env = makeEnv();
  env.applyStreamDelta({ kind: 'answer', text: '半截' });
  const done = env.finishStreaming('完整答案', { msg_id: 42, interrupted: false });
  ok('finishStreaming 命中流式气泡', done === true);
  ok('文本被覆盖而非追加', env.messages.value[0].text === '完整答案', env.messages.value[0].text);
  ok('streaming 标记清掉 + 带上 msg_id', env.messages.value[0].streaming === false && env.messages.value[0].msg_id === 42);
  ok('没有流式气泡时返回 false（调用方走老路径 ✓）', env.finishStreaming('x', {}) === false);
}
console.log('【4】异常/中断兜底：气泡不留「正在回答…」');
{
  const env = makeEnv();
  env.applyStreamDelta({ kind: 'answer', text: '半截' });
  env.finalizeStreamingOnError();
  ok('已有正文的保留原文（中断不丢字 ✓）', env.messages.value[0].text === '半截' && env.messages.value[0].streaming === false);
  const env2 = makeEnv();
  env2.messages.value.push({ role: 'agent', text: '', title: '正在回答…', streaming: true });
  env2.finalizeStreamingOnError();
  ok('空内容给可读兜底而不是空白卡', /没有收到内容/.test(env2.messages.value[0].text), env2.messages.value[0].text);
}
console.log('【5】滚动节流：密增量不狂滚，用户在读历史时不拽回');
{
  const env = makeEnv();
  env._now = 1000;
  env.scroll.value = { scrollHeight: 3000, scrollTop: 3000, clientHeight: 600 };   // 贴底
  env.schedScroll();
  ok('贴底时滚一次', env.scrollCalls === 1);
  env._now = 1050; env.schedScroll();
  ok('120ms 内再触发不滚（节流生效）', env.scrollCalls === 1);
  env._now = 1200; env.schedScroll();
  ok('过窗口后再滚', env.scrollCalls === 2);
  env.scroll.value = { scrollHeight: 3000, scrollTop: 100, clientHeight: 600 };    // 用户在读历史
  env._now = 1400; env.schedScroll();
  ok('离底部 >160px 时不拽回', env.scrollCalls === 2);
}

console.log('\n结果：' + pass + '/' + (pass + fail) + (fail ? '  ✗ 有失败' : '  ✓ 全过'));
process.exit(fail ? 1 : 0);
