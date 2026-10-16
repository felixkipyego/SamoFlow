# dashboard track — progress and decision log

**Folded into PROJECT_SPEC.md §4/§5/§7/§9 on 2026-10-16 at the dashboard track's own close-out.** This file is now archived detail only — kept, not removed, per §11.5 point 3's own instruction. PROJECT_SPEC.md §5 (the Phase 6 closure) is the current record; nothing below should be edited further.

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

---

## Steps 6.2 + 6.3 + 6.4 + 6.6 — batched (2026-10-16)

Per §11.2's own batch plan: "6.2 + 6.3 + 6.4 + 6.6 (plumbing, batched) —
Tenant settings, sources screens, escalation inbox/usage, Phase 6
acceptance. *Dashboard track ends here.*" 6.5 (Platform admin) excluded
throughout, by direct instruction — POST-PILOT per Felix's decision G.

### Step 6.2 — Tenant settings

**Backend** (`app/tenancy/schemas.py`, `app/tenancy/repository.py`,
`app/dashboard/tenant_routes.py`, `app/dashboard/tenant_schemas.py`,
new): real Pydantic schemas for every dashboard-writable
`tenants.config` section — `SupportDetailsUpdate`, `AlertEmailsUpdate`,
`WidgetLookUpdate` (with `WidgetColors`, CSS color/URL validation
mirroring the widget's own client-side rules exactly: hex/rgb/hsl
regex, WHATWG-URL-parsing tab/newline-stripping for the
`javascript:`-bypass defense, https-only), `RetentionUpdate` (1-90
days / 1-24 months), `TraceTextUpdate`, `AllowedOriginsUpdate`.
`TenantScopedRepository.update_config()` merges a patch into
`tenants.config` (`{**old, **patch}` reassignment, never in-place
mutation — the column is a plain JSONB, not `MutableDict`, so mutating
in place would silently fail to persist, the identical staleness-bug
class this same task also fixed in `update_source()` at 6.3).
`GET/PATCH /api/v1/dashboard/tenant[/support-details|/alert-emails|
/widget-look|/retention|/trace-text]`, `GET/POST .../site-keys`,
`PATCH .../site-keys/{id}/origins` (behind `require_recent_auth()`,
docs/SPEC.md §12's own "changing site keys or allowed origins" rule),
`GET .../install-snippet`.

**Contract additions** (Felix pre-authorised, additive-only, proven via
`scripts/check_openapi_additive.py`): (a) `verified_domains` added to
`widget_config` (`app/auth/routes.py`'s `_public_widget_config()`,
filtered to `status == "verified"`); (b) `conversation_id` added to
`/chat`'s `done` SSE event for a first turn; (c) the verified
`SessionResponse` ASSUMPTION marker deleted. All three were
widget-track-found gaps whose fix ownership the dashboard track took at
the pre-6.2 alignment task — built here as originally scheduled.

**`admin_cli.py`**: `reset-password` (prompts via `getpass`, never a
CLI argument, never logged), `set-platform-admin`/`unset-platform-admin`
— all three built ON `UserManager.update()` so the Step 6.1 session-
invalidation hook applies automatically, closing that hook's own
residual gap (the real commands finally exist; `seed_cli.py`'s own
"hand-edit the database" workaround note was removed and replaced with
a pointer at these commands).

**The real per-tenant support-details fallback template** (Part G, the
duplication-check/acceptance pass): built LATE in this batch, once the
6.6 acceptance pass's own clause-by-clause check against docs/SPEC.md
found it was the one genuinely still-open piece of 6.2's own original
scope — `_no_answer_fallback_node`/`_dependency_failure_fallback_node`/
the quota-exhausted `_fallback_node` (`app/agent/graph.py`) were still
using the Task-3.4.e-era PLATFORM-DEFAULT fixed text, with the real
`tenants.config` fields sitting unused. Fixed: `render_support_details_
text()` builds the real template (email/phone/contact-link/hours/
message interpolated into a real sentence, `None` when no contact
method is configured — falling back cleanly to the platform-default
text, since a draft site key can still serve chats before support
details exist); `app/chat/routes.py` renders it once per turn (it
already has `tenant.config` open with a real DB session, right next to
the quota/retention/trace-text checks) and threads it through the
LangGraph config — the identical config-not-state pattern
`quota_exhausted_from_config()` already established at Task 4.3 — read
back by all three fallback nodes via a new `support_text_from_config()`
accessor, still passing through `_sanitize_answer_text()` unchanged.

**[SECURITY] finding made and fixed in the same pass**: confirmed live
that `langgraph.checkpoint.base.get_checkpoint_metadata()` copies every
`configurable` entry whose value is a bare `str`/`int`/`bool`/`float`
into the checkpoint's own PERSISTED `metadata` column —
`EXCLUDED_METADATA_KEYS` is a fixed library constant this project
cannot extend. A bare `support_text` string would have leaked a
tenant's real email/phone/contact-link into a database column nothing
else expects to hold tenant contact PII. Fixed by storing it wrapped in
a single-key dict (`{"text": ...}`), which that function's own
`isinstance` filter silently skips — proven live against the real
checkpoint table, not merely reasoned about
(`test_support_text_is_never_persisted_in_checkpoint_metadata`,
mirroring `tenant_id`'s own identical Task-3.3.a guard). **A related
pre-existing gap found, not fixed, flagged in the register**:
`quota_exhausted` (Task 4.3, a bare `bool`) is NOT similarly wrapped and
DOES leak into persisted checkpoint metadata today — non-sensitive (a
boolean flag), left for whoever next touches that field.

**Frontend** (`dashboard/src/screens/SettingsScreen.tsx`,
`WidgetScreen.tsx`, `OverviewScreen.tsx`, `lib/tenant-api.ts`, new): a
shared `DataView` component (loading-skeleton/error-no-internals/
permission-denied/empty/ready, used by every data view this whole batch
builds) and `useAsyncData` hook. `SettingsScreen`: support details,
alert/escalation email, retention (with the "shortening deletes older
chats" explanation before saving), trace-text switch, site keys +
allowed origins. `WidgetScreen`: widget look (native `<input
type="color">` paired with a validated hex text input) + the real
install snippet (tells the tenant to ask their platform admin when
`PUBLIC_API_BASE_URL` is unset, rather than showing a wrong URL).
`OverviewScreen`: the real go-live checklist, built once 6.3's own
source data existed to check against — a new, read-only `GET .../
tenant/go-live-check` endpoint mirrors docs/SPEC.md's real gate (at
least one contact method AND one allowed origin) server-side; this
screen never duplicates that rule client-side. Verified domain / enabled
source are shown as real, useful signals but correctly do NOT gate
`ready_for_go_live` — they are not part of the actual server-side gate.

**A real axe-core "color-contrast" finding, fixed without hand-editing
the generated file**: shadcn's own generated `alert.tsx` destructive
variant applies a 90%-opacity red to `AlertDescription` (and, via CSS
`color` inheritance, to a nested Button too) — 4.49:1, below WCAG AA's
4.5:1. Fixed at the usage site: the Retry button moved into the
`AlertAction` slot (a sibling, outside the inheritance chain) and
`!text-destructive` (a Tailwind `!important` override) forces full
opacity on `AlertDescription` itself.

**Duplication check (n=4, due mid-batch)**: `errorMessageFor()` and the
save-failure `Alert` markup had reached their third real occurrence
across the Settings cards — extracted into `lib/error-messages.ts` and
`components/SaveError.tsx`.

### Step 6.3 — Sources screens

**Backend** (`app/dashboard/source_routes.py`, `source_schemas.py`,
new; `app/ingest/repository.py`, `upload_storage.py` extended):
`GET/POST /api/v1/dashboard/sources`, `PATCH .../sources/{id}` (urls-
type only), `POST .../sources/{id}/disable|enable|refresh-now`,
`DELETE .../sources/{id}`, `POST .../sources/database` (unconditional
400 — Felix decision A, `database`-type creation rejected server-side,
never offered in the UI), the real streaming upload endpoint
`POST .../sources/upload`, `GET/POST .../domains`,
`POST .../domains/{id}/verify`. Source status is LIVE-COMPUTED from the
most recent `Job` row (`_derive_status()`), not the pre-existing
`Source.status` column — confirmed by grepping every real writer in
`app/ingest/`: only `create_source()` ever sets it, always to
`"active"`, so it was never a live signal to begin with.

**Felix decision B**: `delete_source()` now also cleans up a deleted
`database`-type source's own `db_connections` credentials, but only
after confirming no OTHER source still references the same connection
(the schema does not enforce one connection per source).

**The real upload endpoint, with every named security property**:
`upload_id` generated/looked up ONLY through the tenant's own already-
scoped `source.config["uploads"]` list, never client-supplied; size cap
enforced WHILE STREAMING (`save_streaming()`, bounded 1MiB chunks,
abort-and-delete-partial-file on breach), not after full buffering;
reuses the existing content-based type sniffer (`sniff_file_type()` —
PDF magic bytes, ZIP+docx-marker+zip-bomb-size-check, UTF-8-decodable
→txt/md, never trusting claimed Content-Type/extension); filename never
used as a path; on a re-upload matching an existing `original_filename`
within the same source, the PREVIOUS `upload_id`'s own on-disk bytes
are deleted before the new entry replaces it — closing the Task-2.7.c
orphaned-storage marker, and the Task-2.7.c tenant-scoping marker,
together, as this was always their real intended owner.

**Fixed a real, currently-live content-lifecycle bug found along the
way**: `update_source()` overwrote `source.config` wholesale with no
diffing — a tenant removing one URL from a `urls`-type source's list
had no cleanup path anywhere (the url-list adapter only re-fetches URLs
IN the config, never discovers "missing" ones the way crawl discovery
does). Fixed: when `client`/`collection_name` are supplied, it now
computes `removed = old_urls - new_urls`, finds the matching `Document`
rows, calls `delete_points_for_document()` per stale doc, then deletes
the `Document` rows — closing the Task-2.9.g url-list-staleness marker.

**The five ingestion-limit Settings fields → PlanLimits, investigated,
mostly declined with reasons recorded**: `upload_max_size_bytes` wired
directly at its own real new consumer (the upload endpoint). The other
four (`crawl_page_cap`/`db_sync_row_cap`/`refresh_now_daily_allowance`/
`refresh_interval_min_allowed`) left UNWIRED at their existing call
sites — a small, mechanical change for three of them, a real shared-
helper signature change with existing regression coverage for the
fourth — because no real tenant has `plan_id` set yet in production, so
the wiring would have zero observable effect today against a real
signature-change risk in security-adjacent code. Re-owned to whoever
next revisits this once a real tenant actually has a plan assigned.

**Required adversarial pass, every case a committed regression test**
(`backend/tests/test_dashboard_source_routes.py`, 30+ tests): oversized
upload (413, both a small-body-with-wrong-Content-Length case and a
genuinely large streamed body); zip-bomb `.docx` (422); binary-garbage
with a spoofed extension/Content-Type (422, confirmed sniffed by real
content, not trusted labels); path-traversal filenames (proven safe via
`storage_dir.iterdir()` diffing — the real on-disk filename is never
attacker-controlled); another tenant's real `source_id`/`upload_id`
(404, "wrong tenant looks exactly like missing"); concurrent re-uploads
(`asyncio.gather`, last-write-wins with no corruption — the correct bar
for this project's own real pilot scale, not full transactional
locking); `database`-type creation via both the dedicated endpoint and
the generic create endpoint's own closed Literal type (both rejected);
CSRF on every new state-changing route (parametrized, valid bodies per
route so a 422 body-validation failure never masks the real CSRF
check); widget JWT on every new endpoint (401, structural — these
routes depend on the dashboard's own cookie-based session, which a
widget JWT cannot satisfy). Run live 3 times for stability.

**A Qdrant read-path-guard false positive, fixed the least-invasive
way**: `_document_stats()`'s own SQL `func.count()`/`func.bool_or()`
tripped `test_qdrant_read_path_guard.py`'s AST-based attribute-name
scanner (`.count(` matches `AsyncQdrantClient.count()` by bare name).
Fixed by rewriting the helper to fetch rows and count in Python instead
— never by widening the guard's own allow-list (CLAUDE.md: "never widen
a security guard's allow-list for convenience").

**Frontend** (`dashboard/src/screens/SourcesScreen.tsx`,
`lib/sources-api.ts`, new): the sources table (type, status badges,
"may need JavaScript"/"exceeds plan" flags, last/next refresh, document
count), an add-source dialog (URLs/Crawl/Upload tabs — the upload tab's
"answerable to any visitor" warning checkbox gates the submit button,
not merely a disabled-by-default state proven only visually), per-
source disable/enable/refresh-now/delete (delete names exactly what
will be removed: "N indexed pages... the visitor widget can no longer
answer from its content"), and domain verification (dns/file/meta
instructions keyed by the real `verification_token`).

**A real axe-core "button-name" finding, fixed**: the domain-
verification method `<Select>`'s trigger had no accessible name at all
(a combobox-role button with no label) — fixed with `aria-label=
"Verification method"`.

### Step 6.4 — Escalation inbox and usage

**Backend** (`app/handoff/repository.py`, `app/dashboard/
escalation_routes.py`, `escalation_schemas.py`, new): a tenant-scoped
`EscalationRepository` (list/get/count-open/set-status/delete, the same
dataclass shape as `TenantScopedRepository`). `GET /api/v1/dashboard/
escalations` (summaries only — no transcript, a lighter payload),
`GET .../escalations/open-count` (a separate, cheap query for the
sidebar's own badge, which is mounted regardless of which screen is
active), `GET .../escalations/{id}` (the full detail INCLUDING the
transcript), `PATCH .../escalations/{id}/status` (closed `Literal["seen",
"resolved"]` — "new" is creation-only, never a dashboard write),
`DELETE .../escalations/{id}`.

**Transcript display, closing the Step-6.4 half of the Task-4.4-era
marker**: `get_escalation()` applies `markdown_unescape()` (the shared
Step-4.4 helper, `app/text_escaping.py`) to the WHOLE stored transcript
snapshot — one pass over the already-joined string, not per-line like
the email body's own treatment (which iterates live `BaseMessage`
objects and can tell visitor/assistant lines apart; the dashboard only
has the already-joined stored string with no reliable per-line role
boundary left to re-split on). `markdown_unescape()`'s own docstring
names applying it to a visitor's own raw text as harmless for display
purposes, which is exactly this case. The HTML-escape half is satisfied
by construction on the frontend: `InboxScreen.tsx` renders the result
as a plain React text node (`whitespace-pre-wrap`), never
`dangerouslySetInnerHTML` — a React/DOM text node can never be
reinterpreted as markup.

**Usage meter**: `GET /api/v1/dashboard/usage` reads the AUTHORITATIVE
DB-backed count (`app.plans.usage.monthly_count_from_db()`), never the
API process's own in-memory counter — the SAME source of truth the
usage-alert sweep itself reads (`handle_usage_alert_sweep()`), so this
screen's own numbers can never disagree with an alert email the tenant
may have already received. Three states (`normal`/`warning`/`exceeded`)
at the exact same 80%/100% thresholds the sweep emails at.

**Frontend** (`dashboard/src/screens/InboxScreen.tsx`,
`UsageScreen.tsx`, `lib/escalation-api.ts`, new): the escalation table
(created/contact/status/actions), a transcript dialog (lazy-loaded on
open, rendered as the plain text described above), Mark seen/Mark
resolved/Delete actions (delete names exactly what will be removed: "a
permanently removes this escalation request and its transcript").
`DashboardShell` now fetches the real open-escalation count on mount
and passes a `reload` callback down so marking/deleting an escalation
refreshes the sidebar badge too. Usage: a quota meter with badge
variants chosen to avoid this theme's own measured-failing
`destructive` Badge contrast (found live on the Overview checklist,
see 6.6 below) — `default`/`secondary`/`outline` instead, all already
axe-proven elsewhere on this batch.

### Step 6.6 — Phase 6 acceptance

**docs/SPEC.md §18's own Phase 6 accept line, checked clause by clause
against real evidence**: *"a new tenant can onboard end to end without
touching the database."* Per docs/DECISIONS_REGISTER.md's own row ("6.6
Phase 6 acceptance | Verifies 6.1–6.4"), this verifies Steps 6.1-6.4
only — 6.5 (Platform admin, the piece that would normally cover tenant/
site-key creation) is explicitly POST-PILOT. Read literally against
THAT scope: a tenant's owner can self-serve everything docs/SPEC.md §12
names (login, support details, widget theme, retention, sources +
domain verification, escalation inbox, usage) without Felix hand-
editing JSONB in Postgres directly — the only steps still requiring an
operator action (creating the tenant row, the owner's User+Membership,
and the initial site key) go through the real `admin_cli.py` commands
built at 6.1/6.2, a proper CLI, not "touching the database." **This
line is satisfied**, under this scope.

**§12's own "Acceptance additions," each with real evidence, not
overclaimed**:
- **Screenshots, every screen, light AND dark**: 12 real screenshots
  (6 screens × 2 themes) captured via a real Playwright script against
  the real production build under the real backend, saved to the new
  gitignored `dashboard/screenshots/` folder (never committed, the
  identical "real output, not a committed fixture" treatment every
  other per-run artifact in `.gitignore` already gets) — paths listed
  for Felix in this task's own final report.
- **A real light/dark theme toggle, built in the same pass this
  requirement surfaced it was needed for**: the CSS token system
  already supported both themes (`.dark` class rules, every screen
  built on tokens only, confirmed by this whole batch's own work) but
  NOTHING in the app ever applied that class — a real, found-live gap
  in docs/SPEC.md §12's own "light and dark themes from the first
  screen" line. Fixed with `src/lib/theme.ts` (`localStorage` +
  `prefers-color-scheme` fallback, applied synchronously in `main.tsx`
  before the first render to avoid a flash-of-wrong-theme) and a
  header toggle button in `DashboardShell` — no new dependency
  (`next-themes` was tried and reverted earlier in this track for
  being unpinned).
- **Keyboard-only pass + automated accessibility check, every screen,
  committed as a real regression test**: a new `e2e/dashboard-
  screens.spec.ts` logs in ONCE (not once per screen — a real per-
  account dashboard login rate limit, docs/SPEC.md §12's own "rate-
  limited login with temporary lockouts", was tripped live by this
  task's own earlier repeated manual testing, confirming the limit is
  real and working) and, for each of the 6 screens: runs a real axe
  scan (zero serious/critical findings required), and a keyboard-only
  Tab pass using REAL DOM-NODE IDENTITY comparison (a `JSHandle`
  before/after each Tab press), not a derived text signature — a text-
  signature version was tried first and produced a FALSE keyboard-trap
  finding on the Widget screen (two distinct, unlabelled `<input>`
  fields share the same empty textContent/no-aria-label, so a text
  signature can't tell them apart even though focus genuinely moved).
  A short screen legitimately running out of tab stops before 8
  presses (focus leaves the page for browser chrome) is treated as
  normal end-of-page behaviour, not a trap.
- **Loading/empty/error(no internals)/permission-denied for every data
  view**: verified directly, not assumed — every one of the 12
  `DataView` usages across all 6 screens gets loading/error/
  permission-denied automatically (the shared component's own
  unconditional branches); `isEmpty`/`empty` is correctly present on
  every LIST-shaped view (Sources' sources+domains, Settings' site
  keys, Inbox's escalations) and correctly absent on every single-
  record view (Overview, Usage, Widget's/Settings' own tenant config),
  where "empty" has no meaning.

**Duplication check (overdue, caught at this step)**: the counter had
passed n=4 three times since the last reset (6.2 Part F) without a
check running — `test_dashboard_escalation_routes.py` had reimplemented
its own local `_dashboard_http_client()`/`_attach_csrf()`/
`_create_user()`/`_login()`, a FOURTH occurrence of the exact pattern
`tests/dashboard_test_helpers.py` was already extracted for at Step 6.3
(that module's own header comment literally says "every dashboard
route test file should import from here instead of redefining these").
Fixed: imports the shared module directly.

**§9 markers and register reconciled**: every Phase-6-owned marker
found by a dedicated research pass is now RESOLVED in PROJECT_SPEC.md
§9 with the real file/test naming the fix, or left correctly open with
its own reason restated (the per-plan ingestion-limit deferral; the
escalation `reason` field's fixed-string limitation, D-44-reason-
fixed-string — a genuine, accepted limitation, not this step's job to
change the schema). `docs/DECISIONS_REGISTER.md`'s own "6.2"/"6.3"/
"6.4" forward-commitment sections all marked RESOLVED with what was
actually built.

### Prior decisions applied (full list, cited again here for the
permanent record — see this task's own final report for the complete
version with file/test citations)

Every 6.2/6.3/6.4 forward commitment named in docs/DECISIONS_REGISTER.md
as of this batch's start, plus Decisions A (database-type source
rejected server-side) and B (db_connections cleanup on delete) from
Felix's 2026-10-15 ratified decisions — see the register's own "6.2
Tenant settings"/"6.3 Sources screens"/"6.4 Escalation inbox and usage"
sections (now marked RESOLVED) for the complete, dated list.

### Verified (final, authoritative pass, end of batch)

- `make lint`: clean (ruff, whole `backend/evals/hooks.py`).
- Full `backend/tests` (both real test services, `TEST_DATABASE_URL` +
  `TEST_QDRANT_URL`): **1721 passed, 0 failed** (up from 1572 at Step
  6.1's own close-out).
- `dashboard/`: `npx tsc --noEmit` clean; `npm run lint` clean (3
  pre-existing shadcn-boilerplate warnings, unrelated); `npx vitest
  run` **41 passed** (0 failed); `npm run build` succeeds within
  budget.
- `dashboard/e2e` (`npm run test:e2e`, real strict-CSP production
  build against the real backend): **6/6 passing** (the original 5
  `csp.spec.ts` tests plus the new `dashboard-screens.spec.ts`
  per-screen a11y/keyboard/CSP pass), run 3x for stability.
- `backend/tests/test_dashboard_source_routes.py`'s own adversarial
  pass: run live 3x.
- OpenAPI additive-only proof (`scripts/check_openapi_additive.py`):
  confirmed at every contract-touching sub-step — 9 new paths after
  6.2 Part B, 9 more after 6.3 Part B, 5 more after 6.4 Part A, 1 more
  after 6.2 Part F (go-live-check) — 0 changed/removed paths at any
  point; unchanged (0 diff) after the 6.2 Part G support-details fix
  (internal graph wiring only, no endpoint shape touched).
- Live end-to-end smoke test, the real HTTP API, not a unit test:
  seeded a real tenant's own quota to exhausted (1/1 via a real `Plan`
  row + `UsageEvent`, forcing a container restart so the API's own
  in-memory counter reloaded from the DB), sent a real chat message
  through the real session+chat flow, and confirmed the fallback
  reply embedded the tenant's own real configured support email —
  proving the `tenants.config` → `render_support_details_text()` →
  `chat/routes.py` → LangGraph config → fallback node chain end to
  end, not just at the unit level.
- Rebase: `.env.example`/`backend/tests/test_env_example.py` conflicts
  with the ops track's own Step 7.3 prep work resolved by keeping BOTH
  sides' lines (every variable given its own comment line, confirmed
  via `test_every_variable_line_has_a_comment_line_directly_above_it`);
  full suite re-run clean after the rebase.
- `git status --short`: clean after each commit; no `.env` ever
  staged.
- A real Docker Desktop engine outage occurred mid-batch (confirmed
  live via `ps aux` — the build/test processes were genuinely stalled,
  not merely slow, their own CPU time frozen across multiple checks);
  recovered by killing the one confirmed-dead process and restarting
  the disposable test containers fresh, per explicit instruction —
  nothing destructive touched without that instruction.
