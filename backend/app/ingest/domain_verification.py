# backend/app/ingest/domain_verification.py
# Task 2.2.d: DNS-only domain-verification mechanics (PROJECT_SPEC.md's Step
# 2.2 breakdown, decision (b)) -- file/meta-tag verification are deferred
# until immediately after Step 2.3 lands (see PROJECT_SPEC.md's own Open
# marker). NOT wired to confirm_verification() yet -- that is explicitly
# 2.2.e's own job; this module only builds the primitive ahead of its
# eventual real caller (matching ensure_collection()'s own precedent, 1.3.c).
#
# Pure-function-plus-thin-IO-wrapper split, matching this project's own
# established pattern (origin_is_allowed, 1.4.f; _schema_differences,
# 1.3.c): domain_is_verified_by_dns() is the fully unit-testable core logic
# (no I/O); fetch_txt_records() is the thin async wrapper that performs the
# real DNS query.
#
# TXT record format and location, decided here since docs/SPEC.md gives no
# hint either way (confirmed by reading §3/§5.5 before deciding, not
# assumed): the record is TXT_RECORD_PREFIX + token, on the domain's own
# APEX TXT record set -- not a dedicated subdomain (e.g.
# _samoflow-verify.<domain>). Matches the single most common real-world
# convention: Google Search Console's own "google-site-verification=", and
# the identical shape Facebook, Apple, DocuSign, Cisco and OneTrust all use
# too (confirmed live by querying a real domain's own TXT records before
# choosing this, not assumed from memory -- all of the above showed up on
# the SAME apex record set). Lowest friction for docs/SPEC.md §12's own
# "mostly non-technical" dashboard audience: "add one more line to the TXT
# records you already have" is a smaller ask than "create a new subdomain
# record." A distinctive, project-specific prefix avoids colliding with any
# of those other services' own records living on that same apex.
import dns.asyncresolver
import dns.exception

TXT_RECORD_PREFIX = "samoflow-verify="

# DNS resolution over the real internet is a different latency class from
# this project's own Postgres/Qdrant (both on the same Docker network,
# normally sub-millisecond): an uncached domain may need the resolver to
# walk a real delegation chain, each hop a network round trip to a server
# outside our own infrastructure. 5 seconds is generous enough to absorb a
# slow or distant authoritative server without the caller waiting
# indefinitely, while still being short in absolute terms -- a domain that
# cannot resolve within this window is treated as not verified, not as a
# crash (see fetch_txt_records() below).
DNS_QUERY_TIMEOUT_SECONDS = 5.0


def domain_is_verified_by_dns(expected_token: str, txt_records: list[str]) -> bool:
    """Pure, offline-testable: no I/O. True only if `txt_records` contains
    an EXACT match for TXT_RECORD_PREFIX + expected_token -- membership on
    the list (not a substring/startswith check against each record), so a
    record with the right prefix but a different token, or a
    similar-but-not-identical prefix, can never match.
    """
    return f"{TXT_RECORD_PREFIX}{expected_token}" in txt_records


async def fetch_txt_records(domain: str) -> list[str]:
    """Thin async I/O wrapper: the one place this module performs a real
    DNS query. Returns an empty list -- never raises -- for ANY ordinary
    DNS failure (NXDOMAIN, no TXT records at all, timeout, SERVFAIL/no
    reachable nameserver): a domain that simply doesn't resolve is "not
    verified", not a caller-visible crash. dns.exception.DNSException is
    the real base class every one of those shares (confirmed live against
    the installed dnspython==2.8.0, not assumed) -- catching it, not a
    hand-picked list of specific subclasses, is the standard, complete way
    to mean "any clean DNS-layer failure" (rule 11). Only a genuinely
    unexpected error (anything that is NOT a DNSException) propagates.

    A single TXT record can carry more than one quoted character-string
    segment (RFC 1035); dnspython represents each segment separately in
    rdata.strings, and the record's own full value is their concatenation,
    not any one segment alone -- confirmed live, not assumed, by querying a
    real domain and inspecting the returned shape before relying on it.
    """
    resolver = dns.asyncresolver.Resolver()
    resolver.lifetime = DNS_QUERY_TIMEOUT_SECONDS
    try:
        answer = await resolver.resolve(domain, "TXT")
    except dns.exception.DNSException:
        return []
    return [b"".join(rdata.strings).decode("utf-8", errors="replace") for rdata in answer]


async def check_dns_verification(domain: str, expected_token: str) -> bool:
    """2.2.e's own future entry point -- built here, not yet wired to
    confirm_verification() (explicitly that task's own job, per the Step
    2.2 breakdown). Simple composition: fetch, then check.
    """
    txt_records = await fetch_txt_records(domain)
    return domain_is_verified_by_dns(expected_token, txt_records)
