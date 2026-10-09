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
        # M4-1（2026-09-27 修）：老库补两列。**必须放在真的会执行到的路径上** ——
        #   第一版塞进了"if intro 列缺失"的分支里，永远不跑；第二版误放进 except 体里，同样不跑。
        #   表还不存在时 PRAGMA 返回空 → 靠 try 兜住，随后由带新列的 DDL 建表。
        try:
            _have = {r[1] for r in conn.execute("PRAGMA table_info(usage_events)")}
            for _col in ("turn_index", "result_chars"):
                if _have and _col not in _have:
                    conn.execute("ALTER TABLE usage_events ADD COLUMN %s INTEGER NOT NULL DEFAULT 0" % _col)
            conn.commit()
        except Exception as _e:
            print("[schema] usage_events 补列跳过:", _e)

        # M5-1：products / company_products 加列（方案 §5.1）。同一套幂等写法。
        #   教训：这两段都必须待在**函数体顶层**（与外层 try 同级），一旦嵌进别人的 if/except 里就永不执行，
        #   而且不报错、只是"列悄悄没加上" —— 所以每次改完都要**真跑一次并断言列存在**。
        # 再给 price_alerts 补 check_error（方案 §3.5 要求 G8 失败不静默，但 §5.2 的 DDL 漏了这列）
        try:
            _pa = {r[1] for r in conn.execute("PRAGMA table_info(price_alerts)")}
            if _pa and "check_error" not in _pa:
                conn.execute("ALTER TABLE price_alerts ADD COLUMN check_error TEXT")
            conn.commit()
        except Exception as _e:
            print("[schema] price_alerts 补列跳过:", _e)

        try:
            for _tbl, _cols in (
                    ("products", [("url", "TEXT"), ("target_price", "REAL"), ("alert_drop_pct", "REAL"),
                                  ("monitor_enabled", "INTEGER"), ("last_price", "REAL")]),
                    ("company_products", [("url", "TEXT"), ("target_price", "REAL"),
                                          ("monitor_enabled", "INTEGER"), ("last_price", "REAL")])):
                _have = {r[1] for r in conn.execute("PRAGMA table_info(%s)" % _tbl)}
                for _name, _type in _cols:
                    if _have and _name not in _have:
                        conn.execute("ALTER TABLE %s ADD COLUMN %s %s" % (_tbl, _name, _type))
            conn.commit()
        except Exception as _e:
            print("[schema] M5-1 加列跳过:", _e)

        # M6c-1（2026-09-29）：老库补列 —— llm_providers 三处单价 + usage_events token/成本十列。
        #   教训照旧（见 M5-1 注释）：**必须待在函数体顶层**，嵌进别人的 if/except 就永不执行、还不报错。
        try:
            for _tbl, _cols in (
                    ("llm_providers", [("price_in_cached", "REAL"), ("price_in_uncached", "REAL"),
                                       ("price_out", "REAL"), ("supports_vision", "INTEGER DEFAULT 0")]),
                    ("usage_events", [("provider_id", "INTEGER"), ("model_name", "TEXT"),
                                      ("input_tokens", "INTEGER"), ("output_tokens", "INTEGER"),
                                      ("cached_tokens", "INTEGER"), ("uncached_tokens", "INTEGER"),
                                      ("token_source", "TEXT"), ("cost", "REAL"),
                                      ("cost_note", "TEXT"), ("price_snapshot", "TEXT"),
                                      ("recalculated_at", "TEXT")])):
                _have = {r[1] for r in conn.execute("PRAGMA table_info(%s)" % _tbl)}
                for _name, _type in _cols:
                    if _have and _name not in _have:
                        conn.execute("ALTER TABLE %s ADD COLUMN %s %s" % (_tbl, _name, _type))
            conn.commit()
        except Exception as _e:
            print("[schema] M6c 加列跳过:", _e)

        # ★ v3.1：视觉开关的一次性默认（**必须在上面加列之后** ✓ 幂等、只动 NULL 行 ✓）
        #   ① 名字里带 agnes 的（项目默认模型，本身多模态 ✓）→ 1；② 其余老行 NULL → 0（显式"不能"，避免三态 ✓）
        try:
            conn.execute("UPDATE llm_providers SET supports_vision=1 WHERE supports_vision IS NULL"
                         " AND lower(model_name) LIKE '%agnes%'")
            conn.execute("UPDATE llm_providers SET supports_vision=0 WHERE supports_vision IS NULL")
            conn.commit()
        except Exception as _e_v:
            print("[schema] 视觉开关默认值跳过:", _e_v)

        # M6c-6：failure_events.account_id 放开为可空（外壳失败发生在登录前，没有账号）。
        #   ★ 守卫：**仅当表为空**才重建（有数据宁可留着旧约束，绝不静默丢数据）；
        #   SQLite 改不了列的 NOT NULL，只能重建 —— 这也是把它放在这里而不是放"迁移"里的原因。
        try:
            _info = {r[1]: r for r in conn.execute("PRAGMA table_info(failure_events)")}
            _acct = _info.get("account_id")
            if _acct is not None and _acct[3] == 1:          # notnull == 1
                _n = conn.execute("SELECT COUNT(*) FROM failure_events").fetchone()[0]
                if _n == 0:
                    conn.execute("DROP TABLE failure_events")
                    conn.execute("""CREATE TABLE failure_events (
                        id          INTEGER PRIMARY KEY AUTOINCREMENT,
                        account_id  INTEGER REFERENCES accounts(id),
                        code        TEXT NOT NULL,
                        detail      TEXT,
                        occurred_at TEXT NOT NULL
                    )""")
                    conn.execute("CREATE INDEX IF NOT EXISTS idx_failure_time ON failure_events(occurred_at DESC)")
                    conn.execute("CREATE INDEX IF NOT EXISTS idx_failure_acct ON failure_events(account_id, occurred_at DESC)")
                    conn.commit()
                    print("[schema] failure_events.account_id 已放开为可空（表为空，安全重建）")
        except Exception as _e:
            print("[schema] failure_events 放开 NOT NULL 跳过:", _e)
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

            -- ===== 登录会话（★ 2026-09-23 由「进程内内存字典」改为落库）=====
            -- 为什么改：桌面版「记住登录」把 token 存在浏览器 localStorage 里，而桌面壳每次
            -- 重开都会新起一个后端进程 —— 内存版会话一重启就全失效，用户看到的是
            -- 「界面已登录、工作台数据全是 0、天气卡报未登录或登录已失效」（用户实测报障）。
            -- 落库后 token 跨后端重启仍然有效；表里只存 token→账号 的映射，
            -- 用户名/角色/昵称等**运行时从 accounts 联查**，避免资料改了会话里还是旧值。
            CREATE TABLE IF NOT EXISTS sessions (
                token      TEXT PRIMARY KEY,
                account_id INTEGER NOT NULL REFERENCES accounts(id),
                login_at   REAL NOT NULL
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
                owner_id     INTEGER REFERENCES accounts(id),
                price_kind   TEXT,          -- Q6：'simple' / 'rule' / NULL（未定价）
                price_text   TEXT           -- Q6：规则文本价（price_kind='rule' 时用）
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
                status       TEXT DEFAULT 'active',
                price_kind   TEXT,          -- Q6：'simple' / 'rule' / NULL（未定价）
                price_text   TEXT           -- Q6：规则文本价（price_kind='rule' 时用）
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
                -- ★ v3.1 视觉能力：1=这套配置**能收图片**（前端「模型选型」勾选 ✓ 配置导向，不写死厂商 ✗）
                --   0/NULL=不能收 → 带图提问会被明确拦下（**绝不静默丢图** ✗）
                supports_vision INTEGER DEFAULT 0,
                -- ★ M6c-1：三处**可选填**单价（单位固定 元/百万 tokens；NULL=未配置即不折算，
                --   填 0 = 明确免费 → 折算成 ¥0.00，两者语义不同）
                price_in_cached   REAL,               -- 输入价（缓存命中）
                price_in_uncached REAL,               -- 输入价（缓存未命中）
                price_out         REAL,               -- 输出价
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
                docs        TEXT DEFAULT '',                -- 能力描述的依据：网址 / 本机文件路径（模式见 docs_mode；help 模式此列为空）
                docs_mode   TEXT DEFAULT 'url',             -- 依据来源三选一：url（网址）| path（本机文件）| help（跑 --help 取证）
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
                ref         TEXT NOT NULL,                  -- cli: <bin> │ api: <服务名>#<操作名> │ mcp: <server>/<tool>
                                                            --   （v3.0 M1 定稿：api 用「可读且稳定」的 <服务名>#<操作名>，
                                                            --    来源行 id 另存 invoke_spec.source_id 供溯源）
                name        TEXT NOT NULL DEFAULT '',       -- 展示名
                keywords    TEXT NOT NULL DEFAULT '',       -- 关键词行（路由主信号）
                abilities   TEXT NOT NULL DEFAULT '',       -- 用户语言映射（说法 → 命令/用途）
                invoke_hint TEXT NOT NULL DEFAULT '',       -- 调用形态（模型可见的精简版）
                enabled     INTEGER NOT NULL DEFAULT 1,     -- 0 = 不进路由池（如未接入/清单为空）
                -- ===== v3.0 M1（API/MCP 适配框架）：能力演进的五个新槽位（老库由下方 ALTER 幂等补齐）=====
                invoke_spec  TEXT NOT NULL DEFAULT '{}',    -- ★ 执行侧元数据（模型不可见）：
                                                            --   api: {source_id,method,url_template,bindings[{name,location,required}],body_mode,auth}
                                                            --   mcp: {server_id,tool,annotations}
                input_schema TEXT NOT NULL DEFAULT '{}',    -- ★ JSON Schema（模型可见）；CLI 可空
                read_only    INTEGER NOT NULL DEFAULT 1,    -- 0 = 会改外部状态；1 = 只读
                confirmed_at DATETIME,                      -- 人工确认时间。★语义只对 source in ('api','mcp') 生效：
                                                            --   NULL = 未确认 → 不入池、不可调用；
                                                            --   CLI 走既有 user_clis 只读清单放行，**CLI 行本列恒为 NULL 是正常的**
                                                            --   （升级老库时绝不能用本列做放行判定，否则会锁死现有 CLI 工具）
                cost_hint    TEXT NOT NULL DEFAULT '',      -- 可选：计费/配额提示（给成本表用）
                -- ===== M5c-2'（2026-09-27）：工具级描述的用户手写版 =====
                abilities_user TEXT NOT NULL DEFAULT '',    -- ★ 用户手写的**工具级**描述（在「工具描述」弹窗里填 / 一键导入 AI 草稿）。
                                                            --   空 = 用 abilities（OpenAPI summary / MCP 服务自述）自动摘要。
                                                            --   `_entry_from_row` 把它顶到合并文本的**工具级**位置，来源级描述仍保留在后面 ——
                                                            --   于是它既进模型看的卡片，也进路由的向量文本（这是 precision 上不去的正解）。
                updated_at  DATETIME DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(account_id, source, ref)
            );

            -- ===== v3.0 M1（API/MCP 适配框架）：工具来源表 =====
            -- 设计口径见 docs/v3.0/M1M2-API与MCP适配框架方案.md §5.1：
            --   一张表容纳 api / mcp 两种来源；config_json 是**类型扩展位**（加新来源类型不用改表结构）。
            --   模型可见契约**不在这里**——它在 tool_capabilities（派生后的能力）。
            CREATE TABLE IF NOT EXISTS tool_sources (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                account_id  INTEGER NOT NULL REFERENCES accounts(id),
                source      TEXT NOT NULL,                  -- api | mcp
                slug        TEXT NOT NULL,                  -- 短名（ref 里用它）：weather / fs
                name        TEXT NOT NULL DEFAULT '',       -- 展示名（「和风天气」「本地文件系统」）
                config_json TEXT NOT NULL DEFAULT '{}',     -- ★ 类型扩展位：
                    -- api: {"base_url":"https://…","auth":{"type":"header","name":"Authorization","prefix":"Bearer ","secret":"…"},
                    --       "headers":{…},"timeout":30,"max_bytes":1048576}
                    -- mcp: {"transport":"stdio","mcpServers":{"fs":{"command":"npx","args":[…],"env":{…}}}}
                    --      或 {"transport":"http","server_url":"http://127.0.0.1:8765/mcp","auth":{…}}
                abilities   TEXT NOT NULL DEFAULT '',       -- 可选：来源整体的人话说明（进来源级能力卡）
                docs        TEXT NOT NULL DEFAULT '',       -- 文档地址：**网址或本地文件路径**（README/文档，能力撰写 AI 的依据之一）
                -- ★ M5c-2'（2026-09-27）：第三方 MCP/API 的 README 往往只讲"怎么接入"和技术栈，
                --   能力文案在社区/市场页上（有些 API 干脆没有 README）→ 多一个可选的"官方介绍文案"：
                intro       TEXT NOT NULL DEFAULT '',        -- 用户整段复制的官方/社区介绍文案（能力撰写 AI 的依据之二）
                state       TEXT NOT NULL DEFAULT 'none',   -- none | configured | verified | failed（体检结果）
                enabled     INTEGER NOT NULL DEFAULT 1,
                note        TEXT DEFAULT '',
                created_at  DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at  DATETIME DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(account_id, source, slug)
            );

            -- 池版本：任何写入口变更后 +1，路由器据此失效缓存（不用 TTL）
            CREATE TABLE IF NOT EXISTS tool_pool_meta (
                account_id   INTEGER PRIMARY KEY REFERENCES accounts(id),
                pool_version INTEGER NOT NULL DEFAULT 0,
                updated_at   DATETIME DEFAULT CURRENT_TIMESTAMP
            );

            -- ===== v3.0 M3-9：画像版本快照（D9，用户 2026-09-25 新增）=====
            -- 每条 = 一份**被替换掉的画像**：只存 `## USER PROFILE` 段正文（笔记段不参与版本化）。
            -- 每账号只留最近 3 份（写入后立刻裁剪），四类写路径**改动之前**各留一份 →
            -- 详见 tools/memory_snapshots.py。purge_account() 自省 sqlite_master
            -- （凡带 account_id / owner_id 的表都清）→ 这张表**无需改它**。
            CREATE TABLE IF NOT EXISTS memory_snapshots (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                account_id INTEGER NOT NULL,
                content    TEXT    NOT NULL,       -- 画像段正文（≤8000 字）
                reason     TEXT    NOT NULL,       -- init | suggest_import | manual_edit | agent_update | restore
                chars      INTEGER NOT NULL,
                created_at TEXT    NOT NULL
            );

            -- ===== v3.0 M4-1：额度/用量事件（D3：**永不清理**）=====
            -- 每行 = 一次工具调用。**异常/超时也计**（额度已经花掉了，不计就是自欺）。
            -- kind 分类见 agent/usage_counter.py 的 KIND_* 常量；只有 retrieval_external 有额度成本。
            -- 聚合一律走 idx_usage_account_time（D3 不清理 → 表会随账号长期使用增长）。

            -- ===== v3.0 M5-1：价格台账三张表（方案 §5.2 **原文照抄**；G4：来源类型必须区分）=====
            -- price_history=人填/对话/阶段2 API 的**成交或报价**；price_mentions=**资讯价**（周报抽取，绝不混进 history，
            -- 因为资讯里的价格 ≠ 当前售价，混进去就是误导用户）；price_alerts=提醒与检查错误（G8 不静默）。
            -- 价格台账（人填 / 对话 / 阶段2 API）
            CREATE TABLE IF NOT EXISTS price_history (
              id          INTEGER PRIMARY KEY AUTOINCREMENT,
              account_id  INTEGER NOT NULL,
              item_type   TEXT NOT NULL,     -- product | company_product | competitor_product
              item_id     INTEGER,           -- 对应表主键（competitor 时可为 NULL）
              item_name   TEXT,              -- 冗余名称（防止源行被删后无据可查）
              price       REAL NOT NULL,
              currency    TEXT DEFAULT 'CNY',
              source_type TEXT NOT NULL CHECK (source_type IN ('manual','api')),   -- ★ G4 硬闸：资讯价只进 price_mentions
              source_ref  TEXT,              -- 谁记的：username / 'agent' / 源配置名
              observed_at TEXT NOT NULL,     -- 观测时间（价格属于那一刻）
              note        TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_ph_item ON price_history(account_id, item_type, item_id, observed_at DESC);

            -- 资讯价（周报抽取，绝不进 price_history）
            CREATE TABLE IF NOT EXISTS price_mentions (
              id          INTEGER PRIMARY KEY AUTOINCREMENT,
              account_id  INTEGER NOT NULL,
              report_id   INTEGER,           -- digest_reports.id
              entity      TEXT NOT NULL,     -- 抽取出的商品/实体名
              price       REAL,
              price_text  TEXT,              -- 原文价格串（如 "89元起"）
              currency    TEXT DEFAULT 'CNY',
              shop        TEXT,              -- 京东自营 / 天猫旗舰店…
              source_url  TEXT,
              seen_at     TEXT NOT NULL,
              linked_item_type TEXT,         -- 匹配到的商品：product | company_product | NULL
              linked_item_id   INTEGER,
              match_score REAL
            );
            CREATE INDEX IF NOT EXISTS idx_pm_entity ON price_mentions(account_id, entity);

            -- 提醒（触发历史 + 去重 + 错误）
            CREATE TABLE IF NOT EXISTS price_alerts (
              id          INTEGER PRIMARY KEY AUTOINCREMENT,
              account_id  INTEGER NOT NULL,
              item_type   TEXT NOT NULL,
              item_id     INTEGER,
              rule        TEXT NOT NULL,     -- below_target | new_low | competitor_gap | check_error
              price       REAL,
              detail      TEXT,              -- 如 "竞品低 15%" / 错误摘要
              fired_at    TEXT NOT NULL,
              acked_at    TEXT,              -- 桌面壳弹过通知后回写（防重复弹）
              in_digest   INTEGER DEFAULT 0  -- 是否已收进周报附录
            );

            -- M5-1 补：方案 §5.2 没给 price_alerts 建索引，但提醒列表/ack/去重都要按
            -- (账号, 触发时间) 查它 —— 不加索引以后会随提醒数变多而变慢。
            CREATE INDEX IF NOT EXISTS idx_pa_acct ON price_alerts(account_id, fired_at DESC);
            CREATE TABLE IF NOT EXISTS usage_events (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                account_id INTEGER NOT NULL REFERENCES accounts(id),
                thread_id  TEXT    DEFAULT '',
                turn_index INTEGER NOT NULL DEFAULT 0,-- ★ M4-4 需要"本轮"这一维（方案 §3.1 的列）
                kind       TEXT    NOT NULL,          -- internal | retrieval_external | shell | api | mcp
                tool_name  TEXT    DEFAULT '',
                source     TEXT    DEFAULT '',        -- 具体来源（网络搜索 / api:steam#searchGame / mcp:pdd/…）
                ok         INTEGER NOT NULL DEFAULT 1,-- 1 成功 / 0 异常或超时（两者都计）
                latency_ms INTEGER NOT NULL DEFAULT 0,
                result_chars INTEGER NOT NULL DEFAULT 0,  -- ★ 返回字符数（成本卡/排查"这次拉回来多少"）
                -- ★ M6c-1：模型用量（kind='model' 用；工具行全留 NULL）
                provider_id    INTEGER,               -- 哪套模型配置（C3：账目按 provider 维度）
                model_name     TEXT,
                input_tokens   INTEGER,
                output_tokens  INTEGER,
                cached_tokens  INTEGER,               -- 缓存命中输入
                uncached_tokens INTEGER,              -- 缓存未命中输入
                token_source   TEXT,                  -- reported|no_cache_detail|unavailable|unknown_shape
                cost           REAL,                  -- NULL = 未折算（未配价 / 无用量）
                cost_note      TEXT,                  -- no_cache_detail|no_price|unavailable|unknown_shape
                price_snapshot TEXT,                  -- 折算时用的单价 JSON 快照（可追溯）
                recalculated_at TEXT,                 -- ★ M6c-4：显式"按当前单价重算"的时间（NULL=按当时单价折算）
                created_at TEXT    NOT NULL,          -- 'YYYY-mm-dd HH:MM:SS'（人看）
                created_ts REAL    NOT NULL           -- 同刻时间戳（聚合/排序用，避免字符串比较）
            );
            CREATE INDEX IF NOT EXISTS idx_usage_account_time ON usage_events(account_id, created_ts DESC);
            CREATE INDEX IF NOT EXISTS idx_mem_snap_acct ON memory_snapshots(account_id, id DESC);
            -- ★ M6c-1 Part B：失败事件独立表（失败不是用量，混表会给成本卡添噪声）
            CREATE TABLE IF NOT EXISTS failure_events (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                -- ★ account_id **可空**：外壳失败（端口被占 / 后端崩溃）发生在**登录之前**，
                --   那时没有账号可归属（方案 §5.1 原文就是可空的 `account_id INTEGER`）。
                account_id  INTEGER REFERENCES accounts(id),
                code        TEXT NOT NULL,        -- exit_codes.py 里的枚举名
                detail      TEXT,                 -- 原始字符串/退出码/日志摘要
                occurred_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_failure_time ON failure_events(occurred_at DESC);
            CREATE INDEX IF NOT EXISTS idx_failure_acct ON failure_events(account_id, occurred_at DESC);
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
        # -- M5c-2'：tool_sources 老库补列（官方介绍文案）
        try:
            ts_cols = {r[1] for r in conn.execute("PRAGMA table_info(tool_sources)").fetchall()}
            if ts_cols and "intro" not in ts_cols:
                conn.execute("ALTER TABLE tool_sources ADD COLUMN intro TEXT NOT NULL DEFAULT ''")
                
                print("[schema] tool_sources 补列: intro")
        except Exception as e:
            print("[schema] tool_sources.intro 迁移跳过:", e)

        # -- company_profile 老库补列 --
        # M5：user_clis 加「能力描述」列（老库幂等补齐；空值 = 保持 M4c 的命令名简报形态）
        try:
            uc_cols = {r[1] for r in conn.execute("PRAGMA table_info(user_clis)").fetchall()}
            if uc_cols and "abilities" not in uc_cols:
                conn.execute("ALTER TABLE user_clis ADD COLUMN abilities TEXT DEFAULT ''")
            # v3.1：能力描述的依据改为三选一（网址 / 本机文件 / 跑 --help）——老库默认按「网址」解释
            if uc_cols and "docs_mode" not in uc_cols:
                conn.execute("ALTER TABLE user_clis ADD COLUMN docs_mode TEXT DEFAULT 'url'")
        except Exception as e:
            print("[schema] user_clis.abilities 迁移跳过:", e)

        # -- v3.0 M1：tool_capabilities 老库补列（API/MCP 适配框架的五个槽位）--
        #    不加这些列，能力池读取会直接报 no such column（CLI 池也会跟着崩）→ 必须幂等补齐。
        try:
            tc_cols = {r[1] for r in conn.execute("PRAGMA table_info(tool_capabilities)").fetchall()}
            for col, ddl in [
                ("invoke_spec",  "ALTER TABLE tool_capabilities ADD COLUMN invoke_spec TEXT NOT NULL DEFAULT '{}'"),
                ("input_schema", "ALTER TABLE tool_capabilities ADD COLUMN input_schema TEXT NOT NULL DEFAULT '{}'"),
                ("read_only",    "ALTER TABLE tool_capabilities ADD COLUMN read_only INTEGER NOT NULL DEFAULT 1"),
                ("confirmed_at", "ALTER TABLE tool_capabilities ADD COLUMN confirmed_at DATETIME"),
                ("cost_hint",    "ALTER TABLE tool_capabilities ADD COLUMN cost_hint TEXT NOT NULL DEFAULT ''"),
                ("abilities_user", "ALTER TABLE tool_capabilities ADD COLUMN abilities_user TEXT NOT NULL DEFAULT ''"),
            ]:
                if col not in tc_cols:
                    conn.execute(ddl)
                    print("[schema] tool_capabilities 补列:", col)
        except Exception as e:  # noqa: BLE001
            print("[schema] tool_capabilities 补列迁移跳过:", e)

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


    # ★ Q6（2026-09-29 拍板）：**复杂价格规则** —— API 按量计费、租房阶梯违约价这类
    #   "一个数字概括不了"的价格。做法：加两列 + **显式三态**（不用"数字为空"隐式表示复杂价，
    #   那与"还没定价"分不开）：
    #     price_kind='simple' + price 有值  → 经典数值价
    #     price_kind='rule'   + price_text  → 规则文本价（**数值链路一律跳过**）
    #     price_kind IS NULL  + 两者皆空    → 未定价
    #   只加在"用户登记对象"的两张表；台账 / 提醒 / 资讯价表**不加**（台账天生只记确定数值）。
    _qc = get_personal_conn()
    try:
        for _t in ("products", "company_products"):
            _cols = {r[1] for r in _qc.execute("PRAGMA table_info(%s)" % _t)}
            if "price_kind" not in _cols:
                _qc.execute("ALTER TABLE %s ADD COLUMN price_kind TEXT" % _t)
            if "price_text" not in _cols:
                _qc.execute("ALTER TABLE %s ADD COLUMN price_text TEXT" % _t)
            _qc.execute("UPDATE %s SET price_kind='simple' WHERE price_kind IS NULL AND price IS NOT NULL" % _t)
        _qc.commit()
    except Exception as _e:
        print("[schema] price_kind/price_text 迁移跳过:", _e)
    finally:
        _qc.close()

    # ★ M5-8（阶段 2 预留）：价格源**配置表** —— 配置导向：只描述"怎么查"，
    #   不含任何厂商名/内置源（G7）。新增一个源 = 用户填字段，**不改代码**。
    #   凭证只存**环境变量名**（`secret_env`），永不存值；`readonly` 是只读闸（C3）。
    # ★ 注意：此处 `conn` 可能已被上层关闭（这也是"另一张表建不上"的经典坑），
    #   所以自己开一个短连接、用完即关，不依赖上层连接的生命周期。
    _pc = get_personal_conn()
    try:
        _pc.execute(
            "CREATE TABLE IF NOT EXISTS price_sources ("
            " id INTEGER PRIMARY KEY AUTOINCREMENT,"
            " account_id INTEGER NOT NULL REFERENCES accounts(id),"
            " name TEXT NOT NULL,"
            " url_template TEXT NOT NULL,"
            " method TEXT NOT NULL DEFAULT 'GET',"
            " param_map TEXT,"
            " value_path TEXT,"
            " currency TEXT DEFAULT 'CNY',"
            " timeout_s INTEGER DEFAULT 15,"
            " readonly INTEGER NOT NULL DEFAULT 1,"
            " enabled INTEGER NOT NULL DEFAULT 0,"
            " secret_env TEXT,"
            " created_at TEXT)")
        _pc.execute("CREATE INDEX IF NOT EXISTS idx_ps_acct ON price_sources(account_id, enabled)")
        _pc.commit()
    except Exception as _e:
        print("[schema] price_sources 建表跳过:", _e)
    finally:
        _pc.close()

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
