# backend/app/cli.py
# Duplication check after 3.2.b/c/d (2026-11-02), finding A.3: the exact
# `logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s
# %(name)s %(message)s")` call existed identically in app/worker.py,
# app/qdrant.py and app/agent/checkpointer.py -- configure_cli_logging()
# below is the one place this format string is spelled out, used by all
# three.
#
# run_cli() additionally covers app/qdrant.py's and app/agent/
# checkpointer.py's own near-identical "configure logging, run one
# coroutine via asyncio.run(), print a clean one-line message and exit 1
# on a known exception type" shape -- confirmed byte-for-byte identical
# except for the coroutine called and the exception tuple. app/worker.py's
# own main() is deliberately NOT a candidate for this second helper: its
# signal-handling/custom-event-loop control flow (SIGTERM/SIGINT,
# loop.run_until_complete, explicit loop.close()) is genuinely different,
# not a near-copy -- it still calls configure_cli_logging() for the one
# part that really was identical.
import asyncio
import logging
import sys
from collections.abc import Awaitable, Callable

LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s %(message)s"


def configure_cli_logging() -> None:
    logging.basicConfig(level=logging.INFO, format=LOG_FORMAT)


def run_cli(
    coro_factory: Callable[[], Awaitable[None]],
    *exception_types: type[Exception],
) -> None:
    """Configure logging, run `coro_factory()` via `asyncio.run()`, and on
    any of `exception_types`, print its own message to stderr and exit 1
    -- the caller's own exception message must already be proven free of
    secrets (both current callers' docstrings confirm this), since it is
    printed directly, not re-wrapped.
    """
    configure_cli_logging()
    try:
        asyncio.run(coro_factory())
    except exception_types as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)
