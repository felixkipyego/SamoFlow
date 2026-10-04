# backend/app/ingest/ip_safety.py
# Task 2.3.a [SECURITY]: the foundational IP-classification primitive for
# Step 2.3's own SSRF guard (docs/SPEC.md §5.5/§16) -- every later piece of
# this step (DNS validation, the pinned fetch itself, redirect-chain
# handling) calls this function to decide whether a resolved IP is safe to
# connect to. Deliberately its own module, not part of safe_fetch.py
# (built starting 2.3.c): docs/SPEC.md §5.6 implies the database-sync
# adapter (Step 2.8) needs this exact same classification for a
# NON-HTTP protocol (a raw database connection, not a fetch) -- keeping it
# here means importing it never pulls in httpx or anything else
# fetch-specific, which living inside safe_fetch.py would do from 2.3.c
# onward. Pure, offline, protocol-agnostic: one IP address object in, one
# boolean out, no I/O.
#
# Design philosophy: an ALLOW-list of known-safe properties, not a
# DENY-list of known-unsafe ranges. is_unsafe_destination_ip() does not ask
# "is this IP in one of the ranges I've decided are dangerous" -- it asks
# "is this IP a normal, global, unicast, assigned address", and rejects
# everything else, including ranges nobody thought to name. This matters
# concretely, not just philosophically: a deny-list built from
# ip.is_private alone (the obvious-looking alternative) silently passes
# CGNAT addresses (100.64.0.0/10, is_private=False) and multicast
# (is_private=False) straight through, verified live before writing this
# module. Both are real gaps a deny-list would reintroduce. A future
# reader who "simplifies" this back into a deny-list of named ranges will
# reopen exactly that gap -- don't.
import ipaddress


def is_unsafe_destination_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """True if `ip` must not be connected to -- anything that is not a
    normal, globally-routable, non-multicast, non-reserved, non-unspecified
    unicast address. Covers (verified live against the installed Python's
    ipaddress module, both IPv4 and IPv6): RFC 1918 private ranges,
    loopback, link-local (including the cloud-metadata address
    169.254.169.254), IPv6 unique-local, CGNAT (100.64.0.0/10), multicast,
    and anything else `ipaddress` does not classify as a real, assigned,
    global unicast address (e.g. IANA reserved-for-future-use ranges like
    240.0.0.0/4) -- fail-closed on the unknown case, not fail-open.
    """
    return not ip.is_global or ip.is_multicast or ip.is_reserved or ip.is_unspecified
