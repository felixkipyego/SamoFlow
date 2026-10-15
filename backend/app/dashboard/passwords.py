# backend/app/dashboard/passwords.py
# Step 6.1 [SECURITY]: the argon2id password-hashing configuration
# (docs/SPEC.md §12), built on fastapi-users' own PasswordHelper/pwdlib
# stack (see app/dashboard/auth_backend.py's own header comment for the
# full "why a maintained auth library" reasoning) rather than calling
# argon2-cffi directly -- one hashing stack, not two parallel ones.
#
# Deliberately only ONE hasher in the chain (Argon2Hasher), not pwdlib's
# own usual (Argon2Hasher, BcryptHasher) pair: this is a fresh v1 system
# with no legacy bcrypt hashes anywhere to migrate from, and the
# adversarial-pass requirement this task names explicitly ("argon2
# parameters (no downgrade)") reads most naturally as "no weaker verifier
# anywhere in the chain at all" -- PasswordHash.hash() always uses the
# first hasher regardless of how many are listed, but verify_and_update()
# accepts a match against ANY listed hasher, so leaving BcryptHasher in
# would mean a bcrypt-shaped hash (however it got into the password_hash
# column) would still verify. The only real path to writing that column
# is PasswordHelper.hash() itself (always Argon2 here), so this is
# defense in depth, not a fix for an exploitable gap -- cheap enough to
# include given the rule 11 cost (one fewer import, no behavior to keep
# in sync) is also small.
from argon2 import Type
from fastapi_users.password import PasswordHelper
from pwdlib import PasswordHash
from pwdlib.hashers.argon2 import Argon2Hasher

# Explicit, not relied on implicitly: both pwdlib's own Argon2Hasher
# default and argon2-cffi's own PasswordHasher default already match
# these exact numbers today (re-verified against both installed
# libraries before pinning) -- named here anyway so a future upstream
# default change cannot silently move this project's own parameters.
# OWASP's 2026 minimum: memory>=19456 KiB, time>=2, parallelism>=1 --
# comfortably exceeded.
ARGON2_TIME_COST = 3
ARGON2_MEMORY_COST = 65536
ARGON2_PARALLELISM = 4

password_helper = PasswordHelper(
    PasswordHash(
        (
            Argon2Hasher(
                time_cost=ARGON2_TIME_COST,
                memory_cost=ARGON2_MEMORY_COST,
                parallelism=ARGON2_PARALLELISM,
                type=Type.ID,
            ),
        )
    )
)
