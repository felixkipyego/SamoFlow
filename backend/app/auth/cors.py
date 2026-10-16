# backend/app/auth/cors.py
# [ops bugfix]: the shared preflight-response builder for every
# cross-origin widget endpoint. Extracted here because the identical
# six-line "echo back the one real Origin header, else omit it" shape
# (app/auth/routes.py's own session_preflight, app/chat/routes.py's own
# chat_preflight) was about to reach its FIFTH occurrence once
# GET /conversations, GET /conversations/{cid}/messages and
# POST /conversations/{cid}/escalate each needed their own OPTIONS
# handler too -- past this project's own established 3rd-occurrence
# extraction threshold (see PROJECT_SPEC.md's duplication-check
# convention), so extracted now rather than copy-pasted a third, fourth
# and fifth time.
#
# Deliberately NOT a Depends(get_db_session)-carrying dependency, and
# never reads the request body -- a CORS preflight exists only to tell
# the browser whether the REAL request would be allowed to be attempted;
# actual authorization always happens on that real request, not here.
# "Vary: Origin" is present unconditionally (this response's own shape
# differs by the Origin header), matching every other endpoint's own
# real-response headers.
from fastapi import Request, Response


def build_preflight_response(request: Request, *, methods: str, headers: str) -> Response:
    origins = request.headers.getlist("origin")
    response_headers = {
        "Access-Control-Allow-Methods": methods,
        "Access-Control-Allow-Headers": headers,
        "Vary": "Origin",
    }
    if len(origins) == 1:
        response_headers["Access-Control-Allow-Origin"] = origins[0]
    return Response(status_code=200, headers=response_headers)
