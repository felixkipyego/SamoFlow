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
UV_COMPILE = uv pip compile backend/pyproject.toml --universal --python-version 3.12 --generate-hashes --no-header

.DEFAULT_GOAL := help

.PHONY: help env up down down-volumes migrate test test-db test-db-down test-all lint evals install lock lock-upgrade lock-check audit hooks

help:
	@echo "make env          create .env from .env.example if it does not exist yet"
	@echo "make up           build and start postgres, qdrant, api, worker"
	@echo "make down         stop and remove containers (volumes are kept)"
	@echo "make down-volumes stop and remove containers AND volumes (destroys data)"
	@echo "make migrate      run alembic upgrade head in Docker"
	@echo "make test         run the backend test suite (no database required)"
	@echo "make test-db      start the disposable test database and wait until ready"
	@echo "make test-db-down stop and remove the test database"
	@echo "make test-all     run the full suite against test-db, always cleaning up"
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

# Cleanup always runs, and the tests' own exit code is preserved, even when
# they fail: the pytest run is captured explicitly (subshell, so the "cd
# backend" does not leak into the test-db-down call that follows) instead of
# relying on make's own error handling for the cleanup step.
test-all: test-db
	@(cd backend && TEST_DATABASE_URL=postgresql+psycopg://widgetplatform:change-me@127.0.0.1:55432/widgetplatform_test python -m pytest tests -q); \
	rc=$$?; \
	$(MAKE) test-db-down; \
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
	$(UV_COMPILE) --extra dev -o backend/requirements-dev.lock

lock-upgrade:
	$(UV_COMPILE) --upgrade -o backend/requirements.lock
	$(UV_COMPILE) --extra dev --upgrade -o backend/requirements-dev.lock
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
lock-check:
	@tmp=$$(mktemp -d); \
	cp backend/requirements.lock $$tmp/requirements.lock; \
	cp backend/requirements-dev.lock $$tmp/requirements-dev.lock; \
	$(UV_COMPILE) -o $$tmp/requirements.lock; \
	$(UV_COMPILE) --extra dev -o $$tmp/requirements-dev.lock; \
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
