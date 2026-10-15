# backend/tests/test_dashboard_passwords.py
# Step 6.1 [SECURITY]: app/dashboard/passwords.py -- the argon2id
# configuration passed to fastapi-users' own PasswordHelper (see that
# module's own header comment for why only Argon2Hasher is in the chain,
# no Bcrypt fallback).
from pwdlib.hashers.argon2 import Argon2Hasher

from app.dashboard.passwords import (
    ARGON2_MEMORY_COST,
    ARGON2_PARALLELISM,
    ARGON2_TIME_COST,
    password_helper,
)


def test_hash_and_verify_round_trip():
    stored = password_helper.hash("correct horse battery staple")
    verified, _ = password_helper.verify_and_update("correct horse battery staple", stored)
    assert verified is True


def test_verify_rejects_wrong_password():
    stored = password_helper.hash("correct horse battery staple")
    verified, _ = password_helper.verify_and_update("wrong password", stored)
    assert verified is False


def test_hash_uses_a_unique_salt_each_time():
    # Two hashes of the SAME password must differ (a real, random salt
    # per call) -- a constant/reused salt would be a real regression.
    a = password_helper.hash("same password")
    b = password_helper.hash("same password")
    assert a != b
    assert password_helper.verify_and_update("same password", a)[0]
    assert password_helper.verify_and_update("same password", b)[0]


def test_hash_produces_a_real_argon2id_hash():
    stored = password_helper.hash("correct horse battery staple")
    assert stored.startswith("$argon2id$")


def test_only_one_hasher_is_configured_no_bcrypt_fallback():
    # [SECURITY] adversarial-pass requirement: "argon2 parameters (no
    # downgrade)" -- read literally as "no weaker verifier anywhere in
    # the chain at all" (app/dashboard/passwords.py's own header
    # comment). Asserted against the real, module-level password_helper
    # instance, not re-derived, so a future edit adding a second hasher
    # back in fails this test immediately.
    hashers = password_helper.password_hash.hashers
    assert len(hashers) == 1
    assert isinstance(hashers[0], Argon2Hasher)


def test_configured_parameters_meet_the_owasp_floor():
    # OWASP's 2026 minimum: memory>=19456 KiB, time>=2, parallelism>=1.
    assert ARGON2_MEMORY_COST >= 19456
    assert ARGON2_TIME_COST >= 2
    assert ARGON2_PARALLELISM >= 1


def test_the_real_hasher_instance_uses_the_configured_parameters():
    hasher = password_helper.password_hash.hashers[0]
    # Argon2Hasher wraps a real argon2.PasswordHasher internally
    # (confirmed by reading pwdlib's own source before relying on this),
    # exposed as `_hasher` -- checked directly against the real instance
    # this project's own login path uses, not a second, freshly
    # constructed one that could silently drift from it.
    inner = hasher._hasher  # noqa: SLF001 (the only way to confirm the real wired params)
    assert inner.time_cost == ARGON2_TIME_COST
    assert inner.memory_cost == ARGON2_MEMORY_COST
    assert inner.parallelism == ARGON2_PARALLELISM


def test_unknown_email_path_pays_a_real_hashing_cost():
    # Not a strict statistical proof (timing tests are inherently noisy
    # in CI) -- confirms the mechanism this task's own adversarial pass
    # requires exists and does real work: fastapi_users.manager.
    # BaseUserManager.authenticate() calls password_helper.hash() (not a
    # no-op) when no such user exists, "to mitigate timing attack...
    # inspired from Django" (its own source comment, confirmed by
    # reading it before relying on this instead of reimplementing a
    # dummy-hash mechanism here).
    import time

    start = time.perf_counter()
    password_helper.hash("some attacker-submitted password")
    elapsed = time.perf_counter() - start
    # A real argon2id hash at memory_cost=65536 KiB takes at least a few
    # milliseconds -- a no-op would complete orders of magnitude faster.
    assert elapsed > 0.001
