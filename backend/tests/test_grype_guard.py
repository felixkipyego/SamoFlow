# backend/tests/test_grype_guard.py
# Duplication/simplification review after 1.1.o.e/f/g, item C1: a plain-text
# regression guard for .grype.yaml's ignore rule, so a future edit cannot
# silently widen it (e.g. dropping a field, so it starts matching a
# different package/version's CVE by coincidence) or add a second,
# unreviewed rule. Deliberately no YAML parser (no new dependency): a
# small hand-rolled parser reads only what this file's own shape needs
# (one "ignore:" list with dict entries), matching the line-based style
# the other guard tests already use.
from tests.conftest import REPO_ROOT, read_lines

GRYPE_YAML_PATH = REPO_ROOT / ".grype.yaml"

_REQUIRED_FIELDS = ("vulnerability", "package.name", "package.version", "package.type")


def _ignore_rules(lines):
    # Each "- vulnerability: ..." line starts a new rule; every following
    # line more indented than it (up to the next "- " list item at the
    # same indent, or EOF) belongs to that rule. Returns a list of sets of
    # "key" and "key.subkey" strings actually set in each rule, e.g.
    # {"vulnerability", "package.name", ...}.
    rules = []
    current_fields = None
    current_indent = None
    package_indent = None
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        indent = len(line) - len(line.lstrip(" "))

        if stripped.startswith("- vulnerability:"):
            current_fields = {"vulnerability"}
            rules.append(current_fields)
            current_indent = indent
            package_indent = None
            continue

        if current_fields is None or indent <= current_indent:
            # A list item at or above the rule's own indent (or nothing
            # in progress yet) means we've left the current rule.
            current_fields = None
            continue

        if stripped == "package:":
            package_indent = indent
            continue

        if package_indent is not None and indent > package_indent:
            if stripped.startswith("name:"):
                current_fields.add("package.name")
            elif stripped.startswith("version:"):
                current_fields.add("package.version")
            elif stripped.startswith("type:"):
                current_fields.add("package.type")
            continue

        if stripped.startswith("reason:"):
            current_fields.add("reason")

    return rules


def test_grype_yaml_has_exactly_one_fully_scoped_ignore_rule():
    lines = read_lines(GRYPE_YAML_PATH)
    rules = _ignore_rules(lines)

    assert len(rules) == 1, (
        f".grype.yaml must have exactly one ignore rule, found {len(rules)} "
        "(an unreviewed second rule must not be added silently)"
    )

    fields = rules[0]
    missing = [f for f in _REQUIRED_FIELDS if f not in fields]
    assert not missing, (
        ".grype.yaml's ignore rule is missing required field(s) "
        f"{missing} -- a rule must be scoped by vulnerability ID, package "
        "name, version and type, not a looser subset that could match a "
        "different CVE or package by coincidence"
    )
