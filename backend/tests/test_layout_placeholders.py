# backend/tests/test_layout_placeholders.py
# Task 1.1.l: widget/ and dashboard/ are placeholder folders (no build
# tooling, no source code, no config) until their owning steps (5.1 and
# 6.1) pick the bundler/tooling. This guards both properties: each README
# still exists and names its owning step, and no other file has crept in.
from tests.conftest import REPO_ROOT

_READMES = {
    REPO_ROOT / "widget" / "loader" / "README.md": "5.1",
    REPO_ROOT / "widget" / "chat" / "README.md": "5.2",
    REPO_ROOT / "dashboard" / "README.md": "6.1",
}

_PLACEHOLDER_ROOTS = (REPO_ROOT / "widget", REPO_ROOT / "dashboard")


def test_readmes_exist_and_are_non_empty():
    for path in _READMES:
        assert path.is_file(), f"expected README at {path}, not found"
        assert path.read_text().strip(), f"{path} exists but is empty"


def test_readmes_name_their_owning_step_and_say_placeholder():
    for path, step in _READMES.items():
        text = path.read_text()
        assert step in text, f"{path} does not mention its owning step {step!r}"
        assert "placeholder" in text.lower(), f"{path} does not say it is a placeholder"


def test_widget_and_dashboard_contain_only_readmes_and_folders():
    for root in _PLACEHOLDER_ROOTS:
        for path in root.rglob("*"):
            if path.is_dir():
                continue
            assert path.name == "README.md", (
                f"{path} is not a README.md: a placeholder folder gained tooling "
                "(a source file, config file or dependency manifest); that must "
                "be decided in the owning step, not added ahead of it."
            )
