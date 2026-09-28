# backend/tests/test_tokens.py
# Tests for Task 1.4.b's JWT encode/decode module (backend/app/auth/tokens.py).
# All offline: no network, no database, no live Qdrant/Postgres -- every
# test drives encode_session_token()/decode_session_token() purely through
# the environment (via set_valid_env()) and hand-built tokens.
import base64
import json
import uuid
from datetime import UTC, datetime, timedelta

import jwt
import pytest

from app.auth.tokens import (
    TOKEN_AUDIENCE,
    TOKEN_ISSUER,
    InvalidSessionToken,
    decode_session_token,
    encode_session_token,
)
from app.config import get_settings
from tests.conftest import VALID_ENV, set_valid_env

ORIGIN = "https://example.com"


def _valid_payload(**overrides) -> dict:
    payload = {
        "iss": TOKEN_ISSUER,
        "aud": TOKEN_AUDIENCE,
        "sub": str(uuid.uuid4()),
        "tid": str(uuid.uuid4()),
        "org": ORIGIN,
        "exp": datetime.now(UTC) + timedelta(minutes=15),
    }
    payload.update(overrides)
    return payload


def _raw_encode(payload: dict, key: str, **kwargs) -> str:
    # Bypasses encode_session_token() entirely -- builds a token exactly the
    # shape we want, including shapes encode_session_token() would never
    # produce itself (expired, wrong aud/iss, missing claims, signed with an
    # unrelated key), so decode_session_token() can be tested against
    # inputs an attacker (not our own encoder) could plausibly send.
    return jwt.encode(payload, key, algorithm="HS256", **kwargs)


def _b64url(data: bytes) -> bytes:
    return base64.urlsafe_b64encode(data).rstrip(b"=")


def _forge_alg_none_token(payload: dict) -> str:
    # Hand-built, not via jwt.encode() at all: header {"alg": "none"}, the
    # real payload, and an EMPTY signature segment -- the classic
    # "unsecured JWT" bypass (RFC 8725 §2.1). default=str handles the
    # datetime `exp` value the same way jwt.encode() itself would.
    header_segment = _b64url(json.dumps({"alg": "none", "typ": "JWT"}).encode())
    payload_segment = _b64url(json.dumps(payload, default=str).encode())
    return (header_segment + b"." + payload_segment + b".").decode()


def test_valid_round_trip_encodes_and_decodes_back(monkeypatch):
    set_valid_env(monkeypatch, VALID_ENV)
    tenant_id, vid = uuid.uuid4(), uuid.uuid4()
    token = encode_session_token(tenant_id, vid, ORIGIN)
    claims = decode_session_token(token)
    assert claims.tenant_id == tenant_id
    assert claims.vid == vid
    assert claims.origin == ORIGIN


def test_expired_token_is_rejected(monkeypatch):
    set_valid_env(monkeypatch, VALID_ENV)
    token = _raw_encode(
        _valid_payload(exp=datetime.now(UTC) - timedelta(minutes=5)),
        key=VALID_ENV["JWT_SIGNING_KEY"],
    )
    with pytest.raises(InvalidSessionToken):
        decode_session_token(token)


def test_wrong_audience_is_rejected(monkeypatch):
    set_valid_env(monkeypatch, VALID_ENV)
    token = _raw_encode(_valid_payload(aud="some-other-api"), key=VALID_ENV["JWT_SIGNING_KEY"])
    with pytest.raises(InvalidSessionToken):
        decode_session_token(token)


def test_wrong_issuer_is_rejected(monkeypatch):
    set_valid_env(monkeypatch, VALID_ENV)
    token = _raw_encode(_valid_payload(iss="some-other-issuer"), key=VALID_ENV["JWT_SIGNING_KEY"])
    with pytest.raises(InvalidSessionToken):
        decode_session_token(token)


@pytest.mark.parametrize("missing_claim", ["exp", "iss", "aud", "sub", "tid", "org"])
def test_missing_required_claim_is_rejected(monkeypatch, missing_claim):
    set_valid_env(monkeypatch, VALID_ENV)
    payload = _valid_payload()
    del payload[missing_claim]
    token = _raw_encode(payload, key=VALID_ENV["JWT_SIGNING_KEY"])
    with pytest.raises(InvalidSessionToken):
        decode_session_token(token)


def test_tampered_signature_is_rejected(monkeypatch):
    set_valid_env(monkeypatch, VALID_ENV)
    token = encode_session_token(uuid.uuid4(), uuid.uuid4(), ORIGIN)
    header, payload, signature = token.split(".")
    flipped = ("A" if signature[0] != "A" else "B") + signature[1:]
    tampered = f"{header}.{payload}.{flipped}"
    with pytest.raises(InvalidSessionToken):
        decode_session_token(tampered)


def test_alg_none_forged_token_is_rejected(monkeypatch):
    # Does not assume PyJWT's default is safe -- proves it, per the task's
    # own instruction. algorithms=["HS256"] is always passed explicitly by
    # decode_session_token(); confirmed live (see app/auth/tokens.py's
    # header comment) that this is exactly what makes this forged token
    # fail, not any special-cased "none" handling.
    set_valid_env(monkeypatch, VALID_ENV)
    token = _forge_alg_none_token(_valid_payload())
    with pytest.raises(InvalidSessionToken):
        decode_session_token(token)


def test_token_signed_with_a_different_key_entirely_is_rejected(monkeypatch):
    set_valid_env(monkeypatch, VALID_ENV)
    token = _raw_encode(_valid_payload(), key="a-completely-unrelated-signing-key-32ch")
    with pytest.raises(InvalidSessionToken):
        decode_session_token(token)


def test_kid_matching_neither_configured_key_is_rejected_the_same_way(monkeypatch):
    # Same underlying cause as test_token_signed_with_a_different_key above
    # (an unrelated key), but with an explicit, unrelated kid header -- the
    # exact same exception type must come out either way: nothing here
    # distinguishes "kid didn't match" from any other invalid-token reason.
    set_valid_env(monkeypatch, VALID_ENV)
    token = _raw_encode(
        _valid_payload(),
        key="a-completely-unrelated-signing-key-32ch",
        headers={"kid": "ffffffffffff"},
    )
    with pytest.raises(InvalidSessionToken):
        decode_session_token(token)


def test_decode_verifies_via_current_key_when_previous_key_is_unrelated(monkeypatch):
    set_valid_env(
        monkeypatch, VALID_ENV, JWT_SIGNING_KEY_PREVIOUS="unrelated-previous-key-32-chars-ok"
    )
    tenant_id, vid = uuid.uuid4(), uuid.uuid4()
    token = encode_session_token(tenant_id, vid, ORIGIN)
    claims = decode_session_token(token)
    assert claims.tenant_id == tenant_id
    assert claims.vid == vid


def test_decode_verifies_via_previous_key_after_rotation(monkeypatch):
    old_key = "old-jwt-signing-key-32-characters-long-ok"
    set_valid_env(monkeypatch, VALID_ENV, JWT_SIGNING_KEY=old_key)
    tenant_id, vid = uuid.uuid4(), uuid.uuid4()
    token = encode_session_token(tenant_id, vid, ORIGIN)

    new_key = "new-jwt-signing-key-32-characters-long-ok"
    set_valid_env(monkeypatch, VALID_ENV, JWT_SIGNING_KEY=new_key, JWT_SIGNING_KEY_PREVIOUS=old_key)
    get_settings.cache_clear()
    claims = decode_session_token(token)
    assert claims.tenant_id == tenant_id
    assert claims.vid == vid


def test_decode_raises_cleanly_with_no_previous_key_and_a_non_matching_current_key(monkeypatch):
    key_a = "key-a-for-signing-32-characters-long-ok"
    set_valid_env(monkeypatch, VALID_ENV, JWT_SIGNING_KEY=key_a)
    token = encode_session_token(uuid.uuid4(), uuid.uuid4(), ORIGIN)

    key_b = "key-b-for-signing-32-characters-long-ok"
    set_valid_env(monkeypatch, VALID_ENV, JWT_SIGNING_KEY=key_b)
    monkeypatch.delenv("JWT_SIGNING_KEY_PREVIOUS", raising=False)
    get_settings.cache_clear()
    with pytest.raises(InvalidSessionToken):
        decode_session_token(token)


def test_decode_error_never_contains_a_distinctive_fake_key_value(monkeypatch):
    set_valid_env(monkeypatch, VALID_ENV)
    distinctive_key = "distinctive-fake-key-should-never-leak-32c"
    token = _raw_encode(_valid_payload(), key=distinctive_key)
    with pytest.raises(InvalidSessionToken) as exc_info:
        decode_session_token(token)
    err = exc_info.value
    assert distinctive_key not in str(err)
    assert distinctive_key not in repr(err)
    assert VALID_ENV["JWT_SIGNING_KEY"] not in str(err)
    assert err.__cause__ is None
    assert err.__context__ is None
