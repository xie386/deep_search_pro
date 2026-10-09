# -*- coding: utf-8 -*-
"""v3.1 视觉开关（supports_vision）：列/默认值/出参，以及**两条 UPDATE 分支**的不变量。

为什么值得单独测：
  · 这是「配置导向」的落点 ✓（用户明确不许写死厂商枚举 ✗）；
  · 它决定**带图提问会不会被拦** ✓ —— 漏一条 SQL 分支 → 前端勾了却存不进去 → 用户看到「勾了还报错」✗；
  · M6c 加单价时真犯过「只改一条 UPDATE 分支」的错 ✓（customize.py 里还留着注释 ✓），这里用不变量钉死 ✓；
  · 全新库缺列的老坑（M4/M5 各踩过一次）这里也盯一道 ✓。

运行：.venv/Scripts/python.exe -m pytest tests/unit/test_vision_flag.py -q
"""
import io
import os
import re
import sys
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from tools.schema_personal import ensure_tables, get_personal_conn   # noqa: E402

ensure_tables()
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def mk_account(tag="v"):
    """建一个临时账号（用例自清；绝不碰真实账号 ✓）"""
    conn = get_personal_conn()
    try:
        aid = int(conn.execute("INSERT INTO accounts (username, password_hash, role) VALUES (?,?,?)",
                               ("v3v%s_%d" % (tag, int(time.time() * 1000) % 1000000), "x", "user")).lastrowid)
        conn.commit()
    finally:
        conn.close()
    return aid


def add_provider(aid, name, model, vision_sql="NULL"):
    conn = get_personal_conn()
    try:
        conn.execute("INSERT INTO llm_providers (owner_id, provider_name, model_name, base_url, api_key,"
                     " supports_vision) VALUES (?,?,?,?,?," + vision_sql + ")",
                     (aid, name, model, "http://example.invalid", "sk-test"))
        conn.commit()
    finally:
        conn.close()


def vision_of(aid, name):
    conn = get_personal_conn()
    try:
        row = conn.execute("SELECT supports_vision FROM llm_providers WHERE owner_id=? AND provider_name=?",
                           (aid, name)).fetchone()
        return None if row is None else row[0]
    finally:
        conn.close()


# ---------------------------------------------------------------- 列与默认值
def test_column_exists_on_real_db():
    conn = get_personal_conn()
    try:
        cols = [r[1] for r in conn.execute("PRAGMA table_info(llm_providers)")]
    finally:
        conn.close()
    assert "supports_vision" in cols, "llm_providers 缺 supports_vision 列（检查 ALTER 与 CREATE TABLE 两条路径 ✓）"


def test_column_also_in_create_table_ddl():
    """盯「只加 ALTER、忘了建表」这个老坑 ✓"""
    src = io.open(os.path.join(ROOT, "tools", "schema_personal.py"), encoding="utf-8").read()
    ddl = src[src.index("CREATE TABLE IF NOT EXISTS llm_providers"):]
    ddl = ddl[:ddl.index(");")]
    assert "supports_vision" in ddl, "CREATE TABLE 里没有 supports_vision（全新库会缺列 ✗）"


def test_default_is_falsy_for_new_rows():
    """不写这个字段插入 → **falsy**（= 不能收图 ✓）。

    ★ 这里踩过一个真坑（2026-09-30）：`ALTER TABLE ... ADD COLUMN supports_vision INTEGER`
      **不带 DEFAULT** 时，经"老库升级"路径的库存里，新行的值是 **NULL**（不是 0）✗ —— 只有
      CREATE TABLE 里的 `DEFAULT 0` 才对新库生效 ✓。修法：ALTER 也写 `DEFAULT 0` ✓（新库一致 ✓）；
      老库那一列已经建好、改不了定义 ✗，所以**代码侧必须把 NULL 也当"不能收图"** ✓
      （`bool(p.get(...))` → False ✓、闸门写 `not _act.get("supports_vision")` ✓ 都对 NULL 安全 ✓）。
      本用例盯的就是"产品语义"：**没表态 = 不能收图**，NULL 和 0 都不许被当成"能" ✓。
    """
    aid = mk_account("d")
    conn = get_personal_conn()
    try:
        conn.execute("INSERT INTO llm_providers (owner_id, provider_name, model_name, base_url, api_key)"
                     " VALUES (?,?,?,?,?)", (aid, "D", "some-model", "http://example.invalid", "sk"))
        conn.commit()
        v = conn.execute("SELECT supports_vision FROM llm_providers WHERE owner_id=? AND provider_name='D'",
                         (aid,)).fetchone()[0]
    finally:
        conn.close()
    assert not v, "没表态必须是 falsy（NULL 或 0）→ 不许当成\"能收图\" ✓（老库 NULL 也要挡住 ✓）"


# ---------------------------------------------------------------- 一次性默认
def test_migration_sets_agnes_one_and_others_zero():
    """★ 幂等迁移：名字带 agnes 的历史行 → 1（项目默认模型本身多模态 ✓）；其余 NULL → 0 ✓"""
    aid = mk_account("m")
    add_provider(aid, "AG", "agnes-2.5-flash")      # 应被置 1
    add_provider(aid, "AG2", "Agnes-2.5-Pro")       # 大小写不敏感 ✓ 也应 1
    add_provider(aid, "OTHER", "deepseek-chat")     # 应置 0
    assert vision_of(aid, "AG") is None, "插入时应是 NULL（用例前提 ✓）"
    ensure_tables()                                  # 再跑迁移 ✓
    assert vision_of(aid, "AG") == 1
    assert vision_of(aid, "AG2") == 1
    assert vision_of(aid, "OTHER") == 0, "非 agnes 应显式置 0（避免三态）"


def test_migration_does_not_clobber_user_choice():
    """★ 用户已经手动置 0 的 agnes 行**不许**被迁移改回 1（只动 NULL 行 ✓）"""
    aid = mk_account("k")
    add_provider(aid, "AGOFF", "agnes-2.5-flash", vision_sql="0")     # 用户明确关掉 ✓
    add_provider(aid, "AGNULL", "agnes-3", vision_sql="NULL")         # 没表态 ✓
    ensure_tables()
    assert vision_of(aid, "AGOFF") == 0, "用户的选择被迁移覆盖了 ✗（迁移只能动 NULL 行 ✓）"
    assert vision_of(aid, "AGNULL") == 1


# ---------------------------------------------------------------- 出参
def test_normalize_provider_exposes_bool():
    """出参必须是 bool（前端直接绑勾选框 ✓），不是 0/1"""
    from api.customize import _normalize_provider
    base = {"id": 1, "provider_name": "x", "model_name": "m", "base_url": "u", "api_key": "sk",
            "is_active": 0, "price_in_cached": None, "price_in_uncached": None, "price_out": None}
    assert _normalize_provider(dict(base, supports_vision=1))["supports_vision"] is True
    assert _normalize_provider(dict(base, supports_vision=0))["supports_vision"] is False
    # 老库 NULL 行 → False（不是 None ✗ 前端勾选框要 bool ✓）
    assert _normalize_provider(dict(base, supports_vision=None))["supports_vision"] is False


# ---------------------------------------------------------------- 静态不变量
def test_both_update_branches_carry_vision_flag():
    """★ INSERT + **两条** UPDATE 分支都要带 supports_vision（只改一条 = 改了等于没改 ✗）"""
    src = io.open(os.path.join(ROOT, "api", "customize.py"), encoding="utf-8").read()
    assert src.count("price_out=?, supports_vision=? WHERE id=? AND owner_id=?") == 2, (
        "两条 UPDATE 分支必须都带 supports_vision（否则「改了 key 的那条分支」会丢标记 ✗）")
    assert src.count("price_out, supports_vision) VALUES (?,?,?,?,?,?,?,?,?,?)") == 1, "INSERT 列/占位符没改对"
    assert src.count("int(bool(req.supports_vision))") == 3, "取值处应为 INSERT 1 + UPDATE 2 = 3 ✓"
    assert src.count("p_c, p_u, p_o = _norm_prices(req)") == 2, "单价**赋值**那两处不许被误改 ✗"


def test_chat_gate_blocks_unchecked_provider():
    """★ 未勾「支持图片」却带图 → 必须明确拦下（绝不静默丢图 ✗）；且 None（用 .env 默认模型）必须放行 ✓"""
    src = io.open(os.path.join(ROOT, "api", "server.py"), encoding="utf-8").read()
    assert "supports_vision" in src, "视觉闸不见了 ✗"
    assert "未开启「支持图片」" in src, "拦截文案不见了（要能照做 ✓）"
    # 闸门与"能力声明"共用同一个判定（_vision_ok 只算一次 ✓ 不重复查库 ✓）
    m = re.search(r"_vision_ok\s*=\s*\(_act is None\) or bool\(_act\.get\(\"supports_vision\"\)\)", src)
    assert m, "闸门写法必须允许 _act=None（没配自定义模型的账号走默认模型 ✓）否则会被全拦 ✗"
    assert "if not _vision_ok:" in src, "闸门没复用 _vision_ok ✗"
    assert "vision_capable=_vision_ok" in src, "能力声明没复用 _vision_ok ✗"


def test_upload_route_and_module_wired():
    src = io.open(os.path.join(ROOT, "api", "server.py"), encoding="utf-8").read()
    assert '@app.post("/api/chat/upload"' in src, "上传路由没注册"
    assert "chat_images" in src
    ctx = io.open(os.path.join(ROOT, "agent", "build_context.py"), encoding="utf-8").read()
    assert "image_ids" in ctx and "build_content" in ctx, "装配层没接图片 ✗"


def test_no_image_path_is_unchanged():
    """★ 零回归守卫：build_content 在**没图**时必须原样返回字符串（老链路一个字都不变 ✓）"""
    from tools import chat_images as ci
    assert ci.build_content("你好", [], "anyone") == "你好"
    assert ci.build_content("你好", None, "anyone") == "你好"


# ---------------------------------------------------------------- 用户实测崩溃回归
def test_multimodal_content_never_used_as_set_member():
    """★ 2026-09-30 用户实测崩溃回归：`TypeError: unhashable type: 'list'`

    多模态提问的 `content` 是**块列表** ✗ → 任何 `m.content in 集合` 都会当场炸 ✓
    （实测崩在两处：answer_text 的消段匹配 + server 的落库边界 ✓，已归一到 message_text() 一处实现 ✓）。
    这条用例同时钉住：① helper 存在且被两处复用 ✓ ② 全仓不许再出现裸比较 ✗。
    """
    at = io.open(os.path.join(ROOT, "agent", "answer_text.py"), encoding="utf-8").read()
    sv = io.open(os.path.join(ROOT, "api", "server.py"), encoding="utf-8").read()
    assert "def message_text(" in at, "统一取文本的 helper 没了 ✗"
    assert "message_text(m) in since" in at, "消段匹配没走 helper ✗"
    assert "_msg_text(m) in _boundary" in sv, "落库边界没走 helper ✗"
    for rel in ("agent/answer_text.py", "api/server.py", "agent/build_context.py"):
        src = io.open(os.path.join(ROOT, rel), encoding="utf-8").read()
        assert not re.search(r"\bm\.content\s+(?:in|not in)\s+\w+", src), rel + " 又出现裸 content 比较了 ✗"


def test_turn_boundary_with_image_blocks():
    """真跑一遍带图提问的边界定位（不崩 ✓ 且**定位正确** ✓）+ 纯文字老链路不变 ✓"""
    from langchain_core.messages import AIMessage, HumanMessage
    from agent.answer_text import final_answer, message_text, turn_messages
    blocks = [{"type": "text", "text": "这张图里面有什么？"},
              {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}}]
    msgs = [HumanMessage(content="历史一"), AIMessage(content="历史答"),
            HumanMessage(content=blocks), AIMessage(content="图里是一只猫")]
    assert message_text(msgs[2]) == "这张图里面有什么？", "块列表取文字不对 ✗"
    assert [type(x).__name__ for x in turn_messages(msgs, since={"这张图里面有什么？"})] == ["AIMessage"]
    assert final_answer(msgs, since={"这张图里面有什么？"})[0] == "图里是一只猫"
    msgs2 = [HumanMessage(content="历史"), HumanMessage(content="纯文字问题"), AIMessage(content="答案")]
    assert final_answer(msgs2, since={"纯文字问题"})[0] == "答案", "纯文字链路被改坏了 ✗"


def test_vision_self_awareness_injected():
    """★ 2026-09-30 用户实测反馈：模型**看得见**却自称「我没有视觉识别的能力」✗

    根因：注入块长期只有工具视角（人格/记忆/CLI 能力卡）→ 它推理出"我的工具都是文字类的 → 没有视觉"✗。
    修法：两段**条件化**声明 —— ① 能力事实（当前模型支持图片 ✓，纯文字轮也注入 ✓）
                                  ② 本轮真有图的强指令（图片已随消息送达 ✓ 别否认 ✓ 别调工具 ✓）。
    反向也要守：**不支持视觉时两段都不许出现** ✓（免得凭空虚报"我看得见"✗）。
    """
    from agent.build_context import compose_dynamic_prompt as cdp
    kw = dict(soul_text="S", memory_text="M", username="u", cli_brief="", question="q")
    on = cdp(**kw, image_ids=["a.jpg"], vision_capable=True)
    mid = cdp(**kw, vision_capable=True)              # 它就是日志里"纯文字轮否认视觉"的场景 ✓
    off = cdp(**kw)                                   # 老调用方 / 不支持视觉的模型 ✓
    assert "【你自己的能力：支持图片输入】" in on and "【本轮：用户直接贴了图片】" in on
    assert "【你自己的能力：支持图片输入】" in mid, "纯文字轮也要知道自己能看图 ✗（日志就崩在这）"
    assert "【本轮：用户直接贴了图片】" not in mid, "没图不许说本轮有图 ✗"
    assert "【你自己的能力" not in off and "【本轮：用户直接贴了图片】" not in off, "不支持视觉时不许出现 ✗"
    assert off == cdp(**kw), "老链路必须逐字不变 ✗"
    assert "不需要也不会用到任何图像工具" in on, "必须明确「别去找图像工具」✗"


def test_frontend_shows_images_in_bubble_and_keeps_urls():
    """★ 用户实测反馈：图要出现在**用户气泡**里 ✓；且发送后不许 revoke 对象 URL ✗（否则缩略图变空 ✓）"""
    h = io.open(os.path.join(ROOT, "front", "index.html"), encoding="utf-8").read()
    assert "bubble-imgs" in h and "m.imgs" in h, "气泡里没显示图 ✗"
    assert "clearChatImages(true)" in h, "发送后没保留对象 URL ✗（气泡缩略图会变空）"
    assert "imgs: _bubbleImgs" in h, "气泡没带上本轮图片 ✗"
    # 三入口 + 上限 3 + setup 导出（静默失效的老坑 ✓）
    for k in ("onChatPaste", "onChatDrop", "pickChatImages", "CHAT_IMG_MAX = 3"):
        assert k in h, k + " 不见了 ✗"


def test_wechat_style_composer():
    """★ 复刻微信输入框（用户 2026-09-30 指定）：相册真接上传 ✓ 其余是**如实占位** ✓

    要点：① 贴图前就有可见入口（＋ 展开面板 → 相册 ✓），不再是"只有 Ctrl+V 才出现预览条"✗；
         ② 所有新标识符必须进 setup 导出 ✓（否则按钮点了没反应 ✗ 前端静默失效老坑）；
         ③ 占位必须如实说明（不许假装能用 ✗），相册走的必须与粘贴/拖拽是**同一个**上传函数 ✓；
         ④ 面板第二/三/四个入口是 **md / pdf / word** 文档上传（用户指定 ✓ 当前为占位 ✓）。
    """
    h2 = io.open(os.path.join(ROOT, "front", "index.html"), encoding="utf-8").read()
    assert "input-bar wx" in h2, "输入框没换成微信式布局 ✗"
    for k in ("wx-round", "wx-in", "multi-panel", "mp-tile"):
        assert k in h2, k + " 不见了 ✗"
    assert "multiOpen, toggleMulti, pickFromAlbum, soonHint," in h2, "setup 没导出新标识符 ✗（按钮会点了没反应）"
    assert "function pickFromAlbum() { multiOpen.value = false; pickChatImages(); }" in h2, "相册没复用真正的上传 ✗"
    assert "前端占位，将在后续优化中接入" in h2, "占位入口没如实说明 ✗"
    for lb in ("相册", ">md<", ">pdf<", ">word<"):
        assert lb in h2, lb + " 入口不见了 ✗"
    for gone in ("拍摄", "视频通话", "位置"):
        assert gone not in h2, gone + " 应已被 md/pdf/word 取代 ✗"
