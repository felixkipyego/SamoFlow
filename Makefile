# Task 1.1.j: developer entry points for Compose, tests and lint. Plain GNU
# Make, POSIX shell recipes only (no bash-only syntax); nothing beyond make,
# docker and the active Python environment ("python -m ..." so whichever
# interpreter is on PATH is used, never a hard-coded path).
#
# Compose reads .env from deploy/ (its own folder), not the repo root or the
# caller's cwd (see deploy/docker-compose.yml's own header comment), so
# every compose call below passes --env-file/-f explicitly via $(COMPOSE).

COMPOSE = docker compose --env-file .env -f deploy/docker-compose.yml

.DEFAULT_GOAL := help

.PHONY: help env up down down-volumes migrate test test-db test-db-down test-all lint evals

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
	@echo "make lint         ruff check backend and evals"
	@echo "make evals        run the eval placeholder"

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
	ruff check backend evals

evals:
	python evals/run.py
