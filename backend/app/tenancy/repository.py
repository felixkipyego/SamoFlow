# backend/app/tenancy/repository.py
# Task 1.2.d: the tenant-scoped repository core. Engineering rule (§21,
# PROJECT_SPEC.md §3): "All tenant-scoped database access goes through the
# tenant-scoped repository helpers." This is the enforcement mechanism, with
# two minimal concrete methods proving the pattern works against real
# models -- a full CRUD surface is 1.2.e's job, not this one.
#
# Immutability: @dataclass(frozen=True), the standard library mechanism for
# "cannot be reassigned after construction" (chosen over a hand-rolled
# __setattr__ override, per rule 11 -- use a well-known mechanism instead of
# a custom scheme). Any attempt to set an attribute after __init__ raises
# dataclasses.FrozenInstanceError (a subclass of AttributeError), regardless
# of the field's name -- so tenant_id/session are plain, public field names
# (matching a normal constructor call, TenantScopedRepository(tenant_id=...,
# session=...)) rather than underscore-prefixed: the enforcement comes from
# frozen=True, not from hiding the name. tenant_id has no default, so
# omitting it raises Python's own TypeError; __post_init__ additionally
# rejects an explicit tenant_id=None, since a required dataclass field only
# guarantees the argument was *passed*, not that it is a real value.
import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.tenancy.models import Conversation, SiteKey


@dataclass(frozen=True)
class TenantScopedRepository:
    tenant_id: uuid.UUID
    session: AsyncSession

    def __post_init__(self) -> None:
        if self.tenant_id is None:
            raise ValueError("TenantScopedRepository requires a tenant_id, got None")

    async def get_conversation_by_id(self, cid: uuid.UUID) -> Conversation | None:
        # The tenant_id filter is added here, unconditionally, alongside the
        # caller's own id -- never accepted as a parameter, never optional.
        # A cid belonging to a different tenant matches no row and returns
        # None, exactly like a cid that doesn't exist at all.
        result = await self.session.execute(
            select(Conversation).where(
                Conversation.cid == cid,
                Conversation.tenant_id == self.tenant_id,
            )
        )
        return result.scalar_one_or_none()

    async def list_site_keys(self) -> list[SiteKey]:
        # No arguments beyond self: tenant scoping is automatic, not a
        # caller-supplied filter.
        result = await self.session.execute(
            select(SiteKey).where(SiteKey.tenant_id == self.tenant_id)
        )
        return list(result.scalars().all())
