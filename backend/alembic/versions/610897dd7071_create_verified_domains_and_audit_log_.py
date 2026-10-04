"""create verified_domains and audit_log tables

Revision ID: 610897dd7071
Revises: ef16e0a4ce7c
Create Date: 2026-10-04 10:51:28.655020

"""
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "610897dd7071"
down_revision: str | Sequence[str] | None = "ef16e0a4ce7c"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "verified_domains",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("domain", sa.String(), nullable=False),
        sa.Column("method", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("verification_token", sa.String(), nullable=False),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("method IN ('dns')", name="ck_verified_domains_method"),
        sa.CheckConstraint(
            "status IN ('pending', 'verified', 'revoked')", name="ck_verified_domains_status"
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_verified_domains_tenant_id"), "verified_domains", ["tenant_id"], unique=False
    )
    # Global uniqueness (PROJECT_SPEC.md's Step 2.2 breakdown, decision (e)):
    # autogenerate correctly detected this as a genuinely PARTIAL unique
    # index (postgresql_where survives the diff), confirmed by reading this
    # file before committing to it -- not assumed from the model alone. A
    # domain can be actively claimed (pending/verified) by exactly one
    # tenant at a time, across ALL tenants; a revoked domain frees it again.
    op.create_index(
        "ix_verified_domains_domain_active_unique",
        "verified_domains",
        ["domain"],
        unique=True,
        postgresql_where=sa.text("status != 'revoked'"),
    )
    op.create_table(
        "audit_log",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("actor", sa.String(), nullable=False),
        sa.Column("action", sa.String(), nullable=False),
        sa.Column("target_type", sa.String(), nullable=False),
        sa.Column("target_id", sa.UUID(), nullable=True),
        sa.Column(
            "details",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    # Task 2.2.b, PROJECT_SPEC.md's Step 2.2 breakdown decision (a): the
    # primary, DB-level enforcement of audit_log's append-only property.
    #
    # CURRENTLY INERT -- verified live, not assumed, before writing this
    # statement: "widgetplatform" (DATABASE_URL's own user, docker-compose.yml's
    # POSTGRES_USER) is not merely this table's owner, it is a full Postgres
    # SUPERUSER (confirmed via `\du` / pg_roles: rolsuper=t) -- the only role
    # that exists anywhere in this stack, shared by migrate/api/worker alike.
    # A superuser bypasses every permission check, including this REVOKE, by
    # definition; no GRANT/REVOKE/ALTER TABLE OWNER trick at the SQL level can
    # restrict it. Proven live: CREATE TABLE as widgetplatform, REVOKE
    # UPDATE, DELETE FROM widgetplatform, then UPDATE/DELETE both still
    # succeeded. See test_audit_log_update_delete_currently_succeeds_because_
    # widgetplatform_is_a_superuser (test_alembic.py) for the permanent,
    # automated proof of this exact behavior.
    #
    # This statement is written anyway, now, because it costs nothing and
    # becomes REAL enforcement automatically, with zero further migration
    # work, the moment the Phase-7-owned role-split marker (PROJECT_SPEC.md,
    # recorded at 1.1.i) lands and api/worker connect as a non-superuser
    # role instead of this one. Until then, 2.2.h's AST-based guard is the
    # ONLY real enforcement against UPDATE/DELETE on audit_log -- this is
    # the reverse of this step's own original decision (a), which assumed
    # the REVOKE would be primary; see PROJECT_SPEC.md's dated correction to
    # that entry for the full reasoning.
    op.execute("REVOKE UPDATE, DELETE ON audit_log FROM widgetplatform")


def downgrade() -> None:
    """Downgrade schema."""
    # No explicit un-REVOKE needed: dropping the table removes the
    # privilege grant/revocation along with it -- Postgres has no concept
    # of a privilege entry surviving its own object's DROP, so there is
    # nothing here to explicitly restore (matching ef16e0a4ce7c's own
    # restraint for pgcrypto -- never tear down more than this migration's
    # own downgrade is responsible for).
    op.drop_table("audit_log")
    op.drop_index("ix_verified_domains_domain_active_unique", table_name="verified_domains")
    op.drop_index(op.f("ix_verified_domains_tenant_id"), table_name="verified_domains")
    op.drop_table("verified_domains")
