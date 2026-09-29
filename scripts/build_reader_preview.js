/* 生成「周报应用内阅读页」静态预览
 * 用 index.html 里的**真实 CSS** + **真实阅读页 markup** + **真实周报正文**（经真实 tutRender 渲染）
 * 输出一张可直接截图的页面，用来肉眼验收样式是否落在该落的地方（返回按钮位置、.md-doc 排版）。
 * 用法：node scripts/build_reader_preview.js
 * 产物：front/tutorial/previews/reader_preview.html
 * 说明：静态预览里 Vue 指令跑不了 —— 只做三处「预览专用替换」并在此注明：
 *   ① 去掉 v-show / v-if（保留元素本身）
 *   ② {{ readerTitle }} / {{ readerMeta }} 换成样例值
 *   ③ 正文容器塞进真实渲染结果
 * 自检：预览用到的每个 class 必须能在真实 CSS 里找到（除白名单），否则报错退出。
 */
const fs = require('fs');
const path = require('path');

const root = path.resolve(__dirname, '..');
const html = fs.readFileSync(path.join(root, 'front', 'index.html'), 'utf8');
const css = (html.match(/<style>([\s\S]*?)<\/style>/) || [, ''])[1];
if (!css) { console.error('❌ 抽不到 <style>'); process.exit(1); }

// ① 真实阅读页 markup（从注释锚点到它自己的 </div> 结束）
const A = '<!-- 周报应用内阅读页（M5c）';
const start = html.indexOf(A);
const B = "v-show=\"view === 'customize'\"";
const end = html.indexOf(B, start);
if (start < 0 || end < 0) { console.error('❌ 定位不到阅读页 markup'); process.exit(1); }
let view = html.slice(start, html.lastIndexOf('</div>', end) + '</div>'.length);
view = view.replace(/ v-show="[^"]*"/g, '');       // 预览：去掉 v-show

// ② 真实 tutRender（复用教程页那段真实代码）
const i1 = html.indexOf('    function esc(s) {');
const j1 = html.indexOf('    async function loadTutorial(force, kind) {', i1);
if (i1 < 0 || j1 < 0) { console.error('❌ 定位不到 tutRender/esc（index.html 锚点又变了）'); process.exit(1); }
const R = new Function('ref', html.slice(i1, j1) + '\nreturn { tutRender, esc };')((v) => ({ value: v }));

// ③ 真实周报正文：output/ 下**最大**那份（目录里也有几十字节的测试存根）
let best = null, bestSize = -1;
const outDir = path.join(root, 'output');
if (fs.existsSync(outDir)) {
  for (const u of fs.readdirSync(outDir)) {
    const d = path.join(outDir, u);
    if (!fs.statSync(d).isDirectory()) continue;
    for (const f of fs.readdirSync(d)) {
      if (!f.endsWith('.md')) continue;
      const p = path.join(d, f), sz = fs.statSync(p).size;
      if (sz > bestSize) { bestSize = sz; best = p; }
    }
  }
}
if (!best) { console.error('❌ output/ 下没有周报 md，无法预览'); process.exit(1); }
const md = fs.readFileSync(best, 'utf8');
const body = R.tutRender(md);
const title = path.basename(best, '.md');

// 应用真实值
view = view.replace(/\{\{\s*readerTitle\s*\}\}/g, title)
           .replace(/\{\{\s*readerMeta\s*\}\}/g,
             '10 条 · 2026-09-24 12:06:34 · 自 output/ 读取，只读浏览（不改文件）')
           .replace(/(<div class="tut-body md-doc"[^>]*>)(<\/div>)/, (m, open) => open + body + '</div>')
           .replace(/(<div class="tut-body"[^>]*v-else[^>]*>[\s\S]*?<\/div>)/, '')  // 去掉 loading 占位块
           .replace(/ v-if="[^"]*"| v-else/g, '');

const page = `<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8" /><title>周报应用内阅读 · 静态预览</title>
<style>${css}
  .pv-note { position: fixed; right: 14px; bottom: 12px; font: 12px/1.5 system-ui, "Microsoft YaHei";
             background: #111a06; color: #88F529; padding: 6px 12px; border-radius: 8px; opacity: .88; }
</style></head>
<body>
${view}
<div class="pv-note">静态预览：真实 CSS + 真实阅读页 markup + 真实周报正文（${title}）</div>
</body></html>`;

fs.mkdirSync(path.join(root, 'data'), { recursive: true });
const out = path.join(root, 'front', 'tutorial', 'previews', 'reader_preview.html');
fs.writeFileSync(out, page, 'utf8');

// 自检：预览 markup 里的 class 必须来自真实 CSS
const used = new Set();
for (const m of page.matchAll(/class="([^"]+)"/g)) m[1].split(/\s+/).forEach((c) => c && used.add(c));
['pv-note'].forEach((c) => used.delete(c));
const missing = [...used].filter((c) => !new RegExp('\\.' + c.replace(/[-.]/g, '\\$&') + '[\\s,{.:]').test(css));
console.log('预览 class 数：', used.size, '| 正文渲染：', body.length, '字符');
if (missing.length) { console.error('❌ 以下 class 在真实 CSS 里找不到：', missing); process.exit(1); }
console.log('✅ class 自检通过（全部来自真实 CSS）');
console.log('产物：front/tutorial/previews/reader_preview.html', (page.length / 1024).toFixed(1) + ' KB');
