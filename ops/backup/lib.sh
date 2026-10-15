#!/bin/bash
# ops/backup/lib.sh -- shared by backup.sh and restore-drill.sh. Sourced,
# never executed directly. Every function fails loud (set -euo pipefail
# in the caller) rather than swallowing an error -- a silent partial
# backup is worse than a loud failed one.

# All required env vars are checked once, at the top of whichever script
# sources this file, via require_env() below -- fail fast with a clear
# name, never a bash "unbound variable" trace halfway through a run.
require_env() {
    local missing=()
    for name in "$@"; do
        if [ -z "${!name:-}" ]; then
            missing+=("$name")
        fi
    done
    if [ "${#missing[@]}" -gt 0 ]; then
        echo "Missing required environment variable(s): ${missing[*]}" >&2
        exit 1
    fi
}

# Qdrant's own REST auth header (confirmed live against the pinned
# qdrant/qdrant:v1.19.1 image, same as app/qdrant.py's own client):
# "api-key", not "Authorization".
qdrant_curl() {
    curl -fsS -H "api-key: ${QDRANT_API_KEY}" "$@"
}

# Creates a synchronous snapshot of $QDRANT_COLLECTION_NAME (default
# "knowledge_chunks" -- NOTE: must track app/qdrant.py's own
# COLLECTION_NAME constant; this script does not import backend code,
# so the two cannot drift-check each other automatically) and echoes
# its snapshot name on stdout. wait=true (Qdrant's own default) means
# this blocks until the snapshot is actually complete on disk -- no
# polling loop needed.
qdrant_create_snapshot() {
    local collection="${QDRANT_COLLECTION_NAME:-knowledge_chunks}"
    qdrant_curl -X POST "${QDRANT_URL}/collections/${collection}/snapshots" \
        | jq -r '.result.name'
}

# Downloads a named snapshot to $2.
qdrant_download_snapshot() {
    local collection="${QDRANT_COLLECTION_NAME:-knowledge_chunks}"
    local snapshot_name="$1"
    local dest="$2"
    qdrant_curl -o "$dest" \
        "${QDRANT_URL}/collections/${collection}/snapshots/${snapshot_name}"
}

# Uploads a local snapshot file and recovers $QDRANT_COLLECTION_NAME from
# it on whichever Qdrant $QDRANT_URL points at -- the official multipart
# "snapshot upload" recovery endpoint (Qdrant's own documented mechanism,
# rule 11: no custom recovery logic). priority=snapshot: the snapshot's
# own data wins over whatever (nothing, on a fresh scratch instance)
# already exists under that collection name.
qdrant_recover_from_snapshot() {
    local collection="${QDRANT_COLLECTION_NAME:-knowledge_chunks}"
    local snapshot_path="$1"
    qdrant_curl -X POST \
        "${QDRANT_URL}/collections/${collection}/snapshots/upload?priority=snapshot" \
        -F "snapshot=@${snapshot_path}" >/dev/null
}

# age-encrypts $1 to $2 using the public recipient key only -- the
# encrypting side never needs (and never sees) the private key, which is
# the whole point of asymmetric encryption here: BACKUP_AGE_PUBLIC_KEY
# can live in the same .env as every other non-secret config value
# (it is not a secret -- knowing it lets you encrypt TO the backup,
# never read it), while the matching private key
# (BACKUP_AGE_SECRET_KEY) is required only to decrypt, and is never
# read, used, or even present during a normal nightly backup run.
age_encrypt() {
    age -r "${BACKUP_AGE_PUBLIC_KEY}" -o "$2" "$1"
}

# age-decrypts $1 to $2 using the private key -- used only by
# restore-drill.sh / a real restore, never by backup.sh.
age_decrypt() {
    age -d -i <(printf '%s' "${BACKUP_AGE_SECRET_KEY}") -o "$2" "$1"
}

s3_upload() {
    aws --endpoint-url "${S3_BACKUP_ENDPOINT_URL}" s3 cp "$1" "s3://${S3_BACKUP_BUCKET}/$2"
}

s3_download() {
    aws --endpoint-url "${S3_BACKUP_ENDPOINT_URL}" s3 cp "s3://${S3_BACKUP_BUCKET}/$1" "$2"
}

# Deletes every object under $1 (a key prefix, e.g. "pg/") whose own
# embedded timestamp is older than $S3_BACKUP_RETENTION_DAYS. Keys are
# named "<prefix><UTC ISO8601 'basic' timestamp>.<ext>" by backup.sh
# below (e.g. "pg/20261015T030000Z.sql.gz.age") specifically so the
# timestamp can be parsed back out of the key name alone -- no object
# metadata lookup needed, one `aws s3 ls` call lists everything this
# needs.
s3_apply_retention() {
    local prefix="$1"
    local cutoff_epoch
    cutoff_epoch=$(date -u -d "-${S3_BACKUP_RETENTION_DAYS:-14} days" +%s)
    aws --endpoint-url "${S3_BACKUP_ENDPOINT_URL}" s3 ls "s3://${S3_BACKUP_BUCKET}/${prefix}" \
        | awk '{print $4}' | while read -r key; do
        [ -n "$key" ] || continue
        local stamp
        stamp=$(echo "$key" | grep -oE '[0-9]{8}T[0-9]{6}Z')
        [ -n "$stamp" ] || { echo "Skipping retention for unrecognised key: $key" >&2; continue; }
        local key_epoch
        key_epoch=$(date -u -d "${stamp:0:8} ${stamp:9:2}:${stamp:11:2}:${stamp:13:2}" +%s)
        if [ "$key_epoch" -lt "$cutoff_epoch" ]; then
            echo "Retention: deleting s3://${S3_BACKUP_BUCKET}/${prefix}${key} (older than ${S3_BACKUP_RETENTION_DAYS:-14} days)"
            aws --endpoint-url "${S3_BACKUP_ENDPOINT_URL}" s3 rm "s3://${S3_BACKUP_BUCKET}/${prefix}${key}"
        fi
    done
}
