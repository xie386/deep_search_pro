"""M2 WS 链路隔离验证：WS 连 /ws/dbg -> 调 /api/_test_emit?thread_id=dbg，看 WS 是否收到。
用法：python tests/m2_ws_probe.py  （需服务已启动：uvicorn api.server:app --port 8123）
"""
import asyncio, json, os, urllib.request, urllib.error, urllib.parse

os.environ.pop("HTTP_PROXY", None); os.environ.pop("HTTPS_PROXY", None)
os.environ.pop("http_proxy", None); os.environ.pop("https_proxy", None)
os.environ["NO_PROXY"] = "*"; os.environ["no_proxy"] = "*"

BASE = "http://127.0.0.1:8123"
TID = "dbg"
got = []


def http_post(path):
    url = BASE + path
    req = urllib.request.Request(url, data=b"", method="POST")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode())
    except Exception as e:
        return -1, {"detail": str(e)}


async def ws(ready, stop):
    import websockets
    async with websockets.connect(f"ws://127.0.0.1:8123/ws/{TID}", open_timeout=8) as wsock:
        print("[WS] 已连接 /ws/dbg")
        ready.set()
        while not stop.is_set():
            try:
                raw = await asyncio.wait_for(wsock.recv(), timeout=1.0)
            except asyncio.TimeoutError:
                continue
            try:
                p = json.loads(raw)
            except Exception:
                continue
            got.append(p)
            print(f"  [WS recv] type={p.get('type')} event={p.get('event')} msg={p.get('message','')[:50]}")


async def main():
    ready = asyncio.Event(); stop = asyncio.Event()
    ws_task = asyncio.create_task(ws(ready, stop))
    await ready.wait()  # 等 WS 连上
    print("[*] 触发 /api/_test_emit?thread_id=dbg")
    st, resp = await asyncio.to_thread(http_post, f"/api/_test_emit?thread_id={TID}")
    print(f"[*] emit 响应: {st} {resp}")
    await asyncio.sleep(2)  # 等 WS 推送到达
    stop.set()
    ws_task.cancel()
    try:
        await ws_task
    except asyncio.CancelledError:
        pass
    print(f"\n[结果] WS 收到事件数: {len(got)}")
    print("PROBE DONE.")


asyncio.run(main())
