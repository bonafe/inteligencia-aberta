#!/bin/sh
set -e
mkdir -p "${DATA_DIR:-/data}"
chown -R node:node "${DATA_DIR:-/data}" 2>/dev/null || echo "[agora-sync] warning: could not make ${DATA_DIR:-/data} writable by the node user" >&2
exec su-exec node:node "$@"
