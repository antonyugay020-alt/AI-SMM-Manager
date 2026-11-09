@echo off
setlocal
cd /d "%~dp0"
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
set "QA_REPORTS=%USERPROFILE%\Desktop\AI_SMM_REPORTS"
if not exist "%QA_REPORTS%" mkdir "%QA_REPORTS%"
echo AI SMM Manager - Windows source QA. No model downloads or paid APIs.
echo 1. RTX 3080 Ti - preliminary full Windows check
echo 2. RTX 2080 Super 8 GB - ten clean target checks
choice /c 12 /n /m "Select 1 or 2: "
if errorlevel 3 exit /b 2
if errorlevel 2 (set QA_ROLE=target_2080super) else (set QA_ROLE=preliminary_3080ti)
if exist "%QA_REPORTS%\LATEST.json" del "%QA_REPORTS%\LATEST.json"
>"%QA_REPORTS%\STATUS.json" echo {"status":"BOOTSTRAP","target_clean_consecutive":0,"release_eligible":false}
>"%QA_REPORTS%\BOOTSTRAP_STATUS.txt" echo Phase: %QA_ROLE%
echo All reports in ONE folder: %QA_REPORTS%
echo Last completed report ZIP: %QA_REPORTS%\DIAGNOSTICS.zip
echo Initial console output: BOOTSTRAP_CONSOLE.txt
if defined AI_SMM_QA_PYTHON goto run_custom
if exist "%~dp0.venv\Scripts\python.exe" (
 set "AI_SMM_QA_PYTHON=%~dp0.venv\Scripts\python.exe"
 goto run_custom
)
py -3.12 "%~dp0windows_qa_session.py" --role %QA_ROLE% >"%QA_REPORTS%\BOOTSTRAP_CONSOLE.txt" 2>&1
set "QA_RC=%ERRORLEVEL%"
goto finished
:run_custom
"%AI_SMM_QA_PYTHON%" "%~dp0windows_qa_session.py" --role %QA_ROLE% >"%QA_REPORTS%\BOOTSTRAP_CONSOLE.txt" 2>&1
set "QA_RC=%ERRORLEVEL%"
:finished
>>"%QA_REPORTS%\BOOTSTRAP_STATUS.txt" echo Exit code: %QA_RC%
if not exist "%QA_REPORTS%\LATEST.json" (
 >"%QA_REPORTS%\STATUS.json" echo {"status":"BLOCKED","target_clean_consecutive":0,"release_eligible":false}
 echo Python could not start. See BOOTSTRAP_CONSOLE.txt.
 echo Install Python 3.12 x64 and requirements-qa.txt once, or set AI_SMM_QA_PYTHON.
)
echo ONE REPORT TO SEND: %QA_REPORTS%\DIAGNOSTICS.zip
echo Current status: %QA_REPORTS%\STATUS.json
pause
exit /b %QA_RC%
