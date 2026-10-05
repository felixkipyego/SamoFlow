"""restore file and meta_tag to verified_domains method check constraint

Revision ID: 77707a3b5369
Revises: 610897dd7071
Create Date: 2026-10-05 13:48:37.465187

"""
from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "77707a3b5369"
down_revision: str | Sequence[str] | None = "610897dd7071"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Task 2.3.e: schema-only restoration of the Step-2.2-deferred `file`/
# `meta_tag` verification methods, now that Step 2.3's own SSRF guard
# exists for them to reuse (PROJECT_SPEC.md's Open marker). The actual
# verification LOGIC for either method (the fetch itself, well-known-path/
# meta-tag parsing) is NOT built by this migration or anywhere else yet --
# see PROJECT_SPEC.md's own Open markers for that still-unowned work.
#
# `alembic revision --autogenerate` produced an EMPTY migration here
# (confirmed live, not assumed from 1.4.d's own prior finding alone,
# though it predicted exactly this): autogenerate detects a CheckConstraint
# as part of a brand-new CREATE TABLE, but does NOT detect a changed
# CheckConstraint condition on an ALTER to an already-existing table --
# this migration's own upgrade()/downgrade() bodies are hand-written.


def upgrade() -> None:
    """Upgrade schema."""
    op.drop_constraint(
        "ck_verified_domains_method", "verified_domains", type_="check"
    )
    op.create_check_constraint(
        "ck_verified_domains_method",
        "verified_domains",
        "method IN ('dns', 'file', 'meta_tag')",
    )


def downgrade() -> None:
    """Downgrade schema."""
    # No existing row can have method='file'/'meta_tag' to violate the
    # stricter constraint being restored here: no application code
    # anywhere writes either value yet (the verification logic for them
    # was never built -- see this migration's own header comment), so
    # there is no data-migration concern to guard against, not merely an
    # unhandled one.
    op.drop_constraint(
        "ck_verified_domains_method", "verified_domains", type_="check"
    )
    op.create_check_constraint(
        "ck_verified_domains_method", "verified_domains", "method IN ('dns')"
    )
