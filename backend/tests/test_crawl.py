# backend/tests/test_crawl.py
# Task 2.6.e: tests for app/ingest/crawl.py. Part 1 (robots.txt/sitemap
# fetch+parse, URL normalization, the pure BFS logic) and part 2
# (extract_links(), HostThrottle -- the real end-to-end crawl, through
# run_crawl() and the real worker loop, is tested separately in
# test_job_handlers.py, matching handle_ingest_url()'s own precedent of
# keeping job-handler-level end-to-end tests there).
#
# Three groups, matching this project's established discipline:
#   - offline/pure: normalize_url(), parse_sitemap_xml(), is_allowed_by_
#     robots() (protego already parsed -- no I/O), bfs_discover() against
#     a synthetic, in-memory link graph (no network), extract_links()
#     (a fixed HTML string in, no I/O), HostThrottle (fake async
#     "requests" -- real timing, but no real network).
#   - live, against a real local HTTP server (tests/conftest.py's
#     local_http_server()/scripted_handler()): fetch_robots_txt() and
#     discover_sitemap_urls(), the two part-1 functions that actually
#     make a real (SSRF-guarded) fetch.
#
# The is_allowed_by_robots() wildcard tests below are the concrete proof
# motivating this task's protego dependency addition (PROJECT_SPEC.md's
# own decision entry): stdlib urllib.robotparser, confirmed live, does
# NOT implement the `*`/`$` wildcard extensions these patterns need --
# these tests prove protego actually respects them, not just that it
# installs cleanly.
import asyncio
import time

from protego import Protego

from app.ingest import crawl
from app.ingest import safe_fetch as safe_fetch_module
from tests.conftest import fake_is_unsafe_except_loopback, local_http_server, scripted_handler

_LOOPBACK_HOST = "127.0.0.1"

_SITEMAP_XML = (
    b'<?xml version="1.0" encoding="UTF-8"?>'
    b'<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
    b"<url><loc>https://example.com/a</loc><lastmod>2024-01-01</lastmod></url>"
    b"<url><loc>https://example.com/b</loc></url>"
    b"<url><loc>https://example.com/c</loc></url>"
    b"</urlset>"
)

# --- Offline / pure: normalize_url() ----------------------------------------


def test_normalize_url_lowercases_scheme_and_host_but_not_path():
    assert crawl.normalize_url("HTTPS://Example.COM/Foo") == "https://example.com/Foo"


def test_normalize_url_strips_a_trailing_slash():
    assert crawl.normalize_url("https://example.com/foo/") == "https://example.com/foo"


def test_normalize_url_bare_root_and_root_with_slash_are_identical():
    assert crawl.normalize_url("https://example.com") == crawl.normalize_url(
        "https://example.com/"
    )


def test_normalize_url_strips_the_fragment():
    assert crawl.normalize_url("https://example.com/page#section") == (
        "https://example.com/page"
    )


def test_normalize_url_preserves_the_query_string():
    assert crawl.normalize_url("https://example.com/search?q=cats") == (
        "https://example.com/search?q=cats"
    )


def test_normalize_url_combines_all_rules_at_once():
    assert crawl.normalize_url("HTTPS://Example.COM/Page/?x=1#frag") == (
        "https://example.com/Page?x=1"
    )


# --- Offline / pure: parse_sitemap_xml() ------------------------------------


def test_parse_sitemap_xml_extracts_every_loc():
    assert crawl.parse_sitemap_xml(_SITEMAP_XML) == [
        "https://example.com/a",
        "https://example.com/b",
        "https://example.com/c",
    ]


def test_parse_sitemap_xml_malformed_input_returns_empty_list():
    # A realistic 404 page's own body -- unescaped '&', unclosed tags --
    # genuinely raises ET.ParseError (confirmed live before relying on
    # this), not just a toy example.
    html_404 = (
        b"<!DOCTYPE html><html><head><title>404</title></head>"
        b"<body><h1>Not Found</h1><br>Sorry & try again<img src=\"x.png\">"
        b"</body></html>"
    )
    assert crawl.parse_sitemap_xml(html_404) == []


def test_parse_sitemap_xml_empty_bytes_returns_empty_list():
    assert crawl.parse_sitemap_xml(b"") == []


# --- Offline / pure: is_allowed_by_robots() ---------------------------------
# The concrete proof the protego escalation actually fixes the real
# problem: real-world wildcard patterns most major CMS platforms ship by
# default, confirmed live (PROJECT_SPEC.md's own decision entry) to be
# silently ignored by stdlib urllib.robotparser.


def test_is_allowed_by_robots_respects_pdf_wildcard_with_end_anchor():
    robots = Protego.parse("User-agent: *\nDisallow: /*.pdf$\n")
    assert crawl.is_allowed_by_robots(robots, "https://example.com/a.pdf", "SamoFlowBot") is False
    assert crawl.is_allowed_by_robots(robots, "https://example.com/a.html", "SamoFlowBot") is True


def test_is_allowed_by_robots_respects_mid_path_wildcard_on_query_strings():
    robots = Protego.parse("User-agent: *\nDisallow: /search?*\n")
    assert (
        crawl.is_allowed_by_robots(robots, "https://example.com/search?q=x", "SamoFlowBot")
        is False
    )
    assert crawl.is_allowed_by_robots(robots, "https://example.com/other", "SamoFlowBot") is True


def test_is_allowed_by_robots_respects_bare_wildcard_prefix():
    robots = Protego.parse("User-agent: *\nDisallow: /tmp*\n")
    assert (
        crawl.is_allowed_by_robots(robots, "https://example.com/tmp/anything", "SamoFlowBot")
        is False
    )


def test_is_allowed_by_robots_most_specific_rule_wins_regardless_of_order():
    robots = Protego.parse("User-agent: *\nDisallow: /tmp*\nAllow: /tmp/public/\n")
    assert (
        crawl.is_allowed_by_robots(robots, "https://example.com/tmp/public/x", "SamoFlowBot")
        is True
    )
    assert (
        crawl.is_allowed_by_robots(robots, "https://example.com/tmp/private", "SamoFlowBot")
        is False
    )


def test_is_allowed_by_robots_comments_are_ignored():
    robots = Protego.parse(
        "# this entire line is a comment\nUser-agent: *\nDisallow: /private/  # trailing too\n"
    )
    assert (
        crawl.is_allowed_by_robots(robots, "https://example.com/private/x", "SamoFlowBot")
        is False
    )
    assert crawl.is_allowed_by_robots(robots, "https://example.com/public", "SamoFlowBot") is True


def test_is_allowed_by_robots_bot_specific_group_overrides_wildcard_group():
    robots = Protego.parse(
        "User-agent: SamoFlowBot\nDisallow: /bot-only/\n\nUser-agent: *\nDisallow: /everyone/\n"
    )
    assert (
        crawl.is_allowed_by_robots(robots, "https://example.com/bot-only/x", "SamoFlowBot")
        is False
    )
    # The bot-specific group does not mention /everyone/, and it takes
    # over entirely from the wildcard group for this user-agent -- not
    # disallowed for SamoFlowBot specifically.
    assert (
        crawl.is_allowed_by_robots(robots, "https://example.com/everyone/x", "SamoFlowBot")
        is True
    )
    assert (
        crawl.is_allowed_by_robots(robots, "https://example.com/everyone/x", "OtherBot") is False
    )


def test_is_allowed_by_robots_missing_robots_txt_allows_everything():
    robots = Protego.parse("")
    assert crawl.is_allowed_by_robots(robots, "https://example.com/anything", "SamoFlowBot") is True


# --- Offline / pure: bfs_discover() -----------------------------------------


def _run(coro):
    return asyncio.run(coro)


def test_bfs_discover_visits_in_breadth_first_order():
    graph = {
        "https://example.com": ["https://example.com/a", "https://example.com/b"],
        "https://example.com/a": ["https://example.com/c"],
        "https://example.com/b": ["https://example.com/c"],
        "https://example.com/c": [],
    }

    async def get_links(url):
        return graph.get(url, [])

    result = _run(
        crawl.bfs_discover(
            "https://example.com",
            is_same_domain=lambda u: u.startswith("https://example.com"),
            get_links=get_links,
            page_cap=10,
        )
    )
    # BFS order: seed, then both of the seed's own links (in link order),
    # then /c (reachable from both, discovered only once -- the visited
    # set, not merely BFS order, is what proves dedup).
    assert result == [
        "https://example.com",
        "https://example.com/a",
        "https://example.com/b",
        "https://example.com/c",
    ]


def test_bfs_discover_respects_the_page_cap():
    graph = {
        "https://example.com": [f"https://example.com/p{i}" for i in range(20)],
    }

    async def get_links(url):
        return graph.get(url, [])

    result = _run(
        crawl.bfs_discover(
            "https://example.com",
            is_same_domain=lambda u: True,
            get_links=get_links,
            page_cap=5,
        )
    )
    assert len(result) == 5
    assert result[0] == "https://example.com"


def test_bfs_discover_never_queues_a_different_domain():
    graph = {
        "https://example.com": [
            "https://example.com/a",
            "https://attacker.example/evil",
        ],
        "https://example.com/a": [],
    }
    calls = []

    async def get_links(url):
        calls.append(url)
        return graph.get(url, [])

    result = _run(
        crawl.bfs_discover(
            "https://example.com",
            is_same_domain=lambda u: u.startswith("https://example.com"),
            get_links=get_links,
            page_cap=10,
        )
    )
    assert result == ["https://example.com", "https://example.com/a"]
    # The cross-domain link was never added to the queue, so get_links()
    # was never even called with it -- not merely absent from the result.
    assert "https://attacker.example/evil" not in calls


def test_bfs_discover_normalizes_and_dedups_links_that_differ_only_cosmetically():
    graph = {
        "https://example.com": [
            "https://EXAMPLE.com/a/",
            "https://example.com/a",  # same page after normalization
            "https://example.com/a#section",  # same page, different fragment
        ],
    }

    async def get_links(url):
        return graph.get(url, [])

    result = _run(
        crawl.bfs_discover(
            "https://example.com",
            is_same_domain=lambda u: u.startswith("https://example.com"),
            get_links=get_links,
            page_cap=10,
        )
    )
    assert result == ["https://example.com", "https://example.com/a"]


def test_bfs_discover_page_cap_of_one_returns_only_the_seed():
    async def get_links(url):
        raise AssertionError("get_links() must never be called when the cap is already met")

    result = _run(
        crawl.bfs_discover(
            "https://example.com",
            is_same_domain=lambda u: True,
            get_links=get_links,
            page_cap=1,
        )
    )
    assert result == ["https://example.com"]


# --- Live: fetch_robots_txt() / discover_sitemap_urls() --------------------


async def _setup(monkeypatch):
    monkeypatch.setattr(
        safe_fetch_module, "is_unsafe_destination_ip", fake_is_unsafe_except_loopback
    )


async def test_robots_txt_with_a_sitemap_directive_is_extracted(monkeypatch):
    # (a)
    await _setup(monkeypatch)
    robots_txt = (
        b"User-agent: *\nDisallow: /private/\n"
        b"Sitemap: https://example.com/my-sitemap.xml\n"
    )
    with local_http_server(scripted_handler({"/robots.txt": (200, robots_txt)})) as port:
        seed = f"http://{_LOOPBACK_HOST}:{port}/"
        robots = await crawl.fetch_robots_txt(seed)

    assert list(robots.sitemaps) == ["https://example.com/my-sitemap.xml"]
    blocked_url = f"http://{_LOOPBACK_HOST}:1/private/x"
    assert crawl.is_allowed_by_robots(robots, blocked_url, "Bot") is False


async def test_robots_txt_with_no_sitemap_directive_falls_through(monkeypatch):
    # (b)
    await _setup(monkeypatch)
    robots_txt = b"User-agent: *\nDisallow: /private/\n"
    with local_http_server(scripted_handler({"/robots.txt": (200, robots_txt)})) as port:
        seed = f"http://{_LOOPBACK_HOST}:{port}/"
        robots = await crawl.fetch_robots_txt(seed)

    assert list(robots.sitemaps) == []


async def test_missing_robots_txt_means_crawl_allowed_not_an_error(monkeypatch):
    # (c) -- no "/robots.txt" route at all; scripted_handler's own
    # default is a real 404 with a body, not a connection failure.
    await _setup(monkeypatch)
    with local_http_server(scripted_handler({})) as port:
        seed = f"http://{_LOOPBACK_HOST}:{port}/"
        robots = await crawl.fetch_robots_txt(seed)

    assert list(robots.sitemaps) == []
    assert crawl.is_allowed_by_robots(robots, f"{seed}anything", "SamoFlowBot") is True


async def test_discover_sitemap_urls_extracts_every_loc_from_a_real_sitemap(monkeypatch):
    # (d)
    await _setup(monkeypatch)
    with local_http_server(scripted_handler({"/sitemap.xml": (200, _SITEMAP_XML)})) as port:
        seed = f"http://{_LOOPBACK_HOST}:{port}/"
        robots = Protego.parse("")  # no Sitemap: directive -- fallback path
        urls = await crawl.discover_sitemap_urls(seed, robots)

    assert urls == ["https://example.com/a", "https://example.com/b", "https://example.com/c"]


async def test_discover_sitemap_urls_prefers_the_robots_txt_named_sitemap(monkeypatch):
    await _setup(monkeypatch)
    with local_http_server(
        scripted_handler(
            {
                "/sitemap.xml": (200, b'<urlset><url><loc>https://wrong.example/</loc></url></urlset>'),
                "/custom-sitemap.xml": (200, _SITEMAP_XML),
            }
        )
    ) as port:
        seed = f"http://{_LOOPBACK_HOST}:{port}/"
        robots = Protego.parse(f"Sitemap: http://{_LOOPBACK_HOST}:{port}/custom-sitemap.xml\n")
        urls = await crawl.discover_sitemap_urls(seed, robots)

    assert urls == ["https://example.com/a", "https://example.com/b", "https://example.com/c"]


async def test_discover_sitemap_urls_returns_empty_list_when_nothing_exists(monkeypatch):
    await _setup(monkeypatch)
    with local_http_server(scripted_handler({})) as port:
        seed = f"http://{_LOOPBACK_HOST}:{port}/"
        robots = Protego.parse("")
        urls = await crawl.discover_sitemap_urls(seed, robots)

    assert urls == []


# --- Offline / pure: extract_links() ----------------------------------------


def test_extract_links_resolves_relative_hrefs_against_the_base_url():
    html = '<html><body><a href="/page-a">A</a><a href="page-b">B</a></body></html>'
    assert crawl.extract_links(html, "https://example.com/dir/") == [
        "https://example.com/page-a",
        "https://example.com/dir/page-b",
    ]


def test_extract_links_resolves_protocol_relative_and_absolute_hrefs():
    html = (
        '<a href="//other.example/x">rel-proto</a>'
        '<a href="https://example.com/abs">abs</a>'
    )
    assert crawl.extract_links(html, "https://example.com/") == [
        "https://other.example/x",
        "https://example.com/abs",
    ]


def test_extract_links_filters_out_non_crawlable_hrefs():
    html = (
        '<a href="mailto:a@example.com">mail</a>'
        '<a href="tel:+15551234567">tel</a>'
        '<a href="javascript:void(0)">js</a>'
        '<a href="#section">fragment only</a>'
        '<a href="ftp://example.com/file">ftp</a>'
        '<a href="/real-page">real</a>'
    )
    assert crawl.extract_links(html, "https://example.com/") == ["https://example.com/real-page"]


def test_extract_links_no_anchors_returns_empty_list():
    assert crawl.extract_links("<html><body><p>no links here</p></body></html>", "https://x/") == []


def test_extract_links_ignores_an_href_less_anchor():
    html = '<a name="bookmark">no href</a><a href="/ok">ok</a>'
    assert crawl.extract_links(html, "https://example.com/") == ["https://example.com/ok"]


# --- Offline / "pure" (real timing, no real network): HostThrottle ---------


def _run(coro):
    return asyncio.run(coro)


def test_host_throttle_never_exceeds_max_concurrent_for_one_host():
    throttle = crawl.HostThrottle(max_concurrent=2, delay_seconds=0)
    in_flight = 0
    max_observed = 0

    async def visit():
        nonlocal in_flight, max_observed
        async with throttle.slot("example.com"):
            in_flight += 1
            max_observed = max(max_observed, in_flight)
            await asyncio.sleep(0.05)
            in_flight -= 1

    async def run_all():
        await asyncio.gather(*(visit() for _ in range(5)))

    _run(run_all())
    assert max_observed == 2


def test_host_throttle_enforces_the_minimum_delay_between_request_starts():
    throttle = crawl.HostThrottle(max_concurrent=2, delay_seconds=0.2)
    starts: list[float] = []

    async def visit():
        async with throttle.slot("example.com"):
            starts.append(time.monotonic())

    _run(_sequential(visit, visit))
    assert starts[1] - starts[0] >= 0.2 - 0.01  # small tolerance for scheduler jitter


async def _sequential(*coroutine_fns):
    for fn in coroutine_fns:
        await fn()


def test_host_throttle_different_hosts_do_not_block_each_other():
    throttle = crawl.HostThrottle(max_concurrent=1, delay_seconds=1.0)

    async def visit(host):
        async with throttle.slot(host):
            return time.monotonic()

    async def run_both():
        await asyncio.gather(visit("a.example"), visit("b.example"))

    start = time.monotonic()
    _run(run_both())
    # Two DIFFERENT hosts, each with their own 1-second delay budget --
    # if they shared one throttle, the second would be forced to wait;
    # since they don't, both complete almost immediately.
    assert time.monotonic() - start < 0.5
