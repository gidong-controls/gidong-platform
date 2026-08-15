@echo off
setlocal

set "SCRIPT_DIR=%~dp0"
"%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%SCRIPT_DIR%Test-ControlStudio-Development.ps1" %*
exit /b %ERRORLEVEL%
