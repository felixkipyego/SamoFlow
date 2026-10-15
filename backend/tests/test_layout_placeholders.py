# backend/tests/test_layout_placeholders.py
# Task 1.1.l: widget/ and dashboard/ were placeholder folders (no build
# tooling, no source code, no config) until their owning steps (5.1/5.2
# and 6.1) picked their own bundler/tooling. This guards two properties
# for whichever of them is still a placeholder: each README still
# exists and names its owning step, and no other file has crept in.
#
# Narrowed at Step 5.1 (per the Owner-5.1 §9 marker this test's own
# original entry recorded): widget/ tooling is now chosen and real
# (widget/package.json, widget/loader/src/, ...), so the "only a README"
# guard and the "says placeholder" requirement no longer apply to
# widget/loader. Narrowed again at Step 5.2 (2026-10-15, the identical
# §9 marker, same owning track): widget/chat/ now also has real tooling
# (widget/chat/src/, widget/chat/test/), so both requirements are
# dropped for it too. Narrowed a third time at Step 6.1 (2026-10-15, the
# dashboard track's own §9 marker): dashboard/ now also has real tooling
# (Vite + React + TypeScript + shadcn/ui, per the 2026-09-28 decision
# log entry's own "final decision... at the Step 6.1 breakdown") -- no
# folder this test tracks is still a placeholder, but the mechanism
# stays (rather than deleting this file outright) in case a later
# folder ever needs the identical placeholder-guard shape again.
from tests.conftest import REPO_ROOT

_READMES = {
    REPO_ROOT / "widget" / "loader" / "README.md": "5.1",
    REPO_ROOT / "widget" / "chat" / "README.md": "5.2",
    REPO_ROOT / "dashboard" / "README.md": "6.1",
}

_STILL_PLACEHOLDER_READMES: dict = {}

_PLACEHOLDER_ROOTS: tuple = ()


def test_readmes_exist_and_are_non_empty():
    for path in _READMES:
        assert path.is_file(), f"expected README at {path}, not found"
        assert path.read_text().strip(), f"{path} exists but is empty"


def test_readmes_name_their_owning_step():
    for path, step in _READMES.items():
        text = path.read_text()
        assert step in text, f"{path} does not mention its owning step {step!r}"


def test_still_placeholder_readmes_say_placeholder():
    for path in _STILL_PLACEHOLDER_READMES:
        text = path.read_text()
        assert "placeholder" in text.lower(), f"{path} does not say it is a placeholder"


def test_placeholder_roots_contain_only_readmes_and_folders():
    for root in _PLACEHOLDER_ROOTS:
        for path in root.rglob("*"):
            if path.is_dir():
                continue
            assert path.name == "README.md", (
                f"{path} is not a README.md: a placeholder folder gained tooling "
                "(a source file, config file or dependency manifest); that must "
                "be decided in the owning step, not added ahead of it."
            )
