# backend/app/tenancy/models.py
# Task 1.2.b: ORM models for the "Identity, visitors and threads" tables
# (docs/SPEC.md §4.1) -- tenants, site_keys, visitors, conversations. No
# repository/query logic here (that's 1.2.d); this is schema only.
#
# UUID primary keys throughout (PROJECT_SPEC.md decision): generated
# server-side via Postgres's gen_random_uuid(), confirmed by hand to work
# with zero extensions on the pinned postgres:18.2-trixie image (moved into
# Postgres core in PG13+; pg_extension shows only plpgsql, no pgcrypto
# needed). Server-side generation centralizes id creation at the DB layer
# regardless of which process writes a row.
#
# Every FK to tenants.id has ondelete="CASCADE" (PROJECT_SPEC.md decision,
# matching docs/SPEC.md §13's eventual cascading tenant deletion) and an
# explicit index=True: confirmed by hand against the pinned Postgres image
# that a bare `REFERENCES` FK gets no automatic index (only the referenced
# table's own primary key does) -- unlike MySQL/InnoDB, Postgres never
# auto-indexes a foreign key column, and every tenant-scoped query filters
# on tenant_id, so leaving it unindexed would be a real, silent perf/DoS
# surface as data grows.
import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, String, text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.db import Base

_UUID_PK = dict(primary_key=True, server_default=text("gen_random_uuid()"))


class Tenant(Base):
    __tablename__ = "tenants"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), **_UUID_PK)
    name: Mapped[str] = mapped_column(String, nullable=False)
    # ASSUMPTION: no fixed status vocabulary for tenants is given anywhere in
    # docs/SPEC.md (unlike site_keys.status, whose three values §4.1 names
    # explicitly) -- left as a plain string with no CHECK constraint. The
    # first task that actually writes tenant status values (suspend a site,
    # §12 platform admin, likely Step 6.5) should confirm the real set and
    # add a constraint then if it turns out to be fixed.
    status: Mapped[str] = mapped_column(String, nullable=False)
    # Nullable: docs/SPEC.md §5.4 "Plans are assigned manually in an admin
    # screen" implies a tenant can exist before a plan is assigned.
    plan_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("plans.id"), nullable=True
    )
    config: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )


class SiteKey(Base):
    __tablename__ = "site_keys"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), **_UUID_PK)
    # The pk_live_... secret token (docs/SPEC.md §4.1). NOTE: generating that
    # format is not this task's job -- tracked as an open marker in
    # PROJECT_SPEC.md, owner not yet scheduled.
    key: Mapped[str] = mapped_column(String, unique=True, index=True, nullable=False)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    allowed_origins: Mapped[list[str]] = mapped_column(
        ARRAY(String), nullable=False, server_default=text("'{}'")
    )
    environment: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False)

    __table_args__ = (
        # docs/SPEC.md §4.1 names this exact, closed 3-value set -- unlike
        # tenants.status above, a CHECK constraint is warranted here.
        CheckConstraint("status IN ('draft', 'live', 'suspended')", name="ck_site_keys_status"),
    )


class Visitor(Base):
    __tablename__ = "visitors"

    vid: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), **_UUID_PK)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # No ondelete here (default NO ACTION): PROJECT_SPEC.md's cascade
    # decision covers tenants -> {site_keys, visitors, conversations}
    # directly, not this secondary site_keys -> visitors edge, and no
    # site-key-deletion feature exists yet to require one.
    site_key_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("site_keys.id"), nullable=False, index=True
    )
    secret_hash: Mapped[str] = mapped_column(String, nullable=False)
    # Server-side default (matches the UUID PK choice's own rationale):
    # first_seen_at is set once, by the database, at row creation -- no
    # application clock/timezone handling needed to get it right.
    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    # Same server-side default at creation; updating it on later visits is
    # 1.2.d/1.2.e's job (repository/session-endpoint logic), not schema.
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    # Reserved, unused per docs/SPEC.md §4.1/§19 (logged-in visitors are a
    # later phase).
    external_user_id: Mapped[str | None] = mapped_column(String, nullable=True)


class Conversation(Base):
    __tablename__ = "conversations"

    cid: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), **_UUID_PK)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # No ondelete here, same reasoning as visitors.site_key_id above.
    vid: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("visitors.vid"), nullable=False, index=True
    )
    title: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    last_message_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
