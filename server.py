from __future__ import annotations

import argparse
import base64
import html
import hashlib
import ipaddress
import json
import math
import os
import re
import secrets
import signal
import shutil
import sqlite3
import stat
# Bandit B404: fixed-argv SSH probes require subprocess; shell is never enabled.
import subprocess  # nosec B404
import threading
import time
import zlib
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import ExitStack, closing, nullcontext
from datetime import datetime, timedelta, timezone
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from gpu_watch.artificial_analysis import ArtificialAnalysisIndex, unavailable_payload
from gpu_watch.auth import (
    hash_needs_upgrade,
    hash_passphrase,
    is_valid_announcement_passphrase,
    is_valid_pin,
    verify_passphrase,
)
from gpu_watch import __release_date__, __release_model__, __version__
from gpu_watch.maintenance import MaintenanceWorker
from gpu_watch.opslog import audit
from gpu_watch.collection import retry_reason
from gpu_watch.temporary_driver import validate_temporary_driver_fallback, REMOTE_TEMPORARY_DRIVER_PROBE
from gpu_watch.health import observation_event_details, event_details, migrate_host_availability
from gpu_watch.processes import sanitize_disk_snapshot, sanitize_gpu_snapshot, sanitize_processes
from gpu_watch.security import (
    DEFAULT_ALLOWED_HOSTS,
    DEFAULT_ALLOWED_NETWORKS,
    SECURITY_HEADERS,
    SlidingWindowRateLimiter,
    client_ip_from_proxy,
    is_host_allowed,
    is_ip_allowed,
    is_same_origin,
    parse_allowed_networks,
    parse_allowed_hosts,
    redact_sensitive_text,
)


ROOT = Path(__file__).resolve().parent
STATIC_DIR = ROOT / "static"
DATA_DIR = ROOT / "data"
DB_PATH = DATA_DIR / "gpu_watch.sqlite3"
BACKUP_DIR = DATA_DIR / "backups"
HOSTS_PATH = ROOT / "hosts.json"
ADMIN_PIN_HASH_PATH = DATA_DIR / "admin_pin.hash"
SUBPROCESS_CREATIONFLAGS = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
KST = timezone(timedelta(hours=9))
SCHEMA_VERSION = 6
EVENT_USAGE_MIGRATION_KEY = "event_usage_v1_0_1_r2"
RECENT_USAGE_BACKFILL_KEY = "recent_usage_event_backfill_v1_0_1"
OBSOLETE_MAINTENANCE_KEYS = ("event_usage_v1_0_1",)
MAX_REMOTE_PROBE_OUTPUT_BYTES = 4 * 1024 * 1024
MAX_GPUS_PER_HOST = 128
MAX_EVENT_OFFSET = 1_000_000
MAX_ACTIVE_ANNOUNCEMENTS = 20
MAX_CONFERENCE_DEADLINES = 6
CONFERENCE_DEADLINE_TONES = (
    "silver",
    "gold",
    "emerald",
    "diamond",
    "master",
    "grandmaster",
)
CONFERENCE_DEADLINES_KEY = "conference_deadlines"
LEGACY_CONFERENCE_DEADLINE_KEY = "conference_deadline"
CONFERENCE_DEADLINE_PALETTE_MIGRATION_KEY = "conference_deadline_palette_v1_2_0_silver"
CONFERENCE_DEADLINE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,32}$")
AUTH_WORK_SEMAPHORE = threading.BoundedSemaphore(2)
AUTH_ATTEMPT_SEMAPHORE = threading.BoundedSemaphore(2)
AUTH_FAILURE_SUBNET_LIMIT = 10
AUTH_FAILURE_GLOBAL_LIMIT = 20
AUTH_FAILURE_WINDOW_SECONDS = 3600


def compute_release_fingerprint(root: Path | None = None) -> str:
    """Hash the application payload shared by production and emergency runtimes."""
    release_root = ROOT if root is None else Path(root)
    paths = [
        release_root / "VERSION",
        release_root / "server.py",
        release_root / "hosts.json",
    ]
    paths.extend((release_root / "gpu_watch").rglob("*.py"))
    paths.extend(path for path in (release_root / "static").rglob("*") if path.is_file())
    manifest = []
    for path in sorted(paths, key=lambda item: item.relative_to(release_root).as_posix()):
        relative = path.relative_to(release_root).as_posix()
        manifest.append(f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {relative}\n")
    return hashlib.sha256("".join(manifest).encode("utf-8")).hexdigest()




class ProcessOutputLimitExceeded(RuntimeError):
    """Raised after a child is stopped for exceeding the combined output cap."""


class ProcessStopped(RuntimeError):
    """Raised after a child is stopped because its owning worker is shutting down."""


def _communicate_bounded(
    proc: subprocess.Popen[Any],
    *,
    timeout: float,
    max_output_bytes: int,
    stop_event: threading.Event | None = None,
) -> tuple[str, str]:
    """Drain stdout/stderr concurrently without ever retaining unbounded output."""
    if proc.stdout is None or proc.stderr is None:
        raise ValueError("bounded communication requires stdout and stderr pipes")
    limit = max(1, int(max_output_bytes))
    buffers = {"stdout": bytearray(), "stderr": bytearray()}
    total = [0]
    buffer_lock = threading.Lock()
    overflow = threading.Event()
    reader_failed = threading.Event()
    reader_errors: list[BaseException] = []

    def stop_process(*, force: bool) -> None:
        try:
            if proc.poll() is None:
                (proc.kill if force else proc.terminate)()
        except OSError:
            pass

    def drain(name: str, stream: Any) -> None:
        try:
            while True:
                chunk = stream.read(64 * 1024)
                if not chunk:
                    return
                if isinstance(chunk, str):
                    chunk = chunk.encode("utf-8", "replace")
                exceeded = False
                with buffer_lock:
                    remaining = max(0, limit - total[0])
                    if remaining:
                        accepted = chunk[:remaining]
                        buffers[name].extend(accepted)
                        total[0] += len(accepted)
                    if len(chunk) > remaining:
                        exceeded = True
                        overflow.set()
                if exceeded:
                    stop_process(force=True)
        except (OSError, TypeError, ValueError) as exc:
            if proc.poll() is None:
                with buffer_lock:
                    reader_errors.append(exc)
                reader_failed.set()
                stop_process(force=True)

    readers = [
        threading.Thread(target=drain, args=("stdout", proc.stdout), name="gpu-watch-stdout", daemon=True),
        threading.Thread(target=drain, args=("stderr", proc.stderr), name="gpu-watch-stderr", daemon=True),
    ]
    for reader in readers:
        reader.start()

    deadline = time.monotonic() + max(0.0, float(timeout))
    timed_out = False
    stopped = False
    stop_started: float | None = None
    while proc.poll() is None:
        if overflow.is_set():
            stop_process(force=True)
        if reader_failed.is_set():
            stop_process(force=True)
        if stop_event is not None and stop_event.is_set():
            stopped = True
            if stop_started is None:
                stop_started = time.monotonic()
                stop_process(force=False)
            elif time.monotonic() - stop_started >= 0.5:
                stop_process(force=True)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            timed_out = True
            stop_process(force=True)
            break
        try:
            proc.wait(timeout=min(0.05, remaining))
        except subprocess.TimeoutExpired:
            continue

    try:
        proc.wait(timeout=1.0)
    except subprocess.TimeoutExpired:
        stop_process(force=True)
        try:
            proc.wait(timeout=1.0)
        except subprocess.TimeoutExpired:
            pass
    for reader in readers:
        reader.join(1.0)
    if all(not reader.is_alive() for reader in readers):
        for stream in (proc.stdout, proc.stderr):
            try:
                stream.close()
            except (OSError, ValueError):
                pass

    if overflow.is_set():
        raise ProcessOutputLimitExceeded("child output exceeded the configured limit")
    if timed_out:
        command_for_error = str(getattr(proc, "args", None) or "collector")
        raise subprocess.TimeoutExpired(command_for_error, timeout)
    if stopped:
        raise ProcessStopped("collector stopping")
    if reader_errors:
        raise OSError(type(reader_errors[0]).__name__)
    return (
        buffers["stdout"].decode("utf-8", "replace"),
        buffers["stderr"].decode("utf-8", "replace"),
    )


REMOTE_PROBE_COMMON = r"""
import csv
import base64
import json
import os
import pwd
import re
import subprocess
import sys
import time

os.environ["LC_ALL"] = "C"

def run(cmd, timeout=5):
    p = None
    try:
        p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        out, err = p.communicate(timeout=timeout)
        return p.returncode, out.decode("utf-8", "replace"), err.decode("utf-8", "replace")
    except subprocess.TimeoutExpired:
        if p is not None:
            p.kill()
            out, err = p.communicate()
            return 124, out.decode("utf-8", "replace"), "timeout after %ss" % timeout
        return 124, "", "timeout after %ss" % timeout
    except Exception as exc:
        return 999, "", type(exc).__name__ + ": " + str(exc)


def to_int(value):
    try:
        return int(float(value))
    except Exception:
        return None


def parse_csv(text):
    return [row for row in csv.reader(text.splitlines()) if row]


def arg_value(prefix, default):
    for arg in sys.argv[1:]:
        if arg.startswith(prefix):
            try:
                return int(arg.split("=", 1)[1])
            except Exception:
                return default
    return default


def arg_text(prefix, default=""):
    for arg in sys.argv[1:]:
        if arg.startswith(prefix):
            return arg.split("=", 1)[1]
    return default


def arg_json_b64(prefix, default):
    value = arg_text(prefix)
    if not value:
        return default
    try:
        return json.loads(base64.b64decode(value.encode("ascii")).decode("utf-8"))
    except Exception:
        return default


include_disk = "--disk" in sys.argv[1:]
include_docker = "--docker-usage" in sys.argv[1:]
du_timeout = arg_value("--du-timeout=", 20)
remote_cmd_timeout = max(5, arg_value("--remote-timeout=", 12))
docker_usage_timeout = max(10, arg_value("--docker-timeout=", 30))
busy_memory_threshold = max(1, arg_value("--busy-memory-threshold-mib=", 500))
busy_utilization_threshold = max(1, arg_value("--busy-utilization-threshold-percent=", 10))
disk_user_paths = arg_json_b64("--disk-user-paths-b64=", [])
expected_gpu_count = max(0, arg_value("--expected-gpu-count=", 0))

"""

REMOTE_GPU_PROBE = r"""
gpu_env_prefix = []
nvidia_smi_command = ["nvidia-smi"]
gpu_query_args = [
    "--query-gpu=index,name,uuid,utilization.gpu,memory.used,memory.total,temperature.gpu,driver_version",
    "--format=csv,noheader,nounits",
]


def first_error(*values):
    for value in values:
        lines = [line.strip() for line in str(value or "").splitlines() if line.strip()]
        if lines:
            return lines[0][:180]
    return "nvidia-smi failed"


def gpu_query_command(selector=None):
    command = list(nvidia_smi_command)
    if selector:
        command.extend(["-i", selector])
    command.extend(gpu_query_args)
    return command


def parse_gpus(text):
    parsed = []
    versions = set()
    for row in parse_csv(text):
        if len(row) < 8:
            continue
        index, name, uuid, util, mem_used, mem_total, temp, driver = [x.strip() for x in row[:8]]
        index_int = to_int(index)
        memory_used = to_int(mem_used)
        memory_total = to_int(mem_total)
        # An unreadable sample fails the whole host observation; never turn
        # missing memory telemetry into a free GPU.
        if index_int is None or memory_used is None or memory_used < 0 or memory_total is None or memory_total <= 0:
            continue
        if driver:
            versions.add(driver)
        parsed.append({
            "index": index_int,
            "name": name,
            "uuid": uuid,
            "utilization": to_int(util),
            "memory_used": memory_used,
            "memory_total": memory_total,
            "temperature": to_int(temp),
            "processes": [],
        })
    return parsed, versions


rc, out, err = run(gpu_query_command(), timeout=remote_cmd_timeout)
gpu_env_prefix = temporary_driver_prefix(rc)
if gpu_env_prefix:
    nvidia_smi_command = gpu_env_prefix + ["nvidia-smi"]
    rc, out, err = run(gpu_query_command(), timeout=remote_cmd_timeout)
gpus, driver_versions = parse_gpus(out) if rc == 0 else ([], set())
expected_gpu_indices = set(range(expected_gpu_count))
observed_gpu_indices = {gpu["index"] for gpu in gpus}
bulk_complete = rc == 0 and bool(gpus) and len(gpus) == len(observed_gpu_indices) and (
    not expected_gpu_indices or observed_gpu_indices == expected_gpu_indices
)
if not bulk_complete:
    error = first_error(err, out) if rc else "GPU payload does not match expected inventory"
    print(json.dumps({"ok": False, "error": error}))
    raise SystemExit(0)

compute_query_args = [
    "--query-compute-apps=pid,gpu_uuid,used_memory,process_name",
    "--format=csv,noheader,nounits",
]


def compute_query_command():
    command = list(nvidia_smi_command)
    command.extend(compute_query_args)
    return command


def parse_compute_apps(text):
    result = []
    for row in parse_csv(text):
        if len(row) < 4:
            continue
        pid, gpu_uuid, used_memory, process_name = [x.strip() for x in row[:4]]
        try:
            pid_int = int(pid)
        except Exception:
            continue
        try:
            used_memory_int = int(float(used_memory))
        except Exception:
            used_memory_int = None
        result.append({
            "pid": pid_int,
            "gpu_uuid": gpu_uuid,
            "used_memory": used_memory_int,
            "process_name": process_name,
        })
    return result


rc, out, err = run(compute_query_command(), timeout=remote_cmd_timeout) if gpus else (0, "", "")
apps = parse_compute_apps(out) if rc == 0 else []


def has_missing_busy_processes():
    covered_gpu_uuids = {app["gpu_uuid"] for app in apps}
    return any(
        gpu["uuid"] not in covered_gpu_uuids
        and (
            (gpu.get("memory_used") or 0) >= busy_memory_threshold
            or (gpu.get("utilization") or 0) >= busy_utilization_threshold
        )
        for gpu in gpus
    )


def merge_compute_apps(extra_apps):
    seen = {(app["pid"], app["gpu_uuid"]) for app in apps}
    for app in extra_apps:
        key = (app["pid"], app["gpu_uuid"])
        if key in seen:
            continue
        apps.append(app)
        seen.add(key)


# Aggregate telemetry and the compute-process table are separate NVML reads.
# A very short job can cross that boundary, leaving a truthful busy sample but
# no owner. Retry once only for affected GPUs; the normal probe path remains
# unchanged and unresolved sessions stay unassigned rather than being guessed.
if has_missing_busy_processes():
    retry_rc, retry_out, _ = run(compute_query_command(), timeout=remote_cmd_timeout)
    if retry_rc == 0:
        merge_compute_apps(parse_compute_apps(retry_out))

SAFE_COMMAND_TOKEN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,127}$")
SAFE_MODULE_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
SAFE_SCRIPT_NAME_RE = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,127}\.(?:py|pyw|sh|bash|zsh|js|mjs|cjs|r|pl|rb|lua)$",
    re.IGNORECASE,
)


def command_summary_for_pid(pid, process_name):
    # Return a non-sensitive executable/script identity without argv options.
    executable = os.path.basename(str(process_name or ""))[:128]
    if not SAFE_COMMAND_TOKEN_RE.fullmatch(executable):
        executable = "process"
    try:
        with open("/proc/%s/cmdline" % pid, "rb") as f:
            raw = f.read(16384)
        argv = [part.decode("utf-8", "replace") for part in raw.split(b"\0") if part]
    except Exception:
        return executable
    if not argv:
        return executable

    lowered = executable.lower()
    allowed_extensions = ()
    if lowered.startswith("python") or lowered in {"torchrun", "deepspeed", "accelerate"}:
        allowed_extensions = (".py", ".pyw")
    elif lowered in {"sh", "bash", "zsh"}:
        allowed_extensions = (".sh", ".bash", ".zsh")
    elif lowered in {"node", "nodejs"}:
        allowed_extensions = (".js", ".mjs", ".cjs")
    elif lowered == "rscript":
        allowed_extensions = (".r",)
    elif lowered == "perl":
        allowed_extensions = (".pl",)
    elif lowered == "ruby":
        allowed_extensions = (".rb",)
    elif lowered == "lua":
        allowed_extensions = (".lua",)

    index = 1
    while index < len(argv):
        token = argv[index]
        if lowered.startswith("python"):
            if token == "-m":
                module = argv[index + 1] if index + 1 < len(argv) else ""
                if SAFE_MODULE_NAME_RE.fullmatch(module):
                    return "%s -m %s" % (executable, module)
                return executable
            if token in {"-c", "-"}:
                return executable
            if token in {"-W", "-X"}:
                index += 2
                continue
            if token.startswith(("-W", "-X")):
                index += 1
                continue
            if token in {"-u", "-B", "-E", "-I", "-O", "-OO", "-s", "-S", "-q", "-v", "-b", "-bb"}:
                index += 1
                continue
        if token == "--":
            index += 1
            if index >= len(argv):
                return executable
            token = argv[index]
        elif token.startswith("-"):
            # Other launchers have ambiguous option arities. Retain the safe
            # executable rather than exposing an arbitrary option value.
            return executable
        basename = os.path.basename(token)
        if allowed_extensions and basename.lower().endswith(allowed_extensions) and SAFE_SCRIPT_NAME_RE.fullmatch(basename):
            return "%s %s" % (executable, basename)
        return executable
    return executable


def process_start_identity(pid):
    # Linux assigns one monotonic start-time tick value to each PID generation.
    # Parse after the final ')' because a process comm may itself contain spaces
    # or ')' characters. Field 22 is index 19 after fields 1-2 are removed.
    try:
        with open("/proc/%s/stat" % pid, "r", encoding="utf-8", errors="replace") as f:
            raw = f.read(8192)
    except Exception:
        return None
    close_index = raw.rfind(")")
    fields = raw[close_index + 1:].split() if close_index >= 0 else []
    if len(fields) <= 19 or not fields[19].isdigit() or int(fields[19]) <= 0:
        return None
    return fields[19]


def process_metadata(pid):
    # Resolve a live process owner from procfs before using the ps fallback.
    # Refuse metadata without a PID-generation identity: a later reuse could
    # otherwise attach this owner to an unrelated GPU process.
    start_identity = process_start_identity(pid)
    if start_identity is None:
        return None
    result = {
        "user": "",
        "started": "",
        "process_name": "",
        "_proc_start_ticks": start_identity,
    }
    try:
        uid = int(os.stat("/proc/%s" % pid).st_uid)
    except (FileNotFoundError, ProcessLookupError):
        return None
    except Exception:
        uid = None
    # procfs can report a root-owned directory for a non-dumpable process.
    # The effective UID in status is authoritative; directory ownership is a
    # fallback only when that field cannot be read. Revalidation below still
    # proves the PID generation before any owner is published.
    try:
        with open("/proc/%s/status" % pid, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                if line.startswith("Uid:"):
                    fields = line.split()
                    if len(fields) == 5 and all(value.isdigit() for value in fields[1:]):
                        uid = int(fields[2])
                    break
    except (FileNotFoundError, ProcessLookupError):
        return None
    except Exception:
        pass
    if uid is not None:
        try:
            result["user"] = pwd.getpwuid(uid).pw_name
        except KeyError:
            # A numeric UID is stable attribution even when NSS has no name.
            result["user"] = "uid:%s" % uid
        except Exception:
            result["user"] = "uid:%s" % uid
    try:
        with open("/proc/%s/comm" % pid, "r", encoding="utf-8", errors="replace") as f:
            result["process_name"] = os.path.basename(f.read().strip())[:128]
    except Exception:
        pass
    return result


def resolve_process_metadata(pid):
    # A short procfs permission/NSS race should not turn an otherwise stable
    # process into an unknown owner. Retry once, but only within the same PID
    # generation; never carry metadata across a changed start-time identity.
    first = None
    for attempt in range(2):
        metadata = process_metadata(pid)
        if metadata is not None:
            if first is None:
                first = metadata
            elif metadata.get("_proc_start_ticks") != first.get("_proc_start_ticks"):
                return None
            if metadata.get("user"):
                return metadata
        if attempt == 0:
            time.sleep(0.01)
    return first


owners = {}
for app in apps:
    metadata = resolve_process_metadata(app["pid"])
    if metadata is not None:
        owners[app["pid"]] = metadata

# A process can exit after the compute table was sampled but before procfs is
# read.  Reacquire the table once only in that race, then resolve any newly
# observed PIDs.  This adds no command to the normal collection path and never
# guesses an owner for a PID that cannot be observed directly.
missing_owner_pids = {
    app["pid"]
    for app in apps
    if app["pid"] not in owners or not owners[app["pid"]].get("user")
}
if missing_owner_pids:
    owner_retry_rc, owner_retry_out, _ = run(compute_query_command(), timeout=remote_cmd_timeout)
    if owner_retry_rc == 0:
        merge_compute_apps(parse_compute_apps(owner_retry_out))
        for app in apps:
            if owners.get(app["pid"], {}).get("user"):
                continue
            metadata = resolve_process_metadata(app["pid"])
            if metadata is not None:
                owners[app["pid"]] = metadata
if apps:
    pid_arg = ",".join(str(app["pid"]) for app in apps)
    rc, out, err = run(
        ["ps", "-o", "pid=,user=,lstart=,comm=", "-p", pid_arg],
        timeout=remote_cmd_timeout,
    )
    if rc == 0:
        for line in out.splitlines():
            parts = line.strip().split(None, 7)
            if len(parts) >= 7:
                try:
                    pid = int(parts[0])
                except Exception:
                    continue
                meta = owners.setdefault(pid, {"user": "", "started": "", "process_name": ""})
                meta["user"] = meta.get("user") or parts[1]
                meta["started"] = " ".join(parts[2:7])
                meta["process_name"] = (
                    meta.get("process_name")
                    or (os.path.basename(parts[7]) if len(parts) > 7 else "")
                )

# PID metadata and the NVIDIA compute table are separate observations. Verify
# every (PID, GPU UUID) pair once more after metadata resolution so a PID that
# exited and was immediately reused by an unrelated process cannot inherit the
# stale GPU application's ownership. The driver version rides on the primary
# GPU query, keeping the number of nvidia-smi calls unchanged.
verify_rc, verify_out, _ = run(compute_query_command(), timeout=remote_cmd_timeout) if gpus else (0, "", "")
if verify_rc == 0:
    verified_pairs = {
        (app["pid"], app["gpu_uuid"])
        for app in parse_compute_apps(verify_out)
    }
    apps = [app for app in apps if (app["pid"], app["gpu_uuid"]) in verified_pairs]
    live_pids = {app["pid"] for app in apps}
    owners = {
        pid: metadata
        for pid, metadata in owners.items()
        if pid in live_pids
        and metadata.get("_proc_start_ticks")
    }
    apps = [app for app in apps if app["pid"] in owners]
else:
    apps = []
    owners = {}

HEX_CHARS = set("0123456789abcdef")


def container_id_for_pid(pid):
    try:
        with open("/proc/%s/cgroup" % pid, "r", encoding="utf-8", errors="replace") as f:
            text = f.read()
    except Exception:
        return None
    for raw_token in text.replace(":", "/").split("/"):
        token = raw_token.strip()
        for prefix in ("docker-", "cri-containerd-"):
            if token.startswith(prefix):
                token = token[len(prefix):]
        if token.endswith(".scope"):
            token = token[:-6]
        if len(token) >= 12 and all(ch in HEX_CHARS for ch in token[:12].lower()):
            return token
    return None


def docker_container_name_map():
    rc, out, err = run(["docker", "ps", "-a", "--no-trunc", "--format", "{{.ID}} {{.Names}}"], timeout=max(6, remote_cmd_timeout))
    if rc != 0:
        return {}
    result = {}
    for line in out.splitlines():
        parts = line.strip().split(None, 1)
        if len(parts) == 2:
            result[parts[0]] = parts[1]
    return result


def container_name_for_id(container_id, mapping):
    if not container_id:
        return None
    if container_id in mapping:
        return mapping[container_id]
    for full_id, name in mapping.items():
        if full_id.startswith(container_id) or container_id.startswith(full_id):
            return name
    return None


container_names_by_pid = {}
if apps:
    ids_by_pid = {}
    for app in apps:
        container_id = container_id_for_pid(app["pid"])
        if container_id:
            ids_by_pid[app["pid"]] = container_id
    if ids_by_pid:
        name_map = docker_container_name_map()
        for pid, container_id in ids_by_pid.items():
            container_name = container_name_for_id(container_id, name_map)
            if container_name:
                container_names_by_pid[pid] = (container_id, container_name)

gpu_by_uuid = {gpu["uuid"]: gpu for gpu in gpus}
for app in apps:
    meta = owners.get(app["pid"], {})
    # nvidia-smi and procfs are separate observations. If the PID vanished
    # between them, omit the stale process rather than manufacture ownership.
    if not meta or not meta.get("user"):
        continue
    container_id, container_name = container_names_by_pid.get(app["pid"], ("", ""))
    # Unknown ownership remains empty at the data boundary.  The dashboard may
    # render a localized label, but a synthetic "?" must never become an event
    # owner or be persisted in a process snapshot.
    user = meta.get("user", "")
    if container_name and user in {"", "root"}:
        user = container_name
    process_name = os.path.basename(meta.get("process_name") or app.get("process_name", ""))[:128]
    command_summary = command_summary_for_pid(app["pid"], process_name)
    # Keep this as the last process observation before publication. Container
    # and command-summary lookups can take time; validating earlier would leave
    # a PID-reuse window in which stale ownership could still escape.
    if process_start_identity(app["pid"]) != meta.get("_proc_start_ticks"):
        continue
    app.update({
        "user": user,
        "started": meta.get("started", ""),
        "start_identity": meta.get("_proc_start_ticks", ""),
        "process_name": process_name,
        "command_summary": command_summary,
        "container_id": container_id,
        "container_name": container_name,
    })
    gpu = gpu_by_uuid.get(app["gpu_uuid"])
    if gpu is not None:
        gpu["processes"].append(app)

driver_version = ", ".join(sorted(driver_versions)) if driver_versions else None


"""

REMOTE_DISK_FUNCTIONS = r"""
STANDARD_USER_ROOTS = ["/home", "/home2", "/raid", "/raid2", "/nvme", "/data", "/data1", "/data2", "/data3", "/disk"]
SKIP_USER_DIRS = {"lost+found", ".snapshot", ".snapshots"}
SYSTEM_FS_TYPES = {"squashfs", "tmpfs", "devtmpfs", "efivarfs", "vfat", "overlay", "aufs", "fuse.overlayfs"}


def is_user_data_mount(mount, fstype):
    if not mount or mount == "/":
        return False
    if fstype in SYSTEM_FS_TYPES:
        return False
    if any(mount == root or mount.startswith(root + "/") for root in ("/boot", "/sys", "/proc", "/run", "/dev", "/snap")):
        return False
    return True


def is_capacity_filesystem(mount, fstype):
    if not mount:
        return False
    if fstype in SYSTEM_FS_TYPES:
        return False
    if mount == "/":
        return True
    if any(mount == root or mount.startswith(root + "/") for root in ("/boot", "/sys", "/proc", "/run", "/dev", "/snap", "/var/lib/docker/overlay2")):
        return False
    return True


def compact_error(text):
    lines = [line.strip() for line in str(text or "").splitlines() if line.strip()]
    if not lines:
        return ""
    denied = [line for line in lines if "Permission denied" in line or "허가 거부" in line]
    if denied:
        return "%s permission denied paths" % len(denied)
    return lines[0][:180]


def user_roots(filesystems):
    roots = []
    for root in STANDARD_USER_ROOTS:
        if os.path.isdir(root):
            roots.append(root)
    for fs in filesystems:
        mount = fs.get("mount")
        if is_user_data_mount(mount, fs.get("type")) and os.path.isdir(mount):
            roots.append(mount)
    seen = set()
    result = []
    for root in roots:
        root = root.rstrip("/") or "/"
        real_root = os.path.realpath(root)
        try:
            metadata = os.stat(root)
            identity = (metadata.st_dev, metadata.st_ino)
        except OSError:
            identity = real_root
        if identity not in seen:
            seen.add(identity)
            result.append(root)
    return result


def collect_disk():
    disk = {"filesystems": [], "users": [], "errors": []}
    # Discover Docker storage before du so relocated storage is never counted
    # as a user directory and then counted again through Docker's API. Reuse
    # this one lookup below; the normal disk command count does not increase.
    docker_root = None
    if include_docker:
        root_rc, root_out, _ = run(["docker", "info", "--format", "{{.DockerRootDir}}"], timeout=8)
        candidate = root_out.strip()
        if root_rc == 0 and candidate.startswith("/") and candidate != "/" and not any(c in candidate for c in "\n\r\0"):
            docker_root = candidate.rstrip("/")

    # Explicit homes may live below a generic root (for example
    # /tmp/alice -> /data/tmp/alice). Partition these subtrees before du:
    # a parent directory must not absorb the explicitly assigned user's bytes.
    explicit_paths = []
    explicit_real_paths = set()
    for entry in disk_user_paths if isinstance(disk_user_paths, list) else []:
        if isinstance(entry, str):
            path, user = entry, entry.rstrip("/").rsplit("/", 1)[-1]
        elif isinstance(entry, dict):
            path = str(entry.get("path") or "")
            user = str(entry.get("user") or "").strip() or path.rstrip("/").rsplit("/", 1)[-1]
        else:
            continue
        path = path.rstrip("/")
        if not path or not user:
            continue
        real = os.path.realpath(path)
        if real not in explicit_real_paths:
            explicit_real_paths.add(real)
            explicit_paths.append((user, path, real))

    def du_command(path, summary=False):
        # Only follow a command-line root symlink, matching realpath-based
        # deduplication. Descendant links remain untraversed and -x stays active.
        command = ["du", "-H", "-s", "-x", "-B1"] if summary else ["du", "-H", "-x", "-B1", "--max-depth=1"]
        real_path = os.path.realpath(path).rstrip("/")
        excluded_roots = [os.path.realpath(docker_root)] if docker_root else []
        excluded_roots.extend(real for _, _, real in explicit_paths
                              if not summary or real.startswith(real_path + "/"))
        for real_excluded in excluded_roots:
            excluded = None
            if real_excluded == real_path or real_path.startswith(real_excluded + "/"):
                excluded = path
            elif real_excluded.startswith(real_path + "/"):
                excluded = path.rstrip("/") + real_excluded[len(real_path):]
            if excluded:
                # GNU du --exclude is a glob even with shell=False. Escape
                # metacharacters so a literal storage path cannot hide siblings.
                pattern = "".join("[" + ch + "]" if ch in "*?[]" else ch * 2 if ch == chr(92) else ch for ch in excluded)
                command.append("--exclude=" + pattern)
        command.append(path)
        return command

    rc, out, err = run(["df", "-P", "-B1", "-T", "-x", "tmpfs", "-x", "devtmpfs"], timeout=8)
    scale = 1
    if rc != 0:
        rc, out, err = run(["df", "-P", "-k", "-T", "-x", "tmpfs", "-x", "devtmpfs"], timeout=8)
        scale = 1024
    if rc == 0:
        for line in out.splitlines()[1:]:
            parts = line.split(None, 6)
            if len(parts) < 7:
                continue
            filesystem, fstype, total, used, avail, percent, mount = parts
            if fstype in SYSTEM_FS_TYPES or filesystem.startswith("/dev/loop") or mount.startswith("/snap/"):
                continue
            total_i = to_int(total)
            used_i = to_int(used)
            avail_i = to_int(avail)
            disk["filesystems"].append({
                "filesystem": filesystem,
                "type": fstype,
                "mount": mount,
                "total_bytes": None if total_i is None else total_i * scale,
                "used_bytes": None if used_i is None else used_i * scale,
                "available_bytes": None if avail_i is None else avail_i * scale,
                "use_percent": to_int(percent.rstrip("%")),
            })
    else:
        disk["errors"].append(err or out or "df failed")

    du_seconds = max(1, int(du_timeout))
    roots = user_roots(disk["filesystems"])
    per_user = {}
    scanned_paths = []

    def add_user_location(user, path, size, complete=True):
        if not user or user in SKIP_USER_DIRS or str(user).startswith(".") or size is None:
            return
        item = per_user.setdefault(user, {
            "user": user,
            "bytes": 0,
            "locations": [],
        })
        normalized_path = path.rstrip("/") or "/"
        if any(loc.get("path") == normalized_path for loc in item["locations"]):
            return
        item["bytes"] += size
        item["locations"].append({"path": normalized_path, "bytes": size, "complete": bool(complete)})

    def parse_size_bytes(value):
        text = str(value or "").strip()
        if not text:
            return None
        text = text.split("(", 1)[0].strip()
        match = re.match(r"^([0-9]+(?:\.[0-9]+)?)\s*([kmgtpe]?i?b?)$", text, re.I)
        if not match:
            return None
        amount = float(match.group(1))
        unit = match.group(2).lower()
        factors = {
            "": 1, "b": 1,
            "k": 1000 ** 1, "kb": 1000 ** 1, "kib": 1024,
            "m": 1000 ** 2, "mb": 1000 ** 2, "mib": 1024 ** 2,
            "g": 1000 ** 3, "gb": 1000 ** 3, "gib": 1024 ** 3,
            "t": 1000 ** 4, "tb": 1000 ** 4, "tib": 1024 ** 4,
            "p": 1000 ** 5, "pb": 1000 ** 5, "pib": 1024 ** 5,
            "e": 1000 ** 6, "eb": 1000 ** 6, "eib": 1024 ** 6,
        }
        return int(amount * factors.get(unit, 1))

    def user_from_path(path):
        path = str(path or "").strip()
        if not path.startswith("/"):
            return None
        parts = [part for part in path.split("/") if part]
        if not parts:
            return None
        if parts[0] == "mnt" and len(parts) >= 3:
            return parts[2]
        if parts[0] in {"home", "home2", "data", "data1", "data2", "data3", "raid", "raid2", "nvme", "disk"} and len(parts) >= 2:
            return parts[1]
        if len(parts) >= 2 and parts[0] not in {"bin", "boot", "dev", "etc", "lib", "lib64", "media", "mnt", "opt", "proc", "root", "run", "sbin", "snap", "sys", "tmp", "usr", "var"}:
            return parts[0]
        return None

    def user_from_container(name, mounts):
        # A project/container name is not proof of a Unix account owner.
        candidates = set()
        for path in mounts:
            user = user_from_path(path)
            if user:
                try:
                    pwd.getpwnam(user)
                    candidates.add(user)
                except KeyError:
                    pass
        if len(candidates) == 1:
            return candidates.pop()
        name = str(name or "").strip().lstrip("/")
        if not candidates:
            try:
                pwd.getpwnam(name)
                return name
            except KeyError:
                pass
        return "docker:" + (name or "unassigned")

    def collect_docker_usage():
        docker_hint = os.path.exists("/var/run/docker.sock") or os.path.isdir("/var/lib/docker")
        rc, out, err = run(["docker", "ps", "-a", "--no-trunc", "--format", '{"ID":{{json .ID}},"Names":{{json .Names}}}'], timeout=docker_usage_timeout)
        if rc != 0:
            if docker_hint:
                disk["errors"].append("docker usage unavailable: %s" % compact_error(err or out or "docker ps failed"))
            return
        if not docker_root:
            disk["errors"].append("docker usage unavailable: storage root lookup failed")
            return
        containers = []
        ids = []
        for line in out.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                item = json.loads(line)
            except Exception:
                continue
            container_id = str(item.get("ID") or "").strip()
            if container_id:
                ids.append(container_id)
            containers.append(item)

        mounts_by_id = {}
        sizes_by_id = {}
        rc_i = 0
        if ids:
            # Request exact writable bytes and mounts only; never fetch Env,
            # command arguments, labels, or full container configuration.
            template = '{"Id":{{json .Id}},"Name":{{json .Name}},"SizeRw":{{json .SizeRw}},"Mounts":{{json .Mounts}}}'
            from concurrent.futures import ThreadPoolExecutor
            inspect_deadline = time.monotonic() + docker_usage_timeout
            def inspect_one(container_id):
                remaining = inspect_deadline - time.monotonic()
                if remaining <= 0:
                    return 124, "", "timeout"
                return run(["docker", "inspect", "--size", "--format", template, container_id], timeout=remaining)
            with ThreadPoolExecutor(max_workers=2) as pool:
                inspected_results = list(pool.map(inspect_one, ids))
            rc_i = next((code for code, _, _ in inspected_results if code != 0), 0)
            out_i = "\n".join(output for _, output, _ in inspected_results)
            err_i = next((error for code, _, error in inspected_results if code != 0), "")
            if out_i.strip():
                try:
                    inspected = []
                    for line in out_i.splitlines():
                        try:
                            inspected.append(json.loads(line))
                        except ValueError:
                            continue
                    for meta in inspected if isinstance(inspected, list) else []:
                        container_id = str(meta.get("Id") or "")[:12]
                        sizes_by_id[container_id] = meta.get("SizeRw")
                        mounts_by_id[container_id] = [
                            mount.get("Source") or ""
                            for mount in (meta.get("Mounts") or [])
                            if isinstance(mount, dict)
                        ]
                except Exception as exc:
                    disk["errors"].append("docker inspect parse failed: %s" % exc)
            if rc_i != 0:
                disk["errors"].append("docker inspect partial: %s" % compact_error(err_i or "incomplete inspection"))

        for item in containers:
            name = str(item.get("Names") or "").strip()
            container_id = str(item.get("ID") or "").strip()[:12]
            size = sizes_by_id.get(container_id)
            if type(size) is not int or size < 0:
                if rc_i == 0:
                    disk["errors"].append("docker inspect partial: writable size unavailable")
                continue
            mounts = mounts_by_id.get(container_id, [])
            user = user_from_container(name, mounts)
            if user:
                add_user_location(user, "docker:%s" % name, size)

        rc_df, out_df, err_df = run(["docker", "system", "df", "--format", "{{json .}}"], timeout=docker_usage_timeout)
        if rc_df != 0:
            if docker_hint:
                disk["errors"].append("docker system df unavailable: %s" % compact_error(err_df or out_df))
            return
        docker_type_names = {
            "Images": "docker:images",
            "Local Volumes": "docker:volumes",
            "Build Cache": "docker:build-cache",
        }
        for line in out_df.splitlines():
            try:
                item = json.loads(line)
            except Exception:
                continue
            user = docker_type_names.get(str(item.get("Type") or ""))
            if not user:
                continue
            size = parse_size_bytes(item.get("Size"))
            if size and size >= 1024 ** 3:
                add_user_location(user, docker_root, size)

    start = time.monotonic()
    def covered_path(path, locations):
        real = os.path.realpath(path)
        for other in locations:
            if real == other:
                return True
            if real.startswith(other.rstrip("/") + "/"):
                try:
                    if os.stat(real).st_dev == os.stat(other).st_dev:
                        return True
                except OSError:
                    pass
        return False

    roots.sort(key=lambda path: (path.count("/"), path))
    for index, root in enumerate(roots):
        if covered_path(root, scanned_paths):
            continue
        scanned_paths.append(os.path.realpath(root))
        remaining = du_seconds - (time.monotonic() - start)
        if remaining <= 1:
            disk["errors"].append("user usage partial: timeout before %s" % root)
            break
        roots_left = max(1, len(roots) - index)
        root_timeout = max(10, min(300, int(remaining / roots_left) + 15, int(remaining)))
        rc, out, err = run(du_command(root), timeout=root_timeout)
        parsed_root = 0
        for line in out.splitlines():
            parts = line.strip().split(None, 1)
            if len(parts) != 2:
                continue
            size = to_int(parts[0])
            path = parts[1].rstrip("/")
            if path == root.rstrip("/") or not path:
                continue
            user = path.rsplit("/", 1)[-1]
            add_user_location(user, path, size, complete=rc == 0)
            parsed_root += 1
        if rc == 124:
            disk["errors"].append("user usage partial: timeout at %s" % root)
        elif rc != 0:
            detail = compact_error(err or out)
            if parsed_root == 0:
                disk["errors"].append(detail or "du failed at %s" % root)
            elif detail:
                disk["errors"].append("user usage partial at %s: %s" % (root, detail))
    for user, path, real in explicit_paths:
        remaining = du_seconds - (time.monotonic() - start)
        if remaining <= 1:
            disk["errors"].append("user usage partial: timeout before %s" % path)
            break
        if not os.path.exists(path):
            disk["errors"].append("user usage path missing: %s" % path)
            continue
        rc, out, err = run(du_command(path, summary=True), timeout=max(5, min(120, int(remaining))))
        parsed = False
        for line in out.splitlines():
            parts = line.strip().split(None, 1)
            if len(parts) != 2:
                continue
            size = to_int(parts[0])
            add_user_location(user, path, size, complete=rc == 0)
            parsed = True
            break
        if rc == 124:
            disk["errors"].append("user usage partial: timeout at %s" % path)
        elif rc != 0 and not parsed:
            disk["errors"].append(err or out or "du failed at %s" % path)

    if include_docker:
        collect_docker_usage()

    capacity_filesystems = {}
    for fs in disk["filesystems"]:
        if is_capacity_filesystem(fs.get("mount"), fs.get("type")):
            capacity_filesystems.setdefault(fs.get("filesystem") or fs.get("mount"), fs)
    capacity_used = sum(fs.get("used_bytes") or 0 for fs in capacity_filesystems.values())
    attributed = sum(item.get("bytes") or 0 for item in per_user.values() if item["user"] != "docker:build-cache")
    unattributed = capacity_used - attributed
    if unattributed > 1024 ** 3:
        add_user_location("system/other", "df used not attributed to readable user or docker paths", unattributed, complete=False)

    for item in per_user.values():
        item["complete"] = all(loc.get("complete", True) for loc in item["locations"])
        item["locations"].sort(key=lambda loc: loc.get("bytes") or 0, reverse=True)
        item["path"] = item["locations"][0]["path"] if len(item["locations"]) == 1 else "%s locations" % len(item["locations"])
    disk["users"] = sorted(per_user.values(), key=lambda item: item.get("bytes") or 0, reverse=True)[:40]
    return disk


"""

REMOTE_GPU_PAYLOAD = r"""
rc, hostname, _ = run(["hostname"], timeout=max(3, min(remote_cmd_timeout, 8)))
payload = {
    "ok": True,
    "remote_time": time.time(),
    "hostname": hostname.strip(),
    "driver_version": driver_version,
    "gpus": gpus,
    "driver_query_source": "temporary" if gpu_env_prefix else "host",
}
if include_disk:
    payload["disk"] = collect_disk()
print(json.dumps(payload, separators=(",", ":")))
"""

# Encode once at startup rather than recompressing the same collector for every
# host on every ten-second cycle.
REMOTE_PROBE = (
    REMOTE_PROBE_COMMON + REMOTE_TEMPORARY_DRIVER_PROBE
    + REMOTE_GPU_PROBE + REMOTE_DISK_FUNCTIONS + REMOTE_GPU_PAYLOAD
)
REMOTE_PROBE_B64_ZLIB = base64.b64encode(
    zlib.compress(REMOTE_PROBE.encode("utf-8"), level=9)
).decode("ascii")

# Disk telemetry must survive a broken/missing/hanging NVIDIA driver. Reuse the
# same disk implementation and common command runner without executing GPU work.
REMOTE_DISK_PROBE = REMOTE_PROBE_COMMON + REMOTE_DISK_FUNCTIONS + r"""
disk = collect_disk()
capacity = [fs for fs in disk["filesystems"] if is_capacity_filesystem(fs.get("mount"), fs.get("type"))]
valid = bool(capacity) and all(
    type(fs.get(field)) is int and fs[field] >= 0
    for fs in capacity for field in ("total_bytes", "used_bytes", "available_bytes")
)
print(json.dumps(
    {"ok": True, "disk": disk} if valid else {"ok": False, "error": "disk capacity query failed"},
    separators=(",", ":")
))
"""
REMOTE_DISK_PROBE_B64_ZLIB = base64.b64encode(
    zlib.compress(REMOTE_DISK_PROBE.encode("utf-8"), level=9)
).decode("ascii")


def now_ts() -> float:
    return time.time()


def iso(ts: float | None) -> str | None:
    if ts is None:
        return None
    return datetime.fromtimestamp(ts, KST).isoformat(timespec="seconds")


def local_date_bounds(date_text: str | None) -> tuple[float, float] | None:
    if not date_text:
        return None
    try:
        start = datetime.strptime(date_text, "%Y-%m-%d").replace(tzinfo=KST)
    except ValueError:
        return None
    return start.timestamp(), start.timestamp() + 86400


def human_duration(seconds: float | None) -> str:
    if seconds is None:
        return "unknown"
    seconds = max(0, int(seconds))
    days, rem = divmod(seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, secs = divmod(rem, 60)
    if days:
        return f"{days}d {hours}h"
    if hours:
        return f"{hours}h {minutes}m"
    if minutes:
        return f"{minutes}m {secs}s"
    return f"{secs}s"


def merge_intervals(intervals: list[tuple[float, float]]) -> list[tuple[float, float]]:
    ordered = sorted(
        (float(start), float(end))
        for start, end in intervals
        if float(end) > float(start)
    )
    if not ordered:
        return []

    merged: list[tuple[float, float]] = []
    current_start, current_end = ordered[0]
    for start, end in ordered[1:]:
        if start <= current_end:
            current_end = max(current_end, end)
            continue
        merged.append((current_start, current_end))
        current_start, current_end = start, end
    merged.append((current_start, current_end))
    return merged


def merged_interval_seconds(intervals: list[tuple[float, float]]) -> float:
    """Return wall-clock seconds covered by one or more overlapping intervals."""
    return sum(end - start for start, end in merge_intervals(intervals))


def clean_text(value: Any, max_length: int) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return text[:max_length]


def safe_http_url(value: Any, fallback: Any = "") -> str:
    """Return a bounded public HTTP(S) URL, or a separately validated fallback."""
    for candidate in (value, fallback):
        text = clean_text(candidate, 500)
        try:
            parsed = urlparse(text)
        except ValueError:
            continue
        if (
            parsed.scheme in {"http", "https"}
            and parsed.netloc
            and parsed.username is None
            and parsed.password is None
        ):
            return text
    return ""


def clean_multiline_text(value: Any, max_length: int) -> str:
    text = str(value or "").replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text[:max_length]


def safe_probe_error(value: Any, max_length: int = 300) -> str:
    """Bound remote diagnostics and redact common credential assignments."""
    text = redact_sensitive_text(value, max_length)
    # OpenSSH may echo the configured private-key filename in diagnostics.
    # Preserve the error class while keeping host filesystem layout private.
    text = re.sub(
        r"(?i)((?:identity file|load key|load pubkey)\s+).*$",
        r"\1[redacted-path]",
        text,
    )
    text = re.sub(
        r"(?i)(permissions \d+ for\s+).*$",
        r"\1[redacted-path]",
        text,
    )
    if re.search(r"(?i)private key", text):
        text = re.sub(
            r"([\"'])(?:[A-Za-z]:[\\/]|/).*?\1",
            r"\1[redacted-path]\1",
            text,
        )
        text = re.sub(
            r"(?<![A-Za-z0-9])(?:[A-Za-z]:[\\/]|/)[^\s:,\"']+",
            "[redacted-path]",
            text,
        )
    text = re.sub(r"\b[A-Za-z0-9+/]{128,}={0,2}\b", "[redacted-command]", text)
    return text[:max_length]


def sanitize_gpu_errors(value: Any) -> dict[int, str] | None:
    """Validate per-index probe failures without trusting remote JSON keys."""
    if value is None:
        return {}
    if not isinstance(value, dict) or len(value) > MAX_GPUS_PER_HOST:
        return None
    result: dict[int, str] = {}
    for raw_index, raw_error in value.items():
        if isinstance(raw_index, bool):
            return None
        try:
            index = int(raw_index)
        except (TypeError, ValueError):
            return None
        if str(raw_index).strip() != str(index) or not 0 <= index < MAX_GPUS_PER_HOST:
            return None
        error = safe_probe_error(raw_error) or "nvidia-smi failed"
        result[index] = error
    return result


def host_gpu_view(gpus, runtime, expected_count):
    """One host status applies to its entire inventory, including stale rows."""
    online = gpu_is_observed(runtime, 0)
    result = {int(gpu["index"]): dict(gpu) for gpu in gpus}
    for index in range(expected_count):
        result.setdefault(index, {"index": index, "name": "GPU", "uuid": None,
                                  "memory_total": None, "last_seen": None})
    for gpu in result.values():
        gpu["available"] = online
        if not online:
            gpu.update(busy=None, utilization=None, memory_used=None, temperature=None,
                       processes=[], last_error=runtime.get("last_error") or "연결 실패")
    return [result[index] for index in sorted(result)]


def disk_collection_status(row, ts, interval):
    if not row:
        return {"disk_error": None, "disk_stale": False}
    stale = row.get("updated_ts") is not None and ts - row["updated_ts"] > max(120, float(interval) * 2)
    return {"disk_error": row.get("last_error"), "disk_stale": bool(stale or row.get("last_error"))}


def gpu_is_observed(runtime: dict[str, Any], gpu_index: int) -> bool:
    """A failed host contributes no live observation tail for any GPU."""
    return bool(runtime.get("online")) and not runtime.get("consecutive_failures")


def verify_pin(pin: str | None, encoded: str | None) -> bool:
    with AUTH_WORK_SEMAPHORE:
        return verify_passphrase(pin, encoded)


def bounded_hash_passphrase(passphrase: str) -> str:
    with AUTH_WORK_SEMAPHORE:
        return hash_passphrase(passphrase)


def client_network_scope(address: Any) -> str:
    try:
        ip = ipaddress.ip_address(str(address or "").split("%", 1)[0])
    except ValueError:
        return "unknown"
    prefix = 24 if ip.version == 4 else 64
    return str(ipaddress.ip_network(f"{ip}/{prefix}", strict=False))


def load_admin_pin_hash() -> str | None:
    env_hash = os.environ.get("GPU_WATCH_ADMIN_PIN_HASH")
    if env_hash:
        return env_hash.strip()
    try:
        return ADMIN_PIN_HASH_PATH.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return None


def persist_admin_passphrase_hash(passphrase: str) -> str:
    if not is_valid_pin(passphrase):
        raise ValueError("관리자 PIN은 숫자 4자리여야 합니다.")
    encoded = bounded_hash_passphrase(passphrase)
    ADMIN_PIN_HASH_PATH.parent.mkdir(parents=True, exist_ok=True)
    temporary = ADMIN_PIN_HASH_PATH.with_name(
        f".{ADMIN_PIN_HASH_PATH.name}.{os.getpid()}.{threading.get_ident()}.tmp"
    )
    try:
        temporary.write_text(encoded + "\n", encoding="utf-8")
        try:
            temporary.chmod(0o600)
        except OSError:
            pass
        os.replace(temporary, ADMIN_PIN_HASH_PATH)
    finally:
        temporary.unlink(missing_ok=True)
    return encoded


def resolve_local_path(value: str) -> Path:
    path = Path(value).expanduser()
    if path.is_absolute():
        return path
    return ROOT / path


def standalone_database_uri(path: Path) -> str:
    """Open a completed SQLite artifact without creating WAL/SHM sidecars."""
    return path.resolve().as_uri() + "?mode=ro&immutable=1"


def remove_sqlite_sidecars(path: Path) -> None:
    """Remove sidecars only after a standalone database has no open writer."""
    for suffix in ("-wal", "-shm"):
        Path(f"{path}{suffix}").unlink(missing_ok=True)


def read_password_file(value: str | None) -> str | None:
    if not value:
        return None
    path = resolve_local_path(value)
    descriptor: int | None = None
    try:
        if stat.S_ISLNK(path.lstat().st_mode):
            return None
        flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(path, flags)
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_size <= 0 or metadata.st_size > 4096:
            return None
        if os.name == "posix" and (
            metadata.st_uid != os.geteuid() or stat.S_IMODE(metadata.st_mode) & 0o077
        ):
            return None
        raw = os.read(descriptor, 4097)
        if len(raw) > 4096:
            return None
        text = raw.decode("utf-8").strip()
        if not text or "\x00" in text or "\n" in text or "\r" in text:
            return None
        return text
    except (FileNotFoundError, IsADirectoryError, PermissionError, OSError, UnicodeDecodeError):
        return None
    finally:
        if descriptor is not None:
            os.close(descriptor)


def parse_local_datetime(value: Any) -> float | None:
    text = str(value or "").strip()
    if not text:
        return None
    for fmt in ("%Y-%m-%dT%H:%M", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            dt = datetime.strptime(text, fmt).replace(tzinfo=KST)
            if fmt == "%Y-%m-%d":
                dt = dt.replace(hour=23, minute=59, second=59)
            return dt.timestamp()
        except ValueError:
            continue
    return None


def parse_iso_datetime(value: Any) -> float | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=KST)
    return parsed.timestamp()


def split_user_text(user_text: str | None) -> list[str]:
    users = []
    seen = set()
    for part in str(user_text or "").split(","):
        user = part.strip()
        if not user or user == "?" or user in seen:
            continue
        seen.add(user)
        users.append(user)
    return users


def normalize_user_text(user_text: str | None) -> str | None:
    users = split_user_text(user_text)
    return ", ".join(users) if users else None


def same_process_cohort(previous: Any, current: Any) -> bool:
    """Prove session continuity without treating PID reuse as the same job."""
    if not isinstance(previous, list) or not isinstance(current, list) or not previous or not current:
        return False

    def identities(processes: list[Any]) -> dict[int, tuple[str, str]] | None:
        result: dict[int, tuple[str, str]] = {}
        for process in processes:
            if not isinstance(process, dict):
                return None
            try:
                pid = int(process.get("pid"))
            except (TypeError, ValueError):
                return None
            if pid <= 0 or pid in result:
                return None
            result[pid] = (
                clean_text(process.get("started"), 64),
                clean_text(process.get("start_identity"), 32),
            )
        return result

    previous_identities = identities(previous)
    current_identities = identities(current)
    if not previous_identities or not current_identities:
        return False
    if previous_identities.keys() != current_identities.keys():
        return False
    for pid in previous_identities:
        previous_started, previous_generation = previous_identities[pid]
        current_started, current_generation = current_identities[pid]
        if not previous_started or previous_started != current_started:
            return False
        # Once either sample carries the stronger Linux identity, fail closed
        # unless both samples prove the exact same PID generation.
        if (previous_generation or current_generation) and (
            not previous_generation
            or not current_generation
            or previous_generation != current_generation
        ):
            return False
    return True


def confirmed_process_users(processes: Any) -> list[str]:
    if not isinstance(processes, list):
        return []
    return sorted({
        user
        for process in processes
        if isinstance(process, dict)
        and (user := clean_text(process.get("user"), 64))
        and user != "?"
    })


def carry_verified_process_owners(previous: Any, current: Any) -> bool:
    """Carry each proven PID generation independently as sibling jobs change."""
    if not isinstance(previous, list) or not isinstance(current, list):
        return False

    def unique_processes(processes: list[Any]) -> dict[int, dict[str, Any]]:
        result: dict[int, dict[str, Any]] = {}
        ambiguous: set[int] = set()
        for process in processes:
            if not isinstance(process, dict):
                continue
            pid = process.get("pid")
            if type(pid) is not int or pid <= 0:
                continue
            if pid in result:
                ambiguous.add(pid)
            result[pid] = process
        return {pid: process for pid, process in result.items() if pid not in ambiguous}

    previous_by_pid = unique_processes(previous)
    changed = False
    for pid, process in unique_processes(current).items():
        old = previous_by_pid.get(pid)
        if old is None or not same_process_cohort([old], [process]):
            continue
        current_user = clean_text(process.get("user"), 64)
        previous_user = clean_text(old.get("user"), 64)
        if current_user in ("", "?") and previous_user not in ("", "?"):
            process["user"] = previous_user
            changed = True
    return changed


def parse_user_seconds(value: str | None) -> dict[str, float]:
    try:
        raw = json.loads(value or "{}")
    except Exception:
        return {}
    if not isinstance(raw, dict):
        return {}
    result: dict[str, float] = {}
    for user, seconds in raw.items():
        if not isinstance(user, str) or not user.strip() or user.strip() == "?":
            continue
        try:
            result[user] = max(0.0, float(seconds))
        except (TypeError, ValueError):
            continue
    return result


def add_user_seconds(target: dict[str, float], users: list[str], seconds: float) -> None:
    if seconds <= 0 or not users:
        return
    share = seconds / len(users)
    for user in users:
        target[user] = target.get(user, 0.0) + share


def user_share_rows(
    user_seconds: dict[str, float],
    total_seconds: float,
    limit: int,
    include_other: bool = False,
) -> list[dict[str, Any]]:
    items = sorted(
        ((user, seconds) for user, seconds in user_seconds.items() if seconds > 0),
        key=lambda item: item[1],
        reverse=True,
    )
    if include_other and len(items) > limit:
        other_seconds = sum(seconds for _, seconds in items[limit:])
        items = items[:limit] + [("기타", other_seconds)]
    else:
        items = items[:limit]
    return [
        {
            "user": user,
            "seconds": seconds,
            "share_percent": None if total_seconds <= 0 else round((seconds / total_seconds) * 100),
        }
        for user, seconds in items
    ]


def recent_user_share_rows(
    user_seconds: dict[str, float],
    reportable_unassigned_seconds: float,
    limit: int = 5,
) -> list[dict[str, Any]]:
    """Build recent user shares without promoting short unattributed blips."""
    display_seconds = dict(user_seconds)
    known_seconds = sum(display_seconds.values())
    unassigned_seconds = max(0.0, float(reportable_unassigned_seconds))
    total_seconds = known_seconds + unassigned_seconds
    if unassigned_seconds > 0.5:
        display_seconds["사용자 미상"] = (
            display_seconds.get("사용자 미상", 0.0) + unassigned_seconds
        )
    else:
        total_seconds = known_seconds
    return user_share_rows(display_seconds, total_seconds, limit, include_other=True)


def _quarter_vram_usage(
    rows: list[dict[str, Any]],
    quarter_start: float,
    quarter_end: float,
    total_gpu_gb: float,
) -> tuple[float | None, float]:
    observed_capacity = 0.0
    resource_hours_by_gpu: dict[tuple[str, int], float] = {}
    observed_hours_by_gpu: dict[tuple[str, int], float] = {}
    for row in rows:
        overlap = max(
            0.0,
            min(quarter_end, float(row["end_ts"])) - max(quarter_start, float(row["start_ts"])),
        )
        if overlap <= 0 or row.get("memory_used_mib") is None or row.get("memory_total_mib") is None:
            continue
        memory_total_gb = max(0.0, float(row["memory_total_mib"])) / 1024
        memory_used_gb = min(memory_total_gb, max(0.0, float(row["memory_used_mib"])) / 1024)
        gpu_key = (str(row["host"]), int(row["gpu_index"]))
        observed_hours = overlap / 3600
        observed_capacity += memory_total_gb * observed_hours
        resource_hours_by_gpu[gpu_key] = resource_hours_by_gpu.get(gpu_key, 0.0) + memory_used_gb * observed_hours
        observed_hours_by_gpu[gpu_key] = observed_hours_by_gpu.get(gpu_key, 0.0) + observed_hours
    resource_index = sum(
        resource_hours_by_gpu[gpu_key] / observed_hours
        for gpu_key, observed_hours in observed_hours_by_gpu.items()
        if observed_hours > 0
    )
    value = None if observed_capacity <= 0 else min(total_gpu_gb, resource_index)
    return value, observed_capacity


def _hourly_vram_pulse(
    rows: list[dict[str, Any]],
    total_gpu_gb: float,
    window_start: float,
    pulse_hours: int,
    bucket_seconds: float,
) -> list[dict[str, Any]]:
    hourly = []
    quarter_seconds = bucket_seconds / 4
    for offset in range(pulse_hours):
        hour_start = window_start + (offset * bucket_seconds)
        quarter_values: list[float | None] = []
        observed_capacity = 0.0
        for quarter in range(4):
            quarter_start = hour_start + quarter * quarter_seconds
            value, capacity = _quarter_vram_usage(
                rows, quarter_start, quarter_start + quarter_seconds, total_gpu_gb
            )
            quarter_values.append(value)
            observed_capacity += capacity
        candle_values = [float(value) for value in quarter_values if value is not None]
        resource_index = None if not candle_values else sum(candle_values) / len(candle_values)
        hourly.append({
            "label": datetime.fromtimestamp(hour_start, KST).strftime("%m-%d %H:%M"),
            "index": None if resource_index is None else round(resource_index, 2),
            "open": None if not candle_values else round(candle_values[0], 2),
            "high": None if not candle_values else round(max(candle_values), 2),
            "low": None if not candle_values else round(min(candle_values), 2),
            "close": None if not candle_values else round(candle_values[-1], 2),
            "observed_capacity_hours": round(observed_capacity, 2),
        })
    return hourly


def lab_id_for(host: dict[str, Any]) -> str:
    return str(host.get("lab") or "default")


def host_names_for_lab(config: dict[str, Any], lab_id: str | None) -> list[str] | None:
    if not lab_id:
        return None
    return [host["name"] for host in config["hosts"] if lab_id_for(host) == lab_id]


def ssh_target_for(host: dict[str, Any]) -> str:
    if host.get("ssh_target"):
        return str(host["ssh_target"])
    prefix = os.environ.get("GPU_WATCH_SSH_TARGET_PREFIX", "")
    if prefix and lab_id_for(host) == "nlp":
        return f"{prefix}{host['name']}"
    if host.get("ssh_host"):
        user = str(host.get("ssh_user") or "").strip()
        ssh_host = str(host["ssh_host"]).strip()
        return f"{user}@{ssh_host}" if user else ssh_host
    return f"{prefix}{host['name']}"


def host_ip_for_display(host: dict[str, Any]) -> str | None:
    """Expose only a validated address, never an SSH alias or connection options."""
    for key in ("display_ip", "ssh_host"):
        value = host.get(key)
        if not isinstance(value, str):
            continue
        try:
            return str(ipaddress.ip_address(value.strip()))
        except ValueError:
            continue
    return None


def trusted_ssh_executable() -> str:
    """Resolve OpenSSH only from an OS-owned absolute location."""
    if os.name == "nt":
        import ctypes

        buffer = ctypes.create_unicode_buffer(32768)
        length = int(ctypes.windll.kernel32.GetSystemDirectoryW(buffer, len(buffer)))
        if length <= 0 or length >= len(buffer):
            raise FileNotFoundError("Windows system directory is unavailable")
        candidates = [Path(buffer.value) / "OpenSSH" / "ssh.exe"]
    else:
        candidates = [Path("/usr/bin/ssh"), Path("/bin/ssh")]
    for candidate in candidates:
        if candidate.is_absolute() and candidate.is_file() and (os.name == "nt" or os.access(candidate, os.X_OK)):
            return str(candidate)
    raise FileNotFoundError("trusted OpenSSH client was not found")


class Store:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.lock = threading.Lock()
        self._cache_lock = threading.Lock()
        self._insights_cache_ts = 0.0
        self._insights_cache: dict[str, Any] | None = None
        self._init_db()

    def prepare(self, config: dict[str, Any]) -> None:
        """Run one-time data preparation before the HTTP read path is exposed."""
        ts = now_ts()
        with self.lock, closing(self.connect()) as conn, conn:
            completed = conn.execute(
                "select value from maintenance_meta where key=?",
                (RECENT_USAGE_BACKFILL_KEY,),
            ).fetchone()
            if completed and completed["value"] == "complete":
                return
            gpu_rows = conn.execute("select * from gpu_runtime").fetchall()
            self._backfill_recent_usage_intervals(conn, gpu_rows, ts, config)
            conn.execute(
                "insert into maintenance_meta(key, value) values(?, 'complete') "
                "on conflict(key) do update set value=excluded.value",
                (RECENT_USAGE_BACKFILL_KEY,),
            )

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("pragma busy_timeout=10000")
        conn.execute("pragma foreign_keys=on")
        # synchronous is connection-local; setting it only during schema
        # initialization silently leaves every runtime write at FULL.
        conn.execute("pragma synchronous=NORMAL")
        return conn

    def _init_db(self) -> None:
        with closing(self.connect()) as conn, conn:
            schema_table = conn.execute(
                "select 1 from sqlite_master where type='table' and name='schema_meta'"
            ).fetchone()
            if schema_table is not None:
                schema_row = conn.execute(
                    "select value from schema_meta where key='schema_version'"
                ).fetchone()
                try:
                    existing_schema_version = int(schema_row["value"]) if schema_row else 0
                except (TypeError, ValueError):
                    existing_schema_version = 0
                if not 1 <= existing_schema_version <= SCHEMA_VERSION:
                    raise RuntimeError(
                        "unsupported GPU Watch database schema version: "
                        f"{existing_schema_version} (supported: 1..{SCHEMA_VERSION})"
                    )
            conn.execute("pragma journal_mode=WAL")
            conn.execute("pragma synchronous=NORMAL")
            conn.executescript(
                """
                create table if not exists gpu_runtime (
                    host text not null,
                    gpu_index integer not null,
                    gpu_uuid text,
                    gpu_name text,
                    active integer not null default 1,
                    busy integer not null default 0,
                    first_seen_ts real,
                    last_seen_ts real,
                    last_transition_ts real,
                    free_since_ts real,
                    busy_since_ts real,
                    last_user text,
                    last_used_ts real,
                    last_processes_json text,
                    last_snapshot_json text,
                    observed_seconds real not null default 0,
                    busy_seconds real not null default 0,
                    user_busy_json text not null default '{}',
                    primary key (host, gpu_index)
                );
                create table if not exists host_runtime (
                    host text primary key,
                    label text,
                    online integer not null default 0,
                    hostname text,
                    driver_version text,
                    last_seen_ts real,
                    last_error_ts real,
                    last_error text,
                    gpu_errors_json text not null default '{}',
                    consecutive_failures integer not null default 0
                );
                create table if not exists events (
                    id integer primary key autoincrement,
                    ts real not null,
                    host text not null,
                    gpu_index integer,
                    event text not null,
                    users text,
                    processes_json text,
                    note text
                );
                create index if not exists events_ts_idx on events(ts desc, id desc);
                create index if not exists events_host_gpu_ts_idx on events(host, gpu_index, ts);
                create table if not exists gpu_usage_interval (
                    id integer primary key autoincrement,
                    host text not null,
                    gpu_index integer not null,
                    start_ts real not null,
                    end_ts real not null,
                    users text,
                    seconds real not null
                );
                create index if not exists gpu_usage_interval_window_idx on gpu_usage_interval(end_ts, start_ts);
                create index if not exists gpu_usage_interval_host_gpu_idx on gpu_usage_interval(host, gpu_index, end_ts);
                create table if not exists gpu_capacity_interval (
                    id integer primary key autoincrement,
                    host text not null,
                    gpu_index integer not null,
                    start_ts real not null,
                    end_ts real not null,
                    busy integer not null,
                    memory_used_mib real,
                    memory_total_mib real,
                    seconds real not null
                );
                create index if not exists gpu_capacity_interval_window_idx on gpu_capacity_interval(end_ts, start_ts);
                create index if not exists gpu_capacity_interval_host_gpu_idx on gpu_capacity_interval(host, gpu_index, end_ts);
                create table if not exists lab_daily_index (
                    lab_id text not null,
                    day text not null,
                    average_used_gb real not null,
                    observed_seconds real not null,
                    complete integer not null default 0,
                    updated_ts real not null,
                    primary key (lab_id, day)
                );
                create index if not exists lab_daily_index_day_idx on lab_daily_index(day);
                create table if not exists host_disk (
                    host text primary key,
                    updated_ts real not null,
                    snapshot_json text not null
                );
                create table if not exists announcements (
                    id integer primary key autoincrement,
                    created_ts real not null,
                    expires_ts real not null,
                    permanent integer not null default 0,
                    author text not null,
                    message text not null,
                    pin_hash text not null,
                    updated_ts real,
                    deleted_ts real,
                    deleted_by text
                );
                create index if not exists announcements_active_idx on announcements(deleted_ts, expires_ts, created_ts);
                create table if not exists dashboard_settings (
                    key text primary key,
                    value text not null,
                    updated_ts real not null
                );
                create table if not exists maintenance_meta (
                    key text primary key,
                    value text not null
                );
                create table if not exists schema_meta (
                    key text primary key,
                    value text not null,
                    updated_ts real not null
                );
                """
            )
            columns = {row["name"] for row in conn.execute("pragma table_info(host_runtime)")}
            if "driver_version" not in columns:
                conn.execute("alter table host_runtime add column driver_version text")
            if "consecutive_failures" not in columns:
                conn.execute("alter table host_runtime add column consecutive_failures integer not null default 0")
            if "gpu_errors_json" not in columns:
                conn.execute("alter table host_runtime add column gpu_errors_json text not null default '{}'")
            if "usable_gpu_indices_json" not in columns:
                conn.execute("alter table host_runtime add column usable_gpu_indices_json text")
            if "gpu_health_json" not in columns:
                conn.execute("alter table host_runtime add column gpu_health_json text not null default '{}'")
            migrate_host_availability(conn)
            disk_columns = {row["name"] for row in conn.execute("pragma table_info(host_disk)")}
            if "last_attempt_ts" not in disk_columns:
                conn.execute("alter table host_disk add column last_attempt_ts real")
            if "last_error" not in disk_columns:
                conn.execute("alter table host_disk add column last_error text")
            gpu_columns = {row["name"] for row in conn.execute("pragma table_info(gpu_runtime)")}
            added_usage_columns = False
            if "gpu_uuid" not in gpu_columns:
                conn.execute("alter table gpu_runtime add column gpu_uuid text")
            if "active" not in gpu_columns:
                conn.execute("alter table gpu_runtime add column active integer not null default 1")
            if "observed_seconds" not in gpu_columns:
                conn.execute("alter table gpu_runtime add column observed_seconds real not null default 0")
                added_usage_columns = True
            if "busy_seconds" not in gpu_columns:
                conn.execute("alter table gpu_runtime add column busy_seconds real not null default 0")
                added_usage_columns = True
            if "user_busy_json" not in gpu_columns:
                conn.execute("alter table gpu_runtime add column user_busy_json text not null default '{}'")
            announcement_columns = {row["name"] for row in conn.execute("pragma table_info(announcements)")}
            if "updated_ts" not in announcement_columns:
                conn.execute("alter table announcements add column updated_ts real")
            if "permanent" not in announcement_columns:
                conn.execute("alter table announcements add column permanent integer not null default 0")
            conn.execute(
                "update announcements set updated_ts=created_ts where updated_ts is null"
            )
            capacity_columns = {row["name"] for row in conn.execute("pragma table_info(gpu_capacity_interval)")}
            if "memory_used_mib" not in capacity_columns:
                conn.execute("alter table gpu_capacity_interval add column memory_used_mib real")
            if "memory_total_mib" not in capacity_columns:
                conn.execute("alter table gpu_capacity_interval add column memory_total_mib real")
                added_usage_columns = True
            if added_usage_columns:
                self._backfill_usage_stats(conn)
            self._scrub_process_payloads(conn)
            self._scrub_host_errors(conn)
            self._migrate_event_usage_v1_0_1(conn)
            self._migrate_conference_deadline_palette(conn)
            self._prune_obsolete_metadata(conn)
            conn.execute(
                "insert into schema_meta(key, value, updated_ts) values('schema_version', ?, ?) "
                "on conflict(key) do update set value=excluded.value, updated_ts=excluded.updated_ts",
                (str(SCHEMA_VERSION), now_ts()),
            )

    def _migrate_conference_deadline_palette(self, conn: sqlite3.Connection) -> None:
        """Assign the six-tone palette once, then keep tones ID-stable."""
        completed = conn.execute(
            "select value from maintenance_meta where key=?",
            (CONFERENCE_DEADLINE_PALETTE_MIGRATION_KEY,),
        ).fetchone()
        if completed and completed["value"] == "complete":
            return

        migration_complete = False
        row = conn.execute(
            "select value, updated_ts from dashboard_settings where key=?",
            (CONFERENCE_DEADLINES_KEY,),
        ).fetchone()
        if row is not None:
            try:
                stored = json.loads(row["value"])
            except (TypeError, ValueError, json.JSONDecodeError):
                stored = None
            if isinstance(stored, list):
                deadlines: list[dict[str, Any]] = []
                used_ids: set[str] = set()
                for index, candidate in enumerate(stored[:MAX_CONFERENCE_DEADLINES]):
                    fields = self._deadline_fields(candidate, updated_ts=float(row["updated_ts"]))
                    if fields is None:
                        continue
                    raw_id = clean_text(candidate.get("id"), 32) if isinstance(candidate, dict) else ""
                    if not CONFERENCE_DEADLINE_ID_RE.fullmatch(raw_id) or raw_id in used_ids:
                        raw_id = f"slot-{index + 1}"
                        while raw_id in used_ids:
                            raw_id += "-next"
                    fields["id"] = raw_id
                    deadlines.append(fields)
                    used_ids.add(raw_id)
                # An explicit [] means the operator intentionally removed all
                # timers. A non-empty list that sanitizes to nothing is corrupt
                # input, so retain/recover the legacy value instead of erasing it.
                if not stored or deadlines:
                    deadlines = self._sort_deadlines(deadlines)
                    for index, deadline in enumerate(deadlines):
                        deadline["tone"] = CONFERENCE_DEADLINE_TONES[index]
                    self._persist_deadlines(conn, deadlines, now_ts())
                    migration_complete = True

        if not migration_complete and row is not None:
            legacy_row = conn.execute(
                "select value, updated_ts from dashboard_settings where key=?",
                (LEGACY_CONFERENCE_DEADLINE_KEY,),
            ).fetchone()
            if legacy_row is not None:
                try:
                    legacy_candidate = json.loads(legacy_row["value"])
                except (TypeError, ValueError, json.JSONDecodeError):
                    legacy_candidate = None
                fields = self._deadline_fields(
                    legacy_candidate,
                    updated_ts=float(legacy_row["updated_ts"]),
                )
                if fields is not None:
                    fields.update({"id": "primary", "tone": "silver"})
                    self._persist_deadlines(conn, [fields], now_ts())
                    migration_complete = True

        # A fresh database has nothing to migrate. A corrupt current value is
        # deliberately left retryable so a still-valid legacy row is never
        # discarded merely because the replacement key exists.
        if row is None:
            migration_complete = True

        if migration_complete:
            conn.execute(
                "insert into maintenance_meta(key, value) values(?, 'complete') "
                "on conflict(key) do update set value=excluded.value",
                (CONFERENCE_DEADLINE_PALETTE_MIGRATION_KEY,),
            )

    @staticmethod
    def _prune_obsolete_metadata(conn: sqlite3.Connection) -> None:
        """Remove superseded markers only after their current replacements exist."""
        for key in OBSOLETE_MAINTENANCE_KEYS:
            conn.execute("delete from maintenance_meta where key=?", (key,))
        current_deadlines = conn.execute(
            "select value from dashboard_settings where key=?",
            (CONFERENCE_DEADLINES_KEY,),
        ).fetchone()
        palette_migration = conn.execute(
            "select value from maintenance_meta where key=?",
            (CONFERENCE_DEADLINE_PALETTE_MIGRATION_KEY,),
        ).fetchone()
        current_value_is_valid = False
        if current_deadlines is not None:
            try:
                current_value_is_valid = isinstance(json.loads(current_deadlines["value"]), list)
            except (TypeError, ValueError, json.JSONDecodeError):
                pass
        if (
            current_value_is_valid
            and palette_migration is not None
            and palette_migration["value"] == "complete"
        ):
            conn.execute(
                "delete from dashboard_settings where key=?",
                (LEGACY_CONFERENCE_DEADLINE_KEY,),
            )

    def _scrub_process_payloads(self, conn: sqlite3.Connection) -> None:
        """One-way v1.0 migration: discard historic argv and retain safe live metadata."""
        conn.execute("update events set processes_json=null where processes_json is not null")
        rows = conn.execute(
            "select host, gpu_index, last_snapshot_json, last_processes_json from gpu_runtime"
        ).fetchall()
        for row in rows:
            try:
                snapshot = json.loads(row["last_snapshot_json"] or "{}")
            except Exception:
                snapshot = {}
            if not isinstance(snapshot, dict):
                snapshot = {}
            try:
                current = json.loads(row["last_processes_json"] or "[]")
            except Exception:
                current = []
            if not isinstance(current, list):
                current = snapshot.get("processes")
            if not isinstance(current, list):
                current = []
            processes = sanitize_processes(current)
            snapshot["processes"] = [
                {key: value for key, value in process.items() if key != "start_identity"}
                for process in processes
            ]
            conn.execute(
                "update gpu_runtime set last_processes_json=?, last_snapshot_json=? where host=? and gpu_index=?",
                (
                    json.dumps(processes, ensure_ascii=False, separators=(",", ":")),
                    json.dumps(snapshot, ensure_ascii=False, separators=(",", ":")),
                    row["host"],
                    row["gpu_index"],
                ),
            )

    def _scrub_host_errors(self, conn: sqlite3.Connection) -> int:
        """Re-sanitize stored diagnostics so stronger redaction is retroactive."""
        changed = 0
        rows = conn.execute(
            "select host, last_error from host_runtime where last_error is not null"
        ).fetchall()
        for row in rows:
            sanitized = safe_probe_error(row["last_error"])
            if sanitized == row["last_error"]:
                continue
            conn.execute(
                "update host_runtime set last_error=? where host=?",
                (sanitized, row["host"]),
            )
            changed += 1
        return changed

    def _migrate_event_usage_v1_0_1(self, conn: sqlite3.Connection) -> None:
        """Repair legacy interval overlap and remove unresolved event owners once."""
        completed = conn.execute(
            "select value from maintenance_meta where key=?",
            (EVENT_USAGE_MIGRATION_KEY,),
        ).fetchone()
        if completed and completed["value"] == "complete":
            return

        self._repair_usage_intervals_v1_0_1(conn)
        self._scrub_event_users_v1_0_1(conn)
        conn.execute("delete from maintenance_meta where key=?", (RECENT_USAGE_BACKFILL_KEY,))
        conn.execute(
            "insert into maintenance_meta(key, value) values(?, 'complete') "
            "on conflict(key) do update set value=excluded.value",
            (EVENT_USAGE_MIGRATION_KEY,),
        )

    def _repair_usage_intervals_v1_0_1(self, conn: sqlite3.Connection) -> None:
        """Make per-GPU usage intervals non-overlapping and mask known unobserved time."""
        keys = conn.execute(
            "select distinct host, gpu_index from gpu_usage_interval order by host, gpu_index"
        ).fetchall()
        repaired: list[tuple[str, int, float, float, str | None, float]] = []
        epsilon = 0.001

        def merge_labeled(
            host: str,
            gpu_index: int,
            intervals: list[tuple[float, float, str | None]],
        ) -> list[list[Any]]:
            merged: list[list[Any]] = []
            for start_ts, end_ts, users in sorted(intervals, key=lambda item: (item[0], item[1])):
                if not math.isfinite(start_ts) or not math.isfinite(end_ts) or end_ts <= start_ts:
                    raise RuntimeError(f"invalid usage interval for {host} GPU {gpu_index}")
                user_text = normalize_user_text(users)
                if merged and start_ts < float(merged[-1][1]) - epsilon and user_text != merged[-1][2]:
                    raise RuntimeError(f"conflicting usage intervals for {host} GPU {gpu_index}")
                if merged and user_text == merged[-1][2] and start_ts <= float(merged[-1][1]) + epsilon:
                    merged[-1][1] = max(float(merged[-1][1]), end_ts)
                else:
                    merged.append([start_ts, end_ts, user_text])
            return merged

        for key in keys:
            host = str(key["host"])
            gpu_index = int(key["gpu_index"])
            source_rows = conn.execute(
                """
                select start_ts, end_ts, users
                from gpu_usage_interval
                where host=? and gpu_index=?
                order by start_ts, end_ts, id
                """,
                (host, gpu_index),
            ).fetchall()
            source = [
                (float(row["start_ts"]), float(row["end_ts"]), row["users"])
                for row in source_rows
            ]
            deduplicated = merge_labeled(host, gpu_index, source)

            capacity_rows = conn.execute(
                """
                select start_ts, end_ts, busy
                from gpu_capacity_interval
                where host=? and gpu_index=?
                order by start_ts, end_ts, id
                """,
                (host, gpu_index),
            ).fetchall()
            if capacity_rows:
                envelope_start = min(float(row["start_ts"]) for row in capacity_rows)
                envelope_end = max(float(row["end_ts"]) for row in capacity_rows)
                busy_ranges: list[list[float]] = []
                for row in capacity_rows:
                    if not bool(row["busy"]):
                        continue
                    start_ts = float(row["start_ts"])
                    end_ts = float(row["end_ts"])
                    if end_ts <= start_ts:
                        continue
                    if busy_ranges and start_ts <= busy_ranges[-1][1] + epsilon:
                        busy_ranges[-1][1] = max(busy_ranges[-1][1], end_ts)
                    else:
                        busy_ranges.append([start_ts, end_ts])

                masked: list[tuple[float, float, str | None]] = []
                for start_ts, end_ts, users in deduplicated:
                    if start_ts < envelope_start:
                        preserved_end = min(end_ts, envelope_start)
                        if preserved_end > start_ts:
                            masked.append((start_ts, preserved_end, users))
                    inside_start = max(start_ts, envelope_start)
                    inside_end = min(end_ts, envelope_end)
                    if inside_end > inside_start:
                        for busy_start, busy_end in busy_ranges:
                            overlap_start = max(inside_start, busy_start)
                            overlap_end = min(inside_end, busy_end)
                            if overlap_end > overlap_start:
                                masked.append((overlap_start, overlap_end, users))
                    if end_ts > envelope_end:
                        preserved_start = max(start_ts, envelope_end)
                        if end_ts > preserved_start:
                            masked.append((preserved_start, end_ts, users))
                deduplicated = merge_labeled(host, gpu_index, masked)

            for start_ts, end_ts, users in deduplicated:
                repaired.append((host, gpu_index, start_ts, end_ts, users, end_ts - start_ts))

        conn.execute("delete from gpu_usage_interval")
        if repaired:
            conn.executemany(
                """
                insert into gpu_usage_interval(host, gpu_index, start_ts, end_ts, users, seconds)
                values (?, ?, ?, ?, ?, ?)
                """,
                repaired,
            )

    def _scrub_event_users_v1_0_1(self, conn: sqlite3.Connection) -> None:
        """Drop transient '?' ownership changes while preserving real internal boundaries."""
        rows = conn.execute(
            """
            select id, host, gpu_index, event, users
            from events
            order by host, gpu_index, ts, id
            """
        ).fetchall()
        current_users: dict[tuple[str, int | None], str | None] = {}
        delete_ids: list[tuple[int]] = []
        updates: list[tuple[str | None, int]] = []
        for row in rows:
            key = (str(row["host"]), int(row["gpu_index"]) if row["gpu_index"] is not None else None)
            event_name = str(row["event"] or "")
            normalized = normalize_user_text(row["users"])
            if event_name == "user_change":
                if normalized is None or normalized == current_users.get(key):
                    delete_ids.append((int(row["id"]),))
                    continue
                current_users[key] = normalized
            elif event_name == "busy_start":
                current_users[key] = normalized
            elif event_name in ("free_start", "hardware_change"):
                current_users[key] = None
            if normalized != row["users"]:
                updates.append((normalized, int(row["id"])))

        if updates:
            conn.executemany("update events set users=? where id=?", updates)
        if delete_ids:
            conn.executemany("delete from events where id=?", delete_ids)

        runtime_rows = conn.execute(
            "select host, gpu_index, last_user from gpu_runtime where last_user is not null"
        ).fetchall()
        for row in runtime_rows:
            normalized = normalize_user_text(row["last_user"])
            if normalized != row["last_user"]:
                conn.execute(
                    "update gpu_runtime set last_user=? where host=? and gpu_index=?",
                    (normalized, row["host"], row["gpu_index"]),
                )

    def health(self) -> dict[str, Any]:
        started = time.monotonic()
        with self.lock, closing(self.connect()) as conn, conn:
            conn.execute("select 1").fetchone()
            schema_row = conn.execute(
                "select value from schema_meta where key='schema_version'"
            ).fetchone()
        disk = shutil.disk_usage(self.path.parent)
        total_bytes = int(disk.total)
        free_bytes = int(disk.free)
        free_percent = (free_bytes / total_bytes * 100) if total_bytes else 0.0
        return {
            "ok": free_percent >= 5.0,
            "database_bytes": self.path.stat().st_size if self.path.exists() else 0,
            "latency_ms": round((time.monotonic() - started) * 1000, 2),
            "schema_version": int(schema_row["value"]) if schema_row else None,
            "disk_free_bytes": free_bytes,
            "disk_free_percent": round(free_percent, 2),
            "disk_warning": free_percent < 10.0,
        }

    def _refresh_daily_vram_rollups(
        self,
        conn: sqlite3.Connection,
        config: dict[str, Any],
        ts: float,
        days: int = 2,
    ) -> int:
        host_labs = {
            str(host["name"]): lab_id_for(host)
            for host in config.get("hosts", [])
        }
        lab_ids = {
            str(lab.get("id") or "default")
            for lab in config.get("labs", [])
        }
        if not host_labs or not lab_ids:
            return 0

        today = datetime.fromtimestamp(ts, KST).date()
        day_count = min(8, max(1, int(days)))
        dates = [today - timedelta(days=offset) for offset in range(day_count - 1, -1, -1)]
        first_day = dates[0]
        first_start = datetime(first_day.year, first_day.month, first_day.day, tzinfo=KST).timestamp()
        rows = [
            dict(row)
            for row in conn.execute(
                """
                select capacity.host, capacity.gpu_index, capacity.start_ts, capacity.end_ts,
                       capacity.memory_used_mib, capacity.memory_total_mib
                from gpu_capacity_interval as capacity
                join gpu_runtime as runtime
                  on runtime.host=capacity.host
                 and runtime.gpu_index=capacity.gpu_index
                 and runtime.active=1
                where capacity.end_ts>? and capacity.start_ts<?
                  and capacity.memory_used_mib is not null
                  and capacity.memory_total_mib is not null
                order by capacity.host, capacity.gpu_index, capacity.start_ts
                """,
                (first_start, ts),
            ).fetchall()
        ]
        rows_by_lab: dict[str, list[dict[str, Any]]] = {lab_id: [] for lab_id in lab_ids}
        for row in rows:
            lab_id = host_labs.get(str(row["host"]))
            if lab_id in rows_by_lab:
                rows_by_lab[lab_id].append(row)

        upserted = 0
        for day_value in dates:
            day_start = datetime(day_value.year, day_value.month, day_value.day, tzinfo=KST).timestamp()
            day_end = day_start + 86400
            observed_end = min(ts, day_end)
            if observed_end <= day_start:
                continue
            for lab_id, lab_rows in rows_by_lab.items():
                resource_seconds_by_gpu: dict[tuple[str, int], float] = {}
                observed_seconds_by_gpu: dict[tuple[str, int], float] = {}
                observed_intervals: list[tuple[float, float]] = []
                for row in lab_rows:
                    overlap_start = max(day_start, float(row["start_ts"]))
                    overlap_end = min(observed_end, float(row["end_ts"]))
                    overlap = max(0.0, overlap_end - overlap_start)
                    if overlap <= 0:
                        continue
                    observed_intervals.append((overlap_start, overlap_end))
                    total_gb = max(0.0, float(row["memory_total_mib"])) / 1024
                    used_gb = min(
                        total_gb,
                        max(0.0, float(row["memory_used_mib"])) / 1024,
                    )
                    gpu_key = (str(row["host"]), int(row["gpu_index"]))
                    resource_seconds_by_gpu[gpu_key] = (
                        resource_seconds_by_gpu.get(gpu_key, 0.0) + used_gb * overlap
                    )
                    observed_seconds_by_gpu[gpu_key] = (
                        observed_seconds_by_gpu.get(gpu_key, 0.0) + overlap
                    )
                if not observed_seconds_by_gpu:
                    continue
                average_used_gb = sum(
                    resource_seconds_by_gpu[gpu_key] / observed_seconds
                    for gpu_key, observed_seconds in observed_seconds_by_gpu.items()
                    if observed_seconds > 0
                )
                # The UI's "오늘 관측" is lab wall-clock coverage, not a
                # per-GPU maximum. Union disjoint GPU/server intervals so two
                # non-overlapping observed hours are reported as two hours.
                observed_seconds = min(86400.0, merged_interval_seconds(observed_intervals))
                conn.execute(
                    """
                    insert into lab_daily_index(
                        lab_id, day, average_used_gb, observed_seconds, complete, updated_ts
                    ) values (?, ?, ?, ?, ?, ?)
                    on conflict(lab_id, day) do update set
                        average_used_gb=excluded.average_used_gb,
                        observed_seconds=excluded.observed_seconds,
                        complete=excluded.complete,
                        updated_ts=excluded.updated_ts
                    """,
                    (
                        lab_id,
                        day_value.isoformat(),
                        round(average_used_gb, 4),
                        observed_seconds,
                        1 if ts >= day_end else 0,
                        ts,
                    ),
                )
                upserted += 1
        return upserted

    def run_maintenance(self, config: dict[str, Any]) -> dict[str, Any]:
        now = now_ts()
        event_retention_days = max(7, int(config.get("event_retention_days", 180)))
        announcement_retention_days = max(7, int(config.get("announcement_retention_days", 90)))
        backup_retention_days = max(2, int(config.get("backup_retention_days", 14)))
        predeployment_backup_retention_days = max(7, int(config.get("predeployment_backup_retention_days", 30)))
        daily_index_retention_days = max(30, int(config.get("daily_index_retention_days", 180)))
        raw_interval_retention_days = max(8, int(config.get("raw_interval_retention_days", 8)))
        event_cutoff = now - event_retention_days * 86400
        announcement_cutoff = now - announcement_retention_days * 86400
        raw_interval_cutoff = now - raw_interval_retention_days * 86400
        maintenance_time = datetime.fromtimestamp(now, KST)
        week_key = maintenance_time.strftime("%G-W%V")
        backup_key = maintenance_time.strftime("%Y%m%d")
        backup_path = BACKUP_DIR / f"gpu_watch-{backup_key}.sqlite3"
        result: dict[str, Any] = {
            "events_deleted": 0,
            "announcements_deleted": 0,
            "backup": backup_path.name,
            "backup_created": False,
            "integrity": "not_due",
            "daily_rollups_upserted": 0,
            "daily_rollups_deleted": 0,
            "usage_intervals_deleted": 0,
            "capacity_intervals_deleted": 0,
            "host_errors_redacted": 0,
        }

        BACKUP_DIR.mkdir(parents=True, exist_ok=True)
        with self.lock:
            with closing(self.connect()) as conn, conn:
                result["daily_rollups_upserted"] = self._refresh_daily_vram_rollups(
                    conn, config, now, days=8
                )
                result["events_deleted"] = conn.execute(
                    "delete from events where ts<?",
                    (event_cutoff,),
                ).rowcount
                result["usage_intervals_deleted"] = conn.execute(
                    "delete from gpu_usage_interval where end_ts<?",
                    (raw_interval_cutoff,),
                ).rowcount
                result["capacity_intervals_deleted"] = conn.execute(
                    "delete from gpu_capacity_interval where end_ts<?",
                    (raw_interval_cutoff,),
                ).rowcount
                self._scrub_process_payloads(conn)
                result["host_errors_redacted"] = self._scrub_host_errors(conn)
                result["announcements_deleted"] = conn.execute(
                    """
                    delete from announcements
                    where (deleted_ts is not null and deleted_ts<?)
                       or (permanent=0 and expires_ts<?)
                    """,
                    (announcement_cutoff, announcement_cutoff),
                ).rowcount
                daily_cutoff = (
                    datetime.fromtimestamp(now, KST).date()
                    - timedelta(days=daily_index_retention_days)
                ).isoformat()
                result["daily_rollups_deleted"] = conn.execute(
                    "delete from lab_daily_index where day<?",
                    (daily_cutoff,),
                ).rowcount
                integrity_row = conn.execute(
                    "select value from maintenance_meta where key='last_integrity_week'",
                ).fetchone()
                if integrity_row is None or integrity_row["value"] != week_key:
                    integrity = str(conn.execute("pragma quick_check").fetchone()[0])
                    result["integrity"] = integrity
                    if integrity != "ok":
                        raise RuntimeError(f"sqlite integrity check failed: {integrity}")
                    foreign_key_violation = conn.execute("pragma foreign_key_check").fetchone()
                    if foreign_key_violation is not None:
                        raise RuntimeError("sqlite foreign key check failed")
                    conn.execute(
                        "insert into maintenance_meta(key, value) values('last_integrity_week', ?) "
                        "on conflict(key) do update set value=excluded.value",
                        (week_key,),
                    )
                conn.execute("pragma optimize")

            if not backup_path.exists():
                temporary_path = backup_path.with_suffix(".sqlite3.tmp")
                temporary_path.unlink(missing_ok=True)
                remove_sqlite_sidecars(temporary_path)
                try:
                    with ExitStack() as stack:
                        source = stack.enter_context(closing(self.connect()))
                        destination = stack.enter_context(closing(sqlite3.connect(temporary_path)))
                        source.backup(destination)
                        destination.commit()
                        journal_mode = str(
                            destination.execute("pragma journal_mode=delete").fetchone()[0]
                        ).lower()
                        if journal_mode != "delete":
                            raise RuntimeError(f"backup journal mode is not standalone: {journal_mode}")
                    with closing(
                        sqlite3.connect(standalone_database_uri(temporary_path), uri=True)
                    ) as verification:
                        verification.execute("pragma query_only=on")
                        backup_integrity = str(verification.execute("pragma quick_check").fetchone()[0])
                        backup_foreign_key_violation = verification.execute(
                            "pragma foreign_key_check"
                        ).fetchone()
                    if backup_integrity != "ok":
                        raise RuntimeError(f"backup integrity check failed: {backup_integrity}")
                    if backup_foreign_key_violation is not None:
                        raise RuntimeError("backup foreign key check failed")
                    try:
                        temporary_path.chmod(0o600)
                    except OSError:
                        pass
                    os.replace(temporary_path, backup_path)
                    result["backup_created"] = True
                finally:
                    temporary_path.unlink(missing_ok=True)
                    remove_sqlite_sidecars(temporary_path)

        backup_cutoff = now - backup_retention_days * 86400
        removed_backups = 0
        for candidate in BACKUP_DIR.glob("gpu_watch-*.sqlite3"):
            try:
                if candidate.stat().st_mtime < backup_cutoff:
                    candidate.unlink()
                    remove_sqlite_sidecars(candidate)
                    removed_backups += 1
            except FileNotFoundError:
                continue
        result["backups_removed"] = removed_backups
        predeployment_cutoff = now - predeployment_backup_retention_days * 86400
        removed_predeployment_backups = 0
        for pattern in ("pre-deploy-*.sqlite3", "pre-restore-*.sqlite3"):
            for candidate in BACKUP_DIR.glob(pattern):
                try:
                    if candidate.stat().st_mtime < predeployment_cutoff:
                        candidate.unlink()
                        remove_sqlite_sidecars(candidate)
                        removed_predeployment_backups += 1
                except FileNotFoundError:
                    continue
        result["predeployment_backups_removed"] = removed_predeployment_backups
        return result

    def _backfill_usage_stats(self, conn: sqlite3.Connection) -> None:
        rows = conn.execute("select * from gpu_runtime").fetchall()
        for row in rows:
            first_seen = row["first_seen_ts"] or row["last_seen_ts"]
            last_seen = row["last_seen_ts"] or first_seen
            if first_seen is None or last_seen is None or last_seen <= first_seen:
                continue

            events = conn.execute(
                """
                select ts, event, users
                from events
                where host=? and gpu_index=? and ts>=? and ts<=?
                order by ts asc, id asc
                """,
                (row["host"], row["gpu_index"], first_seen - 1, last_seen + 1),
            ).fetchall()
            cursor = float(first_seen)
            state: bool | None = None
            users: list[str] = []
            observed_seconds = 0.0
            busy_seconds = 0.0
            user_seconds: dict[str, float] = {}

            for event in events:
                event_ts = min(float(last_seen), max(float(first_seen), float(event["ts"])))
                duration = max(0.0, event_ts - cursor)
                observed_seconds += duration
                if state:
                    busy_seconds += duration
                    add_user_seconds(user_seconds, users, duration)

                if event["event"] == "busy_start":
                    state = True
                    users = split_user_text(event["users"])
                elif event["event"] == "user_change":
                    state = True
                    users = split_user_text(event["users"])
                elif event["event"] == "free_start":
                    state = False
                cursor = event_ts

            duration = max(0.0, float(last_seen) - cursor)
            observed_seconds += duration
            final_state = bool(row["busy"]) if state is None else state
            final_users = split_user_text(row["last_user"]) if state is None else users
            if final_state:
                busy_seconds += duration
                add_user_seconds(user_seconds, final_users, duration)

            conn.execute(
                """
                update gpu_runtime
                set observed_seconds=?, busy_seconds=?, user_busy_json=?
                where host=? and gpu_index=?
                """,
                (
                    observed_seconds,
                    busy_seconds,
                    json.dumps(user_seconds, ensure_ascii=False, separators=(",", ":")),
                    row["host"],
                    row["gpu_index"],
                ),
            )

    def _recent_usage_by_host(
        self,
        conn: sqlite3.Connection,
        gpu_rows: list[sqlite3.Row],
        host_rows: dict[str, dict[str, Any]],
        ts: float,
        config: dict[str, Any],
    ) -> dict[str, dict[str, Any]]:
        window_seconds = float(config.get("recent_usage_window_seconds", 7 * 86400))
        if window_seconds <= 0:
            return {}
        window_start = ts - window_seconds
        minimum_session_seconds = float(
            config.get("activity_policy", {}).get("cold_min_session_seconds", 60)
        )
        recent_by_host: dict[str, dict[str, Any]] = {}

        rows = conn.execute(
            """
            select host, users, start_ts, end_ts
            from gpu_usage_interval
            where end_ts>? and start_ts<?
            """,
            # Include enough preceding evidence to classify a continuous
            # session split by user/GPU changes at the rolling-window edge.
            (window_start - minimum_session_seconds, ts),
        ).fetchall()
        for row in rows:
            host_usage = recent_by_host.setdefault(row["host"], {
                "busy_seconds": 0.0,
                "user_seconds": {},
                "active_intervals": [],
                "session_intervals": [],
                "unassigned_segments": [],
            })
            host_usage["session_intervals"].append(
                (float(row["start_ts"]), float(row["end_ts"]))
            )
            start_ts = max(window_start, float(row["start_ts"]))
            end_ts = min(ts, float(row["end_ts"]))
            duration = max(0.0, end_ts - start_ts)
            if duration <= 0:
                continue
            host_usage["busy_seconds"] += duration
            users = split_user_text(row["users"])
            add_user_seconds(host_usage["user_seconds"], users, duration)
            if not users:
                host_usage["unassigned_segments"].append((start_ts, end_ts))
            host_usage["active_intervals"].append((start_ts, end_ts))

        usage_gap = float(config.get("usage_gap_seconds", max(120, config["poll_interval_seconds"] * 6)))
        for row in gpu_rows:
            item = dict(row)
            runtime = host_rows.get(item["host"], {})
            if not gpu_is_observed(runtime, item["gpu_index"]) or not bool(item.get("busy")):
                continue
            last_seen = item.get("last_seen_ts")
            if last_seen is None:
                continue
            last_seen_ts = float(last_seen)
            live_elapsed = max(0.0, ts - last_seen_ts)
            if live_elapsed > usage_gap:
                continue
            start_ts = max(window_start, last_seen_ts)
            duration = max(0.0, ts - start_ts)
            if duration <= 0:
                continue
            host_usage = recent_by_host.setdefault(item["host"], {
                "busy_seconds": 0.0,
                "user_seconds": {},
                "active_intervals": [],
                "session_intervals": [],
                "unassigned_segments": [],
            })
            host_usage["busy_seconds"] += duration
            users = confirmed_process_users(json.loads(item.get("last_processes_json") or "[]"))
            add_user_seconds(host_usage["user_seconds"], users, duration)
            if not users:
                host_usage["unassigned_segments"].append((start_ts, ts))
            host_usage["active_intervals"].append((start_ts, ts))
            host_usage["session_intervals"].append((last_seen_ts, ts))

        for host_usage in recent_by_host.values():
            intervals = host_usage.pop("active_intervals", [])
            host_usage["active_seconds"] = merged_interval_seconds(intervals)
            sessions = merge_intervals(host_usage.pop("session_intervals", []))
            meaningful_sessions = [
                (start, end)
                for start, end in sessions
                if end > window_start and end - start >= minimum_session_seconds
            ]
            host_usage["meaningful_active_seconds"] = sum(
                max(0.0, min(ts, end) - max(window_start, start))
                for start, end in meaningful_sessions
            )
            host_usage["last_meaningful_used_ts"] = (
                max(min(ts, end) for _, end in meaningful_sessions)
                if meaningful_sessions
                else None
            )
            unassigned_segments = host_usage.pop("unassigned_segments", [])
            reportable_unassigned_seconds = 0.0
            session_index = 0
            for segment_start, segment_end in sorted(unassigned_segments):
                while (
                    session_index < len(meaningful_sessions)
                    and meaningful_sessions[session_index][1] <= segment_start
                ):
                    session_index += 1
                if session_index >= len(meaningful_sessions):
                    break
                candidate_index = session_index
                while (
                    candidate_index < len(meaningful_sessions)
                    and meaningful_sessions[candidate_index][0] < segment_end
                ):
                    session_start, session_end = meaningful_sessions[candidate_index]
                    reportable_unassigned_seconds += max(
                        0.0,
                        min(segment_end, session_end) - max(segment_start, session_start),
                    )
                    if session_end >= segment_end:
                        break
                    candidate_index += 1
            host_usage["reportable_unassigned_seconds"] = reportable_unassigned_seconds

        return recent_by_host

    def _recent_capacity_by_host(
        self,
        conn: sqlite3.Connection,
        ts: float,
        config: dict[str, Any],
    ) -> dict[str, dict[str, Any]]:
        window_seconds = float(config.get("recent_usage_window_seconds", 7 * 86400))
        if window_seconds <= 0:
            return {}
        window_start = ts - window_seconds
        rows = conn.execute(
            """
            select host, start_ts, end_ts, busy
            from gpu_capacity_interval
            where end_ts>? and start_ts<?
            """,
            (window_start, ts),
        ).fetchall()
        recent_by_host: dict[str, dict[str, Any]] = {}
        for row in rows:
            start_ts = max(window_start, float(row["start_ts"]))
            end_ts = min(ts, float(row["end_ts"]))
            duration = max(0.0, end_ts - start_ts)
            if duration <= 0:
                continue
            capacity = recent_by_host.setdefault(row["host"], {
                "observed_gpu_seconds": 0.0,
                "busy_gpu_seconds": 0.0,
                "observed_intervals": [],
            })
            capacity["observed_gpu_seconds"] += duration
            if bool(row["busy"]):
                capacity["busy_gpu_seconds"] += duration
            capacity["observed_intervals"].append((start_ts, end_ts))

        for capacity in recent_by_host.values():
            intervals = capacity.pop("observed_intervals", [])
            capacity["observed_wall_seconds"] = merged_interval_seconds(intervals)
        return recent_by_host

    def _backfill_recent_usage_intervals(
        self,
        conn: sqlite3.Connection,
        gpu_rows: list[sqlite3.Row],
        ts: float,
        config: dict[str, Any],
    ) -> None:
        window_seconds = float(config.get("recent_usage_window_seconds", 7 * 86400))
        if window_seconds <= 0:
            return
        max_interval_seconds = float(config.get("recent_usage_event_backfill_max_seconds", 3 * 86400))
        if max_interval_seconds <= 0:
            return

        window_start = ts - window_seconds
        lookback_start = window_start - max_interval_seconds
        gpu_state = {(row["host"], int(row["gpu_index"])): dict(row) for row in gpu_rows}
        coverage: dict[tuple[str, int], list[list[float]]] = {}
        coverage_rows = conn.execute(
            """
            select host, gpu_index, start_ts, end_ts
            from gpu_usage_interval
            where end_ts>? and start_ts<?
            order by host, gpu_index, start_ts, end_ts, id
            """,
            (window_start, ts),
        ).fetchall()
        for row in coverage_rows:
            key = (str(row["host"]), int(row["gpu_index"]))
            start_ts = max(window_start, float(row["start_ts"]))
            end_ts = min(ts, float(row["end_ts"]))
            if end_ts <= start_ts:
                continue
            ranges = coverage.setdefault(key, [])
            if ranges and start_ts <= ranges[-1][1]:
                ranges[-1][1] = max(ranges[-1][1], end_ts)
            else:
                ranges.append([start_ts, end_ts])
        capacity: dict[tuple[str, int], dict[str, Any]] = {}
        capacity_rows = conn.execute(
            """
            select host, gpu_index, start_ts, end_ts, busy
            from gpu_capacity_interval
            where end_ts>? and start_ts<?
            order by host, gpu_index, start_ts, end_ts, id
            """,
            (window_start, ts),
        ).fetchall()
        for row in capacity_rows:
            key = (str(row["host"]), int(row["gpu_index"]))
            start_ts = max(window_start, float(row["start_ts"]))
            end_ts = min(ts, float(row["end_ts"]))
            if end_ts <= start_ts:
                continue
            item = capacity.setdefault(key, {
                "start": start_ts,
                "end": end_ts,
                "busy": [],
            })
            item["start"] = min(float(item["start"]), start_ts)
            item["end"] = max(float(item["end"]), end_ts)
            if bool(row["busy"]):
                busy_ranges = item["busy"]
                if busy_ranges and start_ts <= busy_ranges[-1][1]:
                    busy_ranges[-1][1] = max(busy_ranges[-1][1], end_ts)
                else:
                    busy_ranges.append([start_ts, end_ts])
        event_keys = conn.execute(
            """
            select distinct host, gpu_index
            from events
            where gpu_index is not null and ts>=? and ts<=?
            """,
            (lookback_start, ts),
        ).fetchall()
        for key in event_keys:
            host = key["host"]
            gpu_index = int(key["gpu_index"])
            events = conn.execute(
                """
                select ts, event, users, note
                from events
                where host=? and gpu_index=? and ts>=? and ts<=?
                order by ts asc, id asc
                """,
                (host, gpu_index, lookback_start, ts),
            ).fetchall()
            busy_start: float | None = None
            users: str | None = None
            for event in events:
                event_ts = float(event["ts"])
                event_name = str(event["event"] or "")
                gap_boundary = event_name == "observation_gap" or str(event["note"] or "").startswith(
                    "after observation gap "
                )
                if gap_boundary:
                    busy_start = None
                    users = None
                    if event_name == "observation_gap":
                        continue
                if event_name in ("busy_start", "user_change"):
                    if busy_start is not None:
                        self._record_recent_backfill_interval(
                            conn,
                            host,
                            gpu_index,
                            busy_start,
                            event_ts,
                            users,
                            window_start,
                            ts,
                            max_interval_seconds,
                            coverage,
                            capacity,
                        )
                    busy_start = event_ts
                    users = event["users"]
                elif event_name == "free_start":
                    if busy_start is not None:
                        self._record_recent_backfill_interval(
                            conn,
                            host,
                            gpu_index,
                            busy_start,
                            event_ts,
                            users,
                            window_start,
                            ts,
                            max_interval_seconds,
                            coverage,
                            capacity,
                        )
                    busy_start = None
                    users = None

            row = gpu_state.get((host, gpu_index))
            if busy_start is not None and row and bool(row.get("busy")):
                last_seen = row.get("last_seen_ts")
                if last_seen is not None:
                    self._record_recent_backfill_interval(
                        conn,
                        host,
                        gpu_index,
                        busy_start,
                        min(ts, float(last_seen)),
                        users,
                        window_start,
                        ts,
                        max_interval_seconds,
                        coverage,
                        capacity,
                    )

    def _record_recent_backfill_interval(
        self,
        conn: sqlite3.Connection,
        host: str,
        gpu_index: int,
        start_ts: float,
        end_ts: float,
        users: str | None,
        window_start: float,
        now: float,
        max_interval_seconds: float,
        coverage: dict[tuple[str, int], list[list[float]]],
        capacity: dict[tuple[str, int], dict[str, Any]],
    ) -> None:
        duration = max(0.0, end_ts - start_ts)
        if duration <= 0 or duration > max_interval_seconds:
            return
        if end_ts <= window_start or start_ts >= now:
            return
        clipped_start = max(window_start, start_ts)
        clipped_end = min(now, end_ts)
        key = (host, gpu_index)
        candidates = [[clipped_start, clipped_end]]
        capacity_item = capacity.get(key)
        if capacity_item:
            candidates = []
            envelope_start = float(capacity_item["start"])
            envelope_end = float(capacity_item["end"])
            if clipped_start < envelope_start:
                candidates.append([clipped_start, min(clipped_end, envelope_start)])
            inside_start = max(clipped_start, envelope_start)
            inside_end = min(clipped_end, envelope_end)
            if inside_end > inside_start:
                for busy_start, busy_end in capacity_item["busy"]:
                    overlap_start = max(inside_start, busy_start)
                    overlap_end = min(inside_end, busy_end)
                    if overlap_end > overlap_start:
                        candidates.append([overlap_start, overlap_end])
            if clipped_end > envelope_end:
                candidates.append([max(clipped_start, envelope_end), clipped_end])

        for candidate_start, candidate_end in candidates:
            if candidate_end <= candidate_start:
                continue
            existing = coverage.get(key, [])
            cursor = candidate_start
            for existing_start, existing_end in existing:
                overlap_start = max(candidate_start, existing_start)
                overlap_end = min(candidate_end, existing_end)
                if overlap_end <= cursor:
                    continue
                if overlap_start > cursor:
                    self._record_busy_interval(conn, host, gpu_index, cursor, overlap_start, users)
                cursor = max(cursor, overlap_end)
                if cursor >= candidate_end:
                    break
            if cursor < candidate_end:
                self._record_busy_interval(conn, host, gpu_index, cursor, candidate_end, users)
            updated = sorted([*existing, [candidate_start, candidate_end]], key=lambda item: (item[0], item[1]))
            merged: list[list[float]] = []
            for range_start, range_end in updated:
                if merged and range_start <= merged[-1][1]:
                    merged[-1][1] = max(merged[-1][1], range_end)
                else:
                    merged.append([range_start, range_end])
            coverage[key] = merged

    def _record_busy_interval(
        self,
        conn: sqlite3.Connection,
        host: str,
        gpu_index: int,
        start_ts: float,
        end_ts: float,
        users: str | None,
    ) -> None:
        if end_ts <= start_ts:
            return
        user_text = normalize_user_text(users)
        duplicate = conn.execute(
            """
            select id
            from gpu_usage_interval
            where host=? and gpu_index=? and abs(start_ts - ?) < 0.001
              and abs(end_ts - ?) < 0.001 and coalesce(users, '')=coalesce(?, '')
            limit 1
            """,
            (host, gpu_index, start_ts, end_ts, user_text),
        ).fetchone()
        if duplicate:
            return
        previous = conn.execute(
            """
            select id, start_ts, end_ts, users
            from gpu_usage_interval
            where host=? and gpu_index=?
            order by end_ts desc, id desc
            limit 1
            """,
            (host, gpu_index),
        ).fetchone()
        if (
            previous
            and (previous["users"] or None) == user_text
            and abs(float(previous["end_ts"]) - start_ts) <= 1.0
        ):
            merged_start = float(previous["start_ts"])
            conn.execute(
                "update gpu_usage_interval set end_ts=?, seconds=? where id=?",
                (end_ts, max(0.0, end_ts - merged_start), previous["id"]),
            )
            return
        conn.execute(
            """
            insert into gpu_usage_interval(host, gpu_index, start_ts, end_ts, users, seconds)
            values (?, ?, ?, ?, ?, ?)
            """,
            (host, gpu_index, start_ts, end_ts, user_text, max(0.0, end_ts - start_ts)),
        )

    def _record_capacity_interval(
        self,
        conn: sqlite3.Connection,
        host: str,
        gpu_index: int,
        start_ts: float,
        end_ts: float,
        busy: bool,
        memory_used_mib: float | None = None,
        memory_total_mib: float | None = None,
    ) -> None:
        if end_ts <= start_ts:
            return
        try:
            used_value = float(memory_used_mib) if memory_used_mib is not None else math.nan
            used_mib = (
                max(0.0, round(used_value / 16.0) * 16.0)
                if math.isfinite(used_value)
                else None
            )
        except (OverflowError, TypeError, ValueError):
            used_mib = None
        try:
            total_value = float(memory_total_mib) if memory_total_mib is not None else math.nan
            total_mib = max(0.0, total_value) if math.isfinite(total_value) else None
        except (OverflowError, TypeError, ValueError):
            total_mib = None
        if used_mib is not None and total_mib is not None:
            used_mib = min(used_mib, total_mib)
        previous = conn.execute(
            """
            select id, start_ts, end_ts, busy, memory_used_mib, memory_total_mib
            from gpu_capacity_interval
            where host=? and gpu_index=?
            order by end_ts desc, id desc
            limit 1
            """,
            (host, gpu_index),
        ).fetchone()
        if (
            previous
            and bool(previous["busy"]) == bool(busy)
            and previous["memory_used_mib"] == used_mib
            and previous["memory_total_mib"] == total_mib
            and abs(float(previous["end_ts"]) - start_ts) <= 1.0
        ):
            merged_start = float(previous["start_ts"])
            conn.execute(
                "update gpu_capacity_interval set end_ts=?, seconds=? where id=?",
                (end_ts, max(0.0, end_ts - merged_start), previous["id"]),
            )
            return
        conn.execute(
            """
            insert into gpu_capacity_interval(
                host, gpu_index, start_ts, end_ts, busy,
                memory_used_mib, memory_total_mib, seconds
            )
            values (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                host, gpu_index, start_ts, end_ts, 1 if busy else 0,
                used_mib, total_mib, max(0.0, end_ts - start_ts),
            ),
        )

    def announcements(self, ts: float | None = None) -> list[dict[str, Any]]:
        now = now_ts() if ts is None else ts
        with self.lock, closing(self.connect()) as conn, conn:
            rows = conn.execute(
                """
                select id, created_ts, expires_ts, permanent, author, message,
                       coalesce(updated_ts, created_ts) as updated_ts
                from announcements
                where deleted_ts is null and (permanent=1 or expires_ts>?)
                order by created_ts desc, id desc
                limit 20
                """,
                (now,),
            ).fetchall()
        announcements: list[dict[str, Any]] = []
        for row in rows:
            permanent = bool(row["permanent"])
            announcements.append({
                "id": int(row["id"]),
                "author": row["author"],
                "message": row["message"],
                "created_at": iso(row["created_ts"]),
                "expires_at": None if permanent else iso(row["expires_ts"]),
                "remaining_seconds": (
                    None if permanent else max(0.0, float(row["expires_ts"]) - now)
                ),
                "permanent": permanent,
                "updated_at": iso(row["updated_ts"]),
            })
        return announcements

    @staticmethod
    def _validated_announcement_content(
        author: Any,
        message: Any,
        duration_seconds: Any,
        expires_at: Any,
        ts: float,
    ) -> tuple[str, str, float, float | None, bool]:
        author_text = clean_text(author, 32)
        message_text = clean_multiline_text(message, 240)
        expires_text = clean_text(expires_at, 64)
        duration_text = clean_text(duration_seconds, 32)
        permanent = not expires_text and not duration_text
        duration: float | None = None
        if permanent:
            # Keep the legacy NOT NULL column intact for a low-risk additive migration.
            # The permanent flag is authoritative and this timestamp is never exposed.
            expires_ts = ts
        elif expires_text:
            expires_ts = parse_local_datetime(expires_text)
            if expires_ts is None:
                raise ValueError("만료 시각을 다시 선택해 주세요.")
        else:
            try:
                duration = float(int(duration_text))
            except Exception as exc:
                raise ValueError("만료 시각을 다시 선택해 주세요.") from exc
            expires_ts = ts + duration
        if not permanent:
            duration = expires_ts - ts
            if not math.isfinite(expires_ts) or not math.isfinite(duration):
                raise ValueError("만료 시각을 다시 선택해 주세요.")
            try:
                iso(expires_ts)
            except (OverflowError, OSError, ValueError) as exc:
                raise ValueError("만료 시각을 다시 선택해 주세요.") from exc
            if duration <= 0:
                raise ValueError("만료 시각은 현재보다 이후여야 합니다.")
        if not author_text:
            raise ValueError("작성자를 입력해 주세요.")
        if not message_text:
            raise ValueError("내용을 입력해 주세요.")
        return author_text, message_text, expires_ts, duration, permanent

    def add_announcement(
        self,
        author: Any,
        message: Any,
        duration_seconds: Any,
        expires_at: Any = None,
        pin: Any = None,
    ) -> dict[str, Any]:
        ts = now_ts()
        author_text, message_text, expires_ts, duration, permanent = self._validated_announcement_content(
            author,
            message,
            duration_seconds,
            expires_at,
            ts,
        )
        if not is_valid_announcement_passphrase(pin):
            raise ValueError("공지 비밀번호는 출력 가능한 문자 4~64자여야 합니다.")
        with self.lock, closing(self.connect()) as conn:
            active_count = conn.execute(
                """
                select count(*) from announcements
                where deleted_ts is null and (permanent=1 or expires_ts>?)
                """,
                (ts,),
            ).fetchone()[0]
        if int(active_count) >= MAX_ACTIVE_ANNOUNCEMENTS:
            raise ValueError(f"활성 공지는 최대 {MAX_ACTIVE_ANNOUNCEMENTS}개까지 등록할 수 있습니다.")
        pin_hash = bounded_hash_passphrase(str(pin))
        with self.lock, closing(self.connect()) as conn, conn:
            active_count = conn.execute(
                """
                select count(*) from announcements
                where deleted_ts is null and (permanent=1 or expires_ts>?)
                """,
                (ts,),
            ).fetchone()[0]
            if int(active_count) >= MAX_ACTIVE_ANNOUNCEMENTS:
                raise ValueError(
                    f"활성 공지는 최대 {MAX_ACTIVE_ANNOUNCEMENTS}개까지 등록할 수 있습니다."
                )
            cur = conn.execute(
                """
                insert into announcements(
                    created_ts, expires_ts, permanent, author, message, pin_hash, updated_ts
                )
                values (?, ?, ?, ?, ?, ?, ?)
                """,
                (ts, expires_ts, 1 if permanent else 0, author_text, message_text, pin_hash, ts),
            )
            if cur.lastrowid is None:
                raise RuntimeError("announcement insert did not return an id")
            notice_id = int(cur.lastrowid)
        return {
            "id": notice_id,
            "author": author_text,
            "message": message_text,
            "created_at": iso(ts),
            "expires_at": None if permanent else iso(expires_ts),
            "remaining_seconds": duration,
            "permanent": permanent,
            "updated_at": iso(ts),
        }

    def update_announcement(
        self,
        notice_id: int,
        author: Any,
        message: Any,
        duration_seconds: Any,
        expires_at: Any,
        pin: Any,
        admin_pin_hash: str | None,
    ) -> tuple[dict[str, Any] | None, str]:
        ts = now_ts()
        author_text, message_text, expires_ts, duration, permanent = self._validated_announcement_content(
            author,
            message,
            duration_seconds,
            expires_at,
            ts,
        )
        supplied_pin = str(pin or "")
        if not is_valid_announcement_passphrase(supplied_pin):
            return None, "공지 비밀번호는 출력 가능한 문자 4~64자여야 합니다."
        with self.lock, closing(self.connect()) as conn:
            row = conn.execute(
                """
                select id, created_ts, pin_hash, updated_ts
                from announcements
                where id=? and deleted_ts is null and (permanent=1 or expires_ts>?)
                """,
                (notice_id, ts),
            ).fetchone()
        if row is None:
            return None, "수정할 공지를 찾을 수 없습니다."
        owner_allowed = verify_pin(supplied_pin, row["pin_hash"])
        admin_allowed = not owner_allowed and verify_pin(supplied_pin, admin_pin_hash)
        if not owner_allowed and not admin_allowed:
            return None, "공지 비밀번호 또는 관리자 PIN이 일치하지 않습니다."
        with self.lock, closing(self.connect()) as conn, conn:
            updated = conn.execute(
                """
                update announcements
                set expires_ts=?, permanent=?, author=?, message=?, updated_ts=?
                where id=? and deleted_ts is null and (permanent=1 or expires_ts>?)
                  and pin_hash=? and updated_ts=?
                """,
                (
                    expires_ts,
                    1 if permanent else 0,
                    author_text,
                    message_text,
                    ts,
                    notice_id,
                    now_ts(),
                    row["pin_hash"],
                    row["updated_ts"],
                ),
            )
            if updated.rowcount != 1:
                return None, "수정할 공지를 찾을 수 없습니다."
        created_ts = float(row["created_ts"])
        return {
            "id": notice_id,
            "author": author_text,
            "message": message_text,
            "created_at": iso(created_ts),
            "expires_at": None if permanent else iso(expires_ts),
            "remaining_seconds": duration,
            "permanent": permanent,
            "updated_at": iso(ts),
        }, "updated"

    def delete_announcement(
        self,
        notice_id: int,
        pin: Any,
        admin_pin_hash: str | None,
    ) -> tuple[bool, str]:
        ts = now_ts()
        supplied_pin = str(pin or "")
        if not is_valid_announcement_passphrase(supplied_pin):
            return False, "공지 비밀번호는 출력 가능한 문자 4~64자여야 합니다."
        with self.lock, closing(self.connect()) as conn:
            row = conn.execute(
                """
                select id, pin_hash, updated_ts
                from announcements
                where id=? and deleted_ts is null and (permanent=1 or expires_ts>?)
                """,
                (notice_id, ts),
            ).fetchone()
        if row is None:
            return False, "삭제할 공지를 찾을 수 없습니다."
        owner_allowed = verify_pin(supplied_pin, row["pin_hash"])
        admin_allowed = not owner_allowed and verify_pin(supplied_pin, admin_pin_hash)
        if not owner_allowed and not admin_allowed:
            return False, "공지 PIN 또는 관리자 PIN이 일치하지 않습니다."
        with self.lock, closing(self.connect()) as conn, conn:
            deleted = conn.execute(
                """
                update announcements
                set deleted_ts=?, deleted_by=?
                where id=? and deleted_ts is null and (permanent=1 or expires_ts>?)
                  and pin_hash=? and updated_ts=?
                """,
                (
                    ts,
                    "owner" if owner_allowed else "admin",
                    notice_id,
                    now_ts(),
                    row["pin_hash"],
                    row["updated_ts"],
                ),
            )
            if deleted.rowcount != 1:
                return False, "삭제할 공지를 찾을 수 없습니다."
        return True, "deleted"

    @staticmethod
    def _deadline_fields(
        candidate: Any,
        fallback: Any = None,
        updated_ts: float | None = None,
    ) -> dict[str, Any] | None:
        source = candidate if isinstance(candidate, dict) else {}
        default = fallback if isinstance(fallback, dict) else {}
        title = clean_text(source.get("title"), 64) or clean_text(default.get("title"), 64)
        mode = source.get("mode", default.get("mode", "scheduled"))
        if mode not in ("scheduled", "tba"):
            return None
        tba_text = clean_text(source.get("tba_text"), 32) or clean_text(default.get("tba_text"), 32)
        deadline_ts = parse_iso_datetime(source.get("deadline_at"))
        if deadline_ts is None and mode == "scheduled":
            deadline_ts = parse_iso_datetime(default.get("deadline_at"))
        url = safe_http_url(source.get("url"), default.get("url"))
        if not title or not url or (mode == "scheduled" and deadline_ts is None) or (mode == "tba" and not tba_text):
            return None
        item_updated_ts = parse_iso_datetime(source.get("updated_at"))
        return {
            "title": title,
            "deadline_at": iso(deadline_ts) if mode == "scheduled" else None,
            "mode": mode,
            "tba_text": tba_text if mode == "tba" else "",
            "url": url,
            "updated_at": iso(item_updated_ts if item_updated_ts is not None else updated_ts),
        }

    def _load_deadlines(
        self,
        conn: sqlite3.Connection,
        config: dict[str, Any],
    ) -> list[dict[str, Any]]:
        row = conn.execute(
            "select value, updated_ts from dashboard_settings where key=?",
            (CONFERENCE_DEADLINES_KEY,),
        ).fetchone()
        if row is not None:
            try:
                stored = json.loads(row["value"])
            except (TypeError, ValueError, json.JSONDecodeError):
                stored = None
            if isinstance(stored, list):
                deadlines: list[dict[str, Any]] = []
                used_ids: set[str] = set()
                used_tones: set[str] = set()
                for index, candidate in enumerate(stored[:MAX_CONFERENCE_DEADLINES]):
                    fields = self._deadline_fields(candidate, updated_ts=float(row["updated_ts"]))
                    if fields is None:
                        continue
                    raw_id = clean_text(candidate.get("id"), 32) if isinstance(candidate, dict) else ""
                    if not CONFERENCE_DEADLINE_ID_RE.fullmatch(raw_id) or raw_id in used_ids:
                        raw_id = f"slot-{index + 1}"
                        while raw_id in used_ids:
                            raw_id += "-next"
                    raw_tone = clean_text(candidate.get("tone"), 16) if isinstance(candidate, dict) else ""
                    if raw_tone not in CONFERENCE_DEADLINE_TONES or raw_tone in used_tones:
                        raw_tone = next(
                            (tone for tone in CONFERENCE_DEADLINE_TONES if tone not in used_tones),
                            CONFERENCE_DEADLINE_TONES[index % len(CONFERENCE_DEADLINE_TONES)],
                        )
                    fields.update({"id": raw_id, "tone": raw_tone})
                    deadlines.append(fields)
                    used_ids.add(raw_id)
                    used_tones.add(raw_tone)
                return self._sort_deadlines(deadlines)

        legacy_row = conn.execute(
            "select value, updated_ts from dashboard_settings where key=?",
            (LEGACY_CONFERENCE_DEADLINE_KEY,),
        ).fetchone()
        legacy: dict[str, Any] = {}
        if legacy_row is not None:
            try:
                candidate = json.loads(legacy_row["value"])
                if isinstance(candidate, dict):
                    legacy = candidate
            except (TypeError, ValueError, json.JSONDecodeError):
                pass
        default = dict(config.get("default_deadline") or {})
        fields = self._deadline_fields(
            legacy,
            fallback=default,
            updated_ts=float(legacy_row["updated_ts"]) if legacy_row is not None else None,
        )
        if fields is None:
            return []
        fields.update({"id": "primary", "tone": "silver"})
        return [fields]

    @staticmethod
    def _sort_deadlines(deadlines: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Return a stable, soonest-first view without changing each timer's tone.

        Python's sort is stable, so an item appended with the same deadline stays
        behind the already registered items.  Do not add an ID tie-breaker here:
        generated IDs are random and would make equal-time timers jump around.
        """
        return sorted(
            deadlines,
            key=lambda item: parse_iso_datetime(item.get("deadline_at")) or float("inf"),
        )

    def _persist_deadlines(
        self,
        conn: sqlite3.Connection,
        deadlines: list[dict[str, Any]],
        ts: float,
    ) -> None:
        conn.execute(
            """
            insert into dashboard_settings(key, value, updated_ts)
            values (?, ?, ?)
            on conflict(key) do update set value=excluded.value, updated_ts=excluded.updated_ts
            """,
            (
                CONFERENCE_DEADLINES_KEY,
                json.dumps(
                    self._sort_deadlines(deadlines),
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
                ts,
            ),
        )

    def deadlines(self, config: dict[str, Any]) -> list[dict[str, Any]]:
        with self.lock, closing(self.connect()) as conn, conn:
            return self._load_deadlines(conn, config)

    def deadline(self, config: dict[str, Any]) -> dict[str, Any]:
        """Compatibility view for older callers that expect one deadline."""
        deadlines = self.deadlines(config)
        return deadlines[0] if deadlines else {}

    def update_deadline(
        self,
        title: Any,
        deadline_at: Any,
        url: Any,
        *,
        deadline_id: Any = None,
        config: dict[str, Any] | None = None,
        mode: Any = "scheduled",
        tba_text: Any = "",
    ) -> dict[str, Any]:
        title_text = clean_text(title, 64)
        deadline_ts = parse_iso_datetime(deadline_at)
        url_text = clean_text(url, 500)
        if not title_text:
            raise ValueError("타이머 제목을 입력해 주세요.")
        if mode not in ("scheduled", "tba"):
            raise ValueError("타이머 일정 유형이 올바르지 않습니다.")
        tba_text_value = clean_text(tba_text, 32)
        if mode == "tba":
            if not isinstance(tba_text, str) or not tba_text_value or len(tba_text.strip()) > 32:
                raise ValueError("예정 일정을 32자 이내로 입력해 주세요.")
            deadline_ts = None
        else:
            if deadline_ts is None:
                raise ValueError("마감 시각을 다시 선택해 주세요.")
            if deadline_ts <= now_ts() - 365 * 86400 or deadline_ts > now_ts() + 10 * 365 * 86400:
                raise ValueError("마감 시각은 과거 1년부터 향후 10년 범위에서 설정해 주세요.")
        if safe_http_url(url_text) != url_text:
            raise ValueError("연결 주소는 http 또는 https URL이어야 합니다.")
        ts = now_ts()
        fields = {
            "title": title_text,
            "deadline_at": iso(deadline_ts),
            "mode": mode,
            "tba_text": tba_text_value if mode == "tba" else "",
            "url": url_text,
            "updated_at": iso(ts),
        }
        with self.lock, closing(self.connect()) as conn, conn:
            deadlines = self._load_deadlines(conn, config or {})
            requested_id = clean_text(deadline_id, 32)
            if deadline_id is not None and not CONFERENCE_DEADLINE_ID_RE.fullmatch(requested_id):
                raise ValueError("타이머 식별자가 올바르지 않습니다.")
            if requested_id:
                target_index = next(
                    (index for index, item in enumerate(deadlines) if item["id"] == requested_id),
                    None,
                )
                if target_index is None:
                    raise LookupError("수정할 타이머를 찾을 수 없습니다.")
                updated = {
                    **fields,
                    "id": deadlines[target_index]["id"],
                    "tone": deadlines[target_index]["tone"],
                }
                deadlines[target_index] = updated
            else:
                if len(deadlines) >= MAX_CONFERENCE_DEADLINES:
                    raise ValueError(f"타이머는 최대 {MAX_CONFERENCE_DEADLINES}개까지 등록할 수 있습니다.")
                used_ids = {item["id"] for item in deadlines}
                generated_id = secrets.token_hex(8)
                while generated_id in used_ids:
                    generated_id = secrets.token_hex(8)
                used_tones = {item["tone"] for item in deadlines}
                tone = next(tone for tone in CONFERENCE_DEADLINE_TONES if tone not in used_tones)
                updated = {**fields, "id": generated_id, "tone": tone}
                deadlines.append(updated)
            self._persist_deadlines(conn, deadlines, ts)
        return updated

    def delete_deadline(
        self,
        deadline_id: Any,
        config: dict[str, Any],
    ) -> list[dict[str, Any]]:
        requested_id = clean_text(deadline_id, 32)
        if not CONFERENCE_DEADLINE_ID_RE.fullmatch(requested_id):
            raise LookupError("삭제할 타이머를 찾을 수 없습니다.")
        ts = now_ts()
        with self.lock, closing(self.connect()) as conn, conn:
            deadlines = self._load_deadlines(conn, config)
            remaining = [item for item in deadlines if item["id"] != requested_id]
            if len(remaining) == len(deadlines):
                raise LookupError("삭제할 타이머를 찾을 수 없습니다.")
            self._persist_deadlines(conn, remaining, ts)
        return self._sort_deadlines(remaining)

    def mark_host(self, host: str, label: str, online: bool, hostname: str | None = None,
                  driver_version: str | None = None, error: str | None = None) -> None:
        with self.lock, closing(self.connect()) as conn, conn:
            self._mark_host(conn, host, label, online, now_ts(), hostname, driver_version, error)

    def _mark_host(self, conn, host, label, online, ts, hostname=None, driver_version=None, error=None):
        hostname = clean_text(hostname, 255) or None
        driver_version = clean_text(driver_version, 256) or None
        error = safe_probe_error(error) if error else None
        row = conn.execute("select * from host_runtime where host=?", (host,)).fetchone()
        failures = 0 if online else (int(row["consecutive_failures"] or 0) + 1 if row else 1)
        conn.execute(
            """
            insert into host_runtime(host,label,online,hostname,driver_version,last_seen_ts,last_error_ts,last_error,consecutive_failures)
            values (?,?,?,?,?,?,?,?,?)
            on conflict(host) do update set
                label=excluded.label,online=excluded.online,
                hostname=coalesce(excluded.hostname,host_runtime.hostname),
                driver_version=coalesce(excluded.driver_version,host_runtime.driver_version),
                last_seen_ts=coalesce(excluded.last_seen_ts,host_runtime.last_seen_ts),
                last_error_ts=excluded.last_error_ts,last_error=excluded.last_error,
                consecutive_failures=excluded.consecutive_failures,
                gpu_errors_json='{}',usable_gpu_indices_json=null,gpu_health_json='{}'
            """,
            (host,label,int(online),hostname,driver_version,ts if online else None,
             None if online else ts,None if online else error,failures))
        # Initial success is not recovery. Initial failure is DOWN; repeated
        # failures/successes emit no duplicate events, including after restart.
        if (row is None and not online) or (row is not None and bool(row["online"]) != online):
            conn.execute("insert into events(ts,host,gpu_index,event,note) values (?,?,null,?,?)",
                         (ts,host,"host_recovered" if online else "host_down",
                          observation_event_details(row["last_error"] if online else error,
                                                    row["consecutive_failures"] if online else failures)))


    def update_gpu(self, host: str, gpu: dict[str, Any], busy: bool, max_observation_gap_seconds: float | None = None) -> None:
        with self.lock, closing(self.connect()) as conn, conn:
            self._update_gpu(conn, host, gpu, busy, now_ts(), max_observation_gap_seconds)

    def _update_gpu(
        self,
        conn: sqlite3.Connection,
        host: str,
        gpu: dict[str, Any],
        busy: bool,
        ts: float,
        max_observation_gap_seconds: float | None = None,
    ) -> None:
        normalized_gpu = sanitize_gpu_snapshot(gpu)
        if normalized_gpu is None:
            raise ValueError("invalid GPU snapshot")
        gpu = normalized_gpu
        processes = gpu["processes"]
        users = confirmed_process_users(processes)
        user_text = ", ".join(users) if users else None
        gpu_index = int(gpu["index"])

        with nullcontext(conn) as conn:
            row = conn.execute(
                "select * from gpu_runtime where host=? and gpu_index=?",
                (host, gpu_index),
            ).fetchone()
            incoming_uuid = clean_text(gpu.get("uuid"), 128) or None
            if row is not None and row["gpu_uuid"] and incoming_uuid and row["gpu_uuid"] != incoming_uuid:
                conn.execute(
                    "insert into events(ts, host, gpu_index, event, users, processes_json, note) values (?, ?, ?, 'hardware_change', null, null, ?)",
                    (ts, host, gpu_index, "GPU UUID changed; recent per-index intervals reset"),
                )
                conn.execute("delete from gpu_usage_interval where host=? and gpu_index=?", (host, gpu_index))
                conn.execute("delete from gpu_capacity_interval where host=? and gpu_index=?", (host, gpu_index))
                conn.execute("delete from gpu_runtime where host=? and gpu_index=?", (host, gpu_index))
                row = None
            if row is not None and row["last_seen_ts"] is None:
                conn.execute("delete from gpu_runtime where host=? and gpu_index=?", (host, gpu_index))
                row = None
            if row is None:
                proc_json = json.dumps(processes, ensure_ascii=False, separators=(",", ":"))
                public_gpu = {
                    **gpu,
                    "processes": [
                        {key: value for key, value in process.items() if key != "start_identity"}
                        for process in processes
                    ],
                }
                snap_json = json.dumps(public_gpu, ensure_ascii=False, separators=(",", ":"))
                free_since = None if busy else ts
                busy_since = ts if busy else None
                last_user = user_text if busy else None
                last_used = ts if busy else None
                conn.execute(
                    """
                    insert into gpu_runtime(
                        host, gpu_index, gpu_uuid, gpu_name, active, busy, first_seen_ts, last_seen_ts,
                        last_transition_ts, free_since_ts, busy_since_ts, last_user,
                        last_used_ts, last_processes_json, last_snapshot_json
                    )
                    values (?, ?, ?, ?, 1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        host, gpu_index, incoming_uuid, gpu.get("name"), 1 if busy else 0, ts, ts, ts,
                        free_since, busy_since, last_user, last_used, proc_json, snap_json,
                    ),
                )
                conn.execute(
                    "insert into events(ts, host, gpu_index, event, users, processes_json, note) values (?, ?, ?, ?, ?, ?, ?)",
                    (ts, host, gpu_index, "busy_start" if busy else "free_start", user_text, None, "initial observation"),
                )
                return

            was_busy = bool(row["busy"])
            try:
                previous_processes = json.loads(row["last_processes_json"] or "[]")
            except Exception:
                previous_processes = []
            previous_users = confirmed_process_users(previous_processes)
            previous_user_text = ", ".join(previous_users) if previous_users else None
            if busy and carry_verified_process_owners(previous_processes, processes):
                users = confirmed_process_users(processes)
                user_text = ", ".join(users) if users else None
            proc_json = json.dumps(processes, ensure_ascii=False, separators=(",", ":"))
            public_gpu = {
                **gpu,
                "processes": [
                    {key: value for key, value in process.items() if key != "start_identity"}
                    for process in processes
                ],
            }
            snap_json = json.dumps(public_gpu, ensure_ascii=False, separators=(",", ":"))
            free_since = row["free_since_ts"]
            busy_since = row["busy_since_ts"]
            last_user = row["last_user"]
            last_used = row["last_used_ts"]
            observed_seconds = float(row["observed_seconds"] or 0)
            busy_seconds = float(row["busy_seconds"] or 0)
            user_seconds = parse_user_seconds(row["user_busy_json"])
            last_seen = row["last_seen_ts"]
            event = None
            gap_note = None
            observation_gap = False

            if last_seen is not None:
                elapsed = max(0.0, ts - float(last_seen))
                if max_observation_gap_seconds is None or elapsed <= max_observation_gap_seconds:
                    observed_seconds += elapsed
                    try:
                        previous_snapshot = json.loads(row["last_snapshot_json"] or "{}")
                    except Exception:
                        previous_snapshot = {}
                    self._record_capacity_interval(
                        conn,
                        host,
                        gpu_index,
                        float(last_seen),
                        ts,
                        was_busy,
                        previous_snapshot.get("memory_used"),
                        previous_snapshot.get("memory_total"),
                    )
                    if was_busy:
                        busy_seconds += elapsed
                        # Attribute elapsed time from the process evidence saved
                        # at the start of the interval, not from display-only
                        # last_user carryover used for one drain sample.
                        add_user_seconds(user_seconds, previous_users, elapsed)
                        self._record_busy_interval(
                            conn, host, gpu_index, float(last_seen), ts, previous_user_text
                        )
                else:
                    gap_note = f"after observation gap {human_duration(elapsed)}"
                    observation_gap = True

            if observation_gap and busy == was_busy:
                if busy:
                    busy_since = ts
                    free_since = None
                    last_user = user_text
                    last_used = ts
                else:
                    free_since = ts
                    busy_since = None
                event = "observation_gap"

            if busy and not was_busy:
                busy_since = ts
                free_since = None
                # A free -> busy transition is a new session. Never attribute it
                # to the previous session when the new owner is not observable.
                last_user = user_text
                last_used = ts
                event = "busy_start"
            elif not busy and was_busy:
                free_since = ts
                busy_since = None
                last_user = user_text or row["last_user"]
                last_used = ts
                event = "free_start"
            elif busy:
                if user_text and user_text != last_user:
                    last_user = user_text
                    event = "user_change"
                elif not user_text and not same_process_cohort(previous_processes, processes):
                    if not processes and previous_users and last_user:
                        # NVIDIA can report a still-busy drain sample immediately
                        # after its compute process disappears. Preserve the
                        # confirmed last owner for this single display sample so
                        # a following free transition does not erase it. Because
                        # the saved process list is now empty, a second such
                        # sample clears the grace automatically. No user time is
                        # charged for the empty interval above.
                        pass
                    else:
                        # A different/unknown cohort is not proof of continuity.
                        last_user = None
                last_used = ts

            transition_event = event in ("busy_start", "free_start")
            conn.execute(
                """
                update gpu_runtime set
                    gpu_uuid=?, gpu_name=?, active=1, busy=?, last_seen_ts=?, last_transition_ts=?,
                    free_since_ts=?, busy_since_ts=?, last_user=?, last_used_ts=?,
                    last_processes_json=?, last_snapshot_json=?,
                    observed_seconds=?, busy_seconds=?, user_busy_json=?
                where host=? and gpu_index=?
                """,
                (
                    incoming_uuid, gpu.get("name"), 1 if busy else 0, ts,
                    ts if transition_event else row["last_transition_ts"],
                    free_since, busy_since, last_user, last_used,
                    proc_json, snap_json,
                    observed_seconds, busy_seconds,
                    json.dumps(user_seconds, ensure_ascii=False, separators=(",", ":")),
                    host, gpu_index,
                ),
            )
            if event:
                event_users = last_user if event in ("busy_start", "user_change", "observation_gap") else user_text
                conn.execute(
                    "insert into events(ts, host, gpu_index, event, users, processes_json, note) values (?, ?, ?, ?, ?, ?, ?)",
                    (ts, host, gpu_index, event, event_users, None, gap_note),
                )

    def reconcile_host_gpus(self, host: str, observed_indices: set[int]) -> int:
        with self.lock, closing(self.connect()) as conn, conn:
            return self._reconcile_host_gpus(conn, host, observed_indices)

    def _reconcile_host_gpus(
        self,
        conn: sqlite3.Connection,
        host: str,
        observed_indices: set[int],
    ) -> int:
        with nullcontext(conn) as conn:
            if observed_indices:
                active_indices = {
                    int(row["gpu_index"])
                    for row in conn.execute(
                        "select gpu_index from gpu_runtime where host=? and active=1",
                        (host,),
                    ).fetchall()
                }
                missing_indices = sorted(active_indices - observed_indices)
                if not missing_indices:
                    return 0
                cursor = conn.executemany(
                    "update gpu_runtime set active=0 where host=? and gpu_index=? and active=1",
                    ((host, gpu_index) for gpu_index in missing_indices),
                )
                return cursor.rowcount
            return conn.execute(
                "update gpu_runtime set active=0 where host=? and active=1",
                (host,),
            ).rowcount

    def update_disk(self, host: str, disk: dict[str, Any]) -> None:
        with self.lock, closing(self.connect()) as conn, conn:
            self._update_disk(conn, host, disk, now_ts())

    def _update_disk(
        self,
        conn: sqlite3.Connection,
        host: str,
        disk: dict[str, Any],
        ts: float,
    ) -> None:
        disk = sanitize_disk_snapshot(disk)
        snapshot_json = json.dumps(disk, ensure_ascii=False, separators=(",", ":"))
        with nullcontext(conn) as conn:
            conn.execute(
                """
                insert into host_disk(host, updated_ts, snapshot_json, last_attempt_ts, last_error)
                values (?, ?, ?, ?, null)
                on conflict(host) do update set
                    updated_ts=excluded.updated_ts,
                    snapshot_json=excluded.snapshot_json,
                    last_attempt_ts=excluded.last_attempt_ts,
                    last_error=null
                """,
                (host, ts, snapshot_json, ts),
            )

    def mark_disk_error(self, host: str, error: str) -> None:
        with self.lock, closing(self.connect()) as conn, conn:
            conn.execute(
                "insert into host_disk(host,updated_ts,snapshot_json,last_attempt_ts,last_error) values (?,0,'{}',?,?) "
                "on conflict(host) do update set last_attempt_ts=excluded.last_attempt_ts,last_error=excluded.last_error",
                (host, now_ts(), safe_probe_error(error)),
            )

    def apply_host_payload(
        self,
        host: str,
        label: str,
        gpu_states: list[tuple[dict[str, Any], bool]],
        *,
        hostname: str | None = None,
        driver_version: str | None = None,
        disk: dict[str, Any] | None = None,
        max_observation_gap_seconds: float | None = None,
        expected_gpu_indices: set[int] | None = None,
        gpu_errors: dict[int, str] | None = None,
    ) -> None:
        """Atomically publish one successful host observation."""
        prepared: list[tuple[dict[str, Any], bool]] = []
        observed_indices: set[int] = set()
        for gpu, busy in gpu_states:
            normalized = sanitize_gpu_snapshot(gpu)
            if normalized is None:
                raise ValueError("invalid GPU snapshot")
            gpu_index = int(normalized["index"])
            if gpu_index in observed_indices:
                raise ValueError("duplicate GPU index")
            observed_indices.add(gpu_index)
            prepared.append((normalized, bool(busy)))
        normalized_gpu_errors = sanitize_gpu_errors(gpu_errors)
        if normalized_gpu_errors is None:
            raise ValueError("invalid GPU error map")
        unavailable_indices = set(normalized_gpu_errors)
        if observed_indices & unavailable_indices:
            raise ValueError("GPU cannot be both observed and unavailable")
        inventory_indices = observed_indices | unavailable_indices
        if expected_gpu_indices is not None and inventory_indices != expected_gpu_indices:
            raise ValueError("partial GPU payload does not match expected inventory")
        if normalized_gpu_errors:
            self.mark_host(host, label, False, error=next(iter(normalized_gpu_errors.values())))
            return
        if not prepared:
            raise ValueError("empty GPU payload")
        normalized_disk = sanitize_disk_snapshot(disk) if disk is not None else None
        ts = now_ts()
        with self.lock, closing(self.connect()) as conn, conn:
            previous_host = conn.execute(
                "select * from host_runtime where host=?", (host,)
            ).fetchone()
            for gpu, busy in prepared:
                index = int(gpu["index"])
                previously_observed = previous_host is None or gpu_is_observed(dict(previous_host), index)
                gap_limit = max_observation_gap_seconds if previously_observed else 0
                self._update_gpu(conn, host, gpu, busy, ts, gap_limit)
            # Only a complete observation can replace the host inventory.
            self._reconcile_host_gpus(conn, host, inventory_indices)
            if normalized_disk is not None:
                self._update_disk(conn, host, normalized_disk, ts)
            self._mark_host(
                conn,
                host,
                label,
                True,
                ts,
                hostname,
                driver_version,
                None,
            )

    def snapshot(self, config: dict[str, Any]) -> dict[str, Any]:
        ts = now_ts()
        with self.lock, closing(self.connect()) as conn, conn:
            host_rows = {
                row["host"]: dict(row)
                for row in conn.execute("select * from host_runtime")
            }
            gpu_rows = conn.execute("select * from gpu_runtime where active=1 order by host, gpu_index").fetchall()
            disk_rows = {
                row["host"]: dict(row)
                for row in conn.execute("select * from host_disk")
            }
            recent_usage_by_host = self._recent_usage_by_host(conn, gpu_rows, host_rows, ts, config)
            recent_capacity_by_host = self._recent_capacity_by_host(conn, ts, config)

        usage_by_host: dict[str, dict[str, Any]] = {}
        by_host: dict[str, list[dict[str, Any]]] = {}
        tracking_started_by_host: dict[str, float | None] = {}
        usage_gap = float(config.get("usage_gap_seconds", max(120, config["poll_interval_seconds"] * 6)))
        recent_window_seconds = float(config.get("recent_usage_window_seconds", 7 * 86400))
        for row in gpu_rows:
            item = dict(row)
            runtime = host_rows.get(item["host"], {})
            gpu = json.loads(item.get("last_snapshot_json") or "{}")
            free_since = item.get("free_since_ts")
            busy_since = item.get("busy_since_ts")
            observed_seconds = float(item.get("observed_seconds") or 0)
            busy_seconds = float(item.get("busy_seconds") or 0)
            user_seconds = parse_user_seconds(item.get("user_busy_json"))
            last_seen = item.get("last_seen_ts")
            if last_seen is not None:
                live_elapsed = max(0.0, ts - float(last_seen))
                if live_elapsed <= usage_gap and gpu_is_observed(runtime, item["gpu_index"]):
                    observed_seconds += live_elapsed
                    if item["busy"]:
                        busy_seconds += live_elapsed
                        add_user_seconds(user_seconds, confirmed_process_users(json.loads(item.get("last_processes_json") or "[]")), live_elapsed)

            host_usage = usage_by_host.setdefault(item["host"], {
                "observed_seconds": 0.0,
                "busy_seconds": 0.0,
                "user_seconds": {},
            })
            host_usage["observed_seconds"] += observed_seconds
            host_usage["busy_seconds"] += busy_seconds
            for user, seconds in user_seconds.items():
                host_usage["user_seconds"][user] = host_usage["user_seconds"].get(user, 0.0) + seconds

            first_seen = item.get("first_seen_ts")
            if item["host"] not in tracking_started_by_host:
                tracking_started_by_host[item["host"]] = (
                    None if first_seen is None else float(first_seen)
                )
            elif tracking_started_by_host[item["host"]] is not None:
                if first_seen is None:
                    tracking_started_by_host[item["host"]] = None
                else:
                    tracking_started_by_host[item["host"]] = max(
                        tracking_started_by_host[item["host"]],
                        float(first_seen),
                    )

            gpu.update({
                "available": True,
                "last_seen": iso(last_seen),
                "busy": bool(item["busy"]),
                "free_since": iso(free_since),
                "free_for_seconds": None if item["busy"] or free_since is None else ts - free_since,
                "free_for": "busy" if item["busy"] else human_duration(None if free_since is None else ts - free_since),
                "busy_since": iso(busy_since),
                "busy_for_seconds": None if not item["busy"] or busy_since is None else ts - busy_since,
                "busy_for": human_duration(None if busy_since is None else ts - busy_since) if item["busy"] else None,
                "last_user": item.get("last_user"),
                "last_used_at": iso(item.get("last_used_ts")),
                "last_used_ago": human_duration(None if item.get("last_used_ts") is None else ts - item["last_used_ts"]),
                "usage_observed_seconds": observed_seconds,
                "usage_busy_seconds": busy_seconds,
                "usage_busy_index": None if observed_seconds <= 0 else round((busy_seconds / observed_seconds) * 100),
            })
            by_host.setdefault(item["host"], []).append(gpu)

        hosts = []
        for host in config["hosts"]:
            name = host["name"]
            runtime = host_rows.get(name, {})
            disk_row = disk_rows.get(name)
            disk = None
            disk_updated_ts = None
            if disk_row:
                disk_updated_ts = disk_row.get("updated_ts") or None
                try:
                    disk = sanitize_disk_snapshot(json.loads(disk_row.get("snapshot_json") or "{}")) if disk_updated_ts is not None else None
                except Exception:
                    disk = {"filesystems": [], "users": [], "errors": ["bad disk snapshot"]}
            usage = usage_by_host.get(name, {"observed_seconds": 0.0, "busy_seconds": 0.0, "user_seconds": {}})
            observed_seconds = usage["observed_seconds"]
            busy_seconds = usage["busy_seconds"]
            top_users = user_share_rows(usage["user_seconds"], busy_seconds, 3)
            recent_usage = recent_usage_by_host.get(name, {
                "busy_seconds": 0.0,
                "active_seconds": 0.0,
                "meaningful_active_seconds": 0.0,
                "last_meaningful_used_ts": None,
                "user_seconds": {},
                "reportable_unassigned_seconds": 0.0,
            })
            recent_busy_seconds = recent_usage["busy_seconds"]
            recent_active_seconds = recent_usage["active_seconds"]
            meaningful_active_seconds = recent_usage["meaningful_active_seconds"]
            recent_user_seconds = dict(recent_usage["user_seconds"])
            recent_top_users = recent_user_share_rows(
                recent_user_seconds,
                recent_usage["reportable_unassigned_seconds"],
            )
            recent_capacity = recent_capacity_by_host.get(name, {
                "observed_gpu_seconds": 0.0,
                "busy_gpu_seconds": 0.0,
                "observed_wall_seconds": 0.0,
            })
            capacity_observed_seconds = recent_capacity["observed_gpu_seconds"]
            capacity_busy_seconds = recent_capacity["busy_gpu_seconds"]
            capacity_observed_wall_seconds = recent_capacity["observed_wall_seconds"]
            host_gpus = host_gpu_view(by_host.get(name, []), runtime, host.get("expected_gpu_count", 0))
            tracking_started_ts = tracking_started_by_host.get(name)
            tracking_span_seconds = (
                None
                if tracking_started_ts is None
                else max(0.0, ts - tracking_started_ts)
            )
            hosts.append({
                "name": name,
                "label": host.get("label", name),
                "ip_address": host_ip_for_display(host),
                "lab": lab_id_for(host),
                "note": host.get("note", ""),
                "owner": host.get("owner", ""),
                "owner_type": host.get("owner_type", ""),
                "location": host.get("location", ""),
                "online": gpu_is_observed(runtime, 0),
                "availability_state": "up" if gpu_is_observed(runtime, 0) else "down",
                "hostname": runtime.get("hostname"),
                "driver_version": runtime.get("driver_version"),
                "last_seen": iso(runtime.get("last_seen_ts")),
                "last_error": runtime.get("last_error"),
                "last_error_at": iso(runtime.get("last_error_ts")),
                "consecutive_failures": int(runtime.get("consecutive_failures") or 0),
                "gpus": host_gpus,
                "usage": {
                    "busy_index": None if observed_seconds <= 0 else round((busy_seconds / observed_seconds) * 100),
                    "observed_seconds": observed_seconds,
                    "busy_seconds": busy_seconds,
                    "observed_for": human_duration(observed_seconds),
                    "busy_for": human_duration(busy_seconds),
                    "top_users": top_users,
                },
                "recent_usage": {
                    "window_days": round(recent_window_seconds / 86400, 1),
                    "busy_seconds": recent_busy_seconds,
                    "busy_for": human_duration(recent_busy_seconds),
                    "active_seconds": recent_active_seconds,
                    "active_for": human_duration(recent_active_seconds),
                    "active_percent": round((recent_active_seconds / recent_window_seconds) * 100, 1),
                    "meaningful_active_seconds": meaningful_active_seconds,
                    "meaningful_active_for": human_duration(meaningful_active_seconds),
                    "last_meaningful_used_at": iso(recent_usage["last_meaningful_used_ts"]),
                    "minimum_session_seconds": config["activity_policy"]["cold_min_session_seconds"],
                    "tracking_started_at": iso(tracking_started_ts),
                    "tracking_span_seconds": tracking_span_seconds,
                    "tracking_for": human_duration(tracking_span_seconds),
                    "top_users": recent_top_users,
                },
                "recent_capacity": {
                    "window_days": round(recent_window_seconds / 86400, 1),
                    "busy_index": None if capacity_observed_seconds <= 0 else round((capacity_busy_seconds / capacity_observed_seconds) * 100),
                    "observed_gpu_seconds": capacity_observed_seconds,
                    "busy_gpu_seconds": capacity_busy_seconds,
                    "observed_wall_seconds": capacity_observed_wall_seconds,
                    "observed_for": human_duration(capacity_observed_seconds),
                    "busy_for": human_duration(capacity_busy_seconds),
                    "observed_wall_for": human_duration(capacity_observed_wall_seconds),
                },
                "disk": disk,
                "disk_updated_at": iso(disk_updated_ts),
                **disk_collection_status(disk_row, ts, config["disk_poll_interval_seconds"]),
                "disk_age_seconds": None if disk_updated_ts is None else ts - disk_updated_ts,
            })
        deadlines = self.deadlines(config)
        return {
            "now": iso(ts),
            "build_version": config["build_version"],
            "release_fingerprint": config.get("release_fingerprint", ""),
            "runtime_mode": config.get("runtime_mode", "standalone"),
            "poll_interval_seconds": config["poll_interval_seconds"],
            "disk_poll_interval_seconds": config["disk_poll_interval_seconds"],
            "activity_policy": dict(config["activity_policy"]),
            "labs": config.get("labs", []),
            "announcements": self.announcements(ts),
            "deadlines": deadlines,
            "deadline": deadlines[0] if deadlines else None,
            "hosts": hosts,
        }

    def insights(self, config: dict[str, Any], ts: float | None = None) -> dict[str, Any]:
        now = now_ts() if ts is None else float(ts)
        cache_seconds = max(0.0, float(config.get("insights_cache_seconds", 60)))
        if ts is None:
            with self._cache_lock:
                if self._insights_cache is not None and now - self._insights_cache_ts < cache_seconds:
                    return self._insights_cache

        pulse_hours = 24
        bucket_seconds = 3600.0
        current_hour_start = int(now // bucket_seconds) * bucket_seconds
        window_start = current_hour_start - ((pulse_hours - 1) * bucket_seconds)
        today = datetime.fromtimestamp(now, KST).date()
        daily_start = today - timedelta(days=29)
        host_config = {host["name"]: host for host in config["hosts"]}

        with self.lock, closing(self.connect()) as conn, conn:
            self._refresh_daily_vram_rollups(conn, config, now, days=2)
            rows = [
                dict(row)
                for row in conn.execute(
                    """
                    select host, gpu_index, start_ts, end_ts, busy
                         , memory_used_mib, memory_total_mib
                    from gpu_capacity_interval
                    where end_ts>? and start_ts<?
                    order by host, gpu_index, start_ts
                    """,
                    (window_start, now),
                ).fetchall()
            ]
            gpu_rows = conn.execute(
                "select host, gpu_index, last_snapshot_json from gpu_runtime where active=1"
            ).fetchall()
            daily_rows = [
                dict(row)
                for row in conn.execute(
                    """
                    select lab_id, day, average_used_gb, observed_seconds, complete
                    from lab_daily_index
                    where day>=?
                    order by lab_id, day
                    """,
                    (daily_start.isoformat(),),
                ).fetchall()
            ]

        gpu_memory_gb: dict[tuple[str, int], float] = {}
        for row in gpu_rows:
            try:
                snapshot = json.loads(row["last_snapshot_json"] or "{}")
                memory_mib = max(0.0, float(snapshot.get("memory_total") or 0))
            except Exception:
                memory_mib = 0.0
            gpu_memory_gb[(row["host"], int(row["gpu_index"]))] = memory_mib / 1024

        rows_by_host: dict[str, list[dict[str, Any]]] = {}
        for row in rows:
            if row["host"] in host_config:
                rows_by_host.setdefault(row["host"], []).append(row)
        daily_by_lab = {
            (str(row["lab_id"]), str(row["day"])): row
            for row in daily_rows
        }

        labs_payload = []
        for lab in config.get("labs", []):
            lab_id = str(lab.get("id") or "default")
            lab_hosts = [name for name, host in host_config.items() if lab_id_for(host) == lab_id]
            lab_rows = [row for name in lab_hosts for row in rows_by_host.get(name, [])]
            total_gpu_gb = sum(
                memory_gb
                for (host_name, _), memory_gb in gpu_memory_gb.items()
                if host_name in lab_hosts
            )
            hourly = _hourly_vram_pulse(
                lab_rows, total_gpu_gb, window_start, pulse_hours, bucket_seconds
            )[-24:]
            daily_trend = []
            for offset in range(30):
                day_value = daily_start + timedelta(days=offset)
                stored = daily_by_lab.get((lab_id, day_value.isoformat()))
                daily_trend.append({
                    "day": day_value.isoformat(),
                    "label": day_value.strftime("%m-%d"),
                    "index": None if stored is None else round(float(stored["average_used_gb"]), 2),
                    "observed_hours": 0.0 if stored is None else round(float(stored["observed_seconds"]) / 3600, 1),
                    "complete": bool(stored["complete"]) if stored is not None else False,
                })
            latest_trend = daily_trend[-1]
            previous_trend = daily_trend[-2]
            daily_index = latest_trend.get("index")
            previous_daily_index = previous_trend.get("index")
            labs_payload.append({
                "id": lab_id,
                "label": lab.get("label") or lab_id.upper(),
                "total_gpu_gb": round(total_gpu_gb, 1),
                "max_index": round(total_gpu_gb, 1),
                "daily_index": daily_index,
                "previous_daily_index": previous_daily_index,
                "previous_rolling_index": previous_daily_index,
                "observed_hours": latest_trend.get("observed_hours") or 0,
                "previous_observed_hours": previous_trend.get("observed_hours") or 0,
                "comparison_ready": daily_index is not None and previous_daily_index is not None,
                "hourly": hourly,
                "daily_trend": daily_trend,
            })

        payload = {
            "now": iso(now),
            "period_seconds": int(bucket_seconds),
            "range_hours": 24,
            "range_days": 30,
            "change_period_seconds": 86400,
            "labs": labs_payload,
        }
        if ts is None:
            with self._cache_lock:
                self._insights_cache_ts = now
                self._insights_cache = payload
        return payload

    def events(
        self,
        limit: int = 100,
        offset: int = 0,
        hosts: list[str] | None = None,
        host: str | None = None,
        user: str | None = None,
        date: str | None = None,
        include_availability: bool = True,
    ) -> dict[str, Any]:
        limit = min(max(int(limit), 1), 300)
        offset = max(int(offset), 0)
        if offset > MAX_EVENT_OFFSET:
            raise ValueError("event offset is too large")
        if hosts is not None and not hosts:
            return {
                "events": [],
                "limit": limit,
                "offset": offset,
                "next_offset": None,
                "prev_offset": max(0, offset - limit) if offset else None,
                "has_more": False,
            }
        host = host.strip() if host else None
        user = user.strip() if user else None
        date = date.strip() if date else None
        if date and local_date_bounds(date) is None:
            raise ValueError("잘못된 날짜 형식입니다.")
        hosts_json = json.dumps(hosts, ensure_ascii=False) if hosts is not None else None
        user_pattern = None
        if user:
            escaped_user = user.lower().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            user_pattern = f"%{escaped_user}%"
        date_bounds = local_date_bounds(date)
        date_start, date_end = date_bounds if date_bounds is not None else (None, None)
        with self.lock, closing(self.connect()) as conn, conn:
            rows = conn.execute(
                """
                select * from events
                where event in ('busy_start', 'free_start', 'host_down', 'host_recovered', 'gpu_down', 'gpu_recovered')
                  and (:include_availability or event in ('busy_start', 'free_start'))
                  and (:hosts_json is null or host in (select value from json_each(:hosts_json)))
                  and (:host is null or host=:host)
                  and (:user_pattern is null or lower(coalesce(users, '')) like :user_pattern escape '\\')
                  and (:date_start is null or (ts>=:date_start and ts<:date_end))
                order by ts desc, id desc
                limit :fetch_limit offset :offset
                """,
                {
                    "hosts_json": hosts_json,
                    "host": host,
                    "user_pattern": user_pattern,
                    "date_start": date_start,
                    "date_end": date_end,
                    "include_availability": bool(include_availability),
                    "fetch_limit": limit + 1,
                    "offset": offset,
                },
            ).fetchall()
        has_more = len(rows) > limit and offset + limit <= MAX_EVENT_OFFSET
        rows = rows[:limit]
        result = []
        for row in rows:
            item = dict(row)
            item["time"] = iso(item.pop("ts"))
            if item["event"] in {"gpu_down","gpu_recovered","host_down","host_recovered","observation_lost","observation_resumed"}:
                details = event_details(item.get("note"))
                item["details"] = details
                item["gpu_indices"] = details.get("gpu_indices", [item["gpu_index"]] if item["gpu_index"] is not None else [])
            try:
                item["processes"] = json.loads(item.pop("processes_json") or "[]")
            except Exception:
                item["processes"] = []
            result.append(item)
        return {
            "events": result,
            "limit": limit,
            "offset": offset,
            "next_offset": offset + limit if has_more else None,
            "prev_offset": max(0, offset - limit) if offset else None,
            "has_more": has_more,
        }


class Collector:
    def __init__(self, store: Store, config: dict[str, Any]):
        self.store = store
        self.config = config
        self.stop_event = threading.Event()
        self.last_disk_probe_by_host: dict[str, float] = {}
        self.disk_pool = ThreadPoolExecutor(max_workers=max(1, int(config.get("disk_probe_workers", 2))))
        self.disk_futures: dict[str, tuple[dict[str, Any], Any]] = {}
        self.gpu_workers = max(1, int(config.get("collector_workers", 8)))
        self.gpu_pool = ThreadPoolExecutor(max_workers=self.gpu_workers)
        self.gpu_futures: dict[str, tuple[dict[str, Any], Any, float]] = {}
        self.thread = threading.Thread(target=self.run, name="gpu-watch-collector", daemon=True)
        self.status_lock = threading.Lock()
        self.process_lock = threading.Lock()
        self.running_processes: set[subprocess.Popen[Any]] = set()
        self.last_cycle_started_at: float | None = None
        self.last_cycle_completed_at: float | None = None
        self.last_cycle_duration_seconds: float | None = None
        self.last_cycle_error: str | None = None
        self.consecutive_cycle_errors = 0
        self.host_failures: dict[str, int] = {}
        self.host_next_probe_at: dict[str, float] = {}

    def start(self) -> None:
        with self.store.lock, closing(self.store.connect()) as conn:
            for row in conn.execute("select host,consecutive_failures from host_runtime"):
                self.host_failures[row["host"]] = int(row["consecutive_failures"] or 0)
        self.thread.start()

    def stop(self, timeout: float = 7.0) -> None:
        self.stop_event.set()
        with self.process_lock:
            running = list(self.running_processes)
        for process in running:
            try:
                process.terminate()
            except OSError:
                pass
        deadline = time.monotonic() + min(2.0, max(0.0, float(timeout)))
        for process in running:
            try:
                process.wait(timeout=max(0.0, deadline - time.monotonic()))
            except subprocess.TimeoutExpired:
                try:
                    process.kill()
                except OSError:
                    pass
        self.disk_pool.shutdown(wait=False, cancel_futures=True)
        self.gpu_pool.shutdown(wait=False, cancel_futures=True)
        if self.thread.is_alive() and self.thread is not threading.current_thread():
            self.thread.join(max(0.0, float(timeout)))

    def run(self) -> None:
        while not self.stop_event.is_set():
            started_at = now_ts()
            started_monotonic = time.monotonic()
            with self.status_lock:
                self.last_cycle_started_at = started_at
            try:
                self.collect_tick()
                error = None
            except Exception as exc:  # keep a single collection failure from killing monitoring
                error = type(exc).__name__
            completed_at = now_ts()
            with self.status_lock:
                self.last_cycle_completed_at = completed_at
                self.last_cycle_duration_seconds = max(0.0, completed_at - started_at)
                self.last_cycle_error = error
                if error:
                    self.consecutive_cycle_errors += 1
                else:
                    self.consecutive_cycle_errors = 0
            if error:
                audit("collector_cycle_error", error_type=error, failures=self.consecutive_cycle_errors)
            elapsed = time.monotonic() - started_monotonic
            delay = max(0.05, 1.0 - elapsed)
            self.stop_event.wait(delay)

    def status(self) -> dict[str, Any]:
        with self.status_lock:
            return {
                "alive": self.thread.is_alive(),
                "last_cycle_started_at": self.last_cycle_started_at,
                "last_cycle_completed_at": self.last_cycle_completed_at,
                "last_cycle_duration_seconds": self.last_cycle_duration_seconds,
                "last_cycle_error": self.last_cycle_error,
                "consecutive_cycle_errors": self.consecutive_cycle_errors,
            }

    def collect_tick(self) -> None:
        """Drain completed hosts and schedule due hosts without a global barrier."""
        self.collect_finished_disk_probes()
        for name, (host, future, started) in list(self.gpu_futures.items()):
            if not future.done():
                continue
            self.gpu_futures.pop(name, None)
            self.consume_gpu_result(host, future)
            self.host_next_probe_at[name] = max(
                self.host_next_probe_at.get(name, 0),
                started + float(self.config["poll_interval_seconds"]),
                time.monotonic() + 1.0,
            )
        if self.stop_event.is_set():
            return
        # Bound both workers and queued work. Oldest due hosts run first, so
        # slow hosts cannot keep ready hosts behind a growing executor queue.
        now = time.monotonic()
        due = sorted(
            (host for host in self.config["hosts"]
             if host["name"] not in self.gpu_futures
             and self.host_next_probe_at.get(host["name"], 0) <= now),
            key=lambda host: self.host_next_probe_at.get(host["name"], 0),
        )
        for host in due[:max(0, self.gpu_workers - len(self.gpu_futures))]:
            self.gpu_futures[host["name"]] = (
                host, self.gpu_pool.submit(self.probe_host, host, False), time.monotonic())
        # Stagger disk work behind the first completed GPU attempt. Disk must
        # still refresh when SSH works but NVML is broken; an unavailable host
        # costs at most one extra disk connection per disk interval.
        disk_hosts = [host for host in self.config["hosts"]
                      if host["name"] in self.host_next_probe_at
                      and host["name"] not in self.gpu_futures]
        self.start_due_disk_probes(disk_hosts)

    def consume_gpu_result(self, host: dict[str, Any], future: Any) -> None:
        if self.stop_event.is_set():
            return
        try:
            self.handle_payload(host, future.result())
        except Exception as exc:
            # Validation/worker errors follow the same persisted failure,
            # audit and backoff path as transport failures.
            self.handle_payload(host, {"ok": False, "error": safe_probe_error(exc)})

    def collect_once(self) -> None:
        """Blocking one-shot collection for offline diagnostics; run uses collect_tick."""
        self.collect_finished_disk_probes()
        hosts = [host for host in self.config["hosts"]
                 if self.host_next_probe_at.get(host["name"], 0) <= time.monotonic()]
        pool = ThreadPoolExecutor(max_workers=self.gpu_workers)
        try:
            futures = {pool.submit(self.probe_host, host, False): host for host in hosts}
            for future in as_completed(futures):
                if self.stop_event.is_set():
                    break
                self.consume_gpu_result(futures[future], future)
        finally:
            pool.shutdown(wait=not self.stop_event.is_set(), cancel_futures=True)
        self.collect_finished_disk_probes()
        self.start_due_disk_probes(self.config["hosts"])

    def start_due_disk_probes(self, hosts: list[dict[str, Any]]) -> None:
        for host in hosts:
            name = host["name"]
            if name in self.disk_futures:
                continue
            if self.should_probe_disk(host):
                self.disk_futures[name] = (host, self.disk_pool.submit(self.probe_host, host, True))

    def collect_finished_disk_probes(self) -> None:
        for name, (host, future) in list(self.disk_futures.items()):
            if not future.done():
                continue
            self.disk_futures.pop(name, None)
            try:
                payload = future.result()
            except Exception as exc:
                audit("disk_probe_future_error", host=name, error_type=type(exc).__name__)
                self.store.mark_disk_error(name, type(exc).__name__)
                continue
            if payload.get("ok") and "disk" in payload:
                self.store.update_disk(name, payload.get("disk") or {})
            else:
                self.store.mark_disk_error(name, payload.get("error") or "disk probe failed")

    def should_probe_disk(self, host: dict[str, Any]) -> bool:
        interval = self.config["disk_poll_interval_seconds"]
        if interval <= 0:
            return False
        name = host["name"]
        ts = now_ts()
        last = self.last_disk_probe_by_host.get(name, 0)
        if ts - last >= interval:
            self.last_disk_probe_by_host[name] = ts
            return True
        return False

    def _probe_invocation(self, host: dict[str, Any], include_disk: bool) -> tuple[str, float]:
        # The uncompressed Base64 probe is larger than Windows' 32,767-character
        # CreateProcess command-line limit.  That made the Windows emergency
        # runtime fail before OpenSSH could start.  zlib is available in both
        # the local Python runtime and every supported remote Python 3 runtime,
        # and keeps the fixed remote command comfortably below that limit.
        if include_disk and host.get("privileged_disk_helper"):
            return "sudo -n /usr/local/libexec/gpu-watch-disk", float(self.config.get("ssh_disk_probe_timeout_seconds", 720))
        encoded = REMOTE_DISK_PROBE_B64_ZLIB if include_disk else REMOTE_PROBE_B64_ZLIB
        remote_timeout = int(self.config.get("remote_probe_command_timeout_seconds", 12))
        probe_args = (
            f" --remote-timeout={remote_timeout}"
            f" --expected-gpu-count={int(host.get('expected_gpu_count') or 0)}"
            f" --busy-memory-threshold-mib={int(self.config['busy_memory_threshold_mib'])}"
            f" --busy-utilization-threshold-percent={int(self.config['busy_utilization_threshold_percent'])}"
        )
        if host.get("temporary_driver_fallback"):
            fallback = base64.b64encode(json.dumps(host["temporary_driver_fallback"], separators=(",", ":")).encode()).decode("ascii")
            probe_args += f" --temporary-driver-b64={fallback}"
        if include_disk:
            probe_args += f" --disk --du-timeout={self.config['disk_du_timeout_seconds']}"
            collect_docker = host.get("collect_docker_usage")
            if collect_docker is None:
                collect_docker = lab_id_for(host) == "nll"
            if collect_docker:
                docker_timeout = int(self.config.get("docker_usage_timeout_seconds", 30))
                probe_args += f" --docker-usage --docker-timeout={docker_timeout}"
            if host.get("disk_user_paths"):
                path_bytes = json.dumps(
                    host["disk_user_paths"], ensure_ascii=False, separators=(",", ":")
                ).encode("utf-8")
                probe_args += f" --disk-user-paths-b64={base64.b64encode(path_bytes).decode('ascii')}"
        probe_loader = (
            "python3 -c 'import sys,zlib;"
            "exec(compile(zlib.decompress(sys.stdin.buffer.read()),"
            '"<gpu-watch-probe>","exec"))\''
        )
        command = f"echo {encoded} | base64 -d | {probe_loader}{probe_args}"
        timeout_key = "ssh_disk_probe_timeout_seconds" if include_disk else "ssh_gpu_probe_timeout_seconds"
        timeout_default = self.config["disk_du_timeout_seconds"] + 45 if include_disk else 45
        return command, float(self.config.get(timeout_key, timeout_default))

    @staticmethod
    def _probe_password(host: dict[str, Any]) -> tuple[str | None, str | None]:
        env_name = str(host.get("ssh_password_env") or "").strip()
        password_file = str(host.get("ssh_password_file") or "").strip()
        # A configured file is authoritative. In particular, never let a
        # stale inherited user environment override an emergency secret file.
        if password_file:
            return read_password_file(password_file), password_file
        return (os.environ.get(env_name), env_name) if env_name else (None, None)

    def _ssh_command(self, host: dict[str, Any], command: str, password: str | None) -> list[str]:
        ssh_cmd = [
            trusted_ssh_executable(),
            "-o", f"ConnectTimeout={self.config['ssh_timeout_seconds']}",
            "-o", "ConnectionAttempts=1",
        ]
        ssh_config_file = str(self.config.get("ssh_config_file") or "").strip()
        if ssh_config_file:
            ssh_cmd.extend(["-F", ssh_config_file])
        # A probe supplies its own command and must not inherit login-shell/PTY settings.
        ssh_cmd.extend(["-o", "RemoteCommand=none", "-o", "RequestTTY=no"])
        if password:
            ssh_cmd.extend(["-o", "BatchMode=no", "-o", "NumberOfPasswordPrompts=1"])
        else:
            ssh_cmd.extend(["-o", "BatchMode=yes"])
        identity_file = host.get("ssh_identity_file")
        if identity_file:
            identity_file = os.environ.get("GPU_WATCH_SSH_IDENTITY_FILE") or identity_file
            ssh_cmd.extend([
                "-i", os.path.expanduser(str(identity_file)),
                "-o", "IdentitiesOnly=yes",
                "-o", "PreferredAuthentications=publickey",
            ])
        if host.get("ssh_port"):
            ssh_cmd.extend(["-p", str(host["ssh_port"])])
        for option in host.get("ssh_options", []):
            ssh_cmd.extend(["-o", str(option)])
        ssh_cmd.extend([ssh_target_for(host), command])
        return ssh_cmd

    @staticmethod
    def _ssh_environment(password: str | None) -> dict[str, str] | None:
        if not password:
            return None
        env = os.environ.copy()
        env["GPU_WATCH_SSH_PASSWORD"] = password
        askpass = "ssh-askpass.cmd" if os.name == "nt" else "ssh-askpass.sh"
        env["SSH_ASKPASS"] = str(ROOT / "scripts" / askpass)
        env["SSH_ASKPASS_REQUIRE"] = "force"
        env.setdefault("DISPLAY", "localhost:0")
        return env

    def probe_host(self, host: dict[str, Any], include_disk: bool = False) -> dict[str, Any]:
        started = time.monotonic()
        payload = self._probe_host_once(host, include_disk)
        if include_disk and host.get("privileged_disk_helper") and not payload.get("ok"):
            remaining = float(self.config.get("ssh_disk_probe_timeout_seconds", 720)) - (time.monotonic() - started)
            # A missing helper after OS maintenance must not erase df. Retain
            # a clearly partial unprivileged result within the same time budget.
            if remaining >= 20 and not self.stop_event.is_set():
                payload = self._probe_host_once(
                    {**host, "privileged_disk_helper": False}, True, timeout_seconds=remaining)
                if payload.get("ok") and isinstance(payload.get("disk"), dict):
                    payload["disk"].setdefault("errors", []).append("user usage partial: privileged disk helper unavailable")
            return payload
        # Internal provenance can never be supplied by remote JSON.
        payload.pop("_collection_retried", None)
        reason = retry_reason(payload)
        if include_disk or not reason or self.stop_event.is_set():
            return payload
        budget = float(self.config.get("ssh_gpu_probe_timeout_seconds", 45))
        remaining = budget - (time.monotonic() - started) - 0.5
        minimum = max(5.0, float(self.config["ssh_timeout_seconds"])
                      + float(self.config.get("remote_probe_command_timeout_seconds", 8)))
        if remaining < minimum or self.stop_event.wait(0.5):
            return payload
        remaining = budget - (time.monotonic() - started)
        if remaining < minimum:
            return payload
        result = self._probe_host_once(host, False, timeout_seconds=remaining)
        result["_collection_retried"] = True
        audit("gpu_probe_retry", host=host["name"], cause=reason,
              recovered=bool(result.get("ok")) and retry_reason(result) is None)
        return result

    def _probe_host_once(self, host: dict[str, Any], include_disk: bool = False,
                         timeout_seconds: float | None = None) -> dict[str, Any]:
        command, timeout = self._probe_invocation(host, include_disk)
        if timeout_seconds is not None:
            timeout = min(timeout, timeout_seconds)
        password, password_source = self._probe_password(host)
        if password_source and not password:
            return {"ok": False, "error": "SSH credential unavailable"}
        ssh_cmd = self._ssh_command(host, command, password)
        env = self._ssh_environment(password)
        proc: subprocess.Popen[bytes] | None = None
        try:
            with self.process_lock:
                if self.stop_event.is_set():
                    return {"ok": False, "error": "collector stopping"}
                # Bandit B603: validated configuration is passed as argv with shell=False.
                proc = subprocess.Popen(  # nosec B603
                    ssh_cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    stdin=subprocess.DEVNULL,
                    env=env,
                    creationflags=SUBPROCESS_CREATIONFLAGS,
                )
                self.running_processes.add(proc)
            stdout, stderr = _communicate_bounded(
                proc,
                timeout=timeout,
                max_output_bytes=MAX_REMOTE_PROBE_OUTPUT_BYTES,
                stop_event=self.stop_event,
            )
        except ProcessOutputLimitExceeded:
            return {"ok": False, "error": "ssh probe output exceeded limit"}
        except ProcessStopped:
            return {"ok": False, "error": "collector stopping"}
        except subprocess.TimeoutExpired:
            if proc is not None:
                proc.kill()
                try:
                    proc.wait(timeout=1.0)
                except subprocess.TimeoutExpired:
                    pass
            return {"ok": False, "error": f"ssh probe timeout after {timeout}s"}
        except OSError as exc:
            return {"ok": False, "error": f"ssh unavailable: {type(exc).__name__}"}
        finally:
            if proc is not None:
                with self.process_lock:
                    self.running_processes.discard(proc)
        if self.stop_event.is_set():
            return {"ok": False, "error": "collector stopping"}
        if proc.returncode != 0:
            detail = safe_probe_error(stderr or stdout or f"ssh exit {proc.returncode}")
            return {"ok": False, "error": f"ssh exit {proc.returncode}: {detail}"}
        try:
            payload = json.loads(stdout.strip().splitlines()[-1])
            return payload if isinstance(payload, dict) else {"ok": False, "error": "bad json: object required"}
        except Exception as exc:
            return {"ok": False, "error": f"bad json: {type(exc).__name__}"}

    def handle_payload(self, host: dict[str, Any], payload: dict[str, Any]) -> None:
        name = host["name"]
        label = host.get("label", name)
        if not payload.get("ok"):
            error = safe_probe_error(payload.get("error") or "unknown error")
            failures = self.host_failures.get(name, 0) + 1
            self.host_failures[name] = failures
            if failures in (1, 3):
                audit("gpu_probe_failed", host=name, **json.loads(observation_event_details(error, failures)))
            if failures >= 3:
                backoff = min(60.0, float(self.config["poll_interval_seconds"]) * (2 ** min(5, failures - 3)))
                self.host_next_probe_at[name] = time.monotonic() + backoff
            self.store.mark_host(name, label, False, error=error)
            return
        raw_gpus = payload.get("gpus")
        if not isinstance(raw_gpus, list):
            raise ValueError("invalid GPU payload")
        if len(raw_gpus) > MAX_GPUS_PER_HOST:
            raise ValueError("GPU payload exceeds host limit")
        expected_gpu_count = host.get("expected_gpu_count")
        if (
            isinstance(expected_gpu_count, bool)
            or not isinstance(expected_gpu_count, int)
        ):
            raise ValueError("GPU payload does not match expected inventory")
        gpu_errors = sanitize_gpu_errors(payload.get("gpu_errors"))
        if gpu_errors is None:
            raise ValueError("invalid GPU error map")
        if gpu_errors:
            self.handle_payload(host, {"ok": False, "error": next(iter(gpu_errors.values()))})
            return
        normalized_gpus = []
        gpu_indices: set[int] = set()
        for raw_gpu in raw_gpus:
            gpu = sanitize_gpu_snapshot(raw_gpu)
            if gpu is None:
                raise ValueError("invalid GPU payload")
            gpu_index = int(gpu["index"])
            if gpu_index in gpu_indices:
                raise ValueError("duplicate GPU index")
            gpu_indices.add(gpu_index)
            normalized_gpus.append(gpu)
        expected_gpu_indices = set(range(expected_gpu_count))
        unavailable_indices = set(gpu_errors)
        if gpu_indices & unavailable_indices:
            raise ValueError("GPU cannot be both observed and unavailable")
        if not gpu_indices and not unavailable_indices:
            raise ValueError("GPU payload does not match expected inventory")
        if not unavailable_indices and len(raw_gpus) != expected_gpu_count:
            raise ValueError("GPU payload does not match expected inventory")
        if gpu_indices | unavailable_indices != expected_gpu_indices:
            raise ValueError("GPU payload indices do not match expected inventory")
        gpu_states: list[tuple[dict[str, Any], bool]] = []
        for gpu in normalized_gpus:
            processes = gpu.get("processes") or []
            mem = gpu.get("memory_used") or 0
            util = gpu.get("utilization") or 0
            busy = bool(processes) or mem >= self.config["busy_memory_threshold_mib"] or util >= self.config["busy_utilization_threshold_percent"]
            gpu_states.append((gpu, busy))
        disk = None
        if "disk" in payload:
            disk = payload.get("disk")
            if not isinstance(disk, dict):
                raise ValueError("invalid disk payload")
        self.store.apply_host_payload(
            name,
            label,
            gpu_states,
            hostname=payload.get("hostname"),
            driver_version=payload.get("driver_version"),
            disk=disk,
            # A recovered retry is fresh telemetry, but the failed interval is
            # still unobserved and must not be interpolated into usage.
            max_observation_gap_seconds=0 if payload.get("_collection_retried")
            else self.config["usage_gap_seconds"],
            expected_gpu_indices=expected_gpu_indices,
            gpu_errors=gpu_errors,
        )
        self.host_failures.pop(name, None)
        self.host_next_probe_at.pop(name, None)


def _validated_lab_ids(labs: Any) -> list[str]:
    if not isinstance(labs, list) or not labs:
        raise ValueError("hosts.json must define at least one lab")
    lab_ids = []
    for lab in labs:
        if not isinstance(lab, dict):
            raise ValueError("each lab must be an object")
        raw_lab_id = lab.get("id")
        if not isinstance(raw_lab_id, str):
            raise ValueError("lab ids must be strings")
        lab_id = raw_lab_id.strip()
        if not re.fullmatch(r"[A-Za-z0-9._-]{1,64}", lab_id):
            raise ValueError("lab ids must use 1-64 letters, digits, dots, underscores, or hyphens")
        lab["id"] = lab_id
        lab_ids.append(lab_id)
    if len(lab_ids) != len(set(lab_ids)):
        raise ValueError("lab ids must be unique")
    return lab_ids


def _validated_ssh_port(host: dict[str, Any], name: str) -> None:
    port = host.get("ssh_port")
    if port is None:
        return
    if isinstance(port, bool):
        raise ValueError(f"host {name!r} has an invalid SSH port")
    try:
        port_number = float(port)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"host {name!r} has an invalid SSH port") from exc
    if not math.isfinite(port_number) or not port_number.is_integer() or not 1 <= port_number <= 65535:
        raise ValueError(f"host {name!r} has an invalid SSH port")
    host["ssh_port"] = int(port_number)


_ALLOWED_SSH_AUTHENTICATIONS = {"publickey", "password", "keyboard-interactive"}


def _validated_ssh_options(options: Any, name: str) -> list[str]:
    if not isinstance(options, list) or len(options) > 4:
        raise ValueError(f"host {name!r} has invalid SSH options")
    normalized = []
    seen = set()
    for option in options:
        if (
            not isinstance(option, str)
            or not 1 <= len(option) <= 256
            or any(char in option for char in ("\x00", "\n", "\r"))
        ):
            raise ValueError(f"host {name!r} has invalid SSH options")
        key, separator, value = option.partition("=")
        key_lower = key.strip().lower()
        value = value.strip().lower()
        if not separator or not key_lower or not value or key_lower in seen:
            raise ValueError(f"host {name!r} has invalid SSH options")
        if key_lower == "preferredauthentications":
            methods = value.split(",")
            if (
                not methods
                or len(methods) != len(set(methods))
                or any(method not in _ALLOWED_SSH_AUTHENTICATIONS for method in methods)
            ):
                raise ValueError(f"host {name!r} has invalid SSH options")
            normalized.append(f"PreferredAuthentications={','.join(methods)}")
        elif key_lower == "pubkeyauthentication" and value in {"yes", "no"}:
            normalized.append(f"PubkeyAuthentication={value}")
        else:
            raise ValueError(f"host {name!r} has unsafe SSH option {key!r}")
        seen.add(key_lower)
    return normalized


def _validate_host_options(host: dict[str, Any], name: str) -> None:
    if "privileged_disk_helper" in host and type(host["privileged_disk_helper"]) is not bool:
        raise ValueError(f"host {name!r} has invalid privileged_disk_helper")
    if host.get("privileged_disk_helper") and host.get("disk_user_paths"):
        raise ValueError("fixed disk helper does not accept custom paths")
    options = host.get("ssh_options", [])
    host["ssh_options"] = _validated_ssh_options(options, name)
    disk_paths = host.get("disk_user_paths", [])
    if not isinstance(disk_paths, list) or any(
        not isinstance(entry, dict)
        or not isinstance(entry.get("user"), str)
        or not clean_text(entry.get("user"), 64)
        or not isinstance(entry.get("path"), str)
        or not entry["path"].startswith("/")
        or "\n" in entry["path"]
        or "\r" in entry["path"]
        for entry in disk_paths
    ):
        raise ValueError(f"host {name!r} has invalid disk_user_paths")


def _validate_host(host: Any, lab_ids: set[str]) -> str:
    if not isinstance(host, dict):
        raise ValueError("each host must be an object")
    raw_name = host.get("name")
    if not isinstance(raw_name, str):
        raise ValueError("host names must be strings")
    name = raw_name.strip()
    if not re.fullmatch(r"[A-Za-z0-9._-]{1,128}", name) or name.startswith("-"):
        raise ValueError("host names must be safe SSH identifiers")
    host["name"] = name
    if "display_ip" in host:
        try:
            host["display_ip"] = str(ipaddress.ip_address(host["display_ip"]))
        except (TypeError, ValueError) as exc:
            raise ValueError(f"host {name!r} has an invalid display IP") from exc
    raw_host_lab = host.get("lab", "default")
    if not isinstance(raw_host_lab, str):
        raise ValueError(f"host {name!r} has a non-string lab id")
    host_lab = raw_host_lab.strip()
    if not host_lab or host_lab not in lab_ids:
        raise ValueError(f"host {name!r} references an unknown lab")
    host["lab"] = host_lab
    expected_gpu_count = host.get("expected_gpu_count")
    if (
        isinstance(expected_gpu_count, bool)
        or not isinstance(expected_gpu_count, int)
        or not 1 <= expected_gpu_count <= MAX_GPUS_PER_HOST
    ):
        raise ValueError(
            f"host {name!r} expected_gpu_count must be an integer between 1 and {MAX_GPUS_PER_HOST}"
        )
    validate_temporary_driver_fallback(host.get("temporary_driver_fallback"))
    _validated_ssh_port(host, name)
    _validate_host_options(host, name)
    if ssh_target_for(host).startswith("-"):
        raise ValueError(f"host {name!r} resolves to an unsafe SSH target")
    return name


def _coerce_config_range(
    config: dict[str, Any],
    key: str,
    minimum: int,
    maximum: int,
    *,
    integer: bool,
) -> None:
    expected = "an integer" if integer else "numeric"
    if isinstance(config.get(key), bool):
        raise ValueError(f"{key} must be {expected}")
    try:
        value = float(config[key])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"{key} must be {expected}") from exc
    if not math.isfinite(value) or (integer and not value.is_integer()) or not minimum <= value <= maximum:
        if integer:
            raise ValueError(f"{key} must be an integer between {minimum} and {maximum}")
        raise ValueError(f"{key} must be between {minimum} and {maximum}")
    config[key] = int(value) if integer else value


def _validate_numeric_config(config: dict[str, Any]) -> None:
    integer_ranges = {
        "ssh_timeout_seconds": (1, 300),
        "remote_probe_command_timeout_seconds": (1, 600),
        "docker_usage_timeout_seconds": (10, 300),
        "collector_workers": (1, 64),
        "disk_du_timeout_seconds": (1, 7200),
        "disk_probe_workers": (1, 16),
        "event_retention_days": (7, 3650),
        "announcement_retention_days": (7, 3650),
        "backup_retention_days": (2, 3650),
        "predeployment_backup_retention_days": (7, 3650),
        "daily_index_retention_days": (30, 3650),
        "raw_interval_retention_days": (8, 365),
    }
    numeric_ranges = {
        "poll_interval_seconds": (1, 3600),
        "ssh_gpu_probe_timeout_seconds": (1, 3600),
        "ssh_disk_probe_timeout_seconds": (1, 7200),
        "disk_poll_interval_seconds": (0, 604800),
        "busy_memory_threshold_mib": (0, 2_147_483_647),
        "busy_utilization_threshold_percent": (0, 100),
        "usage_gap_seconds": (1, 86400),
        "health_collector_max_age_seconds": (1, 86400),
        "recent_usage_window_seconds": (3600, 31_536_000),
        "insights_cache_seconds": (0, 3600),
        "maintenance_initial_delay_seconds": (0, 86400),
        "maintenance_interval_seconds": (60, 604800),
        "http_read_timeout_seconds": (1, 300),
    }
    for key, (minimum, maximum) in integer_ranges.items():
        _coerce_config_range(config, key, minimum, maximum, integer=True)
    for key, (minimum, maximum) in numeric_ranges.items():
        _coerce_config_range(config, key, minimum, maximum, integer=False)


def _validate_activity_policy(config: dict[str, Any]) -> None:
    policy = config.get("activity_policy")
    if not isinstance(policy, dict):
        raise ValueError("activity_policy must be an object")
    numeric_keys = (
        "hot_server_busy_fraction",
        "cold_idle_days",
        "cold_min_session_seconds",
    )
    if any(isinstance(policy.get(key), bool) for key in numeric_keys):
        raise ValueError("activity_policy values must be numeric")
    try:
        hot_fraction = float(policy["hot_server_busy_fraction"])
        cold_days = float(policy["cold_idle_days"])
        minimum_session_seconds = float(policy["cold_min_session_seconds"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("activity_policy values must be numeric") from exc
    if (
        not all(math.isfinite(value) for value in (
            hot_fraction,
            cold_days,
            minimum_session_seconds,
        ))
        or not 0 < hot_fraction <= 1
        or not 0 < cold_days <= 365
        or not 1 <= minimum_session_seconds <= 3600
    ):
        raise ValueError("activity_policy values are outside the supported range")
    policy["hot_server_busy_fraction"] = hot_fraction
    policy["cold_idle_days"] = cold_days
    policy["cold_min_session_seconds"] = minimum_session_seconds


def validate_config(config: dict[str, Any]) -> None:
    hosts = config.get("hosts")
    if not isinstance(hosts, list) or not hosts:
        raise ValueError("hosts.json must define at least one host")
    lab_ids = _validated_lab_ids(config.get("labs"))

    allowed_lab_ids = set(lab_ids)
    host_names = [_validate_host(host, allowed_lab_ids) for host in hosts]
    if len(host_names) != len(set(host_names)):
        raise ValueError("host names must be unique")
    _validate_numeric_config(config)
    _validate_activity_policy(config)


def load_config() -> dict[str, Any]:
    with HOSTS_PATH.open("r", encoding="utf-8") as f:
        cfg = json.load(f)
    cfg.setdefault("poll_interval_seconds", 10)
    cfg.setdefault("ssh_timeout_seconds", 8)
    cfg.setdefault("ssh_gpu_probe_timeout_seconds", 45)
    cfg.setdefault("ssh_disk_probe_timeout_seconds", 660)
    cfg.setdefault("remote_probe_command_timeout_seconds", 8)
    cfg.setdefault("docker_usage_timeout_seconds", 30)
    cfg.setdefault("collector_workers", 8)
    cfg.setdefault("disk_poll_interval_seconds", 1800)
    cfg.setdefault("disk_du_timeout_seconds", 600)
    cfg.setdefault("disk_probe_workers", 2)
    cfg.setdefault("busy_memory_threshold_mib", 500)
    cfg.setdefault("busy_utilization_threshold_percent", 10)
    if "usage_gap_seconds" not in cfg:
        cfg["usage_gap_seconds"] = max(30, float(cfg["poll_interval_seconds"]) * 3)
    if "health_collector_max_age_seconds" not in cfg:
        cfg["health_collector_max_age_seconds"] = max(90, float(cfg["poll_interval_seconds"]) * 9)
    cfg.setdefault("recent_usage_window_seconds", 7 * 86400)
    cfg.setdefault("insights_cache_seconds", 60)
    cfg.setdefault("default_deadline", {})
    activity_policy = cfg.setdefault("activity_policy", {})
    if not isinstance(activity_policy, dict):
        raise ValueError("activity_policy must be an object")
    activity_policy.setdefault("hot_server_busy_fraction", 0.5)
    activity_policy.setdefault("cold_idle_days", 7)
    activity_policy.setdefault("cold_min_session_seconds", 60)
    cfg.setdefault("event_retention_days", 180)
    cfg.setdefault("announcement_retention_days", 90)
    cfg.setdefault("backup_retention_days", 14)
    cfg.setdefault("predeployment_backup_retention_days", 30)
    cfg.setdefault("daily_index_retention_days", 180)
    cfg.setdefault("raw_interval_retention_days", 8)
    cfg.setdefault("maintenance_initial_delay_seconds", 120)
    cfg.setdefault("maintenance_interval_seconds", 3600)
    cfg.setdefault("artificial_analysis_api_key_file", "secrets/artificial_analysis_api_key")
    cfg["build_version"] = os.environ.get("GPU_WATCH_BUILD_VERSION", __version__)
    cfg["release_fingerprint"] = compute_release_fingerprint()
    runtime_mode = str(os.environ.get("GPU_WATCH_RUNTIME_MODE") or "standalone").strip().lower()
    if runtime_mode not in {"production", "emergency", "standalone"}:
        raise RuntimeError("GPU_WATCH_RUNTIME_MODE is invalid")
    cfg["runtime_mode"] = runtime_mode
    ssh_config_file = str(os.environ.get("GPU_WATCH_SSH_CONFIG_FILE") or "").strip()
    if ssh_config_file:
        ssh_config_path = Path(ssh_config_file).expanduser()
        if not ssh_config_path.is_absolute() or ssh_config_path.is_symlink() or not ssh_config_path.is_file():
            raise RuntimeError("GPU_WATCH_SSH_CONFIG_FILE must be an absolute regular file")
        cfg["ssh_config_file"] = str(ssh_config_path.resolve())
    cfg["allowed_networks"] = parse_allowed_networks(
        os.environ.get("GPU_WATCH_ALLOWED_NETWORKS") or cfg.get("allowed_networks") or DEFAULT_ALLOWED_NETWORKS
    )
    cfg["allowed_hosts"] = parse_allowed_hosts(
        os.environ.get("GPU_WATCH_ALLOWED_HOSTS")
        or cfg.get("allowed_hosts")
        or DEFAULT_ALLOWED_HOSTS
    )
    cfg["trusted_proxy_networks"] = parse_allowed_networks(
        os.environ.get("GPU_WATCH_TRUSTED_PROXY_NETWORKS") or "127.0.0.0/8,::1/128"
    )
    cfg.setdefault("http_read_timeout_seconds", 15)
    cfg["announcement_admin_pin_hash"] = load_admin_pin_hash()
    cfg.setdefault("labs", [{"id": "default", "label": "Servers"}])
    validate_config(cfg)
    return cfg


class DashboardHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    block_on_close = False
    request_queue_size = 64
    _request_slots = threading.BoundedSemaphore(64)

    def process_request(self, request: Any, client_address: Any) -> None:
        if not self._request_slots.acquire(blocking=False):
            audit("http_concurrency_rejected", peer=client_address[0] if client_address else "unknown")
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except Exception:
            self._request_slots.release()
            raise

    def process_request_thread(self, request: Any, client_address: Any) -> None:
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._request_slots.release()


class Handler(SimpleHTTPRequestHandler):
    server_version = "GPUWatch"
    sys_version = ""
    store: Store
    config: dict[str, Any]
    collector: Collector
    maintenance: MaintenanceWorker
    rate_limiter: SlidingWindowRateLimiter
    artificial_analysis: ArtificialAnalysisIndex | None = None

    def __init__(self, *args: Any, **kwargs: Any):
        super().__init__(*args, directory=str(STATIC_DIR), **kwargs)

    def __getattr__(self, name: str) -> Any:
        # BaseHTTPRequestHandler otherwise emits 501 before the application
        # allowlist runs for an arbitrary/WebDAV method name.
        if name.startswith("do_"):
            return self.reject_unsupported_method
        raise AttributeError(name)

    def parsed_request_target(self):
        # Error responses can be generated before URL/header parsing finishes.
        # Keep logging and response headers safe even for malformed input.
        try:
            return urlparse(self.path)
        except ValueError:
            return urlparse("/")

    def parse_request(self) -> bool:
        self.path = "/"
        self.headers = self.MessageClass()
        if not super().parse_request():
            return False
        try:
            urlparse(self.path)
        except ValueError:
            self.send_error(400, "Bad request target")
            return False
        return True

    def setup(self) -> None:
        super().setup()
        self.connection.settimeout(float(self.config.get("http_read_timeout_seconds", 15)))

    def handle_one_request(self) -> None:
        try:
            super().handle_one_request()
        except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError):
            # Browsers can leave while a static or error response is being
            # written.  This is a normal client disconnect and must not turn
            # into a socketserver traceback in the operational log.
            self.close_connection = True

    def client_ip(self) -> str:
        peer = self.client_address[0] if self.client_address else ""
        return client_ip_from_proxy(peer, self.headers, self.config["trusted_proxy_networks"])

    def request_is_https(self) -> bool:
        peer = self.client_address[0] if self.client_address else ""
        return (
            is_ip_allowed(peer, self.config["trusted_proxy_networks"])
            and str(self.headers.get("X-Forwarded-Proto") or "").lower() == "https"
        )

    def end_headers(self) -> None:
        parsed = self.parsed_request_target()
        if parsed.path.startswith("/api/"):
            cache_control = "no-store"
        elif parsed.path in {"/app.js", "/styles.css", "/favicon.svg"} and parsed.query:
            cache_control = "public, max-age=31536000, immutable"
        else:
            cache_control = "no-cache"
        self.send_header("Cache-Control", cache_control)
        for name, value in SECURITY_HEADERS.items():
            self.send_header(name, value)
        if self.request_is_https():
            self.send_header("Strict-Transport-Security", "max-age=31536000")
        super().end_headers()

    def log_message(self, format: str, *args: Any) -> None:
        try:
            status = int(args[1]) if len(args) > 1 else 0
        except (TypeError, ValueError):
            status = 0
        if status >= 400:
            audit("http_error", client=self.client_ip(), method=self.command, path=self.parsed_request_target().path, status=status)

    def send_json(self, payload: Any, status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            # The browser may leave between headers and the body; this is a
            # normal client disconnect, not a server failure.
            return

    def send_api_failure(self, action: str, status: int = 500) -> None:
        audit("api_failure", client=self.client_ip(), action=action)
        message = "상태 확인에 실패했습니다." if action == "health" else "요청을 처리하지 못했습니다."
        self.send_json({"ok": False, "error": message}, status)

    def request_allowed(self) -> bool:
        address = self.client_ip()
        ip_allowed = is_ip_allowed(address, self.config["allowed_networks"])
        host_allowed = is_host_allowed(
            self.headers.get("Host"), self.config["allowed_hosts"]
        )
        if ip_allowed and host_allowed:
            return True
        audit(
            "access_denied",
            client=address,
            method=self.command,
            path=self.parsed_request_target().path,
            reason="ip" if not ip_allowed else "host",
        )
        if self.parsed_request_target().path.startswith("/api/"):
            self.send_json({"ok": False, "error": "forbidden"}, 403)
        else:
            self.send_error(403, "Forbidden")
        return False

    def mutation_allowed(self, action: str, limit: int, window_seconds: int) -> bool:
        if not is_same_origin(self.headers):
            audit("mutation_origin_denied", client=self.client_ip(), action=action)
            self.send_json({"ok": False, "error": "요청 출처를 확인할 수 없습니다."}, 403)
            return False
        address = self.client_ip() or "unknown"
        if not self.rate_limiter.allow(f"{action}:{address}", limit, window_seconds):
            audit("mutation_rate_limited", client=address, action=action)
            self.send_json({"ok": False, "error": "요청이 너무 많습니다. 잠시 후 다시 시도해 주세요."}, 429)
            return False
        return True

    def auth_failure_budget_allowed(self, action: str) -> bool:
        address = self.client_ip() or "unknown"
        subnet = client_network_scope(address)
        subnet_allowed = self.rate_limiter.check(
            f"auth-failure:subnet:{subnet}",
            AUTH_FAILURE_SUBNET_LIMIT,
            AUTH_FAILURE_WINDOW_SECONDS,
        )
        global_allowed = self.rate_limiter.check(
            "auth-failure:global",
            AUTH_FAILURE_GLOBAL_LIMIT,
            AUTH_FAILURE_WINDOW_SECONDS,
        )
        if subnet_allowed and global_allowed:
            return True
        audit("auth_rate_limited", client=address, action=action)
        self.send_json({"ok": False, "error": "요청이 너무 많습니다. 잠시 후 다시 시도해 주세요."}, 429)
        return False

    def record_auth_failure(self) -> None:
        address = self.client_ip() or "unknown"
        subnet = client_network_scope(address)
        self.rate_limiter.record(
            f"auth-failure:subnet:{subnet}", AUTH_FAILURE_WINDOW_SECONDS
        )
        self.rate_limiter.record("auth-failure:global", AUTH_FAILURE_WINDOW_SECONDS)

    def acquire_auth_attempt(self, action: str) -> bool:
        if AUTH_ATTEMPT_SEMAPHORE.acquire(blocking=False):
            return True
        audit("auth_work_limited", client=self.client_ip(), action=action)
        self.send_json({"ok": False, "error": "요청이 너무 많습니다. 잠시 후 다시 시도해 주세요."}, 429)
        return False

    def read_allowed(self, action: str, limit: int = 60, window_seconds: int = 60) -> bool:
        address = self.client_ip() or "unknown"
        if self.rate_limiter.allow(f"read:{action}:{address}", limit, window_seconds):
            return True
        audit("read_rate_limited", client=address, action=action)
        self.send_json({"ok": False, "error": "요청이 너무 많습니다. 잠시 후 다시 시도해 주세요."}, 429)
        return False

    def admin_allowed(self, data: dict[str, Any], action: str) -> bool:
        encoded = self.config.get("announcement_admin_pin_hash")
        if not encoded:
            self.send_json({"ok": False, "error": "관리자 PIN이 설정되지 않았습니다."}, 503)
            return False
        if not self.auth_failure_budget_allowed(action):
            return False
        if not self.acquire_auth_attempt(action):
            return False
        passphrase = str(data.get("pin") or "")
        try:
            allowed = verify_pin(passphrase, encoded)
        finally:
            AUTH_ATTEMPT_SEMAPHORE.release()
        if not allowed:
            self.record_auth_failure()
            audit("admin_auth_failed", client=self.client_ip(), action=action)
            self.send_json({"ok": False, "error": "관리자 PIN이 일치하지 않습니다."}, 403)
            return False
        if hash_needs_upgrade(encoded) and not os.environ.get("GPU_WATCH_ADMIN_PIN_HASH"):
            self.config["announcement_admin_pin_hash"] = persist_admin_passphrase_hash(passphrase)
            audit("admin_hash_upgraded", action=action)
        return True

    def serve_index(self, *, head_only: bool = False) -> None:
        source = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
        version = html.escape(str(self.config.get("build_version") or "local"), quote=True)
        release_model = html.escape(__release_model__, quote=True)
        release_date = html.escape(__release_date__, quote=True)
        asset_hash = hashlib.sha256()
        for asset_name in ("app.js", "styles.css", "favicon.svg"):
            asset_hash.update((STATIC_DIR / asset_name).read_bytes())
        asset_version = asset_hash.hexdigest()[:12]
        body = (
            source.replace("__GPU_WATCH_BUILD_VERSION__", version)
            .replace("__GPU_WATCH_ASSET_VERSION__", asset_version)
            .replace("__GPU_WATCH_RELEASE_MODEL__", release_model)
            .replace("__GPU_WATCH_RELEASE_DATE__", release_date)
            .encode("utf-8")
        )
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if not head_only:
            self.wfile.write(body)

    def read_json_body(self, max_bytes: int = 8192) -> dict[str, Any]:
        if self.headers.get_content_type() != "application/json":
            raise ValueError("Content-Type은 application/json이어야 합니다.")
        if self.headers.get_all("Transfer-Encoding"):
            raise ValueError("Transfer-Encoding은 지원하지 않습니다.")
        content_lengths = self.headers.get_all("Content-Length") or []
        if len(content_lengths) > 1:
            raise ValueError("잘못된 Content-Length입니다.")
        try:
            length = int(content_lengths[0] if content_lengths else 0)
        except ValueError as exc:
            raise ValueError("잘못된 Content-Length입니다.") from exc
        if length < 0:
            raise ValueError("잘못된 Content-Length입니다.")
        if length == 0:
            return {}
        if length > max_bytes:
            raise ValueError("요청이 너무 큽니다.")
        raw = self.rfile.read(length)
        try:
            data = json.loads(raw.decode("utf-8"))
        except Exception as exc:
            raise ValueError("JSON 요청을 읽을 수 없습니다.") from exc
        if not isinstance(data, dict):
            raise ValueError("JSON object가 필요합니다.")
        return data

    def do_GET(self) -> None:
        if not self.request_allowed():
            return
        parsed = self.parsed_request_target()
        if parsed.path in {"/", "/index.html"}:
            self.serve_index()
            return
        if parsed.path == "/favicon.ico":
            # The page declares the versioned SVG favicon.  Some clients still
            # probe this legacy path before parsing the document.
            self.send_response(204)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if parsed.path == "/api/health":
            if not self.read_allowed("health", 120, 60):
                return
            try:
                database = self.store.health()
                collector = self.collector.status()
                maintenance = self.maintenance.status()
            except Exception:
                self.send_api_failure("health", 503)
                return
            completed_at = collector.get("last_cycle_completed_at")
            collector_age = None if completed_at is None else max(0.0, now_ts() - float(completed_at))
            collector_fresh = collector_age is not None and collector_age <= float(self.config["health_collector_max_age_seconds"])
            collector_ok = (
                bool(collector.get("alive"))
                and collector_fresh
                and not collector.get("last_cycle_error")
            )
            maintenance_ok = bool(maintenance.get("alive")) and not maintenance.get("last_error")
            healthy = bool(database.get("ok")) and collector_ok and maintenance_ok
            self.send_json({
                "ok": healthy,
                "build_version": self.config["build_version"],
                "release_fingerprint": self.config["release_fingerprint"],
                "runtime_mode": self.config.get("runtime_mode", "standalone"),
                "now": iso(now_ts()),
                "database": database,
                "collector": collector,
                "collector_fresh": collector_fresh,
                "collector_age_seconds": collector_age,
                "maintenance": maintenance,
            }, 200 if healthy else 503)
            return
        if parsed.path == "/api/intelligence-index":
            if not self.read_allowed("intelligence-index", 30, 60):
                return
            try:
                payload = (
                    self.artificial_analysis.payload()
                    if self.artificial_analysis is not None
                    else unavailable_payload()
                )
            except Exception:
                # This optional, externally sourced widget must never affect the
                # core dashboard or its health contract.
                audit("api_failure", client=self.client_ip(), action="intelligence-index")
                payload = unavailable_payload()
            self.send_json(payload)
            return
        if parsed.path == "/api/snapshot":
            if not self.read_allowed("snapshot"):
                return
            try:
                payload = self.store.snapshot(self.config)
                payload["collector"] = self.collector.status()
            except Exception:
                self.send_api_failure("snapshot")
                return
            self.send_json(payload)
            return
        if parsed.path == "/api/insights":
            if not self.read_allowed("insights"):
                return
            try:
                payload = self.store.insights(self.config)
            except Exception:
                self.send_api_failure("insights")
                return
            self.send_json(payload)
            return
        if parsed.path == "/api/events":
            if not self.read_allowed("events"):
                return
            try:
                qs = parse_qs(parsed.query)
                limit = int(qs.get("limit", ["100"])[0])
                offset = int(qs.get("offset", ["0"])[0])
                lab = qs.get("lab", [None])[0]
                host = qs.get("host", [None])[0]
                user = qs.get("user", [None])[0]
                date = qs.get("date", [None])[0]
                availability = qs.get("include_availability", ["0"])[0]
                if availability not in ("0", "1"):
                    raise ValueError("invalid availability filter")
                include_availability = availability == "1"
                if date and local_date_bounds(date) is None:
                    raise ValueError("invalid date")
            except (TypeError, ValueError):
                self.send_json({"ok": False, "error": "잘못된 조회 조건입니다."}, 400)
                return
            try:
                payload = self.store.events(limit, offset, host_names_for_lab(self.config, lab), host, user, date,
                                            include_availability=include_availability)
            except ValueError:
                self.send_json({"ok": False, "error": "잘못된 조회 조건입니다."}, 400)
                return
            except Exception:
                self.send_api_failure("events")
                return
            self.send_json(payload)
            return
        super().do_GET()

    def send_head(self):
        """Serve only shipped public assets; never list directories or follow links."""
        path = self.parsed_request_target().path
        allowed = path in {"/app.js", "/styles.css", "/favicon.svg"} or bool(re.fullmatch(
            r"/(?:flag-icons/[a-z]{2}\.svg|brand-icons/(?:codex|openai-status|claude-status)\.png)", path
        ))
        candidate = STATIC_DIR / path.lstrip("/")
        try:
            inside = candidate.resolve().is_relative_to(STATIC_DIR.resolve())
            linked = any(item.is_symlink() for item in (candidate, *candidate.parents)
                         if item == STATIC_DIR or STATIC_DIR in item.parents)
            valid = allowed and inside and not linked and candidate.is_file()
        except (OSError, RuntimeError, ValueError):
            valid = False
        if not valid:
            self.send_error(404, "File not found")
            return None
        return super().send_head()

    def do_HEAD(self) -> None:
        if not self.request_allowed():
            return
        parsed = self.parsed_request_target()
        if parsed.path in {"/", "/index.html"}:
            self.serve_index(head_only=True)
            return
        super().do_HEAD()

    def do_POST(self) -> None:
        if not self.request_allowed():
            return
        parsed = self.parsed_request_target()
        parts = parsed.path.strip("/").split("/")
        if parsed.path == "/api/announcements":
            if not self.mutation_allowed("announcement-create", 5, 600):
                return
            if not self.acquire_auth_attempt("announcement-create"):
                return
            try:
                data = self.read_json_body()
                notice = self.store.add_announcement(
                    data.get("author"),
                    data.get("message"),
                    data.get("duration_seconds"),
                    data.get("expires_at"),
                    data.get("pin"),
                )
            except ValueError as exc:
                self.send_json({"ok": False, "error": str(exc)}, 400)
                return
            except Exception:
                self.send_json({"ok": False, "error": "공지 저장에 실패했습니다."}, 500)
                return
            finally:
                AUTH_ATTEMPT_SEMAPHORE.release()
            audit("announcement_created", client=self.client_ip(), notice_id=notice["id"])
            self.send_json({"ok": True, "announcement": notice}, 201)
            return
        if len(parts) == 3 and parts[0] == "api" and parts[1] == "announcements":
            if not self.mutation_allowed("announcement-update", 10, 600):
                return
            if not self.auth_failure_budget_allowed("announcement-update"):
                return
            if not self.acquire_auth_attempt("announcement-update"):
                return
            try:
                notice_id = int(parts[2])
                data = self.read_json_body()
                notice, message = self.store.update_announcement(
                    notice_id,
                    data.get("author"),
                    data.get("message"),
                    data.get("duration_seconds"),
                    data.get("expires_at"),
                    data.get("pin"),
                    self.config.get("announcement_admin_pin_hash"),
                )
            except ValueError as exc:
                self.send_json({"ok": False, "error": str(exc)}, 400)
                return
            except Exception:
                self.send_json({"ok": False, "error": "공지 수정에 실패했습니다."}, 500)
                return
            finally:
                AUTH_ATTEMPT_SEMAPHORE.release()
            if notice is None:
                status = 403 if "비밀번호" in message or "PIN" in message else 404
                if status == 403:
                    self.record_auth_failure()
                self.send_json({"ok": False, "error": message}, status)
                return
            audit("announcement_updated", client=self.client_ip(), notice_id=notice_id)
            self.send_json({"ok": True, "announcement": notice})
            return
        if parsed.path == "/api/deadline":
            if not self.mutation_allowed("deadline-update", 10, 600):
                return
            try:
                data = self.read_json_body()
                if not self.admin_allowed(data, "deadline-update"):
                    return
                creating = not clean_text(data.get("id"), 32)
                deadline = self.store.update_deadline(
                    data.get("title"),
                    data.get("deadline_at"),
                    data.get("url"),
                    deadline_id=data.get("id"),
                    config=self.config,
                    mode=data.get("mode", "scheduled"),
                    tba_text=data.get("tba_text", ""),
                )
                deadlines = self.store.deadlines(self.config)
            except LookupError as exc:
                self.send_json({"ok": False, "error": str(exc)}, 404)
                return
            except ValueError as exc:
                self.send_json({"ok": False, "error": str(exc)}, 400)
                return
            except Exception:
                self.send_json({"ok": False, "error": "타이머 저장에 실패했습니다."}, 500)
                return
            audit(
                "deadline_created" if creating else "deadline_updated",
                client=self.client_ip(),
                deadline_id=deadline["id"],
                deadline_count=len(deadlines),
            )
            self.send_json(
                {"ok": True, "deadline": deadline, "deadlines": deadlines},
                201 if creating else 200,
            )
            return
        self.send_json({"ok": False, "error": "not found"}, 404)

    def do_DELETE(self) -> None:
        if not self.request_allowed():
            return
        parsed = self.parsed_request_target()
        parts = parsed.path.strip("/").split("/")
        if len(parts) == 3 and parts[0] == "api" and parts[1] == "announcements":
            if not self.mutation_allowed("announcement-delete", 20, 600):
                return
            if not self.auth_failure_budget_allowed("announcement-delete"):
                return
            if not self.acquire_auth_attempt("announcement-delete"):
                return
            try:
                notice_id = int(parts[2])
                data = self.read_json_body()
                ok, message = self.store.delete_announcement(
                    notice_id,
                    data.get("pin"),
                    self.config.get("announcement_admin_pin_hash"),
                )
            except ValueError as exc:
                self.send_json({"ok": False, "error": str(exc)}, 400)
                return
            except Exception:
                self.send_json({"ok": False, "error": "공지 삭제에 실패했습니다."}, 500)
                return
            finally:
                AUTH_ATTEMPT_SEMAPHORE.release()
            if not ok:
                status = 403 if "PIN" in message else 404
                if status == 403:
                    self.record_auth_failure()
                self.send_json({"ok": False, "error": message}, status)
                return
            audit("announcement_deleted", client=self.client_ip(), notice_id=notice_id)
            self.send_json({"ok": True})
            return
        if len(parts) == 3 and parts[0] == "api" and parts[1] == "deadline":
            if not self.mutation_allowed("deadline-delete", 10, 600):
                return
            try:
                data = self.read_json_body()
                if not self.admin_allowed(data, "deadline-delete"):
                    return
                deadlines = self.store.delete_deadline(parts[2], self.config)
            except LookupError as exc:
                self.send_json({"ok": False, "error": str(exc)}, 404)
                return
            except ValueError as exc:
                self.send_json({"ok": False, "error": str(exc)}, 400)
                return
            except Exception:
                self.send_json({"ok": False, "error": "타이머 삭제에 실패했습니다."}, 500)
                return
            audit(
                "deadline_deleted",
                client=self.client_ip(),
                deadline_id=parts[2],
                deadline_count=len(deadlines),
            )
            self.send_json({"ok": True, "deadlines": deadlines})
            return
        self.send_json({"ok": False, "error": "not found"}, 404)

    def reject_unsupported_method(self) -> None:
        if not self.request_allowed():
            return
        self.send_error(501, "Unsupported method")

    def do_OPTIONS(self) -> None:
        self.reject_unsupported_method()

    def do_PUT(self) -> None:
        self.reject_unsupported_method()

    def do_PATCH(self) -> None:
        self.reject_unsupported_method()

    def do_CONNECT(self) -> None:
        self.reject_unsupported_method()

    def do_TRACE(self) -> None:
        self.reject_unsupported_method()


def run_maintenance_cycle(
    store: Store,
    config: dict[str, Any],
    artificial_analysis: ArtificialAnalysisIndex,
) -> dict[str, Any]:
    """Run core maintenance, then schedule an optional refresh without blocking it."""
    result = store.run_maintenance(config)
    try:
        artificial_analysis.refresh_if_due()
    except Exception:
        # ArtificialAnalysisIndex already contains its expected failures. Keep
        # this boundary so even an unforeseen widget bug cannot mark database
        # maintenance (and therefore core health) as failed.
        audit("artificial_analysis_maintenance_refresh_failed")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Local-only GPU watch dashboard")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8787)
    args = parser.parse_args()

    config = load_config()
    store = Store(DB_PATH)
    store.prepare(config)
    collector = Collector(store, config)
    artificial_analysis = ArtificialAnalysisIndex(
        DATA_DIR / "artificial_analysis_intelligence_index.json",
        resolve_local_path(str(config["artificial_analysis_api_key_file"])),
    )
    maintenance = MaintenanceWorker(
        lambda: run_maintenance_cycle(store, config, artificial_analysis),
        initial_delay_seconds=config["maintenance_initial_delay_seconds"],
        interval_seconds=config["maintenance_interval_seconds"],
    )
    Handler.store = store
    Handler.config = config
    Handler.collector = collector
    Handler.maintenance = maintenance
    Handler.rate_limiter = SlidingWindowRateLimiter()
    Handler.artificial_analysis = artificial_analysis

    # Bind before starting background work. A duplicate instance or invalid
    # bind address must fail without orphaning collector/maintenance threads.
    server = DashboardHTTPServer((args.host, args.port), Handler)
    collector_started = False
    maintenance_started = False
    shutdown_started = threading.Event()

    def request_shutdown(signum: int, _frame: Any) -> None:
        if shutdown_started.is_set():
            return
        shutdown_started.set()
        audit("shutdown_requested", signal=signum)
        threading.Thread(target=server.shutdown, name="gpu-watch-shutdown", daemon=True).start()

    if threading.current_thread() is threading.main_thread():
        signal.signal(signal.SIGTERM, request_shutdown)
        signal.signal(signal.SIGINT, request_shutdown)
    try:
        Handler.artificial_analysis.refresh_if_due()
        collector.start()
        collector_started = True
        maintenance.start()
        maintenance_started = True
        audit("service_started", host=args.host, port=args.port, build=config["build_version"], schema=SCHEMA_VERSION)
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        if collector_started:
            collector.stop(timeout=7)
        if maintenance_started:
            maintenance.stop(timeout=4)
        server.server_close()
        audit("service_stopped")


if __name__ == "__main__":
    main()
