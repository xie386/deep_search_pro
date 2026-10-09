/* ────────────────────────────────────────────────────────────────────────────
   Typography Vortex —— 登录界面背景（Canvas 2D）
   ────────────────────────────────────────────────────────────────────────────
   设计来源：threeui.com/text-animation/typography-vortex
     · 契约：**Canvas 2D 两遍渲染** / 预渲染环带 + 漂移字形 + 指针消散 + 粒子尘 + 点击吸入
     · 原站明确「Exact source is protected」✗（只发编译产物、不发源码）
     · 因此**本文件是按效果复刻**，不是原站代码 ✓；参数名与它的 Props 对齐 ✓

   Props（与 ThreeUI 一致）：mode · speed · ringGrowth(1.21) · opacity ·
                             dissolveRadius · particleAmount · suctionDuration(920ms)

   为什么纯手写（不引 npm 包）：原组件是 **React** 组件，本项目是 **Vue3 单页内联 JS** ✗；
   为一个登录页背景引入 React 运行时 + 61KB 产物不划算 ✓ → 复刻成零依赖 Canvas 2D ✓。

   设计要点（照契约，不缩水）：
     ① 环带**预渲染到离屏 canvas**（crisp，不每帧重排字形 ✓），环半径按 ringGrowth 逐环 ×1.21；
     ② 字形沿环漂移（角速度按 speed，环越外越慢/越快形成涡旋 ✓）；
     ③ 指针划过 → `destination-out` 径向渐变**消散**局部（dissolveRadius 控制范围 ✓）；
     ④ 点击 → 粒子与字形被**吸入中心**（suctionDuration 缓动 ✓）；
     ⑤ devicePixelRatio 自适应但**封顶 ≤ 2**（省 GPU ✓）；页面不可见/不可见时**停画** ✓。
   全局只暴露一个对象：`window.LoginVortex`（含 mount 与纯函数，便于单测 ✓）
   ──────────────────────────────────────────────────────────────────────────── */
(function (root) {
  'use strict';

  var DEFAULTS = {
    mode: 'dark',            // 'dark' | 'light'
    speed: 1.0,              // 涡旋角速度倍率
    ringGrowth: 1.21,        // 每往外一环的半径倍率（ThreeUI 默认值）
    opacity: 1.0,            // 整体不透明度
    dissolveRadius: 1.0,     // 指针消散半径倍率
    particleAmount: 1.0,     // 粒子密度倍率
    suctionDuration: 920,    // 点击吸入时长(ms)
    maxDpr: 2,               // 像素比上限（契约：adaptive ≤ 2）
    rings: 7,                // 环数
    glyphsPerRing: 22,       // 每环字形数
    font: '"Fragment Mono", ui-monospace, SFMono-Regular, Menlo, Consolas, monospace',
    /* ★ 2026-09-30 按参考图修正的三处： */
    text: '智选情报官 / INTELLIGENCE IN MOTION / ',  // 环上重复的**文字串**（参考图是成句文字，不是随机字母 ✓）
    scatterRate: 5,          // 鼠标处每帧散落的粒子数（粒子散落，不是光晕 ✗）
    moteLife: 900,           // 散落粒子寿命(ms)
    convergeCount: 190,      // 点击时从四周**汇聚**到该点的粒子数
    convergeRing: 34,        // 点击处的小目标环最终半径(px)
    zoomMax: 1.18            // 登录卡片悬停展开时，背景环同步放大到此倍率 ✓
  };

  /* ★ 2026-09-30 用户实测：登录页本身是**浅色底** ✗ —— 浅底必须用**深琥珀线条 + 亮白高光**才看得见。
     所以 light 档 = 深黄/琥珀系（线条、环、字形）+ 亮白（撒点、鼠标暖光）；
     dark 档保留冷白但整体提亮（深底上弱了同样看不清 ✓）。 */
  var PALETTE = {
    dark: { bg: '#0b0f14', ink: '#f4f8ff', dim: 'rgba(244,248,255,.55)',
            accent: '#ffd479', dust: 'rgba(255,255,255,.75)', glow: 'rgba(255,214,120,.55)' },
    light: { bg: '#f4f6f3', ink: '#a9760c', dim: 'rgba(169,118,12,.62)',
             accent: '#c9971c', dust: 'rgba(122,84,6,.75)', glow: 'rgba(255,196,64,.60)' }
  };

  /* ───────────────────────── 纯函数区（可在 node 里直接测 ✓） ───────────────────────── */

  /** 像素比：自适应但封顶（契约 adaptive ≤ 2）；非法输入退回 1。 */
  function clampDpr(dpr, maxDpr) {
    var d = Number(dpr);
    if (!isFinite(d) || d <= 0) d = 1;
    var m = Number(maxDpr);
    if (!isFinite(m) || m <= 0) m = DEFAULTS.maxDpr;
    return Math.max(1, Math.min(d, m));
  }

  /** 第 i 环的半径倍率：growth^i（ringGrowth=1.21 → 1, 1.21, 1.46, …）。 */
  function ringScale(i, growth) {
    var g = Number(growth);
    if (!isFinite(g) || g <= 0) g = DEFAULTS.ringGrowth;
    var n = Math.max(0, Math.floor(Number(i) || 0));
    return Math.pow(g, n);
  }

  /** 指针消散强度：环内为 1，边缘平滑衰减到 0。dist/radius 均为像素。 */
  function dissolveAlpha(dist, radius) {
    var r = Number(radius);
    if (!isFinite(r) || r <= 0) return 0;
    var d = Number(dist);
    if (!isFinite(d) || d < 0) d = 0;
    if (d >= r) return 0;
    var x = 1 - d / r;            // 1 → 0
    return x * x * (3 - 2 * x);   // smoothstep，边缘不硬
  }

  /** 吸入进度 0→1（easeOutCubic）：contract 里的 suctionDuration 用它换算。 */
  function suctionProgress(elapsed, duration) {
    var t = Number(elapsed), d = Number(duration);
    if (!isFinite(d) || d <= 0) return 1;
    if (!isFinite(t) || t <= 0) return 0;
    if (t >= d) return 1;
    var x = t / d;
    return 1 - Math.pow(1 - x, 3);
  }

  /** 第 ring 环上第 k 个字形当前角度：ring 越靠外角速度越慢 → 形成涡旋剪切。 */
  function glyphAngle(k, ring, t, speed, perRing) {
    var n = Math.max(1, perRing || DEFAULTS.glyphsPerRing);
    var sp = Number(speed);
    if (!isFinite(sp)) sp = DEFAULTS.speed;
    var r = Math.max(0, Number(ring) || 0);
    var base = (k / n) * Math.PI * 2;
    var omega = (0.42 / (1 + r * 0.55)) * sp;      // 内快外慢
    return base + (Number(t) || 0) * omega * (r % 2 === 0 ? 1 : -1);   // 相邻环反向 → 剪切感
  }

  /** 字形集（涡旋里用的是等宽字母数字，避免缺字变方块 ✓） */
  var GLYPHS = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789·+=<>/\\';

  /* ───────────────────────────────── 渲染 ───────────────────────────────── */

  function makeRingSprite(size, glyphs, colors, font, ringIdx) {
    // 预渲染：环 + 沿环排布的字形（一次性画进离屏 canvas，之后每帧只做变换 ✓ 契约里的 "prerendered rings"）
    var cv = document.createElement('canvas');
    cv.width = cv.height = size;
    var g = cv.getContext('2d');
    var c = size / 2, R = c - size * 0.06;
    g.strokeStyle = ringIdx % 2 ? colors.dim : colors.ink;
    g.globalAlpha = ringIdx % 2 ? 0.62 : 0.46;
    g.lineWidth = Math.max(1, size * 0.0035);
    g.beginPath(); g.arc(c, c, R, 0, Math.PI * 2); g.stroke();
    g.globalAlpha = 1;
    g.font = Math.round(size * 0.052) + 'px ' + font;
    g.textAlign = 'center'; g.textBaseline = 'middle';
    for (var k = 0; k < glyphs.length; k++) {
      var a = (k / glyphs.length) * Math.PI * 2;
      var ch = glyphs[k];
      if (ringIdx % 3 === 0 && k % 2) {           // 稀疏化：避免每环都糊满字 ✓
        continue;
      }
      g.save();
      g.translate(c + Math.cos(a) * R, c + Math.sin(a) * R);
      g.rotate(a + Math.PI / 2);
      g.fillStyle = (k % 7 === 0) ? colors.accent : (k % 2 ? colors.dim : colors.ink);
      g.globalAlpha = k % 7 === 0 ? 1.0 : 0.86;
      g.fillText(ch, 0, 0);
      g.restore();
    }
    return cv;
  }

  function mount(canvas, options) {
    if (!canvas || !canvas.getContext) return null;
    var o = Object.assign({}, DEFAULTS, options || {});
    var ctx = canvas.getContext('2d', { alpha: true });
    if (!ctx) return null;

    var colors = PALETTE[o.mode] || PALETTE.dark;
    var sprites = [];
    var dust = [];
    var w = 0, h = 0, cx = 0, cy = 0, baseR = 0, dpr = 1;
    var raf = 0, last = 0, clock = 0, running = false;
    var pointer = { x: -9999, y: -9999, on: false };
    var suction = { active: false, t0: 0, x: 0, y: 0 };
    var motes = [];                 // 粒子：散落型(鼠标) + 汇聚型(点击)
    var zoom = 1, zoomTarget = 1;   // ★ 登录卡片悬停 → 背景环同步放大 ✓

    function build() {
      var rect = canvas.getBoundingClientRect();
      w = Math.max(1, Math.round(rect.width));
      h = Math.max(1, Math.round(rect.height));
      dpr = clampDpr(root.devicePixelRatio, o.maxDpr);
      canvas.width = Math.round(w * dpr);
      canvas.height = Math.round(h * dpr);
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      cx = w / 2; cy = h / 2;
      baseR = Math.min(w, h) * 0.13;                 // 最内环半径
      sprites = [];
      var phrase = String(o.text || '') || GLYPHS;
      var gs = phrase.split('');
      for (var i = 0; i < o.rings; i++) {
        var size = Math.round(baseR * ringScale(i, o.ringGrowth) * 2.04) + 24;
        var glyphs = [];
        for (var k = 0; k < o.glyphsPerRing; k++) {
          // 沿环**顺序**取字，形成"重复的句子"（参考图的效果 ✓）；环间错开避免同列对齐
          glyphs.push(gs[(i * 5 + k) % gs.length]);
        }
        sprites.push({ cv: makeRingSprite(size, glyphs, colors, o.font, i), size: size });
      }
      // 粒子尘
      var n = Math.round(90 * Math.max(0, o.particleAmount));
      dust = [];
      for (var d2 = 0; d2 < n; d2++) {
        var a = (d2 / n) * Math.PI * 2 + (d2 % 5) * 0.31;
        var rr = baseR * (1.2 + ((d2 * 37) % 100) / 100 * 5.2);
        dust.push({ a: a, r: rr, s: 0.6 + ((d2 * 13) % 100) / 100 * 1.6, o: 0.25 + ((d2 * 29) % 100) / 100 * 0.5 });
      }
    }

    function frame(ts) {
      if (!running) return;
      var dt = last ? Math.min(64, ts - last) : 16;
      last = ts;
      clock += (dt / 1000) * o.speed;
      zoom += (zoomTarget - zoom) * Math.min(1, dt / 160);        // 平滑跟随卡片展开 ✓
      var colors2 = PALETTE[o.mode] || PALETTE.dark;
      ctx.clearRect(0, 0, w, h);
      ctx.globalAlpha = o.opacity;

      // ① 环带 + 字形（每个环一个预渲染 sprite，只做位移/旋转/缩放 ✓）
      for (var i = 0; i < sprites.length; i++) {
        var s = sprites[i];
        var rot = glyphAngle(0, i, clock, 1, 1) * 0.55;
        var breathe = 1 + 0.015 * Math.sin(clock * 1.7 + i);
        // ★ 环的缩放不再由"点击"驱动 ✗（那是我的理解偏差）→ 改由**登录卡片悬停**驱动 ✓
        var pull = zoom;
        ctx.save();
        ctx.translate(cx, cy);
        ctx.rotate(rot);
        ctx.scale(breathe * pull, breathe * pull);
        ctx.drawImage(s.cv, -s.size / 2, -s.size / 2, s.size, s.size);
        ctx.restore();
      }

      // ② 粒子尘（点击吸入时向中心加速 ✓）
      for (var d3 = 0; d3 < dust.length; d3++) {
        var q = dust[d3];
        var ang = q.a + clock * (0.16 / (1 + q.r / (baseR * 4)));
        var rad = q.r;
        if (suction.active) {
          var pp = suctionProgress(ts - suction.t0, o.suctionDuration);
          rad = q.r * (1 - 0.85 * pp);
        }
        var x = cx + Math.cos(ang) * rad, y = cy + Math.sin(ang) * rad;
        ctx.globalAlpha = Math.min(1, o.opacity * q.o * 1.5)
          * (suction.active ? 1 - 0.3 * suctionProgress(ts - suction.t0, o.suctionDuration) : 1);
        ctx.fillStyle = colors2.dust;
        ctx.beginPath(); ctx.arc(x, y, q.s, 0, Math.PI * 2); ctx.fill();
      }
      ctx.globalAlpha = o.opacity;

      // ③ 鼠标处**粒子散落**（参考图 1 的效果 ✓ —— 不是光晕 ✗）
      if (pointer.on) {
        var RR = Math.min(w, h) * 0.16 * Math.max(0.2, o.dissolveRadius);
        for (var sN = 0; sN < Math.max(1, Math.round(o.scatterRate * o.particleAmount)); sN++) {
          var ang0 = Math.random() * Math.PI * 2;
          var rad0 = Math.sqrt(Math.random()) * RR;                 // 均匀落在半径内
          motes.push({
            x: pointer.x + Math.cos(ang0) * rad0, y: pointer.y + Math.sin(ang0) * rad0,
            vx: Math.cos(ang0) * (0.25 + Math.random() * 0.55),     // 向外散
            vy: Math.sin(ang0) * (0.25 + Math.random() * 0.55),
            born: ts, life: o.moteLife * (0.7 + Math.random() * 0.7),
            size: 0.9 + Math.random() * 1.9, to: null
          });
        }
      }

      // ④ 点击 → 四周粒子**汇聚**到该点（参考图 2 ✓；并在落点画一个小目标环 ✓）
      for (var m = motes.length - 1; m >= 0; m--) {
        var mo = motes[m];
        var age = ts - mo.born;
        if (age > mo.life) { motes.splice(m, 1); continue; }
        var k2 = age / mo.life;
        if (mo.to) {                                   // 汇聚型：向落点插值（easeOutCubic ✓）
          var q = suctionProgress(age, o.suctionDuration);
          mo.x = mo.to.x + (mo.fromX - mo.to.x) * (1 - q);
          mo.y = mo.to.y + (mo.fromY - mo.to.y) * (1 - q);
        } else {                                       // 散落型：向外飘 + 减速
          mo.x += mo.vx * (dt / 16.7) * 1.6;
          mo.y += mo.vy * (dt / 16.7) * 1.6;
          mo.vx *= 1.012; mo.vy *= 1.012;
        }
        ctx.globalAlpha = Math.min(1, o.opacity * (1 - k2) * 1.15);
        ctx.fillStyle = colors2.dust;
        ctx.fillRect(mo.x, mo.y, mo.size, mo.size);    // 参考图里是**小方点** ✓
      }
      if (suction.active) {
        var sp2 = suctionProgress(ts - suction.t0, o.suctionDuration);
        if (sp2 < 1) {
          ctx.globalAlpha = o.opacity * (1 - sp2) * 0.9;
          ctx.strokeStyle = colors2.ink;
          ctx.lineWidth = 1.2;
          ctx.beginPath();
          ctx.arc(suction.x, suction.y, 10 + (o.convergeRing - 10) * sp2, 0, Math.PI * 2);
          ctx.stroke();
        }
      }

      // ④ 指针消散（第二遍：destination-out 在局部"擦掉"内容 ✓ 契约的 pointer dissolve）
      if (pointer.on) {
        var R = Math.min(w, h) * 0.16 * Math.max(0.2, o.dissolveRadius);
        var grad = ctx.createRadialGradient(pointer.x, pointer.y, 0, pointer.x, pointer.y, R);
        var a0 = dissolveAlpha(0, R);
        grad.addColorStop(0, 'rgba(0,0,0,' + a0 + ')');
        grad.addColorStop(0.6, 'rgba(0,0,0,' + dissolveAlpha(R * 0.6, R) * 0.9 + ')');
        grad.addColorStop(1, 'rgba(0,0,0,0)');
        ctx.save();
        ctx.globalCompositeOperation = 'destination-out';
        ctx.fillStyle = grad;
        ctx.beginPath(); ctx.arc(pointer.x, pointer.y, R, 0, Math.PI * 2); ctx.fill();
        ctx.restore();
      }

      if (suction.active && ts - suction.t0 > o.suctionDuration) suction.active = false;
      raf = root.requestAnimationFrame(frame);
    }

    function start() {
      if (running) return;
      running = true; last = 0;
      raf = root.requestAnimationFrame(frame);
    }
    function stop() {
      running = false;
      if (raf) { root.cancelAnimationFrame(raf); raf = 0; }
    }

    // ── 事件：指针 / 点击 / 尺寸 / 可见性（不可见就停画，别在后台烧 CPU ✓）
    function onMove(e) {
      var r = canvas.getBoundingClientRect();
      pointer.x = e.clientX - r.left;
      pointer.y = e.clientY - r.top;
      pointer.on = true;
    }
    function onLeave() { pointer.on = false; }
    function onDown(e) {
      var r = canvas.getBoundingClientRect();
      var px = e.clientX - r.left, py = e.clientY - r.top;
      suction = { active: true, t0: root.performance.now(), x: px, y: py };
      // ★ 参考图 2：点击不是"环收紧"✗，而是**四周粒子汇聚到该点** ✓
      var n2 = Math.max(20, Math.round(o.convergeCount * o.particleAmount));
      for (var i = 0; i < n2; i++) {
        var a3 = Math.random() * Math.PI * 2, r3 = Math.min(w, h) * (0.18 + Math.random() * 0.55);
        motes.push({ x: px + Math.cos(a3) * r3, y: py + Math.sin(a3) * r3,
                     fromX: px + Math.cos(a3) * r3, fromY: py + Math.sin(a3) * r3,
                     to: { x: px, y: py }, vx: 0, vy: 0, born: root.performance.now(),
                     life: o.suctionDuration * 1.15, size: 0.9 + Math.random() * 2.2 });
      }
    }
    function onResize() { build(); }
    function onVis() { if (document.hidden) stop(); else start(); }

    build();
    canvas.addEventListener('pointermove', onMove, { passive: true });
    canvas.addEventListener('pointerleave', onLeave);
    canvas.addEventListener('pointerdown', onDown);
    root.addEventListener('resize', onResize);
    document.addEventListener('visibilitychange', onVis);
    start();

    return {
      destroy: function () {
        stop();
        canvas.removeEventListener('pointermove', onMove);
        canvas.removeEventListener('pointerleave', onLeave);
        canvas.removeEventListener('pointerdown', onDown);
        root.removeEventListener('resize', onResize);
        document.removeEventListener('visibilitychange', onVis);
      },
      setMode: function (m) { o.mode = (m === 'light' ? 'light' : 'dark'); build(); },
      /** 卡片悬停时调用：背景环同步放大/复原（0.16s 内平滑 ✓） */
      setZoom: function (v) { var z = Number(v); zoomTarget = (isFinite(z) && z > 0) ? Math.min(2, z) : 1; },
      set: function (patch) { Object.assign(o, patch || {}); build(); },
      props: o,
      start: start, stop: stop
    };
  }

  /** 挂载入口：登录页调用；尊重 prefers-reduced-motion（该情形直接不画 ✓ 不白屏：底下是 CSS 背景） */
  function autoMount(selector) {
    var el = document.querySelector(selector || '#login-vortex');
    if (!el) return null;
    var reduce = false;
    try { reduce = root.matchMedia && root.matchMedia('(prefers-reduced-motion: reduce)').matches; } catch (e) { reduce = false; }
    if (reduce) { el.style.display = 'none'; return null; }
    // ★ 默认按**浅色底**上色（登录页就是浅底 ✗ 原来按深底配色 → 几乎看不见）；
    //   要深底就写 <canvas data-mode="dark">，也可以显式传参 ✓
    var mode = (el.getAttribute && el.getAttribute('data-mode')) || 'light';
    return mount(el, { mode: mode === 'dark' ? 'dark' : 'light' });
  }

  root.LoginVortex = {
    DEFAULTS: DEFAULTS, PALETTE: PALETTE, GLYPHS: GLYPHS,
    clampDpr: clampDpr, ringScale: ringScale, dissolveAlpha: dissolveAlpha,
    suctionProgress: suctionProgress, glyphAngle: glyphAngle,
    mount: mount, autoMount: autoMount
  };
})(typeof window !== 'undefined' ? window : globalThis);

/* node 环境下供单测（浏览器里这个判断为假，什么都不做 ✓） */
if (typeof module !== 'undefined' && module.exports) { module.exports = globalThis.LoginVortex; }
