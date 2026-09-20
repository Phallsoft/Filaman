#!/usr/bin/env bash
# Back up the filaman data volume (SQLite DB + spool images) to a tarball.
#
# Usage: scripts/backup.sh [output.tgz]
#   Default output: backups/filaman-data-YYYYmmdd-HHMMSS.tgz
#
# The container is stopped for the duration of the copy so the SQLite file is
# consistent, then restarted if it was running.

source "$(dirname "${BASH_SOURCE[0]}")/_common.sh"

OUT="${1:-backups/filaman-data-$(date +%Y%m%d-%H%M%S).tgz}"
mkdir -p "$(dirname "$OUT")"
OUT_DIR="$(cd "$(dirname "$OUT")" && pwd)"
OUT_FILE="$(basename "$OUT")"

VOLUME="$(resolve_volume)"

WAS_RUNNING=0
if container_running; then
    WAS_RUNNING=1
    echo "Stopping ${SERVICE}..."
    docker compose stop "$SERVICE"
fi

echo "Backing up volume ${VOLUME} -> ${OUT}"
docker run --rm \
    -v "${VOLUME}:/data:ro" \
    -v "$(host_path "$OUT_DIR"):/backup" \
    alpine tar czf "/backup/${OUT_FILE}" -C /data .

if [ "$WAS_RUNNING" = 1 ]; then
    echo "Starting ${SERVICE}..."
    docker compose start "$SERVICE"
fi

echo "Done: $(du -h "$OUT" | cut -f1) written to ${OUT}"
