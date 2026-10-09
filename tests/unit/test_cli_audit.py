# -*- coding: utf-8 -*-
"""CLI 形态判定与草稿审计：**flag 风格 CLI** 的两处误报（2026-10-07 实测修复）。

背景：agently-cli 的参数全用开关给（`Usage: agently-cli message +read [flags]`，没有位置参数），
`--help` 正文写着 `Requires --id (message_id from +list output)`。旧判定只读 Usage 行的位置参数，
于是把它判成「无参数直调」，进而：

  · 对**正确的**两步链（`先 message +list 再 message +read`）报「凭空造链」——误报；
  · 反倒抓不住「直调 `message +read`」（原本只对 `needs_id` 报）——漏报。

现在按**帮助原文的证据**判定：出现「必需 + 要取值」的开关 → 形态为 `needs_flag`
（可作两步链第 2 步，且不能直调）；若第 2 步的帮助自己写明了「从第 1 步取」（如 `from +list output`），
第 1 步「不产出 ID」的提示也跳过。

运行：
    .venv/Scripts/python.exe -m pytest tests/unit/test_cli_audit.py -q
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from tools import cli_registry as reg  # noqa: E402

# 真实帮助片段（取自 `agently-cli message +read --help`）
HELP_READ = """Read a single message in full (body, attachments, headers)

  Requires --id (message_id from +list output).
  Example: agently-cli message +read --id msg_001

Usage:
  agently-cli message +read [flags]

Flags:
      --dry-run               print API calls without executing
  -h, --help                  help for +read
      --id string             message_id with msg_ prefix (required, get from +list)
      --print-output-schema   print output field reference and exit
"""

HELP_ME = """Show current user info and alias list

  No flags required.
  Example: agently-cli +me

Usage:
  agently-cli +me [flags]
"""

HELP_COBRA_REQ_SECTION = """Do the thing

Usage:
  tool do [flags]

Required Flags:
      --name string   target name

Flags:
      --verbose   verbose output
"""


# ------------------------------------------------ 帮助原文 → 必需的值开关
def test_required_value_flag_detected():
    assert reg.help_required_flags(HELP_READ) == ["--id"]


def test_required_flags_section_detected():
    """cobra 的 `Required Flags:` 小节里不会再写 required 字样，得靠小节标题判。"""
    assert reg.help_required_flags(HELP_COBRA_REQ_SECTION) == ["--name"]


def test_plain_help_has_no_required_flags():
    assert reg.help_required_flags(HELP_ME) == []


def test_valueless_flag_is_not_an_input():
    """`--dry-run` / `-h` 这类不带值的开关不算「输入」，否则人人都成 needs_flag。"""
    assert reg.help_required_flags("Flags:\n      --dry-run   required for testing\n") == []


# ------------------------------------------------ 形态判定
def test_flag_style_command_is_needs_flag():
    assert reg.classify_usage("agently-cli message +read [flags]", HELP_READ) == "needs_flag"


def test_truly_parameterless_command_stays_standalone():
    assert reg.classify_usage("agently-cli +me [flags]", HELP_ME) == "standalone"


def test_positional_rules_unchanged():
    """位置参数那套判定不受影响（向后兼容）。"""
    assert reg.classify_usage("weread book info <bookId>") == "needs_id"
    assert reg.classify_usage("weread book resolve <title>") == "free_text"
    assert reg.classify_usage("weread shelf list") == "standalone"
    assert reg.classify_usage("") == "unknown"


# ------------------------------------------------ 审计：误报与漏报
KINDS = {"+me": "standalone", "message +list": "standalone",
         "message +read": "needs_flag", "message +search": "standalone"}
HELP_MAP = {"message +read": {"kind": "needs_flag", "help": HELP_READ, "usage": "", "requires_flags": ["--id"]}}


def test_correct_chain_has_no_warning():
    """回归守卫：用户那张截图里的草稿是**对的**，不该报「凭空造链」。"""
    draft = ("关键词：邮箱/邮件/QQ邮箱/收件箱。\n"
             "读这封邮件详情→先 message +search 再 message +read；"
             "打开最新的一封邮件→先 message +list 再 message +read")
    assert reg.audit_ability_draft(draft, KINDS, HELP_MAP) == []


def test_direct_call_of_needs_flag_is_reported():
    """漏报修复：直调 `message +read` 也要提醒，并给出从 help 反查出的第 1 步。"""
    warns = reg.audit_ability_draft("关键词：邮件。\n读这封邮件→message +read", KINDS, HELP_MAP)
    assert len(warns) == 1
    assert "message +read" in warns[0] and "message +list" in warns[0]


def test_true_dead_chain_still_reported():
    """真阳性不能被削掉：第 2 步换成真的不需要参数的 `+me` 仍要报。"""
    warns = reg.audit_ability_draft("关键词：邮件。\n我的邮箱→先 message +list 再 +me", KINDS, HELP_MAP)
    assert len(warns) == 1 and "凭空造链" in warns[0]


def test_unknown_command_still_reported():
    warns = reg.audit_ability_draft("关键词：邮件。\n发个邮件→message +zzz", KINDS, HELP_MAP)
    assert len(warns) == 1 and "不在只读清单里" in warns[0]


def test_docs_link_steps_reads_the_help():
    assert reg._docs_link_steps("message +list", "message +read", HELP_MAP)
    assert not reg._docs_link_steps("message +search", "message +read", HELP_MAP)
