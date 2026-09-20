#!/bin/sh
# Task 1.1.g: single entrypoint for the container's modes ("api", "worker"
# and, since Task 1.1.h, "migrate"). Always exec so the mode's process
# becomes PID 1 and receives SIGTERM directly (no shell left in between to
# swallow the signal).
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
    migrate)
        # No API_HOST/API_PORT check here: Settings validates DATABASE_URL
        # (and every other required variable) itself when alembic/env.py
        # calls get_settings().
        exec alembic upgrade head
        ;;
    *)
        echo "Usage: entrypoint.sh {api|worker|migrate}" >&2
        exit 2
        ;;
esac
