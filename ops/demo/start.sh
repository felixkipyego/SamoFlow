#!/usr/bin/env bash
# ops/demo/start.sh -- local demo stack: db/qdrant/api/worker (Docker),
# the real dashboard dev server, and a static page embedding the real
# widget, all wired together with seeded demo data. Rerunnable: every
# step below is idempotent (seeds/assigns/uploads either create-if-
# missing or replace-in-place), except the owner's password, which is
# ALWAYS freshly rotated and printed -- this script never persists it
# anywhere (see the --email note below).
#
# Runs as its own Compose project (COMPOSE_PROJECT_NAME=samoflow-demo)
# on its own host port for the api service (see
# ops/demo/docker-compose.override.yml), so it never touches or
# conflicts with any other docker-compose.yml-based stack already
# running on this machine (e.g. a track worktree's own `make up`).
#
# See docs/deploy/RUNBOOK.md's "Local demo" section for the full
# walkthrough and what this leaves running.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$REPO_ROOT"

PROJECT_NAME="samoflow-demo"
API_PORT=8010
DASHBOARD_PORT=5180
WIDGET_PORT=4280
API_BASE="http://127.0.0.1:${API_PORT}"
WIDGET_ORIGIN="http://127.0.0.1:${WIDGET_PORT}"
TENANT_NAME="SamoFlow Demo"
# NOT demo@localhost: fastapi-users' own email validator (pydantic
# EmailStr / email-validator) rejects "localhost" and even ".local" as
# a domain outright (confirmed live -- both fail seed-owner's real
# validation, ".example" is the one reserved-for-documentation TLD,
# RFC 2606, that it accepts) -- this is the closest equivalent to
# demo@localhost that the real account-creation and support-details
# paths actually accept.
OWNER_EMAIL="demo@samoflow.example"
PLAN_NAME="Growth"
RUNTIME_DIR="/tmp/${PROJECT_NAME}"

COMPOSE=(docker compose --env-file .env -f deploy/docker-compose.yml -f ops/demo/docker-compose.override.yml)
export COMPOSE_PROJECT_NAME="$PROJECT_NAME"

mkdir -p "$RUNTIME_DIR"

if ! docker info >/dev/null 2>&1; then
  echo "Docker is not running -- start Docker Desktop and re-run this script." >&2
  exit 1
fi

if [ ! -f .env ]; then
  cp .env.example .env
  echo ".env did not exist -- created it from .env.example. Edit it (OPENAI_API_KEY" >&2
  echo "especially) and re-run this script." >&2
  exit 1
fi

# [SECURITY] L2 (Phase 7 role split, 2026-10-18): one-time, idempotent --
# creates/updates the owner+app Postgres roles and (on an EXISTING demo
# volume, the common case here) reassigns ownership of every table the
# old single shared role created, with zero data loss. Must run before
# api/worker ever start: `run` brings up `postgres` itself first (its own
# `depends_on: postgres: condition: service_healthy`), and api's own
# /ready check below would otherwise spend its whole 60s budget failing
# to authenticate as a role that doesn't exist yet.
echo "==> [1/10] Provisioning Postgres roles (owner, app)"
"${COMPOSE[@]}" run --rm provision-roles

echo "==> [2/10] Starting postgres, qdrant, api, worker (project: ${PROJECT_NAME})"
"${COMPOSE[@]}" up -d --build postgres qdrant api worker

echo "==> [3/10] Waiting for the API to report ready on ${API_BASE}"
ready=""
for _ in $(seq 1 60); do
  if curl -fsS "${API_BASE}/ready" >/dev/null 2>&1; then
    ready="yes"
    break
  fi
  sleep 1
done
if [ -z "$ready" ]; then
  echo "API did not become ready within 60s -- check: ${COMPOSE[*]} logs api" >&2
  exit 1
fi

echo "==> [4/10] Running migrations"
"${COMPOSE[@]}" run --rm migrate

echo "==> [5/10] Seeding the two pilot plans (Starter, Growth)"
"${COMPOSE[@]}" run --rm -T migrate admin seed-plans

echo "==> [6/10] Seeding the demo tenant + owner account (${OWNER_EMAIL})"
NEW_PASSWORD="$(python3 -c 'import secrets; print(secrets.token_urlsafe(18))')"
set +e
SEED_OUTPUT=$(printf '%s\n%s\n' "$NEW_PASSWORD" "$NEW_PASSWORD" | \
  "${COMPOSE[@]}" run --rm -T migrate seed-owner --email "$OWNER_EMAIL" --tenant-name "$TENANT_NAME" 2>&1)
SEED_STATUS=$?
set -e
echo "$SEED_OUTPUT"
if [ $SEED_STATUS -eq 0 ]; then
  OWNER_PASSWORD="$NEW_PASSWORD"
elif echo "$SEED_OUTPUT" | grep -q "already exists"; then
  echo "    owner already exists from a previous run -- rotating its password so this"
  echo "    run ends with a known-good login (the old one was shown once and never"
  echo "    stored anywhere, including by this script)"
  RESET_OUTPUT=$(printf '%s\n%s\n' "$NEW_PASSWORD" "$NEW_PASSWORD" | \
    "${COMPOSE[@]}" run --rm -T migrate admin reset-password --email "$OWNER_EMAIL" 2>&1)
  echo "$RESET_OUTPUT"
  OWNER_PASSWORD="$NEW_PASSWORD"
else
  echo "seed-owner failed unexpectedly -- see output above." >&2
  exit 1
fi

echo "==> [7/10] Assigning the ${PLAN_NAME} plan and ensuring a site key for ${WIDGET_ORIGIN}"
# No CLI subcommand does this idempotently (admin_cli's own assign-plan/
# issue-site-key always write/insert unconditionally, which would mint a
# fresh duplicate site key on every rerun) -- same repository primitives
# admin_cli.py itself calls (TenantScopedRepository.create_site_key(),
# generate_site_key(), normalize_origin()), just wrapped in a get-or-
# create check so this script is safe to rerun.
PY_HELPER=$(cat <<PYEOF
import asyncio
from app import all_models  # noqa: F401
from app.auth.origin import normalize_origin
from app.db import _session_factory
from app.plans.models import Plan
from app.tenancy.models import Tenant
from app.tenancy.repository import TenantScopedRepository, generate_site_key
from sqlalchemy import select


async def main() -> None:
    async with _session_factory()() as session:
        tenant = (
            await session.execute(select(Tenant).where(Tenant.name == "${TENANT_NAME}"))
        ).scalar_one()
        plan = (
            await session.execute(select(Plan).where(Plan.name == "${PLAN_NAME}"))
        ).scalar_one()
        tenant.plan_id = plan.id

        repo = TenantScopedRepository(tenant_id=tenant.id, session=session)
        origin = normalize_origin("${WIDGET_ORIGIN}")
        assert origin is not None
        existing = next(
            (sk for sk in await repo.list_site_keys() if origin in (sk.allowed_origins or [])),
            None,
        )
        if existing is not None:
            site_key = existing
        else:
            site_key = await repo.create_site_key(
                key=generate_site_key(), allowed_origins=[origin], environment="development"
            )
        await session.commit()
        print(f"TENANT_ID={tenant.id}")
        print(f"SITE_KEY={site_key.key}")


asyncio.run(main())
PYEOF
)
HELPER_OUTPUT=$("${COMPOSE[@]}" run --rm --entrypoint python3 -T migrate -c "$PY_HELPER")
echo "$HELPER_OUTPUT" | sed 's/^/    /'
SITE_KEY=$(echo "$HELPER_OUTPUT" | sed -n 's/^SITE_KEY=//p')
if [ -z "$SITE_KEY" ]; then
  echo "could not determine the demo site key -- see output above." >&2
  exit 1
fi

echo "==> [8/10] Logging in and setting support details + uploading the golden-set pages"
COOKIE_JAR="$(mktemp)"
trap 'rm -f "$COOKIE_JAR"' EXIT

curl -fsS -c "$COOKIE_JAR" -o /dev/null "${API_BASE}/api/v1/dashboard/auth/csrf"
CSRF_TOKEN=$(awk -F'\t' '$6 ~ /csrf/ {print $7}' "$COOKIE_JAR")
if [ -z "$CSRF_TOKEN" ]; then
  echo "could not read the CSRF cookie -- see ${COOKIE_JAR}" >&2
  exit 1
fi

LOGIN_BODY=$(python3 -c "import json,sys; print(json.dumps({'email': sys.argv[1], 'password': sys.argv[2]}))" \
  "$OWNER_EMAIL" "$OWNER_PASSWORD")
LOGIN_STATUS=$(curl -s -o /dev/null -w '%{http_code}' -b "$COOKIE_JAR" -c "$COOKIE_JAR" \
  -X POST "${API_BASE}/api/v1/dashboard/auth/login" \
  -H "Content-Type: application/json" -H "X-CSRF-Token: ${CSRF_TOKEN}" \
  -d "$LOGIN_BODY")
if [ "$LOGIN_STATUS" != "200" ]; then
  echo "dashboard login failed (HTTP ${LOGIN_STATUS}) -- cannot set support details or upload" >&2
  exit 1
fi

# No CLI command sets support details either (Step 6.2 only ever exposed
# this through the authenticated dashboard API, for the dashboard SPA
# itself to call) -- this calls that same real endpoint, not a new one.
SUPPORT_DETAILS_BODY=$(python3 -c "import json,sys; print(json.dumps({'support_email': sys.argv[1], 'support_hours': 'Mon-Fri 9am-5pm EAT'}))" "$OWNER_EMAIL")
curl -fsS -b "$COOKIE_JAR" -X PATCH "${API_BASE}/api/v1/dashboard/tenant/support-details" \
  -H "Content-Type: application/json" -H "X-CSRF-Token: ${CSRF_TOKEN}" \
  -d "$SUPPORT_DETAILS_BODY" >/dev/null

# Domain verification can't happen on localhost, so this uses the real
# upload path (POST /api/v1/dashboard/sources/upload) instead -- the
# same endpoint and code path the dashboard's own Sources screen uses.
# Reusing one upload-type source's id across every file (looked up
# first, created by the first call otherwise) keeps reruns idempotent:
# a second upload under the same filename replaces the first in place
# rather than piling up duplicates (see upload_source()'s own docstring,
# backend/app/dashboard/source_routes.py).
SOURCE_ID=$(curl -fsS -b "$COOKIE_JAR" "${API_BASE}/api/v1/dashboard/sources" | \
  python3 -c "import json,sys; d=json.load(sys.stdin); print(next((s['id'] for s in d['sources'] if s['type']=='upload'), ''))")

page_count=0
for page in "$REPO_ROOT"/evals/golden_set/pages/*.md; do
  if [ -n "$SOURCE_ID" ]; then
    url="${API_BASE}/api/v1/dashboard/sources/upload?source_id=${SOURCE_ID}"
  else
    url="${API_BASE}/api/v1/dashboard/sources/upload"
  fi
  response=$(curl -fsS -b "$COOKIE_JAR" -X POST "$url" \
    -H "X-CSRF-Token: ${CSRF_TOKEN}" -F "file=@${page};type=text/markdown")
  SOURCE_ID=$(echo "$response" | python3 -c "import json,sys; print(json.load(sys.stdin)['id'])")
  page_count=$((page_count + 1))
done
echo "    uploaded ${page_count} golden-set pages into upload source ${SOURCE_ID}"

echo "==> [9/10] Waiting for the worker to finish indexing"
indexed=""
for _ in $(seq 1 30); do
  status=$(curl -fsS -b "$COOKIE_JAR" "${API_BASE}/api/v1/dashboard/sources" | \
    python3 -c "
import json, sys
d = json.load(sys.stdin)
s = next((s for s in d['sources'] if s['id'] == '${SOURCE_ID}'), None)
print(s['status'] if s else 'missing')
")
  if [ "$status" = "ok" ]; then
    indexed="yes"
    break
  fi
  sleep 1
done
if [ -z "$indexed" ]; then
  echo "    worker did not finish indexing within 30s (last status: ${status}) --" >&2
  echo "    check: ${COMPOSE[*]} logs worker" >&2
fi

echo "==> [10/10] Starting the dashboard dev server and the widget test page"

if [ ! -d dashboard/node_modules ]; then
  echo "    installing dashboard/ npm dependencies (first run only)"
  (cd dashboard && npm ci)
fi
if [ ! -d widget/node_modules ]; then
  echo "    installing widget/ npm dependencies (first run only)"
  (cd widget && npm ci)
fi

if [ -f "$RUNTIME_DIR/dashboard.pid" ] && kill -0 "$(cat "$RUNTIME_DIR/dashboard.pid")" 2>/dev/null; then
  echo "    dashboard dev server already running (pid $(cat "$RUNTIME_DIR/dashboard.pid"))"
else
  # --host 127.0.0.1, not vite's own "localhost" default: confirmed live
  # that default binds the IPv6 loopback ([::1]) only on this machine,
  # not 127.0.0.1 -- every other URL in this script is IPv4, so this
  # must match. Runs node_modules/.bin/vite directly (never `npx vite`):
  # npx spawns vite as a CHILD process rather than exec'ing into it
  # (confirmed live: `npx vite` left two processes, npm's own PID
  # ultimately useless for `kill`), which would make `$!` below capture
  # the wrong PID for stop.sh to kill. The `exec` inside this subshell
  # means the subshell's own PID (captured via `$!` right after
  # backgrounding it) IS vite's real PID throughout -- nohup itself also
  # execs rather than forking, so nothing is interposed in between.
  (cd dashboard && exec nohup node_modules/.bin/vite --config vite.config.demo.ts \
    --host 127.0.0.1 --port "$DASHBOARD_PORT" --strictPort \
    > "$RUNTIME_DIR/dashboard.log" 2>&1) &
  echo $! > "$RUNTIME_DIR/dashboard.pid"
  disown
  sleep 1
fi

echo "    building the widget (esbuild)"
(cd widget && npm run build >"$RUNTIME_DIR/widget-build.log" 2>&1)

WIDGET_SITE_DIR="$RUNTIME_DIR/widget-site"
rm -rf "$WIDGET_SITE_DIR"
mkdir -p "$WIDGET_SITE_DIR"
cp widget/dist/widget.js widget/dist/widget.css "$WIDGET_SITE_DIR/"
cp widget/chat/dist/chat.js widget/chat/dist/chat.css "$WIDGET_SITE_DIR/"
cat > "$WIDGET_SITE_DIR/index.html" <<HTMLEOF
<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <title>SamoFlow widget -- local demo</title>
  </head>
  <body>
    <h1>SamoFlow widget -- local demo</h1>
    <p>The chat launcher should appear in the bottom-right corner.</p>
    <script
      src="/widget.js"
      data-site-key="${SITE_KEY}"
      data-base-url="${API_BASE}"
    ></script>
  </body>
</html>
HTMLEOF

if [ -f "$RUNTIME_DIR/widget.pid" ] && kill -0 "$(cat "$RUNTIME_DIR/widget.pid")" 2>/dev/null; then
  echo "    widget test page server already running (pid $(cat "$RUNTIME_DIR/widget.pid"))"
else
  nohup python3 -m http.server "$WIDGET_PORT" --bind 127.0.0.1 --directory "$WIDGET_SITE_DIR" \
    > "$RUNTIME_DIR/widget.log" 2>&1 &
  echo $! > "$RUNTIME_DIR/widget.pid"
  disown
  sleep 1
fi

trap - EXIT
rm -f "$COOKIE_JAR"

echo ""
echo "================================================================"
echo "Demo is up and left running. See docs/deploy/RUNBOOK.md's"
echo "\"Local demo\" section, or ops/demo/stop.sh to stop it."
echo ""
echo "Dashboard:   http://127.0.0.1:${DASHBOARD_PORT}"
echo "Login email: ${OWNER_EMAIL}"
echo "Password:    ${OWNER_PASSWORD}"
echo ""
echo "Widget test page: ${WIDGET_ORIGIN}/"
echo "================================================================"
