# Task 1.1.g: one image, two entrypoints (api / worker), selected by
# entrypoint.sh's first argument. Build context is backend/:
#   docker build -t widgetplatform-backend:dev backend
#
# Base image pinned to an exact tag (never "latest", never a floating
# "3.12" tag): python:3.12.14-slim-trixie is the latest 3.12 patch release
# published for Debian trixie (the current stable Debian release),
# verified to exist with `docker manifest inspect python:3.12.14-slim-trixie`
# before this file was written.
#
# Multi-stage: the builder stage has pip/hatchling and installs the package
# into a venv; the final stage is the same pinned base image with only the
# built venv and the entrypoint copied in, so pip and the build toolchain
# never reach the runtime image.
#
# One ARG for both FROM lines (duplication check after 1.1.g/h/i): an ARG
# declared before the first FROM is visible to that first FROM only: the
# second stage must re-declare it (empty, no default) to use the same
# value -- otherwise it would silently fall back to being unset there.
ARG PYTHON_IMAGE=python:3.12.14-slim-trixie

FROM ${PYTHON_IMAGE} AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Only what the runtime needs: no tests/, no .env, no dev dependencies.
# requirements.lock (not requirements-dev.lock: the image never needs dev
# tools) is hash-checked via --require-hashes, then the project itself is
# installed with --no-deps since its dependencies already came from the
# lockfile (Task 1.1.o.d; mirrors the Makefile's "install" target).
COPY pyproject.toml ./pyproject.toml
COPY requirements.lock ./requirements.lock
COPY app/ ./app/

RUN python -m venv /venv \
    && /venv/bin/pip install --no-cache-dir --require-hashes -r requirements.lock \
    && /venv/bin/pip install --no-cache-dir --no-deps . \
    && /venv/bin/pip uninstall --yes pip


ARG PYTHON_IMAGE
FROM ${PYTHON_IMAGE}

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/venv/bin:$PATH"

# Fixed numeric UID/GID, no login shell, no home directory: least-privilege
# runtime identity for the app process (rule 11). The UID (10001) is a fixed
# convention rather than one assigned by the host, so file ownership stays
# stable across rebuilds and across hosts; Compose (1.1.i) must use this same
# UID wherever it sets a user: or touches ownership of files from this image.
RUN groupadd --system --gid 10001 app \
    && useradd --system --uid 10001 --gid app --no-create-home --shell /usr/sbin/nologin app

# The base image's own system Python ships pip via ensurepip at build time
# (not a dpkg package -- there is no "apt remove" for it, and the official
# image never removes it). "pip uninstall" alone is not enough: ensurepip
# keeps a fully offline bundled pip wheel that reinstates a working pip with
# one command (verified by hand: uninstalling pip, then running
# "python3 -m ensurepip" successfully reinstalled it). Task 1.1.o.d removes
# both. sysconfig (not a hard-coded "python3.12" path) finds ensurepip's
# directory, so this does not silently stop working if PYTHON_IMAGE above is
# ever bumped to a different Python minor version.
RUN python3 -m pip uninstall --yes pip \
    && rm -rf "$(python3 -c 'import sysconfig; print(sysconfig.get_path("stdlib"))')/ensurepip"

WORKDIR /app

# Copied while still root, before USER below, so the venv, entrypoint and
# Alembic files end up root-owned: readable (and, for entrypoint.sh,
# executable) by the app user, not writable by it. Alembic itself is
# installed into /venv already; only its config and scripts are copied here,
# so "alembic" (run with WORKDIR /app) finds alembic.ini next to it.
COPY --from=builder /venv /venv
COPY --chmod=0755 entrypoint.sh /usr/local/bin/entrypoint.sh
COPY --chmod=0644 alembic.ini ./alembic.ini
COPY --chmod=0755 alembic/ ./alembic/

USER 10001

# Documentation only; Compose (1.1.i) publishes the real port mapping.
EXPOSE 8000

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
CMD ["api"]
