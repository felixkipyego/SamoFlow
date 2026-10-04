# backend/tests/test_ip_safety.py
# Task 2.3.a: offline, table-driven tests for the pure IP-classification
# function (backend/app/ingest/ip_safety.py), matching test_origin.py's
# own established style (1.4.f). No network, no database.
import ipaddress

import pytest

from app.ingest.ip_safety import is_unsafe_destination_ip


@pytest.mark.parametrize(
    ("ip", "expected_unsafe"),
    [
        # RFC 1918 private ranges.
        ("10.0.0.1", True),
        ("10.255.255.255", True),
        ("172.16.0.1", True),
        ("172.31.255.254", True),
        ("192.168.0.1", True),
        ("192.168.255.255", True),
        # Loopback.
        ("127.0.0.1", True),
        ("::1", True),
        # Link-local.
        ("169.254.1.1", True),
        ("fe80::1", True),
        # The cloud-metadata IP, its own explicitly named case -- not just
        # folded into the link-local range above, given how high-value this
        # one specific address is as an SSRF target.
        ("169.254.169.254", True),
        # CGNAT (RFC 6598) -- missed by a naive ip.is_private check alone
        # (confirmed live during Step 2.3's own research), which is exactly
        # why this function is a fail-closed allow-list, not a deny-list.
        ("100.64.0.1", True),
        ("100.127.255.254", True),
        # Multicast, both families -- also missed by ip.is_private alone.
        ("224.0.0.1", True),
        ("239.255.255.255", True),
        ("ff02::1", True),
        # IPv6 unique-local (RFC 1918's rough IPv6 equivalent).
        ("fc00::1", True),
        ("fd12:3456::1", True),
        # Genuinely public, safe addresses -- proves the function isn't
        # accidentally rejecting everything.
        ("8.8.8.8", False),
        ("1.1.1.1", False),
        ("2001:4860:4860::8888", False),
        # The fail-closed proof: 240.0.0.0/4 (IANA "reserved for future
        # use") is not RFC 1918, not loopback, not link-local, not CGNAT,
        # not multicast -- it is in NO category named anywhere above, and
        # must still be rejected. This proves the function fails closed on
        # an unknown/unallocated range, not open, by construction: it
        # never checks "is this one of the ranges I decided were bad," it
        # checks "is this a normal public address" and rejects everything
        # else.
        ("240.0.0.1", True),
    ],
)
def test_is_unsafe_destination_ip_table(ip, expected_unsafe):
    assert is_unsafe_destination_ip(ipaddress.ip_address(ip)) is expected_unsafe
