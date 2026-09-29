# 桌面端看不到前端改动（WebView2 缓存把旧 HTML 喂给了窗口）

> 版本：**v2.5**（小数版 = bug 修补）
> 日期：2026-09-25　范围：`api/server.py`（HTML 入口不缓存）+ `front/desktop/app.py`（加载 URL 加 nonce）

## 问题

同一份前端改动：**网页端刷新后能看到**（报告页操作列多出「阅读」按钮），**桌面端看不到** ——
不是"没刷新"，是重启桌面版也看不到。

### 取证：先排除"代码没生效"

| 证据 | 值 | 说明 |
|---|---|---|
| `front/index.html` 最后写入 | **13:01:46** | 改动确实落盘了 |
| 桌面窗口加载页面 | **13:10:19** | **加载晚于写入 9 分钟** → 不是"改动在加载之后" |
| WebView2 缓存文件 `EBWebView/Default/Cache/Cache_Data/f_000022` | 含 `form-track` **8 处** | 缓存的 HTML **有** 015 表单样式 → 不是"缓存了几周前的老版本" |
| 同一个缓存文件 | `readerHtml` / `dl-text">阅读<` **0 处** | 缓存的 HTML **没有** 阅读按钮 → 正好停在两次改动之间 |
| 后端响应头 | `last-modified` + `etag`，**无 `Cache-Control`** | 没有显式缓存语义 |

结论：**代码生效了，是缓存把旧 HTML 交给了窗口。**

## 根因（两个条件叠加）

1. **后端没有禁缓存**：`@app.get("/")` 返回 `FileResponse(index.html)`，只有 `Last-Modified` / `ETag`。
   按 HTTP 语义，没有 `Cache-Control` 时 Chromium 会启用**启发式新鲜度**（≈ 10% × 距 Last-Modified 的时长），
   在这段时间内**直接拿本地副本、连校验都不做**。
2. **桌面壳刻意保留了持久化缓存**：为了"记住登录"，外壳必须 `private_mode=False` + 固定 `storage_path`
   （TTS/登录态都靠它），代价就是 WebView2 的 **HTTP 缓存跨进程、跨重启保留**。而外壳每次都用**一模一样的 URL**
   （`http://127.0.0.1:8123/?desktop=1`）→ 完全符合"可缓存"的特征。

两者一叠加，用户看到的现象就是：「网页端（你手动 Ctrl+F5 过）是新的，桌面端怎么重启都是旧的」。

## 方案：两道保险

### ① 治本：HTML 入口绝不缓存（`api/server.py`）

```python
@app.middleware("http")
async def _no_store_html_entry(request, call_next):
    resp = await call_next(request)
    if request.method in ("GET", "HEAD"):
        p = request.url.path
        if p == "/" or p.endswith(".html"):
            resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
            resp.headers["Pragma"] = "no-cache"
    return resp
```

**为什么只掐 HTML**：单文件 SPA 的 HTML 是"入口"，它一变整站就变，所以必须每次校验；
而图片/静态 vendor 这类资源改了就是新文件（或新名字），照旧可缓存 —— 用
`tests/test_spa_cache_headers.py` 里的**反向断言**钉住"不要过度应用"。

### ② 保底：桌面壳加载 URL 加一次性 nonce（`front/desktop/app.py`）

```python
url = "%s?desktop=1&_=%d" % (base_url, int(time.time()))   # 每次启动都不同 → 必然缓存未命中
```

为什么还要这一手：`no-store` 只对**之后**拿到的响应用效 —— 已经躺在缓存里的旧条目仍可能被用一次。
nonce 让"旧条目"在构造上就不可能命中，第一条新响应随即写回 `no-store`，从此不再复发。

**可行性已核**：前端判断桌面模式用的是正则 `/[?&]desktop=1/`（容忍额外参数），外壳自检也是
`indexOf('desktop=1')` 子串匹配 —— 加参数不会把"桌面模式"判断弄失效（两处都写成了断言防回退）。

## 结果

| 项 | 结果 |
|---|---|
| 新增回归 `tests/test_spa_cache_headers.py` | **5/5 通过**：`/` 与 `/front/index.html` 都带 `no-store`+`no-cache`+`must-revalidate`（HEAD 也带）；**图片与 JSON API 不带**（反向断言，防过度应用）；`HEAD /` = 405 这个 FastAPI 事实也一并钉住 |
| 真实 uvicorn 实测（非 TestClient） | 起真进程打 `/` → `Cache-Control: no-store, no-cache, must-revalidate, max-age=0` ✅ |
| 桌面壳自检 | 旧断言里写死的 `"?desktop=1"` 字面量（会被 nonce 改动弄坏）已按**意图**改写，并新增 3 项：URL 带 nonce / 外壳自检容忍额外参数 / 前端正则容忍额外参数 |
| 生效条件 | 需要**重启后端**（中间件）+ **重启桌面版**（nonce）；已在运行的旧进程不带新头 |

## ⚠️ 附带发现（未改，待你拍板）

跑桌面壳自检时，有 **3 项 watchdog 断言偶发失败**（"外壳把后端自动拉回来了 / 日志里能看到自动重启 /
外壳进程自己还活着"）。查日志确认**后端是健康的**（`Application startup complete` + 探针 `GET / 200 OK`），
但外壳判成「后端进程启动后立即退出（退出码 1）」→ 直接退出（exit 3）。

- 触发条件：venv 的 `python.exe` 是 launcher（实测 2 个 pid），**它在真服务 bind 端口之前就先退了**；
  而 `wait_ready_monitored` 在"子进程已退 + 端口还没人听"时走**立即失败**分支（本意是"秒级报死因"）。
- 归属：**与本次中间件无关** —— 同参数 A/B 实测（同一份代码，只切换 `Cache-Control` 中间件有无）：**A 臂（含中间件）4 次全部未复现，B 臂（注释掉中间件）4 次也全部未复现**，8 次直接 spawn 都拿到"launcher 存活 + 就绪 7.9~8.6s"。所以它是**低频竞态**（那次失败发生在机器上同时有多个后端进程/测试在跑的时候），不是这次改动引入的。
- 修法（若要做）：给"子进程已退但端口未监听"加一个**短宽限**（例如 15s 继续探端口，仍失败再报死因），
  保留"端口被别人占（日志含 10048）时立刻失败"的现有语义。**涉及 `wait_ready_monitored` 判定口径，不在本次范围内。**

## 复盘思考题

1. 为什么"网页端看得到、桌面端看不到"其实**不是**前端问题？时间线上哪两个数字就把"改动没生效"这个假设排除了？
2. 缓存文件里**有** `form-track`（015 样式）却**没有** `阅读` 按钮，这个组合能推出什么？（提示：它把缓存的时间点夹在哪两次改动之间）
3. `private_mode=False` + 固定 `storage_path` 是"记住登录"的必要条件，代价是什么？如果不用它，还有别的办法保住登录态吗（想一想服务端 `sessions` 表的角色）？
4. 为什么加了 `Cache-Control: no-store` 之后，**还要**给桌面加载 URL 加 nonce？两者分别覆盖对方哪个缺口？
