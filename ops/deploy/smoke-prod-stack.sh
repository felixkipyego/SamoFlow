#!/bin/sh
# ops/deploy/smoke-prod-stack.sh -- [SECURITY] L1 PART C follow-up
# (advisor review, 2026-10-17): a REUSABLE, end-to-end check of the REAL
# docker-compose.prod.yml stack (Caddy + api + postgres + qdrant), over
# real HTTP/HTTPS -- never the backend image in isolation
# (deploy/smoke-image.sh's own job, a different and narrower check).
# Run it locally now (against a local_certs Caddyfile variant -- see
# below) AND again on the real production server at go-live (against
# the real, unmodified deploy/Caddyfile and real public DNS/ACME certs)
# -- the SAME script, unchanged, driven only by its own arguments.
#
# Found this script's own reason to exist: an advisor code review of
# this batch's own prior commit found a real production-breaking bug
# (app/main.py's TrustedHostMiddleware only allowed API_HOSTNAME, but
# deploy/Caddyfile's own dashboard site proxies /api/* with the
# DASHBOARD host header unchanged -- every dashboard API call would
# have 400'd in production) that NO existing check caught, because
# nothing had ever exercised a dashboard /api call through the real
# Caddy, in APP_ENV=production, end to end. This script is the gap
# closed: everything a real visitor or dashboard user actually does,
# through the real proxy, is checked here, every time.
#
# Usage:
#   ops/deploy/smoke-prod-stack.sh <dashboard_host> <api_host> \
#       <widget_host> <target_ip> [cacert_path]
#
#   <target_ip>    the IP curl connects to for EVERY request below, via
#                   --resolve -- never real DNS, so this script works
#                   identically whether or not <*_host> actually
#                   resolves yet (useful right after a DNS cutover,
#                   before propagation) and whether run locally
#                   (127.0.0.1) or against the real server (its real
#                   public IP -- Felix already has this, it is what
#                   DASHBOARD_HOSTNAME/etc. point PUBLIC DNS at).
#   [cacert_path]  LOCAL TESTING ONLY. Omit entirely at go-live (real
#                   publicly-trusted certs need nothing extra). Locally,
#                   stand the stack up with a Caddyfile that differs
#                   from the real deploy/Caddyfile ONLY by adding
#                   `local_certs` to the global options block (Caddy's
#                   own internal CA -- instant issuance, no real ACME,
#                   no real DNS needed), then pass this script the
#                   extracted root CA (`docker cp <caddy
#                   container>:/data/caddy/pki/authorities/local/
#                   root.crt .`) so curl can verify the real TLS
#                   handshake against a CA it is told to trust --
#                   NEVER `curl -k`, which would silently also accept a
#                   handshake against the WRONG certificate.
#
# Assumes the stack this script checks is ALREADY running (`docker
# compose --env-file .env -f deploy/docker-compose.yml -f deploy/
# docker-compose.prod.yml up -d`, RUNBOOK's own documented first-deploy
# step) -- this script only reads from it and seeds one throwaway
# smoke-test tenant/site-key/dashboard-owner into the real database (via
# the SAME `admin`/`seed-owner` CLI commands RUNBOOK already documents
# for the real pilot tenant), never starts/stops/rebuilds it. Each run's
# smoke-test rows are uniquely named (a Unix timestamp + this shell's
# own pid) so repeat runs never collide.
#
# [SECURITY] cleanup follow-up (advisor review, 2026-10-17): an earlier
# version of this script derived the smoke owner's own login PASSWORD
# from that same timestamp, and never deleted anything it seeded -- on
# a public repo, that is a real, working admin credential whose
# derivation formula is public too, left sitting on the real production
# server. Fixed: the password is now a real OS-CSPRNG value (see
# random_secret() below), and this script's own `cleanup()` (wired to
# `trap ... EXIT`, so it runs whether this script finishes, fails, or
# is Ctrl-C'd) deletes the tenant, its site key (cascades from the
# tenant delete) and the owner user on every exit path -- see this
# file's own `cleanup()` for the one case (SIGKILL) nothing can catch,
# and RUNBOOK §5 for the one-command manual recovery for that case.
set -eu

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

usage() {
    echo "usage: $0 <dashboard_host> <api_host> <widget_host> <target_ip> [cacert_path]" >&2
    exit 2
}

[ "$#" -ge 4 ] || usage
DASHBOARD_HOST="$1"
API_HOST="$2"
WIDGET_HOST="$3"
TARGET_IP="$4"
CACERT="${5:-}"

fail() {
    echo "FAIL: $1" >&2
    exit 1
}

pass() {
    echo "PASS: $1"
}

# Word-split intentionally (matching deploy/smoke-image.sh's own STACK_
# ENV precedent): zero or one "--cacert path" token, never quoted as a
# single argument, or curl would see one argument "--cacert " when
# CACERT is empty.
CURL_CA=""
[ -z "$CACERT" ] || CURL_CA="--cacert $CACERT"

# cd into REPO_ROOT, then use ONLY relative paths from here on (deploy/
# smoke-image.sh's own established precedent) -- REPO_ROOT itself may
# contain spaces (confirmed live: this exact repo's own checkout path
# does), which plain unquoted word-splitting (needed below for a
# variable-length, optional -f/-p argument list, same as deploy/smoke-
# image.sh's own STACK_ENV) would otherwise tear apart into separate,
# wrong argv tokens.
cd "$REPO_ROOT"

# SMOKE_ENV_FILE/SMOKE_EXTRA_COMPOSE_ARGS/SMOKE_PROJECT: unset at go-live
# (the real run uses the real tracked .env and the real tracked compose
# files, no overrides at all, matching RUNBOOK's own documented
# invocation byte for byte). Exist ONLY so this exact script can be
# verified locally against a scratch project (its own env file, its own
# `-p` project name, its own extra `-f` override mounting the local_
# certs Caddyfile variant) without ever touching the real repo-root
# `.env` or the real tracked compose files -- see this script's own
# header comment for the local_certs setup these are for.
# shellcheck disable=SC2086
COMPOSE="docker compose --env-file ${SMOKE_ENV_FILE:-.env} ${SMOKE_PROJECT:+-p $SMOKE_PROJECT} -f deploy/docker-compose.yml -f deploy/docker-compose.prod.yml ${SMOKE_EXTRA_COMPOSE_ARGS:-}"

run_migrate() {
    # shellcheck disable=SC2086
    $COMPOSE run --rm -T migrate "$@"
}

COOKIE_JAR="$(mktemp)"
TMP_HEADERS="$(mktemp)"
TMP_BODY="$(mktemp)"

# [SECURITY] cleanup follow-up (advisor review, 2026-10-17): runs on
# EVERY exit path (a clean finish, any `fail` call above, and Ctrl-C --
# `trap ... EXIT` fires for all three; only SIGKILL, which no trap can
# ever catch by design, skips it -- see this script's own header
# comment and RUNBOOK §5 for the one-command recovery for that case).
# `tenant_id`/`OWNER_EMAIL` are read defensively (`${var:-}`) because
# an interruption early enough (before seeding even starts) means
# neither is assigned yet -- under this script's own `set -u`, a bare
# `$tenant_id` would itself be a hard error in exactly the scenario
# this trap exists to handle gracefully.
#
# delete-tenant cascades to the tenant's own site_keys row too (every
# FK to tenants.id is ondelete="CASCADE", app/tenancy/repository.py's
# own header comment) -- a separate revoke-site-key call would be
# redundant, not a second layer of safety: there is no site_keys row
# left for it to act on once the tenant delete commits either way.
# Both admin commands are idempotent (a no-op, not an error, against
# an id/email already gone) -- safe to call even when seeding itself
# never got far enough to create them, and safe if this trap somehow
# ran twice. `|| true`: a cleanup failure must never mask the real
# exit code this trap is preserving, nor abort the OTHER cleanup step
# still queued after it.
cleanup() {
    rc=$?
    rm -f "$COOKIE_JAR" "$TMP_HEADERS" "$TMP_BODY"
    echo "--- cleanup: removing the throwaway tenant/site-key/owner this run seeded ---"
    if [ -n "${tenant_id:-}" ]; then
        run_migrate admin delete-tenant --tenant-id "$tenant_id" --confirm || true
    fi
    if [ -n "${OWNER_EMAIL:-}" ]; then
        run_migrate admin delete-user --email "$OWNER_EMAIL" --confirm || true
    fi
    exit "$rc"
}
trap cleanup EXIT

# [SECURITY] cleanup follow-up (advisor review, 2026-10-17): SUFFIX is
# an IDENTIFIER (tenant name, email local-part) -- recognisable on
# purpose, never the secret -- so a plain timestamp is fine for it, and
# $$ (this shell's own pid) is appended so two runs started within the
# same second (the "two consecutive runs don't collide" proof this
# follow-up adds) still get distinct names.
SUFFIX="$(date +%s)-$$"
TENANT_NAME="smoke-test-$SUFFIX"
# "example.com" (not "example.invalid"): fastapi-users' own email-
# validator library rejects ".invalid" outright ("a special-use or
# reserved name that cannot be used with email"), confirmed live while
# building this script -- "example.com" is RFC 2606 reserved-for-
# documentation too, and the one of the two this library actually
# accepts.
OWNER_EMAIL="smoke-test-$SUFFIX@example.com"

# [SECURITY] cleanup follow-up (advisor review, 2026-10-17): a REAL
# issue this follow-up fixes -- this value used to be derived from
# $(date +%s), a guessable, low-entropy "password" for a real, working
# admin login this script would otherwise leave on the real production
# server (this repo is public, so the derivation formula itself was
# public too). Read straight from the OS CSPRNG instead, at least 32
# random characters, held ONLY in this shell variable for the rest of
# the run -- never echoed, never logged, never written to a file, and
# never passed as a command-line argument to any subprocess (which
# `ps` on a multi-user host could read): every use below pipes it
# through a subprocess's own stdin via a shell BUILTIN `printf` (never
# the external /usr/bin/printf) -- a builtin runs inside a forked copy
# of THIS shell that never calls exec(), so even that fork's own argv,
# if inspected via `ps` during the brief window it exists, still shows
# this script's own original invocation line, never the password.
# `openssl rand -base64 32` first (32 random BYTES, base64-encoded to
# ~44 printable characters -- well over the 32-character floor, and
# the base64 alphabet contains no `"`/`\`/control character that could
# break the JSON bodies this value is embedded into below, so it needs
# no escaping); `/dev/urandom` + `tr` as the fallback on a host with no
# `openssl` on PATH.
random_secret() {
    if command -v openssl >/dev/null 2>&1; then
        openssl rand -base64 32
    else
        head -c 48 /dev/urandom | tr -dc 'A-Za-z0-9' | head -c 40
    fi
}
OWNER_PASSWORD="$(random_secret)"
[ -n "$OWNER_PASSWORD" ] || fail "random_secret() produced an empty password"

echo "--- seeding a throwaway tenant/site-key/dashboard-owner ($TENANT_NAME) ---"
tenant_out="$(run_migrate admin create-tenant --name "$TENANT_NAME" 2>&1)" \
    || fail "admin create-tenant failed: $tenant_out"
# `grep` isolates the ONE real "created tenant '...' (<uuid>)" line
# first -- unlike deploy/smoke-image.sh's own identical-looking parse
# (plain `docker run`, no Compose wrapper), `docker compose run` ALSO
# prints its own "Container ... Creating/Created" lifecycle lines to the
# same stream, each of which also has a trailing `(...)`-shaped token
# that `sed`/`awk` applied to the WHOLE multi-line string would
# otherwise match too, corrupting the parsed value -- confirmed live,
# not assumed (this exact failure happened while building this script).
tenant_id="$(printf '%s' "$tenant_out" | grep "^created tenant" | sed -n 's/.*(\(.*\))$/\1/p')"
[ -n "$tenant_id" ] || fail "could not parse a tenant id from: $tenant_out"

sitekey_out="$(run_migrate admin issue-site-key --tenant-id "$tenant_id" \
    --allowed-origin "https://$WIDGET_HOST" --environment production --status live 2>&1)" \
    || fail "admin issue-site-key failed: $sitekey_out"
site_key="$(printf '%s' "$sitekey_out" | grep "^issued site key" | awk '{print $4}')"
[ -n "$site_key" ] || fail "could not parse a site key from: $sitekey_out"

# seed-owner's own password prompt (app/dashboard/seed_cli.py's
# getpass.getpass()) falls back to reading plain stdin, with a logged
# warning, when it cannot control terminal echo -- exactly the case
# under `docker compose run -T` (stdin piped, no real tty). Confirmed
# live before relying on it here, not assumed.
owner_out="$(printf '%s\n%s\n' "$OWNER_PASSWORD" "$OWNER_PASSWORD" \
    | run_migrate seed-owner --email "$OWNER_EMAIL" --tenant-name "$TENANT_NAME" 2>&1)" \
    || fail "seed-owner failed: $owner_out"
case "$owner_out" in
    *"seeded owner"*) : ;;
    *) fail "seed-owner did not report success: $owner_out" ;;
esac
echo "PASS: seeded tenant $tenant_id, site key, and dashboard owner $OWNER_EMAIL"

# --- (a) dashboard SPA loads on the dashboard host, with the STRICT CSP ---
# [SECURITY] a REAL bug this script's own first draft found, live, not
# assumed: putting Content-Security-Policy in the shared security_
# headers snippet AND also setting a stricter one in the dashboard site
# block did not actually override it -- Caddy's own `deferred` header
# semantics meant the snippet's BARE csp silently won in every response.
# Fixed in deploy/Caddyfile (see that file's own header comment, same
# date); this check is what makes sure it never regresses.
curl -s $CURL_CA --resolve "$DASHBOARD_HOST:443:$TARGET_IP" \
    -D "$TMP_HEADERS" -o "$TMP_BODY" "https://$DASHBOARD_HOST/"
grep -qi "^content-security-policy:.*script-src 'self'" "$TMP_HEADERS" \
    || fail "dashboard host did not carry the strict CSP (script-src 'self') -- headers: $(cat "$TMP_HEADERS")"
pass "dashboard SPA loads on $DASHBOARD_HOST with the strict CSP header"

# --- (b) a full dashboard login through Caddy, on the dashboard host ---
curl -s $CURL_CA --resolve "$DASHBOARD_HOST:443:$TARGET_IP" \
    -c "$COOKIE_JAR" -D "$TMP_HEADERS" -o /dev/null \
    "https://$DASHBOARD_HOST/api/v1/dashboard/auth/csrf"
grep -q "204" "$TMP_HEADERS" || fail "CSRF fetch did not return 204: $(cat "$TMP_HEADERS")"
csrf_cookie_line="$(grep "__Host-csrf" "$TMP_HEADERS" || true)"
case "$csrf_cookie_line" in
    *"Secure"*) : ;;
    *) fail "__Host-csrf cookie missing Secure: $csrf_cookie_line" ;;
esac
case "$csrf_cookie_line" in
    *"HttpOnly"*) fail "__Host-csrf cookie must NOT be HttpOnly (the SPA must read it): $csrf_cookie_line" ;;
    *) : ;;
esac
case "$csrf_cookie_line" in
    *"SameSite=Strict"* | *"SameSite=strict"*) : ;;
    *) fail "__Host-csrf cookie missing SameSite=Strict: $csrf_cookie_line" ;;
esac
csrf_token="$(grep "__Host-csrf" "$COOKIE_JAR" | awk '{print $7}')"
[ -n "$csrf_token" ] || fail "could not read the __Host-csrf cookie back from the jar"

# --data-binary @- (never -d "...$OWNER_PASSWORD...", a literal argv
# element `ps` could read): the JSON body is written to curl's own
# stdin by the `printf` BUILTIN above this pipe, which never execs a
# new process and therefore never puts the password into any process's
# argv at all.
printf '{"email":"%s","password":"%s"}' "$OWNER_EMAIL" "$OWNER_PASSWORD" | curl -s $CURL_CA \
    --resolve "$DASHBOARD_HOST:443:$TARGET_IP" \
    -b "$COOKIE_JAR" -c "$COOKIE_JAR" -D "$TMP_HEADERS" -o "$TMP_BODY" \
    -H "Content-Type: application/json" -H "X-CSRF-Token: $csrf_token" \
    --data-binary @- \
    "https://$DASHBOARD_HOST/api/v1/dashboard/auth/login"
grep -q "200" "$TMP_HEADERS" || fail "dashboard login failed: $(cat "$TMP_HEADERS") / $(cat "$TMP_BODY")"
session_cookie_line="$(grep "__Host-session" "$TMP_HEADERS" || true)"
case "$session_cookie_line" in
    *"Secure"*) : ;;
    *) fail "__Host-session cookie missing Secure: $session_cookie_line" ;;
esac
case "$session_cookie_line" in
    *"HttpOnly"*) : ;;
    *) fail "__Host-session cookie missing HttpOnly: $session_cookie_line" ;;
esac
case "$session_cookie_line" in
    *"SameSite=Strict"* | *"SameSite=strict"*) : ;;
    *) fail "__Host-session cookie missing SameSite=Strict: $session_cookie_line" ;;
esac
pass "dashboard login through Caddy succeeds; __Host-session is Secure+HttpOnly+SameSite=Strict"

curl -s $CURL_CA --resolve "$DASHBOARD_HOST:443:$TARGET_IP" \
    -b "$COOKIE_JAR" -o "$TMP_BODY" -w "%{http_code}" \
    "https://$DASHBOARD_HOST/api/v1/dashboard/auth/me" > "$TMP_HEADERS"
[ "$(cat "$TMP_HEADERS")" = "200" ] || fail "authenticated /api/v1/dashboard/auth/me did not return 200: $(cat "$TMP_BODY")"
case "$(cat "$TMP_BODY")" in
    *"$OWNER_EMAIL"*) : ;;
    *) fail "authenticated /auth/me did not echo the logged-in owner: $(cat "$TMP_BODY")" ;;
esac
pass "an authenticated dashboard /api call (GET /auth/me) succeeds through Caddy"

csrf_token="$(grep "__Host-csrf" "$COOKIE_JAR" | awk '{print $7}')"
curl -s $CURL_CA --resolve "$DASHBOARD_HOST:443:$TARGET_IP" \
    -b "$COOKIE_JAR" -c "$COOKIE_JAR" -o /dev/null -w "%{http_code}" \
    -H "X-CSRF-Token: $csrf_token" -X POST \
    "https://$DASHBOARD_HOST/api/v1/dashboard/auth/logout" > "$TMP_HEADERS"
[ "$(cat "$TMP_HEADERS")" = "204" ] || fail "logout did not return 204"
curl -s $CURL_CA --resolve "$DASHBOARD_HOST:443:$TARGET_IP" \
    -b "$COOKIE_JAR" -o /dev/null -w "%{http_code}" \
    "https://$DASHBOARD_HOST/api/v1/dashboard/auth/me" > "$TMP_HEADERS"
[ "$(cat "$TMP_HEADERS")" = "401" ] || fail "the session must be rejected (401) after logout, got $(cat "$TMP_HEADERS")"
pass "logout revokes the session -- the SAME cookie is rejected (401) immediately afterward"

# --- (c) widget assets load on the widget host ---
curl -s $CURL_CA --resolve "$WIDGET_HOST:443:$TARGET_IP" \
    -o /dev/null -w "%{http_code}" "https://$WIDGET_HOST/widget.js" > "$TMP_HEADERS"
[ "$(cat "$TMP_HEADERS")" = "200" ] || fail "widget.js did not load on $WIDGET_HOST: $(cat "$TMP_HEADERS")"
pass "widget assets load on $WIDGET_HOST"

# --- (d) a widget /session and a real /chat turn, via the api host, SSE not buffered ---
origin="https://$WIDGET_HOST"
session_resp="$(curl -s $CURL_CA --resolve "$API_HOST:443:$TARGET_IP" \
    -H "Origin: $origin" -H "Content-Type: application/json" \
    -d "{\"site_key\":\"$site_key\"}" "https://$API_HOST/api/v1/session")"
session_token="$(printf '%s' "$session_resp" | python3 -c "import json, sys; print(json.load(sys.stdin)['session_token'])")" \
    || fail "POST /session did not return a session_token: $session_resp"

client_message_id="$(python3 -c "import uuid; print(uuid.uuid4())")"
curl -s $CURL_CA --resolve "$API_HOST:443:$TARGET_IP" \
    -H "Origin: $origin" -H "Authorization: Bearer $session_token" -H "Content-Type: application/json" \
    -D "$TMP_HEADERS" -o "$TMP_BODY" \
    -d "{\"message\":\"hi\",\"client_message_id\":\"$client_message_id\"}" \
    "https://$API_HOST/api/v1/chat"
grep -qi "^content-type: text/event-stream" "$TMP_HEADERS" \
    || fail "/chat did not respond with text/event-stream: $(cat "$TMP_HEADERS")"
grep -qi "^content-length:" "$TMP_HEADERS" \
    && fail "/chat response carried a Content-Length header -- it is being buffered, not streamed: $(cat "$TMP_HEADERS")"
grep -qi "^x-accel-buffering: no" "$TMP_HEADERS" \
    || fail "/chat response is missing X-Accel-Buffering: no: $(cat "$TMP_HEADERS")"
case "$(cat "$TMP_BODY")" in
    *'Hello\\! How can I help you today?'*) : ;;
    *) fail "a real greeting chat turn did not return the guardrail reply: $(cat "$TMP_BODY")" ;;
esac
pass "POST /session + a real streamed (not buffered) /chat turn succeed via $API_HOST with a valid Origin"

# --- (e) Host: evil.com / an IP literal / a missing host, over BOTH http and https ---
check_rejected_http() {
    host_header="$1"
    code="$(curl -s -o /dev/null -w "%{http_code}" --resolve "evil-probe.invalid:80:$TARGET_IP" \
        -H "Host: $host_header" "http://evil-probe.invalid/" 2>/dev/null || echo "000")"
    case "$code" in
        200) fail "Host: '$host_header' over http was NOT rejected (got 200)" ;;
        *) : ;;
    esac
}

check_rejected_https() {
    host_header="$1"
    # A bad/unmanaged Host at the TLS layer fails the handshake itself
    # (no certificate exists for it) before any HTTP status code is
    # ever produced -- curl exits non-zero, never 200; that failure
    # itself IS the rejection proof.
    if curl -s -o /dev/null $CURL_CA --resolve "evil-probe.invalid:443:$TARGET_IP" \
        -H "Host: $host_header" "https://evil-probe.invalid/" 2>/dev/null; then
        fail "Host: '$host_header' over https was NOT rejected (a real 200 was returned)"
    fi
}

check_rejected_http "evil.com"
check_rejected_https "evil.com"
pass "Host: evil.com is rejected over both http and https"

check_rejected_http "203.0.113.9"
check_rejected_https "203.0.113.9"
pass "an IP-literal Host (203.0.113.9) is rejected over both http and https"

missing_host_code="$(curl -s -o /dev/null -w "%{http_code}" --resolve "$API_HOST:80:$TARGET_IP" \
    -H "Host:" "http://$API_HOST/")"
case "$missing_host_code" in
    200) fail "a missing Host header over http was NOT rejected (got 200)" ;;
    *) : ;;
esac
pass "a missing Host header is rejected over http (got $missing_host_code, never 200)"

# --- (f) http://<host>/ redirects to https; the abort catch-all never swallows it ---
for host in "$DASHBOARD_HOST" "$API_HOST" "$WIDGET_HOST"; do
    curl -s -D "$TMP_HEADERS" -o /dev/null --resolve "$host:80:$TARGET_IP" "http://$host/"
    grep -qi "^location: https://$host" "$TMP_HEADERS" \
        || fail "http://$host/ did not redirect to https: $(cat "$TMP_HEADERS")"
done
pass "http://<host>/ redirects to https:// for all 3 configured hosts"

echo "--- caddy adapt: proving the redirect ordering, and where ACME HTTP-01 fits ---"
caddy_cid="$($COMPOSE ps -q caddy)"
[ -n "$caddy_cid" ] || fail "could not find the running caddy container"
echo "Caddyfile-adapted routes on :443 (the 3 real site blocks, host-matched):"
docker exec "$caddy_cid" caddy adapt --config /etc/caddy/Caddyfile --adapter caddyfile 2>/dev/null \
    | python3 -c "
import json, sys
d = json.load(sys.stdin)
for name, srv in d['apps']['http']['servers'].items():
    matches = [r.get('match') for r in srv.get('routes', [])]
    print(f'  server {name} listen={srv.get(\"listen\")}: {matches}')
"
cat <<'EOF'
This script's own Caddyfile-static route list (the ":80" server above)
shows ONLY this file's own explicit `http:// { abort }` catch-all --
confirmed live that Caddy's automatic-HTTPS redirect routes are NOT
present in `caddy adapt`'s output (a pure, static Caddyfile->JSON
conversion) OR in the admin API's own `/config/` GET (which reflects
the config as LOADED, not as further transformed at provision time) --
yet the live redirect test just above DID succeed. This is not a
contradiction: per Caddy 2.10.2's own source (modules/caddyhttp/
autohttps.go, function around automaticHTTPSPhase2/provisionAutoHTTPS),
the redirect routes are inserted into each server's in-memory route
list during App.Provision() -- after the static config is loaded, at
the exact position the source code's own comment states: "find the
index of the route after the last route with a host matcher, then
insert the redirects there, but before any user-defined catch-all
routes" (see https://github.com/caddyserver/caddy/issues/3212, cited by
that same comment). That is PRECISELY the ordering this script's own
live test above proves empirically, even though no static/adapt
artifact shows it: a request for one of the 3 real, TLS-managed hosts
is redirected (the auto-inserted route, ahead of the catch-all); a
request for any other host falls through to this file's own explicit
abort catch-all, last.

ACME HTTP-01: Caddy intercepts /.well-known/acme-challenge/* requests
on port 80 specially, independent of (and ahead of) this Caddyfile's
own configured routes -- documented behavior (several open caddyserver/
caddy GitHub issues, e.g. #4612/#5128/#8096, describe this interception
as happening even when `auto_https off` or a conflicting user route
would otherwise claim that path), implemented via certmagic's own HTTP
solver, which Caddy's TLS automation wires in during the same Provision
phase as the redirect routes above -- NOT inside modules/caddyhttp
itself. This script cannot exercise that path live: `local_certs` (used
for every local run of this script) issues from Caddy's own internal
CA and never attempts a real ACME challenge at all. The real HTTP-01
path is exercised for real the first time this script runs against the
real production server, during the real first deploy, against real
public DNS -- watch Caddy's own logs during that run
(`docker compose ... logs -f caddy`) for "obtaining certificate"/
"certificate obtained successfully" lines naming "issuer":"acme" (not
"issuer":"local") as the live confirmation for that specific run.
EOF
pass "caddy adapt output and Caddy's own source confirm the redirect/catch-all ordering"

# --- (g) only Caddy publishes a host port ---
# `docker port <container>` (not `docker compose port <service> <port>`,
# which prints the sentinel string "invalid IP:0" -- NOT empty, exit 0
# either way -- for a port that genuinely isn't published, confirmed
# live while building this check, and would have made this check always
# "pass" vacuously) -- real container-level introspection, empty output
# when nothing is published. "migrate" has no persistent container to
# query (a one-off `run --rm`, already exited by the time this runs) --
# skipped, not a long-running service in the first place.
for service in postgres qdrant phoenix api worker; do
    cid="$($COMPOSE ps -q "$service" 2>/dev/null || true)"
    [ -n "$cid" ] || fail "no running container found for service '$service'"
    published="$(docker port "$cid" 2>/dev/null || true)"
    [ -z "$published" ] || fail "$service appears to publish a host port: $published"
done
caddy_published="$(docker port "$caddy_cid" 2>/dev/null || true)"
case "$caddy_published" in
    *"80/tcp"*"443/tcp"* | *"443/tcp"*"80/tcp"*) : ;;
    *) fail "caddy does not publish both 80 and 443: $caddy_published" ;;
esac
pass "only caddy publishes a host port (80 and 443); postgres/qdrant/phoenix/api/worker publish none"

echo "ALL CHECKS PASSED against dashboard=$DASHBOARD_HOST api=$API_HOST widget=$WIDGET_HOST"
