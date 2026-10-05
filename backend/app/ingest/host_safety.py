# backend/app/ingest/host_safety.py
# Task 2.3.b [SECURITY]: resolves a hostname and validates every IP it
# returns against 2.3.a's is_unsafe_destination_ip() -- the step this
# step's own eventual pinned fetch (2.3.c) and redirect-chain handling
# (2.3.d) both build on: a safe connection target can only ever be an IP
# this function has already resolved and checked, never a hostname handed
# straight to an HTTP client that would resolve it again itself.
#
# A sibling to ip_safety.py, not an extension of it -- same reasoning
# ip_safety.py's own header comment gives for staying out of safe_fetch.py
# (built starting 2.3.c, which will import httpx): this module needs
# dnspython, which ip_safety.py's own pure IP-classification function has
# no reason to require. Keeping dnspython's dependency confined to this
# file means a caller that only ever needs to classify an IP it already
# has in hand (Step 2.8's own database-sync adapter, per docs/SPEC.md
# §5.6's "same host checks as the crawler") can import ip_safety.py alone,
# with no DNS-resolution dependency pulled in for no reason.
#
# Timeout: a new, private _DNS_QUERY_TIMEOUT_SECONDS = 5.0, deliberately
# NOT an import of domain_verification.py's own same-named private
# constant -- that constant is private to ITS module by design (matching
# this project's own convention elsewhere, e.g. health.py's
# _READY_TIMEOUT_SECONDS), and reaching across a module boundary for a
# name a leading underscore says is "owned by and private to this file"
# would undercut the convention, not reuse it. The VALUE is deliberately
# identical: both are a real DNS query over the internet, the same
# latency class, the same reasoning for 5s (generous enough to absorb a
# slow or distant authoritative server, short enough not to hang a
# caller indefinitely). Two occurrences of the same value, independently
# named, is exactly where this project's own established threshold
# (2.2.d/e/f's duplication check: not worth a shared alias until a 3rd
# occurrence of the identical shape appears) says NOT to extract a shared
# constant yet -- matching that precedent, not inventing a new one.
import ipaddress

import dns.asyncresolver
import dns.exception

from app.ingest.ip_safety import is_unsafe_destination_ip

_DNS_QUERY_TIMEOUT_SECONDS = 5.0


async def resolve_and_validate(
    hostname: str,
) -> list[ipaddress.IPv4Address | ipaddress.IPv6Address]:
    """Resolves `hostname` for both A and AAAA records, and returns every
    IP it carries ONLY if every single one of them is safe --
    matching fetch_txt_records()'s own established contract (2.2.d):
    returns an empty list, never raises, for ANY ordinary DNS failure
    (NXDOMAIN, timeout, no records of a given type, no records at all)
    OR if even one resolved IP is unsafe. There is no partial-success
    return: a caller must never be able to pick between a validated and
    an unvalidated address from the same answer, so an unsafe IP anywhere
    in the combined A+AAAA result rejects the WHOLE hostname, not just
    that one address.

    A missing record TYPE (e.g. a hostname with A records but no AAAA at
    all -- the normal, common case for a domain without IPv6) is not a
    failure on its own: each record type's own DNSException is caught
    independently, contributing zero IPs for that type, not failing the
    whole lookup. Only the COMBINED result across both types, after both
    queries have run, decides pass/fail -- both "no records anywhere" and
    "at least one unsafe IP anywhere" collapse to the identical empty-list
    return, deliberately indistinguishable from the caller's own side,
    matching this project's own no-oracle discipline elsewhere.
    """
    resolver = dns.asyncresolver.Resolver()
    resolver.lifetime = _DNS_QUERY_TIMEOUT_SECONDS

    ips: list[ipaddress.IPv4Address | ipaddress.IPv6Address] = []
    for rdtype in ("A", "AAAA"):
        try:
            answer = await resolver.resolve(hostname, rdtype)
        except dns.exception.DNSException:
            continue
        ips.extend(ipaddress.ip_address(rdata.address) for rdata in answer)

    if not ips:
        return []
    if any(is_unsafe_destination_ip(ip) for ip in ips):
        return []
    return ips
