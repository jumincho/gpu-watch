#!/bin/sh
set -eu
export PYTHONDONTWRITEBYTECODE=1

ROOT=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
VERSION_FILE="$ROOT/VERSION"
IMAGE=gpu-watch-dashboard:local
CONTAINER=gpu-watch-dashboard
PREVIOUS_CONTAINER=${CONTAINER}-previous
CADDY_CONTAINER=gpu-watch-caddy
PREVIOUS_CADDY_CONTAINER=${CADDY_CONTAINER}-previous
CADDY_IMAGE=gpu-watch-caddy:local
NETWORK=gpu-watch-network
SSH_DIR=/srv/gpu-watch/ssh
SECRET_DIR="$ROOT/secrets"
OPERATOR_SECRET_DIR="$ROOT/operator-secrets"
APP_DATA_DIR="$ROOT/data"
RUNTIME_SSH_DIR="$ROOT/runtime-ssh"
RUNTIME_SSH_STAGE="$ROOT/.runtime-ssh.stage.$$"
RUNTIME_SSH_PREVIOUS="$ROOT/.runtime-ssh.previous.$$"
RUNTIME_SSH_FAILED="$ROOT/.runtime-ssh.failed.$$"
APP_UID=$(id -u)
APP_GID=$(id -g)
if [ ! -f "$VERSION_FILE" ]; then
    echo "release version file is missing: $VERSION_FILE" >&2
    exit 66
fi
RELEASE_VERSION=$(tr -d '\r\n' < "$VERSION_FILE")
case "$RELEASE_VERSION" in
    ''|*[!0-9A-Za-z.+-]*) echo "invalid release version: $RELEASE_VERSION" >&2; exit 65 ;;
esac
if [ "${GPU_WATCH_BUILD_VERSION:-$RELEASE_VERSION}" != "$RELEASE_VERSION" ]; then
    echo "GPU_WATCH_BUILD_VERSION must match VERSION ($RELEASE_VERSION)" >&2
    exit 65
fi
BUILD_VERSION=$RELEASE_VERSION
case "$BUILD_VERSION" in
    ''|*[!0-9A-Za-z.+-]*) echo "invalid build version: $BUILD_VERSION" >&2; exit 65 ;;
esac
ALLOW_LOW_DISK_DEPLOY=${GPU_WATCH_ALLOW_LOW_DISK_DEPLOY:-0}
case "$ALLOW_LOW_DISK_DEPLOY" in
    0|1) ;;
    *) echo "GPU_WATCH_ALLOW_LOW_DISK_DEPLOY must be 0 or 1" >&2; exit 65 ;;
esac
if [ "$ALLOW_LOW_DISK_DEPLOY" -eq 1 ]; then
    echo "warning: deployment may accept only the known database disk-space health failure" >&2
fi

exec 9>"$ROOT/.deploy.lock"
if ! flock -n 9; then
    echo "another GPU Watch deployment is already running" >&2
    exit 73
fi

cleanup_private_directory() {
    cleanup_path=$1
    case "$cleanup_path" in
        "$ROOT"/.runtime-ssh.stage.*|"$ROOT"/.runtime-ssh.previous.*|"$ROOT"/.runtime-ssh.failed.*) ;;
        *)
            echo "refusing to remove unexpected runtime SSH path: $cleanup_path" >&2
            return 1
            ;;
    esac
    if [ -e "$cleanup_path" ] || [ -L "$cleanup_path" ]; then
        rm -rf -- "$cleanup_path"
    fi
}

cleanup_stale_private_directories() {
    for stale_path in \
        "$ROOT"/.runtime-ssh.stage.* \
        "$ROOT"/.runtime-ssh.previous.* \
        "$ROOT"/.runtime-ssh.failed.*
    do
        if [ -e "$stale_path" ] || [ -L "$stale_path" ]; then
            cleanup_private_directory "$stale_path"
        fi
    done
}

cleanup_dangling_release_images() {
    # Limit cleanup to dangling images produced by this project. Never use a
    # host-wide prune: the production host also runs unrelated services and
    # keeps images for their recovery paths.
    for image_title in "GPU Watch" "GPU Watch Edge" "GPU Watch Edge Builder"; do
        if ! docker image prune --force \
            --filter "label=org.opencontainers.image.title=$image_title" >/dev/null
        then
            echo "warning: failed to prune dangling $image_title images" >&2
        fi
    done
}

early_finish() {
    status=$?
    trap - EXIT HUP INT TERM
    cleanup_private_directory "$RUNTIME_SSH_STAGE" || true
    exit "$status"
}
trap early_finish EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM

# The deployment lock makes every matching directory an abandoned artifact.
# Remove it before Docker evaluates the build context.
cleanup_stale_private_directories

for protected_path in \
    "$APP_DATA_DIR" "$APP_DATA_DIR/backups" "$SECRET_DIR" "$OPERATOR_SECRET_DIR" "$RUNTIME_SSH_DIR"
do
    if [ -L "$protected_path" ]; then
        echo "refusing a symlinked GPU Watch runtime path: $protected_path" >&2
        exit 65
    fi
done
mkdir -p \
    "$APP_DATA_DIR/backups" "$SECRET_DIR" "$OPERATOR_SECRET_DIR" "$RUNTIME_SSH_DIR"

validate_private_directories() {
    for private_directory in "$SECRET_DIR" "$OPERATOR_SECRET_DIR"; do
        if [ -L "$private_directory" ] || \
            find "$private_directory" -type l -print -quit | grep -q .; then
            echo "private runtime directory must not contain symlinks: $private_directory" >&2
            return 1
        fi
    done
}

validate_application_data_directory() {
    if [ -L "$APP_DATA_DIR" ] || [ -L "$APP_DATA_DIR/backups" ] || \
        find "$APP_DATA_DIR" -type l -print -quit | grep -q .; then
        echo "application data must contain only regular files and directories" >&2
        return 1
    fi
    for data_entry in "$APP_DATA_DIR"/* "$APP_DATA_DIR"/.[!.]* "$APP_DATA_DIR"/..?*; do
        if [ ! -e "$data_entry" ] && [ ! -L "$data_entry" ]; then
            continue
        fi
        data_name=${data_entry##*/}
        case "$data_name" in
            backups|audit)
                if [ ! -d "$data_entry" ]; then
                    echo "application data entry must be a directory: $data_name" >&2
                    return 1
                fi
                ;;
            gpu_watch.sqlite3|gpu_watch.sqlite3-wal|gpu_watch.sqlite3-shm|gpu_watch.sqlite3-journal|\
            admin_pin.hash|artificial_analysis_intelligence_index.json|.offsite-backup.lock|\
            .gpu_watch.sqlite3.sanitize.tmp*|.gpu-watch-rollback-*.sqlite3*|\
            .artificial_analysis_intelligence_index.json.*.tmp)
                if [ ! -f "$data_entry" ]; then
                    echo "application data entry must be a regular file: $data_name" >&2
                    return 1
                fi
                ;;
            *)
                echo "unexpected entry in application data directory: $data_name" >&2
                return 1
                ;;
        esac
    done
}
validate_application_data_directory
validate_private_directories
chmod 700 \
    "$APP_DATA_DIR" "$APP_DATA_DIR/backups" "$SECRET_DIR" "$OPERATOR_SECRET_DIR" "$RUNTIME_SSH_DIR"
find "$APP_DATA_DIR" "$SECRET_DIR" "$OPERATOR_SECRET_DIR" -maxdepth 1 -type f -exec sh -c '
    for path do
        if ! chmod 600 "$path" 2>/dev/null && [ -e "$path" ]; then
            exit 1
        fi
    done
' sh {} +
find "$APP_DATA_DIR/backups" -type f -exec chmod 600 {} \;

python3 "$ROOT/scripts/admin-passphrase.py" ensure \
    --hash-path "$APP_DATA_DIR/admin_pin.hash" \
    --initial-path "$OPERATOR_SECRET_DIR/admin-passphrase.initial"

docker build --pull --no-cache \
    --build-arg "APP_UID=$APP_UID" \
    --build-arg "APP_GID=$APP_GID" \
    --build-arg "BUILD_VERSION=$BUILD_VERSION" \
    -t "$IMAGE" "$ROOT"
docker build --pull --no-cache \
    -f "$ROOT/Dockerfile.caddy" \
    --build-arg "APP_UID=$APP_UID" \
    --build-arg "APP_GID=$APP_GID" \
    --build-arg "BUILD_VERSION=$BUILD_VERSION" \
    -t "$CADDY_IMAGE" "$ROOT"

cleanup_private_directory "$RUNTIME_SSH_STAGE"
mkdir -m 700 "$RUNTIME_SSH_STAGE"
ssh_source_ready=1
for source_file in config known_hosts gpu_watch_ed25519; do
    if [ ! -f "$SSH_DIR/$source_file" ]; then
        ssh_source_ready=0
    fi
done
if [ "$ssh_source_ready" -eq 1 ]; then
    docker run --rm \
        --user 0:0 \
        --network none \
        --read-only \
        --cap-drop ALL \
        --cap-add CHOWN \
        --cap-add DAC_OVERRIDE \
        --cap-add FOWNER \
        --security-opt no-new-privileges:true \
        -v "$SSH_DIR/config:/source/config:ro" \
        -v "$SSH_DIR/known_hosts:/source/known_hosts:ro" \
        -v "$SSH_DIR/gpu_watch_ed25519:/source/gpu_watch_ed25519:ro" \
        -v "$RUNTIME_SSH_STAGE:/dest" \
        --entrypoint /bin/sh \
        "$IMAGE" -c "
            set -eu
            umask 077
            sed \
                -e 's#/root/.ssh#/home/gpuwatch/.ssh#g' \
                /source/config > /dest/config
            cp /source/known_hosts /dest/known_hosts
            cp /source/gpu_watch_ed25519 /dest/gpu_watch_ed25519
            chown -R $APP_UID:$APP_GID /dest
            chmod 700 /dest
            chmod 600 /dest/config /dest/known_hosts /dest/gpu_watch_ed25519
        "
else
    echo "source SSH directory is unavailable; staging the verified current runtime SSH snapshot"
    for source_file in config known_hosts gpu_watch_ed25519; do
        if [ ! -s "$RUNTIME_SSH_DIR/$source_file" ]; then
            echo "required runtime SSH file is missing: $RUNTIME_SSH_DIR/$source_file" >&2
            exit 66
        fi
        cp "$RUNTIME_SSH_DIR/$source_file" "$RUNTIME_SSH_STAGE/$source_file"
    done
fi

python3 "$ROOT/scripts/normalize-ssh-config.py" "$RUNTIME_SSH_STAGE/config"
if [ "$(grep -c '^  UserKnownHostsFile /home/gpuwatch/.ssh/known_hosts$' "$RUNTIME_SSH_STAGE/config")" -ne 1 ] || \
    ! grep -q 'StrictHostKeyChecking yes' "$RUNTIME_SSH_STAGE/config" || \
    grep -q '/root/.ssh' "$RUNTIME_SSH_STAGE/config"; then
    echo "current runtime SSH config failed the strict-host/path validation" >&2
    exit 65
fi
chmod 700 "$RUNTIME_SSH_STAGE"
chmod 600 "$RUNTIME_SSH_STAGE/config" "$RUNTIME_SSH_STAGE/known_hosts" "$RUNTIME_SSH_STAGE/gpu_watch_ed25519"

validate_runtime_ssh_stage() {
    docker run --rm \
        --user "$APP_UID:$APP_GID" \
        --network none \
        --read-only \
        --cap-drop ALL \
        --security-opt no-new-privileges:true \
        -e HOME=/home/gpuwatch \
        -v "$RUNTIME_SSH_STAGE:/home/gpuwatch/.ssh:ro" \
        --entrypoint /bin/sh \
        "$IMAGE" -c '
            set -eu
            config=/home/gpuwatch/.ssh/config
            known_hosts=/home/gpuwatch/.ssh/known_hosts
            identity=/home/gpuwatch/.ssh/gpu_watch_ed25519

            fail() {
                printf "%s\n" "runtime SSH contract failed for $1: $2" >&2
                exit 65
            }
            require_line() {
                check_alias=$1
                check_output=$2
                check_expected=$3
                if ! printf "%s\n" "$check_output" | grep -F -x -q "$check_expected"; then
                    fail "$check_alias" "missing effective option: $check_expected"
                fi
            }
            reject_key() {
                check_alias=$1
                check_output=$2
                check_key=$3
                if printf "%s\n" "$check_output" | grep -E -q "^${check_key}[[:space:]]"; then
                    fail "$check_alias" "forbidden effective option: $check_key"
                fi
            }
            require_known_host() {
                check_alias=$1
                check_host=$2
                check_port=$3
                if [ "$check_port" -eq 22 ]; then
                    check_lookup=$check_host
                else
                    check_lookup="[$check_host]:$check_port"
                fi
                if ! /usr/bin/ssh-keygen -F "$check_lookup" -f "$known_hosts" >/dev/null; then
                    fail "$check_alias" "known_hosts has no pinned key for $check_lookup"
                fi
            }

            for name in lab21 lab22 lab23 lab24 lab25 lab26 lab27 lab28; do
                alias=gpuwatch-$name
                if ! effective=$(/usr/bin/ssh -G -F "$config" "$alias" 2>/dev/null); then
                    fail "$alias" "ssh -G failed"
                fi
                case "$effective" in
                    *"/root/.ssh"*) fail "$alias" "unrewritten /root/.ssh path" ;;
                esac

                require_line "$alias" "$effective" "user gpuwatch"
                require_line "$alias" "$effective" "batchmode no"
                require_line "$alias" "$effective" "numberofpasswordprompts 1"
                require_line "$alias" "$effective" "preferredauthentications password,keyboard-interactive"
                require_line "$alias" "$effective" "passwordauthentication yes"
                require_line "$alias" "$effective" "kbdinteractiveauthentication yes"
                require_line "$alias" "$effective" "pubkeyauthentication false"
                require_line "$alias" "$effective" "stricthostkeychecking true"
                require_line "$alias" "$effective" "updatehostkeys false"
                require_line "$alias" "$effective" "userknownhostsfile $known_hosts"
                reject_key "$alias" "$effective" proxycommand

                case "$name" in
                    lab21)
                        require_line "$alias" "$effective" "hostname 198.51.100.21"
                        require_line "$alias" "$effective" "port 2222"
                        reject_key "$alias" "$effective" proxyjump
                        require_known_host "$alias" 198.51.100.21 2222
                        ;;
                    lab22) expected_host=198.51.100.22 ;;
                    lab23) expected_host=198.51.100.23 ;;
                    lab24) expected_host=198.51.100.24 ;;
                    lab25) expected_host=198.51.100.25 ;;
                    lab26) expected_host=198.51.100.26 ;;
                    lab27) expected_host=198.51.100.27 ;;
                    lab28) expected_host=198.51.100.28 ;;
                esac
                if [ "$name" != lab21 ]; then
                    require_line "$alias" "$effective" "hostname $expected_host"
                    require_line "$alias" "$effective" "port 22"
                    require_line "$alias" "$effective" "proxyjump gpuwatch-lab21"
                    require_known_host "$alias" "$expected_host" 22
                fi
            done

            validate_nll() {
                alias=$1
                expected_host=$2
                expected_port=$3
                if ! effective=$(/usr/bin/ssh -G -F "$config" \
                    -o BatchMode=yes \
                    -i "$identity" \
                    -o IdentitiesOnly=yes \
                    -o PreferredAuthentications=publickey \
                    -p "$expected_port" \
                    "gpuwatch@$expected_host" 2>/dev/null); then
                    fail "$alias" "ssh -G failed"
                fi
                require_line "$alias" "$effective" "hostname $expected_host"
                require_line "$alias" "$effective" "user gpuwatch"
                require_line "$alias" "$effective" "port $expected_port"
                require_line "$alias" "$effective" "batchmode yes"
                require_line "$alias" "$effective" "identitiesonly yes"
                require_line "$alias" "$effective" "preferredauthentications publickey"
                require_line "$alias" "$effective" "pubkeyauthentication true"
                require_line "$alias" "$effective" "stricthostkeychecking true"
                require_line "$alias" "$effective" "updatehostkeys false"
                require_line "$alias" "$effective" "identityfile $identity"
                require_line "$alias" "$effective" "userknownhostsfile $known_hosts"
                reject_key "$alias" "$effective" proxycommand
                reject_key "$alias" "$effective" proxyjump
                require_known_host "$alias" "$expected_host" "$expected_port"
            }

            validate_nll atlas 192.0.2.11 22
            validate_nll boreas 192.0.2.12 22
            validate_nll cygnus 192.0.2.13 22
            validate_nll draco 192.0.2.14 22
            validate_nll eridanus 192.0.2.15 22
            validate_nll grus 192.0.2.17 22
            validate_nll hydrus 192.0.2.18 22
            validate_nll indus 192.0.2.10 22
            validate_nll lupus 192.0.2.19 22
            validate_nll mensa 192.0.2.20 22
            validate_nll norma 192.0.2.21 22
            validate_nll octans 192.0.2.22 22
            validate_nll pictor 192.0.2.23 22
            validate_nll fornax 192.0.2.16 22
        '
}
validate_runtime_ssh_stage

docker network inspect "$NETWORK" >/dev/null 2>&1 || docker network create "$NETWORK" >/dev/null
NETWORK_SUBNETS=$(docker network inspect --format '{{range .IPAM.Config}}{{if .Subnet}}{{.Subnet}},{{end}}{{end}}' "$NETWORK" | sed 's/,$//')
if [ -z "$NETWORK_SUBNETS" ]; then
    echo "unable to determine the private Docker network subnets" >&2
    exit 1
fi

docker run --rm \
    --user "$APP_UID:$APP_GID" \
    --read-only \
    --cap-drop ALL \
    --security-opt no-new-privileges:true \
    --tmpfs "/tmp:rw,noexec,nosuid,size=16m,uid=$APP_UID,gid=$APP_GID,mode=0700" \
    --tmpfs "/data:rw,noexec,nosuid,size=16m,uid=$APP_UID,gid=$APP_GID,mode=0700" \
    --tmpfs "/config:rw,noexec,nosuid,size=16m,uid=$APP_UID,gid=$APP_GID,mode=0700" \
    -v "$ROOT/Caddyfile:/etc/caddy/Caddyfile:ro" \
    "$CADDY_IMAGE" validate --config /etc/caddy/Caddyfile --adapter caddyfile >/dev/null

predeploy_backup=
create_predeploy_database_backup() {
    if [ ! -f "$APP_DATA_DIR/gpu_watch.sqlite3" ]; then
        return 0
    fi
    backup_name=$(printf '%s' "$BUILD_VERSION" | tr -c 'A-Za-z0-9._+-' '_')
    backup_stamp=$(date -u +%Y%m%dT%H%M%SZ)
    predeploy_backup="$APP_DATA_DIR/backups/pre-deploy-${backup_name}-${backup_stamp}-$$.sqlite3"
    predeploy_temporary="$APP_DATA_DIR/backups/.pre-deploy-${backup_name}-${backup_stamp}-$$.tmp"
    rm -f "$predeploy_temporary" "$predeploy_temporary-wal" "$predeploy_temporary-shm"
    if ! python3 - "$APP_DATA_DIR/gpu_watch.sqlite3" "$predeploy_temporary" <<'PY'
import sqlite3
import sys
from contextlib import closing

with closing(sqlite3.connect(sys.argv[1])) as source, closing(sqlite3.connect(sys.argv[2])) as destination:
    source.backup(destination)
    destination.commit()
    journal_mode = str(destination.execute("pragma journal_mode=delete").fetchone()[0]).lower()
    if journal_mode != "delete":
        raise SystemExit("pre-deploy backup journal mode is not standalone: " + journal_mode)
    integrity = destination.execute("pragma quick_check").fetchone()[0]
    if integrity != "ok":
        raise SystemExit("pre-deploy backup integrity failure: " + str(integrity))
    if destination.execute("pragma foreign_key_check").fetchone() is not None:
        raise SystemExit("pre-deploy backup foreign key check failed")
PY
    then
        rm -f "$predeploy_temporary" "$predeploy_temporary-wal" "$predeploy_temporary-shm"
        exit 1
    fi
    chmod 600 "$predeploy_temporary"
    mv -f "$predeploy_temporary" "$predeploy_backup"
    rm -f "$predeploy_temporary-wal" "$predeploy_temporary-shm"
}

if docker container inspect "$PREVIOUS_CONTAINER" >/dev/null 2>&1; then
    docker rm -f "$PREVIOUS_CONTAINER" >/dev/null
fi
if docker container inspect "$PREVIOUS_CADDY_CONTAINER" >/dev/null 2>&1; then
    docker rm -f "$PREVIOUS_CADDY_CONTAINER" >/dev/null
fi

HAD_PREVIOUS=0
HAD_APP=0
HAD_CADDY=0
HAD_DATABASE=0
APP_SWITCHED=0
SSH_SWITCHED=0
DATABASE_RESTORE_REQUIRED=0
DEPLOY_COMPLETE=0
if docker container inspect "$CONTAINER" >/dev/null 2>&1; then
    HAD_APP=1
fi
if docker container inspect "$CADDY_CONTAINER" >/dev/null 2>&1; then
    HAD_CADDY=1
fi
if [ -f "$APP_DATA_DIR/gpu_watch.sqlite3" ]; then
    HAD_DATABASE=1
fi

check_low_disk_only() {
    check_container=$1
    check_expected_version=${2:-}
    if [ -n "$check_expected_version" ]; then
        docker exec "$check_container" python3 /app/scripts/check_local.py \
            --allow-low-disk-only --expected-build-version "$check_expected_version" --timeout 10 >/dev/null
    else
        docker exec "$check_container" python3 /app/scripts/check_local.py \
            --allow-low-disk-only --timeout 10 >/dev/null
    fi
}

check_container_health() {
    check_container=$1
    check_expected_version=${2:-}
    if [ -n "$check_expected_version" ]; then
        docker exec "$check_container" python3 /app/scripts/check_local.py \
            --health-only --expected-build-version "$check_expected_version" --timeout 10 >/dev/null
    else
        docker exec "$check_container" python3 /app/scripts/check_local.py \
            --health-only --timeout 10 >/dev/null
    fi
}

wait_container_healthy() {
    wait_container=$1
    wait_attempts=$2
    wait_delay=$3
    wait_expected_version=${4:-}
    wait_attempt=0
    while [ "$wait_attempt" -lt "$wait_attempts" ]; do
        if ! wait_status=$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$wait_container" 2>/dev/null); then
            return 1
        fi
        if [ "$wait_status" = "healthy" ]; then
            if check_container_health "$wait_container" "$wait_expected_version"; then
                return 0
            fi
            return 1
        fi
        if [ "$ALLOW_LOW_DISK_DEPLOY" -eq 1 ] && [ "$wait_status" != "exited" ] && \
            check_low_disk_only "$wait_container" "$wait_expected_version"; then
            return 0
        fi
        if [ "$wait_status" = "exited" ] || \
            { [ "$ALLOW_LOW_DISK_DEPLOY" -eq 0 ] && [ "$wait_status" = "unhealthy" ]; }; then
            return 1
        fi
        wait_attempt=$((wait_attempt + 1))
        sleep "$wait_delay"
    done
    return 1
}

wait_caddy_healthy() {
    wait_expected_version=${1:-}
    wait_attempt=0
    while [ "$wait_attempt" -lt 20 ]; do
        if [ "$ALLOW_LOW_DISK_DEPLOY" -eq 1 ]; then
            if docker exec "$CADDY_CONTAINER" wget -q -O - \
                --header='Host: 192.0.2.10:8787' http://127.0.0.1:8787/api/snapshot | \
                python3 -c 'import json,sys; expected=sys.argv[1]; payload=json.load(sys.stdin); version=str(payload.get("build_version") or ""); raise SystemExit(0 if version and (not expected or version == expected) else 1)' \
                "$wait_expected_version"; then
                return 0
            fi
        elif docker exec "$CADDY_CONTAINER" wget -q -O /dev/null \
            --header='Host: 192.0.2.10:8787' http://127.0.0.1:8787/api/health; then
                return 0
        fi
        wait_attempt=$((wait_attempt + 1))
        sleep 2
    done
    return 1
}

ensure_container_running() {
    ensure_container=$1
    if [ "$(docker inspect --format '{{.State.Running}}' "$ensure_container" 2>/dev/null || true)" = "true" ]; then
        return 0
    fi
    docker start "$ensure_container" >/dev/null 2>&1
}

switch_runtime_ssh() {
    cleanup_private_directory "$RUNTIME_SSH_PREVIOUS"
    cleanup_private_directory "$RUNTIME_SSH_FAILED"
    if ! mv "$RUNTIME_SSH_DIR" "$RUNTIME_SSH_PREVIOUS"; then
        echo "failed to stage the previous runtime SSH directory" >&2
        return 1
    fi
    if mv "$RUNTIME_SSH_STAGE" "$RUNTIME_SSH_DIR"; then
        SSH_SWITCHED=1
        return 0
    fi
    echo "failed to publish the staged runtime SSH directory" >&2
    if ! mv "$RUNTIME_SSH_PREVIOUS" "$RUNTIME_SSH_DIR"; then
        echo "critical: failed to restore the previous runtime SSH directory" >&2
    fi
    return 1
}

restore_runtime_ssh() {
    if [ "$SSH_SWITCHED" -ne 1 ]; then
        return 0
    fi
    if [ ! -d "$RUNTIME_SSH_PREVIOUS" ]; then
        echo "critical: previous runtime SSH directory is missing" >&2
        return 1
    fi
    cleanup_private_directory "$RUNTIME_SSH_FAILED" || return 1
    if [ -e "$RUNTIME_SSH_DIR" ] && ! mv "$RUNTIME_SSH_DIR" "$RUNTIME_SSH_FAILED"; then
        echo "critical: failed to move the new runtime SSH directory aside" >&2
        return 1
    fi
    if ! mv "$RUNTIME_SSH_PREVIOUS" "$RUNTIME_SSH_DIR"; then
        echo "critical: failed to restore the previous runtime SSH directory" >&2
        if [ -e "$RUNTIME_SSH_FAILED" ] && [ ! -e "$RUNTIME_SSH_DIR" ]; then
            mv "$RUNTIME_SSH_FAILED" "$RUNTIME_SSH_DIR" 2>/dev/null || true
        fi
        return 1
    fi
    SSH_SWITCHED=0
    if ! cleanup_private_directory "$RUNTIME_SSH_FAILED"; then
        echo "critical: failed to remove the rejected runtime SSH directory" >&2
        return 1
    fi
    return 0
}

restore_predeploy_database() {
    if [ -z "$predeploy_backup" ] || [ ! -f "$predeploy_backup" ]; then
        echo "critical: verified pre-deploy database backup is unavailable" >&2
        return 1
    fi
    rollback_temporary="$APP_DATA_DIR/.gpu-watch-rollback-$$.sqlite3"
    rm -f "$rollback_temporary" "$rollback_temporary-wal" "$rollback_temporary-shm"
    if ! python3 - "$predeploy_backup" "$APP_DATA_DIR/gpu_watch.sqlite3" "$rollback_temporary" <<'PY'
import os
import sqlite3
import sys
from contextlib import closing
from urllib.parse import quote

source_path, destination_path, temporary_path = sys.argv[1:]
source_uri = f"file:{quote(os.path.abspath(source_path))}?mode=ro&immutable=1"
with closing(sqlite3.connect(source_uri, uri=True)) as source:
    source.execute("pragma query_only=on")
    if source.execute("pragma quick_check").fetchone()[0] != "ok":
        raise SystemExit("pre-deploy database backup integrity failure")
    if source.execute("pragma foreign_key_check").fetchone() is not None:
        raise SystemExit("pre-deploy database backup foreign key failure")
    with closing(sqlite3.connect(temporary_path)) as destination:
        source.backup(destination)
        destination.commit()
        journal_mode = str(destination.execute("pragma journal_mode=delete").fetchone()[0]).lower()
        if journal_mode != "delete":
            raise SystemExit("rollback database is not standalone: " + journal_mode)
        if destination.execute("pragma quick_check").fetchone()[0] != "ok":
            raise SystemExit("rollback database integrity failure")
        if destination.execute("pragma foreign_key_check").fetchone() is not None:
            raise SystemExit("rollback database foreign key failure")

os.chmod(temporary_path, 0o600)
for sidecar in (destination_path + "-wal", destination_path + "-shm"):
    try:
        os.unlink(sidecar)
    except FileNotFoundError:
        pass
os.replace(temporary_path, destination_path)
PY
    then
        rm -f "$rollback_temporary" "$rollback_temporary-wal" "$rollback_temporary-shm"
        return 1
    fi
    chmod 600 "$APP_DATA_DIR/gpu_watch.sqlite3"
    rm -f "$rollback_temporary-wal" "$rollback_temporary-shm"
}

rollback() {
    rollback_failed=0
    if [ "$HAD_APP" -eq 1 ]; then
        if docker container inspect "$PREVIOUS_CONTAINER" >/dev/null 2>&1; then
            app_restore_ready=1
            if docker container inspect "$CONTAINER" >/dev/null 2>&1 && \
                ! docker rm -f "$CONTAINER" >/dev/null 2>&1; then
                echo "critical: failed to remove the rejected app container" >&2
                rollback_failed=1
                app_restore_ready=0
            fi
            if ! restore_runtime_ssh; then
                rollback_failed=1
            fi
            if [ "$app_restore_ready" -eq 1 ] && [ "$DATABASE_RESTORE_REQUIRED" -eq 1 ] && \
                ! restore_predeploy_database; then
                echo "critical: failed to restore the pre-deploy database" >&2
                rollback_failed=1
                app_restore_ready=0
            fi
            if [ "$app_restore_ready" -ne 1 ]; then
                echo "critical: previous app was not restarted without its verified database" >&2
            elif ! docker rename "$PREVIOUS_CONTAINER" "$CONTAINER" >/dev/null 2>&1; then
                echo "critical: failed to rename the previous app container" >&2
                rollback_failed=1
            elif ! ensure_container_running "$CONTAINER"; then
                echo "critical: failed to restart the previous app container" >&2
                rollback_failed=1
            fi
        elif docker container inspect "$CONTAINER" >/dev/null 2>&1; then
            if ! restore_runtime_ssh; then
                rollback_failed=1
            fi
            if ! ensure_container_running "$CONTAINER"; then
                echo "critical: failed to restart the app container" >&2
                rollback_failed=1
            fi
        else
            echo "critical: no app container is available for rollback" >&2
            rollback_failed=1
        fi
    else
        if docker container inspect "$CONTAINER" >/dev/null 2>&1 && \
            ! docker rm -f "$CONTAINER" >/dev/null 2>&1; then
            echo "critical: failed to remove the rejected app container" >&2
            rollback_failed=1
        fi
        if ! restore_runtime_ssh; then
            rollback_failed=1
        fi
        if [ "$DATABASE_RESTORE_REQUIRED" -eq 1 ] && ! restore_predeploy_database; then
            echo "critical: failed to restore the pre-deploy database" >&2
            rollback_failed=1
        fi
    fi
    if [ "$HAD_CADDY" -eq 1 ]; then
        if docker container inspect "$PREVIOUS_CADDY_CONTAINER" >/dev/null 2>&1; then
            if docker container inspect "$CADDY_CONTAINER" >/dev/null 2>&1 && \
                ! docker rm -f "$CADDY_CONTAINER" >/dev/null 2>&1; then
                echo "critical: failed to remove the rejected Caddy container" >&2
                rollback_failed=1
            fi
            if ! docker rename "$PREVIOUS_CADDY_CONTAINER" "$CADDY_CONTAINER" >/dev/null 2>&1; then
                echo "critical: failed to rename the previous Caddy container" >&2
                rollback_failed=1
            elif ! ensure_container_running "$CADDY_CONTAINER"; then
                echo "critical: failed to restart the previous Caddy container" >&2
                rollback_failed=1
            fi
        elif docker container inspect "$CADDY_CONTAINER" >/dev/null 2>&1; then
            if ! ensure_container_running "$CADDY_CONTAINER"; then
                echo "critical: failed to restart the Caddy container" >&2
                rollback_failed=1
            fi
        else
            echo "critical: no Caddy container is available for rollback" >&2
            rollback_failed=1
        fi
    else
        if docker container inspect "$CADDY_CONTAINER" >/dev/null 2>&1 && \
            ! docker rm -f "$CADDY_CONTAINER" >/dev/null 2>&1; then
            echo "critical: failed to remove the rejected Caddy container" >&2
            rollback_failed=1
        fi
    fi
    if [ "$HAD_APP" -eq 1 ] && ! wait_container_healthy "$CONTAINER" 36 5; then
        echo "critical: restored app container did not become healthy" >&2
        docker logs --tail 80 "$CONTAINER" >&2 2>/dev/null || true
        rollback_failed=1
    fi
    if [ "$HAD_CADDY" -eq 1 ]; then
        if [ "$HAD_APP" -eq 1 ]; then
            if ! wait_caddy_healthy; then
                echo "critical: restored Caddy did not pass the API health check" >&2
                docker logs --tail 80 "$CADDY_CONTAINER" >&2 2>/dev/null || true
                rollback_failed=1
            fi
        elif ! docker inspect --format '{{.State.Running}}' "$CADDY_CONTAINER" 2>/dev/null | grep -qx true; then
            echo "critical: restored Caddy is not running" >&2
            rollback_failed=1
        fi
    fi
    [ "$rollback_failed" -eq 0 ]
}

finish() {
    status=$?
    trap - EXIT HUP INT TERM
    rollback_status=0
    if [ "$DEPLOY_COMPLETE" -ne 1 ] && [ "$APP_SWITCHED" -eq 1 ]; then
        echo "deployment interrupted; restoring previous GPU Watch" >&2
        if ! rollback; then
            rollback_status=1
        fi
    fi
    cleanup_private_directory "$RUNTIME_SSH_STAGE" || rollback_status=1
    if [ "$rollback_status" -ne 0 ]; then
        echo "critical: GPU Watch rollback did not complete cleanly" >&2
        exit 74
    fi
    exit "$status"
}
trap finish EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM

# Image builds can take long enough for an operator or stray job to create a
# new file. Re-check the RW bind boundary before stopping the healthy app.
validate_application_data_directory
validate_private_directories

APP_SWITCHED=1
if [ "$HAD_APP" -eq 1 ]; then
    docker stop -t 15 "$CONTAINER" >/dev/null
    docker rename "$CONTAINER" "$PREVIOUS_CONTAINER"
    HAD_PREVIOUS=1
fi

create_predeploy_database_backup

switch_runtime_ssh

if [ "$HAD_DATABASE" -eq 1 ]; then
    if [ -z "$predeploy_backup" ] || [ ! -f "$predeploy_backup" ]; then
        echo "verified pre-deploy database backup is required before mutation" >&2
        exit 1
    fi
    DATABASE_RESTORE_REQUIRED=1
fi
python3 "$ROOT/scripts/sanitize-databases.py" "$APP_DATA_DIR/gpu_watch.sqlite3"
if [ "${GPU_WATCH_SANITIZE_HISTORICAL_BACKUPS:-0}" = "1" ]; then
    echo "explicitly sanitizing historical GPU Watch backups"
    python3 "$ROOT/scripts/sanitize-databases.py" "$APP_DATA_DIR/backups"
fi

docker run -d \
    --name "$CONTAINER" \
    --network "$NETWORK" \
    --init \
    --restart unless-stopped \
    --stop-timeout 15 \
    --cap-drop ALL \
    --security-opt no-new-privileges:true \
    --read-only \
    --ulimit core=0:0 \
    --tmpfs /tmp:rw,noexec,nosuid,size=64m \
    --memory=512m \
    --pids-limit=128 \
    --log-opt max-size=10m \
    --log-opt max-file=3 \
    -e TZ=Asia/Seoul \
    -e HOME=/home/gpuwatch \
    -e GPU_WATCH_ALLOWED_NETWORKS=127.0.0.0/8,::1/128,192.0.2.0/24 \
    -e "GPU_WATCH_ALLOWED_HOSTS=192.0.2.10:8787,192.0.2.50:8787,127.0.0.1:8787,localhost:8787,[::1]:8787" \
    -e GPU_WATCH_TRUSTED_PROXY_NETWORKS="$NETWORK_SUBNETS" \
    -e GPU_WATCH_RUNTIME_MODE=production \
    -e GPU_WATCH_SSH_TARGET_PREFIX=gpuwatch- \
    -e GPU_WATCH_SSH_CONFIG_FILE=/home/gpuwatch/.ssh/config \
    -e GPU_WATCH_SSH_IDENTITY_FILE=/home/gpuwatch/.ssh/gpu_watch_ed25519 \
    -v "$APP_DATA_DIR:/app/data" \
    -v "$SECRET_DIR:/app/secrets:ro" \
    -v "$RUNTIME_SSH_DIR:/home/gpuwatch/.ssh:ro" \
    --health-cmd="python3 /app/scripts/check_local.py --health-only" \
    --health-interval=20s \
    --health-timeout=10s \
    --health-retries=3 \
    --health-start-period=120s \
    "$IMAGE" >/dev/null

if ! wait_container_healthy "$CONTAINER" 36 5 "$BUILD_VERSION"; then
    docker logs --tail 80 "$CONTAINER" >&2 || true
    exit 1
fi

if [ "$HAD_CADDY" -eq 1 ]; then
    docker stop -t 10 "$CADDY_CONTAINER" >/dev/null
    docker rename "$CADDY_CONTAINER" "$PREVIOUS_CADDY_CONTAINER"
fi
docker run -d \
    --name "$CADDY_CONTAINER" \
    --network "$NETWORK" \
    --user "$APP_UID:$APP_GID" \
    --restart unless-stopped \
    --cap-drop ALL \
    --security-opt no-new-privileges:true \
    --read-only \
    --tmpfs /tmp:rw,noexec,nosuid,size=32m \
    --tmpfs "/data:rw,noexec,nosuid,size=16m,uid=$APP_UID,gid=$APP_GID,mode=0700" \
    --tmpfs "/config:rw,noexec,nosuid,size=16m,uid=$APP_UID,gid=$APP_GID,mode=0700" \
    --memory=192m \
    --pids-limit=64 \
    --log-opt max-size=10m \
    --log-opt max-file=3 \
    -p 8787:8787 \
    -v "$ROOT/Caddyfile:/etc/caddy/Caddyfile:ro" \
    "$CADDY_IMAGE" >/dev/null

if ! wait_caddy_healthy "$BUILD_VERSION"; then
    docker logs --tail 80 "$CADDY_CONTAINER" >&2 || true
    exit 1
fi
if [ "$ALLOW_LOW_DISK_DEPLOY" -eq 1 ]; then
    check_low_disk_only "$CONTAINER" "$BUILD_VERSION"
else
    check_container_health "$CONTAINER" "$BUILD_VERSION"
fi

DEPLOY_COMPLETE=1
if [ "$HAD_PREVIOUS" -eq 1 ]; then
    if ! docker rm "$PREVIOUS_CONTAINER" >/dev/null; then
        echo "warning: failed to remove previous app container" >&2
    fi
fi
if [ "$HAD_CADDY" -eq 1 ]; then
    if ! docker rm "$PREVIOUS_CADDY_CONTAINER" >/dev/null; then
        echo "warning: failed to remove previous Caddy container" >&2
    fi
fi
if ! cleanup_private_directory "$RUNTIME_SSH_PREVIOUS"; then
    echo "warning: failed to remove the previous runtime SSH directory" >&2
fi
cleanup_dangling_release_images
echo "GPU Watch deployed: $BUILD_VERSION"
echo "URL: http://192.0.2.10:8787/"
