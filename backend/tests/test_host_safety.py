# backend/tests/test_host_safety.py
# Task 2.3.b: tests for resolve_and_validate() (backend/app/ingest/
# host_safety.py). Fully offline -- the resolver itself is monkeypatched
# with a fake, canned-answer stand-in, matching this project's own
# established convention (test_domain_verification.py's own
# monkeypatch.setattr(dns.asyncresolver, "Resolver", ...) pattern, 2.2.d)
# for not depending on any real third-party domain's current DNS content.
import dns.asyncresolver
import dns.resolver

from app.ingest import host_safety
from app.ingest.host_safety import resolve_and_validate


class _FakeRdata:
    def __init__(self, address: str) -> None:
        self.address = address


def _fake_resolver_class(answers: dict[str, list[str] | None]):
    # `answers` maps rdtype ("A"/"AAAA") to either a list of address
    # strings, or None meaning "raise, as if this record type doesn't
    # exist" -- duck-types only what resolve_and_validate() actually
    # calls (resolver.lifetime = ..., await resolver.resolve(host, rdtype)),
    # matching this codebase's own established fake-object convention
    # elsewhere (e.g. test_ingest_repository.py's _fake_true/_fake_false).
    class _FakeResolver:
        def __init__(self, *args, **kwargs) -> None:
            self.lifetime = None

        async def resolve(self, hostname: str, rdtype: str):
            addresses = answers.get(rdtype)
            if addresses is None:
                raise dns.resolver.NoAnswer()
            return [_FakeRdata(address) for address in addresses]

    return _FakeResolver


async def test_single_safe_public_ip_is_accepted_and_returned(monkeypatch):
    monkeypatch.setattr(
        dns.asyncresolver,
        "Resolver",
        _fake_resolver_class({"A": ["8.8.8.8"], "AAAA": None}),
    )
    ips = await resolve_and_validate("safe-looking-host.example")
    assert [str(ip) for ip in ips] == ["8.8.8.8"]


async def test_single_unsafe_ip_rejects_the_whole_hostname(monkeypatch):
    monkeypatch.setattr(
        dns.asyncresolver,
        "Resolver",
        _fake_resolver_class({"A": ["10.0.0.5"], "AAAA": None}),
    )
    assert await resolve_and_validate("evil-looking-host.example") == []


async def test_one_unsafe_ip_among_several_safe_ones_rejects_everything(monkeypatch):
    # The key property from design point 1: a mixed answer (some safe,
    # some not) must never return a partial "safe subset" -- the whole
    # hostname is rejected, proven directly, not inferred from the
    # single-unsafe-IP case above alone.
    monkeypatch.setattr(
        dns.asyncresolver,
        "Resolver",
        _fake_resolver_class({"A": ["8.8.8.8", "1.1.1.1", "192.168.1.1"], "AAAA": None}),
    )
    assert await resolve_and_validate("mixed-answer-host.example") == []


async def test_dns_rebinding_simulation_to_the_cloud_metadata_ip_is_rejected(monkeypatch):
    # A plausible-sounding, innocuous hostname whose monkeypatched
    # resolver answers with the cloud-metadata IP specifically -- proving
    # validation genuinely inspects the RESOLVED IP, not anything about
    # the hostname's own appearance (the hostname here reads like an
    # ordinary internal API host a tenant might plausibly name; nothing
    # about its text is itself suspicious).
    monkeypatch.setattr(
        dns.asyncresolver,
        "Resolver",
        _fake_resolver_class({"A": ["169.254.169.254"], "AAAA": None}),
    )
    assert await resolve_and_validate("my-internal-api.example") == []


async def test_resolution_failure_on_both_record_types_fails_closed_cleanly(monkeypatch):
    monkeypatch.setattr(
        dns.asyncresolver,
        "Resolver",
        _fake_resolver_class({"A": None, "AAAA": None}),
    )
    # Must not raise -- a clean empty list, matching fetch_txt_records()'s
    # own established contract for an NXDOMAIN-shaped failure.
    assert await resolve_and_validate("this-domain-does-not-exist.example") == []


async def test_a_safe_a_record_and_an_unsafe_aaaa_record_still_rejects(monkeypatch):
    # Proves BOTH record types are actually checked, not just A: the A
    # record alone is perfectly safe, but the AAAA record is the IPv6
    # loopback -- the combined result must still be rejected.
    monkeypatch.setattr(
        dns.asyncresolver,
        "Resolver",
        _fake_resolver_class({"A": ["8.8.8.8"], "AAAA": ["::1"]}),
    )
    assert await resolve_and_validate("dual-stack-host.example") == []


async def test_a_missing_record_type_alone_is_not_a_failure(monkeypatch):
    # A hostname with only A records and genuinely no AAAA at all (the
    # common, normal case for a domain without IPv6) must not be treated
    # as a DNS failure on its own -- only the COMBINED result decides.
    monkeypatch.setattr(
        dns.asyncresolver,
        "Resolver",
        _fake_resolver_class({"A": ["1.1.1.1"], "AAAA": None}),
    )
    ips = await resolve_and_validate("ipv4-only-host.example")
    assert [str(ip) for ip in ips] == ["1.1.1.1"]


async def test_uses_the_modules_own_timeout_constant(monkeypatch):
    # Confirms the resolver's own .lifetime is actually set from this
    # module's constant, not left at the library default -- the same
    # kind of direct proof 2.2.d's own timeout test uses, rather than
    # only trusting the source reads correctly.
    captured = {}

    class _CapturingResolver:
        def __init__(self, *args, **kwargs) -> None:
            self.lifetime = None

        async def resolve(self, hostname: str, rdtype: str):
            captured["lifetime"] = self.lifetime
            raise dns.resolver.NoAnswer()

    monkeypatch.setattr(dns.asyncresolver, "Resolver", _CapturingResolver)
    await resolve_and_validate("whatever.example")
    assert captured["lifetime"] == host_safety._DNS_QUERY_TIMEOUT_SECONDS
