# backend/app/dashboard/repository.py
# Step 6.1: Membership CRUD. User and session persistence are owned by
# fastapi-users' own adapters instead (SQLAlchemyUserDatabase, and app/
# dashboard/auth_backend.py's own SQLAlchemyAccessTokenDatabase-backed
# HashedDatabaseStrategy) -- see that module's own header comment for why.
# Memberships are entirely this project's own concept, with no fastapi-
# users equivalent, so they stay a plain, module-level function set here,
# matching app.tenancy.repository's own "not every table needs a class"
# precedent (create_tenant()/get_site_key_by_key() are both plain
# functions there too, for the identical reason: no natural scoping
# object exists yet at the point they are called).
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.dashboard.models import Membership


async def create_membership(
    session: AsyncSession, user_id: uuid.UUID, tenant_id: uuid.UUID, role: str = "owner"
) -> Membership:
    membership = Membership(user_id=user_id, tenant_id=tenant_id, role=role)
    session.add(membership)
    await session.flush()
    return membership


async def get_membership_for_user(session: AsyncSession, user_id: uuid.UUID) -> Membership | None:
    # v1 is single-owner only (no team invites yet, §11.3 kickoff decision
    # #3) -- a user has at most one real membership in practice today, so
    # "the first one, oldest first" is a deterministic, well-defined
    # choice even though the schema itself does not forbid more than one.
    # Whoever builds real multi-tenant-per-user support later (a tenant
    # switcher) replaces this with a caller-supplied tenant_id, not a
    # second lookup function.
    result = await session.execute(
        select(Membership)
        .where(Membership.user_id == user_id)
        .order_by(Membership.created_at.asc())
        .limit(1)
    )
    return result.scalar_one_or_none()
