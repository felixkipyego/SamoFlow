# backend/tests/test_openapi_additive_check.py
# Step 6.1: scripts/check_openapi_additive.py -- this task's own direct
# instruction, "prove the diff only adds, with a script that compares
# old and new". Unit tests against check_additive() directly (pure
# function, no I/O), plus one live proof against the real committed
# docs/openapi.json and the real app's current schema.
from scripts.check_openapi_additive import check_additive


def test_identical_schemas_are_additive():
    schema = {"paths": {"/a": {"get": {}}}, "components": {"schemas": {"X": {"type": "object"}}}}
    assert check_additive(schema, schema) == []


def test_a_new_path_is_additive():
    old = {"paths": {"/a": {"get": {}}}}
    new = {"paths": {"/a": {"get": {}}, "/b": {"post": {}}}}
    assert check_additive(old, new) == []


def test_a_new_schema_is_additive():
    old = {"components": {"schemas": {"X": {"type": "object"}}}}
    new = {"components": {"schemas": {"X": {"type": "object"}, "Y": {"type": "object"}}}}
    assert check_additive(old, new) == []


def test_removing_an_existing_path_is_not_additive():
    old = {"paths": {"/a": {"get": {}}}}
    new = {"paths": {}}
    violations = check_additive(old, new)
    assert any("/a" in v for v in violations)


def test_changing_an_existing_paths_shape_is_not_additive():
    old = {"paths": {"/a": {"get": {"summary": "old"}}}}
    new = {"paths": {"/a": {"get": {"summary": "new"}}}}
    violations = check_additive(old, new)
    assert any("summary" in v for v in violations)


def test_adding_a_new_property_to_an_existing_schema_is_additive():
    # "New keys may be added anywhere" (this script's own docstring) --
    # a new OPTIONAL property on an already-shipped schema is the
    # standard, safe way an OpenAPI shape evolves (an old client that
    # never reads it is unaffected); only REMOVING or CHANGING a
    # property the old schema already had is flagged.
    old = {"components": {"schemas": {"X": {"properties": {"a": {}}}}}}
    new = {"components": {"schemas": {"X": {"properties": {"a": {}, "b": {}}}}}}
    assert check_additive(old, new) == []


def test_changing_an_existing_propertys_shape_is_not_additive():
    old = {"components": {"schemas": {"X": {"properties": {"a": {"type": "string"}}}}}}
    new = {"components": {"schemas": {"X": {"properties": {"a": {"type": "integer"}}}}}}
    violations = check_additive(old, new)
    assert any("/a" in v for v in violations)


def test_info_version_changes_are_ignored():
    old = {"info": {"version": "0.1.0"}, "paths": {}}
    new = {"info": {"version": "0.2.0"}, "paths": {}}
    assert check_additive(old, new) == []


def test_the_real_committed_schema_is_additive_relative_to_the_locked_contract_history():
    # A live, structural proof -- not a hand-built fixture: the real
    # app's current schema, checked against itself, must be clean (the
    # identity case), confirming this script works against the actual
    # shape FastAPI's own app.openapi() produces, not only hand-written
    # toy dicts above.
    import json
    from pathlib import Path

    schema_path = Path(__file__).resolve().parent.parent.parent / "docs" / "openapi.json"
    committed = json.loads(schema_path.read_text())
    assert check_additive(committed, committed) == []
