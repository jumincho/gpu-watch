# GPU Watch Dashboard v3

A lightweight, agentless dashboard for the GPU servers a research lab shares. It shows which GPUs are free right now, who is using the busy ones, and how much disk space is left. Data is collected over plain SSH.

**English** | [简体中文](README.zh-CN.md) | [繁體中文](README.zh-HK.md) | [日本語](README.ja.md) | [한국어](README.ko.md)

![GPU Watch dashboard showing lab summary, deadline timers, and per-server GPU cards](docs/images/dashboard.png)

<sub>Screenshot with fictional demo data. The interface is in Korean with English technical labels.</sub>

## Why GPU Watch

Before starting a job on a shared server, people usually need three answers: which GPU is free, who is using the busy ones, and whether there is enough disk space. GPU Watch answers all three on one page.

- **Agentless.** Each GPU server needs only SSH access, `nvidia-smi`, and `python3`. Nothing stays running on it.
- **Light.** The backend uses only the Python standard library and SQLite. The frontend is plain HTML, CSS, and JavaScript with no build step.
- **Careful.** It never guesses who owns a process and never shows full command lines. Stale readings are never presented as live.

## What's new in v3

- **Owner attribution** reads the effective UID from `/proc/<pid>/status`. Processes whose `/proc` directory looks root-owned (non-dumpable processes) are still attributed to the right user.
- **Disk keeps refreshing** when SSH works but the GPU probe fails, for example after an NVIDIA driver mismatch.
- **An optional privileged disk helper** measures per-user usage on servers where the monitoring account cannot read other users' home directories. See [Privileged disk helper](#privileged-disk-helper).
- **Artificial Analysis refreshes follow the API quota.** The interval comes from the response's rate-limit headers instead of a fixed six hours.
- **Hardening.** Creating a notice now shares the password-hashing concurrency limit, and the Caddy build pins patched OpenTelemetry modules.
- **LF line endings everywhere**, so the release fingerprint of a Linux deployment and a Windows copy match.
- **Interface polish.** Timer countdowns sit on the vertical centre of their cards. Long titles get priority over a single-line countdown; titles that still overflow use an ellipsis, with the full title available on hover. On phones the Intelligence Index is more compact, and Korean text wraps between words.

## Features

### GPUs and servers

- Live status for every GPU: free or busy, utilization, VRAM, temperature, and how long it has been busy or when it was last used.
- Processes on each GPU with their users, memory, and a short command summary. The details view adds the PID, start time, and container, but never the full command line.
- A lab switcher, a lab-wide summary (servers online, free and busy GPUs, VRAM), and filters for busy servers, fully free servers, connection failures, and disk warnings.
- Activity badges: 🔥 high usage (seven-day busy index of 50% or more), ❄️ long idle (no continuous use of 60 seconds or more for seven days), and ⛔ connection failure.

### Disk

- Free, usable, used, and reserved space per filesystem, with a warning at 90% usage.
- Per-user usage from readable home directories and configured paths, plus Docker writable layers. When a result is partial, lower bounds are marked `≥` and estimates `≈`.

### History and trends

- A Recent Activity log of busy/free transitions, filterable by date, server, and user. Connection DOWN/UP events are hidden by default and can be included.
- A seven-day busy index and a per-user usage share for each server.
- LAB DAILY INDEX: hourly VRAM candles for the last 24 hours and a 30-day trend of daily average VRAM.

### Lab tools

- Up to six conference deadline countdowns in KST, including TBA entries. Flag emoji in timer titles are drawn from bundled SVG files.
- A bulletin board for notices with an optional expiry time. Authors edit their notices with their own passphrase, and admins can manage every notice with the admin PIN.
- Quick links to deadline trackers, AI service status pages, and AI news. It also shows the Artificial Analysis Intelligence Index (top 29 models), which the server fetches so the API key never reaches the browser.

### Everyday use

- Automatic refresh every 10 seconds by default. Refreshing keeps the selected tabs, scroll position, and keyboard focus, and it slows down in background tabs. A banner appears when data stops updating.
- Works on screens as narrow as 320 px, supports keyboard navigation, and respects reduced-motion settings. Text contrast, focus visibility, and responsive layout are checked during release review.

## How it works

```text
Browser ──HTTP──▶ Caddy  (IP allowlist, compression)
                    │
                    ▼
              server.py   (HTTP API · collector · maintenance)
               │      │
          SSH  │      └──▶ SQLite  (data/)
               ▼
          GPU servers  (nvidia-smi · /proc · df · du · docker)
```

1. Every `poll_interval_seconds` (10 s), the collector connects to all hosts in `hosts.json` in parallel. It runs `nvidia-smi` queries and a small inline Python probe on each one.
2. Each process owner comes from the effective UID in `/proc/<pid>/status` and its NSS name, checked against the PID start time and the GPU UUID. If the owner cannot be confirmed, it is shown as unknown rather than guessed.
3. Every `disk_poll_interval_seconds` (30 min), disk usage is measured with `df`, time-limited `du`, and `docker ps --size`, or through the optional privileged helper.
4. Observations are stored in SQLite as time intervals. Overlapping GPUs, processes, and users are merged, so busy time is never counted twice.
5. The page reads `/api/snapshot`, `/api/events`, `/api/insights`, and `/api/intelligence-index`.

### Status rules

- A GPU is **busy** if it has a compute process, uses at least 500 MiB of VRAM, or is at least 10% utilized. Both thresholds are configurable.
- A process that keeps VRAM allocated counts as busy even at 0% utilization, so reserved GPUs are not shown as free.
- A server is **DOWN** when a complete GPU observation fails, whether SSH or the GPU probe failed. None of its GPUs count as available, and earlier readings are not shown as current. A successful disk probe does not bring a DOWN server back UP.

## Security model

- **Access control.** Caddy enforces an IP allowlist. The app checks the client IP, `Host`, and `Origin` again on its own.
- **Writes.** Changes to notices and timers require a same-origin request and a passphrase or PIN. Hashes use PBKDF2-SHA256 with 600,000 iterations. At most two hash checks run at once, and failed attempts are rate-limited per subnet and globally.
- **Limited process details.** Full command lines, environment variables, and credentials are not sent to the browser. Process details use a short sanitized summary. Operational errors are bounded and redacted, but can contain host addresses or ports; they are not a promise of hiding network topology. Server cards intentionally show a validated IP address.
- **Browser hardening.** A strict Content Security Policy blocks inline scripts. Icons and flags are served locally, not from third-party hosts.
- **Containers.** They run as a non-root user with a read-only root filesystem, all capabilities dropped, `no-new-privileges`, and PID, memory, and log limits.
- **Secrets.** SSH passwords, API keys, and the PIN hash live in git-ignored runtime folders (`secrets/`, `data/`, `operator-secrets/`), never in the repository. SSH passwords go to OpenSSH through `SSH_ASKPASS`, not on the command line.
- **Transport.** Plain HTTP is intended for a trusted LAN only. Add TLS and authentication before exposing the dashboard more widely.

## Requirements

| Where | What you need |
|---|---|
| Dashboard host | Python 3.12 (standard library only) and the OpenSSH client, or Docker |
| Each GPU server | SSH access for a monitoring account, the NVIDIA driver with `nvidia-smi`, `python3`, `df`, and GNU `du`. Docker is optional and adds per-container usage. The privileged disk helper additionally needs `sudo`. |
| Viewers | A current web browser |

## Quick start

```sh
git clone https://github.com/jumincho/gpu-watch.git
cd gpu-watch

# 1. Describe your servers (see Configuration below).
$EDITOR hosts.json

# 2. Create the admin PIN. The script prints where the one-time plaintext PIN was saved.
python3 scripts/admin-passphrase.py ensure

# 3. Start the dashboard.
python3 server.py --host 127.0.0.1 --port 8787
```

Open <http://127.0.0.1:8787/>. By default, only loopback clients are allowed. To serve other machines on your LAN, list them explicitly. The addresses below are examples.

```sh
GPU_WATCH_ALLOWED_NETWORKS="127.0.0.0/8,::1/128,192.0.2.0/24" \
GPU_WATCH_ALLOWED_HOSTS="192.0.2.10:8787,127.0.0.1:8787,localhost:8787" \
python3 server.py --host 0.0.0.0 --port 8787
```

## Configuration

### `hosts.json`

The bundled `hosts.json` describes a fictional lab. Replace it with your own servers.

```json
{
  "poll_interval_seconds": 10,
  "disk_poll_interval_seconds": 1800,
  "busy_memory_threshold_mib": 500,
  "busy_utilization_threshold_percent": 10,
  "labs": [{ "id": "vision", "label": "VISION LAB" }],
  "hosts": [
    {
      "name": "atlas",
      "label": "atlas",
      "lab": "vision",
      "ssh_host": "192.0.2.11",
      "ssh_port": 22,
      "ssh_user": "gpuwatch",
      "ssh_identity_file": "~/.ssh/id_ed25519",
      "expected_gpu_count": 4,
      "note": "NVIDIA GeForce RTX 4090 x4",
      "owner": "Vision",
      "owner_type": "assigned",
      "location": "Room 301"
    }
  ]
}
```

| Host field | Meaning |
|---|---|
| `name` | Unique ID (letters, digits, `.`, `_`, `-`). It is also used as the SSH alias when `ssh_host` is omitted. |
| `label`, `lab` | Display name and the lab it belongs to |
| `ssh_host`, `ssh_port`, `ssh_user` | Connection target |
| `ssh_identity_file` | Private key for key-based login |
| `ssh_password_file`, `ssh_options` | Password-based login. The password file sits under `secrets/` and is read at runtime. |
| `display_ip` | The IP shown on the card for alias-based hosts |
| `expected_gpu_count` | Number of GPU slots to keep showing while the server is down |
| `note`, `owner`, `owner_type`, `location` | Card text and badge style (`assigned` or `shared`) |
| `disk_user_paths` | Extra per-user paths to measure, as `{ "user": …, "path": … }` |
| `collect_docker_usage` | Also measure Docker writable layers with `docker ps --size`. On by default only for hosts in the `nll` lab. |
| `privileged_disk_helper` | Measure disk usage through the installed root helper. Cannot be combined with `disk_user_paths`. |

Other top-level settings include probe timeouts, `collector_workers`, the `activity_policy` thresholds for the 🔥 and ❄️ badges, and retention periods. Events are kept for 180 days and daily backups for 14 days by default. Notices are purged 90 days after deletion or expiry; notices without an expiry remain until deleted.

### Environment variables

| Variable | Purpose | Default |
|---|---|---|
| `GPU_WATCH_ALLOWED_NETWORKS` | Client IP allowlist (comma-separated CIDR ranges) | loopback |
| `GPU_WATCH_ALLOWED_HOSTS` | Accepted `Host` header values | loopback |
| `GPU_WATCH_TRUSTED_PROXY_NETWORKS` | Reverse proxies whose `X-Forwarded-For` header is trusted | loopback |
| `GPU_WATCH_SSH_CONFIG_FILE` | Absolute path to an OpenSSH config file to use | none |
| `GPU_WATCH_SSH_IDENTITY_FILE` | Overrides the per-host identity file | none |
| `GPU_WATCH_ADMIN_PIN_HASH` | Admin PIN hash, used instead of `data/admin_pin.hash` | none |
| `GPU_WATCH_RUNTIME_MODE` | `standalone`, `production`, or `emergency` | `standalone` |
| `GPU_WATCH_BUILD_VERSION` | Release number shown in the footer | `VERSION` |

### Artificial Analysis

To show the Intelligence Index, put the API key in `secrets/artificial_analysis_api_key`. It must be a regular file, not a symlink, with mode `0600`. The browser receives only the cached rankings.

- The server spreads complete refreshes across the quota window using the response's `X-RateLimit-*` headers and the number of pages. It keeps a few requests in reserve (up to 8) and never refreshes more often than every 15 minutes. With a quota of 100 requests a day and four pages, that is about once an hour.
- When the quota runs low, it waits until the quota resets. Each request is counted before it is sent, so a restart or a network error never makes it think it has requests left.
- Without rate-limit headers it refreshes every six hours. After a failure it retries in one hour, or later if the API sends a longer `Retry-After`. Data older than 48 hours is marked stale.
- Quota state (limit, remaining, reset time, pages, next attempt) is saved next to the cache in `data/`, separately from the key.
- The page checks the server cache every five minutes. If nobody has the page open, a refresh can wait until the next maintenance cycle (hourly by default).

## Privileged disk helper

On some servers the monitoring account cannot read other users' home directories, so per-user totals stay partial. For those servers you can install a small root helper that runs only GPU Watch's own disk collector.

```sh
# Generate a reviewable installer (this does not install anything).
python3 scripts/provision-disk-helper.py --user gpuwatch --output disk-installer.py

# Review disk-installer.py, copy it to the GPU server, and run it there as an administrator.
sudo python3 -I disk-installer.py
```

The installer adds `/usr/local/libexec/gpu-watch-disk`, a sudoers rule that lets the monitoring account run exactly that helper with no arguments, and a cache folder in `/var/cache/gpu-watch/`. The helper accepts no paths, commands, or environment. It prevents concurrent scans, caches results for five minutes, and runs at low CPU priority. No daemon is installed, and no administrator password is stored.

The example configuration leaves the helper disabled: an omitted or false `privileged_disk_helper` does not use sudo. After installing, set `"privileged_disk_helper": true` only for that host. If the helper is missing, GPU Watch falls back to an unprivileged scan within the same time budget and marks per-user usage as partial.

## Deployment

The reference production setup runs two hardened containers. Caddy is the only published port; the app stays on an internal Docker network.

- `Dockerfile` builds the app on a digest-pinned `python:3.12-alpine` image.
- `Dockerfile.caddy` builds Caddy from a pinned commit and pinned dependency versions.
- `deploy.sh` is the deployment script for the lab's production host. It checks paths and permissions, builds both images, and validates SSH and Caddy configuration. Immediately before switching, it stops the app and creates and validates an online SQLite backup. It keeps the previous containers, checks the new deployment's health and access controls, and rolls back on failure. To reuse it elsewhere, change the host-specific paths and addresses first.

The release fingerprint hashes `VERSION`, `server.py`, `hosts.json`, `gpu_watch/`, and `static/`. Production and the emergency copy must match it.

### Windows emergency fallback

`emergency-local-fallback.ps1 -Action Status|Start|Stop` manages the Windows emergency copy. Adapt its reference paths, production address, SSH configuration, and LAN ranges to your environment first. `Start` refuses while production is reachable unless you pass `-Force`. It verifies `VERSION`, the release fingerprint, and a fresh collection cycle, and limits the firewall rule to the configured LAN. `Stop` removes its listener, firewall rule, and temporary password files. The local database is separate, so reconcile notices and timers created during an outage before switching back.

## Operations

```sh
# Health of a running container
docker exec gpu-watch-dashboard python3 /app/scripts/check_local.py --health-only --expected-build-version 3

# SQLite integrity and aggregation invariants
python3 scripts/audit-data.py data/gpu_watch.sqlite3

# Restore a backup inside data/backups (path, SQLite integrity, foreign keys, schema)
sh scripts/restore-backup.sh "$PWD/data/backups/backup.sqlite3"
```

The maintenance worker makes daily SQLite backups. `deploy.sh` adds a backup before each deployment. There is no automatic off-site copy; `scripts/offsite-backup.sh` is a manual tool.

## Development and tests

```sh
python3 -m unittest discover -s tests -v   # Python regression and contract tests
node tests/test_frontend.js                # frontend logic contracts
```

The tests also lock product behavior, such as status rules, interval math, security headers, and UI wording. A failing contract test usually means a user-visible change that needs a deliberate decision.

## Project layout

| Path | Purpose |
|---|---|
| `server.py` | HTTP API, collector, storage, and maintenance |
| `gpu_watch/` | Authentication, process attribution, security helpers, history, and the Artificial Analysis client with quota tracking |
| `static/` | Frontend (`index.html`, `app.js`, `styles.css`) plus bundled icons and flags |
| `hosts.json` | Servers, labs, and collection settings (fictional example) |
| `Caddyfile`, `Dockerfile`, `Dockerfile.caddy` | Edge proxy and container images |
| `deploy.sh` | Production deployment with backup and rollback |
| `run-dashboard.ps1`, `emergency-local-fallback.ps1` | Windows emergency collector |
| `scripts/` | Health checks, data audit, backup and restore, admin PIN, SSH helpers, and the disk helper installer generator |
| `tests/` | Python and Node test suites |

## Release

v3, released on 2026-09-25. The interface footer credits `GPT-6 Astra Max (Implementation) · Claude Opus 5.5 Max (Frontend)`.

## Acknowledgements

Flag icons come from [Twemoji](https://github.com/jdecked/twemoji) under CC-BY 4.0; see [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md). Service icons belong to their respective owners and are used only to label links to their status pages.

## License

GPU Watch is released under the [MIT License](LICENSE). The license does not cover the bundled flag and service icons; see Acknowledgements above.
