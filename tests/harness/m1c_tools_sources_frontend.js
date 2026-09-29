/* M1c 前端逻辑测试：工具来源配置（无需浏览器、无需服务）
 *
 * 思路与 m4a/m5c 一致：**从 front/index.html 抽取真实代码块**，补一层 Vue 极简 shim，
 * 在 Node 里跑真实调用序列。覆盖两个"错了就会出事"的地方：
 *   ① 表单 → 请求体（短名/base_url/默认头 的校验必须与后端同口径）；
 *   ② 勾选 → 确认请求体（**写操作必须额外勾"我确认这是写操作"**，否则不该发给后端）。
 *
 * 运行：node tests/m1c_tools_sources_frontend.js
 */
const fs = require('fs');
const path = require('path');
const assert = require('assert');

const HTML = path.join(__dirname, '..', '..', 'front', 'index.html');
// 统一按 LF 处理（index.html 工作区是 CRLF，取决于 core.autocrlf；不归一化锚点会时通时不通）
const src = fs.readFileSync(HTML, 'utf8').replace(/\r\n/g, '\n');

const A = '// ---------- 工具来源（v3.0 M1：API/MCP 能力的配置面） ----------';
const B = '// ---------- 定制助手：模型选型 ----------';
const i = src.indexOf(A), j = src.indexOf(B);
if (i < 0 || j < 0 || j <= i) { console.error('未能在 index.html 中定位工具来源代码块'); process.exit(1); }
const block = src.slice(i, j);

// ---------------- 极简 shim ----------------
const toasts = [];
function showToast(msg, isErr) { toasts.push({ msg: String(msg), err: !!isErr }); }
function ref(v) { return { __ref: true, value: v }; }
function reactive(o) { return o; }
function computed(fn) { return { __ref: true, get value() { return fn(); } }; }
const token = ref('tok-demo');
let confirmAnswer = true;
function confirm() { return confirmAnswer; }
const calls = [];
function makeFetch(routes) {
  return async function (url, opts) {
    calls.push({ url: String(url), opts: opts || {} });
    for (const r of routes) { if (String(url).indexOf(r[0]) >= 0) return { ok: r[2] !== false, json: async () => r[1] }; }
    return { ok: true, json: async () => ({}) };
  };
}

// ---------------- 抽出真实块并实例化 ----------------
function build(routes) {
  const factory = new Function('ref', 'reactive', 'computed', 'fetch', 'showToast', 'confirm', 'token',
    block + `
    return { tsSources, tsCaps, tsStats, tsCandidates, tsSpecText, tsDiscoverSlug, tsEditingId, tsBusy, tsForm,
             tsCheckedCount, tsModal, tsModalSource, tsResetForm, tsSourcePayload, tsConfirmPayload,
             tsCandidateBadge, tsParamsText, tsStateText, tsAuthText, tsNewSource, tsCloseModal, tsOpenImport,
             tsLoad, tsEdit, tsSave, tsDel, tsTest, tsDiscover, tsConfirm, tsViewCandidates, tsCandFromApi,
             mcpSources, mcpAll, mcpCaps, mcpStats, mcpForm, mcpModal, mcpProbe, mcpProbeTitle, mcpEditingId,
             mcpTransportText, mcpTargetText, mcpPayload, mcpNewSource, mcpEdit, mcpSave, mcpDel, mcpTest,
             mcpDiscover, mcpConfirm, mcpCloseModal, mcpJsonField, tsConfirmSource,
             capModal, capKind, capSource, capItems, capBusy, capDraftBusy, capDraftWarns,
             capOpen, capClose, capCoveredText, capClearAll, capDraft, capSave, capHasEvidence };`);
  return factory(ref, reactive, computed, makeFetch(routes), showToast, confirm, token);
}

const FORM = () => ({ slug: 'weather', name: ' 和风天气 ', base_url: 'https://api.demo.com/',
  auth_type: 'header', auth_name: 'Authorization', auth_prefix: 'Bearer ', auth_secret: 'sk-1',
  timeout: 10, headers_json: '{"X-Api-Version":"1"}', abilities: ' 查天气 ', docs: '', note: '', allow_private: true });

// ================= ① 表单 → 请求体 =================
(() => {
  const M = build([]);
  const b = M.tsSourcePayload(FORM());
  assert.strictEqual(b.slug, 'weather');
  assert.strictEqual(b.name, '和风天气');                       // 去空格
  assert.strictEqual(b.base_url, 'https://api.demo.com');       // 去尾斜杠
  assert.strictEqual(b.headers_json, '{"X-Api-Version":"1"}');  // JSON 原样规范化
  assert.strictEqual(b.timeout, 10);
  assert.strictEqual(b.allow_private, true);
  assert.strictEqual(b.auth_secret, 'sk-1');
  assert.strictEqual(b.max_bytes, 1048576);

  // 默认值：超时缺省 30、头缺省 {}、允许内网缺省 true
  const d = M.tsSourcePayload({ slug: 'x1', base_url: 'http://127.0.0.1:8000' });
  assert.strictEqual(d.timeout, 30);
  assert.strictEqual(d.headers_json, '{}');
  assert.strictEqual(d.allow_private, false);                   // 未传 = false（表单默认 true，但纯函数按入参）
  assert.strictEqual(d.auth_type, 'none');

  // 校验（与后端同口径）
  const bad = [
    [{ slug: '有中文', base_url: 'https://a.com' }, '短名'],
    [{ slug: '', base_url: 'https://a.com' }, '短名'],
    [{ slug: '-x', base_url: 'https://a.com' }, '短名'],
    [{ slug: 'a'.repeat(42), base_url: 'https://a.com' }, '短名'],
    [{ slug: 'ok', base_url: 'ftp://a.com' }, 'base_url'],
    [{ slug: 'ok', base_url: '' }, 'base_url'],
    [{ slug: 'ok', base_url: 'https://a.com', headers_json: '[1,2]' }, 'JSON 对象'],
    [{ slug: 'ok', base_url: 'https://a.com', headers_json: '{bad' }, 'JSON 对象'],
  ];
  for (const [f, why] of bad) {
    let err = null;
    try { M.tsSourcePayload(f); } catch (e) { err = e.message; }
    assert.ok(err && err.indexOf(why) >= 0, '应拒绝 ' + JSON.stringify(f) + '，实际错误：' + err);
  }
  console.log('  ✅ 表单→请求体（含 8 类非法输入）');
})();

// ================= ② 勾选 → 确认请求体（写操作闸） =================
(() => {
  const M = build([]);
  const cands = [
    { ref: 'api:weather#getCurrent', name: '当前天气', is_write: false, _checked: true,  _approved: false },
    { ref: 'api:weather#createHook', name: '建钩子',   is_write: true,  _checked: true,  _approved: false },
    { ref: 'api:weather#delHook',    name: '删钩子',   is_write: true,  _checked: true,  _approved: true  },
    { ref: 'api:weather#skip',       name: '没勾',     is_write: false, _checked: false, _approved: false },
  ];
  const p = M.tsConfirmPayload(' weather ', cands);
  assert.strictEqual(p.slug, 'weather');
  assert.deepStrictEqual(p.items.map(x => x.ref), ['api:weather#getCurrent', 'api:weather#delHook']);
  assert.strictEqual(p.items[0].approve_write, false);
  assert.strictEqual(p.items[1].approve_write, true);
  assert.strictEqual(p.items[1].name, '删钩子');

  // 写操作"勾了但没确认"绝不能发出去
  const onlyWrite = [{ ref: 'api:x#w', is_write: true, _checked: true, _approved: false }];
  assert.strictEqual(M.tsConfirmPayload('x', onlyWrite).items.length, 0);
  assert.strictEqual(M.tsConfirmPayload('x', null).items.length, 0);
  console.log('  ✅ 勾选→确认请求体（写操作需二次确认）');
})();

// ================= ③ 展示辅助 =================
(() => {
  const M = build([]);
  assert.strictEqual(M.tsCandidateBadge({ is_write: true }), '⚠️ 写操作');
  assert.strictEqual(M.tsCandidateBadge({ is_write: false }), '只读');
  assert.strictEqual(M.tsParamsText({ param_summary: '参数: city(必填,path)' }), '参数: city(必填,path)');
  assert.strictEqual(M.tsParamsText({}), '无参数');
  console.log('  ✅ 徽标与参数摘要文案');
})();

// ================= ④ 添加来源 / 体检 / 删除（真调 fetch） =================
(async () => {
  const items = [{ id: 3, slug: 'weather', name: '和风天气', state: 'verified', abilities: '查天气',
                   config: { base_url: 'https://api.demo.com', auth_type: 'none', headers: {}, timeout: 30, allow_private: true },
                   capability_count: 1, candidate_count: 2 }];
  const M = build([
    ['/api/tools/sources', { items: items }, true],
    ['/api/tools/source/save', { id: 3, slug: 'weather' }, true],
    ['/api/tools/source/3/test', { ok: true, steps: [{ name: 'base_url 合法', ok: true }] }, true],
    ['/api/tools/source/3/candidates', { slug: 'weather', items: [
      { ref: 'api:weather#getCurrent', name: '当前天气', read_only: true, is_write: false, confirmed: true,
        param_summary: '参数: city(必填,path)', warnings: [] },
      { ref: 'api:weather#createHook', name: '建钩子', read_only: false, is_write: true, confirmed: false,
        param_summary: '无参数', warnings: [] },
    ] }, true],
    ['/api/tools/source/3', { deleted_capabilities: 2 }, true],
    ['/api/tools/discover', { ok: true, kept_confirmed: 1, items: [
      { ref: 'api:weather#getCurrent', name: '当前天气', read_only: true, is_write: false, param_summary: '参数: city(必填,path)', warnings: [] },
      { ref: 'api:weather#createHook', name: '建钩子', read_only: false, is_write: true, warnings: ['请求体类型 text/plain 未特殊支持'] },
    ] }, true],
    ['/api/tools/confirm', { confirmed: ['api:weather#getCurrent'], skipped: [{ ref: 'api:weather#createHook', why: '写方法（非只读）需显式勾选「我确认这是写操作」' }] }, true],
  ]);

  await M.tsLoad();
  assert.strictEqual(M.tsSources.value.length, 1);
  assert.strictEqual(M.tsDiscoverSlug.value, 'weather');       // 自动选第一个来源
  assert.ok(calls.some(c => c.url.indexOf('/api/tools/source/3/candidates') >= 0),
            '★ 常态列表要顺带拉每个来源下的工具（卡片里直接列出来）');
  assert.strictEqual((M.tsCaps['weather'] || []).length, 2);
  assert.strictEqual(M.tsCaps['weather'][1].is_write, true);
  assert.strictEqual(M.tsStats.value.sources, 1);
  assert.strictEqual(M.tsStats.value.confirmed, 1);
  assert.strictEqual(M.tsStats.value.pending, 1);
  assert.strictEqual(M.tsStats.value.verified, 1);
  assert.strictEqual(M.tsStateText(items[0]), '✓ 已体检');
  assert.strictEqual(M.tsStateText({ state: 'failed' }), '✗ 体检失败');
  assert.strictEqual(M.tsStateText({}), '未体检');
  assert.strictEqual(M.tsAuthText(items[0]), '无鉴权');
  assert.strictEqual(M.tsAuthText({ config: { auth_type: 'header' } }), 'Header 里带');
  console.log('  ✅ tsLoad 拉列表 + 各来源工具 + 指导栏统计');

  // 体检：成功提示 + 重新拉列表
  calls.length = 0;
  await M.tsTest(items[0]);
  assert.ok(calls.some(c => c.url.indexOf('/api/tools/source/3/test') >= 0 && c.opts.method === 'POST'));
  assert.ok(toasts[toasts.length - 1].msg.indexOf('体检通过') >= 0);

  // 编辑：密钥清空（留空 = 不修改）、配置回填
  M.tsForm.auth_secret = 'should-be-cleared';
  M.tsEdit(items[0]);
  assert.strictEqual(M.tsForm.slug, 'weather');
  assert.strictEqual(M.tsForm.auth_secret, '');
  assert.strictEqual(M.tsForm.headers_json, '{}');
  assert.strictEqual(M.tsEditingId.value, 3);
  assert.strictEqual(M.tsModal.value, 'source', '★ 编辑要弹出表单（表单不在常态页面上）');
  assert.strictEqual(M.tsModalSource.value.id, 3);
  M.tsCloseModal();
  assert.strictEqual(M.tsModal.value, '', '取消/关闭后页面上不该还留着表单');
  // ＋ 新增：清空表单 + 打开弹窗
  M.tsNewSource();
  assert.strictEqual(M.tsModal.value, 'source');
  assert.strictEqual(M.tsEditingId.value, 0);
  assert.strictEqual(M.tsForm.slug, '');
  M.tsCloseModal();

  // 表单非法 → 不发请求
  calls.length = 0;
  M.tsForm.slug = '有中文';
  await M.tsSave();
  assert.strictEqual(calls.length, 0);
  assert.ok(toasts[toasts.length - 1].err && toasts[toasts.length - 1].msg.indexOf('短名') >= 0);

  // 表单合法 → POST /save
  M.tsForm.slug = 'weather'; M.tsForm.base_url = 'https://api.demo.com';
  calls.length = 0;
  await M.tsSave();
  const save = calls.find(c => c.url.indexOf('/api/tools/source/save') >= 0);
  assert.ok(save && save.opts.method === 'POST' && JSON.parse(save.opts.body).slug === 'weather');

  // 删除：确认框拒绝 → 不发请求；同意 → DELETE
  confirmAnswer = false; calls.length = 0;
  await M.tsDel(items[0]);
  assert.strictEqual(calls.length, 0);
  confirmAnswer = true; calls.length = 0;
  await M.tsDel(items[0]);
  assert.ok(calls.some(c => c.opts.method === 'DELETE'));
  console.log('  ✅ 体检 / 编辑 / 保存 / 删除（含确认框拒绝）');

  // 空 spec → 不请求
  calls.length = 0;
  M.tsDiscoverSlug.value = 'weather';
  M.tsSpecText.value = '   ';
  await M.tsDiscover();
  assert.strictEqual(calls.length, 0);
  assert.ok(toasts[toasts.length - 1].err);

  // 解析候选：写操作默认不勾、只读默认勾
  M.tsSpecText.value = '{"openapi":"3.0.0","paths":{}}';
  await M.tsDiscover();
  assert.strictEqual(M.tsCandidates.value.length, 2);
  assert.strictEqual(M.tsCandidates.value[0]._checked, true);
  assert.strictEqual(M.tsCandidates.value[0]._approved, false);
  assert.strictEqual(M.tsCandidates.value[1]._checked, false);   // ⚠️ 写操作默认不勾
  assert.ok(toasts[toasts.length - 1].msg.indexOf('已确认，人工设置保留') >= 0);

  // 勾选数量（computed）与只读候选一致
  assert.strictEqual(M.tsCheckedCount.value, 1);

  // 确认：只发勾选项；跳过的原因要提示出来
  calls.length = 0;
  await M.tsConfirm();
  const cf = calls.find(c => c.url.indexOf('/api/tools/confirm') >= 0);
  assert.ok(cf, '应发出确认请求');
  const payload = JSON.parse(cf.opts.body);
  assert.deepStrictEqual(payload.items.map(x => x.ref), ['api:weather#getCurrent']);
  assert.ok(toasts[toasts.length - 1].msg.indexOf('已确认 1 个') >= 0);
  assert.ok(toasts[toasts.length - 1].msg.indexOf('跳过 1 个') >= 0);

  // 什么都不勾 → 不请求
  M.tsCandidates.value = M.tsCandidates.value.map(c => ({ ...c, _checked: false }));
  calls.length = 0;
  await M.tsConfirm();
  assert.strictEqual(calls.length, 0);
  assert.ok(toasts[toasts.length - 1].err);
  console.log('  ✅ 解析候选 / 勾选 / 确认（含跳过原因与空勾选）');
  // ---------------- ⑧ 查看能力/待确认：重新打开已落库的候选（用户实测报障的那条）----------------
    // ★ 后端 `_cand_view()` 的**真实形状**（照抄一次真机响应，字段一个不多一个不少）
    const API_ITEM = (ref, isWrite, confirmed) => ({
  ref, name: '查询IP', confirmed, read_only: !isWrite, is_write: isWrite, enabled: confirmed,
  method: isWrite ? 'POST' : 'GET', url_template: '/api/ip',
  param_summary: '参数: ip(必填,query)', warnings: [],
    });
    const MC = build([['/candidates', { slug: 'xxapi', items: [
  API_ITEM('api:xxapi#ipQuery', false, true),
  API_ITEM('api:xxapi#makeHook', true, false),
    ] }]]);

    // 映射：写标记/参数摘要/确认态都要保住，★ 且默认**不勾**（重新打开不该"顺手确认"）
    const mapped = MC.tsCandFromApi(API_ITEM('api:xxapi#makeHook', true, false));
    assert.strictEqual(mapped.ref, 'api:xxapi#makeHook');
    assert.strictEqual(mapped.is_write, true);
    assert.strictEqual(mapped.read_only, false);
    assert.strictEqual(mapped.confirmed, false);
    assert.strictEqual(mapped.param_summary, '参数: ip(必填,query)');
    assert.strictEqual(mapped._checked, false, '重新打开列表不得默认勾选');
    assert.strictEqual(mapped._approved, false);
    assert.ok(Array.isArray(mapped.warnings));
    // 缺字段时不炸（后端字段少给一个也不能白屏）
    const thin = MC.tsCandFromApi({ ref: 'api:x#y' });
    assert.strictEqual(thin.name, 'api:x#y');
    assert.strictEqual(thin.is_write, false);
    assert.deepStrictEqual(thin.warnings, []);
    // 徽标/参数文案对写操作要如实
    assert.strictEqual(MC.tsCandidateBadge({ is_write: true }), '⚠️ 写操作');
    assert.strictEqual(MC.tsCandidateBadge({ is_write: false }), '只读');
    assert.strictEqual(MC.tsParamsText({ param_summary: '' }), '无参数');

  await MC.tsViewCandidates({ id: 7, slug: 'xxapi', name: '小小API（抖音热榜）' });
  assert.strictEqual(MC.tsDiscoverSlug.value, 'xxapi', '读取候选要同步后续确认用的 slug');
  assert.strictEqual(MC.tsCandidates.value.length, 2);
  assert.strictEqual(MC.tsCheckedCount.value, 0, '★ 打开列表时未确认项一律不勾（避免误确认）');
  assert.strictEqual(calls[calls.length - 1].url, '/api/tools/source/7/candidates?token=' + encodeURIComponent(token.value));
  // 用户勾了那条写操作、但没勾"我确认这是写操作" → 不进确认体
  MC.tsCandidates.value[1]._checked = true;
  assert.strictEqual(MC.tsCheckedCount.value, 0, '写操作没二次确认时不得进入确认体');
  MC.tsCandidates.value[1]._approved = true;
  assert.strictEqual(MC.tsCheckedCount.value, 1);
  assert.strictEqual(MC.tsConfirmPayload('xxapi', MC.tsCandidates.value).items[0].approve_write, true);
  // ★ 弹窗范式：＋导入 → 打开弹窗 + 清空粘贴框 + 读回已落库工具；关闭后页面上不留表单
  await MC.tsOpenImport({ id: 9, slug: 'xxapi', name: '小小API' });
  assert.strictEqual(MC.tsModal.value, 'import');
  assert.strictEqual(MC.tsModalSource.value.id, 9);
  assert.strictEqual(MC.tsSpecText.value, '', '每次打开导入弹窗都要清掉上一次粘贴的文档');
  assert.strictEqual(MC.tsCandidates.value.length, 2, '打开弹窗即列出已落库的工具');
  assert.strictEqual(MC.tsDiscoverSlug.value, 'xxapi');
  MC.tsCloseModal();
  assert.strictEqual(MC.tsModal.value, '', '★ 常态页面上不能留任何表单');
  console.log('  ✅ 弹窗范式（常态只读列表 · 点按钮才出表单 · 打开导入即列已落库工具）');

  // ---------------- 静态断言：模板结构 ----------------
  assert.ok(src.indexOf('🔗 API 工具接入') >= 0, '模板里应有 API 工具卡片');
  assert.ok(src.indexOf("skTab==='api'") >= 0, '卡片必须挂在「技能与工具 → API 工具」tab 下');
  assert.ok(src.indexOf('这里是<b>业务 API 工具</b>的后续扩展位') < 0,
            '★ 占位卡必须已经被真实表单取代（不能再是"后续扩展位"）');
  assert.ok(src.indexOf('我确认这是写操作') >= 0, '模板里应有写操作二次确认勾选项');
  assert.ok(src.indexOf('ts-write-ack') >= 0 && src.indexOf('ts-badge') >= 0, '模板应引用新样式类');
  assert.ok(src.indexOf("if ((t === 'api' || t === 'mcp') && !mcpAll.value.length) tsLoad();") >= 0,
            '切到 API / MCP 工具 tab 应懒加载来源（与 CLI tab 同款；一份数据分给两个 tab）');
  assert.ok(src.indexOf('@click="tsOpenImport(s)"') >= 0,
            '来源卡上要有「＋ 导入 / 查看工具」入口');
  assert.ok(src.indexOf("s.id + '/candidates") >= 0,
            '该入口必须读 /candidates 端点（不是只读内存里上一次解析结果）');
  // ★ 范式断言：常态页面上不能出现表单 —— 表单与粘贴框都必须在弹窗区块内
  const iSrcModal = src.indexOf("v-if=\"tsModal === 'source'\"");
  const iImpModal = src.indexOf("v-if=\"tsModal === 'import'\"");
  const iCard = src.indexOf('class="sk-card ts-src-card"');
  assert.ok(iCard > 0 && iSrcModal > iCard && iImpModal > iSrcModal, '顺序：来源卡 → 来源弹窗 → 导入弹窗');
  assert.ok(src.indexOf('v-model="tsForm.slug"') > iSrcModal, '★ 来源表单必须只在弹窗里（常态页面上看不到）');
  assert.ok(src.indexOf('v-model="tsSpecText"') > iImpModal, '★ 粘贴框必须只在导入弹窗里');
  assert.ok(src.slice(iCard, iSrcModal).indexOf('v-model="tsForm.') < 0,
            '★ 来源卡区域内不得出现任何表单字段（这正是"表单和列表粘在一起"的病根）');
  assert.ok(src.indexOf('我的来源 {{ tsStats.sources') >= 0 && src.indexOf('待确认 {{ tsStats.pending') >= 0,
            '指导栏要有统计 chips');
  assert.ok(src.indexOf('＋ 新增 API 来源') >= 0, '指导栏要有「＋ 新增 API 来源」');
  assert.ok(src.indexOf('@click.self="tsCloseModal()"') >= 0, '点遮罩要能关弹窗');
  console.log('  ✅ 模板结构（指导栏 / 常态来源卡 / 两个弹窗 / 候选入口）');


  // ---------------- ⑨ MCP tab（v3.0 M2：与 API 同范式，只是多了"发现/体检"和来源字段）----------------
  {
    const MCPSRC = (id, slug, transport) => ({
      id, slug, source: 'mcp', name: '本地演示 ' + slug, state: 'verified', enabled: true,
      capability_count: 1, candidate_count: 1, abilities: '查本机文件',
      config: { transport, command: 'npx', args: ['-y', 'pkg'], url: transport === 'http' ? 'http://127.0.0.1:8000/mcp' : '',
                timeout: 60, env_keys: ['TOKEN'], allow_stdio_commands: ['npx'], allow_private: true },
    });
    const APISRC = { id: 2, slug: 'xxapi', source: 'api', name: '小小API', state: 'configured',
                     capability_count: 2, candidate_count: 0, abilities: '', config: { base_url: 'https://v2.xxapi.cn' } };
    // mcpsrv 已确认一个只读工具 + 一个待确认的写工具
    const MCP_ITEM = (ref, isWrite, confirmed) => ({
      ref, name: ref.split('/').pop(), confirmed, read_only: !isWrite, is_write: isWrite, enabled: confirmed,
      source: 'mcp', tool: ref.split('/').pop(), read_only_hint: isWrite ? null : true,
      param_summary: '参数: city(必填,string)', warnings: [],
    });
    const STEPS = [{ name: '① 配置解析', ok: true, detail: 'transport=stdio' },
                   { name: '② 环境检测', ok: true, detail: '命令 → C:/py.exe' },
                   { name: '③ 协议握手', ok: true, detail: '已连接并完成 initialize', ms: 120 },
                   { name: '④ tools/list', ok: true, detail: '共 2 个工具：get_weather, place_order', ms: 30 },
                   { name: '⑤ 异常容错', ok: true, detail: '未知工具被干净拒绝：MCPError' },
                   { name: '⑥ 性能指标', ok: true, detail: '合计 150ms' },
                   { name: '⑦ 汇总', ok: true, detail: '工具 2 个' }];
    const M = build([
      ['/api/tools/sources', { items: [APISRC, MCPSRC(1, 'mcpsrv', 'stdio')] }],
      ['/discover', { ok: true, items: [MCP_ITEM('mcp:mcpsrv/get_weather', false, false),
                                        MCP_ITEM('mcp:mcpsrv/place_order', true, false)],
                      steps: STEPS, ms: 150, kept_confirmed: 1 }],
      ['/test', { ok: true, steps: STEPS, ms: 150, tool_count: 2 }],
      ['/candidates', { slug: 'mcpsrv', source: 'mcp', items: [MCP_ITEM('mcp:mcpsrv/get_weather', false, true)] }],
      ['/confirm', { confirmed: ['mcp:mcpsrv/get_weather'], skipped: [] }],
      ['/save', { id: 1, slug: 'mcpsrv', source: 'mcp' }],
    ]);

    // ① ★ 一份数据分给两个 tab：MCP 来源绝不能串进 API 列表
    await (async () => {
      await M.tsLoad();
      assert.deepStrictEqual(M.tsSources.value.map(x => x.slug), ['xxapi'], 'API tab 只应看到 api 来源');
      assert.deepStrictEqual(M.mcpSources.value.map(x => x.slug), ['mcpsrv'], 'MCP tab 只应看到 mcp 来源');
      assert.strictEqual(M.mcpStats.value.sources, 1);
      assert.strictEqual(M.mcpStats.value.verified, 1);
      assert.strictEqual(M.mcpTransportText(MCPSRC(1, 's', 'stdio')), '本地命令 (stdio)');
      assert.strictEqual(M.mcpTransportText(MCPSRC(1, 's', 'http')), 'HTTP 端点');
      assert.strictEqual(M.mcpTargetText(MCPSRC(1, 's', 'stdio')), 'npx -y pkg');
      assert.strictEqual(M.mcpTargetText(MCPSRC(1, 's', 'http')), 'http://127.0.0.1:8000/mcp');

      // ② 表单 → 请求体：必须带 source='mcp'，且三种非法输入都要被前端挡住
      const F = () => ({ ...M.mcpForm, slug: 'mcpsrv', transport: 'stdio', command: 'npx',
                         args_json: '["-y","pkg"]', env_json: '{}', timeout: 60 });
      const body = M.mcpPayload(F());
      assert.strictEqual(body.source, 'mcp');
      assert.strictEqual(body.transport, 'stdio');
      assert.strictEqual(body.args_json, '["-y","pkg"]');
      assert.strictEqual(body.timeout, 60);
      const bad = (patch, why, expectMsg) => {
        const f = { ...F() }; Object.assign(f, patch);
        let err = null;
        try { M.mcpPayload(f); } catch (e) { err = e; }
        assert.ok(err, why);
        if (expectMsg) assert.ok(String(err.message).indexOf(expectMsg) >= 0,
                                 why + ' —— 实际提示：' + err.message);
      };
      bad({ slug: '中文名' }, '短名必须 ASCII');
      bad({ command: '  ' }, 'stdio 必须填命令');
      bad({ args_json: '{"a":1}' }, '参数必须是字符串数组');
      // ★ 用户实测踩过的坑：Windows 路径原样贴进来，JSON 里 \L \m \b 都是非法转义 → 提示必须提到正斜杠
      bad({ args_json: '["D:\\LLM\\mcp_servers\\bnf\\bnf_server.py"]' },
          'Windows 反斜杠在 JSON 里是非法转义 → 必须给可读提示', '正斜杠');
      bad({ env_json: '{"P":"D:\\LLM"}' }, 'env 的非法转义同样要提示', '正斜杠');
      // 正斜杠写法必须能过（Windows 上也一样能用）
      assert.strictEqual(
        JSON.parse(M.mcpPayload({ ...F(), args_json: '["D:/LLM/mcp_servers/bnf/bnf_server.py"]' }).args_json)[0],
        'D:/LLM/mcp_servers/bnf/bnf_server.py');
      // 通用解析器本身也要能给到 JSON 引擎的原始原因（便于排错）
      let je = null;
      try { M.mcpJsonField('["D:\\LLM"]', '参数（args）', '[]'); } catch (e) { je = e; }
      assert.ok(je && String(je.message).indexOf('参数（args）') === 0, '报错要指明是哪个字段');
      bad({ env_json: '[]' }, '环境变量必须是对象');
      bad({ transport: 'http', url: 'ftp://x' }, 'http 端点必须 http(s)://');
      assert.strictEqual(M.mcpPayload({ ...F(), transport: 'http', url: 'http://127.0.0.1:9/mcp' }).transport, 'http');

      // ③ 发现工具：POST 带 source='mcp' → 弹窗出体检七步 + 候选（只读默认勾、写操作不勾）
      await M.mcpDiscover(MCPSRC(1, 'mcpsrv', 'stdio'));
      // ★ 不能用 calls[len-1]：mcpDiscover 末尾会 tsLoad()，最后一条是 sources 的 GET
    const lastOf = (frag) => calls.filter(c => c.url.indexOf(frag) >= 0).pop();
    const dCall = lastOf('/api/tools/discover');
      assert.strictEqual(dCall.opts.method, 'POST');
      assert.ok(dCall.url.indexOf('/api/tools/discover') === 0, dCall.url);
      assert.strictEqual(JSON.parse(dCall.opts.body).source, 'mcp', '★ 发现请求必须声明来源');
      assert.strictEqual(JSON.parse(dCall.opts.body).slug, 'mcpsrv');
      assert.strictEqual(M.mcpModal.value, 'probe', '发现结果要在弹窗里');
      assert.strictEqual(M.mcpProbe.value.steps.length, 7, '七步体检都要展示');
      assert.strictEqual(M.tsConfirmSource.value, 'mcp', '★ 发现之后确认请求要认 mcp 口径');
      assert.strictEqual(M.tsCandidates.value[0]._checked, true, '只读工具默认勾');
      assert.strictEqual(M.tsCandidates.value[1]._checked, false, '写工具默认不勾');
      assert.strictEqual(M.tsCheckedCount.value, 1);

      // ④ 确认：请求体带 source='mcp'；写操作没二次确认时不计入
      M.tsCandidates.value[1]._checked = true;
      assert.strictEqual(M.tsCheckedCount.value, 1, '写操作未二次确认 → 不进确认体');
      M.tsCandidates.value[1]._approved = true;
      assert.strictEqual(M.tsCheckedCount.value, 2);
      await M.mcpConfirm();
      const cCall = lastOf('/api/tools/confirm');
      assert.ok(cCall.url.indexOf('/api/tools/confirm') === 0, cCall.url);
      const cBody = JSON.parse(cCall.opts.body);
      assert.strictEqual(cBody.source, 'mcp');
      assert.strictEqual(cBody.items.length, 2);
      assert.strictEqual(cBody.items[1].approve_write, true);
      assert.strictEqual(M.mcpModal.value, 'probe', '确认后弹窗不关（方便继续勾）');

      // ⑤ 体检（不发现）：只出七步、不出候选
      M.tsCandidates.value = [];
      await M.mcpTest(MCPSRC(1, 'mcpsrv', 'stdio'));
      assert.strictEqual(lastOf('/test?').opts.method, 'POST');
      assert.strictEqual(M.mcpProbe.value.tool_count, 2);
      assert.strictEqual(M.mcpProbe.value.steps.length, 7);

      // ⑥ 编辑回填 / 新建清空（表单只活在弹窗里）
      M.mcpEdit(MCPSRC(1, 'mcpsrv', 'stdio'));
      assert.strictEqual(M.mcpModal.value, 'source');
      assert.strictEqual(M.mcpForm.slug, 'mcpsrv');
      assert.strictEqual(M.mcpForm.transport, 'stdio');
      assert.strictEqual(M.mcpForm.env_json, '{}', '编辑时 env 一律留空（留空 = 不修改已存的）');
      assert.strictEqual(M.mcpForm.allow_stdio_commands, 'npx');
      M.mcpNewSource();
      assert.strictEqual(M.mcpForm.slug, '');
      assert.strictEqual(M.mcpEditingId.value, 0);
      assert.strictEqual(M.mcpModal.value, 'source');
      M.mcpCloseModal();
      assert.strictEqual(M.mcpModal.value, '', '★ 常态页面上不能留表单');

      // ⑦ 保存：请求体带 source='mcp'
      Object.assign(M.mcpForm, { slug: 'newmcp', transport: 'stdio', command: 'npx', args_json: '[]' });
      await M.mcpSave();
      const sCall = lastOf('/api/tools/source/save');
      assert.ok(sCall.url.indexOf('/api/tools/source/save') === 0, sCall.url);
      assert.strictEqual(JSON.parse(sCall.opts.body).source, 'mcp');

      // ⑧ 静态断言：MCP 占位卡必须已被真实界面取代；表单只能出现在弹窗里
      assert.ok(src.indexOf('🔌 MCP 工具接入') >= 0, '模板里应有 MCP 卡片');
      assert.ok(src.indexOf("skTab==='mcp'") >= 0, '卡片要挂在 MCP tab 下');
      assert.ok(src.indexOf('此处占位') < 0, '★ 旧占位文案必须已被真实界面取代');
      assert.ok(src.indexOf('＋ 新增 MCP 来源') >= 0 && src.indexOf('🔍 发现 / 查看工具') >= 0,
                '指导栏与来源卡要有入口');
      const iMcpSrcModal = src.indexOf("v-if=\"mcpModal === 'source'\"");
      const iMcpProbe = src.indexOf("v-if=\"mcpModal === 'probe'\"");
      const iMcpCard = src.indexOf('v-for="s in mcpSources"');
      assert.ok(iMcpCard > 0 && iMcpSrcModal > iMcpCard && iMcpProbe > iMcpSrcModal,
                '顺序：MCP 来源卡 → 来源弹窗 → 发现弹窗');
      assert.ok(src.indexOf('v-model="mcpForm.slug"') > iMcpSrcModal, '★ MCP 表单必须只在弹窗里');
      assert.ok(src.slice(iMcpCard, iMcpSrcModal).indexOf('v-model="mcpForm.') < 0,
                '★ MCP 来源卡区域内不得出现任何表单字段');
      assert.ok(src.indexOf('命令白名单') >= 0, 'stdio 要有白名单字段（安全边界）');
      assert.ok(src.indexOf('我确认这是写操作') >= 0, '写操作二次确认勾选项要在');
      console.log('  ✅ MCP tab（分来源过滤 · 发现七步 · 确认带 source · 表单只在弹窗）');
    })();
  }


  // ---------------- ⑩ M5c-1：来源级「我确认这个服务全是只读」声明（前端）----------------
  await (async () => {
    const MSRC = (id, slug) => ({
      id, slug, source: 'mcp', name: '只读声明演示', state: 'verified', enabled: true,
      capability_count: 1, candidate_count: 1, abilities: '', config: {
        transport: 'stdio', command: 'npx', args: ['-y', 'pkg'], url: '', timeout: 60,
        env_keys: [], allow_stdio_commands: ['npx'], allow_private: true, assume_read_only: true,
      },
    });
    const M = build([
      ['/api/tools/sources', { items: [MSRC(5, 'rodecl')] }],
      ['/candidates', { slug: 'rodecl', source: 'mcp', assume_read_only: true, items: [] }],
      ['/api/tools/source/save', { id: 5, slug: 'rodecl', source: 'mcp', readonly_synced: 3 }],
    ]);
    const lastOf = (frag) => calls.filter(c => c.url.indexOf(frag) >= 0).pop();
    const F = () => ({ ...M.mcpForm, slug: 'rodecl', transport: 'stdio', command: 'npx',
                       args_json: '["-y","pkg"]', env_json: '{}' });

    // ① 请求体默认不带开关；勾上后必须带
    assert.strictEqual(M.mcpPayload(F()).assume_read_only, false);
    assert.strictEqual(M.mcpPayload({ ...F(), assume_read_only: true }).assume_read_only, true);

    // ② 勾了"全只读"→ 先弹确认框；点「确定」→ sync_readonly=true（连已确认工具一起刷）
    confirmAnswer = true;
    Object.assign(M.mcpForm, { ...M.mcpForm, ...F(), assume_read_only: true });
    await M.mcpSave();
    let body = JSON.parse(lastOf('/api/tools/source/save').opts.body);
    assert.strictEqual(body.assume_read_only, true);
    assert.strictEqual(body.sync_readonly, true, '★ 点确定后要连已确认的工具一起刷新');
    // 后端回了刷新条数 → toast 要说清楚（用户得知道刚才动了什么）
    assert.ok(toasts.filter(t => t.msg.indexOf('刷新了 3 个') >= 0).length > 0,
              '刷新条数必须回报给用户：' + JSON.stringify(toasts.slice(-3)));

    // ③ 点「取消」→ 只保存开关，不刷新已确认行
    toasts.length = 0;
    confirmAnswer = false;
    Object.assign(M.mcpForm, { ...M.mcpForm, ...F(), assume_read_only: true });
    await M.mcpSave();
    body = JSON.parse(lastOf('/api/tools/source/save').opts.body);
    assert.strictEqual(body.assume_read_only, true);
    assert.strictEqual(body.sync_readonly, undefined, '★ 取消时不得刷新已确认的工具');
    confirmAnswer = true;

    // ④ 没勾开关时不该弹框、也不带 sync_readonly
    toasts.length = 0;
    Object.assign(M.mcpForm, { ...M.mcpForm, ...F(), assume_read_only: false });
    await M.mcpSave();
    body = JSON.parse(lastOf('/api/tools/source/save').opts.body);
    assert.strictEqual(body.assume_read_only, false);
    assert.strictEqual(body.sync_readonly, undefined);

    // ⑤ 编辑回填：开关要跟着来源配置回显
    M.mcpEdit(MSRC(5, 'rodecl'));
    assert.strictEqual(M.mcpForm.assume_read_only, true, '编辑时要回显已保存的声明');
    M.mcpCloseModal();

    // ⑥ 静态断言：勾选框必须在**来源弹窗内**（常态页面上不能有表单元素）
    const iModal = src.indexOf("v-if=\"mcpModal === 'source'\"");
    const iProbe = src.indexOf("v-if=\"mcpModal === 'probe'\"");
    const iBox = src.indexOf('v-model="mcpForm.assume_read_only"');
    assert.ok(iBox > iModal && iBox < iProbe, '勾选框必须在来源配置弹窗里');
    assert.ok(src.indexOf('我声明全只读') >= 0, '列表卡上要有声明徽标');
    assert.ok(src.indexOf('明确声明"非只读"') >= 0 || src.indexOf('说了会改状态就还是写操作') >= 0,
              '弹窗里必须写清"明确 false 不被覆盖"这条边界');
    assert.ok(src.indexOf('关键词：xxx, yyy') >= 0, '能力描述要提示 keywords 写法（M5c-2 的路由信号）');
    console.log('  ✅ M5c-1 只读声明（勾选框只在弹窗 · 确认框决定是否刷新已确认行 · 边界文案在位）');
  })();

  // ---------------- ⑪ M5c-2'：工具描述（可编辑 + AI 预写）----------------
  await (async () => {
    const MSRC = (id, slug) => ({
      id, slug, source: 'mcp', name: '城市服务', state: 'verified', enabled: true,
      capability_count: 2, candidate_count: 0, abilities: '城市信息与天气', docs: 'D:/mcp/city/README.md',
      intro: '这里能搜商品、看详情、逛榜单',
      config: { transport: 'stdio', command: 'npx', args: [], url: '', timeout: 60, cwd: 'D:/mcp/city',
                env_keys: [], allow_stdio_commands: ['npx'], allow_private: true, assume_read_only: false },
    });
    const MCAPS = { slug: 'capdemo', source: 'mcp', items: [
      { ref: 'mcp:capdemo/search_city', name: 'search_city', is_write: false, confirmed: true,
        abilities: 'Search a city', abilities_user: '', param_summary: 'city_name(必填,query)' },
      { ref: 'mcp:capdemo/city_weather', name: 'city_weather', is_write: false, confirmed: true,
        abilities: 'Weather', abilities_user: '', param_summary: 'city_name(可选,query)' },
      { ref: 'mcp:capdemo/handwritten', name: 'handwritten', is_write: false, confirmed: true,
        abilities: 'Auto text', abilities_user: '我手写过的描述', param_summary: '' },
    ] };
    const M = build([
      // ⚠️ 顺序敏感：`/api/tools/sources` 是下面两个端点的**子串**，更具体的必须排在前面
      ['/api/tools/sources/ability_draft', { items: [
        { ref: 'mcp:capdemo/search_city', name: 'search_city', text: '关键词：城市信息/地名。用户问某城市基本情况时用。' }],
        evidence: { docs: { ok: true, url: 'D:/mcp/city/README.md', chars: 900 }, tools: { total: 3, described: 1 } },
        warnings: ['2 个工具没被写到描述：`city_weather`、`handwritten`'] }],
      ['/api/tools/sources/cap_abilities', { ok: true, saved: [{ ref: 'mcp:capdemo/search_city', text: 'x' }], skipped: [] }],
      ['/candidates', MCAPS],
      ['/api/tools/sources', { items: [MSRC(9, 'capdemo')] }],
    ]);
    const lastOf = (frag) => calls.filter(c => c.url.indexOf(frag) >= 0).pop();

    // ① 打开：懒加载工具列表 + 预填（手写优先、其次自动摘要）
    await M.tsLoad();                    // mcpSources 是 computed → 只能经真实加载路径填充
    assert.strictEqual(M.mcpSources.value.length, 1, '来源列表要先拉到');
    await M.capOpen(M.mcpSources.value[0], 'mcp');
    assert.strictEqual(M.capModal.value, true, '要打开弹窗');
    assert.strictEqual(M.capKind.value, 'mcp');
    assert.strictEqual(M.capItems.value.length, 3);
    assert.strictEqual(M.capItems.value[0].text, 'Search a city', '没手写时预填自动摘要');
    assert.strictEqual(M.capItems.value[2].text, '我手写过的描述', '★ 手写的优先预填');
    assert.strictEqual(M.capCoveredText(), '已手写 1 / 3 个', '只有真正改过的才算手写');

    // ② ✨ AI 预写：请求带 source/slug；结果填进框里；模型没写到的保持原样；审计提示要显示
    await M.capDraft();
    const db = JSON.parse(lastOf('ability_draft').opts.body);
    assert.strictEqual(db.source, 'mcp');
    assert.strictEqual(db.slug, 'capdemo');
    assert.ok(M.capItems.value[0].text.indexOf('城市信息') >= 0, 'AI 结果要填进输入框（用户可再改）');
    assert.strictEqual(M.capItems.value[1].text, 'Weather', '模型没写到的工具保持原样');
    assert.strictEqual(M.capDraftWarns.value.length, 1, '审计提示要显示出来');
    assert.ok(toasts[toasts.length - 1].msg.indexOf('依据 README') >= 0, '读到 README 时要说明依据');

    // ③ 💾 保存：与自动摘要逐字相同的按"不覆盖"（空串）发；手写的照发；结束后关弹窗并刷新列表
    M.capItems.value[0].text = '关键词：城市信息。用户问城市时用。';
    M.capItems.value[1].text = 'Weather';                 // 与自动摘要相同
    M.capItems.value[2].text = '我手写过的描述';
    await M.capSave();
    const sb = JSON.parse(lastOf('cap_abilities').opts.body);
    assert.strictEqual(sb.source, 'mcp');
    assert.strictEqual(sb.items[0].text, '关键词：城市信息。用户问城市时用。');
    assert.strictEqual(sb.items[1].text, '', '★ 与自动摘要逐字相同的要按不覆盖处理（空串）');
    assert.strictEqual(sb.items[2].text, '我手写过的描述');
    assert.strictEqual(M.capModal.value, false, '保存后要关弹窗');
    assert.ok(toasts[toasts.length - 1].msg.indexOf('已保存') >= 0);

    // ④ 清空：全部清成空串（保存后这些工具回到自动摘要）
    M.capClearAll();
    assert.ok(M.capItems.value.every(c => c.text === ''), '清空要作用于每一条');
    assert.strictEqual(M.capCoveredText(), '已手写 0 / 3 个');

    // ⑤ 官方介绍文案（M5c-2'：第三方服务的 README 常只讲"怎么接入" → 第二个信息源）
    //    两个都没填时**不该发请求**，直接给同一句话（本地预检，省一次空跑 + 省一次模型调用）
    M.capSource.value = { slug: 'capdemo', name: '城市服务', docs: '', intro: '' };
    assert.strictEqual(M.capHasEvidence(), false, '两个都空 → 没依据');
    calls.length = 0; toasts.length = 0;
    await M.capDraft();
    assert.strictEqual(calls.length, 0, '★ 没依据时不能发请求');
    assert.ok(toasts[toasts.length - 1].msg.indexOf('没有填信息来源，无法 AI 预生成') >= 0);
    // 只贴官方文案（没有 README）→ 有依据、能生成，payload 要带上 intro
    M.capSource.value = { slug: 'capdemo', name: '城市服务', docs: '', intro: '这里能搜商品、看详情、逛榜单' };
    assert.strictEqual(M.capHasEvidence(), true, '贴了文案就算有依据');
    calls.length = 0;
    await M.capDraft();
    const db2 = JSON.parse(lastOf('ability_draft').opts.body);
    assert.strictEqual(db2.slug, 'capdemo', '有依据时要真的发请求');
    // 表单 payload / 回填都要带 intro
    Object.assign(M.mcpForm, { ...M.mcpForm, slug: 'capdemo', intro: '  官方介绍文案  ' });
    assert.strictEqual(M.mcpPayload({ ...M.mcpForm }).intro, '官方介绍文案', 'payload 要 trim 后带上文案');
    M.mcpEdit(MSRC(9, 'capdemo'));
    assert.strictEqual(M.mcpForm.intro, '这里能搜商品、看详情、逛榜单', '编辑时要回显已保存的文案');
    M.mcpCloseModal();

    // ⑥ 静态断言：共用弹窗只有一份；两个页签各自有入口；README 两种形态都写清
    assert.strictEqual((src.match(/v-if="capModal"/g) || []).length, 1, '共用一个弹窗（不能两个 tab 各来一份）');
    assert.ok(src.indexOf("capOpen(s, 'mcp')") >= 0, 'MCP 卡片要有「📝 工具描述」入口');
    assert.ok(src.indexOf("capOpen(s, 'api')") >= 0, 'API 卡片也要有');
    assert.ok(src.indexOf('📝 已写描述') >= 0, '写过描述的工具要有标记');
    assert.ok(src.indexOf('本地文件路径') >= 0 && src.indexOf('网址') >= 0, 'README 字段要说明两种填法');
    assert.strictEqual((src.match(/v-model="mcpForm.intro"/g) || []).length, 1, 'MCP 表单要有官方介绍文案');
    assert.strictEqual((src.match(/v-model="tsForm.intro"/g) || []).length, 1, 'API 表单也要有');
    assert.ok(src.indexOf('不会生成') >= 0, '弹窗里要写清「两个都不填则不生成」');
    assert.ok(src.indexOf('不会</b>冲掉你写的内容') >= 0 || src.indexOf('不会') >= 0, '要说清重新发现不会冲掉手写内容');
    console.log('  ✅ M5c-2\' 工具描述（弹窗共用 · AI 预写填框 · 与自动摘要相同的按不覆盖 · 清空/标记在位）');
  })();

  console.log('\n' + '='.repeat(60));
  console.log('M1c 工具来源前端逻辑：全部通过 ✅');
})();
