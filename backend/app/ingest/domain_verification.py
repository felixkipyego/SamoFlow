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
import httpx
from bs4 import BeautifulSoup

from app.ingest.safe_fetch import UnsafeFetchError, fetch_with_redirects

TXT_RECORD_PREFIX = "samoflow-verify="

# Task 2.6.a. file/meta_tag verification deferred until Step 2.3's SSRF
# guard existed to fetch a tenant-supplied host safely -- this module's
# own header comment above already named the trigger; it has now arrived.
#
# Well-known path, decided here since docs/SPEC.md gives no hint either
# way (confirmed by reading §5.5 before deciding, not assumed): under
# /.well-known/, matching RFC 8615's own standard convention for exactly
# this class of "a service proves it controls this site" file (the same
# directory ACME/Let's Encrypt, Apple's apple-developer-merchantid-domain-
# association, and Google's own Search Console file-upload method all
# use) -- a site owner inspecting their own web root sees a predictable,
# recognizable location, not an arbitrary one. A project-specific
# filename (not a generic "verify.txt") avoids colliding with any other
# service's own file living in the same directory.
FILE_VERIFICATION_PATH = "/.well-known/samoflow-verify.txt"

# meta tag name, matching TXT_RECORD_PREFIX's own naming spirit: a
# distinctive, project-specific attribute value, directly mirroring the
# single most common real-world convention for this exact mechanism
# (Google's own "google-site-verification" meta tag uses the identical
# name="..." content="TOKEN" shape). Expected directly inside <head>,
# matching where every one of those real-world examples places it.
META_TAG_NAME = "samoflow-site-verification"

# DNS resolution over the real internet is a different latency class from
# this project's own Postgres/Qdrant (both on the same Docker network,
# normally sub-millisecond): an uncached domain may need the resolver to
# walk a real delegation chain, each hop a network round trip to a server
# outside our own infrastructure. 5 seconds is generous enough to absorb a
# slow or distant authoritative server without the caller waiting
# indefinitely, while still being short in absolute terms -- a domain that
# cannot resolve within this window is treated as not verified, not as a
# crash (see fetch_txt_records() below).
_DNS_QUERY_TIMEOUT_SECONDS = 5.0


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
    resolver.lifetime = _DNS_QUERY_TIMEOUT_SECONDS
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


# Task 2.6.a. URL construction split into its own tiny pure function per
# method, matching this module's own established "pure-function-plus-
# thin-IO-wrapper split" (see this file's own header comment). Not just
# stylistic: this is what makes check_file_verification()/check_meta_tag_
# verification() testable against a REAL local HTTP server at all --
# both always fetch over https in production (verification is a proof-
# of-control mechanism; plaintext HTTP would make that proof interceptable
# by anyone on the network path, a materially weaker guarantee for no
# benefit), but a local test server speaks plain HTTP, not TLS. Tests
# monkeypatch these two builders directly (the identical bare-name-
# monkeypatch shape this project already uses throughout, e.g.
# check_dns_verification() in confirm_verification()'s own tests) to point
# at a real local server instead, while the REAL fetch_with_redirects()
# call, the REAL SSRF validation, and the REAL token-comparison logic all
# stay genuinely exercised, completely unmodified for tests.
def _file_verification_url(domain: str) -> str:
    return f"https://{domain}{FILE_VERIFICATION_PATH}"


def _meta_tag_verification_url(domain: str) -> str:
    return f"https://{domain}/"


# Both check functions below share the identical failure contract
# check_dns_verification() already established: ANY fetch failure means
# "not verified yet", never a caller-visible crash. Two exception types
# are caught, confirmed by reading the real code paths, not assumed:
#   - UnsafeFetchError (safe_fetch.py, 2.3): unsupported scheme, no safe
#     IP for the host (the SSRF rejection itself), a response exceeding
#     max_size_bytes, or exceeding max_redirects. This is the entire
#     reason this logic waited for Step 2.3 to exist -- a rejected fetch
#     here means the SSRF guard did its job, not that verification
#     itself failed for some unrelated reason, but the caller-visible
#     RESULT is identical either way: this domain is not verified.
#   - httpx.HTTPError (confirmed live, by reading the installed httpx==
#     0.28.1's own exception hierarchy, that EVERY transport/timeout/
#     protocol error -- ConnectError, ConnectTimeout, ReadTimeout,
#     RemoteProtocolError, etc. -- derives from this one base class):
#     safe_fetch.py's own _fetch_pinned() makes a real httpx connection
#     with no try/except of its own around it, so a genuine network-level
#     failure (connection refused, timeout, a mid-response protocol
#     error) propagates here RAW, unlike UnsafeFetchError. A domain
#     that is unreachable, times out, or drops the connection mid-fetch
#     is "not verified yet" in exactly the same sense an NXDOMAIN is for
#     DNS (2.2.d's own fetch_txt_records() precedent) -- not an
#     application error.
# A non-matching token, a missing meta tag, or a non-2xx status whose
# body simply doesn't contain the token all fall out of the ordinary
# comparison logic below with no special-casing needed -- there is
# nothing about a 404 that requires catching as an exception, since
# fetch_with_redirects() never raises for an ordinary non-redirect status
# code; its body (an HTML error page, or empty) will not equal the
# expected token either way.
async def check_file_verification(domain: str, expected_token: str) -> bool:
    """Fetches FILE_VERIFICATION_PATH over https and compares its ENTIRE
    body (decoded, leading/trailing whitespace stripped) against
    expected_token exactly. Stripping whitespace only -- not doing a
    substring search -- matches 'the entire body content is expected to
    be exactly the verification token, nothing else' while still
    tolerating a trailing newline, which almost every real text editor/
    webserver adds automatically; rejecting on that alone would be a
    usability trap for docs/SPEC.md §12's own 'mostly non-technical'
    dashboard audience, the identical reasoning TXT_RECORD_PREFIX's own
    comment above already applies to the DNS method.
    """
    url = _file_verification_url(domain)
    try:
        body = await fetch_with_redirects(url)
    except (UnsafeFetchError, httpx.HTTPError):
        return False
    return body.decode("utf-8", errors="replace").strip() == expected_token


async def check_meta_tag_verification(domain: str, expected_token: str) -> bool:
    """Fetches the domain's own homepage over https and looks for EXACTLY
    <meta name="samoflow-site-verification" content="TOKEN"> inside
    <head> -- parsed with BeautifulSoup (already a dependency, 2.4.b),
    matching extract_html.py's own "lxml" parser choice, never a naive
    string search across the raw HTML. This is load-bearing, not
    stylistic: the expected token appearing anywhere else on the page
    (body text, an HTML comment, a different, unrelated meta tag) must
    NOT be mistaken for the real tag -- only this exact tag's own
    `content` attribute, inside <head> specifically, is ever compared.
    A page with no <head> element at all (malformed, but possible) is
    treated as having no meta tag, not as a crash.
    """
    url = _meta_tag_verification_url(domain)
    try:
        body = await fetch_with_redirects(url)
    except (UnsafeFetchError, httpx.HTTPError):
        return False
    soup = BeautifulSoup(body.decode("utf-8", errors="replace"), "lxml")
    tag = soup.head.find("meta", attrs={"name": META_TAG_NAME}) if soup.head else None
    if tag is None:
        return False
    return tag.get("content", "").strip() == expected_token
