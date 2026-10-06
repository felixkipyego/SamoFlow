"""add is_nearly_empty to documents

Revision ID: 5580e8a60e49
Revises: 3f3fbac167b7
Create Date: 2026-10-06 21:18:55.508782

"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "5580e8a60e49"
down_revision: str | Sequence[str] | None = "3f3fbac167b7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Task 2.6.c: a separate, orthogonal signal from `status` (see
# app/ingest/models.py's Document class for the full reasoning) --
# `alembic revision --autogenerate` caught this correctly (confirmed
# live: "Detected added column 'documents.is_nearly_empty'"), hand-fixed
# only for this project's own lint style (typing.Union/Sequence ->
# X | Y, double quotes, line length) per the already-known template gap
# (PROJECT_SPEC.md's own open marker about alembic/script.py.mako).


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "documents",
        sa.Column(
            "is_nearly_empty", sa.Boolean(), server_default=sa.text("false"), nullable=False
        ),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("documents", "is_nearly_empty")
