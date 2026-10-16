@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
set PYTHONUTF8=1
set "UV_CACHE_DIR=%~dp0.cache\uv"
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" -X utf8 -B tools\setup_source.py %*
  goto finished
)
set "SOURCE_UV=uv"
if exist "_tools\uv.exe" set "SOURCE_UV=_tools\uv.exe"
for /f "delims=" %%P in ('%SOURCE_UV% --no-config --offline python find --no-project --system --no-python-downloads 3.13 2^>nul') do (
  "%%P" -X utf8 -B tools\setup_source.py %*
  goto finished
)
for /f "delims=" %%P in ('%SOURCE_UV% --no-config --offline python find --no-project --system --no-python-downloads 3.12 2^>nul') do (
  "%%P" -X utf8 -B tools\setup_source.py %*
  goto finished
)
py -3 -c "import sys;sys.exit(not sys.version_info[:2] >= (3,12))" >nul 2>&1
if not errorlevel 1 (
  py -3 -X utf8 -B tools\setup_source.py %*
  goto finished
)
python -c "import sys;sys.exit(not sys.version_info[:2] >= (3,12))" >nul 2>&1
if not errorlevel 1 (
  python -X utf8 -B tools\setup_source.py %*
  goto finished
)
echo Python 3.12+ is required to run this installer. See docs\源码安装说明.md
set "SETUP_EXIT=1"
goto wait_and_exit
:finished
set "SETUP_EXIT=%ERRORLEVEL%"
:wait_and_exit
if not "%FLYWIRE_NONINTERACTIVE%"=="1" pause
exit /b %SETUP_EXIT%
