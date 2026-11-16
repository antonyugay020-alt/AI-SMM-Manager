@echo off
setlocal
call "%~dp0RUN_WINDOWS_QA.cmd" --reviewer-diagnostic
exit /b %ERRORLEVEL%
