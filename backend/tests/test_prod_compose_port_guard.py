# backend/tests/test_prod_compose_port_guard.py
# [SECURITY] L1 PART C (#7) (out-of-band Phase 7 batch, 2026-10-17): the
# task's own requirement 4 -- "confirm that docker-compose.prod.yml
# publishes NO api, worker, postgres, qdrant or phoenix port on the host
# -- only Caddy's 80/443 -- with a guard test if one doesn't already
# exist." Checked: no existing test covers this (test_single_api_process_
# guard.py checks for a DIFFERENT thing, `replicas:`, not `ports:`).
#
# Plain-text/regex, not a real YAML parse: deploy/docker-compose.prod.yml
# uses the Compose Specification's own `!reset []` tag (confirmed in its
# own header comment: "plain yaml.safe_load() cannot parse without a
# custom constructor"), the same reason test_single_api_process_guard.py
# already gives for staying with substring/regex checks on this exact
# file -- duplication check: that file's own `_assert_no_api_replicas`
# helper's block-matching regex is reused here (parameterized by service
# name) rather than re-invented.
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
PROD_COMPOSE_PATH = REPO_ROOT / "deploy" / "docker-compose.prod.yml"

# Every service deploy/docker-compose.prod.yml defines, in file order.
_ALL_SERVICES: tuple[str, ...] = (
    "caddy",
    "postgres",
    "qdrant",
    "phoenix",
    "api",
    "worker",
    "migrate",
)
_NO_PORT_SERVICES: tuple[str, ...] = tuple(s for s in _ALL_SERVICES if s != "caddy")


def _service_block(text: str, service: str) -> str:
    match = re.search(rf"\n  {service}:\n(.*?)(?=\n  \w|\Z)", text, re.DOTALL)
    assert match is not None, f"docker-compose.prod.yml has no {service!r} service block"
    return match.group(1)


def test_every_service_block_named_above_actually_exists():
    text = PROD_COMPOSE_PATH.read_text()
    for service in _ALL_SERVICES:
        _service_block(text, service)  # raises its own assert if missing


def test_only_caddy_publishes_a_host_port():
    text = PROD_COMPOSE_PATH.read_text()
    caddy_block = _service_block(text, "caddy")
    assert '"80:80"' in caddy_block
    assert '"443:443"' in caddy_block

    for service in _NO_PORT_SERVICES:
        block = _service_block(text, service)
        # Either no ports: key at all (worker, migrate -- no HTTP surface
        # to begin with), or the Compose Specification's own `!reset []`
        # (postgres/qdrant/phoenix/api -- clears whatever the base
        # deploy/docker-compose.yml file might publish, independent of
        # what that file does later). A bare `ports:` with any entry
        # under it (anything other than `!reset []`) is the one shape
        # this guard must catch.
        if "ports:" in block:
            assert "ports: !reset []" in block, (
                f"{service!r} has a 'ports:' key in docker-compose.prod.yml "
                "that is not '!reset []' -- this would publish a host port "
                "for a service that must stay internal-network-only"
            )


def test_caddy_is_the_only_service_with_a_literal_port_mapping_string():
    # Belt-and-braces: scans the WHOLE file (not block-by-block) for any
    # "HOST:CONTAINER" port-mapping-shaped string, and asserts the only
    # ones present are Caddy's own 80/443 -- catches a mapping accidentally
    # added outside a recognised service block too.
    text = PROD_COMPOSE_PATH.read_text()
    mappings = re.findall(r'"(\d{1,5}:\d{1,5})"', text)
    assert set(mappings) == {"80:80", "443:443"}
