@echo off
rem dspro 包装脚本（Windows cmd/PowerShell）：免激活 venv 直接跑 CLI。
rem 用法：dspro login 用户名 密码  /  dspro list -digest  /  dspro digest "领域名"
setlocal
set "ROOT=%~dp0"
if exist "%ROOT%.venv\Scripts\python.exe" (
  "%ROOT%.venv\Scripts\python.exe" -m cli.main %*
) else (
  python -m cli.main %*
)
exit /b %ERRORLEVEL%
