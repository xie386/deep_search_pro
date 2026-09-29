
const { createApp, ref, reactive, computed, nextTick, onMounted, watch } = Vue;

createApp({
  setup() {
    // ---------- 桌面版适配（P0）----------
    // 桌面外壳加载页面时带 ?desktop=1。**用 URL 判断而不是 window.pywebview**：
    // URL 在 setup 阶段同步可读，而 pywebview 是页面加载后才异步注入的（时序坑）。
    const IS_DESKTOP = /[?&]desktop=1/.test(location.search);
    // 桌面版用 localStorage（关窗后登录态还在）；网页版保持原样用 sessionStorage（行为不变）
    const _store = (() => {
      try { return IS_DESKTOP ? window.localStorage : window.sessionStorage; }
      catch (e) { return window.sessionStorage; }
    })();
    const _USES_LOCAL_STORAGE = IS_DESKTOP;
    function storeGet(k) { try { return _store.getItem(k) || ''; } catch (e) { return ''; } }
    function storeSet(k, v) { try { _store.setItem(k, v); } catch (e) {} }
    function storeClear() { try { _store.clear(); } catch (e) {} }
    // 取原生能力：浏览器里返回 null，调用处一律 if (api && ...) 兜底
    function desktopApi() {
      return (IS_DESKTOP && window.pywebview && window.pywebview.api) ? window.pywebview.api : null;
    }

    // ★ 统一的「把某个 URL 保存成文件」入口（全站导出/下载都走它）
    //   桌面版：fetch 回字节 → base64 → 原生「另存为」（**WebView2 不支持浏览器式下载，
    //           直接给 <a href> 会静默丢弃，文件哪都找不到** —— 实测踩过）
    //   网页版：<a download> 触发浏览器下载（不再用 location.href，避免整页跳转）
    async function saveFromUrl(fileUrl, filename) {
      const api = desktopApi();
      if (api && api.save_binary_b64) {
        const resp = await fetch(fileUrl);
        if (!resp.ok) {
          const msg = '取回文件失败：HTTP ' + resp.status;
          showToast(msg, true);
          return { ok: false, error: msg, native: true };
        }
        const buf = new Uint8Array(await resp.arrayBuffer());
        let bin = ''; const CH = 0x8000;   // 分块拼接，避免超长参数一次性 apply 爆栈
        for (let i = 0; i < buf.length; i += CH) bin += String.fromCharCode.apply(null, buf.subarray(i, i + CH));
        const res = await api.save_binary_b64(filename || '导出.md', btoa(bin));
        if (res && res.ok) { showToast('已保存到：' + res.path); return { ok: true, path: res.path, native: true }; }
        if (res && res.cancelled) { return { ok: false, cancelled: true, native: true }; }   // 用户取消，不打扰
        const emsg = (res && res.error) || '未知错误';
        showToast('保存失败：' + emsg, true);
        return { ok: false, error: emsg, native: true };
      }
      const a = document.createElement('a');
      a.href = fileUrl;
      if (filename) a.download = filename;
      document.body.appendChild(a);
      a.click();
      a.remove();
      showToast('已开始下载：' + (filename || '文件'));
      return { ok: true, native: false };
    }

    // 从下载 URL 里取文件名（后端给的 download_md/download_pdf 带 ?name=xxx）
    function nameFromUrl(fileUrl, fallback) {
      try {
        const m = /[?&]name=([^&]+)/.exec(fileUrl || '');
        if (m) return decodeURIComponent(m[1]);
      } catch (e) {}
      return fallback || '导出.md';
    }

    // 桌面版：把「正在忙什么」写到托盘提示（空串=回到空闲）。网页版是空操作。
    // 用途：周报要生成几十秒~几分钟，用户多半已把窗口收进托盘/最小化——托盘上能看到进度，
    // 就不用盯着窗口猜"它到底在干活吗"。
    function desktopStatus(text) {
      const api = desktopApi();
      if (api && api.set_status) { try { api.set_status(text || ''); } catch (e) {} }
    }

    // ★ 登录态失效处理（2026-09-23 用户报障修）：清掉本地登录态、回登录页并说明原因。
    //   背景：token 存在 localStorage（桌面版「记住登录」），但后端重启会让旧 token 失效。
    //   老代码只判"token 非空"就渲染工作台，各接口 401 又被静默吞掉（loadMyData 里
    //   `if (!r.ok) return;`）→ 用户看到「已登录、但兴趣/关注/收藏全是 0、天气卡报未登录或登录已失效」。
    //   注意：只清**鉴权相关的键**，不要 storeClear()——那会把背景图等偏好一起清掉。
    function forceLogout(msg) {
      try {
        ['zx_token', 'zx_user', 'zx_role'].forEach(k => _store.removeItem(k));
      } catch (e) {}
      if (ws) { try { ws.close(); } catch (e) {} wsTarget = ''; }
      token.value = ''; username.value = ''; password.value = '';
      resetPriceState();            // ★ 注销：清掉上一个账号的价格缓存
      errMsg.value = msg || '';
      authMode.value = 'login';
      if (msg) showToast(msg, true);
    }

    // 启动时验一次登录态：只有后端认这个 token，才算真登录（返回 true=有效）
    async function verifySession() {
      try {
        const r = await fetch('/api/me/overview?token=' + encodeURIComponent(token.value));
        if (r.status === 401) { forceLogout('登录已失效，请重新登录'); return false; }
        return r.ok;
      } catch (e) {
        return true;   // 网络抖动不当作失效，避免把能用的登录态误踢掉
      }
    }

    const token = ref(storeGet('zx_token'));
    const username = ref(storeGet('zx_user'));
    const role = ref(storeGet('zx_role') || 'personal');
    const regRole = ref('personal');
    const password = ref('');
    const errMsg = ref('');
    const busy = ref(false);
    const _initHash = location.hash.replace('#/', '') || 'home';   // #/shell 直达 CLI 面板
    const view = ref(_initHash === 'shell' ? 'skills' : _initHash);

    const messages = ref([]);
    const sessions = ref([]);         // M1 会话列表 [{thread_id,title,message_count,updated_at}]
    const activeThread = ref('');     // M1 当前会话 thread_id（空=旧版直接发问，自动归档）
    const monitor = ref([]);
    const thinkingText = ref('');
    const showBgPanel = ref(false);   // 背景设置面板
    const bgOpacity = ref(60);        // 背景图透明度（5-100）
    const bgImages = ref([]);         // 图库列表 [{name,url}]
    const bgActive = ref('');         // 当前选中的背景图 url
    // 定制助手
    const providers = ref([]);        // 模型配置列表
    const newProv = ref({ provider_name: '', model_name: '', base_url: '', api_key: '', is_active: false });
    const soulContent = ref('');      // 当前人格内容
    const soulReq = ref('');          // 人格生成需求输入
    const soulGenOut = ref('');       // 生成结果预览
    const soulGenning = ref(false);
    const soulSaved = ref(false);
    const soulList = ref([]);         // 人格列表 [{name,file,active}]
    const soulActive = ref('SOUL.md');// 当前激活人格文件
    const newSoulName = ref('');      // 新建人格名
    // 记忆画像 MEMORY.md
    const memoryContent = ref('');
    // M3-7：建议/历史（ref 必须出现在 setup 的 return 里，否则模板静默失效）
    const memSuggestions = ref([]);
    const memSnapshots = ref([]);
    const memBusy = ref(false);
    const memProtectManual = ref(true);      // D8 默认：保护人工行
    const memEvidence = ref('');
    const memSkipped = ref('');
    const memChanged = ref(false);
    const memCheckedCount = computed(function () {
      return memSuggestions.value.filter(function (s) { return s.checked; }).length;
    });
    const memoryInitialized = ref(false);
    const memorySaved = ref(false);
    const memoryGenning = ref(false);
    // 语音包（音色克隆）
    const voicePacks = ref([]);
    const voiceAvailable = ref(true);
    const voiceBusy = ref(false);
    const newVoicePack = ref({ name: '', refText: '' });
    const voiceFile = ref(null);
    const voiceFileInput = ref(null);
    // M3 知识库
    const kbStatus = ref(null);        // {exists, doc_count, docs}
    const kbBusy = ref(false);
    // 通用入库预览弹窗（M3 三条路共用：粘贴 / 上传 / 导出）
    const kbIngestShow = ref(false);
    const kbIngestCtx = ref({});        // {title, source_kind, source_kind_label, content, title_input, title_editable, apply_clean_default, is_binary_upload}
    const kbIngestTitle = ref('');      // 仅 title_editable 时可改
    const kbIngestRaw = ref('');         // 原文（评估后才知道是否清洗）
    const kbEvalDone = ref(false);
    const kbEval = ref({});
    const kbUseClean = ref(true);
    const kbFileInput = ref(null);
    const kbQueryText = ref('');
    const kbQueryHits = ref([]);
    // 旧 kbExportCandidate/kbExportShow 留作 v1 退路（实际改走 kbIngestOpenWith）
    const draft = ref('');
    const loading = ref(false);
    const stopping = ref(false);   // 已点「中断」，等后端在下一个检查点停下
    let chatAbort = null;          // 当前 /api/chat 的 AbortController（中断兜底用）
    const exporting = ref(false);
    const scroll = ref(null);
    const toast = ref(''); const toastErr = ref(false);
    let toastTimer = null;

    // 用户数据
    const emptyMy = () => ({ competitors: [], products: [], interests: [], watchlist: [], collection: [], setup_done: true });
    const myData = ref(emptyMy());
    const prof = reactive({ company_name:'', intro:'', legal_rep:'', reg_capital:'', founded_date:'',
      employee_scale:'', website:'', hq_city:'', industry:'', business_scope:'',
      contact:'', address:'', honorsText:'', note:'' });
    const profSaved = ref(false);

    const showOnboard = ref(false);
    const onboardCompany = ref('');
    const onboardComps = ref([{name:''}]);
    const onboardInterests = ref([{name:''}]);

    const newInterest = ref({ tag:'', description:'', keywords:'' });
    const newWatch = reactive({ brand:'', product:'', note:'', category:'', price:null, _specs:'', _note2:'' });
    const newComp = reactive({ comp_name:'', website:'', note:'' });
    const batchModal = reactive({ show: false, type: 'competitors', text: '' });
    const newProd = reactive({ product_name:'', category:'', price:null, _description:'', _features:'', _target:'' });

    let ws = null;

    const displayName = computed(() => role.value === 'company' ? (prof.company_name || username.value) : username.value);
    const greeting = computed(() => {
      const h = new Date().getHours();
      return h < 6 ? '夜深了' : h < 12 ? '早上好' : h < 14 ? '中午好' : h < 18 ? '下午好' : '晚上好';
    });
    const profileFilled = computed(() => Object.entries(prof).filter(([k,v]) => k !== 'honorsText' && v).length + (prof.honorsText ? 1 : 0));

    const examples = computed(() => role.value === 'company'
      ? ['对比我们公司与主要竞品在定价和功能上的差异，标注来源',
         '分析我们所在行业的竞争格局，找出我们的差异化机会',
         '结合我们的产品定位，给出下季度的差异化竞争建议']
      : ['帮我看看我关注的产品最近有没有降价或新品发布',
         '我的兴趣领域最近有什么新动态或降价？',
         '根据我的收藏，哪款最值得入手？']);

    // ---------- 情报面板 ----------
    const kbPercent = computed(() => {
      const c = kbStatus.value ? kbStatus.value.doc_count : 0;
      return Math.min(Math.round((c / 100) * 100), 100);
    });
    const activityTrend = computed(() => {
      const days = ['日', '一', '二', '三', '四', '五', '六'];
      const today = new Date().getDay();
      return Array.from({ length: 7 }, (_, i) => {
        const d = (today - 6 + i + 7) % 7;
        return { lbl: days[d], h: 20 + Math.random() * 60 + (i > 4 ? 15 : 0) };
      });
    });
    const recentActivity = computed(() => {
      const out = [];
      if (kbStatus.value && kbStatus.value.doc_count > 0) {
        out.push({ text: `知识库已有 ${kbStatus.value.doc_count} 篇文档`, time: '刚刚', cls: 'kb' });
      }
      if (subs.value.length > 0) {
        out.push({ text: `${subs.value.length} 个订阅运行中`, time: '今天', cls: 'report' });
      }
      if (messages.value.length > 0) {
        out.push({ text: `本次会话 ${messages.value.length} 条对话`, time: '本次', cls: '' });
      }
      return out.slice(0, 4);
    });

    // ---------- M4 报告/订阅状态 ----------
    const reports = ref([]);
    const subs = ref([]);
    const newReports = ref(0);
    const digestRunning = ref(false);
    const newSubName = ref('');
    const newSubScope = ref('personal');
    const newSubLang = ref('zh');
    const selCount = computed(() => subs.value.filter(s => s._sel && s.enabled).length);

    async function loadReports() {
      try {
        const r = await fetch('/api/reports?token=' + encodeURIComponent(token.value));
        if (r.ok) { reports.value = (await r.json()).items || []; }
      } catch (e) {}
    }
    async function loadSubs() {
      try {
        const r = await fetch('/api/digest/subs?token=' + encodeURIComponent(token.value));
        if (r.ok) {
          const arr = (await r.json()).subs || [];
          subs.value = arr.map(s => ({
            ...s,
            _kws: (() => { try { return JSON.parse(s.keywords || '[]').join(', '); } catch (e) { return ''; } })(),
            _name: s.name || '',
            lang: s.lang || 'zh',
            enabled: !!s.enabled,
            _sel: !!s.enabled,
          }));
        }
      } catch (e) {}
    }
    async function updateSub(s) {
      const kws = s._kws.split(/[,，]/).map(x => x.trim()).filter(Boolean).slice(0, 8);
      const r = await fetch('/api/digest/subs/' + s.id + '?token=' + encodeURIComponent(token.value), {
        method: 'PUT', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name: s._name, keywords: JSON.stringify(kws), schedule: s.schedule, lang: s.lang, enabled: s.enabled }),
      });
      if (r.ok) { showToast('订阅已保存 ✅'); await loadSubs(); } else { const e = await r.json().catch(() => ({})); showToast('保存失败：' + (e.detail || r.status), true); }
    }
    async function addSub() {
      const name = newSubName.value.trim();
      if (!name) { showToast('请填写订阅名', true); return; }
      const defaultKws = newSubScope.value === 'company' ? ['行业动态'] : ['数码新品'];
      const r = await fetch('/api/digest/subs?token=' + encodeURIComponent(token.value), {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name, scope: newSubScope.value, keywords: JSON.stringify(defaultKws), schedule: 'weekly', lang: newSubLang.value, enabled: true }),
      });
      if (r.ok) { newSubName.value = ''; showToast('订阅已新增 ✅'); await loadSubs(); }
      else { const e = await r.json().catch(() => ({})); showToast('新增失败：' + (e.detail || r.status), true); }
    }
    async function delSub(s) {
      const r = await fetch('/api/digest/subs/' + s.id + '?token=' + encodeURIComponent(token.value), { method: 'DELETE' });
      if (r.ok) { showToast('已删除'); subs.value = subs.value.filter(x => x.id !== s.id); }
      else { const e = await r.json().catch(() => ({})); showToast('删除失败：' + (e.detail || r.status), true); }
    }
    async function runDigestNow() {
      const ids = subs.value.filter(s => s._sel && s.enabled).map(s => s.id);
      if (!ids.length) { showToast('请先勾选要生成的订阅', true); return; }
      digestRunning.value = true;
      desktopStatus('正在生成周报…');
      try {
        const r = await fetch('/api/digest/run?token=' + encodeURIComponent(token.value), {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ sub_ids: ids }),
        });
        const d = await r.json();
        if (!r.ok) { showToast(d.detail || '生成失败', true); return; }
        showToast(`✅ 已生成 ${d.generated}/${d.total} 份报告`);
        newReports.value = 0;
        await loadReports();
      } catch (e) { showToast('出错：' + e.message, true); }
      finally { digestRunning.value = false; desktopStatus(''); }
    }

    // ---------- 桌面版：托盘调用的 hook（外壳用 window.evaluate_js 触发；网页版存在但无害）----------
    // ★ 为什么托盘动作走这里而不是外壳自己发请求：前端已有「取订阅 → 生成 → 刷新列表 → 提示」
    //   的完整逻辑，外壳重写一遍会重复实现，还会把 token 搬到页面之外。
    async function desktopRunDigest() {
      if (!token.value) { showToast('请先登录再生成周报', true); return; }
      if (!subs.value.length) { try { await loadSubs(); } catch (e) {} }
      const ids = subs.value.filter(s => s.enabled).map(s => s.id);   // 托盘入口：默认全部启用的订阅
      if (!ids.length) { showToast('没有启用的订阅，请先去「报告」页配置', true); return; }
      digestRunning.value = true;
      desktopStatus('正在生成周报…');
      try {
        const r = await fetch('/api/digest/run?token=' + encodeURIComponent(token.value), {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ sub_ids: ids }),
        });
        const d = await r.json();
        if (!r.ok) { showToast(d.detail || '生成失败', true); return; }
        showToast(`✅ 已生成 ${d.generated}/${d.total} 份报告`);
        newReports.value = 0;
        await loadReports();
        const _api = desktopApi();
        if (_api && _api.notify && document.hidden) { try { _api.notify('📰 周报已生成', `共 ${d.generated} 份`); } catch (e) {} }
      } catch (e) { showToast('出错：' + e.message, true); }
      finally { digestRunning.value = false; desktopStatus(''); }
    }

    window.__zxDesktop = {
      version: 1,
      usesLocalStorage: _USES_LOCAL_STORAGE,
      account: () => ({ username: username.value, role: role.value, token: !!token.value }),
      runDigest: () => desktopRunDigest(),
      goto: (v) => go(v),
      // 统一落盘入口也挂出来：托盘/外壳与自动化测试都走同一条路径（不绕开原生「另存为」）
      saveFromUrl: (url, name) => saveFromUrl(url, name),
      downloadReport: (r, fmt) => downloadReport(r, fmt),
    };

    function showToast(msg, isErr) {
      toast.value = msg; toastErr.value = !!isErr;
      clearTimeout(toastTimer); toastTimer = setTimeout(() => toast.value = '', 3500);
    }

    // ---------- 路由 ----------
    function go(v) {
      if (v === 'shell') { skTab.value = 'shell'; v = 'skills'; }   // #/shell → CLI 面板 tab
      location.hash = '#/' + v; view.value = v; if (v === 'chat') { nextTick(scrollToBottom); loadPriceSummary(); loadPriceAlerts(); } if (v === 'kb') loadKbStatus(); if (v === 'skills') loadSkills();
    }
    window.addEventListener('hashchange', () => {
      view.value = location.hash.replace('#/', '') || 'home';
      if (view.value === 'shell') { skTab.value = 'shell'; view.value = 'skills'; }
      if (view.value === 'chat') nextTick(scrollToBottom);
      if (view.value === 'reports') { newReports.value = 0; loadReports(); loadSubs(); }
      if (view.value === 'skills') loadSkills();
    });

    // ---------- 简报渲染 ----------
    function splitSections(text) {
      if (!text) return [];
      const re = /(①|②|③|④|⑤|^#{1,3}\s*[^\n]+)/gm;
      const marks = []; let m;
      while ((m = re.exec(text)) !== null) marks.push({ idx: m.index, title: m[0].trim() });
      if (marks.length === 0) return [{ title: '内容', body: text }];
      const secs = [];
      if (marks[0].idx > 0) { const intro = text.slice(0, marks[0].idx).trim(); if (intro) secs.push({ title: '概述', body: intro }); }
      for (let i = 0; i < marks.length; i++) {
        const start = marks[i].idx + marks[i].title.length;
        const end = i + 1 < marks.length ? marks[i + 1].idx : text.length;
        secs.push({ title: marks[i].title.replace(/^#+\s*/, ''), body: text.slice(start, end).trim() });
      }
      return secs;
    }
    function renderRich(text) {
      if (!text) return '';
      let h = text.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
      h = h.replace(/🔍\s*网络/g,'<span class="tag-net">🔍网络</span>')
           .replace(/🗄️?\s*数据库/g,'<span class="tag-db">🗄️数据库</span>')
           .replace(/📚\s*知识库/g,'<span class="tag-kb">📚知识库</span>')
           .replace(/🗄️?\s*个人库/g,'<span class="tag-db">🗄️个人库</span>');
      const lines = h.split('\n'); let out = [], inTable = false;
      for (let ln of lines) {
        const tline = ln.trim();
        if (tline.startsWith('|') && tline.endsWith('|')) {
          if (/^\|[\s:\-|]+\|$/.test(tline)) continue;
          if (!inTable) { out.push('<table class="mdt">'); inTable = true; }
          out.push('<tr>' + tline.split('|').slice(1, -1).map(c => `<td>${c.trim()}</td>`).join('') + '</tr>');
        } else {
          if (inTable) { out.push('</table>'); inTable = false; }
          out.push(ln);
        }
      }
      if (inTable) out.push('</table>');
      return out.join('\n').replace(/\n/g, '<br/>');
    }
    function nowStr() { return new Date().toLocaleTimeString('zh-CN', { hour12: false }); }

    // ---------- 数据加载 ----------
    async function api(method, url, body) {
      const opt = { method, headers: { 'Content-Type': 'application/json' } };
      if (body !== undefined) opt.body = JSON.stringify(body);
      const r = await fetch(url, opt);
      if (r.status === 401) { forceLogout('登录已失效，请重新登录'); return null; }
      if (!r.ok) { showToast(((await r.json().catch(()=>({})))).detail || '操作失败', true); return null; }
      return r.json();
    }
    async function loadMyData() {
      try {
        const r = await fetch('/api/me/overview?token=' + encodeURIComponent(token.value));
        // ★ 401 不能静默 return：那会让"登录已失效"表现成"数据全是 0"（用户实测报障）
        if (r.status === 401) { forceLogout('登录已失效，请重新登录'); return; }
        if (!r.ok) return;
        const d = await r.json();
        d.collection = d.products || [];
        if (role.value === 'company') d.products = d.products || [];
        // 展开 attributes JSON 到可编辑字段（公司产品 + 个人收藏）
        [...(d.products || []), ...(d.collection || [])].forEach(unpackAttrs);
        myData.value = d;
        showOnboard.value = !d.setup_done;
        if (role.value === 'company') loadProfile();
      } catch (e) {}
    }
    async function loadProfile() {
      try {
        const r = await fetch('/api/me/profile?token=' + encodeURIComponent(token.value));
        if (!r.ok) return;
        const d = await r.json();
        for (const k of Object.keys(prof)) {
          if (k === 'honorsText') {
            let honors = d.honors || '';
            try { honors = JSON.parse(honors).join('\n'); } catch (e) {}
            prof.honorsText = Array.isArray(honors) ? honors.join('\n') : honors;
          } else prof[k] = d[k] || '';
        }
      } catch (e) {}
    }
    async function saveProfile() {
      const tk = encodeURIComponent(token.value);
      let honorsJson = null;
      const lines = prof.honorsText.split('\n').map(s=>s.trim()).filter(Boolean);
      if (lines.length) honorsJson = JSON.stringify(lines);
      const okResp = await api('POST', '/api/me/profile?token=' + tk, {
        company_name: prof.company_name || null, intro: prof.intro || null,
        legal_rep: prof.legal_rep || null, reg_capital: prof.reg_capital || null,
        founded_date: prof.founded_date || null, employee_scale: prof.employee_scale || null,
        website: prof.website || null, hq_city: prof.hq_city || null,
        industry: prof.industry || null, business_scope: prof.business_scope || null,
        contact: prof.contact || null, address: prof.address || null,
        honors: honorsJson, note: prof.note || null,
      });
      if (okResp) { profSaved.value = true; setTimeout(() => profSaved.value = false, 2500); await loadMyData(); showToast('公司信息已保存 ✅'); }
    }

    // ---------- CRUD ----------
    function openBatch(type) {
      batchModal.type = type;
      batchModal.text = '';
      batchModal.show = true;
    }
    async function submitBatch() {
      const type = batchModal.type;
      const lines = batchModal.text;
      if (!lines.trim()) { showToast('请先粘贴内容', true); return; }
      const url = type === 'competitors' ? '/api/me/competitors/batch' : '/api/me/collection/batch';
      try {
        const r = await fetch(url + '?token=' + encodeURIComponent(token.value), {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ lines: lines }) });
        const d = await r.json();
        if (!r.ok) { showToast(d.detail || '导入失败', true); return; }
        showToast('已导入 ' + (d.added || 0) + ' 条 ✅');
        batchModal.show = false;
        await loadMyData();
      } catch (e) { showToast('导入出错：' + e.message, true); }
    }
    async function delItem(type, id) {
      const pathMap = { interests:'interests', watchlist:'watchlist', collection:'collection',
                        competitors:'competitors', products:'products' };
      if (await api('DELETE', `/api/me/${pathMap[type]}/${id}?token=` + encodeURIComponent(token.value))) {
        await loadMyData(); showToast('已删除');
      }
    }
    async function saveRow(url, payload, after) {
      const tk = encodeURIComponent(token.value);
      if (await api('PUT', `/api/me/${url}?token=${tk}`, payload)) {
        await loadMyData(); showToast('已保存 ✅');
      }
    }
    const saveInterest = (i) => saveRow(`interests/${i.id}`,
      { interest_tag: i.interest_tag, description: i.description || null, keywords: i.keywords || null });
    const saveWatch = (w) => saveRow(`watchlist/${w.id}`,
      { brand: w.brand || null, product: w.product || null, note: w.note || null });
    const saveCollection = (p) => saveRow(`collection/${p.id}`,
      { product_name: p.product_name, brand: p.brand || null, category: p.category || null,
        price: p.price ?? null,
        attributes: packAttrs(p, ['_specs', '_note2']) });
    const saveCompetitor = (c) => saveRow(`competitors-meta/${c.id}`,
      { comp_name: c.comp_name, website: c.website || null,
        is_competitor: !!c.is_competitor, note: c.note || null,
        mapped_product_ids: typeof c.mapped_product_ids === 'string' ? c.mapped_product_ids : null });
    // 把描述/卖点/目标客户打包进 attributes JSON（后端零 schema 变更，Agent 可读）
    function packAttrs(obj, keys) {
      const a = {};
      for (const k of keys) { const v = (obj[k] ?? '').toString().trim(); if (v) a[k.slice(1)] = v; }
      return Object.keys(a).length ? JSON.stringify(a) : null;
    }
    function unpackAttrs(row) {
      // 从 attributes JSON 展开到 _description/_features/_target/_specs/_note2 便于编辑
      let a = {};
      try { a = row.attributes ? JSON.parse(row.attributes) : {}; } catch (e) {}
      row._description = a.description ?? '';
      // 兼容新旧 key：新数据写 features/target，早期数据是 core_features/target_customers
      row._features = a.features ?? a.core_features ?? '';
      row._target = a.target ?? a.target_customers ?? '';
      row._specs = a.specs ?? '';
      if (!row._note2 && typeof row.note === 'string') row._note2 = row.note;
    }
    const saveCProduct = (p) => saveRow(`products/${p.id}`,
      { product_name: p.product_name, category: p.category || null, price: p.price ?? null,
        currency: p.currency || 'CNY', status: p.status || 'active',
        attributes: packAttrs(p, ['_description', '_features', '_target']) });

    async function addInterest() {
      const f = newInterest.value;
      if (!f.tag.trim()) return;
      if (await api('POST', '/api/me/interests?token=' + encodeURIComponent(token.value),
          { interest_tag: f.tag.trim(), description: f.description || null, keywords: f.keywords || null })) {
        newInterest.value = { tag:'', description:'', keywords:'' };
        await loadMyData(); showToast('兴趣已添加 ✅');
      }
    }
    async function addItemSimple(type) {
      if (type === 'watchlist') {
        if (!(newWatch.brand || newWatch.product)) return;
        if (await api('POST', '/api/me/watchlist?token=' + encodeURIComponent(token.value),
            { brand: newWatch.brand || null, product: newWatch.product || null, note: newWatch.note || null })) {
          await loadMyData(); showToast('已添加 ✅');
        }
      } else {
        if (!newWatch.product && !newWatch.brand) return;
        if (await api('POST', '/api/me/collection?token=' + encodeURIComponent(token.value),
            { product_name: newWatch.product || newWatch.brand, brand: newWatch.brand || null,
              category: newWatch.category || null,
              // Q6：勾了「复杂价格规则」就走 price_kind=rule（数值列由后端置空）
              price: newWatch._complex ? null : (newWatch.price ?? null),
              price_kind: newWatch._complex ? 'rule' : 'simple',
              price_text: newWatch._complex ? (newWatch._priceText || '') : '',
              attributes: packAttrs(newWatch, ['_specs', '_note2']) })) {
          Object.assign(newWatch, { price:null, _complex:false, _priceText:'', _specs:'', _note2:'' });
          await loadMyData(); showToast('已收藏 ✅');
        }
      }
    }
    async function addCompetitor() {
      if (!newComp.comp_name.trim()) return;
      if (await api('POST', '/api/me/competitors?token=' + encodeURIComponent(token.value),
          { comp_name: newComp.comp_name.trim(), website: newComp.website || null, note: newComp.note || null })) {
        Object.assign(newComp, { comp_name:'', website:'', note:'' });
        await loadMyData(); showToast('竞品已添加 ✅');
      }
    }
    async function addProduct() {
      if (!newProd.product_name.trim()) return;
      if (await api('POST', '/api/me/products?token=' + encodeURIComponent(token.value),
          { product_name: newProd.product_name.trim(), category: newProd.category || null,
            price: newProd._complex ? null : (newProd.price ?? null),
            price_kind: newProd._complex ? 'rule' : 'simple',
            price_text: newProd._complex ? (newProd._priceText || '') : '',
            attributes: packAttrs(newProd, ['_description', '_features', '_target']) })) {
        Object.assign(newProd, { product_name:'', category:'', price:null, _complex:false, _priceText:'',
                                 _description:'', _features:'', _target:'' });
        await loadMyData(); showToast('产品已添加 ✅');
      }
    }

    // ---------- 引导 ----------
    async function finishOnboard() {
      if (role.value === 'company') {
        if (onboardCompany.value.trim())
          await api('POST', '/api/me/profile?token=' + encodeURIComponent(token.value), { company_name: onboardCompany.value.trim() });
        for (const c of onboardComps.value)
          if (c.name.trim()) await api('POST', '/api/me/competitors?token=' + encodeURIComponent(token.value), { comp_name: c.name.trim() });
      } else {
        for (const it of onboardInterests.value)
          if (it.name.trim()) await api('POST', '/api/me/interests?token=' + encodeURIComponent(token.value), { interest_tag: it.name.trim() });
      }
      showOnboard.value = false;
      await loadMyData(); showToast('初始设置完成 ✅');
    }
    function skipOnboard() { showOnboard.value = false; }

    // ---------- WS ----------
    let wsTarget = '';  // 当前 WS 连接的 thread_id（用于会话切换时判断是否需要重连）
    function connectWS(threadId) {
      const proto = location.protocol === 'https:' ? 'wss' : 'ws';
      const target = threadId || username.value;  // 缺省=username（v1.0 兼容：直发走用户会话）
      if (ws && wsTarget === target && ws.readyState === WebSocket.OPEN) return;  // 已连对目标
      if (ws) { try { ws.close(); } catch (e) {} }
      wsTarget = target;
      ws = new WebSocket(`${proto}://${location.host}/ws/${encodeURIComponent(target)}`);
      ws.onmessage = (ev) => {
        try {
          const p = JSON.parse(ev.data);
          if (p.type === 'monitor_event') {
          if (p.event === 'tool_start') scheduleUsageRefresh();   // M4-5：工具一开跑就安排刷新
            if (p.event === 'thinking') {
              // 思考过程：追加到独立折叠区（小字灰色），不刷监控列表
              thinkingText.value += (p.data && p.data.text) || '';
              return;
            }
            const cls = p.event === 'tool_start' ? 'tool' : p.event === 'assistant_call' ? 'assistant' : p.event === 'task_result' ? 'task' : '';
            monitor.value.unshift({ time: nowStr(), msg: p.message, cls });
            if (monitor.value.length > 60) monitor.value.pop();
            if (p.event === 'report_ready') {
              newReports.value++; showToast(p.message); loadReports();
              desktopStatus('');            // 托盘进度收回空闲（定时/手动生成完成都走这里）
              // 桌面版：窗口不在前台时（收在托盘）弹系统通知——网页 toast 这时候根本看不见
              const _api = desktopApi();
              if (_api && _api.notify && document.hidden) {
                try { _api.notify('📰 周报已生成', p.message || ''); } catch (e) {}
              }
            }
          }
        } catch (e) {}
      };
    }

    // ---------- 登录 / 注册（两张卡分开：登录绝不建号；注册要确认密码，且实时回显将创建的账号） ----------
    const authMode = ref('login');          // 'login' | 'register'
    const regPwd2 = ref('');                // 注册卡的「确认密码」
    const notExist = ref(false);            // 登录遇 404（账号不存在）→ 给出「去注册」引导
    const regReady = computed(() => !!((username.value || '').trim() && password.value && regPwd2.value
                                       && password.value === regPwd2.value && password.value.length >= 6));
    const regHint = computed(() => {
      const u = (username.value || '').trim();
      const role = regRole.value === 'company' ? '公司账号' : '个人账号';
      if (!u) return '填好用户名、密码与确认密码后点「创建账号」';
      return '即将创建：' + u + '（' + role + '）—— 请再核对一遍用户名，写错了会真的建出这个账号';
    });
    function switchAuth(m) {
      authMode.value = m; errMsg.value = ''; notExist.value = false;
      if (m === 'register') { password.value = ''; regPwd2.value = ''; }   // 不把登录密码当注册密码误提交
    }
    function goRegister() { switchAuth('register'); }

    async function doLogin() {
      errMsg.value = '';
      notExist.value = false;
      if (!username.value || !password.value) { errMsg.value = '请填写用户名和密码'; return; }
      busy.value = true;
      try {
        const r = await fetch('/api/login', { method:'POST', headers:{'Content-Type':'application/json'},
          body: JSON.stringify({ username: username.value, password: password.value }) });
        const data = await r.json().catch(() => ({}));
        // 后端语义（2026-09-16 起）：404 = 账号不存在；401 = 密码错误。
        // 这里**只提示，绝不自动注册**——旧版就是在这里自动建号，错别字会静默变成账号。
        if (r.status === 404) {
          notExist.value = true;
          errMsg.value = '账号「' + (username.value || '').trim() + '」不存在';
          return;
        }
        if (!r.ok) { errMsg.value = data.detail || '登录失败'; return; }
        token.value = data.token; role.value = data.account.role;
        storeSet('zx_token', data.token);
        storeSet('zx_user', data.account.username);
        storeSet('zx_role', data.account.role);
        go('home');
        connectWS(); loadMyData();
        loadReports(); loadSubs(); loadProviders(); loadSouls(); loadMemory(); loadVoicePacks();
        loadSessions();  // M1 会话列表
        loadKbStatus();  // M3 知识库
        restoreBg();  // 登录后恢复该账号背景
      } catch (e) { errMsg.value = '网络错误：' + e.message; }
      finally { busy.value = false; }
    }

    async function doRegister() {
      errMsg.value = '';
      const u = (username.value || '').trim();
      if (!u) { errMsg.value = '请填写用户名'; return; }
      if (/\s/.test(u)) { errMsg.value = '用户名不能包含空格'; return; }
      if (!password.value || password.value.length < 6) { errMsg.value = '密码至少 6 位'; return; }
      if (password.value !== regPwd2.value) { errMsg.value = '两次输入的密码不一致'; return; }
      busy.value = true;
      try {
        const r = await fetch('/api/register', { method:'POST', headers:{'Content-Type':'application/json'},
          body: JSON.stringify({ username: u, password: password.value, role: regRole.value }) });
        const d = await r.json().catch(() => ({}));
        if (r.status === 409) {
          errMsg.value = '用户名「' + u + '」已被注册，请直接登录';
          return;
        }
        if (!r.ok) {
          errMsg.value = typeof d.detail === 'string' ? d.detail : '注册失败';
          return;
        }
        // 用户拍板：注册成功后不自动登录 —— 回到登录卡，让用户用刚设的密码亲手登一次（复核的机会）
        authMode.value = 'login';
        username.value = u;
        resetPriceState();          // ★ 登录：先清干净，再按新账号重新加载
        password.value = ''; regPwd2.value = '';
        errMsg.value = '';
        showToast('账号「' + u + '」已创建，请用刚设置的密码登录');
      } catch (e) { errMsg.value = '网络错误：' + e.message; }
      finally { busy.value = false; }
    }

    function doLogout() {
      fetch('/api/logout?token=' + encodeURIComponent(token.value)).catch(()=>{});
      if (ws) ws.close(); wsTarget = '';
      token.value = ''; role.value = 'personal';
      storeClear();
      // 登出即清除页面背景（不删 localStorage key，重新登录时按账号恢复）
      document.body.style.removeProperty('--user-bg');
      document.body.style.setProperty('--bg-opacity', '.6');
      bgActive.value = '';
      messages.value = []; monitor.value = []; thinkingText.value = '';
      sessions.value = []; activeThread.value = '';
      kbStatus.value = null; kbExportShow.value = false; kbIngestShow.value = false;
      myData.value = emptyMy();
    }

    // ---------- 背景设置（服务端持久化图库）----------
    function applyBg() {
      const b = document.body;
      b.style.setProperty('--bg-opacity', (bgOpacity.value / 100).toFixed(2));
      if (username.value) localStorage.setItem('bgOpacity_' + username.value, String(bgOpacity.value));
    }
    function setUserBg(url) {
      document.body.style.setProperty('--user-bg', `url("${url}")`);
      bgActive.value = url;
      if (username.value) localStorage.setItem('userBg_' + username.value, url);
    }
    function toggleBgPanel() {
      showBgPanel.value = !showBgPanel.value;
      if (showBgPanel.value) loadBgList();  // 每次打开都重新拉取图库（服务重启后 token 变化也能刷新）
    }
    async function loadBgList() {
      try {
        const r = await fetch('/api/bg/list?token=' + encodeURIComponent(token.value));
        const d = await r.json();
        bgImages.value = d.images || [];
        const saved = localStorage.getItem('userBg');
        if (saved && bgImages.value.some(i => i.url === saved)) { bgActive.value = saved; }
        else if (bgImages.value.length) { bgActive.value = bgImages.value[0].url; }
      } catch (e) {}
    }
    async function onBgUpload(ev) {
      const f = ev.target.files && ev.target.files[0];
      if (!f) return;
      const fd = new FormData();
      fd.append('file', f);
      try {
        const r = await fetch('/api/bg/upload?token=' + encodeURIComponent(token.value), { method: 'POST', body: fd });
        const d = await r.json();
        if (!r.ok) { showToast(d.detail || '上传失败', true); return; }
        setUserBg(d.url);
        applyBg();
        await loadBgList();
        showToast('背景图片已上传并应用 ✅');
      } catch (e) { showToast('上传出错：' + e.message, true); }
      ev.target.value = '';
    }
    function pickBg(url) {
      setUserBg(url); applyBg(); showToast('已应用该背景');
    }
    async function delBg(name) {
      if (!confirm('删除这张背景图？')) return;
      try {
        const r = await fetch('/api/bg/' + encodeURIComponent(name) + '?token=' + encodeURIComponent(token.value), { method: 'DELETE' });
        if (!r.ok) { showToast('删除失败', true); return; }
        const wasActive = bgActive.value && bgActive.value.endsWith('/' + name);
        await loadBgList();
        if (wasActive) {
          document.body.style.removeProperty('--user-bg');
          bgActive.value = ''; if (username.value) localStorage.removeItem('userBg_' + username.value);
        }
        showToast('已删除');
      } catch (e) { showToast('删除出错：' + e.message, true); }
    }
    function resetBg() {
      document.body.style.removeProperty('--user-bg');
      document.body.style.setProperty('--bg-opacity', '.6');
      bgOpacity.value = 60;
      bgActive.value = '';
      if (username.value) { localStorage.removeItem('userBg_' + username.value); localStorage.removeItem('bgOpacity_' + username.value); }
      showToast('已恢复默认背景');
    }
    function restoreBg() {
      // 未登录（或未确定账号）一律不显示任何用户背景，保持默认
      if (!token.value || !username.value) {
        document.body.style.removeProperty('--user-bg');
        bgActive.value = '';
        return;
      }
      // 兼容迁移：旧版全局 key userBg（未按账号隔离）——若新 key 不存在则回退旧 key，
      // setUserBg 会自动写入新 key，老用户登录一次即完成迁移
      let saved = localStorage.getItem('userBg_' + username.value);
      if (!saved) saved = localStorage.getItem('userBg');
      let opStr = localStorage.getItem('bgOpacity_' + username.value) || localStorage.getItem('bgOpacity') || '60';
      const op = parseInt(opStr, 10);
      if (saved) { setUserBg(saved); }
      else { document.body.style.removeProperty('--user-bg'); bgActive.value = ''; }
      bgOpacity.value = isNaN(op) ? 60 : Math.min(100, Math.max(5, op));
      applyBg();
      loadBgList();
    }

    // ---------- 工具来源（v3.0 M1：API/MCP 能力的配置面） ----------
    //  ★ 设计对齐「自定义 CLI」：**常态**只显示 指导栏 + 已配置来源卡（含它下面每个工具的只读列表）；
    //    表单与导入只在弹窗里出现（tsModal）。这样列表干净、也不会出现"源标题和工具列表被表单隔开"。
    const TS_FORM_EMPTY = { slug: '', name: '', base_url: '', auth_type: 'none', auth_name: '',
      auth_prefix: '', auth_secret: '', timeout: 30, headers_json: '{}', abilities: '', docs: '',
      intro: '', note: '', allow_private: true };   // M5c-2': intro = 手动粘贴的官方介绍文案
    const tsSources = ref([]);
    const tsCaps = reactive({});        // slug → 该来源下的工具（含未确认候选）：卡片里常态列出
    const tsCandidates = ref([]);       // 导入弹窗里的候选（勾选用，默认一律不勾）
    const tsSpecText = ref('');
    const tsDiscoverSlug = ref('');
    const tsEditingId = ref(0);
    const tsBusy = ref(false);
    const tsModal = ref('');            // '' | 'source' | 'import' —— ★ 常态为空 = 页面上没有表单
    const tsModalSource = ref({});
    const tsForm = reactive({ ...TS_FORM_EMPTY });

    /** 指导栏上的统计（工具数优先用已拉到的明细，退回列表自带的计数）。 */
    const tsStats = computed(() => {
      const all = [].concat.apply([], Object.keys(tsCaps).map(k => tsCaps[k] || []));
      const confirmed = all.length ? all.filter(c => c.confirmed).length
        : tsSources.value.reduce((n, s) => n + (s.capability_count || 0), 0);
      const pending = all.length ? all.filter(c => !c.confirmed).length
        : tsSources.value.reduce((n, s) => n + (s.candidate_count || 0), 0);
      return { sources: tsSources.value.length, confirmed: confirmed, pending: pending,
               verified: tsSources.value.filter(s => s.state === 'verified').length };
    });

    function tsStateText(s) {
      const st = (s || {}).state;
      return st === 'verified' ? '✓ 已体检' : (st === 'failed' ? '✗ 体检失败' : '未体检');
    }
    function tsAuthText(s) {
      const t = ((s || {}).config || {}).auth_type || 'none';
      return t === 'none' ? '无鉴权' : (t === 'header' ? 'Header 里带' : (t === 'query' ? 'Query 参数里带' : t));
    }

    function tsResetForm() { Object.assign(tsForm, TS_FORM_EMPTY); tsEditingId.value = 0; }
    /** ＋ 新增 → 只把弹窗打开（表单不放页面上）。 */
    function tsNewSource() { tsResetForm(); tsModalSource.value = {}; tsModal.value = 'source'; }
    function tsCloseModal() { tsModal.value = ''; tsBusy.value = false; }

    /** 表单 → 请求体（纯函数）。校验失败抛错，由调用方转成 toast。 */
    function tsSourcePayload(f) {
      const slug = String(f.slug || '').trim();
      if (!/^[A-Za-z0-9][A-Za-z0-9_-]{0,40}$/.test(slug)) {
        throw new Error('短名只能用 ASCII 字母/数字/连字符/下划线（≤41 字，以字母或数字开头）');
      }
      const base = String(f.base_url || '').trim().replace(/\/+$/, '');
      if (!/^https?:\/\//i.test(base)) throw new Error('base_url 必须以 http:// 或 https:// 开头');
      let headers = {};
      try { headers = JSON.parse(f.headers_json || '{}'); } catch (e) { throw new Error('默认请求头必须是 JSON 对象'); }
      if (!headers || typeof headers !== 'object' || Array.isArray(headers)) throw new Error('默认请求头必须是 JSON 对象');
      return {
        slug: slug, name: String(f.name || '').trim(), base_url: base,
        auth_type: f.auth_type || 'none', auth_name: String(f.auth_name || '').trim(),
        auth_prefix: f.auth_prefix || '', auth_secret: f.auth_secret || '',
        headers_json: JSON.stringify(headers), timeout: Number(f.timeout) || 30, max_bytes: 1048576,
        allow_private: !!f.allow_private, abilities: String(f.abilities || '').trim(),
        docs: String(f.docs || '').trim(), intro: String(f.intro || '').trim(),
        note: String(f.note || '').trim(),
      };
    }

    /** 勾选状态 → 确认请求体（纯函数）：写操作必须额外勾"我确认这是写操作"才放行。 */
    function tsConfirmPayload(slug, cands, src) {
      const items = (cands || [])
        .filter(c => c && c._checked && (!c.is_write || c._approved))
        .map(c => ({ ref: c.ref, name: c.name || '', approve_write: !!c._approved }));
      // ★ source：两种来源可以同短名，且"写操作"的判定口径不同（api 看 method、mcp 看 read_only_hint）。
      return { slug: String(slug || '').trim(), items: items, source: src || 'api' };
    }

    function tsCandidateBadge(c) { return (c && c.is_write) ? '⚠️ 写操作' : '只读'; }
    function tsParamsText(c) { return (c && c.param_summary) ? c.param_summary : '无参数'; }

    const tsCheckedCount = computed(() => tsConfirmPayload(tsDiscoverSlug.value, tsCandidates.value, tsConfirmSource.value).items.length);

    /** 后端候选/能力条目 → 界面条目（纯函数，便于 harness 抽取测试）。 */
    function tsCandFromApi(c) {
      return {
        ref: c.ref, name: c.name || c.ref, method: c.method || '', url_template: c.url_template || '',
        read_only: !!c.read_only, is_write: !!c.is_write, param_summary: c.param_summary || '',
        confirmed: !!c.confirmed, warnings: c.warnings || [],
        // ★ M5c-2'：工具描述（abilities = 自动摘要；abilities_user = 用户手写/AI 草稿导入的）
        abilities: c.abilities || '', abilities_user: c.abilities_user || '',
        _checked: false, _approved: false,   // ★ 默认不勾：重新打开列表不该"顺手确认"
      };
    }

    // ===== M5c-2'：工具描述（用户可编辑 + AI 预写）—— API / MCP 两个页签共用 =====
    //   为什么单独一个弹窗：和来源配置、导入工具一样，编辑界面只在用户要求时出现（本页的既有范式）；
    //   两个页签共用同一份实现（各自只是工具列表的来源容器不同：tsCaps / mcpCaps）。
    const capModal = ref(false);
    const capKind = ref('api');          // api | mcp
    const capSource = ref({});
    const capItems = ref([]);            // [{ref,name,is_write,confirmed,abilities,abilities_user,text}]
    const capBusy = ref(false);
    const capDraftBusy = ref(false);
    const capDraftWarns = ref([]);

    /** 打开「工具描述」：先确保这个来源的工具列表已拉过（懒加载，与卡片同一份数据）。 */
    async function capOpen(s, kind) {
      capKind.value = kind === 'mcp' ? 'mcp' : 'api';
      capSource.value = s || {};
      capDraftWarns.value = [];
      const map = capKind.value === 'mcp' ? mcpCaps : tsCaps;
      if (!((map[s.slug] || []).length)) {
        try { await (capKind.value === 'mcp' ? mcpLoadCaps() : tsLoadCaps()); } catch (e) { /* 拉不到也能手写 */ }
      }
      capItems.value = (map[s.slug] || []).map(c => ({
        ref: c.ref, name: c.name, is_write: !!c.is_write, confirmed: !!c.confirmed,
        abilities: c.abilities || '', abilities_user: c.abilities_user || '',
        text: c.abilities_user || c.abilities || '',   // 预填：手写的优先，其次自动摘要（便于在原句上改）
      }));
      capModal.value = true;
    }

    function capClose() { capModal.value = false; }

    /** 手写了几条（与自动摘要逐字相同的不算"手写"，保存时会当作不覆盖）。 */
    function capCoveredText() {
      const n = capItems.value.filter(c => (c.text || '').trim() && (c.text || '').trim() !== (c.abilities || '').trim()).length;
      return '已手写 ' + n + ' / ' + capItems.value.length + ' 个';
    }

    function capClearAll() {
      capItems.value.forEach(c => { c.text = ''; });
      showToast('已清空 —— 保存后这些工具回到自动摘要');
    }

    /** ✨ AI 预写：按 README/文档 + 工具清单为每个工具生成描述草稿（**只填进输入框，不落库**）。 */
    /** 两个信息源是否至少有一个（与后端同一口径：都没有就不生成）。 */
    function capHasEvidence() {
      return !!((capSource.value.docs || '').trim() || (capSource.value.intro || '').trim());
    }

    async function capDraft() {
      if (capDraftBusy.value) return;
      if (!capHasEvidence()) {
        showToast('没有填信息来源，无法 AI 预生成 —— 先在来源配置里填「README / 文档地址」或贴「官方介绍文案」', true);
        return;
      }
      capDraftBusy.value = true;
      try {
        const r = await fetch('/api/tools/sources/ability_draft?token=' + encodeURIComponent(token.value), {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ source: capKind.value, slug: capSource.value.slug }),
        });
        const d = await r.json();
        if (!r.ok) { showToast(d.detail || 'AI 预写失败', true); return; }
        const got = {};
        (d.items || []).forEach(it => { got[it.ref] = it.text; });
        let filled = 0;
        capItems.value.forEach(c => { if (got[c.ref]) { c.text = got[c.ref]; filled += 1; } });
        capDraftWarns.value = d.warnings || [];
        const ev = d.evidence || {};
        const usedIntro = !!(ev.intro || {}).chars;
        const usedDoc = !!(ev.docs || {}).ok;
        const by = [usedIntro ? '官方文案' : '', usedDoc ? 'README' : ''].filter(Boolean).join(' + ');
        showToast('AI 预写了 ' + filled + ' 条' + (by ? '（依据 ' + by + '）' : '（只按工具名与参数保守写）'));
      } catch (e) { showToast('AI 预写出错：' + e.message, true); }
      finally { capDraftBusy.value = false; }
    }

    /** 💾 保存全部：写 `abilities_user`；与自动摘要逐字相同的按"不覆盖"处理（空串）。 */
    async function capSave() {
      if (capBusy.value) return;
      capBusy.value = true;
      try {
        const items = capItems.value.map(c => {
          const t = (c.text || '').trim();
          return { ref: c.ref, text: t === (c.abilities || '').trim() ? '' : t };
        });
        const r = await fetch('/api/tools/sources/cap_abilities?token=' + encodeURIComponent(token.value), {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ source: capKind.value, slug: capSource.value.slug, items: items }),
        });
        const d = await r.json();
        if (!r.ok) { showToast(d.detail || '保存失败', true); return; }
        showToast('已保存 ' + ((d.saved || []).length) + ' 条工具描述');
        capModal.value = false;
        await (capKind.value === 'mcp' ? mcpLoadCaps() : tsLoadCaps());
      } catch (e) { showToast('保存出错：' + e.message, true); }
      finally { capBusy.value = false; }
    }

    /** 逐个来源拉"工具 + 待确认候选"→ 卡片里常态列出（只读端点，来源数量很小）。 */
    async function tsLoadCaps() {
      await Promise.all(tsSources.value.map(async (s) => {
        try {
          const r = await fetch('/api/tools/source/' + s.id + '/candidates?token=' + encodeURIComponent(token.value));
          const d = await r.json();
          tsCaps[s.slug] = (d.items || []).map(tsCandFromApi);
        } catch (e) { tsCaps[s.slug] = tsCaps[s.slug] || []; }
      }));
    }

    async function tsLoad() {
      try {
        const r = await fetch('/api/tools/sources?token=' + encodeURIComponent(token.value));
        const d = await r.json();
        const items = d.items || [];
        // ★ 后端一次返回 api + mcp 两类来源 → **前端按来源分给两个 tab**。
        //   不在前端过滤的话，MCP 来源会串进 API 列表（M2 加来源时最容易漏的一处）。
        mcpAll.value = items;
        tsSources.value = items.filter(s => (s.source || 'api') === 'api');
        if (!tsDiscoverSlug.value && tsSources.value.length) tsDiscoverSlug.value = tsSources.value[0].slug;
        await Promise.all([tsLoadCaps(), mcpLoadCaps()]);
      } catch (e) {}
    }

    /** ✏️ 编辑 → 回填表单 + 打开弹窗（密钥留空 = 不修改）。 */
    function tsEdit(s) {
      const c = s.config || {};
      tsEditingId.value = s.id;
      Object.assign(tsForm, {
        slug: s.slug, name: s.name || '', base_url: c.base_url || '',
        auth_type: c.auth_type || 'none', auth_name: c.auth_name || '', auth_prefix: c.auth_prefix || '',
        auth_secret: '', timeout: c.timeout || 30, headers_json: JSON.stringify(c.headers || {}),
        abilities: s.abilities || '', docs: s.docs || '', intro: s.intro || '',
        note: s.note || '', allow_private: c.allow_private !== false,
      });
      tsModalSource.value = s;
      tsModal.value = 'source';
    }

    async function tsSave() {
      let body;
      try { body = tsSourcePayload(tsForm); } catch (e) { showToast(e.message, true); return; }
      const isNew = !tsEditingId.value;
      tsBusy.value = true;
      try {
        const r = await fetch('/api/tools/source/save?token=' + encodeURIComponent(token.value), {
          method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
        const d = await r.json();
        if (!r.ok) { showToast(d.detail || '保存失败', true); return; }
        tsCloseModal();
        await tsLoad();
        const saved = tsSources.value.find(x => x.slug === (d.slug || body.slug)) || null;
        if (isNew && saved) {
          showToast('来源已保存 ✅ 接着贴 OpenAPI 文档导入工具');
          await tsOpenImport(saved);      // 新建后直接进导入弹窗，少点一次
        } else {
          showToast('已保存 ✅');
        }
      } catch (e) { showToast('保存出错：' + e.message, true); }
      finally { tsBusy.value = false; }
    }

    async function tsDel(s) {
      if (!confirm('删除来源「' + (s.name || s.slug) + '」？它下面已确认的工具会一起删掉（不可恢复）')) return;
      try {
        const r = await fetch('/api/tools/source/' + s.id + '?token=' + encodeURIComponent(token.value), { method: 'DELETE' });
        const d = await r.json();
        if (!r.ok) { showToast(d.detail || '删除失败', true); return; }
        showToast('已删除（连带 ' + (d.deleted_capabilities || 0) + ' 个工具）');
        tsCandidates.value = [];
        try { delete tsCaps[s.slug]; } catch (e) {}
        await tsLoad();
      } catch (e) { showToast('删除出错：' + e.message, true); }
    }

    async function tsTest(s) {
      tsBusy.value = true;
      try {
        const r = await fetch('/api/tools/source/' + s.id + '/test?token=' + encodeURIComponent(token.value), { method: 'POST' });
        const d = await r.json();
        const bad = (d.steps || []).filter(x => !x.ok);
        showToast(d.ok ? '体检通过 ✅' : ('体检未通过：' + (bad[0] ? (bad[0].name + ' — ' + bad[0].detail) : '见详情')), !d.ok);
        await tsLoad();
      } catch (e) { showToast('体检出错：' + e.message, true); }
      finally { tsBusy.value = false; }
    }

    /** 拉某个来源下**已落库**的工具与待确认候选（弹窗里用；不必重贴 OpenAPI 文档）。 */
    async function tsViewCandidates(s, quiet) {
      tsBusy.value = true;
      try {
        const r = await fetch('/api/tools/source/' + s.id + '/candidates?token=' + encodeURIComponent(token.value));
        const d = await r.json();
        if (!r.ok) { showToast(d.detail || '读取工具列表失败', true); return; }
        tsDiscoverSlug.value = d.slug || s.slug;
        tsCandidates.value = (d.items || []).map(tsCandFromApi);
        const pend = tsCandidates.value.filter(c => !c.confirmed).length;
        if (!quiet) {
          showToast((s.name || s.slug) + '：共 ' + tsCandidates.value.length + ' 个工具'
            + (pend ? ('，待确认 ' + pend + ' 个 —— 勾选后点「确认勾选」') : '（都已确认）'));
        }
      } catch (e) { showToast('读取工具列表出错：' + e.message, true); }
      finally { tsBusy.value = false; }
    }

    /** 打开「导入 / 查看工具」弹窗：先读回已落库的工具，再让用户贴文档。 */
    async function tsOpenImport(s) {
      tsConfirmSource.value = 'api';
      tsModalSource.value = s || {};
      tsSpecText.value = '';
      tsCandidates.value = [];
      tsModal.value = 'import';
      if (s && s.slug) {
        tsDiscoverSlug.value = s.slug;
        await tsViewCandidates(s, true);
      }
    }

    async function tsDiscover() {
      tsConfirmSource.value = 'api';
      const text = String(tsSpecText.value || '').trim();
      if (!text) { showToast('请先粘贴 OpenAPI 文档（JSON 或 YAML）', true); return; }
      if (!tsDiscoverSlug.value) { showToast('请先选择一个来源（或先新增来源）', true); return; }
      tsBusy.value = true;
      try {
        const r = await fetch('/api/tools/discover?token=' + encodeURIComponent(token.value), {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ slug: tsDiscoverSlug.value, text: text }) });
        const d = await r.json();
        if (!d.ok) { showToast('解析失败：' + (d.error || '未知错误'), true); return; }
        tsCandidates.value = (d.items || []).map(c => ({ ...c, _checked: !!c.read_only, _approved: false }));
        showToast('解析到 ' + tsCandidates.value.length + ' 个操作'
          + (d.kept_confirmed ? ('（其中 ' + d.kept_confirmed + ' 个已确认，人工设置保留）') : '')
          + '；勾选后点「确认勾选」');
        await tsLoad();
      } catch (e) { showToast('解析出错：' + e.message, true); }
      finally { tsBusy.value = false; }
    }

    async function tsConfirm() {
      const payload = tsConfirmPayload(tsDiscoverSlug.value, tsCandidates.value, tsConfirmSource.value);
      if (!payload.items.length) { showToast('没有勾选任何操作（写操作还需勾"我确认这是写操作"）', true); return; }
      tsBusy.value = true;
      try {
        const r = await fetch('/api/tools/confirm?token=' + encodeURIComponent(token.value), {
          method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
        const d = await r.json();
        if (!r.ok) { showToast(d.detail || '确认失败', true); return; }
        showToast('已确认 ' + (d.confirmed || []).length + ' 个'
          + ((d.skipped || []).length ? ('，跳过 ' + d.skipped.length + ' 个（' + d.skipped[0].why + '）') : ''));
        await tsLoad();                                   // 卡片里的工具列表跟着更新
        if (tsModal.value === 'import' && (tsModalSource.value || {}).id) {
          await tsViewCandidates(tsModalSource.value, true);   // 弹窗里刷新"已确认"标记（静默）
        }
      } catch (e) { showToast('确认出错：' + e.message, true); }
      finally { tsBusy.value = false; }
    }

    // ---------- MCP 工具来源（v3.0 M2）----------
    //  ★ 范式与 API 完全一致（用户 2026-09-26 定）：常态 = 指导栏 + 来源卡（工具列表长在卡内）；
    //    表单与"发现结果"只在弹窗里出现。
    //  ★ 与 API 共用的部分：候选视图 / 勾选 / 确认（tsCandidates、tsConfirmPayload、tsConfirm）——
    //    只在确认请求里多带一个 source='mcp'（后端据此判"写操作"的口径不同）。
    const MCP_FORM_EMPTY = { slug: '', name: '', transport: 'stdio', command: 'npx',
      args_json: '["-y","@modelcontextprotocol/server-everything"]', env_json: '{}', cwd: '',
      url: '', timeout: 60, allow_stdio_commands: '', abilities: '', docs: '', intro: '', note: '',
      allow_private: true,
      assume_read_only: false };     // M5c-1：来源级「该服务全是只读」声明；intro = 官方介绍文案（M5c-2'）
    const mcpAll = ref([]);            // 后端返回的全部来源（api + mcp）；两个 tab 各取所需
    const mcpCaps = reactive({});      // slug → 该 MCP 服务里的工具（含未确认候选）
    const mcpForm = reactive({ ...MCP_FORM_EMPTY });
    const mcpEditingId = ref(0);
    const mcpBusy = ref(false);
    const mcpModal = ref('');          // '' | 'source' | 'probe' —— ★ 常态为空 = 页面上没有表单
    const mcpProbe = ref(null);        // 体检/发现结果：{ ok, steps, ms, tool_count }
    const mcpProbeTitle = ref('体检结果');
    const mcpModalSource = ref({});
    const tsConfirmSource = ref('api');   // 确认请求带哪个来源（api 流程='api'，mcp 流程='mcp'）

    const mcpSources = computed(() => (mcpAll.value || []).filter(s => (s.source || 'api') === 'mcp'));

    /** MCP 指导栏统计（与 API 那一份同口径，只是来源集合不同）。 */
    const mcpStats = computed(() => {
      const all = [].concat.apply([], Object.keys(mcpCaps).map(k => mcpCaps[k] || []));
      const confirmed = all.length ? all.filter(c => c.confirmed).length
        : mcpSources.value.reduce((n, s) => n + (s.capability_count || 0), 0);
      const pending = all.length ? all.filter(c => !c.confirmed).length
        : mcpSources.value.reduce((n, s) => n + (s.candidate_count || 0), 0);
      return { sources: mcpSources.value.length, confirmed: confirmed, pending: pending,
               verified: mcpSources.value.filter(s => s.state === 'verified').length };
    });

    function mcpTransportText(s) {
      const t = ((s || {}).config || {}).transport;
      return t === 'http' ? 'HTTP 端点' : '本地命令 (stdio)';
    }
    /** 卡片上那一行"到底连的是什么"：stdio 显示完整命令行，http 显示 URL。 */
    function mcpTargetText(s) {
      const c = (s || {}).config || {};
      if (c.transport === 'http') return c.url || '（未填 url）';
      return [c.command || ''].concat(c.args || []).join(' ') || '（未填命令）';
    }

    function mcpResetForm() { Object.assign(mcpForm, MCP_FORM_EMPTY); mcpEditingId.value = 0; }
    function mcpNewSource() { mcpResetForm(); mcpModalSource.value = {}; mcpModal.value = 'source'; }
    function mcpCloseModal() { mcpModal.value = ''; mcpBusy.value = false; }

    /** JSON 文本字段解析（纯函数）：报错必须说清**哪里不对**。 */
    function mcpJsonField(text, what, fallback) {
      const raw = String(text || '').trim() || fallback;
      try { return JSON.parse(raw); }
      catch (e) {
        // ★ 用户实测踩过：把 Windows 路径原样贴进来 → JSON 里 \L \m \b 都不是合法转义 → 解析直接失败，
        //   而旧提示只说"必须是 JSON 数组"，一个字都没提到反斜杠。这里把该知道的都写出来。
        throw new Error(what + '不是合法 JSON：' + ((e && e.message) || e) +
          '。Windows 路径请改用正斜杠，例如 ["D:/LLM/mcp_servers/bnf/bnf_server.py"]' +
          '（或用两个反斜杠转义）；只想填一个参数时方括号也要在，如 ["C:/x/server.py"]');
      }
    }

    /** 表单 → 请求体（纯函数）。校验失败抛错，由调用方转成 toast。 */
    function mcpPayload(f) {
      const slug = String(f.slug || '').trim();
      if (!/^[A-Za-z0-9][A-Za-z0-9_-]{0,40}$/.test(slug)) {
        throw new Error('短名只能用 ASCII 字母/数字/连字符/下划线（≤41 字，以字母或数字开头）');
      }
      const transport = String(f.transport || 'stdio').trim();
      if (transport !== 'stdio' && transport !== 'http') throw new Error('连接方式只支持 stdio / http');
      let args = [], env = {};
      if (transport === 'stdio') {
        if (!String(f.command || '').trim()) throw new Error('stdio 必须填命令（如 npx）');
        args = mcpJsonField(f.args_json, '参数（args）', '[]');
        if (!Array.isArray(args) || args.some(a => typeof a !== 'string')) {
          throw new Error('参数必须是字符串数组（JSON 数组里每个元素都要带引号），如 ["-y","pkg@latest"]');
        }
      } else {
        const u = String(f.url || '').trim();
        if (!/^https?:\/\//i.test(u)) throw new Error('端点 URL 必须以 http:// 或 https:// 开头');
      }
      env = mcpJsonField(f.env_json, '环境变量（env）', '{}');
      if (!env || typeof env !== 'object' || Array.isArray(env)) throw new Error('环境变量必须是 JSON 对象');
      return {
        source: 'mcp', slug: slug, name: String(f.name || '').trim(), transport: transport,
        command: String(f.command || '').trim(), args_json: JSON.stringify(args), env_json: JSON.stringify(env),
        cwd: String(f.cwd || '').trim(), url: String(f.url || '').trim(),
        timeout: Number(f.timeout) || 60, allow_stdio_commands: String(f.allow_stdio_commands || '').trim(),
        abilities: String(f.abilities || '').trim(), docs: String(f.docs || '').trim(),
        intro: String(f.intro || '').trim(),
        note: String(f.note || '').trim(), allow_private: !!f.allow_private,
        assume_read_only: !!f.assume_read_only,     // M5c-1（sync_readonly 由 mcpSave 视用户确认再补）
      };
    }

    /** 逐个 MCP 来源拉工具列表 → 卡片里常态列出。 */
    async function mcpLoadCaps() {
      await Promise.all(mcpSources.value.map(async (s) => {
        try {
          const r = await fetch('/api/tools/source/' + s.id + '/candidates?token=' + encodeURIComponent(token.value));
          const d = await r.json();
          mcpCaps[s.slug] = (d.items || []).map(tsCandFromApi);
        } catch (e) { mcpCaps[s.slug] = mcpCaps[s.slug] || []; }
      }));
    }

    /** ✏️ 编辑 → 回填表单 + 打开弹窗（env 留空 = 不修改已存的）。 */
    function mcpEdit(s) {
      const c = s.config || {};
      mcpEditingId.value = s.id;
      Object.assign(mcpForm, {
        slug: s.slug, name: s.name || '', transport: c.transport || 'stdio', command: c.command || 'npx',
        args_json: JSON.stringify(c.args || []), env_json: '{}', cwd: c.cwd || '', url: c.url || '',
        timeout: c.timeout || 60, allow_stdio_commands: (c.allow_stdio_commands || []).join(','),
        abilities: s.abilities || '', docs: s.docs || '', intro: s.intro || '',
        note: s.note || '', allow_private: c.allow_private !== false,
        assume_read_only: !!c.assume_read_only,     // M5c-1
      });
      mcpModalSource.value = s;
      mcpModal.value = 'source';
    }

    async function mcpSave() {
      let body;
      try { body = mcpPayload(mcpForm); } catch (e) { showToast(e.message, true); return; }
      const isNew = !mcpEditingId.value;
      // ★ M5c-1：勾了"全只读"就先把后果摊开 —— 尤其是"已确认的工具也会被刷新"这件事，
      //   因为它默认受 M1 铁律保护（已确认行保留人工决定），必须由用户显式点头。
      if (body.assume_read_only) {
        const ok = confirm('「' + (body.name || body.slug) + '」将按「全只读」对待：\n\n'
          + '· 该服务里【没声明只读】的工具立即按只读处理，进池后不再要求勾「我确认这是写操作」；\n'
          + '· 服务端【明确声明非只读】的工具不受影响，仍然要二次确认；\n'
          + '· 点「确定」= 连**已确认**的工具也一起刷新；点「取消」= 只保存开关，已确认的不动。\n\n'
          + '要现在就连已确认的工具一起刷新吗？');
        if (ok) body.sync_readonly = true;
      }
      mcpBusy.value = true;
      try {
        const r = await fetch('/api/tools/source/save?token=' + encodeURIComponent(token.value), {
          method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
        const d = await r.json();
        if (!r.ok) { showToast(d.detail || '保存失败', true); return; }
        mcpCloseModal();
        await tsLoad();
        const saved = mcpSources.value.find(x => x.slug === (d.slug || body.slug)) || null;
        if (d.readonly_synced) {
          showToast('已保存 ✅ 并按「全只读」刷新了 ' + d.readonly_synced + ' 个工具的只读标记');
          if (isNew && saved) await mcpDiscover(saved);
          return;
        }
        if (isNew && saved) {
          showToast('来源已保存 ✅ 接着点「发现工具」把它身上的工具列出来');
          await mcpDiscover(saved);          // 新建后直接发现，少点一次
        } else {
          showToast('已保存 ✅');
        }
      } catch (e) { showToast('保存出错：' + e.message, true); }
      finally { mcpBusy.value = false; }
    }

    async function mcpDel(s) {
      if (!confirm('删除来源「' + (s.name || s.slug) + '」？它下面已确认的工具会一起删掉（不可恢复）')) return;
      try {
        const r = await fetch('/api/tools/source/' + s.id + '?token=' + encodeURIComponent(token.value), { method: 'DELETE' });
        const d = await r.json();
        if (!r.ok) { showToast(d.detail || '删除失败', true); return; }
        showToast('已删除（连带 ' + (d.deleted_capabilities || 0) + ' 个工具）');
        await tsLoad();
      } catch (e) { showToast('删除出错：' + e.message, true); }
    }

    /** 🩺 体检：真连一次（握手 → 列工具 → 容错试一次），把七步结果摊开给用户看。 */
    async function mcpTest(s) {
      mcpModalSource.value = s;
      mcpProbeTitle.value = '体检结果';
      mcpBusy.value = true;
      try {
        const r = await fetch('/api/tools/source/' + s.id + '/test?token=' + encodeURIComponent(token.value), { method: 'POST' });
        const d = await r.json();
        if (!r.ok) { showToast(d.detail || '体检失败', true); return; }
        mcpProbe.value = { ok: d.ok, steps: d.steps || [], ms: d.ms || 0, tool_count: d.tool_count || 0 };
        mcpModal.value = 'probe';
        showToast(d.ok ? ('体检通过 ✅ 看到 ' + (d.tool_count || 0) + ' 个工具') : '体检未通过 ✗ 看弹窗里的哪一步红了', !d.ok);
        await tsLoad();                    // 状态徽标（已体检/体检失败）跟着更新
      } catch (e) { showToast('体检出错：' + e.message, true); }
      finally { mcpBusy.value = false; }
    }

    /** 🔍 发现工具：连上去列工具并落成候选（未确认不入池），顺手把体检结果一起展示。 */
    async function mcpDiscover(s, quiet) {
      mcpModalSource.value = s;
      mcpProbeTitle.value = '发现工具（含体检结果）';
      mcpBusy.value = true;
      try {
        const r = await fetch('/api/tools/discover?token=' + encodeURIComponent(token.value), {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ slug: s.slug, source: 'mcp' }) });
        const d = await r.json();
        mcpProbe.value = { ok: !!d.ok, steps: d.steps || [], ms: d.ms || 0, tool_count: (d.items || []).length };
        if (!d.ok) {
          mcpModal.value = 'probe';
          showToast('发现失败：' + (d.error || '未知错误'), true);
          return;
        }
        tsDiscoverSlug.value = s.slug;
        tsConfirmSource.value = 'mcp';
        tsCandidates.value = (d.items || []).map(c => ({ ...c, _checked: !!c.read_only, _approved: false }));
        mcpModal.value = 'probe';
        if (!quiet) {
          showToast('发现 ' + tsCandidates.value.length + ' 个工具'
            + (d.kept_confirmed ? ('（其中 ' + d.kept_confirmed + ' 个已确认，人工设置保留）') : '')
            + '；勾选后点「确认勾选」（只读默认已勾，写操作要额外确认）');
        }
        await tsLoad();
      } catch (e) { showToast('发现出错：' + e.message, true); }
      finally { mcpBusy.value = false; }
    }

    /** 弹窗里重读该来源的工具（确认之后刷新"已确认"标记用；不打服务，很快）。 */
    async function mcpReloadCandidates(s) {
      if (!(s || {}).id) return;
      try {
        const r = await fetch('/api/tools/source/' + s.id + '/candidates?token=' + encodeURIComponent(token.value));
        const d = await r.json();
        const old = {};
        (tsCandidates.value || []).forEach(c => { old[c.ref] = c; });
        tsCandidates.value = (d.items || []).map(tsCandFromApi).map(c => {
          const o = old[c.ref];
          return o ? { ...c, _checked: o._checked, _approved: o._approved } : c;
        });
      } catch (e) {}
    }

    /** 确认勾选（MCP）：走与 API 同一个 `tsConfirm`，只是确认请求里带 source='mcp'。 */
    async function mcpConfirm() {
      tsConfirmSource.value = 'mcp';
      await tsConfirm();
      await mcpReloadCandidates(mcpModalSource.value);
    }

    // ---------- 定制助手：模型选型 ----------
    async function loadProviders() {
      try {
        const r = await fetch('/api/llm/providers?token=' + encodeURIComponent(token.value));
        const d = await r.json();
        providers.value = d.providers || [];
      } catch (e) {}
    }
    async function addProvider() {
      const p = newProv.value;
      if (!p.provider_name.trim() || !p.model_name.trim() || !p.base_url.trim() || !p.api_key.trim()) {
        showToast('请填写完整的模型配置（名称/模型名/接口/Key）', true); return;
      }
      try {
        const r = await fetch('/api/llm/providers?token=' + encodeURIComponent(token.value), {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ provider_name: p.provider_name, model_name: p.model_name,
            base_url: p.base_url, api_key: p.api_key, is_active: p.is_active,
            price_in_cached: numOrNull(p.price_in_cached),
            price_in_uncached: numOrNull(p.price_in_uncached),
            price_out: numOrNull(p.price_out) }) });   // 空串要转 null：后端是 float|None，收不了 '' ✗
        const d = await r.json();
        if (!r.ok) { showToast(d.detail || '添加失败', true); return; }
        showToast('模型配置已添加 ✅');
        newProv.value = { provider_name: '', model_name: '', base_url: '', api_key: '', is_active: false,
                          price_in_cached: '', price_in_uncached: '', price_out: '' };
        loadProviders();
      } catch (e) { showToast('添加出错：' + e.message, true); }
    }
    async function saveProvider(p) {
      try {
        const r = await fetch('/api/llm/providers/' + p.id + '?token=' + encodeURIComponent(token.value), {
          method: 'PUT', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ provider_name: p.provider_name, model_name: p.model_name,
            base_url: p.base_url, api_key: p.api_key, is_active: p.is_active,
            price_in_cached: numOrNull(p.price_in_cached),
            price_in_uncached: numOrNull(p.price_in_uncached),
            price_out: numOrNull(p.price_out) }) });
        const d = await r.json();
        if (!r.ok) { showToast(d.detail || '保存失败', true); return; }
        showToast('已保存 ✅');
      } catch (e) { showToast('保存出错：' + e.message, true); }
    }
    async function activateProvider(p) {
      try {
        const r = await fetch('/api/llm/providers/' + p.id + '/activate?token=' + encodeURIComponent(token.value), { method: 'POST' });
        const d = await r.json();
        if (!r.ok) { showToast(d.detail || '启用失败', true); return; }
        showToast('已启用该模型：后续对话将使用它 🧠');
        loadProviders();
      } catch (e) { showToast('启用出错：' + e.message, true); }
    }
    async function delProvider(p) {
      if (!confirm('删除该模型配置？')) return;
      try {
        const r = await fetch('/api/llm/providers/' + p.id + '?token=' + encodeURIComponent(token.value), { method: 'DELETE' });
        if (!r.ok) { showToast('删除失败', true); return; }
        showToast('已删除');
        loadProviders();
      } catch (e) { showToast('删除出错：' + e.message, true); }
    }

    // ---------- 定制助手：人格 SOUL.md（多人格集） ----------
    async function loadSouls() {
      try {
        const r = await fetch('/api/soul/list?token=' + encodeURIComponent(token.value));
        const d = await r.json();
        soulList.value = d.souls || [];
        const active = d.active || 'SOUL.md';
        soulActive.value = active;
        await loadSoulContent(active === 'SOUL.md' ? 'default' : active.replace(/^SOUL\//, '').replace(/\.md$/, ''));
      } catch (e) {}
    }
    async function loadSoulContent(name) {
      try {
        const r = await fetch('/api/soul/named?name=' + encodeURIComponent(name) + '&token=' + encodeURIComponent(token.value));
        const d = await r.json();
        soulContent.value = d.content || '';
      } catch (e) {}
    }
    async function saveSoul() {
      const name = soulActive.value === 'SOUL.md' ? 'default' : soulActive.value.replace(/^SOUL\//, '').replace(/\.md$/, '');
      try {
        const r = await fetch('/api/soul/named?token=' + encodeURIComponent(token.value), {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ name: name, content: soulContent.value }) });
        const d = await r.json();
        if (!r.ok) { showToast(d.detail || '保存失败', true); return; }
        soulSaved.value = true; setTimeout(() => soulSaved.value = false, 2000);
        showToast('人格已保存，下次对话生效 ✅');
        loadSouls();
      } catch (e) { showToast('保存出错：' + e.message, true); }
    }
    async function switchSoul() {
      const file = soulActive.value;
      try {
        const r = await fetch('/api/soul/active?token=' + encodeURIComponent(token.value), {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ file: file }) });
        const d = await r.json();
        if (!r.ok) { showToast(d.detail || '切换失败', true); loadSouls(); return; }
        showToast('已切换到该人格 ✅');
        await loadSouls();
      } catch (e) { showToast('切换出错：' + e.message, true); }
    }
    async function createSoul() {
      const name = newSoulName.value.trim();
      if (!name) { showToast('请输入新人格名', true); return; }
      if (soulList.value.some(s => s.name === name)) { showToast('该人格已存在', true); return; }
      try {
        const r = await fetch('/api/soul/create?token=' + encodeURIComponent(token.value), {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ name: name, content: '' }) });
        const d = await r.json();
        if (!r.ok) { showToast(d.detail || '新建失败', true); return; }
        newSoulName.value = '';
        // 自动激活新人格
        await fetch('/api/soul/active?token=' + encodeURIComponent(token.value), {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ file: d.file }) });
        showToast('人格已创建并切换 ✅');
        await loadSouls();
      } catch (e) { showToast('新建出错：' + e.message, true); }
    }
    async function delSoul() {
      const name = soulActive.value.replace(/^SOUL\//, '').replace(/\.md$/, '');
      if (!confirm('删除人格「' + name + '」？将自动切回默认人格。')) return;
      try {
        const r = await fetch('/api/soul/delete?token=' + encodeURIComponent(token.value), {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ name: name }) });
        const d = await r.json();
        if (!r.ok) { showToast(d.detail || '删除失败', true); return; }
        showToast('已删除，切回默认人格');
        await loadSouls();
      } catch (e) { showToast('删除出错：' + e.message, true); }
    }

    // ---------- 定制助手：记忆画像 MEMORY.md ----------
    async function loadMemory() {
      try {
        const r = await fetch('/api/memory?token=' + encodeURIComponent(token.value));
        const d = await r.json();
        memoryContent.value = d.content || '';
        memoryInitialized.value = !!d.initialized;
        loadSnapshots();      // ★ 顺手把历史版本数拉出来（否则按钮一直显示 0，用户实测反馈误导）
      } catch (e) {}
    }
    async function saveMemory() {
      try {
        const r = await fetch('/api/memory?token=' + encodeURIComponent(token.value), {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ content: memoryContent.value }) });
        const d = await r.json();
        if (!r.ok) { showToast(d.detail || '保存失败', true); return; }
        memorySaved.value = true; setTimeout(() => memorySaved.value = false, 2000);
        memoryInitialized.value = !!d.initialized;
        showToast('画像已保存，下次对话生效 ✅');
      } catch (e) { showToast('保存出错：' + e.message, true); }
    }
    async function initMemory() {
      if (!confirm('用 AI 根据系统中已有信息生成初始画像？每个账号仅可初始化一次。')) return;
      memoryGenning.value = true;
      try {
        const r = await fetch('/api/memory/init?token=' + encodeURIComponent(token.value), {
          method: 'POST' });
        const d = await r.json();
        if (!r.ok) { showToast(d.detail || '初始化失败', true); return; }
        memoryContent.value = d.content || '';
        memoryInitialized.value = true;
        showToast('初始画像已生成 ✅');
      } catch (e) { showToast('初始化出错：' + e.message, true); }
      finally { memoryGenning.value = false; }
    }

    // ---------- M3-7：AI 建议 / 应用 / 一键导入 / 历史还原（M3）----------
    async function suggestMemory() {
      memBusy.value = true; memSkipped.value = ''; memEvidence.value = '';
      try {
        const r = await fetch('/api/memory/suggest?token=' + encodeURIComponent(token.value), {
          method: 'POST' });
        const d = await r.json();
        if (!r.ok) { showToast(d.detail || '建议生成失败', true); return; }
        memSuggestions.value = decorateSuggestions(d.suggestions);
        const ev = d.evidence || {};
        memEvidence.value = d.note || ('依据：系统资料 ' + (ev.rows || 0) + ' 条 · 周报 ' + (ev.reports || 0) + ' 篇 · 对话 ' + (ev.sessions || 0) + ' 条');
        if (!memSuggestions.value.length) showToast('没有需要更新的地方（画像已是现状）');
      } catch (e) { showToast('建议出错：' + e.message, true); }
      finally { memBusy.value = false; }
    }
    // ★ 默认勾选口径（D8/D6）：人工行不勾（保护用户手写），白名单外的新维度不勾（要人确认）
    function decorateSuggestions(list) {
      return (list || []).map(function (s) {
        return Object.assign({}, s, { checked: !s.manual && !s.flagged });
      });
    }
    function clearSuggestions() { memSuggestions.value = []; memSkipped.value = ''; memEvidence.value = ''; }
    function _applyResult(d) {
      memoryContent.value = d.content || memoryContent.value;
      memChanged.value = !!d.wrote;
      const ap = (d.applied || []).length;
      if (d.wrote) showToast('已更新 ' + ap + ' 个维度（画像里同一维度只有一行）✅');
      else showToast('没有可应用的改动');
      const sk = (d.skipped || []).map(function (s) { return s.field + '：' + s.why; });
      memSkipped.value = sk.length ? ('已跳过 ' + sk.length + ' 条 —— ' + sk.join('；')) : '';
      if (d.wrote) memSuggestions.value = [];
    }
    async function applyMemory(all) {
      const picked = memSuggestions.value.filter(function (s) { return all || s.checked; });
      if (!picked.length) { showToast('先勾选要应用的建议', true); return; }
      if (all) {
        const man = picked.filter(function (s) { return s.manual; }).length;
        if (!confirm('一键导入会整份应用这 ' + picked.length + ' 条建议' +
            (man ? '（其中 ' + man + ' 条是人工行' + (memProtectManual.value ? '，将被保护不覆盖' : '，且你已关闭保护 → 会被覆盖') + '）' : '') +
            '。确定？')) return;
      }
      memBusy.value = true;
      try {
        const url = all ? '/api/memory/import' : '/api/memory/apply';
        const body = { items: picked, protect_manual: memProtectManual.value };
        if (all) body.confirm = true;      // ★ 后端要求显式确认（整份覆盖破坏面最大）
        const r = await fetch(url + '?token=' + encodeURIComponent(token.value), {
          method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
        const d = await r.json();
        if (!r.ok) { showToast(d.detail || '应用失败', true); return; }
        _applyResult(d);
      } catch (e) { showToast('应用出错：' + e.message, true); }
      finally { memBusy.value = false; }
    }
    const SNAP_REASON = { init: '初始化', suggest_import: 'AI 建议导入', manual_edit: '人工编辑',
                          agent_update: '助手更新', restore: '还原' };
    async function loadSnapshots() {
      try {
        const r = await fetch('/api/memory/snapshots?token=' + encodeURIComponent(token.value));
        const d = await r.json();
        if (!r.ok) { showToast(d.detail || '读历史失败', true); return; }
        memSnapshots.value = (d.snapshots || d.items || []).map(function (s) {
          return Object.assign({}, s, { when: s.created_at_text || s.created_at || '',
                                        why: SNAP_REASON[s.reason] || s.reason || '',
                                        open: false });
        });
        if (!memSnapshots.value.length) showToast('还没有历史版本（每次修改前会自动留一份）');
      } catch (e) { showToast('读历史出错：' + e.message, true); }
    }
    async function restoreSnapshot(s) {
      if (!confirm('还原到「' + s.when + ' ' + s.why + '」这一版？当前版本会被先存一份，可来回切换。')) return;
      memBusy.value = true;
      try {
        const r = await fetch('/api/memory/restore?token=' + encodeURIComponent(token.value), {
          method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ snapshot_id: s.id }) });   // ★ 后端字段名是 snapshot_id（发 {id} 会 422）
        const d = await r.json();
        if (!r.ok) { showToast(d.detail || '还原失败', true); return; }
        memoryContent.value = d.content || memoryContent.value;
        memChanged.value = true;
        clearSuggestions();
        showToast('已还原 ✅');
        loadSnapshots();
      } catch (e) { showToast('还原出错：' + e.message, true); }
      finally { memBusy.value = false; }
    }

    // ---------- M4-5：用量（本会话外部检索次数）----------
    // 数据源 = M4-4 的 GET /api/usage（按 thread_id 聚合 usage_events，本轮靠 turn_index）。
    // D1：**只提示不阻断** —— 超阈值只多一行琥珀色提醒，不拦任何操作。
    const usageInfo = ref(null);
    // ---------- M6c-7：成本（token 一定数得准；金额只有你填了单价才折算，且一律标注为估算值）----------
    const COST_NOTE = '金额为估算值（你填的单价 × 数到的 token），不含工具与检索成本，不是账单';
    const costInfo = ref(null);
    function numOrNull(v) {
      if (v === '' || v === null || v === undefined) return null;   // ★ 留空 ≠ 0（0 = 明确免费）
      const n = Number(v);
      return isFinite(n) ? n : null;
    }
    function costTokens(total) {
      if (!total || !total.tokens) return '—';
      return (total.tokens.input + total.tokens.output).toLocaleString();
    }
    function costAmountText(info) {
      const t = info ? (info.total || info) : null;
      if (!t) return '—';
      if (t.cost === null || t.cost === undefined) return '未配置单价';
      return '¥' + Number(t.cost).toFixed(4);
    }
    async function loadCost() {
      try {
        const r = await fetch('/api/cost/summary?days=30&token=' + encodeURIComponent(token.value));
        costInfo.value = r.ok ? await r.json() : null;
      } catch (e) { costInfo.value = null; }
    }
    async function recalcCost() {
      try {
        const r = await fetch('/api/cost/recalc?token=' + encodeURIComponent(token.value), {
          method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ days: 30 }) });
        const d = await r.json();
        showToast(r.ok ? ('已按当前单价重算 ' + d.updated + ' 条（扫描 ' + d.scanned + ' 条）✅')
                       : (d.detail || '重算失败'), !r.ok);
        await loadCost();
      } catch (e) { showToast('重算出错：' + e.message, true); }
    }
    let _usageTimer = null;
    async function loadUsage() {
      try {
        const tid = (typeof activeThread !== 'undefined' && activeThread && activeThread.value) || '';
        const r = await fetch('/api/usage?token=' + encodeURIComponent(token.value) +
                              '&thread_id=' + encodeURIComponent(tid));
        const d = await r.json();
        if (!r.ok) { loadCost();
      usageInfo.value = null; return; }
        loadPriceAlerts();                     // M5-4：顺手看一眼有没有待提示的价格提醒
        loadPriceSummary();                    // M5-6：价格台账也一起刷（同一处节奏，不新增定时器）
        usageInfo.value = {
          cost: (d.this_session && d.this_session.cost_calls) || 0,
          total: (d.this_session && d.this_session.total) || 0,
          turn: (d.this_turn && d.this_turn.cost_calls) || 0,
          threshold: d.threshold || 3,
        };
      } catch (e) { usageInfo.value = null; }
    }
    // 复用 WS 的 tool_start 做"近实时"：去抖 1.2s，避免连发工具时把接口打爆
    function scheduleUsageRefresh() {
      clearTimeout(_usageTimer);
      _usageTimer = setTimeout(loadUsage, 1200);
    }

    // ---------- M5-4：价格提醒（取走 → 提示 → 回执，避免重复弹）----------
    const priceAlerts = ref([]);
    const PRICE_RULE_TEXT = { new_low: '创历史新低', below_target: '首次跌破目标价',
                              drop_pct: '跌幅提醒', competitor_gap: '竞品价差超阈值',
                              check_error: '价格源检查失败' };
    function priceAlertText(a) {
      return (PRICE_RULE_TEXT[a.rule] || a.rule || '价格提醒') + '：' + (a.detail || '') +
             (a.price != null ? '（' + a.price + ' 元）' : '');
    }
    async function loadPriceAlerts() {
      try {
        const r = await fetch('/api/price/alerts?token=' + encodeURIComponent(token.value));
        const d = await r.json();
        priceAlerts.value = r.ok ? (d.items || []) : [];
        if (priceAlerts.value.length) notifyPriceAlerts();
      } catch (e) { priceAlerts.value = []; }
    }
    // 桌面壳在就跑系统通知（外壳只暴露 notify，不自己发 HTTP）；不在就应用内提示
    function notifyPriceAlerts() {
      const a = priceAlerts.value[0];
      if (!a) return;
      const msg = priceAlertText(a);
      try {
        const _sysNotify = (window.__zxDesktop && typeof window.__zxDesktop.notify === 'function')
          ? (t, m) => window.__zxDesktop.notify(t, m)
          : ((window.pywebview && window.pywebview.api && typeof window.pywebview.api.notify === 'function')
             ? (t, m) => window.pywebview.api.notify(t, m) : null);
        if (_sysNotify) {
          _sysNotify('价格提醒', msg);   // 桌面壳 js_api 或页面 hook，都没有则回退 toast
        } else { showToast(msg); }
      } catch (e) { showToast(msg); }
    }
    async function ackPriceAlert(a) {
      try {
        const r = await fetch('/api/price/alerts/ack?token=' + encodeURIComponent(token.value), {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ alert_id: a.id }) });
        if (r.ok) { priceAlerts.value = priceAlerts.value.filter(function (x) { return x.id !== a.id; }); }
        else { const d = await r.json(); showToast(d.detail || '回执失败', true); }
      } catch (e) { showToast('回执出错：' + e.message, true); }
    }

    // ---------- M5-6：价格台账（跌幅 / 目标价 / 更新价格 / 资讯价折叠）----------
    const priceSummary = ref(null);
    const priceEdit = ref({});
    const priceSaving = ref("");
    function itemKey(it) { return it.item_type + ':' + it.item_id; }
    async function loadPriceSummary() {
      try {
        const r = await fetch('/api/price/summary?token=' + encodeURIComponent(token.value));
        const d = await r.json();
        // ★ 账号戳记：价格数据只属于加载它的那个账号；切账号/未选会话时宁可显示空，
        //   也不能把上一个账号的商品显示给当前账号（实测报过越界：公司账号的商品出现在个人账号上）
        if (r.ok && d) { d._account = username.value || ''; }
        priceSummary.value = r.ok ? d : null;
      } catch (e) { priceSummary.value = null; }
    }
    async function savePrice(it) {
      const k = itemKey(it);
      const raw = String(priceEdit.value[k] == null ? '' : priceEdit.value[k]).trim();
      const v = Number(raw);
      if (!raw || !isFinite(v) || v <= 0) { showToast('请填一个大于 0 的价格', true); return; }
      priceSaving.value = k;
      try {
        const r = await fetch('/api/price/record?token=' + encodeURIComponent(token.value), {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ item_type: it.item_type, item_id: it.item_id, price: v }) });
        const d = await r.json();
        if (!r.ok) { showToast(d.detail || '记价失败', true); return; }
        priceEdit.value[k] = '';
        if (d.deduped) {
          // ★ 后端"同一天同一价格不重复记账"命中时，台账确实不会变 —— 必须说清楚，
          //   不能报"已记价"（用户实测：填 7 提示已记价但台账没动，因为今天已经记过 7）
          showToast('⚠️ ' + (d.reason || '同一天同一价格不重复记账') + '：台账没变（换一个价格，或明天再记）', true);
        } else {
          showToast(d.alert ? '已记价 ✅ 并生成了一条提醒' : '已记价 ✅');
        }
        await loadPriceSummary(); await loadPriceAlerts(); await loadMyData();
        if (priceHist.value[k] && priceHist.value[k].open) {      // 展开中的台账列表也要重取
          priceHist.value[k].open = false; await toggleHistory(it);
        }
      } catch (e) { showToast('记价出错：' + e.message, true); }
      finally { priceSaving.value = ''; }
    }
    function deltaText(d) { return (d > 0 ? '+' : '') + Number(d).toFixed(1) + '%'; }

    // ---------- Q6④：登记价 ⇄ 台账价（复杂价格规则 + 可展开台账历史 + 设为登记价）----------
    // 状态**只从 /api/price/summary 读**（它已返回 price_kind / price_show）→ 不依赖 /api/me 的字段，
    // 从而避免"同一条数据两套口径"（本会话已经吃过一次亏：登记价 vs 台账价）。
    const priceHist = ref({});
    // ★ 删除一条台账：二次确认 + 删完刷新（最近价会被后端重算）
    async function delHistory(it, h) {
      const when = String(h.observed_at || '').slice(0, 16);
      if (!window.confirm('删除这条台账记录？\n\n' + when + ' · ' + h.price + ' ' + (h.currency || '') +
          '\n\n删除后该商品的最近价会回落到上一条，且不可撤销。')) return;
      const k = itemKey(it);
      try {
        const r = await fetch('/api/price/history/delete?token=' + encodeURIComponent(token.value), {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ item_type: it.item_type, item_id: it.item_id, history_id: h.id }) });
        const d = await r.json();
        if (!r.ok) { showToast(d.detail || '删除失败', true); return; }
        showToast('已删除这条台账（该商品还剩 ' + d.remaining + ' 条）✅');
        await loadPriceSummary(); await loadMyData();   // 最近价/个人信息页同步
        if (priceHist.value[k]) priceHist.value[k].open = false;
        await toggleHistory(it);                        // 重新展开 → 取最新列表
      } catch (e) { showToast('删除出错：' + e.message, true); }
    }
    const targetEdit = ref({});
    // ★ 商品切换（010 sliding-tab 风格）：一个账号常有多个收藏/公司产品
    const priceSelectedKey = ref('');
    const pricePickerOpen = ref(false);
    const priceItems = computed(() => (priceSummary.value && priceSummary.value.items) || []);
    const priceCurrent = computed(() => priceItems.value.find(x => itemKey(x) === priceSelectedKey.value) || priceItems.value[0] || null);
    const priceIdx = computed(() => Math.max(0, priceItems.value.findIndex(x => priceCurrent.value && itemKey(x) === itemKey(priceCurrent.value))));
    function pickPriceItem(x) { priceSelectedKey.value = itemKey(x); pricePickerOpen.value = false; loadPriceAlerts(); }        // 目标价输入框（与"新价"分开，避免误操作）
    async function saveTarget(it) {
      const k = itemKey(it);
      const raw = String(targetEdit.value[k] == null ? '' : targetEdit.value[k]).trim();
      if (raw === '') { showToast('请填目标价（填 0 表示清除目标价）', true); return; }
      const v = Number(raw);
      if (!isFinite(v)) { showToast('目标价要填数字', true); return; }
      try {
        const r = await fetch('/api/price/register?token=' + encodeURIComponent(token.value), {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ item_type: it.item_type, item_id: it.item_id, target_price: v }) });
        const d = await r.json();
        if (!r.ok) { showToast(d.detail || '保存失败', true); return; }
        targetEdit.value[k] = '';
        showToast(v > 0 ? ('目标价已设为目标 ' + v + ' 元 ✅') : '目标价已清除 ✅');
        await loadPriceSummary(); await loadMyData();   // ★ 个人信息/公司信息页也跟着变
      } catch (e) { showToast('保存出错：' + e.message, true); }
    }
    // ★ 换账号（登录/注销/校验会话）时必须清空——否则会把上个账号的价格数据留在界面上
    function resetPriceState() {
      priceSummary.value = null; priceAlerts.value = []; priceHist.value = {};
      complexDraft.value = {}; priceEdit.value = {}; priceSaving.value = '';
    }
    const complexDraft = ref({});
    function priceInfoOf(it) {
      const arr = (priceSummary.value && priceSummary.value.items) || [];
      return arr.find(x => x.item_type === it.item_type && x.item_id === it.item_id) || null;
    }
    function isComplex(it) { const i = priceInfoOf(it); return !!i && i.price_kind === 'rule'; }
    async function toggleHistory(it) {
      const k = itemKey(it);
      const cur = priceHist.value[k] || {};
      if (cur.open) {
        priceHist.value = Object.assign({}, priceHist.value, { [k]: Object.assign({}, cur, { open: false }) });
        return;
      }
      try {
        const r = await fetch('/api/price/history?item_type=' + it.item_type + '&item_id=' + it.item_id +
                              '&token=' + encodeURIComponent(token.value));
        const d = await r.json();
        priceHist.value = Object.assign({}, priceHist.value,
          { [k]: { open: true, items: d.items || [], total: d.total || 0, registered: d.registered || {} } });
      } catch (e) { showToast('台账读取失败：' + e.message, true); }
    }
    async function setRegistered(it, h) {
      if (!h) return;
      try {
        const r = await fetch('/api/price/register?token=' + encodeURIComponent(token.value), {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ item_type: it.item_type, item_id: it.item_id,
                                 price: h.price, price_kind: 'simple' }) });
        const d = await r.json();
        if (!r.ok) { showToast(d.detail || '设置失败', true); return; }
        showToast('已把 ' + h.price + ' 元设为登记价 ✅（不写台账、不触发提醒）');
        await loadPriceSummary(); await loadMyData();   // ★ 个人信息/公司信息页同步
        await toggleHistory(it);
      } catch (e) { showToast('设置出错：' + e.message, true); }
    }
    async function saveComplex(it, text) {
      const v = String(text || '').trim();
      if (!v) { showToast('请填写复杂价格规则', true); return; }
      try {
        const r = await fetch('/api/price/register?token=' + encodeURIComponent(token.value), {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ item_type: it.item_type, item_id: it.item_id,
                                 price_kind: 'rule', price_text: v }) });
        const d = await r.json();
        if (!r.ok) { showToast(d.detail || '保存失败', true); return; }
        showToast('已保存复杂价格规则 ✅（该商品不参与涨跌/目标价/提醒）');
        await loadPriceSummary(); await loadMyData();
      } catch (e) { showToast('保存出错：' + e.message, true); }
    }
    async function clearComplex(it, numericPrice) {
      if (numericPrice === null || numericPrice === undefined || String(numericPrice).trim() === '') {
        showToast('切回数值价要先填一个价格', true); return;
      }
      try {
        const r = await fetch('/api/price/register?token=' + encodeURIComponent(token.value), {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ item_type: it.item_type, item_id: it.item_id,
                                 price_kind: 'simple', price: Number(numericPrice) }) });
        const d = await r.json();
        if (!r.ok) { showToast(d.detail || '切换失败', true); return; }
        showToast('已切回数值价 ✅');
        await loadPriceSummary(); await loadMyData();
      } catch (e) { showToast('切换出错：' + e.message, true); }
    }


    // ---------- 定制助手：语音包（音色克隆，联动 voice_test） ----------
    async function loadVoicePacks() {
      try {
        const r = await fetch('/api/voice/packs?token=' + encodeURIComponent(token.value));
        const d = await r.json();
        voicePacks.value = d.packs || [];
        voiceAvailable.value = d.available !== false;
      } catch (e) { voiceAvailable.value = false; }
    }
    function onVoiceFile(e) {
      voiceFile.value = e.target.files[0] || null;
    }
    async function buildVoicePack() {
      const name = newVoicePack.value.name.trim();
      const refText = newVoicePack.value.refText.trim();
      if (!name) { showToast('请填写语音包名', true); return; }
      if (!refText) { showToast('请填写参考音频对应的台词文本', true); return; }
      if (!voiceFile.value) { showToast('请选择参考音频文件（3-8秒人声 wav 最佳）', true); return; }
      voiceBusy.value = true;
      try {
        const fd = new FormData();
        fd.append('file', voiceFile.value);
        fd.append('ref_text', refText);
        fd.append('pack_name', name);
        const r = await fetch('/api/voice/packs?token=' + encodeURIComponent(token.value), {
          method: 'POST', body: fd });
        const d = await r.json();
        if (!r.ok) { showToast(d.detail || '克隆失败', true); return; }
        showToast('音色克隆成功 ✅');
        newVoicePack.value = { name: '', refText: '' };
        voiceFile.value = null;
        if (voiceFileInput.value) voiceFileInput.value.value = '';
        await loadVoicePacks();
      } catch (e) { showToast('克隆出错：' + e.message, true); }
      finally { voiceBusy.value = false; }
    }
    async function selectVoicePack(p) {
      voiceBusy.value = true;
      try {
        const r = await fetch('/api/voice/packs/select?token=' + encodeURIComponent(token.value), {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ text: '', pack_name: p.name }) });
        const d = await r.json();
        if (!r.ok) { showToast(d.detail || '选用失败', true); return; }
        showToast('已选用音色「' + p.name + '」✅');
        await loadVoicePacks();
      } catch (e) { showToast('选用出错：' + e.message, true); }
      finally { voiceBusy.value = false; }
    }
    async function delVoicePack(p) {
      if (!confirm('删除语音包「' + p.name + '」？')) return;
      try {
        const r = await fetch('/api/voice/packs/' + encodeURIComponent(p.name) + '?token=' + encodeURIComponent(token.value), { method: 'DELETE' });
        if (!r.ok) { showToast('删除失败', true); return; }
        showToast('已删除');
        await loadVoicePacks();
      } catch (e) { showToast('删除出错：' + e.message, true); }
    }
    async function speakMsg(m) {
      if (!m.text) { showToast('没有可朗读的文本', true); return; }
      if (m.speaking) return;
      m.speaking = true;
      showToast('🎙️ 合成任务已提交（首次需加载模型，长文本约数分钟），完成后自动出现播放条…');
      try {
        // 异步提交：立即返回 task_id，后台线程合成，前端轮询状态
        const r = await fetch('/api/tts/speak?token=' + encodeURIComponent(token.value), {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ text: m.text }) });
        const d = await r.json();
        if (!r.ok) { showToast(d.detail || '提交失败', true); m.speaking = false; return; }
        const taskId = d.task_id;
        // 轮询：每 8 秒查一次（合成 1 段约 1-2 分钟）
        const poll = setInterval(async () => {
          try {
            const s = await fetch('/api/tts/status/' + taskId + '?token=' + encodeURIComponent(token.value));
            const st = await s.json();
            if (st.status === 'done') {
              clearInterval(poll);
              m._audio = st.url;
              m.speaking = false;
              showToast('语音已生成 🔊');
            } else if (st.status === 'error') {
              clearInterval(poll);
              m.speaking = false;
              showToast('合成失败：' + (st.error || '未知错误'), true);
            }
            // pending/running：继续等待
          } catch (e) {
            clearInterval(poll);
            m.speaking = false;
            showToast('查询失败：' + e.message, true);
          }
        }, 8000);
      } catch (e) { showToast('朗读出错：' + e.message, true); m.speaking = false; }
    }
    async function genSoul() {
      if (!soulReq.value.trim()) { showToast('先描述你想要的风格，如「活泼女仆」', true); return; }
      soulGenning.value = true; soulGenOut.value = '';
      try {
        const r = await fetch('/api/soul/generate?token=' + encodeURIComponent(token.value), {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ content: soulReq.value }) });
        const d = await r.json();
        if (!r.ok) { showToast(d.detail || '生成失败', true); return; }
        soulGenOut.value = d.content;
      } catch (e) { showToast('生成出错：' + e.message, true); }
      finally { soulGenning.value = false; }
    }
    function soulImport() {
      if (!soulGenOut.value) return;
      soulContent.value = soulGenOut.value;
      soulGenOut.value = '';
      showToast('已导入编辑框，点「保存人格」生效 📥');
    }

    // ---------- 对话 ----------
    // ================= M1 会话管理 =================
    async function loadSessions() {
      if (!token.value) return;
      try {
        const r = await fetch('/api/chat/sessions?token=' + encodeURIComponent(token.value));
        if (r.ok) sessions.value = (await r.json()).items || [];
      } catch (e) { /* 静默 */ }
    }
    async function newSession() {
      try {
        const r = await fetch('/api/chat/sessions?token=' + encodeURIComponent(token.value), { method: 'POST' });
        const d = await r.json();
        if (!r.ok) { showToast(d.detail || '新建会话失败', true); return; }
        activeThread.value = d.thread_id;
        messages.value = [];
        thinkingText.value = '';
        connectWS(d.thread_id);    // 新会话 → WS 对准新 UUID
        await loadSessions();
        await nextTick(); scrollToBottom();
      } catch (e) { showToast('新建会话出错：' + e.message, true); }
    }
    async function switchSession(tid) {
      loadUsage();            // M4-5：换会话就刷新本会话用量
      if (loading.value) return;
      activeThread.value = tid;
      connectWS(tid);              // 切换会话 → WS 重连到该会话 UUID
      monitor.value = []; thinkingText.value = '';  // 右栏重置为当前会话进度
      try {
        const r = await fetch('/api/chat/sessions/' + encodeURIComponent(tid) + '?token=' + encodeURIComponent(token.value));
        if (!r.ok) { showToast('加载会话失败', true); return; }
        const items = (await r.json()).messages || [];
        const msgs = [];
        for (const m of items) {
          if (m.role === 'user') {
            msgs.push({ role: 'user', text: typeof m.content === 'string' ? m.content : JSON.stringify(m.content), msg_id: m.id });
          } else if (m.role === 'assistant') {
            let c = m.content;
            if (Array.isArray(c)) c = c.map(b => b.text || '').join('');
            const _txt = String(c || '');
            // ★ 2026-09-24 报障修复（前端侧）：内容为空的 assistant 消息**不渲染**。
            //   服务端已不再落库这类消息，但历史里仍有旧数据；且工具轮本身也会产生
            //   一批 content="\n" 的 assistant —— 原来每条都 push 一张报告卡，
            //   于是刷新后出现一屏「只有标题、内容为空」的卡片。
            if (!_txt.trim()) continue;
            // msg_id 必须带上：AI 气泡右上角「删除」按它定位这一轮问答
            msgs.push({ role: 'agent', text: _txt, title: '', msg_id: m.id });
          }
          // tool / system 消息跳过（过程细节在右栏监控）
        }
        messages.value = msgs;
        await nextTick(); scrollToBottom();
      } catch (e) { showToast('加载会话出错：' + e.message, true); }
    }
    async function delSession(s) {
      if (!confirm('删除会话「' + (s.title || '新对话') + '」？此操作不可恢复。')) return;
      try {
        const r = await fetch('/api/chat/sessions/' + encodeURIComponent(s.thread_id) + '?token=' + encodeURIComponent(token.value), { method: 'DELETE' });
        if (r.ok) {
          if (activeThread.value === s.thread_id) {
            activeThread.value = ''; messages.value = [];
            connectWS('');  // 回退 username 通道
          }
          await loadSessions();
        } else showToast('删除失败', true);
      } catch (e) { showToast('删除出错：' + e.message, true); }
    }
    async function renameSession(s) {
      const t = prompt('重命名会话', s.title || '');
      if (!t || !t.trim()) return;
      try {
        const r = await fetch('/api/chat/sessions/' + encodeURIComponent(s.thread_id) + '?token=' + encodeURIComponent(token.value) + '&title=' + encodeURIComponent(t.trim()), { method: 'PATCH' });
        if (r.ok) await loadSessions(); else showToast('重命名失败', true);
      } catch (e) { showToast('重命名出错：' + e.message, true); }
    }
    function fmtTime(ts) {
      if (!ts) return '';
      const d = new Date(ts.replace(' ', 'T'));
      if (isNaN(d)) return ts;
      const diff = (Date.now() - d.getTime()) / 1000;
      if (diff < 60) return '刚刚';
      if (diff < 3600) return Math.floor(diff / 60) + ' 分钟前';
      if (diff < 86400) return Math.floor(diff / 3600) + ' 小时前';
      if (diff < 86400 * 7) return Math.floor(diff / 86400) + ' 天前';
      return d.toLocaleDateString();
    }

    // ================= M3 知识库 =================
    async function loadKbStatus() {
      if (!token.value) return;
      try {
        const r = await fetch('/api/kb/status?token=' + encodeURIComponent(token.value));
        if (r.ok) kbStatus.value = await r.json();
      } catch (e) { /* 静默 */ }
    }
    // 三条入库路径统一入口：把候选内容填到弹窗上下文，让用户走完整评估+清洗预览+确认
    function kbIngestOpenWith(ctx) {
      kbIngestCtx.value = {
        title: ctx.title,                // 弹窗顶部标题（"粘贴文本入库" / "上传 .md 入库" / "导出报告入库"）
        source_kind: ctx.source_kind,    // upload / paste / export
        source_kind_label: ctx.source_kind_label,
        content: ctx.content,            // 原文（粘贴/读文件/导出报告的 text）
        title_input: ctx.title_input || '',
        title_editable: !!ctx.title_editable,  // 上传/导出可改标题；粘贴必填标题
        apply_clean_default: ctx.apply_clean_default !== false,
        message_id: ctx.message_id || null,    // M3：消息级重复检测（仅 export 路径）
        already_ingested: !!ctx.already_ingested,
      };
      kbIngestRaw.value = ctx.content || '';
      kbIngestTitle.value = ctx.title_input || '';
      kbUseClean.value = ctx.apply_clean_default !== false;
      kbEval.value = {};
      kbEvalDone.value = false;
      kbIngestShow.value = true;
    }
    function kbUploadClick() { if (kbFileInput.value) kbFileInput.value.click(); }
    async function kbUploadFile(ev) {
      const f = ev.target.files && ev.target.files[0];
      ev.target.value = '';
      if (!f) return;
      if (!f.name.toLowerCase().endsWith('.md')) { showToast('仅支持 .md 文件（文档同一性）', true); return; }
      const title = f.name.replace(/\.md$/i, '');
      const content = await f.text();
      kbIngestOpenWith({
        title: '⬆️ 上传 .md 入库',
        source_kind: 'upload',
        source_kind_label: '上传文件',
        content,
        title_input: title,
        title_editable: true,
      });
    }
    function kbIngestOpen() {
      kbIngestOpenWith({
        title: '✍️ 粘贴文本入库',
        source_kind: 'paste',
        source_kind_label: '粘贴文本',
        content: '',
        title_input: '',
        title_editable: true,
      });
    }
    async function kbIngestEvalNow() {
      const text = (kbIngestRaw.value || '').trim();
      if (!text) { showToast('内容为空', true); return; }
      const title = (kbIngestTitle.value || '').trim() || '未命名';
      kbBusy.value = true;
      try {
        const r = await fetch('/api/kb/evaluate?token=' + encodeURIComponent(token.value), {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ title, content: text }) });
        if (!r.ok) { showToast('评估失败', true); return; }
        kbEval.value = await r.json();
        kbEvalDone.value = true;
        // 评估结果可能含"建议清洗预览"（kbEval.cleaned_preview），让用户勾选 useClean
        if (kbEval.value.cleaned_preview && kbEval.value.needs_clean) {
          kbUseClean.value = kbIngestCtx.value.apply_clean_default;
        }
      } catch (e) { showToast('评估出错：' + e.message, true); }
      finally { kbBusy.value = false; }
    }
    function kbIngestRetry() { kbEvalDone.value = false; kbEval.value = {}; }
    async function kbIngestConfirm() {
      const text = (kbIngestRaw.value || '').trim();
      if (!text) { showToast('内容为空', true); return; }
      let title = (kbIngestTitle.value || '').trim();
      if (!title) title = '未命名文档';
      kbBusy.value = true;
      try {
        const r = await fetch('/api/kb/ingest?token=' + encodeURIComponent(token.value), {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ title, content: text, source_kind: kbIngestCtx.value.source_kind,
                                 apply_clean: kbUseClean.value,
                                 message_id: kbIngestCtx.value.message_id,
                                 force: !!kbIngestCtx.value.already_ingested }) });
        const d = await r.json();
        if (!r.ok) {
          // 后端拦截（重复入库 / 评估拒绝）→ 弹窗内显示，不关闭
          if (d.detail) {
            showToast(d.detail, true);
            if (d.detail.includes('已入过知识库')) {
              // 给用户多一个"强制再入"选项
              kbIngestCtx.value.already_ingested = true;
            }
          } else {
            showToast('入库失败', true);
          }
          return;
        }
        // 入库成功 → 标记该消息 kb_ingested
        if (kbIngestCtx.value.message_id) {
          const m = messages.value.find(x => x.msg_id === kbIngestCtx.value.message_id);
          if (m) m.kb_ingested = true;
        }
        const c = d.applied_clean ? '（已清洗）' : '';
        showToast(d.dup ? 'ℹ️ 该内容已在知识库中（未重复添加）' : `✅ 已入库《${d.title}》${c}（${d.chunks} 片段）`);
        kbIngestShow.value = false;
        await loadKbStatus();
      } catch (e) { showToast('入库出错：' + e.message, true); }
      finally { kbBusy.value = false; }
    }
    async function kbDeleteDoc(d) {
      if (!confirm('删除《' + d.title + '》？不可恢复。')) return;
      try {
        const r = await fetch('/api/kb/docs/' + encodeURIComponent(d.source_id) + '?token=' + encodeURIComponent(token.value), { method: 'DELETE' });
        if (r.ok) { showToast('已删除'); await loadKbStatus(); }
        else showToast('删除失败', true);
      } catch (e) { showToast('删除出错：' + e.message, true); }
    }
    async function kbQueryRun() {
      const q = kbQueryText.value.trim();
      if (!q) return;
      kbBusy.value = true; kbQueryHits.value = [];
      try {
        const r = await fetch('/api/kb/query?token=' + encodeURIComponent(token.value), {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ question: q, mode: 'fast' }) });
        const d = await r.json();
        kbQueryHits.value = (d.hits || []).slice(0, 3);
      } catch (e) { showToast('检索出错：' + e.message, true); }
      finally { kbBusy.value = false; }
    }
    // 导出报告 → 走统一入库预览弹窗（评估+清洗+确认，批注 4/11：用户确认才入；消息级去重）
    function askIngestExport(m) {
      const title = (m.title || '情报简报') + '_导出';
      kbIngestOpenWith({
        title: '📚 导出报告入库预览',
        source_kind: 'export',
        source_kind_label: '导出报告',
        content: m.text,
        title_input: title,
        title_editable: true,
        message_id: m.msg_id || null,         // M3：消息级重复入库检测
        already_ingested: !!m.kb_ingested,    // 已入过则顶部警告
      });
    }

    // ==================== M4a：技能与工具 ====================
    const skills = ref([]);            // 我的技能（agents_docs/{username}/skills/*.md）
    const skTab = ref(_initHash === 'shell' ? 'shell' : 'skill');   // 页面 tab：skill / shell / cli / mcp / api
    const skActive = ref('');          // 当前编辑的技能名
    const skContent = ref('');         // 当前编辑的正文
    const skSaved = ref(false);        // 保存成功提示
    const skNewName = ref('');
    const skPicked = ref([]);          // 本轮启用（多选叠加，发送后清空）
    const skMenuOpen = ref(false);     // 输入框 / 唤出技能目录
    const skMenuIdx = ref(0);
    const skFilter = ref('');

    // ---------- M4b：CLI 面板（沙箱白名单命令行） ----------
    const shAllowed = ref([]);         // 白名单命令（GET /api/shell/allowed）
    const shSandbox = ref('');         // 沙箱绝对路径（按账号隔离）
    const shNotes = ref([]);           // 后端附带的用法/限制说明
    const shLines = ref([]);           // 终端输出行 [{cls:'cmd'|'out'|'err'|'ok'|'dim', text}]
    const shInput = ref('');           // 当前输入
    const shBusy = ref(false);         // 命令执行中（此时禁输入，发送按钮变灰）
    const shElapsed = ref(0);          // 执行耗时（秒，一位小数）
    const shOut = ref(null);           // 输出区 DOM（自动滚到底）
    const shIn = ref(null);            // 输入框 DOM（点输出区即聚焦）
    const shHistList = [];             // 命令历史（↑↓ 翻）
    let shHistIdx = -1;
    let shTimer = null;

    const SH_MAX_LINES = 400;          // 输出行上限，超出丢最早的（防长跑命令撑爆 DOM）

    async function shScroll() {
      await nextTick();
      if (shOut.value) shOut.value.scrollTop = shOut.value.scrollHeight;
    }
    function shPush(cls, text) {
      shLines.value.push({ cls: cls, text: text });
      if (shLines.value.length > SH_MAX_LINES) {
        shLines.value.splice(0, shLines.value.length - SH_MAX_LINES);
      }
    }
    function shFill(cmd) { shInput.value = cmd; nextTick(() => { if (shIn.value) shIn.value.focus(); }); }
    function shFocus() { if (shIn.value) shIn.value.focus(); }
    function shClear() { shLines.value = []; }

    async function shLoadAllowed() {
      if (!token.value) return;
      try {
        const r = await fetch('/api/shell/allowed?token=' + encodeURIComponent(token.value));
        const d = await r.json();
        if (!r.ok) { showToast(d.detail || 'CLI 白名单读取失败', true); return; }
        shAllowed.value = d.commands || [];
        shSandbox.value = d.sandbox || '';
        shNotes.value = d.notes || [];
      } catch (e) { showToast('CLI 白名单读取失败：' + e.message, true); }
    }

    function shHist(dir) {
      if (!shHistList.length) return;
      if (shHistIdx === -1) shHistIdx = shHistList.length;
      shHistIdx = Math.min(Math.max(shHistIdx + dir, 0), shHistList.length);
      shInput.value = shHistIdx >= shHistList.length ? '' : shHistList[shHistIdx];
    }

    async function shExec() {
      const line = (shInput.value || '').trim();
      if (!line || shBusy.value) return;
      shPush('cmd', line);
      shInput.value = '';
      if (shHistList[shHistList.length - 1] !== line) shHistList.push(line);
      shHistIdx = -1;
      shBusy.value = true; shElapsed.value = 0;
      const t0 = Date.now();
      shTimer = setInterval(() => { shElapsed.value = ((Date.now() - t0) / 1000).toFixed(1); }, 100);
      await shScroll();
      try {
        const r = await fetch('/api/shell/exec?token=' + encodeURIComponent(token.value), {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ command: line })
        });
        const d = await r.json().catch(() => ({}));
        if (!r.ok) {
          shPush('err', '⚠️ ' + (d.detail || ('HTTP ' + r.status)));
        } else {
          if (d.sandbox) shSandbox.value = d.sandbox;
          if (d.stdout) shPush('out', d.stdout.replace(/\s+$/, ''));
          if (d.stderr) shPush('err', d.stderr.replace(/\s+$/, ''));
          if (d.error) shPush('err', d.error);
          shPush(d.ok ? 'ok' : 'err',
                 (d.ok ? '✓ 完成' : '✗ 失败') +
                 (d.elapsed != null ? ' · ' + d.elapsed + 's' : '') +
                 (d.returncode != null ? ' · exit ' + d.returncode : '') +
                 (d.truncated ? ' · 输出已截断(1MB)' : ''));
        }
      } catch (e) {
        shPush('err', '⚠️ 请求失败：' + e.message);
      } finally {
        if (shTimer) { clearInterval(shTimer); shTimer = null; }
        shBusy.value = false;
        await shScroll();
      }
    }

    // 进 CLI 面板 tab 才拉白名单（省一次请求；切回来复用缓存）
    watch(skTab, (t) => { if (t === 'shell' && !shAllowed.value.length) shLoadAllowed(); if (t === 'cli' && !cliLoaded.value) loadCliCatalog(); if ((t === 'api' || t === 'mcp') && !mcpAll.value.length) tsLoad(); });

    // 与后端 customize.py 常量对齐（前后端双硬校验）
    const SK_MAX = 4000;               // SKILL_MAX_LEN 单技能正文字数
    const SK_MAX_PICK = 5;             // SKILL_MAX_PICK 单轮最多叠加技能数
    const SK_TPL = [
      '# 技能名',
      '',
      '## 适用场景',
      '- 什么时候该用这个技能',
      '',
      '## 执行步骤',
      '1. ',
      '2. ',
      '',
      '## 输出格式',
      '- ',
      '',
      '## 注意事项',
      '- ',
      ''
    ].join('\n');
    // ---- M4c 自定义 CLI 接入配置（配置导向，服务端单一来源：tools/cli_registry.py → /api/cli/*）----
    // 系统不预设厂商：名称 / 可执行名 / 安装认证命令 / 只读命令清单都由用户填，状态落 SQLite（按账号隔离）。
    const cliItems = ref([]);
    const cliTemplates = ref([]);
    const cliStates = ref({ none: '未接入', installed: '已安装（未认证）', authed: '已接入（已授权）' });
    const cliSummary = ref({ total: 0, authed: 0, installed: 0, detected_local: 0, agent_live: 0, with_rules: 0 });
    const cliNote = ref({});
    const cliLoaded = ref(false);
    const cliEditing = ref(false);
    const cliErr = ref('');
    const cliParseNotes = ref([]);   // 保存后回显「整条命令 → 裁成的路径」
    const cliRunning = ref(null);
    const cliRunOut = ref({});
    const cliDrafting = ref(false);   // M5：能力描述草稿生成中
    const cliDraftInfo = ref('');     // 草稿证据回显（读了哪份文档、覆盖几条命令）
    const cliDraftWarns = ref([]);    // 草稿审计警告（需要 ID 却直调 / 凭空造链 / 命令不在清单里）
    const cliDoneCount = computed(() => cliItems.value.filter(c => c.state !== 'none').length);

    function emptyCliForm() {
      // helpText 是**临时字段**（只用于草稿生成，不落库）：文档没覆盖的命令，用户可以粘 --help 原文
      return { id: null, name: '', bin: '', install_cmd: '', auth_cmd: '', docs: '', readonly: '',
               state: 'none', note: '', abilities: '', helpText: '' };
    }
    const cliForm = ref(emptyCliForm());

    async function loadCliCatalog(force) {
      if (cliLoaded.value && !force) return;
      if (!token.value) return;
      try {
        const r = await fetch('/api/cli/list?token=' + encodeURIComponent(token.value));
        const d = await r.json();
        if (!r.ok) { showToast(d.detail || 'CLI 配置读取失败', true); return; }
        cliItems.value = d.items || [];
        cliTemplates.value = d.templates || [];
        cliStates.value = d.states || cliStates.value;
        cliSummary.value = d.summary || cliSummary.value;
        const notes = {};
        (d.items || []).forEach(c => { notes[c.id] = c.note || ''; });
        cliNote.value = notes;
        cliLoaded.value = true;
      } catch (e) { showToast('CLI 配置出错：' + e.message, true); }
    }
    function cliNew() {
      cliForm.value = emptyCliForm();
      cliEditing.value = true;
      cliErr.value = '';
      cliParseNotes.value = [];
    }
    function cliFill(t) {   // 预填示例：只把表单填好，不写库
      cliForm.value = Object.assign(emptyCliForm(), {
        name: t.name, bin: t.bin, install_cmd: t.install_cmd || '',
        auth_cmd: t.auth_cmd || '', docs: t.docs || '', readonly: t.readonly || '', state: 'none',
      });
      cliEditing.value = true;
      cliErr.value = '';
      cliParseNotes.value = [];
      showToast('已按示例预填，可自由修改后再保存' + (t.readonly ? '' : '（这个包没有文档，只读清单请自己补）'));
    }
    function cliEdit(c) {
      cliForm.value = Object.assign(emptyCliForm(), {
        id: c.id, name: c.name, bin: c.bin, install_cmd: c.install_cmd, auth_cmd: c.auth_cmd,
        docs: c.docs, readonly: c.readonly, state: c.state, note: c.note,
        abilities: c.abilities || '',
      });
      cliEditing.value = true;
      cliErr.value = '';
      cliParseNotes.value = [];
    }
    function cliCancel() { cliEditing.value = false; cliErr.value = ''; cliParseNotes.value = []; }
    async function cliSave() {
      if (!token.value) return;
      cliErr.value = '';
      try {
        const r = await fetch('/api/cli/save?token=' + encodeURIComponent(token.value), {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(cliForm.value),
        });
        const d = await r.json();
        if (!r.ok || !d.ok) { cliErr.value = d.detail || '保存失败'; return; }
        cliEditing.value = false;
        cliParseNotes.value = (d.item && d.item.parse_notes) || [];
        await loadCliCatalog(true);
        if (cliParseNotes.value.length) showToast('已保存：清单按子命令路径存了 ' + cliParseNotes.value.length + ' 条（整条粘贴已自动裁掉参数）');
        else showToast(cliForm.value.id ? '配置已更新' : '已新增 CLI：' + d.item.name);
      } catch (e) { cliErr.value = '保存出错：' + e.message; }
    }
    // M5：按只读清单生成「能力描述」草稿（AI 生成、用户确认后再保存；不落库）
    async function cliAbilityDraft() {
      if (!token.value || cliDrafting.value) return;
      cliErr.value = '';
      const f = cliForm.value;
      if (!(f.readonly || '').trim()) {
        cliErr.value = '请先填「只读命令清单」——能力描述里的命令必须来自它';
        return;
      }
      cliDrafting.value = true;
      cliDraftInfo.value = ''; cliDraftWarns.value = [];
      try {
        const r = await fetch('/api/cli/ability_draft?token=' + encodeURIComponent(token.value), {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          // docs：让 AI 读官方文档（语义依据）；help_text：文档没覆盖时用户粘的 --help 原文
          body: JSON.stringify({ name: f.name, bin: f.bin, readonly: f.readonly,
                                 docs: f.docs, help_text: f.helpText }),
        });
        const d = await r.json();
        if (!r.ok) { cliErr.value = d.detail || '生成失败'; return; }
        cliForm.value.abilities = d.content || '';
        // 证据与审计回显（2026-09-24）：告诉用户这份草稿**基于什么**写的、哪里可疑
        const ev = d.evidence || {}, dcs = ev.docs || {}, hp = ev.help || {};
        if (dcs.ok) {
          cliDraftInfo.value = '已按官方文档生成：' + (dcs.url || '') + '（文档覆盖 ' + dcs.covered + '/' + ev.commands
            + ' 条命令' + ((dcs.missing && dcs.missing.length) ? '；未覆盖：' + dcs.missing.join('、') : '') + '）'
            + (hp.total ? '｜本机 --help 实测 ' + hp.ok + '/' + hp.total + ' 条' : '');
        } else {
          cliDraftInfo.value = '未读到官方文档（' + (dcs.note || '未填地址') + '）'
            + (hp.total ? '；改用本机 --help 实测 ' + hp.ok + '/' + hp.total + ' 条作依据' : '；只能保守生成');
        }
        cliDraftWarns.value = d.warnings || [];
        showToast(cliDraftWarns.value.length
          ? '草稿已生成，但审计发现 ' + cliDraftWarns.value.length + ' 处可疑，请核对后再保存'
          : '已生成草稿：请核对右边命令是否都在只读清单里，再点保存');
      } catch (e) { cliErr.value = '生成出错：' + e.message; }
      finally { cliDrafting.value = false; }
    }

    async function cliSetState(c, state) {
      if (!token.value) return;
      try {
        const r = await fetch('/api/cli/status?token=' + encodeURIComponent(token.value), {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ id: c.id, state: state, note: cliNote.value[c.id] || '' }),
        });
        const d = await r.json();
        if (!r.ok || !d.ok) { showToast(d.detail || '登记失败', true); return; }
        c.state = d.item.state; c.state_label = d.item.state_label;
        c.live = d.item.live; c.updated_at = '刚刚';
        cliSummary.value = Object.assign({}, cliSummary.value, {
          authed: cliItems.value.filter(x => x.state === 'authed').length,
          installed: cliItems.value.filter(x => x.state === 'installed').length,
          agent_live: cliItems.value.filter(x => x.live).length,
        });
        showToast('已把「' + c.name + '」登记为' + d.item.state_label);
      } catch (e) { showToast('登记出错：' + e.message, true); }
    }
    async function cliSaveNote(c) {
      if (!token.value || (cliNote.value[c.id] || '') === (c.note || '')) return;
      try {
        const r = await fetch('/api/cli/status?token=' + encodeURIComponent(token.value), {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ id: c.id, state: c.state, note: cliNote.value[c.id] || '' }),
        });
        const d = await r.json();
        if (!r.ok || !d.ok) { showToast(d.detail || '备注保存失败', true); return; }
        c.note = d.item.note; c.updated_at = '刚刚';
        showToast('备注已保存');
      } catch (e) { showToast('备注保存出错：' + e.message, true); }
    }
    async function cliDelete(c) {
      if (!confirm('删除「' + c.name + '」的配置？\n\n删除后 AI 就不能再代跑它的命令了（CLI 本身不受影响）。')) return;
      try {
        const r = await fetch('/api/cli/delete?token=' + encodeURIComponent(token.value), {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ id: c.id }),
        });
        const d = await r.json();
        if (!r.ok || !d.ok) { showToast(d.detail || '删除失败', true); return; }
        cliItems.value = cliItems.value.filter(x => x.id !== c.id);
        delete cliRunOut.value[c.id];
        cliSummary.value = Object.assign({}, cliSummary.value, {
          total: cliItems.value.length,
          authed: cliItems.value.filter(x => x.state === 'authed').length,
          installed: cliItems.value.filter(x => x.state === 'installed').length,
          agent_live: cliItems.value.filter(x => x.live).length,
          with_rules: cliItems.value.filter(x => (x.rules || []).length).length,
        });
        showToast('已删除「' + c.name + '」的配置');
      } catch (e) { showToast('删除出错：' + e.message, true); }
    }
    // 逐条试跑只读命令（与 AI 走同一执行器：越权照样被拒）
    async function cliRunAll(c) {
      if (!token.value || cliRunning.value) return;
      cliRunning.value = c.id;
      cliRunOut.value[c.id] = [];
      try {
        for (const cmd of c.agent_commands) {
          let row = { cmd: cmd, ok: false, msg: '…' };
          cliRunOut.value[c.id].push(row);
          try {
            const r = await fetch('/api/shell/exec?token=' + encodeURIComponent(token.value), {
              method: 'POST', headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({ command: cmd, timeout: 20 }),
            });
            const d = await r.json();
            row.ok = !!d.ok;
            const out = (d.stdout || '').trim() || (d.error || d.stderr || '').trim() || '（无输出）';
            row.msg = out.split('\n')[0].slice(0, 120);
          } catch (e) { row.msg = '请求出错：' + e.message; }
          cliRunOut.value[c.id] = cliRunOut.value[c.id].slice();
        }
      } finally { cliRunning.value = null; }
    }

    // ---- 工作台时钟（秒级）+ 天气（Open-Meteo 免 Key，城市级；012 号 cards/weather-gradient）----
    const clockTime = ref('--:--:--');
    const clockDate = ref('');
    const wx = ref(null);
    const wxLoading = ref(false);
    const wxErr = ref('');
    const wxOpen = ref(false);
    let _wxStarted = false;

    function _pad2(n) { return String(n).padStart(2, '0'); }
    function tickClock() {
      const d = new Date();
      clockTime.value = _pad2(d.getHours()) + ':' + _pad2(d.getMinutes()) + ':' + _pad2(d.getSeconds());
      const wk = ['星期日', '星期一', '星期二', '星期三', '星期四', '星期五', '星期六'][d.getDay()];
      clockDate.value = d.getFullYear() + ' 年 ' + _pad2(d.getMonth() + 1) + ' 月 ' + _pad2(d.getDate()) + ' 日 · ' + wk;
    }
    const wxCityEdit = ref(false);
    const wxCityInput = ref('');
    // 卡片配色（默认松绿=项目主色系；色值同时喂给 CSS 变量与选择器圆点）
    const WX_SKINS = [
      { k: 'green', t: '松绿（项目主色）', c1: '#4fae6b', c2: '#1f5c37' },
      { k: 'amber', t: '暖阳', c1: '#e2ab34', c2: '#8a5a08' },
      { k: 'indigo', t: '靛蓝', c1: '#4f6bd8', c2: '#23306f' },
      { k: 'violet', t: '紫罗兰（012 原味）', c1: '#5936b4', c2: '#362a84' },
    ];
    const wxSkin = ref('green');
    function wxSavedSkin() {
      try { return localStorage.getItem('dw_wx_skin_' + (username.value || 'anon')) || 'green'; } catch (e) { return 'green'; }
    }
    function wxSetSkin(k) {
      if (!WX_SKINS.some(x => x.k === k)) return;
      wxSkin.value = k;
      try { localStorage.setItem('dw_wx_skin_' + (username.value || 'anon'), k); } catch (e) { /* 隐身模式忽略 */ }
    }
    function wxSavedCity() {
      try { return localStorage.getItem('dw_wx_city_' + (username.value || 'anon')) || ''; } catch (e) { return ''; }
    }
    async function loadWeather(force) {
      if (!token.value) return;
      wxLoading.value = true; wxErr.value = '';
      try {
        const q = '/api/weather?token=' + encodeURIComponent(token.value)
                + (wxSavedCity() ? '&city=' + encodeURIComponent(wxSavedCity()) : '')
                + (force ? '&force=1' : '');
        const r = await fetch(q);
        const d = await r.json();
        if (!r.ok) { wxErr.value = d.detail || '天气获取失败'; return; }
        wx.value = d;
        if (d.warning) showToast(d.warning, true);
      } catch (e) { wxErr.value = '天气获取失败：' + e.message; }
      finally { wxLoading.value = false; }
    }
    function wxToggleDock(ev) {     // 点击药丸=钉住/取消；卡片/输入面板内部的点击不收起
      const t = ev && ev.target;
      if (t && t.closest && t.closest('.wx-card, .wx-city-form')) return;
      wxOpen.value = !wxOpen.value;
    }
    function wxPromptCity() {       // 打开卡片内联输入（不用系统 prompt，避免跳出页面风格）
      wxCityInput.value = (wx.value && wx.value.city_input) || wxSavedCity() || '';
      wxCityEdit.value = true;
      wxOpen.value = true;
    }
    function wxCancelCity() { wxCityEdit.value = false; }
    function wxSetCity() {
      const name = (wxCityInput.value || '').trim();
      try {
        const k = 'dw_wx_city_' + (username.value || 'anon');
        if (name) localStorage.setItem(k, name); else localStorage.removeItem(k);
        if (!name) loadWeather(true);        // 清空 = 回到 .env 默认城市
      } catch (e) { /* 隐身模式忽略 */ }
      wxCityEdit.value = false;
      if (name) loadWeather(true);
    }
    // 登录后补取：token 存在 sessionStorage，刷新页面时往往「挂载早于登录」——
    // 那时 loadWeather 因无 token 直接 return，若不补一次，工作台会一直停在「天气还没取到」兜底卡。
    watch(token, (v) => {
      if (!v) { wx.value = null; wxErr.value = ''; return; }   // 登出：清掉上一个账号的天气
      // _wxStarted 为真说明挂载时已起过定时器（那次因无 token 没取到数）→ 这里只补一次取数；
      // 否则交给 wxStart()（它内部会取数并起定时器）。两条路径都只请求一次。
      if (_wxStarted) loadWeather(); else wxStart();
    });
    function wxStart() {   // 秒针 + 天气定时刷新（只启一次）
      if (_wxStarted) return;
      _wxStarted = true;
      wxSkin.value = wxSavedSkin();
      tickClock();
      setInterval(tickClock, 1000);
      loadWeather();
      setInterval(() => loadWeather(), 10 * 60 * 1000);   // 与后端 10 分钟缓存对齐
    }


    // ---- M4 收尾：教程指导（只读 md 阅读页；对齐蜀道「政策文件只读阅读器」）----
    const tutKind = ref('cli');          // 'cli' | 'api' —— 决定去哪取教程（M1 API 模块自带一篇）
    const TUT_API_MD = '/front/tutorial/api_tools_tutorial.md';
    const tutHtml = ref('');
    const tutTitle = ref('CLI 接入教程');
    const tutPath = ref('');
    const tutUpdated = ref('');
    const tutImages = ref(0);
    const tutMissing = ref(0);
    const tutLoading = ref(false);
    const tutLoaded = ref(false);

    function esc(s) {
      return String(s == null ? '' : s)
        .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
    }
    // 手写 md 渲染（零外部依赖、离线可用）：标题 / 表格 / 列表 / 引用 / 代码块 / 图片 / 加粗 / 行内码 / 折叠块
    function tutRender(md) {
      if (!md) return '';
      let text = String(md).replace(/\r\n/g, '\n');
      const blocks = [];
      text = text.replace(/```([a-zA-Z0-9_-]*)\n([\s\S]*?)```/g, (m, lang, code) => {
        blocks.push('<pre class="md-pre"><code' + (lang ? ' data-lang="' + esc(lang) + '"' : '') + '>'
                    + esc(code) + '</code></pre>');
        return '\u0000BLOCK' + (blocks.length - 1) + '\u0000';
      });
      const inline = (s) => {
        let h = esc(s);
        h = h.replace(/!\[([^\]]*)\]\(([^)]+)\)/g,
                      (m, alt, url) => '<img src="' + url + '" alt="' + alt + '" loading="lazy" />');
        h = h.replace(/`([^`]+)`/g, '<code>$1</code>');
        h = h.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
        h = h.replace(/\[([^\]]+)\]\(([^)]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>');
        h = h.replace(/\u0000BLOCK(\d+)\u0000/g, (m, i) => blocks[+i]);
        // 白名单放行折叠块（教程里用来收起长终端回显）
        return h.replace(/&lt;details&gt;/g, '<details class="md-det">')
                .replace(/&lt;\/details&gt;/g, '</details>')
                .replace(/&lt;summary&gt;/g, '<summary>')
                .replace(/&lt;\/summary&gt;/g, '</summary>');
      };
      const out = [];
      let inTable = false, inList = false; const para = [];
      const flushPara = () => { if (para.length) { out.push('<p>' + inline(para.join(' ')) + '</p>'); para.length = 0; } };
      const closeList = () => { if (inList) { out.push(inList === 'ul' ? '</ul>' : '</ol>'); inList = false; } };
      const closeTable = () => { if (inTable) { out.push('</tbody></table>'); inTable = false; } };
      for (const rawLn of text.split('\n')) {
        const t = rawLn.trim();
        if (!t) { flushPara(); closeList(); closeTable(); continue; }
        if (/^\u0000BLOCK\d+\u0000$/.test(t)) {   // 代码块占位符：还原成 <pre>
          flushPara(); closeList(); closeTable();
          out.push(t.replace(/\u0000BLOCK(\d+)\u0000/g, (m, bi) => blocks[+bi]));
          continue;
        }
        const h = t.match(/^(#{1,6})\s+(.*)$/);
        if (h) { flushPara(); closeList(); closeTable(); const lv = h[1].length;
                 out.push('<h' + lv + ' class="md-h">' + inline(h[2]) + '</h' + lv + '>'); continue; }
        if (/^(---+|\*\*\*+)$/.test(t)) { flushPara(); closeList(); closeTable(); out.push('<hr/>'); continue; }
        if (t.charAt(0) === '|') {
          if (/^\|[\s:\-|]+\|$/.test(t)) continue;
          flushPara(); closeList();
          if (!inTable) { out.push('<table class="md-table"><tbody>'); inTable = true; }
          const cells = t.split('|').slice(1, -1).map(c => c.trim());
          out.push('<tr>' + cells.map(c => '<td>' + inline(c) + '</td>').join('') + '</tr>');
          continue;
        }
        closeTable();
        if (/^[-*]\s+/.test(t)) { flushPara();
          if (inList !== 'ul') { closeList(); out.push('<ul class="md-ul">'); inList = 'ul'; }
          out.push('<li>' + inline(t.replace(/^[-*]\s+/, '')) + '</li>'); continue; }
        if (/^\d+\.\s+/.test(t)) { flushPara();
          if (inList !== 'ol') { closeList(); out.push('<ol class="md-ol">'); inList = 'ol'; }
          out.push('<li>' + inline(t.replace(/^\d+\.\s+/, '')) + '</li>'); continue; }
        closeList();
        if (t.charAt(0) === '>') { flushPara(); out.push('<blockquote class="md-quote">' + inline(t.replace(/^>\s?/, '')) + '</blockquote>'); continue; }
        para.push(t);
      }
      flushPara(); closeList(); closeTable();
      return out.join('\n');
    }
    async function loadTutorial(force, kind) {
      const k = kind || tutKind.value;
      if (k === tutKind.value && tutLoaded.value && !force) return;
      // ★ API 那篇是静态资源（不带鉴权）；CLI 那篇走 /api/tutorial/doc（要 token，
      //   因为它的图片来自用户目录 / Typora 贴图，需要后端按文件名同步到缓存目录）
      if (k === 'cli' && !token.value) return;
      tutKind.value = k;
      tutLoading.value = true;
      try {
        if (k === 'api') {
          const r = await fetch(TUT_API_MD);
          if (!r.ok) { showToast('教程文档读取失败（HTTP ' + r.status + '）', true); return; }
          const md = await r.text();
          const first = (md.split('\n').find(ln => ln.indexOf('# ') === 0) || '').replace(/^#\s*/, '').trim();
          tutTitle.value = first || 'API 工具接入教程';
          tutPath.value = 'front/tutorial/api_tools_tutorial.md';
          tutUpdated.value = '';
          tutImages.value = (md.match(/!\[[^\]]*\]\([^)]+\)/g) || []).length;
          tutMissing.value = 0;
          tutHtml.value = tutRender(md);
          tutLoaded.value = true;
          return;
        }
        const r = await fetch('/api/tutorial/doc?token=' + encodeURIComponent(token.value));
        const d = await r.json();
        if (!r.ok) { showToast(d.detail || '教程读取失败', true); return; }
        tutTitle.value = d.title || 'CLI 接入教程';
        tutPath.value = d.path || '';
        tutUpdated.value = d.updated || '';
        tutImages.value = (d.images || []).length;
        tutMissing.value = (d.missing || []).length;
        tutHtml.value = tutRender(d.content || '');
        tutLoaded.value = true;
        if (tutMissing.value) showToast(tutMissing.value + ' 张图片没找到，已跳过', true);
      } catch (e) { showToast('教程读取出错：' + e.message, true); }
      finally { tutLoading.value = false; }
    }
    function openTutorial(kind) { tutKind.value = kind || 'cli'; go('tutorial'); loadTutorial(true, tutKind.value); }
    // ★ 返回要回到**来的那个 tab**（CLI 教程 ← 自定义 CLI；API 教程 ← API 工具）
    function tutBack() { skTab.value = (tutKind.value === 'api' ? 'api' : 'cli'); go('skills'); }


    function copyCmd(cmd) {
      const done = () => showToast('已复制：' + cmd);
      const fallback = () => {
        const ta = document.createElement('textarea');
        ta.value = cmd; ta.style.position = 'fixed'; ta.style.opacity = '0';
        document.body.appendChild(ta); ta.select();
        try { document.execCommand('copy'); done(); } catch (e) { showToast('复制失败，请手动选择文本', true); }
        document.body.removeChild(ta);
      };
      try {
        if (navigator.clipboard && window.isSecureContext) navigator.clipboard.writeText(cmd).then(done, fallback);
        else fallback();
      } catch (e) { fallback(); }
    }
    async function loadSkills() {
      if (!token.value) return;
      try {
        const r = await fetch('/api/skills?token=' + encodeURIComponent(token.value));
        const d = await r.json();
        if (!r.ok) { showToast(d.detail || '技能列表读取失败', true); return; }
        skills.value = d.skills || [];
      } catch (e) { showToast('技能列表出错：' + e.message, true); }
    }
    async function skillOpen(name) {
      if (!token.value) return;
      try {
        const r = await fetch('/api/skills/get?name=' + encodeURIComponent(name) + '&token=' + encodeURIComponent(token.value));
        const d = await r.json();
        if (!r.ok) { showToast(d.detail || '读取技能失败', true); return; }
        skActive.value = d.name || name;
        skContent.value = d.content || '';
        skSaved.value = false;
      } catch (e) { showToast('读取技能出错：' + e.message, true); }
    }
    async function skillCreate() {
      const name = (skNewName.value || '').trim();
      if (!name) { showToast('先填技能名', true); return; }
      try {
        const r = await fetch('/api/skills/create?token=' + encodeURIComponent(token.value), {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ name, content: '# ' + name + '\n\n## 适用场景\n- \n\n## 执行步骤\n1. \n\n## 输出格式\n- \n\n## 注意事项\n- \n' })
        });
        const d = await r.json();
        if (!r.ok) { showToast(d.detail || '新建失败', true); return; }
        skNewName.value = '';
        await loadSkills();
        await skillOpen(d.name || name);
        showToast('技能「' + (d.name || name) + '」已创建，接着写正文 →');
      } catch (e) { showToast('新建出错：' + e.message, true); }
    }
    async function skillSave() {
      if (!skActive.value) return;
      if (skContent.value.length > SK_MAX) { showToast('正文超过 ' + SK_MAX + ' 字，请先精简', true); return; }
      try {
        const r = await fetch('/api/skills/save?token=' + encodeURIComponent(token.value), {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ name: skActive.value, content: skContent.value })
        });
        const d = await r.json();
        if (!r.ok) { showToast(d.detail || '保存失败', true); return; }
        skActive.value = d.name || skActive.value;
        skSaved.value = true;
        setTimeout(() => { skSaved.value = false; }, 1800);
        await loadSkills();
      } catch (e) { showToast('保存出错：' + e.message, true); }
    }
    async function skillDelete() {
      if (!skActive.value) return;
      if (!confirm('删除技能「' + skActive.value + '」？此操作不可撤销。')) return;
      try {
        const r = await fetch('/api/skills/delete?token=' + encodeURIComponent(token.value), {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ name: skActive.value })
        });
        const d = await r.json();
        if (!r.ok) { showToast(d.detail || '删除失败', true); return; }
        showToast('技能「' + skActive.value + '」已删除');
        skActive.value = ''; skContent.value = '';
        await loadSkills();
      } catch (e) { showToast('删除出错：' + e.message, true); }
    }
    function skillTpl() {
      if (skContent.value.trim() && !confirm('正文已有内容，用模板覆盖？')) return;
      skContent.value = SK_TPL;
    }
    async function skillSeed() {
      const tpls = [
        { name: '竞品对比表', content:
          '# 竞品对比表\n\n## 适用场景\n- 用户要求对比两个及以上竞品，或问「我们与 XX 的差别在哪」\n\n## 执行步骤\n1. 先查本地知识库已收录的双方资料，缺口再联网检索\n2. 按「产品定位 / 核心功能 / 价格 / 目标客户 / 近期动态」五个维度拉齐信息\n3. 取不到公开信息的格子写「未获取到公开信息」，禁止推测填充\n4. 表后给一句结论：我方哪一维度领先、哪一维度落后\n\n## 输出格式\n- Markdown 表格（竞品横向为列）+ 结论段 + 来源清单\n\n## 注意事项\n- 每个数据点标注来源与日期；超出 90 天的标注「可能已过时」\n' },
        { name: '来源可追溯', content:
          '# 来源可追溯\n\n## 适用场景\n- 用户要求「给出来源」「标注出处」「要能验证」\n\n## 执行步骤\n1. 每条关键结论后用 [n] 标注对应来源\n2. 文末列「参考来源」：[n] 标题 · 机构 · 日期 · 链接\n3. 无来源支撑的判断单独成行并写明「推测：…」\n4. 多个来源互相冲突时并列呈现各自说法，不擅自裁决\n\n## 输出格式\n- 正文带 [n] 角标 + 文末来源清单\n\n## 注意事项\n- 不得编造链接与日期；确实检索不到就写「未检索到公开来源」\n' },
      ];
      let made = 0, skipped = 0;
      for (const t of tpls) {
        try {
          const r = await fetch('/api/skills/create?token=' + encodeURIComponent(token.value), {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(t)
          });
          if (r.ok) made++; else skipped++;
        } catch (e) { skipped++; }
      }
      await loadSkills();
      showToast('示例技能：新建 ' + made + ' 个' + (skipped ? '，跳过 ' + skipped + ' 个（已存在）' : ''));
    }
    // ---- 输入框「/」技能目录 ----
    const skMenuList = computed(() => {
      const f = (skFilter.value || '').trim();
      const all = skills.value || [];
      if (!f) return all;
      return all.filter(s => s.name.indexOf(f) >= 0 || (s.desc || '').indexOf(f) >= 0);
    });
    function skOnInput() {
      const v = draft.value;
      // 仅当「/」在开头且后面还没打空格时视为唤出目录，避免打断正常提问
      if (/^\/[^\s]*$/.test(v)) { skFilter.value = v.slice(1); skMenuOpen.value = true; skMenuIdx.value = 0; }
      else skMenuOpen.value = false;
    }
    function skOnKey(e) {
      if (e.ctrlKey || e.metaKey) return;                    // Ctrl+Enter 发送交给原逻辑
      if (!skMenuOpen.value) { if (e.key === '/') { skMenuOpen.value = true; skMenuIdx.value = 0; skFilter.value = ''; } return; }
      const n = skMenuList.value.length;
      if (e.key === 'ArrowDown') { e.preventDefault(); skMenuIdx.value = n ? (skMenuIdx.value + 1) % n : 0; }
      else if (e.key === 'ArrowUp') { e.preventDefault(); skMenuIdx.value = n ? (skMenuIdx.value - 1 + n) % n : 0; }
      else if (e.key === 'Enter' || e.key === 'Tab') {
        if (n) { e.preventDefault(); skillPick(skMenuList.value[skMenuIdx.value].name); }
      } else if (e.key === 'Escape') {
        e.preventDefault(); skMenuOpen.value = false;
        if (/^\/[^\s]*$/.test(draft.value)) draft.value = '';
      }
    }
    function skillPick(name) {
      if (skPicked.value.indexOf(name) >= 0) { skMenuOpen.value = false; return; }
      if (skPicked.value.length >= SK_MAX_PICK) { showToast('单轮最多叠加 ' + SK_MAX_PICK + ' 个技能', true); return; }
      skPicked.value.push(name);
      draft.value = draft.value.replace(/^\/[^\s]*/, '');     // 只吃掉「/」与筛选词，保留已写的问题
      skMenuOpen.value = false;
      nextTick(() => { const el = document.querySelector('.input-area textarea'); if (el) el.focus(); });
    }
    function skillUnpick(name) { skPicked.value = skPicked.value.filter(n => n !== name); }

    async function send(text) {
      const q = (text != null ? text : draft.value).trim();
      if (!q || loading.value) return;
      draft.value = '';
      skMenuOpen.value = false;
      go('chat');
      connectWS(activeThread.value);  // WS 对准当前会话（UUID 或 username），保证右栏进度能收到
      // M4a：技能「本轮一次性」——发送即消费掉选择，同时记一份在气泡上供追溯
      const usedSkills = skPicked.value.slice();
      skPicked.value = [];
      messages.value.push({ role: 'user', text: q, skills: usedSkills });
      loading.value = true;
      stopping.value = false;
      desktopStatus('正在回答…');      // 桌面版：托盘提示跟着回答进度（长回答不用盯窗口）
      thinkingText.value = '';  // 新一轮对话清空思考区
      await nextTick(); scrollToBottom();
      const ctl = new AbortController();  // 中断兜底：后端卡在工具里时可客户端撒手
      chatAbort = ctl;
      try {
        let url = '/api/chat?token=' + encodeURIComponent(token.value) + '&question=' + encodeURIComponent(q);
        if (activeThread.value) url += '&thread_id=' + encodeURIComponent(activeThread.value);
        if (usedSkills.length) url += '&skills=' + encodeURIComponent(usedSkills.join(','));
        const r = await fetch(url, { method:'POST', signal: ctl.signal });
        const data = await r.json();
        if (!r.ok) messages.value.push({ role:'agent', text:'⚠️ ' + (data.detail || '请求失败'), title:'出错了' });
        else {
          const cut = !!data.cancelled;  // 用户点了「中断」：后端落一条占位回答并正常返回
          messages.value.push({ role:'agent', text: data.answer,
                                title: cut ? '⏹ 已中断' : (q.slice(0,12) + (q.length > 12 ? '…' : '')),
                                msg_id: data.last_msg_id || null,  // M3：消息 id 供导出入库去重 / 删除问答用
                                interrupted: cut,
                                kb_ingested: false });
          if (data.thread_id && !activeThread.value) activeThread.value = data.thread_id; // 旧版直发自动归档
          await loadSessions();  // 标题/消息数/时间已变，刷新列表
          if (cut) showToast('已中断本轮回答');
        }
      } catch (e) {
        if (e && e.name === 'AbortError') {
          // 已请求中断但后端仍卡在工具调用里 → 前端先撒手，不等它了
          messages.value.push({ role:'agent', text:'⏹ 已中断本轮回答（后端仍在收尾，结果会被丢弃）。',
                                title:'⏹ 已中断', interrupted:true });
        } else {
          messages.value.push({ role:'agent', text:'⚠️ 请求出错：' + e.message, title:'出错了' });
        }
      } finally { chatAbort = null; stopping.value = false; loading.value = false; desktopStatus(''); await nextTick(); scrollToBottom(); }
    }

    // 中断本轮回答：给后端置中断标记（它会在下一次模型调用前停下并落占位回答），
    // 8s 内没回来就前端先撒手（工具内部等 HTTP 响应那一步打断不了，只能等它返回）。
    async function stopGen() {
      if (!loading.value || stopping.value) return;
      stopping.value = true;
      showToast('正在中断本轮回答…');
      try {
        let url = '/api/chat/cancel?token=' + encodeURIComponent(token.value);
        if (activeThread.value) url += '&thread_id=' + encodeURIComponent(activeThread.value);
        const r = await fetch(url, { method:'POST' });
        const d = await r.json().catch(() => ({}));
        if (r.ok && d.hit === false) showToast('这轮回答可能刚好结束了');
        setTimeout(() => { if (loading.value && chatAbort) chatAbort.abort(); }, 8000);
      } catch (e) {
        showToast('中断请求出错，改为不再等待：' + e.message, true);
        if (chatAbort) chatAbort.abort();
      }
    }

    // 删除一轮问答（AI 回复气泡右上角「🗑 删除」）：只把回答的 msg_id 交给后端，
    // 成对删除「该轮提问 + 回答 + 工具链」；本地同步抹掉这条回复与它前面最近的提问。
    async function delPair(m, i) {
      const mid = m.msg_id || null;
      if (!mid) { showToast('这条回复没有消息 id（刚生成或历史异常），请刷新会话后再删', true); return; }
      if (!confirm('删除这轮问答？\n\n该轮的「问题 + 回答」会一起永久删除，不可恢复。')) return;
      try {
        const qs = new URLSearchParams({ token: token.value, msg_id: String(mid) });
        if (activeThread.value) qs.set('thread_id', activeThread.value);
        const r = await fetch('/api/chat/message/delete?' + qs.toString(), { method:'POST' });
        const d = await r.json().catch(() => ({}));
        if (!r.ok) { showToast(d.detail || '删除失败', true); return; }
        let j = i - 1;
        while (j >= 0 && messages.value[j] && messages.value[j].role !== 'user') j--;
        const from = (j >= 0 && messages.value[j] && messages.value[j].role === 'user') ? j : i;
        messages.value.splice(from, i - from + 1);
        showToast('已删除 ' + (d.deleted || 1) + ' 条消息（问题 + 回答）');
        await loadSessions();
      } catch (e) { showToast('删除出错：' + e.message, true); }
    }
    async function doExport(m, fmt) {
      exporting.value = true;
      try {
        const qs = new URLSearchParams({ token: token.value, title: m.title || '情报简报', fmt }).toString();
        const r = await fetch('/api/export?' + qs, { method:'POST',
          headers:{'Content-Type':'application/json'}, body: JSON.stringify({ content: m.text }) });
        const d = await r.json();
        if (!r.ok) { showToast(d.detail || '导出失败', true); return; }
        const fileUrl = (fmt !== 'md' && d.download_pdf) ? d.download_pdf : d.download_md;
        const ext = (fmt !== 'md' && d.download_pdf) ? 'pdf' : 'md';
        const base = (m.title || '情报简报').replace(/[\\/:*?"<>|]/g, '_');
        await saveFromUrl(fileUrl, base + '.' + ext);   // ★ 桌面版走原生「另存为」，网页版走浏览器下载
        if (fmt !== 'pdf') askIngestExport(m);  // M3：导出后询问是否入库（仅 md 导出可入）
      } catch (e) { showToast('导出出错：' + e.message, true); }
      finally { exporting.value = false; }
    }
    // 下载已生成的周报（走统一的 saveFromUrl：桌面版原生「另存为」，网页版浏览器下载）
    async function downloadReport(r, fmt) {
      const url = (fmt === 'pdf' && r.download_pdf) ? r.download_pdf : r.download_md;
      if (!url) { showToast('该周报没有可下载的文件', true); return { ok: false, error: 'no-url' }; }
      const base = (r.title || '周报').replace(/[\\/:*?"<>|]/g, '_');
      return await saveFromUrl(url, nameFromUrl(url, base + '.' + (fmt === 'pdf' ? 'pdf' : 'md')));
    }
    // ---- M5c：周报应用内阅读（读 output/{user}/ 下已落盘的 md，不再只能"下载到本地看"）----
    //  设计对齐「技能与工具 → CLI 在线教程指南」那一页：同一个 .tutorial-view 壳 + 左上角「← 返回」。
    //  ★ 数据来源与下载**完全同源**（同一个 /api/download?token&name=），只是这里 fetch 出文本自己渲染 ——
    //    所以后端零改动，也不会出现"能下载却读不到"的两套逻辑。
    const readerHtml = ref('');
    const readerTitle = ref('周报');
    const readerMeta = ref('');
    const readerKey = ref('');        // 缓存键 = 该周报的 md URL（不能只看标题：同名周报可能有多份）
    const readerItem = ref(null);
    const readerLoading = ref(false);
    const READER_NO_MD = '该周报在 output/ 下没有 md 文件（只能下载 PDF）';

    async function openReport(r, force) {
      if (!r) return { ok: false, error: 'no-item' };
      readerItem.value = r;
      go('reader');
      const url = r.download_md;
      if (!url) { showToast(READER_NO_MD, true); return { ok: false, error: 'no-md' }; }
      if (readerHtml.value && readerKey.value === url && !force) return { ok: true, cached: true };
      readerLoading.value = true; readerHtml.value = '';
      try {
        const resp = await fetch(url);
        if (!resp.ok) {
          const d = await resp.json().catch(() => ({}));
          showToast(d.detail || ('周报读取失败（HTTP ' + resp.status + '）'), true);
          return { ok: false, error: 'http-' + resp.status };
        }
        const md = await resp.text();
        readerTitle.value = r.title || '周报';
        readerMeta.value = (r.item_count ? r.item_count + ' 条 · ' : '') + (r.created_at || '')
                         + ' · 自 output/ 读取，只读浏览（不改文件）';
        readerHtml.value = tutRender(md);     // 复用教程页那套零依赖 md 渲染器
        readerKey.value = url;
        return { ok: true, chars: md.length };
      } catch (e) {
        showToast('周报读取出错：' + e.message, true);
        return { ok: false, error: 'exception' };
      } finally { readerLoading.value = false; }
    }
    // ★ 视图名是**复数** reports（导航也是这个名字）—— 2026-09-25 用户报障：这里曾误写成
    //   go('report')，项目里没有 v-show="view === 'report'" → 所有视图都被隐藏 → 整页纯色空白，
    //   点任意导航按钮才被拉回。已加两条静态断言（frontend_setup_exports_check 的"死路由"+ 本文件）。
    //   顺带：go('reports') 会改 hash，hashchange 里那条分支会顺手刷新报告列表（newReports=0 + loadReports）。
    function readerBack() { readerItem.value = null; go('reports'); }
    function scrollToBottom() { if (scroll.value) scroll.value.scrollTop = scroll.value.scrollHeight; }

    onMounted(async () => {
      restoreBg();
      // M5c：直接刷新/直达 #/reader 时没有"要读哪份周报"这个上下文（readerItem 只存在于内存），
      //   否则会停在空阅读页且「↻ 重新读取」无从下手 —— 直接送回报告列表并说明入口。
      if (view.value === 'reader') { view.value = 'report'; showToast('阅读页请从报告列表点「阅读」进入', true); }
      // ★ 启动时先验登录态（2026-09-23 用户报障修）：localStorage 里的 token 可能已被后端重启弄失效。
      //   验不过就直接回登录页，而不是"假装已登录"再去各接口吃 401（那正是"数据全是 0"的成因）。
      if (token.value) {
        if (await verifySession()) {
          connectWS(); loadReports(); loadSubs(); loadMyData(); loadProviders(); loadSouls(); loadMemory();
          loadVoicePacks(); loadSessions(); loadKbStatus(); loadSkills();
        }
      }
      wxStart();
    });

    return {
      token, username, password, regRole, errMsg, busy, role, view, go,
      messages, monitor, sessions, activeThread, draft, loading, exporting, scroll, examples, thinkingText,
      loadSessions, newSession, switchSession, delSession, renameSession, fmtTime,
      kbStatus, kbBusy, kbIngestShow, kbIngestCtx, kbIngestRaw, kbIngestTitle,
      kbEvalDone, kbEval, kbUseClean, kbFileInput, kbQueryText, kbQueryHits,
      loadKbStatus, kbUploadClick, kbUploadFile, kbIngestOpen, kbIngestOpenWith, kbIngestEvalNow,
      kbIngestRetry, kbIngestConfirm, kbDeleteDoc, kbQueryRun, askIngestExport,
      kbPercent, activityTrend, recentActivity,
      showBgPanel, toggleBgPanel, bgOpacity, bgImages, bgActive, onBgUpload, applyBg, resetBg, pickBg, delBg,
      providers, newProv, loadProviders, addProvider, saveProvider, activateProvider, delProvider,
      tsSources, tsCaps, tsStats, tsCandidates, tsSpecText, tsDiscoverSlug, tsEditingId, tsBusy, tsForm,
      capModal, capKind, capSource, capItems, capBusy, capDraftBusy, capDraftWarns,
      capOpen, capClose, capCoveredText, capClearAll, capDraft, capSave, capHasEvidence,
      tsCheckedCount, tsModal, tsModalSource,
      mcpSources, mcpAll, mcpCaps, mcpStats, mcpForm, mcpEditingId, mcpBusy, mcpModal, mcpProbe, mcpProbeTitle,
      mcpTransportText, mcpTargetText, mcpNewSource, mcpEdit, mcpSave, mcpDel, mcpTest, mcpDiscover,
      mcpConfirm, mcpCloseModal, mcpJsonField, tsConfirmSource,
      tsResetForm, tsSourcePayload, tsConfirmPayload, tsCandidateBadge, tsParamsText,
      tsStateText, tsAuthText, tsNewSource, tsCloseModal, tsOpenImport,
      tsLoad, tsEdit, tsSave, tsDel, tsTest, tsDiscover, tsConfirm, tsViewCandidates, tsCandFromApi,
      soulContent, soulReq, soulGenOut, soulGenning, soulSaved, saveSoul, genSoul, soulImport,
      soulList, soulActive, newSoulName, loadSouls, switchSoul, createSoul, delSoul,
      memoryContent, memoryInitialized, memorySaved, memoryGenning, loadMemory, saveMemory, initMemory,
      memSuggestions, memSnapshots, memBusy, memProtectManual, memEvidence, memSkipped, memChanged,
      usageInfo, loadUsage, scheduleUsageRefresh,
      costInfo, loadCost, recalcCost, costTokens, costAmountText, numOrNull, COST_NOTE,
      priceAlerts, priceAlertText, loadPriceAlerts, notifyPriceAlerts, ackPriceAlert,
      priceSummary, priceEdit, priceSaving, itemKey, loadPriceSummary, savePrice, deltaText,
      priceHist, complexDraft, targetEdit, saveTarget, delHistory, priceItems, priceCurrent, priceIdx, priceSelectedKey, pricePickerOpen, pickPriceItem, priceInfoOf, isComplex, toggleHistory, setRegistered, saveComplex, clearComplex,
      resetPriceState,
      memCheckedCount, suggestMemory, decorateSuggestions, clearSuggestions, applyMemory,
      loadSnapshots, restoreSnapshot,
      voicePacks, voiceAvailable, voiceBusy, newVoicePack, loadVoicePacks, onVoiceFile, buildVoicePack,
      selectVoicePack, delVoicePack, speakMsg, voiceFileInput,
      toast, toastErr, myData, prof, profSaved, profileFilled,
      showOnboard, onboardCompany, onboardComps, onboardInterests,
      newInterest, newWatch, newComp, newProd, batchModal, openBatch, submitBatch,
      displayName, greeting,
      doLogin, doLogout, send, doExport, downloadReport, splitSections, renderRich,
      // 2026-09-16 收尾：登录/注册两卡分离（authMode 切卡、注册校验、账号不存在引导）
      authMode, regPwd2, notExist, regReady, regHint, switchAuth, goRegister, doRegister,
      stopping, stopGen, delPair,  // 聊天界面优化：中断本轮回答 / 删除一轮问答
      finishOnboard, skipOnboard, delItem,
      reports, subs, newSubName, newSubScope, newSubLang, selCount, newReports, digestRunning,
      loadReports, loadSubs, updateSub, addSub, delSub, runDigestNow,
      // M5c 周报应用内阅读（模板里用到的每个标识符都要在这儿出现，否则静默失效）
      openReport, readerBack, readerHtml, readerLoading, readerTitle, readerMeta, readerItem,
      saveProfile, saveInterest, saveWatch, saveCollection, saveCompetitor, saveCProduct,
      addInterest, addItemSimple, addCompetitor, addProduct,
      // M4a 技能与工具
      skills, skTab, skActive, skContent, skSaved, skNewName, skPicked, skMenuOpen, skMenuIdx, skFilter,
      skMenuList, SK_MAX, SK_TPL, cliItems, cliTemplates, cliStates, cliSummary, cliNote, cliDoneCount,
      // ⚠️ 输入框「/」技能目录的四个**处理函数**必须导出：漏了的话模板把它们绑成 undefined，
      //    表现为「敲 / 完全没反应」（v-if/v-for 用的状态变量导出与否看不出来，只有交互会失效）。
      //    静态不变量检查：tests/frontend_setup_exports_check.py（已固化为用例）
      skOnInput, skOnKey, skillPick, skillUnpick,
      loadSkills, skillOpen, skillCreate, skillSave, skillDelete, skillSeed, skillTpl, copyCmd,
      loadCliCatalog, cliNew, cliFill, cliEdit, cliCancel, cliSave, cliSetState, cliSaveNote,
      cliDelete, cliRunAll, cliEditing, cliErr, cliForm, cliRunning, cliRunOut, cliParseNotes,
      cliDrafting, cliDraftInfo, cliDraftWarns, cliAbilityDraft,
      openTutorial, loadTutorial, tutBack, tutRender, tutHtml, tutTitle, tutPath, tutUpdated, tutImages, tutMissing, tutLoading, tutKind,
       // 工作台 时钟 + 天气（012 号设计）
       clockTime, clockDate, wx, wxLoading, wxErr, wxOpen, wxCityEdit, wxCityInput, wxSkin, wxSetSkin, WX_SKINS,
       loadWeather, wxPromptCity, wxCancelCity, wxSetCity, wxStart, wxToggleDock,
      // M4b CLI 面板
      shAllowed, shSandbox, shNotes, shLines, shInput, shBusy, shElapsed, shOut, shIn,
      shLoadAllowed, shExec, shFill, shFocus, shClear, shHist,
    };
  }
}).mount('#app');
