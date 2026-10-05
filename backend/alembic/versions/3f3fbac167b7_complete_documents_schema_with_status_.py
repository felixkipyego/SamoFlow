"""complete documents schema with status check and url/file_name partial unique indexes

Revision ID: 3f3fbac167b7
Revises: 77707a3b5369
Create Date: 2026-10-05 15:02:32.567088

"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "3f3fbac167b7"
down_revision: str | Sequence[str] | None = "77707a3b5369"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Task 2.4.a: resolves documents' own TODO(2.4) -- a status CheckConstraint
# (pending/extracted/failed, matching sources.type's/jobs.status's own
# precedent) plus two partial unique indexes modeling url/file_name's
# mutual exclusivity (a web document has a url, an upload has a file_name,
# a `database`-source document has neither -- see app/ingest/models.py's
# Document class for the full reasoning on why neither-set is valid, not a
# gap).
#
# `alembic revision --autogenerate` caught BOTH new partial indexes
# correctly (confirmed live: the console log explicitly reported "Detected
# added index" for each one) -- this is a NEW finding, not yet checked
# before now: 2.2.b only confirmed partial-index detection on a CREATE
# TABLE, not an ADD to an already-existing table. It did NOT detect the
# new CheckConstraint at all (an empty diff for it) -- consistent with
# 1.4.d's/2.3.e's own prior finding that autogenerate misses
# CheckConstraint changes on an ALTER to an existing table, now confirmed
# to extend to a brand-new CheckConstraint being added, not just an
# existing one's condition changing. The index calls below are
# autogenerate's own unedited output; the CheckConstraint calls are
# hand-written.


def upgrade() -> None:
    """Upgrade schema."""
    op.create_check_constraint(
        "ck_documents_status",
        "documents",
        "status IN ('pending', 'extracted', 'failed')",
    )
    op.create_index(
        "ix_documents_source_id_file_name_unique",
        "documents",
        ["source_id", "file_name"],
        unique=True,
        postgresql_where=sa.text("file_name IS NOT NULL"),
    )
    op.create_index(
        "ix_documents_source_id_url_unique",
        "documents",
        ["source_id", "url"],
        unique=True,
        postgresql_where=sa.text("url IS NOT NULL"),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(
        "ix_documents_source_id_url_unique",
        table_name="documents",
        postgresql_where=sa.text("url IS NOT NULL"),
    )
    op.drop_index(
        "ix_documents_source_id_file_name_unique",
        table_name="documents",
        postgresql_where=sa.text("file_name IS NOT NULL"),
    )
    op.drop_constraint("ck_documents_status", "documents", type_="check")
