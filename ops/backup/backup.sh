#!/bin/bash
# ops/backup/backup.sh -- Step 7.3 prep (ops track). Nightly backup: a
# Postgres pg_dump plus a Qdrant snapshot, both age-encrypted before
# upload, both landing in the SAME S3-compatible bucket (S3_BACKUP_
# BUCKET) -- the age PRIVATE key never appears anywhere in this script
# (only BACKUP_AGE_PUBLIC_KEY, the encrypting side), so even a fully
# compromised backup bucket's own credentials cannot decrypt anything it
# stores. Run via cron, inside this directory's own Docker image
# (ops/backup/Dockerfile) on the Compose network, e.g.:
#   docker compose -f deploy/docker-compose.yml -f ops/backup/docker-compose.yml \
#     run --rm backup ./backup.sh
# (docs/deploy/RUNBOOK.md's own crontab line.)
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
# shellcheck source=lib.sh
source ./lib.sh

require_env \
    PGHOST PGUSER PGPASSWORD PGDATABASE \
    QDRANT_URL QDRANT_API_KEY \
    BACKUP_AGE_PUBLIC_KEY \
    S3_BACKUP_BUCKET S3_BACKUP_ENDPOINT_URL AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY

timestamp=$(date -u +%Y%m%dT%H%M%SZ)
workdir=$(mktemp -d)
trap 'rm -rf "$workdir"' EXIT

echo "== backup $timestamp starting =="

echo "-- pg_dump --"
# --format=plain (not custom): the restore drill's own proof (decrypt a
# db_connections row) only needs `psql < dump.sql` to work on any
# Postgres with pgcrypto available, no pg_restore version-matching
# concerns to manage on top of the pg_dump-vs-server version pin this
# image's own header comment already states.
pg_dump --no-owner --no-privileges > "$workdir/pg.sql"
gzip "$workdir/pg.sql"
age_encrypt "$workdir/pg.sql.gz" "$workdir/pg.sql.gz.age"
s3_upload "$workdir/pg.sql.gz.age" "pg/${timestamp}.sql.gz.age"
echo "pg_dump uploaded: pg/${timestamp}.sql.gz.age"

echo "-- qdrant snapshot --"
snapshot_name=$(qdrant_create_snapshot)
qdrant_download_snapshot "$snapshot_name" "$workdir/qdrant.snapshot"
age_encrypt "$workdir/qdrant.snapshot" "$workdir/qdrant.snapshot.age"
s3_upload "$workdir/qdrant.snapshot.age" "qdrant/${timestamp}.snapshot.age"
echo "qdrant snapshot uploaded: qdrant/${timestamp}.snapshot.age"

echo "-- retention (${S3_BACKUP_RETENTION_DAYS:-14} days) --"
s3_apply_retention "pg/"
s3_apply_retention "qdrant/"

echo "== backup $timestamp complete =="
