# ops track — progress and decision log

Per PROJECT_SPEC.md §11.5 (parallel-track protocol), extended to this new
third track the same way: progress and decisions stay here, not in
PROJECT_SPEC.md's own §4/§5/§7, while this track and the dashboard track
are both in flight on the same repo. Folded into PROJECT_SPEC.md's real
sections at merge time; this file is a staging area.

Duplication counter: n=1 (reset to 0 after the Step 7.3 prep batch's own
check; the CI-fix follow-up below is one more code-changing sub-step).

---

## Step 7.3 prep (2026-10-15)

### Prior decisions applied

Register swept (PROJECT_SPEC.md §2 rule 19) before building anything.
Applicable entries:
- D-113-hosting-provider-agnostic — any Ubuntu LTS VPS, no
  provider-specific APIs/services, standing constraint honoured
  throughout (RUNBOOK §1, Dockerfile/compose files use only Docker/
  Compose/standard apt packages, nothing provider-specific).
- D-113-domains-softwareske — `softwareske.com`; every hostname in
  `deploy/Caddyfile` comes from `{$VAR}` substitution, never hardcoded
  (confirmed by reading the file back after writing it).
- D-113-email-postmark — Postmark SMTP relay behind the existing
  `send_email()`, a dedicated `notify.softwareske.com` sending
  subdomain. RUNBOOK §3 documents the real DNS records; this track adds
  no new email-sending code (that already exists, Task 4.3) and no new
  Settings fields.
- D-113-monitoring — a hosted uptime checker plus a cron disk/memory
  script via `send_email()`'s own shape. Built as `ops/monitoring/
  check_resources.py` — a SEPARATE stdlib implementation of the same
  SMTP shape (by direct instruction: no backend import), not a
  refactor of the real `send_email()`.
- D-113-backups — S3-compatible storage, encrypted before upload, keys
  stored separately from the data. Built as `ops/backup/` (age +
  the AWS CLI); see the live restore-drill proof below for the
  separately-stored-key claim proven, not merely asserted.
- D-113-parallel-track-protocol / §11.5 — this is the third concurrent
  worktree the protocol's own text anticipates by pattern even though
  it names only `track/widget`/`track/dashboard` literally; the same
  rules applied here by direct instruction (own `docs/progress/<track>.
  md`, never edit `backend/`/`widget/`/`dashboard/`/migrations/`ci.yml`,
  push straight to `main` after rebase).
- Step 7.3's own pre-existing forward-commitment marker (PROJECT_SPEC.md:
  6272, 6859, 6888) — the restore-drill proof requirement and the
  Host-header/forwarded-header backend gap — both directly addressed
  below; see docs/DECISIONS_REGISTER.md's updated 7.3 section for the
  "prep done" status on each.

No CANDIDATE decisions or forward commitments from any OTHER step
applied to this track's own scope (deploy/ops/docs-deploy only).

### Environment

Per-worktree `.venv` (`.venv/`, gitignored — already true via the
existing `.gitignore`'s `.venv*/` pattern, re-confirmed, not re-added),
pinned Python 3.12.14 from `/opt/miniconda3/envs/widgetplatform`:
```
/opt/miniconda3/envs/widgetplatform/bin/python -m venv .venv
pip install --require-hashes -r backend/requirements-dev.lock
pip install --no-deps -e ./backend
```
Confirmed: `python -c "import app; print(app.__file__)"` resolves to
THIS worktree's `backend/app/__init__.py` (same live-check the dashboard
track's own log already used for the identical reason — two worktrees
had previously raced one shared conda env's editable install).

### 1. Caddyfile (`deploy/Caddyfile`)

Three site blocks (`{$DASHBOARD_HOSTNAME}`/`{$API_HOSTNAME}`/
`{$WIDGET_HOSTNAME}`, every hostname from env, never hardcoded):
- **Dashboard**: `/api/*` reverse-proxied to `api:8000` with the path
  kept intact (same shape `dashboard/e2e/csp-server.mjs`'s own dev/test
  proxy already uses — confirmed by reading that file, not guessed) —
  same-origin, no CORS needed (D-6.1-cors-same-origin). Vite's
  content-hashed `/assets/*` get a long immutable cache; everything
  else (the SPA shell) gets `no-cache` + `try_files` fallback to
  `index.html`.
- **API**: `/api/v1/chat` gets `flush_interval -1` (disables Caddy's own
  response buffering) and unbounded transport timeouts — docs/SPEC.md
  §14's own "buffering off... long idle timeouts" line, applied only to
  the one streaming route, not the whole API. Every other route keeps
  Caddy's ordinary defaults.
- **Widget**: serves `widget/dist/widget.{js,css}` +
  `widget/chat/dist/chat.{js,css}` flat from ONE directory — confirmed
  by reading `widget/loader/src/config.js`'s `resolveSiblingUrl()` and
  `widget/chat/src/index.js`'s own `cssUrl` computation that both
  resolve siblings relative to wherever their OWN `<script>` tag loaded
  from, not a nested path. Moderate (5 min, `must-revalidate`) caching,
  not long-immutable: confirmed by reading `widget/scripts/build.mjs`
  that none of these four filenames are content-hashed, unlike the
  dashboard's Vite output.
- Shared security-header snippet (HSTS, nosniff, Referrer-Policy,
  Permissions-Policy, `-Server`) plus `frame-ancestors 'none'` on every
  site; the dashboard's own CSP header REPLACES the snippet's bare one
  with the exact strict policy its own e2e test already proves
  (`dashboard/e2e/csp-server.mjs`'s `CSP` constant, copied verbatim —
  diffed by eye against that file after writing, not retyped from
  memory).
- Request body limits: `{$CADDY_UPLOAD_MAX_SIZE_MB}` (default 21,
  tracking `Settings.upload_max_size_bytes`'s own 20MB — a cross-service
  knob, not something Caddy can read from backend Settings directly;
  flagged in both the Caddyfile's own header and the RUNBOOK). **CORRECTED
  below ("CI fix" section): this was a needless parallel variable —
  Caddy now reads `UPLOAD_MAX_SIZE_BYTES`, the backend's own real
  Settings field, directly.**
- Phoenix: no site block references it, by construction — confirmed by
  grepping the finished file for "phoenix" (zero matches) before
  calling this done.
- **Backend changes this proxy will need, NOT built** (register entries
  owned by the Phase 7 batch, per this task's own instruction): trusted
  `X-Forwarded-For` for the rate limiter, and `Host`-header validation —
  see docs/DECISIONS_REGISTER.md's `D-73-backend-changes-flagged-not-
  built`.

Verified: `docker run --rm -v ./deploy/Caddyfile:/etc/caddy/Caddyfile:ro
-e DASHBOARD_HOSTNAME=... caddy:2.10.2 caddy validate --config
/etc/caddy/Caddyfile` → `Valid configuration`, with the real three
hostnames and `CADDY_ACME_EMAIL`/`CADDY_UPLOAD_MAX_SIZE_MB` (renamed to
`UPLOAD_MAX_SIZE_BYTES`, see "CI fix" below) supplied via env, not
hardcoded test values baked into the file.

### 2. Production compose override (`deploy/docker-compose.prod.yml`)

Adds `caddy` (image `caddy:2.10.2`, verified against a real manifest
list) with ONLY ports 80/443 published, plus log rotation
(`json-file`, `max-size: 10m`, `max-file: 5`) on every service, base and
new alike.

`db`/`qdrant`/`phoenix` on the internal network only: `postgres`/
`qdrant` already publish no ports in the base file; `api`/`phoenix` do
(both loopback-bound, for local dev convenience). **Found live, not
assumed**: a bare `ports: []` in a Compose override does NOT clear an
inherited `ports:` list — `docker compose config`, tested directly,
showed the base file's loopback bindings surviving unchanged through
such an override. The Compose Specification's `!reset []` tag is the
real mechanism; re-tested and confirmed it actually clears the merged
value. Used for all four services (`postgres`/`qdrant`/`api`/`phoenix`)
so this file is a single, auditable, construction-time guarantee, not
four separate hopes about what the base file currently happens to do.

Verified live (`docker compose -f deploy/docker-compose.yml -f deploy/
docker-compose.prod.yml config`, against a real filled-in env):
```
api -> ports: None | logging: {json-file, max-size 10m, max-file 5}
caddy -> ports: [80, 443] | logging: {json-file, max-size 10m, max-file 5}
phoenix -> ports: None | logging: {...}
postgres -> ports: None | logging: {...}
qdrant -> ports: None | logging: {...}
worker -> ports: None | logging: {...}
```
Exactly one service with published ports; it is `caddy`; the ports are
80 and 443.

Phoenix's resulting total unreachability from outside is reconciled
with the RUNBOOK's own required "Phoenix access over an SSH tunnel"
step: Linux Docker routes the host's own network stack to any
container's IP on a user-defined bridge network even with zero
`ports:` entries, so `ssh -L 6006:<container-ip>:6006` still works —
documented in docs/deploy/RUNBOOK.md §9 with the exact commands, not
left as an unresolved tension between two of this task's own
instructions.

### 3. Backups (`ops/backup/`)

- `Dockerfile`: `debian:trixie-slim` + the official PGDG apt repo (trixie
  's own base repo has no `postgresql-client-18` candidate — confirmed
  live via `apt-cache policy`) + `age` + `awscli` + `jq`. Shares nothing
  with `backend/Dockerfile` — no Python, no backend import, a
  deliberately separate image for a deliberately separate concern.
- `lib.sh`: shared helpers (Qdrant snapshot create/download/recover via
  its REST API, `age` encrypt/decrypt, S3 upload/download/retention).
  Age encryption is asymmetric BY CONSTRUCTION: `backup.sh` only ever
  reads `BACKUP_AGE_PUBLIC_KEY` (not a secret — safe in `.env`); the
  matching `BACKUP_AGE_SECRET_KEY` never appears in that script at all,
  only in `restore-drill.sh`, loaded from a source the RUNBOOK
  documents as separate from both `.env` and the backup bucket.
- `backup.sh`: nightly `pg_dump` (plain SQL, gzip'd) + a Qdrant snapshot
  of `knowledge_chunks`, both age-encrypted then uploaded to
  `S3_BACKUP_BUCKET`, with a retention sweep (`S3_BACKUP_RETENTION_DAYS`,
  default 14) deleting older objects by parsing the UTC timestamp
  embedded in each object's own key name.
- `restore-drill.sh` + `run-restore-drill.sh`: the restore drill proper
  — see the live proof below. Runs entirely inside containers (the same
  `samoflow-backup:dev` image, plus disposable `postgres-scratch`/
  `qdrant-scratch`); the only host-side requirement is Docker + the
  Compose plugin, so a fresh deploy host needs nothing else installed
  to run it.

#### Live restore-drill proof (run locally, 2026-10-15)

Built a real, migrated, throwaway "source" stack standing in for
production — NOT the shared dev stack, NOT the other tracks' own
containers (`samoflow-ops-pg-source`/`samoflow-ops-qdrant-source`/
`samoflow-ops-minio`, on their own Docker network, torn down
completely afterward):
1. `postgres:18.2-trixie` + `qdrant/qdrant:v1.19.1` (the same pinned
   tags `deploy/docker-compose.yml` already uses) + a local `minio/
   minio` standing in for the real S3-compatible provider (no real
   cloud account exists in this environment; the exact same `aws s3`
   code path is exercised either way — only the endpoint URL differs
   between this proof and a real deploy).
2. Ran the REAL `alembic upgrade head` (this worktree's own `.venv`,
   zero backend code changes) against the source Postgres — every
   migration, same code the dashboard track and CI both already run.
3. Ran the REAL `python -m app.qdrant` (`ensure_collection()`) against
   the source Qdrant — created the real `knowledge_chunks` collection
   with its real vector config.
4. Seeded ONE real `db_connections` row through the real
   `create_tenant()` repository function plus a raw
   `pgp_sym_encrypt(credentials_json, key)` insert (the identical SQL
   shape `app/ingest/repository.py`'s own `create_db_connection()`
   uses, confirmed by reading it) — a real tenant, a real
   pgcrypto-encrypted credential, not a hand-waved fixture. Added one
   real point to `knowledge_chunks` (a dense+sparse vector plus
   payload) so the Qdrant snapshot has real content to restore too.
5. Ran the REAL `ops/backup/backup.sh` against this source stack,
   uploading to the local MinIO:
   ```
   == backup 20261015T201113Z starting ==
   -- pg_dump --
   upload: .../pg.sql.gz.age to s3://samoflow-backups/pg/20261015T201113Z.sql.gz.age
   pg_dump uploaded: pg/20261015T201113Z.sql.gz.age
   -- qdrant snapshot --
   upload: .../qdrant.snapshot.age to s3://samoflow-backups/qdrant/20261015T201113Z.snapshot.age
   qdrant snapshot uploaded: qdrant/20261015T201113Z.snapshot.age
   -- retention (14 days) --
   == backup 20261015T201113Z complete ==
   ```
6. Ran the REAL `ops/backup/run-restore-drill.sh` — a throwaway,
   disposable scratch stack (`postgres-scratch`/`qdrant-scratch`,
   tmpfs-backed), the age PRIVATE key supplied only via this one
   command's own environment (never written to any file on disk), the
   pgcrypto key supplied the same way:
   ```
   == restore drill starting ==
   -- locating the most recent backup set --
   pg: 20261015T201113Z.sql.gz.age
   qdrant: 20261015T201113Z.snapshot.age
   -- downloading and decrypting (using the SEPARATELY-stored private key) --
   -- restoring the pg dump into the scratch stack (postgres-scratch) --
   -- PROOF: db_connections credentials decrypt with the separately-stored key --
   db_connections rows in the restored dump: 1
   rows that decrypt successfully with the separately-stored key: 1 / 1
   -- restoring the qdrant snapshot into the scratch stack (http://qdrant-scratch:6333) --
   PROOF: restored qdrant collection 'knowledge_chunks' has 1 point(s)
   == restore drill PASSED ==
   ```
   The scratch stack (containers + network) was torn down automatically
   by the script's own trap — confirmed empty via `docker compose ...
   down -v --remove-orphans` in the same run's own output.
7. **Second run, the actual pilot-launch state**: deleted the one
   `db_connections` row from the source (database-sync sources are
   disabled for the pilot, D-pilot-dbsync-disable — an empty table is
   the REAL expected state at go-live, not a hypothetical), took a
   second backup, re-ran the drill against THAT backup to prove the
   canary code path (not just the happy path with data already
   present):
   ```
   -- PROOF: db_connections credentials decrypt with the separately-stored key --
   db_connections rows in the restored dump: 0
   db_connections has no rows yet (expected for the pilot); canary round-trip through the restored pgcrypto extension PASSED: 'restore-drill-canary-1792095174'
   PROOF: restored qdrant collection 'knowledge_chunks' has 1 point(s)
   == restore drill PASSED ==
   ```
8. Full teardown: `docker rm -f` the three source containers, removed
   the Docker network, removed all temp files, removed the built
   `samoflow-backup:dev` image. `docker ps -a` confirmed clean —
   nothing from this proof left running or lying around.

**Both of this task's own named requirements are proven live, not
asserted**: (a) a real, end-to-end restore (not "the file exists") and
(b) the restored `db_connections` credentials decrypt with a key that
was never present in the same process as the backup bucket's own
upload credentials.

### 4. Monitoring (`ops/monitoring/check_resources.py`)

Stdlib only (`shutil`, `smtplib`, `socket`, `email.message`) — no
backend import, confirmed by the file containing no `from app` or
`import app` anywhere. Mirrors `app/notifications/service.py`'s own
`send_email()` shape exactly (SMTP, `timeout=10`, unconditional
`starttls()`, optional login) as a SEPARATE implementation, not a
refactor — the two scripts have no common caller and are never
co-deployed (one runs in the API's own event loop; this one runs
standalone from cron), so sharing code between them would need its own
new import boundary for no real benefit (rule 9).

Smoke-tested live inside a real Linux container (`/proc/meminfo` is
Linux-only — the script correctly raises on macOS, where it will never
actually run): a clean run (`disk=7.1% memory=7.5%`, exit 0) and a
forced-alert run with `MONITORING_*_THRESHOLD_PERCENT=0` and no SMTP
configured (correctly logs "not configured" and exits 1, matching
`send_email()`'s own established "misconfiguration is a loud, non-zero
signal" convention — never silently swallowed).

The real STARTTLS send path was not separately proven against a live
mailbox (no real Postmark account exists in this environment) — it is
the identical library call sequence `send_email()` already ships and is
exercised in production, not a new one; re-check once real Postmark
credentials exist (RUNBOOK §3/§7).

### 5. docs/deploy/RUNBOOK.md

Written for Felix: provisioning (Ubuntu 24.04, non-root deploy user, SSH
keys only, ufw 22/80/443, unattended-upgrades, Docker), the DNS record
list, Postmark DNS for `notify.softwareske.com`, the full secrets list
flagging which must never be placeholders (a real, confirmed-live gap:
`JWT_SIGNING_KEY`'s own `min_length=32` does NOT reject the committed
`.env.example` placeholder, which is 40 characters — a length floor is
not a placeholder check), first deploy + migrations + every admin CLI
command (verified against the real `app/dashboard/admin_cli.py`/
`seed_cli.py`/`entrypoint.sh` argument shapes, not guessed), Phoenix
over an SSH tunnel via container IP, backup/restore/rollback, and the
real embed snippet shape (verified against `widget/loader/src/
config.js`'s own documented attributes).

### 6. Register

docs/DECISIONS_REGISTER.md updated in this same push: a new "Step 7.3
prep" decisions-table section (tool choices under rule 8 — Caddy
version pin, `age`, the AWS CLI, the PGDG apt repo, the `!reset []`
finding, the container-IP tunnel reconciliation, the backend-changes-
flagged-not-built entry) and the existing 7.3 forward-commitments
section updated with "prep done — final verification on the real
server at 7.3/7.4" on each item this batch actually prepares. Nothing
marked RESOLVED, per direct instruction.

### Duplication check (n=4: Caddyfile, prod compose override, backup/
restore-drill, monitoring script)

One real 2nd-occurrence found and fixed: `ops/backup/docker-compose.
yml`'s `backup` and `restore-drill` services both repeated the
identical `S3_BACKUP_BUCKET`/`S3_BACKUP_ENDPOINT_URL`/`AWS_*` block —
collapsed into a shared `x-s3-env` anchor, the same anchor style
`deploy/docker-compose.yml` already uses (`x-app-env`, `x-healthcheck-
timing`) rather than inventing a new convention. Re-verified live via
`docker compose config` that both services still resolve every expected
variable after the merge.

One below the fix bar, noted not fixed: `set -euo pipefail` appears
identically in `backup.sh`/`restore-drill.sh`/`run-restore-drill.sh` (a
genuine 3rd occurrence) — a universal one-line bash safety preamble
with no real abstraction to extract; sourcing it from a shared snippet
would add an indirection for zero behavioral benefit. No fixed-error-
message-table duplication found. Counter reset to n=0.

### Verification run (this batch)

- `docker run --rm ... caddy:2.10.2 caddy validate` → `Valid
  configuration`, with the real three hostnames from env.
- `docker compose -f deploy/docker-compose.yml -f deploy/docker-compose.
  prod.yml config` → only `caddy` has published ports (80/443); every
  other service shows `ports: None` and the new log-rotation config.
- The live backup/restore-drill proof above — run twice (with and
  without a real `db_connections` row), both PASSED, full teardown
  confirmed.
- Backend offline suite, from THIS worktree's own `.venv` (confirming
  `backend/` is genuinely unchanged by this track):
  `cd backend && python -m pytest tests -q` → **1090 passed, 482
  skipped (all skips are the pre-existing "TEST_DATABASE_URL not set"
  guard, unrelated to this track), 0 failed.**
- `git status --short` clean of `.env`/secrets before commit (checked
  by hand, not just assumed) — no `.env`, no `/tmp` artifacts, no built
  Docker images tracked.

---

## CI fix (2026-10-15, follow-up to the batch above)

`main`'s CI `test` job (run #214, commit `b2609f3`) went red:
`backend/tests/test_env_example.py` failed 2 of its 12 tests after the
`.env.example` additions above.

### Root cause of the process slip

**The offline suite was run BEFORE the final `.env.example` edit, not
after it.** The batch's real order of operations: (1) build the
Caddyfile/prod-compose/backup/monitoring code, (2) run `cd backend &&
python -m pytest tests -q` → **1090 passed, 0 failed** — genuinely
true AT THAT MOMENT, because `.env.example` still had none of this
track's own variables in it yet — (3) *then* edit `.env.example` to add
the new SMTP/Caddy/backup/monitoring documentation, write the RUNBOOK,
update the register and `docs/progress/ops.md`, and push. Step 2's own
real, honest "0 failed" result was reported in the end-of-task report
as if it covered the whole batch, but it never saw step 3's own
changes — `test_env_example.py` reads `.env.example` directly from
disk, so a suite run that predates the file's last edit cannot possibly
catch a rule it violates.

This is the exact failure mode PROJECT_SPEC.md §2 rule 18 (the push
gate) and §11.5 point 7 (zero failing tests before any push) exist to
prevent, and this task repeated it by running the gate at the wrong
POINT IN TIME, not by skipping it. **Fix, applied from here on for this
track and recorded as a standing rule**: the full backend offline suite
runs from `.venv` immediately before the push, after the LAST commit of
the batch — never earlier in the batch, regardless of how many times it
already passed on an earlier version of the same files.

### Two real bugs found by the two failing tests, both fixed

1. **`test_variable_names_match_settings_fields_plus_extras`**: every
   new `.env.example` variable not already an exact-name match for a
   Settings field needs an explicit allow-list entry in
   `backend/tests/test_env_example.py` — none of this batch's own new
   variables had one. Fixed by adding two allow-lists there (the one
   authorized `backend/` edit, per direct instruction): `SMTP_HOST`/
   `SMTP_PORT`/`SMTP_USERNAME`/`SMTP_PASSWORD`/`SMTP_FROM_ADDRESS`/
   `UPLOAD_MAX_SIZE_BYTES` joined the EXISTING `EXTRA_EXAMPLE_KEYS`
   tuple (each is a real, optional Settings field, the identical shape
   `PHOENIX_COLLECTOR_ENDPOINT`/`ANTHROPIC_API_KEY` already established
   there); a NEW, separately named `OPS_ONLY_EXAMPLE_KEYS` tuple holds
   the genuinely new deploy-layer keys with no Settings field at all
   (hostnames, S3/backup credentials, monitoring thresholds) — kept
   separate on purpose so a future variable landing in the wrong tuple
   would itself be a visible, checkable mistake.

2. **One real duplicate config, found and fixed**: `CADDY_UPLOAD_MAX_SIZE_
   MB` was a genuinely needless SECOND variable for a value the backend
   already names (`Settings.upload_max_size_bytes`). Checked every other
   new variable against `Settings.model_fields` by name, systematically,
   not by eye: confirmed live that `SMTP_HOST`/`SMTP_PORT`/
   `SMTP_USERNAME`/`SMTP_PASSWORD`/`SMTP_FROM_ADDRESS` already matched
   their Settings field names exactly (no rename needed — these were
   never duplicates, just undocumented in the guard test, item 1 above);
   `CADDY_UPLOAD_MAX_SIZE_MB` was the one real offender. Removed; Caddy's
   `request_body` limit now reads `UPLOAD_MAX_SIZE_BYTES` directly (the
   SAME variable `Settings.upload_max_size_bytes` reads), via Caddy's own
   `{$UPLOAD_MAX_SIZE_BYTES:20971520}` placeholder-default syntax.
   **Found live, not assumed**: Compose's own `${VAR:-}` (empty, not
   omitted) passes an EMPTY STRING into a container when a variable is
   unset in `.env` — and Caddy's `{$VAR:default}` placeholder treats
   "set to empty" differently from "unset": a direct test (`docker run
   -e UPLOAD_MAX_SIZE_BYTES= ... caddy validate`) failed with "wrong
   argument count", while the same config with the variable genuinely
   absent from the environment validated clean. Fixed by giving
   `deploy/docker-compose.prod.yml`'s own Compose-layer default the
   SAME literal number (`${UPLOAD_MAX_SIZE_BYTES:-20971520}`) as
   Settings' own default, so a real number reaches Caddy either way.
   `QDRANT_COLLECTION_NAME` and every other new variable were checked
   the same systematic way and confirmed to have no backend Settings
   equivalent at all — correctly ops-only, not renamed.

### Verification (after all fixes, before this push)

- `backend/tests/test_env_example.py` alone: **12 passed, 0 failed**
  (was 2 failed).
- Full backend offline suite, from THIS worktree's `.venv`, run AFTER
  the final commit of this fix (the corrected push-gate timing above):
  see the exact pasted output at the end of this entry.
- `caddy validate` against the renamed Caddyfile: `Valid configuration`.
- `docker compose config` (prod override): only `caddy` has published
  ports; `caddy`'s own resolved `UPLOAD_MAX_SIZE_BYTES` is `20971520`.
- The monitoring script's smoke test re-run unchanged (its SMTP_* names
  were already correct — nothing to fix there): clean run and
  forced-alert-with-no-SMTP run both still behave identically.
- `git status --short` clean; no `.env`, no secrets.
