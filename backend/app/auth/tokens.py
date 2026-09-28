# backend/app/auth/tokens.py
# Task 1.4.b: widget session-token encode/decode (docs/SPEC.md §4.2/§4.3).
# Nothing here reads the environment or calls get_settings() at import time
# (module import must succeed with an empty environment, matching every
# other app/*.py module's own rule) -- get_settings() is only ever called
# from inside encode_session_token()/decode_session_token(), at call time.
#
# ASSUMPTION: the `iss` claim's exact value is not pinned anywhere in
# docs/SPEC.md (unlike `aud`, which §4.2 fixes to "widget-api") -- TOKEN_ISSUER
# below uses the same neutral code name already established elsewhere
# (app/main.py's `_TITLE`, backend/pyproject.toml's package name), not the
# "SamoFlow" brand name, matching that existing convention.
#
# Algorithm is pinned to HS256 only (docs/SPEC.md §4.3); `algorithms=` is
# always passed explicitly to jwt.decode(), never left to a default --
# confirmed live against the installed PyJWT==2.15.1 that a hand-forged
# `{"alg": "none"}` token is rejected with InvalidAlgorithmError specifically
# *because* "none" is absent from the explicit algorithms list, not because
# of any special-cased "none" handling PyJWT does on its own (RFC 8725 §2.1's
# own warning, which PyJWT's decode() docstring quotes directly: never
# compute `algorithms` from the token itself).
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from hashlib import sha256

import jwt

from app.config import get_settings

# Both noqa: S105 -- public claim values, not secrets.
TOKEN_AUDIENCE = "widget-api"  # noqa: S105  -- docs/SPEC.md §4.2, fixed.
TOKEN_ISSUER = "widgetplatform"  # noqa: S105  -- ASSUMPTION, see header comment.
TOKEN_LIFETIME = timedelta(minutes=15)  # docs/SPEC.md §4.2.
TOKEN_LEEWAY_SECONDS = 30  # docs/SPEC.md §4.3.
KID_LENGTH = 12  # docs/SPEC.md §4.2's own "kid header" length is unspecified;
# 12 hex characters (48 bits) is far more than enough to distinguish two
# concurrently-valid keys during a rotation window, per the Step 1.4
# decision entry.

# Every claim decode_session_token() requires to be present. exp/iss/aud/sub
# are claims PyJWT itself recognizes (and separately verifies the *value* of
# aud/iss when passed as decode() kwargs, and that sub is a string); tid/org
# are ours alone -- PyJWT's `require` option enforces presence generically
# for any claim name, verified live against the installed library, so one
# option list covers all six uniformly rather than a hand-rolled presence
# check for the two custom ones.
_REQUIRED_CLAIMS = ["exp", "iss", "aud", "sub", "tid", "org"]


class InvalidSessionToken(Exception):
    """Raised by decode_session_token() for any verification failure --
    expired, forged, wrong aud/iss, a missing claim, signed with an unknown
    key, a tampered signature, or a tid/sub claim that isn't a well-formed
    UUID. The message never contains the token, either signing key, or any
    claim value -- only ever a fixed, generic description.
    """


@dataclass(frozen=True)
class SessionTokenClaims:
    tenant_id: uuid.UUID
    vid: uuid.UUID
    origin: str


def _key_id(key: str) -> str:
    # Shared by encode (the `kid` header) and decode (choosing which
    # configured key to try first) -- one definition, used both places, so
    # the two can never silently drift apart.
    return sha256(key.encode("utf-8")).hexdigest()[:KID_LENGTH]


def encode_session_token(tenant_id: uuid.UUID, vid: uuid.UUID, origin: str) -> str:
    settings = get_settings()
    key = settings.jwt_signing_key_str()
    now = datetime.now(UTC)
    payload = {
        "iss": TOKEN_ISSUER,
        "aud": TOKEN_AUDIENCE,
        "sub": str(vid),
        "tid": str(tenant_id),
        "org": origin,
        "exp": now + TOKEN_LIFETIME,
    }
    return jwt.encode(payload, key, algorithm="HS256", headers={"kid": _key_id(key)})


def _select_key_order(kid: str | None, current_key: str, previous_key: str | None) -> list[str]:
    # kid is read only to choose which configured key to TRY FIRST -- every
    # configured key is still tried in turn regardless of whether it
    # matches, so a missing, wrong or attacker-controlled kid can never skip
    # a real verification attempt, only reorder them. With only two possible
    # keys, the only reordering that can ever matter is "try the previous
    # key first because kid unambiguously points at it, not at the current
    # key" -- anything else (kid matches current, matches neither, or is
    # absent) leaves the default current-then-previous order untouched.
    if previous_key is None:
        return [current_key]
    if kid is not None and kid == _key_id(previous_key) and kid != _key_id(current_key):
        return [previous_key, current_key]
    return [current_key, previous_key]


def decode_session_token(token: str) -> SessionTokenClaims:
    settings = get_settings()
    current_key = settings.jwt_signing_key_str()
    previous_key = settings.jwt_signing_key_previous_str()

    try:
        kid = jwt.get_unverified_header(token).get("kid")
    except jwt.PyJWTError:
        kid = None

    payload = None
    for key in _select_key_order(kid, current_key, previous_key):
        try:
            payload = jwt.decode(
                token,
                key=key,
                algorithms=["HS256"],
                audience=TOKEN_AUDIENCE,
                issuer=TOKEN_ISSUER,
                leeway=TOKEN_LEEWAY_SECONDS,
                options={"require": _REQUIRED_CLAIMS},
            )
            break
        except jwt.PyJWTError:
            continue
    if payload is None:
        # Raised after leaving every except block above (each one hit
        # `continue`, not a re-raise) -- confirmed by hand that this is no
        # longer "while handling" any of them, so Python does not implicitly
        # chain __cause__/__context__ here, matching app/config.py's own
        # get_settings() reasoning for the identical pattern.
        raise InvalidSessionToken("session token could not be verified")

    try:
        tenant_id = uuid.UUID(payload["tid"])
        vid = uuid.UUID(payload["sub"])
    except (ValueError, TypeError, AttributeError):
        raise InvalidSessionToken("session token claims are malformed") from None
    return SessionTokenClaims(tenant_id=tenant_id, vid=vid, origin=payload["org"])
