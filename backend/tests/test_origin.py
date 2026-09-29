# backend/tests/test_origin.py
# Task 1.4.f: offline, table-driven tests for the pure origin-matching
# function (backend/app/auth/origin.py). No network, no database.
import pytest

from app.auth.origin import origin_is_allowed

_ALLOWED = ["https://widget.example", "http://plain.example:8080"]


@pytest.mark.parametrize(
    ("origin", "expected"),
    [
        ("https://widget.example", True),
        # Effective-port equivalence: 443 is https's default, so omitting
        # it must still match the explicit form and vice versa.
        ("https://widget.example:443", True),
        ("http://plain.example:8080", True),
        # Wrong scheme, same host: never matches (no wildcards, no
        # scheme-insensitive comparison).
        ("http://widget.example", False),
        # Wrong host.
        ("https://evil.example", False),
        # Wrong (non-default) port.
        ("https://widget.example:8443", False),
        # Default-port form on the allowed side (8080 is not http's
        # default, so an omitted port must NOT match it).
        ("http://plain.example", False),
        # Host case: RFC 3986 hosts are case-insensitive.
        ("HTTPS://WIDGET.EXAMPLE", True),
        # The literal "null" Origin (sandboxed/opaque origins): no scheme,
        # rejected without any special-casing.
        ("null", False),
        # Malformed/nonsense strings must not raise, only return False.
        ("not-a-url", False),
        ("", False),
        ("ftp://widget.example", False),
        # A path/query on the Origin (never sent by real browsers, but
        # must not crash and must not affect the scheme/host/port match).
        ("https://widget.example/some/path?x=1", True),
    ],
)
def test_origin_is_allowed_table(origin, expected):
    assert origin_is_allowed(origin, _ALLOWED) is expected


def test_origin_is_allowed_false_for_empty_allow_list():
    assert origin_is_allowed("https://widget.example", []) is False


def test_origin_is_allowed_never_treats_an_allowed_entry_as_a_wildcard():
    # An allowed_origins entry that happens to be a bare "*" must never
    # match anything: it has no scheme/hostname, so it always normalizes to
    # None and can never equal any real origin's key.
    assert origin_is_allowed("https://widget.example", ["*"]) is False
    assert origin_is_allowed("https://anything.example", ["*"]) is False


def test_origin_is_allowed_is_order_independent():
    assert origin_is_allowed("http://plain.example:8080", list(reversed(_ALLOWED))) is True
