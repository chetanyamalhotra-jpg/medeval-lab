@echo off
REM One-command reproduction on Windows. Double-click this file, or run it from cmd.
REM
REM PowerShell blocks unsigned scripts by default ("run_all.ps1 cannot be loaded
REM because it is not digitally signed"). This wrapper starts PowerShell with
REM -ExecutionPolicy Bypass, which applies to this single process only and does
REM not change the machine-wide setting.
setlocal
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0run_all.ps1" %*
exit /b %ERRORLEVEL%
