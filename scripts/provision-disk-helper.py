#!/usr/bin/env python3
"""Build a reviewable, fixed-command root disk helper installer (does not install).

Run on the dashboard source: python3 scripts/provision-disk-helper.py --user MONITOR
--output installer.py. Transfer the installer to an authorized GPU host and run
it with sudo python3 -I. No administrator password is retained by GPU Watch.
"""
from __future__ import annotations

import argparse
import ast
import base64
import hashlib
from pathlib import Path
import re

HELPER_PATH = "/usr/local/libexec/gpu-watch-disk"


def helper_source(source: str) -> str:
    values = {node.targets[0].id: node.value for node in ast.parse(source).body
              if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name)}
    common = ast.literal_eval(values["REMOTE_PROBE_COMMON"])
    disk = ast.literal_eval(values["REMOTE_DISK_FUNCTIONS"])
    payload = ast.literal_eval(values["REMOTE_DISK_PROBE"].right)
    prelude = '''#!/usr/bin/python3 -I
# Generated from GPU Watch's reviewed disk collector; do not edit in place.
import os, sys, json, time, fcntl
from pathlib import Path
if os.geteuid() != 0 or len(sys.argv) != 1:
    raise SystemExit("fixed disk helper requires root and accepts no arguments")
os.environ.clear()
os.environ.update(PATH="/usr/sbin:/usr/bin:/sbin:/bin", LC_ALL="C", HOME="/root")
os.chdir("/")
os.nice(15)
cache = Path("/var/cache/gpu-watch/disk.json")
lock_fd = os.open(str(cache.parent / "disk.lock"), os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
def cached():
    try:
        value = json.loads(cache.read_text())
        if 0 <= time.time() - value["at"] < 300:
            return value["payload"]
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return None
try:
    fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
except BlockingIOError:
    print(json.dumps(cached() or {"ok": False, "error": "disk collection in progress"}))
    raise SystemExit(0)
previous = cached()
if previous is not None:
    print(json.dumps(previous, separators=(",", ":")))
    raise SystemExit(0)
sys.argv = [sys.argv[0], "--disk", "--docker-usage", "--du-timeout=600", "--docker-timeout=30"]
'''
    # Reuse the same parser and algorithms as unprivileged collection. Only
    # replace the final print so one bounded result can be cached under root.
    payload = payload.replace('print(json.dumps(', 'result = json.loads(json.dumps(')
    payload += '''
temporary = cache.with_name("disk.%s.tmp" % os.getpid())
try:
    with temporary.open("w") as stream:
        json.dump({"at": time.time(), "payload": result}, stream, separators=(",", ":"))
    temporary.chmod(0o600)
    os.replace(str(temporary), str(cache))
finally:
    if temporary.exists():
        temporary.unlink()
print(json.dumps(result, separators=(",", ":")))
'''
    value = prelude + common + disk + payload
    compile(value, HELPER_PATH, "exec")
    return value


def installer_source(helper: str, user: str) -> str:
    if not re.fullmatch(r"[a-z_][a-z0-9_-]{0,31}", user):
        raise ValueError("invalid monitoring account")
    encoded = base64.b64encode(helper.encode()).decode()
    digest = hashlib.sha256(helper.encode()).hexdigest()
    prelude = f'''# Generated GPU Watch disk helper installer; no credentials.
HELPER_B64 = {encoded!r}
HELPER_SHA256 = {digest!r}
MONITOR_USER = {user!r}
'''
    return prelude + '''
import base64, hashlib, json, os, pathlib, pwd, stat, subprocess, tempfile
if os.geteuid() != 0:
    raise SystemExit("run this reviewed installer as root")
pwd.getpwnam(MONITOR_USER)
os.umask(0o077)
def trusted_dir(path, mode=0o755):
    path=pathlib.Path(path)
    if not path.exists():
        trusted_dir(path.parent)
        path.mkdir(mode=mode)
        path.chmod(mode)
    for parent in [path, *path.parents]:
        info=parent.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
            raise SystemExit("untrusted installation directory: " + str(parent))
    return path
lib=trusted_dir("/usr/local/libexec")
rules=trusted_dir("/etc/sudoers.d",0o750)
trusted_dir("/var/cache/gpu-watch",0o700)
helper=base64.b64decode(HELPER_B64)
if hashlib.sha256(helper).hexdigest() != HELPER_SHA256:
    raise SystemExit("helper checksum mismatch")
rule=(MONITOR_USER+' ALL=(root) NOPASSWD: /usr/local/libexec/gpu-watch-disk ""\\n').encode()
def install(path, content, mode, validate=False):
    if path.exists() or path.is_symlink():
        info=path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
            raise SystemExit("untrusted existing installation file")
    fd,name=tempfile.mkstemp(prefix=".gpu-watch-",dir=str(path.parent))
    try:
        with os.fdopen(fd,"wb") as stream:
            stream.write(content);stream.flush();os.fsync(stream.fileno())
        os.chmod(name,mode)
        if validate:
            subprocess.run(["/usr/sbin/visudo","-cf",name],check=True,stdout=subprocess.DEVNULL)
        os.replace(name,str(path))
    finally:
        if os.path.exists(name):os.unlink(name)
install(lib/"gpu-watch-disk",helper,0o755)
install(rules/("gpu-watch-disk-"+MONITOR_USER),rule,0o440,True)
subprocess.run(["/usr/sbin/visudo","-c"],check=True,stdout=subprocess.DEVNULL)
print(json.dumps({"installed":True,"sha256":HELPER_SHA256,"user":MONITOR_USER,"hostname":os.uname().nodename}))
'''


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--user", required=True)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    source = Path(__file__).resolve().parents[1] / "server.py"
    installer = installer_source(helper_source(source.read_text(encoding="utf-8")), args.user)
    compile(installer, str(args.output), "exec")
    args.output.write_text(installer, encoding="utf-8")
    args.output.chmod(0o600)
    print("Installer generated; review it before elevated execution.")


if __name__ == "__main__":
    main()
