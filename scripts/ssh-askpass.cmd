@echo off
setlocal DisableDelayedExpansion
if not defined GPU_WATCH_SSH_PASSWORD exit /b 1
"%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" -NoLogo -NoProfile -NonInteractive -Command "[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false); $value = [Environment]::GetEnvironmentVariable('GPU_WATCH_SSH_PASSWORD', 'Process'); if ($null -eq $value) { exit 1 }; [Console]::Out.WriteLine($value)"
exit /b %ERRORLEVEL%
