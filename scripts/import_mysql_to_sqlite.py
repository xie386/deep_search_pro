"""一次性导入：把旧 MySQL 公司侧三表数据导入 SQLite 指定账号名下。

用法：
    .venv/Scripts/python.exe scripts/import_mysql_to_sqlite.py <目标用户名> [--reset-demo]

说明：
  - 目标账号必须已存在（如 demo_company）；数据按该账号 owner_id 写入。
  - --reset-demo：改为重建演示库（含内置 demo_company 样例），忽略 MySQL。
  - MySQL 连接读取 .env 的 MYSQL_* 配置；库不存在/连不上会明确报错。
"""
import sys, os

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, _ROOT)
os.chdir(_ROOT)
sys.path = [p for p in sys.path if "hermes-agent" not in p and "hermes_agent" not in p]

import json
from dotenv import load_dotenv
load_dotenv()


def main():
    args = [a for a in sys.argv[1:]]
    reset_demo = "--reset-demo" in args
    if reset_demo:
        from tools.schema_personal import init_personal_db
        init_personal_db()
        print("演示库已重置（alice + demo_company 内置样例）。")
        return

    username = args[0] if args else ""
    if not username:
        print(__doc__)
        sys.exit(1)

    from tools.schema_personal import get_personal_conn, ensure_tables
    ensure_tables()
    conn = get_personal_conn()
    acc = conn.execute("SELECT id FROM accounts WHERE username=?", (username,)).fetchone()
    if not acc:
        print(f"账号 {username} 不存在，请先注册。")
        sys.exit(1)
    aid = acc["id"]

    # 清空该账号旧的公司侧数据后导入（幂等）
    conn.execute("DELETE FROM company_products WHERE owner_id=?", (aid,))
    conn.execute("DELETE FROM company_competitors WHERE owner_id=?", (aid,))
    conn.execute("DELETE FROM company_profile WHERE owner_id=?", (aid,))

    # 读 MySQL
    from tools.db_tools import get_db_config
    from mysql.connector import connect
    cfg = get_db_config()
    try:
        my = connect(**cfg)
    except Exception as e:
        print(f"MySQL 连接失败（{e}）。若无需迁移可使用 --reset-demo 重建演示库。")
        sys.exit(1)
    mcur = my.cursor()

    # profile（MySQL 无对应表，用库名占位）
    conn.execute("INSERT INTO company_profile (owner_id, company_name, note) VALUES (?,?,?)",
                 (aid, os.getenv("MYSQL_DATABASE", "my_company"), "自 MySQL 迁移"))

    mcur.execute("SELECT product_name, category, price, core_features, target_scale FROM our_products")
    for r in mcur.fetchall():
        attrs = json.dumps({"core_features": r[3] or "", "target_scale": r[4] or "", "billing_unit": "人/年"}, ensure_ascii=False)
        conn.execute(
            "INSERT INTO company_products (owner_id, product_name, category, price, attributes) VALUES (?,?,?,?,?)",
            (aid, r[0], r[1], float(r[2] or 0), attrs))

    mcur.execute("SELECT comp_name, category, website, is_competitor, note, mapped_product_ids FROM competitors")
    for r in mcur.fetchall():
        mapped = json.dumps(r[5]) if r[5] else None
        if isinstance(mapped, str) and mapped.startswith('"'):
            mapped = json.loads(mapped)  # MySQL JSON_EXTRACT 可能带引号
        conn.execute(
            "INSERT INTO company_competitors (owner_id, comp_name, category, website, is_competitor, note, mapped_product_ids) "
            "VALUES (?,?,?,?,?,?,?)",
            (aid, r[0], r[1], r[2], 1 if r[3] else 0, r[4], mapped))

    conn.commit()
    n_prod = conn.execute("SELECT COUNT(*) c FROM company_products WHERE owner_id=?", (aid,)).fetchone()["c"]
    n_comp = conn.execute("SELECT COUNT(*) c FROM company_competitors WHERE owner_id=?", (aid,)).fetchone()["c"]
    conn.close(); my.close()
    print(f"导入完成：账号 {username} <- our_products×{n_prod}, competitors×{n_comp}")


if __name__ == "__main__":
    main()
