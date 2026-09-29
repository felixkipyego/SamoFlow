# backend/app/auth/origin.py
# Task 1.4.f: the pure origin-matching function (docs/SPEC.md §4.3 "Origin
# binding"; decision (8) recorded at the Step 1.4 breakdown). Exact match on
# scheme, host and EFFECTIVE port (a default port omitted on either side
# must still match its explicit form, e.g. "https://a.example" ==
# "https://a.example:443") -- both the incoming Origin and every
# allowed_origins entry are normalized the same way, so this holds
# regardless of which side happens to spell the port out.
#
# This module only matches ONE already-extracted Origin string against a
# site key's allowed_origins list. It deliberately does NOT handle a
# missing Origin header or a repeated Origin header -- those are properties
# of the raw HTTP request (how many values a header had), not of any single
# string's own well-formedness, so that decision belongs to the route
# itself (backend/app/auth/routes.py), which reads the header via
# Request.headers.getlist("origin") before ever calling this function.
# Only the literal "null" Origin (sent by browsers for sandboxed/opaque
# origins) is naturally rejected here, since "null" has no scheme -- no
# special-casing needed for it.
#
# No wildcards: allowed_origins entries are compared for an exact
# (scheme, host, port) match, never a pattern -- there is no code path here
# that could ever treat a "*" entry as matching anything.
from urllib.parse import urlsplit


def _origin_key(value: str) -> tuple[str, str, int] | None:
    parts = urlsplit(value)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return None
    default_port = 443 if parts.scheme == "https" else 80
    port = parts.port if parts.port is not None else default_port
    return (parts.scheme, parts.hostname.lower(), port)


def origin_is_allowed(origin: str, allowed_origins: list[str]) -> bool:
    origin_key = _origin_key(origin)
    if origin_key is None:
        return False
    return any(_origin_key(allowed) == origin_key for allowed in allowed_origins)
