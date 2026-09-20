#!/usr/bin/env bash
# Restore the filaman data volume from a tarball made by backup.sh.
#
# Usage: scripts/restore.sh <backup.tgz>
#
# WARNING: replaces everything currently in the data volume.
# Creates the volume via compose if it doesn't exist yet, so this works on a
# fresh server before the first `docker compose up`.

source "$(dirname "${BASH_SOURCE[0]}")/_common.sh"

if [ $# -lt 1 ] || [ ! -f "$1" ]; then
    echo "Usage: scripts/restore.sh <backup.tgz>" >&2
    exit 1
fi
IN_DIR="$(cd "$(dirname "$1")" && pwd)"
IN_FILE="$(basename "$1")"

# Ensure the volume exists with compose's naming/labels.
docker compose create "$SERVICE" >/dev/null
VOLUME="$(resolve_volume)"

WAS_RUNNING=0
if container_running; then
    WAS_RUNNING=1
    echo "Stopping ${SERVICE}..."
    docker compose stop "$SERVICE"
fi

echo "Restoring ${1} -> volume ${VOLUME}"
# App runs as uid 1000 (see Dockerfile), so files must be owned by it.
docker run --rm \
    -v "${VOLUME}:/data" \
    -v "$(host_path "$IN_DIR"):/backup:ro" \
    alpine sh -c "rm -rf /data/* /data/..?* /data/.[!.]* 2>/dev/null; tar xzf '/backup/${IN_FILE}' -C /data && chown -R 1000:1000 /data"

if [ "$WAS_RUNNING" = 1 ]; then
    echo "Starting ${SERVICE}..."
    docker compose start "$SERVICE"
else
    echo "Restored. Start the app with: docker compose up -d"
fi
