"""Protect recovery boundaries without launching, stopping, or mutating a service."""
import os
import runpy
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock
from gpu_watch.auth import hash_passphrase, is_valid_passphrase_hash, verify_passphrase

ROOT = Path(__file__).resolve().parents[1]

class RecoveryBoundaryTests(unittest.TestCase):
    def test_deploy_ensure_preserves_valid_legacy_pin_for_login_upgrade(self):
        ensure = runpy.run_path(str(ROOT / "scripts/admin-passphrase.py"))["ensure"]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "admin.hash"
            initial = Path(directory) / "initial.txt"
            encoded = hash_passphrase("2468", iterations=240_000)
            path.write_text(encoded + "\n", encoding="utf-8")
            with mock.patch.dict(ensure.__globals__, {"hash_passphrase": mock.Mock(side_effect=AssertionError("must not rotate"))}):
                self.assertEqual(ensure(path, initial), 0)
            self.assertEqual(path.read_text(encoding="utf-8").strip(), encoded)
            self.assertTrue(verify_passphrase("2468", encoded))
            self.assertFalse(initial.exists())

    def test_invalid_existing_hash_requires_explicit_repair(self):
        ensure = runpy.run_path(str(ROOT / "scripts/admin-passphrase.py"))["ensure"]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "admin.hash"
            initial = Path(directory) / "initial.txt"
            path.write_text("broken\n", encoding="utf-8")
            with self.assertRaises(ValueError):
                ensure(path, initial)
            self.assertEqual(path.read_text(encoding="utf-8"), "broken\n")
            self.assertFalse(initial.exists())
        for encoded in (None, "bad", "pbkdf2_sha256$999999999$somesalt$AAAA", "pbkdf2_sha256$600000$somesalt$%%%%"):
            self.assertFalse(is_valid_passphrase_hash(encoded))

    @unittest.skipUnless(os.name == "nt" and shutil.which("powershell"), "requires Windows ACLs")
    def test_private_acl_removes_preexisting_explicit_everyone_grants(self):
        command = r'''
$ErrorActionPreference = "Stop"
Import-Module (Join-Path $PSHOME "Modules\Microsoft.PowerShell.Security\Microsoft.PowerShell.Security.psd1")
$tokens = $null; $errors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile($env:GPU_WATCH_TEST_SCRIPT, [ref]$tokens, [ref]$errors)
if ($errors.Count -ne 0) { throw ($errors -join "; ") }
$function = $ast.Find({ param($node) $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq "Protect-PrivatePathAcl" }, $true)
if ($null -eq $function) { throw "missing ACL helper" }
Invoke-Expression $function.Extent.Text
$directory = $env:GPU_WATCH_TEST_DIRECTORY
$file = Join-Path $directory "secret.txt"
[IO.File]::WriteAllText($file, "test-only")
foreach ($path in @($directory, $file)) {
    & (Join-Path $env:SystemRoot "System32\icacls.exe") $path "/grant" "*S-1-1-0:(R)" | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "could not prepare explicit grant" }
}
Protect-PrivatePathAcl -Path $directory -Container
Protect-PrivatePathAcl -Path $file
$allowed = @([Security.Principal.WindowsIdentity]::GetCurrent().User.Value, "S-1-5-18", "S-1-5-32-544")
foreach ($path in @($directory, $file)) {
    $acl = Get-Acl -LiteralPath $path
    if (-not $acl.AreAccessRulesProtected) { throw "inheritance remained active" }
    $rules = @($acl.Access)
    if ($rules.Count -ne 3) { throw "unexpected rule count" }
    foreach ($rule in $rules) {
        $sid = $rule.IdentityReference.Translate([Security.Principal.SecurityIdentifier]).Value
        if ($allowed -notcontains $sid -or $rule.IsInherited -or $rule.AccessControlType -ne "Allow" -or $rule.FileSystemRights -ne "FullControl") { throw "unsafe retained ACL" }
    }
}
'''
        with tempfile.TemporaryDirectory() as directory:
            env = os.environ.copy()
            env["GPU_WATCH_TEST_SCRIPT"] = str(ROOT / "scripts/emergency-local-fallback.ps1")
            env["GPU_WATCH_TEST_DIRECTORY"] = directory
            shells = ["powershell"] + ([shutil.which("pwsh")] if shutil.which("pwsh") else [])
            for shell in shells:
                with self.subTest(shell=shell):
                    result = subprocess.run([shell, "-NoProfile", "-NonInteractive", "-Command", command], env=env, capture_output=True, text=True, timeout=20)
                    self.assertEqual(result.returncode, 0, result.stderr or result.stdout)

    def test_runner_pins_validated_identity_and_direct_listener_proxy_scope(self):
        source = (ROOT / "run-dashboard.ps1").read_text(encoding="utf-8")
        self.assertIn('$env:GPU_WATCH_SSH_IDENTITY_FILE = $IdentityPath', source)
        self.assertIn('$env:GPU_WATCH_TRUSTED_PROXY_NETWORKS = "127.0.0.0/8,::1/128"', source)
        self.assertLess(source.index('$env:GPU_WATCH_SSH_IDENTITY_FILE = $IdentityPath'), source.index('& $Python $ServerScript'))


class DeploymentGateTests(unittest.TestCase):
    @unittest.skipUnless(os.name == "posix" and shutil.which("sh"), "POSIX deployment gate")
    def test_low_disk_override_still_accepts_a_fully_healthy_release(self):
        source = (ROOT / "deploy.sh").read_text()
        end = source.index("\n\nDEPLOY_COMPLETE=1")
        start = source.rindex('if [ "$ALLOW_LOW_DISK_DEPLOY" -eq 1 ]; then', 0, end)
        gate = source[start:end]
        for override, healthy, low_only, expected in [(0,0,1,0),(0,1,0,1),(1,0,1,0),(1,1,0,0),(1,1,1,1)]:
            with self.subTest(override=override, healthy=healthy, low_only=low_only):
                script = ('set -e\nCONTAINER=fixture\nBUILD_VERSION=2.5\n'
                          f'ALLOW_LOW_DISK_DEPLOY={override}\n'
                          f'check_container_health() {{ return {healthy}; }}\n'
                          f'check_low_disk_only() {{ return {low_only}; }}\n' + gate)
                result = subprocess.run(["sh", "-c", script], capture_output=True, timeout=5)
                self.assertEqual(result.returncode, expected, result.stderr)

if __name__ == "__main__":
    unittest.main()
