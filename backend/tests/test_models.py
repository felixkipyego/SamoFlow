# backend/tests/test_models.py
# Task 1.2.b: model-only tests -- inspect SQLAlchemy's metadata directly, no
# database needed. Real-database proof (autogenerate detects all 5 tables
# cleanly, the DDL actually applies, FK delete-actions match) was done by
# hand against the test-db and recorded in PROJECT_SPEC.md; that throwaway
# revision was discarded, never committed.
import uuid

from sqlalchemy.dialects.postgresql import UUID

from app.db import Base
from app.plans import models as plans_models  # noqa: F401
from app.tenancy import models as tenancy_models  # noqa: F401
from tests.conftest import (
    CASCADE_FK_COLUMNS,
    EXPECTED_PK_COLUMNS,
    EXPECTED_TABLES,
    NO_ACTION_FK_COLUMNS,
)


def test_expected_tables_are_registered():
    assert set(Base.metadata.tables.keys()) == EXPECTED_TABLES


def test_primary_keys_are_server_default_uuid():
    for table_name, pk_name in EXPECTED_PK_COLUMNS.items():
        table = Base.metadata.tables[table_name]
        column = table.columns[pk_name]
        assert column.primary_key
        assert isinstance(column.type, UUID)
        assert column.type.as_uuid is True
        assert column.server_default is not None, (
            f"{table_name}.{pk_name} has no server-side default (expected "
            "gen_random_uuid())"
        )
        assert "gen_random_uuid" in str(column.server_default.arg)


def _fk_ondelete(table_name: str, column_name: str) -> str | None:
    table = Base.metadata.tables[table_name]
    column = table.columns[column_name]
    (fk,) = column.foreign_keys
    return fk.ondelete


def test_tenant_id_foreign_keys_cascade_on_delete():
    for table_name, column_name in CASCADE_FK_COLUMNS:
        assert _fk_ondelete(table_name, column_name) == "CASCADE", (
            f"{table_name}.{column_name} must cascade from tenants"
        )


def test_non_tenant_foreign_keys_have_no_cascade():
    for table_name, column_name in NO_ACTION_FK_COLUMNS:
        assert _fk_ondelete(table_name, column_name) is None, (
            f"{table_name}.{column_name} should not cascade "
            "(no delete feature needs it yet)"
        )


def test_tenant_id_columns_are_not_nullable():
    # Cheap static backstop for the live-database proof in test_alembic.py
    # (which actually attempts a NULL insert against real Postgres): a null
    # tenant_id would silently escape tenant-scoped filtering later.
    for table_name in ("site_keys", "visitors", "conversations"):
        table = Base.metadata.tables[table_name]
        assert table.columns["tenant_id"].nullable is False, (
            f"{table_name}.tenant_id must be NOT NULL"
        )


def test_every_tenant_scoped_table_indexes_its_tenant_id():
    # Postgres does not auto-index FK columns (confirmed by hand against the
    # pinned image); every tenant-scoped query filters on tenant_id, so each
    # must have an explicit index, not just the FK constraint.
    for table_name in ("site_keys", "visitors", "conversations"):
        table = Base.metadata.tables[table_name]
        column = table.columns["tenant_id"]
        assert column.index, f"{table_name}.tenant_id has no index"


def test_site_keys_status_check_constraint_matches_the_spec_vocabulary():
    table = Base.metadata.tables["site_keys"]
    check_constraints = [c for c in table.constraints if c.__class__.__name__ == "CheckConstraint"]
    assert len(check_constraints) == 1
    assert "draft" in str(check_constraints[0].sqltext)
    assert "live" in str(check_constraints[0].sqltext)
    assert "suspended" in str(check_constraints[0].sqltext)


def test_uuid_type_hint_matches_python_uuid():
    # Sanity check that Mapped[uuid.UUID] and UUID(as_uuid=True) agree
    # (as_uuid=False would hand back plain strings at runtime instead).
    column = Base.metadata.tables["tenants"].columns["id"]
    assert column.type.python_type is uuid.UUID
