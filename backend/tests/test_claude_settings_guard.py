# backend/tests/test_claude_settings_guard.py
# Out-of-band maintenance (2026-10-17), Part A: a prior batch ran
# `git reset --hard` without checking status, against CLAUDE.md's own
# "Executing actions with care" rule. .claude/settings.json's own
# permissions.deny list is the mechanical backstop for the handful of
# whole-tree-destructive commands named there -- this guards that the file
# stays valid JSON and that each named command family is still covered,
# so a future edit cannot silently drop a deny entry (or break the file
# outright, which would silently disable every setting in it, not just
# this list).
import json

from tests.conftest import REPO_ROOT

SETTINGS_PATH = REPO_ROOT / ".claude" / "settings.json"

# One representative prefix per destructive command family named in the
# CLAUDE.md pointer -- not every flag permutation (Bash deny rules are
# literal-prefix matches, not argv-aware; see the pointer for the known
# gap and the safe alternative for each).
_REQUIRED_DENY_PREFIXES = (
    "Bash(git reset --hard",
    "Bash(git clean -f",
    "Bash(git clean -fd",
    "Bash(git clean -fdx",
    "Bash(git push --force",
    "Bash(git push -f",
    "Bash(git push --force-with-lease",
    "Bash(git checkout -- .",
    "Bash(git restore .",
    "Bash(git branch -D",
    "Bash(docker system prune",
    "Bash(docker volume prune",
    "Bash(rm -rf .",
    "Bash(rm -rf /",
)


def test_settings_json_parses():
    assert SETTINGS_PATH.is_file(), f"expected {SETTINGS_PATH} to exist"
    text = SETTINGS_PATH.read_text()
    data = json.loads(text)  # raises json.JSONDecodeError on malformed JSON
    assert isinstance(data, dict)


def test_deny_list_covers_every_named_destructive_command():
    data = json.loads(SETTINGS_PATH.read_text())
    deny = data.get("permissions", {}).get("deny", [])
    assert deny, ".claude/settings.json has no permissions.deny entries"
    for prefix in _REQUIRED_DENY_PREFIXES:
        assert any(entry.startswith(prefix) for entry in deny), (
            f"no permissions.deny entry covers {prefix!r}"
        )


def test_deny_entries_use_the_bash_prefix_wildcard_syntax():
    # Every entry should be of the form "Bash(<prefix>:*)" (prefix match,
    # covers trailing arguments) -- not an exact-match entry that a single
    # extra argument would silently slip past, and not a bare "Bash(...)"
    # with no tool-scoping.
    data = json.loads(SETTINGS_PATH.read_text())
    deny = data.get("permissions", {}).get("deny", [])
    for entry in deny:
        assert entry.startswith("Bash("), f"deny entry is not Bash-scoped: {entry!r}"
        assert entry.endswith(":*)"), f"deny entry has no trailing :* wildcard: {entry!r}"
