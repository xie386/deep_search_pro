# -*- coding: utf-8 -*-
"""M5-8（阶段 2 预留）· 用户自配价格源：读配置 → 拼 URL → 取值 → 记价，失败绝不静默。

**配置导向（G7）**：本模块**不内置任何厂商**、不出现任何厂商名。用户填四样东西就能接一个源：
  `url_template`（URL 模板，`{sku}` 之类占位）、`param_map`（参数位置）、`value_path`（取值路径）、
  `readonly`（只读闸，C3 恒为 1）。新增源 = 填字段，**不改代码**。

**只读闸（C3）**：价格源只允许读 —— `readonly=0` 的源一律拒绝（不改远端、不下单）。

**失败不静默（G8）**：超时 / HTTP 非 2xx / 非 JSON / 取值路径取不到 / 不是数字 —— 都给出**可读原因**，
并落进 `price_alerts.check_error`（用户能看见"这个源坏了"，而不是"什么都没发生"）。

**阶段边界**：本版**不接任何真实源**（C2/§3.6）。`enabled` 默认 0 —— 没开着的源时，
`check_price()` 明确回"阶段 2 未接源"，绝不假装查过。
"""
import json
import os
import re
import time

from tools.price_ledger import record_price
from tools.schema_personal import get_personal_conn

PLACEHOLDER_RE = re.compile(r"\{(\w+)\}")
MAX_BODY = 200000
ERROR_RULE = "check_failed"          # 落进 price_alerts.rule 的固定值（G8）


def list_sources(account_id: int) -> list:
    conn = get_personal_conn()
    try:
        return [dict(r) for r in conn.execute(
            "SELECT id, name, url_template, method, param_map, value_path, currency, timeout_s,"
            " readonly, enabled, secret_env, created_at FROM price_sources WHERE account_id=?"
            " ORDER BY id", (int(account_id),))]
    finally:
        conn.close()


def upsert_source(account_id: int, data: dict) -> int:
    """新增/更新一个源。校验：URL 模板必须 http(s)、只读闸必须为真、名字必填。"""
    url = str(data.get("url_template") or "").strip()
    if not url.lower().startswith(("http://", "https://")):
        raise ValueError("url_template 必须以 http:// 或 https:// 开头")
    if not int(data.get("readonly", 1)):
        raise ValueError("价格源只允许只读（readonly 必须为 1）")
    name = str(data.get("name") or "").strip()
    if not name:
        raise ValueError("请给这个源起个名字（会记进台账 source_ref）")
    pm = data.get("param_map")
    if pm and isinstance(pm, str):
        try:
            json.loads(pm)
        except Exception:
            raise ValueError("param_map 得是 JSON 对象，例：{\"sku\": \"sku\"}")
    conn = get_personal_conn()
    try:
        cur = conn.cursor()
        pid = data.get("id")
        if pid:
            own = cur.execute("SELECT id FROM price_sources WHERE id=? AND account_id=?",
                              (int(pid), int(account_id))).fetchone()
            if not own:
                raise ValueError("这个源不属于当前账号")
            cur.execute("UPDATE price_sources SET name=?, url_template=?, method=?, param_map=?,"
                        " value_path=?, currency=?, timeout_s=?, enabled=?, secret_env=? WHERE id=?",
                        (name, url, data.get("method") or "GET", data.get("param_map"),
                         data.get("value_path"), data.get("currency") or "CNY",
                         int(data.get("timeout_s") or 15), int(data.get("enabled") or 0),
                         data.get("secret_env"), int(pid)))
            conn.commit()
            return int(pid)
        cur.execute("INSERT INTO price_sources (account_id, name, url_template, method, param_map,"
                    " value_path, currency, timeout_s, readonly, enabled, secret_env, created_at)"
                    " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                    (int(account_id), name, url, data.get("method") or "GET", data.get("param_map"),
                     data.get("value_path"), data.get("currency") or "CNY",
                     int(data.get("timeout_s") or 15), 1, int(data.get("enabled") or 0),
                     data.get("secret_env"), time.strftime("%Y-%m-%d %H:%M:%S")))
        conn.commit()
        return int(cur.lastrowid)
    finally:
        conn.close()


def build_url(src: dict, context: dict) -> str:
    """按 `url_template` 的 `{占位}` 与 `param_map` 拼真实 URL —— **参数位置由配置决定**，不是代码里写死。"""
    url = str(src["url_template"])
    pmap = src.get("param_map")
    if isinstance(pmap, str) and pmap.strip():
        pmap = json.loads(pmap)
    ctx = dict(context or {})
    vals = {}
    for ph, key in (pmap or {}).items():
        vals[ph] = ctx.get(key, "")
    for ph in PLACEHOLDER_RE.findall(url):
        vals.setdefault(ph, ctx.get(ph, ""))
    for ph, v in vals.items():
        url = url.replace("{%s}" % ph, str(v))
    return url


def extract_value(body, value_path: str):
    """按 `value_path` 取值（支持 `a.b.0.c` 这种带列表下标的路径）；取不到返回 None。"""
    if not value_path:
        return None
    cur = body
    for part in str(value_path).split("."):
        if part == "":
            continue
        if isinstance(cur, (list, tuple)):
            try:
                cur = cur[int(part)]
            except Exception:
                return None
            continue
        if isinstance(cur, dict):
            if part not in cur:
                return None
            cur = cur[part]
        else:
            return None
    return cur


def _to_price(v):
    if isinstance(v, bool) or v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    m = re.search(r"[-+]?\d+(?:\.\d+)?", str(v).replace(",", ""))
    return float(m.group(0)) if m else None


def _log_check_error(account_id: int, item_type: str, item_id, reason: str):
    """G8：失败落 `price_alerts.check_error`（提醒列表里能看见"这个源坏了"）。"""
    conn = get_personal_conn()
    try:
        conn.execute("INSERT INTO price_alerts (account_id, item_type, item_id, rule, price, detail,"
                     " check_error, fired_at) VALUES (?,?,?,?,?,?,?,?)",
                     (int(account_id), item_type, item_id, ERROR_RULE, None, reason, reason,
                      time.strftime("%Y-%m-%d %H:%M:%S")))
        conn.commit()
    except Exception as e:
        print("[price_source] 落 check_error 失败（不抛）:", e)
    finally:
        conn.close()


def check_price(account_id: int, item_type: str, item_id, sku: str = "", source_id=None,
                fetch=None) -> dict:
    """按配置检查一个商品的价并记进台账。

    返回 `{ok, reason, price, url, source}`；`ok=False` 时 `reason` 是**给人看的**原因（G8）。
    `fetch(url, timeout, headers) -> (status, text)` 可注入（用例 mock 超时/字段缺失/非 JSON）。
    """
    srcs = [s for s in list_sources(account_id) if source_id is None or int(s["id"]) == int(source_id)]
    srcs = [s for s in srcs if int(s["enabled"])]
    if not srcs:
        return {"ok": False, "price": None, "url": "", "source": "",
                "reason": "阶段 2 未接源（没有启用的价格源；本版不内置任何源）"}
    src = srcs[0]
    if not int(src.get("readonly", 1)):
        reason = "该源不是只读的，已拒绝（价格源只允许读）"
        _log_check_error(account_id, item_type, item_id, reason)
        return {"ok": False, "reason": reason, "price": None, "url": "", "source": src["name"]}
    try:
        url = build_url(src, {"sku": sku, "id": item_id})
    except Exception as e:
        reason = "URL 模板拼装失败：%s" % e
        _log_check_error(account_id, item_type, item_id, reason)
        return {"ok": False, "reason": reason, "price": None, "url": "", "source": src["name"]}
    headers = {"User-Agent": "Mozilla/5.0"}
    if src.get("secret_env"):
        val = os.environ.get(str(src["secret_env"]), "")
        if val:
            headers["Authorization"] = "Bearer %s" % val   # ★ 只从环境变量读，值永不入库/不回传
    if fetch is None:                                       # pragma: no cover - 用例一律注入
        def fetch(u, timeout=15, headers=None):
            import urllib.request
            req = urllib.request.Request(u, headers=headers or {"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.status, r.read(MAX_BODY).decode("utf-8", "replace")
    try:
        status, text = fetch(url, int(src.get("timeout_s") or 15), headers)
    except Exception as e:
        reason = "请求失败（%s）：%s" % (type(e).__name__, e)
        _log_check_error(account_id, item_type, item_id, reason)
        return {"ok": False, "reason": reason, "price": None, "url": url, "source": src["name"]}
    if int(status or 0) // 100 != 2:
        reason = "源返回 HTTP %s" % status
        _log_check_error(account_id, item_type, item_id, reason)
        return {"ok": False, "reason": reason, "price": None, "url": url, "source": src["name"]}
    try:
        body = json.loads(text)
    except Exception:
        reason = "响应不是 JSON（取值映射没法解析）"
        _log_check_error(account_id, item_type, item_id, reason)
        return {"ok": False, "reason": reason, "price": None, "url": url, "source": src["name"]}
    raw = extract_value(body, src.get("value_path"))
    price = _to_price(raw)
    if price is None:
        reason = "取值路径「%s」没取到数字（实际：%r）" % (src.get("value_path"), raw)
        _log_check_error(account_id, item_type, item_id, reason)
        return {"ok": False, "reason": reason, "price": None, "url": url, "source": src["name"]}
    rec = record_price(account_id, item_type, item_id, price, source_type="api",
                       source_ref=str(src["name"]))
    return {"ok": True, "reason": "", "price": price, "url": url, "source": src["name"], "record": rec}
