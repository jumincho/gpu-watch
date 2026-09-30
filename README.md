# GPU Watch Dashboard v2

A lightweight, agentless dashboard for shared NVIDIA GPU servers. Check GPU occupancy, process owners, recent activity, storage, and lab VRAM trends from one page. Collection uses SSH; the backend uses the Python standard library and SQLite, and the frontend uses HTML, CSS, and JavaScript without a build step.

**Released 2026-09-30 · GPT-6.1 Sol (max).** Release details belong in the repository and API metadata; the dashboard has no release footer.

**English** | [简体中文](README.zh-CN.md) | [繁體中文](README.zh-HK.md) | [日本語](README.ja.md) | [한국어](README.ko.md)

This public edition contains fictional server names, documentation-range addresses, and example SSH paths. Replace them with your own configuration before deployment. Runtime credentials, databases, and internal operational documents are excluded.

## What it provides

- GPU free/busy state, utilization, VRAM, temperature, recent use, and verified process summaries.
- Multiple processes and users per GPU, lab summaries, and busy/free/DOWN/disk-warning filters.
- Disk capacity and per-user usage, including optional Docker accounting and a restricted privileged disk helper.
- Seven-day occupancy, long-idle/high-usage badges, hourly VRAM candles, and LAB DAILY INDEX.
- Recent Activity with date, server, user, and pagination controls. DOWN/UP events are optional and hidden by default; observation gaps and owner changes remain internal data.
- Six conference timers with stable colors, deadline order, registration-order ties, TBA, and local flag SVGs.
- Editable notices with optional expiry, including notices without an expiry date.
- Artificial Analysis Intelligence Index: server-side caching, top 29 models, concise displayed model names, and quota-aware refresh scheduling.
- Automatic refresh with selected tabs, scroll position, and keyboard focus preserved. Forms stay open on backdrop clicks; closing a form clears its password.

## Collection and calculation contracts

GPU collection defaults to **10 seconds**; disk collection defaults to **30 minutes**, with separate workers and time budgets. GPU servers need SSH, `python3`, `nvidia-smi`, `df`, and GNU `du`; Docker is optional. No collection daemon runs on a GPU server. The remote probe supports older Python installations and does not require a persistent home directory.

A GPU is busy when a compute process exists, VRAM reaches 500 MiB, or utilization reaches 10%. A resident process at 0% utilization still reserves the GPU and counts as occupancy. These metrics describe occupancy, not compute efficiency.

Owners are verified using effective UID, NSS or a numeric UID fallback, PID start ticks, GPU UUID, and a repeated GPU process observation. A root-owned `/proc` directory alone does not establish root ownership. A verified process remains visible when ownership is temporarily unavailable. Previously confirmed owners carry over only for the same verified PID generation. When every owner is known, time is shared once among distinct users; an interval containing an unresolved owner remains unassigned rather than being overcharged to known users.

Recent use records all observed sessions. Sessions shorter than 60 seconds remain in history and occupancy totals but do not clear the long-idle badge. Long idle needs seven days of tracking and no meaningful session; it does not require 100% observation coverage. The seven-day index uses observed GPU-slot time as its denominator. Missing time is not filled with zero use, and rounded 100% coverage displays `7일 / 7일`.

LAB DAILY INDEX sums each GPU's observation-weighted mean VRAM for the KST calendar day. Observed wall time is the union of intervals, and overlapping GPU/user intervals are not counted twice. Raw VRAM is quantized to 16 MiB; raw intervals last eight days and daily aggregates last 180 days by default.

DOWN means a complete GPU observation failed. Diagnostics distinguish SSH/timeout failures from driver or device faults; earlier readings are not presented as current. Disk collection continues independently and cannot bring a GPU-unobservable host UP. Driver mismatch fallback is a guarded, explicitly configured temporary option; the normal path uses the native driver.

## Disk accounting

The warning badge and Disk tab use the same aggregate: **sum(used) / (sum(used) + sum(available))**. Reserved blocks are outside that denominator. Filesystem aliases are counted once, and the warning begins at a displayed 90%.

Per-user totals combine readable home/configured paths and Docker writable layers. They do not replace filesystem totals. Images, volumes, shared blocks, and system/other data are not arbitrarily assigned to a person. Partial lower bounds use `≥`, estimates use `≈`, and Docker failures show a readable message instead of an internal command.

Where the monitoring account cannot read other users' directories, generate and review the optional helper installer:

```sh
python3 scripts/provision-disk-helper.py --user gpuwatch --output disk-installer.py
# Review and transfer the installer, then run it as administrator on that GPU server.
sudo python3 -I disk-installer.py
```

The helper is root-owned, accepts no arguments, uses a fixed environment and low CPU priority, serializes scans, and caches for five minutes. Its sudoers rule permits only that fixed command with no arguments. No administrator password or daemon is stored. Enable `privileged_disk_helper` only after installation; it cannot be combined with `disk_user_paths`. Reinstall the helper after changing the disk probe.

## Quick start

Use Python **3.12+** and OpenSSH, or Docker. The server code uses only standard-library modules.

```sh
git clone https://github.com/jumincho/gpu-watch.git
cd gpu-watch

# Adapt the fictional inventory and verify SSH keys/known_hosts first.
$EDITOR hosts.json
python3 scripts/admin-passphrase.py ensure
python3 server.py --host 127.0.0.1 --port 8787
```

Open <http://127.0.0.1:8787/>. The admin helper reports the one-time PIN file; store that PIN securely and remove the plaintext file. Password-based hosts use a restricted file under `secrets/` through SSH_ASKPASS, never a command-line password.

To serve a specific trusted LAN, adapt these example addresses:

```sh
GPU_WATCH_ALLOWED_NETWORKS="127.0.0.0/8,::1/128,192.0.2.0/24" \
GPU_WATCH_ALLOWED_HOSTS="192.0.2.10:8787,127.0.0.1:8787,localhost:8787" \
python3 server.py --host 0.0.0.0 --port 8787
```

## Configuration

`hosts.json` defines labs, collection intervals/timeouts, retention, activity thresholds, and hosts. Each host has a unique `name`, `lab`, `expected_gpu_count`, and either a verified SSH alias or `ssh_host`/`ssh_port`/`ssh_user`. Optional fields include `label`, `display_ip`, `note`, `owner`, `owner_type`, `location`, `ssh_identity_file`, `ssh_password_file`, `ssh_options`, `disk_user_paths`, `collect_docker_usage`, and `privileged_disk_helper`. Display IPs must be valid IP addresses.

| Environment variable | Purpose |
|---|---|
| `GPU_WATCH_ALLOWED_NETWORKS` | Explicit client IP allowlist; loopback by default |
| `GPU_WATCH_ALLOWED_HOSTS` | Accepted Host header values |
| `GPU_WATCH_TRUSTED_PROXY_NETWORKS` | Only these peers may supply a trusted forwarding chain |
| `GPU_WATCH_SSH_CONFIG_FILE`, `GPU_WATCH_SSH_IDENTITY_FILE` | Reviewed SSH configuration and identity paths |
| `GPU_WATCH_ADMIN_PIN_HASH` | Optional admin PIN hash override |
| `GPU_WATCH_RUNTIME_MODE` | `standalone`, `production`, or `emergency` |
| `GPU_WATCH_BUILD_VERSION` | Must agree with the checked-in VERSION |

For the Intelligence Index, put the API key in `secrets/artificial_analysis_api_key`, owned by the app user with mode `0600` and a restricted parent directory. The browser sees cached rankings only. Redirects are refused, so the key stays on the configured API origin.

The AA client spreads full-page refreshes across the API's `X-RateLimit-*` window, reserves up to eight requests, and persists reservations before requests. At 100 requests per day and four pages, updates are roughly hourly; the minimum interval is 15 minutes. A small scheduler keeps appointments even without viewers. Missing headers use a conservative six-hour fallback, failures retry after one hour or a longer Retry-After, and data older than 48 hours is stale. The page checks its own server cache every five minutes. Index versions are displayed as supplied by the API, without inventing a patch version.

## Security and deployment

Caddy and the app both enforce IP/Host policy. Writes require a same-origin request and a notice passphrase or admin PIN. PBKDF2-SHA256 uses 600,000 iterations, password checks have a two-worker concurrency cap, and failures are limited per subnet and globally. Full process command lines, environment variables, and option values are never exposed. Static files are explicitly allowlisted, symlinks and traversal are refused, and a strict CSP prevents external frontend scripts. Password-manager ignore hints reduce save prompts; browser policy can still override page hints.

The reference deployment uses plain HTTP on a trusted LAN. IP filtering is not transport encryption; use an appropriate encrypted/authenticated deployment before expanding the trust boundary. The AI Deadlines link intentionally uses its working HTTP URL.

`Dockerfile` and `Dockerfile.caddy` pin base digests and dependencies. Containers run as a non-root user with a read-only root filesystem, dropped capabilities, `no-new-privileges`, and PID/memory/log limits. Caddy alone publishes a port; the app stays on an internal network.

Adapt `deploy.sh`, `Caddyfile`, SSH paths, and LAN addresses before use. The deployment script validates paths, takes a verified SQLite backup before mutation, keeps previous containers through health/access checks, and rolls back containers, SSH runtime files, and the database on failure. It cleans only this project's labelled unused build images.

## Emergency copy and operations

The Windows helper is a reference for a manually started emergency copy. Adapt its paths, addresses, and SSH configuration first. Production and emergency source must share VERSION and the release fingerprint, which hashes `VERSION`, `server.py`, `hosts.json`, `gpu_watch/`, and `static/`. Also compare a complete file manifest because scripts/docs/tests sit outside that fingerprint.

```powershell
.\emergency-local-fallback.ps1 -Action Status
.\emergency-local-fallback.ps1 -Action Start
.\emergency-local-fallback.ps1 -Action Stop
```

Start refuses a reachable production instance unless `-Force` requests a deliberate takeover; Force still cannot bypass a version/fingerprint mismatch. The app stays a standard-user process; only the restricted LAN firewall operation requires Windows UAC. A fresh collection cycle must pass before startup succeeds. Stop checks the exact Python/script/port identity, removes its firewall rule and temporary SSH secret, and leaves unrelated processes alone. The emergency copy is normally OFF. Reconcile notices/timers created during an outage before returning to production.

```sh
docker exec gpu-watch-dashboard python3 /app/scripts/check_local.py --health-only --expected-build-version 2
python3 scripts/audit-data.py data/gpu_watch.sqlite3
sh scripts/restore-backup.sh "$PWD/data/backups/backup.sqlite3"
python3 -m unittest discover -s tests -v
node tests/test_frontend.js
```

Daily SQLite backups remain on the deployment host for 14 days by default; pre-deployment backups remain for 30 days. Events and daily indices last 180 days. Notices without an expiry remain until deleted. There is no automatic offsite backup; the supplied offsite script is a manual tool.

Run the complete development suite on Windows or a Linux host with GNU coreutils (`du` and its exclusion options). The minimal Alpine application image uses BusyBox and is not the environment for GNU `du` integration fixtures. See [v2 release validation](RELEASE_VALIDATION.md) for the checked environments and scope.

## Source layout and maintenance

| Path | Responsibility |
|---|---|
| `server.py` | Probe, Store, Collector, API, lifecycle |
| `gpu_watch/` | Authentication, security, ownership/history, maintenance, AA/quota |
| `static/` | HTML/CSS/JS, bundled service icons and flag SVGs |
| `hosts.json` | Fictional inventory and validated collection policy |
| `deploy.sh`, Dockerfiles, `Caddyfile` | Deployment, edge, isolation |
| Windows helpers and `scripts/` | Emergency operation, SSH, backups, health, disk helper |
| `tests/` | Behavioral regressions, independent interval oracles, API/security/UI contracts |

Keep migrations, meaningful regressions, verified backups, and timer colors. Avoid rewriting the monolithic Store/probe while changing UI: use focused modules and preserve the explicit calculation contracts. Release validation establishes tested behavior at a stated source version, not a guarantee against every future environment or failure.

## License and attribution

[MIT License](LICENSE). Bundled flags derive from [Twemoji](https://github.com/jdecked/twemoji) under CC-BY 4.0; service icons belong to their respective owners. See [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
