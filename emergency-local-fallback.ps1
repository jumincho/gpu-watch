$global:LASTEXITCODE = 0
& (Join-Path $PSScriptRoot "scripts\emergency-local-fallback.ps1") @args
$exitCode = [int]$global:LASTEXITCODE
exit $exitCode
