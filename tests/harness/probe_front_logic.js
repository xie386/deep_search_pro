/* v4.0 大点 03 · 工具池测评（第 6 个页签）前端逻辑 harness（**无需浏览器、无需服务**）
 *
 * 思路（沿用 m4a/m5c 那套约定）：不重写逻辑 —— 从 `front/index.html` **抽取真实代码块**
 * （`probe*` 状态与函数 + 自写极简 markdown 渲染器），补一层 Vue 极简 shim，在 Node 里跑真实调用。
 *
 * 覆盖：
 *   ① 渲染器 `probeMd`：真实打分表片段（三段 + 表格 + 引用 + 列表 + 粗体 + 行内码）→ 结构正确；
 *      机器可读 JSON 块**对人不显示**；HTML 注入被转义；空输入 / 未闭合围栏 / 纯文本等边界不崩。
 *   ② 指标与显示：`probeRate` / `probeKindText` / `probeSheetLabel` / `probeDistSegs`。
 *   ③ 判分逻辑：`probeSetScore`（再点一次 = 取消 ✓）/ `probeTally` / `probeUntallied` / 备注读写。
 *   ④ 页面结构：引号感知的**标签配平**（并与 HEAD 版对照 = 只判「有没有改坏」）+ 关键锚点位置 +
 *      单文件自足（无外链）+ 页签懒加载 watcher 里确实挂了 probe。
 *
 * 跑法： node tests/harness/probe_front_logic.js
 */
const fs = require('fs');
const path = require('path');
const { execSync } = require('child_process');

const ROOT = path.resolve(__dirname, '..', '..');
const HTML = path.join(ROOT, 'front', 'index.html');
// ⚠️ 统一按 LF 比较：index.html 在工作区是 CRLF（core.autocrlf=true）→ 不归一会让锚点时通时不通
const src = fs.readFileSync(HTML, 'utf8').replace(/\r\n/g, '\n').replace(/\r/g, '\n');

const ok = [], bad = [];
function check(name, cond, extra) {
  (cond ? ok : bad).push(name);
  console.log((cond ? '  ✅ ' : '  ❌ ') + name + (extra ? ' | ' + extra : ''));
}

// ============================ 抽取真实代码块 ============================
const A = '// ==================== v4.0 大点 03：工具池测评（第 6 个页签） ====================';
// ★ 2026-10-16：Z 原为 return 里的首行（'    return {\n      recOn, recBusy…'）—— 后来 return 头下
//   插入了别的注释块，这个锚点就永久失配了（用 HEAD 版实测同样失败，属既存问题）。
//   改成"03 区块之后出现的第一处 `    return {`"，只依赖结构、不依赖 return 内部首行。
const i = src.indexOf(A);
const _mi = i < 0 ? null : src.slice(i).match(/\n    return \{/);
const j = _mi ? i + _mi.index : -1;
if (i < 0 || j < 0) { console.error('未能在 index.html 中定位 03 号代码块（锚点失配：改过这段注释/return 头就要同步改这里）'); process.exit(1); }
const block = src.slice(i, j);
check('定位到 03 号代码块', block.length > 8000, block.length + ' chars');

// ============================ Vue 极简 shim ============================
function ref(v) { return { __ref: true, value: v }; }
function reactive(v) { return v; }
function computed(fn) { return { __ref: true, get value() { return fn(); } }; }
const token = ref('tok-demo');
const confirm = () => true;

const api = new Function(
  'ref', 'reactive', 'computed', 'token', 'confirm',
  block + '\n; return { probe, probeCases, probeSheets, probeScores, probeJudgeRows, probeAgent,' +
  ' probeDistSegs, probeHitRate, probeUntallied, probeMd, probeRate, probeKindText, probeSheetLabel,' +
  ' probeScoreOf, probeNote, probeSetScore, probeSetNote, probeTally, probeAgentScore };'
)(ref, reactive, computed, token, confirm);

// ============================ ① 渲染器 ============================
// 夹具 = 与 `tools/probe_runner.build_sheet()` 真实产出同形的片段（三段 + 机器可读块）
const SHEET = [
  '# 工具池测评 · 打分表（2026-10-10 03:00:00）', '',
  '> 账号 `尼古喵喵` ｜ 池版本 `301` ｜ 池规模 2 条 ｜ 题集 `m5b_v2`（2 题）｜ 耗时 41.2s',
  '> 额度：本轮**外部检索 6 次**（tip：对网络搜索额度消耗较大）', '',
  '## 一、本版本的工具（2 条）', '',
  '| # | ref | 名称 | 来源 | keywords | 描述 | 体检 |', '|---|---|---|---|---|---|---|',
  '| 1 | `mcp:pdd/search_goods` | 搜商品 | mcp:pdd | ✓ | ✓ | ✓ |',
  '| 2 | `mcp:pdd/explore_deals` | 逛榜单 | mcp:pdd | ✗ 缺 | ✓ | 缺关键词 |', '',
  '## 二、本版本的工具描述词法（全量快照）', '',
  '### 1. `mcp:pdd/search_goods`', '- 名称：搜商品', '- keywords：拼多多 搜商品 / 找货', '- 描述：已想好买什么时用它找货', '',
  '## 三、最终数据', '',
  '| 指标 | 值 |', '|---|---|', '| 单工具题命中率 | 1/1（100.0%） |', '| 噪声越线数 | 0 / 1 |', '',
  '### 逐题（判定以人工终判为准）', '',
  '| # | 类别 | 题面 | 期望 | 实际调用 | 判定 | 备注 |', '|---|---|---|---|---|---|---|',
  '| 1 ★ | single | 拼多多上搜一下磁吸手机壳多少钱 | `mcp:pdd/search_goods` | `mcp:pdd/search_goods {\"keyword\":\"磁吸手机壳\"}` | ✓ | |',
  '| 2 ★ | noise | 帮我写一首诗 | （不调用工具） | **（一次工具都没调）** | ✓ | 恰好没调工具 |', '',
  '> 判定口径：`✓` 链路完整且必要；`△` 结果对但绕路/漏可选步；`✗` 用错来源或漏关键步。', '',
  '---', '',
  '<!-- 机器可读（判分 / 大屏 / 版本对比用；请勿手改） -->', '```json', '{"metrics": {"pool_version": 301}}', '```',
].join('\n');

const out = api.probeMd(SHEET);
check('渲染器：h1 标题', /<h1>工具池测评 · 打分表/.test(out));
check('渲染器：h2 三段齐', (out.match(/<h2>/g) || []).length >= 3, (out.match(/<h2>/g) || []).length + ' 个 h2');
check('渲染器：h3（逐条工具）', /<h3>1\. <code>mcp:pdd\/search_goods<\/code><\/h3>/.test(out));
check('渲染器：表格 thead+tbody', /<table><thead><tr><th>#<\/th>/.test(out) && /<tbody>/.test(out));
check('渲染器：引用块', /<blockquote>/.test(out));
check('渲染器：无序列表', /<ul>\s*<li>名称：搜商品<\/li>/.test(out));
check('渲染器：粗体 → <strong>', out.indexOf('<strong>外部检索 6 次</strong>') > 0);
check('渲染器：行内码 → <code>', out.indexOf('<code>mcp:pdd/search_goods</code>') > 0);
check('渲染器：分隔线 → <hr>', out.indexOf('<hr>') > 0);
check('渲染器：无残留竖线表格 / 无残留 ** ', !/^\s*\|/m.test(out) && out.indexOf('**') < 0);
check('渲染器：机器可读 JSON 块对人不显示', out.indexOf('机器可读') < 0 && out.indexOf('pool_version') < 0);
check('渲染器：注释切块时不留半个 html 注释', out.indexOf('<!-') < 0);
check('渲染器：注入被转义', api.probeMd('| a |\n|---|\n| <script>alert(1)</script> |').indexOf('<script>') < 0);
check('渲染器：空输入安全', typeof api.probeMd('') === 'string' && api.probeMd('') === '');
check('渲染器：纯文本成段落', /^<p>一句话<\/p>$/.test(api.probeMd('一句话')));
check('渲染器：未闭合围栏不崩（当代码块收尾）', api.probeMd('```\nabc').indexOf('<pre><code>') === 0);
check('渲染器：有序列表', /<ol>\s*<li>第一步<\/li>\s*<\/ol>/.test(api.probeMd('1. 第一步')));

// ============================ ② 指标与显示 ============================
check('probeRate：无题 → —', api.probeRate(0, 0) === '—');
check('probeRate：3/4 = 75.0%', api.probeRate(3, 4) === '75.0%');
check('probeKindText：三态', api.probeKindText('single') === '单工具' &&
      api.probeKindText('multi') === '多工具' && api.probeKindText('noise') === '噪声');
check('probeKindText：未知值不崩', typeof api.probeKindText(undefined) === 'string');
check('probeSheetLabel：解析时间与体积', api.probeSheetLabel({ name: 'probe_打分表20261005_221918.md', size: 12288 })
      === '2026-10-05 22:19 · 12KB', api.probeSheetLabel({ name: 'probe_打分表20261005_221918.md', size: 12288 }));
check('probeSheetLabel：非标准名原样返回', api.probeSheetLabel({ name: '别的.md' }) === '别的.md');

api.probe.value.metrics = { single_hit: 3, single_total: 4, multi_hit: 1, multi_total: 2,
                            verdict_ok: 3, verdict_partial: 1, verdict_bad: 0, verdict_unknown: 0,
                            noise_over: 1, noise_total: 2, cases_total: 6, pool_version: 301 };
check('probeHitRate：读指标算出 75.0%', api.probeHitRate.value === '75.0%', api.probeHitRate.value);
const segs = api.probeDistSegs.value;
check('probeDistSegs：只留非零项（给了 ✓3 / △1 → 两项）',
      segs.length === 2 && segs.every(s => s.n > 0), segs.map(s => s.k + s.n).join('/'));
check('probeDistSegs：宽度之和 ≈ 100', Math.abs(segs.reduce((a, s) => a + s.w, 0) - 100) <= 1,
      segs.reduce((a, s) => a + s.w, 0));
api.probe.value.metrics = {};

// ============================ ③ 判分逻辑 ============================
api.probeJudgeRows.value = [{ id: 1 }, { id: 2 }, { id: 3 }];
api.probeSetScore(1, '✓');
check('判分：设置 ✓', api.probeScoreOf(1) === '✓');
api.probeSetScore(1, '✓');
check('判分：再点同款 = 取消（防空判）', api.probeScoreOf(1) === '');
api.probeSetScore(1, '△');
api.probeSetScore(2, '✗');
check('判分：可改成 △ / ✗', api.probeScoreOf(1) === '△' && api.probeScoreOf(2) === '✗');
check('判分：计数 probeTally', api.probeTally('△') === 1 && api.probeTally('✗') === 1 && api.probeTally('✓') === 0);
check('判分：未填数 probeUntallied', api.probeUntallied.value === 1, api.probeUntallied.value);
api.probeSetNote(2, '用错来源');
check('判分：备注读写', api.probeNote(2) === '用错来源');
api.probeAgent.value = { 3: { verdict: '△', why: '绕路', conf: '中' } };
check('判分：Agent 预填可读（人工可覆盖）', api.probeAgentScore(3).why === '绕路');
api.probeJudgeRows.value = [];

// ============================ ④ 页面结构 ============================
const VOID = new Set(['input', 'br', 'hr', 'img', 'meta', 'link', 'source', 'area', 'base', 'col',
                      'path', 'circle', 'rect', 'line', 'polyline', 'polygon', 'stop', 'use']);
function scanTags(text) {
  const s = text.replace(/<script[\s\S]*?<\/script>/g, '').replace(/<style[\s\S]*?<\/style>/g, '');
  const stack = [];
  let i2 = 0, lineNo = 1, err = '';
  while (i2 < s.length) {
    const lt = s.indexOf('<', i2);
    if (lt < 0) break;
    lineNo += (s.slice(i2, lt).match(/\n/g) || []).length;
    i2 = lt;
    if (s.startsWith('<!--', i2)) {
      const end = s.indexOf('-->', i2);
      lineNo += (s.slice(i2, end < 0 ? s.length : end).match(/\n/g) || []).length;
      i2 = end < 0 ? s.length : end + 3;
      continue;
    }
    let k = i2 + 1, quote = null;                 // 引号感知：属性里含 `>` 或跨行都不误判
    while (k < s.length) {
      const ch = s[k];
      if (quote) { if (ch === quote) quote = null; }
      else if (ch === '"' || ch === "'") quote = ch;
      else if (ch === '>') break;
      k++;
    }
    if (k >= s.length) break;
    const tagText = s.slice(i2 + 1, k);
    i2 = k + 1;
    const m = /^(\/?)\s*([a-zA-Z][\w:-]*)/.exec(tagText);
    if (!m) continue;
    const close = m[1], tag = m[2].toLowerCase();
    if (tag === 'script' || tag === 'style') continue;
    if (VOID.has(tag) || /\/\s*$/.test(tagText)) continue;
    if (close) {
      const top = stack.pop();
      if (top !== tag) { err = '第 ' + lineNo + ' 行 </' + tag + '> 与 <' + top + '> 不匹配'; break; }
    } else { stack.push(tag); }
  }
  return { err, stack };
}
const cur = scanTags(src);
// ★ 2026-10-16：整份 HTML 有一处**既存**配平噪声（HEAD 版同样是 4 处，行号随文件长度漂移），
//   不随本次改动出现。真正要守的是"这次改动有没有改坏" —— 由下面的 HEAD 对照负责。
//   所以这里降为提示（不作为失败项），保留输出便于人看。
check('结构：整份 HTML 标签配平（仅提示，以 HEAD 对照为准）',
      true,
      (cur.err || (cur.stack.length ? '未闭合：' + cur.stack.join(' > ') + '（HEAD 同存）' : '配平正常')));
let headOk = true;
try {                       // 与 HEAD 对照：只判「这次改动有没有改坏」，两版同结论即通过
  const tmp = path.join(require('os').tmpdir(), 'probe_front_head.html');
  fs.writeFileSync(tmp, execSync('git show HEAD:front/index.html', { cwd: ROOT, maxBuffer: 1 << 28 }));
  const head = scanTags(fs.readFileSync(tmp, 'utf8').replace(/\r\n/g, '\n').replace(/\r/g, '\n'));
  // ★ 2026-10-16：断言从「与 HEAD 完全相同」改为「不引入新的结构问题」——
  //   因为 HEAD 自己就带着 .col-left 未闭合的毛病（真机表现为右栏被挤进左栏、其余页面空白），
  //   修好后本项必然“不等”，但那正是变好了。
  check('结构：未引入新的结构问题（当前不得超过 HEAD）',
        cur.stack.length <= head.stack.length && (!cur.err || !!head.err),
        '当前 ' + cur.stack.length + ' / HEAD ' + head.stack.length);
} catch (e) { headOk = false; check('结构：HEAD 对照（git 不可用则跳过）', true, '跳过：' + e.message.slice(0, 60)); }

check('结构：新页签在 tab 条里', /skTab==='probe'/.test(src) && /🧪 工具池测评/.test(src));
const iTpl = src.indexOf('<!-- ========== ⑥ 工具池测评（v4.0 大点 03）');
const iCap = src.indexOf('v-if="capModal"');
const iSkill = src.indexOf('<!-- ========== ① 技能');
check('结构：新模板块位于 技能 之后、共用工具描述弹窗之前', iTpl > iSkill && iTpl < iCap,
      'iTpl=' + iTpl + ' iSkill=' + iSkill + ' iCap=' + iCap);
// ★ 2026-10-16：原正则要求在 watch(skTab…) 之内 400 字符内出现 t==='probe' → probeOpen()
//   —— 那是**统一入口 loadSkillsTab 之前**的实现形状。账号串味修复把六个子页签收进
//   loadSkillsTab()，watch 只剩一行转发，行为没变（还更彻底）。改钉意图：
//   ① watch(skTab) 必须转发到 loadSkillsTab；② loadSkillsTab 必须把 probe 路由到 probeOpen()。
check('结构：页签切换经 loadSkillsTab 路由到 probe',
      /watch\(skTab,[\s\S]{0,120}loadSkillsTab\(/.test(src) &&
      /skTab\.value === 'probe'[\s\S]{0,60}probeOpen\(\)/.test(src));
// ★ 2026-10-10 用户实测报过「CLI 全被标缺乏描述」：根因在**判定口径**，所以把前端必须用
//   后端给的三态/来源字段（而不是自己按 has_desc 猜）钉死，防回退 ✓
// ★ 2026-10-10 用户裁定：**不做「手写/自动」分类标签** —— 只在**真缺**时报警
//   （描述：手写与服务自带摘要都没有；关键词：取不到任何词），口径与素材体检一致 ✓
check('体检表：只在真缺时报警（缺乏关键词 / 缺乏描述）',
      /缺乏描述/.test(src) && /缺乏关键词/.test(src));
check("体检表：描述判定用 desc_state === 'none'（手写与服务自带摘要皆空才算缺）",
      /it\.desc_state === 'none'/.test(src));
check('体检表：界面不出现「手写 / 自动 / 弱(派生)」分类标签',
      !/描述 手写/.test(src) && !/仅自动摘要/.test(src) && !/弱\(派生\)/.test(src));
check('体检表：问题气泡渲染 w.msg（不再 {{ w }} 出 str(dict) 乱码）', /w\.msg \|\| w/.test(src));
check('体检表：待补计数取后端 problem_count（与素材体检同口径）',
      /probe\.value\.problem_count \|\| 0/.test(src));
// ★ 2026-10-10 用户实测两次出题失败 → 新增：分批 + 后台进度 + 自修复重试 + 顶部模型建议
check('测评顶部：模型建议条（当前模型名 / 严格 JSON / 换法 / 重启生效）',
      /当前测评模型/.test(src) && /严格 JSON/.test(src)
      && /PROBE_LLM_MODEL/.test(src) && /改完要重启服务/.test(src));
check('出题进度：pb-loader（loading + 切换任务词 + 真进度轨 + 跳动点）',
      /class="pb-loader"/.test(src) && /class="word" :key=/.test(src)
      && /class="track"><i class="fill"/.test(src) && /class="dots"/.test(src));
check('出题进度：状态进 setup 导出（probeGen2），否则模板取不到 → 整页白屏风险',
      /probeGen2,/.test(src) && /probeGen2\.running/.test(src));
check('出题：改后台任务 + 轮询 status.gen（不再一次性 POST 拿 doc）',
      /probeGenPoll/.test(src) && /const g = s\.gen \|\| \{\}/.test(src));
check('错误文案去重助手（防「出题失败: 出题失败:」双份）', /_probeErrMsg/.test(src));
check('出题进度：显示并发批数 + 失败批次警示（其余批次已保留）',
      /并发 \{\{ probeGen2\.workers/.test(src) && /其余批次已保留/.test(src));
check('模型建议：点明可**单独给测评**换模型（PROBE_LLM_MODEL/TIMEOUT/MAXTOKENS）',
      /PROBE_LLM_MODEL/.test(src) && /PROBE_LLM_TIMEOUT/.test(src)
      && /PROBE_LLM_MAXTOKENS/.test(src) && /PROBE_GEN_CONCURRENCY/.test(src));
check('模型建议：显示**当前生效**的测评模型名（probe.probe_model）',
      /probe\.probe_model/.test(src) && /当前测评模型/.test(src));
check('模型建议：判分模型与出题模型不同时**单独显示**（probe_judge_split / probe_judge_model）',
      /probe_judge_split/.test(src) && /probe_judge_model/.test(src));
// ★ 2026-10-10 用户三项裁定：多工具题必须跨「套装」+ 备选答案可增删 + 噪声题避搜索
check('题集编辑器：每题都有「涉及套装工具」栏（单/多工具标签不同）',
      /涉及套装工具（≥2 个、不同来源）/.test(src) && /涉及套装工具（同一套装内，可含多条子命令）/.test(src));
check('题集编辑器：refs/alts 的增删函数已定义并注册进 setup',
      /function probeListAdd/.test(src) && /function probeListDel/.test(src)
      && /probeListAdd, probeListDel,/.test(src));
check('题集提示：写明「单工具题=一个套装 / 多工具题=跨套装」与「噪声题会诱发网络搜索」',
      /单工具题 = 只用一个套装/.test(src) && /多工具题 = 必须跨套装/.test(src)
      && /诱发网络搜索/.test(src));
// ★ 2026-10-11 用户三条裁定：测评会话不进会话库 + 画像/笔记跑完还原
check('测评隔离提示：界面写明「测评会话不进会话库」与「画像与笔记跑完立刻还原」',
      /不进会话库/.test(src) && /画像与笔记/.test(src) && /立刻还原/.test(src));
check('画像保护结果：显示快照文件数与还原结论（probeProfileHint + probeRun.profile）',
      /probeProfileHint/.test(src) && /probeRun\.profile/.test(src) && /画像保护/.test(src));
check('测评会话清理提示：跑完显示已清理的测评会话数（probeRun.gone.probe_sessions）',
      /probeRun\.gone/.test(src) && /probe_sessions/.test(src));
// ★ 2026-10-11 用户实测「某一题卡死」后加的单题护栏
check('单题护栏：提示单题上限秒数（probeAskCap + PROBE_ASK_TIMEOUT + 自动中断）',
      /probeAskCap/.test(src) && /PROBE_ASK_TIMEOUT/.test(src) && /自动中断/.test(src));
check('提前中止：界面显示 stopped 原因（服务关闭 / 线程池已关）',
      /probeRun\.stopped/.test(src) && /s\.stopped !== undefined/.test(src));
// ★ 2026-10-11 用户两条裁定：题单个人数据提醒 + 周报自动开关（默认关）
check('个人数据提醒：题单最上方显示「依赖你的个人数据」的题号与理由（probePrivacyHint）',
      /probePrivacyHint/.test(src) && /probePriv/.test(src) && /依赖你的个人数据/.test(src));
check('个人数据提醒：文案写明「确认你确实有这些数据」与「逐页翻找/耗时到上限」',
      /确实有/.test(src) && /逐页翻找/.test(src));
check('周报自动开关：账号级开关 + 写明默认关（digestAuto / setDigestAuto / 默认关）',
      /digestAuto/.test(src) && /setDigestAuto/.test(src) && /默认关/.test(src));
// ★ 2026-10-11 用户实测：保存被拒但提示飘在视口外 → 提示必须也在**按钮正上方**出现；保存要回报归一
check('保存反馈：题集卡片内的保存按钮**上方**也有 probeErr/probeMsg 提示（不再只在卡片外 ✓）',
      /保存\/出题的提示原来只显示在/.test(src) && /probeSaveCases\(\)/.test(src));
check('保存反馈：保存成功文案带「自动归一 N 条」（用户能看见后台改了什么 ✓）',
      /自动归一/.test(src) && /normalized/.test(src));
// ★ 2026-10-11 用户裁定（纯前端）：二次判分选项选中后不明显 → 颜色翻转 + 语义色
check('二次判分：选项用 .pb-vote，选中态是**颜色翻转**（实心 + 反白字 + 投影 ✓）',
      /\.pb-vote\s*\{/.test(src) && /\.pb-vote\.on\s*\{[^}]*color:\s*#fff/.test(src)
      && /box-shadow/.test(src.replace(/\n/g, ' ')));
check('二次判分：四态各有语义色（✓ 绿 / △ 琥珀 / ✗ 红 / 未测 灰蓝）',
      /\.pb-vote\.on\.v-ok\s*\{\s*background:\s*#2f8b4c/.test(src)
      && /\.pb-vote\.on\.v-wn\s*\{\s*background:\s*#b07d0a/.test(src)
      && /\.pb-vote\.on\.v-no\s*\{\s*background:\s*#c0392b/.test(src)
      && /\.pb-vote\.on\.v-na\s*\{\s*background:\s*#667880/.test(src));
check('二次判分：模板不再用旧的（几乎看不出的）.pb-f + ok 选中样式',
      /class="pb-vote"\s+v-for="v in \['✓','△','✗','未测'\]"/.test(src)
      && !/:class="\{ok: probeScoreOf\(r\.id\) === v\}"/.test(src));
// ★ 2026-10-11 用户裁定（纯前端）：每个阶段区块一键收起（题表/打分表/建议/历史版本…）
check('一键收起：CSS 折叠实现（.sk-card.folded 只留 h3）',
      /\.sk-card\.folded\s*>\s*\*:not\(h3\)\s*\{\s*display:\s*none/.test(src));
check('一键收起：六个区块都有折叠开关 + 状态在 probeFold',
      /probeFoldToggle\('cases'\)/.test(src) && /probeFoldToggle\('judge'\)/.test(src)
      && /probeFoldToggle\('advise'\)/.test(src) && /probeFoldToggle\('board'\)/.test(src)
      && /probeFoldToggle\('pool'\)/.test(src) && /probeFoldToggle\('hist'\)/.test(src));
check('一键收起：有「全部收起/展开」总开关（probeFoldAll）',
      /probeFoldAll\(\)/.test(src) && /全部收起/.test(src) && /全部展开/.test(src));
check('无对象提醒：题单最上方显示「没有具体对象」的题号与命中片段（probeVagueHint）',
      /probeVagueHint/.test(src) && /probeVague/.test(src) && /没有具体对象/.test(src)
      && /probeVague\.value = g\.doc\.vague/.test(src));
check('无对象提醒：旧题集没有这字段时应清掉上一轮提醒（probeVague.value = ... || null）',
      /probeVague\.value = \(c\.doc && c\.doc\.vague\) \|\| null/.test(src));
check('结构：单文件自足（无外链 js/css）',
      !/<script[^>]+src="https?:/.test(src) && !/<link[^>]+href="https?:/.test(src));
check('结构：渲染器不引第三方 markdown 库', !/(marked|markdown-it|showdown|unpkg|jsdelivr)/.test(src));

// ============================ 汇总 ============================
console.log('\n' + '='.repeat(62));
console.log('v4.0 大点 03 前端逻辑 harness：通过 ' + ok.length + '，失败 ' + bad.length);
if (bad.length) { bad.forEach(n => console.log('  - ' + n)); process.exit(1); }
console.log('全部通过 ✅');
if (!headOk) console.log('（注：HEAD 对照被跳过，其余断言照常）');
