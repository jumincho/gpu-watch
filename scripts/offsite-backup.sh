#!/bin/sh
set -eu
umask 077

ROOT=$(CDPATH='' cd -- "$(dirname -- "$0")/.." && pwd)
APP_DATA_DIR="$ROOT/data"
ENV_FILE=${GPU_WATCH_OFFSITE_ENV:-$ROOT/operator-secrets/offsite-backup.env}

if [ ! -d "$APP_DATA_DIR/backups" ] || [ -L "$APP_DATA_DIR" ] || \
    [ -L "$APP_DATA_DIR/backups" ] || \
    find "$APP_DATA_DIR" -type l -print -quit | grep -q .; then
    echo "application data directory is missing or unsafe" >&2
    exit 65
fi

exec 9>"$APP_DATA_DIR/.offsite-backup.lock"
if ! flock -n 9; then
    echo "another off-site backup is already running" >&2
    exit 73
fi

if [ ! -f "$ENV_FILE" ]; then
    echo "off-site backup is not configured: $ENV_FILE" >&2
    exit 2
fi

# shellcheck disable=SC1090
. "$ENV_FILE"
: "${OFFSITE_TARGET:?OFFSITE_TARGET is required, for example user@host:/secure/path}"
: "${OFFSITE_CERT:?OFFSITE_CERT must point to an X.509 encryption certificate}"
if [ ! -f "$OFFSITE_CERT" ]; then
    echo "off-site encryption certificate is missing: $OFFSITE_CERT" >&2
    exit 2
fi

case "$OFFSITE_TARGET" in
    *:*) ;;
    *) echo "OFFSITE_TARGET must use user@host:/absolute/path syntax" >&2; exit 64 ;;
esac
remote_host=${OFFSITE_TARGET%%:*}
remote_dir=${OFFSITE_TARGET#*:}
case "$remote_host" in
    ''|-*|*[!A-Za-z0-9_.@-]*|*@*@*)
        echo "OFFSITE_TARGET has an unsafe SSH host component" >&2
        exit 64
        ;;
esac
case "$remote_dir" in
    /*) ;;
    *) echo "OFFSITE_TARGET must contain an absolute remote path" >&2; exit 64 ;;
esac
case "$remote_dir" in
    *[!A-Za-z0-9_./+-]*|*/../*|*/..|*/./*|*/.)
        echo "OFFSITE_TARGET has an unsafe remote path" >&2
        exit 64
        ;;
esac

latest=$(find "$APP_DATA_DIR/backups" -maxdepth 1 -type f -name 'gpu_watch-*.sqlite3' -print | sort | tail -n 1)
if [ -z "$latest" ]; then
    echo "no daily backup is available" >&2
    exit 3
fi

python3 - "$latest" <<'PY'
import sqlite3
import sys
from pathlib import Path

uri = Path(sys.argv[1]).resolve().as_uri() + "?mode=ro&immutable=1"
connection = sqlite3.connect(uri, uri=True)
try:
    connection.execute("pragma query_only=on")
    integrity = str(connection.execute("pragma quick_check").fetchone()[0])
    if integrity != "ok":
        raise SystemExit("backup integrity check failed: " + integrity)
    tables = {
        str(row[0])
        for row in connection.execute("select name from sqlite_master where type='table'")
    }
    required = {"events", "gpu_runtime", "host_runtime", "schema_meta"}
    missing = sorted(required - tables)
    if missing:
        raise SystemExit("not a GPU Watch backup; missing tables: " + ", ".join(missing))
finally:
    connection.close()
PY

name=$(basename "$latest").p7m
temporary="$APP_DATA_DIR/backups/.${name}.$$.tmp"
remote_base=${remote_dir%/}
remote_final="$remote_base/$name"
remote_partial="$remote_base/.${name}.partial.$$"
remote_checksum="${remote_final}.sha256"

cleanup() {
    rm -f "$temporary"
}
trap cleanup EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM

openssl cms -encrypt -binary -aes256 -outform DER -in "$latest" -out "$temporary" "$OFFSITE_CERT"
chmod 600 "$temporary"
digest=$(sha256sum "$temporary" | awk '{print $1}')

scp -q \
    -o BatchMode=yes \
    -o StrictHostKeyChecking=yes \
    -o ConnectTimeout=15 \
    "$temporary" "$remote_host:$remote_partial"

ssh \
    -o BatchMode=yes \
    -o StrictHostKeyChecking=yes \
    -o ConnectTimeout=15 \
    "$remote_host" sh -s -- "$remote_partial" "$remote_final" "$digest" "$remote_checksum" <<'SH'
set -eu
partial=$1
final=$2
expected=$3
checksum=$4
checksum_temporary="${checksum}.tmp.$$"

cleanup_remote() {
    rm -f "$partial" "$checksum_temporary"
}
trap cleanup_remote EXIT HUP INT TERM

actual=$(sha256sum "$partial" | awk '{print $1}')
if [ "$actual" != "$expected" ]; then
    echo "off-site upload checksum mismatch" >&2
    exit 1
fi
printf '%s  %s\n' "$expected" "$(basename "$final")" > "$checksum_temporary"
chmod 600 "$partial" "$checksum_temporary"
mv -f "$partial" "$final"
mv -f "$checksum_temporary" "$checksum"
trap - EXIT HUP INT TERM
SH
echo "encrypted off-site backup copied: $name"
