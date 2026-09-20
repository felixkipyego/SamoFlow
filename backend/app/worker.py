# backend/app/worker.py
# Task 1.1.f: worker entrypoint stub. No job logic yet (TODO(2.1): the job
# queue consumer lands in Step 2.1) -- this only proves the process starts,
# reads settings the same way the API does, and shuts down cleanly on
# SIGTERM/SIGINT.
import asyncio
import logging
import signal
import sys

from app.config import SettingsError, get_settings

logger = logging.getLogger(__name__)


async def run(stop: asyncio.Event | None = None) -> None:
    # Never Settings() directly: only app/config.py may do that (guard test).
    settings = get_settings()
    if stop is None:
        stop = asyncio.Event()
    # Only app_env, never database_url/qdrant_url: those must not reach logs.
    logger.info("worker starting: app_env=%s", settings.app_env)
    await stop.wait()


def main() -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s"
    )
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    stop = asyncio.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)
    try:
        loop.run_until_complete(run(stop))
    except SettingsError as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)
    finally:
        loop.close()


if __name__ == "__main__":
    main()
