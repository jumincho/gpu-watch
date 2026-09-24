#!/bin/sh
set -eu

ROOT=$(CDPATH='' cd -- "$(dirname -- "$0")/.." && pwd)
CONTAINER=gpu-watch-dashboard
APP_DATA_DIR="$ROOT/data"
DATABASE="$APP_DATA_DIR/gpu_watch.sqlite3"
BACKUP=${1:-}

if [ ! -d "$APP_DATA_DIR/backups" ] || [ -L "$APP_DATA_DIR" ] || \
    [ -L "$APP_DATA_DIR/backups" ] || \
    find "$APP_DATA_DIR" -type l -print -quit | grep -q .; then
    echo "application data directory is missing or unsafe" >&2
    exit 65
fi

if [ -z "$BACKUP" ] || [ ! -f "$BACKUP" ]; then
    echo "usage: $0 /absolute/path/to/backup.sqlite3" >&2
    exit 64
fi

exec 9>"$ROOT/.deploy.lock"
if ! flock -n 9; then
    echo "deployment or restore already running" >&2
    exit 73
fi

case "$(realpath "$BACKUP")" in
    "$(realpath "$APP_DATA_DIR/backups")"/*) ;;
    *) echo "backup must be inside $APP_DATA_DIR/backups" >&2; exit 65 ;;
esac

timestamp=$(date -u +%Y%m%dT%H%M%SZ)
rollback="$APP_DATA_DIR/backups/pre-restore-${timestamp}.sqlite3"
temporary="$APP_DATA_DIR/.gpu_watch.restore.tmp"
rollback_temporary="$APP_DATA_DIR/.gpu_watch.rollback.tmp"
container_stopped=0
database_replaced=0
restore_complete=0

verify_gpu_watch_database() {
    python3 - "$1" "$ROOT" <<'PY'
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, sys.argv[2])
from server import SCHEMA_VERSION

required_tables = {
    "announcements",
    "dashboard_settings",
    "events",
    "gpu_runtime",
    "host_runtime",
    "maintenance_meta",
    "schema_meta",
}
uri = Path(sys.argv[1]).resolve().as_uri() + "?mode=ro&immutable=1"
connection = sqlite3.connect(uri, uri=True)
try:
    connection.execute("pragma query_only=on")
    integrity = str(connection.execute("pragma quick_check").fetchone()[0])
    if integrity != "ok":
        raise SystemExit("database integrity check failed: " + integrity)
    if connection.execute("pragma foreign_key_check").fetchone() is not None:
        raise SystemExit("database foreign key check failed")
    tables = {
        str(row[0])
        for row in connection.execute("select name from sqlite_master where type='table'")
    }
    missing = sorted(required_tables - tables)
    if missing:
        raise SystemExit("not a GPU Watch database; missing tables: " + ", ".join(missing))
    schema_row = connection.execute(
        "select value from schema_meta where key='schema_version'"
    ).fetchone()
    try:
        schema_version = int(schema_row[0]) if schema_row else 0
    except (TypeError, ValueError):
        schema_version = 0
    supported_schema_version = SCHEMA_VERSION
    if not 1 <= schema_version <= supported_schema_version:
        raise SystemExit(
            "unsupported GPU Watch database schema_version: "
            f"{schema_version} (supported: 1..{supported_schema_version})"
        )
finally:
    connection.close()
PY
}

wait_for_container_health() {
    health_attempt=0
    while [ "$health_attempt" -lt 18 ]; do
        if ! health_status=$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$CONTAINER" 2>/dev/null); then
            return 1
        fi
        if [ "$health_status" = "healthy" ]; then
            return 0
        fi
        if [ "$health_status" = "unhealthy" ] || [ "$health_status" = "exited" ]; then
            return 1
        fi
        health_attempt=$((health_attempt + 1))
        sleep 5
    done
    return 1
}

rollback_restore() {
    rollback_failed=0
    safe_to_start=1
    if [ "$database_replaced" -eq 1 ]; then
        if [ ! -f "$rollback" ]; then
            echo "critical: pre-restore database is missing: $rollback" >&2
            rollback_failed=1
            safe_to_start=0
        elif ! docker stop -t 15 "$CONTAINER" >/dev/null 2>&1; then
            echo "critical: failed to stop $CONTAINER before database rollback" >&2
            rollback_failed=1
            safe_to_start=0
        fi
    fi
    if [ "$database_replaced" -eq 1 ] && [ "$safe_to_start" -eq 1 ]; then
        rm -f "$rollback_temporary"
        if cp "$rollback" "$rollback_temporary" && verify_gpu_watch_database "$rollback_temporary"; then
            chmod 600 "$rollback_temporary"
            rm -f "$DATABASE-wal" "$DATABASE-shm"
            mv "$rollback_temporary" "$DATABASE"
        else
            rm -f "$rollback_temporary"
            echo "critical: failed to restore the pre-restore database $rollback" >&2
            rollback_failed=1
            safe_to_start=0
        fi
    fi
    if [ "$container_stopped" -eq 1 ] && [ "$safe_to_start" -eq 1 ]; then
        if ! docker start "$CONTAINER" >/dev/null 2>&1; then
            echo "critical: failed to restart $CONTAINER" >&2
            rollback_failed=1
        elif ! wait_for_container_health; then
            echo "critical: rolled-back $CONTAINER did not become healthy" >&2
            docker logs --tail 80 "$CONTAINER" >&2 2>/dev/null || true
            rollback_failed=1
        fi
    fi
    [ "$rollback_failed" -eq 0 ]
}

finish() {
    status=$?
    trap - EXIT HUP INT TERM
    rm -f "$temporary" "$rollback_temporary"
    rollback_status=0
    if [ "$status" -ne 0 ] && [ "$restore_complete" -ne 1 ] && [ "$container_stopped" -eq 1 ]; then
        echo "restore interrupted; restoring the previous database" >&2
        if ! rollback_restore; then
            rollback_status=1
        fi
    fi
    if [ "$rollback_status" -ne 0 ]; then
        echo "critical: database rollback did not complete cleanly" >&2
        exit 74
    fi
    exit "$status"
}
trap finish EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM

verify_gpu_watch_database "$BACKUP"

docker container inspect "$CONTAINER" >/dev/null 2>&1

container_stopped=1
docker stop -t 15 "$CONTAINER" >/dev/null

rm -f "$rollback"
python3 - "$DATABASE" "$rollback" <<'PY'
import sqlite3
import sys
from contextlib import closing

with closing(sqlite3.connect(f"file:{sys.argv[1]}?mode=ro", uri=True)) as source, \
     closing(sqlite3.connect(sys.argv[2])) as destination:
    source.backup(destination)
    destination.commit()
    journal_mode = str(destination.execute("pragma journal_mode=delete").fetchone()[0]).lower()
    if journal_mode != "delete":
        raise SystemExit("pre-restore backup journal mode is not standalone: " + journal_mode)
    result = destination.execute("pragma quick_check").fetchone()[0]
    foreign_key_violation = destination.execute("pragma foreign_key_check").fetchone()
if result != "ok":
    raise SystemExit("pre-restore backup integrity check failed: " + str(result))
if foreign_key_violation is not None:
    raise SystemExit("pre-restore backup foreign key check failed")
PY
chmod 600 "$rollback"
verify_gpu_watch_database "$rollback"

cp "$BACKUP" "$temporary"
chmod 600 "$temporary"
verify_gpu_watch_database "$temporary"
database_replaced=1
mv "$temporary" "$DATABASE"
chmod 600 "$DATABASE"
rm -f "$DATABASE-wal" "$DATABASE-shm"
docker start "$CONTAINER" >/dev/null

if wait_for_container_health; then
    restore_complete=1
    container_stopped=0
    echo "restore completed: $(basename "$BACKUP")"
    exit 0
fi

echo "restored database failed health check" >&2
exit 1
