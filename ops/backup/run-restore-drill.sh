#!/bin/bash
# ops/backup/run-restore-drill.sh -- Step 7.3 prep (ops track). The host-
# level orchestrator: brings up the scratch stack, runs restore-drill.sh
# INSIDE the samoflow-backup:dev image against it, then tears the scratch
# stack down unconditionally. The only host-side requirements are Docker
# and the Compose plugin -- every real tool the drill itself needs
# (psql, age, the AWS CLI, curl, jq) lives inside the image, never on the
# host, so a fresh deploy host needs nothing installed beyond Docker to
# run this.
#
#   BACKUP_AGE_SECRET_KEY=... DB_CONNECTION_ENCRYPTION_KEY=... \
#     ./ops/backup/run-restore-drill.sh
#
# BACKUP_AGE_SECRET_KEY/DB_CONNECTION_ENCRYPTION_KEY are read from the
# CALLER's environment (docs/deploy/RUNBOOK.md's own separate secrets
# file), never from .env -- every other variable below (S3_BACKUP_*,
# AWS_*, QDRANT_COLLECTION_NAME) comes from the ordinary .env, same as
# every other Compose service already reads it.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"

project="${DRILL_COMPOSE_PROJECT:-samoflow-ops-drill}"
compose=(docker compose -p "$project" --env-file "${ENV_FILE:-../../.env}" -f docker-compose.yml --profile drill)

cleanup() {
    echo "-- tearing down the scratch stack ($project) --"
    "${compose[@]}" down -v --remove-orphans
}
trap cleanup EXIT

echo "-- starting the scratch stack ($project) --"
"${compose[@]}" up -d --wait postgres-scratch qdrant-scratch

echo "-- running the restore drill --"
"${compose[@]}" run --rm \
    -e BACKUP_AGE_SECRET_KEY="${BACKUP_AGE_SECRET_KEY:?BACKUP_AGE_SECRET_KEY must be set (from the separate secrets file, never .env)}" \
    -e DB_CONNECTION_ENCRYPTION_KEY="${DB_CONNECTION_ENCRYPTION_KEY:?DB_CONNECTION_ENCRYPTION_KEY must be set (from the separate secrets file, never .env)}" \
    restore-drill
