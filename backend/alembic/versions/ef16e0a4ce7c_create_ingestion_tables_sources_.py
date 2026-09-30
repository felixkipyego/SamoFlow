"""create ingestion tables: sources, documents, db_connections, jobs

Revision ID: ef16e0a4ce7c
Revises: dcd1f5b27bb7
Create Date: 2026-09-30 15:02:39.326513

"""
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "ef16e0a4ce7c"
down_revision: str | Sequence[str] | None = "dcd1f5b27bb7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    # Task 2.1.b: db_connections.encrypted_credentials needs pgcrypto's
    # pgp_sym_encrypt/pgp_sym_decrypt (app/ingest/repository.py). This is
    # the real deployment path that must enable it -- 2.1.a's own test
    # fixture (backend/tests/conftest.py's reset_test_database) already
    # does the same statement for tests, but that was only ever a
    # workaround for a database that has no migration history at all; a
    # real deployment goes through this migration, not that fixture.
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")

    op.create_table(
        "db_connections",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("host", sa.String(), nullable=False),
        sa.Column(
            "allowlisted_tables",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "row_templates",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("encrypted_credentials", sa.LargeBinary(), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_db_connections_tenant_id"), "db_connections", ["tenant_id"], unique=False
    )
    op.create_table(
        "sources",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("type", sa.String(), nullable=False),
        sa.Column(
            "config",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("refresh_interval", sa.String(), nullable=False),
        sa.Column("enabled", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("last_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "type IN ('urls', 'crawl', 'upload', 'database')", name="ck_sources_type"
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_sources_tenant_id"), "sources", ["tenant_id"], unique=False)
    op.create_table(
        "documents",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("source_id", sa.UUID(), nullable=False),
        sa.Column("url", sa.String(), nullable=True),
        sa.Column("file_name", sa.String(), nullable=True),
        sa.Column("title", sa.String(), nullable=True),
        sa.Column("content_hash", sa.String(), nullable=True),
        sa.Column("etag", sa.String(), nullable=True),
        sa.Column(
            "last_seen_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("status", sa.String(), nullable=False),
        sa.ForeignKeyConstraint(["source_id"], ["sources.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_documents_source_id"), "documents", ["source_id"], unique=False)
    op.create_index(op.f("ix_documents_tenant_id"), "documents", ["tenant_id"], unique=False)
    op.create_table(
        "jobs",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("source_id", sa.UUID(), nullable=True),
        sa.Column("job_type", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("attempts", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("max_attempts", sa.Integer(), server_default=sa.text("5"), nullable=False),
        sa.Column(
            "next_run_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "payload",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("error", sa.String(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'running', 'succeeded', 'failed')", name="ck_jobs_status"
        ),
        sa.ForeignKeyConstraint(["source_id"], ["sources.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_jobs_next_run_at"), "jobs", ["next_run_at"], unique=False)
    op.create_index(op.f("ix_jobs_source_id"), "jobs", ["source_id"], unique=False)
    op.create_index(op.f("ix_jobs_tenant_id"), "jobs", ["tenant_id"], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f("ix_jobs_tenant_id"), table_name="jobs")
    op.drop_index(op.f("ix_jobs_source_id"), table_name="jobs")
    op.drop_index(op.f("ix_jobs_next_run_at"), table_name="jobs")
    op.drop_table("jobs")
    op.drop_index(op.f("ix_documents_tenant_id"), table_name="documents")
    op.drop_index(op.f("ix_documents_source_id"), table_name="documents")
    op.drop_table("documents")
    op.drop_index(op.f("ix_sources_tenant_id"), table_name="sources")
    op.drop_table("sources")
    op.drop_index(op.f("ix_db_connections_tenant_id"), table_name="db_connections")
    op.drop_table("db_connections")

    # Deliberately NOT dropping the pgcrypto extension: other things in
    # this database, present or future, could depend on it existing (it
    # is a database-wide capability, not something this migration alone
    # owns), and CREATE EXTENSION IF NOT EXISTS above is already safe to
    # run again on the next upgrade regardless. Dropping it here would
    # also break any other extension-dependent object created by anything
    # else in the meantime -- this migration only cleans up what it
    # itself created (the four tables), matching the same restraint
    # app/main.py's own lifespan hook uses for disposal (never tear down
    # more than what this exact code path is responsible for).
