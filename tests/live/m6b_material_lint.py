# -*- coding: utf-8 -*-
"""素材层体检（薄包装）：规则在 **产品模块** `tools/capability_lint.py`（接入流程里自动生效的就是它）。

用法：.venv/Scripts/python.exe tests/live/m6b_material_lint.py [账号名或 id，默认 3]
只读：不写数据库、不写任何文件。
"""
import os
import sqlite3
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
from tools import capability_lint as lint   # noqa: E402


def main() -> int:
    acct = sys.argv[1] if len(sys.argv) > 1 else 3
    con = sqlite3.connect("file:%s?mode=ro" % os.path.join(ROOT, "data", "personal.db").replace("\\", "/"), uri=True)
    try:
        aid = int(acct) if str(acct).isdigit() else list(con.execute("SELECT id FROM accounts WHERE name=?", (acct,)))[0][0]
        # ★ 必须带上来源级描述（池子取词的第三顺位）；否则本脚本与「来源列表接口」的
        #   体检结果会不一致（实测：脚本报"只有派生关键词"，接口不报）。
        src_map = {r[0]: (r[1] or "") for r in con.execute(
            "SELECT slug, COALESCE(abilities,'') FROM tool_sources WHERE account_id=?", (aid,))}
        rows = []
        for r in con.execute("""SELECT ref, name, COALESCE(keywords,''), COALESCE(abilities,''),
                                       COALESCE(abilities_user,'')
                                FROM tool_capabilities WHERE account_id=? AND enabled=1
                                ORDER BY ref""", (aid,)):
            row = dict(zip(("ref", "name", "keywords", "abilities", "abilities_user"), r))
            slug = (row["ref"] or "").split(":", 1)[-1].split("/")[0].split("#")[0]
            row["src_abilities"] = src_map.get(slug, "")
            rows.append(row)
    finally:
        con.close()
    issues = lint.check_rows(rows)
    print("素材层体检 ｜ 账号 %s ｜ 条目 %d 条 ｜ 有问题 %d 条" % (acct, len(rows), len(issues)))
    print("-" * 78)
    for r in rows:
        probs = issues.get(r["ref"])
        if not probs:
            continue
        print("  %-42s %s" % (r["ref"], r["name"]))
        for pr in probs:
            print("      · [%s/%s] %s" % (pr["code"], pr.get("severity", "hard"), pr["msg"]))
    if not issues:
        print("  全部条目齐备：关键词可用、工具级描述到位、名字非英文标识符、无疑似近名冲突。")
    print("-" * 78)
    print("★ 静态闸门只保证素材「写得齐」；是否真的提升路由，仍须跑动态闸门：")
    print("   tests/live/m6b_material_ab.py（冻结池）或 tests/live/m6b_delta_rerun.py --live-pool（真实池）")
    print("★ 规则与产品模块同源：tools/capability_lint.py（来源列表/刷新接口也用它）。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
