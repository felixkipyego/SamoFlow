# backend/tests/test_client_ip.py
# [SECURITY] L1 PART C (#7) (out-of-band Phase 7 batch, 2026-10-17): unit
# tests for app/client_ip.py's trust boundary. Real fastapi.Request
# objects built from a raw ASGI scope (not a hand-rolled stub) -- the
# "multiple X-Forwarded-For headers get comma-joined" case specifically
# needs Starlette's own real Headers.get() behavior, which a stub could
# easily get subtly wrong.
#
# Every test injects its own resolver (never a real DNS lookup) and, where
# relevant, its own clock -- same injectable-clock discipline as
# TrustedProxyCache's own sibling, app/auth/dependencies.py's StatusCache.
from fastapi import FastAPI, Request

from app.client_ip import TrustedProxyCache, get_client_ip
from app.config import get_settings
from tests.conftest import VALID_ENV, set_valid_env


def _make_request(client_host, headers=None, app=None):
    encoded_headers = [
        (name.lower().encode(), value.encode()) for name, value in (headers or [])
    ]
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/",
        "query_string": b"",
        "server": ("test", 80),
        "scheme": "http",
        "root_path": "",
        "headers": encoded_headers,
        "client": (client_host, 12345) if client_host is not None else None,
        "app": app if app is not None else FastAPI(),
    }
    return Request(scope)


def _settings(monkeypatch, **overrides):
    set_valid_env(monkeypatch, VALID_ENV, **overrides)
    get_settings.cache_clear()
    return get_settings()


def test_no_client_returns_unknown(monkeypatch):
    settings = _settings(monkeypatch)
    request = _make_request(None)
    assert get_client_ip(request, settings) == "unknown"


def test_unresolvable_trusted_proxy_hostname_falls_back_to_peer(monkeypatch):
    # The real-world dev/test case: TRUSTED_PROXY_HOSTNAME defaults to
    # "caddy", and no such service/hostname exists outside a real Caddy
    # deployment -- DNS resolution fails, nothing is ever trusted, every
    # request behaves exactly as it did before this helper existed.
    settings = _settings(monkeypatch)
    app = FastAPI()
    app.state.trusted_proxy_cache = TrustedProxyCache(resolver=lambda host: frozenset())
    request = _make_request(
        "203.0.113.5", headers=[("X-Forwarded-For", "198.51.100.9")], app=app
    )
    assert get_client_ip(request, settings) == "203.0.113.5"


def test_untrusted_peer_spoofed_header_is_ignored(monkeypatch):
    # A spoofed XFF sent by a peer that is NOT the configured trusted
    # proxy -- the real adversarial case this helper exists to stop.
    settings = _settings(monkeypatch)
    app = FastAPI()
    app.state.trusted_proxy_cache = TrustedProxyCache(
        resolver=lambda host: frozenset({"10.0.0.5"})
    )
    request = _make_request(
        "203.0.113.5", headers=[("X-Forwarded-For", "198.51.100.9")], app=app
    )
    assert get_client_ip(request, settings) == "203.0.113.5"


def test_trusted_peer_single_xff_value_is_used(monkeypatch):
    settings = _settings(monkeypatch)
    app = FastAPI()
    app.state.trusted_proxy_cache = TrustedProxyCache(
        resolver=lambda host: frozenset({"10.0.0.5"})
    )
    request = _make_request(
        "10.0.0.5", headers=[("X-Forwarded-For", "198.51.100.9")], app=app
    )
    assert get_client_ip(request, settings) == "198.51.100.9"


def test_trusted_peer_chain_uses_the_rightmost_entry(monkeypatch):
    # A chain whose spoofed left-most hop tries to dodge a rate limit --
    # only the right-most entry (the one the trusted proxy itself added)
    # is ever honoured.
    settings = _settings(monkeypatch)
    app = FastAPI()
    app.state.trusted_proxy_cache = TrustedProxyCache(
        resolver=lambda host: frozenset({"10.0.0.5"})
    )
    request = _make_request(
        "10.0.0.5",
        headers=[("X-Forwarded-For", "198.51.100.9, 203.0.113.7")],
        app=app,
    )
    assert get_client_ip(request, settings) == "203.0.113.7"


def test_trusted_peer_malformed_rightmost_entry_falls_back_to_peer(monkeypatch):
    settings = _settings(monkeypatch)
    app = FastAPI()
    app.state.trusted_proxy_cache = TrustedProxyCache(
        resolver=lambda host: frozenset({"10.0.0.5"})
    )
    request = _make_request(
        "10.0.0.5", headers=[("X-Forwarded-For", "not-an-ip-address")], app=app
    )
    assert get_client_ip(request, settings) == "10.0.0.5"


def test_trusted_peer_empty_header_falls_back_to_peer(monkeypatch):
    settings = _settings(monkeypatch)
    app = FastAPI()
    app.state.trusted_proxy_cache = TrustedProxyCache(
        resolver=lambda host: frozenset({"10.0.0.5"})
    )
    request = _make_request("10.0.0.5", headers=[("X-Forwarded-For", "")], app=app)
    assert get_client_ip(request, settings) == "10.0.0.5"


def test_trusted_peer_ipv6_value_is_accepted(monkeypatch):
    settings = _settings(monkeypatch)
    app = FastAPI()
    app.state.trusted_proxy_cache = TrustedProxyCache(
        resolver=lambda host: frozenset({"10.0.0.5"})
    )
    request = _make_request(
        "10.0.0.5", headers=[("X-Forwarded-For", "2001:db8::1")], app=app
    )
    assert get_client_ip(request, settings) == "2001:db8::1"


def test_trusted_peer_private_range_value_is_accepted(monkeypatch):
    # Not rejected -- a visitor reaching this deployment over a private
    # network (a real, legitimate topology, e.g. a staging environment
    # behind a VPN) still gets its own real address used, not "unknown".
    settings = _settings(monkeypatch)
    app = FastAPI()
    app.state.trusted_proxy_cache = TrustedProxyCache(
        resolver=lambda host: frozenset({"10.0.0.5"})
    )
    request = _make_request(
        "10.0.0.5", headers=[("X-Forwarded-For", "192.168.1.42")], app=app
    )
    assert get_client_ip(request, settings) == "192.168.1.42"


def test_trusted_peer_multiple_xff_headers_are_comma_joined_rightmost_wins(monkeypatch):
    # Starlette's own Headers.get() joins repeated same-named headers
    # with ", " -- a request smuggling two separate X-Forwarded-For
    # header LINES is indistinguishable from one comma-joined value by
    # the time get_client_ip() ever sees it.
    settings = _settings(monkeypatch)
    app = FastAPI()
    app.state.trusted_proxy_cache = TrustedProxyCache(
        resolver=lambda host: frozenset({"10.0.0.5"})
    )
    request = _make_request(
        "10.0.0.5",
        headers=[
            ("X-Forwarded-For", "198.51.100.1"),
            ("X-Forwarded-For", "198.51.100.2"),
        ],
        app=app,
    )
    assert get_client_ip(request, settings) == "198.51.100.2"


def test_x_real_ip_used_when_no_xff_present(monkeypatch):
    settings = _settings(monkeypatch)
    app = FastAPI()
    app.state.trusted_proxy_cache = TrustedProxyCache(
        resolver=lambda host: frozenset({"10.0.0.5"})
    )
    request = _make_request(
        "10.0.0.5", headers=[("X-Real-IP", "198.51.100.3")], app=app
    )
    assert get_client_ip(request, settings) == "198.51.100.3"


def test_xff_preferred_over_x_real_ip_when_both_present(monkeypatch):
    settings = _settings(monkeypatch)
    app = FastAPI()
    app.state.trusted_proxy_cache = TrustedProxyCache(
        resolver=lambda host: frozenset({"10.0.0.5"})
    )
    request = _make_request(
        "10.0.0.5",
        headers=[("X-Forwarded-For", "198.51.100.9"), ("X-Real-IP", "198.51.100.3")],
        app=app,
    )
    assert get_client_ip(request, settings) == "198.51.100.9"


def test_cache_is_created_lazily_on_app_state(monkeypatch):
    settings = _settings(monkeypatch)
    app = FastAPI()
    request = _make_request("10.0.0.5", app=app)
    assert not hasattr(app.state, "trusted_proxy_cache")
    get_client_ip(request, settings)
    assert isinstance(app.state.trusted_proxy_cache, TrustedProxyCache)


class _FakeClock:
    def __init__(self, start: float = 1_000.0) -> None:
        self._now = start

    def __call__(self) -> float:
        return self._now

    def advance(self, seconds: float) -> None:
        self._now += seconds


def test_trusted_proxy_cache_only_resolves_once_within_the_ttl():
    clock = _FakeClock()
    calls = []

    def resolver(hostname):
        calls.append(hostname)
        return frozenset({"10.0.0.5"})

    cache = TrustedProxyCache(clock=clock, resolver=resolver)
    assert cache.resolve("caddy") == frozenset({"10.0.0.5"})
    assert cache.resolve("caddy") == frozenset({"10.0.0.5"})
    assert calls == ["caddy"]  # second call served from cache, no new resolve


def test_trusted_proxy_cache_resolves_again_after_ttl_expires():
    clock = _FakeClock()
    calls = []

    def resolver(hostname):
        calls.append(hostname)
        return frozenset({"10.0.0.5"})

    cache = TrustedProxyCache(clock=clock, resolver=resolver)
    cache.resolve("caddy")
    clock.advance(61.0)
    cache.resolve("caddy")
    assert calls == ["caddy", "caddy"]


def test_default_resolver_catches_a_real_getaddrinfo_failure(monkeypatch):
    # app/client_ip.py's own module-level _resolve_hostname (the default
    # TrustedProxyCache resolver) must degrade to "nothing resolved", not
    # raise -- proved against socket.getaddrinfo itself raising (not a
    # real, possibly network-dependent DNS lookup, which would make this
    # test flaky in a sandboxed/offline environment).
    import socket

    from app.client_ip import _resolve_hostname

    def raising_getaddrinfo(*args, **kwargs):
        raise OSError("name resolution failed")

    monkeypatch.setattr(socket, "getaddrinfo", raising_getaddrinfo)
    assert _resolve_hostname("caddy") == frozenset()
