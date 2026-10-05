# Task 1.1.j: developer entry points for Compose, tests and lint. Plain GNU
# Make, POSIX shell recipes only (no bash-only syntax); nothing beyond make,
# docker and the active Python environment ("python -m ..." so whichever
# interpreter is on PATH is used, never a hard-coded path).
#
# Compose reads .env from deploy/ (its own folder), not the repo root or the
# caller's cwd (see deploy/docker-compose.yml's own header comment), so
# every compose call below passes --env-file/-f explicitly via $(COMPOSE).

COMPOSE = docker compose --env-file .env -f deploy/docker-compose.yml

# Shared by lock, lock-upgrade and lock-check (Task 1.1.o.b's uv invocation):
# each target adds only --extra dev, --upgrade or the -o/output path it needs.
# --no-header: uv's default header embeds the literal -o path used, which
# would make lock-check's tmp-directory comparison always show a spurious
# diff (Task 1.1.o.c).
#
# --overrides backend/requirements-overrides.txt (Task 2.5.b): this
# project now has TWO distinct uv mechanisms for two distinct problems --
# don't reach for the wrong one. -c/--constraints (added at Task 2.4.c,
# applied per-target to the dev lockfile only, see each target's own
# comment below) forces a package SHARED between the runtime and dev
# graphs to agree on one version across the two separately-compiled
# lockfiles -- a cross-lockfile agreement problem. --overrides (here,
# applied once, to every lock-related target uniformly) forces a SINGLE
# package to a specific version regardless of what any dependency's own
# marker-scoped constraint says for a Python version this project will
# never actually run on (confirmed live: a plain pyproject.toml pin is
# unsatisfiable here no matter how its own marker is scoped, since uv's
# --universal resolution must find one consistent answer across every
# theoretical split any dependency's markers reference, not just the
# one requires-python actually targets) -- a marker-conflict problem,
# not a cross-lockfile one. Reach for -c when two lockfiles disagree
# with each other; reach for --overrides when one package's own upstream
# constraint graph is unsatisfiable for an unreachable Python version.
UV_COMPILE = uv pip compile backend/pyproject.toml --universal --python-version 3.12 --overrides backend/requirements-overrides.txt --generate-hashes --no-header

.DEFAULT_GOAL := help

.PHONY: help env up down down-volumes migrate test test-db test-db-down test-qdrant test-qdrant-down test-all lint evals install lock lock-upgrade lock-check audit hooks

help:
	@echo "make env          create .env from .env.example if it does not exist yet"
	@echo "make up           build and start postgres, qdrant, api, worker"
	@echo "make down         stop and remove containers (volumes are kept)"
	@echo "make down-volumes stop and remove containers AND volumes (destroys data)"
	@echo "make migrate      run alembic upgrade head in Docker"
	@echo "make test         run the backend test suite (no database required)"
	@echo "make test-db      start the disposable test database and wait until ready"
	@echo "make test-db-down stop and remove the test database"
	@echo "make test-qdrant  start the disposable test Qdrant and wait until healthy"
	@echo "make test-qdrant-down stop and remove the test Qdrant"
	@echo "make test-all     run the full suite against test-db and test-qdrant, always cleaning up"
	@echo "make lint         ruff check backend, evals and hooks.py"
	@echo "make evals        run the eval placeholder"
	@echo "make install      install hashed runtime+dev deps, then the backend package"
	@echo "make lock         regenerate both lockfiles (no upgrade)"
	@echo "make lock-upgrade regenerate both lockfiles, allowing newer versions"
	@echo "make lock-check   fail if the committed lockfiles are out of date"
	@echo "make audit        pip-audit against both lockfiles"
	@echo "make hooks        list TODO/ASSUMPTION/UNCERTAIN/NOTE markers (never fails)"

# Never overwrites an existing .env. Every target below that touches
# $(COMPOSE) depends on this.
env:
	@if [ ! -f .env ]; then \
	  cp .env.example .env; \
	  echo "Created .env from .env.example. Edit it for anything beyond local use."; \
	fi

up: env
	$(COMPOSE) up -d --build

down:
	$(COMPOSE) down

down-volumes:
	@echo "WARNING: this stops the stack AND deletes the postgres/qdrant data volumes."
	$(COMPOSE) down -v

# Runs in Docker (not "cd backend && alembic upgrade head"), so this does
# not depend on the caller's working directory or local Python environment.
migrate: env
	$(COMPOSE) run --rm migrate

# No database: the Alembic integration test skips itself when
# TEST_DATABASE_URL is unset (see backend/tests/conftest.py).
test:
	cd backend && python -m pytest tests -q

test-db: env
	$(COMPOSE) --profile test up -d test-db
	@i=0; until $(COMPOSE) --profile test exec -T test-db pg_isready -U widgetplatform -d widgetplatform_test >/dev/null 2>&1; do \
	  i=$$((i + 1)); \
	  if [ $$i -ge 30 ]; then \
	    echo "test-db: postgres did not become ready within 30s" >&2; \
	    exit 1; \
	  fi; \
	  sleep 1; \
	done

test-db-down:
	$(COMPOSE) --profile test stop test-db
	$(COMPOSE) --profile test rm -f -s -v test-db

# Polls Docker's own healthcheck status (docker inspect), not a re-typed
# copy of the bash/dev/tcp probe deploy/docker-compose.yml's healthcheck
# already runs: qdrant has no lightweight in-container readiness command
# the way postgres has pg_isready, so re-implementing the same HTTP probe
# a second time here would only duplicate it, not simplify anything.
test-qdrant: env
	$(COMPOSE) --profile test up -d test-qdrant
	@i=0; until [ "$$(docker inspect -f '{{.State.Health.Status}}' $$($(COMPOSE) --profile test ps -q test-qdrant))" = "healthy" ]; do \
	  i=$$((i + 1)); \
	  if [ $$i -ge 30 ]; then \
	    echo "test-qdrant: qdrant did not become healthy within 30s" >&2; \
	    exit 1; \
	  fi; \
	  sleep 1; \
	done

test-qdrant-down:
	$(COMPOSE) --profile test stop test-qdrant
	$(COMPOSE) --profile test rm -f -s -v test-qdrant

# Cleanup always runs, and the tests' own exit code is preserved, even when
# they fail: the pytest run is captured explicitly (subshell, so the "cd
# backend" does not leak into the test-db-down/test-qdrant-down calls that
# follow) instead of relying on make's own error handling for cleanup.
# TEST_QDRANT_API_KEY matches the literal key test-qdrant sets above.
test-all: test-db test-qdrant
	@(cd backend && TEST_DATABASE_URL=postgresql+psycopg://widgetplatform:change-me@127.0.0.1:55432/widgetplatform_test TEST_QDRANT_URL=http://127.0.0.1:56333 TEST_QDRANT_API_KEY=test-qdrant-key python -m pytest tests -q); \
	rc=$$?; \
	$(MAKE) test-db-down; \
	$(MAKE) test-qdrant-down; \
	exit $$rc

lint:
	ruff check backend evals hooks.py

evals:
	python evals/run.py

# Installs from the hash-pinned dev lockfile (never resolves versions itself,
# see Task 1.1.o.b), then the backend package with no dependency resolution
# of its own (--no-deps: its dependencies already came from the lockfile).
install:
	pip install --require-hashes -r backend/requirements-dev.lock
	pip install --no-deps -e ./backend

# Regenerates both lockfiles with the same uv invocation as Task 1.1.o.b,
# overwriting the committed files. No --upgrade: existing pins are kept
# unless something about the dependency graph itself changed.
lock:
	$(UV_COMPILE) -o backend/requirements.lock
	$(UV_COMPILE) --extra dev -c backend/requirements.lock -o backend/requirements-dev.lock

lock-upgrade:
	$(UV_COMPILE) --upgrade -o backend/requirements.lock
	$(UV_COMPILE) --extra dev --upgrade -c backend/requirements.lock -o backend/requirements-dev.lock
	@echo "Review the lockfile diff, run 'make test-all', then commit."

# Regenerates both lockfiles into a throwaway directory and diffs each
# against the committed version -- the committed files are never touched.
# The temp directory is always removed, whether the diff passes or fails.
# The temp files are seeded with the committed lockfiles before compiling
# into them (matching what `lock` does, since it overwrites the committed
# files in place): uv pip compile prefers a version already pinned in an
# existing output file over the latest one, so a fresh unseeded file would
# always re-resolve every transitive dependency to its current latest
# release and report a spurious diff for any patch published since the
# lockfiles were last regenerated, even though nothing here is out of date.
# The dev recompile is constrained against backend/requirements.lock (-c),
# the exact same literal argument `lock`'s own second line uses, so a
# package needed by both graphs can never disagree between the two files
# (Task 2.4.c: found live when tiktoken pulled `requests`, and therefore
# `charset-normalizer`, into the runtime lockfile for the first time).
# Deliberately the real committed path, not $$tmp/requirements.lock: uv
# embeds the literal -c path string into every affected package's own
# "via" comment, so using the temp file's absolute path would make this
# recompile's output byte-differ from what `lock` itself produces (a
# constraint-path string, not a version) even when nothing is out of
# date -- confirmed live, this was a real bug in this fix's own first
# draft. Pointing at the committed file instead of the temp copy is safe:
# when requirements.lock has NOT drifted (rc1 below is 0) its content is
# byte-identical to $$tmp/requirements.lock anyway, so the constraint
# resolves identically either way; when it HAS drifted, rc1 alone already
# reports "out of date" regardless of what this second compile does.
lock-check:
	@tmp=$$(mktemp -d); \
	cp backend/requirements.lock $$tmp/requirements.lock; \
	cp backend/requirements-dev.lock $$tmp/requirements-dev.lock; \
	$(UV_COMPILE) -o $$tmp/requirements.lock; \
	$(UV_COMPILE) --extra dev -c backend/requirements.lock -o $$tmp/requirements-dev.lock; \
	diff -u backend/requirements.lock $$tmp/requirements.lock; rc1=$$?; \
	diff -u backend/requirements-dev.lock $$tmp/requirements-dev.lock; rc2=$$?; \
	rm -rf $$tmp; \
	if [ $$rc1 -ne 0 ] || [ $$rc2 -ne 0 ]; then echo "Lockfiles are out of date: run 'make lock' and review the change." >&2; exit 1; fi

# --require-hashes is already implied by the lockfiles' own --hash entries;
# kept explicit for readability. Exits non-zero on any finding (pip-audit's
# default) -- never swallowed here.
audit:
	pip-audit --require-hashes -r backend/requirements.lock -r backend/requirements-dev.lock

# Task 1.1c: a visibility tool, not a gate -- never fails because markers
# exist (hooks.py's own exit code is 0 unless the scan itself errors, e.g.
# an unreadable file).
hooks:
	python hooks.py
