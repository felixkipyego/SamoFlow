# backend/app/main.py
# Task 1.1.e: FastAPI app factory. Nothing at module level reads the
# environment or calls get_settings(): importing this module must succeed
# even with an empty environment (config errors must surface from
# create_app(), i.e. at startup, not at import time).
#
# ASSUMPTION: importlib.metadata.version("widgetplatform") reads the
# version pinned in backend/pyproject.toml (Task 1.1.a). This only works
# because Task 1.1.b's `pip install -e .` check made the package
# importable-as-installed; a plain `python -m app.main` without that
# install step would raise importlib.metadata.PackageNotFoundError here.
from importlib.metadata import version

from fastapi import FastAPI

from app.config import Settings, get_settings
from app.health import router as health_router

_TITLE = "widgetplatform API"  # neutral name; no brand name in code


def create_app(settings: Settings | None = None) -> FastAPI:
    # Never Settings() directly here: the guard test (test_config_guard.py)
    # only allows app/config.py to construct Settings(), so a missing or
    # invalid environment must fail fast through get_settings() -> SettingsError.
    if settings is None:
        settings = get_settings()

    docs_enabled = settings.app_env != "production"
    app = FastAPI(
        title=_TITLE,
        version=version("widgetplatform"),
        docs_url="/docs" if docs_enabled else None,
        redoc_url="/redoc" if docs_enabled else None,
        openapi_url="/openapi.json" if docs_enabled else None,
    )
    app.state.settings = settings
    app.include_router(health_router)
    return app
