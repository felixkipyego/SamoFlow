# backend/tests/test_openapi_contract.py
# Step 4.4-follow-up: kickoff decision #9 (Felix's §11.3 parallel-track
# protocol, 2026-10-15) -- "add a test that fails if the app's schema
# drifts from it [the committed docs/openapi.json]." This is the real
# enforcement mechanism for that protocol's own "changing the contract
# during the tracks is stop condition (a)" rule: once Felix starts the
# two parallel worktrees, this test is the thing that actually catches
# a contract change landing on either branch before it ships silently.
import json
from pathlib import Path

from app.config import get_settings
from app.main import create_app
from tests.conftest import VALID_ENV, set_valid_env

_SCHEMA_PATH = Path(__file__).resolve().parent.parent.parent / "docs" / "openapi.json"


def test_the_committed_openapi_schema_file_exists():
    assert _SCHEMA_PATH.exists(), (
        f"{_SCHEMA_PATH} not found -- generate it with "
        "`python backend/scripts/generate_openapi_schema.py`"
    )


def test_the_real_app_schema_matches_the_committed_contract_file(monkeypatch):
    set_valid_env(monkeypatch, VALID_ENV)
    get_settings.cache_clear()
    app = create_app()
    real_schema = app.openapi()

    committed_schema = json.loads(_SCHEMA_PATH.read_text())

    assert real_schema == committed_schema, (
        "The real app's OpenAPI schema no longer matches the committed "
        f"contract at {_SCHEMA_PATH}. If this change is deliberate, "
        "regenerate it with `python backend/scripts/generate_openapi_"
        "schema.py` and commit the result. If the two parallel tracks "
        "(widget/dashboard, PROJECT_SPEC.md §11.5) are currently running, "
        "this drift is stop condition (a) -- stop and report, do not "
        "silently regenerate."
    )


def test_the_committed_contract_file_is_the_real_regeneration_script_output(monkeypatch):
    # A second, independent proof of the same fact the test above checks
    # -- this one drives the actual committed script end to end (not a
    # hand-reimplemented equivalent), confirming the generator itself
    # (scripts/generate_openapi_schema.py) produces exactly this file,
    # not merely that SOME schema happens to match it.
    from scripts.generate_openapi_schema import generate_schema

    set_valid_env(monkeypatch, VALID_ENV)
    get_settings.cache_clear()
    regenerated = generate_schema()
    committed_schema = json.loads(_SCHEMA_PATH.read_text())
    assert regenerated == committed_schema
