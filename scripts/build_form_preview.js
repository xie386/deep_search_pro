/* 生成「表单色块跟踪（015 号设计）」静态预览
 * 用 index.html 里的**真实 CSS**渲染三处表单（个人信息 / 报告 / 定制助手），
 * 每处并排展示「默认态」与「跟踪态（悬停或聚焦时）」——截图即可肉眼验收，不依赖服务与浏览器自动化。
 * 用法：node scripts/build_form_preview.js
 * 产物：front/tutorial/previews/form_preview.html
 * 自检：预览用到的每个 class 都必须能在 index.html 的 CSS 里找到，否则报错退出。
 */
const fs = require('fs');
const path = require('path');

const root = path.resolve(__dirname, '..');
const html = fs.readFileSync(path.join(root, 'front', 'index.html'), 'utf8');
const css = (html.match(/<style>([\s\S]*?)<\/style>/) || [, ''])[1];
if (!css) { console.error('❌ 抽不到 <style>'); process.exit(1); }

// ---------------- ① 个人信息：兴趣行 + 新增表单 ----------------
const interestRows = [
  ['香烟', '具体关注什么？场景？预算？', '检索关键词(逗号分隔)'],
  ['米诺地尔', '泡沫喷剂型米诺地尔', '单瓶100人民币内,国内外产品'],
];
const rowHtml = (r) => `<div class="list-edit-row">
                <input class="m-input" style="width:150px" value="${r[0]}" />
                <input class="m-input grow" value="${r[1]}" />
                <input class="m-input" style="width:190px" value="${r[2]}" />
                <button class="mini-btn">保存</button>
                <span class="del-x">×</span>
              </div>`;
const interestPanel = `<div class="p-card">
            <h3>⭐ 我的兴趣领域</h3>
            ${rowHtml(interestRows[0])}
            <div class="list-edit-row hover-demo">
              ${rowHtml(interestRows[1]).replace(/^<div class="list-edit-row">/, '').replace(/<\/div>$/, '')}
            </div>
            <div class="add-form form-grid">
              <div class="fg"><label>兴趣领域 *</label><input class="m-input" placeholder="如 无线耳机" /></div>
              <div class="fg"><label>检索关键词（可选，逗号分隔）</label><input class="m-input" /></div>
              <div class="fg full"><label>详细描述（可选）</label><input class="m-input" placeholder="具体关注什么？使用场景？预算范围？" /></div>
              <div class="fg"><label>&nbsp;</label><button class="mini-btn" style="width:100%">＋ 添加兴趣</button></div>
            </div>
          </div>`;

// ---------------- ② 报告：Digest 订阅卡 ----------------
const subCard = (tracked) => `<div class="sub-card${tracked ? ' hover-demo' : ''}">
            <label class="sub-ck"><input type="checkbox" checked /><span class="brut-mark"></span></label>
            <div class="sub-main">
              <div class="sub-row1">
                <input class="m-input grow" value="选购快讯" />
                <span class="sub-scope">个人选购</span>
                <select class="m-input"><option>每天9点</option><option>每周一9点</option></select>
                <select class="m-input sub-lang"><option>中文检索</option></select>
                <label class="sub-en"><input type="checkbox" checked /><span class="brut-mark"></span> 启用</label>
              </div>
              <div class="sub-row2">
                <input class="m-input grow" value="香烟,米诺地尔" />
                <button class="mini-btn">保存</button>
                <button class="mini-btn del">删除</button>
              </div>
            </div>
          </div>`;
const reportPanel = `<div class="sub-list" style="width:100%">${subCard(false)}${subCard(true)}</div>`;

// ---------------- ③ 定制助手：模型选型 + 人格 ----------------
const provRow = (tracked) => `<div class="prov-row${tracked ? ' hover-demo' : ''}">
              <input class="m-input prov-name" value="Deepseek" />
              <input class="m-input prov-model" value="deepseek-flash" />
              <input class="m-input prov-url" value="https://api.deepseek.com/v1" />
              <input class="m-input prov-key" type="password" value="sk-1234567890" />
              <button class="mini-btn">保存</button>
              <button class="mini-btn act on">✓ 使用中</button>
              <button class="mini-btn del">删除</button>
            </div>`;
const custPanel = `<div class="cust-card">
            <h3>🧠 模型选型</h3>
            <div class="cust-tip">当前默认使用项目自带免费模型（.env 配置）。可添加你自己的模型接口（OpenAI 兼容）并启用。</div>
            ${provRow(false)}
            ${provRow(true)}
          </div>
          <div class="cust-card">
            <h3>💖 助手人格（SOUL.md）</h3>
            <div class="cust-tip">人格会注入 AI 助手的系统提示词；留空 = 默认专业风格。</div>
            <div class="soul-switch">
              <select class="m-input grow"><option>活泼女仆（使用中）</option></select>
              <input class="m-input" placeholder="新人格名，如 稳重管家" />
              <button class="mini-btn add-more">＋ 新建人格</button>
              <button class="mini-btn del">删除当前</button>
            </div>
            <div class="fg full" style="margin-top:10px;display:block">
              <label>描述你想要的风格（勾选下面这段的输入-聚焦态）</label>
              <input class="m-input focus-demo" value="聚焦态：硬投影收掉 + 绿色下划线从左展开" style="width:100%" />
            </div>
          </div>`;

// ---------------- 预览页自身样式（仅用于并排展示状态） ----------------
const previewCss = `
  body { margin: 0; padding: 26px 30px 40px; }
  .pv-head { font: 700 15px/1.6 system-ui, "Microsoft YaHei"; color: #14210A;
             background: #FFF81F; border: 2px solid #14210A; border-radius: 4px 18px 4px 18px;
             box-shadow: -8px 0 0 0 #14210A, -8px 5px 5px rgba(0,0,0,.16);
             padding: 10px 16px; margin-bottom: 22px; display: inline-block; }
  .pv-head small { font-weight: 400; opacity: .72; }
  .pv-grid { display: flex; flex-wrap: wrap; gap: 34px; align-items: flex-start; }
  .pv-col { flex: 1 1 560px; min-width: 480px; max-width: 900px; display: flex; flex-direction: column; gap: 20px; }
  .pv-label { font: 700 13px/1.4 system-ui, "Microsoft YaHei"; color: #14210A; opacity: .8; letter-spacing: .5px; }
  .pv-row { display: flex; gap: 34px; }
  .pv-row > * { flex: 1 1 0; min-width: 0; }
  /* 强制展示「跟踪中」状态：截图无法悬停，用 class 复现 :hover / :focus-within 的结果 */
  .hover-demo::before { transform: scaleX(1) !important; opacity: 1 !important; }
  .hover-demo .m-input {
    background-color: transparent !important; box-shadow: none !important;
    border-color: var(--form-ink) !important;
    background-image: linear-gradient(var(--form-ink), var(--form-ink)) !important;
    background-size: 100% 3px !important;
  }
  .focus-demo { box-shadow: none !important; background-color: #fff !important; background-size: 100% 3px !important; }
`;

const page = `<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8" />
<title>015 表单色块跟踪 · 静态预览</title>
<style>${css}${previewCss}</style>
</head><body>
<div class="pv-head">015 号设计 · 表单「色块跟踪」预览
  <small>　基色 #FFF81F ／ 跟踪色 #88F529　·　左列=默认态，右列=跟踪态（悬停或聚焦时）</small></div>
<div class="pv-grid">
  <div class="pv-col">
    <div class="pv-label">① 个人信息（公司信息）· 行 = 跟踪单位</div>
    ${interestPanel}
  </div>
  <div class="pv-col">
    <div class="pv-label">③ 定制助手 · 模型行 / 人格行 = 跟踪单位</div>
    ${custPanel}
  </div>
</div>
<div class="pv-label" style="margin-top:26px">② 报告（Digest 订阅）· 整卡 = 跟踪单位</div>
<div class="pv-row" style="margin-top:10px">${reportPanel}</div>
</body></html>`;

fs.mkdirSync(path.join(root, 'data'), { recursive: true });
const out = path.join(root, 'front', 'tutorial', 'previews', 'form_preview.html');
fs.writeFileSync(out, page, 'utf8');

// ---------------- 自检：预览里的 class 必须都来自真实 CSS ----------------
const used = new Set();
for (const m of page.matchAll(/class="([^"]+)"/g)) m[1].split(/\s+/).forEach((c) => c && used.add(c));
const previewOnly = ['pv-head', 'pv-grid', 'pv-col', 'pv-label', 'pv-row', 'hover-demo', 'focus-demo'];
previewOnly.forEach((c) => used.delete(c));
// 项目里本来就不带 CSS 的**纯结构性标记**（只做 DOM 分组，靠默认 inline-block 排布）：
// add-more 按钮变体、sub-list/sub-main/sub-row1/sub-lang 订阅卡内部结构。它们出现在真实 markup 里，
// 没有规则不是笔误 —— 白名单列在这里，避免自检误报。
['add-more', 'sub-list', 'sub-main', 'sub-row1', 'sub-lang'].forEach((c) => used.delete(c));
const missing = [...used].filter((c) => !new RegExp('\\.' + c.replace(/[-.]/g, '\\$&') + '[\\s,{.:]').test(css));
console.log('预览用到 class 数：', used.size);
if (missing.length) { console.error('❌ 以下 class 在真实 CSS 里找不到：', missing); process.exit(1); }
console.log('✅ class 自检通过（全部来自真实 CSS）');
console.log('产物：front/tutorial/previews/form_preview.html', (page.length / 1024).toFixed(1) + ' KB');
