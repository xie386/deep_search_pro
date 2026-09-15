# -*- coding: utf-8 -*-
"""dspro digest —— 生成周报（与 web 端「立即生成」同一条引擎）。

    dspro digest                     # 默认：所有启用中的关注领域各生成一份
    dspro digest "降噪耳机"           # 只生成该领域（名称必须属于当前账号，否则报错）
    dspro digest --list              # 只列出可用的关注领域名
    dspro digest "降噪耳机" -o r.md   # 同时另存一份 markdown
    dspro digest --json              # 机器可读（脚本/管道用）

说明：
  - 引擎是 agent.digest_engine.run_digest（主 Agent 编排「网络搜索助手 + 周报助手」），
    与 web 端 /api/digest/run 调的是同一个函数，生成物同样落 digest_reports 表；
  - 单个领域约 1-3 分钟（联网检索 + 撰写），多个领域串行执行（避免并发压垮模型）；
  - 进度行由 monitor 的控制台 sink 渲染（工具调用 / 子智能体调度 / 思考片段）。
"""

import json
import sys
import time
from pathlib import Path

from cli import ui
from cli import session as ss
from cli.errors import CliError

_SCOPE_LABEL = {"company": "竞品", "personal": "选购/兴趣"}


def _load_subs(conn, aid: int, only_enabled: bool = True) -> list[dict]:
    sql = "SELECT id, scope, name, keywords, schedule, lang, enabled FROM digest_subs WHERE owner_id=?"
    if only_enabled:
        sql += " AND enabled=1"
    sql += " ORDER BY scope, id"
    return [dict(r) for r in conn.execute(sql, (aid,)).fetchall()]


def _keywords(raw) -> str:
    try:
        arr = json.loads(raw or "[]")
        return "、".join(str(x) for x in arr) if isinstance(arr, list) else str(arr)
    except Exception:
        return str(raw or "")


def _sub_line(s: dict) -> str:
    """一条订阅的单行描述（歧义/报错时用，含 id 与关键词便于区分同名订阅）。"""
    return f"#{s['id']} {s.get('name') or '-'}（关键词：{_keywords(s.get('keywords')) or '-'}）"


def _match(subs: list[dict], domain: str) -> dict:
    """把「领域名」解析到唯一订阅。

    匹配优先级：`#7` / 纯数字 → 精确名称 → 名称包含或关键词命中。
    命中的候选不止一个时报错并列出（含 #id），提示改用 #id 指定——
    实测订阅名可能重复（同一账号 3 条都叫「选购快讯」），此时静默取第一条会误导用户。
    """
    want = (domain or "").strip()
    if not want:
        raise CliError("领域名为空")

    by_id = {int(s["id"]): s for s in subs}
    raw_id = want.lstrip("#").strip()
    if raw_id.isdigit() and int(raw_id) in by_id:
        return by_id[int(raw_id)]

    key = "".join(want.split()).lower()

    def norm(s):
        return "".join((s.get("name") or "").split()).lower()

    hits = [s for s in subs if norm(s) == key]
    if not hits:                                  # 名称包含 / 关键词命中
        for s in subs:
            kws = "".join(str(_keywords(s.get("keywords"))).split()).lower()
            if (key and key in norm(s)) or (key and key in kws):
                hits.append(s)
    if len(hits) == 1:
        return hits[0]
    if len(hits) > 1:
        detail = "\n".join("    " + _sub_line(s) for s in hits)
        raise CliError(f"「{want}」匹配到 {len(hits)} 条订阅，请用 #id 指定要生成哪一条：\n{detail}")

    if raw_id.isdigit():
        raise CliError(f"当前账号没有启用的订阅 #{raw_id}（或它已被停用）。\n" + _available_text(subs))
    raise CliError(f"当前账号没有关注领域「{want}」。\n" + _available_text(subs))


def _available_text(subs: list[dict]) -> str:
    if not subs:
        return "  该账号没有任何启用中的关注领域。先到 web 端「情报周报」页新增订阅。"
    lines = "\n".join("    " + _sub_line(s) for s in subs)
    return f"  可用（共 {len(subs)} 条，可写名称或 #id）：\n{lines}\n  查看全部：dspro list -digest"


def _md_path(p: str) -> Path:
    path = Path(p or "")
    return path if path.is_absolute() else (ss.PROJECT_ROOT / path)


def run(domain: str | None = None, out: str | None = None, no_print: bool = False,
        as_json: bool = False, list_only: bool = False) -> int:
    data = ss.require()
    aid = data["account_id"]

    conn = ss.db()
    try:
        subs = _load_subs(conn, aid)
    finally:
        conn.close()

    if list_only:
        if as_json:
            print(json.dumps([{"id": s["id"], "name": s["name"], "scope": s["scope"]} for s in subs],
                             ensure_ascii=False, indent=2))
            return 0
        ui.title("可用的关注领域", f"来自 digest_subs（仅启用中）")
        if not subs:
            ui.hint("（空）到 web 端「情报周报」页新增订阅")
            return 0
        ui.table(["#id", "领域名（也可直接用 #id）", "范围", "关键词"],
                 [[s["id"], s["name"] or "-", _SCOPE_LABEL.get(s.get("scope"), s.get("scope") or "-"),
                   ui.trunc(_keywords(s.get("keywords")), 60)] for s in subs],
                 max_width=[4, 26, 10, 60])
        ui.hint('生成某一领域：dspro digest "领域名"  或  dspro digest #<上表 id>')
        return 0

    if not subs:
        raise CliError("该账号没有启用中的关注领域，无法生成。先到 web 端「情报周报」页新增订阅，"
                       "或用 dspro list -digest 查看现状。")

    targets = [_match(subs, domain)] if domain else subs
    if out and len(targets) > 1:
        raise CliError("-o/--out 需要配合指定一个领域名使用（一次只导出一份）；多个领域请分次执行。")

    if not as_json:
        ui.title("开始生成周报", f"账号 {data['username']} · {len(targets)} 个关注领域")
        ui.table(["#id", "关注领域", "范围", "检索语言", "关键词"],
                 [[s["id"], s["name"] or "-", _SCOPE_LABEL.get(s.get("scope"), s.get("scope") or "-"),
                   {"zh": "中文", "en": "英文", "both": "中英双语"}.get(s.get("lang"), s.get("lang") or "-"),
                   ui.trunc(_keywords(s.get("keywords")), 52)] for s in targets],
                 max_width=[4, 24, 10, 10, 52])
        ui.hint("每个领域约 1-3 分钟（联网检索 + 撰写），串行执行，请勿关闭窗口。")

    # 进度：注册 monitor 的控制台 sink（工具调用/子智能体/思考）
    from api.monitor import monitor
    try:
        monitor.set_console_sink(ui.progress)
    except Exception:
        pass

    results = []
    try:
        from agent.digest_engine import run_digest
        for i, s in enumerate(targets, 1):
            if not as_json:
                ui.section(f"[{i}/{len(targets)}] {s['name'] or s['id']}", "📝")
            t0 = time.time()
            try:
                res = run_digest(int(s["id"]), aid)
            except Exception as e:                      # noqa: BLE001 - 单个失败不影响其余
                res = {"ok": False, "error": f"{type(e).__name__}: {e}"}
            elapsed = round(time.time() - t0, 1)
            rec = {"sub_id": s["id"], "name": s["name"], "scope": s.get("scope"),
                   "ok": bool(res.get("ok")), "item_count": res.get("item_count"),
                   "md_path": res.get("md_path"), "elapsed_s": elapsed, "error": res.get("error")}
            # 覆盖说明（报告头写了覆盖有限时要让用户知道）
            if rec["ok"] and not as_json:
                ui.ok(f"已生成：{rec['item_count']} 条情报 · 用时 {elapsed}s")
                ui.kv([("文件", str(rec["md_path"] or "-"))], indent="    ")
            elif not rec["ok"]:
                ui.err(f"生成失败：{res.get('error')}")
            results.append(rec)

            # 输出正文
            if rec["ok"] and rec.get("md_path") and not as_json and not no_print:
                path = _md_path(rec["md_path"])
                try:
                    text = path.read_text(encoding="utf-8")
                    print()
                    print(ui.md_light(text))
                except Exception as e:                  # noqa: BLE001
                    ui.warn(f"报告正文读取失败（文件仍已保存）：{e}")
    finally:
        try:
            monitor.set_console_sink(None)
        except Exception:
            pass

    if out and results and results[0]["ok"]:
        src = _md_path(results[0]["md_path"])
        try:
            Path(out).expanduser().write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
            if not as_json:
                ui.ok(f"已另存：{Path(out).expanduser()}")
        except Exception as e:                          # noqa: BLE001
            ui.warn(f"另存失败：{e}")

    ok_n = sum(1 for r in results if r["ok"])
    if as_json:
        print(json.dumps({"ok": ok_n == len(results), "generated": ok_n, "total": len(results),
                          "results": results}, ensure_ascii=False, indent=2))
    else:
        ui.blank()
        (ui.ok if ok_n == len(results) else ui.warn)(
            f"完成：成功 {ok_n} / 共 {len(results)} 份（生成物已入库，web 端「情报周报」可见）")
        if ok_n != len(results):
            for r in results:
                if not r["ok"]:
                    ui.err(f"  {r['name']}：{r['error']}")
    return 0 if ok_n == len(results) else 1
