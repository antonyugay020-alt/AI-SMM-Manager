@echo off
setlocal EnableExtensions DisableDelayedExpansion
cd /d "%~dp0"
if errorlevel 1 goto :wrong_directory
echo ========================================================
echo AI SMM MANAGER - WORK A75 WINDOWS VALIDATION
echo This is a TEST CANDIDATE, NOT A RELEASE.
if not defined AI_SMM_TEST_PROFILE set "AI_SMM_TEST_PROFILE=fast"
echo Test profile: %AI_SMM_TEST_PROFILE% (fast=cloud plus random local when available; full=all eligible models + full GUI)
echo Candidate folder: %CD%
echo ========================================================
where py >nul 2>nul
if errorlevel 1 goto :try_python
set "PY_RUN=py -3"
goto :verify_source
:try_python
where python >nul 2>nul
if errorlevel 1 goto :missing_python
set "PY_RUN=python"
:verify_source
set "AI_SMM_TEST_SOURCE=%CD%\..\AI_SMM_Developer_A75\tests"
%PY_RUN% release_guard.py verify-tree "."
if errorlevel 1 goto :blocked
if exist ".venv\Scripts\python.exe" goto :install
%PY_RUN% -m venv .venv
if errorlevel 1 goto :venv_failed
:install
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto :install_failed
".venv\Scripts\python.exe" windows_acceptance.py
set "TEST_EXIT=%ERRORLEVEL%"
if exist "VALIDATION_RESULTS.zip" goto :results_ready
echo Test report was not created. Send a screenshot of the full window.
goto :finish
:results_ready
echo.
echo SEND THIS FILE: %CD%\VALIDATION_RESULTS.zip
:finish
echo Test process exit code: %TEST_EXIT%
pause
exit /b %TEST_EXIT%
:wrong_directory
echo ERROR: Cannot open the extracted program folder.
goto :fatal
:missing_python
echo ERROR: Python 3 is missing from PATH.
goto :fatal
:blocked
echo BLOCKED: Source integrity failed. Extract this ZIP to a NEW folder.
goto :fatal
:venv_failed
echo ERROR: Cannot create Python virtual environment.
goto :fatal
:install_failed
echo ERROR: Cannot install Python dependencies.
goto :fatal
:fatal
pause
exit /b 2
