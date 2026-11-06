@echo off
setlocal EnableExtensions DisableDelayedExpansion
cd /d "%~dp0"
if errorlevel 1 exit /b 2
echo AI SMM A75 - REVIEWER CONTROLS ONLY - NOT RELEASE
echo Six randomized controls per model. No content generation or full test campaign.
where py >nul 2>nul
if errorlevel 1 goto :try_python
set "PY_RUN=py -3"
goto :run
:try_python
where python >nul 2>nul
if errorlevel 1 goto :missing_python
set "PY_RUN=python"
:run
%PY_RUN% windows_acceptance.py --reviewers-only
set "CHECK_EXIT=%ERRORLEVEL%"
pause
exit /b %CHECK_EXIT%
:missing_python
echo ERROR: Python 3 is missing from PATH.
pause
exit /b 2
