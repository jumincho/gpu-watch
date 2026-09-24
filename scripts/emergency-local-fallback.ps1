param(
    [ValidateSet("Start", "Stop", "Status")]
    [string]$Action = "Start",
    [switch]$Force
)

$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $PSScriptRoot
$Port = 8787
$LocalAddress = "192.0.2.50"
$LabRemoteAddress = "192.0.2.0/24"
$FirewallRuleName = "GpuWatchDashboard-LabLAN-8787"
$FirewallDisplayName = "GPU Watch Dashboard - Emergency Local 8787"
$ProductionSnapshotUrl = "http://192.0.2.10:8787/api/snapshot"
$LocalSnapshotUrl = "http://$LocalAddress`:$Port/api/snapshot"
$RunScript = Join-Path $Root "run-dashboard.ps1"
$BundledPython = Join-Path $env:USERPROFILE ".cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
$ServerScript = [IO.Path]::GetFullPath((Join-Path $Root "server.py"))
$VersionPath = Join-Path $Root "VERSION"
$ExpectedBuildVersion = $null
$ExpectedHostCount = $null
$ExpectedReleaseFingerprint = $null
$ReleaseMetadataError = $null
$SecretPath = Join-Path $Root "secrets\nlp_password"
$StartupShortcut = Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs\Startup\GPU Watch Dashboard.lnk.disabled"
$SensitiveEnvironmentPattern = '(?i)(PASSWORD|PASSWD|PASSPHRASE|TOKEN|SECRET|CREDENTIAL|AUTHORIZATION|COOKIE|API[_-]?KEY|PRIVATE[_-]?KEY|ACCESS[_-]?KEY)'

function Get-LocalReleaseFingerprint {
    $relativePaths = @("VERSION", "server.py", "hosts.json")
    $relativePaths += Get-ChildItem -LiteralPath (Join-Path $Root "gpu_watch") -File -Recurse -Filter "*.py" -ErrorAction Stop | ForEach-Object {
        $_.FullName.Substring($Root.Length).TrimStart('\').Replace('\', '/')
    }
    $relativePaths += Get-ChildItem -LiteralPath (Join-Path $Root "static") -File -Recurse -ErrorAction Stop | ForEach-Object {
        $_.FullName.Substring($Root.Length).TrimStart('\').Replace('\', '/')
    }
    $relativePaths = [string[]]@($relativePaths | Select-Object -Unique)
    [Array]::Sort($relativePaths, [StringComparer]::Ordinal)
    $lines = foreach ($relative in $relativePaths) {
        $fullPath = Join-Path $Root $relative.Replace('/', '\')
        $hash = (Get-FileHash -LiteralPath $fullPath -Algorithm SHA256).Hash.ToLowerInvariant()
        "$hash  $relative`n"
    }
    $bytes = [Text.UTF8Encoding]::new($false).GetBytes($lines -join "")
    $sha = [Security.Cryptography.SHA256]::Create()
    try {
        return ([BitConverter]::ToString($sha.ComputeHash($bytes))).Replace("-", "").ToLowerInvariant()
    } finally {
        $sha.Dispose()
    }
}

function Initialize-ReleaseMetadata {
    $buildVersion = (Get-Content -Raw -LiteralPath $VersionPath -ErrorAction Stop).Trim()
    if ($buildVersion -notmatch '^[0-9A-Za-z.+-]+$') {
        throw "GPU Watch VERSION is missing or invalid."
    }
    $hostsDocument = Get-Content -Raw -LiteralPath (Join-Path $Root "hosts.json") -ErrorAction Stop | ConvertFrom-Json
    $hostCount = @($hostsDocument.hosts).Count
    if ($hostCount -lt 1) {
        throw "GPU Watch hosts.json does not contain any hosts."
    }
    $script:ExpectedBuildVersion = $buildVersion
    $script:ExpectedHostCount = $hostCount
    $script:ExpectedReleaseFingerprint = Get-LocalReleaseFingerprint
}

function Test-Administrator {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = [Security.Principal.WindowsPrincipal]::new($identity)
    return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Quote-Argument([string]$Value) {
    return '"' + ($Value -replace '"', '\"') + '"'
}

function Clear-SensitiveProcessEnvironment {
    foreach ($item in Get-ChildItem Env:) {
        if ($item.Name -match $SensitiveEnvironmentPattern) {
            [Environment]::SetEnvironmentVariable($item.Name, $null, "Process")
        }
    }
}

function Invoke-FirewallOperation([ValidateSet("Enable", "Remove")][string]$Operation) {
    $enableScript = @'
$ErrorActionPreference = "Stop"
Get-ChildItem Env: | Where-Object { $_.Name -match '(?i)(PASSWORD|PASSWD|PASSPHRASE|TOKEN|SECRET|CREDENTIAL|AUTHORIZATION|COOKIE|API[_-]?KEY|PRIVATE[_-]?KEY|ACCESS[_-]?KEY)' } | ForEach-Object { [Environment]::SetEnvironmentVariable($_.Name, $null, "Process") }
$name = "GpuWatchDashboard-LabLAN-8787"
$display = "GPU Watch Dashboard - Emergency Local 8787"
$rule = Get-NetFirewallRule -Name $name -ErrorAction SilentlyContinue
if ($null -ne $rule) {
    Set-NetFirewallRule -Name $name -DisplayName $display -Enabled False -Direction Inbound -Action Allow -Profile Any | Out-Null
    $rule = Get-NetFirewallRule -Name $name -ErrorAction Stop
    Set-NetFirewallAddressFilter -AssociatedNetFirewallRule $rule -LocalAddress "192.0.2.50" -RemoteAddress "192.0.2.0/24" | Out-Null
    Set-NetFirewallPortFilter -AssociatedNetFirewallRule $rule -Protocol TCP -LocalPort 8787 | Out-Null
    Set-NetFirewallRule -Name $name -Enabled True | Out-Null
} else {
    New-NetFirewallRule -Name $name -DisplayName $display -Direction Inbound -Action Allow -Protocol TCP -LocalPort 8787 -LocalAddress "192.0.2.50" -RemoteAddress "192.0.2.0/24" -Profile Any | Out-Null
}
'@
    $removeScript = @'
$ErrorActionPreference = "Stop"
Get-ChildItem Env: | Where-Object { $_.Name -match '(?i)(PASSWORD|PASSWD|PASSPHRASE|TOKEN|SECRET|CREDENTIAL|AUTHORIZATION|COOKIE|API[_-]?KEY|PRIVATE[_-]?KEY|ACCESS[_-]?KEY)' } | ForEach-Object { [Environment]::SetEnvironmentVariable($_.Name, $null, "Process") }
Remove-NetFirewallRule -Name "GpuWatchDashboard-LabLAN-8787" -ErrorAction SilentlyContinue
'@
    $scriptText = if ($Operation -eq "Enable") { $enableScript } else { $removeScript }

    if (Test-Administrator) {
        & ([ScriptBlock]::Create($scriptText))
        return
    }

    $encoded = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($scriptText))
    $powerShellPath = (Get-Process -Id $PID -ErrorAction Stop).Path
    $process = Start-Process `
        -FilePath $powerShellPath `
        -ArgumentList @("-NoProfile", "-NonInteractive", "-EncodedCommand", $encoded) `
        -Verb RunAs `
        -WindowStyle Hidden `
        -Wait `
        -PassThru
    if ($process.ExitCode -ne 0) {
        throw "Elevated firewall operation '$Operation' failed with exit code $($process.ExitCode)."
    }
}

function Get-ProductionSnapshot {
    try {
        $snapshot = Invoke-RestMethod -Uri $ProductionSnapshotUrl -TimeoutSec 3 -ErrorAction Stop
        if ([string]::IsNullOrWhiteSpace([string]$snapshot.build_version) -or
            $null -eq $snapshot.hosts -or
            $null -eq $snapshot.labs) {
            return $null
        }
        return $snapshot
    } catch {
        return $null
    }
}

function Test-LocalGpuWatchSnapshot([double]$NotBeforeEpochSeconds = 0) {
    try {
        $snapshot = Invoke-RestMethod -Uri $LocalSnapshotUrl -TimeoutSec 2 -ErrorAction Stop
        if ($snapshot.build_version -ne $ExpectedBuildVersion -or
            $snapshot.release_fingerprint -ne $ExpectedReleaseFingerprint -or
            $snapshot.runtime_mode -ne "emergency" -or
            $null -eq $snapshot.hosts -or $null -eq $snapshot.labs -or
            @($snapshot.hosts).Count -ne $ExpectedHostCount -or
            $null -eq $snapshot.collector -or
            -not $snapshot.collector.alive -or
            $snapshot.collector.last_cycle_error) {
            return $false
        }
        $completedAt = [double]$snapshot.collector.last_cycle_completed_at
        $freshCutoff = [Math]::Max(
            $NotBeforeEpochSeconds,
            [DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds() / 1000.0 - 120.0
        )
        if ($completedAt -lt $freshCutoff) {
            return $false
        }
        $freshHosts = @($snapshot.hosts | Where-Object {
            if (-not $_.online -or @($_.gpus).Count -eq 0 -or -not $_.last_seen) {
                return $false
            }
            try {
                return [DateTimeOffset]::Parse([string]$_.last_seen).ToUnixTimeMilliseconds() / 1000.0 -ge $freshCutoff
            } catch {
                return $false
            }
        }).Count
        $minimumFreshHosts = [Math]::Max(1, [Math]::Ceiling($ExpectedHostCount * 0.75))
        return $freshHosts -ge $minimumFreshHosts
    } catch {
        return $false
    }
}

function Get-LocalListeners {
    Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue |
        Where-Object { $_.LocalAddress -eq "0.0.0.0" -or $_.LocalAddress -eq "::" -or $_.LocalAddress -eq $LocalAddress }
}

function Get-FallbackFirewallRule {
    Get-NetFirewallRule -Name $FirewallRuleName -ErrorAction SilentlyContinue
}

function Get-TrustedPythonExecutables {
    $paths = @()
    if (Test-Path -LiteralPath $BundledPython -PathType Leaf) {
        $paths += [IO.Path]::GetFullPath($BundledPython)
    }
    $pythonCommand = Get-Command python -CommandType Application -ErrorAction SilentlyContinue
    if ($pythonCommand -and $pythonCommand.Source) {
        $paths += [IO.Path]::GetFullPath($pythonCommand.Source)
    }
    return @($paths | Select-Object -Unique)
}

function Test-GpuWatchCommandLine(
    [string]$CommandLine,
    [string]$ExpectedServerScript,
    [string]$ExpectedPythonExecutable
) {
    if ([string]::IsNullOrWhiteSpace($CommandLine) -or [string]::IsNullOrWhiteSpace($ExpectedPythonExecutable)) {
        return $false
    }
    $normalizedCommand = $CommandLine.Replace('/', '\').Trim()
    $normalizedScript = [IO.Path]::GetFullPath($ExpectedServerScript).Replace('/', '\')
    $normalizedPython = [IO.Path]::GetFullPath($ExpectedPythonExecutable).Replace('/', '\')
    $escapedPython = [Regex]::Escape($normalizedPython)
    $escapedScript = [Regex]::Escape($normalizedScript)
    $pythonToken = if ($normalizedPython -match '\s') { '"' + $escapedPython + '"' } else { '(?:"' + $escapedPython + '"|' + $escapedPython + ')' }
    $scriptToken = if ($normalizedScript -match '\s') { '"' + $escapedScript + '"' } else { '(?:"' + $escapedScript + '"|' + $escapedScript + ')' }
    $pattern = '^\s*' + $pythonToken + '\s+' + $scriptToken + '\s+--host\s+0\.0\.0\.0\s+--port\s+8787\s*$'
    return [Regex]::IsMatch($normalizedCommand, $pattern, [Text.RegularExpressions.RegexOptions]::IgnoreCase)
}

function Test-GpuWatchListener([int]$ProcessId) {
    try {
        $process = Get-CimInstance Win32_Process -Filter "ProcessId=$ProcessId" -ErrorAction Stop
        if ([string]::IsNullOrWhiteSpace($process.ExecutablePath)) {
            return $false
        }
        $actualPython = [IO.Path]::GetFullPath($process.ExecutablePath)
        $trusted = @(Get-TrustedPythonExecutables | Where-Object {
            [string]::Equals($_, $actualPython, [StringComparison]::OrdinalIgnoreCase)
        })
        return $trusted.Count -gt 0 -and (Test-GpuWatchCommandLine $process.CommandLine $ServerScript $actualPython)
    } catch {
        return $false
    }
}

function Protect-NlpPasswordFile {
    $item = Get-Item -LiteralPath $SecretPath -Force -ErrorAction Stop
    if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
        throw "Refusing a reparse-point NLP secret file."
    }
    $identityName = [Security.Principal.WindowsIdentity]::GetCurrent().Name
    $icacls = Join-Path $env:SystemRoot "System32\icacls.exe"
    & $icacls $SecretPath "/inheritance:r" "/grant:r" "${identityName}:(F)" "*S-1-5-18:(F)" | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to restrict the NLP secret ACL."
    }
    attrib +H $SecretPath 2>$null
}

function Protect-LocalRuntimeDirectories {
    $identityName = [Security.Principal.WindowsIdentity]::GetCurrent().Name
    $icacls = Join-Path $env:SystemRoot "System32\icacls.exe"

    function Protect-PathAcl([string]$Path, [switch]$Container) {
        if (-not (Test-Path -LiteralPath $Path)) {
            return
        }
        $item = Get-Item -LiteralPath $Path -Force -ErrorAction Stop
        if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
            throw "Refusing a reparse-point GPU Watch runtime path: $Path"
        }
        $grants = if ($Container) {
            @("${identityName}:(OI)(CI)(F)", "*S-1-5-18:(OI)(CI)(F)", "*S-1-5-32-544:(OI)(CI)(F)")
        } else {
            @("${identityName}:(F)", "*S-1-5-18:(F)", "*S-1-5-32-544:(F)")
        }
        & $icacls $Path "/inheritance:r" "/grant:r" @grants | Out-Null
        if ($LASTEXITCODE -ne 0) {
            throw "Failed to restrict the local GPU Watch runtime ACL: $Path"
        }
    }

    foreach ($path in @((Join-Path $Root "data"), (Join-Path $Root "secrets"))) {
        if (-not (Test-Path -LiteralPath $path -PathType Container)) {
            continue
        }
        Protect-PathAcl -Path $path -Container
    }
    foreach ($path in @(
        (Join-Path $Root "data\admin_pin.hash"),
        (Join-Path $Root "data\artificial_analysis_intelligence_index.json"),
        (Join-Path $Root "data\gpu_watch.sqlite3"),
        (Join-Path $Root "data\gpu_watch.sqlite3-wal"),
        (Join-Path $Root "data\gpu_watch.sqlite3-shm"),
        (Join-Path $Root "secrets\artificial_analysis_api_key"),
        $SecretPath
    )) {
        Protect-PathAcl -Path $path
    }
}

function Ensure-NlpPasswordFile {
    if (-not (Test-Path -LiteralPath $SecretPath)) {
        $secretDir = Split-Path -Parent $SecretPath
        New-Item -ItemType Directory -Force -Path $secretDir | Out-Null

        $secure = Read-Host -AsSecureString "Enter the NLP lab SSH password for emergency fallback"
        $bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
        $plain = $null
        try {
            $plain = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr)
            if ([string]::IsNullOrWhiteSpace($plain)) {
                throw "NLP lab password was empty."
            }
            $utf8NoBom = [Text.UTF8Encoding]::new($false)
            [IO.File]::WriteAllText($SecretPath, $plain.Trim() + [Environment]::NewLine, $utf8NoBom)
        } finally {
            $plain = $null
            if ($bstr -ne [IntPtr]::Zero) {
                [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr)
            }
        }
    }
    Protect-NlpPasswordFile
}

function Remove-NlpPasswordFile {
    Remove-Item -LiteralPath $SecretPath -Force -ErrorAction SilentlyContinue
}

function Stop-LocalDashboard {
    $listeners = @(Get-LocalListeners)
    $unexpected = @()
    foreach ($listener in $listeners) {
        $process = Get-Process -Id $listener.OwningProcess -ErrorAction SilentlyContinue
        if ($process -and (Test-GpuWatchListener $process.Id)) {
            Write-Host "Stopping local TCP/$Port listener: PID $($process.Id) $($process.ProcessName)"
            Stop-Process -Id $process.Id -Force -ErrorAction Stop
        } elseif ($process) {
            $unexpected += "PID $($process.Id) $($process.ProcessName)"
        }
    }
    if ($unexpected.Count -gt 0) {
        throw "TCP/$Port is owned by a non-GPU-Watch process: $($unexpected -join ', ')"
    }
}

function Start-LocalDashboard {
    $productionSnapshot = Get-ProductionSnapshot
    if ($productionSnapshot) {
        $productionBuildVersion = [string]$productionSnapshot.build_version
        if ($productionBuildVersion -ne $ExpectedBuildVersion) {
            throw "Production/local VERSION mismatch ($productionBuildVersion != $ExpectedBuildVersion). Synchronize the emergency copy before takeover."
        }
        $productionFingerprint = [string]$productionSnapshot.release_fingerprint
        if ([string]::IsNullOrWhiteSpace($productionFingerprint)) {
            throw "Production is reachable but does not publish a release fingerprint. Deploy/synchronize GPU Watch before takeover."
        }
        if ($productionFingerprint -ne $ExpectedReleaseFingerprint) {
            throw "Production/local release fingerprint mismatch. Synchronize the emergency copy before takeover."
        }
        if (-not $Force) {
            Write-Host "Production is reachable. Local fallback was not started."
            Write-Host "Use -Force only for a deliberate manual takeover."
            exit 2
        }
    }
    if (Test-Administrator) {
        throw "Start the fallback from a standard PowerShell session. Only the firewall helper may run elevated."
    }

    $existingListeners = @(Get-LocalListeners)
    if ($existingListeners.Count -gt 0) {
        $ownedByGpuWatch = @($existingListeners | Where-Object { Test-GpuWatchListener $_.OwningProcess }).Count
        if ($ownedByGpuWatch -eq $existingListeners.Count) {
            if (-not (Test-LocalGpuWatchSnapshot)) {
                throw "The existing GPU Watch listener did not pass its local snapshot identity check."
            }
            $secretExisted = Test-Path -LiteralPath $SecretPath
            try {
                Clear-SensitiveProcessEnvironment
                Protect-LocalRuntimeDirectories
                Ensure-NlpPasswordFile
                Protect-LocalRuntimeDirectories
                Invoke-FirewallOperation "Enable"
            } catch {
                $failure = $_
                try { Invoke-FirewallOperation "Remove" } catch { Write-Warning $_ }
                if (-not $secretExisted) {
                    Remove-NlpPasswordFile
                }
                throw $failure
            }
            Write-Host "Local TCP/$Port is already healthy; the restricted firewall rule was reconciled."
            Write-Host "Local URL: http://$LocalAddress`:$Port"
            exit 0
        }
        throw "TCP/$Port is already used by a non-GPU-Watch process."
    }

    $secretExisted = Test-Path -LiteralPath $SecretPath
    $launcher = $null
    try {
        Clear-SensitiveProcessEnvironment
        Protect-LocalRuntimeDirectories
        Ensure-NlpPasswordFile
        Protect-LocalRuntimeDirectories
        Invoke-FirewallOperation "Enable"

        $launchStartedAt = [DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds() / 1000.0
        $launchTokenName = "GPU_WATCH_EMERGENCY_LAUNCH_TOKEN"
        $launchIssuedName = "GPU_WATCH_EMERGENCY_LAUNCH_ISSUED_AT"
        try {
            [Environment]::SetEnvironmentVariable($launchTokenName, [Guid]::NewGuid().ToString("N"), "Process")
            [Environment]::SetEnvironmentVariable(
                $launchIssuedName,
                [DateTimeOffset]::UtcNow.ToUnixTimeSeconds().ToString(),
                "Process"
            )
            $launcher = Start-Process `
                -FilePath (Get-Process -Id $PID -ErrorAction Stop).Path `
                -ArgumentList @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", (Quote-Argument $RunScript)) `
                -WorkingDirectory $Root `
                -WindowStyle Hidden `
                -PassThru
        } finally {
            [Environment]::SetEnvironmentVariable($launchTokenName, $null, "Process")
            [Environment]::SetEnvironmentVariable($launchIssuedName, $null, "Process")
        }

        for ($attempt = 1; $attempt -le 90; $attempt++) {
            Start-Sleep -Seconds 1
            if (Test-LocalGpuWatchSnapshot -NotBeforeEpochSeconds $launchStartedAt) {
                Write-Host "Emergency local fallback is running."
                Write-Host "Local URL: http://$LocalAddress`:$Port"
                exit 0
            }
        }
        throw "Local fallback did not complete a fresh collector cycle on $LocalSnapshotUrl within 90 seconds."
    } catch {
        $failure = $_
        if ($launcher -and -not $launcher.HasExited) {
            Stop-Process -Id $launcher.Id -Force -ErrorAction SilentlyContinue
        }
        try { Stop-LocalDashboard } catch { Write-Warning $_ }
        try { Invoke-FirewallOperation "Remove" } catch { Write-Warning $_ }
        if (-not $secretExisted) {
            Remove-NlpPasswordFile
        }
        throw $failure
    }
}

function Stop-Fallback {
    $failures = @()
    try { Stop-LocalDashboard } catch { $failures += $_.Exception.Message }
    try { Invoke-FirewallOperation "Remove" } catch { $failures += $_.Exception.Message }
    try { Remove-NlpPasswordFile } catch { $failures += $_.Exception.Message }
    if ($failures.Count -gt 0) {
        throw "Emergency fallback cleanup was incomplete: $($failures -join '; ')"
    }
    Write-Host "Emergency local fallback is stopped and local-only firewall/secret state was removed."
}

function Show-Status {
    $rule = Get-FallbackFirewallRule
    $listeners = @(Get-LocalListeners)
    $userEnvPassword = [Environment]::GetEnvironmentVariable("GPU_WATCH_NLP_PASSWORD", "User")
    $productionSnapshot = Get-ProductionSnapshot
    $productionBuildVersion = if ($productionSnapshot) { [string]$productionSnapshot.build_version } else { $null }
    $productionFingerprint = if ($productionSnapshot) { [string]$productionSnapshot.release_fingerprint } else { $null }
    $productionPublishesFingerprint = $productionSnapshot -and -not [string]::IsNullOrWhiteSpace($productionFingerprint)

    [PSCustomObject]@{
        ProductionReachable = ($null -ne $productionSnapshot)
        ProductionBuildVersion = $productionBuildVersion
        LocalBuildVersion = $ExpectedBuildVersion
        VersionMatches = if ($productionSnapshot) { $productionBuildVersion -eq $ExpectedBuildVersion } else { $null }
        ProductionReleaseFingerprint = $productionFingerprint
        LocalReleaseFingerprint = $ExpectedReleaseFingerprint
        ProductionPublishesReleaseFingerprint = [bool]$productionPublishesFingerprint
        ReleaseFingerprintMatches = if ($productionPublishesFingerprint) { $productionFingerprint -eq $ExpectedReleaseFingerprint } else { $null }
        LocalReleaseMetadataValid = [string]::IsNullOrWhiteSpace($ReleaseMetadataError)
        LocalReleaseMetadataError = $ReleaseMetadataError
        LocalListening = ($listeners.Count -gt 0)
        LocalSnapshotValid = Test-LocalGpuWatchSnapshot
        LocalListenerPids = (($listeners | Select-Object -ExpandProperty OwningProcess -Unique) -join ",")
        FirewallRulePresent = ($null -ne $rule)
        FirewallRuleEnabled = if ($rule) { $rule.Enabled } else { "Absent" }
        NlpSecretFilePresent = Test-Path -LiteralPath $SecretPath
        UserEnvPasswordPresent = -not [string]::IsNullOrEmpty($userEnvPassword)
        UserEnvPasswordIgnored = $true
        StartupShortcutDisabledPresent = Test-Path -LiteralPath $StartupShortcut
    } | Format-List
}

switch ($Action) {
    "Start" {
        Initialize-ReleaseMetadata
        Start-LocalDashboard
    }
    "Stop" { Stop-Fallback }
    "Status" {
        try {
            Initialize-ReleaseMetadata
        } catch {
            $script:ReleaseMetadataError = $_.Exception.Message
        }
        Show-Status
    }
}
