# -*- coding: utf-8 -*-
"""「CLI 能力描述」证据链回归（离线，不调模型、不联网）。

对应 2026-09-24 用户报障：CLI 能力描述撰写 AI 写的子命令描述**经常是错的** —— 根因是它当时
只拿到「命令名清单」，没有任何依据，只能望文生义（实测 13 条映射 3 条错，且全错在需要判断力的
地方：哪些命令要 ID、哪条是「书名→ID」的解析器）。修复后的证据分层：
    ① 官方文档（README）当**语义依据** → `tools/cli_docs.py`
    ② 逐条 `<bin> <cmd> --help` 当**校验依据**（形态分类 + 草稿审计）→ `tools/cli_registry.py`
    ③ 都没有 → 用户粘贴的 help 文本 → 保守化（宁可少写一条）

本文件锁住：URL 规范化、SSRF 守卫、证据抽取与覆盖统计、形态分类、审计四条规则、解析器推荐。
夹具用的是**真实 weread 的 --help 真值**（本机实测抄录），所以这些断言不是自说自话。
"""
from __future__ import annotations

import io
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools import cli_docs  # noqa: E402
from tools import cli_registry as reg  # noqa: E402

# ---------------------------------------------------------------- 夹具：真实 weread --help 真值
HELP = {
    "config set-key": ("weread config set-key [options] <apiKey>", "Store the WeRead API key"),
    "search": ("weread search [options] <keyword>", "Search store content"),
    "book info": ("weread book info [options] <bookId>", "Get /book/info"),
    "book resolve": ("weread book resolve [options] <title>", "Resolve a book title to likely bookId matches"),
    "book progress": ("weread book progress [options] <bookId>", "Get /book/getprogress"),
    "reviews list": ("weread reviews list [options] <bookId>", "Get /review/list"),
    "discover recommend": ("weread discover recommend [options]", "Get /book/recommend"),
    "discover similar": ("weread discover similar [options] <bookId>", "Get /book/similar"),
    "shelf list": ("weread shelf list [options]", "Get /shelf/sync"),
    "shelf recent": ("weread shelf recent [options]", "Show recently read or updated shelf books"),
    "readdata summary": ("weread readdata summary [options]", "Summarize reading statistics"),
    "readdata detail": ("weread readdata detail [options]", "Get /readdata/detail"),
    "notes notebooks": ("weread notes notebooks [options]", "Get /user/notebooks"),
    "notes top": ("weread notes top [options]", "Rank notebook books by highlight count"),
    "notes export": ("weread notes export [options] <bookId>", "Export one book's highlights"),
    "doctor": ("weread doctor [options]", "Check local CLI configuration"),
}
HELP_MAP = {c: {"ok": True, "usage": u, "desc": d, "kind": reg.classify_usage(u), "error": ""}
            for c, (u, d) in HELP.items()}
KINDS = {c: v["kind"] for c, v in HELP_MAP.items()}

# 夹具：README（模拟 shiquda/weread-cli 的结构：安装段 + 常用命令段 + 许可段）
README = """# weread

微信读书命令行工具。

## 安装

```bash
npm i -g weread-cli
```

## 配置凭据

```bash
weread config set-key "wrk-xxx"
```

## 常用命令

```powershell
weread doctor
weread search "三体" --scope book
weread book resolve "三体"
weread shelf list
weread book info 3300045871
weread book progress 3300045871
weread notes export 3300045871 --format markdown --output notes.md
weread readdata detail --mode annually
weread discover similar 3300045871
```

## 更多说明

所有子命令都支持 `--json`；human 输出在截断时会提示 `Showing first ...`，
可用 `--limit` 或 `--all` 控制显示量。需要 `bookId` 的命令（info / progress /
notes export / discover similar）请先用 `book resolve <title>` 把书名解析成 bookId
再调用，否则会报参数错。本夹具刻意不写「书评」相关子命令，用来验证「文档未覆盖」的统计。
这一段刻意写长一些，以便抽取器有足够的正文可保留（真实 README 都在 4K~19K 量级）。

## 贡献

欢迎 PR。

## 许可

MIT
"""

# 夹具：修复前（望文生义）AI 草稿里的三条错映射
BAD_DRAFT = ("关键词：读书 / 书 / 笔记\n\n"
             "这本书讲什么→search 再 book info；最近读的同类书→discover similar；"
             "详细阅读数据→readdata summary 再 readdata detail；把我笔记导出来→notes export；"
             "我的书架→shelf list；这本书的评论→search 再 reviews list")
# 夹具：读 README 之后（修复后）的草稿
GOOD_DRAFT = ("关键词：找书 / 我的书架 / 阅读进度 / 笔记 / 书评 / 推荐\n\n"
              "这本书讲什么→book resolve 再 book info；我读到哪了→book resolve 再 book progress；"
              "我的书架有什么→shelf list；我的读书笔记在哪→notes notebooks；"
              "推荐点类似的→discover recommend；有没有类似的→book resolve 再 discover similar")


# ---------------------------------------------------------------- ① URL 规范化
def test_normalize_docs_url():
    assert (cli_docs.normalize_docs_url("https://github.com/a/b/blob/main/README.md")
            == "https://raw.githubusercontent.com/a/b/main/README.md")
    assert (cli_docs.normalize_docs_url("https://github.com/a/b")
            == "https://raw.githubusercontent.com/a/b/HEAD/README.md")
    assert cli_docs.normalize_docs_url("github.com/a/b") == "https://raw.githubusercontent.com/a/b/HEAD/README.md"
    assert cli_docs.normalize_docs_url("https://example.com/docs/x.md") == "https://example.com/docs/x.md"
    assert cli_docs.normalize_docs_url("") == ""


# ---------------------------------------------------------------- ② SSRF 守卫（不联网，用字面量 IP）
@pytest.mark.parametrize("host", ["localhost", "127.0.0.1", "10.0.0.5", "192.168.1.9",
                                  "169.254.1.1", "172.16.3.4", "foo.local", "::1"])
def test_ssrf_guard_blocks_internal(host):
    ok, why = cli_docs._host_is_safe(host)
    assert ok is False and why


def test_ssrf_guard_allows_public_ip():
    assert cli_docs._host_is_safe("140.82.113.3")[0] is True     # github.com 的一个公网 IP


# ---------------------------------------------------------------- ③ 证据抽取 / 覆盖
def test_extract_evidence_keeps_commands_drops_noise():
    ev = cli_docs.extract_evidence(README, list(HELP), "weread")
    assert "book resolve" in ev and "weread book info 3300045871" in ev
    assert "npm i -g weread-cli" not in ev                      # 安装段被丢
    assert "欢迎 PR" not in ev and "MIT" not in ev               # 贡献/许可段被丢


def test_extract_evidence_falls_back_when_too_thin():
    """抽得太狠（< 400 字）时整篇给——宁可多给，也别把有用的滤没。"""
    tiny = "just a line\n"
    assert cli_docs.extract_evidence(tiny, ["foo"], "bar") == tiny.strip()


def test_coverage_lists_missing_commands():
    covered, missing = cli_docs.coverage(README, list(HELP))
    assert "book resolve" in covered
    assert "reviews list" in missing                            # 该 README 确实没写它（真实 gap）


# ---------------------------------------------------------------- ④ 形态分类
def test_classify_usage_three_kinds():
    assert reg.classify_usage("weread book info [options] <bookId>") == "needs_id"
    assert reg.classify_usage("weread book resolve [options] <title>") == "free_text"
    assert reg.classify_usage("weread shelf list [options]") == "standalone"
    assert reg.classify_usage("bili favorites <ID> --page 2") == "needs_id"
    assert reg.classify_usage("foo bar KEYWORD") == "free_text"       # 全大写形参也认
    assert reg.classify_usage("foo bar [OPTIONS]") == "standalone"    # OPTIONS 不是位置参数
    assert reg.classify_usage("") == "unknown"


# ---------------------------------------------------------------- ⑤ 审计四条规则
def test_audit_flags_the_three_real_errors():
    warns = reg.audit_ability_draft(BAD_DRAFT, KINDS, HELP_MAP)
    joined = " | ".join(warns)
    assert "直调了需要 ID 的 `discover similar`" in joined
    assert "直调了需要 ID 的 `notes export`" in joined
    assert "凭空造链" in joined and "readdata detail" in joined     # readdata summary 再 readdata detail
    assert len(warns) == 3


def test_audit_passes_the_evidence_based_draft():
    assert reg.audit_ability_draft(GOOD_DRAFT, KINDS, HELP_MAP) == []


def test_audit_suggests_the_real_resolver():
    """需要 ID 却直调时，建议里必须是**专门的解析器**（book resolve），不是 search。"""
    warns = reg.audit_ability_draft("我读到哪了→book progress", KINDS, HELP_MAP)
    assert warns and "book resolve 再 book progress" in warns[0]


def test_audit_flags_out_of_list_command():
    warns = reg.audit_ability_draft("我的书架→shelf books", KINDS, HELP_MAP)
    assert warns and "不在只读清单里" in warns[0]


def test_suggest_resolver_prefers_resolve_like_command():
    assert reg.suggest_resolver(KINDS, HELP_MAP) == "book resolve"
    assert reg.suggest_resolver({"a": "standalone"}, {}) == ""


def test_coverage_note():
    note = reg.coverage_note(GOOD_DRAFT, list(HELP))
    assert "reviews list" in note and "没被描述到" in note
    # 全覆盖时（每条命令都出现在描述里）不该再提示
    full = "；".join("随便说说→%s" % c for c in HELP)
    assert reg.coverage_note(full, list(HELP)) == ""


# ---------------------------------------------------------------- ⑥ 接线与提示词不变量（防回退）
def test_server_passes_evidence_and_owner_context():
    src = io.open(ROOT / "api" / "server.py", encoding="utf-8").read()
    seg = src[src.find('"/api/cli/ability_draft"'):]
    seg = seg[:seg.find("@app.post", 10)]
    assert "docs=req.get" in seg and "help_text=req.get" in seg
    assert "username=sess.get" in seg and "account_id=sess.get" in seg


def test_generate_uses_docs_help_and_audit():
    src = io.open(ROOT / "api" / "customize.py", encoding="utf-8").read()
    seg = src[src.find("def cli_ability_generate("):]
    seg = seg[:seg.find("def soul_generate(")]
    assert "cli_docs.fetch_docs" in seg and "reg.collect_help" in seg
    assert "reg.audit_ability_draft" in seg and "warnings" in seg
    assert "唯一事实来源" in seg


def test_prompt_has_evidence_rules():
    """提示词必须留「按证据判断、不要凭命令名猜」的硬规则（防后续追加式迭代把它冲掉）。"""
    yml = io.open(ROOT / "prompt" / "prompts.yml", encoding="utf-8").read()
    seg = yml[yml.find("ability_writer:"):yml.find("memory_initializer:")]
    for needle in ("不要凭命令名猜", "需要 ID", "不要编造两步链", "官方文档"):
        assert needle in seg, needle


def test_frontend_shows_evidence_and_warnings():
    html = io.open(ROOT / "static" / "index.html", encoding="utf-8").read()
    assert "cliDraftInfo" in html and "cliDraftWarns" in html
    assert "help_text: f.helpText" in html and "docs: f.docs" in html
    # helpText 是临时字段：三个打开表单的路径都从 emptyCliForm() 派生
    assert "helpText: ''" in html


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
