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

FROM python:3.12.14-slim-trixie AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Only what the runtime needs: no tests/, no .env, no dev dependencies.
COPY pyproject.toml ./pyproject.toml
COPY app/ ./app/

RUN python -m venv /venv \
    && /venv/bin/pip install --no-cache-dir . \
    && /venv/bin/pip uninstall --yes pip


FROM python:3.12.14-slim-trixie

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

WORKDIR /app

# Copied while still root, before USER below, so the venv and entrypoint end
# up root-owned: readable and executable by the app user, not writable by it.
COPY --from=builder /venv /venv
COPY --chmod=0755 entrypoint.sh /usr/local/bin/entrypoint.sh

USER 10001

# Documentation only; Compose (1.1.i) publishes the real port mapping.
EXPOSE 8000

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
CMD ["api"]
