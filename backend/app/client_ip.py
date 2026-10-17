# backend/app/client_ip.py
# [SECURITY] L1 PART C (#7) (out-of-band Phase 7 batch, 2026-10-17): the
# ONE place a request's visitor IP is derived -- every rate limiter
# (app/chat/routes.py, app/auth/routes.py, app/dashboard/routes.py) calls
# get_client_ip() instead of reading request.client.host (or a header)
# directly. audit_log/usage_events capture no IP at all today (checked
# live: app/ingest/models.py's AuditLog has no ip_address column, and
# nothing else derives one) -- there is nothing to migrate there yet, but
# this is the function any future caller must use too.
#
# Trust boundary: X-Forwarded-For/X-Real-IP are honoured ONLY when the
# immediate TCP peer (request.client.host) is the configured trusted
# proxy -- Caddy, on the compose network. Resolved by DNS hostname
# (Settings.trusted_proxy_hostname, default "caddy"), not a hardcoded
# CIDR: deploy/docker-compose.yml assigns no static subnet (Docker's own
# dynamic pool), so a hardcoded range would be unreliable and strictly
# worse than Docker's own collision-avoiding auto-assignment. Otherwise
# the socket peer itself is used -- identical to every one of today's
# existing `request.client.host if request.client else "unknown"` call
# sites, so a request that never goes through Caddy (direct container-
# to-container access, or any dev/test environment with no "caddy"
# service to resolve at all) behaves exactly as it does today.
#
# A chain is handled by taking the RIGHT-MOST entry only (and only after
# confirming it parses as a real IP address -- a malformed value falls
# through to the socket peer, never raises). This is deliberately
# defensive, not merely decorative: confirmed live against Caddy 2.10.2's
# own docs (reverse_proxy directive; the global `trusted_proxies`
# server option) that when the immediate peer is untrusted -- which,
# with no `trusted_proxies` configured in deploy/Caddyfile (the correct,
# secure default: this deployment has no CDN or other proxy in front of
# Caddy itself, docs/SPEC.md §14), is every request Caddy ever receives
# -- Caddy "ignores [the] values from incoming requests, to prevent
# spoofing" and sets a fresh X-Forwarded-For containing only the real
# connecting peer. In this architecture that header therefore always
# arrives at the api service as a single value; taking the right-most
# entry anyway costs nothing and is what keeps this helper correct if a
# second hop (a future CDN in front of Caddy, configured with its own
# `trusted_proxies`) is ever added, without anyone having to remember to
# revisit this file.
import socket
import time
from collections.abc import Callable
from dataclasses import dataclass
from ipaddress import ip_address

from fastapi import Request

from app.config import Settings

_CACHE_TTL_SECONDS = 60.0
_TRUSTED_HEADERS: tuple[str, ...] = ("x-forwarded-for", "x-real-ip")


@dataclass
class _CachedResolution:
    ips: frozenset[str]
    cached_at: float


def _resolve_hostname(hostname: str) -> frozenset[str]:
    try:
        infos = socket.getaddrinfo(hostname, None)
    except OSError:
        # Unresolvable (no such service -- every development/test
        # environment, which has no "caddy" service at all) is the safe
        # default: an empty set can never match any real peer, so the
        # helper below falls back to the socket peer, unchanged from
        # today's behavior.
        return frozenset()
    return frozenset(info[4][0] for info in infos)


class TrustedProxyCache:
    # Same injectable-clock TTL-cache shape as app/auth/dependencies.py's
    # StatusCache (Task 1.4.h) -- one entry (there is only ever one
    # configured trusted-proxy hostname), refreshed by a real DNS lookup
    # at most once per _CACHE_TTL_SECONDS, so a rename/IP change of the
    # Caddy container (a restart, a redeploy) is picked up within a
    # minute without a DNS lookup on every single request.
    def __init__(
        self,
        clock: Callable[[], float] = time.monotonic,
        resolver: Callable[[str], frozenset[str]] = _resolve_hostname,
    ) -> None:
        self.clock = clock
        self._resolver = resolver
        self._entry: _CachedResolution | None = None

    def resolve(self, hostname: str) -> frozenset[str]:
        entry = self._entry
        if entry is not None and self.clock() - entry.cached_at < _CACHE_TTL_SECONDS:
            return entry.ips
        ips = self._resolver(hostname)
        self._entry = _CachedResolution(ips, self.clock())
        return ips


def _get_trusted_proxy_cache(request: Request) -> TrustedProxyCache:
    # Lives on app.state, exactly like StatusCache -- a fresh create_app()
    # gets a fresh cache with no explicit reset needed between tests, and
    # tests can pre-seed app.state.trusted_proxy_cache with an injectable
    # clock/resolver before making a request.
    cache = getattr(request.app.state, "trusted_proxy_cache", None)
    if cache is None:
        cache = TrustedProxyCache()
        request.app.state.trusted_proxy_cache = cache
    return cache


def _rightmost_valid_ip(raw: str) -> str | None:
    # Starlette's own Headers.get() already joins repeated same-named
    # headers with ", " -- confirmed live against the installed starlette
    # that this is NOT the case (Headers.get()/__getitem__ returns only
    # the FIRST matching header line; Headers.getlist() is what returns
    # every one). get_client_ip() below calls getlist() and joins the
    # results itself before calling this function, so a request
    # smuggling multiple X-Forwarded-For header LINES is handled the
    # same way as one comma-joined value by the time this function ever
    # runs.
    for candidate in reversed([part.strip() for part in raw.split(",")]):
        if not candidate:
            continue
        try:
            ip_address(candidate)
        except ValueError:
            # The right-most entry is malformed -- never fall further
            # left (everything left of a trusted proxy's own entry is,
            # by definition, attacker-supplied): treat the whole header
            # as unusable.
            return None
        return candidate
    return None


def get_client_ip(request: Request, settings: Settings) -> str:
    peer = request.client.host if request.client else None
    if peer is None:
        return "unknown"

    cache = _get_trusted_proxy_cache(request)
    trusted_ips = cache.resolve(settings.trusted_proxy_hostname)
    if peer not in trusted_ips:
        # Not connecting through the configured trusted proxy -- use the
        # raw socket peer, exactly like every pre-existing call site did
        # before this helper existed. Any X-Forwarded-For/X-Real-IP on
        # this request is attacker-controlled and ignored entirely.
        return peer

    for header_name in _TRUSTED_HEADERS:
        values = request.headers.getlist(header_name)
        if not values:
            continue
        raw = ",".join(values)
        candidate = _rightmost_valid_ip(raw)
        if candidate is not None:
            return candidate
    return peer
