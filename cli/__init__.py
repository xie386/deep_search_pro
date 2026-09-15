# -*- coding: utf-8 -*-
"""dspro · 智选情报官命令行版（与 web 版共用同一套数据与 Agent）。

模块划分：
  ui.py        终端输出（ANSI 颜色 / 东亚宽度表格 / markdown 轻渲染）
  session.py   登录态与账号访问（data/dspro_session.json）
  errors.py    面向用户的错误类型
  cmd_auth.py  login / logout / whoami
  cmd_list.py  list       罗列账号信息（-db / -digest）
  cmd_digest.py digest    生成周报（可按领域名指定）
  cmd_chat.py  chat       命令行对话（复用 web 同一套装配管线）
  main.py      argparse 入口（pyproject 里注册为 dspro 命令）
"""

__version__ = "2.0.0"
