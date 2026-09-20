# Shared helpers for backup.sh / restore.sh. Not meant to be run directly.

set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_DIR"

SERVICE="filaman"
VOLUME_KEY="filaman-data"

# Host path for `docker -v`. Git Bash on Windows rewrites POSIX paths, so use the
# native Windows form there and disable MSYS path conversion for docker calls.
host_path() {
    if command -v cygpath >/dev/null 2>&1; then
        cygpath -w "$1"
    else
        printf '%s' "$1"
    fi
}
export MSYS_NO_PATHCONV=1

# Real volume name (compose prefixes it with the project name, e.g. filaman_filaman-data).
resolve_volume() {
    local name
    name="$(docker volume ls -q --filter "label=com.docker.compose.volume=${VOLUME_KEY}" | head -n1)"
    if [ -z "$name" ]; then
        echo "Volume for '${VOLUME_KEY}' not found. Run 'docker compose create' first." >&2
        exit 1
    fi
    printf '%s' "$name"
}

container_running() {
    [ -n "$(docker compose ps -q --status running "$SERVICE" 2>/dev/null)" ]
}
