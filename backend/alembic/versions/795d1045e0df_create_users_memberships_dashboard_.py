"""create user, memberships, dashboard_sessions tables

Revision ID: 795d1045e0df
Revises: 8837877f0360
Create Date: 2026-10-15 00:00:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "795d1045e0df"
down_revision: str | Sequence[str] | None = "8837877f0360"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    # Step 6.1 [SECURITY]: real dashboard login (docs/SPEC.md §12/§15),
    # deferred from Task 1.2's own original "database, migrations and
    # tenant-scoped access" task to here per Felix's own direct
    # correction to PROJECT_SPEC.md §11.2 that kicked off this step.
    #
    # `user` (singular, fastapi-users' own convention, kept as-is -- see
    # app/dashboard/models.py's own header comment for why renaming it
    # would also require overriding dashboard_sessions.user_id's FK
    # target below): the real user table (docs/SPEC.md §12's own "use a
    # maintained auth library rather than hand-rolling", Felix's own
    # 2026-10-15 correction naming fastapi-users). `hashed_password`
    # holds a real argon2id hash (app/dashboard/passwords.py), never
    # anything weaker.
    op.create_table(
        "user",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("hashed_password", sa.String(length=1024), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("is_superuser", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("is_verified", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_user_email"), "user", ["email"], unique=True)

    # v1 is single-owner only (Felix's §11.3 kickoff decision #3: "a
    # single-owner login now, not the full team-invite system") -- the
    # CHECK constraint below keeps that a real, enforced invariant rather
    # than a convention the application alone must remember, matching
    # site_keys.status's/tenants.status's own "small, closed vocabulary ->
    # CHECK constraint" precedent. Widening to real team roles later (owner
    # vs. member) is an additive migration on this same column, not a
    # redesign.
    op.create_table(
        "memberships",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("role", sa.String(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("role IN ('owner')", name="ck_memberships_role"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_memberships_tenant_id"), "memberships", ["tenant_id"], unique=False)
    op.create_index(op.f("ix_memberships_user_id"), "memberships", ["user_id"], unique=False)
    op.create_index(
        "ix_memberships_user_id_tenant_id", "memberships", ["user_id", "tenant_id"], unique=True
    )

    # `token` holds a SHA-256 hex digest (64 chars), never the raw session
    # token -- app/dashboard/auth_backend.py's own HashedDatabaseStrategy
    # extends fastapi-users' own DatabaseStrategy (which stores the raw
    # value in a 43-char column by default) specifically to avoid that.
    # `last_seen_at`: the idle-expiry anchor this task's own "revocable,
    # with idle and absolute expiry" requirement needs -- the library's
    # own access-token table has no equivalent column.
    op.create_table(
        "dashboard_sessions",
        sa.Column("token", sa.String(length=64), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "last_seen_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("token"),
    )
    op.create_index(
        op.f("ix_dashboard_sessions_user_id"), "dashboard_sessions", ["user_id"], unique=False
    )
    op.create_index(
        op.f("ix_dashboard_sessions_created_at"), "dashboard_sessions", ["created_at"], unique=False
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f("ix_dashboard_sessions_created_at"), table_name="dashboard_sessions")
    op.drop_index(op.f("ix_dashboard_sessions_user_id"), table_name="dashboard_sessions")
    op.drop_table("dashboard_sessions")
    op.drop_index("ix_memberships_user_id_tenant_id", table_name="memberships")
    op.drop_index(op.f("ix_memberships_user_id"), table_name="memberships")
    op.drop_index(op.f("ix_memberships_tenant_id"), table_name="memberships")
    op.drop_table("memberships")
    op.drop_index(op.f("ix_user_email"), table_name="user")
    op.drop_table("user")
