# backend/app/ingest/crawl.py
# Task 2.6.e (part 1 of 2): crawl discovery -- robots.txt/sitemap fetch
# and parse, URL normalization, and the pure BFS queue/visited-set logic.
# The actual page-by-page fetching (ingest_url() per discovered URL,
# feeding real page links back into the BFS loop, the heartbeat
# mechanism, the real `crawl` job handler) is this task's own
# continuation (part 2) -- deliberately NOT built here, matching this
# module's own "pure function plus thin IO wrapper" split: every
# function below is either pure (no I/O at all, fully testable against
# synthetic data) or a thin wrapper around exactly one real fetch.
#
# [SECURITY]: every outbound request (robots.txt, sitemap.xml) goes
# through fetch_with_redirects() (2.3) -- the same SSRF-guarded primitive
# every other fetch in this pipeline uses. Never a raw/unguarded request,
# even for a "just reading metadata" fetch like robots.txt.
#
# robots.txt library: escalated from Python's stdlib urllib.robotparser
# to protego (new runtime dependency, rule 8 -- PROJECT_SPEC.md's own
# decision entry has the full justification), confirmed live, not
# assumed, that stdlib does not implement the de-facto `*`/`$` wildcard
# extensions most real-world robots.txt files rely on (Google/Bing's own
# convention) -- it treats `*`/`$` inside a Disallow/Allow path as
# literal characters. protego (Scrapy's own robots.txt library) handles
# these correctly, confirmed live before adding it. The Step 2.6 decision
# entry's own robots.txt-parsing decision explicitly pre-authorized this
# exact escalation path, contingent on confirming genuine inadequacy
# first -- done, not skipped.
#
# protego's own API, confirmed live (not assumed from its docs): `Protego.
# parse(content: str) -> Self` takes the FULL text as one string (not a
# list of lines, unlike stdlib); `instance.can_fetch(url, user_agent) ->
# bool` -- note the argument ORDER (url first), the reverse of stdlib's
# own `can_fetch(useragent, url)`; `instance.sitemaps` is a plain
# iterable, empty (not None) when no Sitemap: directive exists, unlike
# stdlib's own `site_maps()` which returns None (confirmed live, a real
# footgun in the API this escalation also sidesteps). A missing/404
# robots.txt is handled by feeding Protego the FETCH FAILURE'S OWN
# BODY, not by special-casing the status code -- confirmed live that
# Protego.parse() of an arbitrary non-robots.txt-shaped text (an HTML
# 404 page) gracefully defaults to "allow everything, no sitemaps",
# exactly the desired "missing robots.txt means crawl allowed" behavior,
# with zero special-casing needed (matches domain_verification.py's own
# established "a non-matching body simply doesn't match" posture).
#
# Sitemap XML: stdlib xml.etree.ElementTree is sufficient for this
# simple, well-known format (rule 8 N/A -- no new dependency). Namespace-
# tolerant tag matching (`tag.endswith("loc")`, not a hardcoded namespace
# URI) -- real-world sitemaps reliably declare the sitemaps.org
# namespace, but matching by local name only is simpler and more
# forgiving than requiring an exact declared URI, with no real downside
# for this well-known, single-purpose format. Confirmed live that a
# malformed/non-XML body (e.g. a 404's own HTML) raises
# xml.etree.ElementTree.ParseError -- caught explicitly, unlike Protego's
# own graceful tolerance, and treated the same way: no sitemap found, not
# an error. v1 scope limit, not silently decided: only a flat <urlset> of
# <url><loc> entries is handled -- a <sitemapindex> of nested sitemap
# files (a real-world pattern for very large sites) is NOT recursed into;
# flagged as an Open marker, not built speculatively ahead of evidence
# real tenant sites need it (rule 11).
#
# [SECURITY] ruff/bandit flags ET.fromstring() on untrusted input (S314:
# "use defusedxml instead") -- investigated live rather than silently
# suppressed or silently escalated to a second new dependency in this
# same task. Three classic attacks, tested directly against the
# installed Python 3.12.14's own xml.etree.ElementTree (libexpat),
# parsing a tenant-controlled sitemap.xml being exactly the "untrusted
# input" this rule warns about:
#   - XXE via a local-file SYSTEM entity (file:///etc/hostname): raises
#     ParseError ("undefined entity") -- external entities are never
#     resolved at all, confirmed live.
#   - XXE/SSRF via an external DTD reference (SYSTEM "http://169.254.
#     169.254/evil.dtd"): parses successfully with ZERO network activity
#     -- confirmed live via the identical socket.socket.connect-raises
#     proof this project already uses elsewhere (assert_no_socket_
#     connections(), tests/conftest.py) -- external DTDs are never
#     fetched.
#   - Billion-laughs entity-expansion DoS (9 levels of 10x expansion, a
#     real 10^9-scale payload, not a toy example): raises ParseError
#     ("limit on input amplification factor... breached") -- libexpat's
#     own built-in amplification-factor guard (added years ago
#     specifically against this attack class) rejects it before
#     expansion completes.
# All three of S314's own underlying concerns are already closed by this
# Python version's actual default behavior, not merely assumed safe
# because "it's probably fine" -- defusedxml would add a second new
# dependency in this same task to guard against attacks already
# unreachable here. Suppressed with this comment as the documented
# justification, matching this project's own established noqa-with-
# reasoning convention (e.g. worker.py's HEARTBEAT_PATH).
#
# Task 2.6.e part 2 adds: extract_links() (the <a href> discovery
# get_links() needs -- extract_html.py deliberately NOT extended for
# this, see that function's own docstring for why); HostThrottle (the
# "2 concurrent requests per host with a delay" mechanism, docs/SPEC.md
# §5.5, deferred from 2.3 to 2.6 specifically); and run_crawl(), the
# real orchestration tying discovery to per-page ingest_url() calls.
import asyncio
import logging
import time
import uuid
import xml.etree.ElementTree as ET
from collections import deque
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from urllib.parse import urljoin, urlsplit, urlunsplit

import httpx
from bs4 import BeautifulSoup
from protego import Protego
from qdrant_client import AsyncQdrantClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.ingest.safe_fetch import UnsafeFetchError, fetch_with_redirects
from app.ingest.web_adapter import IngestResult, ingest_url

logger = logging.getLogger(__name__)

_SITEMAP_FALLBACK_PATH = "/sitemap.xml"
_ROBOTS_PATH = "/robots.txt"


def _well_known_url(seed_url: str, path: str) -> str:
    # Builds `{scheme}://{host}{path}` from the seed URL's own origin --
    # shared by fetch_robots_txt()'s own /robots.txt and discover_
    # sitemap_urls()'s own /sitemap.xml fallback, both one-line callers
    # of the identical "take the seed's origin, append a fixed well-known
    # path" shape.
    parts = urlsplit(seed_url)
    return urlunsplit((parts.scheme, parts.netloc, path, "", ""))


def normalize_url(url: str) -> str:
    """Pure. Step 2.6 decision: case-insensitive scheme/host, strip a
    trailing slash, strip the fragment, PRESERVE the query string (it can
    produce genuinely different content, unlike a fragment) -- two URLs
    are the same page only after this normalization, never compared raw.

    A bare root path ("/") and no path at all both normalize to "" (the
    empty path), so "https://Example.com" and "https://example.com/"
    dedup to the exact same string -- confirmed deliberately, not an
    accident of urlunsplit()'s own formatting.
    """
    parts = urlsplit(url)
    path = parts.path
    if path == "/":
        path = ""
    elif path.endswith("/"):
        path = path[:-1]
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), path, parts.query, ""))


async def fetch_robots_txt(seed_url: str) -> Protego:
    """Thin IO wrapper: fetches `{origin}/robots.txt` via
    fetch_with_redirects() (SSRF-guarded) and parses it with protego.

    ANY fetch failure (an SSRF rejection, a timeout, a real connection
    error, or an ordinary 404's own body, which fetch_with_redirects()
    never raises for) is treated identically: parsed as empty content,
    which protego's own can_fetch()/sitemaps already default to "allow
    everything, no sitemaps" for -- matching fetch_txt_records()'s own
    established "any clean failure means the permissive default, never a
    caller-visible crash" posture (domain_verification.py, 2.2.d).
    """
    robots_url = _well_known_url(seed_url, _ROBOTS_PATH)
    try:
        result = await fetch_with_redirects(robots_url)
        text = result.body.decode("utf-8", errors="replace")
    except (UnsafeFetchError, httpx.HTTPError):
        text = ""
    return Protego.parse(text)


def is_allowed_by_robots(robots: Protego, url: str, user_agent: str) -> bool:
    """Pure (no I/O -- `robots` is already parsed). A thin, named wrapper
    around `robots.can_fetch(url, user_agent)` rather than a bare call at
    every call site: protego's own argument order (url, THEN user_agent)
    is the reverse of stdlib robotparser's `can_fetch(useragent, url)`,
    confirmed live before relying on it -- naming both parameters here
    guards every future call site against silently swapping them.
    """
    return robots.can_fetch(url, user_agent)


def parse_sitemap_xml(xml_bytes: bytes) -> list[str]:
    """Pure: no I/O. Extracts every <loc> entry's own text from a
    standard XML sitemap (a <urlset> of <url><loc> entries). Returns []
    for malformed/non-XML content (e.g. a 404's own HTML body) rather
    than raising -- confirmed live that ET.fromstring() genuinely raises
    ET.ParseError on realistic malformed input (an unescaped `&`, an
    unclosed tag), unlike protego's own more tolerant parser.
    """
    try:
        root = ET.fromstring(xml_bytes)  # noqa: S314 -- see module header, verified live
    except ET.ParseError:
        return []
    return [elem.text.strip() for elem in root.iter() if elem.tag.endswith("loc") and elem.text]


async def discover_sitemap_urls(seed_url: str, robots: Protego) -> list[str]:
    """Thin IO wrapper. Sitemap location, per the Step 2.6 decision:
    check `robots.txt`'s own Sitemap: directive(s) first (protego's own
    `.sitemaps`, confirmed live to be a plain, possibly-empty iterable --
    NOT None, unlike stdlib's `site_maps()`); if none were declared, fall
    back to the fixed `/sitemap.xml` path. If that also doesn't exist (or
    fails to fetch/parse), no sitemap is available at all -- an empty
    list, not an error; the crawl falls back to pure link-following
    (this task's own continuation, part 2).

    Every sitemap URL robots.txt names (there can be more than one) is
    fetched and its own <loc> entries concatenated into one flat list --
    not deduplicated here, since normalize_url()+a visited-set (the BFS
    layer) already owns deduplication; duplicating that logic here too
    would be a second, independently-maintained copy of the same concern.
    """
    sitemap_urls = list(robots.sitemaps) or [_well_known_url(seed_url, _SITEMAP_FALLBACK_PATH)]
    discovered: list[str] = []
    for sitemap_url in sitemap_urls:
        try:
            result = await fetch_with_redirects(sitemap_url)
        except (UnsafeFetchError, httpx.HTTPError):
            continue
        discovered.extend(parse_sitemap_xml(result.body))
    return discovered


async def bfs_discover(
    seed_url: str,
    *,
    is_same_domain: Callable[[str], bool],
    get_links: Callable[[str], Awaitable[list[str]]],
    page_cap: int,
) -> list[str]:
    """Pure BFS traversal -- no real I/O of its own; `get_links` is the
    one injected seam that would do real fetching in a real caller (this
    task's own continuation, part 2), making this function fully
    testable against a synthetic, in-memory link graph (`get_links` as a
    plain async function reading a dict) with zero network involved,
    matching this project's own established async-fake-callable testing
    discipline (e.g. patch_embed_dense(), _fake_resolve_and_validate()).

    `async def get_links`, not a plain sync callable: the REAL
    implementation (part 2) must fetch a page to discover its own links,
    an inherently async operation -- this lets part 2 reuse this EXACT
    function unchanged with a real async fetcher, rather than needing a
    second, parallel sync-vs-async version of the same BFS logic.

    Returns URLs in BFS visit order, normalized and deduplicated,
    INCLUDING the seed itself as the first entry (a real crawl's own page
    cap counts the seed page too, so excluding it here would create an
    off-by-one mismatch between "pages this list names" and "pages a
    real caller would actually fetch"). `is_same_domain` and `page_cap`
    are both checked at the point a link is ABOUT to be added to the
    queue/result, not inside `get_links` itself -- `get_links` only ever
    answers "what does this one page link to", a separate concern from
    "is that link in-scope and is there still room for it" (rule 11: one
    function, one job).

    Same-domain scope is deliberately an injected predicate, not derived
    from `seed_url` internally -- the Step 2.6 decision's own "whole
    verified domain" scope is a `verified_domains`-table concern the real
    caller (part 2) owns; this function stays genuinely pure, with no
    opinion on what "same domain" means beyond "whatever the caller says
    it is" (fake predicates in this task's own tests, a real
    verified_domains-backed one in part 2).
    """
    seed_normalized = normalize_url(seed_url)
    visited = {seed_normalized}
    result = [seed_normalized]
    queue: deque[str] = deque([seed_normalized])

    while queue and len(result) < page_cap:
        current = queue.popleft()
        for link in await get_links(current):
            normalized = normalize_url(link)
            if normalized in visited or not is_same_domain(normalized):
                continue
            visited.add(normalized)
            result.append(normalized)
            queue.append(normalized)
            if len(result) >= page_cap:
                break

    return result


# Non-http(s)/anchor-only hrefs a real page can carry that are never
# meaningful crawl targets -- filtered in extract_links() below, not left
# for is_same_domain()/normalize_url() downstream to reject less clearly.
_NON_CRAWLABLE_SCHEMES = ("mailto:", "tel:", "javascript:", "#")


def extract_links(html: str, base_url: str) -> list[str]:
    """Pure: no I/O. Extracts every <a href> on the page, resolved to an
    absolute URL against `base_url` (urljoin() handles relative,
    root-relative and protocol-relative hrefs correctly per RFC 3986,
    matching fetch_with_redirects()'s own established use of the
    identical stdlib mechanism for Location headers, 2.3.d).

    Deliberately NOT extract_html.py's own job: that module is pure
    content extraction (headings/paragraphs/word_count), shared by
    EVERY adapter (urls, crawl, upload) -- outgoing links are a
    crawl-ONLY concern (a url-list/upload source never needs them), so
    extending that already-shared, already-tested module's contract for
    one caller's own need would be scope creep onto it, not reuse (rule
    11). A fresh BeautifulSoup parse here, not a reuse of extract_html()'s
    own souped tree -- those two parses are genuinely independent
    concerns (this one wants every <a>, including ones inside <script>/
    hidden elements that extract_html() deliberately decomposes).
    """
    soup = BeautifulSoup(html, "lxml")
    links: list[str] = []
    for tag in soup.find_all("a", href=True):
        href = tag["href"].strip()
        if not href or href.startswith(_NON_CRAWLABLE_SCHEMES):
            continue
        resolved = urljoin(base_url, href)
        if urlsplit(resolved).scheme not in ("http", "https"):
            continue
        links.append(resolved)
    return links


# docs/SPEC.md §5.5: "2 concurrent requests per host with a delay" --
# deferred from 2.3 (that step's own decision (d) explicitly named this
# as 2.6's job, not a single-fetch primitive's concern) to this task.
# The "2" is spec-fixed (never described as configurable), matching
# DENSE_VECTOR_SIZE's own "fixed until a deliberate, documented decision"
# precedent -- a plain module constant, not a Settings field.
MAX_CONCURRENT_REQUESTS_PER_HOST = 2


class HostThrottle:
    """Per-host concurrency cap + minimum delay between request starts.

    HONEST LIMITATION, stated plainly, not glossed over: run_crawl()'s
    own discovery loop (bfs_discover(), unchanged from part 1) visits
    one URL at a time -- it never has more than one request in flight to
    a given host at once in THIS task's own v1 architecture. The
    semaphore below is a real, correctly-enforced cap (never a no-op),
    reusable without change if a later task parallelizes discovery
    further, but it is not yet the BINDING constraint today -- the delay
    is the half of this requirement actually exercised by every crawl
    right now. Built this way deliberately, not left half-finished:
    rebuilding bfs_discover()'s own already-tested sequential algorithm
    into a batched/concurrent one, just to make a cap bind that a
    sequential loop already respects for free, would be speculative
    complexity for no proven benefit yet (rule 11).
    """

    def __init__(
        self, *, max_concurrent: int = MAX_CONCURRENT_REQUESTS_PER_HOST, delay_seconds: float
    ) -> None:
        self._max_concurrent = max_concurrent
        self._delay_seconds = delay_seconds
        self._semaphores: dict[str, asyncio.Semaphore] = {}
        self._last_request_started_at: dict[str, float] = {}

    def _semaphore_for(self, host: str) -> asyncio.Semaphore:
        semaphore = self._semaphores.get(host)
        if semaphore is None:
            semaphore = asyncio.Semaphore(self._max_concurrent)
            self._semaphores[host] = semaphore
        return semaphore

    @asynccontextmanager
    async def slot(self, host: str):
        """Acquires this host's own concurrency slot, waits out any
        remaining delay since the LAST request to this host started,
        then yields. The delay is measured from request START, not
        completion -- two requests issued back-to-back must still be
        `delay_seconds` apart even if the first one is slow, matching
        ordinary rate-limiting convention (a request's own duration
        should not shrink the gap before the next one is allowed to
        start).
        """
        async with self._semaphore_for(host):
            last_started_at = self._last_request_started_at.get(host)
            if last_started_at is not None:
                remaining = self._delay_seconds - (time.monotonic() - last_started_at)
                if remaining > 0:
                    await asyncio.sleep(remaining)
            self._last_request_started_at[host] = time.monotonic()
            yield


async def run_crawl(
    session: AsyncSession,
    client: AsyncQdrantClient,
    collection_name: str,
    *,
    tenant_id: uuid.UUID,
    source_id: uuid.UUID,
    seed_url: str,
    page_cap: int,
    user_agent: str,
    delay_seconds: float,
    on_page_visited: Callable[[], None] | None = None,
) -> list[IngestResult]:
    """The real `crawl` orchestration: robots.txt -> sitemap-first
    discovery, otherwise BFS link-following -- then a capped,
    concurrency-limited, heartbeat-emitting pass over the result, calling
    ingest_url() (2.6.c) per page.

    `on_page_visited`: a plain sync callback, called once after each
    page's own ingest_url() call -- deliberately NOT a direct import of
    app.worker.write_heartbeat() here: worker.py imports app.ingest.
    job_handlers, which (for handle_ingest_crawl()) imports this module --
    importing worker.py back from here would be circular. job_handlers.py
    wires the real callback; this module stays fully decoupled from
    worker.py, matching web_adapter.py's own precedent of taking a
    `clock` callable rather than reaching for a global.

    Sitemap-first vs. BFS are mutually exclusive branches ("otherwise",
    the Step 2.6 decision's own wording), not combined: a sitemap
    (robots.txt-named or the /sitemap.xml fallback) that names ANY URLs
    is treated as the authoritative page list; BFS link-following only
    runs when no sitemap exists at all.

    robots.txt disallow rules are checked at TWO points, not merely one:
    inside the BFS branch's own `get_links` (so a disallowed page's own
    OUTGOING links are never discovered, matching real crawler etiquette
    -- don't even request a disallowed path's own content) and again,
    uniformly, on every candidate URL right before ingest_url() is called
    (covers the seed itself, and every sitemap-mode URL, neither of which
    passes through `get_links`). A disallowed URL is never fetched at
    all -- skipped before any network call, not merely excluded from the
    final result.

    Partial-failure policy: identical to handle_ingest_url()'s own
    (2.6.d) -- an `IngestResult` of any status (ingested/unchanged/
    skipped/failed) is a definitive, recorded, per-page outcome; the loop
    continues regardless. An exception from ingest_url() itself (an
    embed_and_upsert() infrastructure failure, by that function's own
    documented contract) is deliberately left to propagate UNCAUGHT,
    aborting the rest of this crawl -- the same failure would almost
    certainly recur for every remaining page, matching handle_ingest_
    url()'s own identical reasoning exactly.

    A fetch failure discovering one page's own links (inside `get_links`)
    is NOT the same class of problem -- that page simply contributes no
    further links (caught and treated as "found nothing"), not aborted;
    only embed_and_upsert()'s own infrastructure-level failure gets the
    abort-the-whole-crawl treatment.
    """
    seed_host = urlsplit(normalize_url(seed_url)).hostname or ""
    throttle = HostThrottle(delay_seconds=delay_seconds)

    def is_same_domain(url: str) -> bool:
        return urlsplit(url).hostname == seed_host

    async with throttle.slot(seed_host):
        robots = await fetch_robots_txt(seed_url)
    async with throttle.slot(seed_host):
        sitemap_urls = await discover_sitemap_urls(seed_url, robots)

    if sitemap_urls:
        urls_to_visit: list[str] = []
        seen = set()
        for candidate in (seed_url, *sitemap_urls):
            normalized = normalize_url(candidate)
            if normalized in seen or not is_same_domain(normalized):
                continue
            seen.add(normalized)
            urls_to_visit.append(normalized)
            if len(urls_to_visit) >= page_cap:
                break
    else:

        async def get_links(url: str) -> list[str]:
            async with throttle.slot(seed_host):
                try:
                    fetch_result = await fetch_with_redirects(url)
                except (UnsafeFetchError, httpx.HTTPError):
                    return []
            html = fetch_result.body.decode("utf-8", errors="replace")
            return [
                link
                for link in extract_links(html, url)
                if is_allowed_by_robots(robots, link, user_agent)
            ]

        urls_to_visit = await bfs_discover(
            seed_url, is_same_domain=is_same_domain, get_links=get_links, page_cap=page_cap
        )

    results: list[IngestResult] = []
    for url in urls_to_visit:
        if not is_allowed_by_robots(robots, url, user_agent):
            logger.info("run_crawl: source %s url %s disallowed by robots.txt", source_id, url)
            continue
        async with throttle.slot(seed_host):
            result = await ingest_url(
                session,
                client,
                collection_name,
                tenant_id=tenant_id,
                source_id=source_id,
                source_type="crawl",
                url=url,
            )
        await session.commit()
        results.append(result)
        logger.info("run_crawl: source %s url %s -> %s", source_id, url, result.status)
        if on_page_visited is not None:
            on_page_visited()

    return results
