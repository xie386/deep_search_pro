/**
 * 登录页背景「Typography Vortex」harness
 * 设计契约来自 threeui.com/text-animation/typography-vortex（原源码受保护 ✗ → 按效果复刻 ✓）
 * 重点测**纯函数**：环增长率 / 指针消散衰减 / 吸入缓动 / 像素比封顶 —— 这些错了画面就"看着不对但说不清"。
 */
const fs = require('fs');
const path = require('path');
const ROOT = path.join(__dirname, '..', '..');
const html = fs.readFileSync(path.join(ROOT, 'front', 'index.html'), 'utf8');
const mod = require(path.join(ROOT, 'front', 'login_vortex.js'));
const src = fs.readFileSync(path.join(ROOT, 'front', 'login_vortex.js'), 'utf8');

let ok = 0, bad = 0;
function t(name, cond, note) {
  if (cond) { ok++; console.log('  ✅ ' + name); }
  else { bad++; console.log('  ❌ ' + name + (note ? '  → ' + note : '')); }
}

console.log('\n[登录页背景 Typography Vortex]');

// ---- 纯函数（真跑）----
t('ringGrowth 契约默认值 = 1.21', mod.DEFAULTS.ringGrowth === 1.21);
t('suctionDuration 契约默认值 = 920ms', mod.DEFAULTS.suctionDuration === 920);
t('像素比自适应但封顶 ≤ 2（契约 adaptive）',
  mod.clampDpr(3, 2) === 2 && mod.clampDpr(1.5, 2) === 1.5 && mod.clampDpr(0, 2) === 1 && mod.clampDpr('x', 2) === 1);
t('环半径按 ringGrowth 逐环相乘（0/1/2 环）',
  mod.ringScale(0, 1.21) === 1 && Math.abs(mod.ringScale(2, 1.21) - 1.4641) < 1e-9);
t('指针消散：中心最强、边缘归零、单调递减',
  mod.dissolveAlpha(0, 100) === 1 && mod.dissolveAlpha(100, 100) === 0 && mod.dissolveAlpha(150, 100) === 0
  && mod.dissolveAlpha(30, 100) > mod.dissolveAlpha(70, 100));
t('吸入缓动 easeOutCubic：0 → 1，中点 > 0.5（前快后慢）',
  mod.suctionProgress(0, 920) === 0 && mod.suctionProgress(920, 920) === 1
  && mod.suctionProgress(460, 920) > 0.5 && mod.suctionProgress(9999, 920) === 1);
t('字形角度：确定性（同参数同结果）+ 相邻环反向（剪切感）',
  mod.glyphAngle(3, 1, 2.5, 1, 22) === mod.glyphAngle(3, 1, 2.5, 1, 22)
  && Math.sign(mod.glyphAngle(0, 0, 1, 1, 22)) !== 0
  && Math.abs(mod.glyphAngle(1, 0, 1, 1, 22) - mod.glyphAngle(1, 1, 1, 1, 22)) > 0);
t('speed 影响角速度（0 时角位移为 0）',
  Math.abs(mod.glyphAngle(1, 0, 5, 0, 22) - mod.glyphAngle(1, 0, 0, 0, 22)) < 1e-12);

// ---- 页面接入 ----
t('登录页有背景 canvas 且带 aria-hidden（装饰元素不该被读屏念 ✓）',
  /<canvas id="login-vortex"[^>]*aria-hidden="true"/.test(html));
t('背景铺满且位于卡片之下（z-index:0，卡片是 1）', /\.login-vortex \{[^}]*position: fixed/.test(html)
  && /\.login-vortex \{[^}]*z-index: 0/.test(html));
t('降级不白屏：登录页有 CSS 底色兜底', /\.login-page \{ background: radial-gradient/.test(html));
t('模块以 /front 前缀载入（前端唯一家 ✓）', /<script src="\/front\/login_vortex\.js"><\/script>/.test(html));
t('生命周期跟随登录块（出现挂载 / 消失销毁）', /MutationObserver/.test(html)
  && /window\.LoginVortex\.autoMount/.test(html));
t('尊重 prefers-reduced-motion（不画，交给 CSS 底色）',
  /prefers-reduced-motion: reduce/.test(fs.readFileSync(path.join(ROOT, 'front', 'login_vortex.js'), 'utf8')));
t('不可见时停画（visibilitychange）',
  /visibilitychange/.test(fs.readFileSync(path.join(ROOT, 'front', 'login_vortex.js'), 'utf8')));

// ---- 配色（用户实测反馈：登录页是浅底 ✗ 浅色墨迹看不见）----
t('★ 浅色档线条用深琥珀（浅底可读）',
  /light: \{[^}]*ink: '#a9760c'/.test(src) && /light: \{[^}]*accent: '#c9971c'/.test(src));
t('★ 参数含散落速率/汇聚数/放大倍率（参考图三处交互 ✓）',
  /scatterRate:/.test(src) && /convergeCount:/.test(src) && /zoomMax:/.test(src));
t('★ 深色档也一并提亮（深底上弱了同样看不清）', /dark: \{[^}]*ink: '#f4f8ff'/.test(src));
t('★ 鼠标处是**粒子散落**（不是光晕 ✗）：散落粒子 + 小方点',
  /motes\.push/.test(src) && /fillRect\(mo\.x, mo\.y/.test(src) && /scatterRate/.test(src));
t('★ 点击是**粒子汇聚**：四周飞向落点 + 落点小目标环',
  /to: \{ x: px, y: py \}/.test(src) && /convergeRing/.test(src));
t('★ 环放大由卡片悬停驱动（点击不再缩环 ✗）',
  /setZoom: function/.test(src) && /var pull = zoom;/.test(src) && /inst\.setZoom\(1\.18\)/.test(html));
t('环上是**重复文字串**（参考图效果），不是随机字母',
  /text: '/.test(src) && /智选情报官/.test(src));
t('★ autoMount 默认走浅色档（可用 canvas data-mode 覆盖）',
  /\|\| 'light';/.test(src) && /data-mode/.test(src));
t('撒点/粒子在浅底上用的是深色（不是白点）', /light: \{[^}]*dust: 'rgba\(122,84,6/.test(src));

console.log('\n' + (bad ? '❌ 有失败：' : '✅ 全部通过：') + ok + ' 通过 / ' + bad + ' 失败');
process.exit(bad ? 1 : 0);
