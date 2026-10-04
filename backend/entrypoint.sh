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
        #
        # Task 2.1.g: ensure_collection() (app/qdrant.py) runs right after
        # the schema migration, as this same deploy step's second idempotent
        # setup action -- Postgres's schema and Qdrant's collection are both
        # one-shot setup concerns, not the worker's own long-running loop
        # (PROJECT_SPEC.md's own decision). alembic is not exec'd here (only
        # the last command in this script should be, so it becomes PID 1):
        # `set -eu` above already aborts this script with alembic's own
        # exit code if it fails, before "python -m app.qdrant" ever runs.
        alembic upgrade head
        exec python -m app.qdrant
        ;;
    *)
        echo "Usage: entrypoint.sh {api|worker|migrate}" >&2
        exit 2
        ;;
esac
