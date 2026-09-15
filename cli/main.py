# -*- coding: utf-8 -*-
"""dspro 命令行入口（与 web 版共用同一套数据与 Agent）。

用法速查：
    dspro login 用户名 密码          登录（密码写 `-` 则隐藏输入）
    dspro logout                     退出登录
    dspro whoami                     当前登录态与数据概况
    dspro list [-db] [-digest]       罗列账号信息（默认两者都打）
    dspro digest [领域名]            生成周报（默认所有启用领域，指定则只生成该领域）
    dspro chat [问题]                命令行对话（不给问题则进交互模式）

运行方式（三种任选，都指向同一份 cli.main）：
    dspro ...                        # 装了项目（uv sync / pip install -e .）后的控制台脚本
    uv run dspro ...                 # 用 uv 直接跑
    python -m cli.main ...           # 或不依赖安装：直接以模块方式跑
    ./dspro ...  /  .\\dspro.cmd ...  # 项目根的两个包装脚本（免激活 venv）
"""

import argparse
import sys
from pathlib import Path

# 项目根入 sys.path（支持 `python cli/main.py` 直接跑），并剔除同名包遮蔽（同 api/server.py 的防御）
_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))
sys.path = [p for p in sys.path if "hermes-agent" not in p and "hermes_agent" not in p]

from cli import ui, __version__          # noqa: E402
from cli.errors import CliError          # noqa: E402
from cli import cmd_auth, cmd_list, cmd_digest, cmd_chat   # noqa: E402

BANNER = r"""
  ██████╗ ███████╗██████╗ ██████╗  ██████╗
  ██╔══██╗██╔════╝██╔══██╗██╔══██╗██╔═══██╗
  ██║  ██║███████╗██████╔╝██████╔╝██║   ██║
  ██║  ██║╚════██║██╔═══╝ ██╔══██╗██║   ██║
  ██████╔╝███████║██║     ██║  ██║╚██████╔╝
  ╚═════╝ ╚══════╝╚═╝     ╚═╝  ╚═╝ ╚═════╝
"""

HELP_TEXT = """用法：dspro <命令> [参数]

登录鉴权
  dspro login <用户名> <密码>      登录（密码写 `-` 则隐藏输入；--register 可直接注册）
  dspro logout                     退出登录（删除本地登录态文件）
  dspro whoami                     当前登录态与数据概况

罗列信息
  dspro list                       默认：个人信息/公司信息 + 周报关注信息
  dspro list -db                   只打印个人信息 / 公司信息
  dspro list -digest               只打印周报生成模块的所有关注信息
  dspro list --json                机器可读输出（脚本用）

生产周报
  dspro digest                     默认：所有启用中的关注领域各生成一份
  dspro digest <领域名>             只生成该领域一份（领域名必须存在，否则报错并列出可用值）
  dspro digest --list              只列出可用的领域名
  dspro digest <领域名> -o out.md   同时另存一份 markdown

命令行聊天
  dspro chat                       进入对话（默认续用上次的 CLI 会话）
  dspro chat "<问题>"               单轮问答后退出
  dspro chat --new | --session <id> | --sessions
  dspro chat --skill "<技能名>"      本轮注入技能说明书（可重复，--skills 查看可用）

通用
  -h, --help        帮助；--no-color 关闭彩色；--version 版本
"""


def _print_landing() -> None:
    print(ui.c(BANNER, "green"))
    print(ui.c("  智选情报官 · 命令行版 (dspro)", "green", "bold")
          + ui.c(f"  v{__version__}", "grey"))
    st = ""
    try:
        from cli import session as ss
        data = ss.current()
        if data:
            st = f"当前登录：{data.get('username')}（{data.get('role')}）"
    except Exception:
        pass
    print(ui.c(f"  {st}" if st else "  未登录", "grey"))
    print(HELP_TEXT)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="dspro", add_help=False,
                                description="智选情报官命令行版：登录鉴权 / 罗列信息 / 生产周报 / 命令行聊天")
    p.add_argument("-h", "--help", action="store_true")
    p.add_argument("-V", "--version", action="store_true")
    p.add_argument("--no-color", action="store_true", help="关闭彩色输出")
    sub = p.add_subparsers(dest="cmd")

    a = sub.add_parser("login", add_help=False, help="登录鉴权")
    a.add_argument("username", nargs="?")
    a.add_argument("password", nargs="?", help="密码；写 `-` 则隐藏输入")
    a.add_argument("--register", action="store_true", help="账号不存在时直接注册")
    a.add_argument("--role", default="personal", choices=["company", "personal"], help="配合 --register")
    a.add_argument("--display-name", default=None)
    a.set_defaults(func=lambda x: cmd_auth.login(x.username, x.password, x.register, x.role, x.display_name))

    for name, fn, helptext in (("logout", cmd_auth.logout, "退出登录"),
                               ("whoami", cmd_auth.whoami, "当前登录态")):
        s = sub.add_parser(name, add_help=False, help=helptext)
        s.set_defaults(func=lambda x, f=fn: f())

    l = sub.add_parser("list", add_help=False, help="罗列账号信息")
    l.add_argument("-db", "--db", action="store_true", help="只打印个人信息/公司信息")
    l.add_argument("-digest", "--digest", action="store_true", help="只打印周报关注信息")
    l.add_argument("--json", action="store_true", help="JSON 输出")
    l.set_defaults(func=lambda x: cmd_list.run(x.db, x.digest, x.json))

    d = sub.add_parser("digest", add_help=False, help="生产周报")
    d.add_argument("domain", nargs="?", help="关注领域名（不填=所有启用领域）")
    d.add_argument("--list", action="store_true", help="只列出可用领域名")
    d.add_argument("-o", "--out", default=None, help="另存 markdown 到指定文件")
    d.add_argument("--no-print", action="store_true", help="不打印正文（只落库）")
    d.add_argument("--json", action="store_true", help="JSON 输出")
    d.set_defaults(func=lambda x: cmd_digest.run(x.domain, x.out, x.no_print, x.json, x.list))

    c = sub.add_parser("chat", add_help=False, help="命令行聊天")
    c.add_argument("question", nargs="?", help="单轮问题（不填=交互模式）")
    c.add_argument("--new", action="store_true", help="开新会话")
    c.add_argument("--session", default=None, help="续指定 thread_id")
    c.add_argument("--sessions", action="store_true", help="列出会话后退出")
    c.add_argument("--skills", action="store_true", help="列出可用技能后退出")
    c.add_argument("--skill", action="append", dest="skills_pick", default=[], help="本轮注入技能（可重复）")
    c.add_argument("--quiet", action="store_true", help="不显示工具/思考进度")
    c.set_defaults(func=lambda x: cmd_chat.run(x.question, x.new, x.session, x.sessions, x.skills,
                                              x.skills_pick, x.quiet))
    return p


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    parser = build_parser()
    args = parser.parse_args(argv)
    ui.setup(no_color_flag=bool(getattr(args, "no_color", False)))

    if getattr(args, "version", False):
        print(f"dspro {__version__}")
        return 0
    if getattr(args, "help", False) or not getattr(args, "cmd", None):
        print(ui.c(BANNER, "green"))
        print(HELP_TEXT)
        return 0

    try:
        return int(args.func(args) or 0)
    except CliError as e:
        ui.err(e.message)
        return e.code
    except KeyboardInterrupt:
        print()
        ui.warn("已中断")
        return 130


if __name__ == "__main__":
    sys.exit(main())
