@echo off
setlocal EnableExtensions DisableDelayedExpansion
cd /d "%~dp0"
if errorlevel 1 goto :failed
where py >nul 2>nul
if errorlevel 1 goto :python
set "PY_RUN=py -3"
goto :verify
:python
where python >nul 2>nul
if errorlevel 1 goto :missing
set "PY_RUN=python"
:verify
%PY_RUN% release_guard.py verify-tree "."
if errorlevel 1 goto :failed
if exist ".venv\Scripts\python.exe" goto :dependencies
%PY_RUN% -m venv .venv
if errorlevel 1 goto :failed
:dependencies
".venv\Scripts\python.exe" -c "import PySide6, PIL, gradio_client" >nul 2>nul
if not errorlevel 1 goto :start
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto :failed
:start
".venv\Scripts\python.exe" app.py
set "APP_EXIT=%ERRORLEVEL%"
if not "%APP_EXIT%"=="0" pause
exit /b %APP_EXIT%
:missing
echo Python 3 is required.
:failed
echo Application could not start. Read the error above.
pause
exit /b 2
