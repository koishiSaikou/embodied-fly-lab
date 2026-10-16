@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
set PYTHONUTF8=1
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" -X utf8 -B launcher.py %*
  goto finished
)
if exist ".brain-venv\Scripts\python.exe" (
  ".brain-venv\Scripts\python.exe" -X utf8 -B launcher.py %*
  goto finished
)
echo Project environments are missing.
echo If Setup.cmd is included, run Setup.cmd or Setup.cmd --body-only.
echo Otherwise restore the existing project environments.
echo Instructions: docs\启动与停止说明.md
set "FLYWIRE_EXIT=1"
goto wait_and_exit
:finished
set "FLYWIRE_EXIT=%ERRORLEVEL%"
echo.
echo FlyWire exited with code %FLYWIRE_EXIT%. Logs: output\launch
:wait_and_exit
if not "%FLYWIRE_NONINTERACTIVE%"=="1" pause
exit /b %FLYWIRE_EXIT%
