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
from datetime import UTC, datetime, timedelta

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError

from app import qdrant
from app.config import get_settings
from app.ingest import repository as repository_module
from app.ingest import upload_storage
from app.ingest.extract_html import extract_html
from app.ingest.models import AuditLog, DbConnection, Document, Job, Source, VerifiedDomain
from app.ingest.qdrant_writer import HIDDEN_FIELD, embed_and_upsert, finish_ingest
from app.ingest.queue import claim_next_job
from app.ingest.repository import (
    VALID_JOB_TYPES,
    CredentialEncryptionError,
    DomainAlreadyClaimedError,
    DomainNotVerifiedError,
    DomainRevokedError,
    IngestRepository,
    RefreshIntervalExceedsPlanError,
    RefreshNowAllowanceExhaustedError,
    RefreshNowCooldownError,
    SourceDisabledError,
    TenantSuspendedError,
    UnknownJobTypeError,
    UnknownRefreshIntervalError,
    UnknownSourceError,
    flag_source_if_exceeds_plan,
    revoke_domain,
)
from app.ingest.scheduler import get_due_sources
from app.plans import models as plans_models  # noqa: F401 (registers "plans" on Base.metadata)
from app.tenancy.models import Tenant
from tests.conftest import (
    _verify_domain_directly,
    assert_db_connection_credential_round_trip,
    assert_secret_not_in_exception_chain,
    db_session,
    local_http_server,
    patch_embed_dense,
    tracking_handler,
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


# --- Task 2.9.b: source lifecycle primitives -------------------------------
# create_source()/update_source()/disable_source() and the downgrade-
# flagging primitive, flag_source_if_exceeds_plan(). All live against the
# real test-db -- disable_source()'s own proof in particular needs a REAL
# get_due_sources() call (app/ingest/scheduler.py), not a mock, to actually
# demonstrate the three-layer defense-in-depth backstop this task reused
# rather than reinvented (see disable_source()'s own docstring).


async def _create_source(
    tenant_id: uuid.UUID,
    *,
    source_type: str = "urls",
    config: dict | None = None,
    refresh_interval: str = "daily",
) -> uuid.UUID:
    # Task 2.9.g duplication-check fix: the create_source()-then-commit-
    # then-grab-id shape repeated 12 times in this file (confirmed by
    # grep before extracting, well past this project's own 3-occurrence
    # threshold) -- extracted here. Returns only the id, not the ORM
    # object: every real call site already re-queries the row fresh in
    # its own later session anyway (this project's own established
    # "never hold a stale ORM object across a session boundary"
    # discipline), so returning more would invite exactly that misuse.
    # The two call sites inside a `pytest.raises(...)` block (proving
    # create_source() itself REJECTS) and the one batch-creates-three-
    # sources-in-one-shared-transaction isolation test are deliberately
    # NOT converted to this helper -- genuinely different shapes, not an
    # oversight.
    async with db_session() as session:
        repo = IngestRepository(tenant_id=tenant_id, session=session)
        source = await repo.create_source(
            source_type=source_type,
            config=config if config is not None else {"urls": ["https://example.com"]},
            refresh_interval=refresh_interval,
        )
        await session.commit()
        return source.id


async def _index_real_content(
    client,
    collection_name: str,
    *,
    tenant_id: uuid.UUID,
    source_id: uuid.UUID,
    source_url: str,
    html_body: str,
) -> uuid.UUID:
    # Task 2.9.g duplication-check fix: the extract_html()-then-embed_
    # and_upsert()-with-a-fresh-document_id shape repeated 3 times in
    # this file (confirmed by grep before extracting). Returns the
    # generated document_id, since every real call site needs it
    # afterward (asserting on Qdrant payloads/scroll results keyed by it).
    content = extract_html(html_body)
    document_id = uuid.uuid4()
    await embed_and_upsert(
        content,
        client=client,
        collection_name=collection_name,
        tenant_id=tenant_id,
        source_id=source_id,
        source_type="urls",
        document_id=document_id,
        source_url=source_url,
        file_name=None,
        embedding_version="v1",
    )
    return document_id


async def test_create_source_rejects_an_unrecognized_refresh_interval(_seeded_tenants):
    ids = _seeded_tenants
    async with db_session() as session:
        repo = IngestRepository(tenant_id=ids["tenant_a"], session=session)
        with pytest.raises(UnknownRefreshIntervalError, match="hourly"):
            await repo.create_source(
                source_type="urls", config={"urls": ["https://example.com"]},
                refresh_interval="hourly",
            )
        count = (
            await session.execute(sa.select(sa.func.count()).select_from(Source))
        ).scalar_one()
        assert count == 0  # the rejected call left no row behind


async def test_create_source_rejects_an_interval_more_frequent_than_the_plan_allows(
    _seeded_tenants, monkeypatch
):
    ids = _seeded_tenants
    # The Settings-stand-in floor (app/config.py's own refresh_interval_
    # min_allowed), tightened past its generous "daily" default so "daily"
    # itself becomes a real violation -- the plan-exceeding case, distinct
    # from the unrecognized-vocabulary case above.
    monkeypatch.setenv("REFRESH_INTERVAL_MIN_ALLOWED", "weekly")
    get_settings.cache_clear()
    async with db_session() as session:
        repo = IngestRepository(tenant_id=ids["tenant_a"], session=session)
        with pytest.raises(RefreshIntervalExceedsPlanError, match="daily"):
            await repo.create_source(
                source_type="urls", config={"urls": ["https://example.com"]},
                refresh_interval="daily",
            )
        count = (
            await session.execute(sa.select(sa.func.count()).select_from(Source))
        ).scalar_one()
        assert count == 0


async def test_create_source_rejects_a_crawl_type_source_for_an_unverified_domain(
    _seeded_tenants,
):
    # Task 2.10.b [SECURITY]: the §16 bullet-1 fix, primary layer. Before
    # this fix, create_source() never checked domain verification at all
    # -- a crawl-type source for ANY domain, verified or not, could be
    # created and scheduled, and only the per-page WRITE (ingest_url(),
    # inside run_crawl()'s own discovery) was ever rejected, strictly
    # AFTER real, SSRF-guarded but ownership-unproven HTTP traffic
    # (robots.txt, sitemap.xml, every discovered page) had already gone
    # out. This tenant never verifies anything at all -- the domain is
    # not merely absent from `verified_domains`, there IS no such row.
    ids = _seeded_tenants
    hits: list = []
    with local_http_server(tracking_handler({}, hits)) as port:
        seed_url = f"http://127.0.0.1:{port}/"
        async with db_session() as session:
            repo = IngestRepository(tenant_id=ids["tenant_a"], session=session)
            with pytest.raises(DomainNotVerifiedError, match="127.0.0.1"):
                await repo.create_source(
                    source_type="crawl", config={"seed_url": seed_url},
                    refresh_interval="daily",
                )
    assert hits == []  # zero network activity -- rejected before any fetch

    async with db_session() as session:
        source_count = (
            await session.execute(sa.select(sa.func.count()).select_from(Source))
        ).scalar_one()
        job_count = (
            await session.execute(
                sa.select(sa.func.count()).select_from(Job).where(Job.tenant_id == ids["tenant_a"])
            )
        ).scalar_one()
    assert source_count == 0  # the rejected call left no Source row behind
    assert job_count == 0  # and nothing was ever enqueued


async def test_create_source_accepts_a_crawl_type_source_for_a_verified_domain(
    _seeded_tenants,
):
    # The success path, proven alongside the rejection above so the fix
    # is confirmed to reject the right thing, not everything.
    ids = _seeded_tenants
    async with db_session() as session:
        repo = IngestRepository(tenant_id=ids["tenant_a"], session=session)
        domain = await repo.claim_domain("verified-for-crawl.example")
        await session.commit()
        domain_id = domain.id
    await _verify_domain_directly(domain_id)

    async with db_session() as session:
        repo = IngestRepository(tenant_id=ids["tenant_a"], session=session)
        source = await repo.create_source(
            source_type="crawl",
            config={"seed_url": "https://verified-for-crawl.example/"},
            refresh_interval="daily",
        )
        await session.commit()
    assert source.type == "crawl"


async def test_create_source_accepts_a_valid_refresh_interval(_seeded_tenants):
    ids = _seeded_tenants
    source_id = await _create_source(ids["tenant_a"], refresh_interval="weekly")

    async with db_session() as session:
        row = (await session.execute(sa.select(Source).where(Source.id == source_id))).scalar_one()
    assert row.tenant_id == ids["tenant_a"]
    assert row.type == "urls"
    assert row.refresh_interval == "weekly"
    assert row.enabled is True
    assert row.exceeds_plan is False
    assert row.last_run_at is None


async def test_create_source_is_immediately_due_with_no_extra_seeding_call(_seeded_tenants):
    # Decision (confirmed before building, not assumed): create_source()
    # seeds no scheduler state of its own -- get_due_sources() already
    # selects every one of this tenant's own enabled sources, and a fresh
    # row's own `last_run_at IS NULL` already makes it due. Proves that is
    # genuinely true live, with NOTHING else called in between.
    ids = _seeded_tenants
    source_id = await _create_source(ids["tenant_a"])

    async with db_session() as session:
        due = await get_due_sources(session, tenant_id=ids["tenant_a"])
    assert {s.id for s in due} == {source_id}


async def test_update_source_enforces_the_same_refresh_interval_rules_on_change(
    _seeded_tenants, monkeypatch
):
    ids = _seeded_tenants
    source_id = await _create_source(ids["tenant_a"])

    # Unrecognized interval: rejected, existing row untouched.
    async with db_session() as session:
        repo = IngestRepository(tenant_id=ids["tenant_a"], session=session)
        with pytest.raises(UnknownRefreshIntervalError, match="hourly"):
            await repo.update_source(source_id, refresh_interval="hourly")

    # Plan-exceeding interval: rejected the same way create_source() would.
    monkeypatch.setenv("REFRESH_INTERVAL_MIN_ALLOWED", "weekly")
    get_settings.cache_clear()
    async with db_session() as session:
        repo = IngestRepository(tenant_id=ids["tenant_a"], session=session)
        with pytest.raises(RefreshIntervalExceedsPlanError, match="daily"):
            await repo.update_source(source_id, refresh_interval="daily")

    async with db_session() as session:
        unchanged = (
            await session.execute(sa.select(Source).where(Source.id == source_id))
        ).scalar_one()
    assert unchanged.refresh_interval == "daily"  # both rejections left it alone

    # A valid change (still within the now-tightened "weekly" floor) applies.
    async with db_session() as session:
        repo = IngestRepository(tenant_id=ids["tenant_a"], session=session)
        updated = await repo.update_source(
            source_id, config={"urls": ["https://example.com/new"]}, refresh_interval="weekly"
        )
        await session.commit()
    assert updated.refresh_interval == "weekly"
    assert updated.config == {"urls": ["https://example.com/new"]}


async def test_disable_source_stops_it_from_being_returned_as_due(
    _seeded_tenants, live_test_services
):
    ids = _seeded_tenants
    _, client, collection_name = live_test_services
    await qdrant.ensure_collection(client, collection_name)
    async with db_session() as session:
        never_run = Source(
            tenant_id=ids["tenant_a"], type="urls", config={}, refresh_interval="daily",
            enabled=True, status="active", last_run_at=None,
        )
        stale = Source(
            tenant_id=ids["tenant_a"], type="urls", config={}, refresh_interval="daily",
            enabled=True, status="active",
            last_run_at=datetime.now(UTC) - timedelta(days=30),
        )
        session.add_all([never_run, stale])
        await session.commit()
        never_run_id, stale_id = never_run.id, stale.id

    # Before disabling: both are due (NULL and stale last_run_at alike).
    async with db_session() as session:
        due_before = await get_due_sources(session, tenant_id=ids["tenant_a"])
    assert {s.id for s in due_before} == {never_run_id, stale_id}

    async with db_session() as session:
        repo = IngestRepository(tenant_id=ids["tenant_a"], session=session)
        disabled = await repo.disable_source(
            stale_id, client=client, collection_name=collection_name
        )
        await session.commit()
    assert disabled.enabled is False

    # After disabling: get_due_sources() -- the real structural backstop,
    # reusing its existing Source.enabled.is_(True) filter with zero query
    # changes -- never returns it, regardless of its stale last_run_at.
    async with db_session() as session:
        due_after = await get_due_sources(session, tenant_id=ids["tenant_a"])
    assert {s.id for s in due_after} == {never_run_id}


async def test_disable_source_raises_for_a_source_belonging_to_a_different_tenant(
    _seeded_tenants, live_test_services
):
    ids = _seeded_tenants
    # UnknownSourceError is raised by _get_owned_source() BEFORE this
    # method ever touches Qdrant (confirmed by reading disable_source()'s
    # own code: the hide_points_for_source() call is reached only after
    # the owned-source lookup succeeds) -- client/collection_name are
    # still the real ones from live_test_services, not a stand-in, since
    # nothing about this test's own proof depends on them being unusable.
    _, client, collection_name = live_test_services
    async with db_session() as session:
        source = Source(
            tenant_id=ids["tenant_a"], type="urls", config={}, refresh_interval="daily",
            enabled=True, status="active",
        )
        session.add(source)
        await session.commit()
        source_id = source.id

    async with db_session() as session:
        repo = IngestRepository(tenant_id=ids["tenant_b"], session=session)
        with pytest.raises(UnknownSourceError, match=str(source_id)):
            await repo.disable_source(source_id, client=client, collection_name=collection_name)


async def test_flag_source_if_exceeds_plan_flags_without_disabling_or_touching_scheduling(
    _seeded_tenants, monkeypatch
):
    ids = _seeded_tenants
    last_run_at = datetime.now(UTC) - timedelta(days=5)
    async with db_session() as session:
        source = Source(
            tenant_id=ids["tenant_a"], type="urls", config={}, refresh_interval="daily",
            enabled=True, status="active", last_run_at=last_run_at,
        )
        session.add(source)
        await session.commit()
        source_id = source.id

    # Created under today's generous "daily" default -- no violation yet.
    async with db_session() as session:
        result = await session.execute(sa.select(Source).where(Source.id == source_id))
        source = result.scalar_one()
        exceeds = flag_source_if_exceeds_plan(source)
        await session.commit()
    assert exceeds is False
    assert source.exceeds_plan is False

    # A hypothetical stricter plan (a downgrade, simulated the only way
    # this task's own Settings-stand-in can be): the SAME existing source,
    # re-checked, is now flagged -- disable_source() is never called, and
    # neither enabled nor last_run_at move.
    monkeypatch.setenv("REFRESH_INTERVAL_MIN_ALLOWED", "weekly")
    get_settings.cache_clear()
    async with db_session() as session:
        result = await session.execute(sa.select(Source).where(Source.id == source_id))
        source = result.scalar_one()
        exceeds = flag_source_if_exceeds_plan(source)
        await session.commit()
    assert exceeds is True

    async with db_session() as session:
        row = (await session.execute(sa.select(Source).where(Source.id == source_id))).scalar_one()
    assert row.exceeds_plan is True
    assert row.enabled is True  # not disabled
    assert row.last_run_at == last_run_at  # scheduling untouched


# --- Task 2.9.c: refresh-now (request_refresh_now()) -----------------------
# All live against the real test-db -- the daily-allowance/cooldown tests
# use request_refresh_now()'s own `clock` parameter to control elapsed time
# deterministically, matching get_due_sources()'s own identical precedent
# (2.9.a), rather than real sleep() calls.


async def test_request_refresh_now_enqueues_a_real_job_when_both_checks_pass(_seeded_tenants):
    ids = _seeded_tenants
    now = datetime(2026, 1, 15, 12, 0, 0, tzinfo=UTC)
    source_id = await _create_source(ids["tenant_a"])

    async with db_session() as session:
        repo = IngestRepository(tenant_id=ids["tenant_a"], session=session)
        job = await repo.request_refresh_now(source_id, clock=lambda: now)
        await session.commit()
        job_id = job.id

    async with db_session() as session:
        job_row = (await session.execute(sa.select(Job).where(Job.id == job_id))).scalar_one()
        source_row = (
            await session.execute(sa.select(Source).where(Source.id == source_id))
        ).scalar_one()
    assert job_row.source_id == source_id
    assert job_row.job_type == "ingest_url"
    assert job_row.status == "pending"
    assert source_row.last_refresh_requested_at == now
    assert source_row.refresh_now_count == 1
    assert source_row.refresh_now_window_started_at == now


async def test_request_refresh_now_rejects_within_the_cooldown_window(_seeded_tenants):
    ids = _seeded_tenants
    t0 = datetime(2026, 1, 15, 12, 0, 0, tzinfo=UTC)
    source_id = await _create_source(ids["tenant_a"])

    async with db_session() as session:
        repo = IngestRepository(tenant_id=ids["tenant_a"], session=session)
        await repo.request_refresh_now(source_id, clock=lambda: t0)
        await session.commit()

    # Settings.refresh_now_cooldown_seconds defaults to 60s -- 10s later is
    # still well inside the cooldown window.
    async with db_session() as session:
        repo = IngestRepository(tenant_id=ids["tenant_a"], session=session)
        with pytest.raises(RefreshNowCooldownError, match=str(source_id)):
            await repo.request_refresh_now(
                source_id, clock=lambda: t0 + timedelta(seconds=10)
            )

    async with db_session() as session:
        jobs = (
            await session.execute(sa.select(Job).where(Job.source_id == source_id))
        ).scalars().all()
        source_row = (
            await session.execute(sa.select(Source).where(Source.id == source_id))
        ).scalar_one()
    assert len(jobs) == 1  # the rejected second call enqueued nothing
    assert source_row.refresh_now_count == 1  # and left the row untouched
    assert source_row.last_refresh_requested_at == t0


async def test_request_refresh_now_rejects_once_exhausted_then_allows_again_after_the_window_resets(
    _seeded_tenants, monkeypatch
):
    ids = _seeded_tenants
    monkeypatch.setenv("REFRESH_NOW_COOLDOWN_SECONDS", "1")
    monkeypatch.setenv("REFRESH_NOW_DAILY_ALLOWANCE", "2")
    get_settings.cache_clear()

    t0 = datetime(2026, 1, 15, 12, 0, 0, tzinfo=UTC)
    source_id = await _create_source(ids["tenant_a"])

    # Two requests, spaced past the 1s cooldown, both within the allowance.
    async with db_session() as session:
        repo = IngestRepository(tenant_id=ids["tenant_a"], session=session)
        await repo.request_refresh_now(source_id, clock=lambda: t0)
        await session.commit()
    async with db_session() as session:
        repo = IngestRepository(tenant_id=ids["tenant_a"], session=session)
        await repo.request_refresh_now(source_id, clock=lambda: t0 + timedelta(seconds=2))
        await session.commit()

    # A third, still past cooldown, but the allowance (2) is now exhausted.
    async with db_session() as session:
        repo = IngestRepository(tenant_id=ids["tenant_a"], session=session)
        with pytest.raises(RefreshNowAllowanceExhaustedError, match=str(source_id)):
            await repo.request_refresh_now(source_id, clock=lambda: t0 + timedelta(seconds=4))

    async with db_session() as session:
        source_row = (
            await session.execute(sa.select(Source).where(Source.id == source_id))
        ).scalar_one()
    assert source_row.refresh_now_count == 2  # the rejected third call changed nothing

    # A fourth, more than 24h after the window started (t0) -- the window
    # has expired, so this is correctly allowed again, with a fresh count.
    async with db_session() as session:
        repo = IngestRepository(tenant_id=ids["tenant_a"], session=session)
        job = await repo.request_refresh_now(
            source_id, clock=lambda: t0 + timedelta(hours=25)
        )
        await session.commit()
        job_id = job.id

    async with db_session() as session:
        job_row = (await session.execute(sa.select(Job).where(Job.id == job_id))).scalar_one()
        source_row = (
            await session.execute(sa.select(Source).where(Source.id == source_id))
        ).scalar_one()
    assert job_row.status == "pending"
    assert source_row.refresh_now_count == 1
    assert source_row.refresh_now_window_started_at == t0 + timedelta(hours=25)


async def test_request_refresh_now_rejects_a_disabled_source_and_enqueues_nothing(
    _seeded_tenants, live_test_services, monkeypatch
):
    # Task 2.9.g [SECURITY] duplication-check fix D.1: before this fix,
    # request_refresh_now() never checked source.enabled at all, so a
    # disabled (soft-hidden) source's content could be made answerable
    # again via this call alone, with no re-enable step. Proven live, not
    # just unit-level, because disable_source() itself needs a real Qdrant
    # client to hide the source's points first -- the same live pattern as
    # the disable_source() tests just above.
    patch_embed_dense(monkeypatch)
    ids = _seeded_tenants
    _, client, collection_name = live_test_services
    await qdrant.ensure_collection(client, collection_name)

    source_id = await _create_source(ids["tenant_a"])

    async with db_session() as session:
        repo = IngestRepository(tenant_id=ids["tenant_a"], session=session)
        await repo.disable_source(source_id, client=client, collection_name=collection_name)
        await session.commit()

    async with db_session() as session:
        repo = IngestRepository(tenant_id=ids["tenant_a"], session=session)
        with pytest.raises(SourceDisabledError, match=str(source_id)):
            await repo.request_refresh_now(source_id)

    async with db_session() as session:
        jobs = (
            await session.execute(sa.select(Job).where(Job.source_id == source_id))
        ).scalars().all()
        source_row = (
            await session.execute(sa.select(Source).where(Source.id == source_id))
        ).scalar_one()
    assert jobs == []  # no refresh job was enqueued for the disabled source
    assert source_row.last_refresh_requested_at is None  # and the row is untouched


async def test_request_refresh_now_rejects_a_suspended_tenants_source_and_enqueues_nothing(
    _seeded_tenants,
):
    # Task 2.9.g [SECURITY], Finding 1 (Part 2 close-out review): the
    # scheduled path is protected by three independent layers against a
    # suspended tenant's background ingestion continuing (Task 2.9.a) --
    # request_refresh_now() went through none of them. No live services
    # needed here (unlike the disabled-source proof above): suspending a
    # tenant is a plain Postgres UPDATE, no Qdrant involved at all.
    ids = _seeded_tenants
    source_id = await _create_source(ids["tenant_a"])

    async with db_session() as session:
        await session.execute(
            sa.update(Tenant).where(Tenant.id == ids["tenant_a"]).values(status="suspended")
        )
        await session.commit()

    async with db_session() as session:
        repo = IngestRepository(tenant_id=ids["tenant_a"], session=session)
        with pytest.raises(TenantSuspendedError, match=str(ids["tenant_a"])):
            await repo.request_refresh_now(source_id)

    async with db_session() as session:
        jobs = (
            await session.execute(sa.select(Job).where(Job.source_id == source_id))
        ).scalars().all()
        source_row = (
            await session.execute(sa.select(Source).where(Source.id == source_id))
        ).scalar_one()
    assert jobs == []  # no refresh job was enqueued for the suspended tenant's source
    assert source_row.last_refresh_requested_at is None  # and the row is untouched


# --- Task 2.9.e [SECURITY]: source deletion/disable side effects -----------
# Live against both real services together (test-db + test-qdrant), the
# first tests in this file to need Qdrant at all -- a real, necessary
# consequence of widening disable_source() (2.9.b) to also touch Qdrant,
# flagged plainly rather than silently worked around: the two existing
# disable_source() tests above were widened to pass a real client/
# collection_name too (UnknownSourceError's own test needs one in name
# only -- confirmed by reading disable_source()'s own code that the
# Qdrant call is never reached on that path -- but passes the real one
# anyway rather than an untyped stand-in).


async def test_delete_source_removes_qdrant_points_and_documents_rows(
    _seeded_tenants, live_test_services, monkeypatch
):
    patch_embed_dense(monkeypatch)
    ids = _seeded_tenants
    _, client, collection_name = live_test_services
    await qdrant.ensure_collection(client, collection_name)

    source_id = await _create_source(
        ids["tenant_a"], config={"urls": ["https://delete-proof.example/page"]}
    )

    content = extract_html(
        "<html><head><title>Delete Proof</title></head><body>"
        "<h2>Section</h2><p>real content that must be hard-deleted</p>"
        "</body></html>"
    )
    async with db_session() as session:
        await finish_ingest(
            session,
            client,
            collection_name,
            content,
            tenant_id=ids["tenant_a"],
            source_id=source_id,
            source_type="urls",
            url="https://delete-proof.example/page",
            file_name=None,
            embedding_version="v1",
        )
        await session.commit()

    count_before = await client.count(collection_name=collection_name)
    assert count_before.count >= 1
    async with db_session() as session:
        docs_before = (
            await session.execute(sa.select(Document).where(Document.source_id == source_id))
        ).scalars().all()
    assert len(docs_before) == 1

    async with db_session() as session:
        repo = IngestRepository(tenant_id=ids["tenant_a"], session=session)
        await repo.delete_source(source_id, client=client, collection_name=collection_name)
        await session.commit()

    # The Source row itself is gone.
    async with db_session() as session:
        remaining_source = (
            await session.execute(sa.select(Source).where(Source.id == source_id))
        ).scalar_one_or_none()
    assert remaining_source is None

    # Its documents row(s) cascaded away at the database level.
    async with db_session() as session:
        docs_after = (
            await session.execute(sa.select(Document).where(Document.source_id == source_id))
        ).scalars().all()
    assert docs_after == []

    # Its Qdrant points are hard-deleted, confirmed via a real query.
    records, _ = await client.scroll(
        collection_name=collection_name,
        scroll_filter=qdrant.tenant_filter(ids["tenant_a"]),
        limit=20,
    )
    assert records == []


async def test_delete_source_removes_the_on_disk_file_for_an_upload_type_source(
    _seeded_tenants, live_test_services, monkeypatch, tmp_path
):
    patch_embed_dense(monkeypatch)
    ids = _seeded_tenants
    _, client, collection_name = live_test_services
    await qdrant.ensure_collection(client, collection_name)

    storage_dir = tmp_path / "uploads"
    upload_id = upload_storage.save(
        b"real upload bytes", storage_dir=storage_dir, max_size_bytes=1024
    )
    stored_path = storage_dir / str(upload_id)
    assert stored_path.exists()  # confirms the real file exists before deletion

    monkeypatch.setenv("UPLOAD_STORAGE_PATH", str(storage_dir))
    get_settings.cache_clear()

    source_id = await _create_source(
        ids["tenant_a"],
        source_type="upload",
        config={"uploads": [{"upload_id": str(upload_id), "original_filename": "proof.txt"}]},
    )

    async with db_session() as session:
        repo = IngestRepository(tenant_id=ids["tenant_a"], session=session)
        await repo.delete_source(source_id, client=client, collection_name=collection_name)
        await session.commit()

    assert not stored_path.exists()


async def test_disable_source_hides_points_without_deleting_them(
    _seeded_tenants, live_test_services, monkeypatch
):
    patch_embed_dense(monkeypatch)
    ids = _seeded_tenants
    _, client, collection_name = live_test_services
    await qdrant.ensure_collection(client, collection_name)

    source_id = await _create_source(
        ids["tenant_a"], config={"urls": ["https://disable-proof.example/page"]}
    )

    await _index_real_content(
        client,
        collection_name,
        tenant_id=ids["tenant_a"],
        source_id=source_id,
        source_url="https://disable-proof.example/page",
        html_body=(
            "<html><head><title>Disable Proof</title></head><body>"
            "<h2>Section</h2><p>real content that must stay present but hidden</p>"
            "</body></html>"
        ),
    )

    async with db_session() as session:
        repo = IngestRepository(tenant_id=ids["tenant_a"], session=session)
        disabled = await repo.disable_source(
            source_id, client=client, collection_name=collection_name
        )
        await session.commit()
    assert disabled.enabled is False

    # Present, not deleted -- then flagged hidden, read directly from the
    # real payload, not inferred from retrieve() (which does not exist).
    records, _ = await client.scroll(
        collection_name=collection_name,
        scroll_filter=qdrant.tenant_filter(ids["tenant_a"]),
        limit=20,
    )
    assert len(records) >= 1
    assert all(record.payload.get(HIDDEN_FIELD) is True for record in records)


async def test_enable_source_unhides_points_with_zero_re_ingestion(
    _seeded_tenants, live_test_services, monkeypatch
):
    patch_embed_dense(monkeypatch)
    ids = _seeded_tenants
    _, client, collection_name = live_test_services
    await qdrant.ensure_collection(client, collection_name)

    source_id = await _create_source(
        ids["tenant_a"], config={"urls": ["https://enable-proof.example/page"]}
    )

    await _index_real_content(
        client,
        collection_name,
        tenant_id=ids["tenant_a"],
        source_id=source_id,
        source_url="https://enable-proof.example/page",
        html_body=(
            "<html><head><title>Enable Proof</title></head><body>"
            "<h2>Section</h2><p>real content that must be un-hidden, not re-ingested</p>"
            "</body></html>"
        ),
    )
    points_before, _ = await client.scroll(
        collection_name=collection_name,
        scroll_filter=qdrant.tenant_filter(ids["tenant_a"]),
        limit=20,
    )
    point_ids_before = {record.id for record in points_before}

    async with db_session() as session:
        repo = IngestRepository(tenant_id=ids["tenant_a"], session=session)
        await repo.disable_source(source_id, client=client, collection_name=collection_name)
        await session.commit()

    async with db_session() as session:
        repo = IngestRepository(tenant_id=ids["tenant_a"], session=session)
        enabled = await repo.enable_source(
            source_id, client=client, collection_name=collection_name
        )
        await session.commit()
    assert enabled.enabled is True

    records, _ = await client.scroll(
        collection_name=collection_name,
        scroll_filter=qdrant.tenant_filter(ids["tenant_a"]),
        limit=20,
    )
    # The exact same points -- zero re-ingestion, not a fresh set.
    assert {record.id for record in records} == point_ids_before
    assert all(HIDDEN_FIELD not in record.payload for record in records)


async def test_delete_and_disable_source_never_touch_a_different_sources_content(
    _seeded_tenants, live_test_services, monkeypatch
):
    # Both axes this project always proves for a deletion/isolation-shaped
    # operation: a DIFFERENT TENANT's content (source_b, tenant_b) and a
    # DIFFERENT SOURCE belonging to the SAME tenant (source_a2, tenant_a)
    # -- only source_a1 (tenant_a) is ever disabled/deleted.
    patch_embed_dense(monkeypatch)
    ids = _seeded_tenants
    _, client, collection_name = live_test_services
    await qdrant.ensure_collection(client, collection_name)

    async with db_session() as session:
        repo_a = IngestRepository(tenant_id=ids["tenant_a"], session=session)
        source_a1 = await repo_a.create_source(
            source_type="urls", config={"urls": ["https://isolation-proof.example/a1"]},
            refresh_interval="daily",
        )
        source_a2 = await repo_a.create_source(
            source_type="urls", config={"urls": ["https://isolation-proof.example/a2"]},
            refresh_interval="daily",
        )
        repo_b = IngestRepository(tenant_id=ids["tenant_b"], session=session)
        source_b = await repo_b.create_source(
            source_type="urls", config={"urls": ["https://isolation-proof.example/b"]},
            refresh_interval="daily",
        )
        await session.commit()
        source_a1_id, source_a2_id, source_b_id = source_a1.id, source_a2.id, source_b.id

    for tenant_id, source_id, suffix in (
        (ids["tenant_a"], source_a1_id, "a1"),
        (ids["tenant_a"], source_a2_id, "a2"),
        (ids["tenant_b"], source_b_id, "b"),
    ):
        await _index_real_content(
            client,
            collection_name,
            tenant_id=tenant_id,
            source_id=source_id,
            source_url=f"https://isolation-proof.example/{suffix}",
            html_body=(
                f"<html><head><title>{suffix}</title></head><body><h2>S</h2>"
                f"<p>content for source {suffix}, must only ever be touched by "
                "an operation against its own source_id</p></body></html>"
            ),
        )

    # Only source_a1 (tenant_a) is disabled, then deleted.
    async with db_session() as session:
        repo_a = IngestRepository(tenant_id=ids["tenant_a"], session=session)
        await repo_a.disable_source(source_a1_id, client=client, collection_name=collection_name)
        await repo_a.delete_source(source_a1_id, client=client, collection_name=collection_name)
        await session.commit()

    # source_a2 (same tenant, different source) and source_b (different
    # tenant) are both completely untouched: still enabled, their own
    # points still present and never flagged hidden.
    async with db_session() as session:
        source_a2_row = (
            await session.execute(sa.select(Source).where(Source.id == source_a2_id))
        ).scalar_one()
        source_b_row = (
            await session.execute(sa.select(Source).where(Source.id == source_b_id))
        ).scalar_one()
    assert source_a2_row.enabled is True
    assert source_b_row.enabled is True

    records, _ = await client.scroll(
        collection_name=collection_name,
        scroll_filter=qdrant.tenant_filter(ids["tenant_a"]),
        limit=20,
    )
    remaining_source_ids = {record.payload["source_id"] for record in records}
    assert remaining_source_ids == {str(source_a2_id)}  # source_a1's own points are gone
    assert all(HIDDEN_FIELD not in record.payload for record in records)

    records_b, _ = await client.scroll(
        collection_name=collection_name,
        scroll_filter=qdrant.tenant_filter(ids["tenant_b"]),
        limit=20,
    )
    assert len(records_b) >= 1
    assert all(record.payload["source_id"] == str(source_b_id) for record in records_b)
    assert all(HIDDEN_FIELD not in record.payload for record in records_b)


# --- Step 6.3 fix: update_source() url-list staleness --------------------


async def test_update_source_removing_a_url_deletes_its_document_and_qdrant_points(
    _seeded_tenants, live_test_services, monkeypatch
):
    patch_embed_dense(monkeypatch)
    ids = _seeded_tenants
    _, client, collection_name = live_test_services
    await qdrant.ensure_collection(client, collection_name)

    kept_url = "https://url-staleness-proof.example/keep"
    dropped_url = "https://url-staleness-proof.example/drop"
    source_id = await _create_source(
        ids["tenant_a"], config={"urls": [kept_url, dropped_url]}
    )

    content = extract_html(
        "<html><head><title>T</title></head><body>"
        "<p>real content, long enough to chunk</p></body></html>"
    )
    async with db_session() as session:
        for url in (kept_url, dropped_url):
            await finish_ingest(
                session,
                client,
                collection_name,
                content,
                tenant_id=ids["tenant_a"],
                source_id=source_id,
                source_type="urls",
                url=url,
                file_name=None,
                embedding_version="v1",
            )
        await session.commit()

    async with db_session() as session:
        docs_before = (
            await session.execute(sa.select(Document).where(Document.source_id == source_id))
        ).scalars().all()
    assert {d.url for d in docs_before} == {kept_url, dropped_url}
    dropped_doc_id = next(d.id for d in docs_before if d.url == dropped_url)

    # The real fix: update_source() with `dropped_url` removed from the
    # list, given a real client/collection_name.
    async with db_session() as session:
        repo = IngestRepository(tenant_id=ids["tenant_a"], session=session)
        await repo.update_source(
            source_id,
            config={"urls": [kept_url]},
            client=client,
            collection_name=collection_name,
        )
        await session.commit()

    async with db_session() as session:
        docs_after = (
            await session.execute(sa.select(Document).where(Document.source_id == source_id))
        ).scalars().all()
    assert {d.url for d in docs_after} == {kept_url}

    # The dropped URL's own Qdrant points are gone...
    records, _ = await client.scroll(
        collection_name=collection_name,
        scroll_filter=qdrant.Filter(
            must=[
                qdrant.FieldCondition(
                    key="doc_id", match=qdrant.MatchValue(value=str(dropped_doc_id))
                )
            ]
        ),
        limit=20,
    )
    assert records == []

    # ...but the KEPT url's own points survive untouched.
    kept_records, _ = await client.scroll(
        collection_name=collection_name,
        scroll_filter=qdrant.tenant_filter(ids["tenant_a"]),
        limit=20,
    )
    assert len(kept_records) >= 1
    assert all(r.payload["source_url"] == kept_url for r in kept_records)


async def test_update_source_without_client_does_not_attempt_cleanup(
    _seeded_tenants,
):
    # The refresh_interval-only call shape (no client/collection_name) --
    # confirmed to still work exactly as before this fix, never
    # attempting the new cleanup logic (which would crash without a
    # real client).
    ids = _seeded_tenants
    source_id = await _create_source(
        ids["tenant_a"], config={"urls": ["https://example.com/a", "https://example.com/b"]}
    )
    async with db_session() as session:
        repo = IngestRepository(tenant_id=ids["tenant_a"], session=session)
        updated = await repo.update_source(source_id, config={"urls": ["https://example.com/a"]})
        await session.commit()
    assert updated.config == {"urls": ["https://example.com/a"]}


# --- Step 6.3 (Felix decision B): delete_source() db_connections cleanup --


async def test_delete_source_cleans_up_its_own_unreferenced_db_connection(
    _seeded_tenants, live_test_services
):
    ids = _seeded_tenants
    _, client, collection_name = live_test_services
    await qdrant.ensure_collection(client, collection_name)
    async with db_session() as session:
        repo = IngestRepository(tenant_id=ids["tenant_a"], session=session)
        db_connection = await repo.create_db_connection(
            host="db.example.internal",
            credentials={"database": "d", "user": "u", "password": "p"},  # noqa: S106
        )
        await session.flush()
        source = Source(
            tenant_id=ids["tenant_a"],
            type="database",
            config={"db_connection_id": str(db_connection.id)},
            refresh_interval="daily",
            enabled=True,
            status="active",
        )
        session.add(source)
        await session.commit()
        source_id, db_connection_id = source.id, db_connection.id

    async with db_session() as session:
        repo = IngestRepository(tenant_id=ids["tenant_a"], session=session)
        await repo.delete_source(source_id, client=client, collection_name=collection_name)
        await session.commit()

    async with db_session() as session:
        remaining = (
            await session.execute(
                sa.select(DbConnection).where(DbConnection.id == db_connection_id)
            )
        ).scalar_one_or_none()
    assert remaining is None


async def test_delete_source_keeps_a_db_connection_still_referenced_by_another_source(
    _seeded_tenants, live_test_services
):
    ids = _seeded_tenants
    _, client, collection_name = live_test_services
    await qdrant.ensure_collection(client, collection_name)
    async with db_session() as session:
        repo = IngestRepository(tenant_id=ids["tenant_a"], session=session)
        db_connection = await repo.create_db_connection(
            host="db.example.internal",
            credentials={"database": "d", "user": "u", "password": "p"},  # noqa: S106
        )
        await session.flush()
        source_a = Source(
            tenant_id=ids["tenant_a"],
            type="database",
            config={"db_connection_id": str(db_connection.id)},
            refresh_interval="daily",
            enabled=True,
            status="active",
        )
        source_b = Source(
            tenant_id=ids["tenant_a"],
            type="database",
            config={"db_connection_id": str(db_connection.id)},
            refresh_interval="daily",
            enabled=True,
            status="active",
        )
        session.add_all([source_a, source_b])
        await session.commit()
        source_a_id, db_connection_id = source_a.id, db_connection.id

    async with db_session() as session:
        repo = IngestRepository(tenant_id=ids["tenant_a"], session=session)
        await repo.delete_source(source_a_id, client=client, collection_name=collection_name)
        await session.commit()

    async with db_session() as session:
        remaining = (
            await session.execute(
                sa.select(DbConnection).where(DbConnection.id == db_connection_id)
            )
        ).scalar_one_or_none()
    assert remaining is not None
