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

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import InstrumentedAttribute
from sqlalchemy.sql import func

from app.auth.secrets import verify_visitor_secret
from app.tenancy.models import Conversation, SiteKey, Tenant, Visitor


async def create_tenant(session: AsyncSession, name: str, status: str) -> Tenant:
    tenant = Tenant(name=name, status=status)
    session.add(tenant)
    await session.flush()
    return tenant


async def get_site_key_by_key(session: AsyncSession, key: str) -> SiteKey | None:
    # Task 1.4.e: module-level, deliberately never a TenantScopedRepository
    # method -- same structural reasoning as create_tenant() above. A site
    # key is looked up by its own secret token *before* any tenant is known
    # (docs/SPEC.md §4.2: the widget sends {site_key, visitor_secret} at the
    # very start of the session flow); a repository requires a real
    # tenant_id to construct, which doesn't exist yet at this point. One
    # SELECT, no side effects: the caller (1.4.g) decides what a missing or
    # suspended key means, this function only reports which row (if any)
    # the key names.
    result = await session.execute(select(SiteKey).where(SiteKey.key == key))
    return result.scalar_one_or_none()


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

    async def get_visitor_by_secret(self, secret: str, site_key_id: uuid.UUID) -> Visitor | None:
        # Task 1.4.e. Scoped to BOTH self.tenant_id AND the given
        # site_key_id (decided at the Step 1.4 breakdown, PROJECT_SPEC.md's
        # own 1.4.e task-list row: "scoped both to the repository's own
        # tenant_id and to the given site_key_id") -- matching 1.4.d's
        # index, which is scoped per site key for the same reason: a
        # visitor secret is only ever meaningful within the one site key
        # that issued it (docs/SPEC.md §4.2's localStorage key is
        # "aw:{site_key}"), never across a tenant's other, unrelated sites.
        #
        # Candidates are read scoped, then each is checked in Python with
        # verify_visitor_secret() (1.4.c's constant-time comparison) --
        # never a SQL "secret_hash = <computed hash>" equality filter, which
        # would bypass 1.4.c's comparison entirely and let the query
        # itself, not this function, decide what "matches" means. A wrong
        # secret for a real row and a secret matching no row at all both
        # fall out of the same empty-match path below and return None --
        # nothing here distinguishes them, no exception, no log line
        # containing the attempted secret.
        result = await self.session.execute(
            select(Visitor).where(
                Visitor.tenant_id == self.tenant_id,
                Visitor.site_key_id == site_key_id,
            )
        )
        for candidate in result.scalars().all():
            if verify_visitor_secret(secret, candidate.secret_hash):
                # "on match" (the same task-list row): bundled here, not a
                # separate step the caller must remember to invoke -- every
                # successful verification is a real visit (docs/SPEC.md
                # §4.2's "Return visit: verify the secret ... " has no case
                # where verifying but not recording the visit is correct),
                # and there is exactly one call site planned (1.4.g), so
                # nothing is lost by not exposing this as two steps.
                await self.touch_visitor_last_seen(candidate.vid)
                return candidate
        return None

    async def touch_visitor_last_seen(self, vid: uuid.UUID) -> None:
        # Task 1.4.e. Unlike create_visitor/create_conversation's
        # _verify_owned()-then-raise pattern (guarding a new row's FK
        # reference to a caller-supplied foreign id), this is a bounded
        # UPDATE that scopes itself by tenant_id in its own WHERE clause --
        # the same "automatic filter" shape get_visitor_by_id/
        # get_conversation_by_id already use for reads, applied to a write.
        # A vid that doesn't belong to self.tenant_id (or doesn't exist)
        # matches zero rows and this is a silent no-op, not an error: the
        # one call site (get_visitor_by_secret above) only ever passes a
        # vid it just read from a row already scoped to this same
        # tenant_id, so there is no real "caller made a mistake" case here
        # to raise about -- and a housekeeping/heartbeat-style update
        # quietly doing nothing on an out-of-scope id matches every other
        # read method's own behavior on this class, rather than
        # introducing a third response shape (reads return None, creates
        # raise ValueError) for what is not a new-row FK integrity check.
        await self.session.execute(
            update(Visitor)
            .where(Visitor.vid == vid, Visitor.tenant_id == self.tenant_id)
            .values(last_seen_at=func.now())
        )
        await self.session.flush()
