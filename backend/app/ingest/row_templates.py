# backend/app/ingest/row_templates.py
# Task 2.8.c: the database-sync adapter's own `allowlisted_tables`/
# `row_templates` shape (the two `db_connections` JSONB columns that have
# existed, unused, since 2.1.a -- that task's own comment deliberately left
# their internal shape undecided "until the real adapter knows exactly what
# it needs"), plus the one real piece of logic this shape needs today: a
# membership check (`is_table_allowlisted()`) and the row-to-text template
# renderer (`render_row_to_text()`). A new, separate module from
# `database_adapter.py` -- that file owns the connection/SSRF/read-only/
# one-query-guard chain (2.8.a/b); this one owns data SHAPING (which table,
# which columns, how a row becomes text), a genuinely different concern,
# mirroring the upload adapter's own precedent of splitting narrow,
# independently-testable concerns into their own files rather than growing
# one file indefinitely.
#
# Single-table-per-sync only -- a real, confirmed scope decision, not an
# oversight. No joins, no multi-table queries, in this step (or in 2.8.d/e,
# until a later task deliberately revisits this). `allowlisted_tables`'s
# own shape (a flat list) does not structurally PREVENT someone from later
# deciding a `sources.config` entry could name more than one table for one
# source -- but the renderer and any query construction THIS step builds
# only ever target ONE allowlisted table at a time. Recorded as a new,
# explicit Open marker (PROJECT_SPEC.md): joins across tables are deferred,
# not built now.
#
# `allowlisted_tables` shape: `{"tables": ["public.products", ...]}` -- a
# named key wrapping a list, matching this project's own established
# `sources.config` convention exactly (`{"urls": [...]}`, `{"uploads":
# [...]}`), not a bare list stored directly in a column the ORM types as
# `Mapped[dict]` (`app/ingest/models.py`'s `DbConnection.allowlisted_
# tables`) -- a bare JSON array would still be valid JSONB, but storing it
# under a named key keeps this column's own Python-level shape genuinely a
# dict, matching its own type hint, and leaves room to add a sibling key
# later (e.g. a per-table row cap override) without a breaking shape
# change.
#
# Each entry is schema-qualified ("schema.table"), not a bare table name --
# checked, not assumed: docs/SPEC.md §5.6 gives no schema guidance either
# way, but this connector targets an ARBITRARY tenant-owned external
# database (not this project's own Postgres), and non-public schemas are a
# completely ordinary, common real-world pattern for exactly that kind of
# database (enterprise/ERP-style apps, multi-schema setups) -- assuming
# "public" only would be a real limitation for real tenants, and spelling
# out the schema costs nothing in complexity (it is still one plain
# string). Deliberately a plain dotted string, not a nested `{"schema":
# ..., "table": ...}` object: a single string is simpler to store, compare
# and use as a `row_templates` dict key, and this step has no evidence a
# richer structure is ever needed (rule 11). A table/schema name containing
# a literal `.` itself (a quoted, non-standard identifier) is out of scope
# -- `is_table_allowlisted()` does plain string membership, nothing more.
#
# `row_templates` shape, keyed by the SAME schema-qualified string
# `allowlisted_tables` uses -- a deliberate adjustment from the task's own
# illustrative example (which keyed by a bare table name, "products"):
# keying by the bare name would be ambiguous the moment two allowlisted
# tables in different schemas share one name, and the whole point of
# schema-qualifying `allowlisted_tables` above is to avoid exactly that
# ambiguity -- keying `row_templates` any other way would silently
# reintroduce it one dict over. `{"<schema.table>": {"columns": [...],
# "template": "..."}}` -- `columns` is the list of column names this
# table's own template actually uses (also the list a future query-builder
# -- confirmed, at the duplication check after 2.8.d/e/f, to have actually
# been built at 2.8.e, NOT 2.8.d as this paragraph originally predicted
# here; see that same check's own note on the paragraph below -- would
# SELECT, never `SELECT *`, a real `COLUMNS <<` least-privilege property
# this step's own shape already enables, even though building that query
# is not this step's job); `template` is a plain `str.format()`-style
# string, deliberately NOT a templating DSL (rule 11 -- no evidence a real
# tenant row-to-text need is more complex than substitution).
#
# Composition with 2.8.b, stated plainly (not yet wired -- originally
# predicted here as "2.8.d's own job," confirmed WRONG at the duplication
# check after 2.8.d/e/f, 2026-10-16: neither 2.8.c nor 2.8.d ever built a
# query-builder; it was actually built at 2.8.e, build_table_select_query()
# in database_adapter.py -- left here, corrected, rather than silently
# fixed, matching the extract_docx.py/2.7.c precedent for a stale forward-
# reference in a completed task's own file; see PROJECT_SPEC.md's own
# tracked Open marker for this): a real future query-builder must call
# is_table_allowlisted() BEFORE ever constructing a SELECT string, as a
# FIFTH independent layer (FOURTH when this paragraph was first written,
# at 2.8.c -- 2.8.e added a fifth, the strict SQL-identifier allow-list
# check build_table_select_query() also performs, alongside 2.8.b's own
# three (the one-query textual guard, ensure_read_only()'s own write
# probe, and the real `transaction(readonly=True)` wrapping) --
# table-level authorization is a genuinely different property from any of
# those (none of them know or care WHICH table a query touches, only that
# it is a single, real SELECT against a connection that cannot write).
# fetch_readonly_rows() (2.8.b) takes an already-fully-formed query string
# and has no idea what this module's own allowlist even is; the CALLER
# (2.8.e, not 2.8.d) is responsible for building a query this allowlist,
# the textual guard, AND the actual database's own real grants would all
# separately accept.
#
# Task 2.8.d: each table's own config gains a THIRD field, `"primary_key"`
# -- a plain column name (a string, matching `columns`'s own "just names,
# no structure" shape), alongside the existing `"columns"`/`"template"` --
# not a restructuring of what 2.8.c already built, exactly as that task's
# own instruction required. Deliberately NOT required to also appear in
# `"columns"` itself: an internal id column is a completely ordinary real
# case for a primary key that should never be rendered into the knowledge-
# base text itself (confirmed by this module's own 2.8.c precedent --
# render_row_to_text() already tolerates and ignores a row carrying extra
# columns beyond `columns`, a design decision made exactly so an id column
# fetched alongside the rendered ones would not need to appear in the
# rendered text). derive_row_identity() below is the one new function this
# field exists for -- see its own docstring for the full identity scheme
# and how it composes with finish_ingest() (app/ingest/qdrant_writer.py,
# widened at this same task to accept a third identity dimension,
# `row_identity`, alongside its own pre-existing `url`/`file_name`).
from collections.abc import Mapping
from typing import Any


class MissingColumnError(Exception):
    """Raised by render_row_to_text() when a column named in the
    template's own `columns` list is genuinely absent from `row` -- a
    configuration problem (the template is stale against the real table
    shape), not a data condition, so it is never silently papered over.
    Distinct from a NULL value (see this module's own header comment and
    render_row_to_text()'s own docstring for why those are handled
    differently).
    """


class TemplateRenderError(Exception):
    """Raised by render_row_to_text() when `template` references a
    placeholder not present in the template's own declared `columns` --
    almost always a typo in the template string itself, caught here
    rather than surfacing as a bare, unexplained KeyError from
    str.format() internals.
    """


class MissingPrimaryKeyError(Exception):
    """Raised by derive_row_identity() when the table's own declared
    `primary_key` column is either absent from `row` or NULL -- same
    "surface loudly, don't paper over it" philosophy as MissingColumnError
    (this module's own header comment): a missing primary-key column is a
    stale template, matching MissingColumnError's own reasoning exactly;
    a NULL primary-key VALUE is different again (the column resolved fine,
    but a NULL can never be a stable identity for anything) -- both are
    genuine configuration/data-integrity problems, never a per-row
    variation to render around the way a NULL in `columns` is.
    """


def derive_row_identity(
    row: Mapping[str, Any], table: str, template_config: Mapping[str, Any]
) -> str:
    """The stable identity string for one fetched row: `f"{table}:
    {pk_value}"`, where `pk_value` is `row[template_config["primary_key"]]`
    -- the scheme named directly by this task's own scope decision, not
    invented here. `table` is the SAME schema-qualified string
    `allowlisted_tables`/`row_templates` both use (2.8.c) -- passed
    explicitly, not read from `template_config` itself, matching
    is_table_allowlisted()'s own "table is always an explicit parameter"
    convention (this module's own header comment). Schema-qualification
    is exactly what keeps this scheme collision-free across tables: two
    different allowlisted tables can never produce the same identity
    string unless their own schema-qualified names were themselves
    ambiguous, which `is_table_allowlisted()`'s own plain-string membership
    check already assumes they are not.

    Raises MissingPrimaryKeyError if `primary_key`'s own column is absent
    from `row` or its value is NULL -- see that exception's own docstring.
    Never renders a NULL primary key as an empty string the way
    render_row_to_text() does for an ordinary template column: a NULL
    identity would make every such row collide on `f"{table}:"`, silently
    merging genuinely distinct rows into one `documents` row -- the exact
    failure mode this whole mechanism exists to prevent, so it fails
    loudly here instead.
    """
    pk_column = template_config["primary_key"]
    if pk_column not in row:
        raise MissingPrimaryKeyError(
            f"row is missing its declared primary_key column {pk_column!r}"
        )
    pk_value = row[pk_column]
    if pk_value is None:
        raise MissingPrimaryKeyError(
            f"primary_key column {pk_column!r} is NULL for this row -- a NULL "
            "value can never be a stable row identity"
        )
    return f"{table}:{pk_value}"


def is_table_allowlisted(allowlisted_tables: Mapping[str, Any], table: str) -> bool:
    """Plain membership check against `allowlisted_tables`'s own `"tables"`
    list -- the exact shape stored on `db_connections.allowlisted_tables`
    (see this module's own header comment). Case-sensitive, exact-string
    match only; no schema-default-assumption, no identifier normalization
    -- `table` must already be the same schema-qualified string the
    allowlist itself uses.
    """
    return table in allowlisted_tables.get("tables", [])


def render_row_to_text(row: Mapping[str, Any], template_config: Mapping[str, Any]) -> str:
    """Renders one fetched row into the text block finish_ingest() expects
    as `content` (app/ingest/qdrant_writer.py), using `template_config`'s
    own `"columns"`/`"template"` (the shape documented at this module's
    own header comment -- `row_templates[<schema.table>]`).

    Edge cases, decided plainly, not left implicit:
    - A column named in `columns` whose value in `row` is NULL (Python
      `None`, confirmed live that asyncpg.Record surfaces a SQL NULL this
      way): rendered as an empty string. NULL is legitimate, present data
      that happens to be empty -- "Description: " is harmless output, and
      failing an entire sync row over one empty column would discard
      otherwise-good content for no real benefit.
    - A column named in `columns` that is genuinely ABSENT from `row`
      (confirmed live that both a plain dict and a real asyncpg.Record
      support `in`/`[]` by column name identically, so this check works
      against either): raises MissingColumnError. Unlike NULL, this is not
      a data condition at all -- it means the template's own `columns`
      list no longer matches the table's real shape (a dropped/renamed
      column since the template was configured), a configuration problem
      that should surface loudly so whoever configured the template can
      fix it, not fail silently into confusing, permanently-incomplete
      knowledge-base content.
    - A `template` placeholder naming something outside `columns` (e.g. a
      typo, or a stale reference to a column the template no longer
      declares): raises TemplateRenderError -- a wrapped, named version
      of str.format()'s own bare KeyError/IndexError, which `template.
      format(**values)` raises for exactly this (confirmed live: an
      unknown named placeholder raises KeyError; `{0}`/positional-style
      raises IndexError since no positional args are ever passed). Left
      to Python's own real format-string grammar to parse (format specs
      like `{price:.2f}`, conversions like `{name!r}`, included for free)
      rather than a hand-rolled placeholder-name regex that would need to
      re-implement that grammar to stay correct.
    """
    columns = template_config["columns"]
    template = template_config["template"]

    values: dict[str, Any] = {}
    for column in columns:
        if column not in row:
            raise MissingColumnError(f"row is missing declared column {column!r}")
        value = row[column]
        values[column] = "" if value is None else value

    try:
        return template.format(**values)
    except (KeyError, IndexError) as exc:
        raise TemplateRenderError(
            f"template references a placeholder not in this table's own "
            f"declared columns {sorted(columns)}: {exc}"
        ) from exc
