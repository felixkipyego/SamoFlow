# backend/tests/test_visitor_secrets.py
# Tests for Task 1.4.c's visitor-secret module (backend/app/auth/secrets.py).
# All offline: no network, no database -- pure functions only.
import hmac
import re

from app.auth import secrets as visitor_secrets

_HEX64 = re.compile(r"^[0-9a-f]{64}$")


def test_generate_visitor_secret_has_reasonable_length_and_no_fixed_prefix():
    samples = [visitor_secrets.generate_visitor_secret() for _ in range(20)]
    # secrets.token_urlsafe(32) is always 43 characters for a fixed input
    # size (32 bytes -> base64url with no padding) -- checked, not assumed.
    assert all(len(s) == 43 for s in samples)
    # No two of the 20 share a prefix: if all 20 shared even their first two
    # characters, that would indicate a fixed/predictable prefix rather than
    # a fully random token.
    prefixes = {s[:2] for s in samples}
    assert len(prefixes) > 1
    assert len(set(samples)) == len(samples)


def test_generate_visitor_secret_produces_different_values():
    first = visitor_secrets.generate_visitor_secret()
    second = visitor_secrets.generate_visitor_secret()
    assert first != second


def test_hash_visitor_secret_is_deterministic_and_fixed_length_hex():
    secret = visitor_secrets.generate_visitor_secret()
    first = visitor_secrets.hash_visitor_secret(secret)
    second = visitor_secrets.hash_visitor_secret(secret)
    assert first == second
    assert _HEX64.match(first)


def test_hash_visitor_secret_output_never_contains_the_plaintext():
    secret = visitor_secrets.generate_visitor_secret()
    digest = visitor_secrets.hash_visitor_secret(secret)
    assert secret not in digest


def test_verify_visitor_secret_true_for_the_correct_secret():
    secret = visitor_secrets.generate_visitor_secret()
    stored_hash = visitor_secrets.hash_visitor_secret(secret)
    assert visitor_secrets.verify_visitor_secret(secret, stored_hash) is True


def test_verify_visitor_secret_false_for_a_different_secret():
    secret = visitor_secrets.generate_visitor_secret()
    stored_hash = visitor_secrets.hash_visitor_secret(secret)
    other = visitor_secrets.generate_visitor_secret()
    assert visitor_secrets.verify_visitor_secret(other, stored_hash) is False


def test_verify_visitor_secret_false_for_a_near_miss_one_character_off():
    # Proves the comparison is a full constant-time equality check, not a
    # short-circuit substring/prefix match: only the LAST character differs,
    # so any prefix-based check would wrongly accept this.
    secret = visitor_secrets.generate_visitor_secret()
    stored_hash = visitor_secrets.hash_visitor_secret(secret)
    flipped_char = "A" if secret[-1] != "A" else "B"
    near_miss = secret[:-1] + flipped_char
    assert near_miss != secret
    assert visitor_secrets.verify_visitor_secret(near_miss, stored_hash) is False


def test_verify_visitor_secret_false_for_empty_string():
    secret = visitor_secrets.generate_visitor_secret()
    stored_hash = visitor_secrets.hash_visitor_secret(secret)
    assert visitor_secrets.verify_visitor_secret("", stored_hash) is False


def test_verify_visitor_secret_false_for_malformed_stored_hash_no_raise():
    secret = visitor_secrets.generate_visitor_secret()
    # Wrong length (too short), still ASCII: hmac.compare_digest() itself
    # returns False for mismatched lengths, never raising.
    assert visitor_secrets.verify_visitor_secret(secret, "abc") is False
    # Not hex, but still ASCII: compare_digest() only cares about ASCII-ness
    # for str inputs, not hex-ness -- returns False on content mismatch.
    assert visitor_secrets.verify_visitor_secret(secret, "z" * 64) is False
    # Non-ASCII: without this module's own isascii() guard, compare_digest()
    # would raise TypeError here -- proves the guard is load-bearing, not
    # redundant defensiveness.
    assert visitor_secrets.verify_visitor_secret(secret, "日" * 32) is False


def test_verify_visitor_secret_calls_hmac_compare_digest(monkeypatch):
    # Monkeypatching (already this project's established way of proving a
    # library call actually happens, e.g. test_qdrant.py's warnings-filter
    # test) is simpler and more robust here than disassembling bytecode --
    # it proves the real call, with the real arguments, not just that some
    # bytecode instruction referencing the name exists.
    calls = []
    real_compare_digest = hmac.compare_digest

    def spy(a, b):
        calls.append((a, b))
        return real_compare_digest(a, b)

    monkeypatch.setattr(hmac, "compare_digest", spy)
    secret = visitor_secrets.generate_visitor_secret()
    stored_hash = visitor_secrets.hash_visitor_secret(secret)
    assert visitor_secrets.verify_visitor_secret(secret, stored_hash) is True
    assert calls == [(stored_hash, stored_hash)]
