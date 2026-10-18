# backend/app/host_binding.py
# [SECURITY] L1 PART C follow-up (advisor review, 2026-10-17): defence in
# depth, ON TOP OF app/main.py's own TrustedHostMiddleware -- that
# middleware only answers "is this request's Host one of the hostnames
# this deployment recognises at all" (api_hostname, dashboard_hostname,
# 127.0.0.1); it has no concept of WHICH hostname a given ROUTE is
# actually supposed to be reachable under. deploy/Caddyfile already
# enforces that split at the proxy layer by construction: the dashboard
# site's own `handle /api/* { reverse_proxy api:8000 }` only ever
# forwards `/api/*` paths, and the api site's bare `handle { reverse_
# proxy api:8000 }` is the only place every OTHER route (/session,
# /chat, /health, /ready, ...) is reachable from. Nothing on the APP
# side enforced that same split before this file existed: a request
# that reaches this container directly (bypassing Caddy -- the same
# "defence in depth" scenario PART C's own trusted-IP work already
# treats as real, not hypothetical) could use EITHER allowed hostname
# for ANY route, as long as TrustedHostMiddleware's own, coarser check
# passed.
#
# ONE central rule, not a per-route check: every dashboard/admin router
# (app/dashboard/routes.py, tenant_routes.py, source_routes.py,
# escalation_routes.py, app/admin/routes.py) is included in app/main.py
# with `dependencies=[Depends(require_dashboard_host)]`; every widget-
# public router (app/auth/routes.py, app/chat/routes.py, app/handoff/
# routes.py) is included with `dependencies=[Depends(require_api_host)]`.
# health_router gets neither -- /health and /ready are deliberately
# reachable on ANY host TrustedHostMiddleware already allows (including
# 127.0.0.1, the container's own healthcheck), carry no secret and no
# session, and docs/SPEC.md's own health-check design never scoped them
# to one hostname.
#
# A FastAPI `dependencies=[...]` on `include_router()` is itself "one
# central rule" in exactly the sense asked for: the check is written and
# reasoned about ONCE per family here, not copy-pasted into every
# individual route handler -- `include_router()` is simply FastAPI's own
# mechanism for attaching one dependency to many routes at once, not a
# second, parallel enforcement point.
#
# Host comparison strips a ":port" suffix, matching Starlette's own
# TrustedHostMiddleware behavior (confirmed by reading the installed
# starlette's parse_host_header()) -- a browser that includes the port
# in its own Host header must not be rejected for it here either, having
# already been judged acceptable by that same middleware one layer out.
from fastapi import Depends, HTTPException, Request

from app.config import Settings, get_settings

_INVALID_HOST_DETAIL = "Invalid host header"


def _host_without_port(request: Request) -> str:
    return request.headers.get("host", "").partition(":")[0]


def require_dashboard_host(
    request: Request,
    settings: Settings = Depends(get_settings),  # noqa: B008 (FastAPI's own Depends() idiom)
) -> None:
    # Non-production is unaffected, matching every other app_env-gated
    # check this batch has added (TrustedHostMiddleware itself, the
    # production secrets gate) -- dev/test has no Caddy and no real
    # hostname to bind a route family to.
    if settings.app_env != "production":
        return
    if _host_without_port(request) != settings.dashboard_hostname:
        raise HTTPException(status_code=400, detail=_INVALID_HOST_DETAIL)


def require_api_host(
    request: Request,
    settings: Settings = Depends(get_settings),  # noqa: B008
) -> None:
    if settings.app_env != "production":
        return
    if _host_without_port(request) != settings.api_hostname:
        raise HTTPException(status_code=400, detail=_INVALID_HOST_DETAIL)
