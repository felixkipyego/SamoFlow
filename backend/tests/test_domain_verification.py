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
import dns.asyncresolver
import pytest

from app.ingest import domain_verification
from app.ingest.domain_verification import (
    TXT_RECORD_PREFIX,
    check_dns_verification,
    domain_is_verified_by_dns,
    fetch_txt_records,
)

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
    # domain carries changes over time. google.com's own outbound-mail SPF
    # record alone makes a fully TXT-record-free future implausible.
    records = await fetch_txt_records("google.com")
    assert records != []


async def test_check_dns_verification_returns_false_for_a_nonexistent_domain():
    # The combining function's own end-to-end proof (fetch + check),
    # reusing the same structurally-guaranteed NXDOMAIN case above rather
    # than a third, separate live case.
    verified = await check_dns_verification(
        "this-domain-genuinely-does-not-exist-abc123xyz987.example", _TOKEN
    )
    assert verified is False
