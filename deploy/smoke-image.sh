#!/bin/sh
# deploy/smoke-image.sh: Task 1.1.m post-build smoke checks for the
# widgetplatform-backend image (backend/Dockerfile, Task 1.1.g). Each check
# prints one PASS/FAIL line; the first FAIL exits non-zero immediately. The
# trap below always removes every container and temp file this script
# started, even on failure or an early exit.
set -eu

image="${1:?usage: smoke-image.sh <image>}"
containers=""
tmp_headers=$(mktemp)
tmp_body=$(mktemp)

cleanup() {
    for c in $containers; do
        docker rm -f "$c" >/dev/null 2>&1 || true
    done
    rm -f "$tmp_headers" "$tmp_body"
}
trap cleanup EXIT

fail() {
    echo "FAIL: $1" >&2
    exit 1
}

# Shared by the api and worker checks below; word-splitting each
# "-e NAME=value" token is safe since none contain whitespace.
APP_ENV_ARGS="-e APP_ENV=development -e DATABASE_URL=postgresql+psycopg://widgetplatform:change-me@postgres:5432/widgetplatform -e QDRANT_URL=http://qdrant:6333 -e API_HOST=0.0.0.0 -e API_PORT=8000"

stop_and_time() {
    # $1 = container id; fails unless docker stop exits 0 within 10s.
    start=$(date +%s)
    set +e
    docker stop "$1" >/dev/null
    rc=$?
    set -e
    elapsed=$(($(date +%s) - start))
    [ "$rc" -eq 0 ] || fail "docker stop $1 exited $rc"
    [ "$elapsed" -lt 10 ] || fail "docker stop $1 took ${elapsed}s (>= 10s)"
}

# (a) non-root numeric uid
uid=$(docker run --rm --entrypoint id "$image" -u)
[ "$uid" = "10001" ] || fail "container uid is $uid, expected 10001"
echo "PASS: runs as uid 10001"

# (b) the app venv has no pip module (1.1.g removes it from /venv).
# NOTE: the base image's own system Python separately ships its own pip,
# still on PATH -- out of scope for this check (see PROJECT_SPEC.md open
# markers, owner 1.1.o/Phase 7).
if docker run --rm --entrypoint sh "$image" -c 'python -m pip --version' >/dev/null 2>&1; then
    fail "pip is present in the app venv"
fi
echo "PASS: pip not present in the app venv"

# (c) a bogus mode is rejected with the usage line and exit code 2
set +e
out=$(docker run --rm "$image" bogus 2>&1)
rc=$?
set -e
[ "$rc" -eq 2 ] || fail "bogus mode exited $rc, expected 2"
case "$out" in
    *"Usage: entrypoint.sh {api|worker|migrate}"*) : ;;
    *) fail "bogus mode did not print the usage line: $out" ;;
esac
echo "PASS: bogus mode exits 2 with the usage line"

# (d) api mode: /health is reachable, correct body, no server header, stops fast
cid_api=$(docker run --rm -d $APP_ENV_ARGS -p 127.0.0.1:18080:8000 "$image" api)
containers="$containers $cid_api"
i=0
until curl -fsS -D "$tmp_headers" -o "$tmp_body" http://127.0.0.1:18080/health >/dev/null 2>&1; do
    i=$((i + 1))
    [ "$i" -lt 30 ] || fail "GET /health did not respond within 30s"
    sleep 1
done
[ "$(cat "$tmp_body")" = '{"status":"ok"}' ] || fail "unexpected /health body: $(cat "$tmp_body")"
grep -qi '^server:' "$tmp_headers" && fail "/health response has a server: header"
echo "PASS: GET /health returns 200, the expected body, and no server header"
stop_and_time "$cid_api"
echo "PASS: api container stops within 10s (exit 0)"

# (e) worker mode: logs the startup line, stops fast
cid_worker=$(docker run --rm -d $APP_ENV_ARGS "$image" worker)
containers="$containers $cid_worker"
i=0
until docker logs "$cid_worker" 2>&1 | grep -q "worker starting: app_env=development"; do
    i=$((i + 1))
    [ "$i" -lt 30 ] || fail "worker did not log its startup line within 30s"
    sleep 1
done
echo "PASS: worker logs its startup line"
stop_and_time "$cid_worker"
echo "PASS: worker container stops within 10s (exit 0)"

# (f) migrate mode against an unreachable host fails without leaking the password.
# invalid.invalid (RFC 2606) never resolves, so this fails fast on DNS, not a
# TCP-connect timeout; PGCONNECT_TIMEOUT bounds the worst case regardless.
set +e
out=$(docker run --rm -e APP_ENV=development -e PGCONNECT_TIMEOUT=5 \
    -e DATABASE_URL=postgresql+psycopg://widgetplatform:change-me@invalid.invalid:5432/widgetplatform \
    -e QDRANT_URL=http://qdrant:6333 -e API_HOST=0.0.0.0 -e API_PORT=8000 \
    "$image" migrate 2>&1)
rc=$?
set -e
[ "$rc" -ne 0 ] || fail "migrate against an unreachable host exited 0"
case "$out" in
    *change-me*) fail "migrate output leaked the database password" ;;
esac
echo "PASS: migrate against an unreachable host fails without leaking the password"
