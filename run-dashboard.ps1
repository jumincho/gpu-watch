$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$BundledPython = Join-Path $env:USERPROFILE ".cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
$ServerScript = [IO.Path]::GetFullPath((Join-Path $Root "server.py"))
$VersionPath = Join-Path $Root "VERSION"
$ExpectedBuildVersion = (Get-Content -Raw -LiteralPath $VersionPath -ErrorAction Stop).Trim()
$Port = 8787
$LocalSnapshotUrl = "http://127.0.0.1`:$Port/api/snapshot"
$LocalSshConfig = Join-Path $Root "scripts\local-ssh-config"
$KnownHostsPath = Join-Path $env:USERPROFILE ".ssh\known_hosts"
$IdentityPath = Join-Path $env:USERPROFILE ".ssh\id_ed25519"
$SystemOpenSsh = Join-Path ([Environment]::SystemDirectory) "OpenSSH\ssh.exe"
$SystemSshKeygen = Join-Path ([Environment]::SystemDirectory) "OpenSSH\ssh-keygen.exe"
$SensitiveEnvironmentPattern = '(?i)(PASSWORD|PASSWD|PASSPHRASE|TOKEN|SECRET|CREDENTIAL|AUTHORIZATION|COOKIE|API[_-]?KEY|PRIVATE[_-]?KEY|ACCESS[_-]?KEY)'

if ($ExpectedBuildVersion -notmatch '^[0-9A-Za-z.+-]+$') {
    throw "GPU Watch VERSION is missing or invalid."
}

$EmergencyLaunchToken = [string][Environment]::GetEnvironmentVariable("GPU_WATCH_EMERGENCY_LAUNCH_TOKEN", "Process")
$EmergencyLaunchIssuedText = [string][Environment]::GetEnvironmentVariable("GPU_WATCH_EMERGENCY_LAUNCH_ISSUED_AT", "Process")
$EmergencyLaunchIssuedAt = [long]0
$EmergencyLaunchNow = [DateTimeOffset]::UtcNow.ToUnixTimeSeconds()
if (
    $EmergencyLaunchToken -notmatch '^[0-9a-f]{32}$' -or
    -not [long]::TryParse($EmergencyLaunchIssuedText, [ref]$EmergencyLaunchIssuedAt) -or
    [Math]::Abs($EmergencyLaunchNow - $EmergencyLaunchIssuedAt) -gt 120
) {
    throw "Direct local launch is disabled. Use emergency-local-fallback.ps1."
}
[Environment]::SetEnvironmentVariable("GPU_WATCH_EMERGENCY_LAUNCH_TOKEN", $null, "Process")
[Environment]::SetEnvironmentVariable("GPU_WATCH_EMERGENCY_LAUNCH_ISSUED_AT", $null, "Process")

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
    $manifest = $lines -join ""
    $bytes = [Text.UTF8Encoding]::new($false).GetBytes($manifest)
    $sha = [Security.Cryptography.SHA256]::Create()
    try {
        return ([BitConverter]::ToString($sha.ComputeHash($bytes))).Replace("-", "").ToLowerInvariant()
    } finally {
        $sha.Dispose()
    }
}

$ExpectedReleaseFingerprint = Get-LocalReleaseFingerprint

function Test-Administrator {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = [Security.Principal.WindowsPrincipal]::new($identity)
    return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Clear-SensitiveProcessEnvironment {
    foreach ($item in Get-ChildItem Env:) {
        if ($item.Name -match $SensitiveEnvironmentPattern) {
            [Environment]::SetEnvironmentVariable($item.Name, $null, "Process")
        }
    }
}

function Test-LocalSshContract {
    foreach ($path in @($LocalSshConfig, $KnownHostsPath, $IdentityPath, $SystemOpenSsh, $SystemSshKeygen)) {
        if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
            throw "Required local SSH file is missing: $path"
        }
        $item = Get-Item -LiteralPath $path -Force -ErrorAction Stop
        if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
            throw "Refusing a reparse-point local SSH file: $path"
        }
    }

    function Require-EffectiveLine([string]$Alias, [string[]]$Effective, [string]$Expected) {
        if ($Effective -notcontains $Expected) {
            throw "Local SSH contract failed for ${Alias}: missing '$Expected'."
        }
    }

    function Reject-EffectiveKey([string]$Alias, [string[]]$Effective, [string]$Key) {
        if ($Effective -match "^$([Regex]::Escape($Key))\s") {
            throw "Local SSH contract failed for ${Alias}: forbidden '$Key'."
        }
    }

    function Require-EffectivePath(
        [string]$Alias,
        [string[]]$Effective,
        [string]$Key,
        [string]$ExpectedPath
    ) {
        $prefix = "$Key "
        $matches = @($Effective | Where-Object { $_.StartsWith($prefix, [StringComparison]::Ordinal) })
        if ($matches.Count -ne 1) {
            throw "Local SSH contract failed for ${Alias}: expected one '$Key' path."
        }
        $actual = [IO.Path]::GetFullPath($matches[0].Substring($prefix.Length).Replace('/', '\'))
        $expected = [IO.Path]::GetFullPath($ExpectedPath)
        if (-not [string]::Equals($actual, $expected, [StringComparison]::OrdinalIgnoreCase)) {
            throw "Local SSH contract failed for ${Alias}: wrong '$Key' path."
        }
    }

    $nlpEndpoints = [ordered]@{
        lab21 = @("198.51.100.21", 2222, $null)
        lab22 = @("198.51.100.22", 22, "gpuwatch-local-lab21")
        lab23 = @("198.51.100.23", 22, "gpuwatch-local-lab21")
        lab24 = @("198.51.100.24", 22, "gpuwatch-local-lab21")
        lab25 = @("198.51.100.25", 22, "gpuwatch-local-lab21")
        lab26 = @("198.51.100.26", 22, "gpuwatch-local-lab21")
        lab27 = @("198.51.100.27", 22, "gpuwatch-local-lab21")
        lab28 = @("198.51.100.28", 22, "gpuwatch-local-lab21")
    }
    foreach ($name in $nlpEndpoints.Keys) {
        $expected = $nlpEndpoints[$name]
        $alias = "gpuwatch-local-$name"
        $effective = @(& $SystemOpenSsh -G -F $LocalSshConfig $alias 2>$null)
        if ($LASTEXITCODE -ne 0) {
            throw "Local SSH contract expansion failed for $alias."
        }
        foreach ($required in @(
            "hostname $($expected[0])",
            "user gpuwatch",
            "port $($expected[1])",
            "batchmode no",
            "numberofpasswordprompts 1",
            "preferredauthentications password,keyboard-interactive",
            "passwordauthentication yes",
            "kbdinteractiveauthentication yes",
            "pubkeyauthentication false",
            "stricthostkeychecking true",
            "updatehostkeys false"
        )) {
            Require-EffectiveLine $alias $effective $required
        }
        Require-EffectivePath $alias $effective "userknownhostsfile" $KnownHostsPath
        Reject-EffectiveKey $alias $effective "proxycommand"
        if ($null -eq $expected[2]) {
            Reject-EffectiveKey $alias $effective "proxyjump"
        } else {
            Require-EffectiveLine $alias $effective "proxyjump $($expected[2])"
        }
    }

    $configuredHosts = @((Get-Content -Raw -LiteralPath (Join-Path $Root "hosts.json") | ConvertFrom-Json).hosts)
    foreach ($configuredHost in $configuredHosts) {
        $hostname = if ($configuredHost.ssh_host) { [string]$configuredHost.ssh_host } else { [string]$nlpEndpoints[[string]$configuredHost.name][0] }
        $port = if ($configuredHost.ssh_port) { [int]$configuredHost.ssh_port } else { [int]$nlpEndpoints[[string]$configuredHost.name][1] }
        if ([string]$configuredHost.lab -ne "nlp") {
            $user = [string]$configuredHost.ssh_user
            $alias = [string]$configuredHost.name
            if ([string]::IsNullOrWhiteSpace($hostname) -or [string]::IsNullOrWhiteSpace($user) -or $port -lt 1) {
                throw "Local SSH contract is incomplete for $alias."
            }
            $effective = @(& $SystemOpenSsh `
                -G -F $LocalSshConfig `
                -o BatchMode=yes `
                -i $IdentityPath `
                -o IdentitiesOnly=yes `
                -o PreferredAuthentications=publickey `
                -p $port `
                "${user}@${hostname}" 2>$null)
            if ($LASTEXITCODE -ne 0) {
                throw "Local SSH contract expansion failed for $alias."
            }
            foreach ($required in @(
                "hostname $hostname",
                "user $user",
                "port $port",
                "batchmode yes",
                "identitiesonly yes",
                "preferredauthentications publickey",
                "pubkeyauthentication true",
                "stricthostkeychecking true",
                "updatehostkeys false"
            )) {
                Require-EffectiveLine $alias $effective $required
            }
            Require-EffectivePath $alias $effective "identityfile" $IdentityPath
            Require-EffectivePath $alias $effective "userknownhostsfile" $KnownHostsPath
            Reject-EffectiveKey $alias $effective "proxycommand"
            Reject-EffectiveKey $alias $effective "proxyjump"
        }
        $lookup = if ($port -eq 22) { $hostname } else { "[$hostname]:$port" }
        & $SystemSshKeygen -F $lookup -f $KnownHostsPath *> $null
        if ($LASTEXITCODE -ne 0) {
            throw "known_hosts has no pinned key for $lookup."
        }
    }
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

function Test-LocalGpuWatchSnapshot {
    try {
        $snapshot = Invoke-RestMethod -Uri $LocalSnapshotUrl -TimeoutSec 2 -ErrorAction Stop
        return $snapshot.build_version -eq $ExpectedBuildVersion -and
            $snapshot.release_fingerprint -eq $ExpectedReleaseFingerprint -and
            $snapshot.runtime_mode -eq "emergency" -and
            $null -ne $snapshot.hosts -and $null -ne $snapshot.labs
    } catch {
        return $false
    }
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

if (Test-Administrator) {
    throw "GPU Watch must run as a standard user. Use emergency-local-fallback.ps1 so only the firewall helper is elevated."
}

Test-LocalSshContract

$Listeners = @(Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue)
if ($Listeners.Count -gt 0) {
    $ProcessIds = @($Listeners | Select-Object -ExpandProperty OwningProcess -Unique)
    $OnlyGpuWatch = $ProcessIds.Count -gt 0
    foreach ($ProcessId in $ProcessIds) {
        if (-not (Test-GpuWatchListener $ProcessId)) {
            $OnlyGpuWatch = $false
            break
        }
    }
    if ($OnlyGpuWatch) {
        if (-not (Test-LocalGpuWatchSnapshot)) {
            throw "The existing GPU Watch listener did not pass its local snapshot identity check."
        }
        Write-Host "GPU Watch is already listening on TCP/$Port."
        exit 0
    }
    throw "TCP/$Port is already used by a non-GPU-Watch process. Stop it or choose another port."
}

$TrustedPythonExecutables = @(Get-TrustedPythonExecutables)
if ($TrustedPythonExecutables.Count -eq 0) {
    throw "Python was not found. Install Python 3.10+ or run this from Codex where the bundled Python is available."
}
$Python = $TrustedPythonExecutables[0]

Set-Location $Root
Clear-SensitiveProcessEnvironment
$env:PYTHONDONTWRITEBYTECODE = "1"
$env:GPU_WATCH_ALLOWED_NETWORKS = "127.0.0.0/8,::1/128,192.0.2.0/24"
$env:GPU_WATCH_ALLOWED_HOSTS = "192.0.2.10:8787,192.0.2.50:8787,127.0.0.1:8787,localhost:8787,[::1]:8787"
$env:GPU_WATCH_BUILD_VERSION = $ExpectedBuildVersion
$env:GPU_WATCH_RUNTIME_MODE = "emergency"
$env:GPU_WATCH_SSH_TARGET_PREFIX = "gpuwatch-local-"
$env:GPU_WATCH_SSH_CONFIG_FILE = $LocalSshConfig
& $Python $ServerScript --host 0.0.0.0 --port $Port
