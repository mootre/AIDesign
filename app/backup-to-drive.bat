@echo off
chcp 65001 >nul
setlocal
set "PYTHONUTF8=1"
if exist "%~dp0.venv\Scripts\python.exe" (
  "%~dp0.venv\Scripts\python.exe" "%~dp0backup_training.py" %*
) else (
  python "%~dp0backup_training.py" %*
)
pause
