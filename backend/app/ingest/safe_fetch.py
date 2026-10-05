# backend/app/ingest/safe_fetch.py
# Task 2.3.c [SECURITY]: the single-fetch primitive -- the first piece of
# Step 2.3 that actually makes an outbound network connection. Builds on
# 2.3.a's is_unsafe_destination_ip() and 2.3.b's resolve_and_validate();
# this is the one place httpx is imported anywhere in this step (and the
# reason httpx was promoted to a direct runtime dependency at this task).
# Deliberately NOT redirect-aware: follow_redirects=False is set
# explicitly below, and a 3xx response is returned to the caller exactly
# like any other response -- following a redirect safely (re-validating
# the new target from scratch at every hop) is 2.3.d's own job, not this
# function's.
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
import ipaddress

import httpx

from app.ingest.host_safety import resolve_and_validate
from app.ingest.ip_safety import is_unsafe_destination_ip


class UnsafeFetchError(Exception):
    """Raised by safe_fetch() for every rejection reason -- an unsupported
    scheme, no safe IP to connect to for the given host, or a response
    exceeding max_size_bytes. Deliberately one exception for every reason,
    not one per cause: this project's own no-oracle discipline elsewhere
    (e.g. a wrong secret and no secret producing the identical failure)
    applies here too -- a caller has no legitimate need to distinguish
    WHY a fetch was rejected, only that it was. (Retry-worthy vs.
    permanent-rejection distinctions, if a real caller ever needs one,
    are that caller's own job to add later -- not built speculatively
    here for callers that don't exist yet.)
    """


async def _fetch_pinned(
    original_url: httpx.URL,
    pinned_ip: ipaddress.IPv4Address | ipaddress.IPv6Address,
    sni_hostname: str,
    *,
    max_size_bytes: int,
    timeout_seconds: float,
) -> bytes:
    """The actual pinned-connection fetch, factored out from safe_fetch()
    so the TLS/SNI mechanism itself can be exercised directly in tests
    (a deliberately WRONG sni_hostname, while still connecting to a real,
    already-validated-safe IP) without needing to re-drive every
    validation step safe_fetch() already covers separately. Never called
    directly by anything other than safe_fetch() and this module's own
    tests.
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
            return bytes(body)


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
    "5MB"), 15s. Does not follow redirects (2.3.d's own job).
    """
    original_url = httpx.URL(url)
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

    return await _fetch_pinned(
        original_url,
        safe_ips[0],
        hostname,
        max_size_bytes=max_size_bytes,
        timeout_seconds=timeout_seconds,
    )
