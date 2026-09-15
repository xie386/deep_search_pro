# ============================================================
# 智选情报官 · 本地 SQLite 库 Schema（M1.5 建立，前置工作扩展）
# 存储：data/personal.db（零部署，本地单文件应用）
#
# 前置改造（M4 设计文档第〇章）要点：
#   - ensure_tables()：幂等建表（IF NOT EXISTS），服务启动时调用，
#     绝不破坏现有数据；新增表通过它增量创建。
#   - init_personal_db()：【仅用于重置演示库】DROP 后重建并写入
#     alice 样例数据。样例只属于 alice，新注册用户空置起步。
#   - 公司侧三表（company_profile / company_products /
#     company_competitors）按 owner_id 隔离，替代 MySQL 全局表。
# 设计原则（延续 M1.5）：表结构收敛至绝对共性，业务特性托管 JSON 扩展列。
# ============================================================

import os
from pathlib import Path
import sqlite3

# 库文件位置：项目根/data/personal.db
DB_PATH = os.getenv("PERSONAL_DB_PATH") or str(
    Path(__file__).resolve().parents[1] / "data" / "personal.db"
)


def get_personal_conn() -> sqlite3.Connection:
    """返回个人库连接（启用外键 + Row 工厂）。"""
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def purge_account(account_id: int | None) -> dict:
    """删除账号及其**全部账号域数据**，返回 {表名: 删除行数}。

    为什么需要它：连接启用了 `PRAGMA foreign_keys = ON`，而各账号域表都带
    `REFERENCES accounts(id)`——新增一张表就会让「删账号」这条路撞外键约束。
    这里**自省 sqlite_master**（凡带 `account_id` / `owner_id` 列的表都清），
    所以以后加表（tool_api / tool_mcp / …）无需再改这个函数，也不会再有测试清理漏表。

    顺序：messages（经 conversations 关联）→ 其余账号域表 → accounts 本身。
    全程容错：单表失败只记录，不中断（便于残留数据兜底清理）。
    """
    if not account_id:
        return {}
    aid = int(account_id)
    removed: dict[str, int] = {}
    conn = get_personal_conn()
    try:
        tables = [r["name"] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
        cols_of = {t: [r[1] for r in conn.execute("PRAGMA table_info(%s)" % t)] for t in tables}

        # 1) messages 先从 conversations 关联里摘掉（外键最下游）
        if "messages" in tables and "conversations" in tables:
            try:
                cur = conn.execute(
                    "DELETE FROM messages WHERE conversation_id IN "
                    "(SELECT id FROM conversations WHERE account_id=?)", (aid,))
                removed["messages"] = cur.rowcount
            except Exception:  # noqa: BLE001
                pass

        # 2) 其余带 account_id / owner_id 的表（conversations 在此被清）
        for t in tables:
            if t == "accounts":
                continue
            col = "account_id" if "account_id" in cols_of[t] else (
                "owner_id" if "owner_id" in cols_of[t] else None)
            if not col:
                continue
            try:
                cur = conn.execute("DELETE FROM %s WHERE %s=?" % (t, col), (aid,))
                if cur.rowcount:
                    removed[t] = removed.get(t, 0) + cur.rowcount
            except Exception as e:  # noqa: BLE001
                removed.setdefault("_errors", [])
                if isinstance(removed["_errors"], list):
                    removed["_errors"].append("%s: %s" % (t, type(e).__name__))

        # 3) 账号本身
        cur = conn.execute("DELETE FROM accounts WHERE id=?", (aid,))
        removed["accounts"] = cur.rowcount
        conn.commit()
    finally:
        conn.close()
    return removed


def ensure_tables() -> None:
    """幂等建表（服务启动 / 首次使用时调用）。绝不修改或删除已有数据。"""
    conn = get_personal_conn()
    try:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS accounts (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                username     TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                role         TEXT NOT NULL DEFAULT 'personal',
                display_name TEXT,
                created_at   DATETIME DEFAULT CURRENT_TIMESTAMP
            );

            -- ===== 个人侧（M1.5 已有，此处幂等声明）=====
            CREATE TABLE IF NOT EXISTS products (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                product_name TEXT NOT NULL,
                brand        TEXT,
                category     TEXT,
                price        REAL,
                currency     TEXT DEFAULT 'CNY',
                attributes   TEXT,
                status       TEXT DEFAULT 'active',
                owner_id     INTEGER REFERENCES accounts(id)
            );

            CREATE TABLE IF NOT EXISTS interests (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                owner_id   INTEGER NOT NULL REFERENCES accounts(id),
                interest_tag TEXT,
                description TEXT,              -- 爱好详细描述
                keywords   TEXT                -- 检索关键词(JSON数组文本)，供快讯订阅用
            );

            CREATE TABLE IF NOT EXISTS watchlist (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                owner_id   INTEGER NOT NULL REFERENCES accounts(id),
                brand      TEXT,
                product    TEXT,
                note       TEXT
            );

            -- ===== 公司侧（前置工作新增：按账号隔离，替代 MySQL 全局表）=====
            CREATE TABLE IF NOT EXISTS company_profile (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                owner_id     INTEGER NOT NULL UNIQUE REFERENCES accounts(id),
                company_name TEXT,
                intro        TEXT,          -- 公司简介
                legal_rep    TEXT,          -- 法定代表人
                reg_capital  TEXT,          -- 注册资本（文本，如 '5000万人民币'）
                founded_date TEXT,          -- 成立时间
                employee_scale TEXT,        -- 员工规模
                website      TEXT,
                hq_city      TEXT,          -- 总部城市
                industry     TEXT,          -- 主营领域/行业
                business_scope TEXT,        -- 经营范围/业务描述
                contact      TEXT,          -- 联系方式
                address      TEXT,          -- 地址
                honors       TEXT,          -- 荣誉资质(JSON数组文本)
                note         TEXT
            );

            CREATE TABLE IF NOT EXISTS company_products (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                owner_id     INTEGER NOT NULL REFERENCES accounts(id),
                product_name TEXT NOT NULL,
                category     TEXT,
                price        REAL,
                currency     TEXT DEFAULT 'CNY',
                attributes   TEXT,              -- JSON 扩展列（泛用化）
                status       TEXT DEFAULT 'active'
            );

            CREATE TABLE IF NOT EXISTS company_competitors (
                id                 INTEGER PRIMARY KEY AUTOINCREMENT,
                owner_id           INTEGER NOT NULL REFERENCES accounts(id),
                comp_name          TEXT NOT NULL,
                category           TEXT,
                website            TEXT,
                is_competitor      INTEGER DEFAULT 1,
                note               TEXT,
                mapped_product_ids TEXT             -- JSON 数组文本，如 [1,4]
            );

            -- M4 Digest：订阅与报告（按账号隔离）
            CREATE TABLE IF NOT EXISTS digest_subs (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                owner_id    INTEGER NOT NULL REFERENCES accounts(id),
                scope       TEXT NOT NULL DEFAULT 'personal',   -- company|personal
                name        TEXT,                               -- 订阅显示名
                keywords    TEXT NOT NULL DEFAULT '[]',         -- JSON 数组文本
                schedule    TEXT NOT NULL DEFAULT 'weekly',     -- daily|weekly
                lang        TEXT NOT NULL DEFAULT 'zh',        -- 检索语言: zh|en|both
                enabled     INTEGER NOT NULL DEFAULT 1,
                last_run_at TEXT
                -- 不再限制唯一：同一账号可有多个订阅（不同关键词/范围）
            );
            CREATE TABLE IF NOT EXISTS digest_reports (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                owner_id      INTEGER NOT NULL REFERENCES accounts(id),
                title         TEXT,
                md_path       TEXT,
                pdf_path      TEXT,
                item_count    INTEGER DEFAULT 0,
                status        TEXT DEFAULT 'done',              -- done|failed|running
                coverage_note TEXT,
                created_at    TEXT DEFAULT CURRENT_TIMESTAMP
            );

            -- ===== 定制助手：用户自定义大模型接口（按账号隔离）=====
            CREATE TABLE IF NOT EXISTS llm_providers (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                owner_id   INTEGER NOT NULL REFERENCES accounts(id),
                provider_name TEXT NOT NULL,          -- 显示名，如「DeepSeek」
                model_name TEXT NOT NULL,             -- 模型名，如 deepseek-chat
                base_url   TEXT NOT NULL,             -- OpenAI 兼容接口地址
                api_key    TEXT NOT NULL,             -- 密钥
                is_active  INTEGER DEFAULT 0,         -- 1=当前启用
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            );

            -- ===== M1 上下文工程化：AI 会话持久化（多会话管理，按账号隔离）=====
            -- conversations: 会话元数据（thread_id = LangGraph 线程兼业务主键）
            CREATE TABLE IF NOT EXISTS conversations (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                account_id    INTEGER NOT NULL REFERENCES accounts(id),
                thread_id     TEXT NOT NULL UNIQUE,   -- uuid4().hex，前端切换会话用
                title         TEXT,                   -- 首问前 20 字，可手动重命名
                token_usage   INTEGER DEFAULT 0,      -- 累计 token 估算
                message_count INTEGER DEFAULT 0,      -- 历史消息数
                created_at    DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at    DATETIME DEFAULT CURRENT_TIMESTAMP
            );
            CREATE INDEX IF NOT EXISTS idx_conversations_account
                ON conversations(account_id, updated_at DESC);

            -- messages: 对话消息（OpenAI 格式 JSON 存 content，兼容多模态/工具结果）
            CREATE TABLE IF NOT EXISTS messages (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                conversation_id  INTEGER NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
                turn_index       INTEGER NOT NULL,    -- 轮次序号（0=首问，逐轮递增）
                role             TEXT NOT NULL,       -- user|assistant|tool|system
                content          TEXT,                -- 主内容（JSON 序列化）
                name             TEXT,                -- tool role 的工具名
                tool_call_id     TEXT,                -- tool role 对应 assistant.tool_calls 的 id
                tool_calls       TEXT,                -- assistant 的工具调用 JSON
                additional_kwargs TEXT,               -- reasoning_content 等扩展字段 JSON
                kb_ingested      INTEGER DEFAULT 0,   -- M3：该 assistant 消息的报告已入过知识库（防重复入库）
                created_at       DATETIME DEFAULT CURRENT_TIMESTAMP
            );
            CREATE INDEX IF NOT EXISTS idx_messages_conv
                ON messages(conversation_id, turn_index);

            -- ===== M4c 第三方 CLI 接入登记（按账号隔离；命令由用户自己终端执行）=====
            CREATE TABLE IF NOT EXISTS third_party_clis (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                account_id INTEGER NOT NULL REFERENCES accounts(id),
                cli_id     TEXT NOT NULL,                  -- 注册表 id：lark|wecom|dingtalk|google
                state      TEXT NOT NULL DEFAULT 'none',   -- none|installed|authed
                note       TEXT DEFAULT '',                -- 用户备注（版本 / 遇到的问题）
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(account_id, cli_id)
            );

            -- ===== M4c（配置导向版）：用户自己配置的 CLI —— 不预设厂商 =====
            CREATE TABLE IF NOT EXISTS user_clis (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                account_id  INTEGER NOT NULL REFERENCES accounts(id),
                name        TEXT NOT NULL,                  -- 显示名（如「飞书」「公司内部工具」）
                bin         TEXT NOT NULL,                  -- 可执行名（纯名字，PATH 里叫什么填什么）
                install_cmd TEXT DEFAULT '',                -- 安装命令（展示用，在你自己的终端跑）
                auth_cmd    TEXT DEFAULT '',                -- 认证命令（展示用）
                docs        TEXT DEFAULT '',                -- 文档链接
                readonly    TEXT DEFAULT '',                -- 只读命令清单：每行一条（空 = 不放行 Agent 代跑）
                abilities   TEXT DEFAULT '',                -- M5：能力描述（用户语言：触发词 + 能力→命令映射；空 = 回退命令名简报）
                state       TEXT NOT NULL DEFAULT 'none',   -- none|installed|authed
                note        TEXT DEFAULT '',
                created_at  DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at  DATETIME DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(account_id, bin)
            );

            -- ===== M5b（统一能力目录）：路由用的能力池 —— 三种来源（cli/api/mcp）共用一张表 =====
            -- 设计口径见 docs/v2.0/M5b工具检索路由.md §2：
            --   CLI 侧是**派生**（从 user_clis 同步而来，不搬家），api/mcp 实装后各写各的来源表再派生进这里；
            --   路由器与注入层只认这张表，不感知来源。
            CREATE TABLE IF NOT EXISTS tool_capabilities (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                account_id  INTEGER NOT NULL REFERENCES accounts(id),
                source      TEXT NOT NULL,                  -- cli | api | mcp
                ref         TEXT NOT NULL,                  -- cli: bin │ api: tool_id │ mcp: server/tool
                name        TEXT NOT NULL DEFAULT '',       -- 展示名
                keywords    TEXT NOT NULL DEFAULT '',       -- 关键词行（路由主信号）
                abilities   TEXT NOT NULL DEFAULT '',       -- 用户语言映射（说法 → 命令/用途）
                invoke_hint TEXT NOT NULL DEFAULT '',       -- 调用形态（模型可见的精简版）
                enabled     INTEGER NOT NULL DEFAULT 1,     -- 0 = 不进路由池（如未接入/清单为空）
                updated_at  DATETIME DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(account_id, source, ref)
            );

            -- 池版本：任何写入口变更后 +1，路由器据此失效缓存（不用 TTL）
            CREATE TABLE IF NOT EXISTS tool_pool_meta (
                account_id   INTEGER PRIMARY KEY REFERENCES accounts(id),
                pool_version INTEGER NOT NULL DEFAULT 0,
                updated_at   DATETIME DEFAULT CURRENT_TIMESTAMP
            );
            """
        )

        # -- 老库平滑升级：缺失列用 ALTER TABLE 补齐（幂等）--
        cols = {r[1] for r in conn.execute("PRAGMA table_info(interests)").fetchall()}
        if "description" not in cols:
            conn.execute("ALTER TABLE interests ADD COLUMN description TEXT")
        if "keywords" not in cols:
            conn.execute("ALTER TABLE interests ADD COLUMN keywords TEXT")
        # -- digest_subs 老库补列：lang（检索语言 zh|en|both）--
        ds_cols = {r[1] for r in conn.execute("PRAGMA table_info(digest_subs)").fetchall()}
        if "lang" not in ds_cols:
            conn.execute("ALTER TABLE digest_subs ADD COLUMN lang TEXT NOT NULL DEFAULT 'zh'")
        # -- messages 老库补列：kb_ingested（防报告重复入库）--
        msg_cols = {r[1] for r in conn.execute("PRAGMA table_info(messages)").fetchall()}
        if "kb_ingested" not in msg_cols:
            conn.execute("ALTER TABLE messages ADD COLUMN kb_ingested INTEGER DEFAULT 0")
        # -- company_profile 老库补列 --
        # M5：user_clis 加「能力描述」列（老库幂等补齐；空值 = 保持 M4c 的命令名简报形态）
        try:
            uc_cols = {r[1] for r in conn.execute("PRAGMA table_info(user_clis)").fetchall()}
            if uc_cols and "abilities" not in uc_cols:
                conn.execute("ALTER TABLE user_clis ADD COLUMN abilities TEXT DEFAULT ''")
        except Exception as e:
            print("[schema] user_clis.abilities 迁移跳过:", e)

        cp_cols = {r[1] for r in conn.execute("PRAGMA table_info(company_profile)").fetchall()}
        for col, ddl in [
            ("intro", "ALTER TABLE company_profile ADD COLUMN intro TEXT"),
            ("legal_rep", "ALTER TABLE company_profile ADD COLUMN legal_rep TEXT"),
            ("reg_capital", "ALTER TABLE company_profile ADD COLUMN reg_capital TEXT"),
            ("founded_date", "ALTER TABLE company_profile ADD COLUMN founded_date TEXT"),
            ("employee_scale", "ALTER TABLE company_profile ADD COLUMN employee_scale TEXT"),
            ("website", "ALTER TABLE company_profile ADD COLUMN website TEXT"),
            ("hq_city", "ALTER TABLE company_profile ADD COLUMN hq_city TEXT"),
            ("industry", "ALTER TABLE company_profile ADD COLUMN industry TEXT"),
            ("business_scope", "ALTER TABLE company_profile ADD COLUMN business_scope TEXT"),
            ("contact", "ALTER TABLE company_profile ADD COLUMN contact TEXT"),
            ("address", "ALTER TABLE company_profile ADD COLUMN address TEXT"),
            ("honors", "ALTER TABLE company_profile ADD COLUMN honors TEXT"),
        ]:
            if col not in cp_cols:
                conn.execute(ddl)
        conn.commit()
        # -- digest_subs 老库升级：去掉 owner_id+scope 唯一约束（支持多订阅）--
        # 用「重建表」方式最稳妥：建无约束新表 -> 迁移数据 -> 删旧表 -> 重命名。
        try:
            idxs = conn.execute("PRAGMA index_list(digest_subs)").fetchall()
            has_unique = False
            for _r in idxs:
                _unique = _r[2]
                _name = _r[1] or ""
                if (str(_unique) in ("1", "c") or _unique is True) and "autoindex_digest_subs" in _name:
                    has_unique = True
                    break
            if has_unique:
                conn.execute(
                    "CREATE TABLE digest_subs_new ("
                    "id INTEGER PRIMARY KEY AUTOINCREMENT, owner_id INTEGER NOT NULL REFERENCES accounts(id), "
                    "scope TEXT NOT NULL DEFAULT 'personal', name TEXT, keywords TEXT NOT NULL DEFAULT '[]', "
                    "schedule TEXT NOT NULL DEFAULT 'weekly', enabled INTEGER NOT NULL DEFAULT 1, last_run_at TEXT)"
                )
                conn.execute(
                    "INSERT INTO digest_subs_new (id, owner_id, scope, name, keywords, schedule, enabled, last_run_at) "
                    "SELECT id, owner_id, scope, name, keywords, schedule, enabled, last_run_at FROM digest_subs")
                conn.execute("DROP TABLE digest_subs")
                conn.execute("ALTER TABLE digest_subs_new RENAME TO digest_subs")
        except Exception as e:
            print("[schema] digest_subs 迁移跳过:", e)
        conn.commit()
    finally:
        conn.close()


def init_personal_db() -> None:
    """【仅用于重置演示库】DROP 全部表后重建，写入 alice 样例数据。
    服务运行期间不要调用；新用户走 register()，空置起步。"""
    conn = get_personal_conn()
    cur = conn.cursor()
    cur.executescript(
        """
        DROP TABLE IF EXISTS digest_reports;
        DROP TABLE IF EXISTS digest_subs;
        DROP TABLE IF EXISTS company_competitors;
        DROP TABLE IF EXISTS company_products;
        DROP TABLE IF EXISTS company_profile;
        DROP TABLE IF EXISTS watchlist;
        DROP TABLE IF EXISTS interests;
        DROP TABLE IF EXISTS products;
        DROP TABLE IF EXISTS accounts;

        CREATE TABLE accounts (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            username     TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            role         TEXT NOT NULL DEFAULT 'personal',
            display_name TEXT,
            created_at   DATETIME DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE products (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            product_name TEXT NOT NULL,
            brand        TEXT,
            category     TEXT,
            price        REAL,
            currency     TEXT DEFAULT 'CNY',
            attributes   TEXT,
            status       TEXT DEFAULT 'active',
            owner_id     INTEGER REFERENCES accounts(id)
        );

        CREATE TABLE interests (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            owner_id   INTEGER NOT NULL REFERENCES accounts(id),
            interest_tag TEXT,
            description TEXT,
            keywords   TEXT
        );

        CREATE TABLE watchlist (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            owner_id   INTEGER NOT NULL REFERENCES accounts(id),
            brand      TEXT,
            product    TEXT,
            note       TEXT
        );

        CREATE TABLE company_profile (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            owner_id     INTEGER NOT NULL UNIQUE REFERENCES accounts(id),
            company_name TEXT,
            intro TEXT, legal_rep TEXT, reg_capital TEXT, founded_date TEXT,
            employee_scale TEXT, website TEXT, hq_city TEXT, industry TEXT,
            business_scope TEXT, contact TEXT, address TEXT, honors TEXT, note TEXT
        );

        CREATE TABLE company_products (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            owner_id     INTEGER NOT NULL REFERENCES accounts(id),
            product_name TEXT NOT NULL,
            category     TEXT,
            price        REAL,
            currency     TEXT DEFAULT 'CNY',
            attributes   TEXT,
            status       TEXT DEFAULT 'active'
        );

        CREATE TABLE company_competitors (
            id                 INTEGER PRIMARY KEY AUTOINCREMENT,
            owner_id           INTEGER NOT NULL REFERENCES accounts(id),
            comp_name          TEXT NOT NULL,
            category           TEXT,
            website            TEXT,
            is_competitor      INTEGER DEFAULT 1,
            note               TEXT,
            mapped_product_ids TEXT
        );
        """
    )

    # ---------- alice 演示样例（个人侧耳机/显卡）----------
    cur.execute(
        "INSERT INTO accounts (username, password_hash, role, display_name) VALUES (?,?,?,?)",
        ("alice", "fakehash_not_real", "personal", "Alice（耳机爱好者）"),
    )
    alice = cur.lastrowid

    products = [
        ("Sony WH-1000XM5", "Sony", "耳机", 2499.0, '{"type":"头戴","battery":"30h","anc":"强","weight":"250g"}'),
        ("Bose QC45", "Bose", "耳机", 2299.0, '{"type":"头戴","battery":"24h","anc":"强","weight":"240g"}'),
        ("AirPods Pro 2", "Apple", "耳机", 1899.0, '{"type":"入耳","battery":"6h(单次)","anc":"强","weight":"5.3g"}'),
        ("RTX 4070", "NVIDIA", "显卡", 4499.0, '{"vram":"12GB","type":"中端","tdp":"200W","benchmark":"+30% vs 3070"}'),
        ("RTX 4090", "NVIDIA", "显卡", 12999.0, '{"vram":"24GB","type":"旗舰","tdp":"450W","benchmark":"顶级"}'),
    ]
    cur.executemany(
        "INSERT INTO products (product_name, brand, category, price, attributes, owner_id) VALUES (?,?,?,?,?,?)",
        [(p[0], p[1], p[2], p[3], p[4], alice) for p in products],
    )

    interests = [("无线耳机",), ("显卡",), ("咖啡机",)]
    cur.executemany(
        "INSERT INTO interests (owner_id, interest_tag) VALUES (?,?)",
        [(alice, t[0]) for t in interests],
    )

    watchlist = [
        ("Sony", "WH-1000XM5", "主力候选"),
        ("Bose", "QC45", "降噪对比"),
        ("NVIDIA", "RTX 4070", "装机备选"),
    ]
    cur.executemany(
        "INSERT INTO watchlist (owner_id, brand, product, note) VALUES (?,?,?,?)",
        [(alice, w[0], w[1], w[2]) for w in watchlist],
    )

    # ---------- demo_company 演示样例（公司侧云协科技，原 MySQL 数据迁入）----------
    import hashlib
    demo_pwd = hashlib.sha256(("zhixuan_local_salt_v1" + "demo123").encode()).hexdigest()
    cur.execute(
        "INSERT INTO accounts (username, password_hash, role, display_name) VALUES (?,?,?,?)",
        ("demo_company", demo_pwd, "company", "云协科技（演示）"),
    )
    demo = cur.lastrowid
    cur.execute(
        "INSERT INTO company_profile (owner_id, company_name, note) VALUES (?,?,?)",
        (demo, "云协科技", "国产企业协作套件厂商（演示账号，原 M1 MySQL 数据）"),
    )
    demo_products = [
        ("云协IM", "IM", 360.00, "即时通讯,已读回执,万人群,安全合规", "中小企业"),
        ("云协文档", "文档", 480.00, "在线协作文档,表格,知识库,模板中心", "中大型"),
        ("云协会议", "会议", 300.00, "高清会议,屏幕共享,实时字幕,会议纪要", "全规模"),
        ("云协OA", "OA", 600.00, "审批流,考勤,人事,报表,低代码搭建", "中大型"),
        ("云协免费版", "IM", 0.00, "基础IM,100人上限,有限存储", "中小企业"),
    ]
    cur.executemany(
        "INSERT INTO company_products (owner_id, product_name, category, price, attributes) "
        "VALUES (?,?,?,?,?)",
        [(demo, n, c, p, f'{{"core_features":"{f}","target_scale":"{s}","billing_unit":"人/年"}}')
         for (n, c, p, f, s) in demo_products],
    )
    demo_comp = [
        ("飞书", "协作套件", "https://www.feishu.cn", 1, "字节系，强在文档与OKR", "[1,2,4]"),
        ("钉钉", "协作套件", "https://www.dingtalk.com", 1, "阿里系，强在OA与生态", "[1,4]"),
        ("企业微信", "协作套件", "https://work.weixin.qq.com", 1, "腾讯系，微信生态连接", "[1]"),
        ("Slack", "IM", "https://slack.com", 0, "海外标杆，对标参考", "[1]"),
        ("Notion", "文档", "https://www.notion.so", 0, "海外文档知识库标杆", "[2]"),
    ]
    cur.executemany(
        "INSERT INTO company_competitors (owner_id, comp_name, category, website, is_competitor, note, mapped_product_ids) "
        "VALUES (?,?,?,?,?,?,?)",
        [(demo, *c) for c in demo_comp],
    )

    conn.commit()
    conn.close()


if __name__ == "__main__":
    ensure_tables()
    print(f"表结构就绪（幂等，未动数据）：{DB_PATH}")
