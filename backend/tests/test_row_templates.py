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
import uuid

import pytest

from app.ingest.row_templates import (
    MissingColumnError,
    MissingPrimaryKeyError,
    TemplateRenderError,
    derive_row_identity,
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


# --- derive_row_identity() ------------------------------------------------

_PRODUCTS_CONFIG = {
    "columns": ["name", "price"],
    "template": "{name}: {price}",
    "primary_key": "id",
}


def test_a_normal_integer_primary_key_derives_the_expected_identity():
    row = {"id": 42, "name": "Widget", "price": 9.99}
    assert derive_row_identity(row, "public.products", _PRODUCTS_CONFIG) == "public.products:42"


def test_a_string_primary_key_derives_the_expected_identity():
    row = {"id": "SKU-001", "name": "Widget", "price": 9.99}
    assert (
        derive_row_identity(row, "public.products", _PRODUCTS_CONFIG) == "public.products:SKU-001"
    )


def test_a_uuid_primary_key_derives_the_expected_identity():
    pk = uuid.uuid4()
    row = {"id": pk, "name": "Widget", "price": 9.99}
    assert derive_row_identity(row, "public.products", _PRODUCTS_CONFIG) == f"public.products:{pk}"


def test_a_primary_key_value_containing_a_colon_does_not_break_derivation():
    # Edge case named directly by this task's own test requirement: the
    # table prefix is always written first and whole, so a colon inside
    # the pk VALUE itself cannot be confused with the table/pk separator
    # -- it simply becomes part of the (still perfectly valid, still
    # collision-free in practice) tail of the string.
    row = {"id": "weird:value", "name": "Widget", "price": 9.99}
    assert (
        derive_row_identity(row, "public.products", _PRODUCTS_CONFIG)
        == "public.products:weird:value"
    )


def test_a_zero_primary_key_value_is_a_genuine_identity_not_treated_as_falsy():
    # 0 is a perfectly real primary key value -- derive_row_identity()
    # must not accidentally treat it like None/missing via a truthiness
    # check instead of an explicit `is None` check.
    row = {"id": 0, "name": "Widget", "price": 9.99}
    assert derive_row_identity(row, "public.products", _PRODUCTS_CONFIG) == "public.products:0"


def test_a_missing_primary_key_column_raises_missing_primary_key_error():
    row = {"name": "Widget", "price": 9.99}  # no "id" at all
    with pytest.raises(MissingPrimaryKeyError, match="id"):
        derive_row_identity(row, "public.products", _PRODUCTS_CONFIG)


def test_a_null_primary_key_value_raises_missing_primary_key_error():
    row = {"id": None, "name": "Widget", "price": 9.99}
    with pytest.raises(MissingPrimaryKeyError, match="NULL"):
        derive_row_identity(row, "public.products", _PRODUCTS_CONFIG)


def test_different_tables_with_the_same_primary_key_value_never_collide():
    row = {"id": 1, "name": "Widget", "price": 9.99}
    products_identity = derive_row_identity(row, "public.products", _PRODUCTS_CONFIG)
    orders_identity = derive_row_identity(row, "public.orders", _PRODUCTS_CONFIG)
    assert products_identity != orders_identity
    assert products_identity == "public.products:1"
    assert orders_identity == "public.orders:1"
