# backend/tests/test_ingest_repository.py
# Task 2.1.a: tests for db_connections' credential accessor
# (backend/app/ingest/repository.py). All live, against the real test
# database -- pgcrypto's own behavior cannot be proven any other way.
#
# Two groups:
#   - the encryption proof: a credential written through
#     create_db_connection() is genuinely encrypted at rest (a raw SQL
#     SELECT bypassing the accessor never returns the plaintext, and
#     neither does the ORM row's own default repr()/str()/vars() once
#     fetched), and is read back correctly through
#     get_decrypted_credentials() -- proving encryption is real, not just
#     declared.
#   - the tenant-isolation proof, matching TenantScopedRepository's own
#     hostile-caller discipline (test_tenancy_repository.py): a real
#     db_connection belonging to a different tenant, and a nonexistent id,
#     must produce the identical outcome (both raise), never distinguished.
import uuid

import pytest
import sqlalchemy as sa

from app.config import get_settings
from app.ingest.models import Job, VerifiedDomain
from app.ingest.queue import claim_next_job
from app.ingest.repository import (
    CredentialEncryptionError,
    DomainAlreadyClaimedError,
    IngestRepository,
)
from app.plans import models as plans_models  # noqa: F401 (registers "plans" on Base.metadata)
from app.tenancy.models import Tenant
from tests.conftest import (
    assert_db_connection_credential_round_trip,
    assert_secret_not_in_exception_chain,
    db_session,
)

DISTINCTIVE_PASSWORD = "sUpEr-DiStInCtIvE-fake-password-123-never-leak-me"  # noqa: S105 (test fixture value, not a real secret)


@pytest.fixture
async def _seeded_tenants(reset_test_database):
    ids = {"tenant_a": uuid.uuid4(), "tenant_b": uuid.uuid4()}
    async with db_session() as session:
        session.add_all(
            [
                Tenant(id=ids["tenant_a"], name="Tenant A", status="active"),
                Tenant(id=ids["tenant_b"], name="Tenant B", status="active"),
            ]
        )
        await session.commit()
    yield ids


async def test_create_and_get_decrypted_credentials_round_trip(_seeded_tenants):
    ids = _seeded_tenants
    credentials = {"username": "dbuser", "password": DISTINCTIVE_PASSWORD}
    await assert_db_connection_credential_round_trip(ids["tenant_a"], credentials)


async def test_raw_sql_select_never_returns_plaintext_credentials(_seeded_tenants):
    # Proves encryption is real, not just declared: bypassing
    # get_decrypted_credentials() entirely with a raw SQL SELECT of the
    # stored column must never return the plaintext password.
    ids = _seeded_tenants
    async with db_session() as session:
        repo = IngestRepository(tenant_id=ids["tenant_a"], session=session)
        db_connection = await repo.create_db_connection(
            host="db.example.internal",
            credentials={"username": "dbuser", "password": DISTINCTIVE_PASSWORD},
        )
        await session.commit()
        db_connection_id = db_connection.id

    async with db_session() as session:
        raw = (
            await session.execute(
                sa.text("SELECT encrypted_credentials FROM db_connections WHERE id = :id"),
                {"id": db_connection_id},
            )
        ).scalar_one()

    assert DISTINCTIVE_PASSWORD.encode() not in bytes(raw)


async def test_fetched_db_connection_row_never_leaks_plaintext_via_repr_or_str(_seeded_tenants):
    # The genuine leak proof, using REAL ciphertext (not a hand-built fake
    # bytes value): once a real, encrypted row is fetched via a plain
    # ORM select, the plaintext must not appear in that instance's own
    # repr()/str()/vars() -- only ciphertext ever sits in
    # encrypted_credentials.
    ids = _seeded_tenants
    async with db_session() as session:
        repo = IngestRepository(tenant_id=ids["tenant_a"], session=session)
        db_connection = await repo.create_db_connection(
            host="db.example.internal",
            credentials={"username": "dbuser", "password": DISTINCTIVE_PASSWORD},
        )
        await session.commit()
        db_connection_id = db_connection.id

    async with db_session() as session:
        result = await session.execute(
            sa.select(type(db_connection)).where(type(db_connection).id == db_connection_id)
        )
        fetched = result.scalar_one()

        assert DISTINCTIVE_PASSWORD.encode() not in repr(fetched).encode()
        assert DISTINCTIVE_PASSWORD.encode() not in str(fetched).encode()
        assert DISTINCTIVE_PASSWORD.encode() not in repr(vars(fetched)).encode()


async def test_get_decrypted_credentials_rejects_another_tenants_real_id(_seeded_tenants):
    # The hostile-caller case: tenant B's db_connection id is a real,
    # valid row -- it must still be rejected for a repository scoped to A.
    ids = _seeded_tenants
    async with db_session() as session:
        repo_b = IngestRepository(tenant_id=ids["tenant_b"], session=session)
        db_connection = await repo_b.create_db_connection(
            host="tenant-b-db.internal", credentials={"username": "u", "password": "p"}
        )
        await session.commit()
        db_connection_id = db_connection.id

    async with db_session() as session:
        repo_a = IngestRepository(tenant_id=ids["tenant_a"], session=session)
        with pytest.raises(ValueError):
            await repo_a.get_decrypted_credentials(db_connection_id)


async def test_get_decrypted_credentials_raises_the_same_way_for_a_nonexistent_id(
    _seeded_tenants,
):
    # Must produce the identical outcome as the foreign-tenant case above
    # -- not a different error, not a crash -- otherwise the two are
    # distinguishable and a caller could learn "that id is real, just not
    # yours" (the same enumeration-oracle concern
    # TenantScopedRepository's own reads already guard against).
    ids = _seeded_tenants
    async with db_session() as session:
        repo = IngestRepository(tenant_id=ids["tenant_a"], session=session)
        with pytest.raises(ValueError):
            await repo.get_decrypted_credentials(uuid.uuid4())


async def test_identical_plaintext_encrypts_to_different_ciphertext_across_rows(_seeded_tenants):
    # Duplication check after 2.1.a/b, item C2: pgp_sym_encrypt() must never
    # behave like deterministic/ECB-style encryption -- two rows (even
    # across different tenants) encrypting the exact same credential
    # dict must produce different encrypted_credentials bytes, or an
    # attacker who can compare ciphertexts (without decrypting either)
    # could still learn that two tenants share a password.
    ids = _seeded_tenants
    same_credentials = {"username": "dbuser", "password": DISTINCTIVE_PASSWORD}
    raw_values = []
    for tenant_id in (ids["tenant_a"], ids["tenant_b"]):
        async with db_session() as session:
            repo = IngestRepository(tenant_id=tenant_id, session=session)
            db_connection = await repo.create_db_connection(
                host="db.example.internal", credentials=same_credentials
            )
            await session.commit()
            db_connection_id = db_connection.id

        async with db_session() as session:
            raw = (
                await session.execute(
                    sa.text("SELECT encrypted_credentials FROM db_connections WHERE id = :id"),
                    {"id": db_connection_id},
                )
            ).scalar_one()
        raw_values.append(bytes(raw))

    assert raw_values[0] != raw_values[1]


async def test_enqueue_creates_a_pending_job_with_the_right_initial_state(_seeded_tenants):
    # Task 2.1.c: enqueue() is a method here, not a standalone function --
    # see queue.py's own header comment and PROJECT_SPEC.md's Step 2.1.c
    # decision entry for why (a real tenant_id is always already known at
    # enqueue time, unlike create_tenant()'s case).
    ids = _seeded_tenants
    async with db_session() as session:
        repo = IngestRepository(tenant_id=ids["tenant_a"], session=session)
        job = await repo.enqueue(job_type="crawl", payload={"url": "https://example.com"})
        await session.commit()
        job_id = job.id

    async with db_session() as session:
        row = (await session.execute(sa.select(Job).where(Job.id == job_id))).scalar_one()

    assert row.tenant_id == ids["tenant_a"]
    assert row.source_id is None
    assert row.job_type == "crawl"
    assert row.status == "pending"
    assert row.attempts == 0
    assert row.max_attempts == get_settings().job_max_attempts
    assert row.payload == {"url": "https://example.com"}
    assert row.next_run_at is not None

    # next_run_at's own "now" default is a database server-side value
    # (func.now(), matching TIMESTAMP_NOW everywhere else in this
    # codebase) -- comparing it against an app-process Python timestamp
    # would be comparing two different clocks with no guaranteed
    # sub-millisecond sync between the app process and the Postgres
    # container, a real, observed flake, not a hypothetical one. The
    # behavioral guarantee that actually matters -- "immediately eligible
    # for claiming" -- is proven functionally instead: claim_next_job()
    # must succeed on this exact job right away.
    async with db_session() as session:
        claimed = await claim_next_job(session)
        await session.commit()
    assert claimed is not None
    assert claimed.id == job_id


async def test_get_decrypted_credentials_never_leaks_the_key_on_a_wrong_key_failure(
    _seeded_tenants, monkeypatch
):
    # Duplication check after 2.1.a/b, item C1: a genuine failure path in
    # IngestRepository itself, not just a Settings-validation-layer proof
    # like test_config.py's own leak tests -- reproduced live before
    # writing this test: encrypt with one key, then attempt to decrypt the
    # same row with a different key (the same shape as an operational
    # key-rotation mistake). Confirmed live that SQLAlchemy's own default
    # exception formatting embeds every bound parameter -- including the
    # key itself -- in str(exc) unless the repository sanitizes it; this
    # asserts the NEW, sanitized CredentialEncryptionError, walking its
    # full chain (both keys, not just the wrong one, in case a future
    # change ever binds the right key into the same failing statement).
    ids = _seeded_tenants
    right_key = "right-key-DISTINCTIVE-fake-38924710293847"
    wrong_key = "WRONG-key-DISTINCTIVE-fake-91827364509182"

    monkeypatch.setenv("DB_CONNECTION_ENCRYPTION_KEY", right_key)
    get_settings.cache_clear()
    async with db_session() as session:
        repo = IngestRepository(tenant_id=ids["tenant_a"], session=session)
        db_connection = await repo.create_db_connection(
            host="db.example.internal", credentials={"username": "u", "password": "p"}
        )
        await session.commit()
        db_connection_id = db_connection.id

    monkeypatch.setenv("DB_CONNECTION_ENCRYPTION_KEY", wrong_key)
    get_settings.cache_clear()
    async with db_session() as session:
        repo = IngestRepository(tenant_id=ids["tenant_a"], session=session)
        with pytest.raises(CredentialEncryptionError) as exc_info:
            await repo.get_decrypted_credentials(db_connection_id)

    assert_secret_not_in_exception_chain(exc_info.value, right_key, wrong_key)


# --- Task 2.2.c: tenant-scoped domain-claim primitives ---------------------


async def test_claim_domain_creates_a_pending_row_with_a_fresh_token(_seeded_tenants):
    ids = _seeded_tenants
    async with db_session() as session:
        repo = IngestRepository(tenant_id=ids["tenant_a"], session=session)
        domain = await repo.claim_domain("example.com")
        await session.commit()
        domain_id = domain.id

    async with db_session() as session:
        row = (
            await session.execute(sa.select(VerifiedDomain).where(VerifiedDomain.id == domain_id))
        ).scalar_one()

    assert row.tenant_id == ids["tenant_a"]
    assert row.domain == "example.com"
    assert row.method == "dns"
    assert row.status == "pending"
    assert row.verification_token
    assert len(row.verification_token) > 20
    assert row.verified_at is None
    assert row.revoked_at is None


async def test_list_domains_returns_only_the_calling_tenants_own_rows(_seeded_tenants):
    # Matches every prior isolation test in this project (e.g.
    # TenantScopedRepository.list_site_keys()): two tenants, each with
    # their own real rows, and a repository scoped to one must never see
    # the other's.
    ids = _seeded_tenants
    async with db_session() as session:
        repo_a = IngestRepository(tenant_id=ids["tenant_a"], session=session)
        repo_b = IngestRepository(tenant_id=ids["tenant_b"], session=session)
        await repo_a.claim_domain("tenant-a-one.example")
        await repo_a.claim_domain("tenant-a-two.example")
        await repo_b.claim_domain("tenant-b-one.example")
        await session.commit()

    async with db_session() as session:
        repo_a = IngestRepository(tenant_id=ids["tenant_a"], session=session)
        domains_a = await repo_a.list_domains()

    assert {d.domain for d in domains_a} == {"tenant-a-one.example", "tenant-a-two.example"}
    assert all(d.tenant_id == ids["tenant_a"] for d in domains_a)


async def test_get_domain_by_id_rejects_another_tenants_real_id(_seeded_tenants):
    # The hostile-caller case: tenant B's domain id is a real, valid row --
    # it must still return None for a repository scoped to A, not raise,
    # not leak the row.
    ids = _seeded_tenants
    async with db_session() as session:
        repo_b = IngestRepository(tenant_id=ids["tenant_b"], session=session)
        domain = await repo_b.claim_domain("tenant-b.example")
        await session.commit()
        domain_id = domain.id

    async with db_session() as session:
        repo_a = IngestRepository(tenant_id=ids["tenant_a"], session=session)
        assert await repo_a.get_domain_by_id(domain_id) is None


async def test_get_domain_by_id_returns_none_for_a_nonexistent_id_identically(_seeded_tenants):
    # Must produce the identical outcome (None, no exception) as the
    # foreign-tenant case above -- not distinguishable, matching
    # get_decrypted_credentials()'s/get_visitor_by_id()'s own discipline.
    ids = _seeded_tenants
    async with db_session() as session:
        repo = IngestRepository(tenant_id=ids["tenant_a"], session=session)
        assert await repo.get_domain_by_id(uuid.uuid4()) is None


async def test_claim_domain_rejects_a_different_tenants_active_claim_then_allows_it_once_revoked(
    _seeded_tenants,
):
    # The key proof: the partial unique index's collision handling,
    # exercised through claim_domain() itself (not raw SQL) -- a genuinely
    # different repository instance, different tenant_id, same session's
    # own transaction boundaries as any real caller would use.
    ids = _seeded_tenants
    async with db_session() as session:
        repo_a = IngestRepository(tenant_id=ids["tenant_a"], session=session)
        repo_b = IngestRepository(tenant_id=ids["tenant_b"], session=session)
        domain_a = await repo_a.claim_domain("contested.example")
        await session.commit()

        with pytest.raises(DomainAlreadyClaimedError):
            await repo_b.claim_domain("contested.example")

        # The method's own internal rollback must leave this session
        # usable for more queries afterward, not poisoned by the aborted
        # transaction -- proved here by immediately doing another real
        # query on the SAME session, not a fresh one.
        still_a_only = await repo_a.list_domains()
        assert {d.domain for d in still_a_only} == {"contested.example"}

    # Revoke A's claim directly (raw update -- revoke_domain() doesn't
    # exist until a later subtask, this is only this test's own setup).
    async with db_session() as session:
        await session.execute(
            sa.text(
                "UPDATE verified_domains SET status = 'revoked', revoked_at = now() "
                "WHERE id = :id"
            ),
            {"id": domain_a.id},
        )
        await session.commit()

    async with db_session() as session:
        repo_b = IngestRepository(tenant_id=ids["tenant_b"], session=session)
        domain_b = await repo_b.claim_domain("contested.example")
        await session.commit()

    assert domain_b.tenant_id == ids["tenant_b"]
    assert domain_b.status == "pending"


async def test_claim_domain_rejects_the_same_tenants_own_active_claim_too(_seeded_tenants):
    # PROJECT_SPEC.md's Step 2.2 breakdown decision (e): a same-tenant
    # double-claim is NOT idempotent -- it is rejected exactly like a
    # cross-tenant collision, with the identical exception, since the
    # partial index cannot (and claim_domain() deliberately does not try
    # to) distinguish the two. See claim_domain()'s own comment for the
    # full reasoning.
    ids = _seeded_tenants
    async with db_session() as session:
        repo = IngestRepository(tenant_id=ids["tenant_a"], session=session)
        await repo.claim_domain("self-collision.example")
        await session.commit()

        with pytest.raises(DomainAlreadyClaimedError):
            await repo.claim_domain("self-collision.example")
