# backend/tests/test_dependabot_guard.py
# Task 1.1.o.g: static checks on .github/dependabot.yml, so a future edit
# cannot silently drop an ecosystem, add an unsafe Python/pip ecosystem
# entry (see the file's own comment: neither `uv` nor `pip` safely covers
# backend's hash-pinned lockfiles), or lose the explanatory comment for why
# the base image's Python version isn't covered by a `docker` ecosystem
# entry. Deliberately no YAML parser (no new dependency): line-based text
# checks only, the same style the other guard tests already use.
from tests.conftest import REPO_ROOT, is_comment_or_blank, read_lines

DEPENDABOT_YML_PATH = REPO_ROOT / ".github" / "dependabot.yml"

# Ecosystem values Dependabot itself defines for Python packaging; none of
# these are safe for backend's hash-pinned lockfiles (see the file's own
# comment for why) and must never appear as a configured ecosystem.
_UNSAFE_PYTHON_ECOSYSTEMS = ('"pip"', "'pip'", '"uv"', "'uv'")


def test_dependabot_yml_exists_and_has_basic_structure():
    assert DEPENDABOT_YML_PATH.is_file(), f"{DEPENDABOT_YML_PATH} not found"
    lines = read_lines(DEPENDABOT_YML_PATH)
    assert lines, f"{DEPENDABOT_YML_PATH} is empty"

    # Basic YAML-ish structure check (no parser dependency): a top-level
    # "version:" key, a top-level "updates:" key, and at least one
    # "- package-ecosystem:" list item nested under it.
    top_level = [line for line in lines if line and not line[0].isspace()]
    assert any(line.startswith("version:") for line in top_level), (
        "no top-level 'version:' key found"
    )
    assert any(line.startswith("updates:") for line in top_level), (
        "no top-level 'updates:' key found"
    )
    ecosystem_lines = [line for line in lines if "package-ecosystem:" in line]
    assert ecosystem_lines, "no 'package-ecosystem:' entries found under updates:"
    for line in ecosystem_lines:
        assert line.lstrip().startswith("- package-ecosystem:") or line.lstrip().startswith(
            "package-ecosystem:"
        ), f"'package-ecosystem:' line is not a list item: {line!r}"


def test_dependabot_yml_configures_github_actions_and_docker_compose():
    text = "\n".join(read_lines(DEPENDABOT_YML_PATH))
    assert 'package-ecosystem: "github-actions"' in text, (
        "no 'github-actions' package-ecosystem entry found"
    )
    assert 'package-ecosystem: "docker-compose"' in text, (
        "no 'docker-compose' package-ecosystem entry found"
    )


def test_dependabot_yml_has_no_python_ecosystem_entry():
    lines = read_lines(DEPENDABOT_YML_PATH)
    for line in lines:
        if is_comment_or_blank(line):
            continue
        stripped = line.strip()
        if not stripped.startswith(("- package-ecosystem:", "package-ecosystem:")):
            continue
        assert not any(unsafe in stripped for unsafe in _UNSAFE_PYTHON_ECOSYSTEMS), (
            "a Python/pip/uv package-ecosystem entry was found -- unsafe for "
            f"backend's hash-pinned lockfiles (see the file's own comment): {stripped!r}"
        )


def test_dependabot_yml_explains_the_dockerfile_arg_gap():
    text = "\n".join(read_lines(DEPENDABOT_YML_PATH))
    assert "ARG" in text and "PYTHON_IMAGE" in text, (
        "no comment found explaining why a 'docker' ecosystem entry for "
        "backend/ is omitted (the ARG-substituted FROM line Dependabot "
        "cannot resolve)"
    )


def _ignore_rules(lines):
    # Maps every "ignore:" block to its rules; each rule is the set of
    # "key: value" pairs under one "- " list item, so a rule missing
    # "dependency-name" (which would ignore every dependency in that
    # ecosystem entry, not just the intended one) can be detected.
    rules = []
    for i, line in enumerate(lines):
        if line.strip() != "ignore:":
            continue
        indent = len(line) - len(line.lstrip(" "))
        current = None
        for later in lines[i + 1 :]:
            if later.strip() == "":
                continue
            later_indent = len(later) - len(later.lstrip(" "))
            if later_indent <= indent:
                break
            stripped = later.strip()
            if stripped.startswith("- "):
                current = {}
                rules.append(current)
                stripped = stripped[2:].strip()
            if current is not None and ":" in stripped:
                key, _, value = stripped.partition(":")
                current[key.strip()] = value.strip().strip("\"'")
    return rules


def test_dependabot_yml_ignores_postgres_scoped_by_dependency_name_only():
    # Task 1.1.o.g follow-up: a temporary ignore rule silences the known
    # Dependabot YAML-anchor/alias bug for postgres specifically -- it must
    # never widen into ignoring the whole docker-compose ecosystem entry
    # (which would also silently stop qdrant/qdrant and
    # widgetplatform-backend updates).
    rules = _ignore_rules(read_lines(DEPENDABOT_YML_PATH))
    assert rules, "no 'ignore:' rule found (expected one scoping out postgres)"

    for rule in rules:
        assert "dependency-name" in rule, (
            "an ignore rule has no dependency-name filter -- this would "
            f"ignore every dependency in its ecosystem entry, not just "
            f"postgres: {rule!r}"
        )

    postgres_rules = [r for r in rules if r.get("dependency-name") == "postgres"]
    assert postgres_rules, (
        'no ignore rule scoped to dependency-name: "postgres" was found'
    )
