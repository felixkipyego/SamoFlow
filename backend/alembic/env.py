import sys
from logging.config import fileConfig

from sqlalchemy import create_engine
from sqlalchemy.pool import NullPool

from alembic import context

# Imported for the side effect of registering every domain's tables on
# Base.metadata -- target_metadata below must see all of them, not just
# whichever one happened to be imported first (Task 1.2.b; the gap this
# guards against was real: app.ingest.models' own absence here once made
# autogenerate silently see zero changes for its tables, Task 2.1.b).
# Duplication check after 2.1.c/d/e: moved into one shared module,
# app/all_models.py, also imported by app/worker.py for the identical
# reason (a different mechanism -- SQLAlchemy's flush-time FK dependency
# sort, not Alembic's own target_metadata diff -- but the same fix), so
# neither entrypoint has to separately remember every domain package.
from app import all_models  # noqa: F401
from app.config import SettingsError, get_settings
from app.db import Base

# This is the Alembic Config object, which provides access to the values
# within the .ini file in use.
config = context.config

# Interpret the config file for Python logging. This line sets up loggers
# basically.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_online() -> None:
    # The URL comes from get_settings() only, never from config.set_main_option
    # (that call breaks on "%" characters in the URL and would also copy the
    # secret into the Config object, e.g. for a later config.get_main_option
    # call or exception message).
    try:
        url = get_settings().database_url_str()
    except SettingsError as exc:
        # Same behaviour as app/worker.py's main(): a clean one-line message,
        # no traceback, so a misconfigured environment never prints one.
        print(str(exc), file=sys.stderr)
        sys.exit(1)
    connectable = create_engine(url, poolclass=NullPool)

    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)

        with context.begin_transaction():
            context.run_migrations()


run_migrations_online()
