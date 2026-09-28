# backend/app/auth/secrets.py
# Task 1.4.c: visitor-secret generation, hashing and verification
# (docs/SPEC.md §4.2: "generate a random 256-bit visitor_secret, store only
# its SHA-256 hash"). Pure functions only -- nothing here touches the
# database or the environment. 1.4.e calls hash_visitor_secret() when
# creating a new visitor and verify_visitor_secret() when looking one up.
#
# This module is named `secrets.py`, the same as the stdlib module it
# wraps. Confirmed safe under Python 3's absolute-import rule (verified
# live: `import secrets` below resolves to the stdlib module, not to
# itself -- this file's own directory is never added to sys.path, it is
# only ever reached via the `app.auth.secrets` package path) rather than
# assumed from general Python knowledge.
import hashlib
import hmac
import secrets

# 32 raw bytes = 256 bits (docs/SPEC.md §4.2).
_SECRET_BYTES = 32


def generate_visitor_secret() -> str:
    # token_urlsafe() base64url-encodes with no padding -- URL-safe and
    # directly usable both in the JSON session response and as the widget's
    # own localStorage value (docs/SPEC.md §4.3's "Storage" rule).
    return secrets.token_urlsafe(_SECRET_BYTES)


def hash_visitor_secret(secret: str) -> str:
    # Lowercase hex: the same encoding convention app/auth/tokens.py's
    # _key_id() already uses for the JWT kid header -- one hex-encoding
    # convention project-wide, not a second one for this column.
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()


def verify_visitor_secret(secret: str, stored_hash: str) -> bool:
    # Constant-time comparison (hmac.compare_digest) -- a plain ==/!= on
    # either the secret or its hash would let a timing side channel leak how
    # many leading bytes matched. compare_digest() itself only accepts
    # same-type arguments and, for str, ASCII-only content (raises
    # TypeError otherwise) -- verified live against the installed hmac
    # module -- so a non-ASCII stored_hash is rejected here, before ever
    # reaching compare_digest(), rather than letting it raise. A wrong
    # length or non-hex (but still ASCII) stored_hash needs no such guard:
    # compare_digest() itself returns False for those, never raising.
    if not stored_hash.isascii():
        return False
    return hmac.compare_digest(hash_visitor_secret(secret), stored_hash)
