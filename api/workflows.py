"""06 · workflows：步骤固定的流程定义（存放 + 校验 + 接口层）。

定案见 `Verdict/06-…md` 裁定 8，方案见规划文档 §06 ③ I。

- **存放**：`user_data/agents_docs/{账号}/workflows/{名字}.yml` —— 与 `skills/` **并排**，
  沿用同一套安全校验与上限（名字长度、保留名、字数上限）；
- **引擎不在本模块**：步骤由 `agent/workflow_runner.py` 的**代码逐步驱动**（不把步骤写进提示词），
  本模块只负责"定义怎么存、怎么校验、怎么列给前端选"；
- **本次只做只读型**：`use` 里只允许内置只读工具集与自配只读集；写型 workflow 放到
  **bug 维护与优化期**（方案 §06 ③ I / K）。

YAML 形状（有意做得像"表单填出来的东西"，便于表单式编辑器双向映射）：

    name: 竞品官网取价
    desc: 先定位竞品官网，再进站取价格并对比
    steps:
      - id: 1
        title: 定位竞品官网
        use: [db_agent]
        output: 官网 URL 列表
      - id: 2
        title: 进站取价
        use: [search_then_browse]
        output: 价格与来源
"""
from __future__ import annotations

import re
import time
from pathlib import Path

import yaml
from fastapi import HTTPException, status
from pydantic import BaseModel

from api.customize import _SKILL_RESERVED, _user_doc_dir, _username_by_token

WF_MAX_LEN = 4000        # 单个 workflow（YAML 全文）字数上限，与技能同量级
WF_NAME_MAX = 30         # 名字长度上限（与技能一致）
WF_MAX_STEPS = 12        # 步骤数上限：再多就不该叫"流程固定的小工具"了
WF_MAX_USE = 6           # 单步最多引用几个工具集（防提示词/执行面爆炸）
WF_MAX_WRITE = 7         # 一个 workflow 最多声明几个写工具集（与「/」的选择上限同量级）


def _user_wf_dir(username: str) -> Path:
    """workflow 目录：agents_docs/{username}/workflows/（与 skills/ 并排）。"""
    d = _user_doc_dir(username) / "workflows"
    d.mkdir(parents=True, exist_ok=True)
    return d


def wf_safe_name(name: str) -> str:
    """workflow 名 → 安全文件名（不含扩展名）；非法直接抛 400。三层校验同技能。"""
    raw = (name or "").strip()
    for ext in (".yml", ".yaml", ".md"):
        if raw.lower().endswith(ext):
            raw = raw[: -len(ext)]
    raw = raw.replace("\\", "/").split("/")[-1].strip()
    if not raw or raw in (".", ".."):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "workflow 名不能为空，也不能是 . 或 ..")
    if len(raw) > WF_NAME_MAX:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"workflow 名过长（≤{WF_NAME_MAX} 字）")
    if raw.lower() in _SKILL_RESERVED:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"「{raw}」是系统保留名，请换一个")
    safe = re.sub(r"[^\w.\-]", "_", raw)
    safe = safe.strip("._") or "workflow"
    if safe.lower() in _SKILL_RESERVED:
        safe = f"wf_{safe}"
    return safe


def _wf_path(username: str, name: str) -> Path:
    return _user_wf_dir(username) / f"{wf_safe_name(name)}.yml"


class WfReq(BaseModel):
    name: str
    content: str = ""


# ------------------------------------------------------------------ 校验
def validate_wf(content: str) -> dict:
    """校验并规范化一份 workflow 定义。返回 {name, desc, steps}；不合法抛 400。"""
    content = content or ""
    if len(content) > WF_MAX_LEN:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"workflow 内容过长（≤{WF_MAX_LEN} 字）")
    try:
        doc = yaml.safe_load(content)
    except yaml.YAMLError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"YAML 解析失败：{e}")
    if not isinstance(doc, dict):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "顶层必须是 YAML 映射（name / desc / steps）")
    steps = doc.get("steps")
    if not isinstance(steps, list) or not steps:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "steps 必须是非空列表")
    if len(steps) > WF_MAX_STEPS:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"步骤过多（≤{WF_MAX_STEPS} 步）")
    norm = []
    for i, s in enumerate(steps, 1):
        if not isinstance(s, dict):
            raise HTTPException(status.HTTP_400_BAD_REQUEST, f"第 {i} 步必须是映射（id/title/use/output）")
        title = str(s.get("title") or "").strip()
        if not title:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, f"第 {i} 步缺 title")
        use = s.get("use")
        if use is None:
            use = []
        if isinstance(use, str):
            use = [x.strip() for x in use.split(",") if x.strip()]
        if not isinstance(use, list):
            raise HTTPException(status.HTTP_400_BAD_REQUEST, f"第 {i} 步的 use 必须是列表或逗号分隔字符串")
        if len(use) > WF_MAX_USE:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, f"第 {i} 步引用的工具集过多（≤{WF_MAX_USE} 个）")
        norm.append({"id": s.get("id") or i, "title": title[:60],
                     "use": [str(x)[:40] for x in use],
                     "output": str(s.get("output") or "").strip()[:120]})
    # ★ 2026-10-18 写能力工作流：workflow 级 `write:` 声明本轮要授权的写工具集。
    #   放在顶层而不是每步，是因为授权是**整轮**语义（一次「/」授权 → 整条流程可用）。
    wsets = doc.get("write")
    if wsets is None:
        wsets = []
    if isinstance(wsets, str):
        wsets = [x.strip() for x in wsets.split(",") if x.strip()]
    if not isinstance(wsets, list):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "write 必须是列表或逗号分隔字符串（写工具集名字）")
    if len(wsets) > WF_MAX_WRITE:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"写工具集过多（≤{WF_MAX_WRITE} 个）")
    wsets = [str(x)[:40] for x in wsets if str(x).strip()]
    return {"name": str(doc.get("name") or "").strip()[:WF_NAME_MAX],
            "desc": str(doc.get("desc") or "").strip()[:120], "steps": norm, "write": wsets}


def wf_summary(content: str) -> dict:
    """列表用的轻量摘要（坏文件不炸，标 ok=False 让前端能提示）。"""
    try:
        v = validate_wf(content)
        return {"ok": True, "name": v["name"], "desc": v["desc"], "steps": len(v["steps"])}
    except HTTPException as e:
        return {"ok": False, "error": str(e.detail), "steps": 0, "name": "", "desc": ""}


# ------------------------------------------------------------------ CRUD
def wf_list(token: str) -> dict:
    """列出该账号全部 workflow（含步骤数与坏文件标记）。"""
    user = _username_by_token(token)
    items = []
    for f in sorted(_user_wf_dir(user).glob("*.yml")):
        try:
            content = f.read_text(encoding="utf-8")
        except Exception:                     # noqa: BLE001
            content = ""
        s = wf_summary(content)
        items.append({"file": f.stem, "name": s["name"] or f.stem, "desc": s["desc"],
                      "steps": s["steps"], "ok": s["ok"], "error": s.get("error", ""),
                      "chars": len(content),
                      "updated": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(f.stat().st_mtime))})
    return {"workflows": items, "max_steps": WF_MAX_STEPS, "max_len": WF_MAX_LEN}


def wf_get(token: str, name: str) -> dict:
    user = _username_by_token(token)
    p = _wf_path(user, name)
    return {"name": p.stem, "content": p.read_text(encoding="utf-8") if p.exists() else ""}


def wf_create(token: str, req: WfReq) -> dict:
    """新建（重名 409，不覆盖）；**创建即校验** —— 存进去的定义必须能被引擎跑。"""
    user = _username_by_token(token)
    validate_wf(req.content)
    p = _wf_path(user, req.name)
    if p.exists():
        raise HTTPException(status.HTTP_409_CONFLICT, f"workflow「{p.stem}」已存在")
    p.write_text(req.content or "", encoding="utf-8")
    return {"ok": True, "name": p.stem}


def wf_save(token: str, req: WfReq) -> dict:
    """保存（不存在则创建）；同样创建即校验。"""
    user = _username_by_token(token)
    validate_wf(req.content)
    p = _wf_path(user, req.name)
    p.write_text(req.content or "", encoding="utf-8")
    return {"ok": True, "name": p.stem}


def wf_delete(token: str, name: str) -> dict:
    user = _username_by_token(token)
    p = _wf_path(user, name)
    if not p.exists():
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"workflow「{name}」不存在")
    p.unlink()
    return {"ok": True}


def wf_for_account(aid: int, name: str) -> dict | None:
    """按 account_id 读一份 workflow 定义（引擎侧用；找不到/坏文件返回 None）。"""
    from tools.schema_personal import get_personal_conn
    conn = get_personal_conn()
    try:
        row = conn.execute("SELECT username FROM accounts WHERE id=?", (int(aid),)).fetchone()
    finally:
        conn.close()
    if not row:
        return None
    try:
        p = _wf_path(row["username"], name)
    except HTTPException:
        return None
    if not p.exists():
        return None
    try:
        return validate_wf(p.read_text(encoding="utf-8"))
    except HTTPException:
        return None                             # 坏定义不该让整轮对话炸：交给调用侧如实说明


def wf_template() -> str:
    """表单式编辑器的初始模板（前端「插入模板」用）。"""
    return ("name: 竞品官网取价\n"
            "desc: 先定位竞品官网，再进站取价格并对比\n"
            "steps:\n"
            "  - id: 1\n"
            "    title: 定位竞品官网\n"
            "    use: [db_agent]\n"
            "    output: 官网 URL 列表\n"
            "  - id: 2\n"
            "    title: 进站取价\n"
            "    use: [search_then_browse]\n"
            "    output: 价格与来源\n")
