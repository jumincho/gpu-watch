"""Boot-scoped, operator-provisioned driver libraries for a temporary NVML mismatch."""
import re

def validate_temporary_driver_fallback(value):
    if value is None:
        return
    if (not isinstance(value, dict) or set(value) != {"boot_id", "kernel_version"}
            or not isinstance(value["boot_id"], str)
            or not re.fullmatch(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}", value["boot_id"])
            or not isinstance(value["kernel_version"], str)
            or not re.fullmatch(r"[0-9]{3}\.[0-9]{1,3}\.[0-9]{1,3}", value["kernel_version"])):
        raise ValueError("invalid temporary_driver_fallback")

REMOTE_TEMPORARY_DRIVER_PROBE = r'''
def temporary_driver_prefix(rc):
    # Native success and unrelated faults must never activate the workaround.
    if rc != 18:
        return []
    config = arg_json_b64("--temporary-driver-b64=", None)
    if (not isinstance(config, dict) or set(config) != {"boot_id", "kernel_version"}
            or not isinstance(config.get("boot_id"), str)
            or not re.fullmatch(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}", config["boot_id"])
            or not isinstance(config.get("kernel_version"), str)
            or not re.fullmatch(r"[0-9]{3}\.[0-9]{1,3}\.[0-9]{1,3}", config["kernel_version"])):
        return []
    from pathlib import Path
    import stat
    try:
        if Path("/proc/sys/kernel/random/boot_id").read_text().strip() != config["boot_id"]:
            return []
        loaded = Path("/proc/driver/nvidia/version").read_text()
        match = re.search(r"Kernel Module\s+([0-9.]+)", loaded)
        if not match or match.group(1) != config["kernel_version"]:
            return []
        uid = os.getuid()
        directory = Path(pwd.getpwuid(uid).pw_dir) / ".cache" / ("gpu-watch-driver-" + config["boot_id"])
        if directory.resolve() != directory:
            return []
        metadata = directory.lstat()
        if not stat.S_ISDIR(metadata.st_mode) or metadata.st_uid != uid or metadata.st_mode & 0o077:
            return []
        for name in ("libnvidia-ml.so.1", "libcuda.so.1"):
            library = directory / name
            metadata = library.lstat()
            if (not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != uid
                    or metadata.st_mode & 0o077 or not 4 < metadata.st_size <= 128 * 1024 * 1024):
                return []
            with library.open("rb") as stream:
                if stream.read(4) != bytes.fromhex("7f454c46"):
                    return []
        # Only GPU subprocesses use these libraries. Host procfs/NSS/SSH and
        # Docker inspection remain in the original host process namespace.
        return ["/usr/bin/env", "LD_PRELOAD=", "LD_LIBRARY_PATH=" + str(directory)]
    except (OSError, KeyError, ValueError, RuntimeError):
        return []
'''
