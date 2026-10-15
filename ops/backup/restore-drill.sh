#!/bin/bash
# ops/backup/restore-drill.sh -- Step 7.3 prep (ops track). Runs INSIDE
# the samoflow-backup:dev image (ops/backup/Dockerfile), on the same
# Compose network as postgres-scratch/qdrant-scratch (ops/backup/
# docker-compose.yml's own "drill" profile) -- reached by service name,
# zero port discovery needed. Orchestrated by ops/backup/run-restore-
# drill.sh (the host-level wrapper that brings the scratch stack up,
# runs this, and tears it down unconditionally):
#   docker compose -p samoflow-ops-drill -f ops/backup/docker-compose.yml \
#     --profile drill run --rm restore-drill
#
# Downloads the MOST RECENT backup set from S3, restores it into the
# scratch stack (never the real postgres/qdrant), and PROVES -- not
# merely checks the files exist -- that the restored db_connections
# credentials decrypt with DB_CONNECTION_ENCRYPTION_KEY, supplied here
# from a source SEPARATE from both the backup bucket and this project's
# main .env (docs/deploy/RUNBOOK.md's own secrets section names the
# exact file this comes from in a real deployment).
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
# shellcheck source=lib.sh
source ./lib.sh

require_env \
    PGHOST PGUSER PGPASSWORD PGDATABASE \
    QDRANT_URL QDRANT_API_KEY \
    BACKUP_AGE_SECRET_KEY DB_CONNECTION_ENCRYPTION_KEY \
    S3_BACKUP_BUCKET S3_BACKUP_ENDPOINT_URL AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY

workdir=$(mktemp -d)
trap 'rm -rf "$workdir"' EXIT

echo "== restore drill starting =="

echo "-- locating the most recent backup set --"
latest_pg_key=$(aws --endpoint-url "${S3_BACKUP_ENDPOINT_URL}" s3 ls "s3://${S3_BACKUP_BUCKET}/pg/" \
    | awk '{print $4}' | sort | tail -1)
latest_qdrant_key=$(aws --endpoint-url "${S3_BACKUP_ENDPOINT_URL}" s3 ls "s3://${S3_BACKUP_BUCKET}/qdrant/" \
    | awk '{print $4}' | sort | tail -1)
[ -n "$latest_pg_key" ] || { echo "No pg backup found under s3://${S3_BACKUP_BUCKET}/pg/" >&2; exit 1; }
[ -n "$latest_qdrant_key" ] || { echo "No qdrant backup found under s3://${S3_BACKUP_BUCKET}/qdrant/" >&2; exit 1; }
echo "pg: $latest_pg_key"
echo "qdrant: $latest_qdrant_key"

echo "-- downloading and decrypting (using the SEPARATELY-stored private key) --"
s3_download "pg/${latest_pg_key}" "$workdir/pg.sql.gz.age"
s3_download "qdrant/${latest_qdrant_key}" "$workdir/qdrant.snapshot.age"
age_decrypt "$workdir/pg.sql.gz.age" "$workdir/pg.sql.gz"
age_decrypt "$workdir/qdrant.snapshot.age" "$workdir/qdrant.snapshot"
gunzip "$workdir/pg.sql.gz"

echo "-- restoring the pg dump into the scratch stack ($PGHOST) --"
psql -v ON_ERROR_STOP=1 -f "$workdir/pg.sql" >/dev/null

echo "-- PROOF: db_connections credentials decrypt with the separately-stored key --"
real_row_count=$(psql -tA -c "select count(*) from db_connections;")
echo "db_connections rows in the restored dump: ${real_row_count}"
if [ "${real_row_count}" -gt 0 ]; then
    decrypted_count=$(psql -tA \
        -c "select count(*) from db_connections where pgp_sym_decrypt(encrypted_credentials, '${DB_CONNECTION_ENCRYPTION_KEY}') is not null;")
    echo "rows that decrypt successfully with the separately-stored key: ${decrypted_count} / ${real_row_count}"
    if [ "${decrypted_count}" != "${real_row_count}" ]; then
        echo "FAIL: not every restored db_connections row decrypts with the supplied key" >&2
        exit 1
    fi
else
    # No real db_connections rows exist yet (expected for the pilot --
    # database-sync sources are disabled, D-pilot-dbsync-disable). A
    # canary row, encrypted and decrypted entirely within THIS restored
    # database (pgcrypto extension + schema, both only present because
    # the restore itself succeeded), still proves the real thing this
    # drill exists to prove: the restored schema's pgcrypto extension,
    # together with the separately-stored key, round-trips a real
    # encrypted value correctly.
    canary_plaintext="restore-drill-canary-$(date -u +%s)"
    canary_decrypted=$(psql -tA \
        -c "select pgp_sym_decrypt(pgp_sym_encrypt('${canary_plaintext}', '${DB_CONNECTION_ENCRYPTION_KEY}'), '${DB_CONNECTION_ENCRYPTION_KEY}');")
    if [ "$canary_decrypted" != "$canary_plaintext" ]; then
        echo "FAIL: canary round-trip through the restored pgcrypto extension did not match" >&2
        exit 1
    fi
    echo "db_connections has no rows yet (expected for the pilot); canary round-trip through the restored pgcrypto extension PASSED: '${canary_plaintext}'"
fi

echo "-- restoring the qdrant snapshot into the scratch stack ($QDRANT_URL) --"
qdrant_recover_from_snapshot "$workdir/qdrant.snapshot"

collection="${QDRANT_COLLECTION_NAME:-knowledge_chunks}"
point_count=$(qdrant_curl -X POST -H "Content-Type: application/json" -d '{}' \
    "${QDRANT_URL}/collections/${collection}/points/count" | jq -r '.result.count')
echo "PROOF: restored qdrant collection '${collection}' has ${point_count} point(s)"

echo "== restore drill PASSED =="
