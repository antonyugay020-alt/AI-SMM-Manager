@echo off
setlocal
cd /d "%~dp0"
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
set "QA_REPORTS=%USERPROFILE%\Desktop\AI_SMM_REPORTS"
if not exist "%QA_REPORTS%" mkdir "%QA_REPORTS%"
set "QA_SETUP_LOG=%QA_REPORTS%\SETUP_CONSOLE.txt"
echo Preparing the source-local .venv with free QA Python dependencies.
echo This explicit setup downloads Python packages, not models or CUDA engines.
echo Existing model environments and saved QA settings are preserved.
echo Setup log: %QA_SETUP_LOG%
>"%QA_SETUP_LOG%" echo AI SMM QA setup in "%~dp0"
if exist "%~dp0.venv\Scripts\python.exe" goto validate
py -3.12 -m venv "%~dp0.venv" >>"%QA_SETUP_LOG%" 2>&1
if errorlevel 1 goto failed
:validate
set "QA_SETUP_PYTHON=%~dp0.venv\Scripts\python.exe"
"%QA_SETUP_PYTHON%" -I -c "import sys,struct; print(sys.executable); assert sys.version_info[:2]==(3,12) and struct.calcsize('P')==8 and sys.prefix!=sys.base_prefix, 'A Python 3.12 x64 virtual environment is required'" >>"%QA_SETUP_LOG%" 2>&1
if errorlevel 1 goto failed
"%QA_SETUP_PYTHON%" -I -m pip --isolated install -r "%~dp0requirements-qa.txt" >>"%QA_SETUP_LOG%" 2>&1
if errorlevel 1 goto failed
"%QA_SETUP_PYTHON%" -I -m pip check >>"%QA_SETUP_LOG%" 2>&1
if errorlevel 1 goto failed
"%QA_SETUP_PYTHON%" -I -c "import PySide6.QtWidgets, PIL.Image, gradio_client, pytest, psutil; print('QA dependency imports OK')" >>"%QA_SETUP_LOG%" 2>&1
if errorlevel 1 goto failed
"%QA_SETUP_PYTHON%" -I "%~dp0windows_qa_environment.py" >>"%QA_SETUP_LOG%" 2>&1
if errorlevel 1 goto failed
echo Setup completed. Open RUN_WINDOWS_QA.cmd and select 1 on the RTX 3080 Ti.
if defined AI_SMM_QA_PYTHON echo WARNING: AI_SMM_QA_PYTHON overrides this .venv. Clear that override or point it here.
echo Setup success is not a Windows Qt/CUDA acceptance result.
pause
exit /b 0
:failed
echo Setup failed. See %QA_SETUP_LOG%
echo No QA run has been counted. Existing environments were not deleted.
pause
exit /b 2
