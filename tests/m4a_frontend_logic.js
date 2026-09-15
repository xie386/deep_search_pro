/* M4a 前端交互逻辑测试（无需浏览器）
 *
 * 思路：不重写逻辑，直接从 static/index.html 里**抽取真实代码块**（「/」唤出目录、
 * 多选叠加、一次性消费、上限 5 个、Esc 关闭），补一层 Vue 极简 shim 后在 Node 里
 * 跑真实调用序列。浏览器 CDP 不可用时这是唯一能验证交互行为的方式。
 *
 * 运行：node tests/m4a_frontend_logic.js
 */
const fs = require('fs');
const path = require('path');
const assert = require('assert');

const HTML = path.join(__dirname, '..', 'static', 'index.html');
const src = fs.readFileSync(HTML, 'utf8');

const A = '// ==================== M4a：技能与工具 ====================';
const SEND_END = '      } finally { chatAbort = null; stopping.value = false; loading.value = false; await nextTick(); scrollToBottom(); }\n    }\n';
const i = src.indexOf(A);
const j = src.indexOf(SEND_END, i);
if (i < 0 || j < 0) { console.error('未能在 index.html 中定位 M4a 代码块'); process.exit(1); }
let block = src.slice(i, j + SEND_END.length);   // 含 send()，一并验「技能名怎么传给后端」

// 只保留到 skMenuList / skOnInput / skOnKey / skillPick / skillUnpick（send 之前全部）
// 极简 Vue shim
const logs = [];
let _refDepth = 0;
function ref(v) { return { __ref: true, value: v }; }
function computed(fn) { return { __ref: true, get value() { return fn(); } }; }
const nextTick = (cb) => { if (typeof cb === 'function') cb(); return Promise.resolve(); };
const document = { querySelector: () => null, createElement: () => ({ style: {} }), body: { appendChild() {}, removeChild() {} } };
const localStorage = { _d: {}, getItem(k) { return this._d[k] || null; }, setItem(k, v) { this._d[k] = v; } };
function showToast(msg) { logs.push(msg); }
const token = ref('tok-demo');
const username = ref('尼古喵喵');
const draft = ref('');
const confirm = () => true;
const navigator = {};
const window = { isSecureContext: false };
// send() 依赖的上下文
const loading = ref(false);
const messages = ref([]);
const thinkingText = ref('');
const activeThread = ref('t-9');
const goCalls = [];
function go(v) { goCalls.push(v); }
function connectWS() {}
function scrollToBottom() {}
const _initHash = '';             // 页面初始 hash（view / skTab 初始化用，原在块外）
const stopping = ref(false);      // send() 的中断状态（原在块外声明）
let chatAbort = null;             // send() 的 AbortController 持有者
const watchCalls = [];
function watch(src, fn) { watchCalls.push(fn); }   // M4c：watch(skTab, …) 懒加载
const loadSessionsCalls = [];
async function loadSessions() { loadSessionsCalls.push(1); }
const fetchCalls = [];
// M4c：自定义 CLI 配置的服务端返回（前端不内置厂商目录）
const cliPayload = {
  ok: true,
  states: { none: '未接入', installed: '已安装（未认证）', authed: '已接入（已授权）' },
  templates: [{ key: 'lark', icon: '🐦', name: '飞书 Lark CLI', bin: 'lark-cli',
                install_cmd: 'npm install -g @larksuite/cli', auth_cmd: 'lark-cli auth login',
                docs: 'https://github.com/larksuite/cli', readonly: 'auth status', hint: '官方 README' }],
  items: [{
    id: 7, name: '飞书', bin: 'lark-cli',
    install_cmd: 'npm install -g @larksuite/cli', auth_cmd: 'lark-cli auth login',
    docs: 'https://github.com/larksuite/cli',
    readonly: 'auth status\ncalendar +agenda',
    rules: [['auth', 'status'], ['calendar', '+agenda']],
    agent_commands: ['lark-cli auth status', 'lark-cli calendar +agenda'],
    detected: { found: false, path: '' },
    state: 'none', state_label: '未接入', note: '', updated_at: '', live: false,
  }],
  summary: { total: 1, authed: 0, installed: 0, detected_local: 0, agent_live: 0, with_rules: 1 },
};
const cliStatusPayload = {
  ok: true,
  item: { id: 7, name: '飞书', bin: 'lark-cli', state: 'authed', state_label: '已接入（已授权）',
          live: true, note: 'v1.0.94', rules: [['auth', 'status']],
          agent_commands: ['lark-cli auth status'] },
};
async function fetch(url, opts) {
  fetchCalls.push(url);
  const u = String(url);
  if (u.indexOf('/api/cli/list') >= 0) return { ok: true, json: async () => cliPayload };
  if (u.indexOf('/api/cli/status') >= 0) return { ok: true, json: async () => cliStatusPayload };
  if (u.indexOf('/api/cli/save') >= 0) return { ok: true, json: async () => ({ ok: true, item: cliPayload.items[0] }) };
  if (u.indexOf('/api/cli/delete') >= 0) return { ok: true, json: async () => ({ ok: true, deleted: true }) };
  if (u.indexOf('/api/cli/ability_draft') >= 0) return { ok: true, json: async () => ({ ok: true, content: '关键词：日程/会议/日历。我今天的会议→calendar +agenda' }) };
  return { ok: true, json: async () => ({ answer: '好的', thread_id: 't-9', last_msg_id: 9 }) };
}

// 执行真实抽取代码
const runner = new Function(
  'ref', 'computed', 'nextTick', 'document', 'localStorage', 'showToast',
  'token', 'username', 'draft', 'confirm', 'navigator', 'window', 'fetch',
  'loading', 'messages', 'thinkingText', 'activeThread', 'go', 'connectWS', 'scrollToBottom', 'loadSessions',
  'stopping', 'chatAbort', 'watch', '_initHash',
  block + '\nreturn { skills, skPicked, skMenuOpen, skMenuIdx, skFilter, skMenuList, skOnInput, skOnKey, skillPick, skillUnpick, SK_MAX_PICK, cliItems, cliTemplates, cliStates, cliSummary, cliDoneCount, loadCliCatalog, cliNew, cliFill, cliEdit, cliSave, cliSetState, cliSaveNote, cliDelete, cliForm, cliEditing, cliErr, cliDrafting, cliAbilityDraft, SK_MAX, send };'
);
const M = runner(ref, computed, nextTick, document, localStorage, showToast,
                  token, username, draft, confirm, navigator, window, fetch,
                  loading, messages, thinkingText, activeThread, go, connectWS, scrollToBottom, loadSessions,
                  stopping, chatAbort, watch, _initHash);

let pass = 0, failed = [];
function check(label, cond, extra) {
  if (cond) { pass++; console.log('  [OK]   ' + label); }
  else { failed.push(label); console.log('  [FAIL] ' + label + (extra !== undefined ? '  |  ' + JSON.stringify(extra) : '')); }
}
// 模拟键盘事件
function key(k, opts) { const e = Object.assign({ key: k, preventDefault() {}, ctrlKey: false, metaKey: false }, opts || {}); M.skOnKey(e); return e; }
// 模拟输入框输入
function type(s) { draft.value = s; M.skOnInput(); }

M.skills.value = [
  { name: '竞品对比表', chars: 210, desc: '对比两个以上竞品' },
  { name: '来源可追溯', chars: 180, desc: '标注出处与日期' },
  { name: '数据可视化', chars: 150, desc: '把数据做成图表' },
];

const cases = [
  ['敲「/」唤出技能目录', () => { type('/'); check('菜单打开', M.skMenuOpen.value === true); check('列出全部 3 个技能', M.skMenuList.value.length === 3, M.skMenuList.value.map(s => s.name)); }],
  ['继续输入筛选词「数」', () => { type('/数'); check('筛选后剩 1 个', M.skMenuList.value.length === 1 && M.skMenuList.value[0].name === '数据可视化', M.skMenuList.value.map(s => s.name)); check('filter="数"', M.skFilter.value === '数'); }],
  ['↑↓ 改变高亮', () => { type('/'); M.skMenuIdx.value = 0; key('ArrowDown'); check('下移到 1', M.skMenuIdx.value === 1); key('ArrowUp'); check('上移回 0', M.skMenuIdx.value === 0); key('ArrowUp'); check('顶部回绕到末项', M.skMenuIdx.value === 2, M.skMenuIdx.value); }],
  ['Enter 选中并吃掉「/筛选词」', () => { draft.value = '/来源 今天的新闻'; M.skOnInput(); type('/来源'); key('Enter'); check('已加入本轮', M.skPicked.value.length === 1 && M.skPicked.value[0] === '来源可追溯', M.skPicked.value); check('菜单关闭', M.skMenuOpen.value === false); check('输入框里的 /来源 被清掉', draft.value === '', JSON.stringify(draft.value)); }],
  ['多选叠加（再选 2 个）', () => { type('/'); M.skMenuIdx.value = 0; key('Enter'); type('/'); M.skMenuIdx.value = 2; key('Enter'); check('共 3 个（多选叠加）', M.skPicked.value.length === 3, M.skPicked.value); }],
  ['已选项不重复加入', () => { type('/竞品'); key('Enter'); check('重复选择不叠加', M.skPicked.value.filter(n => n === '竞品对比表').length === 1, M.skPicked.value); }],
  ['上限 5 个（后端 SKILL_MAX_PICK 同值）', () => {
    M.skills.value = [1, 2, 3, 4, 5, 6, 7].map(n => ({ name: '技能' + n, chars: 10, desc: '' }));
    M.skPicked.value = [];
    for (let n = 1; n <= 7; n++) { type('/技能' + n); key('Enter'); }
    check('SK_MAX_PICK = 5（与后端一致）', M.SK_MAX_PICK === 5, M.SK_MAX_PICK);
    check('最多只收 5 个', M.skPicked.value.length === 5, M.skPicked.value);
    check('第 6 个触发提示', logs.some(t => t.indexOf('最多叠加 5 个') >= 0), logs.slice(-2));
  }],
  ['点 × 单独移除一项', () => { M.skillUnpick('技能3'); check('移除后剩 4 个', M.skPicked.value.length === 4 && M.skPicked.value.indexOf('技能3') < 0, M.skPicked.value); }],
  ['Esc 关闭并清掉未选完的「/」', () => { M.skPicked.value = []; M.skills.value = [{ name: '竞品对比表', chars: 1, desc: '' }]; type('/竞'); key('Escape'); check('菜单关闭', M.skMenuOpen.value === false); check('输入框已清空', draft.value === '', JSON.stringify(draft.value)); }],
  ['句中出现「/」不误唤出', () => { type('价格/性能 哪个更划算'); check('菜单保持关闭', M.skMenuOpen.value === false); }],
  ['输入普通问题不唤出', () => { type('帮我对比一下三款耳机'); check('菜单保持关闭', M.skMenuOpen.value === false); }],
  ['自定义 CLI：列表/状态都来自服务端，前端不预设厂商（M4c）', () => {
    check('前端不再内置厂商目录', M.cliItems.value.length === 0, M.cliItems.value.length);
    check('预填示例也来自服务端', M.cliTemplates.value.length === 0, M.cliTemplates.value.length);
    check('状态枚举由后端下发（3 态）', Object.keys(M.cliStates.value).length === 3, M.cliStates.value);
    check('已登记计数初始为 0', M.cliDoneCount.value === 0, M.cliDoneCount.value);
    check('表单初始为空且隐藏', M.cliForm.value.bin === '' && M.cliEditing.value === false);
  }],
];

console.log('=== M4a 前端交互逻辑（真实代码 + Vue shim） ===');
for (const [name, fn] of cases) { console.log('\n▸ ' + name); fn(); }

(async () => {
  console.log('\n▸ send()：技能名真的传给了后端，且用完即清');
  M.skPicked.value = ['竞品对比表', '来源可追溯'];
  M.skills.value = [{ name: '竞品对比表', chars: 1, desc: '' }];
  draft.value = '帮我对比三款耳机';
  fetchCalls.length = 0;
  await M.send();
  const u1 = fetchCalls[0] || '';
  check('URL 带 skills=（逗号分隔，后端约定）', /[?&]skills=%E7%AB%9E%E5%93%81|skills=/.test(u1), u1);
  check('两个技能名都在 URL 里', decodeURIComponent(u1).indexOf('skills=竞品对比表,来源可追溯') >= 0, decodeURIComponent(u1));
  check('发送后本轮选择被清空（一次性）', M.skPicked.value.length === 0, M.skPicked.value);
  check('用户气泡留下技能痕迹供追溯', messages.value[0] && messages.value[0].skills && messages.value[0].skills.length === 2,
        messages.value[0] && messages.value[0].skills);
  check('回答进气泡且带 msg_id', messages.value[1] && messages.value[1].role === 'agent' && messages.value[1].msg_id === 9,
        messages.value[1]);

  M.skPicked.value = [];
  draft.value = '再问一句';
  fetchCalls.length = 0;
  await M.send();
  check('没选技能时不带 skills 参数', (fetchCalls[0] || '').indexOf('skills=') < 0, fetchCalls[0]);

  console.log('\n▸ loadCliCatalog()：拉服务端配置 + 预填示例 + 只读清单');
  fetchCalls.length = 0;
  await M.loadCliCatalog(true);
  check('请求打到 /api/cli/list（带 token）', (fetchCalls[0] || '').indexOf('/api/cli/list?token=') >= 0, fetchCalls[0]);
  check('配置项来自后端', M.cliItems.value.length === 1 && M.cliItems.value[0].bin === 'lark-cli', M.cliItems.value.map(c => c.bin));
  check('只读清单由后端解析成规则', M.cliItems.value[0].rules.length === 2, M.cliItems.value[0].rules);
  check('AI 可代跑命令由后端给出', M.cliItems.value[0].agent_commands[0] === 'lark-cli auth status',
        M.cliItems.value[0].agent_commands);
  check('预填示例来自后端（不是硬编码厂商）', M.cliTemplates.value.length === 1 && M.cliTemplates.value[0].key === 'lark');
  check('未登记时不放行（live=false）', M.cliItems.value[0].live === false);

  console.log('\n▸ cliFill()：预填示例只填表单、不写库');
  M.cliFill(M.cliTemplates.value[0]);
  check('表单被填好', M.cliForm.value.bin === 'lark-cli' && M.cliForm.value.readonly === 'auth status',
        M.cliForm.value);
  check('表单可见', M.cliEditing.value === true);
  check('没有新增请求（只是预填）', fetchCalls.length === 1, fetchCalls.length);

  console.log('\n▸ cliSave()：POST /api/cli/save 后重载列表');
  fetchCalls.length = 0;
  await M.cliSave();
  check('POST /api/cli/save（带 token）', (fetchCalls[0] || '').indexOf('/api/cli/save?token=') >= 0, fetchCalls[0]);
  check('保存后重载列表', (fetchCalls[1] || '').indexOf('/api/cli/list?token=') >= 0, fetchCalls.slice(0, 3));
  check('保存后表单收起', M.cliEditing.value === false);

  console.log('\n▸ cliSetState()：登记状态 POST 到 /api/cli/status 并写回卡片');
  fetchCalls.length = 0;
  await M.cliSetState(M.cliItems.value[0], 'authed');
  check('POST /api/cli/status（带 token）', (fetchCalls[0] || '').indexOf('/api/cli/status?token=') >= 0, fetchCalls[0]);
  check('返回状态写回卡片', M.cliItems.value[0].state === 'authed' && M.cliItems.value[0].live === true,
        M.cliItems.value[0].state);
  check('已登记计数刷新为 1', M.cliDoneCount.value === 1, M.cliDoneCount.value);
  check('Agent 可代跑计数刷新为 1', M.cliSummary.value.agent_live === 1, M.cliSummary.value);

  console.log('\n▸ cliDelete()：POST /api/cli/delete 后本地移除（AI 权限同步回收）');
  fetchCalls.length = 0;
  await M.cliDelete(M.cliItems.value[0]);
  check('POST /api/cli/delete', (fetchCalls[0] || '').indexOf('/api/cli/delete?token=') >= 0, fetchCalls[0]);
  check('本地列表已移除', M.cliItems.value.length === 0, M.cliItems.value.length);
  check('汇总同步归零', M.cliSummary.value.agent_live === 0 && M.cliSummary.value.total === 0, M.cliSummary.value);

  console.log('\n▸ cliAbilityDraft()（M5）：先填清单才能生成，生成结果只进表单、不自动保存');
  M.cliEditing.value = true;
  M.cliForm.value = { id: null, name: '', bin: '', install_cmd: '', auth_cmd: '', docs: '',
                      readonly: '', note: '', abilities: '' };
  fetchCalls.length = 0;
  await M.cliAbilityDraft();
  check('清单为空 → 不发请求并提示先填清单',
        fetchCalls.length === 0 && String(M.cliErr.value || '').indexOf('只读命令清单') >= 0, M.cliErr.value);
  M.cliForm.value.name = '飞书';
  M.cliForm.value.bin = 'lark-cli';
  M.cliForm.value.readonly = 'auth status\ncalendar +agenda';
  await M.cliAbilityDraft();
  check('POST /api/cli/ability_draft（带 token）',
        (fetchCalls[0] || '').indexOf('/api/cli/ability_draft?token=') >= 0, fetchCalls[0]);
  check('生成结果写进表单的能力描述框', String(M.cliForm.value.abilities).indexOf('关键词：') === 0,
        M.cliForm.value.abilities);
  check('只生成草稿、不自动保存（须人工确认才落库）', fetchCalls.length === 1, fetchCalls);
  check('生成中状态已复位', M.cliDrafting.value === false);
  check('错误提示已清空', !M.cliErr.value, M.cliErr.value);

  console.log('\n---------------- 结果 ----------------');
  console.log('通过 ' + pass + ' 项，失败 ' + failed.length + ' 项');
  if (failed.length) { failed.forEach(f => console.log('  ✗ ' + f)); process.exit(1); }
  console.log('M4a 前端交互逻辑全部通过 ✅');
})();
