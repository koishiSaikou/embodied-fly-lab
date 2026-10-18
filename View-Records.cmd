@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo The body/web environment is missing. Install the project first.
  echo See docs\实验保存与分享说明.md
  set "RECORDS_EXIT=1"
  goto finished
)
".venv\Scripts\python.exe" -X utf8 -B experiment_viewer.py %*
set "RECORDS_EXIT=%ERRORLEVEL%"
:finished
if not "%FLYWIRE_NONINTERACTIVE%"=="1" pause
exit /b %RECORDS_EXIT%
