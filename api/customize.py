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


def _mask_key(key: str) -> str:
    """api_key 掩码：只显后 4 位（信息安全；业务信息不打码）"""
    key = (key or "").strip()
    if not key:
        return ""
    if len(key) <= 4:
        return "****" + key
    return "****" + key[-4:]


def _normalize_provider(p: dict) -> dict:
    return {
        "id": p["id"],
        "provider_name": p["provider_name"],
        "model_name": p["model_name"],
        "base_url": p["base_url"],
        "api_key": _mask_key(p["api_key"]),
        "is_active": bool(p["is_active"]),
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
        cur = conn.execute(
            "INSERT INTO llm_providers (owner_id, provider_name, model_name, base_url, api_key, is_active) "
            "VALUES (?,?,?,?,?,?)",
            (aid, req.provider_name.strip(), req.model_name.strip(),
             req.base_url.strip(), req.api_key.strip(), int(req.is_active)))
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
        if not new_key or new_key.startswith("****"):
            # 留空 / 掩码串 = 不修改 key（保留原值）
            conn.execute(
                "UPDATE llm_providers SET provider_name=?, model_name=?, base_url=?, is_active=? "
                "WHERE id=? AND owner_id=?",
                (req.provider_name.strip(), req.model_name.strip(), req.base_url.strip(),
                 int(req.is_active), pid, aid))
        else:
            conn.execute(
                "UPDATE llm_providers SET provider_name=?, model_name=?, base_url=?, api_key=?, is_active=? "
                "WHERE id=? AND owner_id=?",
                (req.provider_name.strip(), req.model_name.strip(), req.base_url.strip(),
                 new_key, int(req.is_active), pid, aid))
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
    aid = _require_account_id(token)
    ensure_tables()
    conn = get_personal_conn()
    try:
        own = conn.execute("SELECT id FROM llm_providers WHERE id=? AND owner_id=?", (pid, aid)).fetchone()
        if not own:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "配置不存在")
        conn.execute("UPDATE llm_providers SET is_active=0 WHERE owner_id=?", (aid,))
        conn.execute("UPDATE llm_providers SET is_active=1 WHERE id=? AND owner_id=?", (pid, aid))
        conn.commit()
        return {"ok": True}
    finally:
        conn.close()


def get_active_provider(aid: int) -> Optional[dict]:
    """查询某账号启用的模型配置（无则 None = 用默认 .env 模型）"""
    ensure_tables()
    conn = get_personal_conn()
    try:
        row = conn.execute(
            "SELECT * FROM llm_providers WHERE owner_id=? AND is_active=1 LIMIT 1", (aid,)).fetchone()
        return _normalize_provider({k: row[k] for k in row.keys()}) if row else None
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


# ============================ 记忆画像 MEMORY.md ============================
def _user_memory_path(username: str) -> Path:
    return _user_doc_dir(username) / "MEMORY.md"


def ensure_user_memory(username: str):
    """账号创建时调用：生成空 MEMORY.md（已存在则跳过，幂等）"""
    p = _user_memory_path(username)
    if not p.exists():
        p.write_text(MEMORY_TEMPLATE, encoding="utf-8")


def _memory_initialized(content: str) -> bool:
    return "memory_init: done" in (content or "")


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

    # 收集该账号系统已有信息
    aid = _require_account_id(token)
    ensure_tables()
    conn = get_personal_conn()
    try:
        interests = conn.execute("SELECT interest_tag, description FROM interests WHERE owner_id=?", (aid,)).fetchall()
        watchlist = conn.execute("SELECT brand, product, note FROM watchlist WHERE owner_id=?", (aid,)).fetchall()
        products = conn.execute("SELECT product_name, brand, category, price FROM products WHERE owner_id=?", (aid,)).fetchall()
        comp_profile = conn.execute("SELECT * FROM company_profile WHERE owner_id=?", (aid,)).fetchall()
        comp_products = conn.execute("SELECT product_name, category, price FROM company_products WHERE owner_id=?", (aid,)).fetchall()
        competitors = conn.execute("SELECT comp_name, category, note FROM company_competitors WHERE owner_id=?", (aid,)).fetchall()
        subs = conn.execute("SELECT name, keywords FROM digest_subs WHERE owner_id=?", (aid,)).fetchall()
    finally:
        conn.close()

    profile_lines = []
    for r in interests:
        profile_lines.append(f"兴趣：{r['interest_tag']}" + (f"（{r['description']}）" if r['description'] else ""))
    for r in watchlist:
        profile_lines.append(f"关注：{r['brand']} {r['product'] or ''}".strip() + (f"（{r['note']}）" if r['note'] else ""))
    for r in products:
        price = f"，价格 {r['price']}" if r['price'] else ""
        profile_lines.append(f"收藏：{r['brand'] or ''}{r['product_name']}（{r['category'] or '未分类'}）{price}")
    for r in comp_profile:
        profile_lines.append(f"公司：{dict(r).get('company_name') or '（未填写）'}")
    for r in comp_products:
        profile_lines.append(f"公司产品：{r['product_name']}（{r['category'] or '未分类'}）")
    for r in competitors:
        profile_lines.append(f"竞品：{r['comp_name']}" + (f"（{r['category']}）" if r['category'] else ""))
    for r in subs:
        profile_lines.append(f"订阅：{r['name']}")

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

    # 组装新 MEMORY.md：保留模板头 + 标记 done + 生成的画像 + 空笔记段
    new_content = (
        "# 用户记忆画像（MEMORY）\n\n"
        "> 智能体会在对话中自行判断是否需要更新本文件；你也可以在「定制助手」页直接编辑。\n\n"
        "<!-- memory_init: done -->\n\n"
        "## USER PROFILE（用户画像）\n\n"
        f"{text}\n\n"
        "## MEMORY（助手笔记）\n\n"
        "（Agent 在对话中自行维护：你的长期偏好、未决问题、重要事实等。）\n"
    )
    p.write_text(new_content, encoding="utf-8")
    return {"ok": True, "content": new_content, "initialized": True}
