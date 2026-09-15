# -*- coding: utf-8 -*-
"""dspro 错误类型：CliError = 面向用户的错误（main 统一捕获 → 红字 + 退出码 1）。

与「程序缺陷」区分开：CliError 只承载「用户需要知道并据此操作」的信息
（未登录、密码错、领域不存在…），异常堆栈一律不打印，避免吓到使用者；
真正的 bug 仍走原生异常 + traceback。
"""


class CliError(Exception):
    """面向用户的 CLI 错误。"""

    def __init__(self, message: str, code: int = 1):
        super().__init__(message)
        self.message = message
        self.code = code
