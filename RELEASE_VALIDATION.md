# GPU Watch v2.5 release validation

2026-10-03 · GPT-6 Astra Ultra.

**PASS within the reviewed source, regression, and live-check scope.** This does not guarantee every future environment, device failure, shared-account identity, or browser policy. The public repository uses a fictional inventory and example deployment settings. Validate your own settings before deployment.

| Check | Result |
|---|---|
| Python, private and public source on Linux | 284 tests each; OK, 3 Windows-specific skips |
| Python, private and public source on Windows | 284 tests each; OK, 16 POSIX-specific skips |
| Frontend behavioral regressions | 25 checks, including 16 deferred-response success/failure scenarios |
| Shell and PowerShell | Shell syntax passed; PowerShell parser errors 0; restricted DACL behavior checked on Windows PowerShell 5.1 and PowerShell 7 |
| Calculations and data | Independent interval/daily-index oracle matched; SQLite quick_check OK, no foreign-key violations; production notice and timer hashes unchanged |
| Process ownership and privacy | GPU/PID-generation/UID validation, partial-cohort non-attribution, limited executable/script summary, no full argv/environment exposure |
| Responsive and interaction checks | 320–1440 CSS px samples, keyboard/drafts/tab/scroll/focus; 12 snapshot refreshes retained mobile Disk scroll |
| Text contrast samples | 1,242 solid-background samples met 4.5:1, minimum 5.20; not a complete WCAG certification |
| Production/emergency parity | Matching v2.5 and complete application fingerprint; real Windows emergency Start/Stop/UAC/firewall/temporary-secret cleanup passed, emergency copy OFF afterward |
| Images | Trivy 0.75.0, vulnerability DB updated 2026-10-02 12:48 UTC; application 0 findings, edge 1 non-applicable module advisory described below |
| Source boundaries | Actual key/password values absent from managed source and reachable Git history; public inventory/settings/history checked for private identifiers |

The edge scan reports [GO-2026-5932](https://pkg.go.dev/vuln/GO-2026-5932) for the `golang.org/x/crypto v0.56.0` module. All seven affected `openpgp` package paths are absent from the exact compiled dependency graph saved in the final image. The Docker build also rejects openpgp dependencies. The original finding is retained as non-applicable to the shipped binary, not described as a zero-finding edge scan.

Actual browser checks covered the retained interface before final deployment. The post-deployment browser provider was unavailable, so final delivery was checked through real HTTP APIs/assets and an exact match of the served JavaScript hash. New asynchronous draft protection was verified through behavioral regression scenarios. No real production notice or timer was saved or deleted for a test.

Linux integration tests require GNU `du`; the Alpine/BusyBox application runtime is not an equivalent development test environment. Real deployment health gates were exercised in the application image. Public source was tested with its own fictional inventory rather than replacing it with private configuration.

Utilization-zero resident processes count as occupancy/reservation, not compute throughput. GPU time is shared equally between distinct confirmed owners only when the complete cohort is attributable. A process that exits between polls cannot always be assigned safely; unresolved intervals remain unassigned. API quota headers and Retry-After control AA refresh timing, and its displayed version is the numeric API value rather than an inferred methodology patch version.

Short post-deployment resource samples were approximately 46–50 MiB for the application and 17–18 MiB for the edge, excluding a transient startup sample. These are measurements, not peak-load guarantees. No extra GPU metrics, frontend framework, resident GPU-server agent, or automated offsite backup was introduced.

Private fleet failures, limited user-directory permissions, the trusted-LAN HTTP boundary, and browser password-manager policies remain documented operational limits. Production addresses, credentials, database files, and screenshots are excluded from this repository. README screenshots retain fictional demo servers and documentation-range addresses.

## 2026-10-03 frontend maintenance on main

The original v2.5 tag and release remain unchanged. Main now includes the reviewed layout and README changes from PR #1, followed by this verification record. The CSS matches the private operating version exactly; no JavaScript, HTML, calculations, collection, inventory, or visible wording changed.

Review found that the enlarged mobile GB unit overlapped the top axis value. A small CSS correction moves the unit to the plot start while retaining its font size and vertical placement. Nine widths (320, 390, 460, 461, 600, 760, 768, 980, 1440 CSS px) passed overflow and unit/axis/candle/date collision checks. Long notice fixtures and editor Escape/PIN clearing were checked without production writes. After deployment, both operating lab views passed 390/768/1440 px checks; three real refresh responses preserved mobile Disk scroll, selection, and focus.

Both PR candidates passed 284 Python tests on Linux (3 platform skips) and 25 frontend checks. The final CSS correction passed another 25-check frontend run. The synchronized Windows operating copy passed 15 release contracts and 25 frontend checks. Backend and launch scripts were unchanged, so the original full Windows/emergency-start validation above retains its own date and scope; this maintenance verified matching source/fingerprint and the stopped emergency state.

The newly deployed application image had zero Trivy findings. The rebuilt edge retained the same explained GO-2026-5932 module finding, with all affected packages absent from its compiled dependencies. Production notice/timer hashes were preserved. The shared CSS SHA256 is `e1bc283f36887b97f15fc8f5eeb753d1efc75490018024b8d1a3584591e7bbed`.

Five README translations now distinguish the normal Docker probe default from the privileged helper's fixed Docker collection policy. The refreshed screenshot contains fictional inventory and documentation-range addresses, without text/EXIF metadata. The pre-existing middle-width daily-chart letterboxing and narrow event-table horizontal scrolling are retained.
