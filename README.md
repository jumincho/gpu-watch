# GPU Watch Dashboard v2.5

A lightweight, agentless dashboard for the GPU servers a research lab shares. It shows which GPUs are free right now, who is using the busy ones, and how much disk space is left. Data is collected over plain SSH.

**v2.5** · released 2026-10-03 · GPT-6 Astra Ultra · [release validation](RELEASE_VALIDATION.md)

**English** | [简体中文](README.zh-CN.md) | [繁體中文](README.zh-HK.md) | [日本語](README.ja.md) | [한국어](README.ko.md)

![GPU Watch dashboard showing lab summary, deadline timers, and per-server GPU cards](docs/images/dashboard.png)

<sub>Screenshot with fictional demo data. The interface is in Korean with English technical labels.</sub>

> [!NOTE]
> This public edition ships a fictional inventory: made-up server names, documentation-range IP addresses, and example SSH paths. Replace them with your own configuration before deploying. Credentials, databases, and internal operating documents are not part of the repository.

## Why GPU Watch

Before starting a job on a shared server, people usually need three answers: which GPU is free, who is using the busy ones, and whether there is enough disk space. GPU Watch answers all three on one page.

- **Agentless.** Each GPU server needs only SSH access, `nvidia-smi`, and `python3`. Nothing stays running on it.
- **Light.** The backend uses only the Python standard library and SQLite. The frontend is plain HTML, CSS, and JavaScript with no build step.
- **Careful.** It never guesses who owns a process and never shows full command lines. Stale readings are never presented as live.

## What's new in v2.5

- **GPU readings are validated before publication.** Missing or invalid mandatory VRAM readings cannot make a GPU appear free or count as observed time. Optional utilization and temperature readings may still be unavailable.
- **Hourly history follows the active GPU inventory.** Retired GPUs no longer contribute old VRAM readings to the hourly Lab Pulse chart.
- **HTTP and diagnostic boundaries are stricter.** Duplicate `Host` headers and truncated JSON request bodies are rejected. Maintenance health reports expose the exception type instead of potentially sensitive exception text.
- **New drafts survive older requests.** A delayed notice or timer save/delete response refreshes committed data without closing, overwriting, refocusing, or unlocking a newer editor. Timer PINs are cleared before the dialog closes.
- **Windows emergency files have explicit private permissions.** Startup replaces broad inherited or explicit runtime ACL grants with access for the current user, SYSTEM, and Administrators, and uses the SSH identity that was actually validated.
- **Existing administrator PINs are preserved.** `ensure` retains a valid older hash and upgrades it after successful authentication. An invalid stored hash requires an explicit repair; startup does not silently replace the PIN.

The existing interface, wording, collection scope, and lightweight architecture are retained. [Release validation](RELEASE_VALIDATION.md) records the tested scope and its limits; it is not a guarantee against every future environment or failure.

## Features

### GPUs and servers

- Live status for every GPU: free or busy, utilization, VRAM, temperature, and how long it has been busy or when it was last used.
- Processes on each GPU with their users, memory, and a short command summary. The details view adds the PID, start time, and container, but never the full command line.
- A lab switcher, a lab-wide summary (servers online, free and busy GPUs, VRAM), and filters for busy servers, fully free servers, connection failures, and disk warnings.
- Activity badges: 🔥 high usage (seven-day busy index of 50% or more), ❄️ long idle (no continuous use of 60 seconds or more for seven days), and ⛔ connection failure.

### Disk

- Free, usable, used, and reserved space per filesystem. A server gets a Disk warning when the combined usage of its filesystems reaches 90%.
- Per-user usage from readable home directories and configured paths, plus Docker writable layers. When a result is partial, lower bounds are marked `≥` and estimates `≈`.

### History and trends

- A Recent Activity log of busy/free transitions, filterable by date, server, and user. Connection DOWN/UP events are hidden by default and can be included.
- A seven-day busy index and a per-user usage share for each server.
- LAB DAILY INDEX: hourly VRAM candles for the last 24 hours and a 30-day trend of daily average VRAM.

### Lab tools

- Up to six conference deadline countdowns in KST, including TBA entries. Each timer keeps its color, and flag emoji in titles are drawn from bundled SVG files.
- A bulletin board for notices with an optional expiry time. Authors edit their notices with their own passphrase, and admins can manage every notice with the admin PIN.
- Quick links to deadline trackers, AI service status pages, and AI news. It also shows the Artificial Analysis Intelligence Index (top 29 models), which the server fetches so the API key never reaches the browser.

### Everyday use

- Automatic refresh every 10 seconds by default. Refreshing keeps the selected tabs, scroll position, and keyboard focus, and it slows down in background tabs. A banner appears when data stops updating.
- Forms stay open when you click outside them, and closing a form clears its password.
- Reviewed layouts work down to 320 px and respect reduced-motion settings. Keyboard navigation and sampled text contrast were checked against the relevant WCAG AA thresholds; this is not a complete accessibility certification.

## How it works

```text
Browser ──HTTP──▶ Caddy  (IP allowlist, compression)
                    │
                    ▼
              server.py   (HTTP API · collector · maintenance · index scheduler)
               │      │
          SSH  │      └──▶ SQLite  (data/)
               ▼
          GPU servers  (nvidia-smi · /proc · df · du · docker)
```

1. Every `poll_interval_seconds` (10 s), the collector connects to all hosts in `hosts.json` in parallel. It runs `nvidia-smi` queries and a small inline Python probe on each one.
2. Each process owner comes from the effective UID in `/proc/<pid>/status` and its NSS name, or `uid:<number>` when there is no name. It is checked against the PID start time and the GPU UUID. If the owner cannot be confirmed, the process is shown with an unknown owner rather than a guessed one.
3. Every `disk_poll_interval_seconds` (30 min), a separate worker measures disk usage with `df`, time-limited `du`, and `docker ps --size`, or through the optional privileged helper.
4. Observations are stored in SQLite as time intervals. Overlapping GPUs, processes, and users are merged, so busy time is never counted twice.
5. The page reads `/api/snapshot`, `/api/events`, `/api/insights`, and `/api/intelligence-index`.

### Status rules

- A GPU is **busy** if it has a compute process, uses at least 500 MiB of VRAM, or is at least 10% utilized. Both thresholds are configurable.
- A process that keeps VRAM allocated counts as busy even at 0% utilization, so reserved GPUs are not shown as free. These figures describe occupancy, not compute efficiency.
- A server is **DOWN** when a complete GPU observation fails. Diagnostics tell timeouts, SSH connection or authentication failures, and driver or device faults apart. None of its GPUs count as available, earlier readings are not shown as current, and a successful disk probe does not bring it back UP.
- A quick, transient connection error is retried once within the same time budget. Time lost to a failure is never filled in as use.
- A guarded fallback for NVIDIA driver/library mismatches can be enabled for explicitly approved hosts. The normal path always uses the native driver.

### Time and indices

- When several users share a GPU, its busy time is split equally among the distinct confirmed owners. If any process on that GPU has an unknown owner, the interval stays unassigned instead of being charged to the known users.
- Sessions shorter than 60 seconds stay in history and totals but do not clear the ❄️ badge. Long idle needs seven days of tracking, not 100% observation coverage.
- The seven-day busy index divides busy time by observed GPU-slot time. Unobserved time is not counted as idle, and rounded 100% coverage reads `관측 7일 / 7일`.
- LAB DAILY INDEX adds up each GPU's observation-weighted mean VRAM over the KST calendar day. Raw VRAM is stored in 16 MiB steps.

## Security model

- **Access control.** Caddy enforces an IP allowlist. The app checks the client IP, `Host`, and `Origin` again on its own.
- **Writes.** Changes to notices and timers require a same-origin request and a passphrase or PIN. Hashes use PBKDF2-SHA256 with 600,000 iterations. At most two hash checks run at once, and failed attempts are rate-limited per subnet and globally.
- **Password fields.** They are masked, turn autocomplete off, carry password-manager ignore hints, and are cleared when a dialog closes. A browser can still offer to save them; a page cannot fully prevent that.
- **What the browser never receives.** Remote commands, stderr, full command lines, environment variables, SSH users, ports, and passwords. Server cards show only a validated IP address.
- **Browser hardening.** A strict Content Security Policy blocks inline and third-party scripts. Static files are served from an explicit allowlist, with path traversal and symlinks refused. Icons and flags are local.
- **Containers.** They run as a non-root user with a read-only root filesystem, all capabilities dropped, `no-new-privileges`, and PID, memory, and log limits.
- **Secrets.** SSH passwords, API keys, and the PIN hash live in git-ignored runtime folders (`secrets/`, `data/`, `operator-secrets/`), never in the repository. SSH passwords go to OpenSSH through `SSH_ASKPASS`, not on the command line.
- **Transport.** Plain HTTP is intended for a trusted LAN only. IP filtering is not encryption; add TLS and authentication before exposing the dashboard more widely.

## Requirements

| Where | What you need |
|---|---|
| Dashboard host | Python 3.12 (standard library only) and the OpenSSH client, or Docker |
| Each GPU server | SSH access for a monitoring account, the NVIDIA driver with `nvidia-smi`, `python3` (the probe stays compatible with Python 3.6), `df`, and GNU `du`. No home directory is required. Docker is optional and adds per-container usage. The privileged disk helper additionally needs `sudo`. |
| Viewers | A current web browser |

## Quick start

```sh
git clone https://github.com/jumincho/gpu-watch.git
cd gpu-watch

# 1. Describe your servers (see Configuration below), then verify SSH keys and known_hosts.
$EDITOR hosts.json

# 2. Create the admin PIN. The script prints where the one-time plaintext PIN was saved.
python3 scripts/admin-passphrase.py ensure

# 3. Start the dashboard.
python3 server.py --host 127.0.0.1 --port 8787
```

Open <http://127.0.0.1:8787/>. Store the admin PIN somewhere safe and delete the plaintext file.

By default, only loopback clients are allowed. To serve other machines on your LAN, list them explicitly. The addresses below are examples.

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
| `display_ip` | The IP shown on the card for alias-based hosts. It must be a valid IP address. |
| `expected_gpu_count` | Number of GPU slots to keep showing while the server is down |
| `note`, `owner`, `owner_type`, `location` | Card text and badge style (`assigned` or `shared`) |
| `disk_user_paths` | Extra per-user paths to measure, as `{ "user": …, "path": … }` |
| `collect_docker_usage` | Also measure Docker writable layers with `docker ps --size`. On by default only for hosts in the `nll` lab. |
| `privileged_disk_helper` | Measure disk usage through the installed root helper. Cannot be combined with `disk_user_paths`. |

Other top-level settings cover probe timeouts, `collector_workers`, the `activity_policy` thresholds for the 🔥 and ❄️ badges, and retention:

| Data | Kept by default |
|---|---|
| Events and daily indices | 180 days |
| Raw intervals | 8 days |
| Deleted or expired notices | 90 days after deletion or expiry. Notices without an expiry stay until deleted. |
| Daily SQLite backups | 14 days |
| Pre-deployment backups | 30 days |

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
| `GPU_WATCH_BUILD_VERSION` | Release number reported by the health API. `deploy.sh` requires it to match `VERSION`. | package version |

### Artificial Analysis

To show the Intelligence Index, put the API key in `secrets/artificial_analysis_api_key`. It must be a regular file owned by the app user, not a symlink, with mode `0600` inside a restricted directory. The key is read again on every attempt, so you can rotate it without a restart. Redirects are refused, so the key is never sent to another origin. The browser receives only the cached rankings.

| Situation | What happens |
|---|---|
| Normal | Complete refreshes are spread across the quota window using the response's `X-RateLimit-*` headers and the number of pages, with up to 8 requests kept in reserve |
| 100 requests a day, four pages | About once an hour; never more often than every 15 minutes |
| Quota low, or `Retry-After` sent | Waits until the quota resets or the requested time |
| No rate-limit headers | Every six hours |
| A failed refresh | Retries in one hour, or later if `Retry-After` asks for longer |
| Data older than 48 hours | Marked stale |

- Each request is counted before it is sent, so a restart or a network error never makes the server think it has requests left. New rankings are published only after every page has been validated.
- Quota state (limit, remaining, reset time, pages, next attempt) is saved next to the cache in `data/`, separately from the key.
- A small scheduler thread keeps these appointments whether or not anyone is viewing. The page itself checks the server cache every five minutes.
- The index version is shown exactly as the API returns it (currently `4.3`); no patch number is invented. Long model variant names are shortened, for example to `(max)`.

## Privileged disk helper

On some servers the monitoring account cannot read other users' home directories, so per-user totals stay partial. For those servers you can install a small root helper that runs only GPU Watch's own disk collector.

```sh
# Generate a reviewable installer (this does not install anything).
python3 scripts/provision-disk-helper.py --user gpuwatch --output disk-installer.py

# Review disk-installer.py, copy it to the GPU server, and run it there as an administrator.
sudo python3 -I disk-installer.py
```

The installer adds `/usr/local/libexec/gpu-watch-disk`, a sudoers rule that lets the monitoring account run exactly that helper with no arguments, and a cache folder in `/var/cache/gpu-watch/`. The helper accepts no paths, commands, or environment. It uses a fixed `PATH`, prevents concurrent scans, caches results for five minutes, and runs at low CPU priority. No daemon is installed, and no administrator password is stored.

After installing, set `"privileged_disk_helper": true` for that host. If the helper is missing, GPU Watch falls back to an unprivileged scan within the same time budget and marks per-user usage as partial. Reinstall the helper whenever you change the disk probe.

## Deployment

The reference production setup runs two hardened containers. Caddy is the only published port; the app stays on an internal Docker network.

- `Dockerfile` builds the app on a digest-pinned `python:3.12-alpine` image.
- `Dockerfile.caddy` builds Caddy from a pinned commit and pinned dependency versions.
- `deploy.sh` is the deployment script for the lab's production host. It checks paths and permissions, builds both images, and validates a candidate container (health, snapshot, collector). It then stops and keeps the previous app container and makes a verified SQLite backup with a checksum before changing the database or SSH runtime files. After switching production, it checks the allowlist and health and rolls back containers, SSH runtime files, and the database on failure. It removes only this project's own unused build images. To reuse it elsewhere, change the host-specific paths and addresses first.

| Container | Limits |
|---|---|
| App | Non-root user, read-only root, capabilities dropped, `no-new-privileges`, 512 MiB memory, 128 PIDs |
| Caddy | Read-only root, 192 MiB memory, 64 PIDs |

> [!IMPORTANT]
> The release fingerprint hashes `VERSION`, `server.py`, `hosts.json`, `gpu_watch/`, and `static/`. Production and the emergency copy must match it. Scripts, documentation, and tests are outside the fingerprint, so compare a full file manifest as well.

### Windows emergency fallback

`emergency-local-fallback.ps1 -Action Status|Start|Stop` runs a local copy only while production is down. Adapt its paths, addresses, and SSH configuration first.

- `Start` refuses while production is reachable unless you pass `-Force` for a deliberate takeover. Even then it requires a matching `VERSION` and release fingerprint, and a fresh collection cycle must pass before it reports success.
- The app runs as a standard user. Only the firewall rule for the lab LAN needs Windows UAC.
- `Stop` checks the exact process identity, then removes the listener, its firewall rule, and any temporary password files. Unrelated processes on the same port are left alone.
- The local database is separate. Reconcile notices and timers created during an outage before switching back.

## Operations

```sh
# Health of a running container
docker exec gpu-watch-dashboard python3 /app/scripts/check_local.py --health-only --expected-build-version 2.5

# SQLite integrity and aggregation invariants
python3 scripts/audit-data.py data/gpu_watch.sqlite3

# Restore a local backup (verifies the destination and checksum)
sh scripts/restore-backup.sh /absolute/path/to/backup.sqlite3
```

The maintenance worker makes daily SQLite backups, and `deploy.sh` adds one before each deployment. There is no automatic off-site copy; `scripts/offsite-backup.sh` is a manual tool.

## Development and tests

```sh
python3 -m unittest discover -s tests -v   # 284 Python regression and contract tests
node tests/test_frontend.js                # 25 frontend checks
```

A few tests are specific to Windows or POSIX and skip elsewhere. Run the full suite on Windows or on Linux with GNU coreutils; the minimal Alpine app image uses BusyBox and cannot run the GNU `du` fixtures.

The tests also lock product behavior, such as status rules, interval math, security headers, and UI wording. A failing contract test usually means a user-visible change that needs a deliberate decision. Keep migrations, meaningful regression tests, verified backups, and timer colors. When you change the UI, prefer focused modules over rewriting the large storage or probe code, and keep the calculation rules explicit.

## Project layout

| Path | Purpose |
|---|---|
| `server.py` | HTTP API, collector, storage, and maintenance |
| `gpu_watch/` | Authentication, process attribution, security helpers, history, and the Artificial Analysis client with its quota tracking and scheduler |
| `static/` | Frontend (`index.html`, `app.js`, `styles.css`) plus bundled icons and flags |
| `hosts.json` | Servers, labs, and collection settings (fictional example) |
| `Caddyfile`, `Dockerfile`, `Dockerfile.caddy` | Edge proxy and container images |
| `deploy.sh` | Production deployment with backup and rollback |
| `run-dashboard.ps1`, `emergency-local-fallback.ps1` | Windows emergency collector |
| `scripts/` | Health checks, data audit, backup and restore, admin PIN, SSH helpers, and the disk helper installer generator |
| `tests/` | Python and Node test suites |

## Release

**v2.5**, the formal launch, released on 2026-10-03 by GPT-6 Astra Ultra. The dashboard shows no release footer; release details live in `VERSION`, `gpu_watch/__init__.py`, this README, and GitHub releases.

[RELEASE_VALIDATION.md](RELEASE_VALIDATION.md) lists the checks this release passed. It records tested behavior at this source version, not a guarantee for every future environment or failure.

## Acknowledgements

Flag icons come from [Twemoji](https://github.com/jdecked/twemoji) under CC-BY 4.0; see [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md). Service icons belong to their respective owners and are used only to label links to their status pages.

## License

GPU Watch is released under the [MIT License](LICENSE). The license does not cover the bundled flag and service icons; see Acknowledgements above.
