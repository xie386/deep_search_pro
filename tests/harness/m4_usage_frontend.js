/* M4-5 前端逻辑测试：本会话用量计数条（无需浏览器、无需服务）
 *
 * 与 m1c/m3 同一套抽取式写法：**从 front/index.html 抽真实代码块** + 极简 Vue shim 在 Node 里跑。
 * 重点钉住四条"错了用户就会看到错数字"的口径：
 *   ① 只在拿到响应后才显示（失败静默置空，绝不显示假数字）；
 *   ② 请求必须带 token 与**当前** thread_id；
 *   ③ 阈值：> threshold 才进 warn 态（D1 只提示不阻断）；
 *   ④ WS tool_start 触发的刷新必须**去抖**（连发工具不能把接口打爆）。
 *
 * 运行：node tests/m4_usage_frontend.js < /dev/null
 */
const fs = require('fs');
const path = require('path');
const assert = require('assert');

const src = fs.readFileSync(path.join(__dirname, '..', '..', 'front', 'index.html'), 'utf8').replace(/\r\n/g, '\n');
const A = '// ---------- M4-5：用量（本会话外部检索次数）----------';
const B = '// ---------- 定制助手：语音包（音色克隆，联动 voice_test） ----------';
const i = src.indexOf(A), j = src.indexOf(B);
if (i < 0 || j < 0 || j <= i) { console.error('未能在 index.html 中定位 M4-5 代码块'); process.exit(1); }
const block = src.slice(i, j);

const calls = [];
function makeFetch(routes) {
  return async function (url, opts) {
    calls.push({ url: String(url), opts: opts || {} });
    for (const r of routes) if (String(url).indexOf(r[0]) >= 0) return { ok: r[2] !== false, json: async () => r[1] };
    return { ok: true, json: async () => ({}) };
  };
}
function ref(v) { return { value: v }; }

let timers = [], cleared = [];
function build(routes, tid) {
  timers = []; cleared = [];
  // ★ computed 也要注入：页面有 computed 派生量（商品切换器 priceCurrent/priceItems）
  const computed = fn => ({ value: fn() });
  const factory = new Function('ref', 'computed', 'fetch', 'token', 'activeThread', 'encodeURIComponent',
    'setTimeout', 'clearTimeout',
    block + `
    return { usageInfo, loadUsage, scheduleUsageRefresh };`);
  return factory(ref, computed, makeFetch(routes), ref('tok-1'), ref(tid),
                 encodeURIComponent,
                 (fn, ms) => { const t = { fn, ms }; timers.push(t); return t; },
                 (t) => { cleared.push(t); });
}
let ok = 0, bad = 0;
function t(name, fn) {
  try { fn(); ok++; console.log('  ✅ ' + name); }
  catch (e) { bad++; console.log('  ❌ ' + name + ' —— ' + e.message); }
}
const RESP = { this_session: { total: 9, cost_calls: 4, by_kind: { '外部检索': 4 } },
               this_turn: { total: 3, cost_calls: 2 }, threshold: 3 };

(async () => {
  await (async () => {
    const v = build([['/api/usage', RESP]], 'th-9');
    await v.loadUsage();
    t('成功：解析出 cost/total/turn/threshold', () => {
      assert.strictEqual(v.usageInfo.value.cost, 4);
      assert.strictEqual(v.usageInfo.value.total, 9);
      assert.strictEqual(v.usageInfo.value.turn, 2);
      assert.strictEqual(v.usageInfo.value.threshold, 3);
    });
    t('请求带 token + 当前 thread_id（GET）', () => {
      const c = calls[0];
      assert.ok(c.url.indexOf('/api/usage') >= 0);
      assert.ok(c.url.indexOf('token=tok-1') >= 0 && c.url.indexOf('thread_id=th-9') >= 0);
      assert.ok(!c.opts.method || c.opts.method === 'GET');
    });
    t('超阈值才 warn（4 > 3）', () => {
      assert.ok(v.usageInfo.value.cost > v.usageInfo.value.threshold);
    });
  })();

  await (async () => {
    const v = build([['/api/usage', { this_session: { cost_calls: 1 }, this_turn: {}, threshold: 3 }]], 'x');
    await v.loadUsage();
    t('字段缺失时兜底为 0（不显示 undefined）', () => {
      assert.strictEqual(v.usageInfo.value.cost, 1);
      assert.strictEqual(v.usageInfo.value.total, 0);
      assert.strictEqual(v.usageInfo.value.turn, 0);
    });
    t('未超阈值则不进 warn', () => assert.ok(!(v.usageInfo.value.cost > v.usageInfo.value.threshold)));
  })();

  await (async () => {
    const v = build([['/api/usage', { detail: 'x' }, false]], 'y');
    await v.loadUsage();
    t('后端报错：置空而不是显示假数字', () => assert.strictEqual(v.usageInfo.value, null));
  })();

  await (async () => {
    const v = build([], 'z');
    const oldFetch = global.fetch;
    global.fetch = async () => { throw new Error('网络炸了'); };
    const v2 = (function () {
      // ★ computed 也要注入：页面有 computed 派生量（商品切换器 priceCurrent/priceItems）
  const computed = fn => ({ value: fn() });
  const factory = new Function('ref', 'computed', 'fetch', 'token', 'activeThread', 'encodeURIComponent',
        'setTimeout', 'clearTimeout', block + `
        return { usageInfo, loadUsage };`);
      return factory(ref, global.fetch, ref('t'), ref('z'), encodeURIComponent, () => ({}), () => {});
    })();
    await v2.loadUsage();
    global.fetch = oldFetch;
    t('网络异常：静默置空、不抛', () => assert.strictEqual(v2.usageInfo.value, null));
  })();

  await (async () => {
    const v = build([['/api/usage', RESP]], 'th-1');
    v.scheduleUsageRefresh(); v.scheduleUsageRefresh(); v.scheduleUsageRefresh();
    t('WS tool_start 触发的刷新会去抖（3 次只留 1 个待执行）', () => {
      assert.strictEqual(timers.length, 3);
      assert.ok(cleared.length >= 2, '前两次都该被 clearTimeout 掉（首次是清理一个空定时器）');
      assert.ok(timers[2] && !cleared.includes(timers[2]));
    });
    t('去抖延迟是 1.2s（与注释一致）', () => assert.strictEqual(timers[2].ms, 1200));
  })();

  t('模板：计数条在右侧「实时过程监控」上方（2026-09-28 用户实测后挪位置）', () => {
    const barIdx = src.indexOf('M4-5：本会话外部检索计数');
    const monIdx = src.indexOf('实时过程监控');
    const colIdx = src.indexOf('class="col-right"');
    assert.ok(barIdx > 0 && monIdx > barIdx, '计数条必须在"实时过程监控"之前');
    assert.ok(colIdx > 0 && barIdx > colIdx, '计数条应在右栏 col-right 内');
  });
  t('★ 还原请求体字段名必须是 snapshot_id（发 {id} 会被后端 422 拒）', () => {
    assert.ok(src.indexOf('JSON.stringify({ snapshot_id: s.id })') >= 0,
              '后端 MemoryRestoreReq 的字段是 snapshot_id；用户实测发 {id} → 422');
  });
  t('成本卡放在两个角色分支之外（个人版也要看得到）', () => {
    const grid = src.indexOf('class="stat-grid"');
    const card = src.indexOf('class="stat-card c5"');
    const gridEnd = src.indexOf('</div>', src.indexOf('</template>', grid));
    assert.ok(card > grid, '卡片要在 stat-grid 内');
    assert.ok(card > src.indexOf("class=\"stat-card c1\"", src.indexOf('v-else')), '要在角色分支之后');
  });
  t('模板：超阈值加 warn 类 + 手动刷新按钮', () => {
    assert.ok(src.indexOf("'warn': usageInfo.cost > usageInfo.threshold") >= 0
              || src.indexOf('warn: usageInfo.cost > usageInfo.threshold') >= 0);
    assert.ok(src.indexOf('@click="loadUsage()"') >= 0);
  });
  t('模板：本轮次数只在有值时显示', () => {
    assert.ok(/v-if="usageInfo\.turn"/.test(src));
  });
  t('setup 导出：新标识符都在 return 里（否则模板静默失效）', () => {
    const ret = src.slice(src.lastIndexOf('return {'));       // 取到文件尾，别用固定窗口（会截断）
    for (const n of ['usageInfo', 'loadUsage', 'scheduleUsageRefresh']) assert.ok(ret.indexOf(n) >= 0, n);
  });
  t('WS 钩子接在 monitor_event 分支里（tool_start → 安排刷新）', () => {
    assert.ok(src.indexOf("if (p.event === 'tool_start') scheduleUsageRefresh();") >= 0);
  });

  console.log('\nM4-5 前端逻辑：通过 ' + ok + '，失败 ' + bad);
  process.exit(bad ? 1 : 0);
})();
