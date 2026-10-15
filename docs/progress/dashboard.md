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
