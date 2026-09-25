/* M5c 周报应用内阅读 · 前端逻辑测试（无需浏览器）
 *
 * 思路同 m4a_frontend_logic.js / m4_tutorial_frontend.js：不重写逻辑，直接从 static/index.html
 * **抽取真实代码**跑：
 *   ① 教程页那段 `esc` + `tutRender`（阅读器复用的就是它，必须真跑，不能另写一个渲染器）
 *   ② 本次新增的 `openReport / readerBack` 阅读器逻辑
 * 再补几条对**模板**的静态契约断言（按钮的 v-if / @click 绑定）。
 *
 * 运行：node tests/m5c_report_reader_frontend.js
 */
const fs = require('fs');
const path = require('path');

const HTML = path.join(__dirname, '..', 'static', 'index.html');
// ⚠️ 统一按 LF 比较：下面的锚点串里含换行符，而工作区的 index.html 是否 CRLF 取决于 git 的
//    core.autocrlf（本项目为 true → 工作区是 CRLF）。不归一化的话锚点会随「文件被谁写过」时通时不通。
const raw = fs.readFileSync(HTML, 'utf8');
const src = raw.replace(/\r\n/g, '\n');

// ① 教程页渲染器（阅读器复用它）
const A1 = '    // ---- M4 收尾：教程指导';
const B1 = '    async function loadTutorial(force) {';
// ② 本次新增的阅读器逻辑
const A2 = '    // ---- M5c：周报应用内阅读';
const B2 = '    function scrollToBottom() {';
const i1 = src.indexOf(A1), j1 = src.indexOf(B1, i1);
const i2 = src.indexOf(A2), j2 = src.indexOf(B2, i2);
if (i1 < 0 || j1 < 0) { console.error('未能在 index.html 中定位 tutRender 代码块'); process.exit(1); }
if (i2 < 0 || j2 < 0) { console.error('未能在 index.html 中定位周报阅读器代码块'); process.exit(1); }
const rendererBlock = src.slice(i1, j1);
const readerBlock = src.slice(i2, j2);

let pass = 0; const failed = [];
function check(label, cond, extra) {
  if (cond) { pass++; console.log('  [OK]   ' + label); }
  else { failed.push(label); console.log('  [FAIL] ' + label + (extra !== undefined ? '  |  ' + JSON.stringify(extra) : '')); }
}

/* ---------------- 测试替身：ref / go / showToast / fetch ---------------- */
const toasts = [];
const shown = [];                       // showToast(msg, isError)
let goLog = [];
let fetchCalls = [];
let nextResponses = [];                 // 每次 fetch 消耗一个 {ok,status,text,json}

function makeEnv() {
  toasts.length = 0; goLog = []; fetchCalls = []; nextResponses = [];
  const ref = (v) => ({ value: v });
  const go = (v) => { goLog.push(v); };
  const showToast = (msg, isErr) => { toasts.push(String(msg)); shown.push({ msg: String(msg), err: !!isErr }); };
  const fetch = (url) => {
    fetchCalls.push(url);
    const r = nextResponses.shift() || { ok: true, status: 200, text: '# 兜底\n' };
    return Promise.resolve({
      ok: r.ok !== false,
      status: r.status || (r.ok === false ? 500 : 200),
      text: () => Promise.resolve(r.text !== undefined ? r.text : ''),
      json: () => Promise.resolve(r.json !== undefined ? r.json : {}),
    });
  };
  const api = new Function('ref', 'go', 'showToast', 'fetch',
    rendererBlock + '\n' + readerBlock +
    '\nreturn { tutRender, openReport, readerBack, readerHtml, readerTitle, readerMeta, readerItem, readerLoading, readerKey };'
  )(ref, go, showToast, fetch);
  return api;
}

const mkReport = (over) => Object.assign({
  id: 7, title: '快讯周报 2026-09-24', item_count: 10, created_at: '2026-09-24 12:06:34',
  download_md: '/api/download?token=tok1&name=%E5%BF%AB%E8%AE%AF%E5%91%A8%E6%8A%A5_20260924_120634.md',
}, over || {});

const MD = '# 快讯周报 2026-09-24\n\n扫描 20 条候选 / 精选 10 条\n\n## 一、今日速览\n\n- **香烟**：某新品上市\n- **米诺地尔**：竞品降价\n';

(async () => {
  console.log('=== M5c 周报应用内阅读（真实代码） ===');

  console.log('\n▸ 打开：跳页 + 取数 URL 与下载**同源**');
  {
    const api = makeEnv();
    const r = mkReport();
    nextResponses.push({ ok: true, text: MD });
    const out = await api.openReport(r);
    check('立刻跳到阅读页（go("reader")）', goLog.indexOf('reader') === 0, goLog);
    check('请求 URL === r.download_md（没有另造第二个接口）', fetchCalls[0] === r.download_md, fetchCalls);
    check('返回 {ok:true} 且带正文字数', out.ok === true && out.chars === MD.length, out);
    check('readerItem 记住了当前周报（供「↻ 重新读取」用）', api.readerItem.value && api.readerItem.value.id === 7);
  }

  console.log('\n▸ 渲染：用的是教程页那套真实 tutRender');
  {
    const api = makeEnv();
    nextResponses.push({ ok: true, text: MD });
    await api.openReport(mkReport());
    const h = api.readerHtml.value;
    check('h1 渲染成 md-h', h.indexOf('<h1 class="md-h">快讯周报 2026-09-24</h1>') >= 0, h.slice(0, 120));
    check('h2 渲染', h.indexOf('<h2 class="md-h">一、今日速览</h2>') >= 0, h);
    check('列表项渲染', h.indexOf('<ul class="md-ul">') >= 0 && h.indexOf('<li>') >= 0, h);
    check('加粗渲染', h.indexOf('<strong>香烟</strong>') >= 0, h);
    check('没有残留 markdown 标题符号', h.indexOf('# 快讯周报') < 0, h);
    check('标题取周报标题', api.readerTitle.value === '快讯周报 2026-09-24', api.readerTitle.value);
    check('元信息含条数/时间/只读说明',
      api.readerMeta.value.indexOf('10 条') >= 0 && api.readerMeta.value.indexOf('2026-09-24 12:06:34') >= 0
      && api.readerMeta.value.indexOf('只读') >= 0, api.readerMeta.value);
  }

  console.log('\n▸ loading 生命周期');
  {
    const api = makeEnv();
    nextResponses.push({ ok: true, text: MD });
    const p = api.openReport(mkReport());
    check('请求期间 readerLoading = true', api.readerLoading.value === true);
    await p;
    check('结束后 readerLoading = false', api.readerLoading.value === false);
  }

  console.log('\n▸ 缓存：同 URL 不重复读盘，换 URL 必重取');
  {
    const api = makeEnv();
    const r1 = mkReport();
    nextResponses.push({ ok: true, text: MD });
    await api.openReport(r1);
    check('首次确实发了一次请求', fetchCalls.length === 1, fetchCalls);
    const again = await api.openReport(r1);
    check('同 URL 二次打开命中缓存（不再请求）', fetchCalls.length === 1 && again.cached === true, again);
    nextResponses.push({ ok: true, text: MD });
    await api.openReport(r1, true);
    check('force=true（↻ 重新读取）强制重取', fetchCalls.length === 2, fetchCalls);
    // 同名周报但不同文件 —— 缓存键是 URL，不是标题
    const r2 = mkReport({ id: 8, download_md: '/api/download?token=tok1&name=%E5%BF%AB%E8%AE%AF_20260911_122205.md' });
    nextResponses.push({ ok: true, text: '# 另一份同名周报\n' });
    await api.openReport(r2);
    check('同名不同文件必须重取（缓存键 = URL）', fetchCalls.length === 3 && api.readerHtml.value.indexOf('另一份同名周报') >= 0, fetchCalls);
  }

  console.log('\n▸ 异常路径');
  {
    const api = makeEnv();
    const out = await api.openReport(mkReport({ download_md: '' }));
    check('没有 md 文件：不发请求 + 明确提示', fetchCalls.length === 0 && toasts.some(t => t.indexOf('md 文件') >= 0), toasts);
    check('返回 {ok:false,error:"no-md"}', out.ok === false && out.error === 'no-md', out);
    check('没有 md 时也跳到了阅读页（页面空态由 loading/空串呈现）', goLog.indexOf('reader') === 0, goLog);
  }
  {
    const api = makeEnv();
    nextResponses.push({ ok: false, status: 404, json: { detail: '文件不存在' } });
    const out = await api.openReport(mkReport());
    check('HTTP 非 2xx：优先用后端 detail 提示', toasts.indexOf('文件不存在') >= 0, toasts);
    check('失败时正文保持空（不残留上一份）', api.readerHtml.value === '');
    check('返回 {ok:false,error:"http-404"}', out.ok === false && out.error === 'http-404', out);
  }
  {
    const api = makeEnv();
    const ref0 = makeEnv;   // 造一个会抛异常的 fetch：直接改 nextResponses 不支持，故用 no-url 之外的路径
    const api2 = new Function('ref', 'go', 'showToast', 'fetch',
      rendererBlock + '\n' + readerBlock +
      '\nreturn { openReport, readerHtml, readerLoading };'
    )((v) => ({ value: v }), (v) => goLog.push(v), (m) => toasts.push(String(m)),
      () => Promise.reject(new Error('network down')));
    const out = await api2.openReport(mkReport());
    check('网络异常：返回 error=exception 且提示含「读取出错」', out.ok === false && out.error === 'exception'
      && toasts.some(t => t.indexOf('读取出错') >= 0), { out, toasts });
    check('网络异常后 loading 归位 false', api2.readerLoading.value === false);
  }

  console.log('\n▸ 返回：回到报告页并清掉当前项');
  {
    const api = makeEnv();
    nextResponses.push({ ok: true, text: MD });
    await api.openReport(mkReport());
    api.readerBack();
    check('readerBack() → go("reports")（★ 视图名是复数！写成单数 report 会跳到不存在的视图 → 整页空白，2026-09-25 用户报障）',
      goLog[goLog.length - 1] === 'reports', goLog);
    check('readerItem 被清空', api.readerItem.value === null);
  }

  console.log('\n▸ 防回退（代码级）');
  {
    check('阅读器复用 tutRender（而不是自带一个渲染器）', readerBlock.indexOf('readerHtml.value = tutRender(') >= 0);
    check('阅读路径不触发下载（阅读段里不出现 saveFromUrl）', readerBlock.indexOf('saveFromUrl') < 0);
    check('阅读器不自己拼 URL（必须用后端给的 download_md）', /\/api\/download\?token=/.test(readerBlock) === false);
  }

  console.log('\n▸ 模板契约（静态断言）');
  {
    const tbl = raw.slice(raw.indexOf('<table class="mdt reports-tbl">'), raw.indexOf('</table>', raw.indexOf('<table class="mdt reports-tbl">')));
    check('操作列有「阅读」按钮且绑到 openReport', /@click="openReport\(r\)"/.test(tbl) && tbl.indexOf('>阅读<') >= 0, tbl.slice(0, 80));
    check('阅读按钮只在有 md 时出现（与 MD↓ 同一条件）', (tbl.match(/v-if="r\.download_md"/g) || []).length === 2,
      (tbl.match(/v-if="r\.download_md"/g) || []).length);
    check('MD↓ / PDF↓ 两个下载按钮仍在（没被替换掉）', tbl.indexOf('>MD↓<') >= 0 && tbl.indexOf('>PDF↓<') >= 0);
    check('PDF 按钮闭合标签已修正为 </button>（原先误写成 </a>）',
      /<span class="dl-text">PDF↓<\/span>\s*<\/button>/.test(tbl.replace(/\r\n/g, '\n')), 'tag mismatch');
    const view = raw.slice(raw.indexOf('<!-- 周报应用内阅读页（M5c）'), raw.indexOf("v-show=\"view === 'customize'\""));
    check('阅读页复用教程页样式壳（.tutorial-view + .tut-*）',
      view.indexOf('class="view tutorial-view"') >= 0 && view.indexOf('tut-body md-doc') >= 0, view.slice(0, 60));
    check('左上角有返回按钮', /class="bubbles tut-back" @click="readerBack\(\)"/.test(view), view.slice(0, 60));
    check('直接刷新 #/reader 会被送回报告页（空阅读页守卫）',
      /if \(view\.value === 'reader'\) \{ view\.value = 'report'; showToast\(/.test(src));
    check('setup() 导出里含模板用到的标识符',
      ['openReport', 'readerBack', 'readerHtml', 'readerLoading', 'readerTitle', 'readerMeta', 'readerItem']
        .every(k => raw.indexOf('\n      ' + k + ', ') >= 0 || new RegExp('[, ]' + k + ',').test(raw.slice(raw.lastIndexOf('return {')))));
  }

  console.log('\n▸ 真实周报文档整体渲染（拿 output/ 下真实 md 跑一遍）');
  {
    const outDir = path.join(__dirname, '..', 'output');
    // 挑**最大**的那份真实周报：output/ 下也有几十字节的测试存根（如 m2tester 的 M3 导出测试），
    // 拿存根断言"有表格/列表"会误判成渲染失败。体积才是"真周报"的判据。
    let real = null, realPath = '';
    if (fs.existsSync(outDir)) {
      let best = -1;
      for (const user of fs.readdirSync(outDir)) {
        const d = path.join(outDir, user);
        if (!fs.statSync(d).isDirectory()) continue;
        for (const f of fs.readdirSync(d)) {
          if (!f.endsWith('.md')) continue;
          const p = path.join(d, f), sz = fs.statSync(p).size;
          if (sz > best) { best = sz; realPath = p; }
        }
      }
      if (realPath) real = fs.readFileSync(realPath, 'utf8');
    }
    if (!real) { console.log('  [SKIP] output/ 下暂时没有真实周报 md'); }
    else {
      const api = makeEnv();
      nextResponses.push({ ok: true, text: real });
      await api.openReport(mkReport());
      const h = api.readerHtml.value;
      const n = (re) => (h.match(re) || []).length;
      console.log('    （夹具：' + path.basename(realPath) + '，' + real.length + ' 字符）');
      check('渲染出 ≥3 个标题', n(/class="md-h"/g) >= 3, n(/class="md-h"/g));
      check('渲染出列表或表格', n(/md-ul|md-ol|md-table/g) >= 1, n(/md-ul|md-ol|md-table/g));
      check('没有残留未解析的表格分隔行', h.indexOf('| --- |') < 0);
      check('正文完整来自文件（HTML 明显长于原文的一半）', h.length > real.length / 2, { html: h.length, md: real.length });
    }
  }

  console.log('\n---------------- 结果 ----------------');
  console.log('通过 ' + pass + ' 项，失败 ' + failed.length + ' 项');
  if (failed.length) { failed.forEach(f => console.log('  ✗ ' + f)); process.exit(1); }
  console.log('M5c 周报应用内阅读全部通过 ✅');
})();
