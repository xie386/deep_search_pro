# 智选情报官 · 桌面版（pywebview 外壳）

双击 **`desktop.cmd`** 即可。独立窗口 + 系统托盘 + 原生对话框 + 系统通知，**后端与现有前端零改动**。

```
front/desktop/
├── desktop.cmd        # 双击启动（★ 内容必须保持纯 ASCII，见下方「编码铁律」）
├── app.py             # 外壳本体：spawn uvicorn + 等待就绪 + 加载页 + 托盘 + 通知 + 退出清理
├── README.md          # 本文档
└── desktop.log        # 运行日志（自动生成，已 gitignore）
```

> ⚠️ **编码铁律：`desktop.cmd` 的字节必须全是 ASCII（连 `rem` 注释里也不能出现中文）。**
> cmd.exe 是按**当前控制台代码页**（中文机上是 GBK）解析批处理文件的：文件里若含 UTF-8 中文
> 字节，解析器会误读并**吃掉后面的字符/把两行并成一行**——实测症状是
> `'local' 不是内部或外部命令`（`setlocal` 被吃）、`'p\' 不是内部或外部命令`、
> 退化成系统 python（报"缺少 pywebview"）、`app.py` 找不到。
> 中文放在 `app.py` 里没问题（Python 按 UTF-8 处理）。文件名也用了 ASCII（`desktop.cmd`）。

## 它做了什么

| 能力 | 说明 |
|---|---|
| **自带后端** | 启动时探测 `127.0.0.1:8123`：**没有服务就自己起一个**（用项目 `.venv`，无窗口），有就直接复用 |
| **同源加载** | 窗口加载的是 **后端自己托管的 `http://127.0.0.1:8123/?desktop=1`**，不是本地文件 |
| **记住登录** | 页面在桌面模式下用 `localStorage`（网页版仍用 `sessionStorage`）→ **关掉再打开不用重新登录**。★ 2026-09-23 起服务端会话也**落库**（`sessions` 表），所以后端重启也不会把登录态弄失效（详见下面「坑 ②-b」） |
| **再次双击 → 唤出已有窗口** | 已有实例在跑时不新开一个，而是**把它的窗口抬到前台**（原来只是"直接退出"，用户看到的是"双击没反应"）|
| **端口自动换** | 起始端口被别的程序占了就**自动往后试**（8123→8127），不再要求你手动 `--port`；已就绪的服务仍按约定复用 |
| **后端崩溃自动重启** | 运行中后端进程意外退出 → 退避重启（2s/5s/15s，最多 3 次）+ 通知 + 自动刷新页面；**复用别人的服务时绝不插手** |
| **托盘常驻** | 右键菜单：显示主窗口 / **立即生成周报** / **打开导出目录** / 当前账号 / 打开日志 / 退出 |
| **托盘状态/进度** | 长任务时托盘提示变「智选情报官 · 正在生成周报…」/「· 正在回答…」，图标右下角出现**琥珀点**——收在托盘也能看到它在干活 |
| **关闭收托盘** | 点窗口 X 默认**收进托盘**而不是退出（托盘不可用时自动放行关闭，不会关不掉）|
| **系统通知** | 周报生成完（窗口不在前台时）弹 Windows 原生通知；页面也可主动调 `pywebview.api.notify()` |
| **应用图标** | 正式设计稿（圆角松绿底 + 白猫头）：**托盘**用 `assets/icon.png`、**任务栏/窗口/`desktop.exe`** 用 `assets/icon.ico`（多尺寸）+ AppUserModelID 纠正归组；换图标跑 `make_icon.py` + `build_launcher.py` |
| **双击入口 `desktop.exe`** | 用系统自带 `csc.exe` 编的小启动器：**文件本身就是我们的图标**（`.cmd` 做不到——图标按文件类型共享）+ **GUI 子系统不弹黑窗**；`desktop.cmd` 保留作排查用后备 |
| **原生「另存为」** | 导出 MD/PDF 走原生保存对话框（能自己选目录与文件名），而不是浏览器闷头下载 |
| **退出清理** | 只关闭**本外壳启动的** uvicorn；用户自己在终端跑的服务绝不碰 |
| **单实例** | 双击两次不会开两个窗口（第二个会把第一个的窗口叫出来，然后自己退出） |

### ★ 必须记住的坑（都实测踩过，且都吃过报障）

**① 窗口必须加载 `http://127.0.0.1:PORT/`，不能加载本地 HTML。** SPA 用 `sessionStorage`/`localStorage` 存登录 token、用 WebSocket 接进度，这些都按**源（origin）**隔离；而后端**没有 CORS 中间件**。若用 `file://` 打开或另起静态端口 → 立刻跨源 → 登录、聊天全废，那就不得不改后端了。同源加载时后端一行不用改。

**② pywebview 默认跑在「隐私模式」，localStorage 不持久化。** `webview.start()` 的 `private_mode` 默认 `True`，文档原话是 *"In private mode, cookies and local storage are not preserved"* —— 只改前端用 localStorage 是**不够**的，必须同时：

```python
webview.start(debug=args.debug, private_mode=False, storage_path=str(ROOT / "data" / "desktop_webview"))
```

实测症状：第 1 轮登录后 localStorage 里确实有 token，**第 2 轮启动就读不到了**（每次进程结束被清空）。这个坑由 `tests/desktop_window_e2e.py` 的两轮对比测试守住。

**②-b 「记住登录」还有后半截：token 必须在服务端**活过重启**（2026-09-23 用户报障修）。** 存储端解决了，服务端还有一半 —— 会话原本是**进程内内存字典**（`api/account.py` 的 `_SESSIONS = {}`，注释还写着"重启即失效，符合纯个人本地定位"）。Web 版你手动起的服务长期不关，看不出问题；**桌面壳每次重开都会新起一个后端进程**，于是：

| 现象（用户截图） | 成因 |
|---|---|
| 打开就是工作台、用户名显示「尼古喵喵」 | localStorage 里的旧 token 非空 → 前端以为已登录 |
| 工作台「我的兴趣/关注品牌商品/收藏商品」**全是 0** | 每个接口都吃 401，而 `loadMyData()` 里是 `if (!r.ok) return;` —— **静默放弃**，页面就留着一堆 0 |
| 天气卡报「未登录或登录已失效」 | 就是 `get_session()` 抛的 401 文案 |
| 手动「退出」再登录 → 一切正常 | 新 token 是当前进程签发的 |

**修法（两侧都补）**：
1. **会话落库**：`tools/schema_personal.py` 新增 `sessions(token PK, account_id, login_at)` 表（90 天有效期，登录时顺手清理过期行）；`api/account.py` 的 `create_session/get_session/logout` 改走 SQLite，用户名/角色/昵称**运行时从 `accounts` 联查**（资料改了立刻生效，不会存成旧快照）。`purge_account()` 自省会认 `account_id` 列，删账号时自动带上这张表（无需改它）。
2. **前端不再"假装已登录"**：启动时先 `verifySession()`（拿 token 调一次 `/api/me/overview`），**401 就 `forceLogout()` 回登录页并提示「登录已失效，请重新登录」**；`api()` 与 `loadMyData()` 也把 401 当失效处理，不再静默吞。注意 `forceLogout()` 只删 `zx_token/zx_user/zx_role` 三个键，**不能用 `storeClear()`**——那会把背景图等偏好一起清掉。

回归测试：`tests/auth_split_e2e.py` 的 [7] 段用**真·新进程**（`subprocess` 里 `from api.account import get_session`）校验旧 token 仍有效，而**不是** reload 模块（reload 语义不可靠，验不出真问题）；`tests/desktop_window_e2e.py` 第 2 轮会断言「新后端进程下用存着的 token 仍取到与第 1 轮相同的数据（不是 0）」——**"进了工作台"从来不算记住登录，"能真读到数据"才算。**

**②-c 持久化缓存的反噬：前端改了，桌面端「连重启都看不到」（2026-09-25 用户报障修）。** 坑 ② 的 `private_mode=False` + 固定 `storage_path` 是把**双刃剑** —— 它保住登录态的同时，也让 WebView2 的 **HTTP 缓存跨进程、跨重启保留**。加上后端原先只发 `Last-Modified`/`ETag`、**没有 `Cache-Control`**，Chromium 就按启发式新鲜度把旧 `index.html` 直接交给了窗口。用户现象：**同一次前端改动，网页端刷新可见（他 Ctrl+F5 过），桌面端重启也看不到**。

怎么证明"不是代码没生效"（排查手法值得复用）：

| 证据 | 值 |
|---|---|
| `front/index.html` 最后写入 | 13:01:46 |
| 桌面窗口加载页面 | **13:10:19（晚于写入 9 分钟）** |
| 缓存文件 `data/desktop_webview/EBWebView/Default/Cache/Cache_Data/f_000022` | 含新样式标记 **8 处**（说明缓存的不算太旧），却 **0 处**新按钮标记 → 正好停在两次改动之间 |

**修法（两道保险）**：
1. **后端**：HTML 入口不缓存 —— `api/server.py` 加中间件，对 `/` 与 `*.html` 打 `Cache-Control: no-store, no-cache, must-revalidate` + `Pragma: no-cache`；**图片/JSON API 不加**（`tests/test_spa_cache_headers.py` 里有反向断言防过度应用）。
2. **外壳**：加载 URL 加一次性 nonce —— `url = "%s?desktop=1&_=%d" % (base_url, int(time.time()))`。`no-store` 只对**之后**的响应用效，已经躺在缓存里的旧条目仍可能被用一次；nonce 让旧条目**必然未命中**。前端判断桌面模式用的是正则 `/[?&]desktop=1/`、外壳自检用 `indexOf('desktop=1')`，两者都能容忍额外参数（`tests/desktop_shell_e2e.py` 已加断言钉住）。

排查提示：桌面端"改动没生效"时，先看 `desktop.log` 的**窗口加载时刻**与文件 mtime 的先后，再 `grep` 一下 `data/desktop_webview/**/Cache_Data/*` 里的新旧标记 —— 能直接判断"缓存的是哪一版"，比反复重启省事。

**③ WebView2 不支持「浏览器式下载」——`<a :href>` 点了等于什么都没发生。** 这是用户报障的核心：周报列表的 `MD↓ / PDF↓` 原本是普通链接，**后端日志里能看到 `GET /api/download?... 200 OK`（请求真发出去了），但文件在「下载」文件夹、桌面、临时目录里都找不到** —— 没有下载 UI、没有落盘，静默丢弃。

所以**凡是让用户拿到文件的入口，一律走原生「另存为」**，前端统一收口到 `saveFromUrl(url, filename)`：

| 前端出口 | 改前 | 改后 |
|---|---|---|
| AI 回答「导出MD / 导出PDF」 | `location.href = 下载URL` | `saveFromUrl()` |
| 周报列表 `MD↓ / PDF↓` | `<a :href>` 普通链接（**点了没反应**） | `saveFromUrl()` |

`saveFromUrl` 的分支逻辑：桌面版 `fetch` 回字节 → base64 → `save_binary_b64()` 弹原生「另存为」（默认目录 = 用户「下载」）；网页版退回 `<a download>` 浏览器下载。**取网络留在前端、只有"写文件"交给外壳**，这样既不多出一个绕过鉴权的请求通道，也不用把 token 搬出页面。

> 排查口诀：**「点了没反应 + 后端日志有 200」= WebView2 把下载吞了**，不是接口问题。

## 用法

```bash
# 直接跑（推荐先这样试）
.venv/Scripts/python.exe front/desktop/app.py

# 换起始端口（被占会自动往后试 8123→8127；也可以指定起始点）
.venv/Scripts/python.exe front/desktop/app.py --port 8200
#  端口自动换的候选个数：--port-attempts 8

# 关掉「后端崩溃自动重启」（默认开启）
.venv/Scripts/python.exe front/desktop/app.py --no-restart

# 只开窗口、不要托盘 / 打开 devtools 排错
.venv/Scripts/python.exe front/desktop/app.py --no-tray --debug

# 点 X 直接退出（默认是收进托盘）
.venv/Scripts/python.exe front/desktop/app.py --no-close-to-tray

# 自检模式：走完整启动流程，采集 DOM/同源/JS API 结果后自动关窗（测试用，也会真开一次窗口）
.venv/Scripts/python.exe front/desktop/app.py --selftest 90 --no-tray --port 8500
#  附加：--selftest-wipe 先清本地存储（清完会**自动刷新**页面）；--selftest-login 用户名:密码 先在页面上真登录
#  附加：--selftest-download 文件名 下载 output/<账号>/<文件名>（验证「点下载→真落盘」整条链路）
#  附加：--selftest-save-dir DIR 让原生「另存为」不弹窗、直接写该目录（否则自动化会卡在无人点的对话框上）
#  ★ 测试必须传 --storage-path <临时目录>：默认数据目录是用户真实桌面版的，
#    用默认值跑自检里的清存储会**把用户自己「记住登录」的状态清掉**
```

双击 **`desktop.exe`**（推荐：有我们的图标、不弹黑窗）或 `desktop.cmd`（排查时能看到完整输出）等同第一条。
**默认直接用你已经开着的服务**，不会重复启动。

启动顺序：**先开窗口显示加载页（不会白屏）→ 起/等后端 → 就绪后自动把窗口切到正式页面**。
首次启动约 20~90 秒（加载模型 + 向量库），控制台每秒打印一个 `.` 表示还在等。

## JS API（现有前端不调用也完全不影响）

| 方法 | 作用 |
|---|---|
| `pywebview.api.info()` | 返回 `{app, desktop: true, backend_managed, pid}`（判断"服务是本外壳起的吗"） |
| `pywebview.api.notify(title, message)` | 系统通知 |
| `pywebview.api.set_status(text)` | 把「正在忙什么」写到**托盘提示**（空串=回到空闲）；前端长任务前后会调（周报生成 / 聊天回答）|
| `pywebview.api.save_text(filename, content)` | 另存为文本/Markdown，返回 `{ok, path}` |
| `pywebview.api.save_binary_b64(filename, b64)` | 另存为**二进制**（导出 PDF 用；前端把字节 base64 后传进来） |
| `pywebview.api.open_file()` | 打开文件对话框，返回 `{ok, path}` |
| `pywebview.api.open_output_dir(username)` | 在资源管理器里打开 `output/<账号>/` |
| `pywebview.api.open_log()` / `open_backend_log()` | 打开外壳日志 / 后端子进程日志 |

⚠️ **有意不提供**「代发 HTTP 请求」这类方法：那会把登录 token 搬到后端之外的通道上，绕过后端自己的鉴权路径。前端该直接 `fetch` 后端（同源），外壳只提供**网络做不到的事**。

### 页面 → 外壳的反向 hook

桌面模式下页面会挂一个 `window.__zxDesktop`（外壳托盘用它触发前端既有逻辑，避免外壳重写一遍业务）：

```js
window.__zxDesktop = {
  version: 1,
  usesLocalStorage: true,                       // 当前用的是 localStorage 还是 sessionStorage
  account: () => ({ username, role, token }),   // 托盘显示"当前账号"
  runDigest: () => desktopRunDigest(),          // 托盘「立即生成周报」（生成全部已启用订阅）
  goto: (v) => go(v),
};
```

前端探测是否运行在桌面壳里：

```js
const IS_DESKTOP = /[?&]desktop=1/.test(location.search);   // ★ 用 URL 判断
```

> 为什么用 URL 而不是 `window.pywebview`：**pywebview 是页面加载后才异步注入的**，在 `setup()` 阶段读它拿不到（时序坑）。URL 查询串在 setup 阶段同步可读。

## 依赖

```bash
uv pip install pywebview pystray pillow
```

- `pywebview`：外壳本体（Windows 上走 WinForms + **Edge WebView2**，Win11 自带运行时）
- `pystray` + `pillow`：托盘与图标（**没装会自动降级为无托盘**，窗口照常可用）
- pythonnet / clr-loader / bottle / proxy-tools 是 pywebview 的传递依赖（已随上条一并安装）

## 排查

| 症状 | 处理 |
|---|---|
| 双击后报 `'local' 不是内部或外部命令` / `'p\' 不是…` | `.cmd` 里混进了非 ASCII 字符（中文注释也算）→ 保持纯 ASCII，见开头「编码铁律」 |
| 双击报"缺少 pywebview" | 说明用了系统 python 而不是项目 venv → 看 `desktop.log` 的「解释器：」那行；`.cmd` 里 `ROOT` 必须退两级（`%HERE%..\..`） |
| **报「后端进程启动后立即退出」+ `UnicodeEncodeError`** | 后端 stdout 是非控制台（文件/管道）时按 **GBK** 编码，而项目 import 阶段会 print 含 emoji 的提示词 → 崩。外壳已强制 `PYTHONUTF8=1`/`PYTHONIOENCODING=utf-8`（2026-09-22 修复）。**若在别处（如计划任务、CI）复现，请自己带上这两个变量** |
| **报「后端进程启动后立即退出」**（其它原因） | 外壳会直接告诉你死因（端口被占 / 缺依赖）并打印 `backend.log` 末尾。要完整现场就看 `front/desktop/backend.log` |
| **报「等待超时」** | 后端进程还活着但太慢/卡住了 → `backend.log` 会停在哪一步。先试 `desktop.cmd --wait 420`（调大等待上限） |
| 双击没反应 | 看 `desktop.log`；`.cmd` 出错时会 `pause` 并显示日志路径与退出码 |
| 窗口长时间停在"正在启动本地后端" | **正常现象**：首次启动要 20~90 秒（import + 模型 + 向量库）。控制台每 10 秒会打印一次「已等待 Ns…（后端进程存活）」，说明它没死 |
| 窗口白屏 | 确认加载的是 `http://127.0.0.1:<port>/`；`--debug` 打开 devtools 看控制台 |
| 托盘图标不出现 | 未装 `pystray`/`pillow` → `uv pip install pystray pillow` |
| **报「后端进程启动后立即退出」但 `backend.log` 里明明写着 `Uvicorn running`** | **venv 的 `python.exe` 是 launcher**：它会再起一个真解释器跑 uvicorn，端口归"孙子进程"，launcher 先退出（码 1）。外壳已按"**服务可不可用**"判定（子进程退出但端口有人在监听 → 继续等），见到这行说明判定逻辑退化了，回看 `wait_ready_monitored` |
| 再次双击 `desktop.cmd` 好像"没反应" | 现在会**把已有窗口唤出来**（单实例锁顺便当 IPC：第二实例发 `SHOW`，第一实例 `show()+restore()`）。若真无反应，看 `desktop.log` 里的「[单实例]」行 |
| 8123 端口被占、又不想被拦 | 现在**自动往后试**（8123→8127）。也可 `--port 8200` 指定起始端口、`--port-attempts 8` 调候选个数。注意：若那个端口上是**能响应 HTTP 的服务**，外壳会按约定**复用它**而不是换端口 |
| 后端跑着跑着崩了 | **自动重启**：连续 3 次探测不通（约 9s）→ 退避 2s/5s/15s 重启，最多 3 次，成功后会弹通知并自动刷新页面。`--no-restart` 可关掉 |
| **想连局域网/手机** | 后端绑的是 `127.0.0.1`，桌面壳同理；要用手机访问需改绑 `0.0.0.0`（**后端改动**）或走 Tailscale 组网 |

### 失败时外壳会给你什么（2026-09-22 加固）

之前的问题是：后端起不来时，**外壳只会傻等 180 秒然后说"请检查 .env"**——因为子进程的输出被丢进了 `DEVNULL`。
现在：

1. 子进程的 stdout/stderr **全部写进 `front/desktop/backend.log`**（起一次覆盖一次）；
2. **监控子进程**：它一退出就立刻（数秒内，不等满超时）报出退出码，并从日志里认出死因——
   `10048` → 「端口被占用」+ 换端口命令；`ModuleNotFoundError` → 「依赖缺失」；
3. 日志尾部会顺手过滤掉那一大坨**提示词转储**（单行几万字符），只留有用信息；
4. 每 10 秒打印一次等待进度，区分「在慢慢启动」和「已经死了」。

## 与 Web 版的关系

```
                     ┌────────────────────────────┐
   浏览器  ──────────▶│  FastAPI（api.server:app） │
                     │  托管 front/index.html     │◀──────── 桌面外壳（同源加载同一页）
   桌面版  ──────────▶│  /api/*   /ws/{thread_id}   │
                     └────────────────────────────┘
```

**同一份后端、同一份前端、同一份数据库**——桌面版只是多了个原生外壳。所以：
- Web 端改了什么，桌面版自动同步（刷新即可）；
- 桌面版不需要"另做一套 UI"，也不会出现两套逻辑漂移。

## 已验证

| 用例 | 覆盖 | 结果 |
|---|---|---|
| `desktop_shell_e2e.py` | 启停后端 / **进程归属（不误杀用户进程）** / 复用别人服务后退出不关它 / 单实例锁 / **第二实例唤起已存窗口** / **端口自动换（含 reuse 与 none 分支）** / **后端崩溃自动重启（判定 + 真机杀掉后自动拉回）** / **托盘状态与忙碌图标** / 托盘降级 / JS API 安全面 / `.cmd` 纯 ASCII / GBK 崩溃回归 | **108/108** |
| `tests/desktop_window_e2e.py` | **真开窗口（两轮，用既有账号 尼古喵喵，账号数前后不变）**：页面渲染、同源地址、`?desktop=1`、登录态落 localStorage、**第 2 轮不登录也直接进工作台**、**「点下载 → 原生另存为 → 真落盘」**（拿账号里已有的真实周报做探针，二进制逐字节比对）、托盘状态链路（页面 `set_status` → 外壳状态）、**真实启动路径确实接上了应用图标**、**★ 两轮都用存着的 token 真读到账号数据（第 2 轮=新后端进程，与第 1 轮一致且不为 0）**、**★ 失效 token 探针（存假 token → 页面自刷 → 必须回登录页 + 提示 + 假 token 被清）** | **46/46** |
| `tests/desktop_export_logic.js` | `saveFromUrl` 两条分支（网页下载 / 桌面另存为）+ base64 字节一致（含 200KB 分块）+ 取消/失败/404 异常路径 | 24 项 |

> 本外壳**不改后端**（无 CORS 需求）、**不改现有前端**（`front/index.html` 零改动），新增文件全部在 `front/desktop/` 内。

## 测试与自检：三处隔离（2026-09-25，代价是用户的一次报障）

自检（`tests/desktop_shell_e2e.py`）会**起临时外壳实例**、还会**杀"端口持有者"**。用户可能正开着桌面版，
所以必须隔离 —— 曾经没隔离，结果是用户实例在自检运行期间收到异常窗口事件，沿"托盘不可用则放行关闭"的兜底
**连自己起的后端一起退出**（日志：`点 X → 收进托盘` / `关闭本次启动的 uvicorn (pid=8884)` / `已退出`）。
三处隔离（缺一不可）：

| # | 隔离对象 | 怎么做 |
|---|---|---|
| 1 | **WebView2 用户目录** | 临时实例必须带 `--storage-path <临时目录>`；**默认 `data/desktop_webview` 属于用户实例**，两边抢同一个 user-data-dir 会让用户窗口收到异常事件 |
| 2 | **日志文件** | 外壳日志目录支持环境变量覆盖：`ZX_DESKTOP_LOGDIR=<临时目录>`（默认 `front/desktop/`）。自检在 `load_shell()` **之前**设好，临时实例通过 env 继承（`desktop.log` 与 `backend.log` 都会跟着走） |
| 3 | **"用户正在用"的探测** | 自检 `[0]` 段先探 **默认端口 8123 / 锁端口 9123** 是否被占 → 在跑就**跳过会开窗口的段落**（`[16]` 真机重启，7 项）并打印"要跑完整自检请先把桌面版从托盘退出" |

**为什么 ② 尤其重要**：自检要**按日志解析"本次实例的后端 pid"**（`_backend_pid()` 取最后一条
`uvicorn 已启动 (pid=N)`）。共享日志时它可能读到**用户的** pid，紧接着的 `taskkill /F /T` 就会去杀用户的进程。
隔离后各写各的，这个入口就不存在了。

**实测**：带隔离跑自检 = **107 通过 / 0 失败**（跳过 7 项）；用户实例自检前后 `8123=PID 20256 / 9123=PID 30192`
**完全不变**。无用户实例时的完整项数是 **114**。

## 应用图标（已接入设计稿）

图标不再是现画的绿点，而是**正式设计稿**：圆角方形松绿底 + 白色猫头（右下角一个橙点）。
文件都在 `front/desktop/assets/`：

| 文件 | 用途 |
|---|---|
| `icon-master.svg` | **矢量母版**（以后要改从它改） |
| `icon.png` (512) | 处理后的主图（去白底、透明外圈）→ **托盘用它** |
| `icon.ico` | 含 **16/24/32/48/64/128/256** 七档 → **任务栏/窗口图标 + `desktop.exe` 的文件图标** |
| `icon-meta.json` | 记下设计稿里**橙点的位置/半径** → 「忙碌指示灯」叠在同一个点上，不会变成两个点 |

**要换图标**（拿了新设计稿之后）：

```bash
.venv/Scripts/python.exe front/desktop/make_icon.py <新设计稿.png>   # 去白底 + 生成多尺寸 ico
.venv/Scripts/python.exe front/desktop/build_launcher.py            # ★ 重编 desktop.exe（图标编在 exe 里，必须重编）
```

`make_icon.py` 做两件"设计稿给不了、但壳必须要有"的事：① **去掉外围白底**（AI 出图多半是"圆角方块画在白底上"，直接贴到深色任务栏会露白方块；它只把**与外边界相连**的白变透明，猫脸那圈白不会被误伤）；② 生成**多尺寸 `.ico`**（`System.Drawing.Icon` 不认 PNG，必须真 .ico）。

> ⚠️ **试过但否掉的方案**：为了 16~24px 更清楚，我曾把图形裁边放大 8% 单独喂给小尺寸。实测**会把圆角切出缺口**（32/48px 下图标变成"削角方块"，比可读性那点收益亏得多），所以默认关闭（`make_icon.py` 的 `SMALL_CROP=0`，留了开关，0.03~0.05 还算安全）。
>
> 实测观感：**64px 以上猫脸很清楚**；32px 能认出；24px 是"白脸 + 耳朵"的轮廓；**16px 只剩绿方块上一个白点**（任何图标在这个尺寸都保不住细节，作为"一眼认出是我们的图标"够用）。

### 托盘 / 任务栏 / 文件图标分别是怎么换上的

- **托盘**（pystray）：`_make_icon_image()` 读 `assets/icon.png`（没有就现画绿点）。运行时也能换：`pystray` 的 `icon.icon`（图）与 `icon.title`（悬停文字）**都有 setter**，忙碌状态的琥珀点就是这么叠上去的。
- **窗口 / 任务栏**（pywebview）：`webview.start(icon=str(assets/icon.ico))`。**关键发现**：pywebview 文档写 `icon=` *仅 GTK/QT 支持*，但 **Windows 的 WinForms 后端其实实现了**（`webview/platforms/winforms.py`）——不传它就从 `sys.executable` 抠图标，**这正是"任务栏显示 Python 图标"的原因**。
- **任务栏归组/名称**：`ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("xieky.zxintel.desktop")`（创建窗口前设）。不设的话壳会被当成 `python.exe` 归组，图标与名称都跟着 Python 走。
- **`desktop.exe`（双击入口）的文件图标**：编译时用 `/win32icon:assets/icon.ico` 把图标**编进 PE 资源**（见下）。

### 双击入口：用 `desktop.exe`（推荐），`desktop.cmd` 留作后备

**为什么不能直接给 `desktop.cmd` 换个图标？** 因为 Windows 给 `.cmd` 用的图标是**按文件类型共享**的：

```
HKLM\SOFTWARE\Classes\.cmd       → (Default) = cmdfile
HKLM\SOFTWARE\Classes\cmdfile\DefaultIcon → %SystemRoot%\System32\imageres.dll,-68
```

改它 = **全系统所有 .cmd 一起变**（不是某一支），而 Windows **没有"单个 .cmd 自带图标"的机制**（没有 per-file icon 存储）。此外双击 `.cmd` 必然弹一个控制台黑窗。

所以做法是**编一个属于自己的启动器 exe**（源码 `launcher/desktop.cs`，编译器用**系统自带**的 .NET `csc.exe`，无需额外安装）：

```bash
.venv/Scripts/python.exe front/desktop/build_launcher.py     # 产出 front/desktop/desktop.exe（~34KB）
```

- `/target:winexe` → **GUI 子系统，双击不弹黑窗**；
- `/win32icon:assets/icon.ico` → **文件本身就是我们的图标**；
- 它只做一件事：定位 `.venv\Scripts\python.exe`，以项目根为工作目录启动 `front/desktop/app.py`，并注入与 `.cmd` 相同的环境（强制 UTF-8、清代理/PYTHONPATH），参数可透传（`desktop.exe --port 8200`）；
- 启动器自己**立刻退出**，app 作为独立进程继续跑（窗口 + 托盘）；
- 无控制台时 stderr 可能为 None → `app.py` 的 `_install_crash_logger()` 会把未捕获异常写进 `desktop.log`，不会出现"双击没反应又没线索"。

> 没有控制台也意味着：**不会再有"不小心点到黑窗的 X 把整个应用关掉"**这种情况（`.cmd` 时代这个问题真实发生过）。
> `desktop.cmd` 仍然保留：排查启动问题时它**能看到完整输出**（`pause` 住错误）。

## 待办（还没做的）

按价值和可行性排序，均**未实现**（做到哪条就把它从本表移到上面的「它做了什么」）：

| # | 待办 | 现状 | 做法要点 / 坦率评估 |
|---|---|---|---|
| 1 | **窗口位置/尺寸记忆** | 每次都是 1280×820 居中 | `x/y/width/height` 是**只读**属性（只能 `create_window` 时传入）→ **关闭时读出来存盘**，下次启动传入。⚠️ 必须做**可见性兜底**：拔掉外接显示器后旧坐标可能落在屏幕外 → 检测是否在任一显示器内，不在就回落主屏居中；顺带处理 125%/150% DPI 缩放 |
| 2 | **开机自启 + tray-only 预热** | 无 | 写 `HKCU\...\Run`（或用 `schtasks /sc onlogon`），托盘菜单给一键开关 + `--no-autostart` 撤销。⚠️ **`webview.start()` 没有 `hidden` 参数**，所以"藏窗口预热"只能做成**无窗口进程**：只跑「托盘 + 后端预热」，点「显示主窗口」再开窗（后端已就绪 → 秒开，省掉 7~9 秒冷启动）。会常驻一个 Python 进程，占内存 |
| 3 | **拖文件进窗口入库** | 只能点按钮选文件 | 前端加 drop 区（`dragover/drop` + `preventDefault`）复用现有上传接口，**零后端改动**。⚠️ WebView2 默认"拖文件 = 导航到该文件"，会把当前页面顶掉 → 必须在壳里拦掉这个导航 |
| 4 | **全局热键唤起/隐藏窗口** | 无 | ctypes `RegisterHotKey` + 消息泵线程即可（**零新依赖**），或 `pynput`。默认**不占用**任何热键，在托盘菜单里配 + 冲突检测 |
| 5 | **打包分发（安装包/exe）** | 只能从源码 `desktop.cmd` 启动 | ⚠️ **PyInstaller 不适合这个项目**：`.venv` 实测 **5.2 GB**（torch + chromadb + bge + 模型缓存），冻结产物体积/构建时间都会失控，且 HF 模型缓存路径冻结后易失效。建议 **A. Inno Setup 安装包/绿色压缩包**（项目目录 + `.venv` + 快捷方式 + 可选文件关联 `.md`）——代价是安装包 5 GB 级，需要先接受体积；或 **B. 轻量引导式启动器**（首次运行建 venv 装依赖，之后复用） |
| 6 | **划词/截图提问** | 无 | ⚠️ **当前前提不成立**：全项目 `image_url` 出现 0 次，链路是**纯文本**，没有多模态入口。要做必须先接一个支持视觉的模型 provider + 后端支持图片消息——**属 v3.0 级别、且要改后端**，超出桌面壳范围。折中版（热键截图 → 存盘+剪贴板，人工上传）价值有限，不建议先做 |
| 7 | ~~控制台黑窗的图标~~ | **已解决**：改用 `desktop.exe`（GUI 子系统 → 没有黑窗，文件本身就是我们的图标）。见「双击入口」一节 |
| 8 | **桌面/开始菜单快捷方式** | 无 | 想让"从桌面点开"更顺：做个 `.lnk`（图标 `assets/icon.ico`、起始位置=项目根）。属于"动用户环境"，做之前先确认 |
