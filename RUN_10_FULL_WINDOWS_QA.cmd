@echo off
setlocal
cd /d "%~dp0"
py -3 "AI_SMM_10_FULL_WINDOWS_QA.py" "AI_SMM_Work_Architecture_A75" --tests-source "AI_SMM_Developer_A75\tests" --output "AI_SMM_TEN_FULL_QA_RESULTS"
set RC=%ERRORLEVEL%
echo.
if not "%RC%"=="0" echo BLOCKED: release acceptance did not pass.
echo Evidence: AI_SMM_TEN_FULL_QA_RESULTS\TEN_RUN_STATUS.json
pause
exit /b %RC%
