# backend/tests/test_provision_before_migrate_guard.py
# [SECURITY] L2 follow-up (2026-10-24): the L2 PR's dashboard e2e job ran
# `make up` -> `make migrate` with no provision step, so on CI's fresh
# volume the owner/app Postgres roles never existed and migrate failed with
# "password authentication failed for user widgetplatform_owner". Nothing
# caught it locally because nothing checked the ORDER. This guard does:
# every GitHub Actions job, shell script and Makefile recipe that runs the
# `migrate` service must provision the roles EARLIER in that same job/
# script/recipe -- or be explicitly listed below with the reason it
# operates only on an already-provisioned stack.
import re

import pytest
import yaml

from tests.conftest import REPO_ROOT

WORKFLOWS = sorted((REPO_ROOT / ".github" / "workflows").glob("*.yml"))
SCRIPTS = sorted(
    p
    for pattern in (
        "ops/**/*.sh",
        "deploy/*.sh",
        "backend/*.sh",
        "dashboard/e2e/**/*.sh",
        "widget/**/*.sh",
    )
    for p in REPO_ROOT.glob(pattern)
    if "node_modules" not in p.parts
)
MAKEFILE = REPO_ROOT / "Makefile"

# A command that runs the migrate service (schema migrations, or any admin/
# seed command, which all connect as the owner role).
_MIGRATE = re.compile(r"\bmake migrate\b|\$\(MAKE\) migrate\b|\brun --rm (-T )?migrate\b")
# A command that provisions the roles first.
_PROVISION = re.compile(
    r"\bmake (provision-roles|db-bootstrap)\b|\$\(MAKE\) (provision-roles|db-bootstrap)\b"
    r"|\brun --rm (-T )?provision-roles(-test)?\b"
)

# Scripts that only ever run migrate against a stack that is ALREADY
# provisioned and running, by documented precondition -- each with the
# reason. Never add an entry to silence a real ordering bug.
ALREADY_PROVISIONED = {
    "ops/deploy/smoke-prod-stack.sh": "runs only admin/seed commands against a stack "
    "that is already up (RUNBOOK §5 provisions and migrates before it is ever run)",
}
# Makefile targets allowed to run migrate without provisioning in the same
# recipe: `migrate` itself (provision-roles is deliberately NOT chained into
# it -- the L2 design; db-bootstrap is the chained, from-scratch path).
MAKEFILE_MIGRATE_ONLY_TARGETS = {"migrate"}


def _first_violation(commands: list[str]) -> str | None:
    # Returns the first migrate command that comes before any provision
    # command, or None if the order is safe.
    provisioned = False
    for command in commands:
        if _PROVISION.search(command):
            provisioned = True
        if _MIGRATE.search(command) and not provisioned:
            return command.strip()
    return None


def _code_lines(text: str) -> list[str]:
    return [line for line in text.splitlines() if not line.lstrip().startswith("#")]


def _workflow_job_commands(path) -> dict[str, list[str]]:
    jobs = yaml.safe_load(path.read_text()).get("jobs", {}) or {}
    return {
        name: [
            line
            for step in job.get("steps", [])
            for line in _code_lines(str(step.get("run", "")))
        ]
        for name, job in jobs.items()
    }


def _makefile_recipes() -> dict[str, list[str]]:
    recipes: dict[str, list[str]] = {}
    current = None
    for line in MAKEFILE.read_text().splitlines():
        target = re.match(r"^([A-Za-z][A-Za-z0-9_-]*):(?!=)", line)
        if target:
            current = target.group(1)
            recipes[current] = []
        elif line.startswith("\t") and current:
            recipes[current].append(line)
        elif line.strip() and not line.startswith("#"):
            current = None
    return recipes


@pytest.mark.parametrize("path", WORKFLOWS, ids=lambda p: p.name)
def test_every_workflow_job_provisions_before_migrate(path):
    for job, commands in _workflow_job_commands(path).items():
        bad = _first_violation(commands)
        assert bad is None, (
            f"{path.name} job '{job}' runs `{bad}` before any provision-roles step -- "
            "on a fresh volume the owner/app roles do not exist yet. Use `make db-bootstrap` "
            "(or `make provision-roles`) before migrate and before `make up`."
        )


@pytest.mark.parametrize("path", SCRIPTS, ids=lambda p: str(p.relative_to(REPO_ROOT)))
def test_every_script_provisions_before_migrate(path):
    rel = str(path.relative_to(REPO_ROOT))
    if rel in ALREADY_PROVISIONED:
        return
    bad = _first_violation(_code_lines(path.read_text()))
    assert bad is None, f"{rel} runs `{bad}` before any provision-roles step"


def test_every_makefile_recipe_provisions_before_migrate():
    for target, recipe in _makefile_recipes().items():
        if target in MAKEFILE_MIGRATE_ONLY_TARGETS:
            continue
        bad = _first_violation(recipe)
        assert bad is None, f"Makefile target '{target}' runs `{bad}` before provisioning"


def test_db_bootstrap_runs_postgres_then_provision_then_migrate():
    recipe = _makefile_recipes()["db-bootstrap"]
    assert "up -d --wait postgres qdrant" in recipe[0]
    assert "provision-roles" in recipe[1] and "migrate" in recipe[2]
    # api/worker are never started by the bootstrap itself.
    assert not any(re.search(r"\b(api|worker)\b", line) for line in recipe)


def test_migrate_target_does_not_chain_provisioning():
    # The L2 design: provision-roles (bootstrap superuser credentials) is an
    # explicit step, never hidden inside `make migrate` or `make up`.
    recipes = _makefile_recipes()
    for target in ("migrate", "up"):
        assert not any(_PROVISION.search(line) for line in recipes[target]), target


def test_the_exemptions_are_real():
    for rel in ALREADY_PROVISIONED:
        assert (REPO_ROOT / rel).exists(), f"stale exemption: {rel}"
        assert _MIGRATE.search((REPO_ROOT / rel).read_text()), f"{rel} no longer runs migrate"


def test_the_guard_actually_scans_the_known_migrate_sites():
    # Non-vacuous: the files that really run migrate are in scope.
    scanned = {str(p.relative_to(REPO_ROOT)) for p in SCRIPTS}
    known = {"ops/demo/start.sh", "deploy/smoke-image.sh", "ops/deploy/smoke-prod-stack.sh"}
    assert known <= scanned
    assert "dashboard.yml" in {p.name for p in WORKFLOWS}


@pytest.mark.parametrize(
    "commands",
    [
        ["cp .env.example .env", "make up", "make migrate"],  # the original CI bug
        ["make migrate", "make provision-roles"],  # provisioning too late
        ['"${COMPOSE[@]}" run --rm -T migrate admin seed-plans'],  # an admin command alone
        ["$(MAKE) migrate"],
    ],
)
def test_the_guard_rejects_migrate_without_prior_provisioning(commands):
    assert _first_violation(commands) is not None


@pytest.mark.parametrize(
    "commands",
    [
        ["make db-bootstrap", "make up", "docker compose run --rm migrate seed-owner"],
        ["make provision-roles", "make migrate"],
        ['"${COMPOSE[@]}" run --rm provision-roles', '"${COMPOSE[@]}" run --rm migrate'],
        ["# make migrate (comment only)"],
    ],
)
def test_the_guard_accepts_a_safe_order(commands):
    assert _first_violation(_code_lines("\n".join(commands))) is None
