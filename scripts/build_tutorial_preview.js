/* 生成教程页静态预览（把 index.html 的真实 CSS + 真实 tutRender 渲染结果拼成独立页面）
 * 用法：node scripts/build_tutorial_preview.js
 * 产物：data/tutorial_preview.html（图片指向 data/tutorial_assets/ 本地文件）
 */
const fs = require('fs');
const path = require('path');

const ROOT = path.join(__dirname, '..');
const html = fs.readFileSync(path.join(ROOT, 'static', 'index.html'), 'utf8');

// 1) 抽真实 tutRender/esc
const A = '    // ---- M4 收尾：教程指导';
const B = '    async function loadTutorial(force) {';
const i = html.indexOf(A), j = html.indexOf(B, i);
if (i < 0 || j < 0) throw new Error('未找到 tutRender 代码块');
const M = new Function('ref', html.slice(i, j) + '\nreturn { tutRender };')((v) => ({ value: v }));

// 2) 抽页面 CSS（<style> 全量，保证与真实页面同源）
const styleM = html.match(/<style[^>]*>([\s\S]*?)<\/style>/);
if (!styleM) throw new Error('未找到 <style>');
let css = styleM[1];
// 预览页只需要教程页用到的规则，但全量更保真；补一个 body 兜底
css += `
  html, body { margin: 0; background: #eef2f7; }
  body { font-family: "Microsoft YaHei", "PingFang SC", system-ui, sans-serif; }
`;

// 3) 抽真实页面骨架（tutorial-view 那段），并用真实渲染结果填充
const vStart = html.indexOf('<div class="view tutorial-view"');
const vEnd = html.indexOf('<div class="view customize-view"', vStart);
if (vStart < 0 || vEnd < 0) throw new Error('未找到 tutorial-view 骨架');
let view = html.slice(vStart, vEnd);

// 4) md：图片路径改写成本地相对路径（预览页读磁盘）
const mdRaw = fs.readFileSync(path.join(ROOT, 'docs', 'v2.0', '演示文档', '演示文档.md'), 'utf8');
const md = mdRaw.replace(/!\[([^\]]*)\]\(([^)]+)\)/g, (m, alt, src) => {
  const name = src.replace(/\\/g, '/').split('/').pop();
  return `![${alt}](./tutorial_assets/${name})`;
});
const body = M.tutRender(md);

// 5) 用静态值替换 Vue 绑定，拼成独立页面（标题/副标题取真实文档值）
view = view
  .replace(/v-show="view === 'tutorial'"/, '')
  .replace(/<button class="bubbles tut-back" @click="tutBack\(\)"><span class="text">← 返回<\/span><\/button>/,
           '<button class="bubbles tut-back"><span class="text">← 返回</span></button>')
  .replace(/<button class="bubbles tut-reload" @click="loadTutorial\(true\)"><span class="text">↻ 重新读取<\/span><\/button>/,
           '<button class="bubbles tut-reload"><span class="text">↻ 重新读取</span></button>')
  .replace(/\{\{\s*tutTitle\s*\}\}/, 'CLI 工具接入教程 —— 以「微信读书」为例')
  .replace(/\{\{\s*tutPath\s*\}\}/, 'docs/v2.0/演示文档/演示文档.md')
  .replace(/\{\{\s*tutUpdated\s*\}\}/, new Date().toISOString().slice(0, 19).replace('T', ' '))
  .replace(/\{\{\s*tutImages\s*\}\}/, '7')
  .replace(/<span v-if="tutMissing">，⚠️ \{\{\s*tutMissing\s*\}\} 张未找到<\/span>/, '')
  .replace(/<div class="tut-body md-doc" v-if="!tutLoading" v-html="tutHtml"><\/div>/, '<div class="tut-body md-doc">' + body + '</div>')
  .replace(/<div class="tut-body" v-else><p class="tut-tip">正在读取教程…<\/p><\/div>/, '');

const page = `<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8" />
<title>预览 · CLI 工具接入教程（只读阅读页）</title>
<style>${css}</style>
</head>
<body>
${view}
</body>
</html>
`;

const out = path.join(ROOT, 'data', 'tutorial_preview.html');
fs.writeFileSync(out, page, 'utf8');
console.log('已生成：' + out + '（' + page.length + ' 字符，图片 ' + (body.match(/<img /g) || []).length + ' 张）');
