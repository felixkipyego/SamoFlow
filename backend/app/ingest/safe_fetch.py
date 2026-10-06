# backend/app/ingest/safe_fetch.py
# Task 2.3.c [SECURITY]: the single-fetch primitive -- the first piece of
# Step 2.3 that actually makes an outbound network connection. Builds on
# 2.3.a's is_unsafe_destination_ip() and 2.3.b's resolve_and_validate();
# this is the one place httpx is imported anywhere in this step (and the
# reason httpx was promoted to a direct runtime dependency at this task).
#
# Mechanism, confirmed live during Step 2.3's own research before writing
# this: a request is built against the VALIDATED IP LITERAL (never the
# original hostname -- this is what "pinning" means mechanically), with
# an explicit Host header carrying the original hostname (httpx's own
# _prepare() only auto-generates a Host header when the caller hasn't
# already supplied one, confirmed by reading its installed source) and
# extensions={"sni_hostname": ...} (httpcore's own mechanism: the TLS
# handshake's server_hostname -- used for BOTH the SNI ClientHello field
# AND certificate hostname verification -- comes from this extension when
# present). httpx.URL.copy_with(host=...) handles both IPv4 and IPv6
# literals correctly, including IPv6 bracketing, confirmed live.
#
# Task 2.3.d adds fetch_with_redirects(): safe_fetch() itself stays
# EXACTLY what 2.3.c built -- a single hop, no redirect following, same
# signature, same bytes-only return, same behavior for any non-redirect
# response (confirmed unchanged by re-running 2.3.c's own test suite
# unmodified). fetch_with_redirects() is a new, separate entry point that
# wraps the same validation+fetch machinery in a loop, re-running the
# ENTIRE validation cycle from scratch on each new hop's target -- no
# validation state carried over from the previous hop, matching this
# step's own core safety property applied per redirect, not just once.
import ipaddress
from dataclasses import dataclass

import httpx

from app.ingest.host_safety import resolve_and_validate
from app.ingest.ip_safety import is_unsafe_destination_ip


class UnsafeFetchError(Exception):
    """Raised by safe_fetch()/fetch_with_redirects() for every rejection
    reason -- an unsupported scheme, no safe IP to connect to for the
    given host, a response exceeding max_size_bytes, or (fetch_with_
    redirects() only) exceeding max_redirects. Deliberately one exception
    for every reason, not one per cause: this project's own no-oracle
    discipline elsewhere (e.g. a wrong secret and no secret producing the
    identical failure) applies here too -- a caller has no legitimate
    need to distinguish WHY a fetch was rejected, only that it was.
    (Retry-worthy vs. permanent-rejection distinctions, if a real caller
    ever needs one, are that caller's own job to add later -- not built
    speculatively here for callers that don't exist yet.)
    """


@dataclass(frozen=True)
class FetchResult:
    # Task 2.6.c's own duplication check (C1 [SECURITY] fix): fetch_with_
    # redirects()'s own public return type, widened from a bare `bytes`
    # so a caller can tell whether a redirect moved it to a DIFFERENT
    # host than the one it originally asked for -- a real gap, not a
    # hypothetical one: web_adapter.py's ingest_url() checks
    # verified_domains against the ORIGINAL url's host before fetching,
    # but fetch_with_redirects() re-validates only SSRF-safety (the IP)
    # on each hop, with no concept of tenant domain verification at all.
    # Without this, a verified domain redirecting to a different,
    # unverified-but-IP-safe external host would have that host's own
    # content fetched and persisted under the tenant's own `documents`
    # row -- defeating domain verification's actual purpose (proving
    # tenant control over the content source), not merely an SSRF
    # concern. safe_fetch() deliberately stays untouched, still returning
    # bare `bytes`: it never follows a redirect, so "final" is never
    # distinct from "original" for it -- adding this field there would
    # be a field with only one possible value, pure over-engineering
    # (rule 11).
    body: bytes
    final_url: httpx.URL


@dataclass(frozen=True)
class _PinnedResponse:
    # Task 2.3.d: _fetch_pinned()'s own return type, widened from a bare
    # `bytes` (2.3.c's original shape) to this, so fetch_with_redirects()
    # can inspect status_code/headers to decide whether to follow a
    # redirect. safe_fetch() itself immediately narrows back to `.body`
    # for its own unchanged public contract -- this type is internal to
    # this module, never part of either public function's own signature.
    status_code: int
    headers: httpx.Headers
    body: bytes


async def _fetch_pinned(
    original_url: httpx.URL,
    pinned_ip: ipaddress.IPv4Address | ipaddress.IPv6Address,
    sni_hostname: str,
    *,
    max_size_bytes: int,
    timeout_seconds: float,
) -> _PinnedResponse:
    """The actual pinned-connection fetch, factored out from safe_fetch()
    so the TLS/SNI mechanism itself can be exercised directly in tests
    (a deliberately WRONG sni_hostname, while still connecting to a real,
    already-validated-safe IP) without needing to re-drive every
    validation step safe_fetch() already covers separately. Never follows
    a redirect itself (follow_redirects=False, explicit) -- it is this
    module's own sole place that ever makes a real connection; both
    safe_fetch() and fetch_with_redirects() call it, never anything else.
    """
    pinned_url = original_url.copy_with(host=str(pinned_ip))
    headers = {"Host": original_url.netloc.decode("ascii")}

    async with httpx.AsyncClient(follow_redirects=False) as client:
        async with client.stream(
            "GET",
            pinned_url,
            headers=headers,
            extensions={"sni_hostname": sni_hostname},
            timeout=timeout_seconds,
        ) as response:
            body = bytearray()
            # Streamed and checked per chunk, never buffered in full
            # first: a response that exceeds the cap is abandoned the
            # instant that becomes true, not after downloading everything
            # and discovering it was too big. Raising inside this loop,
            # while still inside the `async with client.stream(...)`
            # block, closes the response (and the underlying connection)
            # as part of handling the exception -- no further bytes are
            # ever read off the socket afterward.
            async for chunk in response.aiter_bytes():
                body.extend(chunk)
                if len(body) > max_size_bytes:
                    raise UnsafeFetchError(
                        f"response exceeded max_size_bytes ({max_size_bytes})"
                    )
            return _PinnedResponse(response.status_code, response.headers, bytes(body))


async def _validate_and_pick_ip(
    original_url: httpx.URL,
) -> ipaddress.IPv4Address | ipaddress.IPv6Address:
    """Shared by safe_fetch() and fetch_with_redirects() -- the scheme
    check, literal-IP-or-DNS classification, and all-or-nothing IP
    validation, exactly as 2.3.c originally built it inline. Factored out
    at 2.3.d so fetch_with_redirects() can re-run this EXACT same cycle,
    from scratch, on every new redirect target, with zero special-casing
    for "this is hop 2, not the first request."
    """
    if original_url.scheme not in ("http", "https"):
        raise UnsafeFetchError(f"unsupported scheme {original_url.scheme!r}")

    hostname = original_url.host
    if not hostname:
        raise UnsafeFetchError("URL has no host")

    # A literal IP host (e.g. "http://169.254.169.254/") is classified
    # directly, with NO DNS lookup at all -- resolve_and_validate() exists
    # to resolve a HOSTNAME; handing it an IP-shaped string would make it
    # issue a real, pointless (and for this exact case, network-visible)
    # DNS query for a "name" that is actually already an address. This
    # also matters for what this module's own tests can prove: a rejected
    # literal-IP target must cause literally zero network traffic of any
    # kind, DNS included, not just zero HTTP connections.
    try:
        literal_ip = ipaddress.ip_address(hostname)
    except ValueError:
        literal_ip = None

    if literal_ip is not None:
        safe_ips = [] if is_unsafe_destination_ip(literal_ip) else [literal_ip]
    else:
        safe_ips = await resolve_and_validate(hostname)

    if not safe_ips:
        raise UnsafeFetchError(f"no safe IP address to connect to for {hostname!r}")
    return safe_ips[0]


async def safe_fetch(
    url: str,
    *,
    max_size_bytes: int = 5_000_000,
    timeout_seconds: float = 15.0,
) -> bytes:
    """Fetches `url`, rejecting it outright (UnsafeFetchError, no network
    activity at all) unless: the scheme is http or https; the host
    resolves (or, if it is already a literal IP, classifies) to at least
    one safe IP, with every resolved IP safe -- resolve_and_validate()'s
    own all-or-nothing contract (2.3.b) means there is never a partial
    safe subset to pick from here. Defaults match docs/SPEC.md §5.5
    exactly: 5MB (decimal, the stricter of the two plausible readings of
    "5MB"), 15s. Does not follow redirects -- a 3xx response is returned
    exactly like any other (fetch_with_redirects(), 2.3.d, is the
    redirect-aware entry point).
    """
    original_url = httpx.URL(url)
    pinned_ip = await _validate_and_pick_ip(original_url)
    result = await _fetch_pinned(
        original_url,
        pinned_ip,
        original_url.host,
        max_size_bytes=max_size_bytes,
        timeout_seconds=timeout_seconds,
    )
    return result.body


async def fetch_with_redirects(
    url: str,
    *,
    max_size_bytes: int = 5_000_000,
    timeout_seconds: float = 15.0,
    max_redirects: int = 3,
) -> FetchResult:
    """Like safe_fetch(), but follows up to `max_redirects` redirects
    (default matches docs/SPEC.md §5.5's own "max 3 redirects"). Every
    hop -- including the first request and every redirect target after
    it -- runs the EXACT SAME validation cycle from scratch
    (_validate_and_pick_ip(), then a fresh _fetch_pinned() call with the
    full max_size_bytes/timeout_seconds budget of its own, never a
    cumulative one shared across hops -- docs/SPEC.md's own "5MB per
    page" reads naturally as per RESPONSE, and a redirect hop's own
    response is its own distinct fetch, not a continuation of a prior
    one). No validation state from one hop is ever reused for the next:
    a safe hop 1 proves nothing about hop 2's own target, which gets the
    identical scrutiny hop 1 did, including a completely fresh DNS
    resolution if its target is a hostname.

    Relative Location headers are resolved against the CURRENT hop's own
    URL via httpx.URL.join() (handles absolute, root-relative,
    path-relative and protocol-relative forms correctly per RFC 3986,
    confirmed live -- not hand-rolled). Exceeding max_redirects fails
    closed with UnsafeFetchError, never an infinite loop and never a
    silent truncation of the chain.

    Returns a FetchResult, not bare bytes (Task 2.6.c's own duplication
    check, C1 [SECURITY] fix) -- `final_url` is this function's own
    `current_url` at the hop that actually returned content, letting a
    caller that cares about content PROVENANCE (not just SSRF-safety,
    which every hop already gets re-validated for above) tell whether a
    redirect moved it to a different host than the one it started with.
    """
    current_url = httpx.URL(url)
    for hop in range(max_redirects + 1):
        pinned_ip = await _validate_and_pick_ip(current_url)
        result = await _fetch_pinned(
            current_url,
            pinned_ip,
            current_url.host,
            max_size_bytes=max_size_bytes,
            timeout_seconds=timeout_seconds,
        )
        location = result.headers.get("location")
        if not httpx.codes.is_redirect(result.status_code) or location is None:
            return FetchResult(body=result.body, final_url=current_url)
        if hop == max_redirects:
            raise UnsafeFetchError(
                f"exceeded max_redirects ({max_redirects}) while following redirects "
                f"from {url!r}"
            )
        current_url = current_url.join(location)
    raise AssertionError("unreachable: the loop above always returns or raises")
