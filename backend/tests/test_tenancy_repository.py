# backend/tests/test_tenancy_repository.py
# Tasks 1.2.d/1.2.e: tests for the tenant-scoped repository core
# (backend/app/tenancy/repository.py).
#
# Three groups:
#   - structural tests (no database): the class cannot be constructed
#     without a tenant_id, rejects an explicit tenant_id=None, its fields
#     cannot be reassigned after construction (dataclasses'
#     FrozenInstanceError, not a hand-rolled scheme -- see repository.py's
#     own header comment for why), and create_tenant() is never a method on
#     the class (1.2.e).
#   - create_tenant()'s own test: it works standalone, with no tenant
#     scoping (there is no tenant_id yet for a brand-new tenant) (1.2.e).
#   - the security proof, against the real test database: two tenants (A
#     and B) are seeded with one site_key, one visitor and one conversation
#     each; a repository scoped to tenant A must return/create only tenant
#     A's rows, even when handed tenant B's real, valid ids (the
#     hostile-caller case) or an id that was never inserted at all (Task
#     1.2.f's duplication check: both must produce the identical outcome,
#     since distinguishing them would let a caller learn "that id is real,
#     just not yours" -- an enumeration oracle this repository exists to
#     prevent).
import dataclasses
import uuid

import pytest
import sqlalchemy as sa

from app.auth.secrets import generate_visitor_secret, hash_visitor_secret
from app.plans import models as plans_models  # noqa: F401 (registers "plans" on Base.metadata)
from app.tenancy.models import Conversation, SiteKey, Tenant, Visitor
from app.tenancy.repository import (
    SiteKeyAlreadyExistsError,
    TenantScopedRepository,
    create_tenant,
    get_site_key_by_key,
)
from tests.conftest import db_session


def test_repository_requires_a_tenant_id():
    with pytest.raises(TypeError):
        TenantScopedRepository(session=None)  # missing tenant_id


def test_repository_rejects_none_tenant_id():
    with pytest.raises(ValueError):
        TenantScopedRepository(tenant_id=None, session=None)


def test_repository_tenant_id_cannot_be_reassigned():
    # dataclasses.FrozenInstanceError (a subclass of AttributeError): the
    # class-level mechanism, not a naming convention, is what prevents this.
    repo = TenantScopedRepository(tenant_id=uuid.uuid4(), session=None)
    with pytest.raises(dataclasses.FrozenInstanceError):
        repo.tenant_id = uuid.uuid4()


def test_create_tenant_is_not_a_repository_method():
    # create_tenant() is a module-level function, deliberately never a
    # method on TenantScopedRepository (see repository.py's own header
    # comment for why: a brand-new tenant has no tenant_id yet to scope by).
    assert not hasattr(TenantScopedRepository, "create_tenant")


@pytest.fixture
async def _seeded_tenants(reset_test_database):
    # Schema reset (duplication check after 1.4.f/g/h: moved into the
    # shared reset_test_database fixture) via Base.metadata directly (not
    # the real Alembic migration): 1.2.c already proves the migration
    # produces this schema; this fixture's job is only to get a real
    # Postgres schema in place to prove the repository's tenant-isolation
    # behavior, so create_all is the simpler, standard choice here
    # (rule 11).

    # Real secrets/hashes (Task 1.4.e), not the earlier "hash-a"/"hash-b"
    # placeholders: get_visitor_by_secret's own tests need a real secret
    # that actually verifies against its stored hash via
    # verify_visitor_secret() (1.4.c), which an arbitrary literal string
    # cannot provide.
    visitor_a_secret = generate_visitor_secret()
    visitor_b_secret = generate_visitor_secret()

    ids = {
        "tenant_a": uuid.uuid4(),
        "tenant_b": uuid.uuid4(),
        "site_key_a": uuid.uuid4(),
        "site_key_b": uuid.uuid4(),
        "visitor_a": uuid.uuid4(),
        "visitor_b": uuid.uuid4(),
        "conversation_a": uuid.uuid4(),
        "conversation_b": uuid.uuid4(),
        "visitor_a_secret": visitor_a_secret,
        "visitor_b_secret": visitor_b_secret,
    }

    async with db_session() as session:
        session.add_all(
            [
                Tenant(id=ids["tenant_a"], name="Tenant A", status="active"),
                Tenant(id=ids["tenant_b"], name="Tenant B", status="active"),
            ]
        )
        await session.flush()
        session.add_all(
            [
                SiteKey(
                    id=ids["site_key_a"],
                    key="pk_live_tenant_a",
                    tenant_id=ids["tenant_a"],
                    environment="production",
                    status="draft",
                ),
                SiteKey(
                    id=ids["site_key_b"],
                    key="pk_live_tenant_b",
                    tenant_id=ids["tenant_b"],
                    environment="production",
                    status="draft",
                ),
            ]
        )
        session.add_all(
            [
                Visitor(
                    vid=ids["visitor_a"],
                    tenant_id=ids["tenant_a"],
                    site_key_id=ids["site_key_a"],
                    secret_hash=hash_visitor_secret(visitor_a_secret),
                ),
                Visitor(
                    vid=ids["visitor_b"],
                    tenant_id=ids["tenant_b"],
                    site_key_id=ids["site_key_b"],
                    secret_hash=hash_visitor_secret(visitor_b_secret),
                ),
            ]
        )
        await session.flush()
        session.add_all(
            [
                Conversation(
                    cid=ids["conversation_a"], tenant_id=ids["tenant_a"], vid=ids["visitor_a"]
                ),
                Conversation(
                    cid=ids["conversation_b"], tenant_id=ids["tenant_b"], vid=ids["visitor_b"]
                ),
            ]
        )
        await session.commit()

    yield ids


async def test_list_site_keys_returns_only_this_tenants_site_key(_seeded_tenants):
    ids = _seeded_tenants
    async with db_session() as session:
        repo = TenantScopedRepository(tenant_id=ids["tenant_a"], session=session)
        site_keys = await repo.list_site_keys()

    assert [site_key.id for site_key in site_keys] == [ids["site_key_a"]]


async def test_get_conversation_by_id_rejects_another_tenants_real_id(_seeded_tenants):
    # The hostile-caller case: tenant B's conversation id is a real, valid
    # row -- it must still come back None for a repository scoped to A.
    ids = _seeded_tenants
    async with db_session() as session:
        repo = TenantScopedRepository(tenant_id=ids["tenant_a"], session=session)
        result = await repo.get_conversation_by_id(ids["conversation_b"])

    assert result is None


async def test_get_conversation_by_id_returns_none_for_a_nonexistent_id(_seeded_tenants):
    # Must produce the same outcome as the foreign-tenant case above -- not a
    # different error, not a crash -- otherwise the two are distinguishable
    # and a caller could learn "that id is real, just not yours".
    ids = _seeded_tenants
    async with db_session() as session:
        repo = TenantScopedRepository(tenant_id=ids["tenant_a"], session=session)
        result = await repo.get_conversation_by_id(uuid.uuid4())

    assert result is None


async def test_get_conversation_by_id_returns_this_tenants_own_row(_seeded_tenants):
    # Proves the mechanism isn't just blocking everything.
    ids = _seeded_tenants
    async with db_session() as session:
        repo = TenantScopedRepository(tenant_id=ids["tenant_a"], session=session)
        result = await repo.get_conversation_by_id(ids["conversation_a"])

    assert result is not None
    assert result.cid == ids["conversation_a"]


async def test_create_tenant_creates_a_real_row(_seeded_tenants):
    # No tenant scoping applies (there's no tenant_id to scope by yet): a
    # plain function taking a session directly, not a repository.
    async with db_session() as session:
        tenant = await create_tenant(session, name="Brand New Tenant", status="active")
        await session.commit()
        tenant_id = tenant.id

    async with db_session() as session:
        repo = TenantScopedRepository(tenant_id=tenant_id, session=session)
        result = await repo.get_tenant()

    assert result.id == tenant_id
    assert result.name == "Brand New Tenant"


async def test_get_tenant_returns_the_repositorys_own_tenant(_seeded_tenants):
    # Confirms this in a two-tenant database: a repository scoped to A
    # returns A, never B, even though B is a real row right next to it.
    ids = _seeded_tenants
    async with db_session() as session:
        repo = TenantScopedRepository(tenant_id=ids["tenant_a"], session=session)
        result = await repo.get_tenant()

    assert result.id == ids["tenant_a"]
    assert result.name == "Tenant A"


async def test_create_site_key_sets_tenant_id_automatically(_seeded_tenants):
    ids = _seeded_tenants
    async with db_session() as session:
        repo = TenantScopedRepository(tenant_id=ids["tenant_a"], session=session)
        site_key = await repo.create_site_key(
            key="pk_live_new_key", allowed_origins=["https://a.example"], environment="production"
        )

    assert site_key.tenant_id == ids["tenant_a"]
    assert site_key.status == "draft"


async def test_create_site_key_rejects_a_duplicate_key_and_leaves_the_session_usable(
    _seeded_tenants,
):
    # Duplication check after 2.2.a/b/c, item D1: the same rollback-after-
    # IntegrityError gap claim_domain() originally had, found here too --
    # mirrors that fix's own proof shape exactly (test_claim_domain_
    # rejects_a_different_tenants_active_claim_..., test_ingest_
    # repository.py): a duplicate key must raise a clean application-level
    # exception, not an unhandled IntegrityError, and the session must
    # remain usable for more real queries immediately afterward, proving
    # the internal rollback() actually worked rather than leaving the
    # transaction aborted.
    ids = _seeded_tenants
    async with db_session() as session:
        repo = TenantScopedRepository(tenant_id=ids["tenant_a"], session=session)
        await repo.create_site_key(
            key="pk_live_duplicate_probe",
            allowed_origins=["https://a.example"],
            environment="production",
        )
        await session.commit()

        with pytest.raises(SiteKeyAlreadyExistsError):
            await repo.create_site_key(
                key="pk_live_duplicate_probe",
                allowed_origins=["https://b.example"],
                environment="production",
            )

        # Same session, immediately after catching the collision: proves
        # the internal rollback() left it usable, not poisoned by the
        # aborted transaction. Filtered to this test's own key rather than
        # asserting the full list: _seeded_tenants already seeds tenant_a
        # with its own site_key ("pk_live_tenant_a"), unrelated to this
        # test's own probe.
        still_usable = await repo.list_site_keys()
    matching = [k for k in still_usable if k.key == "pk_live_duplicate_probe"]
    assert len(matching) == 1


async def test_create_visitor_sets_tenant_id_automatically(_seeded_tenants):
    ids = _seeded_tenants
    async with db_session() as session:
        repo = TenantScopedRepository(tenant_id=ids["tenant_a"], session=session)
        visitor = await repo.create_visitor(
            site_key_id=ids["site_key_a"], secret_hash="new-hash"  # noqa: S106
        )

    assert visitor.tenant_id == ids["tenant_a"]
    assert visitor.site_key_id == ids["site_key_a"]


async def test_create_visitor_rejects_a_foreign_tenants_site_key_id(_seeded_tenants):
    # Hostile-caller case: tenant B's site_key_id is a real, valid row, but
    # does not belong to tenant A -- must be rejected, not silently accepted.
    ids = _seeded_tenants
    async with db_session() as session:
        repo = TenantScopedRepository(tenant_id=ids["tenant_a"], session=session)
        with pytest.raises(ValueError, match="does not belong to tenant"):
            await repo.create_visitor(
                site_key_id=ids["site_key_b"], secret_hash="new-hash"  # noqa: S106
            )


async def test_create_visitor_rejects_a_nonexistent_site_key_id(_seeded_tenants):
    # Must raise the identical error as the foreign-tenant case above.
    ids = _seeded_tenants
    async with db_session() as session:
        repo = TenantScopedRepository(tenant_id=ids["tenant_a"], session=session)
        with pytest.raises(ValueError, match="does not belong to tenant"):
            await repo.create_visitor(
                site_key_id=uuid.uuid4(), secret_hash="new-hash"  # noqa: S106
            )


async def test_get_visitor_by_id_rejects_another_tenants_real_id(_seeded_tenants):
    ids = _seeded_tenants
    async with db_session() as session:
        repo = TenantScopedRepository(tenant_id=ids["tenant_a"], session=session)
        result = await repo.get_visitor_by_id(ids["visitor_b"])

    assert result is None


async def test_get_visitor_by_id_returns_none_for_a_nonexistent_id(_seeded_tenants):
    # Must produce the same outcome as the foreign-tenant case above.
    ids = _seeded_tenants
    async with db_session() as session:
        repo = TenantScopedRepository(tenant_id=ids["tenant_a"], session=session)
        result = await repo.get_visitor_by_id(uuid.uuid4())

    assert result is None


async def test_get_visitor_by_id_returns_this_tenants_own_row(_seeded_tenants):
    ids = _seeded_tenants
    async with db_session() as session:
        repo = TenantScopedRepository(tenant_id=ids["tenant_a"], session=session)
        result = await repo.get_visitor_by_id(ids["visitor_a"])

    assert result is not None
    assert result.vid == ids["visitor_a"]


async def test_create_conversation_sets_tenant_id_automatically(_seeded_tenants):
    ids = _seeded_tenants
    async with db_session() as session:
        repo = TenantScopedRepository(tenant_id=ids["tenant_a"], session=session)
        conversation = await repo.create_conversation(vid=ids["visitor_a"], title="hello")

    assert conversation.tenant_id == ids["tenant_a"]
    assert conversation.vid == ids["visitor_a"]


async def test_create_conversation_rejects_a_foreign_tenants_vid(_seeded_tenants):
    # Hostile-caller case: tenant B's vid is a real, valid row, but does not
    # belong to tenant A -- must be rejected, not silently accepted.
    ids = _seeded_tenants
    async with db_session() as session:
        repo = TenantScopedRepository(tenant_id=ids["tenant_a"], session=session)
        with pytest.raises(ValueError, match="does not belong to tenant"):
            await repo.create_conversation(vid=ids["visitor_b"])


async def test_create_conversation_rejects_a_nonexistent_vid(_seeded_tenants):
    # Must raise the identical error as the foreign-tenant case above.
    ids = _seeded_tenants
    async with db_session() as session:
        repo = TenantScopedRepository(tenant_id=ids["tenant_a"], session=session)
        with pytest.raises(ValueError, match="does not belong to tenant"):
            await repo.create_conversation(vid=uuid.uuid4())


async def test_get_site_key_by_key_returns_the_right_row_for_either_tenant(_seeded_tenants):
    # Unscoped by design (no tenant_id is known yet at this point in the
    # session flow) -- proven with two site keys belonging to two DIFFERENT
    # tenants, so this isn't accidentally tenant-filtered.
    ids = _seeded_tenants
    async with db_session() as session:
        result_a = await get_site_key_by_key(session, "pk_live_tenant_a")
        result_b = await get_site_key_by_key(session, "pk_live_tenant_b")

    assert result_a is not None
    assert result_a.id == ids["site_key_a"]
    assert result_a.tenant_id == ids["tenant_a"]
    assert result_b is not None
    assert result_b.id == ids["site_key_b"]
    assert result_b.tenant_id == ids["tenant_b"]


async def test_get_site_key_by_key_returns_none_for_an_unknown_key(_seeded_tenants):
    async with db_session() as session:
        result = await get_site_key_by_key(session, "pk_live_does_not_exist")

    assert result is None


async def test_get_visitor_by_secret_returns_the_correct_visitor(_seeded_tenants):
    ids = _seeded_tenants
    async with db_session() as session:
        repo = TenantScopedRepository(tenant_id=ids["tenant_a"], session=session)
        result = await repo.get_visitor_by_secret(
            ids["visitor_a_secret"], site_key_id=ids["site_key_a"]
        )

    assert result is not None
    assert result.vid == ids["visitor_a"]


async def test_get_visitor_by_secret_wrong_secret_for_a_real_visitor_returns_none(_seeded_tenants):
    ids = _seeded_tenants
    wrong_secret = generate_visitor_secret()
    assert wrong_secret != ids["visitor_a_secret"]
    async with db_session() as session:
        repo = TenantScopedRepository(tenant_id=ids["tenant_a"], session=session)
        result = await repo.get_visitor_by_secret(wrong_secret, site_key_id=ids["site_key_a"])

    assert result is None


async def test_get_visitor_by_secret_no_matching_row_is_indistinguishable_from_wrong_secret(
    _seeded_tenants,
):
    # Both cases must produce the exact same outcome: get_visitor_by_secret
    # runs one query (tenant_id, site_key_id and secret_hash equality all
    # in the same WHERE clause) and one `if visitor is None: return None`
    # branch -- there is no separate branch for "no row has this hash at
    # all" vs. "a row exists but a different hash", so a wrong-but-plausible
    # secret and a secret that could never match are structurally unable to
    # produce a different result here.
    ids = _seeded_tenants
    async with db_session() as session:
        repo = TenantScopedRepository(tenant_id=ids["tenant_a"], session=session)
        wrong_secret_result = await repo.get_visitor_by_secret(
            generate_visitor_secret(), site_key_id=ids["site_key_a"]
        )
        no_match_result = await repo.get_visitor_by_secret(
            generate_visitor_secret(), site_key_id=ids["site_key_a"]
        )

    assert wrong_secret_result is None
    assert no_match_result is None
    assert type(wrong_secret_result) is type(no_match_result)


async def test_get_visitor_by_secret_rejects_another_tenants_visitor(_seeded_tenants):
    # Hostile-caller case: tenant B's secret is genuinely correct for tenant
    # B's own visitor/site key -- a repository scoped to A must still never
    # return it, since tenant_id == self.tenant_id is one of the three
    # conditions in the single query's own WHERE clause, alongside
    # site_key_id and the secret's hash.
    ids = _seeded_tenants
    async with db_session() as session:
        repo = TenantScopedRepository(tenant_id=ids["tenant_a"], session=session)
        result = await repo.get_visitor_by_secret(
            ids["visitor_b_secret"], site_key_id=ids["site_key_b"]
        )

    assert result is None


async def test_get_visitor_by_secret_empty_or_malformed_secret_returns_none(_seeded_tenants):
    # C2 (duplication check after 1.4.c/d/e): proves this at the
    # repository/integration layer against the real database -- distinct
    # from verify_visitor_secret's own unit-level coverage of the same
    # shapes in test_visitor_secrets.py. get_visitor_by_secret() computes
    # hash_visitor_secret(secret) unconditionally before querying; neither
    # shape below should ever raise, and neither matches any real row.
    ids = _seeded_tenants
    async with db_session() as session:
        repo = TenantScopedRepository(tenant_id=ids["tenant_a"], session=session)
        empty_result = await repo.get_visitor_by_secret("", site_key_id=ids["site_key_a"])
        malformed_result = await repo.get_visitor_by_secret(
            "not-a-real-secret!!", site_key_id=ids["site_key_a"]
        )

    assert empty_result is None
    assert malformed_result is None


async def _fetch_visitor_row(session, vid):
    # Raw SQL, not an ORM select(Visitor): reads straight from the database
    # rather than through the session's identity map, so a value read here
    # can never be a stale in-memory ORM attribute mistaken for a live
    # database value (the touch_visitor_last_seen tests below need a
    # genuine before/after comparison against Postgres itself).
    result = await session.execute(
        sa.text(
            "SELECT tenant_id, site_key_id, secret_hash, first_seen_at, last_seen_at, "
            "external_user_id FROM visitors WHERE vid = :vid"
        ),
        {"vid": vid},
    )
    return result.mappings().one()


async def test_touch_visitor_last_seen_advances_last_seen_at_and_nothing_else(_seeded_tenants):
    ids = _seeded_tenants
    async with db_session() as session:
        before = await _fetch_visitor_row(session, ids["visitor_a"])

    async with db_session() as session:
        repo = TenantScopedRepository(tenant_id=ids["tenant_a"], session=session)
        await repo.touch_visitor_last_seen(ids["visitor_a"])
        await session.commit()

    async with db_session() as session:
        after = await _fetch_visitor_row(session, ids["visitor_a"])

    assert after["last_seen_at"] > before["last_seen_at"]
    for column in ("tenant_id", "site_key_id", "secret_hash", "first_seen_at", "external_user_id"):
        assert after[column] == before[column], f"{column} should not have changed"


async def test_touch_visitor_last_seen_does_nothing_for_another_tenants_vid(_seeded_tenants):
    # Hostile-caller case: tenant B's vid is real, but a repository scoped
    # to A must not be able to touch B's row -- proved against the real
    # database (every column, not just last_seen_at, byte-for-byte
    # unchanged), mirroring 1.2.e's own cross-tenant proof pattern.
    ids = _seeded_tenants
    async with db_session() as session:
        before = await _fetch_visitor_row(session, ids["visitor_b"])

    async with db_session() as session:
        repo = TenantScopedRepository(tenant_id=ids["tenant_a"], session=session)
        await repo.touch_visitor_last_seen(ids["visitor_b"])
        await session.commit()

    async with db_session() as session:
        after = await _fetch_visitor_row(session, ids["visitor_b"])

    assert after == before
