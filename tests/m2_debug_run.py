"""进程内直接跑 _run_agent，打印真实异常堆栈（绕过 FastAPI 包装）。
用法：python tests/m2_debug_run.py  （需服务所用依赖已装好；不依赖运行中的服务）
"""
import sys, os, traceback
# 项目根 = tests/ 的上一级；保证从任意 CWD 运行都能导入项目包
_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, _ROOT)
sys.chdir(_ROOT)
sys.path = [p for p in sys.path if "hermes-agent" not in p and "hermes_agent" not in p]

import api.server as s

q = "飞书主要对标我们公司的哪些产品？"
try:
    ans = s._run_agent(q, "localtest")
    print("=== 成功 ===")
    print(ans[:500])
except Exception:
    print("=== 真实异常 ===")
    traceback.print_exc()
