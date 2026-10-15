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
        #
        # Task 3.2.b: run_checkpointer_setup() (app/agent/checkpointer.py)
        # runs as a third idempotent setup action, same reasoning as the
        # Qdrant one immediately below -- the checkpoint tables are a
        # one-shot Postgres setup concern too, just not something alembic's
        # own migrations manage (AsyncPostgresSaver.setup() owns its own
        # schema/versioning, confirmed live at Task 3.2.a). Also not exec'd,
        # same reason as alembic above.
        alembic upgrade head
        python -m app.agent.checkpointer
        exec python -m app.qdrant
        ;;
    seed-owner)
        # Step 6.1: one-off operator command, never part of `up` --
        #   docker compose --env-file .env -f deploy/docker-compose.yml \
        #     run --rm migrate seed-owner --email ... --tenant-name "..."
        # The password is prompted interactively (app/dashboard/
        # seed_cli.py's own getpass.getpass() call), never a CLI argument
        # -- "$@" below carries only --email/--tenant-name/--platform-admin,
        # never a password.
        shift
        exec python -m app.dashboard.seed_cli "$@"
        ;;
    admin)
        # Step 6.1: the platform-admin CLI (app/dashboard/admin_cli.py) --
        # create-tenant, assign-plan, issue-site-key, revoke-site-key,
        # revoke-domain, seed-plans. Same one-off `docker compose run`
        # shape as seed-owner above, reusing the identical `migrate`
        # service wiring.
        shift
        exec python -m app.dashboard.admin_cli "$@"
        ;;
    *)
        echo "Usage: entrypoint.sh {api|worker|migrate|seed-owner|admin}" >&2
        exit 2
        ;;
esac
