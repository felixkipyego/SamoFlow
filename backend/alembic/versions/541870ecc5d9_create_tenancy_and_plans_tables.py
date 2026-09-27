"""create tenancy and plans tables

Revision ID: 541870ecc5d9
Revises:
Create Date: 2026-09-27 08:16:34.046653

"""
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "541870ecc5d9"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "plans",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column(
            "limits",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "tenants",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("plan_id", sa.UUID(), nullable=True),
        sa.Column(
            "config",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["plan_id"], ["plans.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "site_keys",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("key", sa.String(), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column(
            "allowed_origins",
            postgresql.ARRAY(sa.String()),
            server_default=sa.text("'{}'"),
            nullable=False,
        ),
        sa.Column("environment", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.CheckConstraint("status IN ('draft', 'live', 'suspended')", name="ck_site_keys_status"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_site_keys_key"), "site_keys", ["key"], unique=True)
    op.create_index(op.f("ix_site_keys_tenant_id"), "site_keys", ["tenant_id"], unique=False)
    op.create_table(
        "visitors",
        sa.Column("vid", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("site_key_id", sa.UUID(), nullable=False),
        sa.Column("secret_hash", sa.String(), nullable=False),
        sa.Column(
            "first_seen_at",
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
        sa.Column("external_user_id", sa.String(), nullable=True),
        sa.ForeignKeyConstraint(["site_key_id"], ["site_keys.id"]),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("vid"),
    )
    op.create_index(op.f("ix_visitors_site_key_id"), "visitors", ["site_key_id"], unique=False)
    op.create_index(op.f("ix_visitors_tenant_id"), "visitors", ["tenant_id"], unique=False)
    op.create_table(
        "conversations",
        sa.Column("cid", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("vid", sa.UUID(), nullable=False),
        sa.Column("title", sa.String(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "last_message_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["vid"], ["visitors.vid"]),
        sa.PrimaryKeyConstraint("cid"),
    )
    op.create_index(
        op.f("ix_conversations_tenant_id"), "conversations", ["tenant_id"], unique=False
    )
    op.create_index(op.f("ix_conversations_vid"), "conversations", ["vid"], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f("ix_conversations_vid"), table_name="conversations")
    op.drop_index(op.f("ix_conversations_tenant_id"), table_name="conversations")
    op.drop_table("conversations")
    op.drop_index(op.f("ix_visitors_tenant_id"), table_name="visitors")
    op.drop_index(op.f("ix_visitors_site_key_id"), table_name="visitors")
    op.drop_table("visitors")
    op.drop_index(op.f("ix_site_keys_tenant_id"), table_name="site_keys")
    op.drop_index(op.f("ix_site_keys_key"), table_name="site_keys")
    op.drop_table("site_keys")
    op.drop_table("tenants")
    op.drop_table("plans")
