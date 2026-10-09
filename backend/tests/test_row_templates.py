# backend/tests/test_row_templates.py
# Task 2.8.c: tests for app/ingest/row_templates.py. Fully offline, no
# database of any kind -- confirmed deliberately, not merely convenient:
# is_table_allowlisted() is a pure dict/string membership check, and
# render_row_to_text() only ever needs something dict-like to hold a row
# (`in`/`[]` by column name). Plain dicts are used throughout as that
# row stand-in, not a real asyncpg.Record -- confirmed live during this
# task's own design (not as a committed test, since it would need a real
# connection for no real benefit here) that asyncpg.Record supports `in`/
# `[]` by column NAME identically to a plain dict (`'col' in record` is a
# key-membership test, not a value test, despite Record's own sequence-
# like aspects; `record['missing']` raises KeyError, exactly like a dict)
# -- so a plain dict is a faithful stand-in for this module's own actual
# contract, and no live test-db round trip would prove anything a dict
# doesn't already prove just as well.
import pytest

from app.ingest.row_templates import (
    MissingColumnError,
    TemplateRenderError,
    is_table_allowlisted,
    render_row_to_text,
)

# --- is_table_allowlisted() ----------------------------------------------

_ALLOWLIST = {"tables": ["public.products", "public.orders"]}


def test_an_allowlisted_table_is_accepted():
    assert is_table_allowlisted(_ALLOWLIST, "public.products") is True


def test_a_non_allowlisted_table_is_rejected():
    assert is_table_allowlisted(_ALLOWLIST, "public.customers") is False


def test_a_bare_table_name_without_its_schema_is_rejected():
    # The allowlist itself only ever carries schema-qualified entries (see
    # row_templates.py's own header comment) -- a caller passing a bare
    # name, even one that's otherwise a real allowlisted table under a
    # different spelling, must not match.
    assert is_table_allowlisted(_ALLOWLIST, "products") is False


def test_case_sensitivity_is_exact_not_normalized():
    assert is_table_allowlisted(_ALLOWLIST, "Public.Products") is False


def test_an_empty_allowlist_rejects_everything():
    assert is_table_allowlisted({"tables": []}, "public.products") is False


def test_a_dict_missing_the_tables_key_entirely_rejects_everything_not_crashes():
    # The real column default (app/ingest/models.py: server_default
    # "'{}'::jsonb") -- a brand-new db_connections row with nothing
    # configured yet must be a safe, empty allowlist, not a KeyError.
    assert is_table_allowlisted({}, "public.products") is False


# --- render_row_to_text() -------------------------------------------------

_TEMPLATE_CONFIG = {
    "columns": ["name", "description", "price"],
    "template": "Product: {name} - {description}, priced at {price}",
}


def test_a_normal_row_with_all_columns_present_renders_correctly():
    row = {"name": "Widget", "description": "A fine widget", "price": 9.99}
    assert (
        render_row_to_text(row, _TEMPLATE_CONFIG)
        == "Product: Widget - A fine widget, priced at 9.99"
    )


def test_a_null_column_renders_as_an_empty_string():
    row = {"name": "Widget", "description": None, "price": 9.99}
    assert render_row_to_text(row, _TEMPLATE_CONFIG) == "Product: Widget - , priced at 9.99"


def test_a_genuinely_missing_declared_column_raises_missing_column_error():
    # "description" is declared in `columns` but never present in the row
    # at all -- a stale template, not a null value (test above).
    row = {"name": "Widget", "price": 9.99}
    with pytest.raises(MissingColumnError, match="description"):
        render_row_to_text(row, _TEMPLATE_CONFIG)


def test_a_template_placeholder_outside_declared_columns_raises_template_render_error():
    row = {"name": "Widget", "description": "A fine widget", "price": 9.99}
    bad_config = {
        "columns": ["name", "description", "price"],
        # "sku" is not in `columns` -- a typo/stale reference.
        "template": "Product {sku}: {name}",
    }
    with pytest.raises(TemplateRenderError):
        render_row_to_text(row, bad_config)


def test_format_specs_and_conversions_work_via_real_str_format_grammar():
    # Proves the documented claim: left to Python's own format-string
    # grammar, not a hand-rolled regex -- a format spec (:.2f) and a
    # conversion flag (!r) both work for free.
    row = {"price": 9.999, "name": "Widget"}
    config = {"columns": ["price", "name"], "template": "{price:.2f} / {name!r}"}
    assert render_row_to_text(row, config) == "10.00 / 'Widget'"


def test_an_extra_column_in_the_row_not_named_in_columns_is_ignored():
    # columns is the authoritative, least-privilege list (also what a
    # future query-builder would SELECT) -- an extra key the row happens
    # to carry (e.g. a primary key fetched for row-identity purposes,
    # 2.8.d's own job) must never leak into the rendered text just
    # because it exists in `row`.
    row = {"name": "Widget", "description": "A fine widget", "price": 9.99, "internal_id": 42}
    assert (
        render_row_to_text(row, _TEMPLATE_CONFIG)
        == "Product: Widget - A fine widget, priced at 9.99"
    )
