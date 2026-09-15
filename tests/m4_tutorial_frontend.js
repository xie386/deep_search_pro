/* M4 收尾 · 教程页 md 渲染器测试（无需浏览器）
 *
 * 思路同 m4a_frontend_logic.js：不重写逻辑，直接从 static/index.html **抽取真实的 tutRender/esc**
 * 函数体，在 Node 里跑真实输入，验证渲染结果与转义安全（XSS）。
 *
 * 运行：node tests/m4_tutorial_frontend.js
 */
const fs = require('fs');
const path = require('path');

const HTML = path.join(__dirname, '..', 'static', 'index.html');
const src = fs.readFileSync(HTML, 'utf8');

const A = '    // ---- M4 收尾：教程指导';
const B = '    async function loadTutorial(force) {';
const i = src.indexOf(A);
const j = src.indexOf(B, i);
if (i < 0 || j < 0) { console.error('未能在 index.html 中定位 tutRender 代码块'); process.exit(1); }
const block = src.slice(i, j);

const M = new Function('ref', block + '\nreturn { tutRender, esc };')((v) => ({ value: v }));
let pass = 0; const failed = [];
function check(label, cond, extra) {
  if (cond) { pass++; console.log('  [OK]   ' + label); }
  else { failed.push(label); console.log('  [FAIL] ' + label + (extra !== undefined ? '  |  ' + JSON.stringify(extra) : '')); }
}

console.log('=== M4 教程页 md 渲染器（真实代码） ===');

console.log('\n▸ 标题 / 段落 / 加粗 / 行内码 / 引用');
{
  const h = M.tutRender('# 大标题\n\n## 二级\n\n### 三级\n\n正文有 **加粗** 与 `行内码`。\n\n> 引用一句话\n');
  check('h1 渲染', h.indexOf('<h1 class="md-h">大标题</h1>') >= 0, h.slice(0, 120));
  check('h2 / h3 渲染', h.indexOf('<h2 class="md-h">二级</h2>') >= 0 && h.indexOf('<h3 class="md-h">三级</h3>') >= 0);
  check('段落 + 加粗 + 行内码', h.indexOf('<strong>加粗</strong>') >= 0 && h.indexOf('<code>行内码</code>') >= 0, h);
  check('引用块', h.indexOf('<blockquote class="md-quote">引用一句话</blockquote>') >= 0);
}

console.log('\n▸ 表格（教程的字段对照表）');
{
  const h = M.tutRender('| 字段 | 填什么 |\n| --- | --- |\n| 可执行名 | `weread` |\n');
  check('生成 table.md-table', h.indexOf('<table class="md-table">') >= 0, h);
  check('分隔行被吃掉（不渲染成 tr）', (h.match(/<tr>/g) || []).length === 2, (h.match(/<tr>/g) || []).length);
  check('单元格内容渲染', h.indexOf('<td>可执行名</td>') >= 0 && h.indexOf('<code>weread</code>') >= 0, h);
}

console.log('\n▸ 图片（图片路径必须原样保留，不能被转义）');
{
  const h = M.tutRender('![官网取 Key](/tutorial-assets/Snipaste_a.png)\n');
  check('img 标签生成', h.indexOf('<img src="/tutorial-assets/Snipaste_a.png"') >= 0, h);
  check('alt 保留', h.indexOf('alt="官网取 Key"') >= 0, h);
  check('URL 未被 HTML 转义', h.indexOf('&amp;') < 0, h);
}

console.log('\n▸ 代码块（含围栏语言标记 + 内容转义）');
{
  const h = M.tutRender('```powershell\nPS C:\\> npm install -g x\nif (a < b) { echo "hi" }\n```\n');
  check('pre/code 生成', h.indexOf('<pre class="md-pre"><code data-lang="powershell">') >= 0, h);
  check('代码里的 < > 被转义（不注入）', h.indexOf('a &lt; b') >= 0 && h.indexOf('<code data-lang="powershell">PS') >= 0, h);
  check('代码块内不生成段落', h.indexOf('<p>PS') < 0, h);
}

console.log('\n▸ 折叠块（长终端回显可收起）');
{
  const h = M.tutRender('<details>\n<summary>原始终端回显</summary>\n\n```\nx\n```\n\n</details>\n');
  check('details 白名单放行', h.indexOf('<details class="md-det">') >= 0 && h.indexOf('</details>') >= 0, h);
  check('summary 放行', h.indexOf('<summary>原始终端回显</summary>') >= 0, h);
}

console.log('\n▸ 列表');
{
  const h = M.tutRender('- 甲\n- 乙\n\n1. 一\n2. 二\n');
  check('无序列表', h.indexOf('<ul class="md-ul">') >= 0 && h.indexOf('<li>甲</li>') >= 0, h);
  check('有序列表', h.indexOf('<ol class="md-ol">') >= 0 && h.indexOf('<li>一</li>') >= 0, h);
  check('列表都正确闭合', (h.match(/<\/ul>/g) || []).length === 1 && (h.match(/<\/ol>/g) || []).length === 1, h);
}

console.log('\n▸ 安全：原始 HTML 注入被转义（只白名单放行 details/summary）');
{
  const h = M.tutRender('<script>alert(1)</script>\n\n<img src=x onerror="alert(2)">\n\n<iframe src="http://evil"></iframe>\n');
  check('script 被转义', h.indexOf('<script>') < 0 && h.indexOf('&lt;script&gt;') >= 0, h);
  check('img onerror 被转义', h.indexOf('<img src=x') < 0 && h.indexOf('&lt;img') >= 0, h);
  check('iframe 被转义', h.indexOf('<iframe') < 0 && h.indexOf('&lt;iframe') >= 0, h);
  check('只有 details/summary 在白名单里', (h.match(/<(details|summary)/g) || []).length === 0, h);
}

console.log('\n▸ 空输入与分隔线');
{
  check('空输入返回空串', M.tutRender('') === '' && M.tutRender(null) === '');
  const h = M.tutRender('上面\n\n---\n\n下面\n');
  check('hr 渲染', h.indexOf('<hr/>') >= 0, h);
}

console.log('\n▸ 真实文档整体渲染（拿 index.html 同源文档跑一遍结构断言）');
{
  const md = fs.readFileSync(path.join(__dirname, '..', 'docs', 'v2.0', '演示文档', '演示文档.md'), 'utf8')
    .replace(/!\[([^\]]*)\]\(([^)]+)\)/g, (m, alt, u) => `![${alt}](/tutorial-assets/${u.split(/[\\/]/).pop()})`);
  const h = M.tutRender(md);
  const n = (re) => (h.match(re) || []).length;
  check('渲染出 ≥8 个标题', n(/class="md-h"/g) >= 8, n(/class="md-h"/g));
  // 期望张数/块数从 md 现场推导（文档会长新内容，硬编码数字会随内容漂移）
  const wantImg = (md.match(/!\[[^\]]*\]\([^)]+\)/g) || []).length;
  const wantPre = Math.floor((md.match(/^\s*```/gm) || []).length / 2);
  check('渲染出全部图片（md 里 ' + wantImg + ' 张）', n(/<img /g) === wantImg, n(/<img /g));
  check('渲染出全部代码块（md 里 ' + wantPre + ' 个）', n(/md-pre/g) === wantPre, n(/md-pre/g));
  check('渲染出 ≥4 个表格', n(/md-table/g) >= 4, n(/md-table/g));
  check('折叠块存在', n(/md-det/g) === 1, n(/md-det/g));
  check('没有残留未解析的 markdown 图片语法', h.indexOf('![') < 0);
  check('没有残留未解析的表格分隔行', h.indexOf('| --- |') < 0);
}

console.log('\n---------------- 结果 ----------------');
console.log('通过 ' + pass + ' 项，失败 ' + failed.length + ' 项');
if (failed.length) { failed.forEach(f => console.log('  ✗ ' + f)); process.exit(1); }
console.log('M4 教程页渲染器全部通过 ✅');
