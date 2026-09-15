# -*- coding: utf-8 -*-
"""CDP 真浏览器探针：复现「聊天框敲 / 唤出技能菜单」并给出可见性诊断。

背景：项目此前的浏览器验证靠「无头 Chrome 截图 + 看图」（`scripts/build_wx_harness.py`），
因为 `agent-browser` CLI 不可用。其实**原生 CDP 是可用的**（Chrome --headless=new +
--remote-debugging-port，用 `websockets` 直连 DevTools 协议），本脚本就是这条链路的落地：
能在真实页面里跑 JS、派发真实按键事件、读计算样式与命中测试（elementFromPoint）。

它回答的问题（静态读代码答不了的）：
  菜单到底有没有渲染？渲染了但**被裁切/被遮挡/在视口外**（=用户看到「没反应」）？
  还是数据为空（技能列表拉不到）？以及页面有没有 JS 异常把逻辑打断？

用法：
    # 1) 先起 Chrome（后台）：
    #    chrome --headless=new --disable-gpu --remote-debugging-port=9333 --user-data-dir=<tmp>
    # 2) 再跑本脚本：
    .venv/Scripts/python.exe scripts/cdp_skill_menu_probe.py
    .venv/Scripts/python.exe scripts/cdp_skill_menu_probe.py --user 尼古喵喵   # 用真实账号（需密码）
"""

import argparse
import asyncio
import json
import os
import shutil
import sys
import time
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import websockets  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CDP_PORT = int(os.environ.get("CDP_PORT", "9333"))
APP = os.environ.get("APP_URL", "http://127.0.0.1:8123")

# 系统代理（127.0.0.1:7897）会拦 localhost，直连必须绕开——本项目踩过多次（实测 502 Bad Gateway）
_NO_PROXY = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def _get_json(url):
    with _NO_PROXY.open(url, timeout=10) as r:
        return json.load(r)

TMP_USER = "cdpprobe_%s" % time.strftime("%m%d%H%M%S")
TMP_PWD = "cdpprobe123"

_events: list[dict] = []          # 控制台/异常事件


async def cdp_call(ws, method, params=None, session=None, _id=[0]):
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
        _collect(raw)


def _collect(raw):
    m = raw.get("method") or ""
    if m in ("Runtime.consoleAPICalled", "Runtime.exceptionThrown", "Log.entryAdded"):
        _events.append(raw)


async def evaluate(ws, sid, expr, await_promise=False):
    r = await cdp_call(ws, "Runtime.evaluate", {
        "expression": expr, "returnByValue": True, "awaitPromise": await_promise,
    }, session=sid)
    if r.get("exceptionDetails"):
        return {"_jsError": r["exceptionDetails"].get("text") or str(r["exceptionDetails"])[:200]}
    return (r.get("result") or {}).get("value")


DIAG_JS = r"""
(() => {
  const ta = document.querySelector('.input-area textarea');
  const menu = document.querySelector('.skill-menu');
  const app = document.querySelector('#app');
  // Vue 根：扫描带 __vue_app__ 的元素（in-DOM 模板下根元素可能已被组件元素替换）
  let vz = null, vzTag = null;
  for (const el of document.querySelectorAll('body *')) {
    if (el.__vue_app__) { vz = el.__vue_app__; vzTag = el.id || el.className || el.tagName; break; }
  }
  const st = vz && vz._instance ? vz._instance.setupState : null;
  const out = {
    hash: location.hash,
    textareaCount: document.querySelectorAll('.input-area textarea').length,
    textareaFound: !!ta,
    draft: ta ? ta.value : null,
    // Vue 是否把 @keydown / @input 绑上了（Vue3 把 invoker 挂在元素的 _vei 上）
    vueBoundEvents: ta && ta._vei ? Object.keys(ta._vei) : null,
    vueRootEl: vzTag,
    mustachesLeft: (document.body.innerText.match(/\{\{/g) || []).length,   // >0 = Vue 没接手渲染
    state: st ? {
      skMenuOpen: !!st.skMenuOpen,
      skFilter: st.skFilter,
      skillsLen: (st.skills || []).length,
      skills: (st.skills || []).map(s => s.name).slice(0, 5),
      view: st.view,
      tokenSet: !!st.token, usernameSet: !!st.username,
    } : null,
    menuExists: !!menu,
  };
  if (!menu) return out;
  const cs = getComputedStyle(menu);
  const r = menu.getBoundingClientRect();
  out.menu = {
    display: cs.display, visibility: cs.visibility, opacity: cs.opacity,
    zIndex: cs.zIndex, position: cs.position,
    rect: {x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width), h: Math.round(r.height)},
    inViewport: r.bottom > 0 && r.top < innerHeight && r.right > 0 && r.left < innerWidth,
    text: (menu.innerText || '').slice(0, 200),
    itemCount: menu.querySelectorAll('.mi').length,
  };
  // 命中测试：菜单中心点上真正被点到的元素是谁（能抓出「被别的元素盖住 / 被裁掉」）
  const cx = r.left + r.width / 2, cy = r.top + Math.min(r.height / 2, 20);
  const hit = (cx > 0 && cy > 0 && cx < innerWidth && cy < innerHeight)
      ? document.elementFromPoint(cx, cy) : null;
  out.menu.hitElement = hit ? (hit.className || hit.tagName) : null;
  out.menu.coveredByOther = !!hit && !menu.contains(hit) && hit !== menu;
  // 祖先里谁在裁切
  out.clippers = [];
  for (let el = menu.parentElement; el && el !== document.body; el = el.parentElement) {
    const s = getComputedStyle(el);
    if (s.overflow !== 'visible' || s.overflowY !== 'visible') {
      const er = el.getBoundingClientRect();
      out.clippers.push({cls: el.className, overflow: s.overflow + '/' + s.overflowY,
        top: Math.round(er.top), bottom: Math.round(er.bottom),
        clipsMenu: er.top > r.top || er.bottom < r.bottom});
    }
  }
  return out;
})()
"""


async def probe(sid, ws, user, pwd, expect_skills=None):
    print("\n[1] 打开应用并登录 %s" % user)
    nav = await cdp_call(ws, "Page.navigate", {"url": APP}, session=sid)
    print("    Page.navigate ->", nav)
    await asyncio.sleep(2.5)
    where = await evaluate(ws, sid, "location.href + ' | ' + document.title + ' | ' + document.readyState")
    print("    当前页面 ->", where)
    login_js = """
    (async () => {
      const r = await fetch('/api/login', {method:'POST',
        headers:{'Content-Type':'application/json'},
        body: JSON.stringify({username:%s, password:%s})});
      const d = await r.json();
      if (!r.ok) return 'LOGIN_FAIL ' + JSON.stringify(d).slice(0,120);
      sessionStorage.setItem('zx_token', d.token);
      sessionStorage.setItem('zx_user', d.account.username);
      sessionStorage.setItem('zx_role', d.account.role);
      return 'OK';
    })()
    """ % (json.dumps(user), json.dumps(pwd))
    print("    login:", await evaluate(ws, sid, login_js, await_promise=True))
    await cdp_call(ws, "Page.navigate", {"url": APP}, session=sid)
    await asyncio.sleep(3.0)

    print("[2] 切到聊天视图")
    await evaluate(ws, sid, "location.hash = '#/chat'")
    await asyncio.sleep(1.5)

    print("[3] 取技能列表（前端拿到的同一接口）")
    sk = await evaluate(ws, sid, """
      (async () => {
        const r = await fetch('/api/skills?token=' + encodeURIComponent(sessionStorage.getItem('zx_token')));
        const d = await r.json().catch(() => null);
        return JSON.stringify({status: r.status, skills: d && d.skills ? d.skills.map(s => s.name) : d});
      })()
    """, await_promise=True)
    print("    /api/skills ->", sk)

    print("[4] 聚焦输入框并派发真实按键 '/'")
    await evaluate(ws, sid, "const t=document.querySelector('.input-area textarea'); t && t.focus(); 'focused'")
    # 事件序列要用 rawKeyDown(不带 text) → char(带 text) → keyUp，
    # 否则 keyDown 与 char 各插一次文字，输入框会变成 "//"（实测踩过）
    for t in ("rawKeyDown", "char", "keyUp"):
        p = {"type": t, "key": "/", "code": "Slash", "windowsVirtualKeyCode": 191,
             "nativeVirtualKeyCode": 191}
        if t == "char":
            p.update({"text": "/", "unmodifiedText": "/"})
        await cdp_call(ws, "Input.dispatchKeyEvent", p, session=sid)
    await asyncio.sleep(0.8)

    print("[5] 诊断（菜单是否存在/可见/被裁切/被遮挡）")
    diag = await evaluate(ws, sid, DIAG_JS)
    print(json.dumps(diag, ensure_ascii=False, indent=2))

    print("[7] 阳性对照 + 页内合成事件（绕开 CDP 输入管线）")
    ctrl = await evaluate(ws, sid, r"""
    (() => {
      const ta = document.querySelector('.input-area textarea');
      const btn = document.querySelector('.send-bubbles');
      const tab = document.querySelector('a.nav-tab');
      const vei = el => el && el._vei ? Object.keys(el._vei) : (el ? 'no-_vei' : 'missing');
      const inst = ta && ta.__vueParentComponent;
      const before = !!document.querySelector('.skill-menu');
      // 页内合成事件：Vue 用 addEventListener 绑定，合成事件能触发；不行就说明真没绑上
      ta.value = '/';
      ta.dispatchEvent(new Event('input', {bubbles: true}));
      ta.dispatchEvent(new KeyboardEvent('keydown', {key: '/', bubbles: true, cancelable: true}));
      const after = !!document.querySelector('.skill-menu');
      const modals = [...document.querySelectorAll('.modal, .modal-mask, .onboard, [class*="modal"]')].map(m => {
        const cs = getComputedStyle(m), r = m.getBoundingClientRect();
        return {cls: m.className, display: cs.display, visibility: cs.visibility,
                pointerEvents: cs.pointerEvents, zIndex: cs.zIndex,
                rect: [Math.round(r.x), Math.round(r.y), Math.round(r.width), Math.round(r.height)]};
      }).filter(m => m.display !== 'none' && m.visibility !== 'hidden');
      const item = document.querySelector('.skill-menu .mi');
      const ir = item && item.getBoundingClientRect();
      const hitItem = ir ? document.elementFromPoint(ir.left + 20, ir.top + ir.height / 2) : null;
      return {
        vei_textarea: vei(ta), vei_sendBtn: vei(btn), vei_navTab: vei(tab),
        textareaHasVueParent: !!inst,
        stateViaParent: inst ? {
          skMenuOpen: !!inst.setupState.skMenuOpen,
          skFilter: inst.setupState.skFilter,
          skillsLen: (inst.setupState.skills || []).length,
          view: inst.setupState.view,
        } : null,
        menuBefore: before, menuAfterSyntheticEvent: after,
        visibleModals: modals,
        menuItemClickable: hitItem ? (item === hitItem || item.contains(hitItem)
                                      ? 'yes' : ('covered by: ' + (hitItem.className || hitItem.tagName))) : 'no-item',
      };
    })()
    """)
    print(json.dumps(ctrl, ensure_ascii=False, indent=2))

    print("[8] 关掉遮挡弹窗（若有）并复验菜单可点 + 真鼠标点击选中技能")
    modal = await evaluate(ws, sid, r"""
    (() => {
      const m = document.querySelector('.modal');
      if (!m || getComputedStyle(m).display === 'none') return {modal: null};
      const txt = (m.innerText || '').replace(/\s+/g, ' ').slice(0, 120);
      const btns = [...m.querySelectorAll('button, .btn, [class*="btn"], a')];
      const names = btns.map(b => b.innerText.trim()).filter(Boolean);
      // 按**文案**挑关闭按钮：不同弹窗的按钮顺序不定（首轮点到「移除」没关掉，实测踩过）
      const target = btns.find(b => /跳过|稍后|完成|开始|知道了|确定/.test(b.innerText));
      const info = {modalText: txt, buttons: names, clicked: target ? target.innerText.trim() : null};
      if (target) target.click();
      return info;
    })()
    """)
    print(json.dumps(modal, ensure_ascii=False, indent=2))
    await asyncio.sleep(0.8)
    still = await evaluate(ws, sid, r"""
      (() => { const m = document.querySelector('.modal');
               return m && getComputedStyle(m).display !== 'none' ? 'still-visible' : 'gone'; })()
    """)
    print("    弹窗状态:", still)
    # 重新唤出菜单后，用真实鼠标事件点第一项
    await evaluate(ws, sid, r"""
      (() => { const t = document.querySelector('.input-area textarea'); t.focus(); t.value = '/';
               t.dispatchEvent(new Event('input', {bubbles: true})); return 'ok'; })()
    """)
    await asyncio.sleep(0.5)
    box = await evaluate(ws, sid, r"""
      (() => { const i = document.querySelector('.skill-menu .mi');
               if (!i) return null; const r = i.getBoundingClientRect();
               return {x: Math.round(r.left + 20), y: Math.round(r.top + r.height / 2),
                       name: (i.innerText || '').split('\n')[0]}; })()
    """)
    print("    菜单首项:", box)
    if box:
        for t in ("mousePressed", "mouseReleased"):
            await cdp_call(ws, "Input.dispatchMouseEvent",
                           {"type": t, "x": box["x"], "y": box["y"], "button": "left",
                            "clickCount": 1}, session=sid)
        await asyncio.sleep(0.6)
    picked = await evaluate(ws, sid, r"""
      (() => ({ pills: [...document.querySelectorAll('.skill-bar .skill-pill')].map(e => e.innerText.trim()),
                menuOpen: !!document.querySelector('.skill-menu'),
                draft: (document.querySelector('.input-area textarea') || {}).value }))()
    """)
    print("    选中结果:", json.dumps(picked, ensure_ascii=False))
    return diag


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--user", default="")
    ap.add_argument("--pwd", default="")
    ap.add_argument("--keep", action="store_true")
    args = ap.parse_args()

    ver = _get_json("http://127.0.0.1:%d/json/version" % CDP_PORT)
    ws_url = ver["webSocketDebuggerUrl"]
    print("CDP:", ver["Browser"])

    temp_created = False
    user = args.user
    if not user:
        from api.account import _create_account, _hash_pwd
        from tools.schema_personal import ensure_tables
        ensure_tables()
        _create_account(TMP_USER, _hash_pwd(TMP_PWD), "personal", None)
        # 复制真实账号的技能文件（题面/内容一致，才谈得上复现）
        src = os.path.join(ROOT, "agents_docs", "尼古喵喵", "skills")
        dst = os.path.join(ROOT, "agents_docs", TMP_USER, "skills")
        os.makedirs(dst, exist_ok=True)
        if os.path.isdir(src):
            for f in os.listdir(src):
                if f.endswith(".md"):
                    shutil.copy2(os.path.join(src, f), os.path.join(dst, f))
        user, pwd = TMP_USER, TMP_PWD
        temp_created = True
        print("临时账号 %s，技能目录已复制：%s" % (user, sorted(os.listdir(dst))))
    else:
        pwd = args.pwd or os.environ.get("APP_PWD", "")
        if not pwd:
            print("⚠️ 指定了 --user 但没有密码，请用 --pwd 或环境变量 APP_PWD 传入（不要写进对话）")
            return 2

    async with websockets.connect(ws_url, max_size=32 * 1024 * 1024, proxy=None) as ws:
        t = await cdp_call(ws, "Target.createTarget", {"url": "about:blank"})
        sid = (await cdp_call(ws, "Target.attachToTarget",
                              {"targetId": t["targetId"], "flatten": True}))["sessionId"]
        for m in ("Page.enable", "Runtime.enable", "Log.enable"):
            await cdp_call(ws, m, {}, session=sid)
        diag = await probe(sid, ws, user, pwd)
        errs = [e for e in _events if e.get("method") in ("Runtime.exceptionThrown", "Log.entryAdded")]
        if errs:
            print("\n[6] 页面异常/错误日志（%d 条）" % len(errs))
            for e in errs[:10]:
                p = e.get("params") or {}
                d = p.get("exceptionDetails") or p.get("entry") or {}
                print("   -", str(d.get("text") or d.get("exception", {}).get("description") or d)[:220])
        else:
            print("\n[6] 页面无异常/错误日志 ✅")

    if temp_created and not args.keep:
        from tools.schema_personal import purge_account, get_personal_conn
        conn = get_personal_conn()
        try:
            row = conn.execute("SELECT id FROM accounts WHERE username=?", (user,)).fetchone()
            aid = row["id"] if row else None
        finally:
            conn.close()
        if aid:
            purge_account(aid)
        shutil.rmtree(os.path.join(ROOT, "agents_docs", user), ignore_errors=True)
        print("\n已清理临时账号 %s" % user)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
