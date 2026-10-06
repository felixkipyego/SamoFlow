# backend/tests/test_domain_verification.py
# Task 2.2.d: tests for DNS-only domain-verification mechanics
# (backend/app/ingest/domain_verification.py).
#
# Two groups:
#   - offline, table-driven tests for the pure core logic
#     (domain_is_verified_by_dns()), matching test_origin.py's own style
#     (1.4.f) -- no network, no database.
#   - live tests against the real internet DNS, deliberately NOT depending
#     on any third-party domain's own TXT record CONTENT (which this
#     project does not control and could change at any time -- a different
#     reliability class from this project's own Postgres/Qdrant test
#     services): a genuinely nonexistent domain (NXDOMAIN is structural,
#     not content, and will never change) proves the clean-failure path;
#     an unroutable nameserver with a short timeout proves the timeout
#     path; one real resolution against a long-lived domain asserts only
#     that the result is non-empty (never its specific content), proving
#     the success path actually works end to end without pinning anything
#     that could drift.
import http.server

import dns.asyncresolver
import pytest

from app.ingest import domain_verification
from app.ingest import safe_fetch as safe_fetch_module
from app.ingest.domain_verification import (
    FILE_VERIFICATION_PATH,
    META_TAG_NAME,
    TXT_RECORD_PREFIX,
    check_dns_verification,
    check_file_verification,
    check_meta_tag_verification,
    domain_is_verified_by_dns,
    fetch_txt_records,
)
from app.ingest.ip_safety import is_unsafe_destination_ip as _real_is_unsafe_destination_ip
from tests.conftest import local_http_server

_FILE_META_TOKEN = "distinctive-file-meta-token-456"  # noqa: S105 (test fixture value)


def _fake_is_unsafe_except_loopback(ip) -> bool:
    # Task 2.6.a: the identical small, local helper test_safe_fetch.py
    # already defined for the identical reason (its own header comment:
    # "not extracted to conftest.py -- only this file needs it so far").
    # Now a second file needs it -- noted here, not consolidated now,
    # matching this project's own established "flag, don't fix mid-task"
    # discipline; a future duplication check can decide whether to
    # extract a shared version. 127.0.0.1 is categorically unsafe by
    # design, so it is treated as safe FOR TEST PURPOSES ONLY here; every
    # other address still goes through the real, unmodified check.
    if str(ip) == "127.0.0.1":
        return False
    return _real_is_unsafe_destination_ip(ip)


def _scripted_handler(routes: dict):
    # routes: path -> (status_code, body_bytes). A small local copy of
    # the identical shape test_safe_fetch.py's own _scripted_handler
    # already has (same duplication note as above) -- this file's own
    # needs are simpler (no custom headers), so a trimmed copy, not an
    # import of that file's own private helper.
    class _Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            status, body = routes.get(self.path, (404, b"not found"))
            self.send_response(status)
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    return _Handler

_TOKEN = "abc123-distinctive-token"  # noqa: S105 (test fixture value, not a real secret)


@pytest.mark.parametrize(
    ("txt_records", "expected"),
    [
        # Matching record, alone.
        ([f"{TXT_RECORD_PREFIX}{_TOKEN}"], True),
        # Non-matching record, alone.
        (["some-other-record=xyz"], False),
        # Empty list.
        ([], False),
        # Multiple records, only one matches.
        (["v=spf1 include:_spf.example ~all", f"{TXT_RECORD_PREFIX}{_TOKEN}", "unrelated=1"], True),
        # Right prefix, wrong token.
        ([f"{TXT_RECORD_PREFIX}wrong-token"], False),
        # Similar-but-wrong prefix -- proves this is an exact match, not a
        # loose substring/startswith check.
        ([f"x{TXT_RECORD_PREFIX}{_TOKEN}"], False),
        ([f"{TXT_RECORD_PREFIX[:-1]}x={_TOKEN}"], False),
        # The expected value as a SUBSTRING of a longer, unrelated record
        # -- must not match just because it appears inside a bigger string.
        ([f"prefix-{TXT_RECORD_PREFIX}{_TOKEN}-suffix"], False),
    ],
)
def test_domain_is_verified_by_dns_table(txt_records, expected):
    assert domain_is_verified_by_dns(_TOKEN, txt_records) is expected


async def test_fetch_txt_records_returns_empty_list_on_nxdomain():
    # Structural, not content-dependent: this domain cannot ever exist
    # (reserved-looking, nonsense label under a real TLD), so NXDOMAIN is
    # guaranteed, not a third-party's own content this project doesn't
    # control.
    records = await fetch_txt_records(
        "this-domain-genuinely-does-not-exist-abc123xyz987.example"
    )
    assert records == []


async def test_fetch_txt_records_returns_empty_list_on_timeout_not_hang(monkeypatch):
    # TEST-NET-3 (RFC 5737): guaranteed non-routable, matching this
    # project's own existing convention for an unroutable test address
    # (app/health.py's own /ready tests). The resolver's own nameserver
    # list is monkeypatched directly, not the host's real resolv.conf, so
    # this test cannot affect or depend on the real system's DNS config;
    # _DNS_QUERY_TIMEOUT_SECONDS is shortened too, so this test proves the
    # timeout path fires without actually waiting out the real 5-second
    # production value.
    class _UnroutableResolver(dns.asyncresolver.Resolver):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, configure=False, **kwargs)
            self.nameservers = ["203.0.113.1"]

    monkeypatch.setattr(dns.asyncresolver, "Resolver", _UnroutableResolver)
    monkeypatch.setattr(domain_verification, "_DNS_QUERY_TIMEOUT_SECONDS", 0.5)

    records = await fetch_txt_records("example.com")
    assert records == []


async def test_fetch_txt_records_against_a_real_domain_returns_a_nonempty_list():
    # The one real "success path" proof, deliberately content-agnostic:
    # asserts only that SOME TXT record came back, never which one or what
    # it says, so this stays correct even if the specific record set this
    # domain carries changes over time. example.com (IANA's own reserved,
    # deliberately small and stable documentation domain, RFC 2606) --
    # NOT google.com, used here originally: google.com's own TXT record
    # set is large (17 records, confirmed live), and a real-world local
    # router was found live to mishandle that specific large response
    # (an EDNS0/large-UDP-response limitation, confirmed by querying the
    # identical record through a public resolver instead, which returned
    # it instantly) -- a genuinely fragile choice of domain regardless of
    # any one network's own quirks, not just a problem on that one
    # machine. example.com's own TXT set is small (an SPF record,
    # "v=spf1 -all", confirmed live via both a local and a public
    # resolver) and, as IANA's own maintained example domain, about as
    # stable a "this will keep resolving" bet as a live DNS test can make.
    records = await fetch_txt_records("example.com")
    assert records != []


async def test_check_dns_verification_returns_false_for_a_nonexistent_domain():
    # The combining function's own end-to-end proof (fetch + check),
    # reusing the same structurally-guaranteed NXDOMAIN case above rather
    # than a third, separate live case.
    verified = await check_dns_verification(
        "this-domain-genuinely-does-not-exist-abc123xyz987.example", _TOKEN
    )
    assert verified is False


# --- Task 2.6.a: file verification -----------------------------------------
#
# Live against a REAL local HTTP server (matching 2.3's own established
# testing discipline, not mocked responses) -- the URL builder
# (_file_verification_url) is monkeypatched to point at the local
# server instead of the real https://{domain} URL (see that function's
# own comment in domain_verification.py for why this is necessary: a
# local test server speaks plain HTTP, not TLS). Everything downstream
# of that -- the real fetch_with_redirects() call, the real SSRF
# validation, the real token-comparison logic -- is genuinely exercised,
# completely unmodified.


def _patch_loopback_safe_and_file_url(monkeypatch, port: int) -> None:
    monkeypatch.setattr(
        safe_fetch_module, "is_unsafe_destination_ip", _fake_is_unsafe_except_loopback
    )
    monkeypatch.setattr(
        domain_verification,
        "_file_verification_url",
        lambda domain: f"http://127.0.0.1:{port}{FILE_VERIFICATION_PATH}",
    )


def _patch_loopback_safe_and_meta_tag_url(monkeypatch, port: int) -> None:
    monkeypatch.setattr(
        safe_fetch_module, "is_unsafe_destination_ip", _fake_is_unsafe_except_loopback
    )
    monkeypatch.setattr(
        domain_verification, "_meta_tag_verification_url", lambda domain: f"http://127.0.0.1:{port}/"
    )


async def test_check_file_verification_with_the_correct_token_is_true(monkeypatch):
    with local_http_server(
        _scripted_handler({FILE_VERIFICATION_PATH: (200, _FILE_META_TOKEN.encode())})
    ) as port:
        _patch_loopback_safe_and_file_url(monkeypatch, port)
        verified = await check_file_verification("test.example", _FILE_META_TOKEN)
    assert verified is True


async def test_check_file_verification_with_a_wrong_token_is_false(monkeypatch):
    with local_http_server(
        _scripted_handler({FILE_VERIFICATION_PATH: (200, b"some-other-token")})
    ) as port:
        _patch_loopback_safe_and_file_url(monkeypatch, port)
        verified = await check_file_verification("test.example", _FILE_META_TOKEN)
    assert verified is False


async def test_check_file_verification_on_a_404_is_false_not_an_exception(monkeypatch):
    with local_http_server(_scripted_handler({})) as port:  # no routes -- every path 404s
        _patch_loopback_safe_and_file_url(monkeypatch, port)
        verified = await check_file_verification("test.example", _FILE_META_TOKEN)
    assert verified is False


async def test_check_file_verification_tolerates_a_trailing_newline(monkeypatch):
    # The "mostly non-technical dashboard audience" usability reasoning
    # from domain_verification.py's own docstring, proven directly: a
    # trailing newline (near-universal from real text editors/webservers)
    # must not fail an otherwise-correct token.
    with local_http_server(
        _scripted_handler({FILE_VERIFICATION_PATH: (200, f"{_FILE_META_TOKEN}\n".encode())})
    ) as port:
        _patch_loopback_safe_and_file_url(monkeypatch, port)
        verified = await check_file_verification("test.example", _FILE_META_TOKEN)
    assert verified is True


async def test_check_file_verification_against_a_loopback_address_is_false_not_an_exception():
    # THE SSRF-inheritance proof (point 2's own requirement): no local
    # server needed at all here, no monkeypatch of is_unsafe_destination_
    # ip() either -- the REAL, completely unmodified SSRF guard must
    # reject this before any network connection is even attempted. This
    # confirms check_file_verification() correctly INHERITS 2.3's own
    # protection rather than bypassing it -- it is handed "127.0.0.1"
    # directly as the "domain" (exactly as a hostile or misconfigured
    # caller might), and the resulting UnsafeFetchError must be caught,
    # not propagated.
    verified = await check_file_verification("127.0.0.1", _FILE_META_TOKEN)
    assert verified is False


# --- Task 2.6.a: meta-tag verification --------------------------------------


async def test_check_meta_tag_verification_with_the_correct_tag_is_true(monkeypatch):
    html = (
        f'<html><head><meta name="{META_TAG_NAME}" content="{_FILE_META_TOKEN}">'
        "</head><body>hello</body></html>"
    ).encode()
    with local_http_server(_scripted_handler({"/": (200, html)})) as port:
        _patch_loopback_safe_and_meta_tag_url(monkeypatch, port)
        verified = await check_meta_tag_verification("test.example", _FILE_META_TOKEN)
    assert verified is True


async def test_check_meta_tag_verification_with_a_wrong_token_in_the_tag_is_false(monkeypatch):
    html = (
        f'<html><head><meta name="{META_TAG_NAME}" content="wrong-token">'
        "</head><body>hello</body></html>"
    ).encode()
    with local_http_server(_scripted_handler({"/": (200, html)})) as port:
        _patch_loopback_safe_and_meta_tag_url(monkeypatch, port)
        verified = await check_meta_tag_verification("test.example", _FILE_META_TOKEN)
    assert verified is False


async def test_check_meta_tag_verification_token_present_elsewhere_but_not_in_the_tag_is_false(
    monkeypatch,
):
    # THE false-positive-avoidance proof (point 2's own explicit
    # requirement): the real token appears on the page -- in an HTML
    # comment AND in ordinary body text -- but never inside the correct
    # meta tag's own content attribute. A naive substring search across
    # the raw HTML would wrongly return True here; this must stay False.
    html = (
        f"<html><head><!-- {_FILE_META_TOKEN} --><meta name=\"{META_TAG_NAME}\" "
        f'content="wrong-token"></head>'
        f"<body>{_FILE_META_TOKEN}</body></html>"
    ).encode()
    with local_http_server(_scripted_handler({"/": (200, html)})) as port:
        _patch_loopback_safe_and_meta_tag_url(monkeypatch, port)
        verified = await check_meta_tag_verification("test.example", _FILE_META_TOKEN)
    assert verified is False


async def test_check_meta_tag_verification_with_no_meta_tag_at_all_is_false_not_an_exception(
    monkeypatch,
):
    html = b"<html><head><title>No tag here</title></head><body>hello</body></html>"
    with local_http_server(_scripted_handler({"/": (200, html)})) as port:
        _patch_loopback_safe_and_meta_tag_url(monkeypatch, port)
        verified = await check_meta_tag_verification("test.example", _FILE_META_TOKEN)
    assert verified is False


async def test_check_meta_tag_verification_against_a_loopback_address_is_false_not_an_exception():
    # The identical SSRF-inheritance proof as the file-verification case
    # above, for the other method.
    verified = await check_meta_tag_verification("127.0.0.1", _FILE_META_TOKEN)
    assert verified is False
