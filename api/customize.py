"""定制助手后端 API（背景图 + 模型选型 + 人格 SOUL + 记忆画像 MEMORY）。

- 背景图：pic/{username}/ 目录，按账号隔离；上传/列表/删除
- 模型配置：llm_providers 表（按 owner_id），api_key 列表时掩码显示
- 人格 SOUL：agents_docs/{username}/SOUL.md（默认）+ SOUL/{name}.md（多人格集），
  激活状态存 ACTIVE_SOUL 文件，注入 system_prompt
- 记忆画像 MEMORY.md：agents_docs/{username}/MEMORY.md，Agent 对话中自行维护
  （read/write 工具），用户可在前端编辑；支持一次性 AI 初始化画像
"""
import os
import re
import shutil
import time
import uuid
from pathlib import Path
from typing import Optional

from fastapi import HTTPException, UploadFile, File, status
from pydantic import BaseModel

from tools.schema_personal import get_personal_conn, ensure_tables

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
PIC_DIR = _PROJECT_ROOT / "pic"                 # 背景图根目录：pic/{username}/
AGENTS_DOCS = _PROJECT_ROOT / "agents_docs"     # 人格/记忆：agents_docs/{username}/

_ALLOWED_IMG_EXT = {".png", ".jpg", ".jpeg", ".webp", ".gif"}

# 记忆画像模板（参照 Hermes 记忆系统：条目简洁、事实化、画像/笔记分段）
MEMORY_TEMPLATE = """# 用户记忆画像（MEMORY）

> 智能体会在对话中自行判断是否需要更新本文件（通过读写文档工具）；
> 你也可以在「定制助手」页直接编辑。画像段记录稳定的用户事实，笔记段记录跨会话有用的偏好与未决问题。

<!-- memory_init: pending -->

## USER PROFILE（用户画像）

（初始为空。可在「定制助手」页点击「✨ 初始化用户画像」，AI 会根据你系统中已有的兴趣/收藏/竞品等信息生成一次初始画像。）

## MEMORY（助手笔记）

（Agent 在对话中自行维护：你的长期偏好、未决问题、重要事实等。）
"""


# ----------------------------- 工具 -----------------------------
def _require_account_id(token: str) -> int:
    from api.account import get_session
    return get_session(token)["account_id"]


def _username_by_token(token: str) -> str:
    from api.account import get_session
    return get_session(token)["username"]


def _user_pic_dir(username: str) -> Path:
    d = PIC_DIR / username
    d.mkdir(parents=True, exist_ok=True)
    return d


def _user_doc_dir(username: str) -> Path:
    d = AGENTS_DOCS / username
    d.mkdir(parents=True, exist_ok=True)
    return d


def _user_soul_path(username: str) -> Path:
    """默认人格文件（兼容旧版单人格逻辑）"""
    return _user_doc_dir(username) / "SOUL.md"


def _user_soul_dir(username: str) -> Path:
    """多人格目录：agents_docs/{username}/SOUL/（存放 {name}.md）"""
    d = _user_doc_dir(username) / "SOUL"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _active_soul_file(username: str) -> Path:
    """记录当前激活人格的文件（内容为相对路径，如 SOUL.md 或 SOUL/稳重管家.md）"""
    return _user_doc_dir(username) / "ACTIVE_SOUL"


def get_active_soul_name(username: str) -> str:
    """当前激活的人格文件相对路径；无记录时默认 SOUL.md"""
    f = _active_soul_file(username)
    if f.exists():
        name = f.read_text(encoding="utf-8").strip()
        if name:
            return name
    return "SOUL.md"


def _soul_abs_path(username: str, name: str) -> Path:
    """按人格名解析绝对路径（SOUL/{name}.md），防路径穿越"""
    name = os.path.basename(name)  # 任何穿越形式只剩文件名
    if name.endswith(".md"):
        name = name[:-3]
    if name in ("", "SOUL"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "非法人格名")
    return _user_soul_dir(username) / f"{name}.md"


def _safe_name(name: str) -> str:
    """只保留安全文件名，防路径穿越"""
    name = os.path.basename(name)
    name = re.sub(r"[^\w.\-]", "_", name)
    return name


# ============================ 背景图 ============================
class BgListResp(BaseModel):
    images: list  # [{name, url}]


def bg_upload(token: str, file: UploadFile = File(...)) -> dict:
    """上传背景图到 pic/{username}/，返回可访问 URL"""
    user = _username_by_token(token)
    ext = os.path.splitext(file.filename or "")[1].lower()
    if ext not in _ALLOWED_IMG_EXT:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"仅支持图片: {sorted(_ALLOWED_IMG_EXT)}")
    # 用 uuid 前缀防重名/覆盖
    fname = f"{uuid.uuid4().hex[:8]}_{_safe_name(file.filename or 'bg')}"
    dest = _user_pic_dir(user) / fname
    try:
        content = file.file.read()
        dest.write_bytes(content)
    except Exception as e:
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, f"保存失败: {e}")
    return {"ok": True, "name": fname, "url": f"/pic/{user}/{fname}"}


def bg_list(token: str) -> dict:
    """列出该账号所有背景图"""
    user = _username_by_token(token)
    d = _user_pic_dir(user)
    items = []
    for f in sorted(d.iterdir(), key=lambda x: x.stat().st_mtime, reverse=True):
        if f.is_file() and f.suffix.lower() in _ALLOWED_IMG_EXT:
            items.append({"name": f.name, "url": f"/pic/{user}/{f.name}"})
    return {"images": items}


def bg_delete(token: str, name: str) -> dict:
    """删除该账号一张背景图"""
    user = _username_by_token(token)
    fname = _safe_name(name)
    dest = _user_pic_dir(user) / fname
    if not dest.is_file():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "图片不存在")
    dest.unlink()
    return {"ok": True}


# ============================ 模型配置 ============================
class LlmProviderReq(BaseModel):
    provider_name: str
    model_name: str
    base_url: str
    api_key: str
    is_active: bool = False
    # ★ M6c-5：三处**可选填**单价（固定单位 元/百万 tokens）。留空=None → 只统计 token、不折算金额。
    price_in_cached: float | None = None
    price_in_uncached: float | None = None
    price_out: float | None = None
    # ★ v3.1 视觉：这套配置能否收图片（配置导向 ✓ 默认 False=不能 ✓ 用户自己勾 ✓ 不写死厂商 ✗）
    supports_vision: bool = False


def _norm_prices(req) -> tuple:
    """三处单价归一化：None 保持 None（留空 ≠ 免费）；负数直接拒（脏数据不进库）。"""
    vals = []
    for name, v in (("输入价(缓存命中)", req.price_in_cached),
                    ("输入价(缓存未命中)", req.price_in_uncached),
                    ("输出价", req.price_out)):
        if v is None:
            vals.append(None)
            continue
        try:
            f = float(v)
        except (TypeError, ValueError):
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "%s 要填数字" % name)
        if f < 0:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "%s 不能是负数（免费请填 0）" % name)
        vals.append(f)
    return tuple(vals)


def _mask_key(key: str) -> str:
    """api_key 掩码：只显后 4 位（信息安全；业务信息不打码）"""
    key = (key or "").strip()
    if not key:
        return ""
    if len(key) <= 4:
        return "****" + key
    return "****" + key[-4:]


def _normalize_provider(p: dict, mask: bool = True) -> dict:
    # ★ M6c-5：单价必须**出现在出口**里，否则前端永远拿不到（第一版容易只改表不改出参 ✗）
    # ★ mask=True 是**回显给前端**用的（默认值 = 保持原行为）；
    #   造模型取配置时必须 mask=False —— 否则掩码串会被当成密钥发给上游（见 get_active_provider）。
    return {
        "id": p["id"],
        "provider_name": p["provider_name"],
        "model_name": p["model_name"],
        "base_url": p["base_url"],
        "api_key": _mask_key(p["api_key"]) if mask else p["api_key"],
        "is_active": bool(p["is_active"]),
        "price_in_cached": p.get("price_in_cached"),
        "price_in_uncached": p.get("price_in_uncached"),
        "price_out": p.get("price_out"),
        "supports_vision": bool(p.get("supports_vision")),
    }


def llm_providers_list(token: str) -> dict:
    aid = _require_account_id(token)
    ensure_tables()
    conn = get_personal_conn()
    try:
        rows = conn.execute(
            "SELECT * FROM llm_providers WHERE owner_id=? ORDER BY id", (aid,)).fetchall()
        items = [_normalize_provider({k: r[k] for k in r.keys()}) for r in rows]
        return {"providers": items}
    finally:
        conn.close()


def llm_provider_add(req: LlmProviderReq, token: str) -> dict:
    aid = _require_account_id(token)
    ensure_tables()
    conn = get_personal_conn()
    try:
        # 若设为启用，先清掉其他启用的
        if req.is_active:
            conn.execute("UPDATE llm_providers SET is_active=0 WHERE owner_id=?", (aid,))
        p_c, p_u, p_o = _norm_prices(req)
        cur = conn.execute(
            "INSERT INTO llm_providers (owner_id, provider_name, model_name, base_url, api_key, is_active,"
            " price_in_cached, price_in_uncached, price_out, supports_vision) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (aid, req.provider_name.strip(), req.model_name.strip(),
             req.base_url.strip(), req.api_key.strip(), int(req.is_active), p_c, p_u, p_o, int(bool(req.supports_vision))))
        conn.commit()
        row = conn.execute("SELECT * FROM llm_providers WHERE id=?", (cur.lastrowid,)).fetchone()
        return {"provider": _normalize_provider({k: row[k] for k in row.keys()})}
    finally:
        conn.close()


def llm_provider_update(pid: int, req: LlmProviderReq, token: str) -> dict:
    aid = _require_account_id(token)
    ensure_tables()
    conn = get_personal_conn()
    try:
        own = conn.execute("SELECT id FROM llm_providers WHERE id=? AND owner_id=?", (pid, aid)).fetchone()
        if not own:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "配置不存在")
        if req.is_active:
            conn.execute("UPDATE llm_providers SET is_active=0 WHERE owner_id=?", (aid,))
        new_key = (req.api_key or "").strip()
        p_c, p_u, p_o = _norm_prices(req)          # ★ 两条分支都要带上单价，别只改一条
        if not new_key or new_key.startswith("****"):
            # 留空 / 掩码串 = 不修改 key（保留原值）
            conn.execute(
                "UPDATE llm_providers SET provider_name=?, model_name=?, base_url=?, is_active=?,"
                " price_in_cached=?, price_in_uncached=?, price_out=?, supports_vision=? WHERE id=? AND owner_id=?",
                (req.provider_name.strip(), req.model_name.strip(), req.base_url.strip(),
                 int(req.is_active), p_c, p_u, p_o, int(bool(req.supports_vision)), pid, aid))
        else:
            conn.execute(
                "UPDATE llm_providers SET provider_name=?, model_name=?, base_url=?, api_key=?, is_active=?,"
                " price_in_cached=?, price_in_uncached=?, price_out=?, supports_vision=? WHERE id=? AND owner_id=?",
                (req.provider_name.strip(), req.model_name.strip(), req.base_url.strip(),
                 new_key, int(req.is_active), p_c, p_u, p_o, int(bool(req.supports_vision)), pid, aid))
        conn.commit()
        row = conn.execute("SELECT * FROM llm_providers WHERE id=?", (pid,)).fetchone()
        return {"provider": _normalize_provider({k: row[k] for k in row.keys()})}
    finally:
        conn.close()


def llm_provider_delete(pid: int, token: str) -> dict:
    aid = _require_account_id(token)
    ensure_tables()
    conn = get_personal_conn()
    try:
        conn.execute("DELETE FROM llm_providers WHERE id=? AND owner_id=?", (pid, aid))
        conn.commit()
        return {"ok": True}
    finally:
        conn.close()


def llm_provider_set_active(pid: int, token: str) -> dict:
    """**开关**：没启用 → 启用；已是「使用中」→ **停用**（回到项目自带的 .env 模型）。

    ★ 2026-09-30 用户要求：原来只有"切到另一套"，没有"回到默认" ✗ ——
      现在再点一次「✓ 使用中」即可停用，默认模型由 `.env` 提供（`get_active_provider` 返回 None 就是它）。
    """
    aid = _require_account_id(token)
    ensure_tables()
    conn = get_personal_conn()
    try:
        row = conn.execute("SELECT is_active FROM llm_providers WHERE id=? AND owner_id=?",
                           (pid, aid)).fetchone()
        if not row:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "配置不存在")
        if int(row[0] or 0) == 1:                     # 已启用 → 停用（回到默认模型）
            conn.execute("UPDATE llm_providers SET is_active=0 WHERE id=? AND owner_id=?", (pid, aid))
            conn.commit()
            return {"ok": True, "active": False, "fallback": "项目默认模型（.env）"}
        conn.execute("UPDATE llm_providers SET is_active=0 WHERE owner_id=?", (aid,))
        conn.execute("UPDATE llm_providers SET is_active=1 WHERE id=? AND owner_id=?", (pid, aid))
        conn.commit()
        return {"ok": True, "active": True}
    finally:
        conn.close()


def get_active_provider(aid: int) -> Optional[dict]:
    """查询某账号启用的模型配置（无则 None = 用默认 .env 模型）。**返回原样密钥**（造模型要用）。

    ★ 2026-09-30 用户实测定位的真 bug：这里原来对所有调用方都套 `_normalize_provider`，
      而它内部会 `_mask_key(...)` ✗ —— 于是**把掩码串当密钥发给了上游**：
        · 上游报 `Your api key: ****f98f is invalid`（掩码**保留尾部 4 位**，看着像真 key，极难看出是本地换掉的 ✗）；
        · 同一条 key 放 `.env` 能用、放自定义配置就 401（用户 A/B 实测确认）。
      现在：默认给前端回显时仍然掩码（`_normalize_provider(mask=True)`），**只有这条造模型的路径取原文**。
    """
    ensure_tables()
    conn = get_personal_conn()
    try:
        row = conn.execute(
            "SELECT * FROM llm_providers WHERE owner_id=? AND is_active=1 LIMIT 1", (aid,)).fetchone()
        return _normalize_provider({k: row[k] for k in row.keys()}, mask=False) if row else None
    finally:
        conn.close()


# ============================ SOUL.md（人格）============================
def soul_get(token: str) -> dict:
    """读取该账号 SOUL.md 内容"""
    user = _username_by_token(token)
    p = _user_soul_path(user)
    content = p.read_text(encoding="utf-8") if p.exists() else ""
    return {"content": content, "path": str(p.relative_to(_PROJECT_ROOT))}


class SoulReq(BaseModel):
    content: str


def soul_save(token: str, req: SoulReq) -> dict:
    """保存该账号 SOUL.md（限长防滥用）"""
    user = _username_by_token(token)
    if len(req.content) > 8000:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "人格内容过长（≤8000字）")
    p = _user_soul_path(user)
    p.write_text(req.content, encoding="utf-8")
    return {"ok": True, "saved_at": str(p.relative_to(_PROJECT_ROOT))}


def get_soul_content(aid: int) -> str:
    """按账号读取 SOUL.md 内容（供 server 注入 system_prompt）"""
    ensure_tables()
    conn = get_personal_conn()
    try:
        row = conn.execute("SELECT username FROM accounts WHERE id=?", (aid,)).fetchone()
        if not row:
            return ""
    finally:
        conn.close()
    p = _user_soul_path(row["username"])
    return p.read_text(encoding="utf-8").strip() if p.exists() else ""


# ============================ CLI 能力描述生成 AI（M5）============================
def cli_ability_generate(name: str, bin_name: str, rules_text: str, *, docs: str = "",
                         docs_mode: str = "url", help_text: str = "",
                         username: str | None = None,
                         account_id: int | None = None, refresh_docs: bool = False) -> dict:
    """按证据生成「CLI 能力描述」草稿（用户语言）。**只生成草稿，不落库**。

    用途：M5 工具智能路由第一层——这段描述会进主 Agent 的系统提示词，
    让它在用户说人话时想起该用这个 CLI。

    依据来源是**三选一**（`docs_mode`，2026-10-07 用户提出并拍板）：
      · `url`  —— 网址（一般指 CLI 的 GitHub README），抓取后当唯一事实来源；
      · `path` —— **本机文件**（自研 / 还没开源的工具，README 就在硬盘上），读文件当唯一事实来源；
      · `help` —— **跑一遍 `--help`**（闭源公测、官网只有一句介绍的工具）：裸 `<bin> --help` +
                  只读清单里每条命令的 `--help`，拼成证据当唯一事实来源。
    为什么要有后两种：qqmail（自研、未上传 GitHub）与 agently-cli（闭源、公测期官网几乎无介绍）
    这两类工具根本没有可读的文档页，`--help` 是**唯一**的方法信息源。

    证据分层（2026-09-24 用户拍板，实测：只给命令名时 13 条映射有 3 条错，全错在「要 ID」这类判断上）：
      ① **语义层**：上面三选一得到的文本 → 作为**唯一事实来源**；
         它的用法示例能表达「引号里的书名 = 自由文本」「裸数字 = ID」这类关键区别。
      ② **校验层**：逐条跑 `<bin> <cmd> --help`（走沙箱运行时 + 只读闸），得到每条命令的形态
         （`needs_id` / `free_text` / `standalone`）——**以表格形式**给模型当事实（不是塞原始 help，
         那里面 `Get /book/info` 这类噪音只会干扰），并在生成后用于**审计草稿**。
      ③ **兜底**：文档抓不到 / 文档没覆盖那条命令 → 用 help 原文补位 → 用户粘贴的 `help_text` 补位
         → 都没有就保守化（宁可少写一条，也不编造）。
    """
    import yaml

    name = (name or "").strip() or "该 CLI"
    bin_name = (bin_name or "").strip()
    rules_text = (rules_text or "").strip()
    if not rules_text:
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            "请先填好「只读命令清单」再生成能力描述——描述里的命令必须来自这份清单")

    # ---- 清单解析（与保存时同一套：只保留子命令路径）
    from tools import cli_registry as reg
    rules, _notes = reg.parse_rules(rules_text, bin_name)
    cmds = [" ".join(r) for r in rules]

    # ---- 证据 ①：依据来源三选一（url / path / help）
    from tools import cli_docs, source_docs
    mode = (docs_mode or "url").strip().lower()
    if mode not in reg.DOCS_MODES:
        mode = "url"
    if mode == "help":
        # 裸 `--help` + 每条只读命令的 `--help`（走沙箱运行时与只读闸；裸 --help 已放行）
        digest = reg.collect_help_digest(bin_name, cmds, username=username, account_id=account_id) \
            if bin_name else {"ok": False, "text": "", "ran": [], "failed": [],
                              "note": "没填可执行名，跑不了 --help"}
        doc = {"ok": bool(digest.get("ok")), "text": digest.get("text") or "", "url": "",
               "source": "help", "note": digest.get("note") or ""}
        doc_label = "本机 --help 取证（%s）" % (bin_name or "缺可执行名")
        # help 原文本身就是密集的用法事实，不做 README 那套过滤，只按上限截断
        doc_text = (doc["text"] or "")[:12000] if doc["ok"] else ""
    elif mode == "path":
        doc = source_docs.read_local(docs) if docs else {
            "ok": False, "text": "", "source": "file", "url": "", "note": "没填本机文件路径"}
        doc_label = "本机文档文件"
        doc_text = cli_docs.extract_evidence(doc["text"], cmds, bin_name) if doc["ok"] else ""
    else:
        doc = cli_docs.fetch_docs(docs, force=refresh_docs) if docs else {
            "ok": False, "text": "", "note": "没填文档地址", "url": "", "source": ""}
        doc_label = "官方文档"
        doc_text = cli_docs.extract_evidence(doc["text"], cmds, bin_name) if doc["ok"] else ""
    covered, missing = cli_docs.coverage(doc["text"], cmds) if doc["ok"] else ([], list(cmds))

    # ---- 证据 ②：help 真值（要 bin 才能采；采不到不影响生成，只降级）
    help_map: dict = {}
    help_ok = 0
    if bin_name:
        try:
            help_map = reg.collect_help(bin_name, cmds, username=username, account_id=account_id)
            help_ok = sum(1 for v in help_map.values() if v["ok"])
        except Exception as e:  # noqa: BLE001 - 采集失败不该让草稿生成整个挂掉
            print(f"[ability] help 采集失败（降级为仅文档依据）: {type(e).__name__}: {e}")
    kinds = {c: v["kind"] for c, v in help_map.items()}

    # ---- 拼证据包
    def _kind_table() -> str:
        if not help_map:
            return ""
        lines = ["| 命令 | 形态 | 依据（本机 --help） |", "| --- | --- | --- |"]
        label = {"needs_id": "需要 ID（**不能直调**）", "free_text": "自由文本参数",
                 "needs_flag": "需要开关参数（如 `--id`，可作两步链第 2 步）",
                 "standalone": "无参数直调", "unknown": "未知"}
        for c, v in help_map.items():
            lines.append("| `%s` | %s | %s |" % (
                c, label.get(v["kind"], "未知"),
                (v["usage"] or v["error"] or "")[:80]))
        return "\n".join(lines)

    parts = ["CLI 名称：%s\n可执行名：%s" % (name, bin_name or "(未填)")]
    if doc_text:
        parts.append("【%s（**唯一事实来源**）——来自 %s】\n%s"
                     % (doc_label, doc["url"] or (("本机 " + bin_name) if bin_name else "本机"), doc_text))
    else:
        parts.append("【%s】未提供或获取失败（%s）" % (doc_label, doc["note"] or "无"))
    if missing and doc_text:
        parts.append("【文档未覆盖的命令】以下命令文档里没写，**不要为它们编造能力描述**：%s"
                     % "、".join(missing))
    if help_map:
        head = "【本机实测的真值表（逐条跑过 --help）】"
        tail = ("\n（文档未覆盖的命令，这里补上 help 原文供你判断：\n%s\n）"
                % "\n".join("- `%s`: %s | %s" % (c, help_map[c]["usage"], help_map[c]["desc"])
                            for c in missing if c in help_map)) if missing else ""
        parts.append(head + "\n" + _kind_table() + tail)
    if (help_text or "").strip():
        parts.append("【用户粘贴的帮助文本（文档没覆盖时以它为准）】\n%s" % help_text.strip()[:6000])
    parts.append("【允许出现的命令（只能从这份只读清单里挑，逐字原样）】\n%s" % rules_text)
    parts.append(
        "# 本次的硬规则（严格按上面的证据判断，**不要凭命令名猜**）\n"
        "1. 形态是「需要 ID」或「需要开关参数」的命令**不能直调**，必须写成两步链的第 2 步；"
        "第 1 步用能拿到 ID 的那条命令（文档/help 里说明是解析/列表用途的那条，如 `book resolve`）；\n"
        "2. 形态是「自由文本参数」的命令可以作两步链的第 1 步；「需要开关参数」的命令"
        "（帮助里要求 `--id` 这类取值开关）可以作第 2 步；只有「无参数直调」的命令"
        "（真的什么输入都不需要）**不能**作第 2 步——**不要编造这种链**；\n"
        "3. 两条证据都没有提到的能力，**不要写**（宁可少写一条，也不要编造子命令语义）；\n"
        "4. 只输出规定格式的两行内容（关键词行 + 映射行），不要解释。")
    user_msg = "\n\n".join(parts)

    # ---- 生成
    try:
        yml_path = _PROJECT_ROOT / "prompt" / "prompts.yml"
        cfg = yaml.safe_load(yml_path.read_text(encoding="utf-8"))
        sys_prompt = cfg["ability_writer"]["system_prompt"]
    except Exception as e:
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, f"读取能力描述提示词失败：{e}")
    try:
        from agent.llm import model as default_model
        resp = default_model.invoke(
            [{"role": "system", "content": sys_prompt},
             {"role": "user", "content": user_msg}]
        )
        text = (resp.content or "").strip()
    except Exception as e:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"生成失败：{type(e).__name__}: {e}")
    if not text:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "模型没有返回内容，请重试")

    # ---- 审计（纯代码，零模型）：把「需要 ID 却直调」「凭空造链」直接指出来
    warnings = reg.audit_ability_draft(text, kinds, help_map)
    note = reg.coverage_note(text, cmds)
    if note:
        warnings.append(note + "（可把该命令的 `--help` 文本粘到「帮助文本」里再生成一次）")

    return {
        "content": text,
        "evidence": {
            "docs": {"ok": bool(doc["ok"]), "url": doc["url"], "source": doc["source"],
                     "mode": mode, "label": doc_label,
                     "note": doc["note"], "covered": len(covered), "missing": missing},
            "help": {"ok": help_ok, "total": len(cmds)},
            "commands": len(cmds),
        },
        "warnings": warnings,
    }


# ============================ 能力撰写 AI（API / MCP 工具 · M5c-2'）============================
def capability_ability_generate(kind: str, slug: str, *, account_id: int | None = None,
                                refresh_docs: bool = False) -> dict:
    """按证据为 API/MCP 来源下的**每个工具**生成「工具级描述」草稿。**只生成草稿，不落库。**

    与 CLI 那套（`cli_ability_generate`）同构的证据分层：
      ① **README / 文档**（来源的 `docs` 字段：http 网址或**本地文件路径**）→ 语义唯一事实来源；
      ② **工具清单与参数 schema**（服务自己暴露的）→ 形态真值 + 生成后审计；
      ③ 用户写的**来源级描述** → 用词与语气向它靠拢；
      ④ 都没有 → 保守化（只按工具名与参数写，宁少勿编）。
    用户在前端确认 / 改完，走 `POST /api/tools/sources/cap_abilities` 落库（`abilities_user` 列）。
    """
    import json as _json
    import yaml
    from tools import capability_draft as cdr
    from tools import source_docs
    from api import tools_sources as ts

    if account_id is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "缺少账号")
    src, caps = ts.load_source_and_caps(int(account_id), kind, slug)
    if not caps:
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            "这个来源下还没有工具 —— 先「发现工具」/「导入工具」，再让 AI 写描述")

    cfg = {}
    try:
        cfg = _json.loads(src.get("config_json") or "{}") or {}
    except Exception:  # noqa: BLE001
        cfg = {}
    transport = "本地 stdio（本机子进程）" if (cfg.get("transport") == "stdio" or cfg.get("command")) \
        else "远程 Streamable HTTP 服务"
    # ★ 故意不把 URL / 命令行 / 环境变量写进提示词：MCP 端点常把部署凭证放在路径段里，
    #   命令行可能带 token，env 里就是密钥 —— 这些一律不进模型上下文。
    base_dir = (cfg.get("cwd") or "").strip() or None

    # ---- 证据 ①：README / 文档（http 网址或本地文件路径）；证据 ②：官方介绍文案
    doc = source_docs.resolve(src.get("docs") or "", force=refresh_docs, base_dir=base_dir)
    doc_text = (doc.get("text") or "").strip() if doc.get("ok") else ""
    intro_text = (src.get("intro") or "").strip()
    if not doc_text and not intro_text:
        # ★ 用户 2026-09-27 定：两个信息源都空 → **不猜着写**，直接告诉用户缺什么
        #   （第三方 API/MCP 的 README 常只讲接入；这时贴官方介绍文案才是正解）
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "没有填信息来源，无法 AI 预生成 —— 请在来源配置里填「README / 文档地址」"
            "（远程填网址、本地 stdio 填 README.md 路径），或贴一段「官方介绍文案」（从服务方的"
            "社区页 / 应用市场页整段复制即可）。两者填一个就能预写。")

    # ---- 证据 ③：工具清单（含参数与现有描述）
    listing = cdr.tool_listing(caps)

    # ---- 把（官方文案 + README + 工具清单 + 来源级描述）交给纯函数拼（见 tools/capability_draft.py）
    user_msg, pre_warnings = cdr.build_user_msg(src, caps, doc, intro_text,
                                                listing=listing, transport=transport)
    # ---- 生成
    try:
        yml_path = _PROJECT_ROOT / "prompt" / "prompts.yml"
        cfg_yml = yaml.safe_load(yml_path.read_text(encoding="utf-8"))
        sys_prompt = cfg_yml["capability_writer"]["system_prompt"]
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, f"读取能力撰写提示词失败：{e}")
    try:
        from agent.llm import model as default_model
        resp = default_model.invoke(
            [{"role": "system", "content": sys_prompt},
             {"role": "user", "content": user_msg}]
        )
        text = (resp.content or "").strip()
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"生成失败：{type(e).__name__}: {e}")
    if not text:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "模型没有返回内容，请重试")

    # ---- 解析 + 审计（纯代码，零模型）
    parsed = cdr.parse_draft(text, caps)
    return {
        "items": parsed["items"],
        "evidence": {
            # ★ 两份依据都要回：前端据此提示"这次是哪份撑起来的"（用户反馈的很实在的一点）
            "intro": {"chars": len(intro_text)},
            "docs": {"ok": bool(doc.get("ok")), "url": doc.get("url") or "", "source": doc.get("source") or "",
                     "note": doc.get("note") or "", "chars": len(doc_text)},
            "tools": {"total": len(caps), "described": len(parsed["items"])},
        },
        "warnings": list(pre_warnings) + list(parsed["warnings"]),
    }


# ============================ 人格生成 AI ============================
def soul_generate(req_text: str) -> dict:
    """人格撰写 AI：按用户需求（如「活泼女仆」）生成一段 SOUL.md 人格提示词。

    独立于 main_agent / sub_agents 体系：不挂工具、不参与情报检索，
    仅用 prompts.yml 顶层 soul_writer 段的 system_prompt 驱动默认模型。
    """
    import yaml
    from langchain_core.messages import HumanMessage
    from agent.llm import model as default_model

    if not req_text or not req_text.strip():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "请描述你想要的助手人格，如「活泼女仆」")
    # 直接读 prompts.yml（不依赖 prompt.prompts 包导入，规避包路径污染）
    try:
        yml_path = _PROJECT_ROOT / "prompt" / "prompts.yml"
        cfg = yaml.safe_load(yml_path.read_text(encoding="utf-8"))
        sys_prompt = cfg["soul_writer"]["system_prompt"]
    except Exception as e:
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, f"读取人格提示词失败：{e}")
    try:
        resp = default_model.invoke(
            [{"role": "system", "content": sys_prompt},
             {"role": "user", "content": f"请为「{req_text.strip()}」风格设计助手人格。"}]
        )
        text = (resp.content or "").strip()
        if not text:
            raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "生成失败：模型无输出")
        return {"ok": True, "content": text}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, f"生成失败：{type(e).__name__}: {e}")
# ============================ 多人格集（SOUL/ 子目录）============================
def soul_list(token: str) -> dict:
    """列出该账号所有人格文件（默认 SOUL.md + SOUL/{name}.md），标记当前激活"""
    user = _username_by_token(token)
    active = get_active_soul_name(user)
    items = []
    # 默认人格
    items.append({"name": "默认人格", "file": "SOUL.md",
                  "has_content": _user_soul_path(user).exists(),
                  "active": active == "SOUL.md"})
    # 多人格目录
    for f in sorted(_user_soul_dir(user).glob("*.md")):
        items.append({"name": f.stem, "file": f"SOUL/{f.name}",
                      "has_content": True, "active": active == f"SOUL/{f.name}"})
    return {"souls": items, "active": active}


class SoulNameReq(BaseModel):
    name: str          # 人格名（不含扩展名），如 稳重管家
    content: str = ""  # 内容（创建/保存用）


class SoulActiveReq(BaseModel):
    file: str  # 要激活的人格文件相对路径，如 SOUL.md 或 SOUL/稳重管家.md


def soul_create(token: str, req: SoulNameReq) -> dict:
    """新建一个人格文件 SOUL/{name}.md（name=default 时写默认 SOUL.md）"""
    user = _username_by_token(token)
    name = req.name.strip()
    if not name:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "人格名不能为空")
    if len(req.content) > 8000:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "人格内容过长（≤8000字）")
    if name.lower() == "default":
        p = _user_soul_path(user)
    else:
        p = _soul_abs_path(user, name)
    if p.exists():
        raise HTTPException(status.HTTP_409_CONFLICT, f"人格「{name}」已存在")
    p.write_text(req.content, encoding="utf-8")
    return {"ok": True, "file": f"SOUL.md" if name.lower() == "default" else f"SOUL/{name}.md"}


def soul_delete(token: str, name: str) -> dict:
    """删除一个人格文件（默认人格除外；激活中的人格先切换到默认再删）"""
    user = _username_by_token(token)
    if name in ("", "default", "SOUL.md"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "默认人格不可删除，可将其内容清空")
    p = _soul_abs_path(user, name)
    if not p.exists():
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"人格「{name}」不存在")
    active = get_active_soul_name(user)
    if active == f"SOUL/{name}.md":
        # 正在激活：先切回默认，避免悬空
        _active_soul_file(user).write_text("SOUL.md", encoding="utf-8")
    p.unlink()
    return {"ok": True}


def soul_set_active(token: str, file: str) -> dict:
    """激活某个人格文件（写 ACTIVE_SOUL）"""
    user = _username_by_token(token)
    file = os.path.basename(file) if file.startswith("SOUL/") else file
    if file == "SOUL.md":
        _active_soul_file(user).write_text("SOUL.md", encoding="utf-8")
        return {"ok": True, "active": "SOUL.md"}
    if file.endswith(".md"):
        file = file[:-3]
    p = _soul_abs_path(user, file)
    if not p.exists():
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"人格「{file}」不存在")
    _active_soul_file(user).write_text(f"SOUL/{file}.md", encoding="utf-8")
    return {"ok": True, "active": f"SOUL/{file}.md"}


def soul_get_named(token: str, name: str) -> dict:
    """读取某个人格文件内容（name=default 读 SOUL.md）"""
    user = _username_by_token(token)
    if name.lower() == "default":
        p = _user_soul_path(user)
    else:
        p = _soul_abs_path(user, name)
    return {"name": name, "content": p.read_text(encoding="utf-8") if p.exists() else ""}


def soul_save_named(token: str, req: SoulNameReq) -> dict:
    """保存某个人格文件内容（name=default 写 SOUL.md）"""
    user = _username_by_token(token)
    if len(req.content) > 8000:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "人格内容过长（≤8000字）")
    name = req.name.strip()
    if name.lower() == "default":
        _user_soul_path(user).write_text(req.content, encoding="utf-8")
        return {"ok": True, "file": "SOUL.md"}
    p = _soul_abs_path(user, name)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(req.content, encoding="utf-8")
    return {"ok": True, "file": f"SOUL/{name}.md"}


def get_soul_content(aid: int) -> str:
    """按账号读取当前激活人格的内容（供 server 注入 system_prompt）"""
    ensure_tables()
    conn = get_personal_conn()
    try:
        row = conn.execute("SELECT username FROM accounts WHERE id=?", (aid,)).fetchone()
        if not row:
            return ""
    finally:
        conn.close()
    user = row["username"]
    active = get_active_soul_name(user)
    if active == "SOUL.md":
        p = _user_soul_path(user)
    else:
        p = _soul_abs_path(user, active[len("SOUL/"):-3] if active.startswith("SOUL/") else active)
    return p.read_text(encoding="utf-8").strip() if p.exists() else ""


# ============================ 技能 SKILL.md（M4a）============================
# 技能 = 一段可复用的任务说明书（Markdown），存在 agents_docs/{username}/skills/{name}.md；
# 用户在输入框用 / 唤出、可多选叠加，作为 user message 前缀注入本轮（一次性，发完即清）。
SKILL_MAX_LEN = 4000   # 单个技能正文字数上限（前后端一致硬校验）
SKILL_NAME_MAX = 30    # 技能名长度上限
SKILL_MAX_PICK = 5     # 单轮最多叠加技能数（防提示词爆炸）
# Windows 保留设备名：即使带扩展名（nul.md）仍被系统当设备，写入会静默失败
_SKILL_RESERVED = {
    "con", "prn", "aux", "nul",
    *(f"com{i}" for i in range(1, 10)),
    *(f"lpt{i}" for i in range(1, 10)),
}


def _user_skill_dir(username: str) -> Path:
    """技能目录：agents_docs/{username}/skills/"""
    d = _user_doc_dir(username) / "skills"
    d.mkdir(parents=True, exist_ok=True)
    return d


def skill_safe_name(name: str) -> str:
    """技能名 → 安全文件名（不含扩展名）；非法直接抛 400。

    注意：只做 basename 不足以防穿越（'..' 也能通过 basename），
    故叠加「. / .. 黑名单 + 字符白名单 + Windows 保留名」三层校验。
    """
    raw = (name or "").strip()
    if raw.lower().endswith(".md"):
        raw = raw[:-3]
    raw = os.path.basename(raw.replace("\\", "/")).strip()
    if not raw or raw in (".", ".."):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "技能名不能为空，也不能是 . 或 ..")
    if len(raw) > SKILL_NAME_MAX:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"技能名过长（≤{SKILL_NAME_MAX} 字）")
    if raw.lower() in _SKILL_RESERVED:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"「{raw}」是系统保留名，请换一个")
    safe = re.sub(r"[^\w.\-]", "_", raw)   # \w 在 Python3 str 下含中文，中文技能名可保留
    safe = safe.strip("._") or "skill"
    if safe.lower() in _SKILL_RESERVED:    # 清洗后再次命中保留名（如 "nul!" → "nul"）
        safe = f"skill_{safe}"
    return safe


def _skill_path(username: str, name: str) -> Path:
    return _user_skill_dir(username) / f"{skill_safe_name(name)}.md"


class SkillReq(BaseModel):
    name: str
    content: str = ""


def _skill_check_content(content: str) -> str:
    content = content or ""
    if len(content) > SKILL_MAX_LEN:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"技能内容过长（≤{SKILL_MAX_LEN} 字）")
    return content


def _skill_desc(content: str) -> str:
    """取正文首个非空行（去 # 标题符）作为列表简介"""
    for line in (content or "").splitlines():
        s = line.strip().lstrip("#").strip()
        if s:
            return s[:60]
    return ""


def skills_list(token: str) -> dict:
    """列出该账号全部技能（含简介/字数/更新时间）"""
    user = _username_by_token(token)
    items = []
    for f in sorted(_user_skill_dir(user).glob("*.md")):
        try:
            content = f.read_text(encoding="utf-8")
        except Exception:
            content = ""
        items.append({
            "name": f.stem,
            "chars": len(content),
            "desc": _skill_desc(content),
            "updated": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(f.stat().st_mtime)),
        })
    return {"skills": items}


def skill_get(token: str, name: str) -> dict:
    """读取单个技能正文"""
    user = _username_by_token(token)
    p = _skill_path(user, name)
    return {"name": p.stem, "content": p.read_text(encoding="utf-8") if p.exists() else ""}


def skill_create(token: str, req: SkillReq) -> dict:
    """新建技能（重名 409，不覆盖）"""
    user = _username_by_token(token)
    content = _skill_check_content(req.content)
    p = _skill_path(user, req.name)
    if p.exists():
        raise HTTPException(status.HTTP_409_CONFLICT, f"技能「{p.stem}」已存在")
    p.write_text(content, encoding="utf-8")
    return {"ok": True, "name": p.stem}


def skill_save(token: str, req: SkillReq) -> dict:
    """保存技能正文（不存在则创建）"""
    user = _username_by_token(token)
    content = _skill_check_content(req.content)
    p = _skill_path(user, req.name)
    p.write_text(content, encoding="utf-8")
    return {"ok": True, "name": p.stem}


def skill_delete(token: str, name: str) -> dict:
    user = _username_by_token(token)
    p = _skill_path(user, name)
    if not p.exists():
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"技能「{name}」不存在")
    p.unlink()
    return {"ok": True}


def get_skills_text(aid: int, names: list[str] | None) -> str:
    """按账号 + 技能名列表读取正文，拼成注入 user message 的**前缀文本**。

    供 server 层调用后经 build_context 装配（本模块不 import agent.*，
    保持「装配管线不反向依赖 API 层」的分层）。
    单个技能缺失/非法名静默跳过——不让一个坏技能拖垮整轮对话。
    """
    picked = [n for n in (names or []) if (n or "").strip()][:SKILL_MAX_PICK]
    if not picked:
        return ""
    ensure_tables()
    conn = get_personal_conn()
    try:
        row = conn.execute("SELECT username FROM accounts WHERE id=?", (aid,)).fetchone()
    finally:
        conn.close()
    if not row:
        return ""
    user = row["username"]
    parts: list[str] = []
    for n in picked:
        try:
            p = _skill_path(user, n)
        except HTTPException:
            continue
        if p.exists():
            parts.append(f"### 技能：{p.stem}\n{p.read_text(encoding='utf-8').strip()}")
    if not parts:
        return ""
    return (
        "【本轮启用技能】用户为本轮对话显式选择了以下技能说明书，"
        "请严格按其中的步骤与口径执行；与默认回答风格冲突时以技能为准。\n\n"
        + "\n\n".join(parts)
    )


# ============================ 记忆画像 MEMORY.md ============================
def _user_memory_path(username: str) -> Path:
    # M4-8：路径的唯一事实源在 tools/user_doc_paths.py（本函数只做 Path 包装，保持旧调用点可用）
    from tools import user_doc_paths as _paths
    return Path(_paths.memory_path(username))


def ensure_user_memory(username: str):
    """账号创建时调用：生成空 MEMORY.md（已存在则跳过，幂等）"""
    p = _user_memory_path(username)
    if not p.exists():
        from tools import user_doc_paths as _paths
        _paths.ensure_user_dir(username)          # M4-8：落盘前显式建目录（原来是旧 _user_doc_dir 顺手建的）
        p.write_text(MEMORY_TEMPLATE, encoding="utf-8")


def _memory_initialized(content: str) -> bool:
    """是否已初始化。★ M3-1 起**两种标记都认**：

    · 老标记 `<!-- memory_init: done -->`（现有文件、`memory_save` 还在维护它）；
    · 新标记 `<!-- memory_meta: … -->`（M3 的写路径会写它，与老标记并存）。
    只认老标记的话，一个"只有新标记"的文件会被判成未初始化 → 前端又放出「✨ 初始化用户画像」，
    用户再点一次就会把已有画像整段覆盖（虽有快照兜底，但这是不该发生的误判）。
    """
    text = content or ""
    if "memory_init: done" in text:
        return True
    from tools import memory_profile as mprof
    return bool(mprof.parse_meta(text).get("has_meta"))


def memory_get(token: str) -> dict:
    """读取该账号 MEMORY.md"""
    user = _username_by_token(token)
    ensure_user_memory(user)
    p = _user_memory_path(user)
    content = p.read_text(encoding="utf-8")
    return {"content": content, "initialized": _memory_initialized(content)}


class MemoryReq(BaseModel):
    content: str


def memory_save(token: str, req: MemoryReq) -> dict:
    """用户手动编辑保存 MEMORY.md（保留 initialized 标记状态）"""
    user = _username_by_token(token)
    if len(req.content) > 8000:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "记忆内容过长（≤8000字）")
    ensure_user_memory(user)
    p = _user_memory_path(user)
    # 若用户编辑时把标记弄丢了，从旧内容里找回并保留在文件头
    old = p.read_text(encoding="utf-8") if p.exists() else ""
    # ★ M3-9（D9）：人工保存属于"四条写路径"之一 → **改动之前**先留一份快照（内容真变了才留）
    if req.content.strip() != old.strip():
        from tools import memory_snapshots as ms
        ms.snapshot(_require_account_id(token), old, ms.REASON_MANUAL_EDIT)
    new_content = req.content
    if _memory_initialized(old) and "memory_init: done" not in new_content:
        new_content = new_content.replace("<!-- memory_init: pending -->", "<!-- memory_init: done -->")
        if "memory_init:" not in new_content:
            new_content = new_content.replace("# 用户记忆画像（MEMORY）",
                                              "# 用户记忆画像（MEMORY）\n\n<!-- memory_init: done -->")
    p.write_text(new_content, encoding="utf-8")
    return {"ok": True, "initialized": _memory_initialized(new_content)}


def memory_initialize(token: str) -> dict:
    """一次性初始化画像：AI 根据该账号系统已有信息生成初始 USER PROFILE。

    仅当 MEMORY.md 尚未初始化（pending）时可调用；已初始化则拒绝（幂等）。
    """
    user = _username_by_token(token)
    ensure_user_memory(user)
    p = _user_memory_path(user)
    old = p.read_text(encoding="utf-8")
    if _memory_initialized(old):
        raise HTTPException(status.HTTP_409_CONFLICT, "画像已初始化（仅可初始化一次）；如需调整请直接编辑或让智能体在对话中更新")

    # 收集该账号系统已有信息（★ M3-2 起改用共用收集器 `tools/profile_evidence.py`，
    #   初始化器与建议器共用同一套表口径 —— 两处各写一份必然漂移）
    aid = _require_account_id(token)
    from tools import profile_evidence as pev
    profile_lines = pev.table_lines(pev.collect_tables(aid))

    data_block = "\n".join(profile_lines) if profile_lines else "（该账号暂无任何资料，画像将保持基础状态）"

    import yaml
    from agent.llm import model as default_model
    try:
        yml_path = _PROJECT_ROOT / "prompt" / "prompts.yml"
        cfg = yaml.safe_load(yml_path.read_text(encoding="utf-8"))
        sys_prompt = cfg["memory_initializer"]["system_prompt"]
    except Exception as e:
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, f"读取画像提示词失败：{e}")

    try:
        resp = default_model.invoke(
            [{"role": "system", "content": sys_prompt},
             {"role": "user", "content": f"以下是该用户的系统资料：\n{data_block}"}]
        )
        text = (resp.content or "").strip()
        if not text:
            raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "生成失败：模型无输出")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, f"生成失败：{type(e).__name__}: {e}")

    # ★ M3-9（D9）：初始化也是写路径 → 改动之前先留一份（老文件里那些内容也值得可还原）
    from tools import memory_profile as mprof
    from tools import memory_snapshots as ms
    from tools import profile_evidence as pev
    ms.snapshot(aid, old, ms.REASON_INIT)

    # 组装新 MEMORY.md：保留模板头 + 标记 done + 生成的画像 + 空笔记段；
    # 同时补上**新元数据行**（`memory_meta`，与老 `memory_init` 并存 —— 方案 §4.4）。
    today = mprof.today()
    head = ("# 用户记忆画像（MEMORY）\n\n"
            "> 智能体会在对话中自行判断是否需要更新本文件；你也可以在「定制助手」页直接编辑。\n\n"
            "<!-- memory_init: done -->\n\n"
            + mprof.PROFILE_HEAD)
    new_content = mprof.render_file(head, text, pev.refresh_meta(aid, len(profile_lines)))
    p.write_text(new_content, encoding="utf-8")
    return {"ok": True, "content": new_content, "initialized": True,
            "meta": mprof.parse_meta(new_content)}


# ============================ M3-9：历史画像与一键还原（D9）============================
class MemoryRestoreReq(BaseModel):
    snapshot_id: int


def memory_snapshots(token: str) -> dict:
    """最近 3 份历史画像（新的在前）+ 当前元信息（前端「🕘 历史画像」用）。"""
    from tools import memory_profile as mprof
    from tools import memory_snapshots as ms
    aid = _require_account_id(token)
    user = _username_by_token(token)
    ensure_user_memory(user)
    content = _user_memory_path(user).read_text(encoding="utf-8")
    meta = mprof.parse_meta(content)
    items = ms.list_snapshots(aid)
    for it in items:                    # 展示用字段（前端不拼时间格式）
        it.setdefault("created_at_text", it.get("created_at") or "")
    return {
        "items": items,
        "snapshots": items,             # ★ 兼容键：前端历史版本列表读的是 `snapshots`（2026-09-27 实测修）
        "keep": ms.KEEP_LATEST,
        "meta": {"init": meta["init"], "refreshed": meta["refreshed"],
                 "sources": meta["sources"], "initialized": meta["initialized"]},
    }


def memory_restore(token: str, req: MemoryRestoreReq) -> dict:
    """还原到某份历史画像。

    ★ 还原自己也是写路径 → **先把当前版本快照**（reason=restore）→ 可以来回切、不会丢东西。
    画像段被替换成历史版本，**笔记段与文件头原样保留**；`refreshed` 更新为今天。
    """
    from tools import memory_profile as mprof
    from tools import memory_snapshots as ms
    aid = _require_account_id(token)
    user = _username_by_token(token)
    ensure_user_memory(user)
    p = _user_memory_path(user)
    cur = p.read_text(encoding="utf-8")
    snap = ms.get_snapshot(aid, int(req.snapshot_id or 0))
    if not snap:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "这份历史画像不存在（或不属于当前账号）")
    ms.snapshot(aid, cur, ms.REASON_RESTORE)          # 先存当前 → 可来回切
    meta = mprof.parse_meta(cur)
    from tools import profile_evidence as pev
    new_content = mprof.render_file(cur, snap["content"],
                                    pev.refresh_meta(aid, len(mprof.parse_rows(snap["content"])), meta))
    p.write_text(new_content, encoding="utf-8")
    return {"ok": True, "content": new_content, "initialized": True,
            "restored_from": snap["id"], "snapshot_of": snap["created_at"],
            "meta": mprof.parse_meta(new_content)}

# ============================ M3-3：画像建议器（只出建议，不落盘）============================
def _profile_suggestions(cur: list, proposed: list) -> list:
    """比对「当前画像行」与「AI 给出的目标行」→ 建议列表（``＋新增 / ～更新 / −移除``）。

    · 移除**只针对占位行**（R3 迁移）：老文件里的「暂无」以 `−` 呈现给人工确认，绝不静默删；
      证据没提到的真实条目一律**保留**（方案 §5.6：`−` 默认不勾）；
    · 人工行（来源=人工）的更新建议会带 `manual=True`，前端默认不勾（D8 的 `protect_manual`）；
    · 值没变的条目不进列表（免得界面上一堆"没变化"）。
    """
    from tools import memory_profile as mprof
    out: list[dict] = []
    cur_by_norm = {(r.get("norm") or ""): r for r in cur}
    seen: set[str] = set()
    for p in proposed:
        norm = p.get("norm") or ""
        if not norm:
            continue
        seen.add(norm)
        old_row = cur_by_norm.get(norm)
        base = {"field": p.get("field") or "", "new": p.get("fact") or "",
                "source": p.get("source") or "", "date": p.get("date") or "",
                "flagged": bool(p.get("flagged")) or (p.get("field_status") == "new")}
        if not old_row:
            out.append(dict(base, op="add", old="", manual=False))
            continue
        if (old_row.get("fact") or "").strip() == (p.get("fact") or "").strip():
            continue                                   # 值没变 → 不进列表
        out.append(dict(base, op="update", old=old_row.get("fact") or "",
                        manual=bool(old_row.get("manual"))))
    for r in cur:
        if (r.get("norm") or "") in seen:
            continue
        if mprof.has_placeholder(r.get("fact") or ""):
            out.append({"op": "remove", "field": r.get("field") or "", "old": r.get("fact") or "",
                        "new": "", "source": "", "date": r.get("date") or "",
                        "manual": bool(r.get("manual")), "flagged": False})
    return out


def memory_suggest(token: str) -> dict:
    """让人工画像的 AI 给**修订建议**（D2-②）。★ **只返回建议，绝不写文件** —— 落盘走 `/apply` 或 `/import`。

    证据 = 7 张结构化表（带 `兴趣#3` 这类可溯源引用）+ 最近 3 篇周报要点 + 最近 N 条用户消息（D3）。
    存在意义：补上"数据库事实与周报主题永远不会在对话里被表达"这一路（根因 R1）。
    """
    import yaml
    from tools import memory_profile as mprof
    from tools import profile_evidence as pev

    aid = _require_account_id(token)
    user = _username_by_token(token)
    ensure_user_memory(user)
    p = _user_memory_path(user)
    content = p.read_text(encoding="utf-8")
    cur_rows = mprof.parse_rows(mprof.split_sections(content)["profile"])

    ev = pev.collect(aid)
    if not (ev["table_lines"] or ev["reports"] or ev["sessions"]) and not cur_rows:
        # ★ 证据不足时**不猜**（方案 §六 M3-3 的判据）：直接说清楚，别让模型编
        return {"suggestions": [], "evidence": ev["counts"], "warnings": [],
                "note": "这个账号既没有结构化资料、也没有周报与会话记录，画像也是空的 —— "
                        "先积累一些数据（兴趣/收藏/关注，或聊几轮）再来让 AI 提建议。"}

    parts = []
    parts.append("【用户当前画像】\n%s" % (mprof.render_rows(cur_rows) if cur_rows else "（还没有画像内容）"))
    parts.append("【证据】\n%s" % pev.render_text(ev))
    parts.append("# 本次的硬规则\n"
                 "1. 只输出条目列表（每行 `- 维度：事实（来源：…）`），不要 diff 符号、不要解释、不要开场白；\n"
                 "2. **证据里没提到的旧条目必须原样保留**（不许擅自删）；\n"
                 "3. 证据支持的新维度才写；维度名不在清单内时在维度名前加 ⚠️；\n"
                 "4. 来源必须写（数据库行用它的引用如 `关注#7`、周报写 `周报`、对话写 `会话`、用户自己写的写 `人工`）；\n"
                 "5. 不要写「暂无 / 待补充」这类占位行。")
    user_msg = "\n\n".join(parts)

    try:
        yml_path = _PROJECT_ROOT / "prompt" / "prompts.yml"
        cfg = yaml.safe_load(yml_path.read_text(encoding="utf-8"))
        sys_prompt = cfg["memory_suggester"]["system_prompt"]
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, f"读取画像建议提示词失败：{e}")
    try:
        from agent.llm import model as default_model
        resp = default_model.invoke([{"role": "system", "content": sys_prompt},
                                     {"role": "user", "content": user_msg}])
        text = (resp.content or "").strip()
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"生成建议失败：{type(e).__name__}: {e}")
    if not text:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "模型没有返回内容，请重试")

    proposed = mprof.parse_rows(text)
    warnings: list[str] = []
    if not proposed:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY,
                            "模型没有给出可解析的条目（期望每行 `- 维度：事实（来源：…）`），请重试")
    dropped = [t for t in text.split("\n") if t.strip().startswith("-") and mprof.has_placeholder(t)]
    if dropped:
        warnings.append("模型输出了 %d 行占位内容，已忽略（缺失的维度不该写占位）" % len(dropped))
    proposed = [r for r in proposed if not mprof.has_placeholder(r.get("fact") or "")]
    sugg = _profile_suggestions(cur_rows, proposed)
    new_dims = [s["field"] for s in sugg if s.get("flagged")]
    if new_dims:
        warnings.append("有 %d 个维度不在白名单内（默认不勾选，确认无误再启用）：%s"
                        % (len(new_dims), "、".join(new_dims[:6])))
    return {"suggestions": sugg, "evidence": ev["counts"], "warnings": warnings,
            "raw": text, "profile_rows": len(cur_rows)}

def _apply_items(rows: list, items: list, protect_manual: bool) -> tuple[list, list, list]:
    """把一批建议作用到画像行上（**纯逻辑**，apply 与 import 共用，避免两条路分叉）。

    返回 `(rows, applied, skipped)`。规则：
      · `add|update` 走 `upsert`（按维度**就地更新**，同一维度只有一行）；
      · `remove` 仅当勾中、且不是受保护的人工行；
      · 未知操作 / 空值 / 人工行 → 进 `skipped` 并写明原因（界面要显示给用户）。
    """
    from tools import memory_profile as mprof
    applied: list[dict] = []
    skipped: list[dict] = []
    for it in (items or []):
        it = it or {}
        op = (it.get("op") or "add").strip().lower()
        field = (it.get("field") or "").strip()
        if op == "remove":
            norm = mprof._norm_field(field)
            hit = next((r for r in rows if r.get("norm") == norm), None)
            if not hit:
                skipped.append({"op": op, "field": field, "why": "画像里已经没有这个维度了"})
            elif hit.get("manual") and protect_manual:
                skipped.append({"op": op, "field": field, "why": "人工行受保护（可关闭保护后再删）"})
            else:
                rows = [r for r in rows if r is not hit]
                applied.append({"op": op, "field": field})
            continue
        if op not in ("add", "update"):
            skipped.append({"op": op, "field": field, "why": "未知操作（只支持 add/update/remove）"})
            continue
        rows, act = mprof.upsert(rows, field, it.get("new") or "", source=it.get("source") or "",
                                 date=it.get("date") or mprof.today(), protect_manual=protect_manual)
        if act == "skip_manual":
            skipped.append({"op": op, "field": field, "why": "人工行受保护（默认不覆盖，可关闭保护）"})
        elif act == "empty":
            skipped.append({"op": op, "field": field, "why": "新值为空"})
        else:
            applied.append({"op": op, "field": field, "action": act})
    return rows, applied, skipped


# ============================ M3-4：应用建议（就地更新 / 保人工行 / 改前快照）============================
class MemoryApplyReq(BaseModel):
    items: list[dict] = []               # 前端勾中的建议条目（形状同 /suggest 的 suggestions）
    protect_manual: bool = True          # ★ 默认保护人工行（D8 的开关）


def memory_apply(token: str, req: MemoryApplyReq) -> dict:
    """把勾选的建议**落到画像文件**。这是四条写路径里唯一"按维度就地更新"的一条（修根因 R2）。

    口径：
      · `op=add|update` → 按维度 `upsert`（**同一维度只有一行**，值取最新）；
      · `op=remove` → 仅当该行被勾中才删；人工行在 `protect_manual` 下**不动**；
      · **改前必快照**（D9）；一条都没应用时**不写文件、不留快照**；
      · 写回只替换画像段：文件头与**笔记段原样保留**；`memory_meta` 的 `refreshed` 更新为今天。
    """
    from tools import memory_profile as mprof
    from tools import memory_snapshots as ms
    aid = _require_account_id(token)
    user = _username_by_token(token)
    ensure_user_memory(user)
    p = _user_memory_path(user)
    cur = p.read_text(encoding="utf-8")
    rows = mprof.parse_rows(mprof.split_sections(cur)["profile"], dedupe=True)

    rows, applied, skipped = _apply_items(rows, req.items, req.protect_manual)

    if not applied:
        return {"ok": True, "wrote": False, "applied": [], "skipped": skipped,
                "meta": mprof.parse_meta(cur)}

    ms.snapshot(aid, cur, ms.REASON_SUGGEST_IMPORT)      # ★ 改之前先留档（D9）
    meta = mprof.parse_meta(cur)
    from tools import profile_evidence as pev
    new_content = mprof.render_file(cur, rows, pev.refresh_meta(aid, len(rows), meta))
    p.write_text(new_content, encoding="utf-8")
    return {"ok": True, "wrote": True, "applied": applied, "skipped": skipped,
            "content": new_content, "meta": mprof.parse_meta(new_content),
            "rows": len(rows)}


# ============================ M3-10：一键导入整份建议（D8）============================
class MemoryImportReq(BaseModel):
    items: list[dict] = []               # AI 建议的**整份**（= 全选）
    protect_manual: bool = True          # ★ 默认保护人工行（D8 的默认值）
    confirm: bool = False                # ★ 必须显式确认（整份覆盖的破坏面最大）


def memory_import(token: str, req: MemoryImportReq) -> dict:
    """一键导入「AI 建议画像」= 全选应用（D8）。与 `/apply` 共用内核，差别在**整份 + 需确认 + 报计数**。

    为什么要独立端点（而不是让前端"全选后再调 apply"）：
      · **必须显式 `confirm`** —— 整份覆盖破坏面最大，不能让一次误点就落盘；
      · 返回**确认面板要用的计数**（将更新几个维度、其中几个是人工行），前端据此弹确认框（§5.6）；
      · 与 `/apply` 共用 `_apply_items` → "保人工行 / 就地更新 / 改前快照"三件事不会分叉。
    """
    from tools import memory_profile as mprof
    from tools import memory_snapshots as ms
    if not req.confirm:
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            "一键导入需要显式确认（confirm=true）—— 这会整份应用建议，请先看过差异")
    aid = _require_account_id(token)
    user = _username_by_token(token)
    ensure_user_memory(user)
    p = _user_memory_path(user)
    cur = p.read_text(encoding="utf-8")
    rows = mprof.parse_rows(mprof.split_sections(cur)["profile"], dedupe=True)

    manual_hit = sum(1 for it in (req.items or [])
                     if (it or {}).get("op") in ("add", "update") and bool((it or {}).get("manual")))
    rows, applied, skipped = _apply_items(rows, req.items, req.protect_manual)
    if not applied:
        return {"ok": True, "wrote": False, "applied": [], "skipped": skipped,
                "dimensions": 0, "manual_kept": manual_hit, "imported": len(req.items or []),
                "meta": mprof.parse_meta(cur)}

    ms.snapshot(aid, cur, ms.REASON_SUGGEST_IMPORT)      # ★ 改之前先留档（D9）
    meta = mprof.parse_meta(cur)
    from tools import profile_evidence as pev
    new_content = mprof.render_file(cur, rows, pev.refresh_meta(aid, len(rows), meta))
    p.write_text(new_content, encoding="utf-8")
    return {"ok": True, "wrote": True, "applied": applied, "skipped": skipped,
            "dimensions": len(applied), "manual_kept": manual_hit, "imported": len(req.items or []),
            "content": new_content, "meta": mprof.parse_meta(new_content), "rows": len(rows)}
