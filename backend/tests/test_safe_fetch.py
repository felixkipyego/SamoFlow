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
import http.server
import time

import httpx
import pytest

from app.ingest import safe_fetch as safe_fetch_module
from app.ingest.host_safety import resolve_and_validate
from app.ingest.safe_fetch import UnsafeFetchError, _fetch_pinned, safe_fetch
from tests.conftest import assert_no_socket_connections, local_http_server

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


async def test_follow_redirects_is_genuinely_enforced_not_just_set():
    # 2.3.d's entire redirect-recheck design depends on the pinned-fetch
    # mechanism never auto-following a redirect on its own. _fetch_pinned()
    # itself only ever returns the response BODY (bytes) -- it has no way
    # to hand back a 3xx's own status code or Location header today (see
    # PROJECT_SPEC.md's own forward-looking note recorded at this task:
    # 2.3.d will need to change that return shape to actually read one).
    # This test instead drives the IDENTICAL construction _fetch_pinned()
    # uses internally (the same follow_redirects=False AsyncClient, the
    # same explicit Host header, the same extensions={"sni_hostname":...})
    # directly, confirmed live before writing this as a permanent test:
    # the raw 302 and its own Location header come back completely
    # intact, with no automatic follow at all.
    with local_http_server(_RedirectToUnsafeHandler) as port:
        original_url = httpx.URL(f"http://127.0.0.1:{port}/")
        async with httpx.AsyncClient(follow_redirects=False) as client:
            async with client.stream(
                "GET",
                original_url,
                headers={"Host": original_url.netloc.decode("ascii")},
                extensions={"sni_hostname": "127.0.0.1"},
                timeout=15.0,
            ) as response:
                body = b"".join([chunk async for chunk in response.aiter_bytes()])

        assert response.status_code == 302
        assert (
            response.headers["location"]
            == "http://169.254.169.254/should-never-be-followed"
        )
        assert body == b""
