#!/bin/sh
# Task 1.1.g: single entrypoint for both container modes ("api" and
# "worker"). Always exec so the mode's process becomes PID 1 and receives
# SIGTERM directly (no shell left in between to swallow the signal).
set -eu

mode="${1:-}"

case "$mode" in
    api)
        if [ -z "${API_HOST:-}" ]; then
            echo "entrypoint.sh: API_HOST is required for mode 'api'" >&2
            exit 2
        fi
        if [ -z "${API_PORT:-}" ]; then
            echo "entrypoint.sh: API_PORT is required for mode 'api'" >&2
            exit 2
        fi
        exec uvicorn app.main:create_app --factory --host "$API_HOST" --port "$API_PORT" --no-server-header
        ;;
    worker)
        exec python -m app.worker
        ;;
    *)
        echo "Usage: entrypoint.sh {api|worker}" >&2
        exit 2
        ;;
esac
