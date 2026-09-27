# backend/app/tenancy/repository.py
# Task 1.2.d: the tenant-scoped repository core. Engineering rule (§21,
# PROJECT_SPEC.md §3): "All tenant-scoped database access goes through the
# tenant-scoped repository helpers." Task 1.2.e extends it with the minimal
# CRUD/lookup surface 1.4 (session endpoint) and later steps will need --
# still no wiring to a real API endpoint, that's 1.4's job.
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
#
# create_tenant() is a deliberately separate, module-level function, never a
# method on TenantScopedRepository: a brand-new tenant has no tenant_id yet
# to scope by, so it structurally cannot fit this class's own invariant
# (construction always requires a real tenant_id). Keeping it as a plain
# function outside the class makes "this is the one unscoped operation, and
# here is why" visible at a glance, rather than a special-cased method that
# would need to explain away the class's central guarantee.
#
# create_visitor()/create_conversation() verify that the site_key_id/vid they
# are given actually belongs to this repository's own tenant before creating
# the row, via the shared _verify_owned() helper (duplication check after
# 1.2.d/e/f). This is not redundant with the FK constraints already on those
# columns (visitors.site_key_id -> site_keys.id, conversations.vid ->
# visitors.vid, from 1.2.c's migration): a foreign key only proves the
# referenced row *exists*, not that it belongs to the same tenant -- nothing
# else in this codebase yet checks that (1.4's session endpoint, the only
# other place that would touch this, doesn't exist yet), so skipping the
# check here would silently accept a tenant-A visitor pointing at tenant B's
# site_key, or a tenant-A conversation pointing at tenant B's visitor.
# _verify_owned() deliberately treats "doesn't exist at all" and "exists but
# belongs to another tenant" identically (both raise the same ValueError,
# with the same message shape) -- distinguishing them would let a caller
# learn "that id is real, just not yours", an enumeration oracle across
# tenants (docs/SPEC.md's own tenant-isolation philosophy, and the same
# choice get_conversation_by_id()/get_visitor_by_id() already make for reads).
import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import InstrumentedAttribute

from app.tenancy.models import Conversation, SiteKey, Tenant, Visitor


async def create_tenant(session: AsyncSession, name: str, status: str) -> Tenant:
    tenant = Tenant(name=name, status=status)
    session.add(tenant)
    await session.flush()
    return tenant


@dataclass(frozen=True)
class TenantScopedRepository:
    tenant_id: uuid.UUID
    session: AsyncSession

    def __post_init__(self) -> None:
        if self.tenant_id is None:
            raise ValueError("TenantScopedRepository requires a tenant_id, got None")

    async def _verify_owned(
        self,
        id_column: InstrumentedAttribute,
        tenant_id_column: InstrumentedAttribute,
        value: uuid.UUID,
    ) -> bool:
        # Shared by create_visitor/create_conversation (duplication check
        # after 1.2.d/e/f): "does this id belong to self.tenant_id" is one
        # query shape regardless of which table it's checked against.
        # Deliberately does not distinguish "value doesn't exist at all"
        # from "exists but belongs to another tenant" -- both produce False
        # here, and both must raise the identical error at the call site,
        # since telling them apart would let a caller learn "that id is
        # real, just not yours" (an enumeration oracle).
        result = await self.session.execute(
            select(id_column).where(id_column == value, tenant_id_column == self.tenant_id)
        )
        return result.scalar_one_or_none() is not None

    async def get_tenant(self) -> Tenant:
        # The one case where "tenant_id" is the whole lookup -- but it is
        # this repository's own bound tenant_id, never a caller-supplied
        # one. scalar_one() (not _or_none): a TenantScopedRepository is only
        # ever constructed with a real tenant's id, so a missing row here is
        # a real error, not a normal "not found" case a caller should expect.
        result = await self.session.execute(select(Tenant).where(Tenant.id == self.tenant_id))
        return result.scalar_one()

    async def list_site_keys(self) -> list[SiteKey]:
        # No arguments beyond self: tenant scoping is automatic, not a
        # caller-supplied filter.
        result = await self.session.execute(
            select(SiteKey).where(SiteKey.tenant_id == self.tenant_id)
        )
        return list(result.scalars().all())

    async def create_site_key(
        self, key: str, allowed_origins: list[str], environment: str
    ) -> SiteKey:
        # New keys always start in "draft" (docs/SPEC.md §4.1/§6.3: a site
        # key moves from draft to live only through a server-side check,
        # done elsewhere -- not this method's job).
        site_key = SiteKey(
            tenant_id=self.tenant_id,
            key=key,
            allowed_origins=allowed_origins,
            environment=environment,
            status="draft",
        )
        self.session.add(site_key)
        await self.session.flush()
        return site_key

    async def get_visitor_by_id(self, vid: uuid.UUID) -> Visitor | None:
        # Same automatic-filter pattern as get_conversation_by_id below.
        result = await self.session.execute(
            select(Visitor).where(
                Visitor.vid == vid,
                Visitor.tenant_id == self.tenant_id,
            )
        )
        return result.scalar_one_or_none()

    async def create_visitor(self, site_key_id: uuid.UUID, secret_hash: str) -> Visitor:
        if not await self._verify_owned(SiteKey.id, SiteKey.tenant_id, site_key_id):
            raise ValueError(f"site_key {site_key_id} does not belong to tenant {self.tenant_id}")
        visitor = Visitor(
            tenant_id=self.tenant_id, site_key_id=site_key_id, secret_hash=secret_hash
        )
        self.session.add(visitor)
        await self.session.flush()
        return visitor

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

    async def create_conversation(self, vid: uuid.UUID, title: str | None = None) -> Conversation:
        if not await self._verify_owned(Visitor.vid, Visitor.tenant_id, vid):
            raise ValueError(f"visitor {vid} does not belong to tenant {self.tenant_id}")
        conversation = Conversation(tenant_id=self.tenant_id, vid=vid, title=title)
        self.session.add(conversation)
        await self.session.flush()
        return conversation
