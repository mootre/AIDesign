@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
set "CODEX_HOME=%USERPROFILE%\.codex"
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" start_ui.py
) else if exist "%USERPROFILE%\Documents\Codex\2026-11-13\browser-https-www-dlt-elearning-com\outputs\ThaiPDFReader\.venv\Scripts\python.exe" (
  "%USERPROFILE%\Documents\Codex\2026-11-13\browser-https-www-dlt-elearning-com\outputs\ThaiPDFReader\.venv\Scripts\python.exe" start_ui.py
) else (
  echo Please run install.bat first.
)
pause
