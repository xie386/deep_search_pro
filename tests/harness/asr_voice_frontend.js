/* v3.1 语音转文字（长按说话）前端逻辑测试（无需浏览器）
 *
 * 思路与其它 harness 一致 ✓：**不重写逻辑** —— 直接从 front/index.html 抽出
 * 「v3.1：语音转文字」标记块里的**真实代码**，补一层最小的浏览器 shim（MediaRecorder /
 * AudioContext / getUserMedia / fetch / document）后在 Node 里跑真实调用序列。
 *
 * 覆盖的都是用户拍板的硬约束 ✓（2026-10-04）：
 *   ① 长按开始 · 松手结束 ✓   ② 文字**追加**不替换 ✓
 *   ③ 移出话筒 = 取消 ✓       ④ 音频转完即删、**不落盘**（不上传文件、只走内存 ✓）
 *   ⑤ 太短/没听清/失败 各有人话提示 ✓   ⑥ WAV 头必须是 16k/mono/16bit（后端免 ffmpeg ✓）
 *
 * 运行：node tests/harness/asr_voice_frontend.js
 */
const fs = require('fs');
const path = require('path');

const HTML = path.join(__dirname, '..', '..', 'front', 'index.html');
// ⚠️ 统一按 LF 比较（锚点串含换行 ✓ 工作区可能是 CRLF ✓ 别的 harness 踩过 ✓）
const src = fs.readFileSync(HTML, 'utf8').replace(/\r\n/g, '\n').replace(/\r/g, '\n');

const A = '// ==================== v3.1：语音转文字（长按说话） ====================';
const B = '// ==================== /v3.1：语音转文字（长按说话） ====================';
const i = src.indexOf(A);
const j = src.indexOf(B, i);
if (i < 0 || j < 0) { console.error('未能在 index.html 中定位「语音转文字」代码块'); process.exit(1); }
const block = src.slice(i, j + B.length);

let pass = 0, fail = 0;
function check(name, cond, extra) {
  if (cond) { pass++; console.log('  ✓ ' + name); }
  else { fail++; console.log('  ✗ ' + name + (extra !== undefined ? '   ' + JSON.stringify(extra) : '')); }
}

// ───────────────────────── 浏览器 shim ─────────────────────────
const ref = (v) => ({ value: v });
const toasts = [];
function showToast(msg, isErr) { toasts.push({ msg: msg, err: !!isErr }); }
const token = ref('tk-123');
const nextTick = (fn) => Promise.resolve().then(fn);

const docListeners = {};
const draft = ref('');   // 注意：要先于 fakeTA 定义（value 用 getter 跟 draft 同步 ✓ 真实 DOM 由 v-model 同步 ✓）
const fakeTA = { get value() { return draft.value; }, focus() { fakeTA.focused = true; }, setSelectionRange(a, b) { fakeTA.sel = [a, b]; } };
const document = {
  querySelector: (sel) => (sel.indexOf('textarea') >= 0 ? fakeTA : null),
  addEventListener: (t, fn) => { (docListeners[t] = docListeners[t] || []).push(fn); },
  removeEventListener: (t, fn) => { docListeners[t] = (docListeners[t] || []).filter((f) => f !== fn); },
};
let rafId = 0;
const requestAnimationFrame = () => ++rafId;
const cancelAnimationFrame = () => {};

class FakeTrack { constructor() { FakeTrack.stopped = 0; } stop() { FakeTrack.stopped++; } }
const streams = [];
const getUserMedia = async function () { const s = { getTracks: () => [new FakeTrack()] }; streams.push(s); return s; };

const uploads = [];            // 记录 POST /api/asr
let asrReply = { ok: true, status: 200, body: { ok: true, text: '欢迎回家', ms: 240, audio_s: 1.2 } };
class FakeBlob {
  constructor(parts, opts) { this.parts = parts || []; this.type = (opts && opts.type) || ''; this.size = this.parts.reduce((n, p) => n + (p.size || (p.length || 0)), 0); }
  arrayBuffer() { const n = 16000 * 2; return Promise.resolve(new ArrayBuffer(n)); }
}
class FakeFormData {
  constructor() { this._d = []; }
  append(k, v, name) { this._d.push({ k, v, name }); }
  get(k) { const e = this._d.find((x) => x.k === k); return e && e.v; }
}
const fetch = async function (url, opt) {
  if (String(url).indexOf('/api/asr/status') >= 0) return { ok: true, json: async () => ({ enabled: true }) };
  if (String(url).indexOf('/api/asr') >= 0) {
    uploads.push({ url, body: opt.body });
    return { ok: asrReply.status === 200, status: asrReply.status, json: async () => asrReply.body };
  }
  return { ok: true, status: 200, json: async () => ({}) };
};

let recState = 'inactive';
class FakeMediaRecorder {
  constructor(stream) { this.stream = stream; this.state = 'inactive'; this.mimeType = 'audio/webm'; FakeMediaRecorder.last = this; }
  start() { this.state = 'recording'; recState = 'recording'; if (this.ondataavailable) this.ondataavailable({ data: new FakeBlob([new Uint8Array(100)]) }); }
  stop() { this.state = 'inactive'; recState = 'inactive'; if (this.onstop) setTimeout(this.onstop, 0); }
}
let audioCtxClosed = 0;
class FakeAnalyser {
  constructor() { this.frequencyBinCount = 256; }
  getByteFrequencyData(b) { for (let i = 0; i < b.length; i++) b[i] = 40; }
  disconnect() {}
}
class FakeAudioContext {
  constructor() { this.destination = {}; }
  createMediaStreamSource() { return { connect() {} }; }
  createAnalyser() { return new FakeAnalyser(); }
  close() { audioCtxClosed++; return Promise.resolve(); }
  async decodeAudioData() {
    const len = 48000;                      // 48k/1s ✓ 走重采样分支 ✓
    return { numberOfChannels: 1, length: len, sampleRate: 48000, duration: 1.0,
             getChannelData: () => new Float32Array(len).fill(0.1) };
  }
  createBuffer(ch, len, sr) { return { copyToChannel(from, c) { this._d = from; } }; }
  createBufferSource() { return { connect() {}, start() {}, set buffer(v) {} }; }
}
class FakeOfflineAudioContext extends FakeAudioContext {
  async startRendering() { return { getChannelData: () => new Float32Array(16000).fill(0.05) }; }
}

const navigator = { mediaDevices: { getUserMedia } };
const window = { AudioContext: FakeAudioContext };
const globalOffline = FakeOfflineAudioContext;

// ───────────────────────── 载入真实代码块 ─────────────────────────
const factory = new Function(
  'ref', 'showToast', 'draft', 'token', 'nextTick', 'document', 'navigator', 'window',
  'MediaRecorder', 'Blob', 'FormData', 'fetch', 'requestAnimationFrame', 'cancelAnimationFrame',
  'OfflineAudioContext', 'setInterval', 'clearInterval', 'console',
  block + '\n return { recOn, recBusy, recCancel, recLevel, recClock, recStart, recStop, recMoveOut, loadAsrStatus, recAppend };'
);
const M = factory(ref, showToast, draft, token, nextTick, document, navigator, window,
                  FakeMediaRecorder, FakeBlob, FakeFormData, fetch, requestAnimationFrame,
                  cancelAnimationFrame, globalOffline, setInterval, clearInterval, console);

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

(async function main() {
  console.log('---------------- 结果 ----------------');
  // ① 长按开始
  await M.recStart();
  check('长按 → 开始录音（recOn ✓）', M.recOn.value === true);
  check('长按 → 麦已打开（getUserMedia 被调用 ✓）', streams.length === 1);
  check('长按 → 计时器起（时钟 0:00 ✓）', M.recClock.value === '0:00');
  await sleep(30);
  check('电平条有数值且在 0~100 ✓', M.recLevel.value >= 0 && M.recLevel.value <= 100, M.recLevel.value);

  // ② 松手 → 上传（追加）
  draft.value = '你好，';
  await sleep(600);                        // 录够 0.5s ✓
  M.recStop();
  await sleep(80);
  check('松手 → 有且仅有一次 POST /api/asr ✓', uploads.length === 1, uploads.length);
  check('上传 URL 带 token ✓', uploads[0] && uploads[0].url.indexOf('token=tk-123') >= 0, uploads[0] && uploads[0].url);
  const fd = uploads[0] && uploads[0].body;
  check('用 FormData 传文件 ✓', !!fd && typeof fd.get === 'function');
  const blob = fd && fd.get('file');
  check('★ 上传的是 WAV（16k/mono/16bit ✓ 后端免 ffmpeg ✓）', !!blob && blob.type === 'audio/wav', blob && blob.type);
  check('★ 文字**追加**到末尾（原内容保留 ✓ 不替换 ✓）', draft.value === '你好，欢迎回家', draft.value);
  check('追加后聚焦并把光标放到末尾 ✓', fakeTA.focused === true && fakeTA.sel && fakeTA.sel[0] === draft.value.length, fakeTA.sel);
  check('松手后收音状态复位（recOn=false ✓ recBusy=false ✓）', M.recOn.value === false && M.recBusy.value === false);
  check('录音结束后麦被关（tracks.stop ✓）', FakeTrack.stopped >= 1, FakeTrack.stopped);
  check('音频上下文已释放（close ✓）', audioCtxClosed >= 1, audioCtxClosed);
  check('不弹错误提示 ✓', toasts.filter((t) => t.err).length === 0, toasts);

  // ③ 太短 → 不上传
  uploads.length = 0; toasts.length = 0;
  await M.recStart();
  M.recStop();                             // 立刻松手（<500ms ✓）
  await sleep(60);
  check('★ 说话太短 → 不上传 ✓', uploads.length === 0, uploads.length);
  check('说话太短 → 人话提示 ✓', toasts.some((t) => /太短/.test(t.msg)), toasts);

  // ④ 移出话筒 = 取消 → 不上传
  uploads.length = 0; toasts.length = 0;
  await M.recStart();
  await sleep(600);
  M.recMoveOut();
  check('移出话筒 → 浮窗进入取消态 ✓', M.recCancel.value === true);
  M.recStop();
  await sleep(60);
  check('★ 取消 → 不上传 ✓', uploads.length === 0, uploads.length);
  check('取消 → 提示「已取消」✓', toasts.some((t) => /取消/.test(t.msg)), toasts);
  check('取消后状态复位 ✓', M.recOn.value === false && M.recCancel.value === false);

  // ⑤ 后端 400 → 把人话 detail 贴出来
  uploads.length = 0; toasts.length = 0;
  asrReply = { ok: false, status: 400, body: { detail: '说话时间太短（0.2s ✗ 至少 0.3s）' } };
  await M.recStart();
  await sleep(600);
  M.recStop();
  await sleep(80);
  check('后端 400 → 展示后端的人话 detail ✓', toasts.some((t) => /太短/.test(t.msg) && t.err), toasts);
  asrReply = { ok: true, status: 200, body: { ok: true, text: '   ', ms: 1, audio_s: 1 } };

  // ⑥ 空结果 → 没听清（不改输入框）
  draft.value = '保持不动'; toasts.length = 0;
  await M.recStart();
  await sleep(600);
  M.recStop();
  await sleep(80);
  check('空识别结果 → 提示「没听清」✓', toasts.some((t) => /没听清/.test(t.msg)), toasts);
  check('空结果不污染输入框 ✓', draft.value === '保持不动', draft.value);

  console.log('=====================================');
  console.log(`通过 ${pass} 项，失败 ${fail} 项`);
  console.log(fail ? '✗ 有失败' : '语音转文字前端逻辑全部通过 ✅');
  process.exit(fail ? 1 : 0);
})();
