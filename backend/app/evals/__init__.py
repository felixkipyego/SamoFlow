# backend/app/evals/__init__.py
# Task 3.7.b: golden-set schema, loader and evidence-passage resolution.
# Data (items.yaml, the synthetic corpus pages and its frozen chunk+
# embedding fixture) lives under the repo-root evals/ directory per
# docs/SPEC.md §21 ("evals/ golden sets, runners"), not inside this
# package -- this package is code only. UNLIKE app/agent/phrases/'s own
# yaml files (small, production-runtime data the built image must ship
# and load via importlib.resources -- a real config-path bug, fixed
# 2026-10-16, moved them inside the app package for exactly this
# reason): evals/ is dev/eval tooling only, never copied into the
# Docker image (backend/Dockerfile's builder stage copies `app/`
# alone), so it has no reason to be real package data and every reason
# to stay outside app/.
