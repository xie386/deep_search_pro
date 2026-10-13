"""把库里已存的**明文密钥**搬进 `.env`（04 补 · E5-A，2026-10-14 用户选 A 案）。

**背景**：`llm_providers.api_key` 与 `tool_sources.config_json.auth.secret` 过去明文存库 ——
备份 / 导出一次 `personal.db` 就等于泄一次钥。项目里早有正确样板 `price_sources.secret_env`
（库里只存**环境变量名**，值只活在 `.env`）。本脚本把存量迁到这个口径。

**默认 dry-run（只看清单、不写任何东西）**，确认无误再加 `--apply`。

两类迁移：
  ① `llm_providers`：`api_key` 非空的行 → 值写 `.env`（键名 `LLM_KEY_<名称>_<id>`）
     → 库里 `secret_env=键名`、`api_key=''`（明文列清空）
  ② `tool_sources`：`config_json.auth.secret` 非空的行 → 同理（`TOOL_KEY_<账号>_<短名>`）
     → `auth.secret` 换成 `auth.secret_env`

用法：
    unset PYTHONPATH
    .venv/Scripts/python.exe scripts/migrate_provider_secrets.py            # 看清单
    .venv/Scripts/python.exe scripts/migrate_provider_secrets.py --apply    # 真写

回滚：`.env` 每次写入前都备份为 `.env.bak_<时间戳>`（值就在里面）；库侧把 `secret_env` 清空、
`api_key` 填回即可。脚本本身幂等：已迁移过的行不会再动。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.schema_personal import ensure_tables, get_personal_conn   # noqa: E402
from utils import secrets as _secrets                                 # noqa: E402


def _rows(conn, sql, args=()):
    return [{k: r[k] for k in r.keys()} for r in conn.execute(sql, args).fetchall()]


def plan() -> list[dict]:
    """算出要迁移哪些行（**不写任何东西**）。"""
    ensure_tables()
    conn = get_personal_conn()
    try:
        out = []
        for p in _rows(conn, "SELECT id, provider_name, api_key, secret_env FROM llm_providers"):
            key = (p["api_key"] or "").strip()
            if key and not (p["secret_env"] or "").strip():
                out.append({"table": "llm_providers", "id": p["id"], "label": p["provider_name"],
                            "env": _secrets.env_name("llm", p["id"], p["provider_name"]),
                            "value": key, "len": len(key)})
        for s in _rows(conn, "SELECT id, account_id, slug, name, config_json FROM tool_sources"):
            try:
                cfg = json.loads(s["config_json"] or "{}")
            except Exception:
                continue
            auth = (cfg or {}).get("auth") or {}
            sec = (auth.get("secret") or "").strip()
            if sec and not (auth.get("secret_env") or "").strip():
                out.append({"table": "tool_sources", "id": s["id"], "label": s["name"] or s["slug"],
                            "env": _secrets.env_name("tool", "%s_%s" % (s["account_id"], s["slug"]),
                                                     s["name"] or s["slug"]),
                            "value": sec, "len": len(sec),
                            "account_id": s["account_id"], "slug": s["slug"]})
        return out
    finally:
        conn.close()


def apply(items: list[dict]) -> dict:
    """执行迁移：写 `.env`（自动备份）+ 改库。逐行做，单行失败不影响其余。"""
    conn = get_personal_conn()
    ok, failed = 0, []
    try:
        for it in items:
            try:
                name = _secrets.put("llm" if it["table"] == "llm_providers" else "tool",
                                    it["id"] if it["table"] == "llm_providers"
                                    else "%s_%s" % (it["account_id"], it["slug"]),
                                    it["value"], it["label"])
                if it["table"] == "llm_providers":
                    conn.execute("UPDATE llm_providers SET secret_env=?, api_key='' WHERE id=?",
                                 (name, it["id"]))
                else:
                    row = conn.execute("SELECT config_json FROM tool_sources WHERE id=?",
                                       (it["id"],)).fetchone()
                    cfg = json.loads(row["config_json"] or "{}")
                    auth = cfg.get("auth") or {}
                    auth.pop("secret", None)
                    auth["secret_env"] = name
                    cfg["auth"] = auth
                    conn.execute("UPDATE tool_sources SET config_json=? WHERE id=?",
                                 (json.dumps(cfg, ensure_ascii=False), it["id"]))
                conn.commit()
                ok += 1
            except Exception as e:      # noqa: BLE001 —— 逐行记账，别让一行炸掉整批
                failed.append("%s#%s: %s" % (it["table"], it["id"], e))
    finally:
        conn.close()
    return {"ok": ok, "failed": failed}


def main() -> int:
    ap = argparse.ArgumentParser(description="把库里明文密钥搬进 .env（默认 dry-run）")
    ap.add_argument("--apply", action="store_true", help="真正执行（默认只看清单）")
    args = ap.parse_args()

    items = plan()
    print("=" * 78)
    print("待迁移的明文密钥：%d 行" % len(items))
    print("=" * 78)
    if not items:
        print("没有需要迁移的行（都已经是「名入库、值走 .env」口径）✓")
        return 0
    print("%-16s %-6s %-22s %-34s %s" % ("表", "id", "显示名", "将写入的 .env 键名", "值长度"))
    for it in items:
        print("%-16s %-6s %-22s %-34s %d" % (it["table"], it["id"], str(it["label"])[:20],
                                             it["env"], it["len"]))
    print("\n（值一律不打印；写入时自动备份 .env → .env.bak_<时间戳>）")
    if not args.apply:
        print("\n当前是 dry-run，什么都没改。确认后加 --apply 执行。")
        return 0
    res = apply(items)
    print("\n迁移完成：成功 %d 行，失败 %d 行" % (res["ok"], len(res["failed"])))
    for f in res["failed"]:
        print("  失败：" + f)
    print("复查命令：`python scripts/migrate_provider_secrets.py`（应显示 0 行）")
    return 0 if not res["failed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
