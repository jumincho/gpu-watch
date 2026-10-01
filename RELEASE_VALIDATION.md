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

Live checks used the established trusted-LAN HTTP boundary and did not publish production inventory, credentials, database files, or production screenshots to this repository. The README screenshot uses fictional demo servers, users, and documentation-range IP addresses. Unreachable servers and GPU device faults remain external operational conditions. No automatic offsite backup or extra GPU metrics were added.

The same-day maintenance changes on `main` add bottom clearance for the back-to-top button and separate the legend and filters at medium widths. Both lab views were checked at 320, 390, 760, 761, 768, 1100, 1299, 1300, and 1440 CSS px: no horizontal page overflow, clipped hostname/IP, or wrapped normal seen line; timer countdowns remain centered. AA link bounds remain clear of the fixed button, with actual bottom-scroll checks at 320, 761, and 1440 px. Release contracts passed 15 tests on both Windows editions and the Linux operating source; frontend regressions passed 21 checks on both Windows editions and Linux. Rebuilt images were scanned against the newer 2026-09-30 vulnerability database, with no application findings and the same explained edge advisory. Production notices and timer settings were preserved, and the emergency copy remains OFF with matching version and source fingerprint.

The annotated `v2` tag points to [`9ad9d18`](https://github.com/jumincho/gpu-watch/commit/9ad9d188ab83c0f52a00d3d1e3fb4bf083d02fab), which has exactly the original release tree with a corrected noreply author identity. The subsequent CSS, demo screenshot, and README updates are on `main`. The tag also uses the noreply identity; the original release date and implementation credit are preserved.

The 2026-10-01 badge palette maintenance on `main` lets an app-serving tag share the physical-AI badge colors while retaining its own label and neutral tooltip. Frontend regressions passed 21 checks and release contracts passed 15 tests in both Windows editions; the Linux operating source passed 15 release contracts. Live browser computed styles confirmed identical text, background, border and alpha values. Configured server order, notices, timers and the OFF emergency copy were preserved. These are scoped maintenance checks; the full 270-test suite and image scan above retain their original verification dates. The formal v2 tag, release date and implementation credit remain unchanged.
