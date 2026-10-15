# backend/app/dashboard/models.py
# Step 6.1 [SECURITY]: ORM models for dashboard login (docs/SPEC.md
# §12/§15) -- `user` (fastapi-users' own table, see below for why this one
# table is singular, not plural like the rest of this project's schema),
# `memberships`, `dashboard_sessions`. Deferred here from Task 1.2 per
# Felix's own direct correction to PROJECT_SPEC.md §11.2 that kicked off
# this step: the dashboard track is not frontend only, and this track owns
# every Alembic migration during the parallel period.
#
# User and DashboardSession are built on fastapi-users' own SQLAlchemy
# table mixins (docs/SPEC.md §12: "use a maintained auth library rather
# than hand-rolling"; Felix's own 2026-10-15 correction naming fastapi-
# users specifically) -- see app/dashboard/auth_backend.py's own header
# comment for the full library-adoption reasoning.
import uuid
from datetime import datetime

from fastapi_users.db import SQLAlchemyBaseUserTableUUID
from fastapi_users_db_sqlalchemy.access_token import SQLAlchemyBaseAccessTokenTableUUID
from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import TIMESTAMP_NOW, UUID_PK, Base


class User(SQLAlchemyBaseUserTableUUID, Base):
    # __tablename__ stays the mixin's own default, "user" (singular) --
    # confirmed by reading fastapi_users_db_sqlalchemy's own source
    # before deciding this: SQLAlchemyBaseAccessTokenTableUUID's own
    # `user_id` column hard-codes `ForeignKey("user.id", ...)` as a
    # literal string, not computed from this class's own __tablename__,
    # so renaming this one table would also require overriding that FK
    # target on DashboardSession below. Keeping the library's own
    # default here avoids that extra override entirely -- the smallest
    # correct choice (rule 11), at the minor, deliberate cost of one
    # singular table name among this project's otherwise-plural ones.
    #
    # Columns `id`, `email`, `hashed_password`, `is_active`,
    # `is_superuser`, `is_verified` all come from the mixin. `is_active`
    # is a real, if unbuilt-UI-for, kill switch -- every dependency that
    # authenticates a dashboard user requires it (app/dashboard/
    # auth_backend.py's own `current_active_user`). `is_superuser` IS
    # this project's `platform_admin` flag (Felix's own 2026-10-15
    # correction: "Build the platform_admin role now... using fastapi-
    # users' own semantics rather than inventing a second, parallel
    # boolean -- `current_platform_admin` (same module) is the one real
    # gate on /api/v1/admin. `is_verified` is unused in v1: no email-
    # verification flow exists (Felix's §11.3 kickoff decision #3,
    # "single-owner login... not the full team-invite/email-verification
    # system"), and no dependency here ever checks it, so it staying
    # False for every user has no effect today.
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), **TIMESTAMP_NOW)


class Membership(Base):
    __tablename__ = "memberships"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), **UUID_PK)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("user.id", ondelete="CASCADE"), nullable=False, index=True
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # v1 is single-owner only (Felix's §11.3 kickoff decision #3) -- the
    # closed, one-value vocabulary is a real, enforced CHECK constraint,
    # matching site_keys.status's/tenants.status's own precedent, not
    # merely an application-level convention. Widening to real roles
    # (owner/member) later is an additive migration on this column.
    role: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), **TIMESTAMP_NOW)

    __table_args__ = (
        CheckConstraint("role IN ('owner')", name="ck_memberships_role"),
        # One user can belong to one tenant at most once -- the real
        # per-(user, tenant) uniqueness invariant; a user having
        # memberships in several DIFFERENT tenants is a real, supported
        # shape (not used by any v1 screen yet, but not precluded by this
        # index either).
        Index("ix_memberships_user_id_tenant_id", "user_id", "tenant_id", unique=True),
    )


class DashboardSession(SQLAlchemyBaseAccessTokenTableUUID, Base):
    __tablename__ = "dashboard_sessions"

    # Overridden from the mixin's own `ForeignKey("user.id",
    # ondelete="cascade")` (lowercase) -- functionally identical in
    # Postgres (CASCADE is case-insensitive at the DB level), but every
    # other FK in this project spells it uppercase, and test_models.py's
    # own CASCADE_FK_COLUMNS check compares the literal Python-side
    # string. Re-declared here only to match that casing convention, not
    # because the library's own lowercase form is wrong.
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("user.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # Overridden from the mixin's own String(length=43) (sized for a raw
    # secrets.token_urlsafe() value, 32 bytes base64url-encoded): this
    # project never stores a raw session token at rest, matching
    # visitors.secret_hash's own established convention (app/tenancy/
    # models.py) -- `token` here holds a SHA-256 hex digest (64 chars)
    # instead, written and read only by app/dashboard/auth_backend.py's
    # own HashedDatabaseStrategy, never the library's own default
    # DatabaseStrategy (which stores the raw token -- confirmed by
    # reading its source before deciding to override this).
    token: Mapped[str] = mapped_column(String(length=64), primary_key=True)
    # Idle-expiry anchor (this task's own "revocable, with idle and
    # absolute expiry" requirement -- the library's own DatabaseStrategy
    # only ever implements absolute expiry, via `created_at` + `max_age`
    # at read time). Updated on every authenticated request that finds
    # this session still valid.
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), **TIMESTAMP_NOW)
