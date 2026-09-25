# backend/tests/test_makefile_guard.py
# Task 1.1.j: static checks on the repo-root Makefile, so a future edit
# cannot silently drop a target, weaken error handling, or bypass the
# $(COMPOSE) variable (see deploy/docker-compose.yml's own header comment on
# why every compose call must pass --env-file/-f explicitly).
import re

from tests.conftest import REPO_ROOT, read_lines

_MAKEFILE = REPO_ROOT / "Makefile"

_EXPECTED_TARGETS = {
    "help",
    "env",
    "up",
    "down",
    "down-volumes",
    "migrate",
    "test",
    "test-db",
    "test-db-down",
    "test-all",
    "lint",
    "evals",
    "install",
    "lock",
    "lock-upgrade",
    "lock-check",
    "audit",
}

# A real target line ("name:" or "name: prereq"), not a variable assignment
# ("NAME = ...", "NAME := ...") and not a dot-prefixed special target
# (.PHONY, .DEFAULT_GOAL).
_TARGET_LINE = re.compile(r"^([A-Za-z][A-Za-z0-9_-]*):(?!=)")


def _targets_and_recipes(lines):
    # Maps each target name to the list of its recipe lines (the tab-indented
    # lines that follow it, up to the next target or end of file).
    recipes = {}
    current = None
    for line in lines:
        match = _TARGET_LINE.match(line)
        if match:
            current = match.group(1)
            recipes.setdefault(current, [])
            continue
        if line.startswith("\t"):
            if current is None:
                continue
            recipes[current].append(line[1:])
    return recipes


def _compose_line():
    for line in read_lines(_MAKEFILE):
        if line.startswith("COMPOSE"):
            return line
    return None


def test_all_expected_targets_are_declared():
    recipes = _targets_and_recipes(read_lines(_MAKEFILE))
    missing = _EXPECTED_TARGETS - recipes.keys()
    assert not missing, f"Makefile is missing target(s): {sorted(missing)}"


def test_phony_lists_every_target():
    phony_line = next(
        (ln for ln in read_lines(_MAKEFILE) if ln.strip().startswith(".PHONY:")), None
    )
    assert phony_line is not None, "Makefile has no .PHONY declaration"
    declared = set(phony_line.split(":", 1)[1].split())
    missing = _EXPECTED_TARGETS - declared
    assert not missing, f".PHONY is missing target(s): {sorted(missing)}"


def test_compose_variable_has_required_flags():
    line = _compose_line()
    assert line is not None, "Makefile has no COMPOSE variable definition"
    assert "--env-file .env" in line, (
        "COMPOSE variable must include '--env-file .env' "
        f"(deploy/docker-compose.yml does not read .env on its own): got {line!r}"
    )
    assert "-f deploy/docker-compose.yml" in line, (
        f"COMPOSE variable must include '-f deploy/docker-compose.yml': got {line!r}"
    )


def test_every_docker_compose_invocation_goes_through_the_compose_variable():
    recipes = _targets_and_recipes(read_lines(_MAKEFILE))
    for target, target_recipes in recipes.items():
        for recipe_line in target_recipes:
            assert "docker compose" not in recipe_line, (
                f"target {target!r} calls 'docker compose' directly instead "
                f"of through $(COMPOSE): {recipe_line!r}"
            )


def test_no_recipe_line_ignores_errors_with_a_leading_dash():
    recipes = _targets_and_recipes(read_lines(_MAKEFILE))
    for target, target_recipes in recipes.items():
        for recipe_line in target_recipes:
            assert not recipe_line.startswith("-"), (
                f"target {target!r} has a recipe line starting with '-' "
                f"(make's error-ignoring prefix): {recipe_line!r}"
            )


def test_no_recipe_line_swallows_errors_with_or_true():
    recipes = _targets_and_recipes(read_lines(_MAKEFILE))
    for target, target_recipes in recipes.items():
        for recipe_line in target_recipes:
            assert "|| true" not in recipe_line, (
                f"target {target!r} has a recipe line containing '|| true': {recipe_line!r}"
            )


def test_down_never_removes_volumes_but_down_volumes_does():
    recipes = _targets_and_recipes(read_lines(_MAKEFILE))
    down_text = " ".join(recipes.get("down", []))
    down_volumes_text = " ".join(recipes.get("down-volumes", []))
    assert "-v" not in down_text, (
        f"'down' must not pass -v (that destroys volumes); use 'down-volumes' "
        f"for that: got recipe {down_text!r}"
    )
    assert "-v" in down_volumes_text, (
        f"'down-volumes' must pass -v to actually remove volumes: got recipe "
        f"{down_volumes_text!r}"
    )
