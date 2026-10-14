# -*- coding: utf-8 -*-
"""应用设置（v4.0 大点 04 · 议题三）：把「没进 `.env` 的外部键」收进一个可开关的表单。

设计口径（2026-10-13 实现期裁决）
--------------------------------
1. **载体分流**：
   - **开关 / 参数** → 落库 `app_settings` 表（键值对）—— 保存即生效（读取方走本模块的 `get_*()`，
     带 2 秒缓存 + `reload()`），不需要重启；
   - **一切密钥 / 敏感值**（模型 Key、Tavily Key、发件箱与授权码）→ **只在 `.env`**：本表不再登记、
     界面不展示、接口不回传；`_write_env()` 仍保留（供将来确有需要的密钥类写回，写前自动备份
     `.env.bak_<时间戳>` 并保留注释与其余行）。
2. **兜底顺序**：库值 → `.env`（环境变量）→ 本模块的 `SPECS` 默认值。所以老 `.env` 配法继续有效，
   表单只覆盖它。
3. **少数"导入期读取"的键**（模块级常量）由 `apply_runtime_hooks()` 热更新，见 `HOT_HOOKS`。
4. 本模块只做「读 / 写 / 校验」，不含 HTTP 路由（那是 `api/settings_api.py` 的事）。

键的来源：`docs/v4.0/research/04-文档架构优化与外接配置env条目优化.md` §6.3（59 个代码引用键中
24 个未进 `.env`，按键性分成开关 / 参数 / 路径 / 平台四类，本表只收**开关与参数**）。
"""
from __future__ import annotations

import os
import re
import shutil
import threading
import time
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
ENV_PATH = PROJECT_ROOT / ".env"

# --------------------------------------------------------------------------- #
# 设置项登记表（唯一事实源：前端表单、读值、校验都从这里取）
#   key    = 环境变量名 / 库键名（同名，便于与 .env 对照）
#   kind   = switch | number | select | text | secret
#   group  = switches（开关） | decision（决策模型参数） | mail（邮件发信） | secrets（密钥）
# --------------------------------------------------------------------------- #
SPECS: tuple[dict, ...] = (
    # ---------------- 开关（用户可在设置页自由开关） ----------------
    dict(key="ASR_ENABLED", kind="switch", default="1", group="switches",
         label="语音转文字", help="关掉后前端不再加载 ASR 模型、长按说话不工作（省内存/显存）。"),
    dict(key="DECISION_CACHE_ENABLED", kind="switch", default="1", group="switches",
         label="决策路由缓存", help="命中缓存直接走上次的裁决，省一次模型调用；关掉则每次重判。"),
    dict(key="DECISION_AUDIT_SEEN", kind="switch", default="1", group="switches",
         label="记录「路由来过」痕迹", help="把每次路由决策写进 data/audit/decision.jsonl，便于复盘。"),
    dict(key="DECISION_ALLOW_NONE", kind="switch", default="0", group="switches",
         label="允许决策模型回答「都不选」", help="默认关：噪声交给路由器兜；打开后模型可以拒选（可能让路由更保守）。"),
    dict(key="ZX_STREAM", kind="switch", default="1", group="switches",
         label="流式输出", help="回答逐字返回（关掉则等全文生成完再一次性返回）。"),
    dict(key="ZX_ASSEMBLY_SOURCES", kind="select", default="new", group="switches",
         choices=("new", "legacy"),
         label="上下文装配实现", help="new = 现役上下文来源注册表；legacy = 老实现（兼容排查用，需要重启）。",
         restart=True),
    dict(key="TOOL_CALL_LOG_ENABLED", kind="switch", default="1", group="switches",
         label="工具调用日志", help="每次工具起跑落一条 data/audit/tool_calls.jsonl（工具池测评会读它兜底）。"),
    dict(key="AGENT_ERROR_LOG_ENABLED", kind="switch", default="1", group="switches",
         label="Agent 错误日志", help="运行出错时把完整调用栈落 data/audit/agent_errors.jsonl。"),
    dict(key="DS_DEBUG_PROMPTS", kind="switch", default="0", group="switches",
         label="提示词调试转储", help="把每轮发给模型的完整提示词转储到 data/audit/prompts_dump.txt（开发排查用；"
                                     "★ 原实现是打到 stdout，Windows 重定向时遇 emoji 会崩，故改为写文件）。"),

    # ---------------- 决策模型参数（进阶，数值/枚举） ----------------
    dict(key="DECISION_TRIGGER", kind="select", default="near_tie", group="decision",
         choices=("near_tie", "always", "gray", "off"),
         label="触发策略", help="near_tie 近分才判（默认）｜always 每题都判｜gray 只在灰区判｜off 完全关闭。"),
    dict(key="DECISION_TIE_THRESHOLD", kind="number", default="0.0005", group="decision",
         label="近分阈值", help="两条候选分差小于它就算「近分」，才交给决策模型精排。"),
    dict(key="DECISION_TOP_K", kind="number", default="3", group="decision", min=2, max=8,
         label="精排候选数", help="交给决策模型比较的候选条数（2~8）。"),
    dict(key="DECISION_TIMEOUT_S", kind="number", default="20", group="decision", min=1,
         label="单次超时（秒）", help="决策模型单次调用超时，超时按规则路由结果走。"),
    dict(key="DECISION_MAX_RETRIES", kind="number", default="2", group="decision", min=0, max=4,
         label="失败重试次数", help="决策模型调用失败时的重试次数（0~4）。"),
    dict(key="DECISION_BUDGET_PER_DAY", kind="number", default="500", group="decision", min=0,
         label="每日额度（次）", help="每天最多调用多少次决策模型，0 = 不限。"),
    dict(key="DECISION_BUDGET_PER_SESSION", kind="number", default="200", group="decision", min=0,
         label="每会话额度（次）", help="单个会话内最多调用多少次，0 = 不限。"),
    dict(key="DECISION_CACHE_TTL_H", kind="number", default="24", group="decision", min=0,
         label="缓存有效（小时）", help="路由缓存多久过期，0 = 不过期。"),

    # ---------------- 邮件发信（议题二 · 验证码通道） ----------------
    # ★ 2026-10-13 用户拍板：**发件箱只放在 `.env`**（QQMAIL = 发件邮箱地址、QQMSTP = 该邮箱授权码），
    #   不进前端、不进库、接口不回传 —— 所以这里不再登记 SMTP_HOST/PORT/SSL/USER/AUTH_CODE。
    #   主机与端口在 utils/mailer.py 里有 QQ 默认值（smtp.qq.com:465 + SSL），换企业邮时直接写 .env 覆盖。
    #   本组只保留「能安全露在界面上的」三项：显示名、调试开关、发信配额。
    dict(key="SMTP_FROM_NAME", kind="text", default="智选情报官", group="mail",
         label="发件人显示名", help="收件人看到的发件人名字（发件邮箱本身在 .env 的 QQMAIL，不在此展示）。"),
    dict(key="MAIL_DEBUG", kind="switch", default="0", group="mail",
         label="邮件调试模式", help="打开后不发信，把验证码写进 tests/test_out/_mail_outbox.log（本机联调用，"
                                    "★ 请勿在生产长期开启）。"),
    dict(key="MAIL_QUOTA_PER_ACCOUNT_DAY", kind="number", default="10", group="mail", min=0,
         label="每账号发信上限（24 小时）", help="单个账号 24 小时内最多发多少封验证码，0 = 不限。"
                                                 "防刷也防撞上 QQ 邮箱的当日发信上限。"),
    dict(key="MAIL_QUOTA_GLOBAL_DAY", kind="number", default="50", group="mail", min=0,
         label="全机发信上限（24 小时）", help="本机所有账号 24 小时内合计上限，0 = 不限。"),

    # ★ 2026-10-13 用户拍板：**密钥组整组撤下**（原先在这里只读掩码展示 OPENAI_API_KEY /
    #   TAVILY_API_KEY）——密钥一律只在 `.env` 管，界面既不展示也不回显。

    # ---------------- 网络搜索子Agent升级（05 · browser-use 浏览器工具） ----------------
    # 口径见 docs/v4.0/v4.0规划文档.md §05 ③ 段；裁定见 docs/v4.0/Verdict/05-网络搜索子Agent升级.md。
    # ★ 总开关标了 restart=True：子 Agent 的工具列表是**装配期**固定的（框架约定），
    #   关掉后需重启后端才彻底不挂载（工具内部另有一道兜底，调用时也会直接回话）。
    dict(key="BROWSER_MODEL_REF", kind="text", default="", group="browser",
         label="浏览器侧模型（留空=跟随聊天侧）",
         help="填「定制助手 · 模型选型」里某条配置的**名字或 id**，浏览器抓取专用它；留空=跟随聊天侧生效配置"
              "（无生效配置则用 .env 默认模型）。"
              "为什么要能单独指定（2026-10-14 实测）：浏览器工具要求模型**按 JSON 输出动作** —— "
              "siliconflow 的 GLM 干净可用、项目默认 agnes 会丢步、deepseek 用自己的 DSML 方言完全不可用。"
              "★ 本项是**全机**设置：请在各账号里用**同名**配置（例如统一叫「浏览-视觉」），"
              "没配同名配置的账号会自动回退到它自己的生效配置。"
              "★★ **强烈建议指向「勾了支持图片」的多模态配置**（2026-10-14 实测）：B 站「大模型世界」这类"
              "**JS 动态浮层**，纯文本模式（supports_vision=0）读 DOM 反复超时、上下文与价格一项都拿不到；"
              "换成多模态看截图后，同一题 4 步 51.6 秒一次拿全（含 1M 上下文、$20 输出价）。"),
    dict(key="BROWSER_USE_ENABLED", kind="switch", default="1", group="browser", restart=True,
         label="浏览器抓取工具", help="打开后【网络搜索助手】多两个取法工作流：`browse_only`（只进指定网址）与 "
                                     "`search_then_browse`（先检索定位、再进站深抓）；常挂的是 `web_search_only`（只全网检索）。"
                                     "关掉=只剩全网检索，与升级前等价。改动需重启后端。"),
    dict(key="BROWSER_MAX_STEPS", kind="number", default="8", group="browser", min=1, max=100,
         label="单次最多步数", help="一次浏览任务最多操作多少步。浏览器侧是多步模型循环，**每步约 35~40 秒**"
                                   "（本机实测），步数越多越慢越费 token；8 步足够「打开一页取要点」。"),
    dict(key="BROWSER_TASK_TIMEOUT_S", kind="number", default="300", group="browser", min=30,
         label="单次超时（秒）", help="一次浏览任务的总超时（默认 300：约等于 8 步的实测耗时）；"
                                     "超时会连浏览器一起收掉，并回退提示改用 Tavily。"),
    dict(key="BROWSER_MAX_URLS", kind="number", default="3", group="browser", min=1, max=10,
         label="单次最多打开页数", help="一次任务最多打开几个目标页，超出只取前 N 个（先筛后进，别一次铺开）。"),
    dict(key="BROWSER_HEADLESS", kind="switch", default="1", group="browser",
         label="无头运行浏览器", help="默认无头（不弹窗口）；排查页面问题时关掉它，观察真实浏览器行为。"),
)

SPEC_BY_KEY = {s["key"]: s for s in SPECS}
_cache: dict[str, Any] = {"t": 0.0, "vals": {}}
_lock = threading.Lock()
_CACHE_TTL = 2.0

_TRUE = ("1", "true", "yes", "on")


def _ensure_tables():
    """确保 app_settings 表在（走 schema_personal.ensure_tables 的同一套幂等建表）。"""
    from tools.schema_personal import ensure_tables
    ensure_tables()


# --------------------------------------------------------------------------- #
# 读
# --------------------------------------------------------------------------- #
def _load_all() -> dict:
    """库值全量（带 2 秒缓存）。"""
    now = time.time()
    with _lock:
        if _cache["vals"] and (now - _cache["t"]) < _CACHE_TTL:
            return _cache["vals"]
    vals: dict[str, str] = {}
    try:
        _ensure_tables()
        from tools.schema_personal import get_personal_conn
        conn = get_personal_conn()
        try:
            for r in conn.execute("SELECT key, value FROM app_settings"):
                vals[r["key"]] = r["value"]
        finally:
            conn.close()
    except Exception as e:                                            # noqa: BLE001
        print("[settings] 读库失败（回落 .env / 默认值）: %s" % e)
    with _lock:
        _cache["vals"] = vals
        _cache["t"] = now
    return vals


def reload() -> None:
    """清缓存（保存设置后调用；也供测试用）。"""
    with _lock:
        _cache["vals"] = {}
        _cache["t"] = 0.0


def get(key: str, default: Any = None) -> Any:
    """取值顺序：库 → 环境变量（.env）→ 登记表默认值 → 调用方 default。"""
    spec = SPEC_BY_KEY.get(key)
    if default is None and spec is not None:
        default = spec.get("default")
    v = _load_all().get(key)
    if v is None or v == "":
        v = os.getenv(key)
    if v is None or v == "":
        v = default
    return v


def get_bool(key: str, default: bool | None = None) -> bool:
    v = get(key)
    if v is None or v == "":
        return bool(default)
    return str(v).strip().lower() in _TRUE


def get_int(key: str, default: int = 0) -> int:
    try:
        return int(str(get(key)).strip())
    except Exception:                                                 # noqa: BLE001
        return int(default)


def get_float(key: str, default: float = 0.0) -> float:
    try:
        return float(str(get(key)).strip())
    except Exception:                                                 # noqa: BLE001
        return float(default)


def source_of(key: str) -> str:
    """这个键当前值来自哪里：db / env / default（设置页要显示，便于排查"改了没生效"）。"""
    if _load_all().get(key) not in (None, ""):
        return "db"
    if os.getenv(key) not in (None, ""):
        return "env"
    return "default"


def masked(key: str, head: int = 4, tail: int = 4) -> str:
    """密钥掩码（只给首尾，短值全遮）。"""
    v = str(get(key) or "")
    if not v:
        return ""
    if len(v) <= head + tail:
        return "*" * len(v)
    return v[:head] + "*" * (len(v) - head - tail) + v[-tail:]


# --------------------------------------------------------------------------- #
# 写
# --------------------------------------------------------------------------- #
def _validate(spec: dict, raw: str) -> str:
    """按 kind 校验并归一（返回要落盘的值；非法就抛 ValueError，前端会看到明确原因）。"""
    kind = spec.get("kind")
    v = ("" if raw is None else str(raw)).strip()
    if kind == "switch":
        return "1" if v.lower() in _TRUE else "0"
    if kind == "number":
        if v == "":
            raise ValueError("%s 不能为空" % spec.get("label") or spec["key"])
        try:
            f = float(v)
        except ValueError:
            raise ValueError("%s 必须是数字，收到 %r" % (spec.get("label") or spec["key"], v))
        for lo_key, op, bad in (("min", "lt", "小于最小值"), ("max", "gt", "大于最大值")):
            lim = spec.get(lo_key)
            if lim is None:
                continue
            if (op == "lt" and f < lim) or (op == "gt" and f > lim):
                raise ValueError("%s %s（%s）" % (spec.get("label") or spec["key"], bad, lim))
        return v            # 原样保留（如 0.0005 不能因为"是整数"被取整丢掉）
    if kind == "select":
        choices = spec.get("choices") or ()
        if v not in choices:
            raise ValueError("%s 只能是 %s 之一" % (spec.get("label") or spec["key"], "/".join(choices)))
        return v
    return v


def _write_env(updates: dict) -> Path:
    """把若干键写进 `.env`（密钥走这条路）：保留注释与其余行，写前备份。"""
    bak = None
    if ENV_PATH.exists():
        bak = ENV_PATH.with_name(ENV_PATH.name + ".bak_" + time.strftime("%Y%m%d_%H%M%S"))
        shutil.copy2(ENV_PATH, bak)
    lines = ENV_PATH.read_text(encoding="utf-8").splitlines() if ENV_PATH.exists() else []
    seen = set()
    out = []
    for l in lines:
        m = re.match(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=", l)
        if m and m.group(1) in updates:
            k = m.group(1)
            out.append("%s=%s" % (k, updates[k])); seen.add(k)
        else:
            out.append(l)
    for k, v in updates.items():
        if k not in seen:
            out.append("%s=%s" % (k, v))
    tmp = ENV_PATH.with_suffix(".env.tmp")
    tmp.write_text("\n".join(out).rstrip("\n") + "\n", encoding="utf-8")
    tmp.replace(ENV_PATH)
    # 让本次进程立即读到新值（.env 是启动时加载的，改完要手动覆盖）
    for k, v in updates.items():
        os.environ[k] = str(v)
    return bak


def save(items: dict, actor: str = "") -> dict:
    """保存设置：开关/参数进库，密钥写 .env。返回 {saved, env_keys, changed}。

    ★ 密钥只落 `.env`（明文不进库），且**回传永远只给掩码**（`all_values` 负责）。
    """
    db_items, env_items = {}, {}
    for k, v in (items or {}).items():
        spec = SPEC_BY_KEY.get(k)
        if spec is None:
            raise ValueError("未知设置项：%s" % k)
        if spec.get("readonly"):
            raise ValueError("%s 为只读项（请在 .env 直接改）" % k)
        val = _validate(spec, v)
        (env_items if spec.get("kind") == "secret" else db_items)[k] = val

    if db_items:
        _ensure_tables()
        from tools.schema_personal import get_personal_conn
        conn = get_personal_conn()
        try:
            for k, v in db_items.items():
                conn.execute(
                    "INSERT INTO app_settings(key, value, updated_at, updated_by) VALUES (?,?,?,?) "
                    "ON CONFLICT(key) DO UPDATE SET value=excluded.value, "
                    "updated_at=excluded.updated_at, updated_by=excluded.updated_by",
                    (k, v, int(time.time()), actor or ""))
            conn.commit()
        finally:
            conn.close()
    bak = _write_env(env_items) if env_items else None
    reload()
    apply_runtime_hooks(list(db_items) + list(env_items))
    return {"saved": sorted(db_items) + sorted(env_items), "env_keys": sorted(env_items),
            "env_backup": (str(bak.name) if bak else ""), "changed": sorted(set(db_items) | set(env_items))}


# --------------------------------------------------------------------------- #
# 少数"导入期读取"的键：热更新（否则要重启才能生效）
# --------------------------------------------------------------------------- #
def apply_runtime_hooks(keys: list) -> list:
    """把模块级常量型的开关就地刷新。返回实际热更的键。"""
    done = []
    ks = set(keys or ())
    if "ZX_ASSEMBLY_SOURCES" in ks:
        try:
            import api.server as srv
            srv._USE_SOURCES = get("ZX_ASSEMBLY_SOURCES") != "legacy"
            done.append("ZX_ASSEMBLY_SOURCES")
        except Exception as e:                                        # noqa: BLE001
            print("[settings] 热更 ZX_ASSEMBLY_SOURCES 失败（重启后生效）: %s" % e)
    return done


# --------------------------------------------------------------------------- #
# 前端要的形状
# --------------------------------------------------------------------------- #
GROUP_LABELS = {
    "switches": ("🔘 开关", "可以随时开关；保存即生效（个别标注需重启）。"),
    "browser": ("🌐 浏览器抓取（网络搜索升级）", "网络搜索助手多一个「进网址取一手资料」的工具："
                                             "有网址时先 Tavily 定位筛选、再打开关键页深挖；另有步数/超时/页数三道闸。"),
    "decision": ("🧭 决策模型参数", "账号路由上「近分精排」的行为参数；保存即生效。"),
    "mail": ("✉️ 邮件发信（验证码通道）", "发件箱在 .env（QQMAIL / QQMSTP）——本页只管显示名、调试开关与发信配额；"
                                          "收件邮箱是各账号自己绑的那个。"),
    # 密钥组已整组撤下（2026-10-13 用户拍板：密钥只在 .env，界面不展示也不回显）
}


def all_values(mask_secrets: bool = True) -> dict:
    """给设置页的完整数据：分组 + 每项的当前值/来源/默认值/帮助。"""
    groups: dict[str, list] = {}
    for s in SPECS:
        k = s["key"]
        raw = get(k)
        from_db = source_of(k) == "db"
        shown = raw
        if s.get("kind") == "secret" and mask_secrets:
            shown = masked(k)
        groups.setdefault(s.get("group", "switches"), []).append(dict(
            key=k, label=s.get("label") or k, kind=s.get("kind"), value=shown,
            has_value=bool(str(raw or "")), source=source_of(k),
            default=s.get("default"), choices=list(s.get("choices") or ()),
            help=s.get("help") or "", restart=bool(s.get("restart")),
            readonly=bool(s.get("readonly")), overridden=from_db,
        ))
    return {
        "groups": [dict(id=g, label=GROUP_LABELS.get(g, (g, ""))[0], tip=GROUP_LABELS.get(g, ("", ""))[1],
                        items=items) for g, items in groups.items()],
        "env_path": str(ENV_PATH),
    }
