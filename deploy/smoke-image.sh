#!/bin/sh
# deploy/smoke-image.sh: Task 1.1.m post-build smoke checks for the
# widgetplatform-backend image (backend/Dockerfile, Task 1.1.g). Each check
# prints one PASS/FAIL line; the first FAIL exits non-zero immediately. The
# trap below always removes every container and temp file this script
# started, even on failure or an early exit.
#
# Extended for the config-path bug fix (2026-10-16, checks (g)/(h) below):
# resolved relative to this script's own location (not the caller's cwd)
# so it keeps working whether invoked from the repo root (CI's own usage)
# or anywhere else.
set -eu

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

image="${1:?usage: smoke-image.sh <image>}"
containers=""
tmp_headers=$(mktemp)
tmp_body=$(mktemp)

cleanup() {
    for c in $containers; do
        docker rm -f "$c" >/dev/null 2>&1 || true
    done
    # --profile test down -v: always attempted, even if checks (g)/(h)
    # below never started these services (e.g. an earlier check already
    # failed) -- `docker compose down` on services that were never
    # created is a no-op, not an error, matching this trap's own existing
    # "always safe to call" style for container removal above.
    (cd "$REPO_ROOT" && docker compose --env-file .env -f deploy/docker-compose.yml \
        --profile test down -v >/dev/null 2>&1) || true
    rm -f "$tmp_headers" "$tmp_body"
}
trap cleanup EXIT

fail() {
    echo "FAIL: $1" >&2
    exit 1
}

# Shared by the api and worker checks below; word-splitting each
# "-e NAME=value" token is safe since none contain whitespace.
APP_ENV_ARGS="-e APP_ENV=development -e DATABASE_URL=postgresql+psycopg://widgetplatform:change-me@postgres:5432/widgetplatform -e QDRANT_URL=http://qdrant:6333 -e QDRANT_API_KEY=smoke-test-key -e QDRANT_READ_ONLY_API_KEY=smoke-test-readonly-key -e JWT_SIGNING_KEY=smoke-test-jwt-signing-key-32-chars-ok -e DB_CONNECTION_ENCRYPTION_KEY=smoke-test-db-connection-key-32-chars-ok -e OPENAI_API_KEY=smoke-test-openai-key -e API_HOST=0.0.0.0 -e API_PORT=8000"

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

# (b) neither Python has a working pip: the app venv's own pip is removed
# in the builder stage (1.1.g), and the base image's system Python's pip
# (installed via ensurepip at image-build time -- not a dpkg package, so
# there is no "apt remove" for it) is removed in the final stage (1.1.o.d).
# "python" resolves to the venv (PATH="/venv/bin:$PATH"); the system
# interpreter is checked by its absolute path so it can't be shadowed.
if docker run --rm --entrypoint sh "$image" -c 'python -m pip --version' >/dev/null 2>&1; then
    fail "pip is present in the app venv"
fi
if docker run --rm --entrypoint sh "$image" -c '/usr/local/bin/python3 -m pip --version' >/dev/null 2>&1; then
    fail "pip is present in the system Python"
fi
echo "PASS: pip not present in the app venv or the system Python"

# (c) a bogus mode is rejected with the usage line and exit code 2
set +e
out=$(docker run --rm "$image" bogus 2>&1)
rc=$?
set -e
[ "$rc" -eq 2 ] || fail "bogus mode exited $rc, expected 2"
case "$out" in
    *"Usage: entrypoint.sh {api|worker|migrate|seed-owner|admin}"*) : ;;
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
# Built from APP_ENV_ARGS (same base env as checks d/e) with DATABASE_URL
# overridden to the unreachable host and PGCONNECT_TIMEOUT added -- repeated
# -e flags for the same name are last-wins, so the later DATABASE_URL wins.
set +e
out=$(docker run --rm $APP_ENV_ARGS -e PGCONNECT_TIMEOUT=5 \
    -e DATABASE_URL=postgresql+psycopg://widgetplatform:change-me@invalid.invalid:5432/widgetplatform \
    "$image" migrate 2>&1)
rc=$?
set -e
[ "$rc" -ne 0 ] || fail "migrate against an unreachable host exited 0"
case "$out" in
    *change-me*) fail "migrate output leaked the database password" ;;
esac
echo "PASS: migrate against an unreachable host fails without leaking the password"

# (g) the config-path bug fix (2026-10-16): the built image's own venv is
# a real NON-EDITABLE install (site-packages, exactly like the Dockerfile
# builder stage's `pip install --no-deps .`) -- the one scenario the
# original bug only showed up in (Path(__file__)-relative arithmetic
# resolves correctly from a source checkout, which is all backend/tests
# ever exercised, but lands on site-packages itself once installed for
# real). No database/Qdrant needed: these three modules do nothing but
# parse yaml and validate a schema. See backend/app/agent/greeting_
# phrases.py's own header comment for the full reasoning.
set +e
out=$(docker run --rm --entrypoint python3 "$image" -c "
from app.agent.escalation_phrases import escalation_phrase_category
from app.agent.greeting_phrases import is_greeting_or_thanks
from app.agent.rewrite_trigger import needs_join
assert escalation_phrase_category('I want to speak to a human please') == 'asks_for_person'
assert is_greeting_or_thanks('hi') is True
assert needs_join('what about it') is True
print('OK')
" 2>&1)
rc=$?
set -e
[ "$rc" -eq 0 ] && [ "$out" = "OK" ] || fail "built image failed to load escalation/greeting/rewrite-trigger phrase data: $out"
echo "PASS: built image loads escalation/greeting/rewrite-trigger phrase data via importlib.resources"

# (h) a REAL greeting turn through POST /chat, against a disposable
# postgres+qdrant and this script's own migrate/admin/api invocations of
# the SAME image under test, returns the fixed Guardrail reply ("Hello!
# How can I help you today?", backend/app/agent/graph.py's own
# _GUARDRAIL_REPLY_TEXT) -- not an error. This is the end-to-end proof
# the config-path bug is actually fixed inside the real built image, not
# only unit-tested: before the fix, is_greeting_or_thanks() raised
# FileNotFoundError deep inside the graph, which create_chat_turn()'s
# own generic exception handler converted into a generic "error" SSE
# event instead of the greeting reply -- every chat turn in the real
# container was affected (every message passes through this same
# detection first), not only greetings; a greeting is simply the
# cheapest one to prove end to end, needing no real OPENAI_API_KEY.
#
# test-db/test-qdrant (deploy/docker-compose.yml's own disposable
# `--profile test` services, the same ones `make test-all` uses) stand
# in for postgres/qdrant here -- resolved relative to REPO_ROOT, not the
# caller's cwd, matching this script's own SCRIPT_DIR/REPO_ROOT
# resolution above.
echo "--- (h) starting a disposable postgres+qdrant for a real chat-turn check ---"
(cd "$REPO_ROOT" && docker compose --env-file .env -f deploy/docker-compose.yml \
    --profile test up -d test-db test-qdrant)

i=0
until (cd "$REPO_ROOT" && docker compose --env-file .env -f deploy/docker-compose.yml \
    --profile test exec -T test-db pg_isready -U widgetplatform -d widgetplatform_test) \
    >/dev/null 2>&1; do
    i=$((i + 1))
    [ "$i" -lt 30 ] || fail "test-db did not become ready within 30s"
    sleep 1
done
test_db_cid=$(cd "$REPO_ROOT" && docker compose --env-file .env -f deploy/docker-compose.yml \
    --profile test ps -q test-db)
test_qdrant_cid=$(cd "$REPO_ROOT" && docker compose --env-file .env -f deploy/docker-compose.yml \
    --profile test ps -q test-qdrant)
i=0
until [ "$(docker inspect -f '{{.State.Health.Status}}' "$test_qdrant_cid")" = "healthy" ]; do
    i=$((i + 1))
    [ "$i" -lt 30 ] || fail "test-qdrant did not become healthy within 30s"
    sleep 1
done

# The real Compose network name, read from the real test-db container --
# not hard-coded (deploy/docker-compose.yml's own top-level `name:` sets
# it today, but re-deriving it this way keeps this check correct even if
# that ever changes) -- so the migrate/admin/api containers below can
# reach test-db/test-qdrant by their Compose service name.
NETWORK=$(docker inspect "$test_db_cid" --format '{{range $k, $v := .NetworkSettings.Networks}}{{$k}}{{end}}')
[ -n "$NETWORK" ] || fail "could not determine the compose network test-db is attached to"

STACK_ENV="-e APP_ENV=development -e DATABASE_URL=postgresql+psycopg://widgetplatform:change-me@test-db:5432/widgetplatform_test -e QDRANT_URL=http://test-qdrant:6333 -e QDRANT_API_KEY=test-qdrant-key -e QDRANT_READ_ONLY_API_KEY=test-qdrant-readonly-key -e JWT_SIGNING_KEY=smoke-test-jwt-signing-key-32-chars-ok -e DB_CONNECTION_ENCRYPTION_KEY=smoke-test-db-connection-key-32-chars-ok -e OPENAI_API_KEY=smoke-test-openai-key -e API_HOST=0.0.0.0 -e API_PORT=8000"

set +e
out=$(docker run --rm --network "$NETWORK" $STACK_ENV "$image" migrate 2>&1)
rc=$?
set -e
[ "$rc" -eq 0 ] || fail "migrate failed against the disposable test-db/test-qdrant stack: $out"

set +e
tenant_out=$(docker run --rm --network "$NETWORK" $STACK_ENV "$image" admin create-tenant --name "Smoke Test" 2>&1)
rc=$?
set -e
[ "$rc" -eq 0 ] || fail "admin create-tenant failed: $tenant_out"
tenant_id=$(printf '%s' "$tenant_out" | sed -n 's/.*(\(.*\))$/\1/p')
[ -n "$tenant_id" ] || fail "could not parse a tenant id from: $tenant_out"

origin="http://smoke-test.invalid"
set +e
sitekey_out=$(docker run --rm --network "$NETWORK" $STACK_ENV "$image" admin issue-site-key \
    --tenant-id "$tenant_id" --allowed-origin "$origin" 2>&1)
rc=$?
set -e
[ "$rc" -eq 0 ] || fail "admin issue-site-key failed: $sitekey_out"
site_key=$(printf '%s' "$sitekey_out" | awk '{print $4}')
[ -n "$site_key" ] || fail "could not parse a site key from: $sitekey_out"

cid_chat=$(docker run --rm -d --network "$NETWORK" -p 127.0.0.1:18081:8000 $STACK_ENV "$image" api)
containers="$containers $cid_chat"
i=0
until curl -fsS http://127.0.0.1:18081/ready >/dev/null 2>&1; do
    i=$((i + 1))
    [ "$i" -lt 30 ] || fail "GET /ready did not respond within 30s for the chat-turn check"
    sleep 1
done

session_body=$(printf '{"site_key":"%s"}' "$site_key")
set +e
session_resp=$(curl -fsS -X POST "http://127.0.0.1:18081/api/v1/session" \
    -H "Origin: ${origin}" -H "Content-Type: application/json" -d "$session_body")
rc=$?
set -e
[ "$rc" -eq 0 ] || fail "POST /session failed: $session_resp"
session_token=$(printf '%s' "$session_resp" | python3 -c "import json, sys; print(json.load(sys.stdin)['session_token'])")
[ -n "$session_token" ] || fail "could not parse a session_token from: $session_resp"

client_message_id=$(python3 -c "import uuid; print(uuid.uuid4())")
chat_body=$(printf '{"message":"hi","client_message_id":"%s"}' "$client_message_id")
set +e
chat_out=$(curl -fsS -X POST "http://127.0.0.1:18081/api/v1/chat" \
    -H "Origin: ${origin}" -H "Authorization: Bearer ${session_token}" \
    -H "Content-Type: application/json" -d "$chat_body")
rc=$?
set -e
[ "$rc" -eq 0 ] || fail "POST /chat failed: $chat_out"
# Literal \\! (TWO backslashes, not one): _sanitize_answer_text()
# (backend/app/agent/graph.py) escapes every "!" in visitor-facing text
# to a literal single backslash + "!" to defuse markdown image syntax
# ("![...]") -- the real HTTP response body then JSON-escapes THAT
# literal backslash a second time, so the raw bytes on the wire carry
# two backslashes before "!". Confirmed live against the real response
# before fixing this pattern, not assumed.
case "$chat_out" in
    *'Hello\\! How can I help you today?'*) : ;;
    *) fail "a real greeting chat turn did not return the guardrail reply: $chat_out" ;;
esac
echo "PASS: a real greeting turn through POST /chat returns the fixed guardrail reply, not an error"

# (i) [SECURITY] L1 (out-of-band Phase 7 batch, 2026-10-17), Decision D
# (D-pilot-placeholder-secrets-gate): under APP_ENV=production, the exact
# .env.example placeholder values (the ones an operator who ran
# `cp .env.example .env` and forgot to replace them would actually have)
# must fail fast and loud -- then the SAME mode, same network, with
# real-looking values, must start cleanly. Uses "api" mode specifically:
# create_app() calls get_settings() synchronously before uvicorn ever
# binds (app/main.py), so a Settings failure here means the container
# exits non-zero almost immediately, before attempting to reach
# test-db/test-qdrant at all -- no disposable stack needed for the
# failure half of this check.
set +e
placeholder_out=$(docker run --rm \
    -e APP_ENV=production \
    -e DATABASE_URL=postgresql+psycopg://widgetplatform:change-me@postgres:5432/widgetplatform \
    -e QDRANT_URL=http://qdrant:6333 \
    -e QDRANT_API_KEY=change-me \
    -e QDRANT_READ_ONLY_API_KEY=change-me \
    -e JWT_SIGNING_KEY=change-me-change-me-change-me-change-me \
    -e DB_CONNECTION_ENCRYPTION_KEY=change-me-change-me-change-me-change-me \
    -e OPENAI_API_KEY=change-me \
    -e API_HOST=0.0.0.0 -e API_PORT=8000 \
    "$image" api 2>&1)
rc=$?
set -e
[ "$rc" -ne 0 ] || fail "api mode started with .env.example's own placeholder values under APP_ENV=production (exited 0)"
case "$placeholder_out" in
    *QDRANT_API_KEY*) : ;;
    *) fail "placeholder-rejection error did not name a variable: $placeholder_out" ;;
esac
echo "PASS: api mode with .env.example's placeholder values under APP_ENV=production exits non-zero, naming the variable"

# The "starts cleanly" half deliberately does NOT reuse test-db/
# test-qdrant: both disposable services are hardcoded, by design, to the
# identical throwaway "change-me"/"test-qdrant-key"/"test-qdrant-readonly-
# key" values this exact check (i) exists to reject -- using them here
# would require either weakening the gate being tested or changing shared
# test infrastructure many other make targets depend on, neither of which
# is this check's job. Settings construction (api/worker/migrate's own
# shared first step, before any network connection is attempted) is the
# precise, correctly-scoped thing to verify clean here -- confirmed
# separately, at check (d) above, that a real api process backed by
# test-db/test-qdrant starts and serves traffic end to end; this check
# only needs to prove THIS gate accepts realistic secrets, not re-prove
# the whole stack a second time with different ones.
set +e
realistic_out=$(docker run --rm --entrypoint python3 \
    -e APP_ENV=production \
    -e DATABASE_URL=postgresql+psycopg://widgetplatform:rK9mLpQ2vXzNcHdWaT@postgres:5432/widgetplatform \
    -e QDRANT_URL=http://qdrant:6333 \
    -e QDRANT_API_KEY=rK9mLpQ2vXzNcHdWaTfYbGjUeSoIhAkR \
    -e QDRANT_READ_ONLY_API_KEY=bN4oPqR7sTuVwXyZaB1cD2eF3gH4iJ5k \
    -e JWT_SIGNING_KEY=rK9mLpQ2vXzNcHdWaTfYbGjUeSoIhAkR \
    -e DB_CONNECTION_ENCRYPTION_KEY=bN4oPqR7sTuVwXyZaB1cD2eF3gH4iJ5k \
    -e OPENAI_API_KEY=sk-live-rK9mLpQ2vXzNcHdWaTfYbGjUeSoIhAkR \
    -e API_HOST=0.0.0.0 -e API_PORT=8000 \
    "$image" -c "from app.config import get_settings; get_settings(); print('OK')" 2>&1)
rc=$?
set -e
[ "$rc" -eq 0 ] && [ "$realistic_out" = "OK" ] || fail "Settings construction failed under APP_ENV=production with real-looking secrets: $realistic_out"
echo "PASS: Settings construction succeeds under APP_ENV=production with real-looking secrets"

# (j) [SECURITY] L1, PART B (2026-10-17): .env can never get into the
# image -- backend/.dockerignore already excludes it (backend/tests/
# test_ignore_files_guard.py's own static check), but this is the live
# proof against the REAL built image's own filesystem, not just the
# ignore-file text. `-xdev` stays inside the image's own single
# filesystem (no bind mounts to traverse); 2>/dev/null discards the
# expected "Permission denied" noise from scanning root-owned
# directories as the image's own non-root uid 10001 -- that noise is not
# a signal either way, only stdout (an actual match) would be.
found=$(docker run --rm --entrypoint find "$image" / -xdev -iname ".env*" 2>/dev/null)
[ -z "$found" ] || fail "found .env-named file(s) inside the built image: $found"
echo "PASS: no .env-named file exists anywhere inside the built image"
