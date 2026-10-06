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
from datetime import UTC, datetime

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError

from app.config import get_settings
from app.ingest import repository as repository_module
from app.ingest.models import AuditLog, Job, Source, VerifiedDomain
from app.ingest.queue import claim_next_job
from app.ingest.repository import (
    VALID_JOB_TYPES,
    CredentialEncryptionError,
    DomainAlreadyClaimedError,
    DomainRevokedError,
    IngestRepository,
    UnknownJobTypeError,
    UnknownSourceError,
    revoke_domain,
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
        job = await repo.enqueue(job_type="ingest_crawl", payload={"url": "https://example.com"})
        await session.commit()
        job_id = job.id

    async with db_session() as session:
        row = (await session.execute(sa.select(Job).where(Job.id == job_id))).scalar_one()

    assert row.tenant_id == ids["tenant_a"]
    assert row.source_id is None
    assert row.job_type == "ingest_crawl"
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


# --- Task 2.6.b: job_type validation and the source_id FK fix ---------------


async def test_enqueue_rejects_an_unregistered_job_type_before_any_database_write(
    _seeded_tenants, monkeypatch
):
    # THE "zero database attempts" proof (matching 2.3.c's own discipline,
    # applied here to a write instead of a network connection): session.add
    # is spied on, not just the final row count checked afterward, so this
    # proves the rejection happens BEFORE any attempt to touch the database
    # at all, not merely that the eventual insert would have failed.
    ids = _seeded_tenants
    add_calls = []

    async with db_session() as session:
        monkeypatch.setattr(session, "add", lambda obj: add_calls.append(obj))
        repo = IngestRepository(tenant_id=ids["tenant_a"], session=session)
        with pytest.raises(UnknownJobTypeError, match="not-a-real-job-type"):
            await repo.enqueue(job_type="not-a-real-job-type", payload={})

    assert add_calls == []


@pytest.mark.parametrize("job_type", sorted(VALID_JOB_TYPES))
async def test_enqueue_accepts_every_value_in_the_real_vocabulary(_seeded_tenants, job_type):
    ids = _seeded_tenants
    async with db_session() as session:
        repo = IngestRepository(tenant_id=ids["tenant_a"], session=session)
        job = await repo.enqueue(job_type=job_type, payload={})
        await session.commit()
    assert job.job_type == job_type
    assert job.status == "pending"


async def test_enqueue_rejects_a_nonexistent_source_id_with_a_usable_rollback(_seeded_tenants):
    # Matches claim_domain()'s own established rollback-proof pattern
    # exactly: a real query on the SAME session immediately afterward
    # proves the transaction was not left aborted, not just that an
    # exception was raised.
    ids = _seeded_tenants
    bogus_source_id = uuid.uuid4()
    async with db_session() as session:
        repo = IngestRepository(tenant_id=ids["tenant_a"], session=session)
        with pytest.raises(UnknownSourceError, match=str(bogus_source_id)):
            await repo.enqueue(job_type="ingest_url", source_id=bogus_source_id, payload={})

        # The session must still be usable for another real query right
        # here -- if rollback() were missing, this would fail with
        # "current transaction is aborted."
        still_counts = (
            await session.execute(sa.select(sa.func.count()).select_from(Job))
        ).scalar_one()
        assert still_counts == 0  # confirms the rejected insert left no row behind either


async def test_enqueue_succeeds_with_a_real_existing_source_id(_seeded_tenants):
    ids = _seeded_tenants
    async with db_session() as session:
        source = Source(
            tenant_id=ids["tenant_a"],
            type="urls",
            refresh_interval="daily",
            status="active",
        )
        session.add(source)
        await session.commit()
        source_id = source.id

    async with db_session() as session:
        repo = IngestRepository(tenant_id=ids["tenant_a"], session=session)
        job = await repo.enqueue(job_type="ingest_url", source_id=source_id, payload={})
        await session.commit()
        job_id = job.id

    async with db_session() as session:
        row = (await session.execute(sa.select(Job).where(Job.id == job_id))).scalar_one()
    assert row.source_id == source_id
    assert row.job_type == "ingest_url"
    assert row.status == "pending"


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


async def test_claim_domain_collision_exception_reveals_neither_the_other_tenant_nor_its_token(
    _seeded_tenants,
):
    # Duplication check after 2.2.a/b/c, item C1: a no-oracle proof
    # matching this project's established leak-test discipline
    # (assert_secret_not_in_exception_chain and friends) -- a tenant
    # probing for a domain's own availability must learn nothing about
    # WHO holds a conflicting active claim, or what their verification
    # token is, from the rejection itself.
    ids = _seeded_tenants
    async with db_session() as session:
        repo_a = IngestRepository(tenant_id=ids["tenant_a"], session=session)
        repo_b = IngestRepository(tenant_id=ids["tenant_b"], session=session)
        domain_a = await repo_a.claim_domain("oracle-probe.example")
        await session.commit()
        # Captured before repo_b's own collision triggers claim_domain()'s
        # internal rollback(): SQLAlchemy's Session.rollback() expires
        # every object in the session's identity map, including domain_a
        # (already committed, in an earlier transaction) -- reading its
        # attributes after that rollback, or after this block closes the
        # session, would need to re-fetch them and raise
        # DetachedInstanceError once the session is gone. Found live
        # while running this exact test, not assumed.
        domain_a_id = str(domain_a.id)
        domain_a_token = domain_a.verification_token

        with pytest.raises(DomainAlreadyClaimedError) as exc_info:
            await repo_b.claim_domain("oracle-probe.example")

    rendered = str(exc_info.value)
    assert str(ids["tenant_a"]) not in rendered
    assert domain_a_token not in rendered
    assert domain_a_id not in rendered


# --- Task 2.2.e: confirm_verification() wiring -----------------------------


async def _never_call_check_dns_verification(*_args, **_kwargs):
    raise AssertionError("check_dns_verification must not be called for this case")


async def test_confirm_verification_on_an_already_verified_domain_is_a_safe_no_op(
    _seeded_tenants, monkeypatch
):
    # docs/SPEC.md §5.5: "verified once... not re-checked" -- taken
    # literally. Not just "the timestamp doesn't change": the DNS check
    # itself must never run at all for this case.
    ids = _seeded_tenants
    fixed_verified_at = datetime(2026, 1, 1, tzinfo=UTC)
    async with db_session() as session:
        repo = IngestRepository(tenant_id=ids["tenant_a"], session=session)
        domain = await repo.claim_domain("already-verified.example")
        domain.status = "verified"
        domain.verified_at = fixed_verified_at
        await session.commit()
        domain_id = domain.id

    monkeypatch.setattr(
        repository_module, "check_dns_verification", _never_call_check_dns_verification
    )

    async with db_session() as session:
        repo = IngestRepository(tenant_id=ids["tenant_a"], session=session)
        result = await repo.confirm_verification(domain_id)

    assert result.status == "verified"
    assert result.verified_at == fixed_verified_at


async def test_confirm_verification_on_a_revoked_domain_is_rejected(_seeded_tenants):
    # A revoked claim is never re-verified in place -- the tenant must
    # claim_domain() again (a fresh token), not resume the revoked one.
    ids = _seeded_tenants
    async with db_session() as session:
        repo = IngestRepository(tenant_id=ids["tenant_a"], session=session)
        domain = await repo.claim_domain("revoked.example")
        domain.status = "revoked"
        domain.revoked_at = datetime.now(UTC)
        await session.commit()
        domain_id = domain.id

    async with db_session() as session:
        repo = IngestRepository(tenant_id=ids["tenant_a"], session=session)
        with pytest.raises(DomainRevokedError):
            await repo.confirm_verification(domain_id)


async def test_confirm_verification_on_a_pending_domain_with_a_positive_dns_match(
    _seeded_tenants, monkeypatch
):
    ids = _seeded_tenants
    async with db_session() as session:
        repo = IngestRepository(tenant_id=ids["tenant_a"], session=session)
        domain = await repo.claim_domain("pending-positive.example")
        await session.commit()
        domain_id = domain.id
        expected_domain = domain.domain
        expected_token = domain.verification_token

    # Duplication check after 2.2.d/e/f: captures the actual (domain, token)
    # it was called with, rather than ignoring both arguments -- proving
    # confirm_verification() drives the DNS check with the row's OWN
    # domain/verification_token, not merely that some call happened with
    # whatever arguments. A bug passing the wrong domain or a stale/wrong
    # token would previously have passed this test unnoticed.
    captured_calls = []

    async def _fake_true(domain_arg, token_arg):
        captured_calls.append((domain_arg, token_arg))
        return True

    monkeypatch.setattr(repository_module, "check_dns_verification", _fake_true)

    fixed_now = datetime(2026, 6, 15, 12, 0, 0, tzinfo=UTC)
    async with db_session() as session:
        repo = IngestRepository(tenant_id=ids["tenant_a"], session=session)
        result = await repo.confirm_verification(domain_id, clock=lambda: fixed_now)
        await session.commit()

    assert result.status == "verified"
    assert result.verified_at == fixed_now
    assert captured_calls == [(expected_domain, expected_token)]


async def test_confirm_verification_on_a_pending_domain_with_a_negative_dns_match(
    _seeded_tenants, monkeypatch
):
    # A negative match -- whether DNS resolved cleanly with no matching
    # token, or failed outright (NXDOMAIN/timeout/etc.) -- is the same
    # plain `False` by the time it reaches confirm_verification() (2.2.d's
    # own design); either way, this must be a silent, unchanged no-op, not
    # an exception.
    ids = _seeded_tenants
    async with db_session() as session:
        repo = IngestRepository(tenant_id=ids["tenant_a"], session=session)
        domain = await repo.claim_domain("pending-negative.example")
        await session.commit()
        domain_id = domain.id
        expected_domain = domain.domain
        expected_token = domain.verification_token

    # Duplication check after 2.2.d/e/f: same strengthening as the positive
    # case above -- captures the actual (domain, token) call, proving the
    # negative path checks the row's own data too, not just that a call
    # happened.
    captured_calls = []

    async def _fake_false(domain_arg, token_arg):
        captured_calls.append((domain_arg, token_arg))
        return False

    monkeypatch.setattr(repository_module, "check_dns_verification", _fake_false)

    async with db_session() as session:
        repo = IngestRepository(tenant_id=ids["tenant_a"], session=session)
        result = await repo.confirm_verification(domain_id)

    assert result.status == "pending"
    assert result.verified_at is None
    assert captured_calls == [(expected_domain, expected_token)]


async def test_confirm_verification_rejects_another_tenants_domain_with_no_side_effects(
    _seeded_tenants, monkeypatch
):
    # The hostile-caller case: tenant B calling confirm_verification() on
    # tenant A's own real, valid domain_id must return None (the identical
    # get_domain_by_id() contract) and must have NO side effects at
    # all -- no DNS check, and the row itself verified unchanged directly
    # against the database, not just inferred from the return value.
    ids = _seeded_tenants
    async with db_session() as session:
        repo_a = IngestRepository(tenant_id=ids["tenant_a"], session=session)
        domain = await repo_a.claim_domain("hostile-confirm.example")
        await session.commit()
        domain_id = domain.id
        original_token = domain.verification_token

    monkeypatch.setattr(
        repository_module, "check_dns_verification", _never_call_check_dns_verification
    )

    async with db_session() as session:
        repo_b = IngestRepository(tenant_id=ids["tenant_b"], session=session)
        result = await repo_b.confirm_verification(domain_id)

    assert result is None

    async with db_session() as session:
        row = (
            await session.execute(sa.select(VerifiedDomain).where(VerifiedDomain.id == domain_id))
        ).scalar_one()
    assert row.status == "pending"
    assert row.verification_token == original_token
    assert row.verified_at is None


# --- Task 2.6.a: claim_domain()'s new `method` parameter, and confirm_
# verification()'s dispatch extended to file/meta_tag ------------------------


@pytest.mark.parametrize("method", ["file", "meta_tag"])
async def test_claim_domain_accepts_the_new_non_dns_methods(_seeded_tenants, method):
    ids = _seeded_tenants
    async with db_session() as session:
        repo = IngestRepository(tenant_id=ids["tenant_a"], session=session)
        domain = await repo.claim_domain(f"{method}-claim.example", method=method)
        await session.commit()
        domain_id = domain.id

    async with db_session() as session:
        row = (
            await session.execute(sa.select(VerifiedDomain).where(VerifiedDomain.id == domain_id))
        ).scalar_one()
    assert row.method == method
    assert row.status == "pending"


async def test_confirm_verification_on_a_pending_domain_with_a_positive_file_match(
    _seeded_tenants, monkeypatch
):
    # Matches test_confirm_verification_on_a_pending_domain_with_a_positive_
    # dns_match's own exact style, for the file method's own dispatch branch.
    ids = _seeded_tenants
    async with db_session() as session:
        repo = IngestRepository(tenant_id=ids["tenant_a"], session=session)
        domain = await repo.claim_domain("pending-file-positive.example", method="file")
        await session.commit()
        domain_id = domain.id
        expected_domain = domain.domain
        expected_token = domain.verification_token

    captured_calls = []

    async def _fake_true(domain_arg, token_arg):
        captured_calls.append((domain_arg, token_arg))
        return True

    monkeypatch.setattr(repository_module, "check_file_verification", _fake_true)

    fixed_now = datetime(2026, 6, 15, 12, 0, 0, tzinfo=UTC)
    async with db_session() as session:
        repo = IngestRepository(tenant_id=ids["tenant_a"], session=session)
        result = await repo.confirm_verification(domain_id, clock=lambda: fixed_now)
        await session.commit()

    assert result.status == "verified"
    assert result.verified_at == fixed_now
    assert captured_calls == [(expected_domain, expected_token)]


async def test_confirm_verification_on_a_pending_domain_with_a_negative_file_match(
    _seeded_tenants, monkeypatch
):
    ids = _seeded_tenants
    async with db_session() as session:
        repo = IngestRepository(tenant_id=ids["tenant_a"], session=session)
        domain = await repo.claim_domain("pending-file-negative.example", method="file")
        await session.commit()
        domain_id = domain.id

    async def _fake_false(domain_arg, token_arg):
        return False

    monkeypatch.setattr(repository_module, "check_file_verification", _fake_false)

    async with db_session() as session:
        repo = IngestRepository(tenant_id=ids["tenant_a"], session=session)
        result = await repo.confirm_verification(domain_id)

    assert result.status == "pending"
    assert result.verified_at is None


async def test_confirm_verification_on_a_pending_domain_with_a_positive_meta_tag_match(
    _seeded_tenants, monkeypatch
):
    ids = _seeded_tenants
    async with db_session() as session:
        repo = IngestRepository(tenant_id=ids["tenant_a"], session=session)
        domain = await repo.claim_domain("pending-meta-positive.example", method="meta_tag")
        await session.commit()
        domain_id = domain.id
        expected_domain = domain.domain
        expected_token = domain.verification_token

    captured_calls = []

    async def _fake_true(domain_arg, token_arg):
        captured_calls.append((domain_arg, token_arg))
        return True

    monkeypatch.setattr(repository_module, "check_meta_tag_verification", _fake_true)

    fixed_now = datetime(2026, 6, 15, 12, 0, 0, tzinfo=UTC)
    async with db_session() as session:
        repo = IngestRepository(tenant_id=ids["tenant_a"], session=session)
        result = await repo.confirm_verification(domain_id, clock=lambda: fixed_now)
        await session.commit()

    assert result.status == "verified"
    assert result.verified_at == fixed_now
    assert captured_calls == [(expected_domain, expected_token)]


async def test_confirm_verification_on_a_pending_domain_with_a_negative_meta_tag_match(
    _seeded_tenants, monkeypatch
):
    ids = _seeded_tenants
    async with db_session() as session:
        repo = IngestRepository(tenant_id=ids["tenant_a"], session=session)
        domain = await repo.claim_domain("pending-meta-negative.example", method="meta_tag")
        await session.commit()
        domain_id = domain.id

    async def _fake_false(domain_arg, token_arg):
        return False

    monkeypatch.setattr(repository_module, "check_meta_tag_verification", _fake_false)

    async with db_session() as session:
        repo = IngestRepository(tenant_id=ids["tenant_a"], session=session)
        result = await repo.confirm_verification(domain_id)

    assert result.status == "pending"
    assert result.verified_at is None


# --- Task 2.2.g: revoke_domain() ---------------------------------------------


async def test_revoke_domain_on_a_pending_domain_sets_status_and_writes_an_audit_row(
    _seeded_tenants,
):
    # Reads everything back from the database directly, not just the
    # return value -- proves the writes are real, not just reflected on
    # the in-memory object revoke_domain() happens to hand back.
    ids = _seeded_tenants
    async with db_session() as session:
        repo = IngestRepository(tenant_id=ids["tenant_a"], session=session)
        domain = await repo.claim_domain("revoke-me.example")
        await session.commit()
        domain_id = domain.id
        expected_tenant_id = domain.tenant_id
        expected_domain_name = domain.domain

    fixed_now = datetime(2026, 6, 15, 12, 0, 0, tzinfo=UTC)
    async with db_session() as session:
        result = await revoke_domain(
            session, domain_id, actor="test-admin", clock=lambda: fixed_now
        )
        await session.commit()

    assert result.status == "revoked"
    assert result.revoked_at == fixed_now

    async with db_session() as session:
        domain_row = (
            await session.execute(sa.select(VerifiedDomain).where(VerifiedDomain.id == domain_id))
        ).scalar_one()
        audit_row = (
            await session.execute(
                sa.select(AuditLog).where(AuditLog.target_id == domain_id)
            )
        ).scalar_one()

    assert domain_row.status == "revoked"
    assert domain_row.revoked_at == fixed_now
    assert audit_row.actor == "test-admin"
    assert audit_row.action == "revoke_domain"
    assert audit_row.target_type == "verified_domains"
    assert audit_row.target_id == domain_id
    assert audit_row.details == {
        "domain": expected_domain_name,
        "tenant_id": str(expected_tenant_id),
        "already_revoked": False,
    }


async def test_revoke_domain_on_a_nonexistent_domain_id_returns_none_with_no_audit_row(
    _seeded_tenants,
):
    bogus_domain_id = uuid.uuid4()
    async with db_session() as session:
        result = await revoke_domain(session, bogus_domain_id, actor="test-admin")
        await session.commit()

    assert result is None

    async with db_session() as session:
        rows = (
            await session.execute(
                sa.select(AuditLog).where(AuditLog.target_id == bogus_domain_id)
            )
        ).scalars().all()
    assert rows == []


async def test_revoke_domain_on_an_already_revoked_domain_is_a_safe_no_op(_seeded_tenants):
    # Idempotent for the DOMAIN's own state only -- see revoke_domain()'s
    # own comment for why this differs from confirm_verification()'s
    # DomainRevokedError case. A second call must change the domain row
    # nothing further (revoked_at must not move to a new timestamp), but
    # (design decision, 2.2.g/h duplication check, item B1 -- changed from
    # this test's own original "no second audit_log row" assertion) IS now
    # expected to write a SECOND audit_log row for the second attempt, with
    # the second, different actor correctly recorded and a details marker
    # distinguishing it from the real revocation.
    ids = _seeded_tenants
    async with db_session() as session:
        repo = IngestRepository(tenant_id=ids["tenant_a"], session=session)
        domain = await repo.claim_domain("revoke-twice.example")
        await session.commit()
        domain_id = domain.id

    first_now = datetime(2026, 6, 15, 12, 0, 0, tzinfo=UTC)
    async with db_session() as session:
        first_result = await revoke_domain(
            session, domain_id, actor="test-admin", clock=lambda: first_now
        )
        await session.commit()
    assert first_result.status == "revoked"
    assert first_result.revoked_at == first_now

    second_now = datetime(2026, 6, 15, 13, 0, 0, tzinfo=UTC)
    async with db_session() as session:
        second_result = await revoke_domain(
            session, domain_id, actor="a-different-admin", clock=lambda: second_now
        )
        await session.commit()

    assert second_result.status == "revoked"
    # Unchanged -- still the FIRST call's timestamp, not the second's.
    assert second_result.revoked_at == first_now

    async with db_session() as session:
        domain_row = (
            await session.execute(sa.select(VerifiedDomain).where(VerifiedDomain.id == domain_id))
        ).scalar_one()
        audit_rows = (
            (
                await session.execute(
                    sa.select(AuditLog)
                    .where(AuditLog.target_id == domain_id)
                    .order_by(AuditLog.created_at)
                )
            )
            .scalars()
            .all()
        )

    # The domain row itself: still exactly the first revocation's values.
    assert domain_row.status == "revoked"
    assert domain_row.revoked_at == first_now

    # Two audit_log rows now -- one per attempt, each with its own actor.
    assert len(audit_rows) == 2
    assert audit_rows[0].actor == "test-admin"
    assert audit_rows[0].details["already_revoked"] is False
    assert audit_rows[1].actor == "a-different-admin"
    assert audit_rows[1].details["already_revoked"] is True


async def test_revoke_domain_is_deliberately_unscoped_and_can_revoke_any_tenants_domain(
    _seeded_tenants,
):
    # The opposite of every other hostile-caller test in this file (which
    # prove tenant isolation) -- intentionally so, not a regression.
    # revoke_domain() is a platform-admin primitive with no tenant identity
    # of its own; it must be able to reach every tenant's own domains
    # through the exact same unscoped call. Seeds one real domain under
    # EACH of two different tenants and revokes both through the same
    # function, proving neither a tenant_id filter nor a same-tenant
    # requirement exists anywhere in revoke_domain()'s own lookup.
    ids = _seeded_tenants
    async with db_session() as session:
        repo_a = IngestRepository(tenant_id=ids["tenant_a"], session=session)
        repo_b = IngestRepository(tenant_id=ids["tenant_b"], session=session)
        domain_a = await repo_a.claim_domain("tenant-a-cross.example")
        domain_b = await repo_b.claim_domain("tenant-b-cross.example")
        await session.commit()
        domain_a_id = domain_a.id
        domain_b_id = domain_b.id

    async with db_session() as session:
        result_a = await revoke_domain(session, domain_a_id, actor="test-admin")
        result_b = await revoke_domain(session, domain_b_id, actor="test-admin")
        await session.commit()

    assert result_a.status == "revoked"
    assert result_b.status == "revoked"

    async with db_session() as session:
        row_a = (
            await session.execute(sa.select(VerifiedDomain).where(VerifiedDomain.id == domain_a_id))
        ).scalar_one()
        row_b = (
            await session.execute(sa.select(VerifiedDomain).where(VerifiedDomain.id == domain_b_id))
        ).scalar_one()
        audit_a = (
            await session.execute(sa.select(AuditLog).where(AuditLog.target_id == domain_a_id))
        ).scalar_one()
        audit_b = (
            await session.execute(sa.select(AuditLog).where(AuditLog.target_id == domain_b_id))
        ).scalar_one()
    assert row_a.status == "revoked"
    assert row_b.status == "revoked"
    # Duplication check after 2.2.g/h, item C1: the single-tenant test above
    # already asserts the audit row's own tenant_id matches the domain's --
    # but with only one tenant in play there, it can't rule out a bug that
    # always records the SAME (or a hardcoded) tenant_id regardless of which
    # domain was actually revoked. Only a test with two different, real
    # tenants can catch a swap -- proving each domain's own audit row names
    # ITS OWN owning tenant, not the other one's.
    assert audit_a.details["tenant_id"] == str(ids["tenant_a"])
    assert audit_b.details["tenant_id"] == str(ids["tenant_b"])


async def test_revoke_domain_never_persists_the_status_change_alone_if_the_audit_write_fails(
    _seeded_tenants,
):
    # Transaction-atomicity proof for point 2: rather than mocking
    # SQLAlchemy internals, this injects a REAL failure through
    # audit_log.actor's own existing NOT NULL constraint (actor=None) --
    # the audit_log insert fails at flush() exactly where it would for any
    # other reason, exercising revoke_domain()'s own real code path, not a
    # synthetic stand-in for it. If the domain's status update and the
    # audit_log insert were not part of the same flush, this would be able
    # to leave the domain silently "revoked" with no corresponding audit
    # row; this test proves that never happens.
    ids = _seeded_tenants
    async with db_session() as session:
        repo = IngestRepository(tenant_id=ids["tenant_a"], session=session)
        domain = await repo.claim_domain("atomicity-proof.example")
        await session.commit()
        domain_id = domain.id

    async with db_session() as session:
        with pytest.raises(IntegrityError):
            await revoke_domain(session, domain_id, actor=None)
        # Not relied upon for correctness (session.close() at this
        # "async with" block's own exit rolls back any uncommitted,
        # failed transaction regardless) -- made explicit here so this
        # test's own proof doesn't depend on that implicit behavior being
        # read correctly from elsewhere.
        await session.rollback()

    async with db_session() as session:
        domain_row = (
            await session.execute(sa.select(VerifiedDomain).where(VerifiedDomain.id == domain_id))
        ).scalar_one()
        audit_rows = (
            await session.execute(
                sa.select(AuditLog).where(AuditLog.target_id == domain_id)
            )
        ).scalars().all()

    assert domain_row.status == "pending"
    assert domain_row.revoked_at is None
    assert audit_rows == []
