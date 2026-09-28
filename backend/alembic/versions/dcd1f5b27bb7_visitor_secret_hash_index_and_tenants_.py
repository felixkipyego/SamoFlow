"""visitor secret-hash index and tenants status check

Revision ID: dcd1f5b27bb7
Revises: 541870ecc5d9
Create Date: 2026-09-28 23:54:49.978684

"""
from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "dcd1f5b27bb7"
down_revision: str | Sequence[str] | None = "541870ecc5d9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    # Autogenerate only detected the index below -- Alembic's autogenerate
    # does not compare CHECK constraints (a documented limitation, not a
    # bug), so this one was added by hand, the same way ck_site_keys_status
    # itself was hand-written directly into the very first migration's
    # op.create_table() call (the one case autogenerate never has to diff
    # a CHECK constraint against, since the table doesn't exist yet there).
    op.create_check_constraint(
        "ck_tenants_status", "tenants", "status IN ('active', 'suspended')"
    )
    # Supports 1.4.e's lookup ("find a visitor by their secret within one
    # site key"). Scoped to (site_key_id, secret_hash) together, not a
    # global unique index on secret_hash alone -- see
    # app/tenancy/models.py's Visitor.__table_args__ comment for why a
    # cross-site-key hash collision is not what this index protects
    # against.
    op.create_index(
        "ix_visitors_site_key_id_secret_hash",
        "visitors",
        ["site_key_id", "secret_hash"],
        unique=True,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_visitors_site_key_id_secret_hash", table_name="visitors")
    op.drop_constraint("ck_tenants_status", "tenants", type_="check")
