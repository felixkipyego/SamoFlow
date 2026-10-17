# backend/tests/test_single_api_process_guard.py
# [SECURITY] PART 0 audit (2026-10-16): `app/chat/routes.py`'s own
# `chat_active_streams`/`chat_stop_requests` (and every other in-process
# mechanism this project already documents the same way -- `app/plans/
# usage.py`'s own quota counters, the various `RateLimiter` instances,
# `StatusCache`) are plain Python objects on `request.app.state`, correct
# ONLY under the standing, already-documented precondition that exactly
# ONE API process serves chats (docs/SPEC.md §17's own named scale-out
# trigger for when this would need to move to a shared store).
#
# Confirmed live, not assumed, that this precondition genuinely holds
# today: `backend/entrypoint.sh`'s own `api)` branch execs uvicorn with
# no `--workers` flag at all (uvicorn's own default with no flag is a
# single process); neither `deploy/docker-compose.yml` nor `deploy/
# docker-compose.prod.yml` sets a `deploy.replicas` entry for the `api`
# service; `docs/deploy/RUNBOOK.md` gives an operator no scaling
# instruction at all. This test is the guard that keeps it that way: it
# fails the moment ANY of those three files changes to allow more than
# one API process, forcing whoever makes that change to first move the
# stop-signal (and re-check every other in-process mechanism named
# above) to shared state, rather than silently shipping a second
# process that can never see another process's own `chat_stop_requests`
# set -- the real, live failure mode a stop click would hit (visitor's
# turn is being served by process B; their own `POST /chat/stop` lands
# on process A instead, mutates A's own empty set, and never reaches
# B's own running stream at all).
#
# Plain-text/substring checks, not a real `docker compose config` call
# (which needs Docker installed and running) and not a real YAML parse
# (deploy/docker-compose.prod.yml uses the Compose Specification's own
# `!reset []` tag, which plain `yaml.safe_load()` cannot parse without a
# custom constructor) -- this keeps the guard in the fast, offline
# `pytest tests` tier the same way `test_env_example.py` is, needing
# neither.
import re
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = BACKEND_DIR.parent


def _api_command_line() -> str:
    entrypoint = (BACKEND_DIR / "entrypoint.sh").read_text()
    match = re.search(r"api\)(.*?);;", entrypoint, re.DOTALL)
    assert match is not None, "entrypoint.sh's own 'api)' case branch was not found at all"
    return match.group(1)


def test_entrypoint_api_mode_has_no_workers_flag():
    api_branch = _api_command_line()
    assert "--workers" not in api_branch, (
        "backend/entrypoint.sh's 'api' mode now passes --workers to uvicorn -- this "
        "means more than one API process can run inside a single container. "
        "chat_active_streams/chat_stop_requests (app/chat/routes.py) and every other "
        "in-process mechanism this project documents the same way (app/plans/usage.py's "
        "quota counters, the various RateLimiter instances, StatusCache) are correct "
        "ONLY under a single API process -- move them to shared state (Postgres, keyed "
        "by vid/lease_token, no new service) BEFORE raising this above 1."
    )


def _assert_no_api_replicas(compose_path: Path) -> None:
    text = compose_path.read_text()
    # A narrow, deliberately simple check: the literal service block
    # name "api:" followed, before the NEXT top-level service starts, by
    # a "replicas:" key under its own "deploy:" block. Good enough for a
    # guard whose only job is "this must stay absent, on purpose" --
    # not a general-purpose Compose parser.
    api_block_match = re.search(r"\n  api:\n(.*?)(?=\n  \w|\Z)", text, re.DOTALL)
    if api_block_match is None:
        return  # this file has no "api:" service block of its own at all (the prod override today)
    api_block = api_block_match.group(1)
    assert "replicas" not in api_block, (
        f"{compose_path} now sets a 'replicas' key under the api service -- "
        "see test_entrypoint_api_mode_has_no_workers_flag's own failure message "
        "for why this must not happen without first moving the stop signal to "
        "shared state."
    )


def test_base_compose_api_service_has_no_replicas():
    _assert_no_api_replicas(REPO_ROOT / "deploy" / "docker-compose.yml")


def test_prod_compose_override_api_service_has_no_replicas():
    _assert_no_api_replicas(REPO_ROOT / "deploy" / "docker-compose.prod.yml")


def test_runbook_warns_against_scaling_the_api_service_without_a_shared_stop_signal():
    # `docker compose up --scale api=N` is an OPERATOR ACTION, never
    # persisted in any repo file -- no static guard above can catch it.
    # This is the one place that can: the RUNBOOK itself must say not to.
    runbook = (REPO_ROOT / "docs" / "deploy" / "RUNBOOK.md").read_text()
    assert "chat_stop_requests" in runbook or "chat_active_streams" in runbook, (
        "docs/deploy/RUNBOOK.md no longer warns an operator against running more "
        "than one api replica/process (e.g. via 'docker compose up --scale api=2') "
        "without first moving the in-process stop signal to shared state."
    )
