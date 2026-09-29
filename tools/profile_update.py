# -*- coding: utf-8 -*-
"""M3-5 · 「更新用户画像」的结构化工具（F4 定案 A，方案 §4.1 / §3.3）。

为什么需要它（根因 R2）：原来 agent 只有 `write_agent_doc`（整文件覆盖 / 追加）——
要"改一行"就得先读全文再整份重写，成本高、又怕覆盖丢内容，于是模型**理性地选择追加**，
画像就烂成了"同一维度好几行"的流水账。修法是**把读改写/去重/加来源/保人工行全交给框架**：
模型只说"哪个维度、新值是什么、来源是什么"。

铁律（与 §5.7 的快照口径一致）：
  · **同一维度就地更新**（`upsert`），只有新维度才追加；
  · **人工行**（来源标 `人工`）**永不被本工具改动**（要改只能用户在前端改）；
  · **改前先快照**（D9，`reason=agent_update`）；
  · 只写 `## USER PROFILE` 段：文件头与笔记段原样保留；`memory_meta.refreshed` 更新为今天。
"""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from langchain_core.tools import tool

from api.context import get_owner_context
from api.monitor import monitor
from tools import memory_profile as mprof
from tools import memory_snapshots as ms
from tools.schema_personal import get_personal_conn

_AGENTS_DOCS = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "agents_docs"))


def _memory_path_of_account(aid: int):
    """账号 → `agents_docs/{username}/MEMORY.md`（找不到账号返回 (None, None)）。"""
    conn = get_personal_conn()
    try:
        row = conn.execute("SELECT username FROM accounts WHERE id=?", (int(aid),)).fetchone()
    finally:
        conn.close()
    if not row:
        return None, None
    uname = row[0] if not hasattr(row, "keys") else row["username"]
    return uname, os.path.join(_AGENTS_DOCS, uname, "MEMORY.md")


@tool
def update_user_profile(op: str, field: str, value: str, source: str = "会话") -> str:
    """更新「用户记忆画像」里的一个维度（**同一维度就地更新，不会写出重复行**）。

    什么时候用：用户说出了一个**稳定的长期事实**（身份/领域、消费偏好、预算范围、价格敏感度、
    情报关注点、竞品关注、关注渠道、品类偏好、身份变化），或者注入块里出现【画像待更新】提示
    且与你本轮要回答的问题相关时。

    怎么用（op）：
      · `op="upsert"`（默认语义）：该维度不存在就新增，已存在就**改这一行**（值取最新），
        例：用户说「我预算最多 100 块」→ op="upsert", field="预算范围", value="≤100 元/瓶", source="会话"；
      · `op="remove"`：删掉某个**不再成立**的维度（人工填的那行删不掉，会提示你）。

    注意：
      · `field` 用上面那套维度名（别的名字也接受，但会标成"新维度"）；
      · `value` 写事实本身，不要写"暂无/待补充"这类占位 —— 缺失的维度**不写**；
      · `source` 写依据：对话里听来的写 `会话`，从系统资料来的写 `收藏#12` 这种引用；
      · **人工手写的那一行不会被本工具覆盖**（工具会如实告诉你没改成）。
    """
    aid = get_owner_context()
    if not aid:
        return "更新失败：当前没有账号上下文（请在登录后的会话里调用）"
    uname, path = _memory_path_of_account(aid)
    if not path:
        return "更新失败：找不到当前账号"
    op = (op or "upsert").strip().lower()
    field = (field or "").strip()
    value = (value or "").strip()
    source = (source or "会话").strip()
    if not field:
        return "更新失败：缺少 field（要更新哪个维度）"
    monitor.report_tool(tool_name="更新用户画像", args={"op": op, "field": field, "len": len(value)})

    try:
        cur = ""
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                cur = f.read()
        else:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            cur = ("# 用户记忆画像（MEMORY）\n\n<!-- memory_init: pending -->\n\n"
                   + mprof.PROFILE_HEAD + "\n\n" + mprof.NOTES_HEAD + "\n\n"
                   "- （还没有内容）\n")
        rows = mprof.parse_rows(mprof.split_sections(cur)["profile"])

        if op == "remove":
            norm = mprof._norm_field(field)
            hit = next((r for r in rows if r.get("norm") == norm), None)
            if not hit:
                return "没改动：画像里没有『%s』这个维度" % field
            if hit.get("manual"):
                return "没改动：『%s』是用户自己手写的（来源=人工），只能由用户在前端修改" % field
            rows = [r for r in rows if r is not hit]
            applied = "removed"
        else:
            if not value:
                return "没改动：value 是空的（要删维度请用 op=\"remove\"）"
            rows, applied = mprof.upsert(rows, field, value, source=source, date=mprof.today(),
                                         protect_manual=True)
            if applied == "skip_manual":
                return ("没改动：『%s』是用户自己手写的（来源=人工），本工具不会覆盖它 —— "
                        "需要改请让用户在前端「🧠 记忆画像」里改" % field)
            if applied == "empty":
                return "没改动：value 是空的"

        ms.snapshot(int(aid), cur, ms.REASON_AGENT_UPDATE)      # ★ 改前先快照（D9）
        from tools import profile_evidence as pev
        meta = mprof.parse_meta(cur)
        new_content = mprof.render_file(cur, rows, pev.refresh_meta(int(aid), len(rows), meta))
        with open(path, "w", encoding="utf-8") as f:
            f.write(new_content)
    except Exception as e:  # noqa: BLE001
        return "更新失败：%s: %s" % (type(e).__name__, e)

    verb = {"add": "已新增", "update": "已就地更新", "same": "值与现值相同，已确认", "removed": "已删除",
            "empty": "没改动"}.get(applied, "已更新")
    tail = ("；来源：%s" % source) if applied in ("add", "update") else ""
    return "%s『%s』%s%s（画像里同一维度始终只有一行）" % (verb, field, value if value else "", tail)


if __name__ == "__main__":     # 简单自测（不依赖 Agent 运行时）
    print(update_user_profile.invoke({"op": "upsert", "field": "预算范围", "value": "≤100 元/瓶",
                                      "source": "会话"}))
