# backend/tests/test_safe_fetch.py
# Task 2.3.c: tests for safe_fetch()/_fetch_pinned() (backend/app/ingest/
# safe_fetch.py). Two groups, matching the task's own split:
#   - offline/structural (mocked resolve_and_validate(), zero network):
#     a non-http(s) scheme, and a host resolve_and_validate() rejects --
#     both proven to cause ZERO connection attempts, not just a clean
#     exception.
#   - live, real network, run standalone first: a real public HTTPS
#     fetch (content + genuine TLS/SNI certificate verification); a
#     response exceeding max_size_bytes aborted mid-stream, proven by a
#     real timing measurement against a local server under our control
#     (127.0.0.1 is categorically unsafe by design, so is_unsafe_
#     destination_ip() is deliberately monkeypatched for this ONE test,
#     clearly labeled -- this test is about the streaming/size-cap
#     mechanism, not the SSRF check); literal private/loopback/metadata
#     IP URLs rejected with proof of zero bytes ever sent; and
#     follow_redirects=False genuinely enforced, not just set in the
#     code -- a real 3xx response comes back raw and unfollowed.
#
# Duplication check after 2.3.a/b/c: the socket.socket.connect-raises
# proof and the local-HTTPServer lifecycle are now shared helpers
# (tests/conftest.py's assert_no_socket_connections()/local_http_server())
# instead of being repeated inline here -- each caller below still
# defines its own handler class where the behavior actually differs.
#
# Task 2.3.d: fetch_with_redirects()'s own tests, against local servers
# under this file's own control (never real third-party redirects, for
# the same reliability reasons 2.3.c's own size-cap test already gives).
# _fake_is_unsafe_except_loopback() is this file's own small, local
# helper (not extracted to conftest.py -- only this file needs it so
# far): 127.0.0.1 is categorically unsafe by design, so every redirect
# test below treats it as safe FOR TEST PURPOSES ONLY, while every OTHER
# address still goes through the real, unmodified is_unsafe_destination_ip()
# -- this is what lets a chain's later hop (a genuinely different,
# untouched address) still be rejected for real within the same test.
import http.server
import ipaddress
import socket
import time

import httpx
import pytest

from app.ingest import safe_fetch as safe_fetch_module
from app.ingest.host_safety import resolve_and_validate
from app.ingest.ip_safety import is_unsafe_destination_ip as _real_is_unsafe_destination_ip
from app.ingest.safe_fetch import (
    UnsafeFetchError,
    _fetch_pinned,
    fetch_with_redirects,
    safe_fetch,
)
from tests.conftest import assert_no_socket_connections, local_http_server


def _fake_is_unsafe_except_loopback(ip) -> bool:
    if str(ip) == "127.0.0.1":
        return False
    return _real_is_unsafe_destination_ip(ip)


def _scripted_handler(routes: dict):
    # routes: path -> (status_code, extra_headers_dict, body_bytes).
    # A fresh class per call, closing over `routes` -- the dict can be
    # mutated by the test AFTER local_http_server() has started (needed
    # since a Location header often must name the server's own,
    # only-known-after-start port).
    class _Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            status, headers, body = routes.get(self.path, (404, {}, b"not found"))
            self.send_response(status)
            for key, value in headers.items():
                self.send_header(key, value)
            self.end_headers()
            if body:
                self.wfile.write(body)

        def log_message(self, *args):
            pass

    return _Handler

# --- Offline / structural ---------------------------------------------------


async def test_non_http_scheme_is_rejected_before_any_resolution(monkeypatch):
    def _fail_if_called(hostname):
        raise AssertionError(
            f"resolve_and_validate({hostname!r}) was called for a non-http(s) scheme"
        )

    monkeypatch.setattr(safe_fetch_module, "resolve_and_validate", _fail_if_called)

    for url in ("ftp://example.com/", "file:///etc/passwd", "gopher://example.com/"):
        with pytest.raises(UnsafeFetchError):
            await safe_fetch(url)


async def test_host_that_fails_validation_is_rejected_with_zero_connection_attempts(
    monkeypatch,
):
    async def _fake_resolve_and_validate(hostname):
        return []  # simulates any resolve_and_validate() rejection reason

    monkeypatch.setattr(
        safe_fetch_module, "resolve_and_validate", _fake_resolve_and_validate
    )
    assert_no_socket_connections(monkeypatch)

    with pytest.raises(UnsafeFetchError):
        await safe_fetch("http://looks-innocuous.example/")


# --- Live, real network -----------------------------------------------------


async def test_safe_fetch_against_a_real_public_https_url_succeeds_with_real_content():
    body = await safe_fetch("https://example.com/")
    # example.com's own page has carried this exact string for years --
    # matching this project's own established convention (the DNS-fix
    # task) of trusting this domain's long-term stability for a live test.
    assert b"Example Domain" in body


async def test_cert_verification_genuinely_checks_the_sni_hostname_not_bypassed():
    # Exercises the EXACT mechanism safe_fetch() uses internally
    # (_fetch_pinned), with a deliberately WRONG sni_hostname while still
    # connecting to example.com's own real, validated-safe IP -- proving
    # certificate verification is genuinely performed against whatever
    # hostname is given, not silently skipped just because the connection
    # target is a raw IP literal. Confirmed live, stable across repeated
    # runs, before writing this as a permanent test: the real server
    # (Cloudflare-fronted) actively rejects the TLS handshake for an
    # unrecognized SNI value with a real alert, rather than silently
    # serving a default/wrong certificate.
    safe_ips = await resolve_and_validate("example.com")
    assert safe_ips  # sanity: resolution must have genuinely succeeded

    with pytest.raises(httpx.ConnectError) as exc_info:
        await _fetch_pinned(
            httpx.URL("https://example.com/"),
            safe_ips[0],
            "totally-wrong-hostname.example",
            max_size_bytes=5_000_000,
            timeout_seconds=15.0,
        )
    assert "SSL" in str(exc_info.value) or "handshake" in str(exc_info.value).lower()


class _SlowLargeHandler(http.server.BaseHTTPRequestHandler):
    # Streams far more than any reasonable max_size_bytes, slowly enough
    # that "finished quickly" vs. "ran to completion" is unambiguous in a
    # test assertion: 200 chunks * 64KB = 12.8MB total at ~10ms apart
    # (~2s to serve in full) against a cap this test sets far below that.
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "application/octet-stream")
        self.end_headers()
        chunk = b"x" * 65536
        try:
            for _ in range(200):
                self.wfile.write(chunk)
                self.wfile.flush()
                time.sleep(0.01)
        except (BrokenPipeError, ConnectionResetError):
            pass  # expected once the client aborts early

    def log_message(self, *args):
        pass  # silence default request logging to stderr


async def test_response_exceeding_max_size_bytes_is_aborted_during_streaming(
    monkeypatch,
):
    # 127.0.0.1 is categorically unsafe by design -- deliberately bypassed
    # here, and ONLY here, so this test can exercise the real streaming/
    # size-cap mechanism against a server under our own control. This is
    # not a test of the SSRF rejection (that's proven separately, below,
    # against the REAL, unmodified safety check).
    monkeypatch.setattr(safe_fetch_module, "is_unsafe_destination_ip", lambda ip: False)

    with local_http_server(_SlowLargeHandler) as port:
        start = time.monotonic()
        with pytest.raises(UnsafeFetchError):
            await safe_fetch(f"http://127.0.0.1:{port}/", max_size_bytes=100_000)
        elapsed = time.monotonic() - start

    # The full body would take ~2s to serve (200 * 10ms). A real abort at
    # the cap (reached after ~2 chunks, ~20-30ms of sleeps) finishes in a
    # small fraction of that -- a generous but still-proving threshold,
    # not a hair-trigger one that would flake under normal test-machine
    # scheduling jitter.
    assert elapsed < 1.0, (
        f"took {elapsed:.2f}s -- expected an early abort, not a full download"
    )


async def test_literal_unsafe_ip_urls_are_rejected_with_zero_bytes_sent(monkeypatch):
    # The most important test in this subtask: a literal private/
    # loopback/metadata IP target must cause ZERO network activity of any
    # kind -- not just "an exception was raised eventually."
    assert_no_socket_connections(monkeypatch)

    for url in ("http://127.0.0.1/", "http://169.254.169.254/", "http://[::1]/"):
        with pytest.raises(UnsafeFetchError):
            await safe_fetch(url)


class _RedirectToUnsafeHandler(http.server.BaseHTTPRequestHandler):
    # Duplication check after 2.3.a/b/c, item C1: issues a real 3xx
    # pointing at the cloud-metadata IP -- the exact shape of thing 2.3.d
    # must one day re-validate and reject at this hop, not follow.
    def do_GET(self):
        self.send_response(302)
        self.send_header("Location", "http://169.254.169.254/should-never-be-followed")
        self.end_headers()

    def log_message(self, *args):
        pass


async def test_follow_redirects_is_genuinely_enforced_not_just_set(monkeypatch):
    # 2.3.d's entire redirect-recheck design depends on _fetch_pinned()
    # never auto-following a redirect on its own. _fetch_pinned() now
    # exposes status_code/headers (2.3.d's own return-shape change, see
    # PROJECT_SPEC.md's decision log) -- this test now calls it directly
    # instead of duplicating its internal httpx construction, closing the
    # gap the duplication check flagged.
    monkeypatch.setattr(safe_fetch_module, "is_unsafe_destination_ip", lambda ip: False)

    with local_http_server(_RedirectToUnsafeHandler) as port:
        original_url = httpx.URL(f"http://127.0.0.1:{port}/")
        result = await _fetch_pinned(
            original_url,
            ipaddress.ip_address("127.0.0.1"),
            "127.0.0.1",
            max_size_bytes=5_000_000,
            timeout_seconds=15.0,
        )

    assert result.status_code == 302
    assert result.headers["location"] == "http://169.254.169.254/should-never-be-followed"
    assert result.body == b""


# --- Task 2.3.d: fetch_with_redirects() ------------------------------------


async def test_a_single_safe_redirect_is_followed_and_final_content_returned(
    monkeypatch,
):
    monkeypatch.setattr(
        safe_fetch_module, "is_unsafe_destination_ip", _fake_is_unsafe_except_loopback
    )
    routes: dict = {}
    with local_http_server(_scripted_handler(routes)) as port:
        routes["/start"] = (302, {"Location": f"http://127.0.0.1:{port}/final"}, b"")
        routes["/final"] = (200, {}, b"final content here")
        body = await fetch_with_redirects(f"http://127.0.0.1:{port}/start")

    assert body == b"final content here"


async def test_hop_2_pointing_at_an_unsafe_address_is_rejected_with_zero_connection_to_it(
    monkeypatch,
):
    # The key hostile proof: hop 1 is genuinely safe and fully validated
    # (a real connection to our own local server, through the real
    # validation cycle); hop 2's Location points at the cloud-metadata
    # IP, which must be rejected with ZERO connection ever attempted to
    # it. assert_no_socket_connections() (the shared helper) does not fit
    # THIS specific test: it fails on ANY connect call, but hop 1 here
    # legitimately needs one (to our own local server) to produce the
    # real redirect response in the first place. A more surgical patch
    # is used instead -- real connections proceed completely unmodified,
    # except to the one address this test must prove is never reached.
    monkeypatch.setattr(
        safe_fetch_module, "is_unsafe_destination_ip", _fake_is_unsafe_except_loopback
    )

    real_connect = socket.socket.connect

    def _fail_only_for_the_unsafe_target(self, address, *args, **kwargs):
        host = address[0] if isinstance(address, tuple) else address
        if host == "169.254.169.254":
            raise AssertionError(
                "socket.socket.connect() was called for the unsafe hop-2 target"
            )
        return real_connect(self, address, *args, **kwargs)

    monkeypatch.setattr(socket.socket, "connect", _fail_only_for_the_unsafe_target)

    routes: dict = {}
    with local_http_server(_scripted_handler(routes)) as port:
        routes["/start"] = (
            302,
            {"Location": "http://169.254.169.254/should-never-be-reached"},
            b"",
        )
        with pytest.raises(UnsafeFetchError):
            await fetch_with_redirects(f"http://127.0.0.1:{port}/start")


async def test_a_chain_exceeding_the_redirect_cap_fails_closed(monkeypatch):
    # 5 sequential, individually-safe redirects against a default
    # max_redirects=3: hops 0-3 are each a redirect (4 requests made,
    # following 3 of them); the 4th request (hop index 3) is STILL a
    # redirect, which is the 4th redirect -- over the cap, so this must
    # fail closed rather than follow it, and never reach the real "final"
    # content a 5th hop would have returned.
    monkeypatch.setattr(
        safe_fetch_module, "is_unsafe_destination_ip", _fake_is_unsafe_except_loopback
    )
    routes: dict = {}
    with local_http_server(_scripted_handler(routes)) as port:
        base = f"http://127.0.0.1:{port}"
        routes["/r0"] = (302, {"Location": f"{base}/r1"}, b"")
        routes["/r1"] = (302, {"Location": f"{base}/r2"}, b"")
        routes["/r2"] = (302, {"Location": f"{base}/r3"}, b"")
        routes["/r3"] = (302, {"Location": f"{base}/r4"}, b"")
        routes["/r4"] = (200, {}, b"should never be reached")

        with pytest.raises(UnsafeFetchError):
            await fetch_with_redirects(f"{base}/r0", max_redirects=3)


async def test_a_relative_location_header_is_resolved_against_the_current_hop(
    monkeypatch,
):
    monkeypatch.setattr(
        safe_fetch_module, "is_unsafe_destination_ip", _fake_is_unsafe_except_loopback
    )
    routes: dict = {}
    with local_http_server(_scripted_handler(routes)) as port:
        # A bare path, no scheme/host at all -- must resolve against the
        # CURRENT request's own scheme+host+port (httpx.URL.join(),
        # confirmed live to handle this per RFC 3986), not fail or be
        # treated as a literal, scheme-less target.
        routes["/start"] = (302, {"Location": "/relative-target"}, b"")
        routes["/relative-target"] = (200, {}, b"reached via a relative redirect")
        body = await fetch_with_redirects(f"http://127.0.0.1:{port}/start")

    assert body == b"reached via a relative redirect"


async def test_each_hop_gets_its_own_fresh_size_cap_not_a_cumulative_one(monkeypatch):
    # docs/SPEC.md's own "5MB per page" reads naturally as per RESPONSE,
    # not a shared budget across an entire redirect chain -- a redirect
    # hop's own response is its own distinct fetch. Proven directly: two
    # hops, each carrying an 80_000-byte body, against max_size_bytes=
    # 100_000 -- individually under the cap, but their SUM (160_000)
    # would exceed it. A cumulative implementation would fail partway
    # through hop 2; this must succeed, returning hop 2's own body.
    monkeypatch.setattr(
        safe_fetch_module, "is_unsafe_destination_ip", _fake_is_unsafe_except_loopback
    )
    routes: dict = {}
    with local_http_server(_scripted_handler(routes)) as port:
        hop_body = b"x" * 80_000
        routes["/start"] = (
            302,
            {"Location": f"http://127.0.0.1:{port}/final"},
            hop_body,
        )
        routes["/final"] = (200, {}, hop_body)
        body = await fetch_with_redirects(
            f"http://127.0.0.1:{port}/start", max_size_bytes=100_000
        )

    assert body == hop_body
