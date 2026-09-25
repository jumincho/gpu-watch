# GPU Watch Dashboard v3

A lightweight dashboard for shared NVIDIA GPU servers. See which GPUs are available, who is occupying them, and how much disk space remains—without a frontend build step or a permanent GPU agent.

[English](README.md) · [한국어](README.ko.md) · [日本語](README.ja.md) · [简体中文](README.zh-CN.md) · [繁體中文](README.zh-HK.md)

![GPU Watch with fictional demonstration data](docs/images/dashboard.png)

The screenshot uses fictional data. The interface uses Korean with English technical labels. This repository contains example infrastructure; it does not contain the deployment's inventory, credentials, database, or private operations handbook.

## What it provides

- GPU utilization, VRAM, temperature, free/busy state, recent use, and compact process details.
- Multiple processes and users per GPU, lab switching, server filters, and filesystem warnings.
- Seven-day busy indices and user shares, observation coverage, long-idle/high-usage badges, and LAB DAILY INDEX based on observed VRAM.
- Recent Activity with date/server/user filters and pagination. DOWN/UP transitions are optional and hidden by default; observation-gap/user-change diagnostics remain internal.
- Six conference timers with stable deadline ordering, persistent colors, TBA mode, and bundled flag SVGs.
- Notices with optional expiry, author passphrases, and administrator management.
- Service links and a cached Artificial Analysis Intelligence Index. Model names use concise effort labels such as `(max)`.
- Keyboard controls, responsive layouts, reduced-motion support, and refreshes that preserve the selected Disk tab, focus, and scroll position.

The backend uses Python's standard library and SQLite. The frontend is plain HTML, CSS, and JavaScript. GPU probes run every 10 seconds and Disk probes every 30 minutes by default. Power, ECC, and throttling telemetry are deliberately outside the product's scope.

## Quick start

Requirements: Python 3.12 and OpenSSH on the dashboard machine; SSH, Python 3, `nvidia-smi`, `df`, and GNU `du` on monitored Linux servers. Docker inspection is optional.

```sh
git clone https://github.com/jumincho/gpu-watch.git
cd gpu-watch
# Replace the fictional inventory with your hosts and pinned SSH host keys.
$EDITOR hosts.json
python3 scripts/admin-passphrase.py ensure
python3 server.py --host 127.0.0.1 --port 8787
```

Open [the local dashboard](http://127.0.0.1:8787/). Retrieve the initial administrator PIN from the path reported by the setup command, then remove the one-time plaintext file after secure storage. Default access is loopback only.

```sh
# Documentation addresses: replace these with your own LAN and host.
GPU_WATCH_ALLOWED_NETWORKS="127.0.0.0/8,::1/128,192.0.2.0/24" \
GPU_WATCH_ALLOWED_HOSTS="192.0.2.10:8787,127.0.0.1:8787,localhost:8787" \
python3 server.py --host 0.0.0.0 --port 8787
```

## Configuration and semantics

Each `hosts.json` entry needs a unique `name`, a configured `lab`, and `expected_gpu_count`. Supply `ssh_host`, `ssh_user`, `ssh_port`, and `ssh_identity_file`, or use an SSH alias. `display_ip` controls the validated address shown on the card. Password login uses a runtime `ssh_password_file`, never a committed credential. `disk_user_paths` adds explicit user/path pairs; `collect_docker_usage` enables Docker accounting (default for the example `nll` group).

A GPU is busy when a compute process is present, VRAM reaches the configured threshold (500 MiB by default), or utilization reaches 10%. A resident process can keep it busy at 0% utilization: occupied memory is not available capacity. A failed complete GPU observation makes the server DOWN, while a separate Disk failure preserves GPU status.

Process attribution checks UID/NSS, PID start ticks, and GPU/PID evidence. Numeric UIDs remain valid when no NSS name exists. Root container processes use the container label for operational distinction; that label does not prove a person's identity. Unconfirmed owners are not charged to a previous user. Tooltips allow a short executable/script/module summary and process metadata, excluding full argv, option values, and environment variables.

Brief sessions under 60 seconds remain in the logs and usage history but do not reset long-idle classification. Missing observations are not silently filled with zero usage. LAB DAILY INDEX sums per-GPU time-weighted observed VRAM averages; coverage uses elapsed-time unions.

### Accurate Disk accounting

Filesystem capacity comes from `df`; user attribution covers readable homes, explicit paths, and Docker writable layers. Docker inspection requests exact writable bytes and mounts, with bounded concurrency and timeouts. Shared images, volumes, system data, reserved blocks, and open deleted files explain why user totals can differ from `df`. Partial results remain marked as partial.

For hosts where ordinary permissions hide home directories, v3 includes an **optional fixed privileged Disk helper**:

```sh
python3 scripts/provision-disk-helper.py --user gpuwatch --output disk-installer.py
# Review the generated installer, then run it with the monitored host's administrator.
sudo python3 -I disk-installer.py
```

The installer creates a root-owned `/usr/local/libexec/gpu-watch-disk` and an exact no-argument sudo rule. Enable `privileged_disk_helper: true` only after installation. The helper accepts no paths or commands, uses isolated Python and a fixed environment, prevents concurrent scans, caches for five minutes, and runs at low CPU priority. It installs no daemon. Custom `disk_user_paths` are incompatible with this fixed helper. If it is unavailable, collection attempts an explicitly partial ordinary-user fallback within the same timeout budget.

### Artificial Analysis

Put the key in `secrets/artificial_analysis_api_key` as a regular, non-symlink file with mode `0600`. The browser receives only cached rankings. Quota metadata and the next scheduled attempt persist separately from the key.

The client uses response rate-limit headers and page count to distribute complete refreshes through the quota window, reserving spare requests. With a 100-request daily allowance and four pages, the target is about hourly; a 15-minute floor applies. Missing headers fall back to six hours. Failures use a one-hour default retry and honor longer `Retry-After`; insufficient quota waits until reset. The browser checks the server cache every five minutes. An inactive dashboard may refresh on the next maintenance cycle. Data older than 48 hours is stale. API `major.minor` version metadata is displayed as supplied, without inventing a patch version.

## Deployment, security, and recovery

`Dockerfile` and `Dockerfile.caddy` build pinned images. `deploy.sh` is a reference deployment that must be adapted to your paths, host keys, LAN, and example addresses. It builds the images, validates runtime SSH and Caddy configuration, preserves the previous containers, verifies a pre-switch SQLite backup, and checks health after switching. Failure triggers rollback. Caddy is the only published port; the application stays on a private Docker network.

The application enforces IP/Host allowlists, same-origin writes, bounded requests, and rate/concurrency limits. Passphrases use PBKDF2-SHA256. Static files use an allowlist and reject symlinks/path traversal; a Content Security Policy blocks inline scripts. Containers run as non-root with read-only roots, dropped capabilities, and resource limits. Diagnostics are bounded and redact common secret assignments; they can still contain operational host/error information inside the permitted LAN.

Plain HTTP provides no transport encryption. Deploy according to your own network trust requirements. No claim of permanent security or external-server availability is made.

```sh
docker exec gpu-watch-dashboard python3 /app/scripts/check_local.py --health-only
python3 scripts/audit-data.py data/gpu_watch.sqlite3
sh scripts/restore-backup.sh /absolute/path/inside/data/backups/backup.sqlite3
```

Restore checks the target, SQLite integrity, foreign keys, and schema compatibility. Daily backups default to 14-day retention and pre-deployment backups to 30 days. There is no automatic offsite backup; the offsite script is an optional manual tool.

The Windows emergency scripts are reference scripts requiring site-specific configuration. Production and emergency copies must share VERSION and release fingerprint. Normal operation leaves the emergency instance off. `Start` refuses when production is reachable unless a deliberate `-Force` takeover is requested; `Stop` removes the owned listener, dedicated firewall rule, and temporary password. Firewall changes require Windows elevation. Data freshness is separate from source parity: plan how to reconcile records created during an emergency.

## Tests and source layout

```sh
python3 -m unittest discover -s tests -v
node tests/test_frontend.js
```

`server.py` contains probes, storage, collection, and HTTP handling; `gpu_watch/` provides focused security, attribution, history, and API helpers. `static/` holds the UI and local assets, `scripts/` the operational tools, and `tests/` regression checks. Runtime folders (`data/`, `secrets/`, `runtime-ssh/`, `operator-secrets/`) stay out of Git and container build contexts. LF checkout rules preserve cross-platform source fingerprints.

Version 3 was prepared on 2026-09-25 by GPT-6 Astra Max. The UI credit is `GPT-6 Astra Max / Claude Opus 5.5 Max`.

## License

Code: [MIT](LICENSE). Bundled Twemoji flags and service artwork have separate terms in [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
