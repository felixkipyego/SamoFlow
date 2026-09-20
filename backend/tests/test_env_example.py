# backend/tests/test_env_example.py
# Tests for Task 1.1.d's .env.example: it must declare exactly the
# variables Settings (Task 1.1.c) requires, each documented and safe to
# commit, and loadable as-is into a working Settings instance.
from pathlib import Path
from urllib.parse import urlsplit

import pytest

from app.config import Settings, get_settings

# NOTE: adding a key here is a deliberate decision; add it in the task that needs it
EXTRA_EXAMPLE_KEYS: tuple[str, ...] = ()

REQUIRED_VARS = [name.upper() for name in Settings.model_fields]

HEADER_WARNING = "# Copy to .env and edit. Never commit .env."

# Derived from this test file's own location, not the current working
# directory, so the test passes regardless of where pytest is invoked from.
ENV_EXAMPLE_PATH = Path(__file__).resolve().parent.parent.parent / ".env.example"


def _lines():
    assert ENV_EXAMPLE_PATH.exists(), f".env.example not found at {ENV_EXAMPLE_PATH}"
    return ENV_EXAMPLE_PATH.read_text().splitlines()


def _classify_lines(lines):
    # Classifies every line as "blank", "comment" or "variable". Any
    # non-blank, non-comment line must contain "=" to be a variable line;
    # anything else is a parse error naming the exact offending line, not a
    # silently-wrong classification.
    classified = []
    for i, line in enumerate(lines):
        stripped = line.strip()
        if not stripped:
            classified.append((i, "blank"))
        elif stripped.startswith("#"):
            classified.append((i, "comment"))
        else:
            assert "=" in line, (
                f"line {i + 1} ({line!r}) is not blank or a comment but has "
                "no '=': cannot be parsed as KEY=VALUE"
            )
            classified.append((i, "variable"))
    return classified


def _variable_line_indexes(lines):
    return [i for i, kind in _classify_lines(lines) if kind == "variable"]


def _parse_env_example():
    # Plain parse: skip blank lines and comment lines, split each other
    # line at the first "=". Returns key -> value for the last occurrence
    # of each key (duplicate-key detection is a separate test). Rejects an
    # inline " #" comment on a value line, since a naive loader (this one
    # included) would otherwise fold the comment into the value.
    lines = _lines()
    values = {}
    for i in _variable_line_indexes(lines):
        line = lines[i]
        key, _, value = line.partition("=")
        key = key.strip()
        assert " #" not in value, (
            f"{key}'s value ({value!r}) contains an inline comment ('  #'); "
            "a plain loader would fold this into the value -- put the "
            "comment on its own line above the variable instead"
        )
        values[key] = value
    return values


@pytest.fixture(autouse=True)
def _isolated_env(monkeypatch):
    for name in REQUIRED_VARS:
        monkeypatch.delenv(name, raising=False)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_no_settings_field_has_a_custom_alias():
    # REQUIRED_VARS maps each field to an env var name by plain
    # field_name.upper(). A custom alias would silently break that mapping
    # without any of the other tests here noticing.
    for name, field in Settings.model_fields.items():
        assert field.alias is None, (
            f"Settings field {name!r} has alias={field.alias!r}; this file's "
            "REQUIRED_VARS assumes env var names are exactly "
            "field_name.upper(), which a custom alias would break"
        )
        assert field.validation_alias is None, (
            f"Settings field {name!r} has validation_alias="
            f"{field.validation_alias!r}; this file's REQUIRED_VARS assumes "
            "env var names are exactly field_name.upper(), which a custom "
            "validation_alias would break"
        )
        assert field.serialization_alias is None, (
            f"Settings field {name!r} has serialization_alias="
            f"{field.serialization_alias!r}; this file's REQUIRED_VARS "
            "assumes env var names are exactly field_name.upper(), which a "
            "custom serialization_alias would break"
        )


def test_variable_names_match_settings_fields_plus_extras():
    required = set(REQUIRED_VARS) | set(EXTRA_EXAMPLE_KEYS)
    lines = _lines()
    found = {lines[i].partition("=")[0].strip() for i in _variable_line_indexes(lines)}
    assert found == required


def test_every_variable_line_has_a_comment_line_directly_above_it():
    lines = _lines()
    for i in _variable_line_indexes(lines):
        assert i > 0, f"line {i} ({lines[i]!r}) has no line above it"
        above = lines[i - 1].strip()
        assert above.startswith("#"), (
            f"line {i} ({lines[i]!r}) is not directly preceded by a comment "
            f"(previous line was {lines[i - 1]!r})"
        )


def test_each_variable_appears_exactly_once():
    lines = _lines()
    keys = [lines[i].partition("=")[0].strip() for i in _variable_line_indexes(lines)]
    for key in set(keys):
        assert keys.count(key) == 1, f"{key} appears {keys.count(key)} times"


def test_parsed_values_load_into_a_working_settings_instance(monkeypatch):
    values = _parse_env_example()
    for key, value in values.items():
        monkeypatch.setenv(key, value)
    settings = get_settings()
    assert settings.app_env == "development"
    assert settings.qdrant_url == "http://qdrant:6333"
    assert settings.api_host == "0.0.0.0"  # noqa: S104 (the example's own placeholder, not a live bind)
    assert settings.api_port == 8000
    assert isinstance(settings.api_port, int)


def test_database_url_password_placeholder_is_exactly_change_me():
    values = _parse_env_example()
    password = urlsplit(values["DATABASE_URL"]).password
    assert password == "change-me"  # noqa: S105 (asserting the placeholder value, not a real secret)


def test_first_non_blank_line_is_the_header_warning():
    lines = _lines()
    first_non_blank = next(line for line in lines if line.strip())
    assert first_non_blank == HEADER_WARNING, (
        f"first non-blank line is {first_non_blank!r}, expected {HEADER_WARNING!r}"
    )


def test_env_example_path_is_independent_of_current_working_directory(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert ENV_EXAMPLE_PATH.is_absolute()
    assert ENV_EXAMPLE_PATH.exists()
