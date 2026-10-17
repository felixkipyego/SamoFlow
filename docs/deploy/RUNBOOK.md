# Deploy runbook

Step 7.3 prep (ops track, 2026-10-15). Written for Felix, operating the
single production server docs/SPEC.md §14 describes. Every step below
has been validated locally (Caddyfile through the official Caddy image,
`docker compose config` against the prod override, a full live
backup/restore drill) except provisioning the real server itself, the
real DNS/Postmark records, and the real first deploy -- those need a
real server and real accounts, neither of which exists in this
environment. **Prep done; final verification owned by whoever runs 7.3/
7.4 on the real server** (docs/DECISIONS_REGISTER.md).

## 1. Server provisioning

Target: Ubuntu 24.04 LTS, the ~4 vCPU/8GB starting size (docs/SPEC.md
§17, an estimate to re-measure at the real load test, Step 7.1).

1. Create the server with your chosen provider (kickoff decision #5,
   D-113-hosting-provider-agnostic: any Ubuntu LTS VPS, no
   provider-specific APIs or services, ever -- so these steps are the
   same regardless of which provider you picked). Note its static IP --
   docs/SPEC.md §14 names this as the fixed outbound IP given to tenants
   for their own database firewalls (relevant once a tenant configures a
   `database`-type source; disabled for the pilot, D-pilot-dbsync-disable).

2. **Non-root deploy user, SSH keys only:**
   ```
   adduser deploy
   usermod -aG sudo deploy
   rsync --archive --chown=deploy:deploy ~/.ssh /home/deploy
   ```
   Then in `/etc/ssh/sshd_config`: `PasswordAuthentication no`,
   `PermitRootLogin no`. `systemctl reload sshd`. Confirm you can log in
   as `deploy` with your key BEFORE closing the root session.

3. **Firewall (ufw):**
   ```
   ufw default deny incoming
   ufw default allow outgoing
   ufw allow 22/tcp
   ufw allow 80/tcp
   ufw allow 443/tcp
   ufw enable
   ```
   Nothing else is ever opened: postgres/qdrant/phoenix/api all stay off
   the public interface by construction (deploy/docker-compose.prod.yml's
   own `!reset []` ports, verified live against the real `docker compose
   config` merge -- see that file's own header comment).

4. **Unattended upgrades** (security patches without a manual login):
   ```
   apt-get update && apt-get install -y unattended-upgrades
   dpkg-reconfigure --priority=low unattended-upgrades
   ```

5. **Docker install** (official convenience script, the standard,
   documented mechanism -- rule 11):
   ```
   curl -fsSL https://get.docker.com | sh
   usermod -aG docker deploy
   ```
   Log out and back in as `deploy` before the group membership takes
   effect. Confirm with `docker compose version` (v2 plugin, not the
   standalone v1 `docker-compose` binary -- every command in this
   runbook and every compose file in this repo assumes v2's `docker
   compose` subcommand form).

## 2. DNS records

All under your chosen root domain (kickoff decision #6,
D-113-domains-softwareske: `softwareske.com`). Replace
`<server-ip>` with the static IP from step 1.

| Record | Type | Value |
|---|---|---|
| `app.softwareske.com` | A | `<server-ip>` |
| `api.softwareske.com` | A | `<server-ip>` |
| `widget.softwareske.com` | A | `<server-ip>` |

These three names are never hardcoded anywhere in this repo --
`deploy/Caddyfile` reads them from `DASHBOARD_HOSTNAME`/
`API_HOSTNAME`/`WIDGET_HOSTNAME` (kickoff decision #6's own standing
rule). If you use different subdomains, change `.env`, not the
Caddyfile.

## 3. Postmark DNS for notify.softwareske.com

Kickoff decision #4 (D-113-email-postmark): Postmark's SMTP relay, sent
from a DEDICATED sending subdomain -- isolating the main domain's own
reputation from the transactional notification stream's bounce/complaint
rate (PROJECT_SPEC.md's own Phase 5-7 decision log entry, same
reasoning not restated here).

1. In the Postmark dashboard, add a Sender Signature / Domain for
   `notify.softwareske.com`. Postmark generates the exact DKIM record
   value for your account -- copy it, don't guess it.
2. Add these DNS records under `notify.softwareske.com`:

   | Record | Type | Value |
   |---|---|---|
   | `notify.softwareske.com` | MX | (Postmark's bounce-handling value, if using Postmark's inbound -- not required for outbound-only, v1's own scope per docs/SPEC.md §14: "v1 records synchronous SMTP results only") |
   | `pm-bounces.notify.softwareske.com` | CNAME | (Postmark's own value, shown in their dashboard) |
   | `<postmark-dkim-selector>._domainkey.notify.softwareske.com` | TXT | (Postmark's own DKIM public key, shown in their dashboard) |
   | `notify.softwareske.com` | TXT (SPF) | `v=spf1 include:spf.mtasv.net ~all` |
   | `_dmarc.notify.softwareske.com` | TXT (DMARC) | `v=DMARC1; p=quarantine; rua=mailto:<your-report-address>` |

3. **Reverse DNS** on the server's own outbound IP (docs/SPEC.md §14's
   own named requirement) -- set via your hosting provider's control
   panel (not DNS you control), pointing the IP back at
   `notify.softwareske.com`.
4. Confirm in Postmark's dashboard that the domain shows "Verified" for
   both DKIM and Return-Path before sending anything real.
5. Set `SMTP_HOST`/`SMTP_PORT`/`SMTP_USERNAME`/`SMTP_PASSWORD` in `.env`
   to Postmark's real SMTP relay values (`smtp.postmarkapp.com`, port
   `587`, your Server Token as both username and password -- Postmark's
   own convention). `SMTP_FROM_ADDRESS` is an address under
   `notify.softwareske.com`, e.g. `alerts@notify.softwareske.com`.

## 4. Environment secrets

Copy `.env.example` to `.env` on the server (never commit `.env`) and
fill in every value. **[SECURITY] L1 (out-of-band Phase 7 batch,
2026-10-17): this is no longer a manual check.** Felix chose to keep
secrets in `.env` rather than move to Docker secrets
(D-7decided-env-secrets-kept), on condition of two real, built, enforced
mechanisms:

1. **`get_settings()` itself now refuses to start** under
   `APP_ENV=production` if any secret-class variable is missing, is one
   of the known placeholder/test values (`change-me`, `test-...`,
   `smoke-...`, `placeholder-...`), or is shorter than its own stated
   minimum -- `backend/app/secrets_policy.py`, wired into `api`/
   `worker`/`migrate` alike (they all construct `Settings` only through
   `get_settings()`). This is a real, code-enforced gate now, not a
   promise to eventually build one.
2. **Run `./ops/deploy/preflight.sh` before every deploy** (first deploy
   and every later one) -- it fails loud, before `docker compose up`
   ever runs, if: `.env`'s own permissions are looser than `600`; `.env`
   is not owned by the user running the script; `.env` is tracked by
   git, or sits untracked-but-unignored in this checkout's own working
   tree (a careless `git add .`/`git add -A` would commit it); or any
   secret-class variable is missing/placeholder/weak, reusing the exact
   same policy `get_settings()` enforces -- one source of truth, never a
   second, independently-maintained copy of the same rules. It never
   prints a secret value, only variable names.

The table below flags which variables must never be a placeholder in
production -- both mechanisms above now ENFORCE this for every
secret-class variable automatically; the table stays as a human-
readable reference for what each one is and where to generate a real
value, not as the only thing standing between a placeholder and a real
deployment. Confirmed live: `Settings.jwt_signing_key`/
`db_connection_encryption_key`'s own `min_length=32` constraint does NOT
by itself reject the committed `.env.example` placeholder value
(`"change-me-change-me-change-me-change-me"` is 40 characters, well
over 32) -- a length check alone is not a placeholder check, which is
exactly why `secrets_policy.py` checks both, separately.

| Variable | Must never be a placeholder in production? | Notes |
|---|---|---|
| `DATABASE_URL`, `POSTGRES_PASSWORD` | Yes | Generate a real random password. |
| `QDRANT_API_KEY`, `QDRANT_READ_ONLY_API_KEY` | Yes | Two DIFFERENT real random values -- confirmed live (Task 3.1.b) that Qdrant itself rejects a write through the read-only key with a real 403, so these must genuinely differ. |
| `JWT_SIGNING_KEY` | Yes | `openssl rand -base64 32`. |
| `DB_CONNECTION_ENCRYPTION_KEY` | Yes | `openssl rand -base64 32`. Loses access to every `db_connections` row if ever changed without a re-encrypt -- see §6 below, this is exactly what the restore drill proves survives a restore. |
| `OPENAI_API_KEY` | Yes | Real key from platform.openai.com. |
| `ANTHROPIC_API_KEY` | No (optional) | Leave empty unless the secondary chat-model fallback (`chat_model_secondary_verified`) has been manually verified per its own Settings field comment. |
| `JWT_SIGNING_KEY_PREVIOUS` | No (optional) | Only set during a key rotation. |
| `SMTP_*` | Yes, if set at all | Real Postmark values (§3 above) or leave entirely empty to run with email disabled (a real, supported state -- never a half-placeholder). |
| `BACKUP_AGE_PUBLIC_KEY` | Yes | Generate with `age-keygen` (§5 below) -- this one is NOT secret, safe to commit to `.env`. |
| `S3_BACKUP_BUCKET`, `S3_BACKUP_ENDPOINT_URL`, `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY` | Yes | Real S3-compatible provider credentials (kickoff decision #8). |
| `CADDY_ACME_EMAIL` | Yes | A real mailbox you read -- Let's Encrypt emails it about certificate problems. |
| `DASHBOARD_HOSTNAME`, `API_HOSTNAME`, `WIDGET_HOSTNAME` | Yes | §2 above. |
| `MONITORING_ALERT_EMAIL` | Yes, if monitoring alerts matter | §7 below. |

**Never in `.env`, stored SEPARATELY** (this task's own explicit
requirement, proven live in §6 below):
- `BACKUP_AGE_SECRET_KEY` -- the age PRIVATE key. Store it in a password
  manager or a printed/offline copy, split from the backup bucket
  entirely. Needed only at restore-drill or real-restore time, supplied
  on the command line (`BACKUP_AGE_SECRET_KEY=... ./ops/backup/
  run-restore-drill.sh`), never written to disk on the server outside
  that one process's environment.
- A second copy of `DB_CONNECTION_ENCRYPTION_KEY` is NOT needed
  separately (it already lives in `.env`, which is itself separate from
  the backup bucket) -- but the backup bucket's own credentials
  (`AWS_ACCESS_KEY_ID`/`AWS_SECRET_ACCESS_KEY`) and the age private key
  above must never be stored in the SAME place as each other, or a
  single compromised location defeats the whole point of encrypting
  backups at all.

**Full-history secret scan** (this repo is public): `./ops/security/scan-secrets.sh`
runs a gitleaks full-history scan via its own official container image,
pinned to an exact version and digest (never `:latest`, no new repo
dependency -- nothing is installed, Docker pulls the image fresh each
run). Already-reviewed false positives are suppressed via the committed
`.gitleaksignore` (repo root), each entry carrying its own one-line
reason. Run this as part of Step 7.2's own full security run, and
whenever in doubt before a push -- it exits non-zero on anything not
already in that file. If it ever finds a REAL credential: do NOT rewrite
history (this is a shared, public repo); rotate the exposed credential
immediately at its real provider, then follow up with whether history
rewriting is still worth it given the credential is already rotated.

## 5. First deploy

```
cd /opt && git clone <this repo> samoflow && cd samoflow
# .env filled in per §4 above, placed at the repo root.

# [SECURITY] L1, PART B (2026-10-17): run before every deploy, first and
# every later one -- fails loud before anything below it runs if .env's
# permissions/ownership/git-exposure are wrong, or any secret-class
# variable is missing/placeholder/weak.
chmod 600 .env
./ops/deploy/preflight.sh

# Pull/build the backend image, run migrations (alembic upgrade head,
# then ensure_collection() and the checkpointer setup -- entrypoint.sh's
# own "migrate" mode, Task 1.1.h/2.1.g/3.2.b):
docker compose --env-file .env -f deploy/docker-compose.yml -f deploy/docker-compose.prod.yml \
  build
docker compose --env-file .env -f deploy/docker-compose.yml -f deploy/docker-compose.prod.yml \
  run --rm migrate

# Build the dashboard and widget (each track's own tooling, not this
# compose file's job -- Vite/shadcn for dashboard/, esbuild for widget/):
(cd dashboard && npm ci && npm run build)
(cd widget && npm ci && npm run build)

# Copy the build output flat into the two host directories Caddy serves
# from (DASHBOARD_DIST_DIR/WIDGET_DIST_DIR, §4 above). The widget's
# four files land in ONE flat directory, never nested: the loader
# resolves "chat.js"/"widget.css" as SIBLINGS of wherever widget.js
# itself loaded from (widget/loader/src/config.js's own
# resolveSiblingUrl()), and chat.js resolves "chat.css" the same way
# relative to ITS OWN <script> src (widget/chat/src/index.js) --
# confirmed by reading both files, not assumed.
mkdir -p /srv/samoflow/dashboard /srv/samoflow/widget
cp -r dashboard/dist/. /srv/samoflow/dashboard/
cp widget/dist/widget.js widget/dist/widget.css /srv/samoflow/widget/
cp widget/chat/dist/chat.js widget/chat/dist/chat.css /srv/samoflow/widget/

# Seed the two pilot plans (kickoff decision #3) and the one pilot
# owner account (entrypoint.sh's own "admin"/"seed-owner" modes --
# app/dashboard/admin_cli.py / app/dashboard/seed_cli.py):
docker compose --env-file .env -f deploy/docker-compose.yml -f deploy/docker-compose.prod.yml \
  run --rm migrate admin seed-plans
docker compose --env-file .env -f deploy/docker-compose.yml -f deploy/docker-compose.prod.yml \
  run --rm migrate seed-owner --email you@example.com --tenant-name "Pilot Tenant" --platform-admin
# (password prompted interactively -- never a CLI argument, never logged)

# Everything up, including Caddy:
docker compose --env-file .env -f deploy/docker-compose.yml -f deploy/docker-compose.prod.yml \
  up -d
```

Confirm: `curl -I https://api.softwareske.com/health` returns `200` with
no `Server:` header (backend/smoke-image.sh already proves this at the
image level); `https://app.softwareske.com` loads the dashboard login.

**Then run `ops/deploy/smoke-prod-stack.sh`** -- the real, end-to-end
proof that this specific first deploy actually works, not just that the
process started. Found and fixed the real reason this step exists: an
advisor code review caught a production-breaking bug (every dashboard
SPA `/api/*` call 400'd -- `app/main.py`'s `TrustedHostMiddleware` only
allowed `API_HOSTNAME`, but Caddy's own dashboard site forwards the
DASHBOARD host header on every one of those calls) that NOTHING had
ever caught, because nothing had exercised a real dashboard login
through the real Caddy, in `APP_ENV=production`, end to end, before.
This script is that exercise, every time:

```
ops/deploy/smoke-prod-stack.sh app.softwareske.com api.softwareske.com \
  widget.softwareske.com <this server's real public IP>
```

Checks, against the REAL running stack: the dashboard SPA loads with
its strict CSP; a full login (CSRF fetch, login, an authenticated
`/api` call, logout) with `__Host-session`/`__Host-csrf` carrying
exactly the cookie attributes designed (`Secure`/`HttpOnly`/
`SameSite=Strict` on the session cookie, `Secure`/`SameSite=Strict`
but NOT `HttpOnly` on the CSRF one); the widget's static assets load;
a real widget `/session` and a real, genuinely-streamed (not buffered)
`/chat` turn; `Host: evil.com`/an IP literal/a missing Host are all
rejected, over both `http` and `https`; every configured hostname's
plain `http://` redirects to `https://` (and that Caddy's own
automatic-HTTPS redirect is never swallowed by the Host-validation
catch-all -- the script's own output explains and cites exactly how,
Caddy 2.10.2 source and docs); only Caddy publishes a host port.
Seeds one throwaway, uniquely-named smoke-test tenant/site-key/
dashboard-owner into the real database each run (via the same
`admin`/`seed-owner` commands above) -- there is no CLI command to
delete one, so these rows are left behind; harmless, and this is
expected to run against a pristine database, before `seed-owner`
creates the real pilot tenant above.

**Locally** (this exact script, unchanged, run against a scratch stack
instead of the real server -- useful before every real deploy, not
only once): bring the stack up with a Caddyfile that differs from the
real `deploy/Caddyfile` ONLY by adding `local_certs` to its global
options block (Caddy's own internal CA -- instant issuance, no real
ACME, no real DNS needed), mounted via a throwaway Compose override
(never edit the real file for this); extract that CA
(`docker cp <caddy container>:/data/caddy/pki/authorities/local/
root.crt .`) and pass it as the script's own 5th argument so curl
verifies the real TLS handshake against a CA it is told to trust --
**never** `curl -k`, which would also silently accept the WRONG
certificate. `SMOKE_ENV_FILE`/`SMOKE_PROJECT`/`SMOKE_EXTRA_COMPOSE_ARGS`
(read from the environment, see the script's own header comment) point
it at that scratch stack instead of the real tracked `.env`/compose
files -- leave all three unset for the real run above.

**Never run more than one `api` process** (never `docker compose up
--scale api=2`, never add `--workers` to entrypoint.sh's own uvicorn
command, never a `deploy.replicas` entry on the `api` service) without
FIRST moving `chat_active_streams`/`chat_stop_requests`
(`backend/app/chat/routes.py`) -- and re-checking every other
in-process mechanism this project documents the same way (the usage
counters, the various rate limiters, `StatusCache`) -- to shared state
(Postgres, keyed by vid/lease_token, no new service needed). A second
process can never see the first process's own in-process state: a
visitor's own `POST /chat/stop` landing on the WRONG process would
silently do nothing, and a visitor could be double-counted or
rate-limited inconsistently depending on which process happened to
serve which request. `backend/tests/test_single_api_process_guard.py`
fails the build the moment entrypoint.sh or either compose file tries
to raise this above one process on its own, but an operator-issued
`--scale` flag at the command line is invisible to that guard --
reading this paragraph is the only thing that catches that case.

### Admin CLI commands (after the first deploy, as pilot tenants onboard)

All run the same way, via the `migrate` service's `admin` mode
(`app/dashboard/admin_cli.py`):

```
docker compose --env-file .env -f deploy/docker-compose.yml -f deploy/docker-compose.prod.yml \
  run --rm migrate admin create-tenant --name "Acme Co"

docker compose --env-file .env -f deploy/docker-compose.yml -f deploy/docker-compose.prod.yml \
  run --rm migrate admin issue-site-key \
  --tenant-id <uuid> --allowed-origin https://acme.example.com --status live
```

`issue-site-key` prints the real `pk_live_...` key (`generate_site_key()`,
D-6.1-site-key-generator) -- this is the key value for §8's embed
snippet below. `assign-plan`/`revoke-site-key`/`revoke-domain`/
`seed-plans` follow the same `admin <subcommand> --flag value` shape;
run `docker compose ... run --rm migrate admin --help` for the full
argument list rather than duplicating argparse's own help text here.

## 6. Backups and restore drill

Nightly, via cron as the `deploy` user (`crontab -e`):

```
0 3 * * * cd /opt/samoflow && docker compose --env-file .env -f deploy/docker-compose.yml -f ops/backup/docker-compose.yml run --rm backup ./backup.sh >> /var/log/samoflow-backup.log 2>&1
```

This runs `ops/backup/backup.sh` inside `ops/backup/Dockerfile`'s own
image, on the same Compose network as the real `postgres`/`qdrant` --
reached by service name, zero published ports needed anywhere. Each
night: a `pg_dump`, a Qdrant snapshot of `knowledge_chunks`, both
age-encrypted (to `BACKUP_AGE_PUBLIC_KEY` only -- the encrypting side
never sees the private key) and uploaded to `S3_BACKUP_BUCKET`, with
objects older than `S3_BACKUP_RETENTION_DAYS` deleted afterward.

**Restore drill** (docs/SPEC.md §14's own "a restore drill before real
tenants go live", and this task's own instruction to run it now and
prove it): `ops/backup/run-restore-drill.sh` is the host-level wrapper
-- the only things it needs on the host are Docker and the Compose
plugin; every real tool the drill uses (psql, age, the AWS CLI, curl,
jq) lives inside the same image, never on the host.

```
BACKUP_AGE_SECRET_KEY="$(cat /path/to/separate/secrets/age-key.txt)" \
DB_CONNECTION_ENCRYPTION_KEY="$(grep DB_CONNECTION_ENCRYPTION_KEY /path/to/.env | cut -d= -f2-)" \
  ./ops/backup/run-restore-drill.sh
```

It downloads the most recent backup set, restores it into a disposable
scratch Postgres/Qdrant pair (never the real ones), and PROVES -- not
merely checks the files exist -- that every restored `db_connections`
row decrypts with the separately-supplied key (or, when the table has
no rows yet, which is the real pilot-launch state since
database-sync sources are disabled per D-pilot-dbsync-disable, that a
canary value round-trips through the restored `pgcrypto` extension with
that same key), and that the restored Qdrant collection has the expected
point count. The scratch stack is torn down unconditionally, success or
failure.

**Run this drill locally, right now, with a real end-to-end proof --
see this task's own report for the full pasted output.** Run it again
for real on the production server before any real tenant goes live
(this is the Step 7.3/7.4 final verification this prep work is owed
against), and periodically afterward (e.g. monthly) -- a backup nobody
has ever restored is a hope, not a backup.

## 7. Monitoring

**Disk/memory** (`ops/monitoring/check_resources.py`, stdlib-only, no
backend import by design): via cron as `deploy`, e.g. every 5 minutes --

```
*/5 * * * * /usr/bin/python3 /opt/samoflow/ops/monitoring/check_resources.py >> /var/log/samoflow-monitoring.log 2>&1
```

Reads `SMTP_*` (§3/§4 above) and `MONITORING_*` (`.env.example`) from
the environment -- run it with `env $(cat .env | xargs) python3
ops/monitoring/check_resources.py` or source `.env` first, same as any
other cron job on this host. Alerts by email when disk or memory usage
crosses its threshold; does nothing (exit 0) otherwise.

**Uptime checker** (docs/SPEC.md §14, kickoff decision #7: a hosted
checker, not a self-run Prometheus/Grafana stack -- one server serving
one pilot tenant does not yet justify operating a second monitoring
stack):

1. Sign up for a hosted uptime checker (UptimeRobot, Better Uptime, or
   similar -- any of them; this repo has no dependency on which one).
2. Add an HTTPS monitor against `https://api.softwareske.com/health`,
   checked every 1-5 minutes, alerting by email (and SMS/phone if the
   provider offers it on your plan) on a failure.
3. Add a second monitor against `https://app.softwareske.com` (the
   dashboard SPA) -- a different failure mode than the API (e.g. Caddy
   up but the dashboard's own static files missing) that the API-only
   monitor above would not catch.
4. The §9 usage-spike alert (docs/SPEC.md) is a separate, already-built
   application-level mechanism (`handle_usage_alert_sweep()`,
   `app/ingest/job_handlers.py`) -- not part of this uptime checker, not
   duplicated here.

## 8. The embed snippet a tenant pastes

Printed by `issue-site-key` above (the real `pk_live_...` value); the
shape itself comes from `widget/loader/src/config.js`'s own documented
attributes, confirmed by reading that file, not guessed:

```html
<script
  src="https://widget.softwareske.com/widget.js"
  data-site-key="pk_live_..."
  async
></script>
```

## 9. Phoenix access over an SSH tunnel

Phoenix has NO published port in production (`deploy/docker-compose.
prod.yml`'s own `!reset []`, verified live -- §1's firewall table above
has no row for it, by construction, not by omission). Linux Docker
routes the HOST's own network stack directly to any container's IP on a
user-defined bridge network, even with zero `ports:` entries -- this is
what makes an SSH tunnel still work with no published port at all:

```
ssh deploy@<server-ip>
# on the server:
docker inspect -f '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}' \
  $(docker compose --env-file .env -f deploy/docker-compose.yml -f deploy/docker-compose.prod.yml ps -q phoenix)
# note the printed IP, e.g. 172.20.0.5
exit

# from your own machine:
ssh -L 6006:172.20.0.5:6006 deploy@<server-ip>
# then open http://127.0.0.1:6006 in a local browser
```

The container's internal IP can change on a restart -- re-run the
`docker inspect` line if the tunnel stops connecting. The same technique
reaches `postgres`/`qdrant`/`api` directly for debugging, if ever
needed, without adding a published port for any of them.

## 10. Rollback

```
docker compose --env-file .env -f deploy/docker-compose.yml -f deploy/docker-compose.prod.yml \
  down
git checkout <previous-good-commit>
docker compose --env-file .env -f deploy/docker-compose.yml -f deploy/docker-compose.prod.yml \
  build
docker compose --env-file .env -f deploy/docker-compose.yml -f deploy/docker-compose.prod.yml \
  up -d
```

A migration rollback is NOT automatic -- `alembic downgrade` only if the
specific migration you're reverting past is confirmed safe to reverse
(check its own `downgrade()` body first; several early migrations in
this project are additive-only and were never written with a real
downgrade path in mind). If in doubt, restore from the most recent
nightly backup (§6) into a fresh stack instead of downgrading in place.

## 11. Local demo

`ops/demo/start.sh` brings up a full local stack -- postgres/qdrant/api/
worker (Docker, its own Compose project `samoflow-demo`, distinct ports
from any other stack already running), the real dashboard dev server,
and a static page embedding the real widget -- with a seeded demo
tenant, a Growth-plan assignment, a site key, support details, and the
golden-set pages uploaded as content. Idempotent: rerunning it resumes
the same seeded tenant/content rather than starting from scratch, except
the owner's password, which is ALWAYS freshly rotated and printed once
(never persisted anywhere, including by the script itself).

```
./ops/demo/start.sh
# ... prints the dashboard URL, login email, a fresh password, and the
# widget test page URL at the end ...
./ops/demo/stop.sh   # stops containers + the two dev servers; data volume kept
```

Needs `.env` at the repo root with a real `OPENAI_API_KEY` (chat answers
and embeddings both need it); the script creates `.env` from
`.env.example` and exits with instructions if one didn't already exist.
Uses Chrome for the widget test page (other browsers are not verified
against the widget's shadow-DOM/CSP requirements for this quick local
check).

Never touches any other Compose project or container on this machine
(`COMPOSE_PROJECT_NAME=samoflow-demo`, distinct host ports for every
published service) -- safe to run alongside a track worktree's own
`make up`.

**Known limitation, by design, not a defect**: a citation badge never
appears on an answer in this local demo. docs/SPEC.md §6.3 restricts
citations to domain-verified web pages ("Sources shown... for web pages
only. No labels for uploads or database content") -- `app/agent/
graph.py`'s own `_build_citations()` drops any chunk whose `source_url`
is `None`, which every chunk ingested through the upload path (`POST
/api/v1/dashboard/sources/upload`, this demo's only way to get content
in without a real, publicly-resolvable domain) always is. Domain
verification itself cannot happen against `127.0.0.1` either --
`app/ingest/safe_fetch.py`'s SSRF guard correctly rejects loopback/
private destinations, by design, with no dev-mode bypass. Answers are
still real, grounded, and correctly cited IN SUBSTANCE (confirmed live
against this demo's own content); only the citation UI badge, which
needs a real crawled/verified domain, cannot be demonstrated locally.
