@echo off
call "%~dp0Start.cmd" check %*
exit /b %ERRORLEVEL%
