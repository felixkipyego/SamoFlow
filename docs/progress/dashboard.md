# dashboard track — progress and decision log

Per PROJECT_SPEC.md §11.5 (parallel-track protocol). Folded into
PROJECT_SPEC.md's own §4/§5/§7/§9 at merge time; this file is a staging
area, not a permanent home for this history.

Duplication counter: n=0 (reset after this entry's own check, below).

---

## Step 6.1 — Dashboard authentication [SECURITY] (2026-10-15)

### Correction to §11.2, recorded here per direct instruction

The dashboard track is **not** frontend-only. Phase 6 needs new backend
work (users/memberships deferred from 1.2 to 6.1, auth endpoints, then
settings/sources/upload/domain-verification/inbox/usage endpoints in
6.2+). This track may edit `backend/`, under limits Felix stated
directly: API changes are additive-only (proven below, not just
asserted); this track owns every Alembic migration during the parallel
period.

### Environment fix (mid-task, direct instruction)

Both worktrees were sharing ONE conda env (`widgetplatform`), whose
editable install pointed at whichever worktree ran `pip install -e`
most recently — confirmed live: `python -c "import app; print(app.__file__)"`
resolved to the **widget** worktree's `backend/app` before the fix.
Fixed: a per-worktree `.venv` here (`.venv/`, gitignored, pinned Python
3.12 from the same conda env), `make install` run against it. Re-
confirmed after the fix: the same import check now resolves to THIS
worktree's `backend/app`. Every verification command in this log below
ran through `source .venv/bin/activate` first.

### Kickoff decisions ratified directly by Felix (mid-task, superseding §11.3)

1. **UI framework**: §11.3 kickoff decision #2 (Vite + React + CSS
   Modules) is REPLACED by shadcn/ui, per the 2026-09-28 decision log
   entry ("Dashboard component model: shadcn/ui is the leading
   candidate, NOT yet approved... final decision... at the Step 6.1
   breakdown"). That entry's own constraints, followed exactly:
   - Vite template (`npm create vite@6.5.0 . -- --template react-ts`),
     not Next.js.
   - ONE primitives library, chosen deliberately: **Radix**, not Base
     UI — `npx shadcn@4.21.5 init --base radix`. Justification (rule
     8): Radix is the longer-established, more broadly registry-
     covered of the two as of this task; Base UI (the MUI team's newer
     project) has a shorter track record. Pinned explicitly in every
     CLI invocation (`@4.21.5`), not `@latest`.
   - Official registry only — every component came from
     `npx shadcn@4.21.5 add <name>`, nothing hand-copied from a
     third-party source.
   - Generated diffs reviewed before commit — read directly, file by
     file (button/input/label/card/dialog), before this entry was
     written.
   - Components added one at a time (`button` from `init`, then
     `input label card dialog` in one `add` call per the official
     CLI's own batching, each file reviewed individually) — no
     "blocks" ever used.
   - No charting library — not needed by any 6.1 screen.
   - Never used in the widget — widget/ is Preact in a shadow root, a
     completely separate track; nothing shared.
   - §12's visual system: theme tokens as CSS custom properties
     (`src/index.css`'s own `:root`/`.dark` blocks, shadcn's own
     generated output — not hand-written), light and dark from the
     first screen, self-hosted fonts (`@fontsource-variable/geist` —
     real `.woff2` files bundled into `dist/assets/`, confirmed via a
     real `npm run build`, never fetched from a third-party host at
     runtime), no third-party requests (confirmed by the live strict-
     CSP Playwright test below, which runs with NO allowance for any
     external host at all).
   - npm supply-chain setup (the 2026-09-28 entry's own "due at 5.1,
     not 6.1" line): superseded by this task's own direct, later
     instruction naming "pinned Node, committed lockfile, npm audit
     clean" as explicit 6.1 deliverables — each track now gets its own
     independent npm setup (a natural consequence of the 2026-10-15
     parallel-track split, which postdates that 2026-09-28 note).
     `.github/dependabot.yml` gets its own `npm`/`/dashboard` entry
     here, not deferred to widget's own 5.1.

2. **Auth implementation**: docs/SPEC.md §12's own "use a maintained
   auth library rather than hand-rolling" — Felix named **fastapi-users**
   (15.0.5) directly. Full rule-8 justification, the real extension
   points needed on top of it, and the real footprint cost are all in
   `backend/pyproject.toml`'s own dependency comment and `app/dashboard/
   auth_backend.py`'s own header comment — not duplicated here. Every
   security requirement this task's own prompt named stays layered on
   top of the library, not replaced by it: CSRF (app/dashboard/csrf.py,
   the library has none), per-IP/per-account rate limiting (app/
   dashboard/routes.py, reuses the existing `RateLimiter`), hashed
   session tokens and idle expiry (`HashedDatabaseStrategy`, extending
   the library's own `DatabaseStrategy`, which stores the raw token
   with absolute-only expiry — confirmed by reading its source before
   deciding to extend it), tenant-scoping via Membership (app/
   dashboard/dependencies.py, a concept the library has no equivalent
   of at all).

3. **Temporary admin key**: docs/SPEC.md §12 says Step 6.1 removes it
   outright and replaces it with `platform_admin` accounts on the same
   login system — this OVERRIDES this task's own original "keep the
   admin key" line. Done in this same task (not split, since
   `require_admin_key()`/`AdminKeyVerified`/`Settings.admin_api_key` had
   **zero real callers** anywhere in this codebase, confirmed by
   grepping before removing — `revoke_domain()` had never been wired to
   an endpoint at all): `app/admin/dependencies.py` deleted;
   `Settings.admin_api_key` and its validator/accessor deleted;
   `.env.example`/`deploy/docker-compose.yml`/`deploy/smoke-image.sh`/
   every test referencing `ADMIN_API_KEY` updated or removed;
   `app/admin/routes.py` rebuilt with `current_platform_admin` (fastapi-
   users' own `is_superuser` semantics, reused directly, not a second
   parallel admin-identity mechanism) as the only gate on `/api/v1/admin`.
   The CLI admin commands Felix named (create a tenant, assign a plan,
   issue/revoke a site key, revoke a domain, seed an owner) are all
   built now too (not split to 6.2 — the backend half was small once
   the repository primitives already existed): `app/dashboard/
   seed_cli.py` (seed an owner, `--platform-admin` flag) and `app/
   dashboard/admin_cli.py` (the other five), wired into `entrypoint.sh`'s
   new `seed-owner`/`admin` modes, run via `docker compose run --rm
   migrate <mode> ...` (reusing that service's own env/network wiring,
   no new Compose service).

### Additional items named directly, resolved or re-owned below

4. **Re-point `revoke_domain()` and every `/api/v1/admin` endpoint at
   platform_admin sessions; fix the stale `TODO(6.5)`.** RESOLVED:
   `POST /api/v1/admin/domains/{domain_id}/revoke`, gated by
   `current_platform_admin`, `actor` is the real admin's own email (no
   more generic placeholder string). `app/admin/routes.py`'s own stale
   comment (which named the now-deleted admin-key mechanism as the
   reason to wait) is rewritten to name the real, current reason 6.5's
   full tenant-management screen still waits (feature scope, still
   POST-PILOT — unchanged from the existing decision).

5. **`backend/tests/test_layout_placeholders.py`.** RESOLVED: dashboard/
   entries removed (it is no longer a placeholder); widget/'s two
   entries are untouched (a separate, concurrently-running track).

6. **CORS/OPTIONS for dashboard and admin endpoints.** RESOLVED by the
   same-origin design itself, not by writing CORS-handling code: the
   dashboard SPA and its API are served from the same origin in
   production (Caddy routes `/api` on the dashboard hostname — a Phase 7
   deployment step, not yet built) and in dev (Vite's own proxy,
   `dashboard/vite.config.ts`). Neither `app/dashboard/routes.py` nor
   `app/admin/routes.py` sets any `Access-Control-*` header or answers
   `OPTIONS` — there is structurally no cross-origin request for either
   surface to handle. The marker's own "today OPTIONS returns 405" is
   now the CORRECT, intended behavior for these two routers specifically
   (never cross-origin, so no preflight is ever legitimately needed) —
   closed, not deferred again.

7. **`require_recent_auth` dependency.** Built now
   (`app/dashboard/dependencies.py`), not consumed by any 6.1 endpoint
   (6.1 has no action needing it) — a parameterized dependency factory
   (default 900s/15min, the upper end of docs/SPEC.md §12's own "10 to
   15 minutes"), reading a NEW `DashboardIdentity.authenticated_at`
   field (the real `dashboard_sessions.created_at`, set only at a real
   login — never touched again, unlike the idle-expiry `last_seen_at`).
   6.2+ endpoints (delete tenant, change roles, replace DB credentials,
   change site keys/allowed origins) add `Depends(require_recent_auth())`
   directly — the mechanism is done, not redesigned per call site.

8. **Dashboard sessions separate from widget tokens; a widget JWT on a
   dashboard/admin endpoint returns 401.** Tested directly (`test_
   dashboard_routes.py`): a real, validly-signed widget JWT presented as
   the `__Host-session` cookie value fails (hashed-token lookup, no
   match); the same JWT presented as an `Authorization: Bearer` header
   also fails (`CookieTransport` only ever reads the cookie, never looks
   at `Authorization` at all).

9. **CLI admin commands' own three named requirements:**
   - (a) **Real `pk_live_...` generator**: `app/tenancy/repository.py`'s
     new `generate_site_key()` — reuses `generate_visitor_secret()`'s
     own 256-bit primitive (`app.auth.secrets`), not a second
     implementation. Closes the §9 marker recorded at the Step 1.4
     breakdown (2026-09-28).
   - (b) **Origin/domain normalization on save**: `app/auth/origin.py`'s
     new `normalize_origin()` (scheme://host[:port], lowercase, no
     path, no wildcard — for `allowed_origins`) and `app/ingest/
     domain_verification.py`'s new `normalize_domain()` (a bare
     hostname, a genuinely different shape — confirmed by reading every
     real caller of `claim_domain()` before writing this, not assumed).
     Both are real, tested, pure functions with a real caller today
     (`admin_cli.py`'s `issue-site-key` normalizes every `--allowed-
     origin` before `create_site_key()`) — closes the §9 marker
     recorded at the Step 1.4 breakdown (2026-09-28, extended to
     `verified_domains.domain` at Task 2.2.a). **Not yet closed**: Step
     6.3's own "add a source"/domain-claim dashboard form must call
     `normalize_domain()` too, once it exists — the function is ready,
     the UI call site isn't built yet (6.3's own job, not invented
     ahead of it here).
   - (c) **Seed the two pilot Plan rows**: `admin_cli.py`'s new
     `seed-plans` command — "Starter" (10,000 msgs/month) and "Growth"
     (25,000 msgs/month), the real numbers from the Task 3.7.i/§11
     marker's own ~21%-headroom derivation (not re-derived here). The
     other five `PlanLimits` fields reuse the existing Settings
     defaults verbatim (`crawl_page_cap=500`, `upload_max_size_bytes=
     20MB`, `db_sync_row_cap=10_000`, `refresh_interval_min_allowed=
     "daily"`, `refresh_now_daily_allowance=5`) — no new numbers
     invented. Closes the "whoever next builds real Plan-row creation"
     §9 marker (narrowed from Task 4.3 at that task's own close-out).

10. **§12's acceptance additions, from the login screen onward.**
    Automated, live, via the Playwright suite (`dashboard/e2e/
    csp.spec.ts`): a keyboard-only pass (tab order through email →
    password → submit, each focus state asserted); an automated
    accessibility check (`@axe-core/playwright`, zero serious/critical
    violations on both the login screen and the open Dialog); loading/
    error states (the login screen's own `submitting`/`error` React
    state, exercised by `LoginScreen.test.tsx`'s Vitest suite).
    Screenshots in light AND dark theme: **not captured as a separate
    artifact in this pass** — the theme tokens exist and are real
    (`:root`/`.dark` in `src/index.css`), but no automated visual-diff
    tooling was wired up to produce committed screenshots, and this
    task has no vision-capable step to review them manually either.
    Re-owned: whoever next touches the dashboard's visual design (6.2's
    own theming/polish pass is the natural point) should add real
    light/dark screenshots to the acceptance record. Permission-denied
    states: not applicable yet (6.1 has no screen that can be reached
    without a tenant membership at all — the one place this matters,
    `get_current_dashboard_user`'s own 403 for a membership-less user,
    is covered by a real test, but no SCREEN renders that state yet,
    since 6.1 only ships login + shell).

### A real finding: CSP and the Dialog scroll-lock (investigated live, not assumed)

Opening the required Dialog under the live strict-CSP Playwright test
surfaced a real violation: Radix's `Dialog` uses `react-remove-scroll`
→ `react-remove-scroll-bar` → `react-style-singleton`, which inserts a
real `<style>` element (`document.createElement('style')` +
`appendChild(textNode)`) for its own scrollbar-gap-compensation CSS.
That is the ONE DOM operation `style-src` actually governs — confirmed
live with a throwaway strict-CSP page before relying on this: direct
`element.style.setProperty()`/`element.style.cssText =` mutations are
NOT affected by `style-src` at all (tested both, neither produced a
violation and both visibly applied); only inserting a real `<style>`
element was blocked.

Radix's own `Dialog` API does not expose a way to pass `removeScrollBar
={false}` through to disable just the cosmetic half (confirmed by
reading `@radix-ui/react-dialog`'s own source: it hardcodes the props
it forwards to `RemoveScroll`, and the overlay+scroll-lock only renders
at all when `modal={true}`, which also controls the backdrop and focus
trap — setting `modal={false}` to dodge this would be a real, much
bigger UX regression, not a scoped fix).

**Resolution shipped, with the policy itself untouched (no hash, no
'unsafe-inline' added anywhere):** `src/index.css` adds one static,
build-time-compiled CSS rule (`body[data-scroll-locked] { overflow:
hidden !important; }`) that restores the REAL functional behavior (the
actual scroll lock) independently of whether the browser blocks the
library's own injected stylesheet. The Playwright test asserts this
real property directly (`getComputedStyle(document.body).overflow ===
"hidden"` while the dialog is open) and tolerates ONLY that one named,
investigated violation by exact message pattern — any OTHER violation
still fails the suite outright.

**What remains open, named plainly:** the one cosmetic violation itself
(the scrollbar-width compensation padding, which fixes a usually-small
layout shift when a scrollbar disappears) is not silenced. The textbook
fix is a real, per-response CSP nonce (`react-style-singleton` already
supports one natively via the `get-nonce` package — confirmed by
reading its source: it calls `getNonce()` and sets a `nonce` attribute
on the `<style>` tag automatically if one is set via `setNonce()`) —
but that needs the SERVED HTML and the CSP header to share a freshly
generated random value per request, which needs dynamic response
templating (e.g. Caddy's own `templates` directive) that this project's
production deployment does not build yet (Phase 7). A CSP hash
allowlist was considered and rejected: the padding value is computed
from the real, measured scrollbar width (`window.innerWidth -
document.documentElement.clientWidth`), which is genuinely different
across browsers/OSes (confirmed live: macOS's own overlay scrollbars
measure 0, unlike Windows) — a hash pinned in one environment would
silently stop applying (CSP-block, with no functional harm after the
static-CSS fix above, but also no longer silencing the console warning)
in another. **Owner: Phase 7's own deployment step, or whoever next
wires Caddy's real production config** — add a per-response nonce,
call `setNonce()` from `get-nonce` at app startup reading it from a
`<meta>` tag the server injects, once dynamic HTML templating exists.

### Duplication check (n=0 → check due at 4; ran now, after Step 6.1's own code, which is more than 4 sub-steps)

Given the sheer size of this single batched step (new package, new
tables, a library migration, two CLIs, a full frontend), the check ran
at the END of the step rather than strictly every 4 sub-steps —
recorded here as a deliberate deviation from the letter of the rule,
not a skipped check. Findings:
- `generate_visitor_secret()`/`hash_visitor_secret()` (app/auth/
  secrets.py) are now used by THREE callers for non-widget-visitor
  secrets (dashboard session tokens via `HashedDatabaseStrategy`, and
  implicitly by `generate_site_key()`) — a real, accepted reuse, not a
  duplication; flagged here per this project's own "reuse, don't
  duplicate" convention, no action needed (the names are slightly
  visitor-specific but the implementation is fully generic, confirmed
  by reading it).
- No other near-duplicate logic found between the new `app/dashboard/`
  package and existing `app/auth/`/`app/tenancy/` code.

### Verified (final, authoritative pass)

- `make lint`: clean.
- Offline suite (`cd backend && python -m pytest tests -q`, no
  `TEST_DATABASE_URL`): 1082 passed, 452 skipped (DB-dependent), 0
  failed.
- `make test-all` (real test-db + test-qdrant, COMPOSE_PROJECT_NAME=
  samoflow-dashboard): see below for the final run's real pass count.
- `make lock-check`: clean (no drift).
- `make audit`: 0 known vulnerabilities (backend).
- `make hooks`: 64 markers (down from 67 — a real, diffed, explained
  change; see `test_hooks.py`'s own updated comment).
- Image job: `docker compose ... config --services` names postgres/
  qdrant/api/worker; `docker build`; `deploy/smoke-image.sh` — all 8
  checks PASS.
- `dashboard/`: `npm run build`/`npm run lint`/`npm run test` (8/8) all
  clean; `npm run audit` (`--omit=dev`) 0 vulnerabilities (see
  dashboard/README.md for the one dev-only, never-shipped finding this
  scoping excludes, and why).
- `dashboard/e2e` (Playwright, real production build, real strict CSP,
  real backend + a real seeded owner): 4/4 passing, including the
  authenticated-shell/Dialog test (not skipped).
- New test files run live 3x individually before trusting them
  (project convention): `test_dashboard_routes.py`, `test_dashboard_
  repository.py`, `test_dashboard_passwords.py`, `test_dashboard_
  seed_cli.py`, `test_dashboard_admin_cli.py` — all stable across 3
  consecutive runs.
- A second real bug found live, this one in `test_admin_routes.py`
  itself, not production code: `POST /auth/login` (app/dashboard/
  routes.py) unconditionally requires a real tenant membership to
  succeed -- confirmed by direct, isolated reproduction (`manager.
  authenticate()` called directly returns a real user; the SAME
  credentials through the real HTTP endpoint 401s, because the test's
  own platform-admin fixture user had no membership at all). This is
  consistent, not a gap: every real admin account this project's own
  tooling can create (`seed_cli.py`'s `--platform-admin` flag) already
  creates a membership alongside the user, so a platform admin who is
  ALSO the pilot tenant's owner (the only real shape this pilot needs)
  can always log in. **Named plainly, not silently absorbed**: there is
  no "platform-admin-only, no tenant at all" account shape today --
  re-owned to whoever builds a second, non-tenant-owning admin account
  (6.5's own post-pilot scope is the natural point, if ever needed).
  Fixed the test (not the production code, which is correct as shipped)
  to create a membership for every fixture user, matching the real
  path.
- A real bug found and fixed live, not caught by any in-process test:
  `app/dashboard/seed_cli.py`/`admin_cli.py` originally omitted `from
  app import all_models`, so a real standalone invocation (outside
  pytest, where `tests/conftest.py` itself masks this by importing
  `all_models` for every test) raised `NoReferencedTableError` on
  `tenants.plan_id -> plans`. Fixed by adding the import to both
  modules; a new subprocess-based regression test (`test_dashboard_
  seed_cli.py`'s own `test_seed_owner_works_as_a_real_standalone_
  subprocess`) now catches this class of bug directly, matching
  `test_alembic.py`'s own established "a subprocess run with a minimal
  env is the only thing that actually proves this" precedent.
- OpenAPI additive-only proof (this task's own direct instruction):
  `backend/scripts/check_openapi_additive.py` (new, tested in
  `test_openapi_additive_check.py`), run against the committed contract
  before this task's own changes vs. the regenerated one after:
  **confirmed additive-only — 6 new paths added
  (`/api/v1/admin/domains/{domain_id}/revoke`, `/api/v1/admin/me`,
  `/api/v1/dashboard/auth/{csrf,login,logout,me}`), zero existing
  paths or schemas changed.** Every widget-facing path (`/session`,
  `/chat`, `/conversations*`, `/escalate`) is byte-identical.

### Rebase/push status (direct heads-up from Felix, mid-task)

`origin/main` is currently RED: `backend/tests/test_layout_placeholders.py`
(at `a12792d`) was restructured by the widget track at Step 5.1 to
narrow as `widget/loader` shed its own placeholder status, then broken
again by Step 5.2 making `widget/chat` real too — the widget track's
own commit message names this directly ("2 known test_layout_
placeholders.py collisions, not 1"). Felix's own instruction: this
track touches ONLY the `dashboard/` entries in that file (already done,
above) — never the `widget/` ones, even if a rebase surfaces a conflict
there before the widget track's own fix lands on `main`. Push gate
(rule 4/§2.18): never push while any test fails, so this track's own
rebase+push waits for `origin/main` to go green on its own widget-side
fix first, re-fetching before every rebase attempt.

### Rebase/push outcome (final)

By the time this track rebased, `origin/main` had already gone green
at `6eea542` ("Alignment: fix CI (narrow test_layout_placeholders.py to
dashboard/ only)...") — the widget track's own fix for the collision
above landed first, exactly as the heads-up anticipated, so no second
wait was needed.

`git rebase origin/main` replayed this track's 5 commits (Parts A-E)
cleanly except for two conflicts, both resolved by hand, both
mechanical (no logic changed on either side):

- `backend/tests/test_layout_placeholders.py` (commit 3, Part C): the
  widget track's own `6eea542` had already renamed/restructured the
  same file this track's own 6.1 change touched (narrowing
  `_STILL_PLACEHOLDER_READMES`/`_PLACEHOLDER_ROOTS` for `widget/chat`).
  Resolved by taking that structure as the base and applying this
  track's own change on top: both dicts/tuples now empty (dashboard/
  also has real tooling as of this task), the generic test name
  `test_placeholder_roots_contain_only_readmes_and_folders` kept (no
  folder this file tracks is still a placeholder), all three README
  entries kept permanently in `_READMES` (established convention —
  real folders stay listed). Verified immediately: `pytest tests/
  test_layout_placeholders.py -q` — 4 passed.
- `.gitignore` (commit 4, Part D): both tracks added a Node-tooling
  comment line over the same pre-existing blank slot (widget's own
  "chosen at Step 5.1" wording vs. this track's "not chosen yet").
  Resolved by merging both into one true statement naming both
  tracks' own real, now-chosen tooling (esbuild/Playwright/ESLint for
  widget at 5.1; Vite/React/TypeScript/shadcn-ui/Playwright/Vitest for
  dashboard at 6.1) — content merge, not a pick-one-side resolution.

Full verification re-run from scratch after the rebase (this
worktree's own `.venv`, confirmed pointed at this worktree's `backend/`
before running anything):

- `make lint`: clean.
- `make test-all` (real test-db/test-qdrant): **1568 passed, 0
  failed**, both services confirmed stopped/removed afterward.
- `make hooks`: unchanged shape (64 markers).
- `make lock-check`: clean.
- `make audit`: 0 known vulnerabilities.
- Image job: fresh `docker build` of `backend/`, `deploy/
  smoke-image.sh` — all 8 checks PASS.
- `dashboard/`: lint (0 errors, 1 pre-existing shadcn-template
  warning), `npm run test` (8/8), `npm run build`, `npm run audit
  --omit=dev` (0 vulnerabilities) — all clean.
- `dashboard/e2e`: real backend stack (`make up`/`make migrate`, a
  real seeded owner via piped stdin), full Playwright suite — **4/4
  passing**, including the authenticated-shell/Dialog test. One
  self-inflicted false alarm during this run, not a real bug: an
  earlier `make up &` was started as a detached background shell
  outside this session's own background-task tracking and killed
  before the `api`/`worker` containers finished starting (only
  postgres/qdrant/phoenix came up); the resulting e2e run correctly
  failed on "Email or password is incorrect" (login had nowhere to
  go). Re-ran `make up` properly, confirmed `/health` returns 200
  first, then re-ran the suite clean. No code or test change was
  needed — recorded here so the failure isn't mistaken for a real
  login/session bug on a future read of this log.
- `git status --short`: clean; `.env` confirmed present only as an
  ignored (`!!`) local file, never staged.

Pushed straight to `main` (rule 4): `6eea542..195de3a`, five commits —
`7519325` (Part A), `1202972` (Part B), `a98cd09` (Part C), `29b444b`
(Part D), `195de3a` (Part E). Not rejected; `origin/main` had not moved
again since the rebase base, so only one push attempt was needed.

## Alignment task (2026-10-15, no 6.2 work)

Re-read CLAUDE.md (including the new "Sync before work" rule),
PROJECT_SPEC.md §2 rules 19-22 and §11.5, and the full
docs/DECISIONS_REGISTER.md, per direct instruction, before touching
anything. `git fetch origin && git rebase origin/main` was a no-op —
`origin/main` had not moved since Step 6.1's own push (`2295fa9`).

### Prior decisions applied (rule 19 sweep)

- D-shadcn (CANDIDATE, unresolved as of the register's own last sweep)
  and D-113-dashboard-tooling (ratified without citing/superseding
  D-shadcn) — both already resolved by Step 6.1's own prior work
  (`29b444b`), but the register itself was never updated to say so.
  Resolved in the register now, citing supersession (rule 20) rather
  than leaving the gap the register's own prior note already flagged.
- D-6.1-admin, D-6.1-site-key-generator, D-6.1-origin-domain-normalise,
  D-6.1-cors-same-origin, D-6.1-test-layout-placeholders,
  D-6.1-session-field-na, D-6.1-recent-auth, D-6.1-recent-auth-scope,
  D-6.1-pilot-plans — all newly recorded rows for work Step 6.1 already
  built but the register never carried forward (the register was built
  2026-10-15, BEFORE Step 6.1 shipped later the same day; this is the
  first sweep since).
- §11.5 point 4 ("neither track edits `backend/`, except through a
  stop condition") — re-confirmed this alignment task's own backend
  changes (the session-invalidation hook, item 5 below) fall under
  Step 6.1's own already-granted exception (§11.2's own corrected
  scope: Step 6.1 may edit `backend/`), not a new stop condition.
- §11.5 point 6 (a new workflow file is unproven until its first real
  CI run is green) and point 7 (zero failing tests before any push) —
  both apply directly to this task's own push gate; see Verified,
  below. No `gh` CLI is available in this environment to check Actions
  runs directly — Felix is told exactly which runs to check in the
  final report instead of this track claiming to have confirmed green.

### 1. Per-worktree Python environment

Already built at Step 6.1 (`.venv/`, pinned Python 3.12 from
`/opt/miniconda3/envs/widgetplatform`, gitignored). Re-confirmed this
task: `pip install -e backend` plus both lockfiles re-run clean;
`python -c "import app, os; print(os.path.dirname(app.__file__))"`
resolves to this worktree's own `backend/app`, not the shared conda
env. No change needed.

### 2. Register updates

docs/DECISIONS_REGISTER.md updated in the same commit as this report:
D-shadcn APPROVED (supersedes D-113-dashboard-tooling); a new "Step 6.1
close-out" decisions-table section (12 rows) covering every item Step
6.1 actually built; the stale "6.1" forward-commitments list (§2) and
the "In-flight (5.1, 6.1)" audit (§5) both annotated RESOLVED, citing
commits, per the file's own "RESOLVED items live in the decisions
table, not repeated as forward-only" convention; ownership of the
three widget-track-found backend contract gaps formally taken and
scheduled for Step 6.2, with Felix's own additive-authorisation for
(a) `verified_domains` in `widget_config` and (b) `conversation_id` on
`/chat`'s `done` event recorded explicitly; a new POST-PILOT entry
(D-postpilot-admin-no-tenant-shape) for the "platform-admin-only, no
tenant" account-shape gap found live during Step 6.1's own adversarial
pass. Matching PROJECT_SPEC.md §9 markers resolved in place (the
admin-key-replacement, `revoke_domain()`-endpoint, CORS/OPTIONS, and
`test_layout_placeholders.py` markers) and four new §9 markers appended
(the two below, the POST-PILOT account-shape gap, and the
session-invalidation residual gap, item 5 below) — not touching §4/§5/
§7, per §11.5 point 3's own "not touched while either track is in
flight" rule; §9 itself is append-safe (the widget track already
appended there mid-flight, confirmed by reading its own 6991-6993
entries before deciding this was safe).

### 3. CSP tolerance made exact

The prior tolerance (`dashboard/e2e/csp.spec.ts`) matched a free-text
console regex (`/Applying inline style violates/i`) — too loose: it
would have silently swallowed ANY future inline-style violation
anywhere in the app, not just the one known, investigated case.

Captured the real violation's exact shape live (a throwaway debug
script, not committed, deleted after use) via the browser's own
structured `securitypolicyviolation` DOM event rather than the
free-text console message. Confirmed the message DOES carry a SHA-256
hash (`sha256-nzTgYzXYDNe6BAHiiI7NNlfK8n/auuOAhh2t92YvuXo=` in this
run), but deliberately did NOT match on it: the hash is computed over
react-style-singleton's own injected CSS text, which embeds the real
MEASURED scrollbar-gap width — platform/browser dependent (this file's
own prior entry already names macOS-overlay-scrollbars-measure-0 vs
Windows as a concrete case) — so a hash-based match would pass locally
and silently fail to match in CI's own different Chromium/OS
combination, for the identical reason this project's CSP already
rejects hash-pinning as non-portable.

The new tolerance (`isKnownScrollLockViolation()`) matches instead on
structural fields that do NOT depend on the gap width at all:
`effectiveDirective === "style-src-elem"`, `blockedURI === "inline"`,
`disposition === "enforce"`, and `sourceFile` under the page's own
`/assets/*.js` (same-origin bundle, not a specific content-hashed
filename, so it survives the next build). The authenticated-shell test
also now asserts the known violation fires EXACTLY once per Dialog
open (not zero — which would mean the library stopped doing this and
the tolerance should be deleted — and not more than one).

**Proof, committed as a permanent regression test**: a new test
deliberately injects a SECOND, differently-shaped violation (an
external `<script src="https://example.invalid/...">`, nowhere near
the Dialog/scroll-lock code path) on the plain login screen, and
asserts it IS reported as unexpected — `effectiveDirective ===
"script-src-elem"`, a different blocked URI. If the exact tolerance
above ever regressed into something broader, this test would start
failing. Verified live: `make dashboard-e2e` against the real backend
stack — 5/5 passing (the 3 original login-screen tests, the new proof
test, and the authenticated-shell/Dialog test with the new structural
matcher).

### 4. fastapi-users maintenance status (checked live, 2026-10-15)

- PyPI (`https://pypi.org/project/fastapi-users/`, and
  `https://pypi.org/pypi/fastapi-users/json` for the machine-readable
  form): latest release v15.0.5, published 2026-03-27 (matches this
  project's own pin exactly); classifier `Development Status :: 5 -
  Production/Stable`.
- GitHub README (`https://raw.githubusercontent.com/fastapi-users/
  fastapi-users/master/README.md`), quoted verbatim: *"This project is
  now in maintenance mode. While we'll continue to provide security
  updates and dependency maintenance, no new features will be added.
  We encourage you to explore the project and use it as-is, knowing it
  will remain stable and secure. We're currently working on a new
  Python authentication toolkit that will ultimately supersede FastAPI
  Users. Stay tuned for updates!"*
- GitHub API (`https://api.github.com/repos/fastapi-users/
  fastapi-users`): not archived; last pushed 2026-08-17 (after the
  maintenance-mode announcement — consistent with "we'll continue to
  provide security updates"); 10 open issues.

**Conclusion, stated plainly**: in maintenance mode means security
fixes and dependency-compatibility updates continue; no new features
will land, and an unnamed, undated successor project is in
development. This does not change this task's own library choice —
the security floor this project needs does not depend on new library
features — but it is a real fact Felix should know, not a detail
buried in a dependency comment. Not swapped in this task, per direct
instruction. Register entry + §9 marker recorded, owner Phase 7
(re-check before pilot go-live).

### 5. Session invalidation on credential/role change

**Challenged the prompt's own literal wording first, per CLAUDE.md's
senior-engineer-judgment rule**: the task named three CLI commands —
"a password reset, an owner re-seed, or removing platform_admin
through the CLI" — as the triggers requiring session invalidation.
None of those three commands exist anywhere in this codebase
(`app/dashboard/admin_cli.py`'s own command list is create-tenant/
assign-plan/issue-or-revoke-site-key/revoke-domain/seed-owner/
seed-plans only; `seed_cli.py`'s own `seed_owner()` explicitly REFUSES
to reset an existing account: *"this command does not reset an
existing password or promote an existing account — use a direct
database update for that"*). Building three new, unrequested CLI
commands to satisfy this literally would itself be new feature work —
the same sentence in the prompt explicitly rules that out ("not new
features").

**What was built instead**: the real, generic mechanism underneath all
three named cases. `UserManager.on_after_update()`
(`app/dashboard/auth_backend.py`) now invalidates every one of a
user's existing `DashboardSession` rows whenever an update changes
`password` or `is_superuser` — hooked at `BaseUserManager.update()`,
the one real path any future password-reset or platform_admin-removal
command would be built on, matching `admin_cli.py`'s/`seed_cli.py`'s
own established "built on the real path, not a second one" precedent
(see those modules' own header comments). The new primitive,
`invalidate_all_sessions_for_user()` (`app/dashboard/repository.py`), is
a plain bulk `DELETE`, matching this module's own existing
flush-not-commit convention (caller commits).

The real payoff: whichever future step builds a real password-reset or
platform_admin-removal CLI command inherits this invalidation
automatically the moment it calls `UserManager.update()`, with zero
code of its own — not three separate call sites each having to
remember it individually.

**Named residual gap, not silently solved**: `seed_cli.py`'s own
documented workaround ("use a direct database update for that") never
calls `update()` at all, so a hand-edited database row bypasses this
hook entirely. Flagged plainly in both the register and a new §9
marker — no amount of application-layer code can close that specific
path; only a real password-reset/admin-removal command, built the
right way, removes the need for that workaround in the first place.

**Logout invalidates the session server-side, not only the cookie**:
already true and already covered by an existing committed test
(`test_dashboard_routes.py::test_logout_revokes_the_session_and_
subsequent_requests_are_401` — replays the exact, now-revoked raw
token from a FRESH client, confirms 401, and confirms the
`DashboardSession` row count is 0). Re-confirmed, not re-built.

**A small, pre-existing inconsistency found and fixed along the way**:
`test_dashboard_repository.py`'s own header comment already claimed
user/session persistence is "exercised via test_dashboard_routes.py
and test_dashboard_auth_backend.py instead" — but `test_dashboard_
auth_backend.py` did not exist until this task created it. The
comment was aspirational, not accurate, from Step 6.1 onward; it is
accurate now.

**Verified live, 3 consecutive runs** (new test file `backend/tests/
test_dashboard_auth_backend.py`, 4 tests, plus the existing logout
test): all 5 passed identically on each of 3 runs against the real
test database (no flakes, no ordering dependency).

### Duplication check

Counter: this task's own code-changing sub-steps are items 3 (CSP test
rewrite) and 5 (session-invalidation hook + new test file) — 2 total,
below the n=4 trigger (rule 9). Item 2 (register bookkeeping) and item
4 (a live library-maintenance check, no code) are documentation-only,
per rule 9's own "does not count" carve-out. No duplication check run;
none due.

### Verified (final, authoritative pass)

- `make lint`: clean.
- Offline suite + `make test-all` (real test-db/test-qdrant): **1572
  passed, 0 failed** (1568 + 4 new tests in `test_dashboard_auth_
  backend.py`).
- `make hooks`: unchanged marker shape.
- `make lock-check`: clean (no new dependency added).
- `make audit`: 0 known vulnerabilities.
- Image job: fresh `docker build` of `backend/`, `deploy/
  smoke-image.sh` — all 8 checks PASS (re-run this task, since
  `backend/` changed).
- `dashboard/`: lint/test/build/audit all clean (no dashboard source
  changed except `e2e/csp.spec.ts`, which has no lint/build/audit
  surface of its own beyond the suite itself).
- `dashboard/e2e`: **5/5 passing**, including the new exact-tolerance
  proof test, against a real backend stack (`make up`/`make migrate`,
  a real seeded owner via piped stdin).
- OpenAPI additive-only proof: unchanged this task (no endpoint shape
  touched) — `docs/openapi.json` not regenerated, confirmed no drift.
- `git status --short`: clean after commit; `.env` confirmed present
  only as an ignored (`!!`) local file, never staged.
