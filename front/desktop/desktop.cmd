@echo off
chcp 65001 >nul 2>&1
rem ============================================================
rem  ZhiXuan Intel Officer - Desktop launcher (pywebview shell)
rem
rem  Double-click to run. Args pass through, e.g.
rem      desktop.cmd --port 8124
rem      desktop.cmd --no-tray --debug
rem
rem  !! KEEP THIS FILE PURE ASCII !!
rem  cmd.exe parses a .cmd file in the *current console code page*. If the
rem  file contains UTF-8 Chinese bytes while the console is GBK, the parser
rem  mis-decodes them and can swallow following characters / merge lines ---
rem  symptoms seen in practice: "'local' is not recognized" (setlocal got
rem  eaten), a wrong python being used, or app.py not found. Comments are
rem  parsed too, so even Chinese comments are unsafe.
rem  Chinese text is fine *inside* app.py (Python handles UTF-8 properly).
rem ============================================================
setlocal
set "HERE=%~dp0"
set "ROOT=%HERE%..\.."
set "HTTP_PROXY="
set "HTTPS_PROXY="
set "ALL_PROXY="
set "NO_PROXY=127.0.0.1,localhost"
set "PYTHONPATH="
rem Force UTF-8 I/O: on a Chinese (GBK) console, printing emoji-bearing text (the project
rem prints prompts at import) raises UnicodeEncodeError and the backend dies at import.
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
set "PY=%ROOT%\.venv\Scripts\python.exe"
if not exist "%PY%" set "PY=python"
"%PY%" "%HERE%app.py" %*
if errorlevel 1 goto :failed
exit /b 0
:failed
echo.
echo [exit code %ERRORLEVEL%] log: %HERE%desktop.log
pause
exit /b %ERRORLEVEL%
