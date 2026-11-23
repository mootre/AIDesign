@echo off
setlocal
cd /d "%~dp0"
echo Installing Thai Furniture Analyzer dependencies. Internet is required.
py -3 -m venv .venv
if errorlevel 1 goto fail
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto fail
".venv\Scripts\python.exe" setup_model.py
if errorlevel 1 goto fail
echo Installation complete. Run codex login, then double-click start.bat. Analysis requires internet.
pause
exit /b 0
:fail
echo Installation failed. Check the messages above. Python 3.10 or newer is required.
pause
exit /b 1
