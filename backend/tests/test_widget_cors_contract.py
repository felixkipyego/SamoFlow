# backend/tests/test_widget_cors_contract.py
# [SECURITY] Step 7-follow-up, Part B: the real reason the e2e suite
# kept passing while the real widget was broken, applied a second time
# to CORS itself (not just the SSE event shapes docs/chat_sse_contract.
# json already covers) -- widget/test/e2e/server.mjs's own fake API must
# never be free to type its own preflight methods/headers independently
# of the real backend's. This asserts the real, live WIDGET_ROUTES
# (app/auth/cors.py) matches the committed docs/widget_cors_contract.
# json exactly; that same file is what server.mjs reads to build its own
# CORS logic.
import json
import re
from pathlib import Path

from app.auth.cors import WIDGET_ROUTES

_CONTRACT_PATH = (
    Path(__file__).resolve().parent.parent.parent / "docs" / "widget_cors_contract.json"
)


def test_the_committed_cors_contract_file_exists():
    assert _CONTRACT_PATH.exists()


def test_the_real_widget_routes_match_the_committed_contract_file():
    contract = json.loads(_CONTRACT_PATH.read_text())
    contract_routes = {k: v for k, v in contract.items() if not k.startswith("_")}

    real_routes = {
        name: {"path": pattern.pattern, "methods": methods, "headers": headers}
        for name, pattern, methods, headers in WIDGET_ROUTES
    }

    assert real_routes == contract_routes, (
        "app.auth.cors.WIDGET_ROUTES no longer matches the committed "
        f"contract at {_CONTRACT_PATH}. If this change is deliberate, "
        "update docs/widget_cors_contract.json AND widget/test/e2e/"
        "server.mjs together, in the same change -- the mock reads this "
        "exact file to build its own preflight/CORS logic."
    )


def test_every_contract_path_pattern_is_a_valid_compilable_regex():
    contract = json.loads(_CONTRACT_PATH.read_text())
    for name, entry in contract.items():
        if name.startswith("_"):
            continue
        re.compile(entry["path"])  # raises re.error on anything malformed
