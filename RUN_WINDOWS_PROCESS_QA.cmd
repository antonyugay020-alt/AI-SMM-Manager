@echo off
setlocal
call "%~dp0RUN_WINDOWS_QA.cmd" --process-diagnostic
exit /b %ERRORLEVEL%
