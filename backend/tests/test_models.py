# backend/tests/test_models.py
# Task 1.2.b: model-only tests -- inspect SQLAlchemy's metadata directly, no
# database needed. Real-database proof (autogenerate detects all 5 tables
# cleanly, the DDL actually applies, FK delete-actions match) was done by
# hand against the test-db and recorded in PROJECT_SPEC.md; that throwaway
# revision was discarded, never committed.
import uuid

from sqlalchemy.dialects.postgresql import UUID

from app.db import Base
from app.ingest import models as ingest_models  # noqa: F401
from app.plans import models as plans_models  # noqa: F401
from app.tenancy import models as tenancy_models  # noqa: F401
from tests.conftest import (
    CASCADE_FK_COLUMNS,
    EXPECTED_PK_COLUMNS,
    EXPECTED_STATUS_CHECK_CONSTRAINTS,
    EXPECTED_TABLES,
    EXPECTED_UNIQUE_INDEXES,
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


def test_status_check_constraints_match_the_spec_vocabulary():
    # Task 2.2.b: EXPECTED_STATUS_CHECK_CONSTRAINTS's values are now a LIST
    # of (constraint_name, allowed_values) pairs, not a single pair --
    # verified_domains is the first table needing two (method, status).
    for table_name, expected in EXPECTED_STATUS_CHECK_CONSTRAINTS.items():
        table = Base.metadata.tables[table_name]
        check_constraints = {
            c.name: c for c in table.constraints if c.__class__.__name__ == "CheckConstraint"
        }
        assert len(check_constraints) == len(expected), (
            f"{table_name} should have exactly {len(expected)} CHECK constraint(s)"
        )
        for constraint_name, allowed_values in expected:
            constraint = check_constraints[constraint_name]
            for value in allowed_values:
                assert value in str(constraint.sqltext)


def test_visitor_secret_hash_unique_index_is_scoped_to_site_key():
    for table_name, (index_name, columns) in EXPECTED_UNIQUE_INDEXES.items():
        table = Base.metadata.tables[table_name]
        (index,) = [i for i in table.indexes if i.name == index_name]
        assert index.unique is True
        assert [c.name for c in index.columns] == columns


def test_uuid_type_hint_matches_python_uuid():
    # Sanity check that Mapped[uuid.UUID] and UUID(as_uuid=True) agree
    # (as_uuid=False would hand back plain strings at runtime instead).
    column = Base.metadata.tables["tenants"].columns["id"]
    assert column.type.python_type is uuid.UUID


def test_jobs_source_id_is_nullable():
    # Task 2.1.a: a deliberate decision, not an afterthought -- reconcile
    # and refresh-scheduling jobs are not always tied to one source row.
    column = Base.metadata.tables["jobs"].columns["source_id"]
    assert column.nullable is True


def test_jobs_next_run_at_is_indexed():
    # Task 2.1.a: 2.1.c's claiming query filters/orders on this column.
    column = Base.metadata.tables["jobs"].columns["next_run_at"]
    assert column.index, "jobs.next_run_at has no index"


def _check_constraints_by_name(table_name: str) -> dict[str, object]:
    table = Base.metadata.tables[table_name]
    return {
        c.name: c for c in table.constraints if c.__class__.__name__ == "CheckConstraint"
    }


def test_verified_domains_active_domain_unique_index_is_partial():
    # Task 2.2.a, PROJECT_SPEC.md's Step 2.2 breakdown decision (e): global
    # uniqueness across ALL tenants, but only while a claim is active
    # (pending/verified) -- a revoked domain must become claimable again.
    # Must genuinely be PARTIAL (postgresql_where), not a full unique
    # constraint wearing a different name; checked directly on the Index
    # object's own dialect kwargs (SQLAlchemy Python metadata, no database
    # needed). The live proof that Postgres itself honors this is 2.2.b's
    # job, against a real migration.
    table = Base.metadata.tables["verified_domains"]
    (index,) = [i for i in table.indexes if i.name == "ix_verified_domains_domain_active_unique"]
    assert index.unique is True
    assert [c.name for c in index.columns] == ["domain"]
    where_clause = index.dialect_options["postgresql"]["where"]
    assert where_clause is not None, "index has no postgresql_where -- it is not partial"
    assert "revoked" in str(where_clause)


def test_documents_url_unique_index_is_partial_and_scoped_to_source():
    # Task 2.4.a: url/file_name are mutually exclusive per row, so each
    # gets its OWN partial unique index rather than one combined index --
    # see app/ingest/models.py's Document class for the full reasoning.
    # Must genuinely be PARTIAL (postgresql_where), not a full unique
    # constraint wearing a different name; checked directly on the Index
    # object's own dialect kwargs, matching
    # test_verified_domains_active_domain_unique_index_is_partial's own
    # pattern above. The live proof that Postgres itself honors this is
    # test_alembic.py's job, against a real migration.
    table = Base.metadata.tables["documents"]
    (index,) = [i for i in table.indexes if i.name == "ix_documents_source_id_url_unique"]
    assert index.unique is True
    assert [c.name for c in index.columns] == ["source_id", "url"]
    where_clause = index.dialect_options["postgresql"]["where"]
    assert where_clause is not None, "index has no postgresql_where -- it is not partial"
    assert "url IS NOT NULL" in str(where_clause)


def test_documents_file_name_unique_index_is_partial_and_scoped_to_source():
    # Task 2.4.a: the file_name-side twin of the url partial index above.
    table = Base.metadata.tables["documents"]
    (index,) = [
        i for i in table.indexes if i.name == "ix_documents_source_id_file_name_unique"
    ]
    assert index.unique is True
    assert [c.name for c in index.columns] == ["source_id", "file_name"]
    where_clause = index.dialect_options["postgresql"]["where"]
    assert where_clause is not None, "index has no postgresql_where -- it is not partial"
    assert "file_name IS NOT NULL" in str(where_clause)


def test_documents_row_identity_unique_index_is_partial_and_scoped_to_source():
    # Task 2.8.d: the row_identity-side twin of the url/file_name partial
    # indexes above -- resolves the long-open "whoever first designs real
    # identity/uniqueness for `database`-source documents" marker (2.4.a).
    table = Base.metadata.tables["documents"]
    (index,) = [
        i for i in table.indexes if i.name == "ix_documents_source_id_row_identity_unique"
    ]
    assert index.unique is True
    assert [c.name for c in index.columns] == ["source_id", "row_identity"]
    where_clause = index.dialect_options["postgresql"]["where"]
    assert where_clause is not None, "index has no postgresql_where -- it is not partial"
    assert "row_identity IS NOT NULL" in str(where_clause)


def test_audit_log_has_no_tenant_id_column():
    # Task 2.2.a: deliberately a platform-level log, not tenant-scoped --
    # confirmed, not merely absent by oversight (see app/ingest/models.py's
    # AuditLog class for the full reasoning).
    table = Base.metadata.tables["audit_log"]
    assert "tenant_id" not in table.columns


def test_audit_log_has_no_action_check_constraint():
    # Task 2.2.a: future actions (threshold overrides, plan changes,
    # docs/SPEC.md §15) are not enumerable yet -- deliberately an open
    # string, unlike every other closed-vocabulary status/type column in
    # this project.
    assert _check_constraints_by_name("audit_log") == {}


def test_db_connections_model_has_no_custom_repr_that_would_dump_columns():
    # Task 2.1.a: confirms the premise the credential-column design relies
    # on -- unlike pydantic's BaseModel (which dumps every field in its
    # own default repr()/str(), exactly why SecretStr exists), a plain
    # SQLAlchemy declarative model with no custom __repr__ does not. This
    # is what makes storing ciphertext in an ordinary column safe against
    # accidental repr/str logging: repr()/str() never reach column values
    # at all. The live, real-encryption leak proof (does the ORIGINAL
    # PLAINTEXT ever appear anywhere once a real row is fetched) is in
    # test_ingest_repository.py -- this test only proves the mechanism
    # neither class defines its own __repr__/__str__.
    assert "__repr__" not in ingest_models.DbConnection.__dict__
    assert "__str__" not in ingest_models.DbConnection.__dict__
    db_connection = ingest_models.DbConnection(
        tenant_id=uuid.uuid4(),
        host="db.example.internal",
        encrypted_credentials=b"placeholder",
        allowlisted_tables={},
        row_templates={},
    )
    assert repr(db_connection).startswith("<app.ingest.models.DbConnection object at 0x")
