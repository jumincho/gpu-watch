# GPU Watch v2 release validation

2026-09-30 · GPT-6.1 Sol (max).

The v2 application and its operational helpers passed the checks below. This is evidence for the reviewed release, not a guarantee against every future environment, hardware failure, or browser policy. The public repository contains a fictional inventory and example deployment settings; adapt and validate those settings before deployment.

| Check | Result |
|---|---|
| Python suite, private and public source on Linux | 270 tests each; OK, 2 Windows-specific tests skipped |
| Python suite, private and public source on Windows | 270 tests each; OK, 15 POSIX/shell-specific tests skipped |
| Frontend behavioral regressions | 21 checks; OK on Node on Linux and Windows |
| Shell and PowerShell syntax | Shell scripts parsed; PowerShell parser reported no errors |
| Interval calculations | Independent SQLite audit: no interval/daily-index failures, quick_check OK, no foreign-key violations |
| Remote compatibility | Both probes parse as Python 3.6; exercised on older remote Python installations |
| Process privacy and ownership | PID-generation/GPU revalidation; unknown-owner occupancy retained; partial cohorts not over-attributed; raw argv/secrets absent |
| Responsive layout | 320, 390, 768, 1440 CSS px; no page overflow or clipped hostname/IP lines |
| Accessibility spot checks | Rendered text contrast met small-text 4.5:1 / large-text 3:1 thresholds; keyboard focus visible; not a complete WCAG certification |
| Forms and state | Notice/timer/delete dialogs retain drafts on outside click, TBA/flags/date limits work, tab/scroll/focus survives refresh |
| Production/emergency parity | Version and source fingerprint agree; emergency LAN Start/Stop/firewall/secret cleanup exercised; emergency copy OFF afterward |
| Published image scan | Trivy 0.74.0 with 2026-09-30 vulnerability DB; application: no findings; edge: one explained UNKNOWN-severity module advisory |
| Secret boundary | Actual API key/password byte values absent from managed source and reachable Git history; public inventory/settings sanitized |

The edge scanner reports [GO-2026-5932](https://vuln.go.dev/ID/GO-2026-5932.json) for `golang.org/x/crypto v0.56.0`. The advisory concerns the `openpgp` packages. The build rejects these packages, and the exact compiled package graph shipped in `/usr/local/share/gpu-watch/caddy-packages.txt` contains none of them. This finding is recorded as non-applicable to the built edge binary rather than hidden or described as a zero-finding scan.

Linux integration tests require GNU `du`; running them inside the minimal Alpine/BusyBox runtime is not an equivalent development environment. The deployment health checks are exercised in the actual application image.

The system treats utilization-zero resident processes as occupancy. User time is shared equally between distinct confirmed owners only when the complete process cohort is attributable. A process that disappears between observations cannot always be assigned safely; such time remains unassigned rather than invented. AA refresh timing follows its response quota/reset and Retry-After, and the displayed index version is the numeric version actually returned by its API.

Live checks used the established trusted-LAN HTTP boundary and did not publish production inventory, credentials, database files, or screenshots to this repository. Unreachable servers and GPU device faults remain external operational conditions. No automatic offsite backup or extra GPU metrics were added.
