# -*- coding: utf-8 -*-
"""CDP 真机探针：登录 / 注册两卡分离的完整交互验证。

为什么必须真机跑：登录/注册是「输入 → 校验 → 请求 → 反馈」的交互链路，静态断言只能证明
代码在、证明不了点下去对不对（本次旁边的技能菜单事故就是"代码在但没接上"）。本脚本用真实
浏览器：真实输入、真实点击，断言 DOM 反馈与后端副作用（账号是否真的建出来了）。

验证项：
  ① 初始是登录卡（有 tab，无确认密码字段）
  ② 切到注册卡 → 出现确认密码 + 账号类型 + 「即将创建」回显
  ③ 回显随用户名/类型实时更新（专治错别字：把将创建的账号名摆给用户看）
  ④ 两次密码不一致 → 点「创建账号」拦下、**后端没建号**（查库断言）
  ⑤ 合法注册 → 建号成功、回登录卡、密码框清空、出现提示
  ⑥ 用刚注册的账号在 UI 上登录 → 进入工作台（有导航栏）
  ⑦ 打错用户名登录 → 提示「账号不存在」+ 出现「去注册」引导，**且不会建号**
  ⑥/⑦ 都通过后清理测试账号。

用法：
    # 先起服务与无头 Chrome（--no-proxy-server 必须）：
    #   .venv/Scripts/python.exe -m uvicorn api.server:app --host 127.0.0.1 --port 8123
    #   chrome --headless=new --no-proxy-server --remote-debugging-port=9333 --user-data-dir=<tmp>
    .venv/Scripts/python.exe scripts/cdp_auth_probe.py
"""

import asyncio
import json
import os
import sys
import time
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import websockets  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CDP_PORT = int(os.environ.get("CDP_PORT", "9333"))
APP = os.environ.get("APP_URL", "http://127.0.0.1:8123")
_NO_PROXY = urllib.request.build_opener(urllib.request.ProxyHandler({}))

SUF = time.strftime("%m%d%H%M%S")
NEW_USER = "authprobe_%s" % SUF
TYPO_USER = "尼姑喵喵_%s" % SUF
PWD = "probe123456"

PASS, FAIL = [], []


def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name)
    print("%s %s%s" % ("  ✅" if cond else "  ❌", name, (" | " + str(extra)) if extra else ""))


def _account_names():
    from tools.schema_personal import get_personal_conn
    conn = get_personal_conn()
    try:
        return {r["username"] for r in conn.execute("SELECT username FROM accounts")}
    finally:
        conn.close()


async def cdp(ws, method, params=None, session=None, _id=[0]):
    _id[0] += 1
    msg = {"id": _id[0], "method": method, "params": params or {}}
    if session:
        msg["sessionId"] = session
    await ws.send(json.dumps(msg))
    while True:
        raw = json.loads(await ws.recv())
        if raw.get("id") == _id[0]:
            if "error" in raw:
                raise RuntimeError("%s -> %s" % (method, raw["error"]))
            return raw.get("result")


async def ev(ws, sid, expr, await_promise=False):
    r = await cdp(ws, "Runtime.evaluate",
                  {"expression": expr, "returnByValue": True, "awaitPromise": await_promise},
                  session=sid)
    if r.get("exceptionDetails"):
        return {"_jsError": str(r["exceptionDetails"])[:200]}
    return (r.get("result") or {}).get("value")


def _set_input_js(sel, val):
    """把值写进输入框并派发 input 事件（Vue v-model 是 input 事件驱动）。"""
    return """(() => {
      const el = document.querySelector(%s);
      if (!el) return 'no-element';
      el.value = %s;
      el.dispatchEvent(new Event('input', {bubbles: true}));
      return el.value;
    })()""" % (json.dumps(sel), json.dumps(val))


STATE_JS = """(() => {
  const card = document.querySelector('.login-card');
  const form = document.querySelector('.login-form');
  const fields = [...document.querySelectorAll('.login-form .field > label')].map(l => l.textContent.trim());
  const tabs = [...document.querySelectorAll('.auth-tab')].map(b => b.textContent.trim() + (b.classList.contains('on') ? '(选中)' : ''));
  const hint = document.querySelector('.reg-hint');
  const err = document.querySelector('.err');
  const sw = document.querySelector('.auth-switch');
  const nav = document.querySelectorAll('.nav-tab').length;
      return {
    cardExpandedH: card ? getComputedStyle(card).height : null,
    tabs, fields,
    hint: hint ? hint.textContent.trim() : null,
    hintReady: hint ? hint.classList.contains('ready') : null,
    err: err ? err.textContent.trim() : '',
    switchText: sw ? sw.textContent.trim() : '',
    pwdType: document.querySelector('input[type=password]') ? 'password' : 'none',
    navTabs: nav,
  };
})()"""


async def click(ws, sid, sel):
    box = await ev(ws, sid, """(() => {
      const el = document.querySelector(%s);
      if (!el) return null;
      const r = el.getBoundingClientRect();
      return {x: Math.round(r.left + r.width / 2), y: Math.round(r.top + r.height / 2)};
    })()""" % json.dumps(sel))
    if not box:
        return False
    for t in ("mousePressed", "mouseReleased"):
        await cdp(ws, "Input.dispatchMouseEvent",
                  {"type": t, "x": box["x"], "y": box["y"], "button": "left", "clickCount": 1},
                  session=sid)
    await asyncio.sleep(0.4)
    return True


async def main():
    ver = json.load(_NO_PROXY.open("http://127.0.0.1:%d/json/version" % CDP_PORT, timeout=10))
    print("CDP:", ver["Browser"])
    before = _account_names()
    print("测试账号：新建=%s | 错别字探针=%s\n" % (NEW_USER, TYPO_USER))

    async with websockets.connect(ver["webSocketDebuggerUrl"], max_size=32 * 1024 * 1024,
                                  proxy=None) as ws:
        t = await cdp(ws, "Target.createTarget", {"url": "about:blank"})
        sid = (await cdp(ws, "Target.attachToTarget",
                         {"targetId": t["targetId"], "flatten": True}))["sessionId"]
        for m in ("Page.enable", "Runtime.enable", "Log.enable"):
            await cdp(ws, m, {}, session=sid)
        await cdp(ws, "Page.navigate", {"url": APP}, session=sid)
        await asyncio.sleep(3)
        await ev(ws, sid, "sessionStorage.clear()")
        await cdp(ws, "Page.navigate", {"url": APP}, session=sid)
        await asyncio.sleep(3)
        # 卡片是 hover 展开的：先把鼠标移到卡片上，否则表单是透明且不可点的
        await ev(ws, sid, "const c=document.querySelector('.login-card'); if(c) c.dispatchEvent(new MouseEvent('mouseover',{bubbles:true})); 'ok'")
        await cdp(ws, "Input.dispatchMouseEvent", {"type": "mouseMoved", "x": 300, "y": 300},
                  session=sid)
        await asyncio.sleep(0.8)

        print("[1] 初始状态：登录卡")
        st = await ev(ws, sid, STATE_JS)
        print("   ", json.dumps(st, ensure_ascii=False))
        check("有「登录/注册」两个 tab", len(st.get("tabs") or []) == 2, st.get("tabs"))
        check("初始选中「登录」", any("登录(选中)" == x for x in (st.get("tabs") or [])), st.get("tabs"))
        check("登录卡只有用户名+密码（无确认密码）",
              "用户名" in st["fields"] and "密码" in st["fields"] and "确认密码" not in st["fields"],
              st["fields"])

        print("\n[2] 切到注册卡")
        await ev(ws, sid, """(() => { const b=[...document.querySelectorAll('.auth-tab')].find(x=>x.textContent.trim()==='注册'); b.click(); return 'ok'; })()""")
        await asyncio.sleep(0.5)
        st = await ev(ws, sid, STATE_JS)
        print("   ", json.dumps(st, ensure_ascii=False))
        check("注册卡出现「确认密码」字段", "确认密码" in st["fields"], st["fields"])
        check("注册卡有「账号类型」字段", "账号类型" in st["fields"], st["fields"])
        check("注册卡有「即将创建」回显", bool(st.get("hint")), st.get("hint"))

        print("\n[3] 回显随用户名实时更新（错别字防护）")
        await ev(ws, sid, _set_input_js(".login-form input[placeholder*='账号名']", TYPO_USER))
        await asyncio.sleep(0.4)
        st = await ev(ws, sid, STATE_JS)
        check("回显里出现「即将创建：<用户名>」", ("即将创建：" + TYPO_USER) in (st.get("hint") or ""),
              st.get("hint"))
        check("回显带上账号类型", "（个人账号）" in (st.get("hint") or ""), st.get("hint"))

        print("\n[4] 两次密码不一致 → 拦下且不建号")
        await ev(ws, sid, _set_input_js(".login-form input[placeholder='至少 6 位']", PWD))
        await ev(ws, sid, _set_input_js(".login-form input[placeholder='再输一次']", "mismatch9999"))
        await asyncio.sleep(0.3)
        await ev(ws, sid, """(() => { const b=document.querySelector('.login-form button.bubbles'); if(!b) return 'no-submit'; b.click(); return 'submit:'+b.textContent.trim(); })()""")
        await asyncio.sleep(1.0)
        st = await ev(ws, sid, STATE_JS)
        check("提示「两次输入的密码不一致」", "不一致" in st["err"], st["err"])
        check("★ 不一致时后端没建号", TYPO_USER not in _account_names())

        print("\n[5] 合法注册 → 建号 + 回登录卡 + 清空密码")
        await ev(ws, sid, _set_input_js(".login-form input[placeholder*='账号名']", NEW_USER))
        await ev(ws, sid, _set_input_js(".login-form input[placeholder='至少 6 位']", PWD))
        await ev(ws, sid, _set_input_js(".login-form input[placeholder='再输一次']", PWD))
        await asyncio.sleep(0.3)
        clicked = await ev(ws, sid, """(() => { const b=document.querySelector('.login-form button.bubbles'); if(!b) return 'no-submit'; b.click(); return 'submit:'+b.textContent.trim(); })()""")
        print("    点击了:", clicked)
        await asyncio.sleep(1.5)
        st = await ev(ws, sid, STATE_JS)
        print("   ", json.dumps(st, ensure_ascii=False)[:300])
        check("★ 账号真的建出来了（查库）", NEW_USER in _account_names())
        check("注册后自动切回登录卡", "确认密码" not in st["fields"], st["fields"])
        check("注册后密码框已清空",
              (await ev(ws, sid, "document.querySelector(\".login-form input[placeholder='密码']\").value")) == "")
        toast_txt = await ev(ws, sid, "(document.querySelector('.toast')||{}).textContent || ''")
        check("有「账号已创建」提示（toast）", "已创建" in (toast_txt or ""), toast_txt)
        btn_txt = await ev(ws, sid, "(document.querySelector('.login-form button.bubbles')||{}).textContent || ''")
        check("提交按钮文案回到「登录」（说明已切回登录卡）", "登录" in (btn_txt or ""), btn_txt)

        print("\n[6] 用新账号在 UI 上登录")
        await ev(ws, sid, _set_input_js(".login-form input[placeholder*='如 alice']", NEW_USER))
        await ev(ws, sid, _set_input_js(".login-form input[placeholder='密码']", PWD))
        await asyncio.sleep(0.3)
        await ev(ws, sid, """(() => { const b=document.querySelector('.login-form button.bubbles'); if(!b) return 'no-submit'; b.click(); return 'submit:'+b.textContent.trim(); })()""")
        await asyncio.sleep(2.5)
        st = await ev(ws, sid, STATE_JS)
        check("★ 登录成功：进入工作台（出现导航栏）", (st.get("navTabs") or 0) > 0, st.get("navTabs"))
        tok = await ev(ws, sid, "sessionStorage.getItem('zx_token') || ''")
        check("登录态已写入 sessionStorage", bool(tok))

        print("\n[7] 登出后用错别字用户名登录 → 404 提示 + 去注册引导，且不建号")
        await ev(ws, sid, "sessionStorage.clear()")
        await cdp(ws, "Page.navigate", {"url": APP}, session=sid)
        await asyncio.sleep(3)
        await cdp(ws, "Input.dispatchMouseEvent", {"type": "mouseMoved", "x": 300, "y": 300},
                  session=sid)
        await asyncio.sleep(0.8)
        await ev(ws, sid, _set_input_js(".login-form input[placeholder*='如 alice']", TYPO_USER))
        await ev(ws, sid, _set_input_js(".login-form input[placeholder='密码']", PWD))
        await asyncio.sleep(0.3)
        clicked = await ev(ws, sid, """(() => { const b=document.querySelector('.login-form button.bubbles'); if(!b) return 'no-submit'; b.click(); return 'submit:'+b.textContent.trim(); })()""")
        print("    点击了:", clicked)
        await asyncio.sleep(1.5)
        st = await ev(ws, sid, STATE_JS)
        print("   ", json.dumps(st, ensure_ascii=False)[:300])
        check("提示「账号不存在」", "不存在" in st["err"], st["err"])
        check("出现「去注册」引导链接", "去注册" in (st.get("switchText") or "") or "注册" in (st.get("switchText") or ""),
              st.get("switchText"))
        check("★ 登录失败不会建号（旧版会静默建号）", TYPO_USER not in _account_names())

    # 清理
    print("\n[8] 清理测试账号")
    from tools.schema_personal import purge_account, get_personal_conn
    conn = get_personal_conn()
    try:
        ids = [r["id"] for r in conn.execute("SELECT id FROM accounts WHERE username IN (?,?)",
                                             (NEW_USER, TYPO_USER))]
    finally:
        conn.close()
    for aid in ids:
        purge_account(aid)
    check("临时账号已清理", not (_account_names() & {NEW_USER, TYPO_USER}),
          sorted(_account_names() & {NEW_USER, TYPO_USER}))

    print("\n" + "=" * 60)
    print("登录/注册两卡分离（真机 CDP）：通过 %d，失败 %d" % (len(PASS), len(FAIL)))
    if FAIL:
        print("失败项：")
        for f in FAIL:
            print("  -", f)
        return 1
    print("全部通过 ✅")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
