# SamoFlow — PROJECT_SPEC (living context — read at the start of every session, update after every task)

## 1. What we are building and why

A production-ready, multi-tenant, embeddable AI widget platform: one website is one tenant, with its own site key(s) and its own `client_id` in Qdrant. The widget answers visitors only from that tenant's own content (crawled pages, listed URLs, uploaded files, synced database data), streams answers in real time, remembers a returning visitor's earlier chats in the same browser, and hands off to a human through an escalation request when the AI cannot help. Full detail: docs/SPEC.md.

## 2. How to work (rules for every session)

1. At the start of a session, read this file and tell me the current task, the next task and any open markers. Do not write code until I say so.
2. One task at a time. Implement only what I name. Do not modify completed tasks.
3. When starting a component, create a skeleton first: stubs that raise NotImplementedError with a TODO(<task-id>) comment explaining what each does, its inputs and its outputs.
4. Keep code simple with short comments. After writing code, add inline markers: ASSUMPTION (something I should verify), UNCERTAIN (something you are unsure about), TODO(<task-id>) (something incomplete). If similar logic already exists, add a NOTE pointing to it instead of duplicating it.
5. Write tests with the code, covering normal cases, edge cases, expected failures and bad inputs (empty, None, wrong type, oversized). Run them and show the real output. Never weaken, skip or delete a test to get green.
6. Finish each task with a report: what changed, test results, deviations from the spec, open questions. Then update this file and stop. Do not start the next task.
7. If the spec is ambiguous, or you want to deviate from it, stop and ask. Record approved deviations under Decisions made.
8. Ask before adding any dependency (name, purpose, alternatives considered).
9. Run a duplication check after every 3rd task, and never go past 4 tasks without one.
10. Clean code means resolved markers: verify then remove ASSUMPTION, resolve then remove UNCERTAIN, finish then remove TODO. A phase is not accepted while any ASSUMPTION or UNCERTAIN marker remains, and every remaining TODO must be listed under Open markers with the task that owns it.
11. Follow widely adopted industry practice and library defaults, including standard security measures: validate input at trust boundaries, use least privilege and secure defaults, keep secrets and sensitive data out of code, logs and error messages, keep dependencies current, and use well-known framework or library mechanisms instead of custom security schemes. Do not add abstractions, options, checks or tests the task does not need. When the choice is between a standard default and a custom rule, use the default unless the spec says otherwise. Security requirements in the spec and in the engineering rules are never optional and always win over simplicity. If you are unsure whether something is needed for security, or you think something is over-engineered, too rigid or insecure, say so and ask me before building it.

## 3. Engineering rules and commands

- Commands: make up, make test, make lint, make migrate, make evals, make hooks (lists TODO, ASSUMPTION, UNCERTAIN and NOTE markers in the code).
- Tenant identity comes only from the verified token. Never accept client_id, visitor id, thread_id or checkpoint ids from the client. Verify conversation_id by database lookup.
- All Qdrant reads go through retrieve(), and the client_id filter is applied inside every sub-query.
- All tenant-scoped database access goes through the tenant-scoped repository helpers.
- The model never writes contact details and has no tool with side effects. Escalation is submitted by the visitor's browser.
- Everything is async. No blocking calls in handlers. Never hold a DB connection while streaming.
- Migrations via Alembic only. Secrets never in the repository.
- Treat all ingested text, tool output and visitor messages as untrusted input.
- Nothing hard-coded that should be configuration (limits, thresholds, paths, URLs).
- Any deviation from the spec is recorded in the decisions log (see SPEC section 3) in the same change.
- After each phase, run the acceptance checks and summarize deviations before starting the next.

## 4. Components and status

| Step | Component | Status | Notes |
|------|-----------|--------|-------|
| 0.1 | Understand the spec | Done | Completed 2026-09-19 |
| 0.2 | Create PROJECT_SPEC.md | Done | Completed 2026-09-19 |
| 1.1 | Repo skeleton and Compose | In progress | 1.1.a–1.1.h done |
| 1.1b | Whole-application skeleton | Not started | – distinct from subtask 1.1.b |
| 1.1c | Marker check | Not started | – |
| 1.2 | Database, migrations and tenant-scoped access | Not started | – |
| 1.3 | Qdrant collection [SECURITY] | Not started | – |
| 1.4 | Session endpoint and JWT [SECURITY] | Not started | – |
| 1.5 | Rate limiter and limits | Not started | – |
| 1.6 | Phase 1 acceptance | Not started | – |
| 2.1 | Ingestion tables and job queue | Not started | – |
| 2.2 | Domain verification [SECURITY] | Not started | – |
| 2.3 | Safe fetcher (SSRF guard) [SECURITY] | Not started | – |
| 2.4 | Extraction and chunking | Not started | – |
| 2.5 | Embedding and Qdrant upserts | Not started | – |
| 2.6 | URL-list and crawl adapters | Not started | – |
| 2.7 | Upload adapter [SECURITY] | Not started | – |
| 2.8 | Database sync adapter [SECURITY] | Not started | – |
| 2.9 | Scheduler, plans and reconcile | Not started | – |
| 2.10 | Phase 2 acceptance | Not started | – |
| 3.1 | Hybrid retrieval [SECURITY] | Not started | – |
| 3.2 | Checkpointer and guarded entry point [SECURITY] | Not started | – |
| 3.3 | Graph skeleton and router | Not started | – |
| 3.4 | Answer node, citations and fallback | Not started | – |
| 3.5 | Follow-up rewrite | Not started | – |
| 3.6 | Tool wrapper and MCP knowledge search [SECURITY] | Not started | – |
| 3.7 | Golden set and eval runner | Not started | – |
| 3.8 | Phase 3 acceptance | Not started | – |
| 4.1 | Chat endpoint (SSE) [SECURITY] | Not started | – |
| 4.2 | Conversations endpoints and retention sweep | Not started | – |
| 4.3 | Usage, quotas and alert emails | Not started | – |
| 4.4 | Escalation [SECURITY] | Not started | – |
| 4.5 | Phoenix tracing | Not started | – |
| 4.6 | Phase 4 acceptance | Not started | – |
| 5.1 | Loader and shadow DOM shell | Not started | – |
| 5.2 | Chat UI core [SECURITY] | Not started | – |
| 5.3 | Conversations UI | Not started | – |
| 5.4 | Escalation panel and fallback UI | Not started | – |
| 5.5 | Theming, accessibility and CSP | Not started | – |
| 5.6 | Phase 5 acceptance | Not started | – |
| 6.1 | Dashboard authentication [SECURITY] | Not started | – |
| 6.2 | Tenant settings | Not started | – |
| 6.3 | Sources screens | Not started | – |
| 6.4 | Escalation inbox and usage | Not started | – |
| 6.5 | Platform admin | Not started | – |
| 6.6 | Phase 6 acceptance | Not started | – |
| 7.1 | Load tests | Not started | – |
| 7.2 | Full security and injection run [SECURITY] | Not started | – |
| 7.3 | Backups, restore drill and monitoring | Not started | – |
| 7.4 | Go-live checklist and spec reconciliation | Not started | – |

## 5. Decisions made

- 2026-09-19: Shadow DOM isolates the widget from the host page (SPEC §11).
- 2026-09-19: One website = one tenant, with its own site key(s) and `client_id` (SPEC §4.1).
- 2026-09-19: Visitors are recognized by browser now; logged-in users (`external_user_id`) are a later phase (SPEC §4.1, §19).
- 2026-09-19: A visitor can have several conversations, shown as a "previous chats" list (SPEC §4.5).
- 2026-09-19: Chat retention is per-tenant, default 30 days, allowed range 1–90 days (SPEC §4.6).
- 2026-09-19: Content sources: URL list, crawl, uploads, database sync, any combination (SPEC §5.1).
- 2026-09-19: All sources are answerable to any visitor; a warning is shown when uploads/DB content is added; one-click disable/delete per source (SPEC §5).
- 2026-09-19: Domain ownership is verified once (file, meta tag or DNS), never re-checked; the platform admin can revoke or force re-verification. Revocation stops fetching only — already-indexed content stays answerable until the source is disabled/deleted or the site is suspended (SPEC §5.5).
- 2026-09-19: English only for v1 (SPEC §3).
- 2026-09-19: Refresh cadence is daily/weekly/monthly/quarterly plus refresh-now, gated by plan (SPEC §5.4).
- 2026-09-19: Plan limits live in a `limits` JSON, assigned manually; payments come later (SPEC §5.4).
- 2026-09-19: Crawler is simple (no JS rendering) for v1 (SPEC §5.5, §19).
- 2026-09-19: Upload types: PDF, .docx, .txt/.md (SPEC §5.6).
- 2026-09-19: Database sync is scheduled first; approved live queries are a later phase (SPEC §5.6, §19).
- 2026-09-19: Supported database engines: MySQL/MariaDB and PostgreSQL first (SPEC §5.6).
- 2026-09-19: Hybrid search (dense + sparse BM25, server-side RRF); reranker hook off by default (SPEC §6.3).
- 2026-09-19: Follow-up questions are rewritten by a small fast model; original message used on rewrite failure/timeout (SPEC §6.3).
- 2026-09-19: Three specialists (Retrieval, Fallback & Guardrail, Escalation Coordinator), rule-based routing, no LLM hop (SPEC §6.1–6.2).
- 2026-09-19: The AI has no tools with side effects; escalation is submitted by the visitor's browser (SPEC §8).
- 2026-09-19: We build and run tools first; tenant tool servers are a later phase (SPEC §8, §19).
- 2026-09-19: Unanswerable questions get a fixed fallback with the tenant's support details, never a guess (SPEC §6.3).
- 2026-09-19: Citations are shown for web pages only; no labels for uploads or database content (SPEC §6.3).
- 2026-09-19: Human handoff is an escalation request (email + dashboard inbox), retained 12 months by default, allowed range 1–24 months (SPEC §7).
- 2026-09-19: Monthly quota counts AI-answered messages only; tenant alerted at 80% and 100% (SPEC §9).
- 2026-09-19: Abuse defense is rate limits only for v1; a challenge (e.g. Turnstile) is deferred (SPEC §9, §19).
- 2026-09-19: A dropped connection cancels the run with no partial answer saved; retry reuses the same `client_message_id` (SPEC §10).
- 2026-09-19: Dashboard login is email/password, with an owner who can invite team members (SPEC §12).
- 2026-09-19: Widget customization is limited to colors, logo, title, greeting and position; no custom CSS in v1 (SPEC §11).
- 2026-09-19: Hosting is one Ubuntu server with Docker Compose for v1 (SPEC §14, §19).
- 2026-09-19: Phoenix trace text is stored 30 days then auto-deleted, with a per-tenant opt-out (SPEC §13).
- 2026-09-19: Plan page cap counts total indexed web pages only (crawl + URL-list); uploads are capped by storage/size and database sync by row count (SPEC §5.4, §5.5).
- 2026-09-19: Supported database engines confirmed as MySQL/MariaDB and PostgreSQL (SPEC §5.6).
- 2026-09-19: The relevance threshold is a platform default; only the platform admin can override it per tenant, and it is not tenant-editable (SPEC §6.3).
- 2026-09-19: Email is sent through our own SMTP server, behind a provider-neutral interface (SPEC §14).
- 2026-09-19: Escalation trigger phrases live in a YAML file in the repo (roughly 50–200 entries, each with a category and a golden-set test reference), schema-validated in CI; every change goes through a deploy and the golden-set tests (SPEC §7).
- 2026-09-19: A site key moves from `draft` to `live` only through a server-side check (at least one contact method and one allowed origin); draft keys work from allowed origins with a test-mode badge (SPEC §4.1, §6.3).
- 2026-09-19: CI runs a Postgres service container so the Alembic upgrade-head test (1.1.h) runs in CI from day one; that test is an integration test reading `TEST_DATABASE_URL`, skipped locally with an explicit message when unset, and must fail (not skip) when `CI=true` and the var is missing. Qdrant is not needed in CI yet (Step 1.1).
- 2026-09-19: Backend targets Python 3.12, pinned in pyproject.toml, the Dockerfile and CI (Step 1.1).
- 2026-09-19: Step 1.1 dependencies approved (rule 8) — runtime: fastapi==0.141.1, uvicorn==0.53.0, pydantic-settings==2.15.0, alembic==1.20.0, sqlalchemy==2.0.54, psycopg[binary]==3.3.6; dev: pytest==9.1.1, pytest-asyncio==1.4.0, httpx==0.28.1, ruff==0.16.8. Pins verified against PyPI on 2026-09-19 (task 1.1.a), replacing the December 2024 pins first recorded here.
- 2026-09-19: Build backend is hatchling==1.32.3, approved as a build-time-only dependency (rule 8), version verified on PyPI (Step 1.1.a).
- 2026-09-19: pytest-asyncio==1.4.0 approved as a dev dependency (rule 8; declares support for pytest>=8.4,<10, compatible with our pytest==9.1.1 pin); `[tool.pytest] asyncio_mode = "auto"` so async test functions need no per-test marker (Step 1.1.a).
- 2026-09-19: ruff `[tool.ruff.lint] select` includes "B" (bugbear) and "S" (bandit/security) in addition to E/F/I/UP; `tests/*` is exempted from S101 (assert use) via per-file-ignores (Step 1.1.a).
- 2026-09-19: Deviation — `pip install -e .` cannot succeed until `backend/app/` exists, so that check moves from 1.1.a to 1.1.b's tests; 1.1.a verifies `backend/pyproject.toml` by parsing it with `tomllib` only (Step 1.1.a; see task table note on 1.1.b).
- 2026-09-19: Step 1.1.b — `backend/app` and its 12 §21 subpackages, plus `backend/tests`, are empty importable placeholders (no NotImplementedError stubs); each `__init__.py` carries a NOTE pointing to PROJECT_SPEC.md's component table instead of a TODO, since the table already tracks which future step fills each one in. The deferred `pip install -e ./backend[dev]` check and full test run needed Python 3.12: none was on PATH, but the user has a conda env `widgetplatform` at `/opt/miniconda3/envs/widgetplatform` with Python 3.12.14; the check ran in a clean venv created from that interpreter (`/opt/miniconda3/envs/widgetplatform/bin/python -m venv ...`), not the conda env itself.
- 2026-09-19: Naming: subtasks of step 1.1 are written with dots (1.1.a to 1.1.n); steps 1.1b (whole-application skeleton) and 1.1c (marker check) are separate steps, written without a dot.
- 2026-09-19: Step 1.1.c — Settings (backend/app/config.py) reads only the process environment (no env_file), is frozen, and is served through get_settings() (functools.lru_cache). DATABASE_URL is a SecretStr, validated to require the `postgresql+psycopg` scheme; QDRANT_URL is validated to require http/https. Verified against the installed pydantic 2.13.5 / pydantic-settings 2.15.0: `hide_input_in_errors=True` hides a bad value from str()/repr() of a ValidationError but not from the structured exc.errors() list, which still carries the raw input. Frozen-instance assignment raises pydantic_core.ValidationError, not TypeError.
- 2026-09-19: Duplication check run after 1.1.c (nothing to consolidate).
- 2026-09-19: Startup code reads settings only via get_settings(), which raises SettingsError with input-free messages; never log exc.errors().
- 2026-09-19: DATABASE_URL must include scheme postgresql+psycopg, a hostname and a database name.
- 2026-09-19: Guard test enforces that only get_settings() constructs settings, .errors() is never called, and database_url_str() has an explicit allow-list of callers.
- 2026-09-20: Product name: SamoFlow (renamed from VileSite on 2026-09-20; brand name only; code, packages and images keep the neutral name widgetplatform until a rename task is scheduled).
- 2026-09-20: Step 1.1.d — `.env.example` declares exactly the five variables Settings requires (no more, no less); required names are derived from `Settings.model_fields` in the test plus an `EXTRA_EXAMPLE_KEYS` constant (empty tuple today) for any future deliberate addition. `API_HOST=0.0.0.0` is the container-listen placeholder; `DATABASE_URL`'s password placeholder is exactly `change-me`, asserted by a dedicated test so a real secret pasted over it would be caught.
- 2026-09-20: Step 1.1.e — `create_app(settings: Settings | None = None)` in `backend/app/main.py` builds the FastAPI app; a `None` settings argument goes through `get_settings()` only (never `Settings()` directly, enforced by the existing config guard test since it scans every file under `backend/app`). Nothing at module level reads the environment. Title is the neutral `"widgetplatform API"`; version comes from `importlib.metadata.version("widgetplatform")` (works because Task 1.1.b's editable install makes the package resolvable). `/health` (backend/app/health.py) is liveness-only and touches neither Postgres nor Qdrant. Docs/redoc/openapi are disabled only when `app_env == "production"`. No CORS, middleware, database or Qdrant code, and no module-level `app` variable (Compose/Docker will run `uvicorn app.main:create_app --factory` from 1.1.g/1.1.i onward).
- 2026-09-20: Step 1.1.f — `backend/app/worker.py`'s `run(stop)` calls `get_settings()` only, logs one INFO line containing `app_env` only (never database_url/qdrant_url), then awaits the stop event; `main()` configures logging, registers SIGTERM/SIGINT handlers via `loop.add_signal_handler` that set the stop event, and exits 0 on clean shutdown or non-zero with no traceback on `SettingsError`. No job logic, no polling loop, no dependency on Postgres/Qdrant. Verified by hand: with the signal-handler registration removed, `SIGTERM`'s default OS disposition still terminates the (unhandled) process quickly rather than hanging it — the process exits with code -15 (terminated by signal), not 0, so the test still fails fast on `assert exit_code == 0` rather than by hitting the 10-second `wait_for` timeout.
- 2026-09-20: Duplication check and simplification review after 1.1.d/e/f: shared conftest.py, three redundant tests deleted, env_file guard added; dependency-set test to be removed when the lockfile task (1.1.o) lands.
- 2026-09-20: Step 1.1.h — Alembic skeleton generated with `alembic init` (default generic template, alembic==1.20.0) then trimmed: `alembic.ini` keeps `script_location = %(here)s/alembic` (the tool's own default), drops the noise comments and the unused `sqlalchemy.url` placeholder line (no database URL lives in the ini); the generated `README` is deleted; `alembic/versions/.gitkeep` keeps the empty directory in git (git does not track empty folders, and Alembic fails if the folder is missing). `alembic/env.py` gets its URL from `get_settings().database_url_str()` and builds the engine directly with `sqlalchemy.create_engine(url, poolclass=NullPool)`; it never calls `config.set_main_option` — verified by hand that pattern breaks with `ValueError: invalid interpolation syntax` on a literal "%" in the password, and would also copy the secret into the Config object. Offline mode is dropped (not "enough extra code to be free"): supporting it without `set_main_option` would need its own URL-plumbing, so only online mode exists; `target_metadata = None` with a `TODO(1.2)` marker (models arrive in 1.2). `backend/tests/test_config_guard.py` (Task 1.1.c's guard) now also scans `backend/alembic` and allow-lists `alembic/env.py` in `ALLOWED_DATABASE_URL_CALLERS` — verified by hand that a scratch file elsewhere calling `database_url_str()` still fails the guard.
- 2026-09-20: Step 1.1.h — Test database is Option A: integration tests read `TEST_DATABASE_URL` from the environment (never construct one from parts). `require_test_database()` (backend/tests/conftest.py) is the single guarded entry point: unset + `CI` != "true" → `pytest.skip` with the local `docker run` command; unset + `CI` == "true" → `pytest.fail` (CI must run this test, not silently skip it); set but not a `postgresql+psycopg` URL naming a database ending in `_test` → `pytest.fail`, never skip, and before anything is touched — this is the safety rail that keeps the test's `DROP SCHEMA public CASCADE` off a real database. `backend/tests/test_alembic.py`'s integration test resets the schema through a sync SQLAlchemy engine, then runs `python -m alembic upgrade head` as a subprocess (harness env: `APP_ENV=test`, `QDRANT_URL`/`API_HOST`/`API_PORT` harmless, `DATABASE_URL` = the test URL) twice, asserting exit 0 both times, that the public schema then contains exactly `alembic_version` (Alembic does create it with zero revisions), and that neither run's captured stdout/stderr contains the real password.
- 2026-09-20: Step 1.1.h — Postgres tag for the local/CI test database: `postgres:18.2-trixie`, verified to exist with `docker manifest inspect` (18.2 is the newest patch of the newest stable major, 18, published for Debian trixie as of 2026-09-20; 19.0 and 18.3 do not exist yet).
- 2026-09-20: Step 1.1.h — `backend/Dockerfile`'s final stage now also copies `alembic.ini` (`COPY --chmod=0644`) and `alembic/` (`COPY --chmod=0755`, so the directory stays traversable) alongside the existing `entrypoint.sh` copy — root-owned, not writable by the app user (verified by hand: `touch` as uid 10001 fails with Permission denied on both). `entrypoint.sh` gained a third mode, `migrate`, that runs `exec alembic upgrade head`; it does not check `API_HOST`/`API_PORT` itself, since `migrate` never uses them and `get_settings()` still validates all five required variables when `alembic/env.py` calls it. Usage message updated to `{api|worker|migrate}`.
- 2026-09-20: Step 1.1.h ruff — `backend/tests/test_alembic.py`'s `subprocess.run` call needed one inline `# noqa: S603` (bandit's "check for execution of untrusted input"): its argument list is fixed test-internal strings (`sys.executable`, `"-m"`, `"alembic"`, `"upgrade"`/`"head"`/`"history"`), not user input. No per-file-ignore was needed in `pyproject.toml`, and the template `env.py` (once rewritten per the design above) passes lint with no ignores at all.
- 2026-09-20: Step 1.1.h refinement (post-review) — `backend/tests/test_alembic.py`'s `_reset_public_schema`/`_table_names` take a pre-built SQLAlchemy `Engine`, never a raw URL string, and wrap their connect/execute calls in `except SQLAlchemyError` that re-raises a plain `RuntimeError` naming only `engine.url.host`/`engine.url.database` and the exception class, via `raise ... from None`; a `_test_engine` fixture builds the real `Engine` so the raw password is a local only in the fixture's own frame, which is never part of a test-body failure's traceback. A dedicated test (`test_reset_schema_failure_never_reveals_the_password_in_a_rendered_traceback`) proved this: it failed against the old code (the password appeared, both via psycopg's own internal frame locals and via the test's own local variable) and passes against the new code, checked with `traceback.TracebackException(..., capture_locals=True)` — strictly more revealing than pytest's default rendering.
- 2026-09-20: Step 1.1.h refinement — `_run_alembic`'s subprocess environment is now built explicitly (`_subprocess_env()`): the five `Settings` variables plus `PATH`/`HOME` from the parent, nothing else; a stray `PGPASSWORD`/`PGHOST`/`PGUSER` in the developer's or CI's shell can no longer reach the child process. Verified with a unit test that sets those three and asserts they're absent from the built env dict.
- 2026-09-20: Step 1.1.h refinement — `test_env_py_compiles_cleanly` (`py_compile.compile(doraise=True)`) added: `alembic history` never calls `env.py`'s `run_env()` (confirmed by reading Alembic's own `command.history` source — it only runs the environment when `revision_environment` is set or `--indicate-current` is passed, neither true here), so without this test a syntax error in `env.py` would pass every check on the common no-database path. The `alembic history` test's docstring now says only what it proves (ini + versions/ directory valid), not that it exercises `env.py`.
- 2026-09-20: Step 1.1.h refinement — `alembic/env.py` catches `SettingsError` from `get_settings()` and prints `str(exc)` to stderr with `sys.exit(1)`, mirroring `app/worker.py`'s `main()`; a subprocess test with an empty environment asserts a non-zero exit, every missing field named, and no `"Traceback"` in stderr.
- 2026-09-20: Step 1.1.h refinement — `require_test_database()` gained a host guard after the existing scheme/suffix check: the URL's host must be `localhost`, `127.0.0.1`, or listed (comma-separated, case-insensitive) in `TEST_DATABASE_ALLOWED_HOSTS`; otherwise `pytest.fail` with a message naming only the (non-secret) hostname. This closes the gap the 1.1.h review found: a `..._test`-named database on an arbitrary reachable host (e.g. a shared/staging server) previously passed the guard untouched — verified with a unit test reproducing that exact example (`prod-db.internal.example.com` + `legacy_customer_test`), which now fails.
- 2026-09-20: Step 1.1.h refinement — `app/config.py`'s `_POSTGRES_SCHEME` renamed to the public `POSTGRES_SCHEME` (no behavior change, two use sites, both in the same file); `backend/tests/conftest.py` imports it instead of duplicating the `"postgresql+psycopg"` literal.
- 2026-09-20: Step 1.1.g — base image pinned to `python:3.12.14-slim-trixie` (Debian trixie is the current stable Debian release; 3.12.14 is the latest 3.12 patch published for it as of 2026-09-20); existence verified with `docker manifest inspect python:3.12.14-slim-trixie` before use (returned a valid multi-arch manifest list). `psycopg[binary]` kept for v1 instead of building from source against the system libpq: the container is rebuilt on every deploy to pick up base-image patches, and the vulnerability scan (1.1.o) will cover the bundled libpq/OpenSSL; revisit in Phase 7 if that scan flags them. `uvicorn --no-server-header` confirmed supported by the pinned uvicorn==0.53.0 (`uvicorn --help` lists `--server-header / --no-server-header`) and used in entrypoint.sh's `api` mode so the `server:` response header is suppressed. Non-root numeric UID/GID 10001 (no login shell, no home dir), WORKDIR /app, no HEALTHCHECK, no .dockerignore yet (1.1.n).
- 2026-09-20: Step 1.1.g refinement — multi-stage build: a builder stage (pip + hatchling, both build-time only) installs the package into a venv at /venv; the final stage is the same pinned base image, copying in only /venv and entrypoint.sh, with `pip uninstall --yes pip` run against /venv at the end of the builder stage so pip never reaches the runtime image (verified: `python -m pip --version` in the built image fails with `No module named pip`). File modes come from `COPY --chmod=0755 entrypoint.sh ...` plus the builder's own root-owned-by-default output for /venv (both COPY instructions run before `USER 10001`), which is simpler than a separate `chmod`/`find` pass and gives the same result: /app and /venv are root-owned and not writable by the app user (verified by hand: `touch` inside either as uid 10001 fails with Permission denied). The `find __pycache__`/`*.pyc` cleanup step is removed — it only mattered when app/ was copied straight into the shipped image; now app/ is copied only into the discarded builder stage, and pip's wheel build already excludes bytecode. The Dockerfile documents UID 10001 as a fixed convention (not host-assigned), so file ownership stays stable across rebuilds and hosts; Compose (1.1.i) must reuse this same UID wherever it sets `user:` or touches ownership of files from this image. Image content size changed only marginally (63,312,007 → 62,831,945 bytes) since the prior single-stage build had no apt build tooling to strip — the main gain is removing pip/hatchling from the runtime image, not size. Entrypoint refinement: `api` mode now checks `API_HOST`/`API_PORT` itself (`[ -z ... ]` before the `exec`) and prints one `entrypoint.sh: <VAR> is required for mode 'api'` line to stderr with exit 2, instead of surfacing `set -u`'s raw "parameter not set" shell error; the script does not duplicate Settings' own validation (scheme checks, etc.) — only the two variables it uses itself.
- 2026-09-20: Step 1.1.i — Qdrant tag `qdrant/qdrant:v1.19.1`: found via the Docker Hub tags API (all 418 tags of `qdrant/qdrant`, filtered to the `vX.Y.Z`-only stable pattern — excluding `dev-*`, `-preview-*` and other branch/experiment tags — then sorted by semver; v1.19.1 is the highest), then confirmed to exist with `docker manifest inspect qdrant/qdrant:v1.19.1` (valid manifest list). Postgres stays on the tag already pinned in 1.1.h, `postgres:18.2-trixie`, re-verified the same way.
- 2026-09-20: Step 1.1.i — `postgres:18.2-trixie`'s declared `VOLUME` is `/var/lib/postgresql`, not the pre-18 `/var/lib/postgresql/data` path; `PGDATA` is a subdirectory of it (`/var/lib/postgresql/18/docker`) — verified with `docker image inspect postgres:18.2-trixie --format '{{json .Config.Volumes}}'` and `--format '{{json .Config.Env}}'`. The named volume (and test-db's tmpfs) mount at `/var/lib/postgresql`.
- 2026-09-20: Step 1.1.i — healthcheck methods verified by hand inside the pinned images: qdrant has neither `curl` nor `wget`, only `bash`/`sh` (`sh` is `dash`, which has no `/dev/tcp`), so its healthcheck is `CMD` (not `CMD-SHELL`) invoking `bash -c` directly to speak raw HTTP to `/healthz` over `/dev/tcp` — proved working with a live container before writing it into Compose. The api image has no `curl` either (Task 1.1.g's slim runtime), so its healthcheck uses Python's stdlib `urllib.request` against `GET /health`, already on `PATH` via the app's own venv.
- 2026-09-20: Step 1.1.i — `migrate` (`profiles: ["tools"]`) and `test-db` (`profiles: ["test"]`) never run on `docker compose up`; each needs its profile passed explicitly (`--profile tools`, `--profile test`).
- 2026-09-20: Step 1.1.i — hardening (`read_only: true` + tmpfs `/tmp`, `cap_drop: [ALL]`, `security_opt: ["no-new-privileges:true"]`, `mem_limit: 512m`, `pids_limit: 200`) applied to api, worker and migrate only (our own image, built from `backend/`); not applied to postgres, qdrant or test-db (upstream images not audited for read-only-rootfs compatibility). Verified end to end: postgres/qdrant/api all reach "healthy", `migrate` exits 0 twice, and the full backend test suite (86 tests) passes against the compose-managed `test-db`, all with hardening in place on api/worker/migrate.
- 2026-09-20: Step 1.1.i — environment passed explicitly via a `x-app-env` YAML anchor with `${NAME:?NAME must be set in .env}` interpolation (no `env_file:`), aliased as-is into api/worker/migrate's `environment:`; postgres's `POSTGRES_USER`/`POSTGRES_PASSWORD`/`POSTGRES_DB` use the same fail-fast form. Verified: `docker compose config` fails immediately (before any container starts) with a message naming the missing variable when `DATABASE_URL` is absent from the env file. Compose resolves `.env` from `deploy/` (this file's own folder), not the repo root, so every command in this repo must pass `--env-file .env` explicitly; documented in a header comment in `deploy/docker-compose.yml`.
- 2026-09-20: Step 1.1.i — `.env.example` gained `POSTGRES_USER`/`POSTGRES_PASSWORD`/`POSTGRES_DB` (placeholders `widgetplatform`/`change-me`/`widgetplatform`, matching `DATABASE_URL`), each with a comment naming which container variable it must match; added to `test_env_example.py`'s `EXTRA_EXAMPLE_KEYS`. The `TODO(1.1.i)` header line is removed. New `backend/tests/test_env_consistency.py` cross-checks `DATABASE_URL`'s user/password/host/database against `POSTGRES_USER`/`POSTGRES_PASSWORD`/`"postgres"`/`POSTGRES_DB`, and `QDRANT_URL`'s host against `"qdrant"`; it reuses `test_env_example.py`'s parser (import, not duplication) and withholds the password from its failure message (proved by hand: a wrong `POSTGRES_PASSWORD` in a scratch edit fails without printing either value; a wrong host fails naming both non-secret values).
- 2026-09-20: Step 1.1.i refinement (post-review) — api/worker/migrate's tmpfs `/tmp` now has an explicit `size=64m` (`tmpfs: - /tmp:size=64m`); verified inside a running container: `mount` shows `tmpfs on /tmp type tmpfs (rw,nosuid,nodev,noexec,relatime,size=65536k)`, i.e. the standard `noexec`/`nosuid`/`nodev` defaults are unchanged. Added short comments: the api healthcheck only proves the process is alive, not that Postgres is reachable; postgres's `POSTGRES_USER`/`PASSWORD`/`DB` apply only when the data volume is first created; the qdrant healthcheck's `bash`/`/dev/tcp` choice should be revisited if the image ever drops `bash`.
- 2026-09-20: Duplication check and simplification review after 1.1.g/h/i: Compose anchors, Dockerfile base-image ARG, shared VALID_ENV, hint points to Compose, test classes flattened, hardening guard test added; A8, B2, B3 declined.
- 2026-09-20: Step 1.1.k — `evals/run.py` is a stdlib-only, ~15-line placeholder (`main()` prints `"no evals defined yet"`, returns 0; `sys.exit(main())` under `__main__`) for the golden-set runner (task 3.7); it imports nothing from `backend/app`, reads no environment variable and touches no network. `backend/tests/test_evals_placeholder.py` locates it via `Path(__file__).resolve().parents[2] / "evals" / "run.py"` and runs it as a subprocess with `cwd` a temporary directory and `env` limited to `PATH`/`HOME` (same restricted-env pattern as 1.1.h's `test_alembic.py`), asserting exit 0, exact stdout, and empty stderr; its `subprocess.run` call needed the same `# noqa: S603` reasoning already used there (fixed, test-internal argument list, not user input). Verified by hand in an isolated scratch copy (real files untouched) that the test fails correctly two ways: `run.py` exiting 1 (`AssertionError: assert 1 == 0`) and `run.py` printing a changed message (`AssertionError` diff showing the added `!`).
- 2026-09-20: Step 1.1.k ruff finding, fixed: root-level `ruff.toml` (one line, `extend = "backend/pyproject.toml"`, plus a comment) makes `evals/` inherit backend's `select = ["E","F","I","UP","B","S"]` and `line-length = 100` instead of ruff 0.16.8's own defaults (line-length 88; a broader prefix set that still excludes some individual codes ruff treats as non-default even within an otherwise-enabled category — e.g. S101/S105 are not flagged bare but are flagged once `S` is explicitly selected, as backend does). Verified: `ruff check --show-settings` now reports an identical enabled-rule-code list and `line_length = 100` for `evals/run.py` and a `backend/` file; `ruff check backend evals` (repo root) and `cd backend && ruff check .` both pass; the `tests/*` → S101 per-file-ignore still resolves only against `backend/tests/*` (its `absolute_matcher` is anchored under `backend/pyproject.toml`'s own directory) regardless of which directory ruff is invoked from. Effectiveness proof (scratch file added under `evals/`, then removed; real `run.py`/`README.md` untouched): a file with `PASSWORD = "hunter2"` and a bare `assert` passed cleanly under ruff's bare defaults in an isolated directory with no config, but is flagged (S105, S101) once placed under `evals/` in this repo with the fix applied.
- 2026-09-20: Step 1.1.j — Makefile (repo root), plain GNU Make with POSIX shell recipes, `COMPOSE = docker compose --env-file .env -f deploy/docker-compose.yml`, default goal `help`. Targets and what each does: `help` (one line per target, plain `echo`); `env` (creates `.env` from `.env.example` only if `.env` does not already exist — never overwrites; every target touching `$(COMPOSE)` depends on it); `up` (`env`, then `$(COMPOSE) up -d --build`); `down` (`$(COMPOSE) down`, keeps volumes); `down-volumes` (`$(COMPOSE) down -v`, prints a destroys-data warning first, kept as a separate target from `down` so volume loss is never a flag away from an ordinary teardown); `migrate` (`env`, then `$(COMPOSE) run --rm migrate` — runs in Docker, so it never depends on the caller's working directory or local Python environment); `test` (`cd backend && python -m pytest tests -q`, no database needed — the Alembic integration test skips itself); `test-db` (`env`, then `$(COMPOSE) --profile test up -d test-db`, then a `pg_isready`-polling loop, 30 x 1s, failing with a clear message on timeout); `test-db-down` (`$(COMPOSE) --profile test stop test-db` then `rm -f -s -v test-db`); `test-all` (starts `test-db`, then one shell recipe that runs the suite from `backend/` with `TEST_DATABASE_URL` set only for that pytest invocation, captures its exit code explicitly, always calls `test-db-down`, then exits with the captured code — cleanup and the tests' real exit status never depend on make's own error handling); `lint` (`ruff check backend evals` from the repo root); `evals` (`python evals/run.py` from the repo root). Every recipe fails on first non-zero exit (no leading `-`, no `|| true`).
- 2026-09-20: Step 1.1.j implementation notes — `docker compose run/up/exec/stop/rm` all work on an explicitly-named service without its profile being activated (verified by hand: `run --rm migrate` and `up -d test-db`/`exec`/`stop`/`rm` on `test-db` all succeeded without `--profile`); `--profile test` is still passed on the `test-db` calls to match the convention `deploy/docker-compose.yml`'s own header comment already established in 1.1.i, `--profile tools` is not added to `migrate` for the same reason (that convention omits it). `test-all`'s recipe wraps its `cd backend && ... pytest` step in a subshell `(...)`: without it, the `cd` would still be in effect for the rest of the same shell recipe, and the following `$(MAKE) test-db-down` call would then resolve `deploy/docker-compose.yml` relative to `backend/` instead of the repo root.
- 2026-09-20: Step 1.1.j — `backend/tests/test_makefile_guard.py` (stdlib-only, path derived from `Path(__file__).resolve().parents[2] / "Makefile"`) parses the Makefile into a target → recipe-lines map and asserts: all 12 targets declared; `.PHONY` lists them; the `COMPOSE` variable contains both `--env-file .env` and `-f deploy/docker-compose.yml`; no recipe line contains the literal text `docker compose` outside the `COMPOSE` variable's own definition (i.e. every compose call goes through the variable); no recipe line starts with `-`; no recipe line contains `|| true`; `down`'s recipe never contains `-v` while `down-volumes`'s does. Verified by hand in a scratch copy (real Makefile untouched) that each of the 7 assertions fails correctly when the rule it guards is broken (missing target, `.PHONY` gap, missing `COMPOSE` flag, a direct `docker compose` call, a leading `-`, an added `|| true`, and `-v` on the wrong target).
- 2026-09-20: Step 1.1.j verification — `make help`/bare `make` lists all 11 non-`.PHONY`-only targets; `make env` run twice leaves `.env` byte-identical (SHA-256 compared); `make lint`, `make evals`, `make test` (96 passed, 1 skipped) all pass; `make up` brings postgres/qdrant/api to `healthy`, `/health` returns 200, `make migrate` exits 0 twice, `make down` empties `docker compose ps -a`, and `make up` again succeeds; `make test-all` passes 97 tests with none skipped and leaves no `test-db` container running afterward; a scratch copy of the Makefile with a wrong `TEST_DATABASE_URL` password proved `make test-all` returns a non-zero exit while still removing the `test-db` container; `make test-db` / `make test-db-down` bring the container up healthy and remove it cleanly; `make down-volumes` removed the leftover `postgres-data`/`qdrant-data` volumes and the network, and with `.env` then deleted, no widgetplatform containers/volumes/networks remained and `git status` showed no `.env`. `make -C <repo root> lint` from `/tmp` passed, confirming no dependency on the caller's working directory.
- 2026-09-20: Step 1.1.l — `widget/loader/README.md`, `widget/chat/README.md` and `dashboard/README.md` are the only files under `widget/` and `dashboard/`, each under 12 lines, naming its owning step (5.1; 5.2–5.5; 6.1–6.5) and docs/SPEC.md section (§11 widget, §12 dashboard), and stating it is a placeholder with no build tooling — the bundler is chosen in step 5.1 (both widget folders share one bundle), dashboard tooling in step 6.1; no framework/bundler is pre-decided beyond what §11/§12 already fix (Preact widget in a shadow DOM, React dashboard SPA). `backend/tests/test_layout_placeholders.py` (stdlib only) asserts each README exists, is non-empty, names its step and says "placeholder" (case-insensitive), and that `widget/`/`dashboard/` contain no file other than `README.md` anywhere in their trees (`Path.rglob("*")`, skipping directories) — the failure message names the rule (tooling must be decided in the owning step). Verified by hand in a scratch copy (real files untouched) that deleting a README and adding a `package.json` each fail the test correctly.
- 2026-09-20: Duplication check after 1.1.k/j/l (rule 9, triggered at n=3): `Path(__file__).resolve().parents[2]` (repo root) was byte-identical across `test_evals_placeholder.py`, `test_makefile_guard.py` and the new `test_layout_placeholders.py`; moved to `conftest.py` as `REPO_ROOT`, imported the same way `VALID_ENV` already is (`from tests.conftest import REPO_ROOT`). Verified: full suite still 99 passed, 1 skipped; lint clean. Counter reset to n=0 after the check (matching the reset after the 1.1.c and 1.1.g/h/i checks), rather than left at the triggering value.

### Estimates to measure

- The default limits in §9 and the budgets in §17 are starting points.
- The server size in §14 is an estimate.
- The rule that trace text is switched off automatically for tenants with chat retention under 30 days is accepted.
- API paths use the `/api/v1` prefix.

## 6. Project constraints

- Out of scope for v1 (deferred): logged-in visitors, live human handoff, approved live database queries, tenant-supplied tool servers, actions with human approval, JavaScript-rendered crawling, a request challenge (e.g. Turnstile), the reranker (unless evals justify it), spreadsheet uploads, payment processing, multiple languages, custom widget CSS, 2FA/Google sign-in, stream resume (SPEC §19).
- Single-server hosting only: one Ubuntu server with Docker Compose; add no extra services until a scale-out trigger in §17 fires (SPEC §14).
- English only for v1 (SPEC §3).
- The reranker hook exists but stays off by default (SPEC §3).
- The AI has no tools with side effects (SPEC §3, §8).
- Domain verification is once-only; the platform admin can revoke it; revocation stops fetching only (already-indexed content stays answerable) (SPEC §3, §5.5).
- Email goes through our own SMTP server behind a provider-neutral interface (SPEC §3, §14).
- The relevance threshold is not tenant-editable (SPEC §3, §6.3).
- The page cap counts web pages only; uploads and database rows have their own limits (SPEC §3, §5.5).
- Escalation trigger phrases live in a YAML file in the repo, not a database table (SPEC §3, §7).

## 7. Current task and next task

Current: Step 1.1 Repo skeleton and Compose, subtask 1.1.m CI workflow.
Next: Step 1.1.n .gitignore and .dockerignore.
Do not modify (completed tasks): 1.1.a, 1.1.b, 1.1.c, 1.1.d, 1.1.e, 1.1.f, 1.1.g, 1.1.h, 1.1.i, 1.1.j, 1.1.k, 1.1.l.

### Step 1.1 task list (approved)

| ID | Goal | Status |
|----|------|--------|
| 1.1.a | pyproject.toml: `widgetplatform` package, Python 3.12, pinned runtime/dev dependencies | Done |
| 1.1.b | backend/app package skeleton (§21 subpackages: auth, tenancy, plans, chat, agent, retrieval, ingest, handoff, notifications, mcp, admin, telemetry); also owns the `pip install -e .` check, deferred from 1.1.a because `backend/app/` doesn't exist until this task creates it | Done |
| 1.1.c | Settings module: env-var configuration, validated at startup | Done |
| 1.1.d | .env.example matching Settings | Done |
| 1.1.e | FastAPI app factory (`create_app`) + GET /health | Done |
| 1.1.f | Worker entrypoint stub | Done |
| 1.1.g | Dockerfile: pinned base image (no `latest`), non-root user, api/worker entrypoints, uvicorn started with `--factory` | Done |
| 1.1.h | Alembic skeleton (no versions yet); upgrade-head integration test reads `TEST_DATABASE_URL` (skip locally if unset, fail in CI) | Done |
| 1.1.i | docker-compose.yml: postgres, qdrant, api, worker; only the api port published, bound to 127.0.0.1; postgres/qdrant ports not published; Qdrant healthcheck verified to work inside the pinned image | Done |
| 1.1.k | evals placeholder (moved before the Makefile, which calls it) | Done |
| 1.1.j | Makefile: up, test, lint, migrate, evals | Done |
| 1.1.l | widget/ and dashboard/ skeleton folders | Done |
| 1.1.m | CI workflow: lint + test, with a Postgres service container for the Alembic integration test; Qdrant not needed yet | Not started |
| 1.1.n | .gitignore and .dockerignore: exclude .env/secrets, virtualenvs, caches and build output; keep .env.example tracked | Not started |
| 1.1.o | Lockfile with hashes for backend dependencies (including transitive ones) and a vulnerability scan (pip-audit or equivalent) wired into CI. Tool choice needs approval under rule 8 when this task is broken down | Not started |

## 8. Task counter since the last duplication check

n = 0 (run the duplication check at 3; never exceed 4) — reset after the 1.1.k/j/l check

## 9. Open markers

- Note (owner to be scheduled, not tied to a task yet): when APP_ENV=production, reject the placeholder password change-me at startup in get_settings(). Schedule it with the deployment steps (1.1.g / Phase 7) or a Settings refinement.
- Owner Phase 7: allowed Host header validation (currently any Host is accepted; decide with the proxy configuration). Not addressed by 1.1.i — that is app-level middleware, out of docker-compose.yml's scope.
- Owner: to be scheduled — CORS and OPTIONS handling: decide with the widget and admin work (Phase 5 and 6); today OPTIONS returns 405.
- TODO(2.1): `backend/app/worker.py`'s `run()` has no job logic yet; the job queue consumer is added in Step 2.1.
- Owner 1.1.o: remove the exact dependency-set test in test_pyproject.py when the lockfile and CI audit exist.
- TODO(1.1.m): CI must build the widgetplatform-backend image and run the 1.1.g smoke checks (a to f) as part of the pipeline.
- Owner 1.1.n: .dockerignore must exclude __pycache__, *.pyc, .pytest_cache, .ruff_cache, tests, .env, .git (a stray .pyc was found in the image during the 1.1.h review).
- Owner 1.1.o: image vulnerability and secret scanning (the built image itself, not just the dependency lockfile).
- Owner CI or Phase 7: verify the image builds and runs on arm64, not just the amd64 host it was built on so far.
- Owner Phase 7: shutdown behaviour of the API under load (1.1.g verified graceful shutdown at idle; 1.1.i verified api/worker stop in under 10 seconds via `docker compose stop`, still at idle, not under load).
- Owner 1.1.m: CI has a Postgres service with the same tag and sets `TEST_DATABASE_URL` and `CI=true`, so the integration test fails rather than skips.
- Owner 1.1.m: CI test of migrate mode inside the container: empty environment and unreachable host must fail without a traceback or password; CI sets `TEST_DATABASE_ALLOWED_HOSTS` to the Postgres service name.
- Owner 1.1.m: CI runs `docker compose config --services` with `.env.example` and checks that the services named in `DATABASE_URL` and `QDRANT_URL` (`postgres` and `qdrant`) exist among them; CI's own Postgres service container uses the same pinned tag as `deploy/docker-compose.yml`.
- Owner 1.2: set `target_metadata` and create the first migration (`TODO(1.2)` in `backend/alembic/env.py`).
- Owner Phase 7 (deployment): use a separate migration database role with DDL rights and application roles with data rights only; two different `DATABASE_URL`s for `migrate` and `api`/`worker`.
- Owner Phase 7: tune `mem_limit`/`pids_limit` for api/worker/migrate from the load tests (7.1); the 512m/200 figures in 1.1.i are unmeasured starting points. Also consider limits and read-only-root settings for postgres and qdrant themselves (left unhardened in 1.1.i as upstream images not audited for it).
- Owner 1.3: enable Qdrant authentication (`QDRANT__SERVICE__API_KEY`, sent by the app as an `api-key` header) and add the key to `.env.example`, Compose and Settings, with a test that an unauthenticated request is rejected.
- Owner Phase 7: replace plaintext secrets in the environment with Docker secrets or an external secrets manager, and document that anyone with `docker inspect` access sees the current secrets.
- Owner the task that first opens a database connection in the API (1.2 or 1.4): add a readiness check that touches Postgres, and use it for the Compose healthcheck (today's api healthcheck only proves the process is alive).
- Owner 2.1: add a real worker healthcheck once the job loop exists.
- Owner 1.1.o: the lockfile task also reviews the pinned Qdrant and Postgres image tags for staleness.
- Owner 1.1.m: CI calls `make lint` and `make test`, and also runs the database-backed suite (a Postgres service container, `TEST_DATABASE_URL` and `CI=true` set so the Alembic integration test fails rather than skips).
- Owner 1.1.n: finalize `.gitignore` — keep `.env`, `.venv/`, caches (`__pycache__/`, `*.pyc`, `.pytest_cache/`, `.ruff_cache/`) ignored; keep `.env.example` tracked.
- Owner 5.1: remove or update `backend/tests/test_layout_placeholders.py` when tooling is added to `widget/`; the test exists to stop tooling being added before the bundler decision.
- Owner 6.1: remove or update `backend/tests/test_layout_placeholders.py` when tooling is added to `dashboard/`; the test exists to stop tooling being added before the dashboard tooling decision.
