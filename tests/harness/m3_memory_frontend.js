/* M3-7 前端逻辑测试：AI 画像建议面板（无需浏览器、无需服务）
 *
 * 思路与 m1c/m4a 一致：**从 front/index.html 抽取真实代码块**，补一层 Vue 极简 shim，在 Node 里跑真实调用序列。
 * 重点钉住四条"错了就会出事"的口径：
 *   ① 默认勾选：**人工行与白名单外新维度默认不勾**（D8/D6 —— 保护用户手写、防维度漂移）；
 *   ② 应用：只发**勾中**的条目，且带 `protect_manual`；
 *   ③ 一键导入：必须先过 `confirm`，取消则**一个请求都不发**（整份覆盖破坏面最大）；
 *   ④ 还原：过 `confirm` → 发快照 id → 刷新正文并清空建议。
 *
 * 运行：node tests/m3_memory_frontend.js < /dev/null
 */
const fs = require('fs');
const path = require('path');
const assert = require('assert');

const src = fs.readFileSync(path.join(__dirname, '..', '..', 'front', 'index.html'), 'utf8').replace(/\r\n/g, '\n');
// 第二段：M3-7 的函数块
const A = '// ---------- M3-7：AI 建议 / 应用 / 一键导入 / 历史还原（M3）----------';
const B = '// ---------- 定制助手：语音包（音色克隆，联动 voice_test） ----------';
const i = src.indexOf(A), j = src.indexOf(B);
if (i < 0 || j < 0 || j <= i) { console.error('未能在 index.html 中定位 M3-7 代码块'); process.exit(1); }
// 第一段：M3-7 的 ref/computed（声明在 setup 顶部，与函数块不在一起 —— 抽两段拼起来，
// 这样 memCheckedCount 用的就是**真实的那份 computed**，而不是测试里重写一遍）
const R0 = "    const memoryContent = ref('');";
const R1 = '    const memoryInitialized = ref(false);';
const ri = src.indexOf(R0), rj = src.indexOf(R1);
if (ri < 0 || rj < 0 || rj <= ri) { console.error('未能在 index.html 中定位 M3-7 状态声明'); process.exit(1); }
const block = src.slice(ri, rj) + '\n' + src.slice(i, j);

const toasts = [];
function showToast(m, e) { toasts.push({ msg: String(m), err: !!e }); }
function ref(v) { return { value: v }; }
function computed(fn) { return { get value() { return fn(); } }; }
const token = ref('tok-demo');
let confirmAnswer = true;
const confirmMsgs = [];
function confirm(msg) { confirmMsgs.push(String(msg || '')); return confirmAnswer; }
let calls = [];
function makeFetch(routes) {
  return async function (url, opts) {
    calls.push({ url: String(url), opts: opts || {} });
    for (const r of routes) if (String(url).indexOf(r[0]) >= 0) return { ok: r[2] !== false, json: async () => r[1] };
    return { ok: true, json: async () => ({}) };
  };
}

function build(routes) {
  const factory = new Function('ref', 'computed', 'fetch', 'showToast', 'confirm', 'token', 'encodeURIComponent',
    block + `
    return { memoryContent, memSuggestions, memSnapshots, memBusy, memProtectManual, memEvidence, memSkipped,
             memChanged, memCheckedCount, suggestMemory, decorateSuggestions, clearSuggestions, applyMemory,
             loadSnapshots, restoreSnapshot };`);
  return factory(ref, computed, makeFetch(routes), showToast, confirm, token, encodeURIComponent);
}

const SUGG = [
  { op: 'update', field: '身份与领域', old: '大四学生', new: 'AI 应用开发实习生', source: '会话', manual: true, flagged: false },
  { op: 'add', field: '消费偏好', old: '', new: '对降价敏感', source: '关注#7', manual: false, flagged: false },
  { op: 'remove', field: '预算范围', old: '暂无', new: '', source: '', manual: false, flagged: false },
  { op: 'add', field: '生日', old: '', new: '6月1日', source: '会话', manual: false, flagged: true },
];
let ok = 0, bad = 0;
function t(name, fn) {
  try { fn(); ok++; console.log('  ✅ ' + name); }
  catch (e) { bad++; console.log('  ❌ ' + name + ' —— ' + e.message); }
}

(async () => {
  // ---------------- ① 默认勾选口径
  t('默认勾选：人工行与新维度不勾，其余勾上', () => {
    const v = build([]);
    const ds = v.decorateSuggestions(SUGG);
    const by = (f) => ds.find((s) => s.field === f);
    assert.strictEqual(by('身份与领域').checked, false, '人工行默认不勾（D8）');
    assert.strictEqual(by('生日').checked, false, '白名单外新维度默认不勾（D6）');
    assert.strictEqual(by('消费偏好').checked, true);
    assert.strictEqual(by('预算范围').checked, true);
    assert.strictEqual(v.decorateSuggestions(SUGG).length, 4);
  });

  // ---------------- ② 建议请求与依据提示
  await (async () => {
    const v = build([['/api/memory/suggest', { suggestions: SUGG, evidence: { rows: 12, reports: 2, sessions: 30 } }]]);
    await v.suggestMemory();
    t('建议：POST /api/memory/suggest，并回填依据行', () => {
      assert.ok(calls.some((c) => c.url.indexOf('/api/memory/suggest') >= 0 && c.opts.method === 'POST'));
      assert.ok(/12/.test(v.memEvidence.value) && /周报/.test(v.memEvidence.value), '依据行要有数字');
      assert.strictEqual(v.memSuggestions.value.length, 4);
      assert.strictEqual(v.memCheckedCount.value, 2);
    });
  })();

  await (async () => {
    const v = build([['/api/memory/suggest', { suggestions: [], evidence: {} }]]);
    await v.suggestMemory();
    t('证据不足/无变化：提示而不报错', () => {
      assert.strictEqual(v.memSuggestions.value.length, 0);
      assert.ok(toasts.some((x) => /没有需要更新/.test(x.msg)));
    });
  })();

  // ---------------- ③ 应用勾选：只发勾中的
  await (async () => {
    calls = []; toasts.length = 0;
    const v = build([['/api/memory/suggest', { suggestions: SUGG, evidence: {} }],
                     ['/api/memory/apply', { ok: true, wrote: true, applied: [{ op: 'add', field: '消费偏好' }],
                                             skipped: [{ op: 'update', field: '身份与领域', why: '人工行受保护' }],
                                             content: '新内容' }]]);
    await v.suggestMemory();
    await v.applyMemory(false);
    t('应用：只发勾选条目 + 带 protect_manual + 回填正文与跳过原因', () => {
      const c = calls.find((x) => x.url.indexOf('/api/memory/apply') >= 0);
      assert.ok(c, '应发 apply');
      const body = JSON.parse(c.opts.body);
      assert.strictEqual(body.items.length, 2, '只发勾中的 2 条');
      assert.deepStrictEqual(body.items.map((s) => s.field).sort(), ['消费偏好', '预算范围']);
      assert.strictEqual(body.protect_manual, true);
      assert.strictEqual(body.confirm, undefined, 'apply 不需要 confirm 字段');
      assert.strictEqual(v.memoryContent.value, '新内容', '正文要刷新');
      assert.ok(/人工行受保护/.test(v.memSkipped.value), '跳过原因要显示');
      assert.strictEqual(v.memSuggestions.value.length, 0, '应用后清空建议列表');
    });
  })();

  // ---------------- ④ 一键导入：必须先确认
  await (async () => {
    calls = []; toasts.length = 0;
    const v = build([['/api/memory/suggest', { suggestions: SUGG, evidence: {} }],
                     ['/api/memory/import', { ok: true, wrote: true, applied: [], skipped: [], content: 'x' }]]);
    await v.suggestMemory();
    const n0 = calls.length;
    confirmAnswer = false;
    await v.applyMemory(true);
    t('一键导入：用户取消 → 一个请求都不发', () => {
      assert.strictEqual(calls.length, n0, '★ 取消确认绝不能发请求（D8）');
      assert.strictEqual(calls.filter((c) => c.url.indexOf('/api/memory/import') >= 0).length, 0);
    });

    confirmAnswer = true;
    calls = []; toasts.length = 0;
    await v.suggestMemory();      // 建议列表被清空后重建
    await v.applyMemory(true);
    t('一键导入：确认后发全量 + confirm=true', () => {
      const c = calls.find((x) => x.url.indexOf('/api/memory/import') >= 0);
      assert.ok(c, '应发 import');
      const body = JSON.parse(c.opts.body);
      assert.strictEqual(body.items.length, 4, '整份 4 条都发');
      assert.strictEqual(body.confirm, true, '★ 后端要求显式确认');
      assert.strictEqual(body.protect_manual, true);
    });
  })();

  // ---------------- ⑤ 历史版本与还原
  await (async () => {
    calls = []; toasts.length = 0; confirmAnswer = true;
    const v = build([['/api/memory/snapshots', { snapshots: [
                        { id: 3, created_at_text: '2026-09-27 10:00', reason: 'agent_update', preview: '身份与领域：…' }] }],
                     ['/api/memory/restore', { ok: true, content: '旧内容' }]]);
    await v.loadSnapshots();
    t('历史版本：reason 翻成中文', () => {
      assert.strictEqual(v.memSnapshots.value.length, 1);
      assert.strictEqual(v.memSnapshots.value[0].why, '助手更新');
      assert.ok(/2026-09-27/.test(v.memSnapshots.value[0].when));
    });
    await v.restoreSnapshot(v.memSnapshots.value[0]);
    t('还原：带快照 id + 刷新正文 + 清空建议', () => {
      const c = calls.find((x) => x.url.indexOf('/api/memory/restore') >= 0);
      assert.ok(c);
      // ★ 2026-09-28 用户实测：原来断言的是 `{id}` —— 后端字段名是 snapshot_id，发 {id} 会被 422 拒。
      //   这条断言当时钉错了契约，所以测试全绿、真机报错。现在钉的是真实字段名。
      assert.strictEqual(JSON.parse(c.opts.body).snapshot_id, 3);
      assert.strictEqual(v.memoryContent.value, '旧内容');
      assert.strictEqual(v.memChanged.value, true);
      assert.strictEqual(v.memSuggestions.value.length, 0);
    });

    confirmAnswer = false; calls = [];
    await v.restoreSnapshot({ id: 9, when: 'x', why: 'y' });
    t('还原：取消 → 不发请求', () => assert.strictEqual(calls.length, 0));
  })();

  // ---------------- ⑥ 边界与失败路径
  await (async () => {
    const v = build([['/api/memory/suggest', { detail: '没有填信息来源' }, false]]);
    await v.suggestMemory();
    t('建议失败：提示后端原因，且不写坏列表', () => {
      assert.strictEqual(v.memSuggestions.value.length, 0);
      assert.ok(toasts.some((x) => x.err && /信息来源/.test(x.msg)), '要提示后端给的原因');
      assert.strictEqual(v.memBusy.value, false, '失败后要解除忙碌');
    });
  })();

  await (async () => {
    calls = []; toasts.length = 0;
    const v = build([['/api/memory/suggest', { suggestions: SUGG, evidence: {} }],
                     ['/api/memory/apply', { detail: '保护人工行' }, false]]);
    await v.suggestMemory();
    const before = v.memoryContent.value;
    await v.applyMemory(false);
    t('应用失败：正文不动 + 建议保留（不能假装成功）', () => {
      assert.strictEqual(v.memoryContent.value, before);
      assert.strictEqual(v.memSuggestions.value.length, 4, '失败后建议还在，用户可以重试');
      assert.ok(toasts.some((x) => x.err));
    });
  })();

  await (async () => {
    calls = []; toasts.length = 0;
    const v = build([['/api/memory/suggest', { suggestions: SUGG, evidence: {} }]]);
    await v.suggestMemory();
    v.memSuggestions.value.forEach((s) => { s.checked = false; });
    const n0 = calls.length;
    await v.applyMemory(false);
    t('一条都没勾就点应用：只提示，不发请求', () => {
      assert.strictEqual(calls.length, n0);
      assert.ok(toasts.some((x) => x.err && /勾选/.test(x.msg)));
      assert.strictEqual(v.memCheckedCount.value, 0, 'computed 勾选数要跟着变');
    });
  })();

  await (async () => {
    calls = []; toasts.length = 0;
    const v = build([['/api/memory/suggest', { suggestions: [], note: '证据不足：系统里还没有资料', evidence: {} }]]);
    await v.suggestMemory();
    t('后端给 note 时优先显示 note（证据不足要说清楚）', () => {
      assert.ok(/证据不足/.test(v.memEvidence.value));
    });
  })();

  await (async () => {
    calls = []; toasts.length = 0;
    const v = build([['/api/memory/suggest', { suggestions: SUGG, evidence: {} }],
                     ['/api/memory/apply', { ok: true, wrote: false, applied: [], skipped: [] }]]);
    await v.suggestMemory();
    await v.applyMemory(false);
    t('wrote=false：提示"没有可应用的改动"，且不谎报成功', () => {
      assert.ok(toasts.some((x) => /没有可应用的改动/.test(x.msg)));
      assert.ok(!toasts.some((x) => /已更新 \d+ 个维度/.test(x.msg)));
      assert.strictEqual(v.memChanged.value, false);
    });
  })();

  await (async () => {
    calls = []; toasts.length = 0;
    const v = build([['/api/memory/suggest', { suggestions: SUGG, evidence: {} }]]);
    await v.suggestMemory();
    v.clearSuggestions();
    t('放弃：清空建议与提示', () => {
      assert.strictEqual(v.memSuggestions.value.length, 0);
      assert.strictEqual(v.memEvidence.value, '');
      assert.strictEqual(v.memSkipped.value, '');
    });
  })();

  await (async () => {
    const v = build([['/api/memory/snapshots', { snapshots: [] }]]);
    await v.loadSnapshots();
    t('没有历史版本：提示而不是空白', () => {
      assert.strictEqual(v.memSnapshots.value.length, 0);
      assert.ok(toasts.some((x) => /还没有历史版本/.test(x.msg)));
    });
  })();

  await (async () => {
    calls = []; toasts.length = 0; confirmAnswer = true; confirmMsgs.length = 0;
    const v = build([['/api/memory/suggest', { suggestions: SUGG, evidence: {} }],
                     ['/api/memory/import', { ok: true, wrote: true, applied: [], skipped: [], content: 'y',
                                              dimensions: 3, manual_kept: 1 }]]);
    await v.suggestMemory();
    await v.applyMemory(true);
    t('导入确认文案要说清人工行怎么处理', () => {
      const m = confirmMsgs[confirmMsgs.length - 1] || '';
      assert.ok(/4 条建议/.test(m), '要说清一共几条');
      assert.ok(/1 条是人工行/.test(m));
      assert.ok(/保护/.test(m), '开着保护时要说"将被保护不覆盖"');
    });

    confirmMsgs.length = 0; calls = [];
    await v.suggestMemory();
    v.memProtectManual.value = false;
    await v.applyMemory(true);
    t('关闭保护：确认文案改为"会被覆盖" + 请求体 false', () => {
      const m = confirmMsgs[confirmMsgs.length - 1] || '';
      assert.ok(/会被覆盖/.test(m), '★ 破坏性操作必须说清后果');
      const c = calls.find((x) => x.url.indexOf('/api/memory/import') >= 0);
      assert.strictEqual(JSON.parse(c.opts.body).protect_manual, false);
    });
  })();

  await (async () => {
    calls = []; toasts.length = 0; confirmAnswer = true;
    const v = build([['/api/memory/restore', { ok: true, content: 'A' }]]);
    await v.restoreSnapshot({ id: 1, when: 'w', why: 'x' });
    await v.restoreSnapshot({ id: 2, when: 'w', why: 'x' });
    t('还原可来回：连续两次都能发（当前版本会先存一份）', () => {
      const restores = calls.filter((c) => c.url.indexOf('/api/memory/restore') >= 0);
      assert.strictEqual(restores.length, 2);
      assert.deepStrictEqual(restores.map((c) => JSON.parse(c.opts.body).snapshot_id), [1, 2]);
    });
  })();

  await (async () => {
    const v = build([['/api/memory/suggest', { suggestions: SUGG, evidence: {} }]]);
    await v.suggestMemory();
    const once = v.decorateSuggestions(SUGG).map((s) => s.checked).join(',');
    const twice = v.decorateSuggestions(SUGG).map((s) => s.checked).join(',');
    t('默认勾选可重复计算（幂等，不累积状态）', () => assert.strictEqual(once, twice));
  })();

  console.log('\nM3-7 前端逻辑：通过 ' + ok + '，失败 ' + bad);
  process.exit(bad ? 1 : 0);
})();
