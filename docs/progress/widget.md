# docs/progress/widget.md

Track: `track/widget`. Shape per PROJECT_SPEC.md §11.5 point 3 (dated entries:
what was built, what was verified). Folded into PROJECT_SPEC.md §4/§5/§7/§9
and removed when this track merges.

Duplication counter (rule 9, this track's own, starting at n=0): **n=1**
after this task (one code sub-step: Step 5.1). Not due (threshold is 4).

## Step 5.1 — Loader and shadow DOM shell (plumbing) (2026-10-15)

**Scope**: `widget/` npm package (esbuild bundler, kickoff decision #1); the
loader (`widget.js`) that reads `data-site-key`/`data-base-url`, mounts an
open shadow root, and lazy-loads the chat UI bundle on first click; unit
tests; a Playwright headless-browser test under a real strict CSP; the
size budget (real for the loader, placeholder for 5.2's chat bundle);
`.github/workflows/widget.yml` CI.

**Package layout**: `widget/package.json` (devDependencies only — the
loader itself ships zero runtime dependencies; Preact arrives with the
chat UI at 5.2): `esbuild` 0.28.2 (bundler, decision #1), `@playwright/test`
1.64.1 (pre-approved, the one thing a Node unit test cannot prove — see
below), `eslint` 10.12.0 + `@eslint/js` 10.0.1 (lint; no third-party
plugins, just the recommended ruleset). `.nvmrc` pins `24.15.0`, matching
this machine's real installed Node (`process.release.lts === "Krypton"`,
confirmed live, not assumed) — not a floating major. `package-lock.json`
committed; `npm ci` is what CI and this verification both use.

**Loader design**:
- `loader/src/config.js` — pure config resolution (`resolveConfig`), takes
  a `{getAttribute, src}`-shaped object, never touches `document`/`window`.
  `data-site-key` and `data-base-url` are both REQUIRED, no fallback:
  decision #6 (PROJECT_SPEC.md §5) says the loader's own hostname is never
  assumed to be the API's hostname, so defaulting `data-base-url` to the
  script's own origin would bake in a topology guess. `data-chat-url` and
  the new `data-css-url` both default to a URL resolved relative to the
  loader's own script `src` (same-origin co-location convenience), always
  overridable.
- `loader/src/widget.js` — the entry point. Captures
  `document.currentScript` **synchronously at top-level script execution**,
  before any deferred mount (a real gotcha: `currentScript` reverts to
  `null` once any later callback, e.g. `DOMContentLoaded`, runs — capturing
  it inside a deferred function would silently break on sites that include
  this script in `<head>` without `defer`). Stays a classic (non-module)
  script for exactly this reason: `document.currentScript` is always
  `null` for `<script type="module">`.
- Shadow root: **open**, not closed — a deliberate reversal of my own first
  instinct. Closed mode buys no real security property here: nothing
  secret ever lives in the shadow tree (the session token stays in a JS
  variable, never in the DOM), so the only thing closed mode would change
  is making the widget harder to test/debug, for zero isolation gain over
  open mode (CSS/DOM-structure isolation from the host page is a shadow-DOM
  property either way). Matches the common choice in comparable production
  chat widgets.
- Styling: `loader/src/widget.css`, loaded via a real `<link
  rel="stylesheet">` inside the shadow root — **not** an inline `<style>`
  element, and **not** a Constructable Stylesheet (`new CSSStyleSheet()` +
  `adoptedStyleSheets`). I initially built the Constructable-Stylesheet
  version on the (commonly repeated, but wrong in current Chromium)
  assumption that it bypasses CSP `style-src`. The Playwright test caught a
  real `style-src 'self'` violation on the very first run; tracing the
  violation's SHA-256 hash to its real source turned up a SEPARATE,
  self-inflicted bug in my own test fixture (an inline `<style>` block in
  the host-page HTML, violating the very CSP I'd configured the test
  server to send) — not evidence either way about Constructable
  Stylesheets. Rather than ship an unverified claim in a code comment, I
  fixed the fixture (moved its styles to an external file) and switched
  production code to `<link>` regardless: it is the long-established,
  zero-ambiguity CSP-compatible mechanism (same reasoning `<script src>`
  already requires for `script-src`), so there was no reason to keep
  relying on a claim I could not actually stand behind. Confirmed live,
  cleanly, with the real fix: zero CSP violations.
  `:host { all: initial }` is what stops the host page's own inherited
  CSS (color, font-family, ...) crossing into the shadow tree.
- `loader/src/chat-loader.js` — lazy-loads the chat bundle via a plain
  `<script src>` (never eval, never inline) on first launcher click,
  idempotent (a second click before the first load finishes inserts
  nothing further).
- No globals leaked: the IIFE output assigns nothing onto `window`. No
  public JS API is exposed — nothing in docs/SPEC.md §11 asks for
  host-page programmatic control in v1; if that need appears later it
  becomes one deliberate namespaced global, not a default.

**Size budget**: `scripts/budgets.mjs` is the single source of truth for
both numbers (docs/SPEC.md §11/§17) — `LOADER_BUDGET_BYTES = 4096`,
`CHAT_UI_BUDGET_BYTES = 61440` — shared by `scripts/check-size.mjs` (the
CI "size budget" step) and `loader/test/size-budget.test.mjs` (so `npm
test` alone also catches a regression). The loader's real built size:
**866 bytes gzipped**, well inside its ~4KB budget. The chat-UI check is a
real placeholder, not a fabricated one: it looks for `chat/dist/chat.js`,
and skips (not fails, not silently absent) with an explicit message when
that file doesn't exist yet — Step 5.2's own build is what turns this into
a real, enforced check.

**Tests**:
- `loader/test/config.test.mjs` (8 cases, Node's built-in `node:test` +
  `node:assert/strict` — no DOM-emulation dependency added; see below) —
  normal resolution, both URL overrides, missing-site-key, missing-base-url,
  empty-string site-key and empty-string base-url (bad input: empty),
  empty `src`/malformed `src` with no override (bad input: malformed/empty).
- `loader/test/size-budget.test.mjs` (2 cases) — the real loader-budget
  assertion, and the chat-UI placeholder (skips with its own message while
  `chat/dist/chat.js` doesn't exist).
- `test/e2e/widget.spec.mjs` (Playwright, pre-approved dev dependency) —
  loads a fixture page served by `test/e2e/server.mjs` (stdlib `node:http`,
  no new dependency) with a REAL strict CSP **response header**
  (`default-src 'none'; script-src 'self'; style-src 'self'; connect-src
  'self'; img-src 'self'; base-uri 'none'`, no `'unsafe-inline'`/
  `'unsafe-eval'` anywhere) against a host page with deliberately clashing
  styles (Comic Sans, red text, yellow background, itself loaded as an
  external stylesheet so the fixture doesn't violate its own CSP). Asserts,
  against a real Chromium: the shadow root mounts and contains the
  launcher; zero CSP violations are reported; the host page's styles don't
  leak into the shadow tree; the widget's styles don't leak onto the host
  page; the chat bundle is not fetched before the first click, is fetched
  on it, and a second click does not insert a second `<script>` tag; no
  global beyond the test's own stub marker leaked onto `window`.

**Why DOM-touching logic has no Node unit test, and no DOM-emulation
dependency (e.g. jsdom) was added**: the pure, DOM-free logic (config
resolution) is unit-tested in plain Node; everything that actually touches
`document`/shadow roots/CSP (mounting, style isolation, lazy-load) is
proven instead by the Playwright test, against a REAL browser DOM and a
REAL CSP enforcement engine — strictly more faithful than a DOM-emulation
library would be for exactly the properties this step most needs proven
(CSP compliance, style leakage), so adding one would have been a second,
weaker proof of the same thing (rule 8: no dependency without a real need).

**CI**: `.github/workflows/widget.yml` — `npm ci`, lint, unit tests, build,
size budget, Playwright browser install + e2e test, `npm audit`, in that
order. Kept out of `ci.yml` per the parallel-track protocol. Covered by
`backend/tests/test_ci_guard.py`'s GENERIC workflow checks (which scan
every file under `.github/workflows/`, not just `ci.yml`/`security.yml`):
pinned 40-hex action SHAs (`actions/checkout` v7.0.1 — already used in
`ci.yml`; `actions/setup-node` v5.0.0, the real latest tag, confirmed via
`git ls-remote --tags`), `contents: read` only, no `pull_request_target`,
no `secrets.` reference, a job timeout, `persist-credentials: false` on
checkout. Confirmed live against the real test, not just by inspection.

**One backend/ edit, Felix-authorized mid-task, not made unilaterally**:
I initially held off on touching anything under `backend/`, including the
one pre-existing test that would obviously break
(`backend/tests/test_layout_placeholders.py`'s "widget/ and dashboard/
contain nothing but a README" guard) — the track rules say never edit
`backend/` without a stop condition, and §11.5 point 4 says to report a
needed `backend/` edit rather than silently make it. I built the rest of
Step 5.1 fully, ran `make lint` + the offline suite, and was about to
report this guard-test failure as a blocker for Felix's decision when
Felix's own message arrived mid-task, directly authorizing exactly this
edit (citing the pre-existing §9 markers that already name Step 5.1 as
this test's owner — PROJECT_SPEC.md §9, "Owner 5.1: remove or update
`backend/tests/test_layout_placeholders.py` when tooling is added to
`widget/`"). Edited narrowly, per Felix's "keep edits minimal" instruction:
split the old `test_readmes_name_their_owning_step_and_say_placeholder`
into two (one requiring the step mention for all three READMEs, one
requiring the word "placeholder" only for the two still-genuinely-empty
ones — `widget/chat`, `dashboard`), and narrowed the "only a README"
structural check's root list from `(widget/, dashboard/)` to
`(widget/chat/, dashboard/)`. `widget/loader/README.md` no longer claims
to be a placeholder (it isn't). The Owner-6.1 marker (dashboard/) is
completely untouched, so that track's own eventual edit to this same file
stays a clean, independent addition.

**Additional npm supply-chain setup, per Felix's mid-task addition**
(PROJECT_SPEC.md §5's 2026-09-28 decision-log entry, which this step was
already the named owner of): an `npm` `package-ecosystem` entry for
`/widget` added to `.github/dependabot.yml`; an `npm-audit` job added to
`.github/workflows/security.yml` (same weekly schedule as the existing
`python-audit` job, plus `widget/package.json`/`widget/package-lock.json`
added to that workflow's push/pull_request `paths:` filters) — both edits
additive only, so the dashboard track's own later edits to these same
shared files stay simple, independent additions.

**A real environment finding, unrelated to the widget's own code**: this
machine has no per-worktree Python virtualenv — `pip install` targets
whatever interpreter is on `PATH` globally. The system `python3` here is
3.13 (miniconda base), which does not satisfy backend's `==3.12.*`
`requires-python` pin; installing against it left the editable
`widgetplatform` package broken (26 unrelated-looking import/contract
failures in `test_main.py`/`test_session.py`/`test_chat_routes.py`/
`test_openapi_contract.py`). The correct interpreter already existed on
this machine (`/opt/miniconda3/envs/widgetplatform`, Python 3.12.14,
already populated with nearly every dependency — presumably from the
dashboard track's own prior setup in its own worktree) and gives a clean
**1063 passed, 422 skipped** both lint and full offline suite, re-run twice
more for stability, identical each time. Flagged because the two tracks'
worktrees apparently share one global Python environment on this machine,
not an isolated one per worktree — a `pip install` from either track can
observably change what the other sees (I had to reinstall several
packages to their lockfile-pinned versions, overwriting whatever was
there). Not something this task should fix (no code change implied, and
it is infrastructure both tracks share), but worth Felix knowing before it
causes a confusing cross-track `make test`/`make lint` discrepancy.

**Verified** (widget/, repeated clean 3x where a new test file was
involved, per this project's convention): `npm ci` — 0 vulnerabilities.
`npm run lint` — clean. `npm test` — 10 passed, 1 skipped (chat-UI
placeholder), stable across 3 runs. `npm run build` — `dist/widget.js`
1.8kb raw. `npm run size` — loader 866 bytes gzipped (budget 4096); chat UI
correctly reported not-yet-built. `npm run test:e2e` — 1 passed, stable
across 3 runs (after the fixture fix above). `npm audit` — 0
vulnerabilities.

## Part A — widget.yml CI was red on `main`, fixed with root cause (2026-10-15)

**Symptom**: Felix reported both `widget.yml` runs so far (push `5ecf8d9`,
run #1; a Dependabot PR bumping `actions/setup-node` 5.0.0→7.1.0, run #2)
failed in CI in ~16-19s, despite every step passing locally at Step 5.1's
own close-out.

**Root cause, found via the real GitHub Actions API** (`gh` is not
installed in this environment; the repo's public, unauthenticated REST API
was enough: `GET /repos/.../actions/workflows/{id}/runs`, then
`/actions/runs/{id}/jobs`): both runs show an IDENTICAL step-level
breakdown — Checkout/Set up Node/Install/Lint all `success`, **Unit tests
`failure`**, everything after (Build, Size budget, Playwright install, the
e2e test, Audit) `skipped`. This by itself rules out every Node-version/
lockfile/action-SHA hypothesis the task prompt suggested checking: run #2
used a DIFFERENT `actions/setup-node` version (7.1.0, not 5.0.0) than run
#1 and failed at the exact same step with the exact same shape, and "Set
up Node"/"Install" both succeeded in both runs. The real cause was a
step-ordering bug in `widget.yml` itself: **"Unit tests" (`npm test`) was
placed BEFORE "Build" (`npm run build`)**, but
`loader/test/size-budget.test.mjs` asserts the real gzipped size of
`dist/widget.js`, which only the Build step produces — on a clean
checkout, `npm test` alone finds no `dist/` at all.

This was invisible in every local verification run during Step 5.1's own
close-out for a specific, identifiable reason: I manually ran `npm run
build` immediately before `npm test`, every single time, as part of my
own ad hoc verification sequencing — never once in the literal order the
committed `widget.yml` actually specifies. Confirmed by direct
reproduction: temporarily restoring the OLD (pre-fix) `widget.yml`
against a clean `dist/` and running the steps in that file's own real
order reproduces the exact CI failure, byte-for-byte the same assertion
message (`dist/widget.js was not found -- run \`npm run build\` before
\`npm test\``); restoring the fix and re-running end to end passes cleanly.

**Fix**: reordered `widget.yml` to Install → Lint → Build → Unit tests →
Size budget → Playwright install → e2e → Audit (Build moved before Unit
tests; everything else unchanged).

**New tool, so this class of bug cannot recur silently**:
`widget/scripts/run-ci-locally.mjs` (stdlib only) reads the real
`.github/workflows/widget.yml`, extracts the `widget` job's `run:` lines
IN THEIR REAL COMMITTED ORDER, and executes each one with
`cwd: widget/` (matching the workflow's own `defaults.run.working-directory`)
— deliberately driven from the real file rather than a hand-written
second copy of the step list, so a future reordering is caught by this
same script automatically, not only by a parallel list someone has to
remember to update. Wired as `npm run ci:local`. Proved it actually
catches this exact bug (not just that it runs): pointed at the OLD
`widget.yml` with a clean `dist/`, it reproduces the real failure
character-for-character; pointed at the fixed file, it passes end to end
(`npm ci` → lint → build → unit tests → size → Playwright install → e2e →
audit, all real, all green).

**New process rule** (written here and into PROJECT_SPEC.md §11.5 point
6, per direct instruction): a brand-new workflow file is not "verified"
or "passing" on my own say-so until its first REAL CI run is observed
green — my previous report's "confirmed live... widget.yml... covered by
test_ci_guard.py" language was true of the generic structural checks, but
I should not have let that read as the workflow itself being proven; it
wasn't, and in fact wasn't even correct. From here on such a claim is
reported as "pending Felix's confirmation" until a real green run is
seen (here, via the public Actions API — ``GET
/repos/felixkipyego/SamoFlow/actions/workflows/385684958/runs`` — which I
checked directly rather than assuming the fix is sufficient).

**Status of this fix, stated precisely, not overclaimed**: the local
`run-ci-locally.mjs` reproduction is strong evidence the ordering fix is
correct, but it is run on my own machine, not GitHub's `ubuntu-24.04`
runner — it does not by itself prove the real run will be green (a
runner-specific issue, e.g. a resource constraint during the Playwright
browser install, remains possible and was never the failure mode observed
here anyway). **Pending Felix's confirmation that the real push's
`widget.yml` run on `main` is green.**

## Part B — per-worktree Python environment (2026-10-15)

**Problem, confirmed from Part A's own 5.1 close-out**: this machine has
no per-worktree virtualenv; both `track/widget` and `track/dashboard`
worktrees were observed sharing the single global conda env
`/opt/miniconda3/envs/widgetplatform`, whose editable `widgetplatform`
install can only ever point `app`/etc. at ONE worktree's `backend/` at a
time — whichever track last ran `pip install -e ./backend` there.

**Fix**: `.venv` created in THIS worktree
(`/Users/mac/.../SamoFlow-widget/.venv`), from the pinned interpreter
(`/opt/miniconda3/envs/widgetplatform/bin/python -m venv .venv` —
`backend/pyproject.toml`'s own `requires-python = "==3.12.*"`, confirmed
`python --version` → `3.12.14`), then `--require-hashes -r
backend/requirements-dev.lock` and `--no-deps -e ./backend` installed
into it. Already covered by the existing `.venv*/` `.gitignore` pattern
(confirmed via `git check-ignore -v .venv/bin/python` and `git status
--short`) — no `.gitignore` change needed.

**Proof `import app` resolves to THIS worktree, not the other one**:
```
app module file: .../SamoFlow-widget/backend/app/__init__.py
this worktree:   .../SamoFlow-widget
resolved under this worktree: True
```
`git worktree list` confirms three distinct worktrees exist on this
machine (`SamoFlow` @ main, `SamoFlow-dashboard` @ track/dashboard,
`SamoFlow-widget` @ track/widget) — `app.__file__` resolves under
`SamoFlow-widget` specifically, never under either of the other two.

**From here on, `.venv/bin/python`/`.venv/bin/ruff` (equivalently,
`source .venv/bin/activate` then the plain `make lint`/`python -m
pytest` invocations) are what this track uses for every backend command**
— confirmed via a real `ruff check` (all checks passed) and a real
`pytest tests -q` (**1063 passed, 422 skipped**, identical to the
shared-env run) through `.venv` directly. The shared conda env is left
alone (not uninstalled from, not relied on going forward) — fixing this
track's own isolation doesn't require touching what the dashboard track
has installed there.

**Verified** (backend/, using the correct `/opt/miniconda3/envs/widgetplatform`
Python 3.12.14 interpreter): `ruff check backend evals hooks.py` — all
checks passed. `python -m pytest tests -q` — **1063 passed, 422 skipped**,
run twice more for stability, identical both times. The three guard test
files touched by this task's edits (`test_layout_placeholders.py`,
`test_ci_guard.py`, `test_dependabot_guard.py`) run standalone, 3x: 12
passed every time. `git status --short` cross-checked: no `.env`; no
`node_modules/`/`dist/`/`test-results/`/`playwright-report/` staged
(confirmed via `git status --ignored=matching`); the `backend/` diff is
scoped to exactly the one Felix-authorized file
(`git diff --stat -- backend/ docs/openapi.json` shows only
`test_layout_placeholders.py`, `docs/openapi.json` untouched).

## Part C — contract check for Step 5.2 (2026-10-15)

**Field names, read from the real locked contract, not assumed**:
`docs/openapi.json`'s `SessionResponse` = `session_token`, `visitor_secret`
(nullable), `test_mode`, `widget_config` (loosely typed
`additionalProperties: true` — a dict, so its real keys live in code, not
the schema). Read `backend/app/auth/routes.py` directly for those real
keys: `_PUBLIC_WIDGET_CONFIG_FIELDS = (title, greeting, colors, logo_url,
position, privacy_notice_url)`, built by `_public_widget_config()` picking
named keys one at a time from `tenant.config` (never a raw spread — a
private key structurally cannot leak). **Confirmed adequate for Step
5.2's own needs**: `title`/`greeting`/`colors` are consumed directly by
the Chat UI built below; `logo_url` by its header; `position`/
`privacy_notice_url` belong to later steps (5.1 launcher placement, 5.4
escalation panel) but are harmless to have now. Recorded as a new §9
marker (PROJECT_SPEC.md) that this VERIFIES — does not merely carry
forward — `SessionResponse`'s own 1.4.g ASSUMPTION marker; the actual
marker-comment deletion in `backend/app/auth/schemas.py` is left for
whoever next legitimately touches that file, per the `backend/` edit
restriction (§11.5 point 4) — not made unilaterally here.

**Verified domains: confirmed ABSENT from the contract.** `grep`-ed the
whole backend for `verified_domain`: the `verified_domains` table (Task
2.2) is real, but is used only by ingestion's own domain-verification/
crawl-safety path — never read by `_public_widget_config()` or anywhere
in `/session`'s response. Per direct instruction, this does NOT stop the
task: Step 5.2's renderer (below) allows a link only when it is `https`
AND is one of THIS TURN's own real citation URLs
(`app/agent/graph.py`'s `Citation.url`, round-tripped through the
`citations` SSE event as `{items: [{n, title, url}]}` —
`app/chat/routes.py::_build_citations_event()`) — narrower AND safer
than a domain allowlist (a citation is a specific page SamoFlow itself
just retrieved and cited for this exact answer; a domain match would
allow any path on that domain, retrieved or not). New §9 marker added,
owner the dashboard track (which owns the real `tenants.config` write
path, Step 6.2): add `verified_domains` to `widget_config` ADDITIVELY —
a new key, never a replacement of the existing six or a change to
`_public_widget_config()`'s own allow-list mechanism — so Step 5.2's
shipped renderer needs no shape change to later adopt it as a second,
separately-gated allow rule.

**The rest of the real contract, read directly from code (not
documented in `docs/openapi.json`, which cannot express SSE streams or
dynamically-raised status codes) — needed to build Part D against real
behavior, not assumption**:
- Auth: `Authorization: Bearer <session_token>`, plus the real `Origin`
  header must appear exactly once and match the token's own origin claim
  (`get_current_visitor`, `app/auth/dependencies.py`) — satisfied
  automatically by `fetch()`, no special widget-side handling needed.
- SSE event vocabulary, all four read from `app/chat/routes.py`:
  `token` (`{text}`, an incremental append — NOT guaranteed one LLM token
  per event), `citations` (`{items: [{n, title, url}]}`, emitted once,
  after the full answer), `done` (`{message_id, usage, offer_escalation}`),
  `error` (`{code, message}`, three fixed codes: `model_error`, `timeout`,
  and a declared-but-currently-never-emitted `dependency_unavailable` —
  the widget handles all three defensively regardless).
- Fixed HTTP failure shapes, all real, all with a fixed `{"detail": ...}`
  body: `413` message-too-long; `429` rate-limited (+`Retry-After`,
  per-IP and per-visitor, separate limiters); `503` twice (quota-counter
  startup not ready, +`Retry-After: 5`; a DB pool-checkout timeout, no
  `Retry-After`); `409` three times (another stream already in progress
  for this visitor; this conversation's lease is held elsewhere; this
  `client_message_id` was already answered/is stale); `401` (missing/
  invalid/origin-mismatched token, structurally prior to all of the
  above). The quota-exhausted reply is NOT a distinct HTTP/SSE shape at
  all — Task 4.3's own design streams it through the exact same
  `token`/`citations`/`done` sequence as any other answer, with a fixed
  tenant-support-details message and no mention of quota, by deliberate
  server-side design (docs/SPEC.md §11) — so the widget needs no special
  quota detection, only the same calm, fixed-text treatment for the
  handful of real error/busy statuses above.

**No other contract gap found; no stop condition (a) raised.** Everything
Step 5.2 needs (session creation, streaming chat, citation URLs for
link-gating, a complete fixed error vocabulary) already exists in the
locked contract.
